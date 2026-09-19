"""Tests for tools/srun.py.  No database, no Oracle, no pytest required.

Three things are checked here that are easy to get wrong and invisible
when they are: that the hand-written Usage block and the parser still
describe the same interface, that a positional parameter starting with a
dash reaches the script rather than the option parser, and that nothing
identifying the account reaches the sqlplus command line.
"""

import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tools')
sys.path.insert(0, os.path.abspath(TOOLS))

import srun                                              # noqa: E402

FAKE_SQLPLUS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'fake_sqlplus.py')
SRUN = os.path.join(os.path.abspath(TOOLS), 'srun.py')

# Invented.  One test below asserts neither reaches the command line.
FIXTURE_PASSWORD = 'tiger'       # scrub: allow - never a real one
FILE_PASSWORD = 'swordfish'      # scrub: allow - never a real one

_WRAPPER = None


def fake_sqlplus_wrapper():
    """A shell wrapper that answers like sqlplus, made once per run."""
    global _WRAPPER
    if _WRAPPER is None:
        fd, path = tempfile.mkstemp(suffix='.sh', prefix='fake_sqlplus_')
        os.write(fd, ('#!/bin/sh\nexec %s %s "$@"\n'
                      % (sys.executable, FAKE_SQLPLUS)).encode('utf-8'))
        os.close(fd)
        os.chmod(path, stat.S_IRWXU)
        _WRAPPER = path
    return _WRAPPER


def documented_options(doc):
    """Every option the Usage block declares, mapped to its metavar.

    The parser is generated from a table and the Usage block is written
    by hand, which is the price of emulating docopt rather than using
    it.  Nothing keeps the two together except this reading.
    """
    found = {}
    inside = False
    for line in doc.splitlines():
        if line.startswith('Options:'):
            inside = True
            continue
        if inside and line and not line.startswith(' '):
            break
        if not inside:
            continue
        m = re.match(r'^ {2,6}(-{1,2}\S[^ ]*(?:[ =]\S+)?'
                     r'(?:, -{1,2}\S[^ ]*(?:[ =]\S+)?)*)(?:\s{2,}|$)', line)
        if not m:
            continue
        for piece in m.group(1).split(','):
            piece = piece.strip()
            if not piece.startswith('-'):
                continue
            if '=' in piece:
                name, metavar = piece.split('=', 1)
            elif ' ' in piece:
                name, metavar = piece.split(None, 1)
            else:
                name, metavar = piece, None
            found[name] = metavar
    return found


def run_srun(args, env=None, cwd=None):
    """Invoke the tool as a user would.  Returns (rc, stdout, stderr)."""
    environ = dict(os.environ)
    for name in ('DB_USERNAME', 'DB_PASSWORD', 'DB_NAME', 'TWO_TASK',
                 'ORACLE_SID'):
        environ.pop(name, None)
    for name in list(environ):
        if name.startswith('SRUN_'):
            del environ[name]
    environ.update(env or {})
    proc = subprocess.Popen([sys.executable, SRUN] + list(args),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            universal_newlines=True, env=environ, cwd=cwd)
    out, err = proc.communicate()
    return proc.returncode, out, err


def script_file(text='SELECT 1 FROM DUAL;\n', name='t.sql'):
    directory = tempfile.mkdtemp(prefix='srun_')
    path = os.path.join(directory, name)
    with open(path, 'wb') as fh:
        fh.write(text.encode('utf-8'))
    return path


