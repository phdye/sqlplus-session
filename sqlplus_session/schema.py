"""What tables, columns and keys actually exist.

A caller inventorying a schema it does not control has to ask the data
dictionary.  Doing that well means ``ALL_TABLES``, ``ALL_TAB_COLS`` for
the hidden and virtual columns, and ``ALL_CONSTRAINTS`` with
``ALL_CONS_COLUMNS`` for the keys.  Doing it badly means matching column
names against patterns and hoping.

Callers do it badly, because the dictionary queries are tedious and the
pattern matching looks like it works.  It looked like it worked right up
until an unrelated table with a properties CLOB and a plausibly-named
key was matched first, and the report said the count was zero.  The
dictionary knew the whole time that there was a foreign key to one and
not the other.  A relationship is a fact to be read, not a pattern to be
matched.

Everything here is a plain SELECT -- no PL/SQL -- so a caller enforcing
read-only can run it.

Deliberately absent: any notion of what a table is *for*.  Deciding that
one column is the name and another is the effective date is domain
judgement dressed as introspection, and it belongs to the caller that
has the domain.
"""

from ._errors import SqlplusSchemaError
from .rows import cat

__all__ = ['Schema', 'Column', 'ForeignKey', 'LOB_TYPES']

#: Types the dictionary reports for a large object.  Kept as the
#: dictionary spells them: a caller that needs to know a column is a
#: BLOB rather than a CLOB is asking a question the two answer
#: differently, and flattening both to "large object" would hide it.
LOB_TYPES = frozenset(['CLOB', 'NCLOB', 'BLOB', 'BFILE'])


def _quote(literal):
    """A SQL string literal.  Doubling the quote is the whole trick."""
    return "'%s'" % str(literal).replace("'", "''")


class Column(object):
    """One column, as the dictionary describes it."""

    __slots__ = ('name', 'type', 'length', 'nullable', 'hidden', 'virtual')

    def __init__(self, name, type, length, nullable, hidden, virtual):
        self.name = name
        self.type = type
        self.length = length
        self.nullable = nullable
        self.hidden = hidden
        self.virtual = virtual

    @property
    def is_lob(self):
        return self.type in LOB_TYPES

    def __repr__(self):
        return ('Column(%r, %r, length=%r, nullable=%r, hidden=%r, '
                'virtual=%r)' % (self.name, self.type, self.length,
                                 self.nullable, self.hidden, self.virtual))

    def __eq__(self, other):
        if not isinstance(other, Column):
            return NotImplemented
        return all(getattr(self, f) == getattr(other, f)
                   for f in self.__slots__)

    def __ne__(self, other):          # Python 3.2 has no automatic __ne__
        result = self.__eq__(other)
        return result if result is NotImplemented else not result


class ForeignKey(object):
    """One foreign key: which columns point at which parent columns.

    *parent_owner* is carried because a key may point out of the schema
    it is declared in, and then *parent* is not a table this Schema
    lists.  A caller checking "does the parent exist here" needs to know
    the difference between absent and elsewhere.
    """

    __slots__ = ('name', 'table', 'columns', 'parent', 'parent_columns',
                 'parent_owner')

    def __init__(self, name, table, columns, parent, parent_columns,
                 parent_owner=None):
        self.name = name
        self.table = table
        self.columns = tuple(columns)
        self.parent = parent
        self.parent_columns = tuple(parent_columns)
        self.parent_owner = parent_owner

    def __repr__(self):
        parent = self.parent
        if self.parent_owner:
            parent = '%s.%s' % (self.parent_owner, parent)
        return ('ForeignKey(%r: %s(%s) -> %s(%s))'
                % (self.name, self.table, ', '.join(self.columns),
                   parent, ', '.join(self.parent_columns)))

    def __eq__(self, other):
        if not isinstance(other, ForeignKey):
            return NotImplemented
        return all(getattr(self, f) == getattr(other, f)
                   for f in self.__slots__)

    def __ne__(self, other):
        result = self.__eq__(other)
        return result if result is NotImplemented else not result


