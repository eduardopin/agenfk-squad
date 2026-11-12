#!/usr/bin/env python3
"""The watcher cuts the main session between 50% and 90% of the window, and only by arithmetic;
it switches the session even with a background task running.

Self-checking: the exit code is the verdict. Runs in a throwaway HOME, with a
fake herdr, and never touches ~/.claude.

    python3 tests/test_watch.py
"""
import atexit
import datetime
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(ROOT, "hooks", "squad-watch.py")
sys.path.insert(0, os.path.join(ROOT, "skills", "session-context", "scripts"))
import session_context as cx  # noqa: E402

M = 1_000_000


_HOMES = []


def home(short=False):
    """A throwaway HOME. short: under a short directory whatever TMPDIR is, for a test whose budget
    counts the length of a path under HOME (a TMPDIR over ~90 characters broke the compact text's)."""
    root = None
    if short and len(tempfile.gettempdir()) > 24:
        root = next((d for d in ("/tmp", os.environ.get("SystemRoot", "C:\\Windows")[:3] + "Temp")
                     if os.path.isdir(d) and os.access(d, os.W_OK)), None)
    h = tempfile.mkdtemp(prefix="watch-", dir=root)
    _HOMES.append(h)
    return h


@atexit.register
def _remove_homes():
    # every test creates a throwaway HOME; without this /tmp (tmpfs) runs out of inodes
    # when several sessions run the suite all day long
    for h in _HOMES:
        shutil.rmtree(h, ignore_errors=True)


def sh(path):
    """A path as a shell command holds it: forward slashes (Git Bash reads a backslash as an escape)."""
    return path.replace("\\", "/") if os.name == "nt" else path


def run_proc(h, event, sid, transcript, extra=None, env=None):
    p = {"hook_event_name": event, "session_id": sid, "transcript_path": transcript,
         "tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": h}
    p.update(extra or {})
    e = dict(os.environ, HOME=h, USERPROFILE=h)
    for key in list(e):
        if key.startswith("SQUAD_") or key.startswith("HERDR_"):
            del e[key]
    e.update(env or {})
    return subprocess.run([sys.executable, HOOK], input=json.dumps(p), capture_output=True,
                          text=True, env=e)


def run(h, event, sid, transcript, extra=None, env=None):
    return run_proc(h, event, sid, transcript, extra, env).stdout.strip()


def session(h, sid, ctx, share, burn=60):
    """Main-session transcript with this context, this reread share and this
    burn (% of the session budget)."""
    path = os.path.join(h, f"{sid}.jsonl")
    if share <= 0:           # everything written now, nothing reread
        recs = [{"cache_creation_input_tokens": ctx}]
    else:                    # 10 turns rereading ctx; the new work makes the share add up
        n = max(10, round(share * burn / 100 * cx.BUDGET / (ctx * 0.1)))
        reread = n * ctx * 0.1
        new = reread * (1 - share) / share
        recs = [{"cache_read_input_tokens": ctx, "output_tokens": int(new / n / 5)} for _ in range(n)]
    with open(path, "w", encoding="utf-8") as fh:
        for i, usage in enumerate(recs):
            fh.write(json.dumps({"type": "assistant", "isSidechain": False, "requestId": f"r{i}",
                                 "message": {"usage": usage}}) + "\n")
    return path


def said(out):
    if not out:
        return "nothing"
    data = json.loads(out)
    if data.get("decision") == "block":
        return "block: " + data["reason"]
    hso = data.get("hookSpecificOutput") or {}
    return hso.get("additionalContext") or hso.get("permissionDecisionReason") or ""


def kind(text):
    # the most specific first: the notice and the cancellation mention "PROACTIVE HANDOFF" in their text
    for word in ("ALERT CANCELLED", "Informational only", "Handoff out of turn",
                 "stopped without writing the handoff", "DISPATCH DENIED", "PROACTIVE HANDOFF"):
        if word in text:
            return word
    return text


def state(h, sid):
    with open(os.path.join(h, ".claude", "squad", "watch", f"{sid}.main.json"), encoding="utf-8") as fh:
        return json.load(fh)


def write_handoff(h, transcript, name="h1.md"):
    folder = os.path.join(h, "proj", ".claude", "handoffs")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name)
    body = "# handoff\nNext step: X\n"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    with open(transcript, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "assistant", "isSidechain": False, "message": {"content": [
            {"type": "tool_use", "name": "Write", "input": {"file_path": path, "content": body}}]}}) + "\n")
    return path


def test_the_math_is_fixed():
    def due(share, ctx, window, burn=60):
        reread = share * burn / 100 * cx.BUDGET
        return cx.handoff_math(int(reread * (1 - share) / share), 0, int(reread / 0.1), 0, ctx, window)["due"]
    assert cx.REREAD_SHARE == 0.60 and cx.REREAD_FLOOR_PCT == 50 and cx.HANDOFF_NOW == 90
    assert not due(0.95, 150_000, M)        # the case that used to cut sessions at 15%
    # lowest floor 50% since November 2026: at 70% no main session ever reached it
    assert not due(0.95, 499_000, M) and due(0.95, 500_000, M)
    assert due(0.60, 700_000, M)
    assert not due(0.59, 890_000, M)
    assert due(0.61, 90_000, 0)             # sub-session: floor 2*CTX_BASE
    assert due(0.61, 90_000, 0, burn=1)     # ...and with no window the burn does not move the floor
    # the floor weighs the burn: 90% of the window with zero burn, 50% with burn at 50% of the budget
    assert cx.BUDGET == 20_000_000 and cx.BURN_FULL_PCT == 50
    assert not due(0.90, 500_000, M, burn=10)   # context 50%, burn 10%: not the time
    assert not due(0.90, 700_000, M, burn=10)   # nor at 70%
    assert not due(0.90, 810_000, M, burn=10) and due(0.90, 830_000, M, burn=10)   # floor 82%
    assert not due(0.90, 690_000, M, burn=25) and due(0.90, 710_000, M, burn=25)   # floor 70%
    assert not due(0.90, 499_000, M, burn=80) and due(0.90, 500_000, M, burn=80)   # floor 50%
    # the floor table at its boundaries: burn 0% -> 90%, 10% -> 82%, 25% -> 70%, 50% and beyond -> 50%
    def floor(burn_pct):
        return cx.handoff_math(int(burn_pct / 100 * cx.BUDGET), 0, 0, 0, 500_000, M)["floor"]
    assert [floor(b) for b in (0, 10, 25, 50, 100)] == [900_000, 820_000, 700_000, 500_000, 500_000]
    assert not due(0.90, 499_000, M, burn=50) and due(0.90, 500_000, M, burn=50)


def test_cut_between_50_and_90_percent():
    cases = [  # context, reread, what the watcher says (burn 60%: floor at 50%)
        (100_000, 0.90, "nothing"), (150_000, 0.90, "nothing"), (300_000, 0.90, "nothing"),
        (490_000, 0.90, "nothing"), (499_000, 0.90, "nothing"),
        (500_000, 0.60, "PROACTIVE HANDOFF"), (520_000, 0.90, "PROACTIVE HANDOFF"),
        (520_000, 0.00, "Informational only"), (850_000, 0.50, "Informational only"),
        (890_000, 0.00, "Informational only"),
        (900_000, 0.00, "PROACTIVE HANDOFF"), (950_000, 0.30, "PROACTIVE HANDOFF"),
    ]
    h = home()
    for i, (ctx, share, expected) in enumerate(cases):
        sid = f"aaaaaaaa-{i:04d}"
        got = kind(said(run(h, "PostToolUse", sid, session(h, sid, ctx, share))))
        assert got == expected, (ctx, share, got)
        assert state(h, sid)["ctx_level"] == {"nothing": 0, "Informational only": 1,
                                              "PROACTIVE HANDOFF": 2}[expected]


def test_context_at_50_with_low_burn_is_not_handoff_time():
    # lowest floor 50% since November 2026: burn 10% -> 82%, burn 25% -> 70%, burn >= 50% -> 50%
    h = home()
    cases = [(550_000, 10, "Informational only"), (810_000, 10, "Informational only"),
             (830_000, 10, "PROACTIVE HANDOFF"), (690_000, 25, "Informational only"),
             (710_000, 25, "PROACTIVE HANDOFF"), (500_000, 60, "PROACTIVE HANDOFF")]
    for i, (ctx, burn, expected) in enumerate(cases):
        sid = f"aaaabbbb-{i:04d}"
        text = said(run(h, "PostToolUse", sid, session(h, sid, ctx, 0.9, burn)))
        assert kind(text) == expected and f"burn {burn}% of the budget" in text, (ctx, burn, text)


def test_default_window_is_1m_even_if_the_cockpit_recorded_200k():
    h = home()
    sid = "bbbbbbbb-0001"
    cache = os.path.join(h, ".claude", "cache", "session-tokens")
    os.makedirs(cache)
    with open(os.path.join(cache, f"{sid}.json"), "w", encoding="utf-8") as fh:
        json.dump({"window": 200_000}, fh)
    assert said(run(h, "PostToolUse", sid, session(h, sid, 170_000, 0.9))) == "nothing"


def test_alert_from_the_old_rule_is_cancelled_in_writing():
    h = home()
    sid = "cccccccc-0001"
    folder = os.path.join(h, ".claude", "squad", "watch")
    os.makedirs(folder)
    with open(os.path.join(folder, f"{sid}.main.json"), "w", encoding="utf-8") as fh:
        json.dump({"ctx_level": 2, "alert_ts": time.time() - 600, "nudges": 1}, fh)
    tr = session(h, sid, 160_000, 0.9)
    assert kind(said(run(h, "PostToolUse", sid, tr))) == "ALERT CANCELLED"
    st = state(h, sid)
    assert st["ctx_level"] == 0 and "alert_ts" not in st
    assert said(run(h, "Stop", sid, tr)) == "nothing"       # no longer demands the handoff


def test_handoff_on_the_agents_opinion_sends_the_work_back():
    h = home()
    sid = "dddddddd-0001"
    tr = session(h, sid, 200_000, 0.9)
    run(h, "UserPromptSubmit", sid, tr, {"prompt": "continue the merchant journey"})
    run(h, "PostToolUse", sid, tr)
    write_handoff(h, tr)
    assert kind(said(run(h, "Stop", sid, tr))) == "Handoff out of turn"
    assert said(run(h, "Stop", sid, tr, {"stop_hook_active": True})) == "nothing"   # once per request
    # when it was the owner who asked, the watcher stays out of it (in Portuguese or in English)
    for i, asked in enumerate(("faca o handoff, vamos encerrar por hoje",
                               "write the handoff, let's wrap up for today")):
        run(h, "UserPromptSubmit", sid, tr, {"prompt": asked})
        assert state(h, sid)["asked_stop"] is True, asked
        os.utime(write_handoff(h, tr, f"h{i + 2}.md"))
        assert said(run(h, "Stop", sid, tr)) == "nothing", asked


def test_mistyped_threshold_does_not_turn_the_watcher_off():
    """The hook fails open: if an invalid value broke the import, the whole
    brake (handoff, loop, sub-session cut) would vanish silently."""
    for bad in ({"SQUAD_REREAD": "sixty"}, {"SQUAD_REREAD": "100"}, {"SQUAD_REREAD": "0"},
                {"SQUAD_REREAD_FLOOR": "x"}, {"SQUAD_CTX_PCT": "abc"}, {"SQUAD_CTX_PCT": "0"},
                {"SQUAD_CTX_BASE": "40k"}):
        h = home()
        sid = "99999999-0001"
        got = kind(said(run(h, "PostToolUse", sid, session(h, sid, 920_000, 0.9), env=bad)))
        assert got == "PROACTIVE HANDOFF", (bad, got)
        sid = "99999999-0002"                     # and no value makes the cut early
        assert said(run(h, "PostToolUse", sid, session(h, sid, 60_000, 0.9), env=bad)) == "nothing", bad
        assert not os.path.exists(os.path.join(h, ".claude", "squad", "watch-errors.log")), bad


def test_handoff_level_with_no_handoff_written_repeats_the_order():
    h = home()
    sid = "eeeeeeee-0001"
    tr = session(h, sid, 750_000, 0.9)
    assert kind(said(run(h, "PostToolUse", sid, tr))) == "PROACTIVE HANDOFF"
    assert kind(said(run(h, "Stop", sid, tr))) == "stopped without writing the handoff"


def fake_herdr(h, sid, clear_switches=True):
    """Fake herdr: `agent get` returns the pane's session, `/clear` switches the session
    (or not, with clear_switches=False: the dialog that holds the pane); keys are logged.
    pane-session-status sets the agent's status (idle by default); pane-session-fail
    holds N empty answers (herdr not answering)."""
    cur, sent = os.path.join(h, "pane-session"), os.path.join(h, "pane-prompts")
    with open(cur, "w", encoding="utf-8") as fh:
        fh.write(sid)
    path = os.path.join(h, "herdr")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"""#!/usr/bin/env python3
import json, sys
a = sys.argv[1:]
if a[:2] == ["agent", "get"]:
    try:
        n = int(open({cur!r} + "-fail").read())
        open({cur!r} + "-fail", "w").write(str(n - 1))
        if n > 0:
            sys.exit(1)
    except OSError:
        pass
    try:
        status = open({cur!r} + "-status").read().strip()
    except OSError:
        status = "idle"
    print(json.dumps({{"result": {{"agent": {{"agent_session": {{"value": open({cur!r}).read()}},
                                           "agent_status": status}}}}}}))
elif a[:2] == ["agent", "prompt"]:
    open({sent!r}, "a").write(a[3] + "\\n")
    if a[3] == "/clear" and {clear_switches!r}:
        open({cur!r}, "w").write("ffffffff-9999")
elif a[:2] == ["agent", "send-keys"]:
    open({sent!r}, "a").write("KEYS " + " ".join(a[3:]) + "\\n")
""")
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
    if os.name == "nt":                  # a shebang does not run on Windows: a .cmd that calls the script does
        with open(path + ".cmd", "w", encoding="utf-8") as fh:
            fh.write(f'@"{sys.executable}" "{path}" %*\r\n')
        path += ".cmd"
    return path, sent


def test_ordered_handoff_switches_session_and_calls_the_pickup():
    h = home()
    sid = "abcdef12-0001"
    herdr, sent = fake_herdr(h, sid)
    env = {"HERDR_PANE_ID": "w1:p1", "HERDR_BIN_PATH": herdr}
    tr = session(h, sid, 750_000, 0.9)
    text = said(run(h, "PostToolUse", sid, tr, env=env))
    assert "PROACTIVE HANDOFF" in text and "calls the pickup on its own" in text, text
    handoff = write_handoff(h, tr)
    assert said(run(h, "Stop", sid, tr, env=env)) == "nothing"
    log = os.path.join(h, ".claude", "squad", "switches.log")
    for _ in range(40):                                  # the switch runs detached, outside the session
        if os.path.exists(log):
            break
        time.sleep(0.5)
    with open(log, encoding="utf-8") as fh:
        line = fh.read()
    assert "abcdef12 -> ffffffff pickup sent" in line, line
    with open(sent, encoding="utf-8") as fh:
        prompts = fh.read().splitlines()
    assert prompts[0] == "/clear" and prompts[1].startswith("/clear-pickup pickup "), prompts
    with open(prompts[1].split()[-1], encoding="utf-8") as fh, open(handoff, encoding="utf-8") as orig:
        assert fh.read() == orig.read()                  # the new session reads this session's handoff
    assert state(h, sid)["switched"] is True
    assert abs(state(h, sid)["switched_ts"] - time.time()) < 120   # squad-wake measures a stuck switch from here
    with open(os.path.join(h, ".claude", "squad", "switches", "ffffffff-9999.pickup"), encoding="utf-8") as fh:
        assert fh.read() == prompts[1].split()[-1]       # squad-wake resends it if it does not land


def test_dispatch_denied_at_the_ceiling_arms_the_switch():
    h = home()
    sid = "12345678-0001"
    tr = session(h, sid, 820_000, 0.0)                   # 82%, low reread: only a notice...
    assert kind(said(run(h, "PostToolUse", sid, tr))) == "Informational only"
    out = run(h, "PreToolUse", sid, tr, {"tool_name": "Agent",
                                         "tool_input": {"subagent_type": "developer", "prompt": "x"}})
    assert "DISPATCH DENIED" in out, out                 # ...but dispatching from here means handoff
    st = state(h, sid)
    assert st["ctx_level"] == 2 and st["ordered"] is True
    path = os.path.join(h, ".claude", "squad", "watch", f"{sid}.main.json")
    st["checked"] = 0                                    # forces a new measurement of the transcript
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(st, fh)
    run(h, "PostToolUse", sid, tr)
    assert state(h, sid)["ctx_level"] == 2               # the next measurement does not disarm
    # below the ceiling the dispatch passes
    sid2 = "12345678-0002"
    tr2 = session(h, sid2, 450_000, 0.9)
    run(h, "PostToolUse", sid2, tr2)
    assert "DISPATCH DENIED" not in run(h, "PreToolUse", sid2, tr2, {
        "tool_name": "Agent", "tool_input": {"subagent_type": "developer", "prompt": "x"}})


def orchestrators(h, n):
    """n other sessions with a live sub-session right now."""
    folder = os.path.join(h, ".claude", "squad", "watch")
    os.makedirs(folder, exist_ok=True)
    for name in os.listdir(folder):
        if name.startswith("other"):
            os.remove(os.path.join(folder, name))
    for i in range(n):
        with open(os.path.join(folder, f"other{i}.sub{i}.json"), "w", encoding="utf-8") as fh:
            fh.write("{}")


def dispatch(h, sid, tr, agent_type="developer", env=None):
    return said(run(h, "PreToolUse", sid, tr, {
        "tool_name": "Agent", "tool_input": {"subagent_type": agent_type, "prompt": "card ABC-1"}}, env))


