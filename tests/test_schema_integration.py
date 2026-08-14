"""Schema facility against a real data dictionary.

Deliberately not stubbed.  The bug this layer exists to prevent survived
its own offline tests because the stub had been written to the same
misunderstanding as the caller, so it agreed with the caller and proved
nothing.  Oracle does not agree with anybody.

Two halves, because the interesting tests need objects that do not exist
anywhere by default, and creating them needs a privilege the package
itself never uses.

Read-only, and the default.  Invariants against whatever schema is
already there: every foreign key names columns that exist, a primary key
is a subset of its table's columns, a LOB is a LOB.  Runs on any account
that can read the dictionary, which is the only thing this layer asks
for in production.  Point it somewhere populated with
``--schema-owner``.

With ``--create-objects``, a fixture schema, and the assertions that
actually pin the behaviour down.  Off by default: the account running
the suite may be as read-only as the package is, and one that is not is
a deliberate choice rather than an assumption.

    pytest tests/test_schema_integration.py --tns orcl --create-objects

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
def built(request, open_session):
    """Create the fixture schema, yield a session, drop it again.

    Skips unless ``--create-objects`` was given.  The privilege is not
    one the package needs and not one to assume: an account that can
    read a dictionary is exactly the account this layer is designed for.
    """
    if not request.config.getoption('--create-objects'):
        pytest.skip('needs --create-objects (creates and drops SPS_* '
                    'tables; requires CREATE TABLE)')

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
    except SqlplusOraError as exc:
        drop_all()
        # One legible line. The same privilege failure repeated down 32
        # tracebacks is 1300 lines of log for one fact.
        if 'ORA-01031' in str(exc):
            pytest.fail(
                'cannot build the fixture schema: %s\n'
                'The connected account needs CREATE TABLE:\n'
                '    GRANT CREATE TABLE TO <user>;\n'
                'Or drop --create-objects and run the read-only checks.'
                % exc, pytrace=False)
        pytest.fail('cannot build the fixture schema: %s' % exc,
                    pytrace=False)
    yield s
    drop_all()


@pytest.fixture(scope='module')
def sch(built):
    return built.schema()


@pytest.fixture(scope='session')
def live(request, session):
    """The schema this run can actually read, without creating anything."""
    return session.schema(request.config.getoption('--schema-owner'))


def names(columns):
    return [c.name for c in columns]


def some_tables(sch, limit=5):
    """A few tables to check, or a skip saying why there were none."""
    found = sch.tables()
    if not found:
        pytest.skip('schema %s owns no tables; point --schema-owner at one '
                    'that does to exercise these' % sch.owner)
    return found[:limit]


class TestReadOnlyInvariants:
    """Things that must hold of any schema, checked against a real one.

    Weaker than the fixture assertions on purpose. These run wherever
    the dictionary can be read, which is the only privilege the package
    asks for, and they still exercise every query in the module against
    Oracle rather than against an idea of Oracle.
    """

    def test_owner_is_what_was_asked_for(self, request, live, session):
        asked = request.config.getoption('--schema-owner')
        if asked:
            assert live.owner == asked.upper()
        else:
            # Defaulting to the connected user is the whole point of
            # letting owner be omitted.
            assert live.owner == session.scalar(
                'SELECT USER FROM dual').upper()

    def test_tables_returns_names(self, live):
        found = live.tables()
        assert isinstance(found, list)
        assert all(isinstance(t, str) and t for t in found)

    def test_tables_is_cached_and_stable(self, live):
        assert live.tables() == live.tables()

    def test_like_narrows_rather_than_widens(self, live):
        every = live.tables()
        narrowed = live.tables(like='A%')
        assert set(narrowed) <= set(every)
        assert all(t.startswith('A') for t in narrowed)

    def test_every_table_has_columns(self, live):
        for table in some_tables(live):
            assert live.columns(table), table

    def test_column_fields_are_populated(self, live):
        for table in some_tables(live):
            for c in live.columns(table):
                assert c.name
                assert c.type
                assert c.nullable in (True, False)
                assert c.hidden in (True, False)
                assert c.virtual in (True, False)

    def test_primary_key_is_a_subset_of_the_columns(self, live):
        for table in some_tables(live):
            cols = set(names(live.columns(table)))
            assert set(live.primary_key(table)) <= cols, table

    def test_lobs_are_a_subset_of_the_columns_and_really_lobs(self, live):
        from sqlplus_session.schema import LOB_TYPES
        for table in some_tables(live):
            cols = live.columns(table)
            lobs = live.lobs(table)
            assert set(names(lobs)) <= set(names(cols))
            assert all(c.type in LOB_TYPES for c in lobs)

    def test_foreign_keys_are_internally_consistent(self, live):
        for table in some_tables(live, limit=10):
            cols = set(names(live.columns(table)))
            for fk in live.foreign_keys(table):
                assert fk.table == table
                assert fk.columns
                # As many parent columns as child columns, or the pairing
                # the caller composes a join from is meaningless.
                assert len(fk.columns) == len(fk.parent_columns), fk
                assert set(fk.columns) <= cols, fk
                assert fk.parent

    def test_a_parent_in_this_schema_really_has_those_columns(self, live):
        here = set(live.tables())
        for table in some_tables(live, limit=10):
            for fk in live.foreign_keys(table):
                if fk.parent_owner and fk.parent_owner != live.owner:
                    continue          # points out of this schema
                if fk.parent not in here:
                    continue
                parent_cols = set(names(live.columns(fk.parent)))
                assert set(fk.parent_columns) <= parent_cols, fk

    def test_children_agree_with_foreign_keys(self, live):
        for table in some_tables(live):
            for child in live.children(table):
                assert any(fk.parent == table
                           for fk in live.foreign_keys(child)), (table, child)

    def test_a_table_to_itself_is_no_hops(self, live):
        if not live.declares_foreign_keys():
            pytest.skip('schema %s declares no foreign keys' % live.owner)
        for table in some_tables(live, limit=1):
            assert live.join_path(table, table) == []

    def test_unknown_table_is_an_error_not_an_empty_list(self, live):
        with pytest.raises(SqlplusSchemaError):
            live.columns('SPS_DEFINITELY_NOT_A_TABLE_XYZZY')

    def test_declares_foreign_keys_answers_yes_or_no(self, live):
        assert live.declares_foreign_keys() in (True, False)

    def test_join_path_refuses_to_guess_when_there_is_nothing_to_search(
            self, live):
        # Only meaningful where the schema has no keys at all -- and
        # there, None would read as "no path" rather than "no graph".
        if live.declares_foreign_keys():
            pytest.skip('schema %s does declare foreign keys' % live.owner)
        with pytest.raises(SqlplusSchemaError):
            live.join_path('A', 'B')


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
