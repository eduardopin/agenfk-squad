#!/usr/bin/env python3
"""Fails when a test file leaves something behind in its temporary directory.

    python3 scripts/check_temp_leftovers.py [--timeout SECONDS] [TEST ...]
                                            (default: every tests/test_*.py of this repository)

Runs each test file alone, one after the other (never in parallel: a suite run is heavy), with
TMPDIR, TEMP and TMP pointing at a fresh empty directory, then counts what is left in it after the
file exits. Prints one line per file, `ok <file>` or `LEFT <file>: <n> left (<names>)`, and a line
for a file that failed or timed out, since a failing run proves nothing about its cleanup. Exits 1
when any file left something or failed, 0 when every one exited 0 and left nothing.

Why it exists: the suite runs many times a day on machines whose /tmp is a small tmpfs, and a test
file that makes one directory per case and never removes it filled that disk. Each test file keeps
one temporary root per run, removes it at exit and proves it is gone; this script is the outside
check that the proof holds, including for the child processes a test starts.

What it cannot see: a child started with an environment built from scratch (no TMPDIR) writes to
the system default (/tmp), not to the directory this script watches.
Standard library only.
"""
import argparse
import glob
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def measure(test, timeout, base):
    """(exit code or None on timeout, sorted names left in the file's own TMPDIR)."""
    tmp = tempfile.mkdtemp(prefix="t-", dir=base)
    env = {**os.environ, "TMPDIR": tmp, "TEMP": tmp, "TMP": tmp, "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        try:
            code = subprocess.run([sys.executable, test], cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            code = None
        return code, sorted(os.listdir(tmp))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Run each test file with an empty TMPDIR and count what it leaves.")
    ap.add_argument("tests", nargs="*", help="test files (default: every tests/test_*.py)")
    ap.add_argument("--timeout", type=float, default=900, help="seconds per test file (default 900)")
    args = ap.parse_args(argv)
    tests = args.tests or sorted(glob.glob(os.path.join(ROOT, "tests", "test_*.py")))
    if not tests:
        print("no test files found", file=sys.stderr)
        return 2
    bad = 0
    # Every per-file TMPDIR sits under one directory of this run, removed at the end. The names are
    # short on purpose: a test that prints a path in a line cut at 80 characters must still fit.
    base = tempfile.mkdtemp(prefix="tl-")
    try:
        for test in tests:
            name = os.path.relpath(os.path.abspath(test), ROOT)
            code, left = measure(os.path.abspath(test), args.timeout, base)
            if code is None:
                print(f"FAIL {name}: timed out after {args.timeout:g}s")
            elif code != 0:
                print(f"FAIL {name}: exited {code}")
            if left:
                shown = ", ".join(left[:5]) + (", ..." if len(left) > 5 else "")
                print(f"LEFT {name}: {len(left)} left ({shown})")
            if code != 0 or left:
                bad += 1
            else:
                print(f"ok   {name}")
    finally:
        shutil.rmtree(base, ignore_errors=True)
    print(f"{bad} of {len(tests)} test file(s) failed or left something behind" if bad
          else f"all {len(tests)} test file(s) left nothing behind")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
