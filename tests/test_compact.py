#!/usr/bin/env python3
"""squad-compact sends /compact to an idle main session just before its prompt
cache goes cold, and only when nothing has changed since the watcher scheduled it.

Self-checking: the exit code is the verdict. Runs in a throwaway HOME, with a
fake herdr, and never touches ~/.claude.

    python3 tests/test_compact.py
"""
import atexit
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time


if os.name == "nt":
    # squad-compact types into a herdr pane and there is no herdr on Windows (docs/installation.md);
    # the fake herdr here is a script with a shebang
    print("skipped: squad-compact needs herdr, which does not run on Windows")
    sys.exit(0)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WATCH = os.path.join(ROOT, "hooks", "squad-watch.py")
COMPACT = os.path.join(ROOT, "hooks", "squad-compact.py")


_HOMES = []


def home():
    h = tempfile.mkdtemp(prefix="compact-")
    _HOMES.append(h)
    return h


@atexit.register
def _remove_homes():
    # every test creates a throwaway HOME; without this /tmp (tmpfs) runs out of inodes
    # when several sessions run the suite all day long
    for h in _HOMES:
        shutil.rmtree(h, ignore_errors=True)


def squad(h, *parts):
    return os.path.join(h, ".claude", "squad", *parts)


def clean_env(h, env=None):
    e = dict(os.environ, HOME=h)
    for key in list(e):
        if key.startswith("SQUAD_") or key.startswith("HERDR_"):
            del e[key]
    e.update(env or {})
    return e


def run(h, event, sid, transcript, extra=None, env=None):
    p = {"hook_event_name": event, "session_id": sid, "transcript_path": transcript, "cwd": h}
    p.update(extra or {})
    return subprocess.run([sys.executable, WATCH], input=json.dumps(p), capture_output=True, text=True,
                          env=clean_env(h, env)).stdout.strip()


def fake_herdr(h, sid):
    """`agent get` returns the pane's session (pane-session) and status (pane-status, idle by
    default); `agent prompt` logs the text to pane-prompts, or fails while prompt-fail exists.
    What Claude Code does with a /compact is set by compact_mode()."""
    cur, sent = os.path.join(h, "pane-session"), os.path.join(h, "pane-prompts")
    with open(cur, "w") as fh:
        fh.write(sid)
    path = os.path.join(h, "herdr")
    with open(path, "w") as fh:
        fh.write(f"""#!/usr/bin/env python3
import json, os, sys
a = sys.argv[1:]
if a[:2] == ["agent", "get"]:
    try:
        status = open({os.path.join(h, "pane-status")!r}).read().strip()
    except OSError:
        status = "idle"
    print(json.dumps({{"result": {{"agent": {{"agent_session": {{"value": open({cur!r}).read()}},
                                           "agent_status": status}}}}}}))
elif a[:2] == ["agent", "prompt"]:
    if os.path.exists({os.path.join(h, "prompt-fail")!r}):
        sys.exit(1)
    open({sent!r}, "a").write(a[3] + "\\n")
    try:
        mode = json.load(open({os.path.join(h, "compact-mode")!r}))
    except (OSError, ValueError):
        mode = {{}}
    if a[3].startswith("/compact") and mode.get("mode") in ("boundary", "auto"):
        open(mode["transcript"], "a").write(json.dumps({{"type": "system", "subtype": "compact_boundary",
            "compactMetadata": {{"trigger": "manual" if mode["mode"] == "boundary" else "auto"}}}}) + "\\n")
    elif a[3].startswith("/compact") and mode.get("mode") == "paste":
        import subprocess
        pasted = '\\n\\n<pasted_content id="a795">\\n' + a[3] + '\\n</pasted_content id="a795">\\n'
        subprocess.run([sys.executable, mode["watch"]], text=True, input=json.dumps({{
            "hook_event_name": "UserPromptSubmit", "session_id": mode["sid"],
            "transcript_path": mode["transcript"], "cwd": "/", "prompt": pasted}}))
""")
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
    return path, sent


