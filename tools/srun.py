#!/usr/bin/env python3
"""Run a SQL*Plus script, with nothing secret on the command line.

Usage:
  srun.py [options] [--] <script> [<arg>...]
  srun.py (-h | --help)
  srun.py (-V | --version)

Arguments:
  <script>  SQL*Plus script to run. Made absolute, and on Cygwin
            converted to the mixed Windows form, so that a native
            sqlplus.exe can open it.
  <arg>     Positional parameter for the script, which reaches it as &1,
            &2 and so on. Everything after <script> belongs to the
            script, whatever it looks like, so a parameter starting with
            a dash needs no escaping; -- before <script> is there for a
            script path that starts with one.

Options:
  -u NAME, --user=NAME           Oracle username. An empty string selects
                                 external authentication -- wallet or OS
                                 -- and the password is then ignored.
                                 Overrides $DB_USERNAME.
  -T ALIAS, --tns=ALIAS          TNS alias or Easy Connect string.
                                 Overrides $DB_NAME, $TWO_TASK and
                                 $ORACLE_SID.
  -P PATH, --password-file=PATH  Read the password from the first line of
                                 PATH. There is no --password: an
                                 argument is visible to every other user
                                 through ps, and it lands in shell
                                 history besides.
  -f PATH, --env-file=PATH       Shell file to source, both for the
                                 credentials and for the variables
                                 sqlplus needs to find its own libraries.
                                 The options above still win over it.
  -s PATH, --sqlplus=PATH        sqlplus binary [default: sqlplus].
  -C DIR, --directory=DIR        Run as if started in DIR.
  -o FILE, --output=FILE         Where the script's output goes; - is
                                 stdout [default: -].
  -w SEC, --timeout=SEC          Give up on the script after SEC seconds.
                                 0 waits as long as it takes
                                 [default: 0].
  -e, --fail-on-error            Exit 1 when the output carries an ORA-,
                                 TNS- or SP2- line. On by default.
      --no-fail-on-error         Print what sqlplus said and exit 0
                                 regardless, as the shell script did.
  -n, --dry-run                  Report the connection and the line that
                                 would be sent; connect to nothing.
      --no-dry-run               Run it, which is the default.
  -q, --quiet                    Errors and nothing else.
      --no-quiet                 Undo a --quiet from the environment.
  -t, --terse                    Errors without the line of advice that
                                 usually follows them.
      --no-terse                 Undo a --terse from the environment.
  -v, --verbose                  Name the target, the binary and the
                                 script before running. Repeatable.
      --no-verbose               Undo a --verbose from the environment.
  -d, --debug                    Traceback on failure; implies --verbose.
      --no-debug                 Undo a --debug from the environment.
  -h, --help                     Show this message.
  -V, --version                  Show the package version.

Volume and format are separate axes, and this tool has only the first:
the script's output is whatever sqlplus printed, reproduced byte for
byte. -q, -t, -v and -d govern what srun.py itself says about the run,
which goes to stderr; the script's output goes to stdout and is not
touched at any volume.

Environment:
  Every option above has an environment variable, SRUN_ followed by the
  long name in upper case with dashes as underscores -- SRUN_SQLPLUS,
  SRUN_TIMEOUT, SRUN_FAIL_ON_ERROR, SRUN_OUTPUT. A boolean takes 1, true,
  yes or on for true and 0, false, no or off for false; anything else is
  a usage error naming the variable.

  The three credentials are the deliberate exception. Their environment
  channel is the package's own -- $DB_USERNAME, $DB_PASSWORD, and
  $DB_NAME or $TWO_TASK or $ORACLE_SID -- so that every tool here reads
  one set of names rather than two, and there is no SRUN_USER or
  SRUN_TNS to disagree with them. $DB_PASSWORD and --password-file are
  the only two ways in for a password.

  Precedence for srun.py's own settings is the option, then the
  environment variable, then the default. For the credentials it is the
  option, then --env-file, then the environment: a file named on the
  command line is a deliberate act and outranks whatever the calling
  shell happens to export. No config files are read.

Exit status:
  0  the script ran and nothing in its output looked like an error
  1  the connection failed, sqlplus died or timed out, the script could
     not be read, or its output carried an error line while
     --fail-on-error was in force
  2  a bad option, a bad environment value, or no connect target

This replaces a shell script that put user/password@tns on the sqlplus
command line, where ps and any process accounting could read it. Here
sqlplus starts as `sqlplus -s /nolog` and the credential goes down the
same stdin pipe the script does, so nothing identifying reaches the
process table. Two other differences are deliberate: the old script
exited 0 whatever Oracle said, and it echoed its command line when its
second argument happened to be -v.
"""

