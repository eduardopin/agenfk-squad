#!/usr/bin/env python3
"""Wakes the session that stopped without delivering the work.

Sessions stop halfway through a delivery (the reply ended with nothing to call
them back, a background command died, an API error, a reboot) and the work
sits there until someone looks at the pane.

Runs detached, from a timer (systemd --user, every 2 min), and looks at every
herdr pane hosting Claude Code:

    squad-wake.py              one scan: wakes whoever needs it
    squad-wake.py --dry-run    prints the decision for each pane, without acting

A pane idle (idle/done) for SQUAD_WAKE_AFTER (600) s gets a one-line prompt
starting with "[squad-watch] WAKE": check the state, relaunch what died and
carry on until it is delivered. If the last stop left a background task alive
(squad-watch's Stop records it), the deadline is SQUAD_WAKE_AFTER_BG (2700) s,
because the task itself wakes the session when it comes back.

It does not wake:
  - a working pane, or one whose session changed between the decision and the send;
  - when the owner asked to stop, pause or hand off (squad-watch's asked_stop);
  - a session at handoff level or switching (squad-switch is in charge);
  - a last reply with `[squad] END: ...` (nothing more it can do; `[squad] DONE: ...` too) or
    `[squad] BLOCKED: ...` (depends on the owner; the owner is told once).
Backoff: each wake without progress doubles the deadline, up to
SQUAD_WAKE_CEILING (3600) s; after SQUAD_WAKE_MAX (6) wakes in a row without
progress it only tells the owner, once. Progress is what changes state after
the wake (Edit/Write, an agent dispatch, git commit/push/merge, gh pr
create/merge, agenfk verify/update/create, squad-turn request), never the checks
the WAKE itself asks for: otherwise a session that only checks and goes back to
waiting would be woken every 10 min forever. Progress resets the count.
A session idle for more than SQUAD_WAKE_AGE (43200) s is not woken (an old
conversation, not interrupted work). Unreadable watcher state = do not wake.
A pane stuck on a dialog (blocked) for SQUAD_WAKE_BLOCKED (900) s: tells the
owner, once; it never answers a dialog by itself.

The handoff/pickup cannot get stuck, because sessions work 24 h a day:
  - stuck switch: the old session is marked as switching (switched or claim)
    and is still in the pane, idle for SQUAD_WAKE_SWITCH (900) s. The switch
    gave up after claiming the session (/clear refused, a pane that did not
    change). squad-wake removes the claim, hands the Stop back to the watcher
    (squad_rearm) and delivers an expired-deadline Stop: the watcher redoes the
    switch with the handoff already on disk.
  - lost pickup: squad-switch records switches/<new-session>.pickup before
    sending the pickup; if after SQUAD_WAKE_PICKUP (120) s the new session is
    idle and the pickup never became a request in the transcript, squad-wake
    resends `/clear-pickup pickup <copy>`.
  Each one tries twice; after that it only tells the owner, once. A switch
  older than SQUAD_WAKE_SWITCH_AGE (21600) s is not redone: that is an old
  session reopened.

From the timer, the environment comes from ~/.claude/squad/wake.env
(SQUAD_WAKE=0 there disables it, and the thresholds are tuned there);
`systemctl --user disable --now squad-wake.timer` stops the timer.
At most SQUAD_WAKE_BATCH (3) wakes per scan, so as not to bring every session
on the machine up at once. SQUAD_WAKE=0 disables it.

Every decision goes to ~/.claude/squad/wakes.log. Any doubt = do not wake.
"""
import glob
import json
import os
import re
import stat
import subprocess
import sys
import time

HOME = os.path.expanduser("~")
BASE = os.path.join(HOME, ".claude", "squad")
STATE = os.path.join(BASE, "watch")
WAKE = os.path.join(BASE, "wake")
LOG = os.path.join(BASE, "wakes.log")
HERDR = os.environ.get("HERDR_BIN_PATH") or "herdr"
PROJECTS = os.environ.get("SQUAD_WAKE_PROJECTS") or os.path.join(HOME, ".claude", "projects")


def env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