class Schema(object):
    """The dictionary's view of one owner, loaded once and cached.

    Row counts are deliberately not part of this.  They are a separate
    and expensive question, and the caller can ask it when it wants the
    answer.
    """

    def __init__(self, session, owner=None, timeout=None):
        self._s = session
        self._timeout = timeout
        self._owner = owner.upper() if owner else None
        self._columns = {}          # table -> [Column]
        self._pk = {}               # table -> [name]
        self._fks = None            # {table: [ForeignKey]}, whole schema
        self._tables = None

    @property
    def owner(self):
        """The schema being described, upper-cased as the dictionary
        stores it.  Defaults to whoever the session connected as."""
        if self._owner is None:
            self._owner = (self._s.scalar('SELECT USER FROM dual',
                                          timeout=self._timeout) or '').upper()
        return self._owner

    # ------------------------------------------------------------------ #
    # Tables and columns                                                  #
    # ------------------------------------------------------------------ #

    def tables(self, like=None):
        """Table names, optionally filtered with a SQL ``LIKE`` pattern.

        The filter is pushed into the dictionary query rather than
        applied afterwards, which matters on a large schema.

        ``\\`` is the escape character, so ``'SPS\\\\_%'`` means a
        literal underscore and ``'SPS_%'`` means any character there.
        """
        if like is None and self._tables is not None:
            return list(self._tables)

        where = 'owner = %s' % _quote(self.owner)
        if like is not None:
            # ESCAPE unconditionally, so a caller can write SPS\_% and
            # mean an underscore. Without it the underscore is a
            # single-character wildcard and a pattern meant to be
            # narrow quietly matches more than intended -- which is the
            # failure mode this whole module exists to avoid.
            where += " AND table_name LIKE %s ESCAPE '\\'" % _quote(like)
        p = cat('table_name')
        names = [r[0] for r in self._s.rows(
            p.select('FROM all_tables WHERE %s ORDER BY table_name' % where),
            timeout=self._timeout)]
        if like is None:
            self._tables = list(names)
        return names

    def columns(self, table):
        """Every column, including the hidden and virtual ones.

        ``ALL_TAB_COLS`` rather than ``ALL_TAB_COLUMNS``: the latter
        omits hidden columns, and a column you cannot see is exactly the
        one that surprises you later.
        """
        table = table.upper()
        if table in self._columns:
            return list(self._columns[table])

        p = cat('column_name', 'data_type', 'data_length',
                'nullable', 'hidden_column', 'virtual_column')
        rows = self._s.rows(p.select(
            'FROM all_tab_cols WHERE owner = %s AND table_name = %s '
            'ORDER BY internal_column_id'
            % (_quote(self.owner), _quote(table))), timeout=self._timeout)

        cols = [Column(name=r[0], type=r[1],
                       length=int(r[2]) if r[2] is not None else None,
                       nullable=(r[3] == 'Y'), hidden=(r[4] == 'YES'),
                       virtual=(r[5] == 'YES'))
                for r in rows]
        if not cols and table not in self.tables():
            raise SqlplusSchemaError(
                'no such table in %s: %s' % (self.owner, table))
        self._columns[table] = cols
        return list(cols)

    def lobs(self, table):
        """Columns whose type is a large object, types unflattened."""
        return [c for c in self.columns(table) if c.is_lob]

    # ------------------------------------------------------------------ #
    # Keys                                                                #
    # ------------------------------------------------------------------ #

    def primary_key(self, table):
        """Primary key column names in key order, or ``[]`` if none.

        Worth reading rather than inferring.  A caller that took
        ``MAX(version)`` to mean "newest row per parent" got several
        rows per parent, because the schema numbered several versions 0.
        The primary key would have said which column identifies a row.
        """
        table = table.upper()
        if table in self._pk:
            return list(self._pk[table])

        p = cat('c.column_name')
        names = [r[0] for r in self._s.rows(p.select(
            'FROM all_constraints k JOIN all_cons_columns c '
            '  ON c.owner = k.owner AND c.constraint_name = k.constraint_name '
            'WHERE k.owner = %s AND k.table_name = %s '
            "  AND k.constraint_type = 'P' "
            'ORDER BY c.position'
            % (_quote(self.owner), _quote(table))), timeout=self._timeout)]
        self._pk[table] = names
        return list(names)

    def _load_foreign_keys(self):
        """Every foreign key in the schema, in one query.

        One round trip rather than one per table, because ``join_path``
        needs the whole graph anyway and the graphs are small.
        """
        if self._fks is not None:
            return self._fks

        p = cat('k.constraint_name', 'k.table_name', 'c.column_name',
                'rk.table_name', 'rc.column_name', 'k.r_owner')
        try:
            rows = self._s.rows(p.select(
                'FROM all_constraints k '
                'JOIN all_cons_columns c '
                '  ON c.owner = k.owner '
                ' AND c.constraint_name = k.constraint_name '
                'JOIN all_constraints rk '
                '  ON rk.owner = k.r_owner '
                ' AND rk.constraint_name = k.r_constraint_name '
                'JOIN all_cons_columns rc '
                '  ON rc.owner = rk.owner '
                ' AND rc.constraint_name = rk.constraint_name '
                ' AND rc.position = c.position '
                'WHERE k.owner = %s AND k.constraint_type = %s '
                'ORDER BY k.constraint_name, c.position'
                % (_quote(self.owner), _quote('R'))), timeout=self._timeout)
        except Exception as exc:
            # Not every login can read ALL_CONSTRAINTS. Say so rather
            # than degrading to "this schema has no relationships",
            # which is indistinguishable from an answer.
            raise SqlplusSchemaError(
                'cannot read foreign keys for %s: %s\n'
                'The login may lack SELECT on ALL_CONSTRAINTS or '
                'ALL_CONS_COLUMNS. Falling back to matching column names '
                'is a choice for the caller to make knowingly.'
                % (self.owner, exc))

        by_name = {}
        order = []
        for name, table, column, parent, parent_column, parent_owner in rows:
            if name not in by_name:
                by_name[name] = (table, [], parent, [], parent_owner)
                order.append(name)
            by_name[name][1].append(column)
            by_name[name][3].append(parent_column)

        fks = {}
        for name in order:
            table, columns, parent, parent_columns, parent_owner = \
                by_name[name]
            fks.setdefault(table, []).append(
                ForeignKey(name, table, columns, parent, parent_columns,
                           parent_owner))
        self._fks = fks
        return fks

    def foreign_keys(self, table):
        """Foreign keys declared *on* *table*."""
        return list(self._load_foreign_keys().get(table.upper(), []))

    def children(self, table):
        """Tables holding a foreign key to *table*."""
        table = table.upper()
        out = []
        for owner_table, fks in self._load_foreign_keys().items():
            for fk in fks:
                if fk.parent == table and owner_table not in out:
                    out.append(owner_table)
        return sorted(out)

    def declares_foreign_keys(self):
        """Whether this schema declares any foreign keys at all.

        A schema with none is a different situation from a pair of
        tables with no path between them, and :meth:`join_path` refuses
        to conflate the two.
        """
        return bool(self._load_foreign_keys())

    def join_path(self, a, b):
        """The foreign-key chain from *a* to *b*, or ``None``.

        Returns a list of :class:`ForeignKey`, each step joining the
        previous table to the next, so the caller composes SQL from
        facts rather than from assumptions.  Follows keys in both
        directions -- a parent is as reachable from a child as the
        reverse -- and breadth-first, because the graphs are small and
        the shortest path is the one wanted.

        Raises :class:`SqlplusSchemaError` when the schema declares no
        foreign keys whatever.  ``None`` would read as "no path between
        these two" when the truth is "there was nothing to search".
        """
        a, b = a.upper(), b.upper()
        fks = self._load_foreign_keys()
        if not fks:
            raise SqlplusSchemaError(
                'schema %s declares no foreign keys, so there is no path to '
                'find between %s and %s. Whether to fall back to matching '
                'column names is the caller\'s decision to make knowingly.'
                % (self.owner, a, b))
        if a == b:
            return []

        # table -> [(neighbour, ForeignKey)], both directions.
        graph = {}
        for table, table_fks in fks.items():
            for fk in table_fks:
                graph.setdefault(table, []).append((fk.parent, fk))
                graph.setdefault(fk.parent, []).append((table, fk))

        queue = [(a, [])]
        seen = set([a])
        while queue:
            here, path = queue.pop(0)
            for neighbour, fk in graph.get(here, []):
                if neighbour in seen:
                    continue
                step = path + [fk]
                if neighbour == b:
                    return step
                seen.add(neighbour)
                queue.append((neighbour, step))
        return None