def test_no_slot_heavy_session_becomes_handoff_with_plan_and_switch():
    h = home()
    sid = "12345678-0003"
    tr = session(h, sid, 550_000, 0.0)                   # 55%: only the informational notice (lowest floor 50%)
    assert kind(said(run(h, "PostToolUse", sid, tr))) == "Informational only"
    assert state(h, sid)["ctx_level"] == 1 and not state(h, sid).get("ordered")   # nothing ordered yet
    orchestrators(h, 6)
    out = dispatch(h, sid, tr)
    assert "DISPATCH DENIED" in out and "PLAN of the denied dispatch" in out, out
    assert "without dispatching an agent" in out and "/clear-pickup pickup <file>" in out, out
    st = state(h, sid)
    assert st["ctx_level"] == 2 and st["ordered"] is True
    briefs = os.path.join(h, ".claude", "squad", "handoffs", f"{sid}-dispatches.md")
    assert briefs in out, out                            # the denied plan is kept in full
    with open(briefs, encoding="utf-8") as fh:
        assert fh.read().count("card ABC-1") == 1
    assert "pickup on its own" in dispatch(h, sid, tr, env={"HERDR_PANE_ID": "w1:p1"})
    with open(briefs, encoding="utf-8") as fh:
        assert fh.read().count("card ABC-1") == 1        # the same brief does not go in twice
    assert "already been ordered" in dispatch(h, sid, tr)   # insisting does not change the answer
    # stopping without the handoff sends the order back; with the handoff written, no more complaints
    assert kind(said(run(h, "Stop", sid, tr))) == "stopped without writing the handoff"
    write_handoff(h, tr)
    assert said(run(h, "Stop", sid, tr)) == "nothing"


def test_no_slot_light_session_implements_here():
    h = home()
    sid = "12345678-0004"
    tr = session(h, sid, 60_000, 0.0)                    # the session that has just done the pickup
    run(h, "PostToolUse", sid, tr)
    orchestrators(h, 6)
    out = dispatch(h, sid, tr)
    assert "RIGHT HERE" in out and "no handoff" in out, out
    st = state(h, sid)
    assert not st.get("ordered") and st.get("ctx_level", 0) == 0 and st["wait_slot"] is True
    assert said(run(h, "PostToolUse", sid, tr)) == "nothing"   # still no slot: silence
    orchestrators(h, 5)
    assert "SLOT FREE" in said(run(h, "PostToolUse", sid, tr))
    assert said(run(h, "PostToolUse", sid, tr)) == "nothing"   # announces once
    assert "DISPATCH DENIED" not in dispatch(h, sid, tr)


def git_repo(h, name):
    d = os.path.join(h, name)
    os.makedirs(d, exist_ok=True)
    subprocess.run(["git", "init", "-q", d], check=True)
    return d


def orchestrators_in(h, n, repo):
    """n other main sessions with a live sub-session, recorded in `repo`."""
    folder = os.path.join(h, ".claude", "squad", "watch")
    os.makedirs(folder, exist_ok=True)
    for i in range(n):
        with open(os.path.join(folder, f"other{i}.sub{i}.json"), "w", encoding="utf-8") as fh:
            fh.write("{}")
        with open(os.path.join(folder, f"other{i}.main.json"), "w", encoding="utf-8") as fh:
            json.dump({"repo": os.path.realpath(os.path.join(repo, ".git"))}, fh)


def test_orchestrator_slots_are_per_repository():
    h = home()
    sid = "12345678-0014"
    tr = session(h, sid, 60_000, 0.0)
    mine, other = git_repo(h, "mine"), git_repo(h, "other")
    orchestrators_in(h, 6, other)          # 6 orchestrating in ANOTHER repository: the slot here is free

    def dispatch_in(cwd):
        return said(run(h, "PreToolUse", sid, tr, {"cwd": cwd, "tool_name": "Agent", "tool_input": {
            "subagent_type": "developer", "prompt": "card ABC-1"}}))
    assert "DISPATCH DENIED" not in dispatch_in(mine)
    assert state(h, sid)["repo"] == os.path.realpath(os.path.join(mine, ".git"))
    out = dispatch_in(other)               # in the other repository the 6 slots are taken
    assert "DISPATCH DENIED" in out and "in this repository" in out, out


def test_worktrees_of_the_same_repository_share_the_slots():
    h = home()
    sid = "12345678-0015"
    tr = session(h, sid, 60_000, 0.0)
    root = git_repo(h, "root")
    subprocess.run(["git", "-C", root, "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false",
                    "commit", "-q", "--allow-empty", "-m", "x"], check=True)
    wt = os.path.join(h, "wt")
    subprocess.run(["git", "-C", root, "worktree", "add", "-q", wt], check=True)
    orchestrators_in(h, 6, root)           # 6 orchestrating in the root: its worktree has no slot
    out = said(run(h, "PreToolUse", sid, tr, {"cwd": wt, "tool_name": "Agent", "tool_input": {
        "subagent_type": "developer", "prompt": "card ABC-1"}}))
    assert "DISPATCH DENIED" in out and "in this repository" in out, out
    # denied because the worktree IS that repository, not because its repository is unknown
    assert state(h, sid)["repo"] == os.path.realpath(os.path.join(root, ".git")), state(h, sid)


def test_rearm_clears_the_switch_time():
    h = home()
    sid = "12345678-0016"
    tr = session(h, sid, 60_000, 0.0)
    folder = os.path.join(h, ".claude", "squad", "watch")
    for initial in ({"switched": True, "switched_ts": 1.0}, {"switched": False, "switched_ts": 1.0},
                    {"switched_ts": 1.0}, {}):
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, f"{sid}.main.json"), "w", encoding="utf-8") as fh:
            json.dump(dict(initial, ctx_level=2), fh)
        run(h, "Stop", sid, tr, {"squad_rearm": True})
        st = state(h, sid)
        assert "switched" not in st and "switched_ts" not in st, (initial, st)


def test_no_slot_reviewer_waits_for_the_slot_without_handoff():
    h = home()
    sid = "12345678-0005"
    tr = session(h, sid, 300_000, 0.0)
    run(h, "PostToolUse", sid, tr)
    orchestrators(h, 6)
    out = dispatch(h, sid, tr, "verifier")
    assert "whoever writes does not approve" in out and "SLOT FREE" in out, out
    st = state(h, sid)
    assert not st.get("ordered") and st["wait_slot"] is True


def test_the_reviewer_cap_takes_the_fix_to_the_owner_never_shipping_the_defect():
    h = home()
    sid = "12345678-0035"
    tr = session(h, sid, 100_000, 0.0)
    run(h, "PostToolUse", sid, tr)
    env = {"SQUAD_MAX_REVIEWS": "2"}
    call = {"tool_name": "Agent", "tool_input": {"subagent_type": "verifier", "prompt": "card ABC-1"}}
    for _ in range(2):                                   # two reviewer rounds go through and are counted
        assert "DISPATCH DENIED" not in dispatch(h, sid, tr, "verifier", env)
        run(h, "PostToolUse", sid, tr, call, env)
    out = dispatch(h, sid, tr, "verifier", env)          # the third is the cap
    assert "DISPATCH DENIED" in out and "already been dispatched" in out, out
    assert "split the card" in out and "Never propose merging with the defect" in out, out
    assert "accept the risk" not in out, out

def meminfo(h, total_kb, avail_kb, name="meminfo"):
    """A fake /proc/meminfo for SQUAD_MEMINFO."""
    path = os.path.join(h, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"MemTotal:       {total_kb} kB\nMemFree:         1 kB\nMemAvailable:   {avail_kb} kB\n"
                 "Buffers:           10 kB\n")
    return path


def mem_session(h, sid):
    tr = session(h, sid, 100_000, 0.0)
    run(h, "PostToolUse", sid, tr)
    return tr


def test_memory_below_the_ceiling_lets_the_dispatch_through():
    h = home()
    tr = mem_session(h, "12345678-0071")
    env = {"SQUAD_MEM_CEILING": "90", "SQUAD_MEMINFO": meminfo(h, 1000, 200)}    # 80% in use
    assert "DISPATCH DENIED" not in dispatch(h, "12345678-0071", tr, env=env)


def test_memory_at_or_above_the_ceiling_denies_the_dispatch_and_names_the_reading():
    h = home()
    tr = mem_session(h, "12345678-0072")
    at = {"SQUAD_MEM_CEILING": "90", "SQUAD_MEMINFO": meminfo(h, 1000, 100)}      # exactly 90%
    out = dispatch(h, "12345678-0072", tr, env=at)
    assert "DISPATCH DENIED" in out and "memory" in out and "90%" in out, out
    above = {"SQUAD_MEM_CEILING": "90", "SQUAD_MEMINFO": meminfo(h, 1000, 50)}    # 95%
    out = dispatch(h, "12345678-0072", tr, env=above)
    assert "DISPATCH DENIED" in out and "95%" in out and "ceiling 90%" in out, out
    task = said(run(h, "PreToolUse", "12345678-0072", tr, {
        "tool_name": "Task", "tool_input": {"subagent_type": "developer", "prompt": "card ABC-1"}}, above))
    assert "DISPATCH DENIED" in task and "memory" in task, task
    # a memory denial is no handoff: no switch armed, nothing ordered, no handoff text
    st = state(h, "12345678-0072")
    assert st.get("ctx_level", 0) != 2 and not st.get("ordered"), st
    assert "Soft stop" not in out and "Soft stop" not in task, (out, task)


def test_memory_ceiling_zero_is_off_even_on_a_full_machine():
    h = home()
    tr = mem_session(h, "12345678-0073")
    full = meminfo(h, 1000, 0)
    assert "DISPATCH DENIED" not in dispatch(h, "12345678-0073", tr, env={"SQUAD_MEMINFO": full})
    assert "DISPATCH DENIED" not in dispatch(h, "12345678-0073", tr, env={
        "SQUAD_MEM_CEILING": "0", "SQUAD_MEMINFO": full})


def test_unreadable_meminfo_never_denies():
    h = home()
    tr = mem_session(h, "12345678-0074")
    bad = os.path.join(h, "garbled")
    with open(bad, "w", encoding="utf-8") as fh:
        fh.write("MemTotal: lots kB\nMemAvailable: none\n")
    empty = os.path.join(h, "empty")
    open(empty, "w", encoding="utf-8").close()
    old_kernel = os.path.join(h, "no-available")       # MemAvailable arrived in Linux 3.14
    with open(old_kernel, "w", encoding="utf-8") as fh:
        fh.write("MemTotal:       1000 kB\nMemFree:          10 kB\n")
    inconsistent = meminfo(h, 1000, 5000, name="inconsistent")
    for path in (os.path.join(h, "missing"), bad, empty, old_kernel, inconsistent):
        env = {"SQUAD_MEM_CEILING": "1", "SQUAD_MEMINFO": path}
        assert "DISPATCH DENIED" not in dispatch(h, "12345678-0074", tr, env=env), path


def test_memory_reading_is_none_without_available_or_with_more_available_than_total():
    h = home()
    m = watch_module()
    old_kernel = os.path.join(h, "no-available")
    with open(old_kernel, "w", encoding="utf-8") as fh:
        fh.write("MemTotal:       1000 kB\nMemFree:          10 kB\n")
    assert m.memory_used(old_kernel) is None
    assert m.memory_used(meminfo(h, 1000, 5000, name="inconsistent")) is None
    assert m.memory_used(meminfo(h, 1000, 250, name="quarter-free")) == 75.0

def test_memory_ceiling_leaves_other_tools_alone():
    h = home()
    tr = mem_session(h, "12345678-0075")
    env = {"SQUAD_MEM_CEILING": "50", "SQUAD_MEMINFO": meminfo(h, 1000, 10)}
    for tool, args in (("Bash", {"command": "ls"}), ("Read", {"file_path": "/etc/hostname"})):
        out = said(run(h, "PreToolUse", "12345678-0075", tr, {"tool_name": tool, "tool_input": args}, env))
        assert "DISPATCH DENIED" not in out, (tool, out)


def test_notice_ahead_of_the_brake_only_for_whoever_orchestrates():
    h = home()
    sid = "12345678-0006"
    tr = session(h, sid, 550_000, 0.0)
    orchestrators(h, 6)
    # 550k is past the lowest floor of 50% (informational notice, level 1); what matters
    # here is that a session that never dispatched does not get the DISPATCH NOTICE
    first = said(run(h, "PostToolUse", sid, tr))
    assert kind(first) == "Informational only" and "DISPATCH NOTICE" not in first, first
    path = os.path.join(h, ".claude", "squad", "watch", f"{sid}.main.json")
    def remeasure():
        st = state(h, sid)
        st.update(checked=0, subs=2, calls=[])
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(st, fh)
        return said(run(h, "PostToolUse", sid, tr))
    out = remeasure()
    assert "DISPATCH NOTICE" in out and "finish the task in progress" in out, out
    assert not state(h, sid).get("ordered")                      # a notice arms nothing
    again = remeasure()
    assert again == "nothing", again                             # one notice per episode
    orchestrators(h, 0)
    remeasure()
    orchestrators(h, 6)
    assert "DISPATCH NOTICE" in remeasure()                      # the slot came back and went away again


def test_sub_agent_return_is_not_the_owner_asking_to_stop():
    """One session in October 2026: a sub-agent's notification mentioned
    "handoff", the watcher read it as the owner's request and let the session
    die with a handoff out of turn."""
    h = home()
    sid = "12345678-0007"
    tr = session(h, sid, 200_000, 0.9)
    run(h, "UserPromptSubmit", sid, tr, {"prompt": "redo the When block"})
    run(h, "UserPromptSubmit", sid, tr, {
        "prompt": "<task-notification>\n<result>Stopped on the squad-watch HANDOFF order</result>"})
    assert state(h, sid)["asked_stop"] is False
    run(h, "PostToolUse", sid, tr)
    write_handoff(h, tr)
    assert kind(said(run(h, "Stop", sid, tr))) == "Handoff out of turn"
    for resume in ("/clear-pickup pickup /x/handoffs/h.md", "retome o handoff de ontem",
                   "resume from yesterday's handoff", "pick up the handoff where we left off"):
        run(h, "UserPromptSubmit", sid, tr, {"prompt": resume})
        assert state(h, sid)["asked_stop"] is False, resume      # resuming is not stopping


def test_session_that_does_not_write_the_handoff_the_watcher_takes_over_and_switches():
    h = home()
    sid = "abcdef12-0002"
    herdr, sent = fake_herdr(h, sid)
    env = {"HERDR_PANE_ID": "w1:p1", "HERDR_BIN_PATH": herdr}
    tr = session(h, sid, 550_000, 0.0)
    run(h, "UserPromptSubmit", sid, tr, {"prompt": "deliver card ABC-1"}, env)
    run(h, "PostToolUse", sid, tr, env=env)
    orchestrators(h, 6)
    assert "DISPATCH DENIED" in dispatch(h, sid, tr, env=env)
    assert kind(said(run(h, "Stop", sid, tr, env=env))) == "stopped without writing the handoff"
    assert said(run(h, "Stop", sid, tr, {"stop_hook_active": True}, env)) == "nothing"
    log = os.path.join(h, ".claude", "squad", "switches.log")
    for _ in range(40):
        if os.path.exists(log):
            break
        time.sleep(0.5)
    with open(log, encoding="utf-8") as fh:
        assert "abcdef12 -> ffffffff pickup sent" in fh.read()
    with open(sent, encoding="utf-8") as fh:
        prompts = fh.read().splitlines()
    with open(prompts[1].split()[-1], encoding="utf-8") as fh:
        body = fh.read()
    assert "written by squad-watch" in body and "deliver card ABC-1" in body, body
    assert "stopped without writing its own" in body and "compacted" not in body, body
    assert f"{sid}-dispatches.md" in body and "UNKNOWN" in body, body
    assert "\n## Next step\n" in body, body


SWITCH = os.path.join(ROOT, "hooks", "squad-switch.py")
SWITCH_LOG = (".claude", "squad", "switches.log")


def wait_for(path, text, secs=40):
    for _ in range(int(secs / 0.25)):
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                body = fh.read()
            if text in body:
                return body
        time.sleep(0.25)
    with open(path, encoding="utf-8") if os.path.exists(path) else open(os.devnull, encoding="utf-8") as fh:
        raise AssertionError(f"{text!r} did not show up in {path}: {fh.read()!r}")


def lines(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return fh.read().splitlines()


def notify(transcript, task="b1"):
    """A background task's result reaching the session."""
    with open(transcript, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "user", "message": {"role": "user", "content":
                             f"<task-notification>\n<task-id>{task}</task-id>\n<status>completed</status>"}}) + "\n")


def pane_status(h, status):
    with open(os.path.join(h, "pane-session-status"), "w", encoding="utf-8") as fh:
        fh.write(status)


BG_SHELL = [{"id": "bvtlvn8km", "type": "local_bash", "status": "running",
             "description": "Wait for quiet", "command": "measure-quiet.sh"}]
BG_AGENT = [{"id": "a1b2", "type": "local_agent", "status": "running",
             "description": "DR-15 part 2", "agent_type": "developer"}]


def test_background_task_does_not_block_the_switch():
    """October 2026: handoff written, a shell waiting for the machine to settle
    stayed alive and the Stop left without a switch. Nobody rearmed it; the owner
    cleared the pane by hand. Now the switch is scheduled and goes out at the deadline."""
    h = home()
    sid = "abcdef12-0003"
    herdr, sent = fake_herdr(h, sid)
    env = {"HERDR_PANE_ID": "w1:p1", "HERDR_BIN_PATH": herdr, "SQUAD_SWITCH_WAIT": "1"}
    tr = session(h, sid, 750_000, 0.9)
    assert kind(said(run(h, "PostToolUse", sid, tr, env=env))) == "PROACTIVE HANDOFF"
    handoff = write_handoff(h, tr)
    assert said(run(h, "Stop", sid, tr, {"background_tasks": BG_SHELL}, env)) == "nothing"
    st = state(h, sid)
    assert not st.get("switched") and st["switch_wait"] > time.time() and st["bg"][0]["id"] == "bvtlvn8km"
    body = wait_for(os.path.join(h, *SWITCH_LOG), "pickup sent")
    assert "deadline of 0 min passed" in body and "with a note on background tasks" in body, body
    prompts = lines(sent)
    assert prompts[0] == "/clear" and prompts[1].startswith("/clear-pickup pickup "), prompts
    with open(prompts[1].split()[-1], encoding="utf-8") as fh, open(handoff, encoding="utf-8") as orig:
        copy, original = fh.read(), orig.read()
    assert copy.startswith(original) and "Watcher's note at the switch" in copy and "Wait for quiet" in copy, copy
    assert state(h, sid)["switched"] is True