AFTER = env_int("SQUAD_WAKE_AFTER", 600)
AFTER_BG = env_int("SQUAD_WAKE_AFTER_BG", 2700)
CEILING = env_int("SQUAD_WAKE_CEILING", 3600)
MAX = env_int("SQUAD_WAKE_MAX", 6)
BATCH = env_int("SQUAD_WAKE_BATCH", 3)
BLOCKED = env_int("SQUAD_WAKE_BLOCKED", 900)
AGE = env_int("SQUAD_WAKE_AGE", 43_200)
SWITCH_AFTER = env_int("SQUAD_WAKE_SWITCH", 900)
SWITCH_AGE = env_int("SQUAD_WAKE_SWITCH_AGE", 21_600)   # a switch older than this is not redone
PICKUP_AFTER = env_int("SQUAD_WAKE_PICKUP", 120)
TRIES = 2
WATCH = os.path.join(os.path.dirname(os.path.realpath(__file__)), "squad-watch.py")
PICKUP = os.environ.get("SQUAD_SWITCH_PROMPT") or "/clear-pickup pickup"
SWITCHES = os.path.join(BASE, "switches")
COPIES = os.path.join(BASE, "handoffs")

PREFIX = "[squad-watch] WAKE"
# only at the start of a line and with real content: quoting the template ("<what ...>") does not count
MARK = re.compile(r"(?m)^[\s>*`-]*\[squad\]\s*(END|DONE|BLOCKED)\s*:\s*([^<\s`][^\n]{0,200})$", re.I)
CHANGES = {"Edit", "Write", "NotebookEdit", "MultiEdit", "Agent", "Task"}
# the git subcommand as a whole word (not `merge-base`, nor `--grep=commit`), with git's global
# options before it; --dry-run only counts outside quotes (the commit message may mention it)
GIT_CHANGES = re.compile(r"\bgit(?:\s+(?:-[Cc]\s*\S+|--[\w-]+(?:=\S+)?))*\s+(commit|push|merge|rebase|cherry-pick)"
                         r"(?=[\s;&|)'\"]|$)([^\n;&|]*)")
CHANGES_CMD = re.compile(r"\bgh\s+pr\s+(?:create|merge|edit|ready)\b"
                         r"|\bagenfk\s+(?:verify|update|create)\b|\bsquad-turn(?:\.py)?\s+request\b")
QUOTES = re.compile(r"'[^']*'|\"[^\"]*\"")
SID = re.compile(r"^[0-9a-f]{8}-[0-9a-f-]{8,40}$")


def changes_state(cmd):
    """Does the Bash command change state (commit, push, merge, PR, card, turn)?"""
    if CHANGES_CMD.search(cmd):
        return True
    for m in GIT_CHANGES.finditer(cmd):
        if "--dry-run" not in QUOTES.sub("", m.group(2)):
            return True
    return False


def text(minutes):
    # One line. No "handoff", "pause", "/clear", "pickup", "you can stop", "for today":
    # squad-watch reads the prompt and must not take it for the owner asking to stop
    # (it also skips this prefix; tests/test_wake.py checks both).
    return (f"{PREFIX}: this session has been idle for {minutes} min and the owner's work has not been "
            "delivered yet (tight deadline, work goes on 24 h a day). Check git status, the card in AgenFK, "
            "the merge queue (squad-turn status) and the background tasks, which may have died; relaunch "
            "whatever is needed and carry on until it is delivered: PRs merged with a green gate and SHIP, "
            "cards in DONE, and then the project's open cards. If you are waiting for something that will "
            "call you back on its own (the merge turn, a live background command), say in one line what you "
            "are waiting for and stop. Only stop for good by ending with one of these lines: "
            "`[squad] BLOCKED: merge approval on PR #64 is the owner's` or "
            "`[squad] END: no card is left that I can take` (your own text, not a <placeholder>).")