def compact_mode(h, mode, sid, transcript):
    """What the fake pane does with a /compact: "boundary" (it ran as a command: a manual
    compact_boundary in the transcript), "auto" (only an auto compaction shows up),
    "paste" (Claude Code pasted it, as above 800 characters, and it reached
    UserPromptSubmit as a message), or nothing at all."""
    write(os.path.join(h, "compact-mode"), json.dumps({"mode": mode, "sid": sid, "transcript": transcript,
                                                       "watch": WATCH}))


def u16(s):
    return len(s.encode("utf-16-le", "surrogatepass")) // 2


def module(path, name):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write(path, text):
    with open(path, "w") as fh:
        fh.write(text)


def transcript(h, sid, ctx, ttl_5m=False, last="r-last"):
    """A main session whose last response wrote 5k to the cache, on the 1 h or the 5 min TTL."""
    path = os.path.join(h, f"{sid}.jsonl")
    key = "ephemeral_5m_input_tokens" if ttl_5m else "ephemeral_1h_input_tokens"
    with open(path, "w") as fh:
        fh.write(json.dumps({"type": "assistant", "isSidechain": False, "requestId": last,
                             "message": {"usage": {"cache_read_input_tokens": ctx - 5000,
                                                   "cache_creation_input_tokens": 5000,
                                                   "cache_creation": {key: 5000}}}}) + "\n")
    return path


def wait_for(path, text, secs=40):
    for _ in range(int(secs / 0.25)):
        if os.path.exists(path):
            with open(path) as fh:
                body = fh.read()
            if text in body:
                return body
        time.sleep(0.25)
    with open(path) if os.path.exists(path) else open(os.devnull) as fh:
        raise AssertionError(f"{text!r} did not show up in {path}: {fh.read()!r}")


def lines(path):
    if not os.path.exists(path):
        return []
    with open(path) as fh:
        return fh.read().splitlines()


def soon(h, herdr):
    """Due in 1 s: SQUAD_CACHE_TTL 3 s, sent 2 s before it goes cold. It waits at most 10 s
    for the boundary: a regression that sends must not leave a process writing to a
    removed HOME for 10 minutes."""
    return {"HERDR_PANE_ID": "w1:p1", "HERDR_BIN_PATH": herdr, "SQUAD_CACHE_TTL": "3",
            "SQUAD_COMPACT_BEFORE": "2", "SQUAD_COMPACT_CONFIRM": "10"}


def job(h, sid, tr, text="/compact keep the plan", req="r-last", due=0.0, st=None):
    """A job written by hand, as the watcher writes it, with the watcher's state it reads
    (`st`; False leaves the state as it is)."""
    if st is not False:
        os.makedirs(squad(h, "watch"), exist_ok=True)
        write(squad(h, "watch", f"{sid}.main.json"), json.dumps(st or {}))
    os.makedirs(squad(h, "compact"), exist_ok=True)
    path = squad(h, "compact", f"{sid}.json")
    with open(path, "w") as fh:
        json.dump({"due": due, "req": req, "pane": "w1:p1", "session": sid, "transcript": tr,
                   "ctx": 300_000, "ttl": 3600, "text": text}, fh)
    return path


def compact(h, herdr, *args, confirm="2"):
    return subprocess.run([sys.executable, COMPACT, *args], capture_output=True, text=True, timeout=60,
                          env=clean_env(h, {"HERDR_BIN_PATH": herdr,
                                            "SQUAD_COMPACT_CONFIRM": confirm})).returncode


