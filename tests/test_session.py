"""Comprehensive tests for SqlplusSession using fake_sqlplus.

These run without a database or Oracle installation.  The fake_sqlplus.py
script acts as a drop-in for sqlplus, responding to queries and honoring
the sentinel protocol.
"""

import os
import sys
import time
import unittest

# Ensure the package is importable from the project root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from sqlplus_session import (
    SqlplusSession,
    SqlplusConnectError,
    SqlplusDied,
    SqlplusOraError,
    SqlplusTimeout,
    SqlplusError,
    credentials_from_environment,
    resolve_credentials,
    load_env_file,
)

# Path to the fake sqlplus script.
FAKE_SQLPLUS = os.path.join(os.path.dirname(__file__), 'fake_sqlplus.py')

# The fake is invoked as: python <fake_sqlplus.py>
# SqlplusSession calls [sqlplus_cmd, '-s', '/nolog'], so we make a
# wrapper that ignores those two arguments and runs the fake.
_PYTHON = sys.executable


def _fake_cmd():
    """Return the sqlplus_cmd string that invokes the fake."""
    return _PYTHON


def _session(**kwargs):
    """Create a SqlplusSession backed by fake_sqlplus."""
    defaults = dict(
        username='test',
        password='test',
        connect_string='fake',
        sqlplus_cmd=_fake_cmd(),
        connect_timeout=5,
        default_timeout=5,
    )
    defaults.update(kwargs)

    # We need to make the fake_sqlplus.py the actual executable.
    # Since SqlplusSession runs [sqlplus_cmd, '-s', login], we need
    # sqlplus_cmd to be a script/binary that acts like sqlplus.
    # Solution: use a wrapper script.
    return SqlplusSession(**defaults)


# We need a real executable that behaves like fake_sqlplus.
# Create a shell wrapper on the fly.
_WRAPPER = None


def _ensure_wrapper():
    """Create a wrapper script that invokes fake_sqlplus.py."""
    global _WRAPPER
    if _WRAPPER is not None:
        return _WRAPPER

    import tempfile
    import stat

    # On Windows/Cygwin we'd need a .bat; on POSIX a shell script.
    fd, path = tempfile.mkstemp(suffix='.sh', prefix='fake_sqlplus_')
    os.write(fd, ('#!/bin/sh\nexec %s %s "$@"\n'
                   % (_PYTHON, FAKE_SQLPLUS)).encode('utf-8'))
    os.close(fd)
    os.chmod(path, stat.S_IRWXU)
    _WRAPPER = path
    return path


def _make_session(**kwargs):
    """Create a session using the wrapper script."""
    wrapper = _ensure_wrapper()
    defaults = dict(
        username='test',
        password='test',
        connect_string='fake',
        sqlplus_cmd=wrapper,
        connect_timeout=5,
        default_timeout=5,
    )
    defaults.update(kwargs)
    return SqlplusSession(**defaults)


class TestConstruction(unittest.TestCase):
    """Constructor and connection verification."""

    def test_basic_connect(self):
        s = _make_session()
        self.assertTrue(s.alive)
        s.close()
        self.assertFalse(s.alive)

    def test_context_manager(self):
        with _make_session() as s:
            self.assertTrue(s.alive)
        self.assertFalse(s.alive)

    def test_double_close(self):
        s = _make_session()
        s.close()
        s.close()  # must not raise

    def test_bad_sqlplus_cmd(self):
        with self.assertRaises(SqlplusConnectError) as ctx:
            SqlplusSession('u', 'p', 'x',
                           sqlplus_cmd='/no/such/binary_xyzzy')
        self.assertIn('cannot start', str(ctx.exception))

    def test_invalid_on_error(self):
        with self.assertRaises(ValueError):
            _make_session(on_error='bogus')


