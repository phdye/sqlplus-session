"""Tests for the benchmark module.  No database and no sqlplus.

Nothing here times anything -- the measurement needs a live instance and
is the point of the module rather than of a test.  What is checked is the
interface it presents and where it lives: that no password can travel as
an argument, and that it reaches the package by being part of it rather
than by editing sys.path, which is how it used to.
"""

import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from sqlplus_session import benchmark                    # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_benchmark(args, env=None):
    environ = dict(os.environ)
    for name in ('DB_USERNAME', 'DB_PASSWORD', 'DB_NAME', 'TWO_TASK',
                 'ORACLE_SID'):
        environ.pop(name, None)
    environ['PYTHONPATH'] = os.pathsep.join(
        [ROOT] + ([environ['PYTHONPATH']] if environ.get('PYTHONPATH')
                  else []))
    environ.update(env or {})
    proc = subprocess.Popen(
        [sys.executable, '-m', 'sqlplus_session.benchmark'] + list(args),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        universal_newlines=True, env=environ)
    out, err = proc.communicate()
    return proc.returncode, out, err


class TestNoSecretOnTheCommandLine(unittest.TestCase):

    def options(self):
        return benchmark.parse_args([])

    def test_there_is_no_password_option(self):
        self.assertFalse(hasattr(self.options(), 'password'))
        self.assertNotIn('--password=', benchmark.__doc__)
        self.assertNotIn('-p PW', benchmark.__doc__)

    def test_there_is_a_password_file_option(self):
        self.assertTrue(hasattr(self.options(), 'password_file'))
        self.assertIn('--password-file=PATH', benchmark.__doc__)

    def test_the_option_is_refused(self):
        rc, out, err = run_benchmark(['--password', 'hunter2'])
        self.assertEqual(rc, 2)

    def test_an_unreadable_file_is_a_usage_error(self):
        rc, out, err = run_benchmark(['-P', '/no/such/file_xyzzy',
                                      '--tns', 'orcl'])
        self.assertEqual(rc, 2)
        self.assertIn('--password-file', err)


class TestPackaging(unittest.TestCase):

    def test_it_is_a_module_of_the_package(self):
        self.assertEqual(benchmark.__name__, 'sqlplus_session.benchmark')

    def test_nothing_manipulates_sys_path(self):
        # It reached the package from tools/ by inserting the parent on
        # sys.path.  One surviving line here means it was moved and not
        # rewired: it would still work from a checkout and fail from an
        # install, which is the wrong half to have working.
        with open(benchmark.__file__) as fh:
            self.assertNotIn('sys.path', fh.read())

    def test_no_console_script(self):
        # Deliberate: this is development tooling, and the entry point
        # would put a timing harness on every installer's PATH.
        with open(os.path.join(ROOT, 'setup.py')) as fh:
            self.assertNotIn('benchmark', fh.read())


class TestStandardOptions(unittest.TestCase):

    def test_version_has_both_spellings(self):
        for flag in ('-V', '--version'):
            rc, out, err = run_benchmark([flag])
            self.assertEqual(rc, 0, err)
            self.assertIn('sqlplus-session', out)

    def test_help(self):
        rc, out, err = run_benchmark(['--help'])
        self.assertEqual(rc, 0, err)
        self.assertIn('Usage:', out)

    def test_no_connect_target_names_the_variables(self):
        rc, out, err = run_benchmark([])
        self.assertEqual(rc, 2)
        self.assertIn('No Oracle connect target', err)
        self.assertIn('DB_NAME', err)


if __name__ == '__main__':
    unittest.main()
