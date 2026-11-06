#!/usr/bin/env python3
"""scripts/check_temp_leftovers.py names each test file that leaves something in its TMPDIR.

Self-checking: the exit code is the verdict. Each case writes small test files into a throwaway
tests/ directory and runs the script on them: one that cleans up, one that leaves a directory, one
whose child process leaves one through TMPDIR, one that fails. The script does not run the real
suite here (that is what it is for, and it takes minutes); it runs it on these planted files.
Everything lives under one temporary directory of this run, which the last case proves is gone.

    python3 tests/test_check_temp_leftovers.py
"""
import os
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "check_temp_leftovers.py")

CLEAN = """import shutil, tempfile
d = tempfile.mkdtemp(prefix="clean-")
shutil.rmtree(d)
"""
LEAKY = """import tempfile
for _ in range(3):
    tempfile.mkdtemp(prefix="leaky-")
"""
CHILD = """import subprocess, sys
subprocess.run([sys.executable, "-c", "import tempfile; tempfile.mkdtemp(prefix='child-')"], check=True)
"""
FAILING = """import sys
sys.exit(1)
"""
SLOW = """import time
time.sleep(30)
"""

RUN_TMP = None   # the one temporary directory of this run, set in main


def planted(**files):
    d = tempfile.mkdtemp(prefix="planted-")
    paths = []
    for name, text in files.items():
        p = os.path.join(d, f"test_{name}.py")
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        paths.append(p)
    return paths


def check(*args):
    r = subprocess.run([sys.executable, "-B", SCRIPT, *args], capture_output=True, text=True, timeout=120)
    return r.returncode, r.stdout + r.stderr


def test_a_clean_file_passes():
    code, out = check(*planted(clean=CLEAN))
    assert code == 0, out
    assert "ok   " in out and "test_clean.py" in out and "left nothing behind" in out, out


def test_a_leaky_file_is_named_with_its_count():
    code, out = check(*planted(clean=CLEAN, leaky=LEAKY))
    assert code == 1, out
    left = [ln for ln in out.splitlines() if ln.startswith("LEFT ")]
    assert len(left) == 1 and "test_leaky.py: 3 left (leaky-" in left[0], out
    assert "1 of 2 test file(s)" in out, out


def test_a_child_that_leaks_through_tmpdir_is_caught():
    code, out = check(*planted(child=CHILD))
    assert code == 1 and "test_child.py: 1 left (child-" in out, out


def test_a_failing_file_fails_even_with_nothing_left():
    code, out = check(*planted(failing=FAILING))
    assert code == 1 and "FAIL " in out and "test_failing.py: exited 1" in out and "LEFT" not in out, out


def test_a_file_that_hangs_times_out():
    code, out = check("--timeout", "1", *planted(slow=SLOW))
    assert code == 1 and "test_slow.py: timed out after 1s" in out, out


def test_the_script_leaves_nothing_of_its_own():
    # the script's TMPDIR is this run's; its per-file directories must go with each file
    before = set(os.listdir(RUN_TMP))
    check(*planted(leaky=LEAKY))
    after = set(os.listdir(RUN_TMP))
    assert [n for n in after - before if not n.startswith("planted-")] == [], sorted(after - before)


def main():
    global RUN_TMP
    RUN_TMP = tempfile.mkdtemp(prefix="check-temp-leftovers-test-")
    tempfile.tempdir = os.environ["TMPDIR"] = RUN_TMP
    failed = []
    try:
        for name, fn in sorted(globals().items()):
            if name.startswith("test_") and callable(fn):
                try:
                    fn()
                    print("ok  ", name)
                except AssertionError as e:
                    failed.append(name)
                    print("FAIL", name, "\n     ", str(e)[:1500].replace("\n", "\n      "))
    finally:
        shutil.rmtree(RUN_TMP, ignore_errors=True)
    if os.path.exists(RUN_TMP):
        failed.append("nothing is left in the temporary directory after the run")
        print("FAIL nothing is left in the temporary directory after the run:", RUN_TMP)
    else:
        print("ok   nothing is left in the temporary directory after the run")
    if failed:
        print("failed:", " ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