def test_task_that_returns_asks_for_the_handoff_rewritten_and_switches_once():
    h = home()
    sid = "abcdef12-0004"
    herdr, sent = fake_herdr(h, sid)
    env = {"HERDR_PANE_ID": "w1:p1", "HERDR_BIN_PATH": herdr, "SQUAD_SWITCH_WAIT_AGENT": "8"}
    tr = session(h, sid, 750_000, 0.9)
    run(h, "PostToolUse", sid, tr, env=env)
    write_handoff(h, tr)
    assert said(run(h, "Stop", sid, tr, {"background_tasks": BG_AGENT}, env)) == "nothing"
    # the same Stop again (session woken and stopped, agent still running): one schedule only
    assert said(run(h, "Stop", sid, tr, {"background_tasks": BG_AGENT}, env)) == "nothing"
    notify(tr, "a1b2")                                     # the sub-agent came back after the handoff
    out = said(run(h, "Stop", sid, tr, {"background_tasks": []}, env))
    assert "A background task came back" in out and "Rewrite the handoff" in out, out
    write_handoff(h, tr, "h1.md")                          # rewritten: the switch goes out now
    assert said(run(h, "Stop", sid, tr, {"background_tasks": []}, env)) == "nothing"
    wait_for(os.path.join(h, *SWITCH_LOG), "pickup sent")
    time.sleep(10)                                         # the schedule expires and sees the switch done
    assert lines(sent).count("/clear") == 1, lines(sent)
    with open(lines(sent)[1].split()[-1], encoding="utf-8") as fh:
        assert "Watcher's note" not in fh.read()


def test_clear_that_does_not_switch_the_session_gets_escape():
    h = home()
    sid = "abcdef12-0005"
    herdr, sent = fake_herdr(h, sid, clear_switches=False)
    env = {"HERDR_PANE_ID": "w1:p1", "HERDR_BIN_PATH": herdr}
    tr = session(h, sid, 750_000, 0.9)
    run(h, "PostToolUse", sid, tr, env=env)
    write_handoff(h, tr)
    assert said(run(h, "Stop", sid, tr, env=env)) == "nothing"
    assert "Escape sent" in wait_for(os.path.join(h, *SWITCH_LOG), "did not change after the /clear")
    assert "KEYS esc" in lines(sent), lines(sent)


def test_session_at_18_percent_with_no_slot_works_here_without_handoff():
    """October 2026: 183k of context and 3 orchestrators were enough for the
    watcher to order a stop. Now, below SQUAD_ORCH_HERE (500k), the card is done here."""
    h = home()
    sid = "12345678-0008"
    tr = session(h, sid, 183_000, 0.9)
    run(h, "PostToolUse", sid, tr)
    orchestrators(h, 6)
    out = dispatch(h, sid, tr)
    assert "RIGHT HERE" in out and "no handoff" in out, out
    assert not state(h, sid).get("ordered")


def test_owner_request_after_the_handoff_outweighs_the_handoff():
    """The owner wrote after the handoff; switching with the old handoff would
    lose the new request. The session is told to write another one."""
    h = home()
    sid = "abcdef12-0006"
    herdr, sent = fake_herdr(h, sid)
    env = {"HERDR_PANE_ID": "w1:p1", "HERDR_BIN_PATH": herdr}
    tr = session(h, sid, 750_000, 0.9)
    run(h, "PostToolUse", sid, tr, env=env)
    write_handoff(h, tr)
    time.sleep(0.05)
    run(h, "UserPromptSubmit", sid, tr, {"prompt": "before stopping, open the PR for card ABC-2"}, env)
    assert kind(said(run(h, "Stop", sid, tr, env=env))) == "stopped without writing the handoff"
    assert not state(h, sid).get("switched") and lines(sent) == []


def test_switch_gives_up_and_rearms_if_the_session_woke_up():
    h = home()
    sid = "abcdef12-0007"
    herdr, sent = fake_herdr(h, sid)
    env = {"HERDR_PANE_ID": "w1:p1", "HERDR_BIN_PATH": herdr}
    tr = session(h, sid, 750_000, 0.9)
    run(h, "PostToolUse", sid, tr, env=env)
    write_handoff(h, tr)
    pane_status(h, "working")                              # woke up between the Stop and the switch
    assert said(run(h, "Stop", sid, tr, env=env)) == "nothing"
    log = os.path.join(h, *SWITCH_LOG)
    wait_for(log, "switch rearmed")
    for _ in range(40):
        if not state(h, sid).get("switched"):
            break
        time.sleep(0.25)
    assert not state(h, sid).get("switched") and "/clear" not in lines(sent)
    pane_status(h, "idle")                                 # stopped again: now it switches
    assert said(run(h, "Stop", sid, tr, env=env)) == "nothing"
    wait_for(log, "pickup sent")
    assert lines(sent).count("/clear") == 1


def test_wait_survives_a_transient_herdr_failure():
    h = home()
    sid = "abcdef12-0008"
    herdr, sent = fake_herdr(h, sid)
    with open(os.path.join(h, "pane-session-fail"), "w", encoding="utf-8") as fh:
        fh.write("3")
    env = {"HERDR_PANE_ID": "w1:p1", "HERDR_BIN_PATH": herdr, "SQUAD_SWITCH_WAIT": "2"}
    tr = session(h, sid, 750_000, 0.9)
    run(h, "PostToolUse", sid, tr, env=env)
    write_handoff(h, tr)
    assert said(run(h, "Stop", sid, tr, {"background_tasks": BG_SHELL}, env)) == "nothing"
    body = wait_for(os.path.join(h, *SWITCH_LOG), "pickup sent")
    assert "wait undone" not in body, body


def test_wait_exits_when_herdr_never_answers_again():
    """A closed pane or herdr down must not keep the waiting process alive forever."""
    h = home()
    e = dict(os.environ, HOME=h, USERPROFILE=h, HERDR_BIN_PATH="/bin/false", SQUAD_SWITCH_BUSY="2")
    t0 = time.time()
    r = subprocess.run([sys.executable, SWITCH, "wait", "w1:p1", "abcdef12-0009", "/x/t.jsonl", "/x", "1"],
                       env=e, capture_output=True, text=True, timeout=60)
    assert r.returncode == 1 and time.time() - t0 < 60
    with open(os.path.join(h, *SWITCH_LOG), encoding="utf-8") as fh:
        assert "herdr silent" in fh.read()


def test_the_claim_refuses_a_second_switch_of_the_same_session():
    """One switch per session: the second process that tries to clear the same
    session finds the claim and gives up before the /clear."""
    import hashlib
    h = home()
    sid = "abcdef12-0010"
    herdr, sent = fake_herdr(h, sid)
    handoff = write_handoff(h, session(h, sid, 750_000, 0.9))
    with open(handoff, "rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()
    e = {k: v for k, v in os.environ.items() if not k.startswith(("SQUAD_", "HERDR_"))}
    e.update(HOME=h, USERPROFILE=h, HERDR_BIN_PATH=herdr)
    def switch():
        return subprocess.run([sys.executable, SWITCH, "w1:p1", sid, handoff, digest], env=e,
                              capture_output=True, text=True, timeout=90).returncode
    assert switch() == 0
    assert os.path.exists(os.path.join(h, ".claude", "squad", "switches", f"{sid}.claim"))
    with open(os.path.join(h, "pane-session"), "w", encoding="utf-8") as fh:
        fh.write(sid)                                      # the same session in the pane again
    assert switch() == 1
    log = lines(os.path.join(h, *SWITCH_LOG))
    assert any("another switch of abcdef12 is already under way" in ln for ln in log), log
    assert lines(sent).count("/clear") == 1, lines(sent)


def test_max_orch_and_orch_here_defaults_and_their_overrides():
    h = home()
    light, heavy = "12345678-0009", "12345678-0010"
    tr_light = session(h, light, 490_000, 0.0)             # just below ORCH_HERE (500k)
    tr_heavy = session(h, heavy, 510_000, 0.0)             # just above it
    run(h, "PostToolUse", light, tr_light)
    run(h, "PostToolUse", heavy, tr_heavy)
    orchestrators(h, 5)                                    # 5 others: the 6th slot is free by default
    assert "DISPATCH DENIED" not in dispatch(h, light, tr_light)
    assert "RIGHT HERE" in dispatch(h, light, tr_light, env={"SQUAD_MAX_ORCH": "5"})
    orchestrators(h, 6)
    assert "RIGHT HERE" in dispatch(h, light, tr_light)
    out = dispatch(h, heavy, tr_heavy, env={"SQUAD_ORCH_HERE": "600000"})
    assert "RIGHT HERE" in out and not state(h, heavy).get("ordered"), out
    assert "DISPATCH DENIED" not in dispatch(h, heavy, tr_heavy, env={"SQUAD_MAX_ORCH": "7"})
    out = dispatch(h, heavy, tr_heavy)
    assert "PLAN of the denied dispatch" in out and state(h, heavy)["ordered"] is True, out


def compact_handoff(h, sid):
    return os.path.join(h, ".claude", "squad", "handoffs", f"{sid}-compact.md")


def dirty_repo(h):
    repo = os.path.join(h, "repo")
    subprocess.run(["git", "init", "-q", "-b", "feat/compact", repo], check=True)
    for key, value in (("user.name", "t"), ("user.email", "t@t"), ("commit.gpgsign", "false")):
        git(repo, "config", key, value)
    with open(os.path.join(repo, "a.txt"), "w", encoding="utf-8") as fh:
        fh.write("base\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "first commit of the card")
    with open(os.path.join(repo, "half-done.py"), "w", encoding="utf-8") as fh:
        fh.write("x = 1\n")
    return repo


def test_compaction_writes_the_handoff_before_the_conversation_is_summarized():
    """A /compact, or an auto-compact, used to run with nothing on disk: the
    decisions were summarized away and git, the owner's request and the denied
    briefs were not written anywhere."""
    for trigger, instructions in (("manual", "keep the merge plan"), ("auto", None)):
        h = home()
        sid = f"c0c0c0c0-{trigger}"
        repo = dirty_repo(h)
        tr = session(h, sid, 300_000, 0.9)
        run(h, "UserPromptSubmit", sid, tr, {"prompt": "deliver card ABC-2", "cwd": repo})
        briefs = os.path.join(h, ".claude", "squad", "handoffs", f"{sid}-dispatches.md")
        os.makedirs(os.path.dirname(briefs), exist_ok=True)
        with open(briefs, "w", encoding="utf-8") as fh:
            fh.write("## brief\n")
        r = run_proc(h, "PreCompact", sid, tr, {"trigger": trigger, "custom_instructions": instructions,
                                                "cwd": repo})
        assert r.returncode == 0 and r.stdout.strip() == "", r      # never blocks the compaction
        with open(compact_handoff(h, sid), encoding="utf-8") as fh:
            body = fh.read()
        assert "written by squad-watch" in body and f"compacted ({trigger})" in body, body
        assert "stopped without writing" not in body, body           # the header says why it was written
        assert "Branch: feat/compact" in body and "?? half-done.py" in body, body
        assert "first commit of the card" in body and "deliver card ABC-2" in body, body
        assert briefs in body and "\n## Next step\n" in body, body
        if instructions:                                             # what the owner typed after /compact
            assert f"Compact instructions:** {instructions}" in body, body
        else:
            assert "Compact instructions" not in body, body
        assert not os.path.exists(os.path.join(h, ".claude", "squad", "handoffs", f"{sid}-watch.md"))


def test_compaction_of_a_sub_session_writes_nothing():
    h = home()
    sid = "c0c0c0c0-0002"
    r = run_proc(h, "PreCompact", sid, session(h, sid, 300_000, 0.9),
                 {"trigger": "auto", "custom_instructions": None, "agent_id": "agent-1"})
    assert r.returncode == 0 and r.stdout.strip() == "", r
    assert not os.path.exists(os.path.join(h, ".claude", "squad", "handoffs")), os.listdir(h)


def test_compaction_at_handoff_level_never_switches_the_session():
    h = home()
    sid = "c0c0c0c0-0003"
    herdr, sent = fake_herdr(h, sid)
    env = {"HERDR_PANE_ID": "w1:p1", "HERDR_BIN_PATH": herdr}
    tr = session(h, sid, 750_000, 0.9)
    assert kind(said(run(h, "PostToolUse", sid, tr, env=env))) == "PROACTIVE HANDOFF"
    for trigger in ("manual", "auto"):
        r = run_proc(h, "PreCompact", sid, tr, {"trigger": trigger, "custom_instructions": None}, env)
        assert r.returncode == 0 and r.stdout.strip() == "", r
    assert os.path.exists(compact_handoff(h, sid))
    log = os.path.join(h, ".claude", "squad", "switches.log")
    for _ in range(16):                    # a switch runs detached and waits 3 s before its first step
        assert not os.path.exists(log) and not os.path.exists(sent), "PreCompact launched the switch"
        time.sleep(0.5)
    assert not state(h, sid).get("switched")


def test_compaction_handler_error_fails_open_and_is_logged():
    h = home()
    sid = "c0c0c0c0-0004"
    os.makedirs(os.path.join(h, ".claude", "squad", "watch"))
    with open(os.path.join(h, ".claude", "squad", "watch", f"{sid}.main.json"), "w", encoding="utf-8") as fh:
        json.dump({"ctx": "not a number"}, fh)            # a corrupt state makes the handoff raise
    r = run_proc(h, "PreCompact", sid, os.path.join(h, "t.jsonl"), {"trigger": "auto", "custom_instructions": None})
    assert r.returncode == 0 and r.stdout.strip() == "" and r.stderr.strip() == "", r
    with open(os.path.join(h, ".claude", "squad", "watch-errors.log"), encoding="utf-8") as fh:
        assert "TypeError" in fh.read()


def test_compaction_handoff_off_with_squad_watch_0():
    h = home()
    sid = "c0c0c0c0-0005"
    r = run_proc(h, "PreCompact", sid, session(h, sid, 300_000, 0.9),
                 {"trigger": "manual", "custom_instructions": None}, {"SQUAD_WATCH": "0"})
    assert r.returncode == 0 and r.stdout.strip() == "", r
    assert not os.path.exists(compact_handoff(h, sid))


def test_stop_schedules_the_compaction_and_precompact_labels_the_one_it_ordered():
    """Outside handoff, with a large context, the Stop schedules squad-compact for just
    before the cache goes cold; the PreCompact handoff of that compaction says the
    watcher ordered it, never that the session stopped without a handoff."""
    h = home()
    sid = "c0c0c0c0-0006"
    tr = os.path.join(h, f"{sid}.jsonl")
    with open(tr, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "assistant", "isSidechain": False, "requestId": "r-last", "message": {
            "usage": {"cache_read_input_tokens": 295_000, "cache_creation_input_tokens": 5000,
                      "cache_creation": {"ephemeral_5m_input_tokens": 5000}}}}) + "\n")
    env = {"HERDR_PANE_ID": "w1:p1"}
    run(h, "UserPromptSubmit", sid, tr, {"prompt": "deliver card ABC-2"}, env)
    assert run(h, "Stop", sid, tr, env=env) == ""
    jpath = os.path.join(h, ".claude", "squad", "compact", f"{sid}.json")
    with open(jpath, encoding="utf-8") as fh:
        job = json.load(fh)
    assert job["ttl"] == 300 and 170 < job["due"] - time.time() <= 180 and job["req"] == "r-last", job
    assert job["text"].startswith("/compact ") and "\n" not in job["text"], job["text"]
    assert 'Owner\'s last request: "deliver card ABC-2".' in job["text"], job["text"]
    job["req"] = "rescheduled"                     # the squad-compact launched for it leaves on its own
    with open(jpath, "w", encoding="utf-8") as fh:
        json.dump(job, fh)
    # the /compact line reaching the session is not the owner's request
    run(h, "UserPromptSubmit", sid, tr, {"prompt": job["text"] + " pause, call it a day"}, env)
    st = state(h, sid)
    assert st["last_prompt"] == "deliver card ABC-2" and not st["asked_stop"], st
    # the record squad-compact leaves before sending: fresh labels the handoff, stale does not
    sent = os.path.join(h, ".claude", "squad", "compact", f"{sid}.sent")
    for age, trigger, ordered in ((5, "manual", True), (3600, "manual", False), (5, "auto", False)):
        with open(sent, "w", encoding="utf-8") as fh:
            json.dump({"req": "r-last", "ts": time.time() - age, "ctx": 300_000, "ttl": 300}, fh)
        r = run_proc(h, "PreCompact", sid, tr, {"trigger": trigger, "custom_instructions": None}, env)
        assert r.returncode == 0 and r.stdout.strip() == "", r
        with open(compact_handoff(h, sid), encoding="utf-8") as fh:
            body = fh.read()
        label = f"The watcher ordered this compaction (squad-compact, {trigger})"
        assert (label in body) is ordered, (age, trigger, body)
        assert ordered or f"about to be compacted ({trigger})" in body, (age, trigger, body)
        assert ("300k of context" in body) is ordered, body
        assert "stopped without writing" not in body, body
    assert not os.path.exists(os.path.join(h, ".claude", "squad", "handoffs", f"{sid}-watch.md"))
    assert not state(h, sid).get("switched")
    # that request was compacted: another Stop on it schedules nothing (only the .sent record
    # stops it here: the mark the /compact line above left is dropped)
    st = state(h, sid)
    assert st.pop("compact_as_text"), st
    with open(os.path.join(h, ".claude", "squad", "watch", f"{sid}.main.json"), "w", encoding="utf-8") as fh:
        json.dump(st, fh)
    os.remove(jpath)
    run(h, "Stop", sid, tr, env=env)
    assert not os.path.exists(jpath)
    # a handoff out of turn sends the work back: that Stop is not an idle session, nothing is scheduled
    sid = "c0c0c0c0-0007"
    tr2 = os.path.join(h, f"{sid}.jsonl")
    shutil.copy(tr, tr2)
    run(h, "UserPromptSubmit", sid, tr2, {"prompt": "deliver card ABC-3"}, env)
    write_handoff(h, tr2)
    assert kind(said(run(h, "Stop", sid, tr2, env=env))) == "Handoff out of turn"
    assert not os.path.exists(os.path.join(h, ".claude", "squad", "compact", f"{sid}.json"))


