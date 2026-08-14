"""Schema facility against a real data dictionary.

Deliberately not stubbed.  The bug this layer exists to prevent survived
its own offline tests because the stub had been written to the same
misunderstanding as the caller, so it agreed with the caller and proved
nothing.  Oracle does not agree with anybody.

The fixture builds a small schema and drops it again:

    SPS_PARENT      id PK, name NOT NULL, note NULL
    SPS_CHILD       id PK, parent_id -> SPS_PARENT, body CLOB, thumb BLOB,
                    descr, doubled (virtual), plus a hidden column from a
                    function-based index
    SPS_GRANDCHILD  id PK, child_id -> SPS_CHILD          (two hops from parent)
    SPS_DECOY       id PK, props CLOB, parent_id          (no foreign key)
    SPS_PAIR        a, b composite PK
    SPS_PAIR_CHILD  a, b -> SPS_PAIR                      (composite FK)

SPS_DECOY is the point of the exercise: it carries a LOB and a column
called parent_id, so a caller matching on names finds it, and it has no
relationship to anything.
"""

import pytest

from sqlplus_session import Column, SqlplusOraError, SqlplusSchemaError

TABLES = ('SPS_PAIR_CHILD', 'SPS_PAIR', 'SPS_GRANDCHILD', 'SPS_DECOY',
          'SPS_CHILD', 'SPS_PARENT')

DDL = [
    """CREATE TABLE sps_parent (
           id    NUMBER        NOT NULL,
           name  VARCHAR2(50)  NOT NULL,
           note  VARCHAR2(100),
           CONSTRAINT sps_parent_pk PRIMARY KEY (id))""",
    """CREATE TABLE sps_child (
           id        NUMBER       NOT NULL,
           parent_id NUMBER,
           descr     VARCHAR2(50),
           body      CLOB,
           thumb     BLOB,
           doubled   NUMBER GENERATED ALWAYS AS (id * 2) VIRTUAL,
           CONSTRAINT sps_child_pk PRIMARY KEY (id),
           CONSTRAINT sps_child_fk FOREIGN KEY (parent_id)
               REFERENCES sps_parent (id))""",
    # A function-based index is the ordinary way a hidden column appears.
    'CREATE INDEX sps_child_ux ON sps_child (UPPER(descr))',
    """CREATE TABLE sps_grandchild (
           id       NUMBER NOT NULL,
           child_id NUMBER,
           CONSTRAINT sps_grandchild_pk PRIMARY KEY (id),
           CONSTRAINT sps_grandchild_fk FOREIGN KEY (child_id)
               REFERENCES sps_child (id))""",
    """CREATE TABLE sps_decoy (
           id        NUMBER NOT NULL,
           parent_id NUMBER,
           props     CLOB,
           CONSTRAINT sps_decoy_pk PRIMARY KEY (id))""",
    """CREATE TABLE sps_pair (
           a NUMBER NOT NULL,
           b NUMBER NOT NULL,
           CONSTRAINT sps_pair_pk PRIMARY KEY (a, b))""",
    """CREATE TABLE sps_pair_child (
           a NUMBER,
           b NUMBER,
           CONSTRAINT sps_pair_child_fk FOREIGN KEY (a, b)
               REFERENCES sps_pair (a, b))""",
]


@pytest.fixture(scope='module')
def built(open_session):
    """Create the fixture schema, yield a session, drop it again."""
    s = open_session()

    def drop_all():
        for t in TABLES:
            try:
                s.execute('DROP TABLE %s CASCADE CONSTRAINTS PURGE' % t)
            except SqlplusOraError:
                pass          # not there; that is the desired state

    drop_all()
    try:
        for statement in DDL:
            s.execute(statement)
    except SqlplusOraError:
        drop_all()
        raise
    yield s
    drop_all()


@pytest.fixture(scope='module')
def sch(built):
    return built.schema()


def names(columns):
    return [c.name for c in columns]


