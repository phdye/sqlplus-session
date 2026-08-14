# Two layers the package is missing

Proposal, 14 August 2026. Written by a caller, for whoever implements.

`query()` returns a list of strings. Everything above that -- turning those
strings into columns, and knowing what columns exist in the first place --
is left to the caller, so every caller writes it again. Two have now been
written independently and both carried a bug that a shared implementation
would have carried once, or not at all.

The proposal is that the package own three layers rather than one:

1. transport, which it already does
2. result decoding: rows and scalars out of line-oriented output
3. schema: what tables, columns and keys actually exist

Domain knowledge stays with the caller. That line is drawn at the end of
this document and it matters more than either API.

---

## Layer 2: result decoding

### The problem

sqlplus emits lines. A query with three columns has to be encoded into one
before it crosses the pipe, and decoded on the other side. The convention
that works is to concatenate with an improbable separator and wrap each
column so a NULL survives the round trip:

```sql
SELECT NVL(TO_CHAR(a),'<NULL>')||'~|~'||NVL(TO_CHAR(b),'<NULL>') FROM t
```

and split on the way back. Without the NVL a null column collapses two
fields into one and every row after it is misaligned. Without a separator
that cannot occur in the data, the same happens on the first row that
contains one.

Every caller needs this and nobody gets it right the first time.

### The bug it already cost

A caller selected a key that was itself a concatenation of four columns,
then asked its own decoder for one column:

```python
keys = [r[0] for r in rows("SELECT k FROM (SELECT <4 cols concatenated> k ...)", 1)]
```

The decoder splits on the separator, counts the fields, and skips any row
whose width does not match what was asked for. The key had four fields, one
was requested, so every row was discarded. No exception, no warning, an
empty list, and a report stating it had measured zero of fifty. It took a
run against a real instance to notice, because the offline stub had been
written to the same misunderstanding.

Silently dropping rows that do not match an expected width is a property of
the decoder, not of the caller. It belongs somewhere it can be got right
once and tested once.

### Proposed API

```python
sess.rows(sql, ncols, timeout=None)   -> list of tuples of str
sess.scalar(sql, timeout=None)        -> str or None
sess.raw(sql, timeout=None)           -> list of str   (query() as it is now)

sqlplus_session.cat(*exprs)           -> the NVL/concatenate projection
sqlplus_session.SEPARATOR, NULL_TOKEN
```

Four things worth deciding rather than inheriting:

- A row whose width does not match should not be silently dropped. Raise,
  or return it and let the caller judge, or expose a discard count. Silence
  is what made the bug expensive.
- `cat()` and the split have to agree about the separator and the null
  token. That is one decision and should live in one place.
- Better still, derive the width from the number of expressions given to
  `cat()` so the two cannot disagree. That removes the class rather than
  the instance.
- `SET LINESIZE` bounds how wide a concatenated row can be before sqlplus
  wraps it, and a wrapped row decodes as garbage. The package sets
  LINESIZE in its own defaults, so it is the right place to know the
  ceiling and to say something useful when a projection exceeds it.

---

## Layer 3: schema

### The problem

A caller that wants to inventory a schema it does not control has to ask
the dictionary what is there. Doing that well means `ALL_TABLES`,
`ALL_TAB_COLS` for hidden and virtual columns, and `ALL_CONSTRAINTS` with
`ALL_CONS_COLUMNS` for the keys. Doing it badly means matching column names
against patterns and hoping.

Callers do it badly, because the dictionary queries are tedious and the
pattern matching looks like it works.

### The bug it already cost

A caller needed the table holding a large object keyed to another table. It
looked for any table with a LOB column and a key column whose name
contained the right substring. A different table -- unrelated, but carrying
a properties CLOB and a key that happened to match -- was found first. The
report then said the count was zero.

Zero reads as an answer. It was an absence, and the two are not the same
thing in a document somebody makes decisions from.

The dictionary knew the truth the whole time: there is a foreign key from
the one table to the other, and no such key to the impostor. A relationship
is a fact to be read, not a pattern to be matched.