def log(pane, msg):
    os.umask(0o077)
    os.makedirs(BASE, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {pane} {msg}\n")


def herdr(*args, timeout=60):
    try:
        r = subprocess.run([HERDR, *args], capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""


def agents():
    rc, out = herdr("agent", "list")
    if rc != 0:
        return None
    try:
        return json.loads(out)["result"]["agents"]
    except (ValueError, KeyError, TypeError):
        return None


def agent(pane):
    rc, out = herdr("agent", "get", pane)
    if rc != 0:
        return None
    try:
        a = json.loads(out)["result"]["agent"]
        return (a.get("agent_session") or {}).get("value"), a.get("agent_status")
    except (ValueError, KeyError, TypeError):
        return None


def notify(title, body):
    herdr("notification", "show", title, "--body", body[:300], "--sound", "request", timeout=20)


def load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
            return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save(path, st):
    os.umask(0o077)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(st, fh)
    os.replace(tmp, path)


def transcript(sid):
    hits = glob.glob(os.path.join(PROJECTS, "*", f"{sid}.jsonl"))
    return max(hits, key=os.path.getmtime) if hits else ""


def last_reply(path):
    """(someone made a request, text of the session's last reply)."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - 2_000_000))
            lines = fh.read().split(b"\n")
    except OSError:
        return False, ""
    asked, reply = False, None
    for raw in reversed(lines):
        if b'"assistant"' not in raw and b'"user"' not in raw:
            continue
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        if rec.get("isSidechain"):
            continue
        c = (rec.get("message") or {}).get("content")
        if rec.get("type") == "assistant" and reply is None:
            parts = [b.get("text") or "" for b in (c or []) if isinstance(b, dict) and b.get("type") == "text"]
            if parts:
                reply = "\n".join(parts)
        elif rec.get("type") == "user" and (isinstance(c, str) or any(
                isinstance(b, dict) and b.get("type") == "text" for b in (c or []))):
            asked = True
        if asked and reply is not None:
            break
    if not asked:                         # nothing read at the end: look for a request at the start of the file
        try:
            with open(path, "rb") as fh:
                asked = re.search(rb'"type":\s*"user"', fh.read(2_000_000)) is not None
        except OSError:
            pass
    return asked, reply or ""


def progress_since(path, offset):
    """Was there state-changing work after `offset`? Checking does not count."""
    try:
        with open(path, "rb") as fh:
            fh.seek(offset)
            lines = fh.read(20_000_000).split(b"\n")
    except OSError:
        return False
    for raw in lines:
        if b'"tool_use"' not in raw:
            continue
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        if rec.get("type") != "assistant" or rec.get("isSidechain"):
            continue
        for b in (rec.get("message") or {}).get("content") or []:
            if not isinstance(b, dict) or b.get("type") != "tool_use":
                continue
            if b.get("name") in CHANGES:
                return True
            if b.get("name") == "Bash" and changes_state(str((b.get("input") or {}).get("command") or "")):
                return True
    return False


def pickup_landed(path):
    """The pickup became a request in the new session's transcript (the /clear alone does not count)."""
    try:
        with open(path, "rb") as fh:
            data = fh.read(5_000_000)
    except OSError:
        return False
    for raw in data.split(b"\n"):
        if b"clear-pickup" not in raw or b'"user"' not in raw:
            continue
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        c = (rec.get("message") or {}).get("content")
        said = c if isinstance(c, str) else " ".join(
            str(b.get("text") or "") for b in (c or []) if isinstance(b, dict))
        if rec.get("type") == "user" and "clear-pickup" in said:
            return True
    return False


def own_file(path, ceiling=4096):
    """A regular file owned by the user, not a symlink, small: anything else is not read."""
    try:
        info = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_size <= ceiling


def valid_copy(pend):
    """Reads switches/<session>.pickup and returns the path of the sealed copy, or "".
    It goes to the pane as a prompt: only a squad-switch copy path gets through
    (handoffs/<session>-<12 hex>.md), a regular file owned by the user, no symlink."""
    if not own_file(pend, 1024):
        return ""
    try:
        fd = os.open(pend, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "r", encoding="utf-8") as fh:
            copy = fh.read(1024).strip()
    except (OSError, UnicodeError):
        return ""
    if not re.fullmatch(re.escape(COPIES) + r"/[0-9a-f-]{8,64}-[0-9a-f]{12}\.md", copy):
        return ""
    return copy if own_file(copy, 5_000_000) else ""


def load_watch(sid):
    """squad-watch's state: {} if it does not exist; None if it exists and cannot be read (doubt)."""
    path = os.path.join(STATE, f"{sid}.main.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def decide(a, now, dry_run=False):
    """(action, reason, context). action: wake | notify | reswitch | pickup | landed | none."""
    if not isinstance(a, dict):
        return "none", "invalid herdr item", {}
    pane = str(a.get("pane_id") or "")
    sid = str((a.get("agent_session") or {}).get("value") or "")
    status = str(a.get("agent_status") or "")
    if a.get("agent") != "claude" or not SID.match(sid):
        return "none", "no Claude session", {}
    mine_path = os.path.join(WAKE, f"{sid}.json")
    mine = load(mine_path)
    tr = transcript(sid)
    pend = os.path.join(SWITCHES, f"{sid}.pickup")
    if os.path.lexists(pend):             # a session born from a switch: did the pickup land?
        ctx = {"pane": pane, "sid": sid, "tr": tr, "path": mine_path, "st": mine, "pend": pend}
        if tr and pickup_landed(tr):
            return "landed", "pickup landed", ctx
        ctx["copy"] = valid_copy(pend)
        if not ctx["copy"]:
            return "none", "invalid pickup record: ignored", ctx
        if status not in ("idle", "done") or now - os.path.getmtime(pend) < PICKUP_AFTER:
            return "none", "pickup on its way", ctx
        if int(mine.get("pickup_n") or 0) >= TRIES:
            if mine.get("pickup_notified"):
                return "none", "pickup lost, owner already told", ctx
            ctx["mark"] = "pickup_notified"
            return "notify", f"pickup did not land after {TRIES} resends: run /clear-pickup pickup", ctx
        return "pickup", "pickup did not land: resending", ctx
    if not tr:
        return "none", "no transcript", {}
    watch = load_watch(sid)
    if watch is None:
        return "none", "unreadable watcher state", {}
    size = os.path.getsize(tr)
    quiet = now - os.path.getmtime(tr)
    ctx = {"pane": pane, "sid": sid, "tr": tr, "path": mine_path, "st": mine, "size": size,
           "quiet": quiet}

    # progress after the last wake resets the count
    if mine.get("n") and size > mine.get("size", 0) and progress_since(tr, mine.get("size", 0)):
        mine.update(n=0, max_notified=False, size=size)
        if not dry_run:
            save(mine_path, mine)

    if status == "working":
        return "none", "working", ctx
    if status == "blocked":
        if quiet >= BLOCKED and mine.get("dialog_notified") != int(os.path.getmtime(tr)):
            ctx["mark"] = "dialog_notified"
            return "notify", f"stuck on a dialog for {int(quiet // 60)} min", ctx
        return "none", "dialog open", ctx
    if status not in ("idle", "done"):
        return "none", f"status {status or '?'}", ctx
    if watch.get("asked_stop"):
        return "none", "the owner asked to stop", ctx
    claim = os.path.join(SWITCHES, f"{sid}.claim")
    has_claim = os.path.exists(claim)
    # a scheduled switch (background task) whose `squad-switch wait` died: its deadline
    # (switch_wait, already with 30 min of slack) has passed and nobody switched the session
    dead_wait = watch.get("ctx_level", 0) >= 2 and not watch.get("switched") and \
        0 < float(watch.get("switch_wait") or 0) <= now
    # old state with no switched_ts: without the switch time a stuck switch cannot be asserted
    if watch.get("switched") and not has_claim and not watch.get("switched_ts"):
        return "none", "old switch with no time: neither redone nor woken", ctx
    # moved after the last sign of the switch (switch time or claim): the owner resumed it, it
    # never gets a /clear (only when a switch is marked: a dead wait has no switched, and an old
    # switched_ts from an earlier attempt cannot hide it)
    alive = False
    if watch.get("switched") or has_claim:
        sign = max(float(watch.get("switched_ts") or 0), os.path.getmtime(claim) if has_claim else 0)
        alive = sign > 0 and os.path.getmtime(tr) > sign + 120
    if not alive and (watch.get("switched") or has_claim or dead_wait):
        # the reference is the last sign of the switch, not only the transcript: a switch
        # that has just gone out (after a 40 min wait) is not stuck
        ref = max(os.path.getmtime(tr), float(watch.get("switched_ts") or 0),
                  os.path.getmtime(claim) if has_claim else 0)
        if now - ref < SWITCH_AFTER:
            return "none", "switch under way", ctx
        # a stuck switch is redone within 15-30 min; one from hours ago is an old session the
        # owner reopened (claude --resume), which must never get a /clear
        last = max(float(watch.get("switched_ts") or 0), os.path.getmtime(claim) if has_claim else 0,
                   float(watch.get("switch_wait") or 0) if dead_wait else 0)
        if not last or now - last > SWITCH_AGE:
            return "none", "old switch: neither redone nor woken", ctx
        if int(mine.get("reswitch_n") or 0) >= TRIES:
            if mine.get("reswitch_notified"):
                return "none", "stuck switch, owner already told", ctx
            ctx["mark"] = "reswitch_notified"
            return "notify", f"session switch stuck after {TRIES} tries: /clear and /clear-pickup pickup", ctx
        ctx["claim"] = claim if has_claim else ""
        return "reswitch", f"switch stuck for {int((now - ref) // 60)} min: redoing it", ctx
    if watch.get("ctx_level", 0) >= 2:
        return "none", "handoff with the watcher", ctx
    if quiet > AGE:
        return "none", f"idle for {int(quiet // 3600)} h: old conversation", ctx
    asked, reply = last_reply(tr)
    if not asked:
        return "none", "session with no request", ctx
    m = None
    for m in MARK.finditer(reply):
        pass
    if m and m.group(1).upper() in ("END", "DONE"):
        return "none", f"declared {m.group(1).upper()}", ctx
    if m and m.group(1).upper() == "BLOCKED":
        if mine.get("blocked_notified") != int(os.path.getmtime(tr)):
            ctx["mark"] = "blocked_notified"
            return "notify", "BLOCKED: " + m.group(2).strip(), ctx
        return "none", "BLOCKED, owner already told", ctx
    n = int(mine.get("n") or 0)
    if n >= MAX:
        if not mine.get("max_notified"):
            ctx["mark"] = "max_notified"
            return "notify", f"{n} wakes in a row without progress", ctx
        return "none", f"{n} wakes without progress, owner already told", ctx
    stopped = watch.get("last_stop") or {}
    bg = stopped.get("bg", 0) and stopped.get("ts", 0) >= os.path.getmtime(tr) - 120
    deadline = min(AFTER * (2 ** n), CEILING)
    if bg:
        deadline = max(deadline, AFTER_BG)
    if quiet < deadline:
        return "none", f"idle for {int(quiet)} s, deadline {deadline} s", ctx
    return "wake", f"idle for {int(quiet // 60)} min (deadline {deadline} s, wake {n + 1})", ctx


def wake(ctx):
    pane, sid = ctx["pane"], ctx["sid"]
    now_ = agent(pane)                    # checked again, right before sending
    if not now_ or now_[0] != sid or now_[1] not in ("idle", "done"):
        log(pane, f"{sid[:8]} gave up: the pane changed ({now_})")
        return False
    st = ctx["st"]
    before = dict(st)
    st.update(n=int(st.get("n") or 0) + 1, ts=time.time(), size=os.path.getsize(ctx["tr"]))
    save(ctx["path"], st)                 # counted before sending: a full disk does not become a wake loop
    rc, _ = herdr("agent", "prompt", pane, text(int(ctx["quiet"] // 60)))
    if rc != 0:
        save(ctx["path"], before)
        log(pane, f"{sid[:8]} gave up: the prompt failed")
        return False
    return True


def to_watch(pane, fields):
    """A synthetic Stop handed to squad-watch, as squad-switch does."""
    payload = {"hook_event_name": "Stop", "background_tasks": [], **fields}
    try:
        subprocess.run([sys.executable, WATCH], input=json.dumps(payload), text=True, timeout=30,
                       capture_output=True, env=dict(os.environ, HERDR_PANE_ID=pane))
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


def reswitch(ctx):
    """A switch that gave up after claiming the session: the watcher redoes it with the handoff on disk."""
    pane, sid = ctx["pane"], ctx["sid"]
    now_ = agent(pane)
    if not now_ or now_[0] != sid or now_[1] not in ("idle", "done"):
        log(pane, f"{sid[:8]} reswitch gave up: the pane changed ({now_})")
        return False
    st = ctx["st"]
    st["reswitch_n"] = int(st.get("reswitch_n") or 0) + 1
    save(ctx["path"], st)
    if ctx["claim"]:
        try:
            os.remove(ctx["claim"])
        except OSError:
            pass
    ok = to_watch(pane, {"session_id": sid, "squad_rearm": True}) and \
        to_watch(pane, {"session_id": sid, "transcript_path": ctx["tr"], "cwd": "", "squad_wait_expired": True})
    log(pane, f"{sid[:8]} stuck switch: " + ("handed back to the watcher" if ok else "the watcher did not answer"))
    return ok


def resend_pickup(ctx):
    pane, sid = ctx["pane"], ctx["sid"]
    copy = valid_copy(ctx["pend"])        # rechecked right before sending
    now_ = agent(pane)
    if not copy or not now_ or now_[0] != sid or now_[1] not in ("idle", "done"):
        return False
    st = ctx["st"]
    st["pickup_n"] = int(st.get("pickup_n") or 0) + 1
    save(ctx["path"], st)
    os.utime(ctx["pend"], follow_symlinks=False)   # the next resend waits for the deadline again
    where = f'"{copy}"' if " " in copy else copy     # a Windows profile can hold a space: the prompt splits on it
    rc, _ = herdr("agent", "prompt", pane, f"{PICKUP} {where}" if PICKUP.startswith("/") else PICKUP)
    log(pane, f"{sid[:8]} pickup resent ({st['pickup_n']}): " + ("ok" if rc == 0 else "the prompt failed"))
    return rc == 0


def main(argv):
    if os.environ.get("SQUAD_WAKE") == "0":
        return 0
    dry_run = "--dry-run" in argv
    items = agents()
    if items is None:
        if dry_run:                       # --dry-run writes nothing, not even the log
            print("herdr did not answer: nothing done")
        else:
            log("-", "herdr did not answer: nothing done")
        return 0
    now, woken = time.time(), 0
    for a in items:
        try:
            action, reason, ctx = decide(a, now, dry_run)
        except Exception as exc:          # one faulty pane does not stop the scan
            if not dry_run:
                log(str(a.get("pane_id") if isinstance(a, dict) else "?"), f"error {type(exc).__name__}: nothing done")
            continue
        pane = str((a.get("pane_id") if isinstance(a, dict) else "") or "-")
        sid = (ctx.get("sid") or "")[:8]
        if dry_run:
            print(f"{pane:8} {sid:8} {action:8} {reason}")
            continue
        if action == "landed":
            try:
                os.remove(ctx["pend"])
            except OSError:
                pass
        elif action == "reswitch":
            reswitch(ctx)
        elif action == "pickup":
            resend_pickup(ctx)
        elif action == "wake":
            if woken >= BATCH:
                log(pane, f"{sid} left for the next scan (batch of {BATCH})")
                continue
            if wake(ctx):
                woken += 1
                log(pane, f"{sid} WAKE sent: {reason}")
        elif action == "notify":
            notify(f"squad-watch: {pane} {sid}", reason)
            # a notice per stop (it changes when the session moves) or once and for all
            per_stop = ctx["mark"] in ("dialog_notified", "blocked_notified") and ctx.get("tr")
            ctx["st"][ctx["mark"]] = int(os.path.getmtime(ctx["tr"])) if per_stop else True
            save(ctx["path"], ctx["st"])
            log(pane, f"{sid} owner told: {reason}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:
        try:
            if "--dry-run" in sys.argv[1:]:
                print(f"error {type(exc).__name__}: scan aborted")
            else:
                log("-", f"error {type(exc).__name__}: scan aborted")
        except Exception:
            pass
        sys.exit(0)
