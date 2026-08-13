# sqlplus-session

Persistent Oracle sqlplus session over stdin/stdout pipes. Connect once,
run many queries on the same session without per-query connect overhead.

Stdlib only. Python 3.2.5+. No cx_Oracle, no python-oracledb, no
third-party dependencies.

## Quick start

```python
from sqlplus_session import SqlplusSession

with SqlplusSession('scott', 'tiger', 'orcl') as s:
    rows = s.query('SELECT ename, sal FROM emp WHERE deptno = 10')
    for r in rows:
        print(r)
    s.execute('UPDATE emp SET sal = sal * 1.1 WHERE deptno = 10')
    s.execute('COMMIT')
```

## How it works

The module manages one long-lived `sqlplus -s` process. SQL goes in on
stdin, results come back on stdout, delimited by a numbered sentinel
(`PROMPT __EOQ__<n>__`). A daemon thread drains the stdout pipe so the
main thread can impose per-query timeouts via `Queue.get(timeout=)`.

## Credentials

Credentials default to the environment, and the defaults live in the package
rather than in each caller:

| argument | variable |
|---|---|
| `username` | `DB_USERNAME` |
| `password` | `DB_PASSWORD` |
| `connect_string` | `DB_NAME`, then `TWO_TASK`, then `ORACLE_SID` |

```python
with SqlplusSession() as s:                        # nothing to pass
    print(s.query('SELECT user FROM dual'))
```

`None` means ask the environment. An empty string means what it says, so
`SqlplusSession('', '', 'ORCLPDB1')` still selects external authentication even
where `DB_USERNAME` is exported. Each of the three resolves on its own:
`SqlplusSession('scott')` takes the password and the target from the
environment.

`credentials_from_environment()` returns the triple without constructing a
session, for a caller that wants to report what it found before connecting.

Where the credentials live in a shell file, the package sources it rather than
making every tool write its own parser:

```python
with SqlplusSession.from_env_file('~/.dbenv') as s:
    ...

user, pw, tns = load_env_file('/etc/app/db.sh')    # if you want to look first
```

Sourcing beats parsing because environment files compute things:
`DB_NAME="${host}:${port}/${svc}"` is a line no parser gets right. Only the
three variables come back out of the subshell, and they are unset before the
file is sourced, so the answer describes the file rather than whatever the
calling shell happened to export.

sqlplus is started as `sqlplus -s /nolog`. Nothing secret is passed as
an argument, so the password does not appear in `ps`, in
`/proc/<pid>/cmdline`, or in any audit trail that records process
arguments. Authentication then happens over the same stdin pipe the
queries use: the session sends `CONNECT <user>@<tns>` and answers the
password prompt on the following line.

Because that line is read verbatim rather than parsed, characters that
would otherwise have to be quoted — `@`, `/`, a double quote, a
trailing `#` — are passed through as they are.

For a wallet or OS authentication, pass an empty username; the password
is then ignored and the session issues `CONNECT /@<tns>`.

```python
with SqlplusSession('', '', 'ORCLPDB1') as s:      # wallet
    print(s.query('SELECT user FROM dual'))
```

## API

### SqlplusSession(username=None, password=None, connect_string=None, ...)

The three credential arguments default to the environment; see above.

Constructor parameters:

- **sqlplus_cmd** -- path to sqlplus binary (default `'sqlplus'`)
- **env** -- environment dict for the subprocess (must include ORACLE_HOME/PATH)
- **setup_commands** -- list of SET commands run after connect (sensible defaults provided)
- **connect_timeout** -- seconds to wait for initial connect (default 30)
- **default_timeout** -- per-query timeout in seconds (default 60)
- **error_patterns** -- list of regex strings for error detection (default: ORA-/TNS-/SP2-)
- **on_error** -- `'raise'` (default) or `'return'`
- **path_converter** -- callable for path conversion (e.g. cygpath on Cygwin)

### Methods

- `query(sql, timeout=None)` -- run SQL, return output lines
- `execute(sql, timeout=None)` -- run SQL, discard output
- `run_file(path, timeout=None)` -- run @file, return output lines
- `close()` -- shut down the session (idempotent)
- `alive` -- property, True if sqlplus is still running
- `SqlplusSession.from_env_file(path, shell='/bin/sh', **kw)` -- classmethod;
  source a shell file for the credentials and open a session

### Module functions

- `credentials_from_environment()` -- `(username, password, connect_string)`
  from `DB_USERNAME` / `DB_PASSWORD` / `DB_NAME`|`TWO_TASK`|`ORACLE_SID`
- `resolve_credentials(u, p, c)` -- fill in whichever are `None`
- `load_env_file(path, shell='/bin/sh')` -- source a shell file, return the
  triple

### Exceptions

All inherit from `SqlplusError`:

- `SqlplusConnectError` -- login or startup failed
- `SqlplusOraError` -- ORA-/TNS-/SP2- error in query output (has `.errors` and `.output`)
- `SqlplusTimeout` -- query deadline exceeded (session is dead after this)
- `SqlplusDied` -- sqlplus process exited unexpectedly

## Testing

Unit tests (no database required):

```
python -m pytest tests/test_session.py -v
```

Integration test (requires live sqlplus + Oracle):

```
python tests/test_spike_oracle.py --user scott --password tiger --tns orcl
```

## License

MIT