The same caller inferred "newest version of each row" from `MAX(version)`
and got several rows per parent, because the schema numbered several
versions 0. A primary key would have told it which column actually
identifies a row.

### Proposed API

```python
sch = sess.schema('OWNER')            # or Schema(sess, 'OWNER')

sch.tables(like=None)                 -> list of table names
sch.columns(table)                    -> list of Column(name, type, length,
                                                        nullable, hidden, virtual)
sch.primary_key(table)                -> list of column names
sch.foreign_keys(table)               -> list of FK(columns, parent, parent_columns)
sch.children(table)                   -> tables holding a FK to this one
sch.join_path(a, b)                   -> the FK chain from a to b, or None
sch.lobs(table)                       -> columns whose type is a large object
```

Notes on the shape:

- Loaded once per schema and cached. Row counts should not be part of it:
  they are a separate, expensive question and the caller can ask.
- `like=` matters. A caller inventorying a large schema does not want every
  table, and pushing the filter into the dictionary query is much cheaper
  than filtering client-side.
- `join_path` is what turns the whole thing from a listing into a facility.
  Given two tables it should return the columns to join on, so the caller
  composes SQL from facts rather than from assumptions. Breadth-first over
  the FK graph is enough; the graphs are small.
- Not every schema declares its foreign keys, and not every login can read
  `ALL_CONSTRAINTS`. Both cases need to be visible rather than silently
  degrading to nothing, so the caller can decide whether to fall back to
  name matching or to stop.
- Types are strings from the dictionary. Resist normalising them. A caller
  that needs to know a column is a BLOB rather than a CLOB is asking a
  question the two answer differently, and flattening that to "large
  object" would have hidden it.

---

## Where the line goes

In:

- getting rows and scalars out of the pipe
- what tables, columns, keys and relationships exist
- how to join two tables the dictionary knows are related

Out:

- which table means what. A caller looking for a particular kind of table
  knows its own vocabulary; the package does not and should not learn it.
- role resolution: deciding that one column is the name and another is the
  effective date. That is domain judgement dressed as introspection.
- what to do with a large object once found. Reading one for text, counting
  bytes in one, searching inside one -- those are caller questions with
  caller answers.
- read-only enforcement. A caller that must not write should refuse to send
  anything that is not a SELECT, and that policy belongs to the caller that
  has it, not to a general session library. Worth stating explicitly because
  it looks like it belongs here.

The test for anything proposed later: could a caller in an unrelated domain
use it unchanged? Rows and keys pass. Anything that needs to know what a
table is for does not.

---

## Cost, and the argument against

This roughly doubles the package. Today it is one clear thing -- a
persistent session over pipes, stdlib only -- and that clarity is worth
something. Layer 2 is small and unambiguously the package's own business:
it is about getting usable data out of the pipe it already owns. Layer 3 is
a data-dictionary library with its own test surface, and a reasonable
person would put it in a separate package that depends on this one.

The case for putting it here anyway is that the two are useless apart. A
schema facility needs a row decoder, and a row decoder with no schema
behind it leaves every caller writing the same dictionary queries. Splitting
them across two packages means two versions to keep in step for no gain
that a module boundary would not also give.

Constraints to respect: stdlib only, Python 3.2.8 floor, and no PL/SQL --
the dictionary queries must be plain SELECTs so that a caller enforcing
read-only can run them.

---

## Evidence

Both bugs are in the caller, not in this package. They are here because a
shared implementation is the reason they would not recur.

| symptom | actual cause | what would have prevented it |
|---|---|---|
| body sample reported 0 of 50 measured, no error | key of four fields decoded as one column, every row discarded | a decoder that does not drop rows in silence |
| stage reported a count of 0 | wrong table matched by column-name pattern | foreign keys read from the dictionary |
| sample of 50 covered 32 distinct parents | `MAX(version)` where several rows shared a version number | a primary key, read rather than inferred |

The first cost a five-minute run against a live instance and produced a
report with two sections in it. The third was found only by reading the
output carefully; nothing in the run said anything was wrong.
