"""Result decoding, offline.

No database and no sqlplus: these cover the split, the projection and
the policy, which is where the bug lived.  What the dictionary actually
answers is in test_schema_integration.py, against a real instance,
because a stub written to the same misunderstanding as the caller agrees
with the caller and proves nothing -- which is how the original bug
survived its own tests.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from sqlplus_session import (
    NULL_TOKEN,
    SEPARATOR,
    Projection,
    SqlplusRowWidthError,
    Statement,
    cat,
    decode_rows,
    raw,
)
import sqlplus_session.session as _s


def line(*fields):
    return SEPARATOR.join(fields)


class TestProjection(unittest.TestCase):

    def test_width_is_the_number_of_expressions(self):
        self.assertEqual(cat('a').width, 1)
        self.assertEqual(cat('a', 'b', 'c').width, 3)
        self.assertEqual(len(cat('a', 'b')), 2)

    def test_each_expression_is_null_wrapped(self):
        sql = cat('a', 'b').sql
        self.assertIn("NVL(TO_CHAR(a),'%s')" % NULL_TOKEN, sql)
        self.assertIn("NVL(TO_CHAR(b),'%s')" % NULL_TOKEN, sql)

    def test_expressions_are_joined_by_the_separator(self):
        self.assertIn("||'%s'||" % SEPARATOR, cat('a', 'b').sql)

    def test_raw_is_not_wrapped(self):
        sql = cat('a', raw('DBMS_LOB.SUBSTR(body,200,1)')).sql
        self.assertIn('DBMS_LOB.SUBSTR(body,200,1)', sql)
        self.assertNotIn('TO_CHAR(DBMS_LOB', sql)

    def test_renders_as_sql_when_interpolated(self):
        self.assertEqual('SELECT %s FROM t' % cat('a'),
                         'SELECT %s FROM t' % cat('a').sql)

    def test_select_carries_the_width(self):
        stmt = cat('a', 'b', 'c').select('FROM t WHERE x = 1')
        self.assertIsInstance(stmt, Statement)
        self.assertEqual(stmt.width, 3)
        self.assertTrue(stmt.startswith('SELECT '))
        self.assertTrue(stmt.endswith('FROM t WHERE x = 1'))

    def test_select_with_no_rest(self):
        self.assertEqual(cat('1').select().width, 1)

    def test_custom_separator_and_token_travel_together(self):
        p = cat('a', 'b', separator='##', null_token='(nil)')
        self.assertIn("||'##'||", p.sql)
        self.assertIn("'(nil)'", p.sql)

    def test_select_carries_the_tokens_not_just_the_width(self):
        # Regression. select() carried only the width, so a custom
        # separator reached the SQL and the output was still split on
        # the default -- the same encode/decode disagreement the
        # Projection exists to prevent, reintroduced one layer up.
        # Caught by a real query, not by this file, which is the
        # argument for testing against an instance.
        stmt = cat('a', 'b', separator='@#@', null_token='(nil)').select(
            'FROM t')
        self.assertEqual(stmt.separator, '@#@')
        self.assertEqual(stmt.null_token, '(nil)')

    def test_a_plain_statement_falls_back_to_the_defaults(self):
        stmt = cat('a').select('FROM t')
        self.assertEqual(stmt.separator, SEPARATOR)
        self.assertEqual(stmt.null_token, NULL_TOKEN)

    def test_empty_projection_is_refused(self):
        self.assertRaises(ValueError, cat)

    def test_a_sequence_may_be_passed_instead_of_varargs(self):
        self.assertEqual(cat(['a', 'b']).width, 2)

    def test_unknown_keyword_is_refused(self):
        self.assertRaises(TypeError, cat, 'a', seperator='##')


class TestDecodeRows(unittest.TestCase):

    def test_splits_on_the_separator(self):
        got = decode_rows([line('1', 'alice'), line('2', 'bob')], 2)
        self.assertEqual(got, [('1', 'alice'), ('2', 'bob')])

    def test_blank_lines_are_dropped(self):
        got = decode_rows(['', '   ', line('1', 'a'), ''], 2)
        self.assertEqual(got, [('1', 'a')])

    def test_null_token_becomes_none_by_default(self):
        got = decode_rows([line('1', NULL_TOKEN)], 2)
        self.assertEqual(got, [('1', None)])

    def test_null_is_distinguishable_from_empty_string(self):
        got = decode_rows([line(NULL_TOKEN, '')], 2)
        self.assertEqual(got, [(None, '')])

    def test_null_may_be_rendered_as_something_else(self):
        got = decode_rows([line('1', NULL_TOKEN)], 2, null='')
        self.assertEqual(got, [('1', '')])

    def test_interior_whitespace_is_preserved(self):
        got = decode_rows([line('  a  ', 'b')], 2)
        self.assertEqual(got, [('  a  ', 'b')])


class TestWidthMismatch(unittest.TestCase):
    """The bug: a four-field key asked for as one column.

    The old decoder counted the fields, saw four where one was wanted,
    and dropped the row. Every row. The caller got an empty list, no
    exception, and a report stating it had measured zero of fifty.
    """

    def four_field_key(self):
        return [line('A', 'B', 'C', 'D'), line('E', 'F', 'G', 'H')]

    def test_raises_by_default(self):
        self.assertRaises(SqlplusRowWidthError,
                          decode_rows, self.four_field_key(), 1)

    def test_the_error_says_what_it_found_and_wanted(self):
        try:
            decode_rows(self.four_field_key(), 1)
        except SqlplusRowWidthError as e:
            self.assertEqual(e.actual, 4)
            self.assertEqual(e.expected, 1)
            self.assertEqual(e.index, 0)
            self.assertIn('4 fields', str(e))
            self.assertEqual(len(e.output), 2)
        else:
            self.fail('no SqlplusRowWidthError')

    def test_return_hands_the_row_back_whole(self):
        got = decode_rows(self.four_field_key(), 1, on_short='return')
        self.assertEqual(got[0], ('A', 'B', 'C', 'D'))
        self.assertEqual(len(got), 2)

    def test_skip_is_the_old_behaviour_and_must_be_asked_for(self):
        got = decode_rows(self.four_field_key(), 1, on_short='skip')
        self.assertEqual(got, [])

    def test_too_few_fields_also_raises(self):
        self.assertRaises(SqlplusRowWidthError,
                          decode_rows, [line('a', 'b')], 3)

    def test_unknown_policy_is_refused(self):
        self.assertRaises(ValueError, decode_rows, [], 1, on_short='ignore')

    def test_wrapped_line_is_named_as_such(self):
        # A row at exactly LINESIZE was almost certainly wrapped, and
        # the decode failure is a symptom rather than the cause.
        long_line = 'x' * 80
        try:
            decode_rows([long_line], 2, linesize=80)
        except SqlplusRowWidthError as e:
            self.assertIn('LINESIZE', str(e))
            self.assertIn('wrapped', str(e))
        else:
            self.fail('no SqlplusRowWidthError')

    def test_no_wrap_hint_when_the_line_is_short(self):
        try:
            decode_rows(['short'], 2, linesize=4000)
        except SqlplusRowWidthError as e:
            self.assertIsNone(e.hint)
            self.assertNotIn('LINESIZE', str(e))
        else:
            self.fail('no SqlplusRowWidthError')


class TestLinesize(unittest.TestCase):
    """LINESIZE is adjustable without restating the whole setup list."""

    def test_read_back_from_the_default_setup(self):
        commands, size = _s._apply_linesize(list(_s._DEFAULT_SETUP), None)
        self.assertEqual(size, 4000)
        self.assertEqual(commands, list(_s._DEFAULT_SETUP))

    def test_explicit_value_replaces_the_one_in_the_list(self):
        commands, size = _s._apply_linesize(list(_s._DEFAULT_SETUP), 12000)
        self.assertEqual(size, 12000)
        self.assertIn('SET LINESIZE 12000', commands)
        self.assertNotIn('SET LINESIZE 4000', commands)
        self.assertEqual(len(commands), len(_s._DEFAULT_SETUP))

    def test_appended_when_the_list_has_none(self):
        commands, size = _s._apply_linesize(['SET PAGESIZE 0'], 9000)
        self.assertEqual(size, 9000)
        self.assertEqual(commands[-1], 'SET LINESIZE 9000')

    def test_abbreviation_is_recognised(self):
        _, size = _s._apply_linesize(['SET LIN 200'], None)
        self.assertEqual(size, 200)
        commands, size = _s._apply_linesize(['SET LIN 200'], 300)
        self.assertEqual(commands, ['SET LINESIZE 300'])

    def test_none_and_no_setting_reads_as_unknown(self):
        _, size = _s._apply_linesize(['SET PAGESIZE 0'], None)
        self.assertIsNone(size)

    def test_out_of_range_is_refused(self):
        self.assertRaises(ValueError, _s._apply_linesize, [], 0)
        self.assertRaises(ValueError, _s._apply_linesize, [], 32768)

    def test_non_integer_is_refused(self):
        self.assertRaises(TypeError, _s._apply_linesize, [], '4000')
        self.assertRaises(TypeError, _s._apply_linesize, [], True)


class TestWidthSource(unittest.TestCase):
    """Where rows() gets the column count, without opening a session."""

    def decode(self, sql, columns):
        return _s.SqlplusSession._decoding_of(_Fake(), sql, columns)

    def test_statement_supplies_its_own_width(self):
        stmt = cat('a', 'b').select('FROM t')
        self.assertEqual(self.decode(stmt, None)[0], 2)

    def test_projection_supplies_width_and_tokens(self):
        p = cat('a', 'b', 'c', separator='##', null_token='(nil)')
        width, sep, token = self.decode('SELECT ...', p)
        self.assertEqual((width, sep, token), (3, '##', '(nil)'))

    def test_integer_is_accepted_for_hand_written_sql(self):
        self.assertEqual(self.decode('SELECT a||b FROM t', 2)[0], 2)

    def test_plain_sql_with_no_count_is_refused(self):
        self.assertRaises(ValueError, self.decode, 'SELECT a FROM t', None)

    def test_a_bad_count_is_refused(self):
        self.assertRaises(ValueError, self.decode, 'SELECT a', 0)
        self.assertRaises(TypeError, self.decode, 'SELECT a', 'two')
        self.assertRaises(TypeError, self.decode, 'SELECT a', True)


class _Fake(object):
    """Enough of a session for _decoding_of, which touches no state."""
    pass


if __name__ == '__main__':
    unittest.main()