class TestUsageMatchesParser(unittest.TestCase):
    """The Usage block is the specification; the parser must agree with it."""

    def setUp(self):
        self.documented = documented_options(srun.__doc__)

    def test_every_spec_option_is_documented(self):
        for short, long_, metavar, dest, kind, default in srun._SPEC:
            self.assertIn(short, self.documented)
            self.assertIn(long_, self.documented)
            self.assertEqual(self.documented[short], metavar)
            self.assertEqual(self.documented[long_], metavar)

    def test_every_documented_option_is_in_the_spec(self):
        known = set(['-h', '--help', '-V', '--version'])
        for short, long_, metavar, dest, kind, default in srun._SPEC:
            known.add(short)
            known.add(long_)
            if metavar is None:
                known.add('--no-' + long_[2:])
        self.assertEqual(set(self.documented) - known, set())

    def test_booleans_have_a_negated_twin(self):
        for short, long_, metavar, dest, kind, default in srun._SPEC:
            if metavar is not None:
                continue
            self.assertIn('--no-' + long_[2:], self.documented)

    def test_defaults_are_stated(self):
        for short, long_, metavar, dest, kind, default in srun._SPEC:
            if kind != 'value' or default is None:
                continue
            self.assertIn('[default: %s]' % default, srun.__doc__)

    def test_reserved_letters_keep_their_meaning(self):
        reserved = {'-v': 'verbose', '-t': 'terse', '-d': 'debug',
                    '-q': 'quiet'}
        for short, long_, metavar, dest, kind, default in srun._SPEC:
            if short in reserved:
                self.assertEqual(dest, reserved.pop(short))
        self.assertEqual(reserved, {})

    def test_no_password_option(self):
        self.assertNotIn('--password', self.documented)
        self.assertIn('--password-file', self.documented)


class TestSplitArgv(unittest.TestCase):
    """Where srun's options stop and the script's parameters begin."""

    def test_plain(self):
        opts, script, args = srun.split_argv(['-v', 'r.sql', 'a', 'b'])
        self.assertEqual((opts, script, args), (['-v'], 'r.sql', ['a', 'b']))

    def test_dash_leading_parameter_belongs_to_the_script(self):
        opts, script, args = srun.split_argv(['r.sql', '-30', '--tns'])
        self.assertEqual(script, 'r.sql')
        self.assertEqual(args, ['-30', '--tns'])
        self.assertEqual(opts, [])

    def test_separate_value(self):
        opts, script, _ = srun.split_argv(['-T', 'orcl', 'r.sql'])
        self.assertEqual(opts, ['-T', 'orcl'])
        self.assertEqual(script, 'r.sql')

    def test_attached_value(self):
        opts, script, _ = srun.split_argv(['-Torcl', 'r.sql'])
        self.assertEqual(opts, ['-T', 'orcl'])
        self.assertEqual(script, 'r.sql')

    def test_bundle_of_flags(self):
        opts, script, _ = srun.split_argv(['-qvv', 'r.sql'])
        self.assertEqual(opts, ['-q', '-v', '-v'])
        self.assertEqual(script, 'r.sql')

    def test_bundle_ending_in_a_value(self):
        opts, script, args = srun.split_argv(['-vw', '30', 'r.sql', 'x'])
        self.assertEqual(opts, ['-v', '-w', '30'])
        self.assertEqual((script, args), ('r.sql', ['x']))

    def test_bundle_with_the_value_attached(self):
        opts, script, _ = srun.split_argv(['-vw30', 'r.sql'])
        self.assertEqual(opts, ['-v', '-w', '30'])
        self.assertEqual(script, 'r.sql')

    def test_long_with_equals(self):
        opts, script, _ = srun.split_argv(['--tns=orcl', 'r.sql'])
        self.assertEqual(opts, ['--tns=orcl'])
        self.assertEqual(script, 'r.sql')

    def test_double_dash_ends_the_options(self):
        opts, script, args = srun.split_argv(['-v', '--', '-weird.sql', '-1'])
        self.assertEqual(opts, ['-v'])
        self.assertEqual((script, args), ('-weird.sql', ['-1']))

    def test_no_script_at_all(self):
        self.assertEqual(srun.split_argv(['--help']), (['--help'], None, []))