def test_compacts_before_the_cache_goes_cold_with_what_to_keep():
    h = home()
    sid = "c0c0c0c0-0001"
    herdr, sent = fake_herdr(h, sid)
    env = soon(h, herdr)
    tr = transcript(h, sid, 300_000)
    compact_mode(h, "boundary", sid, tr)
    run(h, "UserPromptSubmit", sid, tr, {"prompt": "deliver cards ABC-01c in parallel"}, env)
    assert run(h, "Stop", sid, tr, env=env) == ""
    log = squad(h, "compactions.log")
    assert "/compact sent (300k of context, 0 min cache)" in wait_for(log, "/compact sent"), lines(log)
    assert "compacted: manual compact_boundary in the transcript" in wait_for(log, "compacted:"), lines(log)
    prompts = [p for p in lines(sent) if p.startswith("/compact ")]
    assert len(prompts) == 1 and len(lines(sent)) == 1, lines(sent)
    assert u16(prompts[0]) <= 760, (u16(prompts[0]), prompts[0])
    for must in ("owner's current request", "branch, worktree", "DONE with evidence", "background tasks",
                 "UNKNOWN", "check git status and the card", "deliver cards ABC-01c in parallel"):
        assert must in prompts[0], (must, prompts[0])
    with open(squad(h, "compact", f"{sid}.sent")) as fh:
        assert json.load(fh)["req"] == "r-last"
    for _ in range(40):                                 # the job is removed once sent
        if not os.path.exists(squad(h, "compact", f"{sid}.json")):
            break
        time.sleep(0.25)
    assert not os.path.exists(squad(h, "compact", f"{sid}.json"))


def test_the_ordered_compaction_is_labelled_and_never_compacted_twice():
    """The PreCompact handoff (#10) must not read as a session that stopped without
    its handoff when it was the watcher that ordered the compaction."""
    h = home()
    sid = "c0c0c0c0-0002"
    herdr, sent = fake_herdr(h, sid)
    env = soon(h, herdr)
    tr = transcript(h, sid, 300_000)
    compact_mode(h, "boundary", sid, tr)
    run(h, "Stop", sid, tr, env=env)
    wait_for(squad(h, "compactions.log"), "compacted: manual compact_boundary")
    text = lines(sent)[0]
    # the /compact line reaches the session as a prompt: it is not the owner speaking
    run(h, "UserPromptSubmit", sid, tr, {"prompt": "pause the merge for today"}, env)
    run(h, "UserPromptSubmit", sid, tr, {"prompt": text + " pause it, call it a day"}, env)
    with open(squad(h, "watch", f"{sid}.main.json")) as fh:
        st = json.load(fh)
    assert st["last_prompt"] == "pause the merge for today", st["last_prompt"]
    out = run(h, "PreCompact", sid, tr, {"trigger": "manual", "custom_instructions": text[len("/compact "):]}, env)
    assert out == "", out
    with open(squad(h, "handoffs", f"{sid}-compact.md")) as fh:
        body = fh.read()
    assert "The watcher ordered this compaction (squad-compact, manual)" in body, body
    assert "300k of context" in body and "stopped without writing" not in body, body
    assert not os.path.exists(squad(h, "handoffs", f"{sid}-watch.md"))
    assert not st.get("switched") and not os.path.exists(squad(h, "switches.log"))
    # the session stops again with no new request: that request was already compacted
    os.remove(squad(h, "watch", f"{sid}.main.json"))   # also forgets asked_stop from the owner's prompt above
    run(h, "Stop", sid, tr, env=env)
    assert not os.path.exists(squad(h, "compact", f"{sid}.json"))