def watch_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("squad_watch_under_test", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def u16(s):
    return len(s.encode("utf-16-le", "surrogatepass")) // 2


def test_the_compact_text_fits_one_line_of_760_characters():
    """Claude Code pastes every prompt longer than 800 characters, and a pasted
    /compact does not run: it becomes an ordinary message. The text of the first
    port had 900+ characters of instructions alone. The ceiling is 760, with margin
    for an accent counted in bytes; the briefs path goes in whole and absolute (the
    Read tool takes no ~) or not at all; the owner's request takes what is left."""
    w = watch_module()
    assert w.COMPACT_LIMIT == 760
    accents = "ação já é não ótima revisão coração \U0001F600\U0001F680 " * 100
    real = "/srv/someone/.claude/squad/handoffs/" + "c0c0c0c0-0010-4aaa-8aaa-aaaaaaaaaaaa-dispatches.md"
    longest = "/srv/someone/.claude/squad/handoffs/" + "f" * 80 + "-dispatches.md"     # safe() keeps up to 80
    too_long = "/" + "x" * 300 + "/.claude/squad/handoffs/" + "f" * 80 + "-dispatches.md"
    for request, briefs in (("\U0001F600" * 3000, too_long), ("p" * 400, real), ("", ""), ("ok", ""),
                            (accents, ""), (accents, real), ("é" * 3000, ""), ("a\nb\rc" * 300, longest)):
        t = w.compact_text(request, briefs)
        assert u16(t) <= 760 and "\n" not in t and "\r" not in t, (u16(t), t)
        assert t.startswith(w.COMPACT_SIGNATURE) and w.compact_own(t), t
        assert ("Denied dispatch briefs: " + briefs + "." in t) is (bool(briefs) and briefs != too_long), t
        assert ("Denied dispatch briefs" in t) is (bool(briefs) and briefs != too_long), t
        assert too_long not in t and "/" + "x" * 50 not in t, t                # a cut path never goes in
    t = w.compact_text(accents, real)
    assert 'Owner\'s last request: "ação já' in t and t.endswith('\u2026".'), t   # cut, with an ellipsis
    assert u16(w.compact_text("p" * 5000, "")) >= 758                       # uses the room, does not overcut
    for must in ("owner's current request", "branch, worktree", "DONE with evidence", "background tasks",
                 "UNKNOWN", "check git status and the card"):
        assert must in t, must
    # through the Stop: the scheduled text, with the real (absolute) briefs path of this HOME
    h = home(short=True)
    sid = "c0c0c0c0-0010-4aaa-8aaa-aaaaaaaaaaaa"
    tr = os.path.join(h, f"{sid}.jsonl")
    with open(tr, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "assistant", "isSidechain": False, "requestId": "r-last", "message": {
            "usage": {"cache_read_input_tokens": 295_000, "cache_creation_input_tokens": 5000,
                      "cache_creation": {"ephemeral_1h_input_tokens": 5000}}}}) + "\n")
    briefs = os.path.join(h, ".claude", "squad", "handoffs", f"{sid}-dispatches.md")
    os.makedirs(os.path.dirname(briefs), exist_ok=True)
    open(briefs, "w", encoding="utf-8").close()
    env = {"HERDR_PANE_ID": "w1:p1"}
    run(h, "UserPromptSubmit", sid, tr, {"prompt": ("deliver card ABC-2 \U0001F600 ação já\n" * 30)}, env)
    run(h, "Stop", sid, tr, env=env)
    jpath = os.path.join(h, ".claude", "squad", "compact", f"{sid}.json")
    with open(jpath, encoding="utf-8") as fh:
        job = json.load(fh)
    job["req"] = "rescheduled"                     # the squad-compact launched for it leaves on its own
    with open(jpath, "w", encoding="utf-8") as fh:
        json.dump(job, fh)
    text = job["text"]
    assert u16(text) <= 760 and "\n" not in text and "\r" not in text, (u16(text), text)
    # absolute path, never "~/": the Read tool does not expand it (a Windows temp path may hold "RUNNER~1")
    assert f" Denied dispatch briefs: {briefs}." in text and " Denied dispatch briefs: ~" not in text, text
    assert 'Owner\'s last request: "deliver card ABC-2' in text and "\u2026" in text, text


def test_the_watchers_own_compact_pasted_is_not_the_owner_and_marks_the_failure():
    """The form the hook really receives when Claude Code pasted the /compact: wrapped
    in <pasted_content id=...>. It is not the owner's request, not a request to stop
    (the briefs path says "handoffs"), and it marks the session: the /compact arrived
    as a message, so squad-compact counts it as a failure and none is scheduled again."""
    w = watch_module()
    h = home()
    sid = "c0c0c0c0-0011"
    tr = os.path.join(h, f"{sid}.jsonl")
    with open(tr, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "assistant", "isSidechain": False, "requestId": "r-last", "message": {
            "usage": {"cache_read_input_tokens": 295_000, "cache_creation_input_tokens": 5000,
                      "cache_creation": {"ephemeral_1h_input_tokens": 5000}}}}) + "\n")
    env = {"HERDR_PANE_ID": "w1:p1"}
    run(h, "UserPromptSubmit", sid, tr, {"prompt": "open the PR of card ABC-4"}, env)
    ours = w.compact_text("open the PR of card ABC-4", f"/srv/x/.claude/squad/handoffs/{sid}-dispatches.md")
    old = ("/compact Summary to carry the work on without this conversation. Keep the exact values, in this "
           "order: (1) the owner's current request ... Briefs of denied dispatches (the pending plan): "
           f"/srv/x/.claude/squad/handoffs/{sid}-dispatches.md.")
    for text in (ours, old):
        for form in (f'\n\n<pasted_content id="a795">\n{text}\n</pasted_content id="a795">\n', text):
            st = state(h, sid)
            st.pop("compact_as_text", None)
            with open(os.path.join(h, ".claude", "squad", "watch", f"{sid}.main.json"), "w", encoding="utf-8") as fh:
                json.dump(st, fh)
            before = time.time()
            run(h, "UserPromptSubmit", sid, tr, {"prompt": form}, env)
            st = state(h, sid)
            assert st["last_prompt"] == "open the PR of card ABC-4" and st["asked_stop"] is False, (form, st)
            assert st.get("compact_as_text", 0) >= before - 1, (form, st)
    run(h, "Stop", sid, tr, env=env)                               # it does not insist in this session
    assert not os.path.exists(os.path.join(h, ".claude", "squad", "compact", f"{sid}.json"))
    # a paste from the owner that only quotes a /compact is still the owner
    run(h, "UserPromptSubmit", "c0c0c0c0-0012", tr, {"prompt": '<pasted_content id="b1">\nlog: /compact failed\n'
                                                              '</pasted_content id="b1">\nlook at this'}, env)
    st = state(h, "c0c0c0c0-0012")
    assert "look at this" in st["last_prompt"] and not st.get("compact_as_text"), st


def repo_with_gh(h):
    """A real git repository and a fake gh that answers the PR state."""
    repo = os.path.join(h, "repo")
    os.makedirs(repo)
    subprocess.run(["git", "init", "-q", "-b", "main", repo], check=True)
    state_file = os.path.join(h, "gh-state")
    with open(state_file, "w", encoding="utf-8") as fh:
        fh.write("OPEN")
    gh = os.path.join(h, "gh")
    with open(gh, "w", encoding="utf-8") as fh:
        fh.write(f"""#!/usr/bin/env python3
import sys
print("77" if "number" in sys.argv else open({state_file!r}).read())
""")
    os.chmod(gh, os.stat(gh).st_mode | stat.S_IXUSR)
    if os.name == "nt":                  # a shebang does not run on Windows: a .cmd that calls the script does
        launcher = gh + ".cmd"
        with open(launcher, "w", encoding="utf-8") as fh:
            fh.write(f'@"{sys.executable}" "{gh}" %*\r\n')
        gh = launcher
    return repo, {"SQUAD_GH": gh}, state_file


def bash(h, event, sid, repo, cmd, env):
    return said(run(h, event, sid, os.path.join(h, "t.jsonl"),
                    {"tool_input": {"command": cmd}, "cwd": repo}, env))


def queue(h):
    folder = os.path.join(h, ".claude", "squad", "turn")
    name = next(n for n in os.listdir(folder) if n.endswith(".json"))
    with open(os.path.join(folder, name), encoding="utf-8") as fh:
        return json.load(fh), os.path.join(folder, name)


def test_merge_queue_one_session_at_a_time_and_the_next_is_called():
    """Contract since the short turn: one session merges at a time, but only for
    the merge call itself; the next one goes as soon as that call returns, with
    no queue, no MERGE TURN call and no sync denial."""
    h = home()
    repo, env, state_file = repo_with_gh(h)
    a, b = "aaaa1111-0001", "bbbb2222-0001"
    assert bash(h, "PreToolUse", a, repo, "gh pr merge 10 --squash", env) == "nothing"   # turn free: passes
    assert queue(h)[0]["holder"]["session"] == a
    out = bash(h, "PreToolUse", b, repo, f"cd {sh(repo)} && gh pr merge 11 --squash", env)
    assert "MERGE QUEUE" in out and "is merging" in out and "PR #10" in out, out
    assert "queue" not in queue(h)[0] and queue(h)[0]["intents"] == []                    # nobody waits in line
    assert bash(h, "PreToolUse", b, repo, "git fetch origin && git merge origin/main", env) == "nothing"
    assert bash(h, "PreToolUse", a, repo, "git merge origin/main", env) == "nothing"
    assert bash(h, "PostToolUse", a, repo, "gh pr merge 10 --squash", env) == "nothing"   # the call returned
    d, _ = queue(h)
    assert d["holder"] is None and d["last"]["session"] == a and d["last"]["why"] == "done"
    assert bash(h, "PreToolUse", b, repo, "gh pr merge 11 --squash", env) == "nothing"    # the next one goes
    assert queue(h)[0]["holder"]["session"] == b
    assert "MERGE QUEUE" in bash(h, "PreToolUse", a, repo, "gh pr merge 12", env)


def test_merge_queue_stalled_turn_passes_on_and_can_be_requested_and_released():
    h = home()
    repo, env, _ = repo_with_gh(h)
    a, b, c = "aaaa1111-0002", "bbbb2222-0002", "cccc3333-0002"
    assert bash(h, "PreToolUse", a, repo, "squad-turn request 20", env) == "nothing"
    assert bash(h, "PreToolUse", b, repo, "squad-turn request 21", env) == "nothing"
    d, _ = queue(h)
    assert d.get("holder") is None and [(e["session"], e["pr"]) for e in d["intents"]] == [(a, "20"), (b, "21")]
    assert bash(h, "PreToolUse", c, repo, "git push origin HEAD:main", env) == "nothing"   # an intent blocks nobody
    d, path = queue(h)
    d["holder"]["since"] -= 1000                      # C's merge call never came back
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(d, fh)
    assert bash(h, "PreToolUse", a, repo, "gh pr merge", env) == "nothing"        # the stalled turn passes on
    d, _ = queue(h)
    assert d["holder"]["session"] == a and d["holder"]["pr"] == "77" and d["last"]["why"] == "expired"
    assert [e["session"] for e in d["intents"]] == [b]                            # merging consumed A's intent
    assert "expired" in bash(h, "PostToolUse", c, repo, "ls", env)               # C finds out
    bash(h, "PreToolUse", a, repo, "squad-turn release", env)
    assert queue(h)[0]["holder"] is None
    bash(h, "PreToolUse", b, repo, "squad-turn release", env)
    assert queue(h)[0]["intents"] == []
    # text that is provably data does not take the turn; neither does a branch with "main" in its name
    for cmd in ("git push origin feat/main-menu", "git commit -m 'explain gh pr merge'", "echo squad-turn request"):
        assert bash(h, "PreToolUse", a, repo, cmd, env) == "nothing", cmd
    assert queue(h)[0]["holder"] is None
    # fail closed (contract change): code fed to an interpreter counts as a merge, even through a heredoc
    bash(h, "PreToolUse", a, repo, "python3 - <<'E'\npat = 'gh pr merge|git merge origin/main'\nE", env)
    assert queue(h)[0]["holder"]["session"] == a
    bash(h, "PreToolUse", a, repo, "squad-turn release", env)
    assert bash(h, "PreToolUse", a, repo, "gh pr merge 30", {"SQUAD_TURN": "0", **env}) == "nothing"
    assert queue(h)[0]["holder"] is None


def holder(h):
    """Who holds the merge turn, or None (also when no merge was ever seen)."""
    folder = os.path.join(h, ".claude", "squad", "turn")
    if not os.path.isdir(folder) or not any(n.endswith(".json") for n in os.listdir(folder)):
        return None
    return queue(h)[0].get("holder")


GM = "gh pr " + "merge"


def test_merge_text_is_data_only_where_it_provably_does_not_run():
    """October 2026: `agenfk create ... -d "... (gh pr merge, ...)"` took the merge
    turn. Text is data only as the value of -m/-d/--body/--title/--description/
    --evidence or an echo/printf argument, on a line of inert commands; any other
    form counts as a merge (fail closed) and goes through the turn and the seal."""
    h = home()
    repo, env, _ = repo_with_gh(h)
    sid = "dddd4444-0001"
    for cmd in (f'agenfk create story x -d "The turn covers the merge ({GM}, push to main, merge_pull_request)"',
                f"git commit -m 'explain {GM}'", f'gh pr create --title "x" --body "run {GM} 5 after"',
                f"echo \\; {GM} 6", f'cd x && agenfk create --description="{GM} 5" 2>&1'):
        assert bash(h, "PreToolUse", sid, repo, cmd, env) == "nothing", cmd
        assert holder(h) is None, cmd
    for cmd in (f'bash -c "{GM} 5"', f"sh -c '{GM} 5'", 'eval "git push origin main"', f'ssh host "{GM} 5"',
                f"echo 5 | xargs {GM}", f'env FOO=1 sh -c "{GM} 5"', f'echo "$({GM} 5)"', f"echo `{GM} 5`",
                f"watch -n 5 {GM} 5", f"timeout 60 {GM} 5", f"nohup {GM} 5", "git -C . push origin HEAD:main",
                "git push origin +HEAD:refs/heads/main", "gh api -X PUT repos/o/r/pulls/5/merge",
                f'echo "{GM} 5" | sh', 'gh pr "mer""ge" 5', f"cat > n.md <<EOF\n{GM} 9\nEOF"):
        assert bash(h, "PreToolUse", sid, repo, cmd, env) == "nothing", cmd     # seal off: takes the turn
        assert holder(h)["session"] == sid, cmd
        bash(h, "PostToolUse", sid, repo, cmd, env)                            # and gives it back
        assert holder(h) is None, cmd
        out = bash(h, "PreToolUse", sid, repo, cmd, {**env, "SQUAD_SEAL": "1"})
        assert "MERGE SEAL" in out and "squad-seal record" in out, (cmd, out)
        assert holder(h) is None, cmd                                          # a denial releases at once
    out = bash(h, "PreToolUse", sid, repo, f'agenfk comment 1 "see {GM} 3" && {GM} 12', env)
    assert "more than one merge" in out and "#3" in out and "#12" in out, out


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, check=True).stdout.strip()


def origin_repo(h):
    """A clone of a bare origin with a fake gh that reads PRs from prs.json."""
    bare, repo = os.path.join(h, "origin.git"), os.path.join(h, "repo")
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", bare], check=True)
    subprocess.run(["git", "clone", "-q", bare, repo], check=True, capture_output=True)
    for k, v in (("user.name", "t"), ("user.email", "t@t"), ("commit.gpgsign", "false")):
        git(repo, "config", k, v)
    for f in ("a.txt", "b.txt", "c.txt"):
        with open(os.path.join(repo, f), "w", encoding="utf-8") as fh:
            fh.write("base\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    git(repo, "push", "-q", "origin", "HEAD:main")
    prs = os.path.join(h, "prs.json")
    gh = os.path.join(h, "gh")
    with open(gh, "w", encoding="utf-8") as fh:
        fh.write(f"""#!/usr/bin/env python3
import json, sys
prs = json.load(open({prs!r}))
n = next((a for a in sys.argv[3:] if a.isdigit()), "")
pr = prs.get(n) or {{}}
print(json.dumps(pr) if "-q" not in sys.argv else pr.get(sys.argv[-1][1:], ""))
""")
    os.chmod(gh, os.stat(gh).st_mode | stat.S_IXUSR)
    if os.name == "nt":                  # a shebang does not run on Windows: a .cmd that calls the script does
        launcher = gh + ".cmd"
        with open(launcher, "w", encoding="utf-8") as fh:
            fh.write(f'@"{sys.executable}" "{gh}" %*\r\n')
        gh = launcher
    return repo, {"SQUAD_GH": gh}, prs


def branch(repo, name, path, text):
    git(repo, "checkout", "-q", "-b", name, "origin/main")
    with open(os.path.join(repo, path), "w", encoding="utf-8") as fh:
        fh.write(text)
    git(repo, "commit", "-qam", name)
    sha = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "--detach")
    return sha


def set_prs(prs, **kw):
    with open(prs, "w", encoding="utf-8") as fh:
        json.dump({n: {"number": int(n), "baseRefName": "main", "headRefName": b, "headRefOid": s}
                   for n, (b, s) in kw.items()}, fh)


def test_merge_turn_two_sessions_disjoint_prs_merge_without_waiting():
    h = home()
    repo, env, prs = origin_repo(h)
    set_prs(prs, **{"1": ("feat-a", branch(repo, "feat-a", "a.txt", "A\n")),
                    "2": ("feat-b", branch(repo, "feat-b", "b.txt", "B\n"))})
    a, b = "aaaa5555-0001", "bbbb6666-0001"
    assert bash(h, "PreToolUse", a, repo, f"{GM} 1 --squash", env) == "nothing"
    out = bash(h, "PreToolUse", b, repo, f"{GM} 2 --squash", env)          # only while A's call is running
    assert "MERGE QUEUE" in out and "is merging" in out and "a few seconds" in out and "PR #1" in out, out
    assert bash(h, "PreToolUse", b, repo, "git fetch origin && git merge origin/main", env) == "nothing"  # sync: never
    bash(h, "PostToolUse", a, repo, f"{GM} 1 --squash", env)                 # A's call came back: turn released
    assert holder(h) is None and queue(h)[0]["last"]["why"] == "done"
    assert bash(h, "PreToolUse", b, repo, f"{GM} 2 --squash", env) == "nothing"   # B: no queue, no wait
    bash(h, "PostToolUse", b, repo, f"{GM} 2 --squash", env)
    assert holder(h) is None
    # a failed merge call gives the turn back as well
    assert bash(h, "PreToolUse", a, repo, f"{GM} 1", env) == "nothing"
    run(h, "PostToolUseFailure", a, os.path.join(h, "t.jsonl"), {"tool_input": {"command": f"{GM} 1"}, "cwd": repo}, env)
    assert holder(h) is None


def test_merge_turn_held_too_long_expires():
    h = home()
    repo, env, _ = repo_with_gh(h)
    a, b = "aaaa7777-0001", "bbbb8888-0001"
    assert bash(h, "PreToolUse", a, repo, f"{GM} 10", env) == "nothing"
    d, path = queue(h)
    d["holder"]["since"] -= 100
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(d, fh)
    assert "is merging" in bash(h, "PreToolUse", b, repo, f"{GM} 11", env)       # 100 s: still A's
    d, path = queue(h)
    d["holder"]["since"] -= 21
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(d, fh)
    assert bash(h, "PreToolUse", b, repo, f"{GM} 11", env) == "nothing"          # 121 s: expired
    d, _ = queue(h)
    assert d["holder"]["session"] == b and d["last"]["why"] == "expired" and d["last"]["session"] == a
    assert "expired" in bash(h, "PostToolUse", a, repo, "ls", env)              # A finds out


