"""A byte the stream cannot decode must not take the session down.

A query result carries whatever bytes the column holds, and nothing
obliges a database to make them valid UTF-8.  One 0xAE -- the
registered-trademark sign in cp1252, and ordinary in a company name --
used to raise UnicodeDecodeError inside the reader thread.  The thread
died, the session died with it, and every statement after that reported
a dead session.  A run of a hundred and fifty tables stopped on table
one hundred and eight and logged forty-seven consecutive failures, none
of which named the cause.

The requirement is narrow: undecodable input costs the affected
characters and nothing else.  The row still arrives, the session stays
alive, and the next statement runs.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_functionality import _make_session   # noqa: E402


class TestUndecodableOutput(unittest.TestCase):

    def test_a_bad_byte_does_not_kill_the_session(self):
        with _make_session() as s:
            s.query('SELECT 1 FROM DUAL __FAKE_BAD_BYTE__')
            self.assertTrue(s.alive)
            self.assertEqual(s.query('SELECT 7 FROM DUAL'), ['\t 7'])

    def test_the_row_survives_with_the_byte_substituted(self):
        with _make_session() as s:
            lines = s.query('SELECT 1 FROM DUAL __FAKE_BAD_BYTE__')
            joined = '\n'.join(lines)
            self.assertIn('one', joined)
            self.assertIn('two', joined)

    def test_every_statement_after_the_bad_byte_still_runs(self):
        # The defect's signature was not one failure but a cascade: the
        # session was gone, so the rest of the run failed one statement
        # at a time.  Ten in a row is enough to show the cascade is not
        # there.
        with _make_session() as s:
            s.query('SELECT 1 FROM DUAL __FAKE_BAD_BYTE__')
            for n in range(10):
                self.assertTrue(s.alive)
                s.query('SELECT %d FROM DUAL' % n)
