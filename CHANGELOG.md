# Changelog

Versions are read from `sqlplus_session/__init__.py`; `setup.py` no longer
carries its own copy.

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