def test_merge_conflict_with_main_is_denied_with_the_files():
    h = home()
    repo, env, prs = origin_repo(h)
    head = branch(repo, "feat-c", "a.txt", "C\n")
    with open(os.path.join(repo, "b.txt"), "w", encoding="utf-8") as fh:                         # a second, clean file on the PR
        fh.write("C\n")
    git(repo, "checkout", "-q", "feat-c")
    git(repo, "commit", "-qam", "b")
    head = git(repo, "rev-parse", "HEAD")
    set_prs(prs, **{"3": ("feat-c", head)})
    other = os.path.join(h, "other")                                           # main moves elsewhere: no local ref yet
    subprocess.run(["git", "clone", "-q", os.path.join(h, "origin.git"), other], check=True, capture_output=True)
    for k, v in (("user.name", "t"), ("user.email", "t@t")):
        git(other, "config", k, v)
    with open(os.path.join(other, "a.txt"), "w", encoding="utf-8") as fh:
        fh.write("MAIN\n")
    git(other, "commit", "-qam", "main moves")
    git(other, "push", "-q", "origin", "HEAD:main")
    sid = "cccc9999-0001"
    out = bash(h, "PreToolUse", sid, repo, f"{GM} 3", env)
    assert "MERGE CONFLICT" in out and "Conflict in: a.txt." in out and "b.txt" not in out, out
    assert "git merge origin/main" in out and "AgenFK" in out, out
    assert holder(h) is None                                                   # the turn was released
    git(repo, "merge", "-q", "-X", "theirs", "origin/main", "-m", "sync")      # resolved: passes, no suite asked
    set_prs(prs, **{"3": ("feat-c", git(repo, "rev-parse", "HEAD"))})
    assert bash(h, "PreToolUse", sid, repo, f"{GM} 3", env) == "nothing"


def test_turn_status_shows_who_is_merging_and_the_intents():
    h = home()
    repo, env, _ = repo_with_gh(h)
    assert bash(h, "PreToolUse", "eeee1111-0001", repo, "squad-turn request 40", env) == "nothing"
    assert bash(h, "PreToolUse", "ffff2222-0001", repo, f"{GM} 41", env) == "nothing"
    e = dict(os.environ, HOME=h, USERPROFILE=h)
    out = subprocess.run([sys.executable, os.path.join(ROOT, "hooks", "squad-turn.py"), "status"], cwd=repo,
                         capture_output=True, text=True, env=e).stdout
    assert "merging now: session ffff2222 (PR #41)" in out and "eeee1111 (PR #40)" in out, out


def beta_origin(h):
    """origin_repo whose default branch is beta (origin/HEAD -> origin/beta)."""
    repo, env, prs = origin_repo(h)
    git(repo, "push", "-q", "origin", "HEAD:beta")
    git(repo, "fetch", "-q", "origin")
    git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/beta")
    git(repo, "checkout", "-q", "-b", "feat", "origin/beta")
    return repo, env, prs


def test_push_to_the_remote_default_branch_is_a_push_to_the_trunk():
    """Review of October 2026: with beta as the default branch, `git push origin
    HEAD:beta` went past the turn and the seal, which only knew main/master."""
    h = home()
    repo, env, _ = beta_origin(h)
    sid = "abab1111-0001"
    for cmd in ("git push origin HEAD:beta", "git push origin feat:beta", "git push origin +feat:refs/heads/beta"):
        assert bash(h, "PreToolUse", sid, repo, cmd, env) == "nothing", cmd          # seal off: takes the turn
        assert holder(h)["session"] == sid, cmd
        bash(h, "PostToolUse", sid, repo, cmd, env)
        out = bash(h, "PreToolUse", sid, repo, cmd, {**env, "SQUAD_SEAL": "1"})
        assert "MERGE SEAL" in out and "push to the trunk" in out, (cmd, out)
    for cmd in ("git push origin feat", "git push origin HEAD:feat-2", "git push -u origin feat:beta-notes"):
        assert bash(h, "PreToolUse", sid, repo, cmd, {**env, "SQUAD_SEAL": "1"}) == "nothing", cmd
        assert holder(h) is None, cmd


def test_trunk_from_gh_when_origin_head_is_missing_and_from_squad_trunk():
    h = home()
    repo, env, prs = beta_origin(h)
    git(repo, "symbolic-ref", "--delete", "refs/remotes/origin/HEAD")
    sealed = {**env, "SQUAD_SEAL": "1"}
    assert bash(h, "PreToolUse", "abab2222-0001", repo, "git push origin HEAD:beta", sealed) == "nothing"
    with open(prs, "w", encoding="utf-8") as fh:
        json.dump({"": {"defaultBranchRef.name": "beta"}}, fh)       # what `gh repo view` answers
    assert "MERGE SEAL" in bash(h, "PreToolUse", "abab2222-0001", repo, "git push origin HEAD:beta", sealed)
    git(repo, "config", "squad.trunk", "release, stable")
    for dst in ("release", "stable"):
        out = bash(h, "PreToolUse", "abab2222-0001", repo, f"git push origin HEAD:{dst}", sealed)
        assert "MERGE SEAL" in out, (dst, out)


def test_push_with_no_refspec_to_a_trunk_upstream_is_a_push_to_the_trunk():
    """A branch that tracks origin/beta with push.default=upstream sends `git push`
    to beta: the watcher reads the push target, not the branch name."""
    h = home()
    repo, env, _ = beta_origin(h)
    sealed = {**env, "SQUAD_SEAL": "1"}
    assert bash(h, "PreToolUse", "abab3333-0001", repo, "git push", sealed) == "nothing"   # simple: feat -> feat
    git(repo, "branch", "-q", "--set-upstream-to", "origin/beta")
    git(repo, "config", "push.default", "upstream")
    for cmd in ("git push", "git push origin"):
        assert "MERGE SEAL" in bash(h, "PreToolUse", "abab3333-0001", repo, cmd, sealed), cmd


def test_turn_status_compares_against_the_remote_default_branch():
    h = home()
    repo, env, _ = beta_origin(h)
    with open(os.path.join(repo, "a.txt"), "w", encoding="utf-8") as fh:
        fh.write("feat\n")
    git(repo, "commit", "-qam", "feat")
    out = subprocess.run([sys.executable, os.path.join(ROOT, "hooks", "squad-turn.py"), "status"], cwd=repo,
                         capture_output=True, text=True, env=dict(os.environ, HOME=h, USERPROFILE=h, **env)).stdout
    assert "feat x origin/beta" in out and "merges without conflict" in out, out


def seal_head(h, repo):
    """Records a SHIP seal of the repository's HEAD with squad-seal."""
    sha = git(repo, "rev-parse", "HEAD")
    ev = os.path.join(h, f"{sha}.md")
    with open(ev, "w", encoding="utf-8") as fh:
        fh.write("SHIP\n")
    e = {k: v for k, v in os.environ.items() if not k.startswith(("SQUAD_", "HERDR_"))}
    r = subprocess.run([sys.executable, os.path.join(ROOT, "hooks", "squad-seal.py"), "record", "--card", "c-1",
                        "--model", "m", "--verdict", "SHIP", "--evidence", ev], cwd=repo, capture_output=True,
                       text=True, env=dict(e, HOME=h, USERPROFILE=h))
    assert r.returncode == 0, r.stderr
    return sha


def test_push_of_head_from_the_trunk_is_a_push_to_the_trunk():
    """Round 2 review: `git push origin HEAD` with the trunk checked out passed,
    because `origin` counted as the refspec and the current branch was never read."""
    h = home()
    repo, env, _ = beta_origin(h)
    sealed = {**env, "SQUAD_SEAL": "1"}
    sid = "acac1111-0001"
    for cmd in ("git push origin HEAD", "git push -u origin HEAD", "git push origin @", "git push origin feat"):
        assert bash(h, "PreToolUse", sid, repo, cmd, sealed) == "nothing", cmd          # feat is not the trunk
    for cmd in ("git push --all origin", "git push --al origin", "git push --branches origin", "git push --mir"):
        out = bash(h, "PreToolUse", sid, repo, cmd, sealed)
        assert "MERGE SEAL" in out and "push to the trunk" in out, (cmd, out)           # --all pushes the trunk
    git(repo, "checkout", "-q", "-B", "beta", "origin/beta")
    for cmd in ("git push origin HEAD", "git push -u origin HEAD", "git push origin @"):
        out = bash(h, "PreToolUse", sid, repo, cmd, sealed)
        assert "MERGE SEAL" in out and "push to the trunk" in out, (cmd, out)
    git(repo, "checkout", "-q", "-b", "master")                                        # no upstream, no push target
    out = bash(h, "PreToolUse", sid, repo, "git push", sealed)
    assert "MERGE SEAL" in out and "push to the trunk" in out, out                     # the current branch is master


def test_merge_and_push_are_checked_where_the_command_runs():
    """Round 2 review: from a cwd outside the repository, `cd <repo> && <merge>`
    passed unchecked; `cd ../wt-x && git push origin HEAD:beta` checked the HEAD of
    the session's cwd, sealed, instead of the unsealed HEAD of wt-x."""
    h = home()
    repo, env, prs = beta_origin(h)
    git(repo, "config", "squad.seal", "true")
    sid = "acac2222-0001"
    outside = os.path.join(h, "outside")
    os.makedirs(outside)
    set_prs(prs, **{"5": ("feat", git(repo, "rev-parse", "HEAD"))})
    for cmd in (f"cd {sh(repo)} && git push origin HEAD:beta", f"cd {sh(repo)} && {GM} 5", f"cd {sh(repo)}; {GM} 5"):
        out = bash(h, "PreToolUse", sid, outside, cmd, env)
        assert "MERGE SEAL" in out and "no seal for that SHA" in out, (cmd, out)
    for cmd in ('git -C "$WT" push origin HEAD:beta', f'cd "$WT" && {GM} 5', f"cd - && {GM} 5",
                f"(cd {sh(repo)} && true) && {GM} 5", f"GIT_DIR={sh(repo)}/.git git push origin HEAD:beta"):
        out = bash(h, "PreToolUse", sid, outside, cmd, env)
        assert "MERGE SEAL" in out and "literal path" in out, (cmd, out)
        assert holder(h) is None, cmd
    seal_head(h, repo)
    wt = os.path.join(h, "wt-x")
    git(repo, "worktree", "add", "-q", "-b", "other", wt, "origin/beta")
    with open(os.path.join(wt, "a.txt"), "w", encoding="utf-8") as fh:
        fh.write("not reviewed\n")
    git(wt, "commit", "-qam", "unsealed")
    assert bash(h, "PreToolUse", sid, repo, "git push origin HEAD:beta", env) == "nothing"   # sealed here
    bash(h, "PostToolUse", sid, repo, "git push origin HEAD:beta", env)
    for cmd in ("cd ../wt-x && git push origin HEAD:beta", "git -C ../wt-x push origin HEAD:beta",
                f"cd {sh(wt)} && git push origin HEAD:beta"):
        out = bash(h, "PreToolUse", sid, repo, cmd, env)
        assert "MERGE SEAL" in out and "no seal for that SHA" in out, (cmd, out)
    out = bash(h, "PreToolUse", sid, repo, 'cd "$WT" && git push origin HEAD:beta', {**env, "SQUAD_SEAL": "0"})
    assert out == "nothing", out                                                           # seal off: no denial


def test_turn_status_uses_squad_trunk_first():
    h = home()
    repo, env, _ = beta_origin(h)
    git(repo, "push", "-q", "origin", "origin/beta:refs/heads/release")
    git(repo, "fetch", "-q", "origin")
    git(repo, "config", "squad.trunk", "release")
    out = subprocess.run([sys.executable, os.path.join(ROOT, "hooks", "squad-turn.py"), "status"], cwd=repo,
                         capture_output=True, text=True, env=dict(os.environ, HOME=h, USERPROFILE=h, **env)).stdout
    assert "feat x origin/release" in out, out


def test_a_cd_that_may_not_run_leaves_the_directory_unknown():
    """Round 3 review: `false && cd <repo>; git push ...` was checked in <repo>,
    though the cd never ran and the push ran in the session's unsealed worktree."""
    h = home()
    repo, env, _ = beta_origin(h)
    git(repo, "config", "squad.seal", "true")
    sid = "adad1111-0001"
    seal_head(h, repo)
    wt = os.path.join(h, "wt-y")
    git(repo, "worktree", "add", "-q", "-b", "other", wt, "origin/beta")
    with open(os.path.join(wt, "a.txt"), "w", encoding="utf-8") as fh:
        fh.write("not reviewed\n")
    git(wt, "commit", "-qam", "unsealed")
    assert bash(h, "PreToolUse", sid, wt, f"cd {sh(repo)}; git push origin HEAD:beta", env) == "nothing"   # sealed there
    bash(h, "PostToolUse", sid, wt, f"cd {sh(repo)}; git push origin HEAD:beta", env)
    for cmd in (f"false && cd {sh(repo)}; git push origin HEAD:beta", f"[ -d /nada ] && cd {sh(repo)}; git push origin HEAD:beta",
                f"cd {sh(wt)} || cd {sh(repo)}; git push origin HEAD:beta", f"cd {sh(repo)} | cat; git push origin HEAD:beta",
                f"cd {sh(repo)} & git push origin HEAD:beta", f"if true; then cd {sh(repo)}; fi; git push origin HEAD:beta"):
        out = bash(h, "PreToolUse", sid, wt, cmd, env)
        assert "MERGE SEAL" in out and "literal path" in out, (cmd, out)


def test_an_unknown_directory_counts_only_a_push_that_may_reach_the_trunk():
    """Round 3 review: with the directory unknown, every push counted as a push to
    the trunk, so `cd "$D" && git push -u origin feat/x` took the turn or was denied."""
    h = home()
    repo, env, _ = beta_origin(h)
    sid = "adad2222-0001"
    outside = os.path.join(h, "outside")
    os.makedirs(outside)
    for cmd in ('cd "$D" && git push -u origin feat/x', "cd /no/such/dir && git push origin feat/x:feat/x"):
        for where in (repo, outside):
            assert bash(h, "PreToolUse", sid, where, cmd, env) == "nothing", (where, cmd)
            assert holder(h) is None, (where, cmd)
    git(repo, "config", "squad.seal", "true")
    for cmd in ('cd "$D" && git push', 'cd "$D" && git push origin', 'cd "$D" && git push -u origin HEAD',
                'cd "$D" && git push origin @', 'cd "$D" && git push origin HEAD:beta', 'cd "$D" && git push origin main'):
        out = bash(h, "PreToolUse", sid, repo, cmd, env)
        assert "MERGE SEAL" in out and "literal path" in out, (cmd, out)


def test_an_unknown_directory_is_denied_whatever_the_sessions_repository_says():
    """Security review of round 4: from a repository without the seal (trunk main
    only), `cd "$WT_B" && <merge>` and `cd "$WT_B" && git push origin HEAD:beta`
    passed unchecked, though the seal may be on in B and beta may be its trunk;
    `builtin cd <repo>` was not followed and the push was checked in the cwd."""
    h = home()
    repo, env, _ = origin_repo(h)
    sid = "aeae1111-0001"
    for cmd in (f'cd "$WT_B" && {GM} 5', 'cd "$WT_B" && git push origin HEAD:beta',
                'cd "$WT_B" && git push origin release', 'cd "$WT_B" && git push --all origin feat/x',
                f"builtin cd {sh(repo)} && git push origin HEAD:beta", f"command cd {sh(repo)}; {GM} 5",
                f"X=1 cd {sh(repo)} && git push origin HEAD:beta", 'cd "$WT_B" && git push --mir origin feat/x',
                f"function f {{ cd {sh(repo)}; }}; git push origin HEAD:beta"):
        out = bash(h, "PreToolUse", sid, repo, cmd, env)
        assert "MERGE SEAL" in out and "literal path" in out, (cmd, out)
        assert holder(h) is None, cmd
    assert bash(h, "PreToolUse", sid, repo, 'cd "$WT_B" && git push origin HEAD:beta',
                {**env, "SQUAD_SEAL": "0"}) == "nothing"
    assert bash(h, "PreToolUse", sid, repo, f"cd {sh(repo)} && git push origin feat/x", env) == "nothing"


def test_turn_status_falls_back_to_master():
    """With no origin/HEAD, no squad.trunk, no origin/main and gh silent, the trunk is master."""
    h = home()
    repo, env, _ = origin_repo(h)
    git(repo, "push", "-q", "origin", "HEAD:master")
    subprocess.run(["git", "--git-dir", os.path.join(h, "origin.git"), "symbolic-ref", "HEAD", "refs/heads/master"],
                   check=True)
    git(repo, "push", "-q", "origin", ":main")
    git(repo, "fetch", "-q", "--prune", "origin")
    subprocess.run(["git", "-C", repo, "symbolic-ref", "--delete", "refs/remotes/origin/HEAD"], capture_output=True)
    git(repo, "checkout", "-q", "-b", "feat", "origin/master")
    out = subprocess.run([sys.executable, os.path.join(ROOT, "hooks", "squad-turn.py"), "status"], cwd=repo,
                         capture_output=True, text=True, env=dict(os.environ, HOME=h, USERPROFILE=h, **env)).stdout
    assert "feat x origin/master" in out, out


# ------------------------------------------------------------------ review gate
CARD_A = "f93d1785-9a4d-4d28-bbe0-9f1d074fd2e5"
CARD_B = "0b1c2d3e-1111-4222-8333-944455566677"


