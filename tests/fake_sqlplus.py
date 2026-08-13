#!/usr/bin/env python3
"""A fake sqlplus that reads stdin, responds to queries, and honors sentinels.

Used by the test suite as a subprocess stand-in for real sqlplus.  It
understands just enough of the protocol to exercise SqlplusSession:

- Ignores SET/WHENEVER commands (no output).
- Handles CONNECT, including reading the password from the following
  line the way real sqlplus does after prompting for it.
- Responds to SELECT with canned output.
- Echoes PROMPT arguments to stdout (the sentinel protocol).
- Responds to EXIT by terminating.
- Can be told to emit ORA- errors, hang, or die via special "commands"
  embedded in the SQL (trigger words in the query text).

Invocation:  python fake_sqlplus.py -s /nolog
The -s and login arguments are accepted and ignored (matching sqlplus).

Control commands (sent as SQL on stdin):
    __FAKE_DIE__          exit immediately with code 1
    __FAKE_HANG__         stop reading (simulate a wedged process)
    __FAKE_ORA_ERROR__    emit an ORA-00942 line before the next sentinel
    __FAKE_SLOW__<N>__    sleep N seconds before responding

Environment:
    FAKE_SQLPLUS_ARGV     write argv to this path, one argument per
                          line, then carry on.  Lets a test assert what
                          did and did not reach the command line.
    FAKE_SQLPLUS_SEEN     append every stdin line to this path.
    FAKE_SQLPLUS_PROMPT   if set, write "Enter password: " with no
                          trailing newline, as an interactive sqlplus
                          would.  The reader has to cope with a prompt
                          fragment glued to the next line.
    FAKE_SQLPLUS_BADPW    reject the connect with ORA-01017.
"""

import os
import re
import sys
import time


def _record(var, text):
    path = os.environ.get(var)
    if path:
        with open(path, 'a') as fh:
            fh.write(text)


def main():
    # Accept and ignore -s, -L, and the login argument.
    # We just read from stdin and write to stdout.
    _record('FAKE_SQLPLUS_ARGV', '\n'.join(sys.argv[1:]) + '\n')

    for line in iter(sys.stdin.readline, ''):
        line = line.rstrip('\r\n')
        _record('FAKE_SQLPLUS_SEEN', line + '\n')

        if not line:
            continue

        # EXIT terminates cleanly.
        if line.upper().startswith('EXIT'):
            sys.exit(0)

        # PROMPT: echo the argument to stdout (sentinel protocol).
        if line.upper().startswith('PROMPT '):
            arg = line[7:]   # everything after "PROMPT "
            sys.stdout.write(arg + '\n')
            sys.stdout.flush()
            continue

        # SET / WHENEVER: silently consume.
        upper = line.upper().lstrip()
        if (upper.startswith('SET ') or
                upper.startswith('WHENEVER ') or
                upper.startswith('ALTER ')):
            continue

        # CONNECT.  With a username, real sqlplus prompts and then
        # reads the password as the next raw line -- so we consume that
        # line here rather than letting the main loop treat it as SQL.
        if upper.startswith('CONNECT'):
            arg = line.split(None, 1)[1] if len(line.split(None, 1)) > 1 else ''
            if not arg.startswith('/'):
                if os.environ.get('FAKE_SQLPLUS_PROMPT'):
                    sys.stdout.write('Enter password: ')
                    sys.stdout.flush()
                pw = sys.stdin.readline()
                _record('FAKE_SQLPLUS_SEEN', pw)
            if os.environ.get('FAKE_SQLPLUS_BADPW'):
                sys.stdout.write('ERROR:\n')
                sys.stdout.write('ORA-01017: invalid username/password; '
                                 'logon denied\n')
                sys.stdout.flush()
            continue

        # Control commands.
        if '__FAKE_DIE__' in line:
            sys.exit(1)

        if '__FAKE_HANG__' in line:
            # Block forever (well, until killed).
            while True:
                time.sleep(3600)

        if '__FAKE_ORA_ERROR__' in line:
            sys.stdout.write('ORA-00942: table or view does not exist\n')
            sys.stdout.flush()
            continue

        m = re.search(r'__FAKE_SLOW__(\d+)__', line)
        if m:
            time.sleep(int(m.group(1)))
            continue

        # @file: pretend to run a file.
        if line.startswith('@'):
            sys.stdout.write('file-output-line-1\n')
            sys.stdout.write('file-output-line-2\n')
            sys.stdout.flush()
            continue

        # / on its own line: PL/SQL block terminator.
        if line.strip() == '/':
            continue

        # Any SELECT: return a canned row.
        if 'SELECT' in line.upper():
            # Try to extract a literal number from SELECT <n> FROM DUAL
            m = re.search(r'SELECT\s+(\d+)\s+FROM\s+DUAL', line, re.I)
            if m:
                sys.stdout.write('\t %s\n' % m.group(1))
            else:
                sys.stdout.write('\tquery-result\n')
            sys.stdout.flush()
            continue

        # Anything else (DML, DDL, PL/SQL body lines): silently consume.


if __name__ == '__main__':
    main()
