#!/usr/bin/env python3
"""squad-decisions: one store of project decisions that every worktree of a repository sees,
and a SessionStart hook that prints the index of the active ones and never fails the session.

Self-checking: the exit code is the verdict. Runs in a throwaway HOME with real git repositories
under the OS temp dir, and never touches ~/.claude.

    python3 tests/test_decisions.py
"""
import atexit
import contextlib
import importlib.util
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(ROOT, "hooks", "squad-decisions.py")
MANIFEST = os.path.join(ROOT, "hooks", "manifest.json")

_HOMES = []


@atexit.register
def _remove_homes():
    for h in _HOMES:
        shutil.rmtree(h, ignore_errors=True)


def home():
    h = os.path.realpath(tempfile.mkdtemp(prefix="decisions-"))
    _HOMES.append(h)
    return h


def env(h, **kw):
    e = {k: v for k, v in os.environ.items() if not k.startswith(("SQUAD_", "GIT_"))}
    # a throwaway HOME, no global git config of the machine's user, and git never climbs above h
    e.update(HOME=h, USERPROFILE=h, GIT_CONFIG_GLOBAL=os.path.join(h, "gitconfig"), GIT_CONFIG_NOSYSTEM="1",
             GIT_CEILING_DIRECTORIES=h, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    e.update(kw)
    return e


def git(h, cwd, *args):
    return subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, check=True,
                          env=env(h)).stdout.strip()


def repo(h, name="repo"):
    """A repository with one commit on main."""
    path = os.path.join(h, name)
    subprocess.run(["git", "init", "-q", "-b", "main", path], check=True, env=env(h))
    for k, v in (("user.name", "t"), ("user.email", "t@t"), ("commit.gpgsign", "false")):
        git(h, path, "config", k, v)
    with open(os.path.join(path, "a.txt"), "w", encoding="utf-8") as fh:
        fh.write("a\n")
    git(h, path, "add", ".")
    git(h, path, "commit", "-qm", "a")
    return path


def cli(h, cwd, *args, body=None, **kw):
    return subprocess.run([sys.executable, HOOK, *args], cwd=cwd, input=body if body is not None else "",
                          capture_output=True, text=True, encoding="utf-8", env=env(h, **kw))


def hook(h, cwd, payload=None, raw=None, **kw):
    data = raw if raw is not None else json.dumps(payload if payload is not None
                                                  else {"hook_event_name": "SessionStart", "cwd": cwd})
    return subprocess.run([sys.executable, HOOK], cwd=cwd, input=data, capture_output=True, text=True,
                          encoding="utf-8", env=env(h, **kw))


def add(h, cwd, title, source="owner", body="## What\nx\n\n## Why\ny\n", *extra):
    r = cli(h, cwd, "add", "--title", title, "--source", source, *extra, body=body)
    assert r.returncode == 0, (title, r.stdout, r.stderr)
    return r.stdout.strip()


def module():
    spec = importlib.util.spec_from_file_location("squad_decisions", HOOK)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# 1. one store per repository, whatever the worktree

def test_two_worktrees_of_one_repo_resolve_the_same_store():
    h = home()
    main = repo(h)
    other = os.path.join(h, "wt-feature")
    git(h, main, "worktree", "add", "-q", "-b", "feature", other)
    os.makedirs(os.path.join(other, "sub", "dir"))
    paths = {cli(h, cwd, "path").stdout.strip() for cwd in (main, other, os.path.join(other, "sub", "dir"))}
    assert len(paths) == 1, paths
    store = paths.pop()
    assert store.startswith(os.path.join(h, ".claude", "squad", "decisions") + os.sep), store
    assert not store.startswith(main) and not store.startswith(other), "the store must live outside the checkout"
    # a decision added in one worktree shows up in the other one's session at once, no merge needed
    name = add(h, other, "Use Postgres for the ledger")
    out = hook(h, main).stdout
    assert name in out and "Use Postgres for the ledger" in out, out


def test_two_repositories_do_not_share_a_store():
    h = home()
    a, b = repo(h, "a"), repo(h, "b")
    assert cli(h, a, "path").stdout != cli(h, b, "path").stdout
    add(h, a, "Only in a")
    assert hook(h, b).stdout == ""