def fake_agenfk(h, steps, mode="json"):
    """A stub `agenfk` first on PATH: `get <id> --json` answers with the step in
    <h>/agenfk-steps.json and a history whose last move into that step is the time in
    <h>/agenfk-entered.json (see move(); 2000-01-01 when unset), as agenfk does. The mode in
    <h>/agenfk-mode breaks it on purpose: text, garbage, fail, slow, nohistory; delay:<s>
    answers after <s> seconds. The real agenfk is kept off PATH. Returns (env, env without the stub)."""
    folder = os.path.join(h, "bin")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(h, "agenfk-steps.json"), "w", encoding="utf-8") as fh:
        json.dump(steps, fh)
    with open(os.path.join(h, "agenfk-mode"), "w", encoding="utf-8") as fh:
        fh.write(mode)
    path = os.path.join(folder, "agenfk")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"""#!{sys.executable}
import json, os, subprocess, sys, time
H = {h!r}
a = sys.argv[1:]
with open(os.path.join(H, "agenfk-calls"), "a") as fh:
    fh.write(" ".join(a) + "\\n")
mode = open(os.path.join(H, "agenfk-mode")).read().strip()
steps = json.load(open(os.path.join(H, "agenfk-steps.json")))
if mode == "fail":
    sys.exit(1)
if mode == "garbage":
    print("<html>502 Bad Gateway {{not json")
    sys.exit(0)
if mode == "slow":                       # a child that holds the pipe, like a node CLI would
    kid = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    open(os.path.join(H, "agenfk-kid"), "w").write(str(kid.pid))
    time.sleep(30)
    sys.exit(0)
if mode.startswith("delay:"):
    time.sleep(float(mode[6:]))
if a[:1] != ["get"]:
    sys.exit(2)
card = a[1] if a[1] in steps else next((c for c in steps if c.lower().startswith(a[1].lower())), None)
if card is None:
    print("No item found starting with " + a[1])
    sys.exit(1)
step = steps[card]
try:
    entered = json.load(open(os.path.join(H, "agenfk-entered.json"))).get(card)
except OSError:
    entered = None
history = [{{"fromStatus": "TODO", "toStatus": "TODO", "timestamp": "2000-01-01T00:00:00.000Z"}},
           {{"fromStatus": "TODO", "toStatus": step, "timestamp": entered or "2000-01-01T00:00:00.000Z"}}]
if mode == "text":
    print("[x] title\\n  Type:        STORY\\n  Status:      " + step)
elif mode == "nohistory":
    print(json.dumps({{"id": a[1], "status": step}}, indent=2))
else:
    print(json.dumps({{"id": a[1], "status": step, "history": history}}, indent=2))
""")
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
    if os.name == "nt":
        with open(path + ".cmd", "w", encoding="utf-8") as fh:
            fh.write(f'@"{sys.executable}" "{path}" %*\r\n')
    system = os.pathsep.join(d for d in os.environ.get("PATH", "").split(os.pathsep)
                             if d and not any(os.path.exists(os.path.join(d, "agenfk" + x))
                                              for x in ("", ".cmd", ".exe", ".ps1")))
    return {"PATH": folder + os.pathsep + system}, {"PATH": system}


def move(h, card, step, ago=0.0):
    """The card moves into `step` now (or `ago` seconds ago), as `agenfk verify` would record it."""
    time.sleep(0.02)                      # a dispatch just before it is strictly older, at milliseconds
    path = os.path.join(h, "agenfk-steps.json")
    with open(path, encoding="utf-8") as fh:
        steps = json.load(fh)
    steps[card] = step
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(steps, fh)
    path = os.path.join(h, "agenfk-entered.json")
    entered = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            entered = json.load(fh)
    at = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=ago)
    entered[card] = at.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(entered, fh)


def stub_mode(h, mode):
    with open(os.path.join(h, "agenfk-mode"), "w", encoding="utf-8") as fh:
        fh.write(mode)


def stub_calls(h):
    try:
        with open(os.path.join(h, "agenfk-calls"), encoding="utf-8") as fh:
            return fh.read().splitlines()
    except OSError:
        return []


def verify(h, sid, card, env, cmd=None, extra=None):
    payload = {"tool_name": "Bash", "tool_input": {"command": cmd or f'agenfk verify {card} --evidence "looks good"'}}
    payload.update(extra or {})
    return said(run(h, "PreToolUse", sid, os.path.join(h, "none.jsonl"), payload, env))


def reviewer(h, sid, brief, env, agent_type="verifier"):
    """A finished dispatch of `agent_type` from main session `sid` with this brief."""
    tr = session(h, sid, 60_000, 0.0)
    run(h, "PostToolUse", sid, tr, {"tool_name": "Agent", "tool_input": {
        "subagent_type": agent_type, "description": "review the card", "prompt": brief}}, env)


def gate_log(h):
    path = os.path.join(h, ".claude", "squad", "review-gate.jsonl")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def test_review_step_is_not_advanced_without_a_reviewer_from_any_session():
    h = home()
    env, _ = fake_agenfk(h, {CARD_A: "REVIEW"})
    sid = "abcdef12-0101"
    out = verify(h, sid, CARD_A, env)
    assert "REVIEW REQUIRED" in out and CARD_A in out and "on REVIEW" in out, out
    # the way forward: the verifier, with the card id and the branch, never the narrative
    assert "`verifier`" in out and "branch" in out and "never the implementer's narrative" in out, out
    assert gate_log(h)[-1]["decision"] == "deny" and gate_log(h)[-1]["card"] == CARD_A, gate_log(h)
    # any spacing, flags before the id, a cd before it, a wrapper shell, a short id, the MCP tool
    for cmd in (f'cd {sh(h)} &&  agenfk   verify --evidence "a b" --check tests=pass {CARD_A}',
                f'bash -c "agenfk verify {CARD_A} --evidence ok"',
                f'agenfk verify {CARD_A[:8]} \\\n  --evidence "line one\nline two"'):
        assert "REVIEW REQUIRED" in verify(h, sid, CARD_A, env, cmd), cmd
    out = said(run(h, "PreToolUse", sid, "", {"tool_name": "mcp__agenfk__validate_progress",
                                             "tool_input": {"itemId": CARD_A, "evidence": "x"}}, env))
    assert "REVIEW REQUIRED" in out, out
    # what does not advance the card is left alone: a dry run, a read, text that only mentions it
    for cmd in (f"agenfk verify {CARD_A} --plan", f"agenfk get {CARD_A}",
                f'git commit -m "agenfk verify {CARD_A}"', f"echo agenfk verify {CARD_A}"):
        assert verify(h, sid, CARD_A, env, cmd) == "nothing", cmd
    # sub-sessions are not the target: only the main session advances cards
    assert verify(h, sid, CARD_A, env, extra={"agent_id": "a1"}) == "nothing"
    # a verifier dispatched from ANOTHER session (the one before a handoff) unlocks it
    reviewer(h, "abcdef12-0102", f"Review card {CARD_A}, branch feat/x. Verdict SHIP/FIX_FIRST/REJECT.", env)
    calls = len(stub_calls(h))
    assert verify(h, sid, CARD_A, env) == "nothing"
    assert verify(h, "abcdef12-0103", CARD_A[:8], env) == "nothing"           # a third session, short id
    # agenfk is still asked once a dispatch is recorded: the record alone does not say which step it was for
    assert len(stub_calls(h)) == calls + 2, stub_calls(h)
    recorded = os.path.join(h, ".claude", "squad", "reviews", f"{CARD_A}.json")
    with open(recorded, encoding="utf-8") as fh:
        assert json.load(fh)["dispatches"][0]["session"] == "abcdef12-0102"


def test_review_gate_only_on_review_steps():
    h = home()
    cards = {"IMPLEMENT": "11111111-0000-4000-8000-000000000001", "IN_PROGRESS": "11111111-0000-4000-8000-000000000002",
             "TEST": "11111111-0000-4000-8000-000000000003", "DONE": "11111111-0000-4000-8000-000000000004",
             "ADVERSARIAL": "11111111-0000-4000-8000-000000000005", "HEAVY_REVIEW": "11111111-0000-4000-8000-000000000006",
             "review": "11111111-0000-4000-8000-000000000007"}
    env, _ = fake_agenfk(h, {card: step for step, card in cards.items()})
    sid = "abcdef12-0111"
    for step in ("IMPLEMENT", "IN_PROGRESS", "TEST", "DONE"):
        assert verify(h, sid, cards[step], env) == "nothing", step
    for step in ("ADVERSARIAL", "review"):
        assert "REVIEW REQUIRED" in verify(h, sid, cards[step], env), step
    # HEAVY_REVIEW is not in the default: the flow gives it to the main session, whose review is
    # `squad-review` (another model, run through Bash), not an Agent/Task dispatch the gate could count
    assert verify(h, sid, cards["HEAVY_REVIEW"], env) == "nothing"
    # ...but naming it explicitly still gates it
    named = dict(env, SQUAD_REVIEW_STEPS="REVIEW,ADVERSARIAL,HEAVY_REVIEW")
    assert "REVIEW REQUIRED" in verify(h, sid, cards["HEAVY_REVIEW"], named)
    # the steps are configurable; empty turns the gate off
    narrow = dict(env, SQUAD_REVIEW_STEPS="test")
    assert "REVIEW REQUIRED" in verify(h, sid, cards["TEST"], narrow)
    assert verify(h, sid, cards["ADVERSARIAL"], narrow) == "nothing"
    assert verify(h, sid, cards["ADVERSARIAL"], dict(env, SQUAD_REVIEW_STEPS="")) == "nothing"


def test_a_reviewer_unlocks_only_its_own_card():
    h = home()
    env, _ = fake_agenfk(h, {CARD_A: "REVIEW", CARD_B: "REVIEW", "ABC-1": "REVIEW", "ABC-12": "REVIEW"})
    sid = "abcdef12-0121"
    reviewer(h, sid, f"Review card {CARD_A} on branch feat/a", env)
    assert verify(h, sid, CARD_A, env) == "nothing"
    assert "REVIEW REQUIRED" in verify(h, sid, CARD_B, env)          # card A's reviewer does not unlock B
    reviewer(h, sid, "Review the latest changes on branch feat/b", env)   # no card id: unlocks nothing
    reviewer(h, sid, f"Implement card {CARD_B} on branch feat/b", env, agent_type="developer")   # not a reviewer
    assert "REVIEW REQUIRED" in verify(h, sid, CARD_B, env)
    reviewer(h, sid, "Review card ABC-1, branch feat/c", env)
    assert verify(h, sid, "ABC-1", env) == "nothing"
    assert "REVIEW REQUIRED" in verify(h, sid, "ABC-12", env)        # a key is not a prefix of another
    reviewer(h, sid, f"Review card {CARD_B[:8]} on branch feat/b", env)   # the short id in the brief counts
    assert verify(h, sid, CARD_B, env) == "nothing"


def test_review_gate_fails_open_and_says_why():
    h = home()
    env, no_stub = fake_agenfk(h, {CARD_A: "REVIEW", CARD_B: "TEST"})
    sid = "abcdef12-0131"
    assert "REVIEW REQUIRED" in verify(h, sid, CARD_A, env)           # control: the stub works
    stub_mode(h, "text")                                              # older agenfk: no JSON, the text still reads
    assert "REVIEW REQUIRED" in verify(h, sid, CARD_A, env)
    assert verify(h, sid, CARD_B, env) == "nothing"
    for mode, reason in (("garbage", "unreadable"), ("fail", "exited 1")):
        stub_mode(h, mode)
        assert verify(h, sid, CARD_A, env) == "nothing", mode
        last = gate_log(h)[-1]
        assert last["decision"] == "allow" and reason in last["why"] and last["card"] == CARD_A, (mode, last)
    stub_mode(h, "json")
    assert verify(h, sid, CARD_A, no_stub) == "nothing"
    assert gate_log(h)[-1]["why"] == "agenfk not found", gate_log(h)[-1]
    # past the timeout: allow, well inside the hook's 10 s, and the stub's whole tree is gone
    stub_mode(h, "slow")
    t0 = time.time()
    assert verify(h, sid, CARD_A, env, extra={"cwd": ROOT}) == "nothing"
    took = time.time() - t0
    assert took < 8, took
    assert "took more than" in gate_log(h)[-1]["why"], gate_log(h)[-1]
    # agenfk too slow for the first card: that card fails open, the next is not waited on again and,
    # left unchecked, is denied: only the first card a call asks about may pass unchecked
    t0 = time.time()
    out = verify(h, sid, CARD_A, env, f"agenfk verify {CARD_A} && agenfk verify {CARD_B}", {"cwd": ROOT})
    took = time.time() - t0
    assert took < 6, took
    assert "ONE CARD PER CALL" in out and CARD_B in out and "took more than" in out, out
    assert "took more than" in gate_log(h)[-2]["why"] and gate_log(h)[-2]["card"] == CARD_A, gate_log(h)[-2]
    assert gate_log(h)[-2]["decision"] == "allow", gate_log(h)[-2]
    last = gate_log(h)[-1]
    assert (last["card"], last["decision"]) == (CARD_B, "deny") and "one card per call" in last["why"], last
    with open(os.path.join(h, "agenfk-kid"), encoding="utf-8") as fh:
        kid = int(fh.read())

    def alive():
        if os.name != "nt":
            try:
                os.kill(kid, 0)
            except OSError:
                return False
            return True
        # not os.kill: on Windows it is TerminateProcess, and would do the hook's job for it
        out = subprocess.run(["tasklist", "/FI", f"PID eq {kid}", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True).stdout
        return f'"{kid}"' in out
    for _ in range(50):
        if not alive():
            break
        time.sleep(0.1)
    else:
        raise AssertionError(f"the stub's child {kid} outlived the timeout")


def test_an_id_agenfk_fails_on_does_not_open_the_gate_for_the_next_card():
    """A non-zero exit or unreadable output is that id's own fail-open: the other cards in the call
    are still asked. `agenfk verify bogus && agenfk verify <REVIEW card>` used to let the second through."""
    h = home()
    env, no_stub = fake_agenfk(h, {CARD_A: "REVIEW", CARD_B: "TEST"})
    sid = "abcdef12-0136"
    bogus = "bogus-0000"                                              # agenfk get: "No item found", exit 1
    for first in (bogus, "00000000-dead"):
        out = verify(h, sid, CARD_A, env, f"agenfk verify {first} && agenfk verify {CARD_A} --evidence ok")
        assert "REVIEW REQUIRED" in out and CARD_A in out, (first, out)
        rows = gate_log(h)[-2:]
        assert (rows[0]["card"], rows[0]["decision"]) == (first, "allow") and "exited 1" in rows[0]["why"], rows
        assert (rows[1]["card"], rows[1]["decision"]) == (CARD_A, "deny"), rows
    assert verify(h, sid, CARD_A, env, f"agenfk verify {bogus} && agenfk verify {CARD_B}") == "nothing"
    # agenfk missing: one card fails open and is logged; a second card in the same call is not let through
    assert verify(h, sid, CARD_A, no_stub) == "nothing"
    last = gate_log(h)[-1]
    assert (last["card"], last["decision"], last["why"]) == (CARD_A, "allow", "agenfk not found"), last
    out = verify(h, sid, CARD_A, no_stub, f"agenfk verify {CARD_B} && agenfk verify {CARD_A}")
    assert "ONE CARD PER CALL" in out and CARD_A in out and "agenfk not found" in out, out
    rows = gate_log(h)[-2:]
    assert (rows[0]["card"], rows[0]["decision"]) == (CARD_B, "allow"), rows
    assert (rows[1]["card"], rows[1]["decision"]) == (CARD_A, "deny"), rows
    # what needs no agenfk still passes after it: a word that is not an id. A card with a reviewer record
    # no longer does (8354de87 review): the record does not say which step it was for, so a later card
    # needs agenfk like any other; as the first card of a call it still fails open on the record
    reviewer(h, sid, f"Review card {CARD_A}, branch feat/a", env)
    assert verify(h, sid, CARD_A, no_stub, f'agenfk verify {CARD_B} && agenfk verify "$X"') == "nothing"
    out = verify(h, sid, CARD_A, no_stub, f'agenfk verify {CARD_B} && agenfk verify {CARD_A} && agenfk verify "$X"')
    assert "ONE CARD PER CALL" in out and CARD_A in out and "agenfk not found" in out, out
    assert verify(h, sid, CARD_A, no_stub, f'agenfk verify {CARD_A} && agenfk verify "$X"') == "nothing"


def test_review_memory_expires_and_the_owner_lets_one_card_through_once():
    h = home()
    env, _ = fake_agenfk(h, {CARD_A: "REVIEW", CARD_B: "ADVERSARIAL"})
    sid = "abcdef12-0141"
    folder = os.path.join(h, ".claude", "squad", "reviews")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, f"{CARD_A}.json"), "w", encoding="utf-8") as fh:     # a review from 15 days ago
        json.dump({"card": CARD_A, "dispatches": [{"ts": time.time() - 15 * 86400, "session": "old"}]}, fh)
    assert "REVIEW REQUIRED" in verify(h, sid, CARD_A, env)           # default memory: 14 days
    assert verify(h, sid, CARD_A, dict(env, SQUAD_REVIEW_MEMORY_DAYS="30")) == "nothing"
    owner = dict(env, SQUAD_SELF_REVIEW_OK=CARD_B)
    assert "REVIEW REQUIRED" in verify(h, sid, CARD_A, owner)         # the override names one card only
    assert verify(h, sid, CARD_B, owner) == "nothing"
    last = gate_log(h)[-1]
    assert (last["card"], last["decision"], last["why"]) == (CARD_B, "allow", "SQUAD_SELF_REVIEW_OK"), last
    out = verify(h, "abcdef12-0142", CARD_B, owner)                   # once: not again, not from another session
    assert "REVIEW REQUIRED" in out and "already used once" in out, out
    # a broken record neither crashes the watcher nor blocks the next reviewer from counting
    with open(os.path.join(folder, f"{CARD_A}.json"), "w", encoding="utf-8") as fh:
        json.dump({"card": CARD_A, "dispatches": 5}, fh)
    assert "REVIEW REQUIRED" in verify(h, sid, CARD_A, env)
    reviewer(h, sid, f"Review card {CARD_A}, branch feat/a", env)
    assert verify(h, sid, CARD_A, env) == "nothing"


