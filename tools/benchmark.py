#!/usr/bin/env python3
"""Time a persistent session against the fresh-sqlplus-per-call pattern.

Usage:
  benchmark.py [options]
  benchmark.py (-h | --help)
  benchmark.py --version

Options:
  -u NAME, --user=NAME      Oracle username; empty selects external
                            authentication. Overrides $DB_USERNAME.
  -p PW, --password=PW      Oracle password. Overrides $DB_PASSWORD.
                            Prefer --env-file; this lands in shell history.
  -T ALIAS, --tns=ALIAS     TNS alias or Easy Connect string. Overrides
                            $DB_NAME, $TWO_TASK, $ORACLE_SID.
  -f PATH, --env-file=PATH  Shell file to source for credentials and for
                            the runtime variables sqlplus needs. Options
                            above still win over it.
  -s PATH, --sqlplus=PATH   sqlplus binary [default: sqlplus].
  -N N, --iterations=N      Queries per measurement [default: 50].
  -B, --no-baseline         Skip the per-call baseline. It costs about
                            20 ms of wall clock per iteration times the
                            connect time, so roughly a minute at N=50.
  -t, --terse               One line: persistent, per-call, ratio.
  -v, --verbose             Report the latency distribution as well.
  -d, --debug               Traceback on failure instead of a message.
  -h, --help                Show this message.
  --version                 Show the package version.

Precedence is CLI, then environment, and the package resolves the
environment itself -- an option left off is passed as None, which is
what tells sqlplus_session to go and look.

Benchmark on the RHEL replica, not on primary Cygwin: cygwin1.dll 3.6.9
adds about 15 ms to every pipe round trip to a native Windows binary,
which swamps what this measures. See a/handoff/2026-08-14-connect.md.
"""

import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                '..'))

from sqlplus_session import (            # noqa: E402  (path set above)
    SqlplusSession,
    __version__,
    load_env_file,
    resolve_credentials,
)
from sqlplus_session.session import _quote_password    # noqa: E402

_RUNTIME_VARS = ('ORACLE_HOME', 'PATH', 'LD_LIBRARY_PATH', 'TNS_ADMIN',
                 'NLS_LANG', 'NLS_DATE_FORMAT')


def parse_args(argv):
    """argparse wearing the docopt usage above, since docopt is absent."""
    import argparse

    p = argparse.ArgumentParser(add_help=False, usage=__doc__)
    p.add_argument('-u', '--user', default=None)
    p.add_argument('-p', '--password', default=None)
    p.add_argument('-T', '--tns', default=None)
    p.add_argument('-f', '--env-file', dest='env_file', default=None)
    p.add_argument('-s', '--sqlplus', default='sqlplus')
    p.add_argument('-N', '--iterations', type=int, default=50)
    p.add_argument('-B', '--no-baseline', dest='baseline',
                   action='store_false', default=True)
    p.add_argument('-t', '--terse', action='store_true')
    p.add_argument('-v', '--verbose', action='store_true')
    p.add_argument('-d', '--debug', action='store_true')
    p.add_argument('-h', '--help', action='store_true')
    p.add_argument('--version', action='store_true')
    return p.parse_args(argv)


def is_cygwin():
    import platform
    return 'cygwin' in platform.system().lower()


