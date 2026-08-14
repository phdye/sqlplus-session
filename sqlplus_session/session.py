"""Persistent sqlplus session over stdin/stdout pipes.

Connect once, run many queries on the same Oracle session.  Stdlib only,
Python 3.2.8+.  No cx_Oracle, no python-oracledb, no third-party packages.

Credentials never reach the command line.  sqlplus is started as
``sqlplus -s /nolog`` and authenticated over the same stdin pipe the
queries use, so the password is invisible to ``ps``, to
``/proc/<pid>/cmdline``, and to anything that logs process arguments.

Credentials default to ``DB_USERNAME``, ``DB_PASSWORD`` and ``DB_NAME``
(or ``TWO_TASK``, or ``ORACLE_SID``).  The defaults live here rather
than in each caller, so ``SqlplusSession()`` with no arguments is a
working session and no tool has to reinvent the convention::

    with SqlplusSession() as s:
        print(s.query('SELECT user FROM dual'))

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

__all__ = ['SqlplusSession', 'credentials_from_environment',
           'resolve_credentials', 'load_env_file']

# The conventional variables. A caller that passes None for any of the
# three credential arguments gets the corresponding value from here, so
# SqlplusSession() with no arguments at all is a working environment-
# driven session.
ENV_USERNAME = 'DB_USERNAME'
ENV_PASSWORD = 'DB_PASSWORD'
# DB_NAME first, then the two variables sqlplus itself already honours.
ENV_CONNECT = ('DB_NAME', 'TWO_TASK', 'ORACLE_SID')

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


def credentials_from_environment():
    """Return ``(username, password, connect_string)`` from the environment.

    Empty strings where a variable is unset. Nothing is read lazily and
    nothing is cached: a caller that changes the environment between
    sessions gets the change.
    """
    connect = ''
    for name in ENV_CONNECT:
        connect = os.environ.get(name) or ''
        if connect:
            break
    return (os.environ.get(ENV_USERNAME) or '',
            os.environ.get(ENV_PASSWORD) or '',
            connect)


def resolve_credentials(username, password, connect_string):
    """Fill in whichever of the three were left as ``None``.

    ``None`` means take it from the environment. An empty string means
    what it says, which is why the two are not interchangeable: passing
    ``''`` as the username selects external authentication and must not
    be overridden by whatever happens to be exported.
    """
    env_user, env_pw, env_connect = credentials_from_environment()
    return (env_user if username is None else username,
            env_pw if password is None else password,
            env_connect if connect_string is None else connect_string)


def _connect_expansion():
    """``${A:-${B:-$C}}`` built from ENV_CONNECT.

    Composed rather than written out, so adding a name to ENV_CONNECT
    cannot leave the shell script consulting the old list.
    """
    expr = '$' + ENV_CONNECT[-1]
    for name in reversed(ENV_CONNECT[:-1]):
        expr = '${%s:-%s}' % (name, expr)
    return expr


# Sourced in a subshell, which then prints back only the three values we
# asked for.  Sourcing rather than parsing matters: an environment file
# that computes its values, or that defers to another file, is common and
# a line-by-line parser gets it wrong.  NUL separators because a password
# is allowed to contain anything, newlines included.
#
# The credential variables are unset before the file is sourced, so what
# comes back is what the file provides and not whatever the caller
# happened to have exported.  Without that, the answer depends on ambient
# state, which is the kind of thing that works on one box and not the
# next.  A caller wanting file-over-environment merges the two itself;
# credentials_from_environment() is right there.
_ENV_FILE_SCRIPT = (
    'unset %s\n'
    '. "$1" >/dev/null 2>&1 || exit 3\n'
    'printf "%%s\\0%%s\\0%%s\\0" "$%s" "$%s" "%s"\n'
    % (' '.join((ENV_USERNAME, ENV_PASSWORD) + tuple(ENV_CONNECT)),
       ENV_USERNAME, ENV_PASSWORD, _connect_expansion())
)


def load_env_file(path, shell='/bin/sh'):
    """Source a shell environment file; return its credential triple.

    Only the three variables come back, so nothing else in the file
    enters this process, and the password crosses one pipe instead of
    being recovered from a line of text.

    The answer describes the file alone: the credential variables are
    unset before it is sourced, so an exported ``DB_NAME`` in the
    calling shell cannot show through and be mistaken for the file's.

    Raises ``IOError`` if *path* is not there and ``ValueError`` if the
    shell cannot source it.
    """
    path = os.path.expanduser(path)
    if not os.path.isfile(path):
        raise IOError('no such environment file: %s' % path)
    # os.devnull rather than subprocess.DEVNULL, which is 3.3+, and
    # rather than a pipe nobody reads: an environment file that chatters
    # on stderr would fill the buffer and hang.
    devnull = open(os.devnull, 'w')
    try:
        out = subprocess.check_output(
            [shell, '-c', _ENV_FILE_SCRIPT, 'sqlplus-session', path],
            stderr=devnull)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError('cannot source %s: %s' % (path, exc))
    finally:
        devnull.close()
    parts = out.decode('utf-8', 'replace').split('\0')
    while len(parts) < 3:
        parts.append('')
    return parts[0], parts[1], parts[2]


def _quote_password(password):
    """Double-quote a password for the ``CONNECT`` line.

    There is no unquoted place to put a password.  The line after
    ``CONNECT`` looks like one -- sqlplus prompts for the password there
    when it has a terminal -- but with stdin on a pipe it parses that
    line as more CONNECT arguments instead.  Measured against sqlplus
    19c, 14 August 2026: an ``@`` in the password sends sqlplus off to
    resolve a net service name and the connect hangs to the timeout, and
    a ``/`` or a space comes straight back as ``SP2-0306: Invalid
    option``.  Quoting handles all three, and ``#``, ``$``, ``%``,
    ``!`` and ``'`` besides.

    Oracle refuses to create a password containing a double quote
    (``ORA-03001``), so the doubling here is for completeness rather
    than for any password that can exist.
    """
    return '"%s"' % password.replace('"', '""')


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
    username : str or None
        Oracle username.  ``None``, the default, takes it from
        ``DB_USERNAME``.  An empty string selects external
        authentication -- wallet or OS -- and *password* is then
        ignored.  The two are not interchangeable: ``None`` asks the
        environment, ``''`` states an answer.
    password : str or None
        Oracle password.  ``None`` takes it from ``DB_PASSWORD``.
        Written to the stdin pipe, double-quoted, never to the
        command line -- so ``ps`` and ``/proc/<pid>/cmdline`` never
        see it and no character in it needs escaping by the caller.
    connect_string : str or None
        TNS alias or Easy Connect string (e.g. ``'localhost/orcl'``).
        ``None`` takes it from ``DB_NAME``, then ``TWO_TASK``, then
        ``ORACLE_SID``.  Empty means a bequeath connection to
        ``ORACLE_SID``.
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

    def __init__(self, username=None, password=None, connect_string=None,
                 sqlplus_cmd='sqlplus', env=None, setup_commands=None,
                 connect_timeout=30, default_timeout=60,
                 error_patterns=None, on_error='raise',
                 path_converter=None):

        username, password, connect_string = resolve_credentials(
            username, password, connect_string)

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

        # /nolog, always.  Nothing that identifies the account and
        # nothing secret is passed as an argument; authentication
        # happens over stdin in _connect() once the pipe is up.
        try:
            self._proc = subprocess.Popen(
                [sqlplus_cmd, '-s', '/nolog'],
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

        # Authenticate, apply session setup, then prove the connection
        # with a trivial query.  Setup runs after the connect so that
        # ALTER SESSION is legal in setup_commands.
        try:
            self._connect(username, password, connect_string,
                          connect_timeout)

            for cmd in setup_commands:
                self._write(self._terminate_setup(cmd))

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

    @classmethod
    def from_env_file(cls, path, shell='/bin/sh', **kwargs):
        """Open a session using credentials from a shell environment file.

        The three values are sourced out of *path* and passed straight
        to the constructor, so a caller with an environment file does
        not have to know what the variables are called::

            with SqlplusSession.from_env_file('~/.dbenv') as s:
                ...
        """
        user, pw, connect = load_env_file(path, shell)
        return cls(user, pw, connect, **kwargs)

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

    def _connect(self, username, password, connect_string, timeout):
        """Authenticate an already-running ``sqlplus -s /nolog``.

        The credential travels down the stdin pipe the session already
        owns, so it never lands in the process table.  That is the whole
        of what the /nolog start buys, and it holds regardless of where
        on the pipe the password goes.

        Nothing about the password is retained once this returns.
        """
        # ECHO OFF first, so nothing we send is written back to stdout.
        # Deliberately not part of setup_commands: a caller who clears
        # that list still gets it.
        self._write('SET ECHO OFF\n')

        if username:
            login = '%s/%s' % (username, _quote_password(password or ''))
            if connect_string:
                self._write('CONNECT %s@%s\n' % (login, connect_string))
            else:
                self._write('CONNECT %s\n' % login)
        elif connect_string:
            # External authentication: wallet, or OS authentication.
            self._write('CONNECT /@%s\n' % connect_string)
        else:
            self._write('CONNECT /\n')

        lines = self._raw_query('', timeout)
        if password:
            # Belt and braces.  ECHO OFF should mean the credential is
            # never reflected, but an exception raised from here would
            # otherwise carry whatever did come back.
            lines = [l.replace(password, '***') for l in lines]

        errs = [l for l in lines if self._error_re.search(l)]
        if errs:
            self._kill()
            raise SqlplusConnectError(
                'sqlplus connect failed: %s' % '; '.join(errs),
                output=lines)

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
            # endswith, not just ==: sqlplus writes its prompts
            # ("Enter password:") with no trailing newline, so a prompt
            # can arrive glued to the front of the next line -- and
            # during connect that next line is the sentinel.
            if stripped == sentinel or stripped.endswith(sentinel):
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

    # SQL*Plus commands are complete at the end of the line.  SQL
    # statements are not: sqlplus buffers them until a terminator
    # arrives, so an unterminated ALTER SESSION in setup_commands
    # swallows whatever is sent next.  That next thing is the probe
    # query, and the buffer reaches Oracle as
    #
    #     ALTER SESSION SET NLS_DATE_FORMAT = '...' SELECT 1 FROM DUAL
    #
    # which comes back ORA-00922: missing or invalid option.
    #
    # The default setup list is all SQL*Plus commands, so nothing here
    # trips it.  A caller that adds one ALTER SESSION does, and gets a
    # parse error naming a statement it did not write.
    _SQLPLUS_COMMANDS = frozenset("""
        ACCEPT APPEND ARCHIVE ATTRIBUTE BREAK BTITLE CHANGE CLEAR COLUMN
        COMPUTE CONNECT COPY DEFINE DEL DESC DESCRIBE DISCONNECT EDIT
        EXEC EXECUTE EXIT GET HELP HOST INPUT LIST PASSWORD PAUSE PRINT
        PROMPT QUIT RECOVER REM REMARK REPFOOTER REPHEADER RUN SAVE SET
        SHOW SHUTDOWN SPOOL START STARTUP STORE TIMING TTITLE UNDEFINE
        VARIABLE WHENEVER XQUERY
    """.split())

    @classmethod
    def _terminate_setup(cls, cmd):
        """Terminate *cmd* if it is SQL, leave it alone if it is not.

        ``SET PAGESIZE 0;`` is an error, so terminating everything is
        not an option either.
        """
        s = cmd.strip()
        if not s:
            return '\n'
        if s[-1] in ';/' or s[0] in '@/':
            return s + '\n'
        if s.split(None, 1)[0].upper() in cls._SQLPLUS_COMMANDS:
            return s + '\n'
        return s + ';\n'

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
