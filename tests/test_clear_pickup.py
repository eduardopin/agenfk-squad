#!/usr/bin/env python3
"""The clear-pickup helper: what a session touched, the handoff written when a session is
cleared without one, and whether a handoff is still the one to pick up.

Self-checking: the exit code is the verdict. HOME points at a throwaway directory for every
case, so nothing touches ~/.claude. Every throwaway directory lives under one directory of the
run, removed at the end; the last line proves it.

    python3 tests/test_clear_pickup.py
"""
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(ROOT, "skills", "clear-pickup", "scripts", "handoff.py")
OLD = "7e4e62fc-4075-4d0f-ad2f-2f0ac762ed3b"
NEXT = "d744e2a1-bbe1-4821-afae-4122ad13cb22"
ME = "b659b24e-0862-4864-ad8f-9f9a9204d20b"
CARD = "15fbd96a-bdf4-49c8-9ba6-58feb3259246"
OTHER = "e84b26c9-0e2b-4471-89e5-84538e24ca06"


def home():
    """A fresh HOME; the module is loaded after it is set, so its paths point here."""
    h = tempfile.mkdtemp(prefix="clear-pickup-")
    os.environ["HOME"] = h
    os.environ["USERPROFILE"] = h
    return h


def lib():
    spec = importlib.util.spec_from_file_location("handoff_lib", LIB)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def put(path, text, mtime=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    if mtime:
        os.utime(path, (mtime, mtime))
    return path


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def rec(kind, content, sidechain=False, ts="2026-10-26T17:00:00Z", **extra):
    return {"type": kind, "isSidechain": sidechain, "timestamp": ts,
            "message": {"role": kind, "content": content}, **extra}


def bash(command):
    return rec("assistant", [{"type": "tool_use", "name": "Bash", "input": {"command": command}}])


def transcript(path, records):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    return path


def handoff(path, session, body="# Handoff: x\n", mtime=None):
    return put(path, f"{body}\n- **Session:** {session}\n", mtime)


def git(cwd, *args):
    subprocess.run(["git", "-C", cwd, *args], check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


def hdir(h):
    return os.path.join(h, "repo", ".claude", "handoffs")


def cli(h, *args, session=ME, cwd=None):
    env = {**os.environ, "HOME": h, "USERPROFILE": h, "CLAUDE_CODE_SESSION_ID": session}
    return subprocess.run([sys.executable, "-I", LIB, *args], env=env, cwd=cwd,
                          capture_output=True, text=True)


# ---------------------------------------------------------------- what the session touched
def test_cards_are_the_ones_this_session_ran_agenfk_on():
    t = transcript(os.path.join(home(), "s.jsonl"), [
        bash(f"agenfk verify {CARD} --evidence 'x'"),
        bash(f"cd /r && agenfk comment {CARD[:8]} \"PAUSE: done\""),
        rec("assistant", [{"type": "tool_use", "name": "Bash",
                           "input": {"command": f"agenfk verify {OTHER} --evidence y"}}], sidechain=True),
        bash("agenfk list --active --json"),
    ])
    assert lib().session_cards(t) == [CARD]


def test_cards_newest_first_without_duplicates():
    t = transcript(os.path.join(home(), "s.jsonl"), [
        bash(f"agenfk comment {OTHER} 'a'"), bash(f"agenfk verify {CARD} --evidence x"),
        bash(f"agenfk update {OTHER[:8]} --status REVIEW")])
    assert lib().session_cards(t) == [OTHER, CARD]


def test_a_card_only_read_is_not_one_the_session_worked_on():
    t = transcript(os.path.join(home(), "s.jsonl"), [
        bash(f"agenfk get {OTHER} --json"), bash(f"agenfk verify {CARD} --evidence x")])
    assert lib().session_cards(t) == [CARD]


def test_a_project_flag_is_not_a_card():
    t = transcript(os.path.join(home(), "s.jsonl"), [
        bash(f"agenfk create TASK 'x' --project {OTHER}"),
        bash(f"agenfk update-project {OTHER} --verify-command x")])
    assert lib().session_cards(t) == []


def test_last_request_skips_notifications_commands_and_skill_bodies():
    t = transcript(os.path.join(home(), "s.jsonl"), [
        rec("user", "Fix the tokens column on startup"),
        rec("assistant", [{"type": "text", "text": "Working on it."}]),
        rec("user", "<task-notification>done</task-notification>"),
        rec("user", [{"type": "text", "text": "Base directory for this skill: /x"}], isMeta=True),
        rec("user", [{"type": "tool_result", "tool_use_id": "t", "content": "ok"}]),
        rec("user", "Another Claude session sent a message: hand-back"),
        rec("assistant", [{"type": "text", "text": "The fix is merged; reinstall is next."}]),
    ])
    m = lib()
    assert m.last_request(t)[0] == "Fix the tokens column on startup"
    assert m.last_reply(t) == "The fix is merged; reinstall is next."


def test_a_request_that_starts_with_a_bracket_is_still_the_request():
    t = transcript(os.path.join(home(), "s.jsonl"), [rec("user", "older ask"),
                                                     rec("user", "<5 min: retry the deploy")])
    assert lib().last_request(t)[0] == "<5 min: retry the deploy"


def test_harness_tags_are_never_the_request():
    m = lib()
    for tag in ("<bash-stdout>ok</bash-stdout>", "<user-memory-input>x</user-memory-input>",
                "<persisted-output path=x>", "<system-reminder>y</system-reminder>"):
        t = transcript(os.path.join(home(), "s.jsonl"), [rec("user", "the real ask"), rec("user", tag)])
        assert m.last_request(t)[0] == "the real ask", tag


# ---------------------------------------------------------------- the automatic handoff
def test_auto_handoff_holds_ground_truth_and_the_last_request():
    h = home()
    repo = os.path.join(h, "repo")
    subprocess.run(["git", "init", "-q", "-b", "main", repo], check=True)
    put(os.path.join(repo, "a.txt"), "a")
    git(repo, "add", "a.txt")
    git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "first commit")
    put(os.path.join(repo, "dirty.txt"), "x")
    t = transcript(os.path.join(h, "s.jsonl"), [
        rec("user", "Install the build and open the app"),
        bash(f"agenfk verify {CARD} --evidence x"),
        rec("assistant", [{"type": "text", "text": "Build installed; app not opened yet."}]),
    ])
    path, digest = lib().write_auto(ME, t, repo)
    text = read(path)
    copies = os.path.join(h, ".claude", "squad", "handoffs")
    assert os.path.dirname(path) == copies, path
    if os.name != "nt":
        assert (os.stat(path).st_mode & 0o777) == 0o400
    assert f"**Session:** {ME}" in text
    assert "main @ " in text and "first commit" in text and "dirty.txt" in text
    assert "Install the build and open the app" in text
    assert "Build installed; app not opened yet." in text
    assert CARD in text and OTHER not in text
    assert "## Next step" in text and "UNKNOWN" not in text.split("## Goal")[0]
    with open(path, "rb") as fh:
        raw = fh.read()
    assert hashlib.sha256(raw).hexdigest() == digest
    assert os.path.basename(path) == f"{ME}-{digest[:12]}.md", "named by its own sha256"


def test_auto_handoff_outside_git_says_so():
    h = home()
    t = transcript(os.path.join(h, "s.jsonl"), [rec("user", "look at logs")])
    path, _ = lib().write_auto(ME, t, h)
    assert "not a git repository" in read(path)


def test_auto_handoff_goes_through_the_watchers_sealed_copy():
    """No second copy of the write: the file comes out of squad-switch's sealed_copy."""
    h = home()
    t = transcript(os.path.join(h, "s.jsonl"), [rec("user", "ask")])
    m = lib()
    seen = []
    real = m.SWITCH.sealed_copy
    m.SWITCH.sealed_copy = lambda *a, **k: (seen.append(a), real(*a, **k))[1]
    path, digest = m.write_auto(ME, t, h)
    assert len(seen) == 1 and seen[0][1] == digest and seen[0][2] == ME
    assert not [f for f in os.listdir(os.path.dirname(path)) if f.endswith(".tmp")]


# ---------------------------------------------------------------- is it still the one to pick up?
def test_a_handoff_nobody_picked_up_is_current():
    h = home()
    p = handoff(os.path.join(hdir(h), "20261024-231046-x.md"), OLD)
    r = lib().check(p, ME)
    assert r["status"] == "current" and r["session"] == OLD


def test_a_handoff_the_swap_already_delivered_is_superseded():
    h = home()
    d = hdir(h)
    p = handoff(os.path.join(d, "20261025-232538-a.md"), OLD, mtime=time.time() - 3600)
    put(os.path.join(h, ".claude", "squad", "swaps.log"),
        f"2026-10-25 23:32:21 wH:p2V {OLD[:8]} -> {NEXT[:8]} pickup sent\n")
    newer = handoff(os.path.join(d, "20261026-101010-b.md"), NEXT)
    r = lib().check(p, ME)
    assert r["status"] == "superseded"
    assert r["picked_by"] == [NEXT[:8]] and r["newest"] == newer


def test_the_session_the_swap_woke_is_not_its_own_successor():
    h = home()
    p = handoff(os.path.join(h, "copies", "handoffs", f"{OLD}-abc.md"), OLD)
    put(os.path.join(h, ".claude", "squad", "swaps.log"),
        f"2026-10-25 23:32:21 wH:p2V {OLD[:8]} -> {ME[:8]} pickup sent\n")
    assert lib().check(p, ME)["status"] == "current"


def test_a_claimed_handoff_is_superseded_for_the_next_reader():
    h = home()
    p = handoff(os.path.join(hdir(h), "20261024-x.md"), OLD)
    m = lib()
    m.claim(p, NEXT)
    assert m.check(p, NEXT)["status"] == "current"      # the claimer itself, again
    r = m.check(p, ME)
    assert r["status"] == "superseded" and r["picked_by"] == [NEXT[:8]]


def test_a_later_handoff_of_the_same_session_supersedes():
    h = home()
    d = hdir(h)
    p = handoff(os.path.join(d, "1.md"), OLD, mtime=time.time() - 600)
    later = handoff(os.path.join(d, "2.md"), OLD, body="# Handoff: y\n")
    r = lib().check(p, ME)
    assert r["status"] == "superseded" and r["newest"] == later


def test_newer_handoffs_of_unrelated_sessions_make_it_stale():
    h = home()
    d = hdir(h)
    p = handoff(os.path.join(d, "20261024-231046-x.md"), OLD, mtime=time.time() - 86400)
    newer = handoff(os.path.join(d, "20261025-232538-y.md"), NEXT)
    r = lib().check(p, ME)
    assert r["status"] == "stale" and r["newer_in_project"] == [newer]


def test_a_live_successor_owns_the_work():
    h = home()
    p = handoff(os.path.join(hdir(h), "x.md"), OLD)
    m = lib()
    m.claim(p, NEXT)
    put(os.path.join(h, ".claude", "projects", "-p", f"{NEXT}.jsonl"), "{}\n")
    r = m.check(p, ME)
    assert r["status"] == "superseded" and r["live"] == NEXT[:8]


def test_shared_copies_folder_never_makes_a_handoff_stale():
    # the automatic handoffs of every session share one folder
    h = home()
    d = os.path.join(h, ".claude", "squad", "handoffs")
    p = handoff(os.path.join(d, f"{OLD}-auto-1.md"), OLD, mtime=time.time() - 600)
    handoff(os.path.join(d, f"{NEXT}-auto-2.md"), NEXT)
    assert lib().check(p, ME)["status"] == "current"


def test_claim_cannot_forge_a_pickup_row():
    if os.name == "nt":
        # Windows refuses a tab or a newline in a path, so no handoff path can forge a row there.
        print("skipped: Windows paths cannot hold a tab or a newline")
        return
    h = home()
    p = handoff(os.path.join(h, "a\tb\nc", "x.md"), OLD)
    lib().claim(p, NEXT)
    rows = read(os.path.join(h, ".claude", "squad", "pickups.log")).splitlines()
    assert len(rows) == 1 and rows[0].count("\t") == 3


def test_two_sessions_claiming_one_handoff_both_keep_their_row():
    h = home()
    p = handoff(os.path.join(hdir(h), "x.md"), OLD)
    m = lib()
    m.claim(p, NEXT)
    m.claim(p, ME)
    rows = read(os.path.join(h, ".claude", "squad", "pickups.log")).splitlines()
    assert [r.split("\t")[2] for r in rows] == [NEXT[:8], ME[:8]], rows


def test_a_session_id_with_a_tab_cannot_forge_a_column():
    h = home()
    p = handoff(os.path.join(hdir(h), "x.md"), OLD)
    lib().claim(p, "ab\tcd\nef")
    rows = read(os.path.join(h, ".claude", "squad", "pickups.log")).splitlines()
    assert len(rows) == 1 and rows[0].count("\t") == 3, rows

# ---------------------------------------------------------------- the command line
def test_cli_check_prints_json_and_claim_records():
    h = home()
    p = handoff(os.path.join(hdir(h), "x.md"), OLD)
    out = cli(h, "check", p)
    assert out.returncode == 0 and json.loads(out.stdout)["status"] == "current"
    assert cli(h, "claim", p).returncode == 0
    assert f"{OLD[:8]}\t{ME[:8]}" in read(os.path.join(h, ".claude", "squad", "pickups.log"))


def test_cli_facts_lists_only_this_sessions_cards():
    h = home()
    t = transcript(os.path.join(h, ".claude", "projects", "-p", f"{ME}.jsonl"),
                   [bash(f"agenfk verify {CARD} --evidence x")])
    out = cli(h, "facts", cwd=h)
    d = json.loads(out.stdout)
    assert d["session"] == ME and d["transcript"] == t and d["cards"] == [CARD]


def test_cli_help_exits_zero_and_names_every_command():
    h = home()
    for args in (("--help",), ("-h",), ("check", "--help")):
        out = cli(h, *args)
        assert out.returncode == 0, (args, out.stderr)
    out = cli(h, "--help")
    assert all(w in out.stdout for w in ("facts", "check", "claim")), out.stdout


def test_cli_claim_without_a_session_refuses_and_a_bad_command_fails():
    h = home()
    p = handoff(os.path.join(hdir(h), "x.md"), OLD)
    assert cli(h, "claim", p, session="").returncode != 0
    assert not os.path.exists(os.path.join(h, ".claude", "squad", "pickups.log"))
    assert cli(h, "bogus").returncode != 0


def test_cli_write_auto_prints_the_path_and_digest():
    h = home()
    t = transcript(os.path.join(h, ".claude", "projects", "-p", f"{ME}.jsonl"), [rec("user", "go on")])
    out = cli(h, "auto", ME, t, h)
    assert out.returncode == 0, out.stderr
    path, digest = out.stdout.split()
    assert os.path.isfile(path) and digest[:12] in os.path.basename(path)


def test_cli_write_auto_failing_to_write_is_one_line_not_a_traceback():
    h = home()
    t = transcript(os.path.join(h, ".claude", "projects", "-p", f"{ME}.jsonl"), [rec("user", "go on")])
    put(os.path.join(h, ".claude", "squad", "handoffs"), "a file where the folder should be")
    out = cli(h, "auto", ME, t, h)
    assert out.returncode == 1 and "Traceback" not in out.stderr, out.stderr
    assert out.stderr.startswith("handoff.py:") and out.stdout == "", (out.stdout, out.stderr)


def test_a_date_in_the_file_name_is_not_a_session():
    h = home()
    d = hdir(h)
    a = put(os.path.join(d, "20261104-141330-a.md"), "# Handoff: a\n")
    b = put(os.path.join(d, "20261104-194930-b.md"), "# Handoff: b\n")
    m = lib()
    assert m.handoff_session(a) == ""
    m.claim(a, NEXT)
    r = m.check(b, ME)
    assert r["status"] == "current" and r["picked_by"] == [] and r["newest"] is None


def test_a_sealed_copy_name_still_gives_its_session():
    h = home()
    m = lib()
    p = put(os.path.join(m.COPIES, f"{OLD}-0123456789ab.md"), "# Handoff: x\n")
    assert m.handoff_session(p) == OLD


def test_a_symlinked_handoff_is_unreadable():
    h = home()
    d = hdir(h)
    real = handoff(os.path.join(h, "outside.md"), OLD)
    link = os.path.join(d, "20261026-000000-x.md")
    os.makedirs(d, exist_ok=True)
    os.symlink(real, link)
    r = lib().check(link, ME)
    assert r["status"] == "unreadable", r


def test_a_symlinked_candidate_is_never_the_successor():
    h = home()
    d = hdir(h)
    p = handoff(os.path.join(d, "1.md"), OLD, mtime=time.time() - 600)
    real = handoff(os.path.join(h, "outside.md"), OLD)
    os.symlink(real, os.path.join(d, "2.md"))
    assert lib().check(p, ME)["status"] == "current"


def test_pi_session_id_is_the_fallback_session():
    h = home()
    p = handoff(os.path.join(hdir(h), "20261024-231046-x.md"), OLD)
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_SESSION_ID"}
    env.update(HOME=h, USERPROFILE=h, PI_SESSION_ID=ME)
    out = subprocess.run([sys.executable, "-I", LIB, "claim", p], env=env,
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert ME[:8] in read(os.path.join(h, ".claude", "squad", "pickups.log"))
    facts = subprocess.run([sys.executable, "-I", LIB, "facts"], env=env,
                           capture_output=True, text=True)
    assert json.loads(facts.stdout)["session"] == ME


def test_auto_handoff_names_an_unknown_transcript_shape():
    h = home()
    t = transcript(os.path.join(h, "pi.jsonl"),
                   [{"type": "message", "message": {"role": "user", "content": "hi"}}])
    path, _ = lib().write_auto(ME, t, h)
    text = read(path)
    assert "UNKNOWN" in text and "not a Claude Code transcript" in text
    assert "> (none)" not in text


# ---------------------------------------------------------------- git
def test_git_facts_do_not_run_the_repository_fsmonitor():
    h = home()
    repo = os.path.join(h, "repo")
    subprocess.run(["git", "init", "-q", "-b", "main", repo], check=True)
    mark = os.path.join(h, "ran")
    git(repo, "config", "core.fsmonitor", f"touch {mark}; false")
    put(os.path.join(repo, "a.txt"), "a")
    lib().git_facts(repo)
    assert not os.path.exists(mark)


def test_git_runs_without_optional_locks_or_system_config():
    m = lib()
    seen = {}

    def fake(cmd, **kw):
        seen["cmd"], seen["env"] = cmd, kw.get("env") or {}
        raise OSError

    real = subprocess.run
    m.subprocess.run = fake
    try:
        m.run("/x", "status")
    finally:
        m.subprocess.run = real
    assert "--no-optional-locks" in seen["cmd"] and seen["env"].get("GIT_CONFIG_NOSYSTEM") == "1"


if __name__ == "__main__":
    # One temporary directory per run: every mkdtemp above lands in it.
    RUN_TMP = tempfile.mkdtemp(prefix="clear-pickup-run-")
    tempfile.tempdir = os.environ["TMPDIR"] = RUN_TMP
    try:
        for name, fn in sorted(globals().items()):
            if name.startswith("test_") and callable(fn):
                fn()
                print("ok ", name)
    finally:
        shutil.rmtree(RUN_TMP, ignore_errors=True)
    assert not os.path.exists(RUN_TMP), f"left behind: {RUN_TMP}"
    print("ok  nothing is left in the temporary directory after the run")
