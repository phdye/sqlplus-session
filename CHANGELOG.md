# Changelog

Versions are read from `sqlplus_session/__init__.py`; `setup.py` no longer
carries its own copy.

## 0.9.1 — 2026-09-19

`pyproject.toml` declares the build system, which the distribution never
did. Undeclared, pip guesses, and an old pip with no `wheel` in the
environment guesses the deprecated way:

    Using legacy 'setup.py install' for sqlplus-session,
    since package 'wheel' is not installed.

That install works and then stops working, because recent pip has
removed the fallback. Measured here on pip 21.3.1 against 3.6.9: without
the file, the legacy path; with it, `Building wheel for sqlplus-session
(PEP 517)` and a wheel, whether or not `wheel` is installed.

The metadata stays in `setup.py` rather than moving to a `[project]`
table. That table needs setuptools 61, and the interpreter this package
exists to support carries 41, so the modern spelling would make the
package unbuildable on its own target.

One thing the file costs, on a machine with no route to PyPI:
`pip install . --no-build-isolation` now wants `wheel` present and fails
with `invalid command 'bdist_wheel'` where it is missing. The default,
isolated path needs nothing, and the failure names what it wants.

## 0.9.0 — 2026-09-19

The benchmark gets the treatment the runner got, and for the same two
reasons it was flagged.

`tools/benchmark.py` is now `sqlplus_session/benchmark.py`, without the
`sys.path` insert that let a file outside the package import it. It is
run as `python -m sqlplus_session.benchmark` and gets no console script:
a timing harness has no business on the `PATH` of anyone who installs the
library, and the module form is a whole interface already. `tools/` is
empty and gone.

`-p/--password` is gone with it, replaced by `-P/--password-file`. An
argument is readable by every other user on the box through `ps` and
lands in shell history besides, and the old option's own help text said
to prefer `--env-file` — which is an admission rather than a mitigation.
`-V` joins `--version`, which had only the long spelling.

`read_password_file()` and `password_file_is_exposed()` move out of the
runner and into the package, exported. Two tools reading the same file
shape is how they come to disagree about a detail, and the detail here is
a trailing `\r`: a password file written on the Windows side carries
CRLF, and the carriage return that survives into the CONNECT line comes
back from Oracle as a wrong password. One reader, one answer. It raises
`IOError` like `load_env_file` does, and `sqlrun` translates that into
its own usage error at the edge where it knows the option's name.

## 0.8.0 — 2026-09-19

The script runner becomes part of the distribution rather than a file in
`tools/`. `sqlplus_session/sqlrun.py` is an ordinary module of the
package, `sqlrun` is a `console_scripts` entry point, and the three lines
of `sys.path` surgery that let a file in `tools/` find the package it
imports are gone — they work from a checkout and fail from a wheel, which
is the wrong way round for the half that ships.

`srun` was the obvious name and is SLURM's job launcher, present on most
of the machines that would pip-install anything. `sqlrun` collides with
nothing. The environment prefix follows the command, so the variables are
`SQLRUN_SQLPLUS`, `SQLRUN_TIMEOUT` and the rest; the credentials still
answer to `DB_USERNAME`, `DB_PASSWORD` and `DB_NAME`.

`python_requires` stays at 3.2.8. It describes the library, which is what
has to run on the client's interpreter, and raising it to satisfy a
console script would refuse the library to the one platform it was
written for. The command checks the interpreter itself and says which
version it wants; the library underneath is unaffected and the message
says so.

Packaging metadata that a PyPI upload actually needs and did not have:
`long_description_content_type`, without which the README renders as
unformatted text, `url`, and a `MANIFEST.in` — `setup.py` reads
`README.md` at build time, so an sdist lacking it cannot be built from at
all, which nobody notices until an install falls back from the wheel.

Four tests pin the relocation: the module's own name, the entry point
naming a callable that exists, the absence of any `sys.path` line, and
the stated interpreter floor. The suite drives the command as
`python -m sqlplus_session.sqlrun`, so a checkout is testable without
being installed.

## 0.7.0 — 2026-09-19

`tools/srun.py` runs one SQL*Plus script and prints what sqlplus printed.
It replaces a shell wrapper that had grown up beside the package and that
put `$DB_USERNAME/$DB_PASSWORD@$DB_NAME` on the sqlplus command line,
where `ps`, `/proc/<pid>/cmdline` and any process accounting could read
it. The credential now goes down the stdin pipe, which is what the
package was built to do and what the wrapper could not.

Three behaviors are deliberately not the wrapper's.

The tool exits 1 when the output carries an `ORA-`, `TNS-` or `SP2-`
line. The wrapper exited 0 whatever Oracle said, so a caller checking
`$?` was told a failed run had succeeded — the same clean-exit-means-
nothing-there shape the row decoder exists to prevent.
`--no-fail-on-error` restores it for a caller that reads the output
itself.

There is no `--password`, only `--password-file` and `$DB_PASSWORD`. A
path is not a credential; an argument is.

Positional parameters are quoted on the way to the `@` line. The shell
handed sqlplus its arguments already separated; a pipe is a line of text,
and `srun.py report.sql 'two words'` would otherwise reach the script as
two parameters.

Interface: docopt-shaped usage, short bundling (`-qv`), attached values
(`-w30`), a `--no-` twin for every boolean, `SRUN_*` for every setting
that is not a credential, `-h`/`-V`/`-v`/`-t`/`-d`/`-q`. Long options do
not abbreviate; the bundles are expanded before the parser sees them,
because argparse stops bundling when abbreviation is turned off. The
Usage block is written by hand and the parser is generated from a table,
so a test reads the one and compares it against the other — the whole
cost of emulating docopt rather than depending on it.

Credentials keep the package's variables rather than growing `SRUN_USER`
and `SRUN_TNS` beside them. Two names for one setting is how they come to
disagree. One divergence from the older tools: an empty value out of an
`--env-file` counts as one the file did not set, so `$DB_USERNAME` still
shows through. The subshell cannot tell unset from empty, and the other
reading makes a file mentioning none of the three silently select
external authentication.

### Package

`run_file()` takes `args`, the script's positional parameters, and quotes
each. An argument containing a double quote is refused: sqlplus has no
escape for one, and truncating there substitutes a value nobody wrote
into a statement that then runs.

`errors_in()` is public. A script whose last line is `EXIT` takes sqlplus
down before the sentinel returns, so its output arrives on `SqlplusDied`
and never passes the normal scan; a runner deciding an exit code from
that output should use the session's own patterns rather than keep a
second copy that can drift.

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

The schema suite is read-only by default and checks invariants against
whatever schema is there, because the dictionary is all this layer reads in
production and the account running the tests may be no more privileged than
that. `--create-objects` builds the fixture schema and runs the assertions
that need it; `--schema-owner` points the read-only checks somewhere
populated. The first version required `CREATE TABLE` unconditionally and
produced 32 identical `ORA-01031` tracebacks — 1300 log lines for one fact
— on an account that did not have it.

`ForeignKey` gained `parent_owner`. A key can point out of the schema it is
declared in, and "the parent is not in this schema's table list" then means
elsewhere rather than missing.

`pytest.ini` sets `addopts = -rs`, so every skip prints its reason. Pytest
reports only a count by default, which is the same shape as the bug the row
decoder exists to prevent: an empty result and a clean exit reading as
"there was nothing to find". Reporting only — no privilege or environment
is assumed there.

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