import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                '..'))

from sqlplus_session import (            # noqa: E402  (path set above)
    ENV_CONNECT,
    SqlplusConnectError,
    SqlplusDied,
    SqlplusOraError,
    SqlplusSession,
    SqlplusTimeout,
    __version__,
    load_env_file,
    resolve_credentials,
)
from sqlplus_session.session import _quote_script_arg   # noqa: E402

PROG = 'SRUN'

# Runtime variables sqlplus needs to find its own shared libraries.
# Credentials are deliberately not in this list; the package owns those.
_RUNTIME_VARS = ('ORACLE_HOME', 'PATH', 'LD_LIBRARY_PATH', 'TNS_ADMIN',
                 'NLS_LANG', 'NLS_DATE_FORMAT')

# --timeout 0 means "as long as it takes", and the session wants a
# number.  A year is not a limit anyone will reach; infinity is not
# available, because a lock acquired with it raises OverflowError rather
# than waiting.
NO_TIMEOUT = 365 * 24 * 3600

# One table drives the option splitter, the parser, the environment
# names and the test that compares all three against the Usage block
# above.  The Usage block stays the specification; this is what keeps a
# second hand-maintained list from drifting away from it.
#
# Fields: short, long, metavar, dest, kind, default.  A metavar of None
# means the option takes no value, and every such option gets a --no-
# twin.
_SPEC = (
    ('-u', '--user', 'NAME', 'user', 'value', None),
    ('-T', '--tns', 'ALIAS', 'tns', 'value', None),
    ('-P', '--password-file', 'PATH', 'password_file', 'value', None),
    ('-f', '--env-file', 'PATH', 'env_file', 'value', None),
    ('-s', '--sqlplus', 'PATH', 'sqlplus', 'value', 'sqlplus'),
    ('-C', '--directory', 'DIR', 'directory', 'value', None),
    ('-o', '--output', 'FILE', 'output', 'value', '-'),
    ('-w', '--timeout', 'SEC', 'timeout', 'value', '0'),
    ('-e', '--fail-on-error', None, 'fail_on_error', 'flag', True),
    ('-n', '--dry-run', None, 'dry_run', 'flag', False),
    ('-q', '--quiet', None, 'quiet', 'flag', False),
    ('-t', '--terse', None, 'terse', 'flag', False),
    ('-v', '--verbose', None, 'verbose', 'count', 0),
    ('-d', '--debug', None, 'debug', 'flag', False),
)

# The credentials answer to the package's variables instead, so that two
# names cannot describe one setting.
_NO_ENV_VAR = frozenset(('user', 'tns'))

QUIET, TERSE, NORMAL, VERBOSE, DEBUG = range(5)

_TRUE = ('1', 'true', 'yes', 'on')
_FALSE = ('0', 'false', 'no', 'off')


class Usage(Exception):
    """A bad invocation: exit 2, say what was wrong, say nothing else."""

    def __init__(self, message, hint=None):
        super(Usage, self).__init__(message)
        self.hint = hint


class Failed(Exception):
    """The run did not produce an answer.  Exit 1."""

    def __init__(self, message, hint=None):
        super(Failed, self).__init__(message)
        self.hint = hint