class TestTables:

    def test_lists_the_tables_it_created(self, sch):
        found = sch.tables()
        for t in ('SPS_PARENT', 'SPS_CHILD', 'SPS_DECOY'):
            assert t in found

    def test_like_filters_in_the_dictionary(self, sch):
        found = sch.tables(like='SPS\\_PAIR%')
        assert 'SPS_PAIR' in found
        assert 'SPS_PARENT' not in found

    def test_like_that_matches_nothing_is_empty_not_an_error(self, sch):
        assert sch.tables(like='SPS_NO_SUCH_THING%') == []


class TestColumns:

    def test_reports_the_declared_columns_in_order(self, sch):
        found = names(sch.columns('SPS_PARENT'))
        assert found[:3] == ['ID', 'NAME', 'NOTE']

    def test_nullability_is_read_not_guessed(self, sch):
        by_name = dict((c.name, c) for c in sch.columns('SPS_PARENT'))
        assert by_name['NAME'].nullable is False
        assert by_name['NOTE'].nullable is True

    def test_types_are_the_dictionary_spelling(self, sch):
        by_name = dict((c.name, c) for c in sch.columns('SPS_CHILD'))
        assert by_name['BODY'].type == 'CLOB'
        assert by_name['THUMB'].type == 'BLOB'
        assert by_name['DESCR'].type == 'VARCHAR2'
        assert by_name['DESCR'].length == 50

    def test_virtual_columns_are_flagged(self, sch):
        by_name = dict((c.name, c) for c in sch.columns('SPS_CHILD'))
        assert by_name['DOUBLED'].virtual is True
        assert by_name['ID'].virtual is False

    def test_hidden_columns_are_visible_here(self, sch):
        # ALL_TAB_COLUMNS would omit these; the function-based index
        # made one, and a column you cannot see is the one that
        # surprises you later.
        hidden = [c for c in sch.columns('SPS_CHILD') if c.hidden]
        assert hidden, 'expected a hidden column from the function index'

    def test_unknown_table_is_an_error_not_an_empty_list(self, sch):
        with pytest.raises(SqlplusSchemaError):
            sch.columns('SPS_NO_SUCH_TABLE')

    def test_columns_are_comparable(self, sch):
        assert sch.columns('SPS_PARENT') == sch.columns('SPS_PARENT')
        assert isinstance(sch.columns('SPS_PARENT')[0], Column)


class TestLobs:

    def test_finds_both_lob_kinds_and_keeps_them_apart(self, sch):
        lobs = dict((c.name, c.type) for c in sch.lobs('SPS_CHILD'))
        assert lobs['BODY'] == 'CLOB'
        assert lobs['THUMB'] == 'BLOB'

    def test_a_table_without_lobs_reports_none(self, sch):
        assert sch.lobs('SPS_PARENT') == []


class TestPrimaryKey:

    def test_single_column_key(self, sch):
        assert sch.primary_key('SPS_PARENT') == ['ID']

    def test_composite_key_in_key_order(self, sch):
        assert sch.primary_key('SPS_PAIR') == ['A', 'B']

    def test_table_without_a_primary_key(self, sch):
        assert sch.primary_key('SPS_PAIR_CHILD') == []


