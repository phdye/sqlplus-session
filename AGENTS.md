# sqlplus-session

A Python package that keeps one `sqlplus -s /nolog` process alive over
stdin and stdout, so a caller connects once and runs many statements on
the same Oracle session. Stdlib only: no cx_Oracle, no python-oracledb,
nothing from PyPI. `README.md` is the user's documentation and the
statement of the public API; this file is for working on the package.

## Risks, before anything else

**The dangerous failure is a plausible wrong answer.** The package
parses text that sqlplus prints. When it gets that wrong it rarely
raises: it returns every row twice, or no rows and a clean exit. The
existing code refuses to guess for this reason. A row of the wrong width
raises, `join_path` raises rather than returning `None`, and the
integration suite fails rather than skipping when it has no target. New
code follows the same rule: when output cannot be decoded with
confidence, raise.

**Statement termination is decided from the text.** `_terminate_sql` and
`_terminate_setup` in `sqlplus_session/session.py` decide whether input
is SQL, PL/SQL or a SQL*Plus command, and append `;`, `/` or nothing to
match. A wrong call runs a statement twice, or leaves it in the buffer
to be joined to whatever comes next. Any change there needs a test for
each statement shape it touches, and real PL/SQL blocks (`BEGIN ... END;`,
`DECLARE ... END name;`) must still end with `/`.

**Credentials never reach a command line.** sqlplus starts as `/nolog`,
and `CONNECT` is written over the stdin pipe with the password quoted by
the package, so nothing secret shows up in `ps` or `/proc/<pid>/cmdline`.
Do not move the password to the prompt line that follows `CONNECT`: with
stdin on a pipe sqlplus never prompts, and parses that line as more
arguments (the README records what was measured). A new command takes a
password file or the environment, never a password option.

**`None` and `''` are different credentials.** `None` asks the
environment (`DB_USERNAME`, `DB_PASSWORD`, then `DB_NAME`, `TWO_TASK` or
`ORACLE_SID`). `''` states an answer: external authentication, wallet or
OS. Merging the two turns every wallet connection into a password
connection as soon as `DB_USERNAME` is exported.
`test_empty_string_is_an_answer_not_a_question` covers this. If it
fails, read the test before changing it.

**Nothing in the repository names the environment it was built for.**
The package was written for one consumer and carries nothing of that
consumer: no organization, host, bootstrap script, filesystem layout, or
credential variable other than the package's own. History was recreated
once to make that true. Keep it out of code, tests, documentation and
commit messages.

**The interpreter floor is Python 3.6.8**, for the library and the
`sqlrun` command alike. It is stated twice, as `python_requires` in
`setup.py` and as `PYTHON_FLOOR` in `sqlplus_session/sqlrun.py`, and a
test holds the two equal. Nothing from 3.7 or later: no `text=` or
`capture_output=` on `subprocess`, no `dataclasses`, no
`from __future__ import annotations`. Much of the code was first written
for 3.2 (`%`-formatting, `super(Class, self)`, `universal_newlines=True`).
That style is not a defect, and is not rewritten for its own sake. One
of those choices still matters at any version: the reader thread uses
`iter(pipe.readline, '')`, not `for line in pipe`, which reads ahead and
can deadlock it.

## How it works, in one paragraph

Each statement is followed by `PROMPT __EOQ__<n>__`, and a daemon thread
reads stdout into a queue until that numbered sentinel appears. The
number keeps a late sentinel from an earlier statement from ending the
current one. A timeout kills the process, because a session that missed
its sentinel is in an unknown state; the caller opens a new one. An
`ORA-`, `TNS-` or `SP2-` line does not end the session
(`WHENEVER SQLERROR CONTINUE`). It is found by scanning the output, then
raised or returned according to `on_error`.

## Layout

