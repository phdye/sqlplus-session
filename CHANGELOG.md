# Changelog

Versions are read from `sqlplus_session/__init__.py`; `setup.py` no longer
carries its own copy.

## 0.5.1 — 2026-08-14

`setup_commands` entries are terminated when they are SQL and left alone
when they are SQL*Plus commands. An unterminated `ALTER SESSION` sat in
sqlplus's buffer and swallowed the probe query, so Oracle received

    ALTER SESSION SET NLS_DATE_FORMAT = '...' SELECT 1 FROM DUAL

and answered `ORA-00922: missing or invalid option` — a parse error naming
a statement the caller never wrote. The default setup list is all SQL*Plus
commands and never tripped it. One `ALTER SESSION` from a caller does.

Terminating everything is not the alternative: `SET PAGESIZE 0;` is an
error. The first word decides.

## 0.6.0 — 2026-08-14

Implements `a/issue/2026-08-14-rows-and-schema.md`.

### Result decoding

`query()` returns lines. Turning them into columns was left to every
caller, so every caller wrote it again, and two wrote the same bug.

```python
p = cat('id', 'name', 'created')
for row in sess.rows(p.select('FROM employees WHERE dept = 10')):
    ...
```

`cat()` builds the `NVL(TO_CHAR(...))` projection and the separator joins;
the `Projection` it returns knows how many expressions went into it, and
`select()` carries that count — and the separator and null token — into the
statement. There is no second place to state the width, so there is no way
for the two to disagree. That disagreement is the bug: a caller asked for
one column from a key that was four columns concatenated, every row was
discarded, and the report said it had measured zero of fifty.

A row whose field count does not match now raises `SqlplusRowWidthError`
naming the line, what it found and what it expected. `on_short='return'`
hands the row back whole and `'skip'` restores the old behaviour, which is
available and is not the default. Silence is what made this expensive.

`scalar()` returns one value, or `None`, and raises rather than picking the
first of several. `raw()` is `query()` under a name that reads alongside the
other two.

`NULL` decodes to `None`, not `''`, so it stays distinguishable from an
empty string — pass `null=''` for the older shape.

`linesize=` is now a constructor argument. It bounds how wide a row can be
before sqlplus wraps it, and a wrapped row decodes as garbage; it was
adjustable only by restating the whole `setup_commands` list to change one
number. `session.linesize` reports the effective value either way, and a
decode failure on a line that long says so instead of blaming the
projection.

### Schema

```python
sch = sess.schema()                     # or sess.schema('OTHER_OWNER')
sch.tables(like='INVOICE%')
sch.columns('INVOICE')                  # incl. hidden and virtual
sch.primary_key('INVOICE')
sch.foreign_keys('INVOICE_LINE')
sch.children('INVOICE')
sch.join_path('INVOICE_LINE', 'CUSTOMER')
sch.lobs('DOCUMENT')
```

Read from `ALL_TABLES`, `ALL_TAB_COLS` and `ALL_CONSTRAINTS` joined to
`ALL_CONS_COLUMNS`. Plain SELECTs, no PL/SQL, so a caller enforcing
read-only can run them. Loaded lazily and cached; row counts are
deliberately not part of it.

`ALL_TAB_COLS`, not `ALL_TAB_COLUMNS`: the latter omits hidden columns, and
a column you cannot see is the one that surprises you later.

`join_path` returns the foreign-key chain between two tables, following
keys in both directions, so a caller composes SQL from declared facts
rather than from a guess about which column joins to which. It raises
rather than returning `None` when the schema declares no foreign keys at
all — `None` would read as "no path between these two" when the truth is
"there was nothing to search". `declares_foreign_keys()` asks that
directly, and a login that cannot read `ALL_CONSTRAINTS` gets
`SqlplusSchemaError` rather than a silent empty answer.

Types stay as the dictionary spells them. A caller that needs to know a
column is a `BLOB` rather than a `CLOB` is asking a question the two answer
differently.

Out of scope on purpose: which table means what, which column is the name,
what to do with a large object once found, and read-only enforcement. A
caller looking for a particular kind of table knows its own vocabulary; the
package does not and should not learn it.

Tested against a real dictionary rather than a stub — including a decoy
table carrying a CLOB and a column called `PARENT_ID` and no relationship
to anything, which is the shape that fooled a name matcher into reporting a
count of zero. A stub written to the same misunderstanding as the caller
agrees with the caller and proves nothing.

## 0.5.0 — 2026-08-14

`tests/test_spike_oracle.py` becomes `tests/test_oracle_integration.py`, a
pytest suite rather than a script with a `main()`. It stopped being a spike
some time ago.

Credentials now come from the package. Options — `--user`, `--password`,
`--tns`, `--env-file`, `--sqlplus` — win over the environment, and anything
omitted is passed as `None`, which is exactly what tells
`resolve_credentials()` to consult `DB_USERNAME`, `DB_PASSWORD` and
`DB_NAME`/`TWO_TASK`/`ORACLE_SID`. No test reads one of those names itself.

With no connect target the suite fails and says which variable to set,
rather than skipping. It also fails when the sqlplus binary is not there.

Twenty tests, up from six checks, including two the old script never made:
`run_file()` against a real instance, and a read of
`/proc/<pid>/cmdline` on the live process to confirm no credential reached
the command line.

Timings moved out to `tools/benchmark.py`, a CLI in the house style —
docopt-shaped usage, `-h`/`--version`/`-v`/`-t`/`-d`, options over
environment. Wall-clock does not belong in an assertion.

## 0.4.0 — 2026-08-14

The password moves onto the `CONNECT` line, double-quoted. It is still
written to stdin, so it still never reaches `ps` or `/proc/<pid>/cmdline` —
that part was never in question.

What was in question was the line *after* `CONNECT`. 0.2.0 put the password
there on the theory that sqlplus reads it verbatim, the way it does when it
prompts at a terminal. It does not. With stdin on a pipe, sqlplus 19c never
prints the prompt and parses that line as more `CONNECT` arguments:

| password contains | old behavior |
|---|---|
| `@` | connect hangs until `connect_timeout` |
| `/` | `SP2-0306: Invalid option.` |
| space | `SP2-0306: Invalid option.` |

All three now work, verified against eight accounts on Oracle 19c.

The supported floor is now Python 3.2.8. The suite passes under 3.2.5 in
`cyg325`, under 3.6.9 on the RHEL 8.10 replica, and under 3.9.16 on primary
Cygwin.

`tests/test_spike_oracle.py` skips its per-call baseline when the password
has punctuation in it. The baseline puts the login string on the command
line by design — that is the pattern being compared against — and a Windows
`sqlplus.exe` reached through Cygwin mangles the quoting no matter how it is
written.

## 0.3.0 — 2026-08-13

`load_env_file` hardening: stderr to `os.devnull` rather than an undrained
pipe, `expanduser` moved in so `~/.dbenv` works through both entry points,
and the sourcing script composed from `ENV_CONNECT` so the two cannot drift.

## 0.2.0 — 2026-08-13

`DB_USERNAME` / `DB_PASSWORD` / `DB_NAME` become the package's own
convention. All three constructor arguments default to `None`, meaning ask
the environment; `''` still means external authentication. `load_env_file`
and `SqlplusSession.from_env_file` replace the three ad-hoc readers that had
grown up around the repository.

## 0.1.0 — 2026-08-13

Initial package. `SqlplusSession` over stdin/stdout pipes, numbered sentinel
protocol, reader thread with per-query timeouts, ORA-/TNS-/SP2- error
scanning.