def test_gives_up_when_anything_changed():
    h = home()
    log = squad(h, "compactions.log")
    cases = [("d1d1d1d1-0001", "moved", "the session moved on"),
             ("d2d2d2d2-0002", "working", "session is working"),
             ("d3d3d3d3-0003", "other", "pane hosts session 99999999, expected d3d3d3d3"),
             ("d4d4d4d4-0004", "claim", "session at handoff or switching"),
             ("d5d5d5d5-0005", "handoff", "session at handoff or switching"),
             ("d6d6d6d6-0006", "wait", "session at handoff or switching"),
             ("d7d7d7d7-0007", "stop", "the owner asked to stop"),
             ("d8d8d8d8-0008", "switched", "session at handoff or switching"),
             ("d9d9d9d9-0009", "texted", "a /compact already arrived as a message in this session")]
    for sid, case, why in cases:
        herdr, sent = fake_herdr(h, sid)
        write(os.path.join(h, "pane-status"), "working" if case == "working" else "idle")
        tr = transcript(h, sid, 300_000)
        env = dict(soon(h, herdr), SQUAD_CACHE_TTL="4", SQUAD_COMPACT_BEFORE="2")
        run(h, "Stop", sid, tr, env=env)
        assert os.path.exists(squad(h, "compact", f"{sid}.json")), case
        state = squad(h, "watch", f"{sid}.main.json")
        if case == "moved":
            with open(tr, "a") as fh:
                fh.write(json.dumps({"type": "assistant", "requestId": "r-new",
                                     "message": {"usage": {"cache_read_input_tokens": 300_000}}}) + "\n")
        elif case == "other":
            write(os.path.join(h, "pane-session"), "99999999-0000")
        elif case == "claim":
            os.makedirs(squad(h, "switches"), exist_ok=True)
            write(squad(h, "switches", f"{sid}.claim"), "")
        elif case in ("handoff", "wait"):
            with open(state) as fh:
                st = json.load(fh)
            st.update({"ctx_level": 2} if case == "handoff" else {"switch_wait": time.time() + 600})
            write(state, json.dumps(st))
        elif case in ("switched", "texted"):
            with open(state) as fh:
                st = json.load(fh)
            st.update({"switched": True} if case == "switched" else {"compact_as_text": time.time()})
            write(state, json.dumps(st))
        elif case == "stop":
            run(h, "UserPromptSubmit", sid, tr, {"prompt": "write the handoff, I am closing for today"}, env)
        assert why in wait_for(log, f"{sid[:8]} not compacted"), (case, lines(log))
        assert not lines(sent), (case, lines(sent))
        for _ in range(40):                         # the job it gave up on is not left behind
            if not os.path.exists(squad(h, "compact", f"{sid}.json")):
                break
            time.sleep(0.25)
        assert not os.path.exists(squad(h, "compact", f"{sid}.json")), case


def test_nothing_is_scheduled_when_compacting_does_not_pay_or_is_not_ours():
    h = home()
    herdr, _ = fake_herdr(h, "c2c2c2c2-0000")
    env = {"HERDR_PANE_ID": "w1:p1", "HERDR_BIN_PATH": herdr}
    jobs = squad(h, "compact")
    # the TTL comes from the transcript: 5 min, due 3 min after the Stop
    sid = "c2c2c2c2-0001"
    run(h, "Stop", sid, transcript(h, sid, 300_000, ttl_5m=True), env=env)
    with open(os.path.join(jobs, f"{sid}.json")) as fh:
        scheduled = json.load(fh)
    assert scheduled["ttl"] == 300 and 170 < scheduled["due"] - time.time() <= 180, scheduled
    assert scheduled["req"] == "r-last" and scheduled["session"] == sid, scheduled
    scheduled["req"] = "rescheduled"                   # the process launched for it leaves on its own
    write(os.path.join(jobs, f"{sid}.json"), json.dumps(scheduled))
    sid = "c2c2c2c2-0002"                              # 1 h cache
    run(h, "Stop", sid, transcript(h, sid, 300_000), env=env)
    with open(os.path.join(jobs, f"{sid}.json")) as fh:
        scheduled = json.load(fh)
    assert scheduled["ttl"] == 3600 and 3470 < scheduled["due"] - time.time() <= 3480, scheduled
    scheduled["req"] = "rescheduled"
    write(os.path.join(jobs, f"{sid}.json"), json.dumps(scheduled))
    none = []
    sid = "c2c2c2c2-0003"                              # small context: rebuilding a cold cache costs less
    run(h, "Stop", sid, transcript(h, sid, 100_000), env=env)
    none.append(sid)
    sid = "c2c2c2c2-0004"                              # no TTL in the transcript: no telling when it goes cold
    tr = os.path.join(h, f"{sid}.jsonl")
    write(tr, json.dumps({"type": "assistant", "requestId": "r1",
                          "message": {"usage": {"cache_read_input_tokens": 300_000}}}) + "\n")
    run(h, "Stop", sid, tr, env=env)
    none.append(sid)
    sid = "c2c2c2c2-0005"                              # outside herdr
    run(h, "Stop", sid, transcript(h, sid, 300_000), env={"HERDR_BIN_PATH": herdr})
    none.append(sid)
    sid = "c2c2c2c2-0006"                              # turned off
    run(h, "Stop", sid, transcript(h, sid, 300_000), env=dict(env, SQUAD_COMPACT="0"))
    none.append(sid)
    sid = "c2c2c2c2-0007"                              # the owner asked to stop or pause
    tr = transcript(h, sid, 300_000)
    run(h, "UserPromptSubmit", sid, tr, {"prompt": "pause here, we carry on tomorrow"}, env)
    run(h, "Stop", sid, tr, env=env)
    none.append(sid)
    sid = "c2c2c2c2-0008"                              # SQUAD_COMPACT_MIN moves the threshold
    run(h, "Stop", sid, transcript(h, sid, 300_000), env=dict(env, SQUAD_COMPACT_MIN="400000"))
    none.append(sid)
    sid = "c2c2c2c2-0009"                              # another harness: /compact is Claude Code's
    run(h, "Stop", sid, transcript(h, sid, 300_000), {"client": "codex"}, env=env)
    none.append(sid)
    for sid in none:
        assert not os.path.exists(os.path.join(jobs, f"{sid}.json")), sid