def test_the_store_root_moves_with_squad_decisions_dir():
    h = home()
    r = repo(h)
    elsewhere = os.path.join(h, "elsewhere")
    out = cli(h, r, "path", SQUAD_DECISIONS_DIR=elsewhere).stdout.strip()
    assert os.path.dirname(out) == elsewhere, out


# 2. add, supersede, list --all

def test_add_index_supersede_and_list_all():
    h = home()
    r = repo(h)
    old = add(h, r, "Deploy on Fridays is allowed", "card abc123")
    assert old.endswith("-deploy-on-fridays-is-allowed.md"), old
    idx = cli(h, r, "index").stdout
    assert old in idx and "Deploy on Fridays is allowed" in idx, idx
    text = cli(h, r, "show", old).stdout
    assert "status: active" in text and "source: card abc123" in text and "## Why" in text, text

    new = add(h, r, "No deploys on Fridays")
    r2 = cli(h, r, "supersede", old, "--by", new)
    assert r2.returncode == 0, r2.stderr
    idx = cli(h, r, "index").stdout
    assert new in idx and old not in idx, idx
    hooked = hook(h, r).stdout
    assert new in hooked and old not in hooked, hooked
    assert old not in cli(h, r, "list").stdout
    every = cli(h, r, "list", "--all").stdout
    assert old in every and new in every and "superseded" in every, every
    # never deleted, and both sides of the link are recorded
    assert f"superseded_by: {new}" in cli(h, r, "show", old).stdout
    assert f"supersedes: {old}" in cli(h, r, "show", new).stdout
    # superseding twice is refused, and so is a decision superseding itself
    assert cli(h, r, "supersede", old, "--by", new).returncode == 2
    assert cli(h, r, "supersede", new, "--by", new).returncode == 2


def test_add_with_supersedes_marks_the_old_one_in_the_same_call():
    h = home()
    r = repo(h)
    old = add(h, r, "Tabs")
    new = add(h, r, "Spaces", "owner", "## What\nspaces\n\n## Why\nconsistency\n", "--supersedes", old)
    idx = cli(h, r, "index").stdout
    assert new in idx and old not in idx, idx
    assert old in cli(h, r, "list", "--all").stdout


def test_add_with_a_missing_supersedes_writes_nothing():
    h = home()
    r = repo(h)
    add(h, r, "First")
    bad = cli(h, r, "add", "--title", "Second", "--source", "owner", "--supersedes", "2020-01-01-nope.md", body="b\n")
    assert bad.returncode == 2 and "no such decision" in bad.stderr, bad.stderr
    assert "Second" not in cli(h, r, "list", "--all").stdout


def test_the_same_title_twice_gets_two_files():
    """One file per decision is the drop-box: a second add never overwrites the first."""
    h = home()
    r = repo(h)
    a, b = add(h, r, "Same"), add(h, r, "Same")
    assert a != b and b.endswith("-same-2.md"), (a, b)
    assert len(cli(h, r, "list").stdout.splitlines()) == 2


def test_add_refuses_a_bad_source_an_empty_body_and_an_empty_title():
    h = home()
    r = repo(h)
    for args, body in ((("--title", "T", "--source", "somebody"), "b\n"),
                       (("--title", "T", "--source", "card ../x"), "b\n"),
                       (("--title", "T", "--source", "owner"), "  \n"),
                       (("--title", " \n ", "--source", "owner"), "b\n")):
        out = cli(h, r, "add", *args, body=body)
        assert out.returncode == 2 and "Nothing was written" in out.stderr, (args, out.stderr)
    assert cli(h, r, "list", "--all").stdout == ""


def test_a_newline_in_the_title_cannot_add_a_field():
    h = home()
    r = repo(h)
    name = add(h, r, "Real\nstatus: superseded")
    text = cli(h, r, "show", name).stdout
    assert "status: active" in text and "\nstatus: superseded" not in text, text
    assert name in cli(h, r, "index").stdout


def test_the_cli_outside_a_repository_says_so():
    h = home()
    out = cli(h, h, "path")
    assert out.returncode == 2 and "not a git repository" in out.stderr, out.stderr


# 3. the budget