class TestSettings(unittest.TestCase):
    """Option, then environment variable, then default."""

    def test_default(self):
        self.assertEqual(srun.settings([], {})['sqlplus'], 'sqlplus')

    def test_environment_beats_default(self):
        got = srun.settings([], {'SRUN_SQLPLUS': '/opt/bin/sqlplus'})
        self.assertEqual(got['sqlplus'], '/opt/bin/sqlplus')

    def test_option_beats_environment(self):
        got = srun.settings(['-s', 'mine'], {'SRUN_SQLPLUS': '/opt/x'})
        self.assertEqual(got['sqlplus'], 'mine')

    def test_boolean_from_the_environment(self):
        self.assertFalse(srun.settings([], {'SRUN_FAIL_ON_ERROR': 'no'})
                         ['fail_on_error'])
        self.assertTrue(srun.settings([], {'SRUN_FAIL_ON_ERROR': 'on'})
                        ['fail_on_error'])

    def test_negated_option_overrides_the_environment(self):
        got = srun.settings(['--no-fail-on-error'],
                            {'SRUN_FAIL_ON_ERROR': '1'})
        self.assertFalse(got['fail_on_error'])

    def test_bad_boolean_names_the_variable(self):
        with self.assertRaises(srun.Usage) as ctx:
            srun.settings([], {'SRUN_QUIET': 'maybe'})
        self.assertIn('SRUN_QUIET', str(ctx.exception))

    def test_credentials_have_no_srun_variable(self):
        self.assertIsNone(srun.env_name('user'))
        self.assertIsNone(srun.env_name('tns'))
        self.assertEqual(srun.env_name('fail_on_error'), 'SRUN_FAIL_ON_ERROR')

    def test_verbose_counts(self):
        bundled = srun.split_argv(['-vv', 'r.sql'])[0]
        self.assertEqual(srun.settings(bundled, {})['verbose'], 2)
        self.assertEqual(srun.settings([], {'SRUN_VERBOSE': '3'})['verbose'], 3)
        self.assertEqual(srun.settings([], {'SRUN_VERBOSE': 'yes'})
                         ['verbose'], 1)


class TestVolume(unittest.TestCase):

    def level(self, **kw):
        setting = {'quiet': False, 'terse': False, 'verbose': 0,
                   'debug': False}
        setting.update(kw)
        return srun.volume(setting)

    def test_ladder(self):
        self.assertEqual(self.level(), srun.NORMAL)
        self.assertEqual(self.level(quiet=True), srun.QUIET)
        self.assertEqual(self.level(terse=True), srun.TERSE)
        self.assertEqual(self.level(verbose=1), srun.VERBOSE)
        self.assertEqual(self.level(debug=True), srun.DEBUG)

    def test_loudest_wins_within_one_source(self):
        self.assertEqual(self.level(quiet=True, verbose=1), srun.VERBOSE)
        self.assertEqual(self.level(verbose=1, debug=True), srun.DEBUG)


class TestTimeout(unittest.TestCase):

    def test_zero_means_no_practical_limit(self):
        self.assertEqual(srun.resolve_timeout('0'), srun.NO_TIMEOUT)

    def test_a_number(self):
        self.assertEqual(srun.resolve_timeout('2.5'), 2.5)

    def test_rubbish_is_a_usage_error(self):
        for bad in ('soon', '-1', ''):
            with self.assertRaises(srun.Usage):
                srun.resolve_timeout(bad)


class TestPasswordFile(unittest.TestCase):

    def write(self, data):
        fd, path = tempfile.mkstemp(prefix='srun_pw_')
        os.write(fd, data)
        os.close(fd)
        return path

    def test_first_line_only(self):
        path = self.write(b'hunter2\nnot this\n')
        self.assertEqual(srun.read_password_file(path), 'hunter2')

    def test_crlf_is_stripped(self):
        # A password file written on the Windows side carries CRLF, and
        # a surviving \r comes back as a wrong password.
        path = self.write(b'hunter2\r\n')
        self.assertEqual(srun.read_password_file(path), 'hunter2')

    def test_trailing_space_survives(self):
        path = self.write(b'hunter2 \n')
        self.assertEqual(srun.read_password_file(path), 'hunter2 ')

    def test_missing_file_is_a_usage_error(self):
        with self.assertRaises(srun.Usage):
            srun.read_password_file('/no/such/file_xyzzy')

    def test_exposure_is_noticed(self):
        path = self.write(b'hunter2\n')
        os.chmod(path, 0o600)
        self.assertFalse(srun.password_file_is_exposed(path))
        os.chmod(path, 0o644)
        self.assertTrue(srun.password_file_is_exposed(path))