def test_a_job_it_cannot_trust_is_never_sent():
    h = home()
    sid = "c3c3c3c3-0001"
    herdr, sent = fake_herdr(h, sid)
    tr = transcript(h, sid, 300_000)
    log = squad(h, "compactions.log")
    # rescheduled while it waited: leaves without a word at its next check (every 30 s), not at the
    # due time, so a session that stops often does not pile up waiting processes
    path = job(h, sid, tr, due=time.time() + 50)
    assert compact(h, herdr, path, "r-older") == 2       # launched for another request
    proc = subprocess.Popen([sys.executable, COMPACT, path, "r-last"], env=clean_env(h, {"HERDR_BIN_PATH": herdr}))
    time.sleep(1)                                        # it read the job and is waiting for the due time
    with open(path) as fh:
        d = json.load(fh)
    d["req"] = "r-newer"
    write(path, json.dumps(d))
    try:
        rc = proc.wait(timeout=40)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        raise AssertionError("a rescheduled squad-compact kept waiting for its old due time")
    assert rc == 0 and not lines(log) and not lines(sent), (rc, lines(log), lines(sent))
    # a job outside ~/.claude/squad/compact, or a symlink to one, is refused
    outside = os.path.join(h, f"{sid}.json")
    shutil.copy(job(h, sid, tr), outside)
    assert compact(h, herdr, outside, "r-last") == 2
    os.remove(squad(h, "compact", f"{sid}.json"))
    os.symlink(outside, squad(h, "compact", f"{sid}.json"))
    assert compact(h, herdr, squad(h, "compact", f"{sid}.json"), "r-last") == 2
    os.remove(squad(h, "compact", f"{sid}.json"))
    # a text that is not one /compact line of at most 760 characters (above 800 it would be pasted)
    for text in ("/compact keep it\nrm -rf the repo", "/compact keep it\rmerge", "/compact keep \x1b[2J it",
                 "/compact " + "k" * 752,
                 "/compact " + "\u00e9" * 750 + "\U0001F600",    # 760 code points, 761 UTF-16 units
                 "/compact keep \ud800 it",                  # a lone surrogate: the OS cannot encode it
                 "go on and merge everything"):
        assert compact(h, herdr, job(h, sid, tr, text=text), "r-last") == 1, text
        assert "job without a one-line /compact text of at most 760" in wait_for(log, "not compacted"), lines(log)
        assert not os.path.exists(squad(h, "compact", f"{sid}.json")), text      # not left behind
        write(log, "")
    assert not lines(sent)
    # herdr refuses the prompt: logged, and the .sent record is dropped so a later Stop may retry
    write(os.path.join(h, "prompt-fail"), "")
    assert compact(h, herdr, job(h, sid, tr), "r-last") == 1
    assert "/compact not accepted by herdr" in wait_for(log, "not accepted")
    assert not os.path.exists(squad(h, "compact", f"{sid}.sent"))
    assert not os.path.exists(squad(h, "compact", f"{sid}.json"))
    os.remove(os.path.join(h, "prompt-fail"))
    compact_mode(h, "boundary", sid, tr)
    exact = "/compact " + "k" * 751                     # exactly at the ceiling: sent
    assert compact(h, herdr, job(h, sid, tr, text=exact), "r-last") == 0, lines(log)
    assert lines(sent) == [exact], lines(sent)