def _takes_value():
    """Which option spellings consume the token after them."""
    table = {}
    for short, long_, metavar, dest, kind, default in _SPEC:
        table[short] = metavar is not None
        table[long_] = metavar is not None
        if metavar is None:
            table['--no-' + long_[2:]] = False
    table['-h'] = table['--help'] = False
    table['-V'] = table['--version'] = False
    return table


def split_argv(argv):
    """Divide *argv* into srun's own options, the script, and its arguments.

    argparse cannot do this.  It would read a dash-leading token after
    the script as one of ours, and a SQL*Plus positional parameter is
    data: `srun.py report.sql -30` asks for thirty of something, not for
    an option nobody defined.  So the split happens first, on the rule
    that the first token which is not an option or an option's value is
    the script and everything past it is the script's.

    Short bundles are expanded on the way through, so what comes back is
    one token per option and per value.

    Returns ``(option_tokens, script, script_args)`` with *script* None
    where the command line held no script at all -- which is legal, for
    --help and --version, and a usage error otherwise.
    """
    takes_value = _takes_value()
    opts = []
    i = 0
    while i < len(argv):
        token = argv[i]
        if token == '--':
            i += 1
            break
        if token.startswith('--'):
            opts.append(token)
            i += 1
            name = token.split('=', 1)[0]
            if takes_value.get(name) and '=' not in token and i < len(argv):
                opts.append(argv[i])
                i += 1
            continue
        if len(token) > 1 and token[0] == '-':
            i += 1
            # A bundle ends at its first value-taking letter: -vw30 is
            # -v -w 30, and there is no reading under which 30v is an
            # option.  Expanded here rather than left for argparse,
            # whose own bundling stops working when abbreviation is
            # turned off, and turning abbreviation off is what keeps
            # --ver from resolving to an option nobody wrote.
            for pos in range(1, len(token)):
                letter = '-' + token[pos]
                opts.append(letter)
                if takes_value.get(letter):
                    if pos + 1 < len(token):
                        opts.append(token[pos + 1:])
                    elif i < len(argv):
                        opts.append(argv[i])
                        i += 1
                    break
            continue
        break
    if i >= len(argv):
        return opts, None, []
    return opts, argv[i], list(argv[i + 1:])


class _Parser(argparse.ArgumentParser):
    """argparse wearing the docopt usage above, since docopt is absent.

    Its own error path exits 2 and prints a usage dump nobody reads;
    this one raises, so every failure in the tool leaves by one door.
    """

    def error(self, message):
        raise Usage(message, 'Try --help.')


def build_parser():
    """A parser for srun's own options, and nothing positional.

    The positionals are settled by :func:`split_argv` before this sees
    anything, which is why they are absent here.
    """
    p = _Parser(prog='srun.py', add_help=False, allow_abbrev=False,
                usage=__doc__)
    for short, long_, metavar, dest, kind, default in _SPEC:
        if kind == 'value':
            p.add_argument(short, long_, dest=dest, metavar=metavar,
                           default=argparse.SUPPRESS)
            continue
        if kind == 'count':
            p.add_argument(short, long_, dest=dest, action='count',
                           default=argparse.SUPPRESS)
            p.add_argument('--no-' + long_[2:], dest=dest,
                           action='store_const', const=0,
                           default=argparse.SUPPRESS)
            continue
        p.add_argument(short, long_, dest=dest, action='store_const',
                       const=True, default=argparse.SUPPRESS)
        p.add_argument('--no-' + long_[2:], dest=dest,
                       action='store_const', const=False,
                       default=argparse.SUPPRESS)
    p.add_argument('-h', '--help', dest='help', action='store_const',
                   const=True, default=False)
    p.add_argument('-V', '--version', dest='version', action='store_const',
                   const=True, default=False)
    return p


def env_name(dest):
    """``SRUN_FAIL_ON_ERROR`` for ``fail_on_error``, or None where the
    setting answers to the package's variables instead."""
    if dest in _NO_ENV_VAR:
        return None
    return '%s_%s' % (PROG, dest.upper())