def test_the_hook_stays_under_the_budget_and_names_the_overflow():
    h = home()
    r = repo(h)
    names = [add(h, r, f"Decision number {i} with a reasonably long title to fill the line") for i in range(40)]
    budget = 1200
    out = hook(h, r, SQUAD_DECISIONS_BUDGET=str(budget)).stdout
    assert len(out.encode("utf-8")) <= budget, len(out.encode("utf-8"))
    shown = sum(1 for n in names if n in out)
    assert 0 < shown < 40, shown
    assert f"{40 - shown} more left out" in out and "squad-decisions list" in out, out
    # the default budget is 4000 bytes
    default = hook(h, r).stdout
    assert len(default.encode("utf-8")) <= 4000 and "more left out" in default, len(default)
    # under the budget, nothing is cut
    few = hook(h, r, SQUAD_DECISIONS_BUDGET="100000").stdout
    assert all(n in few for n in names) and "left out" not in few


def test_a_tiny_budget_never_overflows():
    h = home()
    r = repo(h)
    for i in range(3):
        add(h, r, f"D{i}")
    for budget in (0, 1, 40, 90, 200):
        out = hook(h, r, SQUAD_DECISIONS_BUDGET=str(budget)).stdout
        assert len(out.encode("utf-8")) <= budget, (budget, out)


def test_the_budget_counts_bytes_not_characters():
    h = home()
    r = repo(h)
    for i in range(30):
        add(h, r, f"Decisão número {i}: usar ações idempotentes em toda migração ✓")
    out = hook(h, r, SQUAD_DECISIONS_BUDGET="900").stdout
    assert len(out.encode("utf-8")) <= 900 and "more left out" in out, len(out.encode("utf-8"))


def test_the_budget_holds_in_bytes_on_a_crlf_stdout():
    """A text-mode stdout on Windows writes "\r\n" for "\n"; the hook writes bytes, so the budget still holds:
    the raw bytes are the same as on a plain pipe, which the budget tests above hold to the cap."""
    h = home()
    r = repo(h)
    for i in range(12):
        add(h, r, f"Line ending case {i}")
    budget = len(cli(h, r, "index").stdout.encode("utf-8")) - 150
    plain = subprocess.run([sys.executable, HOOK], cwd=r, input=json.dumps({"cwd": r}).encode(),
                           capture_output=True, env=env(h, SQUAD_DECISIONS_BUDGET=str(budget))).stdout
    assert b"more left out" in plain and len(plain) <= budget, plain
    crlf = ("import importlib.util, io, sys\n"
            "spec = importlib.util.spec_from_file_location('d', sys.argv[1])\n"
            "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
            "sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', newline='\\r\\n')\n"
            "m.hook(); sys.stdout.flush()\n")
    out = subprocess.run([sys.executable, "-c", crlf, HOOK], cwd=r, input=json.dumps({"cwd": r}).encode(),
                         capture_output=True, env=env(h, SQUAD_DECISIONS_BUDGET=str(budget)))
    assert out.returncode == 0 and out.stderr == b"", out
    assert len(out.stdout) <= budget and out.stdout == plain, (len(out.stdout), budget, out.stdout[:200])


# 4. the hook never fails a session

def test_a_full_stdout_still_exits_0():
    """The interpreter's last flush of a full stdout used to end the hook with exit 120."""
    h = home()
    r = repo(h)
    add(h, r, "Anything at all")
    full = ("import io, runpy, sys\n"
            "class Full(io.RawIOBase):\n"
            "    def writable(self): return True\n"
            "    def write(self, b): raise OSError(28, 'No space left on device')\n"
            "sys.stdout = io.TextIOWrapper(io.BufferedWriter(Full()), encoding='utf-8')\n"
            "sys.argv = [sys.argv[1]]\n"
            "runpy.run_path(sys.argv[0], run_name='__main__')\n")
    out = subprocess.run([sys.executable, "-c", full, HOOK], cwd=r, input=json.dumps({"cwd": r}),
                         capture_output=True, text=True, encoding="utf-8", env=env(h))
    assert out.returncode == 0 and out.stderr == "", out
    if os.path.exists("/dev/full"):                   # the real device too, where the OS has one
        with open("/dev/full", "w") as dev:
            out = subprocess.run([sys.executable, HOOK], cwd=r, input=b"{}", stdout=dev,
                                 stderr=subprocess.PIPE, env=env(h))
        assert out.returncode == 0 and out.stderr == b"", out


