"""Persistent sqlplus session over stdin/stdout pipes.

Connect once, run many queries on the same Oracle session.  Stdlib only,
Python 3.2.5+.  No cx_Oracle, no python-oracledb, no third-party packages.

The module manages one long-lived ``sqlplus -s`` process.  SQL goes in on
stdin, results come back on stdout, delimited by a numbered sentinel
(``PROMPT __EOQ__<n>__``).  A reader thread drains the stdout pipe so the
main thread can impose per-query timeouts via ``Queue.get(timeout=)``.

Typical usage::

    from sqlplus_session import SqlplusSession

    with SqlplusSession('scott', 'tiger', 'orcl') as s:
        rows = s.query('SELECT ename, sal FROM emp WHERE deptno = 10')
        for r in rows:
            print(r)
        s.execute('UPDATE emp SET sal = sal * 1.1 WHERE deptno = 10')
        s.execute('COMMIT')
"""

import os
import re
import subprocess
import sys
import threading
import time

try:
    import queue
except ImportError:
    import Queue as queue  # pragma: no cover  (Python 2 compat)

from ._errors import (
    SqlplusConnectError,
    SqlplusDied,
    SqlplusOraError,
    SqlplusTimeout,
)

__all__ = ['SqlplusSession']

# Sensible defaults for scripted (non-interactive) use.  Callers can
# override via the *setup_commands* constructor argument.
_DEFAULT_SETUP = [
    'SET PAGESIZE 0',
    'SET HEADING OFF',
    'SET FEEDBACK OFF',
    'SET VERIFY OFF',
    'SET ECHO OFF',
    'SET TRIMSPOOL ON',
    'SET LINESIZE 4000',
    'WHENEVER SQLERROR CONTINUE',
]

# Patterns that flag an output line as an Oracle or sqlplus error.
_DEFAULT_ERROR_PATTERNS = [
    r'ORA-\d',
    r'TNS-\d',
    r'SP2-\d',
    r'ERROR:',
    r'Undefined variable',
    r'Illegal variable',
]

# Prefix used for sentinel lines.  Incrementing counter prevents a stale
# sentinel from a prior (timed-out) query from being mistaken for the
# current one.
_SENTINEL_PREFIX = '__EOQ__'
_SENTINEL_SUFFIX = '__'


def _reader_loop(pipe, q):
    """Read lines from *pipe*, put each on *q*.  ``None`` signals EOF."""
    try:
        # iter(pipe.readline, '') calls readline() one line at a time.
        # ``for line in pipe`` uses a read-ahead buffer at Python 3.2 that
        # can hold back lines, causing deadlocks.
        for line in iter(pipe.readline, ''):
            q.put(line)
    finally:
        q.put(None)