class TestQuery(unittest.TestCase):
    """The query() method."""

    def test_select_from_dual(self):
        with _make_session() as s:
            rows = s.query('SELECT 42 FROM DUAL')
            # The fake returns "\t 42"
            vals = [r.strip() for r in rows if r.strip()]
            self.assertIn('42', vals)

    def test_multiple_queries(self):
        with _make_session() as s:
            for i in range(20):
                rows = s.query('SELECT %d FROM DUAL' % i)
                vals = [r.strip() for r in rows if r.strip()]
                self.assertIn(str(i), vals)

    def test_query_adds_semicolon(self):
        with _make_session() as s:
            rows = s.query('SELECT 7 FROM DUAL')
            vals = [r.strip() for r in rows if r.strip()]
            self.assertIn('7', vals)

    def test_query_with_existing_semicolon(self):
        with _make_session() as s:
            rows = s.query('SELECT 8 FROM DUAL;')
            vals = [r.strip() for r in rows if r.strip()]
            self.assertIn('8', vals)

    def test_empty_query(self):
        with _make_session() as s:
            rows = s.query('')
            # Empty SQL should not crash; result is empty.
            self.assertIsInstance(rows, list)


class TestExecute(unittest.TestCase):
    """The execute() method."""

    def test_execute_returns_none(self):
        with _make_session() as s:
            result = s.execute('SELECT 1 FROM DUAL')
            self.assertIsNone(result)


class TestRunFile(unittest.TestCase):
    """The run_file() method."""

    def test_run_file_basic(self):
        with _make_session() as s:
            rows = s.run_file('/tmp/test.sql')
            # The fake returns two lines for @file.
            self.assertTrue(len(rows) >= 2)
            self.assertIn('file-output-line-1', rows)

    def test_run_file_with_path_converter(self):
        converted = []

        def converter(p):
            converted.append(p)
            return '/converted' + p

        with _make_session(path_converter=converter) as s:
            s.run_file('/tmp/test.sql')
            self.assertEqual(converted, ['/tmp/test.sql'])


class TestErrorHandling(unittest.TestCase):
    """Error detection and on_error modes."""

    def test_ora_error_raises(self):
        with _make_session(on_error='raise') as s:
            with self.assertRaises(SqlplusOraError) as ctx:
                s.query('__FAKE_ORA_ERROR__')
            exc = ctx.exception
            self.assertTrue(any('ORA-00942' in e for e in exc.errors))
            self.assertIsInstance(exc.output, list)

    def test_session_survives_ora_error(self):
        with _make_session(on_error='raise') as s:
            try:
                s.query('__FAKE_ORA_ERROR__')
            except SqlplusOraError:
                pass
            # Session should still be alive.
            self.assertTrue(s.alive)
            rows = s.query('SELECT 99 FROM DUAL')
            vals = [r.strip() for r in rows if r.strip()]
            self.assertIn('99', vals)

    def test_on_error_return(self):
        with _make_session(on_error='return') as s:
            rows = s.query('__FAKE_ORA_ERROR__')
            ora_lines = [r for r in rows if 'ORA-' in r]
            self.assertTrue(len(ora_lines) > 0)

    def test_custom_error_patterns(self):
        with _make_session(error_patterns=[r'CUSTOM-\d+'],
                           on_error='raise') as s:
            # ORA- should NOT be caught by the custom pattern.
            rows = s.query('__FAKE_ORA_ERROR__')
            ora_lines = [r for r in rows if 'ORA-' in r]
            self.assertTrue(len(ora_lines) > 0)


class TestTimeout(unittest.TestCase):
    """Timeout behavior."""

    def test_query_timeout(self):
        with _make_session(default_timeout=1) as s:
            with self.assertRaises(SqlplusTimeout) as ctx:
                s.query('__FAKE_HANG__')
            self.assertFalse(s.alive)

    def test_per_query_timeout_override(self):
        with _make_session(default_timeout=30) as s:
            with self.assertRaises(SqlplusTimeout):
                s.query('__FAKE_HANG__', timeout=1)
            self.assertFalse(s.alive)


