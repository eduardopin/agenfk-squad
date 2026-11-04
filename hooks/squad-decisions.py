#!/usr/bin/env python3
"""The project's decisions: one store that every worktree of a repository sees.

A decision the owner (or a card) settled is written once and read by every
session that starts in that repository, in any worktree, before it works.
The store lives outside the checkout, so a decision made in one worktree is
visible in the others at once, without waiting for a merge:

    ${SQUAD_DECISIONS_DIR:-~/.claude/squad/decisions}/<repo key>/

<repo key> is a hash of `git rev-parse --git-common-dir`, the same from every
worktree of a repository (the merge turn and the seal use the same key).

One file per decision, <YYYY-MM-DD>-<slug>.md, with a frontmatter (title,
date, source: owner | card <id>, status: active | superseded, supersedes and
superseded_by when they apply) and a body of What / Why. One file per decision
is the drop-box: two worktrees never write the same file, so parallel writes
never conflict. A superseded decision is never deleted, only left out of the
index.

    squad-decisions add --title T --source S [--supersedes F]   body on stdin
    squad-decisions list [--all]          active decisions (--all: superseded too)
    squad-decisions show <file>           prints one decision
    squad-decisions supersede <file> --by <file>
    squad-decisions index                 the index of active decisions
    squad-decisions path                  the store directory of this repository

With no arguments it is the SessionStart hook: it reads the hook's JSON on
stdin and prints the index of active decisions (date and title, one line
each), capped at SQUAD_DECISIONS_BUDGET bytes (4000); when the cap cuts, the
last line says how many were left out and the command that lists them. Silent
when there are none, outside a git repository, on malformed input or any
error: it never fails the session (exit 0). SQUAD_DECISIONS=0 turns the hook
off. Standard library only.
"""
import contextlib
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import unicodedata

NAME = re.compile(r"\d{4}-\d{2}-\d{2}-[a-z0-9]+(?:-[a-z0-9]+)*\.md")
SOURCE = re.compile(r"owner|card [A-Za-z0-9][A-Za-z0-9._-]{0,63}")
FIELDS = ("title", "date", "source", "status", "supersedes", "superseded_by")
TAG = "[squad-decisions]"
LOCK = ".lock"                                        # not a decision name: list and the index never see it


class Refused(Exception):
    """A request that would write something wrong; nothing was written."""


def root():
    return os.environ.get("SQUAD_DECISIONS_DIR") or os.path.join(os.path.expanduser("~"), ".claude", "squad", "decisions")