class TestForeignKeys:

    def test_reads_the_declared_relationship(self, sch):
        fks = sch.foreign_keys('SPS_CHILD')
        assert len(fks) == 1
        assert fks[0].columns == ('PARENT_ID',)
        assert fks[0].parent == 'SPS_PARENT'
        assert fks[0].parent_columns == ('ID',)

    def test_composite_foreign_key_keeps_column_order(self, sch):
        fk = sch.foreign_keys('SPS_PAIR_CHILD')[0]
        assert fk.columns == ('A', 'B')
        assert fk.parent_columns == ('A', 'B')

    def test_children_are_found_from_the_other_side(self, sch):
        assert 'SPS_CHILD' in sch.children('SPS_PARENT')
        assert 'SPS_GRANDCHILD' in sch.children('SPS_CHILD')

    def test_the_decoy_has_none(self, sch):
        # This is the whole argument. SPS_DECOY has a CLOB and a column
        # called PARENT_ID, so name matching finds it. The dictionary
        # says it is related to nothing, and the dictionary is right.
        assert sch.foreign_keys('SPS_DECOY') == []
        assert 'SPS_DECOY' not in sch.children('SPS_PARENT')

    def test_the_decoy_looks_right_to_a_name_matcher(self, sch):
        # Guard the premise: if this stops being true the test above
        # stops proving anything.
        assert sch.lobs('SPS_DECOY')
        assert 'PARENT_ID' in names(sch.columns('SPS_DECOY'))


class TestJoinPath:

    def test_direct_relationship(self, sch):
        path = sch.join_path('SPS_CHILD', 'SPS_PARENT')
        assert len(path) == 1
        assert path[0].parent == 'SPS_PARENT'

    def test_two_hops(self, sch):
        path = sch.join_path('SPS_GRANDCHILD', 'SPS_PARENT')
        assert len(path) == 2
        assert [fk.parent for fk in path] == ['SPS_CHILD', 'SPS_PARENT']

    def test_follows_keys_downwards_too(self, sch):
        assert len(sch.join_path('SPS_PARENT', 'SPS_GRANDCHILD')) == 2

    def test_a_table_to_itself_is_no_hops(self, sch):
        assert sch.join_path('SPS_PARENT', 'SPS_PARENT') == []

    def test_no_path_to_the_decoy(self, sch):
        assert sch.join_path('SPS_PARENT', 'SPS_DECOY') is None

    def test_the_schema_declares_keys_so_none_means_none(self, sch):
        # None is only trustworthy because there were keys to search.
        assert sch.declares_foreign_keys() is True


class TestRowsAgainstRealData:
    """The decoder against output Oracle actually produced."""

    def test_nulls_survive_the_round_trip(self, built):
        from sqlplus_session import cat
        built.execute("INSERT INTO sps_parent VALUES (1, 'one', NULL)")
        built.execute("INSERT INTO sps_parent VALUES (2, 'two', '')")
        built.execute('COMMIT')
        p = cat('id', 'name', 'note')
        got = built.rows(p.select('FROM sps_parent ORDER BY id'))
        assert got == [('1', 'one', None), ('2', 'two', None)]

    def test_the_separator_inside_the_data_raises_rather_than_lying(
            self, built):
        # The separator is improbable, not impossible. When it does
        # occur the row splits into too many fields, and the width
        # check is the only thing standing between the caller and a
        # tuple of plausible nonsense.
        from sqlplus_session import SqlplusRowWidthError, cat
        built.execute("INSERT INTO sps_parent VALUES (3, 'a~|~b', 'plain')")
        built.execute('COMMIT')
        p = cat('name', 'note')
        with pytest.raises(SqlplusRowWidthError) as excinfo:
            built.rows(p.select('FROM sps_parent WHERE id = 3'))
        assert excinfo.value.actual == 3
        assert excinfo.value.expected == 2

    def test_a_custom_separator_gets_the_row_back(self, built):
        # And the way out is to choose one the data does not contain.
        from sqlplus_session import cat
        p = cat('name', 'note', separator='@#@')
        got = built.rows(p.select('FROM sps_parent WHERE id = 3'))
        assert got == [('a~|~b', 'plain')]

    def test_scalar_returns_one_value(self, built):
        assert built.scalar('SELECT COUNT(*) FROM sps_decoy') == '0'

    def test_scalar_of_nothing_is_none(self, built):
        assert built.scalar(
            'SELECT id FROM sps_decoy WHERE id = -1') is None

    def test_scalar_refuses_more_than_one_row(self, built):
        with pytest.raises(ValueError):
            built.scalar('SELECT id FROM sps_parent')