class SqlplusSession(object):
    """A persistent Oracle sqlplus session.

    The session connects at construction and stays connected until
    :meth:`close` (or the context manager exits).  Every :meth:`query`
    and :meth:`run_file` call reuses the same connection.

    Parameters
    ----------
    username : str
        Oracle username.
    password : str
        Oracle password.
    connect_string : str
        TNS alias or Easy Connect string (e.g. ``'localhost/orcl'``).
    sqlplus_cmd : str
        Path or bare name of the sqlplus binary.  Default ``'sqlplus'``.
    env : dict or None
        Environment for the sqlplus process.  ``None`` inherits
        ``os.environ``.  Must include ``ORACLE_HOME`` / ``PATH`` so
        sqlplus finds its shared libraries.
    setup_commands : list of str or None
        SQL*Plus SET/ALTER commands executed immediately after connect.
        ``None`` uses a sensible default for scripted use.  Pass an
        empty list to skip all setup.
    connect_timeout : int or float
        Seconds to wait for the initial connect + probe query.
    default_timeout : int or float
        Default per-query timeout (overridable per call).
    error_patterns : list of str (regex) or None
        Patterns that flag an output line as an error.  ``None`` uses
        the built-in ORA-/TNS-/SP2- set.  Each element is a regex
        fragment; they are combined with ``|``.
    on_error : str
        ``'raise'`` (default) raises :class:`SqlplusOraError` when
        error lines appear.  ``'return'`` returns the output including
        errors and lets the caller decide.
    path_converter : callable or None
        ``f(str) -> str`` that converts filesystem paths for ``@file``
        commands.  On Cygwin, pass a wrapper around ``cygpath -m``.

    Raises
    ------
    SqlplusConnectError
        If the sqlplus binary cannot be found, the connect string is
        wrong, or the credentials are rejected.
    """

    def __init__(self, username, password, connect_string,
                 sqlplus_cmd='sqlplus', env=None, setup_commands=None,
                 connect_timeout=30, default_timeout=60,
                 error_patterns=None, on_error='raise',
                 path_converter=None):

        if on_error not in ('raise', 'return'):
            raise ValueError("on_error must be 'raise' or 'return', "
                             "got %r" % on_error)

        self.default_timeout = default_timeout
        self._on_error = on_error
        self._path_converter = path_converter
        self._n = 0          # sentinel counter
        self._closed = False

        # Compile the error-detection regex once.
        if error_patterns is None:
            error_patterns = list(_DEFAULT_ERROR_PATTERNS)
        self._error_re = re.compile(
            '|'.join('(?:%s)' % p for p in error_patterns))

        if setup_commands is None:
            setup_commands = list(_DEFAULT_SETUP)

        # Open devnull for stderr redirection; closed in close().
        self._devnull = open(os.devnull, 'w')

        # Build the connect string.  Password may contain special chars
        # (#, $, @); putting it on the command line is the standard
        # sqlplus pattern and avoids an interactive CONNECT prompt.
        login = '%s/%s@%s' % (username, password, connect_string)

        try:
            self._proc = subprocess.Popen(
                [sqlplus_cmd, '-s', login],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=self._devnull,
                universal_newlines=True,  # 'text' kwarg is 3.7+
                bufsize=1,               # line-buffered stdin
                env=env,
            )
        except OSError as e:
            self._devnull.close()
            raise SqlplusConnectError(
                'cannot start %s: %s' % (sqlplus_cmd, e))

        # Start the reader thread before sending anything.
        self._q = queue.Queue()
        t = threading.Thread(target=_reader_loop,
                             args=(self._proc.stdout, self._q))
        t.daemon = True    # daemon kwarg in Thread() is 3.3+
        t.start()
        self._reader_thread = t

        # Send session setup, then probe with a trivial query.
        try:
            for cmd in setup_commands:
                self._write(cmd + '\n')

            probe = self._raw_query('SELECT 1 FROM DUAL;\n',
                                    timeout=connect_timeout)

            # Any ORA-/SP2- in the probe means connect failed.
            errs = [l for l in probe if self._error_re.search(l)]
            if errs:
                self._kill()
                raise SqlplusConnectError(
                    'sqlplus connect failed: %s' % '; '.join(errs),
                    output=probe)
        except SqlplusTimeout as e:
            raise SqlplusConnectError(
                'sqlplus did not respond within %ds (connect timeout)'
                % connect_timeout,
                output=e.output)
        except SqlplusDied as e:
            raise SqlplusConnectError(
                'sqlplus exited during connect (rc=%s)' % e.returncode,
                output=e.output)

    # ------------------------------------------------------------------ #
    # Public API                                                          #
    # ------------------------------------------------------------------ #

    def query(self, sql, timeout=None):
        """Run *sql* and return the output lines.

        Parameters
        ----------
        sql : str
            A SQL statement, PL/SQL block, or sqlplus command.  A
            trailing ``;`` or ``/`` is added if missing.
        timeout : int, float, or None
            Seconds.  ``None`` uses ``self.default_timeout``.

        Returns
        -------
        list of str
            Output lines with trailing newlines stripped.  Leading/
            trailing whitespace within each line is preserved (sqlplus
            right-justifies NUMBER columns, for instance).

        Raises
        ------
        SqlplusOraError
            When ``on_error='raise'`` and error patterns appear.
        SqlplusTimeout
            When the deadline is exceeded.  The session is dead.
        SqlplusDied
            When sqlplus exits mid-query.
        """
        self._check_alive()
        if timeout is None:
            timeout = self.default_timeout
        lines = self._raw_query(self._terminate_sql(sql), timeout)
        return self._handle_errors(lines)

    def run_file(self, path, timeout=None):
        """Run a ``.sql`` file via ``@<path>``.

        Parameters
        ----------
        path : str
            Filesystem path to the SQL file.  Converted via
            *path_converter* if one was supplied at construction.
        timeout : int, float, or None
            Seconds.  ``None`` uses ``self.default_timeout``.

        Returns
        -------
        list of str
        """
        self._check_alive()
        if timeout is None:
            timeout = self.default_timeout
        if self._path_converter is not None:
            path = self._path_converter(path)
        lines = self._raw_query('@%s\n' % path, timeout)
        return self._handle_errors(lines)

    def execute(self, sql, timeout=None):
        """Run *sql* and discard the output.

        Same error/timeout behavior as :meth:`query`.
        """
        self.query(sql, timeout=timeout)

    @property
    def alive(self):
        """``True`` if the sqlplus process is still running."""
        if self._closed:
            return False
        if self._proc.poll() is not None:
            self._closed = True
            return False
        return True

    def close(self):
        """Shut down the session.  Idempotent."""
        if self._closed:
            return
        self._closed = True
        if self._proc.poll() is None:
            try:
                self._write('EXIT\n')
                self._proc.stdin.close()
                self._proc.wait()
            except (IOError, OSError):
                self._proc.kill()
                self._proc.wait()
        try:
            self._devnull.close()
        except (IOError, OSError):
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def __del__(self):
        # Best-effort cleanup if the caller forgets close().
        try:
            self.close()
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # Internals                                                           #
    # ------------------------------------------------------------------ #

    def _write(self, text):
        """Write *text* to sqlplus stdin and flush."""
        try:
            self._proc.stdin.write(text)
            self._proc.stdin.flush()
        except (IOError, OSError):
            rc = self._proc.poll()
            self._closed = True
            raise SqlplusDied(rc)

    def _raw_query(self, sql_text, timeout):
        """Send *sql_text* + sentinel, read lines until sentinel."""
        self._n += 1
        sentinel = '%s%d%s' % (_SENTINEL_PREFIX, self._n, _SENTINEL_SUFFIX)

        # The SQL text must end with a newline so sqlplus sees a complete
        # input line.  The sentinel goes on a separate line after it.
        if not sql_text.endswith('\n'):
            sql_text += '\n'
        self._write(sql_text)
        self._write('PROMPT %s\n' % sentinel)

        lines = []
        deadline = time.time() + timeout
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                self._timeout_kill(lines)
                # _timeout_kill always raises; this is unreachable but
                # makes the control flow obvious to readers and linters.
                break  # pragma: no cover
            try:
                line = self._q.get(timeout=max(0.05, remaining))
            except queue.Empty:
                self._timeout_kill(lines)
                break  # pragma: no cover
            if line is None:
                # EOF: sqlplus died.
                rc = self._proc.poll()
                self._closed = True
                raise SqlplusDied(rc, lines)
            stripped = line.rstrip('\r\n')
            if stripped == sentinel:
                break
            lines.append(stripped)
        return lines

    def _timeout_kill(self, partial_output):
        """Kill the sqlplus process and raise :class:`SqlplusTimeout`."""
        self._proc.kill()
        self._proc.wait()
        self._closed = True
        raise SqlplusTimeout(partial_output)

    def _kill(self):
        """Kill sqlplus without raising.  For constructor cleanup."""
        if self._proc.poll() is None:
            self._proc.kill()
            self._proc.wait()
        self._closed = True

    def _check_alive(self):
        """Raise :class:`SqlplusDied` if the process has exited."""
        if self._closed:
            raise SqlplusDied(getattr(self._proc, 'returncode', None))
        if self._proc.poll() is not None:
            self._closed = True
            raise SqlplusDied(self._proc.returncode)

    def _handle_errors(self, lines):
        """Scan *lines* for error patterns; raise or return per policy."""
        errs = [l for l in lines if self._error_re.search(l)]
        if errs and self._on_error == 'raise':
            raise SqlplusOraError(errs, lines)
        return lines

    @staticmethod
    def _terminate_sql(sql):
        """Ensure *sql* ends with a statement terminator and newline.

        Plain SQL gets ``;`` if missing.  PL/SQL blocks (ending with
        ``END;``) or blocks already terminated with ``/`` get ``/``.
        """
        s = sql.rstrip()
        if not s:
            return '\n'

        # Already terminated with / (PL/SQL)
        if s.endswith('/'):
            return s + '\n'

        # PL/SQL block: ends with END or END <name>, possibly with ;
        upper = s.rstrip(';').rstrip()
        if re.search(r'\bEND\b\s*\w*\s*$', upper, re.IGNORECASE):
            # Needs / on its own line to execute
            if not s.endswith(';'):
                s += ';'
            return s + '\n/\n'

        # Plain SQL: needs ;
        if not s.endswith(';'):
            s += ';'
        return s + '\n'