class TestProcessDeath(unittest.TestCase):
    """Behavior when sqlplus exits unexpectedly."""

    def test_process_dies_mid_query(self):
        with _make_session() as s:
            with self.assertRaises((SqlplusDied, SqlplusOraError)):
                s.query('__FAKE_DIE__')
            self.assertFalse(s.alive)

    def test_query_after_death_raises(self):
        s = _make_session()
        try:
            s.query('__FAKE_DIE__')
        except (SqlplusDied, SqlplusOraError, SqlplusTimeout):
            pass
        with self.assertRaises(SqlplusDied):
            s.query('SELECT 1 FROM DUAL')
        s.close()


class TestLifecycle(unittest.TestCase):
    """Session lifecycle edge cases."""

    def test_close_then_query(self):
        s = _make_session()
        s.close()
        with self.assertRaises(SqlplusDied):
            s.query('SELECT 1 FROM DUAL')

    def test_alive_property(self):
        s = _make_session()
        self.assertTrue(s.alive)
        s.close()
        self.assertFalse(s.alive)
        # Repeated checks should be stable.
        self.assertFalse(s.alive)


class TestTerminateSQL(unittest.TestCase):
    """The _terminate_sql static method."""

    def test_adds_semicolon(self):
        result = SqlplusSession._terminate_sql('SELECT 1 FROM DUAL')
        self.assertTrue(result.rstrip('\n').endswith(';'))

    def test_preserves_existing_semicolon(self):
        result = SqlplusSession._terminate_sql('SELECT 1 FROM DUAL;')
        # Should not double the semicolon.
        self.assertNotIn(';;', result)

    def test_plsql_block(self):
        block = 'BEGIN\n  NULL;\nEND'
        result = SqlplusSession._terminate_sql(block)
        # Should end with ;  then / on the next line.
        self.assertIn('/', result)

    def test_slash_terminated(self):
        result = SqlplusSession._terminate_sql('BEGIN NULL; END;/')
        self.assertTrue(result.rstrip('\n').endswith('/'))

    def test_empty_sql(self):
        result = SqlplusSession._terminate_sql('')
        self.assertEqual(result, '\n')

    def test_whitespace_only(self):
        result = SqlplusSession._terminate_sql('   ')
        self.assertEqual(result, '\n')

    def test_trailing_newline(self):
        result = SqlplusSession._terminate_sql('SELECT 1 FROM DUAL')
        self.assertTrue(result.endswith('\n'))


class TestSentinelCounter(unittest.TestCase):
    """The sentinel counter increments and prevents stale matches."""

    def test_counter_increments(self):
        with _make_session() as s:
            n_before = s._n
            s.query('SELECT 1 FROM DUAL')
            # The constructor already ran one probe query, so _n > 0.
            self.assertEqual(s._n, n_before + 1)
            s.query('SELECT 2 FROM DUAL')
            self.assertEqual(s._n, n_before + 2)


class TestExceptionHierarchy(unittest.TestCase):
    """All specific exceptions inherit from SqlplusError."""

    def test_ora_error_is_sqlplus_error(self):
        self.assertTrue(issubclass(SqlplusOraError, SqlplusError))

    def test_timeout_is_sqlplus_error(self):
        self.assertTrue(issubclass(SqlplusTimeout, SqlplusError))

    def test_died_is_sqlplus_error(self):
        self.assertTrue(issubclass(SqlplusDied, SqlplusError))

    def test_connect_error_is_sqlplus_error(self):
        self.assertTrue(issubclass(SqlplusConnectError, SqlplusError))

    def test_ora_error_attributes(self):
        exc = SqlplusOraError(['ORA-1'], ['line1', 'ORA-1', 'line3'])
        self.assertEqual(exc.errors, ['ORA-1'])
        self.assertEqual(len(exc.output), 3)

    def test_timeout_attributes(self):
        exc = SqlplusTimeout(['partial'])
        self.assertEqual(exc.output, ['partial'])

    def test_died_attributes(self):
        exc = SqlplusDied(1, ['last line'])
        self.assertEqual(exc.returncode, 1)
        self.assertEqual(exc.output, ['last line'])