class TestEndToEnd(unittest.TestCase):
    """The whole tool, against the fake sqlplus, as a user would run it."""

    def go(self, args, env=None, cwd=None):
        environ = {'DB_USERNAME': 'scott', 'DB_PASSWORD': FIXTURE_PASSWORD,
                   'DB_NAME': 'fake'}
        environ.update(env or {})
        return run_srun(['--sqlplus', fake_sqlplus_wrapper()] + list(args),
                        env=environ, cwd=cwd)

    def test_output_comes_back(self):
        rc, out, err = self.go([script_file()])
        self.assertEqual(rc, 0, err)
        self.assertIn('file-output-line-1', out)
        self.assertIn('file-output-line-2', out)

    def test_parameters_reach_the_script(self):
        rc, out, err = self.go([script_file(), 'alpha', '42'])
        self.assertEqual(rc, 0, err)
        self.assertIn('param 1=alpha', out)
        self.assertIn('param 2=42', out)

    def test_a_parameter_with_a_space_stays_one_parameter(self):
        rc, out, err = self.go([script_file(), 'two words', 'next'])
        self.assertEqual(rc, 0, err)
        self.assertIn('param 1=two words', out)
        self.assertIn('param 2=next', out)

    def test_a_dash_leading_parameter_is_not_read_as_an_option(self):
        rc, out, err = self.go([script_file(), '-30'])
        self.assertEqual(rc, 0, err)
        self.assertIn('param 1=-30', out)

    def test_a_parameter_with_a_double_quote_is_refused(self):
        rc, out, err = self.go([script_file(), 'a"b'])
        self.assertEqual(rc, 1)
        self.assertIn('double quote', err)

    def test_an_error_line_fails_the_run(self):
        rc, out, err = self.go([script_file(name='__FAKE_ORA_ERROR__.sql')])
        self.assertEqual(rc, 1)
        self.assertIn('ORA-00942', out)
        self.assertIn('ORA-00942', err)

    def test_no_fail_on_error_reports_and_exits_zero(self):
        rc, out, err = self.go(['--no-fail-on-error',
                                script_file(name='__FAKE_ORA_ERROR__.sql')])
        self.assertEqual(rc, 0, err)
        self.assertIn('ORA-00942', out)

    def test_fail_on_error_through_the_environment(self):
        rc, out, err = self.go([script_file(name='__FAKE_ORA_ERROR__.sql')],
                               env={'SRUN_FAIL_ON_ERROR': '0'})
        self.assertEqual(rc, 0, err)

    def test_a_script_that_exits_is_not_a_failure(self):
        # Ending the script with EXIT takes sqlplus down before the
        # sentinel comes back.  That is how plenty of scripts are
        # written, so it has to read as a clean run.
        rc, out, err = self.go([script_file(name='__FAKE_EXIT__.sql')])
        self.assertEqual(rc, 0, err)
        self.assertIn('file-output-line-2', out)

    def test_output_to_a_file(self):
        target = os.path.join(tempfile.mkdtemp(prefix='srun_out_'), 'o.txt')
        rc, out, err = self.go(['-o', target, script_file()])
        self.assertEqual(rc, 0, err)
        self.assertEqual(out, '')
        with open(target, 'rb') as fh:
            written = fh.read()
        self.assertIn(b'file-output-line-1', written)
        self.assertNotIn(b'\r\n', written)

    def test_no_credential_reaches_the_command_line(self):
        # The whole reason this tool exists rather than the shell script
        # it replaces, which wrote user/password@tns into argv.
        seen = os.path.join(tempfile.mkdtemp(prefix='srun_argv_'), 'argv')
        rc, out, err = self.go([script_file()],
                               env={'FAKE_SQLPLUS_ARGV': seen})
        self.assertEqual(rc, 0, err)
        with open(seen) as fh:
            argv = fh.read()
        self.assertIn('/nolog', argv)
        self.assertNotIn(FIXTURE_PASSWORD, argv)
        self.assertNotIn('scott', argv)

    def test_missing_script(self):
        rc, out, err = self.go(['/no/such/script_xyzzy.sql'])
        self.assertEqual(rc, 1)
        self.assertIn('cannot read script', err)

    def test_no_connect_target(self):
        rc, out, err = run_srun([script_file()],
                                env={'DB_USERNAME': 'scott'})
        self.assertEqual(rc, 2)
        self.assertIn('No Oracle connect target', err)
        self.assertIn('DB_NAME', err)

    def test_terse_drops_the_hint(self):
        rc, out, err = run_srun(['-t', script_file()],
                                env={'DB_USERNAME': 'scott'})
        self.assertEqual(rc, 2)
        self.assertIn('No Oracle connect target', err)
        self.assertNotIn('DB_NAME', err)

    def test_dry_run_starts_nothing(self):
        rc, out, err = self.go(['-n', script_file(), 'x'],
                               env={'SRUN_SQLPLUS': '/no/such/binary_xyzzy'})
        self.assertEqual(rc, 0, err)
        self.assertIn('--password--', out)
        self.assertNotIn(FIXTURE_PASSWORD, out)
        self.assertIn('"x"', out)

    def test_verbose_goes_to_stderr(self):
        rc, out, err = self.go(['-v', script_file()])
        self.assertEqual(rc, 0, err)
        self.assertIn('CONNECT scott/', err)
        self.assertIn('--password--@fake', err)
        self.assertNotIn('--password--', out)

    def test_directory(self):
        path = script_file()
        rc, out, err = self.go(['-C', os.path.dirname(path),
                                os.path.basename(path)])
        self.assertEqual(rc, 0, err)
        self.assertIn('file-output-line-1', out)

    def test_password_file(self):
        fd, pw = tempfile.mkstemp(prefix='srun_pw_')
        os.write(fd, (FILE_PASSWORD + '\n').encode('utf-8'))
        os.close(fd)
        os.chmod(pw, 0o600)
        seen = os.path.join(tempfile.mkdtemp(prefix='srun_seen_'), 'seen')
        rc, out, err = self.go(['-P', pw, script_file()],
                               env={'DB_PASSWORD': FIXTURE_PASSWORD,
                                    'FAKE_SQLPLUS_SEEN': seen})
        self.assertEqual(rc, 0, err)
        with open(seen) as fh:
            stdin_lines = fh.read()
        self.assertIn('CONNECT scott/"%s"@fake' % FILE_PASSWORD,
                      stdin_lines)

    def test_bundles_and_attached_values_reach_the_parser(self):
        rc, out, err = self.go(['-nvw30', script_file()])
        self.assertEqual(rc, 0, err)
        self.assertIn('send:', out)

    def test_an_abbreviated_long_option_is_refused(self):
        # docopt has no abbreviation and neither does this; --ver would
        # otherwise resolve to whichever of --verbose and --version
        # argparse liked, silently.
        rc, out, err = self.go(['--ver', script_file()])
        self.assertEqual(rc, 2)

    def test_help_and_version(self):
        rc, out, err = run_srun(['--help'])
        self.assertEqual(rc, 0)
        self.assertIn('Usage:', out)
        rc, out, err = run_srun(['-V'])
        self.assertEqual(rc, 0)
        self.assertIn('sqlplus-session', out)


if __name__ == '__main__':
    unittest.main()