def test_a_reviewer_dispatch_unlocks_one_review_step_not_the_next_one():
    """November 2026: one reviewer dispatch let REVIEW, ADVERSARIAL, HEAVY_REVIEW and every re-review
    after FIX_FIRST through for 14 days. A dispatch now counts only when it is newer than the card's
    entry into the step it is on, read from the history `agenfk get --json` returns."""
    h = home()
    env, _ = fake_agenfk(h, {CARD_A: "IN_PROGRESS", CARD_B: "IN_PROGRESS"})
    sid = "abcdef12-0145"
    move(h, CARD_A, "REVIEW")
    reviewer(h, sid, f"Review card {CARD_A}, branch feat/a", env)
    assert verify(h, sid, CARD_A, env) == "nothing"                 # the dispatch on this step unlocks it
    # the next review step: the same dispatch no longer counts
    move(h, CARD_A, "ADVERSARIAL")
    out = verify(h, sid, CARD_A, env)
    assert "REVIEW REQUIRED" in out and "on ADVERSARIAL since" in out and "came before that" in out, out
    assert "each re-review after FIX_FIRST" in out and "`verifier`" in out, out
    last = gate_log(h)[-1]
    assert (last["card"], last["step"], last["decision"]) == (CARD_A, "ADVERSARIAL", "deny"), last
    assert last["why"] == "no reviewer dispatch since the card entered the step", last
    reviewer(h, sid, f"Adversarial review of card {CARD_A}, branch feat/a", env, agent_type="adversarial-verifier")
    assert verify(h, sid, CARD_A, env) == "nothing"
    move(h, CARD_A, "HEAVY_REVIEW")
    assert verify(h, sid, CARD_A, env) == "nothing"                 # not in the default steps
    named = dict(env, SQUAD_REVIEW_STEPS="REVIEW,ADVERSARIAL,HEAVY_REVIEW")
    assert "REVIEW REQUIRED" in verify(h, sid, CARD_A, named)       # one dispatch does not unlock the next step
    # FIX_FIRST: the card goes back to the implementer and returns to REVIEW; the old dispatches do not count
    move(h, CARD_A, "IN_PROGRESS")
    assert verify(h, sid, CARD_A, env) == "nothing"                 # not a review step
    move(h, CARD_A, "REVIEW")
    assert "REVIEW REQUIRED" in verify(h, sid, CARD_A, env)
    reviewer(h, "abcdef12-0146", f"Re-review card {CARD_A[:8]} after the fix, branch feat/a", env)
    assert verify(h, sid, CARD_A, env) == "nothing"                 # a new dispatch, from any session
    # a reviewer dispatched before the card entered the review step does not count for it either
    reviewer(h, sid, f"Review card {CARD_B}, branch feat/b", env)
    move(h, CARD_B, "REVIEW")
    assert "REVIEW REQUIRED" in verify(h, sid, CARD_B, env)


def test_records_from_before_the_step_check_neither_lock_out_nor_unlock_every_step():
    """The records #59 wrote carry no step: {"card", "dispatches": [{"ts", "session", "kind"}]}. They
    are read as they are, and their time is compared with the card's entry into its step."""
    h = home()
    env, no_stub = fake_agenfk(h, {CARD_A: "IN_PROGRESS"})
    sid = "abcdef12-0147"
    folder = os.path.join(h, ".claude", "squad", "reviews")
    os.makedirs(folder, exist_ok=True)
    old = os.path.join(folder, f"{CARD_A}.json")
    with open(old, "w", encoding="utf-8") as fh:            # a dispatch an hour ago, in the format #59 wrote
        json.dump({"card": CARD_A, "dispatches": [{"ts": time.time() - 3600, "session": "s59",
                                                    "kind": "verifier"}]}, fh)
    move(h, CARD_A, "REVIEW", ago=7200)   # the card has been on REVIEW for two hours: the dispatch was for it
    assert verify(h, sid, CARD_A, env) == "nothing"
    move(h, CARD_A, "ADVERSARIAL", ago=600)   # it moved on after the dispatch: that step needs its own
    assert "REVIEW REQUIRED" in verify(h, sid, CARD_A, env)
    reviewer(h, sid, f"Review card {CARD_A}, branch feat/a", env)   # not locked out: a new dispatch unlocks it
    assert verify(h, sid, CARD_A, env) == "nothing"
    with open(old, encoding="utf-8") as fh:
        assert [d["session"] for d in json.load(fh)["dispatches"]] == ["s59", sid]   # the same file, appended
    # fail-open is unchanged: with agenfk missing or without a history, a recorded dispatch still counts
    move(h, CARD_A, "HEAVY_REVIEW")
    env = dict(env, SQUAD_REVIEW_STEPS="REVIEW,ADVERSARIAL,HEAVY_REVIEW")   # HEAVY_REVIEW is gated only when named
    no_stub = dict(no_stub, SQUAD_REVIEW_STEPS=env["SQUAD_REVIEW_STEPS"])
    assert "REVIEW REQUIRED" in verify(h, sid, CARD_A, env)          # control: the step is gated
    assert verify(h, sid, CARD_A, no_stub) == "nothing"
    last = gate_log(h)[-1]
    assert last["decision"] == "allow" and last["why"].startswith("agenfk not found"), last
    stub_mode(h, "nohistory")
    assert verify(h, sid, CARD_A, env) == "nothing"
    last = gate_log(h)[-1]
    assert (last["decision"], last["step"]) == ("allow", "HEAVY_REVIEW") and "no step history" in last["why"], last
    # ...and without a history nor any record, the step is still gated
    stub_mode(h, "json")
    os.remove(old)
    stub_mode(h, "nohistory")
    assert "REVIEW REQUIRED" in verify(h, sid, CARD_A, env)


def test_stale_state_is_cleaned_with_a_folder_in_it():
    """SubagentStop drops state older than 2 days. A folder there (the review records lived in
    watch/reviews/ once) used to raise on os.remove and end the loop, leaving every file behind."""
    h = home()
    env, _ = fake_agenfk(h, {CARD_A: "REVIEW"})
    sid = "abcdef12-0151"
    reviewer(h, sid, f"Review card {CARD_A}, branch feat/a", env)
    folder = os.path.join(h, ".claude", "squad", "watch")
    assert not os.path.exists(os.path.join(folder, "reviews"))       # the records live outside the state
    assert os.path.exists(os.path.join(h, ".claude", "squad", "reviews", f"{CARD_A}.json"))
    old = time.time() - 3 * 86400
    sub = session(h, sid + "-sub", 60_000, 0.0)
    run(h, "PostToolUse", sid, os.path.join(h, f"{sid}.jsonl"), {"agent_id": "b1", "agent_transcript_path": sub})
    assert os.path.exists(os.path.join(folder, f"{sid}.b1.json"))      # the sub-session has a state file to drop
    stale = []

    def stale_file():                     # names on both sides of "reviews": NTFS lists by name, and a session id
        n = len(stale)                    # (hex) would always come before the folder there
        path = os.path.join(folder, f"{'0000' if n % 2 else 'zzzz'}{n:04d}-dead.a{n}.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{}")
        os.utime(path, (old, old))
        stale.append(path)

    def after_folder():                   # stale files the cleanup lists after the folder: the ones it used to keep
        names = os.listdir(folder)
        return [n for n in names[names.index("reviews") + 1:] if "-dead.a" in n]
    for _ in range(20):                   # files before and after the folder: listing order is the filesystem's
        stale_file()                      # (tmpfs: newest first; ext4: by hash; NTFS: by name)
    os.makedirs(os.path.join(folder, "reviews"))
    os.utime(os.path.join(folder, "reviews"), (old, old))
    while len(stale) < 40 or (not after_folder() and len(stale) < 200):
        stale_file()
    assert after_folder(), os.listdir(folder)
    run(h, "SubagentStop", sid, os.path.join(h, f"{sid}.jsonl"),
        {"agent_id": "b1", "agent_type": "developer", "agent_transcript_path": sub})
    left = [f for f in stale if os.path.exists(f)]
    assert not left, f"{len(left)}/{len(stale)} stale state files left"
    assert not os.path.exists(os.path.join(folder, f"{sid}.b1.json"))
    assert os.path.isdir(os.path.join(folder, "reviews"))               # a folder is not the cleanup's to remove
    assert verify(h, sid, CARD_A, env) == "nothing"                      # the record survived the cleanup


def test_many_cards_in_one_call_are_all_checked_or_denied():
    h = home()
    tests = [f"22222222-0000-4000-8000-00000000000{i}" for i in range(1, 5)]
    env, _ = fake_agenfk(h, dict({c: "TEST" for c in tests}, **{CARD_A: "REVIEW"}), mode="delay:0.8")
    sid = "abcdef12-0161"
    cmd = " && ".join(f"agenfk verify {c}" for c in tests + [CARD_A])
    # 4 cards at 0.8 s and the REVIEW card last: it is still asked, and denied
    out = verify(h, sid, CARD_A, env, cmd, {"cwd": ROOT})
    assert "REVIEW REQUIRED" in out and CARD_A in out, out
    # an agenfk that answers, but too slowly for every card in one call: the card left unasked is denied,
    # never let through, and the hook stays inside its 10 s
    stub_mode(h, "delay:1.9")
    t0 = time.time()
    out = verify(h, sid, CARD_A, env, cmd, {"cwd": ROOT})
    took = time.time() - t0
    assert "ONE CARD PER CALL" in out and "advance one card per call" in out, out
    assert took < 9.5, took
    last = gate_log(h)[-1]
    assert last["decision"] == "deny" and "one card per call" in last["why"], last
    # the REVIEW card itself is the one agenfk runs out of time on, after answering for the others
    out = verify(h, sid, CARD_A, env, " && ".join(f"agenfk verify {c}" for c in tests[:3] + [CARD_A]), {"cwd": ROOT})
    assert "ONE CARD PER CALL" in out and "then failed for this one" in out, out
    assert verify(h, sid, CARD_A, env, f"agenfk verify {tests[0]}", {"cwd": ROOT}) == "nothing"   # one card: fine


def test_what_is_not_a_card_id_never_reaches_agenfk():
    h = home()
    env, _ = fake_agenfk(h, {CARD_A: "REVIEW"})
    sid = "abcdef12-0171"
    calls = len(stub_calls(h))
    for cmd in ('agenfk verify "$ID" --evidence ok', "agenfk verify '--json' --evidence ok"):
        assert verify(h, sid, CARD_A, env, cmd) == "nothing", cmd
    for item in ("--", "--help", "$(id)"):
        out = said(run(h, "PreToolUse", sid, "", {"tool_name": "mcp__agenfk__validate_progress",
                                                 "tool_input": {"itemId": item, "evidence": "x"}}, env))
        assert out == "nothing", (item, out)
    assert len(stub_calls(h)) == calls, stub_calls(h)
    rows = [r for r in gate_log(h) if r["why"] == "not a card id"]
    assert len(rows) >= 3 and all(r["decision"] == "allow" for r in rows), gate_log(h)


def test_every_verify_the_gate_lets_through_unread_is_logged_and_wrapped_forms_are_read():
    h = home()
    env, _ = fake_agenfk(h, {CARD_A: "REVIEW"})
    sid = "abcdef12-0181"
    # forms that put agenfk in command position behind a keyword or a wrapper with options
    for cmd in (f"timeout 30 agenfk verify {CARD_A}", f"timeout -s KILL 5 agenfk verify {CARD_A}",
                f"nice -n 5 agenfk verify {CARD_A}", f"env -i FOO=1 agenfk verify {CARD_A}",
                f"env -u HOME agenfk verify {CARD_A}", f"sudo -u root agenfk verify {CARD_A}",
                f"npx -y agenfk verify {CARD_A}", f"npx agenfk@latest verify {CARD_A}",
                f"if true; then agenfk verify {CARD_A}; fi", f"while true; do agenfk verify {CARD_A}; done",
                f"if false; then :; else agenfk verify {CARD_A}; fi",
                f"if false; then :; elif agenfk verify {CARD_A}; then :; fi",
                f"until agenfk verify {CARD_A}; do :; done", f"{{ agenfk verify {CARD_A}; }}",
                f"! agenfk verify {CARD_A}", f"echo {CARD_A} | xargs -I ID agenfk verify {CARD_A}"):
        assert "REVIEW REQUIRED" in verify(h, sid, CARD_A, env, cmd), cmd
    # agenfk and verify in the command and no id read: it passes, and the pass is a row in the log
    for cmd in (f"agenfk verify {CARD_A} --plan", f'git commit -m "agenfk verify {CARD_A}"',
                f'eval "agenfk verify {CARD_A}"'):
        n = len(gate_log(h))
        assert verify(h, sid, CARD_A, env, cmd) == "nothing", cmd
        assert len(gate_log(h)) == n + 1 and gate_log(h)[-1]["decision"] == "allow", (cmd, gate_log(h)[-1])
        assert "no card id read" in gate_log(h)[-1]["why"], gate_log(h)[-1]
    n = len(gate_log(h))
    assert verify(h, sid, CARD_A, env, "ls -la") == "nothing" and len(gate_log(h)) == n   # the hot path logs nothing


def test_mcp_dry_run_passes_and_the_deprecated_advancing_tools_are_gated():
    h = home()
    env, _ = fake_agenfk(h, {CARD_A: "REVIEW"})
    sid = "abcdef12-0191"

    def mcp(tool, inp):
        return said(run(h, "PreToolUse", sid, "", {"tool_name": tool, "tool_input": inp}, env))
    calls = len(stub_calls(h))
    assert mcp("mcp__agenfk__validate_progress", {"itemId": CARD_A, "plan": True}) == "nothing"
    assert len(stub_calls(h)) == calls                                # a dry run does not even ask agenfk
    assert "REVIEW REQUIRED" in mcp("mcp__agenfk__validate_progress", {"itemId": CARD_A, "plan": False})
    for tool in ("mcp__agenfk__review_changes", "mcp__agenfk__test_changes"):
        out = mcp(tool, {"itemId": CARD_A, "evidence": "x"})
        assert "REVIEW REQUIRED" in out, (tool, out)
    assert mcp("mcp__agenfk__get_item", {"itemId": CARD_A}) == "nothing"
    assert mcp("mcp__other__my_review_changes", {"itemId": CARD_A}) == "nothing"   # only the whole tool name


def test_no_session_writes_the_review_records_with_a_tool():
    h = home()
    env, _ = fake_agenfk(h, {CARD_A: "REVIEW"})
    sid = "abcdef12-0201"
    folder = os.path.join(h, ".claude", "squad", "reviews")
    record = os.path.join(folder, f"{CARD_A}.json")
    body = json.dumps({"card": CARD_A, "dispatches": [{"ts": time.time(), "session": "me"}]})

    def call(tool, inp, agent=None, cwd=None):
        extra = {"tool_name": tool, "tool_input": inp}
        if agent:
            extra["agent_id"] = agent
        if cwd:
            extra["cwd"] = cwd
        return said(run(h, "PreToolUse", sid, "", extra, env))
    for agent in (None, "a1"):                                        # the main session and a sub-session
        for tool, inp in (("Write", {"file_path": record, "content": body}),
                          ("Edit", {"file_path": record, "old_string": "a", "new_string": "b"}),
                          ("MultiEdit", {"file_path": os.path.join(folder, "x", "..", "y.json"), "edits": []})):
            out = call(tool, inp, agent)
            assert "REVIEW RECORD" in out and "verifier" in out, (tool, agent, out)
        for cmd in (f"echo '{body}' > ~/.claude/squad/reviews/{CARD_A}.json",
                    f"mkdir -p {sh(folder)} && cp /tmp/x {sh(record)}",
                    f'python3 -c "open(\'{sh(record)}\', \'w\')"',
                    "find ~/.claude/squad/reviews -delete", "rm -rf $HOME/.claude/squad/reviews",
                    # readers that can also write a file, and find's other writing action
                    f"sort -o ~/.claude/squad/reviews/{CARD_A}.json /tmp/x",
                    f"uniq /tmp/x ~/.claude/squad/reviews/{CARD_A}.json",
                    f"tree -o ~/.claude/squad/reviews/{CARD_A}.json /tmp",
                    f"less ~/.claude/squad/reviews/{CARD_A}.json", f"more ~/.claude/squad/reviews/{CARD_A}.json",
                    f"find /tmp -maxdepth 0 -fprint0 ~/.claude/squad/reviews/{CARD_A}.json",
                    # a redirection with `&` into a file is still a redirection into a file
                    f"echo x >& ~/.claude/squad/reviews/{CARD_A}.json",
                    f"echo x &> ~/.claude/squad/reviews/{CARD_A}.json",
                    f"echo x >&~/.claude/squad/reviews/{CARD_A}.json"):
            out = call("Bash", {"command": cmd}, agent)
            assert "REVIEW RECORD" in out, (cmd, agent, out)
        assert "REVIEW RECORD" in call("Bash", {"command": f"touch {CARD_A}.json"}, agent, cwd=folder)
        for cmd in (f"echo x >& reviews/{CARD_A}.json", f"echo x &> reviews/{CARD_A}.json"):
            out = call("Bash", {"command": cmd}, agent, cwd=os.path.dirname(folder))
            assert "REVIEW RECORD" in out, (cmd, agent, out)
    assert not os.path.exists(record)
    # reading passes, and so do writes anywhere else
    for tool, inp in (("Read", {"file_path": record}),
                      ("Bash", {"command": "cat ~/.claude/squad/reviews/*.json 2>/dev/null | head"}),
                      ("Bash", {"command": f"ls -la {sh(folder)} && jq . {sh(record)} 2>&1"}),
                      ("Bash", {"command": f"cat {sh(record)} >/dev/null 2>&1 || echo none >&2"}),
                      ("Bash", {"command": f"grep -c ts {sh(record)} 1>&- &>/dev/null"}),
                      ("Write", {"file_path": os.path.join(h, "reviews", "x.json"), "content": "{}"}),
                      ("Bash", {"command": f"echo x > {sh(os.path.join(h, 'reviews.json'))}"})):
        assert call(tool, inp) == "nothing", (tool, inp)



def test_naming_the_review_records_in_text_is_not_a_write():
    """f93d1785 review: a commit message or a card comment that only mentions the records' path was denied
    as a write, and the message told the session to dispatch the verifier, which was not the way forward."""
    h = home()
    env, _ = fake_agenfk(h, {CARD_A: "TEST"})
    sid = "abcdef12-0211"
    rec = "~/.claude/squad/reviews"

    def bash(cmd, cwd=None):
        extra = {"tool_name": "Bash", "tool_input": {"command": cmd}}
        if cwd:
            extra["cwd"] = cwd
        return said(run(h, "PreToolUse", sid, "", extra, env))
    for cmd in (f'git commit -m "Keep {rec}/<card>.json out of reach of tools"',
                f'git -C {sh(h)} commit -q -am "fix: {rec}"', f'git commit --message="{rec}" && git log -1 | tee log',
                f"agenfk comment {CARD_A} 'The records live in {rec}/{CARD_A}.json'",
                f'agenfk verify {CARD_A} --evidence "gate reads {rec}"',
                f'gh pr create --title "Gate" --body "records in {rec}" --base beta'):
        assert bash(cmd) == "nothing", cmd
    # text elsewhere than those values still counts, and so does a write next to the text
    for cmd in (f"python3 scripts/tidy.py {rec}", f'git commit -m "$(touch {rec}/x.json)"',
                f'git commit -m "{rec}" > {rec}/x.json', f"git worktree add -d {rec}/x",
                f'agenfk comment {CARD_A} "x"; touch {rec}/x.json', f"cd {rec} && git commit -m wip"):
        out = bash(cmd)
        assert "REVIEW RECORD" in out, (cmd, out)
        assert "this command names the review records (" in out and "reading is fine, writing is the watcher's" in out, out
        assert "Where the work continues: dispatch the verifier" not in out, out
    out = bash(f"git commit -m {CARD_A}", cwd=os.path.join(h, ".claude", "squad", "reviews"))
    assert "REVIEW RECORD" in out, out                               # the directory is the records: no text exemption
    # a write with a tool says "call", not "command"
    out = said(run(h, "PreToolUse", sid, "", {"tool_name": "Write", "tool_input": {
        "file_path": os.path.join(h, ".claude", "squad", "reviews", "x.json"), "content": "{}"}}, env))
    assert "this call names the review records" in out, out


