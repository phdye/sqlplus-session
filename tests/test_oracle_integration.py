"""Integration suite: SqlplusSession against a real Oracle database.

Requires a live sqlplus and a reachable instance, so it is not part of
the default unit run.  Credentials come from the command line or the
environment; see ``conftest.py``.

    pytest tests/test_oracle_integration.py --tns orclpdb1
    pytest tests/test_oracle_integration.py --env-file ~/.dbenv
    DB_NAME=orclpdb1 pytest tests/test_oracle_integration.py

Timings live in ``tools/benchmark.py``, not here.  A test that asserts
on wall-clock is a test that fails on a busy machine, and those numbers
are worth reporting rather than merely passing.
"""

import os

import pytest

from sqlplus_session import (
    SqlplusDied,
    SqlplusOraError,
    SqlplusSession,
)

MISSING_TABLE = 'nonexistent_table_xyzzy_99'


def value_of(rows):
    """The single value in *rows*, blank lines and padding discarded."""
    return ''.join(r.strip() for r in rows if r.strip())


class TestConnection:

    def test_session_is_alive_after_construction(self, session):
        assert session.alive

    def test_identity_query_returns_a_username(self, session):
        assert value_of(session.query('SELECT user FROM dual'))

    def test_setup_commands_were_applied(self, session):
        # PAGESIZE 0 and HEADING OFF are in the default setup. Without
        # them there would be a column header and a rule here.
        rows = [r for r in session.query('SELECT 1 FROM dual') if r.strip()]
        assert len(rows) == 1
        assert rows[0].strip() == '1'


class TestQuery:

    @pytest.mark.parametrize('n', [0, 1, 42, 999999])
    def test_literal_round_trips(self, session, n):
        assert value_of(session.query('SELECT %d FROM DUAL' % n)) == str(n)

    def test_fifty_queries_stay_in_step(self, session):
        # The sentinel is numbered precisely so answers cannot slide by
        # one. Fifty in a row catches that if it ever regresses.
        for i in range(50):
            assert value_of(session.query('SELECT %d FROM DUAL' % i)) == str(i)

    def test_multi_statement_returns_every_result(self, session):
        rows = session.query('SELECT 111 FROM DUAL;\nSELECT 222 FROM DUAL')
        values = [r.strip() for r in rows if r.strip()]
        assert '111' in values
        assert '222' in values

    def test_interior_spaces_survive(self, session):
        assert 'a b  c' in ''.join(session.query("SELECT 'a b  c' FROM dual"))


class TestErrors:

    def test_missing_table_raises(self, session):
        with pytest.raises(SqlplusOraError) as excinfo:
            session.query('SELECT * FROM %s' % MISSING_TABLE)
        assert any('ORA-00942' in line for line in excinfo.value.errors)

    def test_session_survives_the_error(self, session):
        # WHENEVER SQLERROR CONTINUE is what makes this true. If it
        # regressed the session would be dead and every later test in
        # this module would fail with it.
        try:
            session.query('SELECT * FROM %s' % MISSING_TABLE)
        except SqlplusOraError:
            pass
        assert session.alive
        assert value_of(session.query('SELECT 42 FROM DUAL')) == '42'

    def test_on_error_return_hands_back_the_error_lines(self, open_session):
        s = open_session(on_error='return')
        rows = s.query('SELECT * FROM %s' % MISSING_TABLE)
        assert any('ORA-' in line for line in rows)

    def test_custom_patterns_replace_the_defaults(self, open_session):
        # ORA- is not in this list, so the error must come back as
        # ordinary output rather than raising.
        s = open_session(error_patterns=[r'ZZZ-\d'])
        rows = s.query('SELECT * FROM %s' % MISSING_TABLE)
        assert any('ORA-00942' in line for line in rows)


class TestRunFile:

    def test_runs_a_sql_file(self, open_session, tmpdir):
        path = str(tmpdir.join('probe.sql'))
        with open(path, 'w') as fh:
            fh.write('SELECT 4242 FROM dual;\n')
        assert '4242' in ''.join(open_session().run_file(path))

    def test_errors_in_a_file_are_scanned_too(self, open_session, tmpdir):
        path = str(tmpdir.join('bad.sql'))
        with open(path, 'w') as fh:
            fh.write('SELECT * FROM %s;\n' % MISSING_TABLE)
        with pytest.raises(SqlplusOraError):
            open_session().run_file(path)


class TestLifecycle:

    def test_close_then_query_raises(self, open_session):
        s = open_session()
        s.query('SELECT 1 FROM DUAL')
        s.close()
        assert not s.alive
        with pytest.raises(SqlplusDied):
            s.query('SELECT 1 FROM DUAL')

    def test_close_is_idempotent(self, open_session):
        s = open_session()
        s.close()
        s.close()
        assert not s.alive

    def test_context_manager_closes(self, credentials, sqlplus_cmd, run_env,
                                    path_converter):
        username, password, connect = credentials
        with SqlplusSession(username, password, connect,
                            sqlplus_cmd=sqlplus_cmd, env=run_env,
                            path_converter=path_converter) as s:
            assert s.alive
        assert not s.alive


class TestCredentialExposure:
    """The property the whole connect design exists to provide."""

    def test_nothing_secret_reached_the_command_line(self, session):
        # Read it back off the running process rather than trusting the
        # code that built it.
        path = '/proc/%d/cmdline' % session._proc.pid
        if not os.path.exists(path):
            pytest.skip('no /proc/<pid>/cmdline on this platform')
        with open(path, 'rb') as fh:
            argv = fh.read().decode('utf-8', 'replace').split('\0')
        argv = [a for a in argv if a]
        assert '/nolog' in argv
        # A login string would look like user/pw@tns or /@tns.
        assert not [a for a in argv if '@' in a], argv