def store(cwd=None):
    """The store directory of the repository at cwd, or "" outside a git repository."""
    try:
        r = subprocess.run(["git", "-C", cwd or os.getcwd(), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                           capture_output=True, text=True, timeout=5, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return ""
    repo = r.stdout.strip()
    if r.returncode != 0 or not repo:
        return ""
    repo = os.path.realpath(repo)
    return os.path.join(root(), hashlib.sha1(repo.encode("utf-8", "replace")).hexdigest()[:16])


def one_line(text, limit=200):
    """Control characters (newline, NUL) and runs of blanks become one space: a title cannot add a field."""
    text = "".join(" " if unicodedata.category(c).startswith("C") else c for c in str(text))
    return " ".join(text.split())[:limit]


def slug(title):
    """ASCII letters and digits joined by '-': nothing in a title can name a directory."""
    ascii_ = unicodedata.normalize("NFKD", one_line(title)).encode("ascii", "ignore").decode().lower()
    words = re.findall(r"[a-z0-9]+", ascii_)
    out = ""
    for w in words:
        if len(out) + len(w) + 1 > 60:
            break
        out = f"{out}-{w}" if out else w
    return out or "decision"


def safe_name(name):
    """A decision file named by the user: a bare <YYYY-MM-DD>-<slug>[.md], never a path."""
    name = str(name)
    if not name.endswith(".md"):
        name += ".md"
    if not NAME.fullmatch(name):
        raise Refused(f"{name!r} is not a decision file name (<YYYY-MM-DD>-<slug>.md, as `squad-decisions list` "
                      "prints it); paths are not accepted")
    return name


def inside(folder, name):
    path = os.path.join(folder, name)
    real = os.path.realpath(path)
    if os.path.dirname(real) != os.path.realpath(folder):
        raise Refused(f"{name!r} resolves outside the store")
    return path


def parse(text):
    """(frontmatter dict, body) of a decision file; ({}, text) when there is no frontmatter.

    A UTF-8 byte order mark (an editor on Windows adds one) is dropped first: it would hide the opening ---."""
    if text.startswith("\ufeff"):
        text = text[1:]
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, text
    meta = {}
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            return meta, "\n".join(lines[i + 1:])
        key, sep, value = line.partition(":")
        if sep and key.strip() in FIELDS:
            meta[key.strip()] = value.strip()
    return {}, text


def render(meta, body):
    head = "".join(f"{k}: {meta[k]}\n" for k in FIELDS if meta.get(k))
    return f"---\n{head}---\n{body.rstrip()}\n"


def read(folder, name):
    path = inside(folder, name)
    if os.path.islink(path):
        raise Refused(f"{name} is a symbolic link; the store holds only regular files")
    with open(path, encoding="utf-8") as fh:
        return parse(fh.read())


def write(folder, name, meta, body):
    """Replaces an existing decision in one step (a reader never sees half a file). A failed write or
    replace removes its temporary file: nothing is left behind in the store."""
    path = inside(folder, name)
    tmp = f"{path}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(render(meta, body))
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def entries(folder):
    """[(file, frontmatter)] of every readable decision, newest first. Unreadable files are skipped."""
    out = []
    for name in os.listdir(folder):
        if not NAME.fullmatch(name):
            continue
        try:
            meta, _ = read(folder, name)
        except (OSError, UnicodeDecodeError, Refused):
            continue
        if meta:
            out.append((name, meta))
    out.sort(key=lambda e: (e[1].get("date", e[0][:10]), e[0]), reverse=True)
    return out


def status(meta):
    """The status in lower case; a missing or empty one is active. A hand-written `Active` is active; any
    other value (a typo) keeps the decision out of the index, and `list --all` shows it as written."""
    return (meta.get("status") or "active").strip().lower()


def active(folder):
    return [e for e in entries(folder) if status(e[1]) == "active"]


def line(name, meta):
    return f"{meta.get('date', name[:10])}  {one_line(meta.get('title', name), 120)}  ({name})"


def index(folder, budget=None):
    """The index text of the active decisions; with a budget, at most that many bytes (UTF-8)."""
    decisions = active(folder) if os.path.isdir(folder) else []
    if not decisions:
        return ""
    head = (f"{TAG} {len(decisions)} active project decision(s), shared by every worktree of this repository. "
            "Follow them; to change one, record a new decision that supersedes it. Full text: squad-decisions show <file>")
    rows = [line(n, m) for n, m in decisions]
    if budget is None:
        return "\n".join([head] + rows) + "\n"

    def tail(left):
        return f"{TAG} {left} more left out (SQUAD_DECISIONS_BUDGET={budget} bytes): squad-decisions list"

    size = lambda s: len(s.encode("utf-8")) + 1
    worst = size(tail(len(rows)))                     # the overflow line is longest with the biggest count
    used, kept = size(head), []
    for i, row in enumerate(rows):
        last = i == len(rows) - 1
        if used + size(row) + (0 if last else worst) > budget:
            break
        kept.append(row)
        used += size(row)
    left = len(rows) - len(kept)
    if left == 0:
        return "\n".join([head] + kept) + "\n"
    if used + size(tail(left)) <= budget:
        return "\n".join([head] + kept + [tail(left)]) + "\n"
    return tail(left) + "\n" if size(tail(left)) <= budget else ""


def hook():
    if os.environ.get("SQUAD_DECISIONS") == "0" or sys.stdin.isatty():
        return 0                                      # a hook always gets its JSON on a pipe
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except (ValueError, UnicodeDecodeError):
        return 0
    if not isinstance(payload, dict):
        return 0
    cwd = payload.get("cwd")
    folder = store(cwd if isinstance(cwd, str) and cwd and os.path.isdir(cwd) else None)
    if not folder:
        return 0
    try:
        budget = int(os.environ.get("SQUAD_DECISIONS_BUDGET") or 4000)
    except ValueError:
        budget = 4000
    text = index(folder, max(budget, 0))
    if text:
        # Bytes, not text: a text-mode stdout on Windows turns each "\n" into "\r\n", one byte per line over
        # the budget the index counted. Flushed here, so a full or closed stdout fails inside the caller's try.
        out = getattr(sys.stdout, "buffer", None)
        if out is None:
            sys.stdout.write(text)
            sys.stdout.flush()
        else:
            sys.stdout.flush()
            out.write(text.encode("utf-8"))
            out.flush()
    return 0


@contextlib.contextmanager
def locked(folder, wait=30.0):
    """An exclusive lock on the store while a status is checked and changed: two sessions superseding the
    same decision at once cannot both win. flock on POSIX, msvcrt.locking on Windows; a lock still held
    after `wait` seconds is refused rather than waited on forever."""
    os.makedirs(folder, mode=0o700, exist_ok=True)
    path = inside(folder, LOCK)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        deadline = time.monotonic() + wait
        if os.name == "nt":
            import msvcrt
            while True:
                try:
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        raise Refused(f"the store is locked by another session ({path}); try again")
                    time.sleep(0.05)
            try:
                yield
            finally:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() > deadline:
                        raise Refused(f"the store is locked by another session ({path}); try again")
                    time.sleep(0.05)
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def cmd_add(folder, a):
    title = one_line(a.title)
    if not title:
        raise Refused("the title is empty")
    source = one_line(a.source)
    if not SOURCE.fullmatch(source):
        raise Refused(f"source {a.source!r}: use `owner` or `card <id>`")
    old = safe_name(a.supersedes) if a.supersedes else ""
    if old:
        meta, _ = read(folder, old)                   # must exist before anything is written
        if status(meta) != "active":
            raise Refused(f"{old} is already superseded (by {meta.get('superseded_by') or '?'})")
    if sys.stdin.isatty():
        raise Refused("the body comes on stdin (What / Why); pipe it in, e.g. a heredoc")
    body = sys.stdin.read().strip()
    if not body:
        raise Refused("the body is empty: write What was decided and Why")
    date = time.strftime("%Y-%m-%d")
    meta = {"title": title, "date": date, "source": source, "status": "active", "supersedes": old}
    base = f"{date}-{slug(title)}"
    with locked(folder):
        before = None
        if old:                                       # again, under the lock: another session may have won
            before = read(folder, old)
            if status(before[0]) != "active":
                raise Refused(f"{old} is already superseded (by {before[0].get('superseded_by') or '?'})")
        name = reserve(folder, base)
        try:
            # The new one is written in full first and the old one marked last. A crash between the two leaves
            # both active, which the index shows, never the old one superseded by a file that has no text.
            write(folder, name, meta, body)
            if old:
                mark(folder, old, name)
        except BaseException:
            roll_back(folder, old, before, name, lambda: os.unlink(inside(folder, name)))
            raise
    print(name)
    return 0


def reserve(folder, base):
    """Claims <base>.md (or -2, -3...) with an exclusive create and leaves it empty, invisible to the index."""
    for n in range(1, 1000):
        name = f"{base}.md" if n == 1 else f"{base}-{n}.md"
        try:                                          # O_EXCL: two worktrees adding the same title get two files
            os.close(os.open(inside(folder, name), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        except FileExistsError:
            continue
        return name
    raise Refused(f"too many decisions named {base} today")


def mark(folder, old, new):
    """Marks old as superseded by new. The last step of add and supersede: new is already written in full."""
    meta, body = read(folder, old)
    meta["status"], meta["superseded_by"] = "superseded", new
    write(folder, old, meta, body)


def roll_back(folder, old, before, new, undo_new):
    """After a failure in add or supersede: puts old back as it was if it already points at new, and only then
    undoes new. A second fault on the way stops the undo (and says so) with either both decisions active, or
    old still marked as superseded by new, which is complete; old is never left pointing at a decision that
    is gone."""
    try:
        if old:
            meta, _ = read(folder, old)
            if status(meta) != "active" and meta.get("superseded_by") == new:
                write(folder, old, *before)
        undo_new()
    except Exception as e:                            # the first failure is the one raised; this one is told
        print(f"squad-decisions: could not undo ({e}); {new} is kept and {old or 'no decision'} may still be "
              "active: check `squad-decisions list --all`; if both are active, run `supersede` again.",
              file=sys.stderr)


def cmd_supersede(folder, a):
    old, new = safe_name(a.file), safe_name(a.by)
    if old == new:
        raise Refused("a decision cannot supersede itself")
    read(folder, old), read(folder, new)              # a missing file is named before the store is locked
    with locked(folder):                              # check and mark in one step: one of two racing sessions wins
        meta_old, body_old = read(folder, old)
        meta_new, body_new = read(folder, new)
        if status(meta_old) != "active":
            raise Refused(f"{old} is already superseded (by {meta_old.get('superseded_by') or '?'})")
        if status(meta_new) != "active":
            raise Refused(f"{new} is itself superseded; supersede with an active decision")
        # Same order as add: new records what it supersedes first, old is marked last. A crash between the two
        # leaves both active (visible in the index), and running supersede again finishes the job.
        wrote = False
        try:
            if not meta_new.get("supersedes"):
                write(folder, new, dict(meta_new, supersedes=old), body_new)
                wrote = True
            mark(folder, old, new)
        except BaseException:
            undo_new = (lambda: write(folder, new, meta_new, body_new)) if wrote else (lambda: None)
            roll_back(folder, old, (meta_old, body_old), new, undo_new)
            raise
    print(f"{old} superseded by {new}")
    return 0


def cmd_list(folder, a):
    rows = (entries if a.all else active)(folder) if os.path.isdir(folder) else []
    for name, meta in rows:
        raw = one_line(meta.get("status") or "active", 40)   # as written: a hand edit stays visible
        extra = f"  -> {meta['superseded_by']}" if status(meta) == "superseded" and meta.get("superseded_by") else ""
        print(f"{line(name, meta)}  [{raw}, {meta.get('source', '?')}]{extra}")
    return 0


def cmd_show(folder, a):
    name = safe_name(a.file)
    path = inside(folder, name)
    if os.path.islink(path):
        raise Refused(f"{name} is a symbolic link; the store holds only regular files")
    with open(path, encoding="utf-8") as fh:
        sys.stdout.write(fh.read())
    return 0


def main(argv):
    import argparse
    p = argparse.ArgumentParser(prog="squad-decisions", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("add", help="record a decision; the body (What / Why) comes on stdin")
    s.add_argument("--title", required=True)
    s.add_argument("--source", required=True, help="owner | card <id>")
    s.add_argument("--supersedes", default="")
    s = sub.add_parser("list", help="the active decisions")
    s.add_argument("--all", action="store_true", help="superseded decisions too")
    sub.add_parser("show", help="print one decision").add_argument("file")
    s = sub.add_parser("supersede", help="mark a decision as superseded by another")
    s.add_argument("file")
    s.add_argument("--by", required=True)
    sub.add_parser("index", help="the index of active decisions")
    sub.add_parser("path", help="the store directory of this repository")
    a = p.parse_args(argv)
    folder = store()
    if not folder:
        print("squad-decisions: this is not a git repository; decisions belong to a repository.", file=sys.stderr)
        return 2
    if a.cmd == "path":
        print(folder)
        return 0
    if a.cmd == "index":
        sys.stdout.write(index(folder))
        return 0
    os.umask(0o077)
    try:
        return {"add": cmd_add, "list": cmd_list, "show": cmd_show, "supersede": cmd_supersede}[a.cmd](folder, a)
    except Refused as e:
        print(f"squad-decisions: {e}. Nothing was written.", file=sys.stderr)
        return 2
    except FileNotFoundError as e:
        print(f"squad-decisions: no such decision: {os.path.basename(e.filename or '?')} "
              "(see `squad-decisions list --all`).", file=sys.stderr)
        return 2
    except (OSError, UnicodeDecodeError) as e:
        print(f"squad-decisions: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    try:                                              # the budget counts UTF-8 bytes; a cp1252 console would refuse a title
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    if len(sys.argv) == 1:
        try:
            hook()
            sys.stdout.flush()
        except BaseException:                         # the session must start whatever happens here
            pass
        # os._exit, not sys.exit: the interpreter flushes stdout once more on the way out, and a stdout that is
        # full or closed would turn that into exit 120. Everything the hook printed was flushed above.
        os._exit(0)
    sys.exit(main(sys.argv[1:]))