| Path | What it holds |
|---|---|
| `sqlplus_session/session.py` | `SqlplusSession`, credential resolution, statement termination, the sentinel protocol |
| `sqlplus_session/rows.py` | `cat()`, `Projection`, and decoding lines into rows |
| `sqlplus_session/schema.py` | Tables, columns and keys read from the data dictionary |
| `sqlplus_session/_errors.py` | The exception hierarchy, rooted at `SqlplusError` |
| `sqlplus_session/sqlrun.py` | The `sqlrun` console script |
| `sqlplus_session/benchmark.py` | Timings, run with `python3 -m`; deliberately not installed as a script |
| `tests/` | Unit tests against `tests/fake_sqlplus.py`, and integration suites that need a live instance |
| `reference/` | `srun.sh`, a one-shot sqlplus wrapper kept for reference; shipped in the sdist |
| `log/error/` | Tracked transcripts of failures that a fix was made from |

The version is stated once, in `sqlplus_session/__init__.py`, and
`setup.py` reads it from there. Distribution metadata stays in
`setup.py`. `pyproject.toml` only declares the build system, because a
`[project]` table needs setuptools 61 and the target interpreter carries
41. `MANIFEST.in` decides what the sdist holds, and `setup.py` reads
`README.md` at build time, so the README must stay in it.

## The annex

`a/` is a working annex. It is ignored, never committed, and nothing
committed refers to a file inside it. `a/issue/` holds defect reports
and proposals this repository owns; `a/handoff/` holds session
handoffs. Read `a/issue/` before changing the code, since a report there
may already describe the fault and the fix. Every report opens with a
`Status:` line, and that line is the only statement of its state.
Handoffs are dated snapshots and some are out of date; `main` is the
authority.

## Verifying a change

Unit tests need no Oracle and no pytest:

    cd tests
    python3 -m unittest test_functionality test_rows test_encoding \
        test_fold test_benchmark test_sqlrun test_lint

Run them on the floor's interpreter too, as close to 3.6.8 as is
available, since that is where a 3.7-only call fails. `test_lint` runs
pyflakes over the tree and skips with a reason where pyflakes is not
installed.

The fake sqlplus models only what it was written to model. A change to
connecting, termination, error detection or decoding is not verified
until the integration suites have run against a real instance:

    pytest tests/test_oracle_integration.py --tns ALIAS
    pytest tests/test_schema_integration.py --tns ALIAS --schema-owner OWNER

`--create-objects` lets the schema suite build and drop its own `SPS_*`
tables, which needs `CREATE TABLE`, so it is off by default. `pytest.ini`
sets `-rs`, which prints the reason for every skip.

A claim about how sqlplus or Oracle behaves is measured, not reasoned,
and the text that states it names the version measured against.

## Conventions

- Commits are conventional (`type(scope): summary`), imperative, one
  logical change each. No attribution trailers.
- A behavior change updates `README.md` in the same series. A release is
  its own commit, `chore(release): X.Y.Z`, which bumps
  `sqlplus_session/__init__.py` and adds a `## X.Y.Z — YYYY-MM-DD` section
  to `CHANGELOG.md` saying what changed and why.
- Comments explain why, and where a reason was measured they say so.
- A tracked file is executable if and only if it starts with `#!`.
  Today that is `tests/fake_sqlplus.py` and `reference/srun.sh`.

## Project state

As of 2026-10-08, `main` is at 0.11.0.

- A refused `CONNECT` is retried on the same process, once by default
  (0.11.0): `connect_retries`, else `SQLPLUS_SESSION_CONNECT_RETRIES`.
  Keep the default at one; each retry with a wrong credential spends
  some of the account's failed-logon allowance.
- Termination follows how a statement starts (0.10.1). The two defects
  recorded in `a/issue/` for it, a query ending in `CASE ... END` that
  ran twice and `SET TRANSACTION`/`ROLE`/`CONSTRAINT(S)` left
  unterminated in `setup_commands`, are fixed.
- Administrative connect modes (`AS SYSDBA` and the rest) are proposed
  and built on the `admin-modes` branch, but are not on `main`.
- Five branches (`admin-modes`, `benchmark-credential`,
  `connect-identifier`, `release-handles`, `proposal-0002-part-1`) hold
  work that never reached `main`, including a `doc/` tree with a
  specification, proposals and decision records. Each is 24 commits
  behind `main`. Whether to merge, port or drop them is undecided.