class TestSetupCommands(unittest.TestCase):
    """Custom setup_commands."""

    def test_empty_setup(self):
        with _make_session(setup_commands=[]) as s:
            rows = s.query('SELECT 1 FROM DUAL')
            vals = [r.strip() for r in rows if r.strip()]
            self.assertIn('1', vals)

    def test_custom_setup(self):
        with _make_session(setup_commands=['SET LINESIZE 200']) as s:
            rows = s.query('SELECT 1 FROM DUAL')
            vals = [r.strip() for r in rows if r.strip()]
            self.assertIn('1', vals)


class TestEnvironmentDefaults(unittest.TestCase):
    """DB_USERNAME, DB_PASSWORD and DB_NAME are the package's own defaults."""

    VARS = ('DB_USERNAME', 'DB_PASSWORD', 'DB_NAME', 'TWO_TASK', 'ORACLE_SID')

    def setUp(self):
        self.saved = dict((k, os.environ.get(k)) for k in self.VARS)
        for k in self.VARS:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_reads_the_conventional_variables(self):
        os.environ['DB_USERNAME'] = 'envuser'
        os.environ['DB_PASSWORD'] = 'envpw'
        os.environ['DB_NAME'] = 'envtns'
        self.assertEqual(credentials_from_environment(),
                         ('envuser', 'envpw', 'envtns'))

    def test_connect_target_falls_back_in_order(self):
        os.environ['ORACLE_SID'] = 'sid'
        self.assertEqual(credentials_from_environment()[2], 'sid')
        os.environ['TWO_TASK'] = 'two'
        self.assertEqual(credentials_from_environment()[2], 'two')
        os.environ['DB_NAME'] = 'name'
        self.assertEqual(credentials_from_environment()[2], 'name')

    def test_unset_reads_as_empty_not_none(self):
        self.assertEqual(credentials_from_environment(), ('', '', ''))

    def test_none_asks_the_environment(self):
        os.environ['DB_USERNAME'] = 'envuser'
        os.environ['DB_PASSWORD'] = 'envpw'
        os.environ['DB_NAME'] = 'envtns'
        self.assertEqual(resolve_credentials(None, None, None),
                         ('envuser', 'envpw', 'envtns'))

    def test_empty_string_is_an_answer_not_a_question(self):
        # '' selects external authentication and must survive a set
        # DB_USERNAME, or a wallet connection would silently become a
        # password connection.
        os.environ['DB_USERNAME'] = 'envuser'
        os.environ['DB_PASSWORD'] = 'envpw'
        self.assertEqual(resolve_credentials('', '', 'tns'),
                         ('', '', 'tns'))

    def test_explicit_values_win(self):
        os.environ['DB_USERNAME'] = 'envuser'
        self.assertEqual(resolve_credentials('given', 'pw', 'tns'),
                         ('given', 'pw', 'tns'))

    def test_each_argument_resolves_independently(self):
        os.environ['DB_PASSWORD'] = 'envpw'
        os.environ['DB_NAME'] = 'envtns'
        self.assertEqual(resolve_credentials('given', None, None),
                         ('given', 'envpw', 'envtns'))

    def test_session_connects_with_no_arguments(self):
        import shutil
        import tempfile
        d = tempfile.mkdtemp(prefix='sqlplus_env_')
        try:
            seen = os.path.join(d, 'seen')
            os.environ['DB_USERNAME'] = 'envuser'
            os.environ['DB_PASSWORD'] = 'envpw'
            os.environ['DB_NAME'] = 'envtns'
            os.environ['FAKE_SQLPLUS_SEEN'] = seen
            try:
                s = SqlplusSession(sqlplus_cmd=_ensure_wrapper(),
                                   connect_timeout=5, default_timeout=5)
                rows = s.query('SELECT 4 FROM DUAL')
                s.close()
            finally:
                os.environ.pop('FAKE_SQLPLUS_SEEN', None)
            self.assertIn('4', [r.strip() for r in rows if r.strip()])
            with open(seen) as fh:
                lines = fh.read().splitlines()
            self.assertIn('CONNECT envuser@envtns', lines)
            self.assertIn('envpw', lines)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class TestEnvFile(unittest.TestCase):
    """One loader in the package, not one per tool."""

    def setUp(self):
        import tempfile
        self.dir = tempfile.mkdtemp(prefix='sqlplus_envfile_')

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, body, name='env.sh'):
        path = os.path.join(self.dir, name)
        with open(path, 'w') as fh:
            fh.write(body)
        return path

    def test_reads_the_three(self):
        p = self.write('DB_USERNAME=scott\nDB_PASSWORD=tiger\n'
                       'DB_NAME=orcl\nexport DB_USERNAME DB_PASSWORD DB_NAME\n')
        self.assertEqual(load_env_file(p), ('scott', 'tiger', 'orcl'))

    def test_sources_rather_than_parses(self):
        # A parser would return the literal text of the assignment.
        p = self.write('host=box\nport=1521\nsvc=orcl\n'
                       'DB_NAME="${host}:${port}/${svc}"\n')
        self.assertEqual(load_env_file(p)[2], 'box:1521/orcl')

    def test_connect_target_falls_back(self):
        p = self.write('TWO_TASK=fromtwotask\n')
        self.assertEqual(load_env_file(p)[2], 'fromtwotask')
        p = self.write('ORACLE_SID=fromsid\n', 'sid.sh')
        self.assertEqual(load_env_file(p)[2], 'fromsid')

    def test_awkward_password_survives(self):
        # Spaces, a hash, an at sign, a quote: all legal in a password
        # and all things a line parser or a CONNECT line would mangle.
        p = self.write("DB_PASSWORD='a b#c@d\"e'\n")
        self.assertEqual(load_env_file(p)[1], 'a b#c@d"e')

    def test_unset_variables_read_as_empty(self):
        p = self.write('# nothing here\n')
        self.assertEqual(load_env_file(p), ('', '', ''))

    def test_missing_file_raises(self):
        self.assertRaises(IOError, load_env_file,
                          os.path.join(self.dir, 'absent.sh'))

    def test_tilde_is_expanded(self):
        # from_env_file used to expand and load_env_file did not, which
        # made the same path work through one entry point and not the
        # other.
        self.assertRaises(IOError, load_env_file, '~/no_such_env_file_xyzzy')

    def test_script_clears_every_variable_it_reads(self):
        # A name added to the constants but not to the unset list would
        # let the caller's environment show through as the file's.
        import sqlplus_session.session as _s
        first = _s._ENV_FILE_SCRIPT.splitlines()[0].split()
        self.assertEqual(first[0], 'unset')
        for name in (_s.ENV_USERNAME, _s.ENV_PASSWORD) + tuple(_s.ENV_CONNECT):
            self.assertIn(name, first[1:])
            self.assertIn(name, _s._ENV_FILE_SCRIPT.splitlines()[-1])

    def test_unsourceable_file_raises(self):
        p = self.write('exit 7\n')
        self.assertRaises(ValueError, load_env_file, p)

    def test_from_env_file_opens_a_session(self):
        seen = os.path.join(self.dir, 'seen')
        p = self.write('DB_USERNAME=filer\nDB_PASSWORD=filepw\n'
                       'DB_NAME=filetns\n')
        os.environ['FAKE_SQLPLUS_SEEN'] = seen
        try:
            s = SqlplusSession.from_env_file(
                p, sqlplus_cmd=_ensure_wrapper(),
                connect_timeout=5, default_timeout=5)
            rows = s.query('SELECT 6 FROM DUAL')
            s.close()
        finally:
            os.environ.pop('FAKE_SQLPLUS_SEEN', None)
        self.assertIn('6', [r.strip() for r in rows if r.strip()])
        with open(seen) as fh:
            lines = fh.read().splitlines()
        self.assertIn('CONNECT filer@filetns', lines)
        self.assertIn('filepw', lines)


