# Handoff: credential handling, 13 August 2026

The package no longer puts the password on the sqlplus command line, and it now
owns the credential convention instead of leaving each caller to invent one.
Version went 0.1.0 to 0.3.0 across two commits. A third is pending with the
hardening described below and this file.

Everything here was verified against `tests/fake_sqlplus.py`. Nothing has been
run against a real database since the change. That is the top open item and it
is not a formality: see "What has not been proved".

## State

| | |
|---|---|
| branch | `main`, no remote configured |
| commits | `ea1dab6`, `f2b4b9b`, plus one uncommitted |
| version | 0.3.0 in `setup.py` and `sqlplus_session/__init__.py` |
| tests | 64, about 5.5s, no Oracle and no sqlplus required |

Run them with `cd tests && python3 -m unittest test_session`.

## What the password did before

`session.py` built `'%s/%s@%s' % (username, password, connect_string)` and
passed it as `argv[2]` to sqlplus. The comment above it called this "the
standard sqlplus pattern", which it is, and that is the problem: the credential
sits in the process table for the life of the session, readable by `ps`, by
`/proc/<pid>/cmdline`, and by anything that records process arguments.

`ea1dab6` starts `sqlplus -s /nolog` instead and authenticates over the same
stdin pipe the queries already use.

The password does not go on the `CONNECT` line either. The session sends
`CONNECT <user>@<tns>`, sqlplus asks for the password, and the session answers
on the following line. That line is read verbatim rather than parsed, so `@`,
`/`, a double quote and a trailing `#` need no escaping. Quoting the password
onto the `CONNECT` line would have worked for most passwords and failed for
those, silently, at connect time.

Supporting changes in the same commit, each of which matters:

`SET ECHO OFF` is sent unconditionally before `CONNECT`, outside
`setup_commands`, so a caller who passes an empty setup list still gets it.
Without that the credential could be echoed back into the output stream.

Sentinel matching accepts a line that ends with the sentinel, not only one equal
to it. sqlplus writes `Enter password: ` with no trailing newline, so it arrives
glued to the front of whatever comes next, and during connect that is the
sentinel line. Strict equality hangs until the connect timeout.

Session setup now runs after the connect rather than before it, which also makes
`ALTER SESSION` legal in `setup_commands`. It was not, before.

The connect exchange scrubs the password out of any captured output before that
output can reach an exception. `SET ECHO OFF` should make this unnecessary. It
costs one list comprehension on one exchange.

An empty username selects external authentication and issues `CONNECT /@<tns>`,
so a wallet connection needs no placeholder credential.

## The convention

`f2b4b9b` moved the variable names into the package:

```python
ENV_USERNAME = 'DB_USERNAME'
ENV_PASSWORD = 'DB_PASSWORD'
ENV_CONNECT  = ('DB_NAME', 'TWO_TASK', 'ORACLE_SID')   # first one set wins
```

All three credential arguments default to `None`, and `None` means ask the
environment. `SqlplusSession()` with nothing passed is a working session.

`None` and `''` are not interchangeable, and this is the decision most likely to
look like an oversight to someone reading quickly. `None` asks a question. `''`
states an answer, specifically that external authentication is wanted. Collapse
them and every wallet connection silently becomes a password connection the
moment `DB_USERNAME` appears in the environment. There is a test named for this
(`test_empty_string_is_an_answer_not_a_question`); if it starts failing, read it
before changing it.

Each argument resolves on its own, so `SqlplusSession('scott')` takes the
password and the target from the environment.

`load_env_file(path)` sources a shell file and returns the triple. It replaced
three separate readers that had grown up in and around this repository, each
with its own idea of what the variables were called. `SqlplusSession.from_env_file(path)`
is the one-liner over it.

Sourcing, not parsing. Environment files compute their values, and
`DB_NAME="${host}:${port}/${svc}"` is a line no line-oriented parser gets right.
There is a test for exactly that shape.

The subshell unsets the credential variables before sourcing, so the answer
describes the file rather than the calling shell. This was found by two failing
tests rather than by reasoning, and it is worth understanding why: without the
unset, an exported `DB_NAME` shows through and gets attributed to a file that
never mentioned it. Works on one box, not the next. A caller wanting
file-over-environment merges the two itself.

## Hardening, uncommitted at time of writing

Three small things, all in `load_env_file`:

`stderr` goes to `os.devnull` rather than to a pipe nobody drains. An
environment file that chatters on stderr would have filled the buffer and hung.
`subprocess.DEVNULL` is 3.3 and later, and this package claims 3.2.5, so the
file object is opened by hand.

`os.path.expanduser` moved into `load_env_file`. It was only in `from_env_file`,
so `~/.dbenv` worked through one entry point and not the other.

The shell script is composed from the constants rather than written out, so
adding a name to `ENV_CONNECT` cannot leave the `unset` list or the fallback
chain consulting the old one. `test_script_clears_every_variable_it_reads` holds
that together. The generated text is byte-identical to what was there before.

## What has not been proved

Nothing has connected to a real database since any of this landed. The fake is
faithful to the protocol as understood, and understanding is exactly what is at
risk. Three specific unknowns:

Whether real sqlplus in silent mode prints the password prompt at all when stdin
is a pipe. The code handles both cases, and only one of them has been exercised
by a real binary.

What a rejected `CONNECT` actually does when stdin is not a terminal. SQL*Plus
is documented not to re-prompt in that situation, and if it does re-prompt, the
sentinel line would be consumed as a username and the connect would hang to the
timeout. All three failure paths (error scan, process death, timeout) end in
`SqlplusConnectError`, so the caller sees the right exception either way. Which
path fires is unverified.

Whether a password containing a newline survives. It should: the value is read
as one line by sqlplus, which means a newline would terminate it early. This is
a known limitation rather than a bug, and it is untested.

`tests/test_spike_oracle.py` is the tool for all three. It takes `--env-file`
and now reads the same three variables through `load_env_file`. Run it first.

## Other open items

The version string lives in `setup.py` and `__init__.py` with nothing keeping
them in step, and there is no CHANGELOG. Two bumps in one day is enough evidence
that this will drift.

`load_env_file` needs a POSIX shell. There is no native Windows path and no
graceful degradation if `/bin/sh` is absent; it raises `ValueError` with the
underlying `OSError` attached, which is honest but not helpful.

`from_env_file` passes the triple positionally, so a file that sets no username
yields `''` and therefore external authentication. That follows from the
`None`-versus-`''` rule and is intended. It will still surprise someone.

The repository has no remote. Nothing has been pushed anywhere.