def _boolean(text, source):
    lowered = text.strip().lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise Usage('%s: expected one of %s or %s, got %r'
                % (source, '/'.join(_TRUE), '/'.join(_FALSE), text))


def settings(option_tokens, environ=None):
    """Resolve every setting: option, then environment variable, then default.

    Returns a plain dict keyed by dest, plus ``help`` and ``version``.
    An option absent from the command line is absent from the parsed
    namespace rather than present as its default, which is the whole
    reason the parser suppresses defaults -- otherwise there is no way
    to tell `-s sqlplus` from not having said so, and the environment
    could never win.
    """
    environ = os.environ if environ is None else environ
    parsed = vars(build_parser().parse_args(option_tokens))

    out = {'help': parsed.pop('help', False),
           'version': parsed.pop('version', False)}
    for short, long_, metavar, dest, kind, default in _SPEC:
        if dest in parsed:
            out[dest] = parsed[dest]
            continue
        name = env_name(dest)
        raw = environ.get(name) if name else None
        if raw is None or raw == '':
            out[dest] = default
        elif kind == 'value':
            out[dest] = raw
        elif kind == 'count':
            out[dest] = _count_from_env(raw, '$' + name)
        else:
            out[dest] = _boolean(raw, '$' + name)
    return out


def _count_from_env(raw, source):
    """A repeatable option through a variable: a level, or a boolean word."""
    text = raw.strip()
    if text.isdigit():
        return int(text)
    return 1 if _boolean(text, source) else 0


def volume(setting):
    """One level out of the four flags, loudest winning.

    Loudest-wins is right within a source and wrong across them, so the
    precedence has already happened in :func:`settings`: by the time
    this runs, each flag holds whichever source outranked the others.
    The order of the four assignments below is the ladder itself.
    """
    level = NORMAL
    if setting['quiet']:
        level = QUIET
    if setting['terse']:
        level = TERSE
    if setting['verbose']:
        level = VERBOSE
    if setting['debug']:
        level = DEBUG
    return level


def is_cygwin():
    import platform
    return 'cygwin' in platform.system().lower()


