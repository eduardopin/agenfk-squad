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
    default); `agent prompt` logs the text to pane-prompts, or fails while prompt-fail exists."""
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
""")
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
    return path, sent


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
    """Due in 1 s: SQUAD_CACHE_TTL 3 s, sent 2 s before it goes cold."""
    return {"HERDR_PANE_ID": "w1:p1", "HERDR_BIN_PATH": herdr, "SQUAD_CACHE_TTL": "3",
            "SQUAD_COMPACT_BEFORE": "2"}


def job(h, sid, tr, text="/compact keep the plan", req="r-last", due=0.0):
    """A job written by hand, as the watcher writes it."""
    os.makedirs(squad(h, "compact"), exist_ok=True)
    path = squad(h, "compact", f"{sid}.json")
    with open(path, "w") as fh:
        json.dump({"due": due, "req": req, "pane": "w1:p1", "session": sid, "transcript": tr,
                   "ctx": 300_000, "ttl": 3600, "text": text}, fh)
    return path


def compact(h, herdr, *args):
    return subprocess.run([sys.executable, COMPACT, *args], capture_output=True, text=True, timeout=60,
                          env=clean_env(h, {"HERDR_BIN_PATH": herdr})).returncode


def test_compacts_before_the_cache_goes_cold_with_what_to_keep():
    h = home()
    sid = "c0c0c0c0-0001"
    herdr, sent = fake_herdr(h, sid)
    env = soon(h, herdr)
    tr = transcript(h, sid, 300_000)
    run(h, "UserPromptSubmit", sid, tr, {"prompt": "deliver cards ABC-01c in parallel"}, env)
    assert run(h, "Stop", sid, tr, env=env) == ""
    log = squad(h, "compactions.log")
    assert "/compact sent (300k of context, 0 min cache)" in wait_for(log, "/compact sent"), lines(log)
    prompts = [p for p in lines(sent) if p.startswith("/compact ")]
    assert len(prompts) == 1 and len(lines(sent)) == 1, lines(sent)
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
    run(h, "Stop", sid, tr, env=env)
    wait_for(squad(h, "compactions.log"), "/compact sent")
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
             ("d7d7d7d7-0007", "stop", "the owner asked to stop")]
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
        elif case == "stop":
            run(h, "UserPromptSubmit", sid, tr, {"prompt": "write the handoff, I am closing for today"}, env)
        assert why in wait_for(log, f"{sid[:8]} not compacted"), (case, lines(log))
        assert not lines(sent), (case, lines(sent))


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
    # a text that is not one /compact line
    for text in ("/compact keep it\nrm -rf the repo", "go on and merge everything"):
        assert compact(h, herdr, job(h, sid, tr, text=text), "r-last") == 1
        assert "job without a one-line /compact text" in wait_for(log, "not compacted"), lines(log)
        write(log, "")
    assert not lines(sent)
    # herdr refuses the prompt: logged, and the .sent record is dropped so a later Stop may retry
    write(os.path.join(h, "prompt-fail"), "")
    assert compact(h, herdr, job(h, sid, tr), "r-last") == 1
    assert "/compact not accepted" in wait_for(log, "not accepted")
    assert not os.path.exists(squad(h, "compact", f"{sid}.sent"))
    os.remove(os.path.join(h, "prompt-fail"))
    assert compact(h, herdr, job(h, sid, tr), "r-last") == 0
    assert lines(sent) == ["/compact keep the plan"], lines(sent)


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