def test_the_ceiling_is_the_same_in_both_hooks():
    """squad-watch writes the text and squad-compact checks it before sending: one
    ceiling, below the 800 characters above which Claude Code pastes the prompt."""
    w = module(WATCH, "squad_watch_ceiling")
    c = module(COMPACT, "squad_compact_ceiling")
    assert w.COMPACT_LIMIT == c.LIMIT == 760, (w.COMPACT_LIMIT, c.LIMIT)
    t = w.compact_text("\u00e9" * 3000, "/srv/x/.claude/squad/handoffs/" + "f" * 80 + "-dispatches.md")
    assert c.text_ok(t) and u16(t) <= 760, (u16(t), t)


def test_a_compact_that_arrives_as_a_message_is_a_failure_and_not_the_owner():
    """The real form (03/11/2026, herdr pane): a pasted /compact reaches UserPromptSubmit
    wrapped in <pasted_content>. Not the owner's request, not a request to stop (the
    briefs path says "handoffs"), not compacted; no residue, and no second try."""
    h = home()
    sid = "c0c0c0c0-0011"
    herdr, sent = fake_herdr(h, sid)
    env = dict(soon(h, herdr), SQUAD_COMPACT_CONFIRM="20")
    tr = transcript(h, sid, 300_000)
    briefs = squad(h, "handoffs", f"{sid}-dispatches.md")
    os.makedirs(os.path.dirname(briefs), exist_ok=True)
    write(briefs, "")
    compact_mode(h, "paste", sid, tr)
    run(h, "UserPromptSubmit", sid, tr, {"prompt": "deliver card ABC-03"}, env)
    run(h, "Stop", sid, tr, env=env)
    log = squad(h, "compactions.log")
    body = wait_for(log, "arrived as a message")
    assert "compacted: manual" not in body, body
    with open(squad(h, "watch", f"{sid}.main.json")) as fh:
        st = json.load(fh)
    assert st["last_prompt"] == "deliver card ABC-03" and st["asked_stop"] is False, st
    assert st.get("compact_as_text"), st
    for _ in range(40):
        if not os.path.exists(squad(h, "compact", f"{sid}.json")):
            break
        time.sleep(0.25)
    assert not os.path.exists(squad(h, "compact", f"{sid}.json"))
    assert not os.path.exists(squad(h, "compact", f"{sid}.sent"))    # it did not order any compaction
    run(h, "Stop", sid, tr, env=env)                                   # it does not insist in this session
    assert not os.path.exists(squad(h, "compact", f"{sid}.json"))


