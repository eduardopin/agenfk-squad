#!/usr/bin/env python3
"""tests/_tmpclean.py removes a test file's throwaway directories even when they hold read-only entries or a
child still has them busy, and a file that exits early still leaves nothing in its TMPDIR.

Self-checking: the exit code is the verdict. The read-only cases are the POSIX form of the Windows ones:
there the read-only attribute of a file (a git object) stops its removal; here a directory without the
write bit does, and the helper clears the same bit.

    python3 tests/test_tmpclean.py
"""
import os
import stat
import subprocess
import sys
import tempfile

import _tmpclean

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0     # root removes anything: the read-only cases prove nothing


def _write(path, text="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _read_only_tree(top):
    """top/.git/objects/ab/cdef (read-only file in read-only directories), as git leaves them."""
    obj = os.path.join(top, ".git", "objects", "ab")
    _write(os.path.join(obj, "cdef"))
    os.chmod(os.path.join(obj, "cdef"), stat.S_IREAD)
    for d in (obj, os.path.dirname(obj)):
        os.chmod(d, stat.S_IREAD | stat.S_IEXEC)


def test_a_tree_of_read_only_entries_is_removed():
    top = tempfile.mkdtemp(prefix="tmpclean-")
    _read_only_tree(top)
    if not IS_ROOT and os.name != "nt":
        import shutil
        shutil.rmtree(top, ignore_errors=True)               # what the helper replaces cannot
        assert os.path.exists(top), "the premise: ignore_errors=True leaves a read-only tree behind"
    assert _tmpclean.rmtree(top, patience=5) is True
    assert not os.path.lexists(top)


def test_a_directory_nobody_may_enter_is_removed():
    if IS_ROOT or os.name == "nt":
        print("   (skipped: chmod does not deny anything here)")
        return
    top = tempfile.mkdtemp(prefix="tmpclean-")
    _write(os.path.join(top, "sub", "f.txt"))
    os.chmod(os.path.join(top, "sub"), 0)
    assert _tmpclean.rmtree(top, patience=5) is True
    assert not os.path.lexists(top)


def test_a_missing_directory_counts_as_removed():
    assert _tmpclean.rmtree(os.path.join(tempfile.gettempdir(), "tmpclean-never-existed"), patience=0) is True


def test_a_busy_directory_is_retried_until_it_frees_up():
    top = tempfile.mkdtemp(prefix="tmpclean-")
    _write(os.path.join(top, "f.txt"))
    real, calls = _tmpclean._rmtree_once, []

    def busy_twice(path):
        calls.append(path)
        if len(calls) <= 2:
            raise PermissionError("in use by another process")      # a child that has not exited yet
        real(path)
    _tmpclean._rmtree_once = busy_twice
    try:
        assert _tmpclean.rmtree(top, patience=30) is True
    finally:
        _tmpclean._rmtree_once = real
    assert len(calls) == 3 and not os.path.lexists(top), calls


def test_a_directory_that_stays_busy_is_reported_not_hidden():
    top = tempfile.mkdtemp(prefix="tmpclean-")
    real = _tmpclean._rmtree_once
    _tmpclean._rmtree_once = lambda path: None                  # the removal never takes
    try:
        assert _tmpclean.rmtree(top, patience=0.2) is False
    finally:
        _tmpclean._rmtree_once = real
        _tmpclean.rmtree(top, patience=5)


CHILD = r"""
import os, stat, sys
sys.path.insert(0, {tests!r})
import _tmpclean
a = _tmpclean.mkdtemp(prefix="one-")
b = _tmpclean.mkdtemp(prefix="two-", dir=a)
obj = os.path.join(a, ".git", "objects", "ab")
os.makedirs(obj)
with open(os.path.join(obj, "cdef"), "w") as fh:
    fh.write("x")
os.chmod(os.path.join(obj, "cdef"), stat.S_IREAD)
for d in (obj, os.path.dirname(obj)):
    os.chmod(d, stat.S_IREAD | stat.S_IEXEC)
{ending}
"""


def _run_child(ending):
    """A python that makes two throwaway directories and ends as `ending` says; what is left in its TMPDIR."""
    tmp = tempfile.mkdtemp(prefix="tmpclean-parent-")
    try:
        env = dict(os.environ, TMPDIR=tmp, TEMP=tmp, TMP=tmp, PYTHONDONTWRITEBYTECODE="1")
        r = subprocess.run([sys.executable, "-c", CHILD.format(tests=os.path.dirname(os.path.abspath(__file__)),
                                                               ending=ending)],
                           capture_output=True, text=True, env=env, timeout=120)
        return r, sorted(os.listdir(tmp))
    finally:
        _tmpclean.rmtree(tmp, patience=5)


def test_a_file_that_ends_normally_leaves_nothing_in_its_tmpdir():
    r, left = _run_child("pass")
    assert r.returncode == 0, r.stderr
    assert left == [], left


def test_a_file_that_exits_early_or_fails_leaves_nothing_in_its_tmpdir():
    r, left = _run_child("sys.exit(0)           # a skip, like 'for Windows only'")
    assert r.returncode == 0 and left == [], (r.stderr, left)
    r, left = _run_child("raise AssertionError('a test failed')")
    assert r.returncode != 0 and left == [], (r.stderr, left)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok ", name)
