"""Throwaway directories for a test file, removed on every platform when the file exits.

Why not shutil.rmtree(path, ignore_errors=True): on Windows it silently leaves a tree behind when it holds a
read-only file (git writes its objects read-only, and a chmod'ed fixture stays so) or a file or directory
that a child process still has open (a detached hook, a PowerShell run). CI then fails the file for leaving
entries in its TMPDIR. Here the read-only bit is cleared and the removal retried, and the removal is
retried for a while for a child that is still exiting; whatever cannot be removed is said on stderr.

    import _tmpclean                       # tests/ is sys.path[0] when a test file is run
    h = _tmpclean.mkdtemp(prefix="x-")     # removed at exit, whatever happens to the test
    _tmpclean.rmtree(h)                    # or by hand, sooner

The cleanup is registered by the first mkdtemp, which is before any test body runs, and so before any
early skip or sys.exit in the file.
"""
import atexit
import os
import shutil
import stat
import sys
import tempfile
import time

_DIRS = []
_REGISTERED = []
PATIENCE = 60       # seconds, for ALL the directories together, that they may stay busy (a child that has not
                    # exited yet) before we give up: a lock that never lets go must not cost this much per directory


def _writable(path):
    try:
        mode = os.lstat(path).st_mode
        if stat.S_ISLNK(mode):
            return
        # a directory needs rwx to be listed and emptied on POSIX; on Windows only the write bit exists
        os.chmod(path, mode | (stat.S_IRWXU if stat.S_ISDIR(mode) else stat.S_IWUSR))
    except OSError:
        pass


def _on_error(func, path, _exc):
    """rmtree's error hook (the third argument is an exc_info tuple before Python 3.12, the exception
    after it; neither is needed): make the entry and its parent writable, and retry the failed call when it
    takes a path alone. Where rmtree works on file descriptors (POSIX), the failed call is os.open and takes
    more, so the pass that follows, which rmtree's caller makes, does the work."""
    _writable(path)
    _writable(os.path.dirname(path))
    if func in (os.unlink, os.remove, os.rmdir):
        try:
            func(path)
        except OSError:
            pass        # still busy: rmtree's caller looks again


def _rmtree_once(path):
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_on_error)
    else:
        shutil.rmtree(path, onerror=_on_error)


def rmtree(path, patience=None, deadline=None):
    """Remove path and everything under it, or return False once `deadline` (a time.time() value) or, without
    one, `patience` seconds (default PATIENCE) have passed with something left. At least one attempt is made
    whatever the clock says. A tree that is already gone is True."""
    end = deadline if deadline is not None else time.time() + (PATIENCE if patience is None else patience)
    delay = 0.05
    while True:
        try:
            _rmtree_once(path)
        except OSError:
            pass
        if not os.path.lexists(path):
            return True
        if time.time() >= end:
            return False
        time.sleep(delay)
        delay = min(delay * 2, 1.0)


def _remove_all():
    """One deadline for every tracked directory, not one per directory: the CI job that runs a test file has
    a timeout, and dozens of directories stuck behind the same lock would each wait out their own."""
    deadline = time.time() + PATIENCE
    for path in reversed(_DIRS):
        if not rmtree(path, deadline=deadline):
            print(f"warning: could not remove {path} (a file in it is still in use)", file=sys.stderr)
    del _DIRS[:]


def track(path):
    """Have path removed at exit as well. Returns it."""
    if not _REGISTERED:
        _REGISTERED.append(True)
        atexit.register(_remove_all)
    _DIRS.append(path)
    return path


def mkdtemp(prefix="tmp", dir=None):
    """tempfile.mkdtemp, removed at exit."""
    return track(tempfile.mkdtemp(prefix=prefix, dir=dir))
