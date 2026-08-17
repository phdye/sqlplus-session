# Where the package fits, and where it doesn't

Notes from a caller, 16 August 2026. Written during a session that built a
non-CDB Oracle 19c database on Windows and moved four schemas into it, and
kept asking whether this package was the right tool for each step. Mostly it
was not, and the reasons are more useful than the conclusion.

Nothing here is a defect report. The package did what it says. This is about
the boundary of what it says, which is not currently written down anywhere a
caller would look before reaching for it.

---

## The session, in one paragraph

Export four schemas out of a PDB with `expdp`. Register a Windows service
with `oradim`. Bootstrap a database with `CREATE DATABASE` from a pfile.
Build the dictionary with `catalog.sql` and `catproc.sql`. Import the dump
with `impdp`. Verify the result. Six steps; the package is a good fit for
exactly one of them, a plausible fit for a second, and the wrong tool for
the other four.

---

## Where it does not fit, and why

### The other Oracle binaries are not sqlplus

`expdp`, `impdp`, `oradim`, `rman`, `nid`, `lsnrctl` and `dbca` are separate
executables with their own argument grammars and their own exit codes. They
do not take SQL on stdin, so nothing about a persistent sqlplus session
applies. A caller driving a migration end to end spends most of its time in
these, not in sqlplus, and the package has nothing to say about them.

This is not a request that it wrap them. `oradim` alone would drag in
Windows service semantics, an interactive password prompt on stdin, and a
message catalog that fails in a way that reports a message number Oracle
does not document. That is a different package if it is anything.

What would help is the README saying plainly that the package is for
sqlplus, so a caller planning a migration knows to budget for the rest
rather than discovering it one binary at a time.

### Long-running install scripts are the wrong shape

`catalog.sql` and `catproc.sql` are single invocations that run for minutes
and emit tens of thousands of lines. The connection cost the package exists
to amortise is a rounding error against that. `run_file()` will start them,
but starting them was never the hard part.

Two properties actively work against this use:

`default_timeout` is 60 seconds and these run far past it. A caller must
pass a per-call timeout large enough for the slowest script on the slowest
machine, which is a number nobody knows in advance, and `SqlplusTimeout`
leaves the session dead rather than merely late -- an expensive way to find
out the estimate was low.

`on_error='raise'` with the default `ORA-` pattern is wrong for this output.
The dictionary build drops objects that do not exist yet and reports it,
repeatedly, and that is normal. A caller would have to set `on_error='return'`
and then decide for itself which of thousands of `ORA-` lines mattered --
which is to say, do the whole job the error detection was there to do.

The mechanism these scripts actually need is `SET ECHO ON` plus `SPOOL` to a
file, then read the file afterwards. That is sqlplus's own facility and it
works; the package neither helps nor hinders. Calling `sqlplus` directly for
these is not a workaround, it is the right call.

### A crashing instance is not a query error

`CREATE DATABASE` can take the instance down with it. In this session a bad
`REUSE` clause produced `ORA-01092: ORACLE instance terminated`, which means
the process on the other end of the pipe is gone and every subsequent
statement in the same script fails for a reason that has nothing to do with
the statement.

`SqlplusDied` covers the transport fact. What a caller wants to know is
whether the instance died, which is a different question, and after a
`STARTUP NOMOUNT` / `CREATE DATABASE` sequence it is the only question.
Bootstrap work is inherently a series of connections to an instance that is
being rebuilt underneath them, so a session object that assumes one durable
connection is modelling the wrong thing.

---

## Where it fits well

### Verification after a migration

This is the case the package is built for and the session's clearest win.

After moving four schemas between databases, the questions are: did every
account arrive, does each own the objects it owned before, do the row counts
match, and did anything land `INVALID`. That is a dozen or more small
queries, run against two databases, compared pairwise. Connection cost
matters at that granularity, and `rows()` returning typed tuples removes the
output parsing that this session got wrong twice by hand.

Concretely, both of these were mistakes made while poking at sqlplus output
directly, and both are the kind the package is meant to prevent:

- Queried `file_id` from `v$datafile`, which does not have that column; it is
  `file#`. A dictionary-aware layer would have said so before the query ran.
- Piped multi-line SQL to `sqlplus` on stdin and had it mangled -- `prompt`
  lines swallowing the statements after them, `SP2-0734` on every following
  line. Fixed by writing `.sql` files and using `@`, which is what the
  package does internally anyway.

### Schema comparison specifically

`schema()` is a better verification than "the import reported no errors".
`sch.tables()`, `sch.columns()`, `sch.primary_key()` and `sch.foreign_keys()`
read the dictionary, so comparing source and target becomes a diff of two
structures rather than a diff of two text reports. An import can succeed and
still leave a constraint behind; only the dictionary knows.

This suggests a use the package does not currently name: two sessions open
at once, one per database, compared. Nothing prevents it, but nothing in the
README suggests it either, and it is the natural shape of migration
verification.

---

## Environment notes, for anyone using this on the Cygwin box

Not defects. Facts a caller needs before the first call.

- The package targets the Python 3.2 dialect. The rhel root runs 3.6.9 and
  the primary root 3.9.16, so either will run it, but a caller editing it
  must stay in the older dialect: no f-strings, no `shutil.which`.
- `path_converter` exists for Cygwin and should be pointed at `cygpath`.
  Whether the Windows `sqlplus.exe` or a Cygwin wrapper is on `PATH` decides
  which direction paths need converting; this box has a wrapper at
  `~/.local/bin/sqlplus` that already handles argument conversion, so
  stacking both would convert twice. Worth checking which layer owns it
  before setting the parameter.
- The wrapper resolves `sqlplus` off `PATH` and this box has two real ones
  (`dbhome_1` and `client_1`). Passing `sqlplus_cmd` explicitly removes the
  ambiguity where it matters.
- `/@ALIAS` wallet authentication works here and is the right way to avoid
  putting a password in a script. `SqlplusSession('', '', 'ORCLPDB1')` is the
  documented form.

---

## What would make the boundary obvious

One paragraph in the README, near the top, saying what the package is for
and what it is not: sqlplus sessions, queries and result decoding; not Data
Pump, not service management, not dictionary installs, not instance
lifecycle. A caller reading that budgets correctly on day one instead of
discovering the edges during a migration.

The rest of this document is that paragraph's evidence, not a request for
more surface area. The package is better for being narrow. It is only worse
for not saying so.