def cygpath_m(path):
    try:
        return subprocess.check_output(['cygpath', '-m', path],
                                       universal_newlines=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return path


def source_runtime_vars(env_file):
    """ORACLE_HOME and friends out of *env_file*; credentials come from
    the package, not from here."""
    env = dict(os.environ)
    if not env_file:
        return env
    devnull = open(os.devnull, 'w')
    try:
        proc = subprocess.Popen(['sh', '-c', '. "$1" ; env', 'sh', env_file],
                                stdout=subprocess.PIPE, stderr=devnull,
                                universal_newlines=True)
        out, _ = proc.communicate()
    finally:
        devnull.close()
    for line in out.splitlines():
        eq = line.find('=')
        if eq > 0 and line[:eq] in _RUNTIME_VARS:
            env[line[:eq]] = line[eq + 1:]
    return env


def baseline_query(user, pw, tns, sql, run_env, sqlplus):
    """One query the old way: a whole new sqlplus, login string on argv.

    The credential is on the command line here on purpose -- that is the
    pattern being measured, and the reason the package stopped doing it.
    """
    fd, tmp = tempfile.mkstemp(suffix='.sql', prefix='bench_')
    try:
        os.write(fd, ('set heading off feedback off pagesize 0 verify off'
                      ' trimspool on linesize 4000\n%s\nexit\n'
                      % sql).encode('utf-8'))
        os.close(fd)
        script = cygpath_m(tmp) if is_cygwin() else tmp
        login = ('%s/%s@%s' % (user, _quote_password(pw), tns)) if user \
            else '/@%s' % tns
        devnull = open(os.devnull, 'r')
        try:
            proc = subprocess.Popen(
                [sqlplus, '-s', '-L', login, '@%s' % script],
                stdin=devnull, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, universal_newlines=True,
                env=run_env)
            out, _ = proc.communicate()
        finally:
            devnull.close()
        return out.strip()
    finally:
        os.unlink(tmp)


def describe(times):
    times = sorted(times)
    n = len(times)
    return dict(median=times[n // 2], mean=sum(times) / n,
                lo=times[0], hi=times[-1],
                over10=sum(1 for t in times if t > 10.0), n=n)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)

    if args.help:
        sys.stdout.write(__doc__)
        return 0
    if args.version:
        print('sqlplus-session %s' % __version__)
        return 0

    given = (args.user, args.password, args.tns)
    if args.env_file:
        given = tuple(opt if opt is not None else val
                      for opt, val in zip(given, load_env_file(args.env_file)))
    user, pw, tns = resolve_credentials(*given)

    if not tns:
        sys.stderr.write(
            'No Oracle connect target.\n'
            'Set $DB_NAME, $TWO_TASK or $ORACLE_SID, or pass --tns ALIAS '
            'or --env-file PATH.\n')
        return 2

    run_env = source_runtime_vars(args.env_file)
    converter = cygpath_m if is_cygwin() else None
    N = args.iterations

    if not args.terse:
        print('connect:  %s@%s' % (user, tns) if user
              else 'connect:  /@%s (external authentication)' % tns)
        print('sqlplus:  %s' % args.sqlplus)
        print('queries:  %d' % N)
        print()

    t0 = time.time()
    with SqlplusSession(user, pw, tns, sqlplus_cmd=args.sqlplus,
                        env=run_env, path_converter=converter) as s:
        connect_s = time.time() - t0
        each = []
        for i in range(N):
            t = time.time()
            rows = s.query('SELECT %d FROM DUAL' % i)
            each.append((time.time() - t) * 1000.0)
            got = ''.join(r.strip() for r in rows if r.strip())
            if got != str(i):
                sys.stderr.write('wrong value at %d: %r\n' % (i, rows))
                return 1
    persistent_ms = sum(each) / N

    baseline_ms = None
    if args.baseline:
        t2 = time.time()
        for i in range(N):
            out = baseline_query(user, pw, tns, 'SELECT %d FROM DUAL;' % i,
                                 run_env, args.sqlplus)
            if str(i) not in out:
                sys.stderr.write('baseline failed at %d: %r\n' % (i, out))
                if user and not user.replace('_', '').isalnum():
                    sys.stderr.write(
                        'A login string on the command line cannot carry '
                        'this password. Use --no-baseline.\n')
                return 1
        baseline_ms = (time.time() - t2) / N * 1000.0

    if args.terse:
        if baseline_ms is None:
            print('%.3f' % persistent_ms)
        else:
            print('%.3f %.1f %.1fx'
                  % (persistent_ms, baseline_ms, baseline_ms / persistent_ms))
        return 0

    print('connect:      %.3f s' % connect_s)
    print('persistent:   %.3f ms/query' % persistent_ms)
    if baseline_ms is not None:
        print('per-call:     %.1f ms/query' % baseline_ms)
        print('ratio:        %.1fx' % (baseline_ms / persistent_ms))
    if args.verbose:
        d = describe(each)
        print()
        print('persistent distribution over %(n)d queries:' % d)
        print('  median %(median).3f  mean %(mean).3f  min %(lo).3f  '
              'max %(hi).3f  ms' % d)
        print('  over 10 ms: %(over10)d of %(n)d' % d)
        if d['over10'] > d['n'] // 2:
            print('  most queries over 10 ms -- if this is Cygwin, check '
                  'cygcheck -V; 3.6.9 adds ~15 ms per pipe round trip to a '
                  'native Windows binary.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception:
        if '-d' in sys.argv or '--debug' in sys.argv:
            raise
        sys.stderr.write('%s\n' % sys.exc_info()[1])
        sys.exit(1)