def test_the_hook_is_silent_when_there_is_nothing_to_say():
    h = home()
    r = repo(h)
    out = hook(h, r)
    assert out.returncode == 0 and out.stdout == "" and out.stderr == "", out
    outside = os.path.join(h, "plain")
    os.makedirs(outside)
    out = hook(h, outside)
    assert out.returncode == 0 and out.stdout == "" and out.stderr == "", out


def test_the_hook_on_malformed_input_prints_nothing():
    h = home()
    r = repo(h)
    add(h, r, "Something")
    for raw in ("{not json", "[1, 2]", "\"text\"", "\xff\xfe", ""):
        out = hook(h, r, raw=raw)
        if raw == "":                                 # no input at all is not malformed: the cwd is used
            assert out.returncode == 0 and out.stderr == "", out
            continue
        assert out.returncode == 0 and out.stdout == "" and out.stderr == "", (raw, out)
    # a cwd that is not a string, or does not exist, falls back to the process cwd
    for cwd in (5, None, os.path.join(h, "gone")):
        out = hook(h, r, payload={"cwd": cwd})
        assert out.returncode == 0 and "Something" in out.stdout and out.stderr == "", (cwd, out)


def test_the_hook_uses_the_cwd_of_the_payload():
    h = home()
    r = repo(h)
    add(h, r, "From the payload")
    outside = os.path.join(h, "plain")
    os.makedirs(outside)
    out = subprocess.run([sys.executable, HOOK], cwd=outside, input=json.dumps({"cwd": r}),
                         capture_output=True, text=True, encoding="utf-8", env=env(h))
    assert "From the payload" in out.stdout, out


def test_the_hook_with_an_unreadable_store_prints_nothing():
    if os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0):
        print("   (skipped the unreadable store: chmod does not deny reading here)")
        return
    h = home()
    r = repo(h)
    add(h, r, "Hidden")
    store = cli(h, r, "path").stdout.strip()
    os.chmod(store, 0)
    try:
        out = hook(h, r)
        assert out.returncode == 0 and out.stdout == "" and out.stderr == "", out
    finally:
        os.chmod(store, stat.S_IRWXU)


def test_an_unreadable_or_broken_decision_is_skipped_not_fatal():
    h = home()
    r = repo(h)
    good = add(h, r, "Good")
    store = cli(h, r, "path").stdout.strip()
    with open(os.path.join(store, "2026-01-01-broken.md"), "wb") as fh:
        fh.write(b"\xff\xfe not utf-8")
    with open(os.path.join(store, "2026-01-02-nofront.md"), "w", encoding="utf-8") as fh:
        fh.write("just text\n")
    out = hook(h, r)
    assert out.returncode == 0 and good in out.stdout and "broken" not in out.stdout, out


def test_squad_decisions_0_turns_the_hook_off():
    h = home()
    r = repo(h)
    add(h, r, "Any")
    assert hook(h, r, SQUAD_DECISIONS="0").stdout == ""


