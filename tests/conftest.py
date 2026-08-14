"""Options and fixtures for the Oracle integration suite.

Credentials follow the precedence the package already implements: what
you pass here wins, and whatever you leave out falls through to
sqlplus_session's own environment resolution -- ``DB_USERNAME``,
``DB_PASSWORD``, and ``DB_NAME`` or ``TWO_TASK`` or ``ORACLE_SID``.

No test reads one of those variables itself.  ``resolve_credentials()``
is the single place that knows what they are called, which is the point
of it living in the package rather than in each tool.

An unset option is ``None``, which is what makes the fall-through work:
``None`` asks the environment, ``''`` states an answer (external
authentication).  Passing ``--user ''`` therefore selects a wallet or OS
connection even where ``DB_USERNAME`` is exported.
"""

import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from sqlplus_session import (          # noqa: E402  (path set above)
    ENV_CONNECT,
    ENV_PASSWORD,
    ENV_USERNAME,
    load_env_file,
    resolve_credentials,
)

# Runtime variables sqlplus needs to find its own libraries.  Credentials
# are deliberately not in this list: those come from the package.
_RUNTIME_VARS = ('ORACLE_HOME', 'PATH', 'LD_LIBRARY_PATH', 'TNS_ADMIN',
                 'NLS_LANG', 'NLS_DATE_FORMAT')


def pytest_addoption(parser):
    g = parser.getgroup('oracle', 'Oracle integration suite')
    g.addoption('--user', action='store', default=None, metavar='NAME',
                help='Oracle username; empty string selects external '
                     'authentication. Overrides $%s.' % ENV_USERNAME)
    g.addoption('--password', action='store', default=None, metavar='PW',
                help='Oracle password. Overrides $%s. Prefer --env-file '
                     'or the environment; this lands in your shell '
                     'history.' % ENV_PASSWORD)
    g.addoption('--tns', action='store', default=None, metavar='ALIAS',
                help='TNS alias or Easy Connect string. Overrides $%s.'
                     % ', $'.join(ENV_CONNECT))
    g.addoption('--env-file', action='store', default=None, metavar='PATH',
                help='Shell file to source for credentials and for the '
                     'runtime variables sqlplus needs. Command-line '
                     'options still win over it.')
    g.addoption('--sqlplus', action='store', default='sqlplus',
                metavar='PATH', help='sqlplus binary. Default: sqlplus.')


def _missing_target_message():
    """One line, because pytest repeats it once per test.

    It still names the variables and the option, which is the part that
    matters; ``pytest --help`` carries the rest.
    """
    return ('No Oracle connect target: set %s, or pass --tns ALIAS or '
            '--env-file PATH.'
            % ' or '.join('$' + name for name in ENV_CONNECT))


def _missing_sqlplus_message(cmd):
    return ('sqlplus not found: %r -- pass --sqlplus PATH or put it on '
            '$PATH.' % cmd)


def _which(cmd):
    if os.path.isabs(cmd) or os.sep in cmd:
        return cmd if os.path.exists(cmd) else None
    for d in (os.environ.get('PATH') or '').split(os.pathsep):
        if d and os.path.exists(os.path.join(d, cmd)):
            return os.path.join(d, cmd)
    return None


@pytest.fixture(scope='session')
def credentials(request):
    """``(username, password, connect_string)``, options over environment.

    Fails the run rather than skipping it: a suite that quietly does
    nothing when the database is unreachable is a suite that reports
    success for work it did not do.
    """
    given = (request.config.getoption('--user'),
             request.config.getoption('--password'),
             request.config.getoption('--tns'))

    env_file = request.config.getoption('--env-file')
    if env_file:
        # The file fills only what the command line left alone.
        from_file = load_env_file(env_file)
        given = tuple(opt if opt is not None else val
                      for opt, val in zip(given, from_file))

    # None still means "ask the environment"; the package does that.
    username, password, connect = resolve_credentials(*given)

    if not connect:
        pytest.fail(_missing_target_message(), pytrace=False)
    return username, password, connect


@pytest.fixture(scope='session')
def sqlplus_cmd(request):
    cmd = request.config.getoption('--sqlplus')
    if _which(cmd) is None:
        pytest.fail(_missing_sqlplus_message(cmd), pytrace=False)
    return cmd


@pytest.fixture(scope='session')
def run_env(request):
    """Environment for the sqlplus process.

    An env file is sourced for ORACLE_HOME and friends, because sqlplus
    needs them to find its own shared libraries.  Credentials are not
    taken from here -- the *credentials* fixture owns those.
    """
    env = dict(os.environ)
    env_file = request.config.getoption('--env-file')
    if not env_file:
        return env

    devnull = open(os.devnull, 'w')
    try:
        proc = subprocess.Popen(['sh', '-c', '. "$1" ; env', 'sh', env_file],
                                stdout=subprocess.PIPE, stderr=devnull,
                                universal_newlines=True)
        out, _ = proc.communicate()
    finally:
        devnull.close()

    sourced = {}
    for line in out.splitlines():
        eq = line.find('=')
        if eq > 0:
            sourced[line[:eq]] = line[eq + 1:]
    for name in _RUNTIME_VARS:
        if name in sourced:
            env[name] = sourced[name]
    return env


@pytest.fixture(scope='session')
def open_session(credentials, sqlplus_cmd, run_env, path_converter):
    """Factory for sessions against the configured instance.

    Session-scoped so the connect cost is paid once per run rather than
    once per test, and so every session gets closed even if a test
    leaves one open.
    """
    from sqlplus_session import SqlplusSession

    username, password, connect = credentials
    created = []

    def factory(**kwargs):
        opts = dict(sqlplus_cmd=sqlplus_cmd, env=run_env,
                    path_converter=path_converter)
        opts.update(kwargs)
        s = SqlplusSession(username, password, connect, **opts)
        created.append(s)
        return s

    yield factory

    for s in created:
        s.close()


@pytest.fixture(scope='session')
def session(open_session):
    """One shared session for the tests that do not disturb it."""
    return open_session()


@pytest.fixture(scope='session')
def path_converter():
    """``cygpath -m`` on Cygwin, ``None`` elsewhere.

    Windows sqlplus cannot open a POSIX path, so ``run_file`` needs the
    mixed form.  Nothing else in the suite cares.
    """
    import platform
    if 'cygwin' not in platform.system().lower():
        return None

    def convert(path):
        try:
            out = subprocess.check_output(['cygpath', '-m', path],
                                          universal_newlines=True)
            return out.strip()
        except (OSError, subprocess.CalledProcessError):
            return path

    return convert
