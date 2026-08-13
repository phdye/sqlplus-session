#!/usr/bin/env python3
"""Integration spike: proves SqlplusSession against a real Oracle DB.

This test requires a live sqlplus and an Oracle database.  It is NOT run
by the default test suite.  Run it on the target box to answer two
questions:

  1. Does the sentinel flush reliably over the pipe?
  2. How much faster is the persistent session vs. per-call sqlplus?

Usage:
    python3 tests/test_spike_oracle.py --user U --password P --tns T
    python3 tests/test_spike_oracle.py --tns T          # OS authentication
    python3 tests/test_spike_oracle.py --env-file /path/to/env.sh
                                       [--iterations N]

With --user/--password/--tns, credentials are passed directly.  With
--tns alone, OS authentication (/@TNS) is used.  With --env-file, the
script is sourced for DB_USERNAME, DB_PASSWORD and DB_NAME (or TWO_TASK
or ORACLE_SID), and for the runtime variables sqlplus needs.
"""

import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from sqlplus_session import (
    SqlplusSession,
    SqlplusOraError,
    SqlplusTimeout,
    SqlplusDied,
    load_env_file,
)


def bootstrap_env_file(env_file_path):
    """Source an env file in sh and return the resulting env dict.

    This is for the runtime variables only -- ORACLE_HOME, PATH and the
    rest that sqlplus needs to find its libraries. Credentials come from
    load_env_file(), so the package stays the one place that knows what
    a credential variable is called.
    """
    devnull = open(os.devnull, 'w')
    try:
        proc = subprocess.Popen(
            ['sh', '-c', '. %s ; env' % env_file_path],
            stdout=subprocess.PIPE, stderr=devnull,
            universal_newlines=True)
        try:
            out, _ = proc.communicate(timeout=30)
        except TypeError:
            # Python 3.2: communicate() has no timeout kwarg.
            out, _ = proc.communicate()
    finally:
        devnull.close()

    env = {}
    for line in out.splitlines():
        eq = line.find('=')
        if eq > 0:
            env[line[:eq]] = line[eq + 1:]
    return env


def cygpath_m(path):
    """Convert a POSIX path to mixed Windows form."""
    try:
        out = subprocess.check_output(
            ['cygpath', '-m', path], universal_newlines=True)
        return out.strip()
    except (OSError, subprocess.CalledProcessError):
        return path


def is_cygwin():
    import platform
    return 'cygwin' in platform.system().lower()