class TestCredentialExposure(unittest.TestCase):
    """The password must never reach the command line."""

    def setUp(self):
        import tempfile
        self.dir = tempfile.mkdtemp(prefix='sqlplus_cred_')
        self.argv = os.path.join(self.dir, 'argv')
        self.seen = os.path.join(self.dir, 'seen')

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def _env(self, **extra):
        e = dict(os.environ)
        e['FAKE_SQLPLUS_ARGV'] = self.argv
        e['FAKE_SQLPLUS_SEEN'] = self.seen
        e.update(extra)
        return e

    def _read(self, path):
        if not os.path.exists(path):
            return ''
        with open(path) as fh:
            return fh.read()

    def test_login_is_nolog_and_argv_holds_no_secret(self):
        with _make_session(password='s3kr1t-pw', env=self._env()) as s:
            s.query('SELECT 1 FROM DUAL')
        argv = self._read(self.argv)
        self.assertIn('/nolog', argv)
        self.assertNotIn('s3kr1t-pw', argv)
        self.assertNotIn('test/', argv)

    def test_password_travels_on_stdin(self):
        with _make_session(password='s3kr1t-pw', env=self._env()):
            pass
        seen = self._read(self.seen).splitlines()
        self.assertIn('CONNECT test@fake', seen)
        self.assertIn('s3kr1t-pw', seen)
        # It is its own line, not part of the CONNECT.
        self.assertFalse(any('CONNECT' in l and 's3kr1t-pw' in l
                             for l in seen))

    def test_special_characters_need_no_quoting(self):
        # @ / " and a trailing # all break a CONNECT line if the
        # password is written on it.  On its own line they are literal.
        pw = 'p@ss/w"rd#'
        with _make_session(password=pw, env=self._env()) as s:
            rows = s.query('SELECT 5 FROM DUAL')
            self.assertIn('5', [r.strip() for r in rows if r.strip()])
        self.assertIn(pw, self._read(self.seen).splitlines())
        self.assertNotIn(pw, self._read(self.argv))

    def test_password_prompt_does_not_wedge_the_reader(self):
        # An interactive sqlplus writes "Enter password: " with no
        # newline, so it arrives glued to the head of the next line.
        env = self._env(FAKE_SQLPLUS_PROMPT='1')
        with _make_session(password='pw', env=env) as s:
            rows = s.query('SELECT 3 FROM DUAL')
            self.assertIn('3', [r.strip() for r in rows if r.strip()])

    def test_external_auth_sends_no_password_line(self):
        with _make_session(username='', password='', env=self._env()) as s:
            self.assertTrue(s.alive)
        seen = self._read(self.seen).splitlines()
        self.assertIn('CONNECT /@fake', seen)

    def test_rejected_login_raises_without_echoing_the_secret(self):
        env = self._env(FAKE_SQLPLUS_BADPW='1')
        with self.assertRaises(SqlplusConnectError) as ctx:
            _make_session(password='s3kr1t-pw', env=env)
        msg = str(ctx.exception)
        self.assertIn('ORA-01017', msg)
        self.assertNotIn('s3kr1t-pw', msg)


def tearDownModule():
    """Clean up the wrapper script."""
    global _WRAPPER
    if _WRAPPER and os.path.exists(_WRAPPER):
        os.unlink(_WRAPPER)
        _WRAPPER = None


if __name__ == '__main__':
    unittest.main()