def test_a_megabyte_command_is_read_inside_the_hook_budget():
    """f93d1785 review: verify_ids ran shlex over the whole command and shlex is superlinear: 1.1 MB
    took 21.9 s, past the hook's 10 s. Only the first PARSE_MAX characters are parsed now; the rest is
    searched for `agenfk verify <id>`, so padding does not hide one, and the cut is logged."""
    h = home()
    env, _ = fake_agenfk(h, {CARD_A: "REVIEW", CARD_B: "TEST"})
    sid = "abcdef12-0221"
    pad = "word " * 220_000                                           # 1.1 MB inside one quoted argument
    for cmd in (f'agenfk verify {CARD_A} --evidence "{pad}"', f'true "{pad}"; agenfk verify {CARD_A}'):
        t0 = time.time()
        out = verify(h, sid, CARD_A, env, cmd)
        took = time.time() - t0
        assert "REVIEW REQUIRED" in out, out[:300]
        assert took < 5, took
        cut = [r for r in gate_log(h) if r["decision"] == "cut"]
        assert cut and "1100" in cut[-1]["why"] and "65536" in cut[-1]["why"], gate_log(h)[-2:]
    t0 = time.time()
    assert verify(h, sid, CARD_B, env, f'agenfk verify {CARD_B} --evidence "{pad}"') == "nothing"
    assert time.time() - t0 < 5
    # the record guard too: a megabyte naming the records is decided in time (denied: it is too long to read)
    t0 = time.time()
    out = said(run(h, "PreToolUse", sid, "", {"tool_name": "Bash", "tool_input": {
        "command": f'cat ~/.claude/squad/reviews/x.json "{pad}"'}}, env))
    assert "REVIEW RECORD" in out and time.time() - t0 < 5, out[:200]
    # the fallback for a command shlex cannot read (an open quote) is linear too: `\S*/` took 3 s on 64 KB
    w = watch_module()
    t0 = time.time()
    assert w.verify_ids(";a" * 32_768 + "'") is None
    assert time.time() - t0 < 1, time.time() - t0
    assert w.verify_ids(f"x; /usr/bin/agenfk verify {CARD_A} '") == [CARD_A]   # a path before agenfk still reads


def test_a_padded_verify_past_the_cap_is_not_let_through():
    """8354de87 review: past the 64 KB cap the rest of a command was only searched for the plain
    `agenfk verify <id>`, so an option or a quote before the id, a continuation, a quote cut open at the
    cap or a run of blanks across it let a verify through unchecked (beta denied every one). Each
    `agenfk ... verify` in the whole command must now be matched by a card id that was read."""
    h = home()
    env, _ = fake_agenfk(h, {CARD_A: "REVIEW", CARD_B: "TEST"})
    sid = "abcdef12-0231"
    w = watch_module()
    pad = "word " * 16_000                                            # 80 KB inside one quoted argument
    lead = "true " + "a" * (w.PARSE_MAX - 400) + "; agenfk"           # agenfk well before the cap...
    for cmd in (f'true "{pad}"; agenfk verify --evidence ok {CARD_A}',
                f'true "{pad}"; agenfk verify "{CARD_A}"',
                f'true "{pad}"; agenfk verify \\\n{CARD_A}',
                f'agenfk verify --evidence "{pad}" {CARD_A}',
                lead + " " * 500 + f"verify {CARD_A}",                # ...and its verify past it
                # a card that was read does not cover one that was not
                f'agenfk verify {CARD_B}; true "{pad}"; agenfk verify --evidence ok {CARD_A}'):
        assert len(cmd) > w.PARSE_MAX
        calls = len(stub_calls(h))
        t0 = time.time()
        out = verify(h, sid, CARD_A, env, cmd)
        assert time.time() - t0 < 5
        assert "VERIFY NOT READ" in out and "run `agenfk verify <id>` on its own" in out, (cmd[-80:], out[:300])
        assert len(stub_calls(h)) == calls                            # decided without asking agenfk
        last = gate_log(h)[-1]
        assert last["decision"] == "deny" and "card ids read" in last["why"], last
    # what was let through before still is: a verify whose id is read, past the cap or before it
    assert verify(h, sid, CARD_B, env, f'agenfk verify {CARD_B} --evidence "{pad}"') == "nothing"
    assert verify(h, sid, CARD_B, env, f'true "{pad}"; agenfk verify {CARD_B} --evidence ok') == "nothing"
    assert "REVIEW REQUIRED" in verify(h, sid, CARD_A, env, f'true "{pad}"; agenfk verify {CARD_A}')
    assert verify(h, sid, CARD_B, env, f'true "{pad}"; agenfk get {CARD_B} | grep verify') == "nothing"
    assert w.verify_mentions(f"agenfk get x; verify; agenfk  \\\n --x verify y | agenfk\nverify") == 1


def test_newline_and_separator_runs_are_read_in_linear_time():
    r"""8354de87 review: VERIFY_LOOSE took `\s*` after a separator, so every newline of a run scanned the
    rest of it (24,000 newlines in a heredoc shlex cannot read: 10.3 s). TURN_CLI had the `\S*/` the
    verify fallback lost earlier: 100 KB of `;a` took 9.6 s in turn_kind, before either guard ran."""
    w = watch_module()
    cmd = "cat <<'EOF' >x\ndon't" + "\n" * 24_000 + f"EOF\nagenfk verify {CARD_A}"
    t0 = time.time()
    assert w.verify_ids(cmd) == [CARD_A]                              # the fallback: shlex fails on don't
    assert time.time() - t0 < 2, time.time() - t0
    t0 = time.time()
    assert w.turn_kind({"tool_name": "Bash", "tool_input": {"command": ";a" * 50_000}}) is None
    assert time.time() - t0 < 2, time.time() - t0
    t0 = time.time()
    assert w.turn_kind({"tool_name": "Bash", "tool_input": {"command": "\n" * 50_000 + "x"}}) is None
    assert time.time() - t0 < 2, time.time() - t0
    for cmd, verb in (("x; ~/.claude/bin/squad-turn request 12", "request"), ("\n\n  squad-turn.py status", "status"),
                      ("cd x && /opt/s/squad-turn release", "release")):
        assert w.turn_kind({"tool_name": "Bash", "tool_input": {"command": cmd}})[:2] == ("cli", verb), cmd
    assert w.verify_ids(f"x\n\n\t /usr/bin/agenfk verify {CARD_A} '") == [CARD_A]


def test_a_comment_line_does_not_hide_a_verify_or_a_write():
    """8354de87 review (on beta too): the lines were joined with " ; " before shlex read them, and
    shlex drops everything after a `#`, so a comment line hid every line after it from the review gate
    and from the record guard."""
    h = home()
    env, _ = fake_agenfk(h, {CARD_A: "REVIEW"})
    sid = "abcdef12-0241"
    rec = "~/.claude/squad/reviews"

    def bash(cmd):
        return said(run(h, "PreToolUse", sid, "", {"tool_name": "Bash", "tool_input": {"command": cmd}}, env))
    for cmd in (f"# note\nagenfk verify {CARD_A}", f"echo hi # note\nagenfk verify {CARD_A} --evidence ok"):
        out = bash(cmd)
        assert "REVIEW REQUIRED" in out and CARD_A in out, (cmd, out)
    for cmd in (f"# x\ncp x {rec}/F.json", f"cat {rec}/F.json # x\ntouch {rec}/F.json"):
        out = bash(cmd)
        assert "REVIEW RECORD" in out, (cmd, out)
    # a comment that only comments still reads as nothing to stop
    assert bash(f"# agenfk verify {CARD_A}") == "nothing"
    assert bash(f"cat {rec}/F.json # just reading") == "nothing"


def test_only_the_bare_git_gh_and_agenfk_have_text_values():
    """8354de87 review: the text exemption keyed on the basename, so `./agenfk comment <id> "<records>"`
    or `/tmp/x/git commit -m <records>` (any program under that name) wrote the records unchecked."""
    h = home()
    env, _ = fake_agenfk(h, {CARD_A: "TEST"})
    sid = "abcdef12-0251"
    rec = "~/.claude/squad/reviews"

    def bash(cmd):
        return said(run(h, "PreToolUse", sid, "", {"tool_name": "Bash", "tool_input": {"command": cmd}}, env))
    for cmd in (f'./agenfk comment {CARD_A} "{rec}/{CARD_A}.json"', f'/tmp/x/git commit -m "{rec}/F.json"',
                f'bin/gh pr create --body "{rec}/F.json"', f'/tmp/agenfk verify {CARD_A} --evidence "{rec}/F.json"'):
        out = bash(cmd)
        assert "REVIEW RECORD" in out, (cmd, out)
    for cmd in (f'agenfk comment {CARD_A} "{rec}/{CARD_A}.json"', f'git commit -m "{rec}/F.json"'):
        assert bash(cmd) == "nothing", cmd


def test_a_record_does_not_let_a_later_card_of_the_call_through_unchecked():
    """8354de87 review: once the hook's 7 s were spent, or agenfk broke on a card after the first, a
    later card with any reviewer record passed without its step being read, so a dispatch made for an
    earlier step unlocked the next one. Only the first card of a call may fail open."""
    h = home()
    tests = [f"33333333-0000-4000-8000-00000000000{i}" for i in range(1, 4)]
    env, _ = fake_agenfk(h, dict({c: "TEST" for c in tests}, **{CARD_A: "REVIEW"}))
    sid = "abcdef12-0261"
    reviewer(h, sid, f"Review card {CARD_A}, branch feat/a", env)
    move(h, CARD_A, "ADVERSARIAL")                                    # the record is for REVIEW, not this step
    assert "REVIEW REQUIRED" in verify(h, sid, CARD_A, env)          # control: the stale record does not count
    cases = (("slow", tests[:1], "could not be asked"),               # agenfk broke on the first card
             # answered twice, then out of time on CARD_A. Whether the third ask fails ("then failed for this
             # one") or the 7 s are already spent ("ran out") depends on how long a process takes to start and
             # be killed (Windows spends more of the budget), so the reason pinned is what both texts say.
             ("delay:2.4", tests[:2], "answered for the cards before it"))
    for mode, before, why in cases:
        stub_mode(h, mode)
        t0 = time.time()
        out = verify(h, sid, CARD_A, env, " && ".join(f"agenfk verify {c}" for c in before + [CARD_A]),
                     {"cwd": ROOT})
        assert time.time() - t0 < 9.5
        assert "ONE CARD PER CALL" in out and why in out, (mode, out)
        # which later card is left unchecked depends on the runner: with 2.4 s per answer, a slow Windows
        # runner (7 s minus 2 for the kill) runs out on the second card instead of CARD_A. What must hold
        # is that the call is denied on a card after the first, never let through.
        m = re.search(r"and card (\S+) was left unchecked", out)
        assert m and m.group(1) in before[1:] + [CARD_A], (mode, out)
        last = gate_log(h)[-1]
        assert (last["card"], last["decision"]) == (m.group(1), "deny") and "one card per call" in last["why"], last
    stub_mode(h, "slow")
    assert verify(h, sid, CARD_A, env, extra={"cwd": ROOT}) == "nothing"   # the first card still fails open
    # no time left for a later card (`wait` under 0.5 s): unchecked, record or not. Called directly: through
    # the hook this needs agenfk to answer three cards within 0.5 s of the 7 s budget, too tight to time
    saved = os.environ.get("HOME"), os.environ.get("USERPROFILE")
    os.environ["HOME"] = os.environ["USERPROFILE"] = h
    try:
        w = watch_module()
    finally:
        for key, value in zip(("HOME", "USERPROFILE"), saved):
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    assert w.review_files(CARD_A), w.REVIEWS                         # the record is there
    assert w.review_check({"session_id": sid, "cwd": ROOT}, CARD_A, 0.2, True) == ("", "unchecked", "")

# ---------------------------------------------------------------- handoff on burn and age
def main_state(h, sid, hours, idle_s=0):
    """A main session that already has `hours` of work counted, last measured `idle_s` ago."""
    d = os.path.join(h, ".claude", "squad", "watch")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, f"{sid}.main.json"), "w", encoding="utf-8") as fh:
        json.dump({"active": hours * 3600, "seen": time.time() - idle_s}, fh)


def watch_turn(ctx, env=None, hours=None, idle_s=0, sid="abcdef12-0020"):
    """One PostToolUse of a main session at `ctx` with no reread; what the watcher said."""
    h = home()
    if hours is not None:
        main_state(h, sid, hours, idle_s)
    tr = session(h, sid, ctx, 0)
    return h, kind(said(run(h, "PostToolUse", sid, tr, env=env)))


def test_burn_above_its_line_hands_off():
    # ctx 300k, nothing reread: not a handoff by context or reread, only the burn can order it
    _, got = watch_turn(300_000, {"SQUAD_MAIN_BURN": "100000"})
    assert got == "PROACTIVE HANDOFF", got


def test_burn_reason_is_in_the_message():
    h = home()
    sid = "abcdef12-0021"
    tr = session(h, sid, 300_000, 0)
    text = said(run(h, "PostToolUse", sid, tr, env={"SQUAD_MAIN_BURN": "100000"}))
    assert "PROACTIVE HANDOFF" in text and "weighted >= 100k" in text, text


def test_burn_below_its_line_does_not_hand_off():
    _, got = watch_turn(300_000)           # default line is 15M
    assert got != "PROACTIVE HANDOFF", got


def test_burn_exactly_on_its_line_hands_off():
    h = home()
    sid = "abcdef12-0022"
    tr = session(h, sid, 300_000, 0)
    run(h, "PostToolUse", sid, tr)
    burn = state(h, sid)["burn"]
    assert burn > 0
    for line, handoff in ((burn, True), (burn + 1, False)):
        h2 = home()
        tr2 = session(h2, sid, 300_000, 0)
        got = kind(said(run(h2, "PostToolUse", sid, tr2, env={"SQUAD_MAIN_BURN": str(line)})))
        assert (got == "PROACTIVE HANDOFF") == handoff, (line, got)


def test_a_small_context_is_left_alone_whatever_the_burn_or_age():
    _, got = watch_turn(100_000, {"SQUAD_MAIN_BURN": "1"}, hours=20)
    assert got != "PROACTIVE HANDOFF", got


def test_context_exactly_on_the_floor_counts():
    _, got = watch_turn(150_000, {"SQUAD_MAIN_BURN": "1"})
    assert got == "PROACTIVE HANDOFF", got
    _, got = watch_turn(149_999, {"SQUAD_MAIN_BURN": "1"})
    assert got != "PROACTIVE HANDOFF", got


def test_the_floor_is_a_setting():
    _, got = watch_turn(100_000, {"SQUAD_MAIN_BURN": "1", "SQUAD_MAIN_FLOOR": "90000"})
    assert got == "PROACTIVE HANDOFF", got


def test_main_session_hands_off_after_hours_open_and_says_so():
    h = home()
    sid = "abcdef12-0023"
    main_state(h, sid, 9)
    tr = session(h, sid, 280_000, 0)
    text = said(run(h, "PostToolUse", sid, tr))
    assert "PROACTIVE HANDOFF" in text and "open 9.0 h of work >= 8 h" in text, text


def test_age_exactly_on_its_line_hands_off():
    _, got = watch_turn(280_000, hours=8)
    assert got == "PROACTIVE HANDOFF", got
    _, got = watch_turn(280_000, hours=7.9)
    assert got != "PROACTIVE HANDOFF", got


def test_age_line_is_a_setting():
    _, got = watch_turn(280_000, {"SQUAD_MAIN_AGE": "3600"}, hours=1.1)
    assert got == "PROACTIVE HANDOFF", got


def test_a_gap_over_30_minutes_is_not_work():
    # 7.9 h counted, then a night away: the gap does not push it over 8 h
    _, got = watch_turn(280_000, hours=7.9, idle_s=10 * 3600)
    assert got != "PROACTIVE HANDOFF", got
    # a gap of 31 minutes is still a gap
    _, got = watch_turn(280_000, hours=7.9, idle_s=31 * 60)
    assert got != "PROACTIVE HANDOFF", got


def test_a_gap_of_10_minutes_counts_as_work():
    _, got = watch_turn(280_000, hours=7.95, idle_s=600)
    assert got == "PROACTIVE HANDOFF", got


def test_the_work_counted_is_kept_in_the_state():
    h = home()
    sid = "abcdef12-0024"
    main_state(h, sid, 1, idle_s=600)
    tr = session(h, sid, 280_000, 0)
    run(h, "PostToolUse", sid, tr)
    assert 1 * 3600 + 590 <= state(h, sid)["active"] <= 1 * 3600 + 700


def test_a_bad_burn_age_or_floor_falls_back_to_the_default():
    bad = {"SQUAD_MAIN_BURN": "abc", "SQUAD_MAIN_AGE": "x", "SQUAD_MAIN_FLOOR": ""}
    _, got = watch_turn(300_000, bad, hours=7)
    assert got != "PROACTIVE HANDOFF", got
    _, got = watch_turn(300_000, bad, hours=9)
    assert got == "PROACTIVE HANDOFF", got


def test_handoff_reason_by_function():
    w = watch_module()
    assert w.handoff_reason(900_000, True, 0, 0) == "context"
    assert w.handoff_reason(200_000, False, 0, 0) == ""
    assert w.handoff_reason(149_999, False, w.MAIN_BURN, w.MAIN_AGE) == ""
    assert w.handoff_reason(150_000, False, w.MAIN_BURN, 0).startswith("burn ")
    assert w.handoff_reason(150_000, False, w.MAIN_BURN - 1, 0) == ""
    assert w.handoff_reason(150_000, False, 0, w.MAIN_AGE).startswith("open ")
    assert w.handoff_reason(150_000, False, 0, w.MAIN_AGE - 1) == ""
    assert (w.MAIN_BURN, w.MAIN_AGE, w.MAIN_FLOOR, w.ACTIVE_GAP) == (15_000_000, 8 * 3600, 150_000, 1800)


if __name__ == "__main__":
    # every test runs, so one failure does not hide the others; the exit code is 1 when any failed
    import traceback
    failed = []
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception:
                traceback.print_exc()
                failed.append(name)
                print("FAIL", name)
                continue
            print("ok ", name)
    if failed:
        print("failed:", " ".join(failed))
        sys.exit(1)