def test_without_a_manual_boundary_after_sending_it_is_not_compacted():
    """Sent is not done: an old manual boundary, or an auto one after sending, proves nothing."""
    h = home()
    sid = "c0c0c0c0-0013"
    herdr, sent = fake_herdr(h, sid)
    tr = transcript(h, sid, 300_000)
    with open(tr, "a") as fh:                       # a manual boundary from before, then the last request
        fh.write(json.dumps({"type": "system", "subtype": "compact_boundary",
                             "compactMetadata": {"trigger": "manual"}}) + "\n")
        fh.write(json.dumps({"type": "assistant", "isSidechain": False, "requestId": "r-later",
                             "message": {"usage": {"cache_read_input_tokens": 295_000}}}) + "\n")
    log = squad(h, "compactions.log")
    for mode in ("auto", "nothing"):
        compact_mode(h, mode, sid, tr)
        assert compact(h, herdr, job(h, sid, tr, req="r-later"), "r-later") == 1, mode
        body = wait_for(log, "no manual compact_boundary within 2 s")
        assert "/compact sent" in body and "compacted: manual" not in body, body
        assert not os.path.exists(squad(h, "compact", f"{sid}.sent")), mode
        assert not os.path.exists(squad(h, "compact", f"{sid}.json")), mode
        write(log, "")
    assert len(lines(sent)) == 2, lines(sent)


def test_doubt_about_the_state_or_the_transcript_does_not_compact():
    """Missing, corrupt or non-dict watcher state, or an unreadable transcript: no telling
    whether the session is switching, nor whether the /compact ran. Do not compact."""
    h = home()
    log = squad(h, "compactions.log")
    for sid, body in (("c4c4c4c4-0001", None), ("c5c5c5c5-0002", "{corrupt"), ("c6c6c6c6-0003", "[1, 2]"),
                      ("c7c7c7c7-0004", '"text"')):
        herdr, sent = fake_herdr(h, sid)
        tr = transcript(h, sid, 300_000)
        compact_mode(h, "boundary", sid, tr)
        path = job(h, sid, tr)
        state = squad(h, "watch", f"{sid}.main.json")
        if body is None:
            os.remove(state)
        else:
            write(state, body)
        assert compact(h, herdr, path, "r-last") == 1, sid
        assert "watcher state missing or unreadable" in wait_for(log, f"{sid[:8]} not compacted"), sid
        assert not lines(sent), lines(sent)
        assert not os.path.exists(path), sid
    sid = "c8c8c8c8-0005"
    herdr, sent = fake_herdr(h, sid)
    gone = os.path.join(h, "gone", f"{sid}.jsonl")
    assert compact(h, herdr, job(h, sid, gone), "r-last") == 1
    assert "transcript unreadable" in wait_for(log, f"{sid[:8]} not compacted"), lines(log)
    assert not lines(sent), lines(sent)


def test_a_job_owned_by_another_user_is_refused():
    """The job's text goes to the pane as a prompt: one written by someone else is never read."""
    h = home()
    c = module(COMPACT, "squad_compact_uid")
    path = os.path.join(h, "job.json")
    write(path, json.dumps({"req": "r-last", "text": "/compact keep the plan"}))
    assert c.load_job(path).get("req") == "r-last"
    real = os.getuid
    os.getuid = lambda: real() + 1
    try:
        assert c.load_job(path) == {}
    finally:
        os.getuid = real


def test_install_links_squad_compact():
    h = home()
    bin_dir = os.path.join(h, "bin")
    os.makedirs(bin_dir)
    write(os.path.join(bin_dir, "systemctl"), "#!/bin/sh\nexit 1\n")   # no user manager: the timer is skipped
    os.chmod(os.path.join(bin_dir, "systemctl"), 0o755)
    cfg = os.path.join(h, "claude")
    r = subprocess.run(["bash", os.path.join(ROOT, "install.sh")], capture_output=True, text=True,
                       env={"HOME": h, "CLAUDE_CONFIG_DIR": cfg, "PATH": f"{bin_dir}:/usr/bin:/bin"})
    link = os.path.join(cfg, "hooks", "squad-compact.py")
    assert os.path.islink(link) and os.path.realpath(link) == os.path.realpath(COMPACT), r.stdout + r.stderr


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
