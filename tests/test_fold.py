"""Folding a statement so sqlplus will read it.

sqlplus refuses an input line over 4999 characters with SP2-0027 and ignores
it, so a wide projection fails at the terminal and never reaches Oracle. A
table of forty columns is enough to cross the limit, and the caller sees five
tables of a run quietly missing while the narrow ones come back fine.
"""

from sqlplus_session.session import fold_long_lines

SEP = "'~'"


def squash(s):
    """Every character but whitespace, for inputs carrying none of its own."""
    return ''.join(s.split())


def projection(columns):
    """What cat() emits: one concatenated item, and not a space in it."""
    return ('||%s||' % SEP).join('NVL(TO_CHAR("COL%03d"),%s)' % (i, SEP)
                                 for i in range(columns))


def test_a_wide_projection_is_folded_under_the_limit():
    sql = 'SELECT %s FROM WIDE_TABLE' % projection(120)
    assert len(sql) > 2000
    out = fold_long_lines(sql)
    assert all(len(line) < 4999 for line in out.split('\n'))
    assert squash(out) == squash(sql)


def test_folding_happens_at_commas_and_concatenation_too():
    """The case spaces alone cannot reach, and the one that fails in practice."""
    sql = 'SELECT %s FROM T' % ','.join('"COL%03d"' % i for i in range(400))
    assert ' ' not in sql[7:-9]
    out = fold_long_lines(sql)
    assert out.count('\n') > 0
    assert squash(out) == squash(sql)


def test_a_space_cut_consumes_exactly_one_space():
    sql = 'SELECT %s FROM t' % ' '.join('x%d' % i for i in range(900))
    out = fold_long_lines(sql)
    assert squash(out) == squash(sql)


def test_a_literal_is_never_split():
    body = 'yz' * 3000
    out = fold_long_lines("SELECT '%s' FROM DUAL" % body)
    assert "'%s'" % body in out


def test_an_escaped_quote_does_not_end_the_literal():
    sql = "SELECT '%sit''s' FROM t" % ('a,b||' * 700)
    out = fold_long_lines(sql)
    assert out.count("'") == sql.count("'")
    assert "it''s'" in out.replace('\n', '')


def test_a_short_line_is_returned_untouched():
    for sql in ('SET LINESIZE 32767', 'SELECT 1 FROM dual', '',
                'SELECT 1\nFROM dual'):
        assert fold_long_lines(sql) == sql


def test_width_is_a_parameter():
    sql = 'SELECT %s FROM T' % ','.join('"C%d"' % i for i in range(200))
    assert max(len(l) for l in fold_long_lines(sql, 200).split('\n')) <= 210