def baseline_query(user, pw, tns, sql, run_env):
    """One query the slow way: fresh sqlplus per call."""
    import tempfile

    script = (
        'set heading off feedback off pagesize 0 verify off'
        ' trimspool on linesize 4000\n'
        '%s\nexit\n' % sql
    )
    fd, tmp = tempfile.mkstemp(suffix='.sql', prefix='spike_')
    try:
        os.write(fd, script.encode('utf-8'))
        os.close(fd)
        tmp_conv = cygpath_m(tmp) if is_cygwin() else tmp

        if user:
            login = '%s/%s@%s' % (user, pw, tns)
        else:
            login = '/@%s' % tns

        proc = subprocess.Popen(
            ['sqlplus', '-s', '-L', login, '@%s' % tmp_conv],
            stdin=open(os.devnull, 'r'),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            env=run_env,
        )
        out, _ = proc.communicate()
        return out.strip()
    finally:
        os.unlink(tmp)


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--env-file', default=None,
                        help='Shell script to source for env vars '
                             '(reads DB_USERNAME, DB_PASSWORD, and '
                             'DB_NAME or TWO_TASK or ORACLE_SID)')
    parser.add_argument('--user', default=None,
                        help='Oracle username (empty or omit for OS auth)')
    parser.add_argument('--password', default=None,
                        help='Oracle password (empty or omit for OS auth)')
    parser.add_argument('--tns', default=None,
                        help='TNS alias or Easy Connect string')
    parser.add_argument('--iterations', '-N', type=int, default=50)
    parser.add_argument('--no-baseline', action='store_true',
                        help='Skip the per-call baseline timing test')
    args = parser.parse_args()

    N = args.iterations
    run_env = dict(os.environ)

    if args.tns is not None:
        # Explicit credentials on the command line.
        user = args.user or ''
        pw = args.password or ''
        tns = args.tns
    elif args.env_file:
        # Bootstrap from an env file.
        if not os.path.isfile(args.env_file):
            sys.exit('env file not found: %s' % args.env_file)
        print('env-file: %s' % args.env_file)
        print('bootstrapping...')
        user, pw, tns = load_env_file(args.env_file)
        env = bootstrap_env_file(args.env_file)
        if not tns:
            sys.exit('env file set none of DB_NAME, TWO_TASK or ORACLE_SID')
        for k in ('ORACLE_HOME', 'PATH', 'LD_LIBRARY_PATH', 'TNS_ADMIN',
                  'NLS_LANG', 'NLS_DATE_FORMAT'):
            if k in env:
                run_env[k] = env[k]
    else:
        sys.exit('Pass --tns (with optional --user/--password), '
                 'or --env-file')

    if user:
        print('connect: %s@%s' % (user, tns))
    else:
        print('connect: /@%s (OS authentication)' % tns)

    converter = cygpath_m if is_cygwin() else None
    fail = 0

    # ---- Test 1: persistent session timing ----------------------------
    print()
    print('=== Test 1: persistent session -- %d queries ===' % N)
    t0 = time.time()
    with SqlplusSession(user, pw, tns, env=run_env,
                        path_converter=converter) as s:
        connect_elapsed = time.time() - t0
        print('  connect: %.3f s' % connect_elapsed)

        t1 = time.time()
        for i in range(N):
            rows = s.query('SELECT %d FROM DUAL' % i)
            val = ''.join(rows).strip()
            if val != str(i):
                print('  FAIL at i=%d: expected %r, got %r' % (i, str(i), val))
                fail += 1
                break
        persistent_elapsed = time.time() - t1
        print('  %d queries: %.3f s  (%.1f ms/query)' %
              (N, persistent_elapsed, persistent_elapsed / N * 1000))

    # ---- Test 2: per-call baseline ------------------------------------
    if not args.no_baseline:
        print()
        print('=== Test 2: per-call baseline -- %d queries ===' % N)
        t2 = time.time()
        for i in range(N):
            val = baseline_query(user, pw, tns,
                                'SELECT %d FROM DUAL;' % i, run_env)
            if str(i) not in val:
                print('  FAIL at i=%d: expected %r in %r' % (i, str(i), val))
                fail += 1
                break
        baseline_elapsed = time.time() - t2
        print('  %d queries: %.3f s  (%.1f ms/query)' %
              (N, baseline_elapsed, baseline_elapsed / N * 1000))

        print()
        print('=== Speedup ===')
        if persistent_elapsed > 0:
            print('  persistent: %.1f ms/query' % (persistent_elapsed / N * 1000))
            print('  per-call:   %.1f ms/query' % (baseline_elapsed / N * 1000))
            print('  ratio:      %.1fx' % (baseline_elapsed / persistent_elapsed))
    else:
        print()
        print('(skipping baseline -- --no-baseline)')

    # ---- Test 3: error scanning ---------------------------------------
    print()
    print('=== Test 3: error scanning ===')
    with SqlplusSession(user, pw, tns, env=run_env,
                        path_converter=converter) as s:
        try:
            s.query('SELECT * FROM nonexistent_table_xyzzy_99')
            print('  FAIL: expected SqlplusOraError')
            fail += 1
        except SqlplusOraError as e:
            print('  OK: %s' % e.errors[0][:70])

        if not s.alive:
            print('  FAIL: session died after ORA error')
            fail += 1
        else:
            rows = s.query('SELECT 42 FROM DUAL')
            if ''.join(rows).strip() == '42':
                print('  OK: session survived, next query OK')
            else:
                print('  FAIL: post-error query returned %r' % rows)
                fail += 1

    # ---- Test 4: on_error='return' ------------------------------------
    print()
    print('=== Test 4: on_error=return ===')
    with SqlplusSession(user, pw, tns, env=run_env,
                        path_converter=converter, on_error='return') as s:
        rows = s.query('SELECT * FROM nonexistent_table_xyzzy_99')
        ora = [l for l in rows if 'ORA-' in l]
        if ora:
            print('  OK: error in output: %s' % ora[0][:70])
        else:
            print('  FAIL: no ORA- in output')
            fail += 1

    # ---- Test 5: multi-statement query --------------------------------
    print()
    print('=== Test 5: multi-statement ===')
    with SqlplusSession(user, pw, tns, env=run_env,
                        path_converter=converter) as s:
        rows = s.query('SELECT 111 FROM DUAL;\nSELECT 222 FROM DUAL')
        vals = [l.strip() for l in rows if l.strip()]
        if '111' in vals and '222' in vals:
            print('  OK: both values: %s' % vals)
        else:
            print('  FAIL: expected [111, 222], got %s' % vals)
            fail += 1

    # ---- Test 6: lifecycle --------------------------------------------
    print()
    print('=== Test 6: lifecycle ===')
    s = SqlplusSession(user, pw, tns, env=run_env,
                       path_converter=converter)
    s.query('SELECT 1 FROM DUAL')
    s.close()
    if s.alive:
        print('  FAIL: alive after close()')
        fail += 1
    s.close()  # double close
    try:
        s.query('SELECT 1 FROM DUAL')
        print('  FAIL: query after close() did not raise')
        fail += 1
    except SqlplusDied:
        print('  OK: lifecycle correct')

    # ---- Summary ------------------------------------------------------
    print()
    if fail:
        print('FAILED: %d test(s)' % fail)
    else:
        print('All tests passed.')
    return 1 if fail else 0


if __name__ == '__main__':
    sys.exit(main())