def test_a_bom_or_a_capitalised_status_does_not_hide_a_decision():
    h = home()
    r = repo(h)
    store = cli(h, r, "path").stdout.strip()
    os.makedirs(store)
    files = {"2026-01-01-bom.md": "\ufeff---\ntitle: With a BOM\nstatus: active\n---\nx\n",
             "2026-01-02-caps.md": "---\r\ntitle: Capitalised\r\nstatus: Active\r\n---\r\nx\r\n",
             "2026-01-03-typo.md": "---\ntitle: Typo status\nstatus: actvie\n---\nx\n"}
    for name, text in files.items():
        with open(os.path.join(store, name), "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
    out = hook(h, r).stdout
    assert "With a BOM" in out and "Capitalised" in out, out
    assert "Typo status" not in out, "an unknown status is not active"
    every = cli(h, r, "list", "--all").stdout
    assert "Typo status" in every and "[actvie," in every, every
    assert "[Active," in every, "list --all shows the status as written"
    # the capitalised one is active for supersede too
    assert cli(h, r, "supersede", "2026-01-02-caps.md", "--by", "2026-01-01-bom.md").returncode == 0
    assert "Capitalised" not in hook(h, r).stdout


# 5. one session wins a race, and a failure leaves no half state

def _store_with(m, *titles):
    folder = home()
    names = []
    for i, t in enumerate(titles):
        name = f"2026-01-0{i + 1}-{m.slug(t)}.md"
        m.write(folder, name, {"title": t, "date": f"2026-01-0{i + 1}", "source": "owner", "status": "active"},
                "## What\nx\n")
        names.append(name)
    return folder, names


def test_two_concurrent_supersedes_of_one_decision_one_wins():
    """Reading the old decision is slowed down so both sessions are inside check-then-mark at once."""
    m = module()
    folder, (old, a, b) = _store_with(m, "Old", "New A", "New B")
    real = m.read

    def slow(f, name):
        got = real(f, name)
        if name == old:
            time.sleep(0.3)
        return got
    m.read = slow
    results = {}

    def run(by):
        try:
            results[by] = m.cmd_supersede(folder, types.SimpleNamespace(file=old, by=by))
        except m.Refused as e:
            results[by] = e
    with contextlib.redirect_stdout(io.StringIO()):
        threads = [threading.Thread(target=run, args=(n,)) for n in (a, b)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    m.read = real
    wins = [n for n, v in results.items() if v == 0]
    assert len(wins) == 1, results
    meta, _ = m.read(folder, old)
    assert meta["status"] == "superseded" and meta["superseded_by"] == wins[0], meta
    loser = a if wins[0] == b else b
    assert not m.read(folder, loser)[0].get("supersedes"), "the loser does not claim to supersede it"


def test_a_failure_in_add_supersedes_or_supersede_leaves_no_half_state():
    m = module()
    real = m.write

    def failing(target):
        def write(f, name, meta, body):
            if target(name):
                raise OSError(28, "No space left on device")
            return real(f, name, meta, body)
        return write

    def add_args(old):
        return types.SimpleNamespace(title="Replacement", source="owner", supersedes=old)

    # marking the old one fails: no new decision, the old one still the only active one
    folder, (old,) = _store_with(m, "Old")
    m.write = failing(lambda n: n == old)
    sys.stdin, stdin = io.StringIO("## What\nnew\n"), sys.stdin
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                m.cmd_add(folder, add_args(old))
            except OSError:
                pass
            else:
                raise AssertionError("the failure was swallowed")
        assert [n for n, _ in m.active(folder)] == [old], m.active(folder)
        assert sorted(os.listdir(folder)) == [m.LOCK, old], os.listdir(folder)

        # writing the new one fails after the old one was marked: the old one is put back as it was
        m.write = failing(lambda n: n != old)
        sys.stdin = io.StringIO("## What\nnew\n")
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                m.cmd_add(folder, add_args(old))
            except OSError:
                pass
            else:
                raise AssertionError("the failure was swallowed")
    finally:
        sys.stdin = stdin
        m.write = real
    assert [n for n, _ in m.active(folder)] == [old] and not m.read(folder, old)[0].get("superseded_by")
    assert sorted(os.listdir(folder)) == [m.LOCK, old], os.listdir(folder)

    # supersede: recording `supersedes` on the new one fails, so the old one is not left marked
    folder, (old, new) = _store_with(m, "Old", "New")
    m.write = failing(lambda n: n == new)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                m.cmd_supersede(folder, types.SimpleNamespace(file=old, by=new))
            except OSError:
                pass
            else:
                raise AssertionError("the failure was swallowed")
    finally:
        m.write = real
    assert m.status(m.read(folder, old)[0]) == "active", m.read(folder, old)


# 6. nothing escapes the store

def test_a_title_cannot_name_a_path():
    h = home()
    r = repo(h)
    store = cli(h, r, "path").stdout.strip()
    for title in ("../../escape", "/etc/passwd", "..", "a/../../b", "C:\\Windows\\x", "~/.ssh/id"):
        name = add(h, r, title)
        assert os.sep not in name and "/" not in name and ".." not in name, name
        assert os.path.isfile(os.path.join(store, name)), name
    assert not os.path.exists(os.path.join(h, "escape.md")) and not os.path.exists(os.path.join(h, "escape"))
    assert sorted(os.listdir(os.path.dirname(store))) == [os.path.basename(store)], "nothing next to the store"


def test_a_file_argument_cannot_leave_the_store():
    h = home()
    r = repo(h)
    real = add(h, r, "Real")
    store = cli(h, r, "path").stdout.strip()
    secret = os.path.join(h, "2026-01-01-secret.md")
    with open(secret, "w", encoding="utf-8") as fh:
        fh.write("---\ntitle: SECRET\nstatus: active\n---\nSECRET BODY\n")
    rel = os.path.relpath(secret, store)
    for bad in ("../2026-01-01-secret.md", rel, secret, "/etc/passwd", "..", ".md", "2026-01-01-x/../../y.md"):
        for args in (("show", bad), ("supersede", bad, "--by", real), ("supersede", real, "--by", bad)):
            out = cli(h, r, *args)
            assert out.returncode == 2 and "SECRET" not in out.stdout, (args, out.stdout, out.stderr)
        out = cli(h, r, "add", "--title", "T", "--source", "owner", "--supersedes", bad, body="b\n")
        assert out.returncode == 2, (bad, out.stderr)
    assert "status: active" in cli(h, r, "show", real).stdout, "the real decision was left alone"
    with open(secret, encoding="utf-8") as fh:
        assert "superseded" not in fh.read()


def test_a_symlink_in_the_store_is_not_followed():
    if os.name == "nt":
        print("   (skipped the symlink case: creating one needs a privilege on Windows)")
        return
    h = home()
    r = repo(h)
    add(h, r, "Real")
    store = cli(h, r, "path").stdout.strip()
    secret = os.path.join(h, "outside.md")
    with open(secret, "w", encoding="utf-8") as fh:
        fh.write("---\ntitle: SECRET\nstatus: active\n---\nSECRET BODY\n")
    os.symlink(secret, os.path.join(store, "2026-01-01-link.md"))
    assert "SECRET" not in hook(h, r).stdout
    assert "SECRET" not in cli(h, r, "show", "2026-01-01-link.md").stdout


def test_nul_and_separators_never_survive_the_name_checks():
    """A NUL cannot reach argv (the OS refuses it), so the functions behind --title and <file> are checked here."""
    m = module()
    for title in ("a\x00b", "\x00../../x", "../\x00/etc/passwd", "/abs/path", "..\\..\\win"):
        s = m.slug(title)
        assert s and all(c.isalnum() and c.isascii() or c == "-" for c in s), (title, s)
        assert "\x00" not in m.one_line(title) and "\n" not in m.one_line(title + "\nstatus: x")
    for bad in ("2026-01-01-a\x00b.md", "2026-01-01-a.md\x00", "\x002026-01-01-a.md", "../2026-01-01-a.md",
                "/tmp/2026-01-01-a.md", "2026-01-01-a/b.md", "2026-01-01-a\\b.md", "2026-01-01-.md"):
        try:
            m.safe_name(bad)
        except m.Refused:
            continue
        raise AssertionError(f"accepted {bad!r}")
    assert m.safe_name("2026-01-01-ok") == "2026-01-01-ok.md"


# wiring

def test_the_manifest_registers_the_hook_on_session_start():
    with open(MANIFEST, encoding="utf-8") as fh:
        hooks = json.load(fh)["hooks"]
    mine = [x for x in hooks if x["script"] == "squad-decisions.py"]
    assert len(mine) == 1 and mine[0]["events"] == ["SessionStart"], mine
    assert mine[0]["timeout"] <= 10


def test_install_links_the_hook_and_the_command():
    if os.name == "nt":
        print("   (skipped install.sh: it is the Linux and macOS installer)")
        return
    h = home()
    e = env(h, CLAUDE_CONFIG_DIR=os.path.join(h, "claude"), PATH="/usr/bin:/bin")
    out = subprocess.run(["bash", os.path.join(ROOT, "install.sh")], capture_output=True, text=True, env=e)
    for link in (os.path.join(h, "claude", "hooks", "squad-decisions.py"),
                 os.path.join(h, ".local", "bin", "squad-decisions")):
        assert os.path.islink(link) and os.path.realpath(link) == os.path.realpath(HOOK), (link, out.stderr)
    assert "squad-decisions.py on SessionStart" in out.stdout, out.stdout


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok ", name)
