"""pyflakes over the tree, as a test.

The sweep that produced this file turned up four dead names: two imports
nobody used, an exception binding nobody asserted on, and a name imported
for a test that was never written. None of the four cost anything to fix
and all four had been there for weeks, which is the argument for checking
mechanically rather than by remembering to look.

Two of them were not litter. An `as ctx` with no assertion after it, and
a type imported and never named, are both somebody stopping halfway; the
repair was to finish the assertion, not to delete the evidence that one
was missing.
"""

import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Nothing generated, nothing vendored, nothing in the working annex.
_SKIP = frozenset(('.git', '.pytest_cache', '.tox', '__pycache__', 'a',
                   'build', 'dist', '.worktrees'))


def python_files(root=ROOT):
    """Every .py in the checkout, walked rather than asked of git.

    An unpacked sdist has no .git, and a check that silently covers
    nothing there would report success for work it did not do.
    """
    found = []
    for base, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs
                   if d not in _SKIP and not d.endswith('.egg-info')]
        found.extend(os.path.join(base, n) for n in names
                     if n.endswith('.py'))
    return sorted(found)


class TestNoDeadNames(unittest.TestCase):

    def test_the_walk_finds_something(self):
        # A clean result over an empty list is the failure mode this
        # whole file exists to avoid.
        files = python_files()
        self.assertGreater(len(files), 5)
        self.assertIn(os.path.abspath(__file__), files)

    def pyflakes_runs(self):
        """Asked of the interpreter rather than by importing it.

        `import pyflakes` to see whether pyflakes exists is an unused
        import, which is the one thing this test refuses to allow -- and
        a `# noqa` on it would not help, since noqa is flake8's and
        pyflakes has never read it.  This file failed itself that way on
        its first run.
        """
        proc = subprocess.Popen(
            [sys.executable, '-m', 'pyflakes', '--version'],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            universal_newlines=True)
        proc.communicate()
        return proc.returncode == 0

    def test_pyflakes_is_clean(self):
        if not self.pyflakes_runs():
            raise unittest.SkipTest(
                'pyflakes is not installed: pip install pyflakes')
        proc = subprocess.Popen(
            [sys.executable, '-m', 'pyflakes'] + python_files(),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            universal_newlines=True)
        out, _ = proc.communicate()
        self.assertEqual(out.strip(), '', 'pyflakes said:\n' + out)


if __name__ == '__main__':
    unittest.main()