def cygpath_m(path):
    """The mixed Windows form, which a native sqlplus.exe can open.

    The shell script this replaces ran `cygpath -m -a`; making the path
    absolute has moved up into the caller, so that --directory can be
    honored first and so a failure names a path the caller recognizes.
    """
    try:
        return subprocess.check_output(['cygpath', '-m', path],
                                       universal_newlines=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return path


def source_runtime_vars(env_file, environ=None):
    """ORACLE_HOME and friends out of *env_file*.

    Credentials do not come from here.  The package sources the same
    file for those, through its own subshell, and one of the two has to
    own it.
    """
    env = dict(os.environ if environ is None else environ)
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
    for line in out.splitlines():
        eq = line.find('=')
        if eq > 0 and line[:eq] in _RUNTIME_VARS:
            env[line[:eq]] = line[eq + 1:]
    return env


def read_password_file(path):
    """The first line of *path*, its line ending removed.

    Read as bytes and decoded here rather than opened in text mode: a
    password file written on Windows carries CRLF, and a trailing \\r
    that survives into the CONNECT line is rejected by Oracle as a wrong
    password, which is an expensive thing to debug.  Only the ending is
    stripped -- a password is allowed to end in a space.
    """
    expanded = os.path.expanduser(path)
    try:
        with open(expanded, 'rb') as fh:
            first = fh.readline()
    except (IOError, OSError) as exc:
        raise Usage('--password-file %s: %s' % (path, exc.strerror or exc))
    return first.decode('utf-8', 'replace').rstrip('\r\n')


def password_file_is_exposed(path):
    """True where the file is readable by group or other.

    Reported, never enforced: refusing to read it would strand whoever
    is on a filesystem that cannot express the modes, and saying nothing
    is how a credential stays world-readable for a year.
    """
    try:
        mode = os.stat(os.path.expanduser(path)).st_mode
    except OSError:
        return False
    return bool(mode & 0o044)


def resolve_timeout(raw):
    """Seconds, with 0 meaning no practical limit."""
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        raise Usage('--timeout: expected a number of seconds, got %r' % (raw,))
    if seconds < 0:
        raise Usage('--timeout: expected a number of seconds, got %r' % (raw,))
    return NO_TIMEOUT if seconds == 0 else seconds


def credentials(setting):
    """``(username, password, connect_string)``, by the documented order.

    An empty value out of the environment file is treated as one the
    file did not set, so that $DB_USERNAME still shows through.  The
    subshell cannot tell an unset variable from one set to the empty
    string, and the second reading would make an env file that mentions
    none of the three silently select external authentication.
    """
    password = None
    if setting['password_file']:
        password = read_password_file(setting['password_file'])
    given = (setting['user'], password, setting['tns'])

    if setting['env_file']:
        try:
            from_file = load_env_file(setting['env_file'])
        except (IOError, OSError, ValueError) as exc:
            raise Usage('--env-file %s: %s' % (setting['env_file'], exc))
        given = tuple(opt if opt is not None else (val or None)
                      for opt, val in zip(given, from_file))

    return resolve_credentials(*given)


def _no_target_message():
    return ('No Oracle connect target.',
            'Set %s, or pass --tns ALIAS or --env-file PATH.'
            % ' or '.join('$' + name for name in ENV_CONNECT))


def describe_connection(user, tns):
    """The login as it would be written, with the password not in it."""
    if not user:
        return '/@%s  (external authentication)' % tns
    return '%s/--password--@%s' % (user, tns)


def at_line(script_path, script_args):
    """The line srun will send, quoted the way the session quotes it.

    Built from the package's own quoting rather than a second copy, so
    that --dry-run cannot describe one thing and the run send another.
    """
    line = '@%s' % script_path
    if script_args:
        line += ' ' + ' '.join(_quote_script_arg(a) for a in script_args)
    return line


def emit(lines, destination):
    """The script's output, verbatim, to stdout or to a file.

    Written as LF whatever the platform would prefer: this output is
    read by awk and by diff at least as often as by a person, and a
    file that gained CRLF on the way out is a difference nobody asked
    for.
    """
    if destination == '-':
        for line in lines:
            sys.stdout.write(line + '\n')
        sys.stdout.flush()
        return
    with open(destination, 'w', encoding='utf-8', errors='replace',
              newline='\n') as fh:
        for line in lines:
            fh.write(line + '\n')


def run_script(user, password, tns, script_path, script_args, setting):
    """Run the script.  Returns ``(lines, error_lines, failure)``.

    A failure comes back rather than being raised, because whatever
    sqlplus managed to print before it is still the answer to the
    question that was asked, and the caller writes that out first.  A
    timeout in particular is worth reading: the partial output is
    usually where the statement that hung is named.
    """
    timeout = resolve_timeout(setting['timeout'])
    env = source_runtime_vars(setting['env_file'])
    try:
        session = SqlplusSession(user, password, tns,
                                 sqlplus_cmd=setting['sqlplus'],
                                 env=env, setup_commands=[],
                                 default_timeout=timeout)
    except SqlplusConnectError as exc:
        return [], [], Failed(
            'cannot connect as %s: %s' % (describe_connection(user, tns), exc),
            'Check the credentials and that %s can reach the instance.'
            % setting['sqlplus'])

    try:
        try:
            lines = session.run_file(script_path, script_args)
            return lines, [], None
        except SqlplusOraError as exc:
            return exc.output, exc.errors, None
        except SqlplusDied as exc:
            # Plenty of scripts end in EXIT, which takes sqlplus down
            # before the sentinel comes back.  A clean exit code says
            # that is what happened; anything else is a crash, and the
            # output is then evidence rather than a result.
            if exc.returncode:
                return exc.output, [], Failed(
                    'sqlplus exited with code %s' % exc.returncode,
                    'The output above is everything it printed first.')
            return exc.output, session.errors_in(exc.output), None
        except SqlplusTimeout as exc:
            return exc.output, [], Failed(
                'the script did not finish within %s seconds'
                % setting['timeout'],
                'Raise --timeout, or pass --timeout 0 to wait indefinitely.')
        except ValueError as exc:
            # A script argument the @ line cannot carry.  Nothing ran.
            return [], [], Failed(str(exc))
    finally:
        session.close()


def _report(message, hint, level):
    sys.stderr.write('%s\n' % message)
    if hint and level >= NORMAL:
        sys.stderr.write('%s\n' % hint)


# Set as soon as the volume is known, so that a failure raised later can
# be reported at the volume the caller asked for.  A failure raised
# before then -- a bad option, a bad environment value -- is reported at
# normal volume, which is the only safe reading when the setting that
# would have quieted it is the one that could not be read.
_LEVEL = [NORMAL]


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    option_tokens, script, script_args = split_argv(argv)
    setting = settings(option_tokens)

    if setting['help']:
        sys.stdout.write(__doc__)
        return 0
    if setting['version']:
        sys.stdout.write('sqlplus-session %s\n' % __version__)
        return 0
    if script is None:
        raise Usage('no script given',
                    'Usage: srun.py [options] <script> [<arg>...]')

    level = volume(setting)
    _LEVEL[0] = level

    if setting['directory']:
        try:
            os.chdir(os.path.expanduser(setting['directory']))
        except OSError as exc:
            raise Usage('--directory %s: %s'
                        % (setting['directory'], exc.strerror or exc))

    script_path = os.path.abspath(os.path.expanduser(script))
    if not os.path.isfile(script_path):
        raise Failed('cannot read script: %s' % script,
                     'Looked at %s.' % script_path)

    user, password, tns = credentials(setting)
    if not tns:
        raise Usage(*_no_target_message())
    if setting['password_file'] and level >= NORMAL \
            and password_file_is_exposed(setting['password_file']):
        sys.stderr.write('%s is readable beyond its owner; chmod 600 it.\n'
                         % setting['password_file'])

    # One conversion, here, so that --dry-run reports the path the run
    # would actually send.  The session's own path_converter is left
    # alone for that reason.
    sent_path = cygpath_m(script_path) if is_cygwin() else script_path

    if setting['dry_run']:
        sys.stdout.write('sqlplus:  %s -s /nolog\n' % setting['sqlplus'])
        sys.stdout.write('connect:  %s\n' % describe_connection(user, tns))
        sys.stdout.write('send:     %s\n' % at_line(sent_path, script_args))
        return 0

    if level >= VERBOSE:
        sys.stderr.write('+ %s -s /nolog\n' % setting['sqlplus'])
        sys.stderr.write('+ CONNECT %s\n' % describe_connection(user, tns))
        sys.stderr.write('+ %s\n' % at_line(sent_path, script_args))

    lines, errors, failure = run_script(user, password, tns, sent_path,
                                        script_args, setting)
    emit(lines, setting['output'])

    if failure is not None:
        raise failure
    if errors and setting['fail_on_error']:
        raise Failed('%d error line%s in the output, the first being: %s'
                     % (len(errors), '' if len(errors) == 1 else 's',
                        errors[0].strip()),
                     'Pass --no-fail-on-error to report these and exit 0.')
    return 0


def cli(argv=None):
    """main(), with every exit path funnelled through one reporter."""
    debugging = '-d' in (sys.argv if argv is None else argv) \
        or '--debug' in (sys.argv if argv is None else argv)
    try:
        return main(argv)
    except Usage as exc:
        _report(str(exc), exc.hint, _LEVEL[0])
        return 2
    except Failed as exc:
        _report(str(exc), exc.hint, _LEVEL[0])
        return 1
    except KeyboardInterrupt:
        return 130
    except Exception:
        if debugging:
            raise
        _report('%s' % sys.exc_info()[1], 'Run again with --debug for a '
                'traceback.', _LEVEL[0])
        return 1


if __name__ == '__main__':
    sys.exit(cli())
