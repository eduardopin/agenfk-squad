#!/usr/bin/env python3
"""Compacts an idle main session just before its prompt cache goes cold.

Launched by squad-watch on the Stop of a main session (outside handoff, context
>= SQUAD_COMPACT_MIN). Runs detached, outside the session:

    squad-compact.py <job.json> <request-id>

The job (~/.claude/squad/compact/<session>.json, written by the watcher) holds
the herdr pane, the session, the transcript, the time it is due (cache TTL minus
SQUAD_COMPACT_BEFORE) and the /compact text. Why at that time: with the cache
still warm, compacting reads the context at 0.1 of the price; a minute after it
goes cold, the next message writes the whole context again (1.25x on the 5 min
cache, 2x on the 1 h one).

At the due time it compacts only if all of this still holds:
  1. the job is still this one (every Stop reschedules; the old process leaves on its own);
  2. the transcript has no new request (the cache was renewed);
  3. the pane hosts the same session, idle (idle or done);
  4. no switch in progress (claim or scheduled wait), no ordered handoff and no
     request from the owner to stop, in the watcher's state;
  5. the job's text is one line starting with /compact.
Before sending, it records compact/<session>.sent (request id and time): the
watcher's PreCompact reads it to label the handoff as a compaction the watcher
ordered, and the watcher does not schedule a second one for the same request.
Every decision goes to ~/.claude/squad/compactions.log. Any doubt = do not compact.
"""
import json
import os
import re
import subprocess
import sys
import time

WINDOWS = os.name == "nt"
HOME = os.path.expanduser("~")
BASE = os.path.join(HOME, ".claude", "squad")
JOBS = os.path.join(BASE, "compact")
STATE = os.path.join(BASE, "watch")
CLAIMS = os.path.join(BASE, "switches")
HERDR = os.environ.get("HERDR_BIN_PATH") or "herdr"
LOG = os.path.join(BASE, "compactions.log")
STEP = 30              # seconds between checks of the job while waiting
MAX_TEXT = 4000        # the /compact text is one prompt line; a longer one is not the watcher's


def log(pane, msg):
    os.umask(0o077)
    os.makedirs(BASE, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {pane} {msg}\n")


def load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
            return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def load_job(path):
    """The job, read without following a symlink and only if it is ours: its
    text goes to the pane as a prompt."""
    nofollow = getattr(os, "O_NOFOLLOW", 0)      # Windows has no such flag: the link is refused by hand
    binary = getattr(os, "O_BINARY", 0)
    try:
        if not nofollow and os.path.islink(path):
            return {}
        fd = os.open(path, os.O_RDONLY | nofollow | binary)
    except OSError:
        return {}
    try:
        info = os.fstat(fd)
        if not WINDOWS and info.st_uid != os.getuid():
            return {}
        with os.fdopen(fd, "rb") as fh:
            fd = None
            d = json.loads(fh.read(65_536).decode("utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}
    finally:
        if fd is not None:
            os.close(fd)


def herdr(*args, timeout=20):
    try:
        r = subprocess.run([HERDR, *args], capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
        return r.returncode, r.stdout
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""


def agent(pane):
    rc, out = herdr("agent", "get", pane, timeout=15)
    try:
        a = json.loads(out)["result"]["agent"]
        return (a.get("agent_session") or {}).get("value") or "", a.get("agent_status") or ""
    except (ValueError, KeyError, TypeError, AttributeError):
        return "", ""


def last_request(transcript):
    """requestId of the last response with usage in the transcript (reads only its tail)."""
    try:
        with open(transcript, "rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - 2_000_000))
            lines = fh.read().split(b"\n")
    except OSError:
        return None
    for raw in reversed(lines):
        if b'"usage"' not in raw or b'"assistant"' not in raw:
            continue
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("type") == "assistant" and rec.get("requestId"):
            return rec["requestId"]
    return ""


def job_path_ok(path):
    return (os.path.dirname(os.path.abspath(path)) == os.path.abspath(JOBS)
            and re.fullmatch(r"[A-Za-z0-9_-]+\.json", os.path.basename(path)) is not None)


def text_ok(text):
    return (text.startswith("/compact ") and len(text) <= MAX_TEXT
            and not any(ord(c) < 32 or ord(c) == 127 for c in text))


def mark_sent(session, req, job):
    path = os.path.join(JOBS, f"{session}.sent")
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"req": req, "ts": time.time(), "ctx": job.get("ctx"), "ttl": job.get("ttl")}, fh)
        os.replace(tmp, path)
        return path
    except OSError:
        return ""


def main():
    if len(sys.argv) != 3:
        return 2
    jpath, req = sys.argv[1:3]
    if not job_path_ok(jpath):
        return 2
    job = load_job(jpath)
    pane, session = str(job.get("pane") or ""), str(job.get("session") or "")
    if (job.get("req") != req or not re.fullmatch(r"[A-Za-z0-9:_-]{1,40}", pane)
            or not re.fullmatch(r"[0-9a-f-]{8,64}", session)):
        return 2
    try:
        due = float(job.get("due") or 0)
    except (TypeError, ValueError):
        return 2
    while time.time() < due:                       # waits, checking whether it was rescheduled
        time.sleep(max(0.2, min(STEP, due - time.time())))
        if load_job(jpath).get("req") != req:
            return 0
    job = load_job(jpath)
    if job.get("req") != req:
        return 0
    why = ""
    if last_request(str(job.get("transcript") or "")) != req:
        why = "the session moved on after it was scheduled (cache renewed)"
    sid, status = agent(pane) if not why else ("", "")
    if not why and sid != session:
        why = f"pane hosts session {sid[:8] or '?'}, expected {session[:8]}"
    elif not why and status not in ("idle", "done"):
        why = f"session is {status or '?'}"
    st = load(os.path.join(STATE, f"{session}.main.json"))
    if not why:
        try:
            busy = (int(st.get("ctx_level") or 0) >= 2 or st.get("switched")
                    or float(st.get("switch_wait") or 0) > time.time()
                    or os.path.exists(os.path.join(CLAIMS, f"{session}.claim")))
        except (TypeError, ValueError):
            busy = True                            # unreadable state: doubt
        if busy:
            why = "session at handoff or switching"
        elif st.get("asked_stop"):
            why = "the owner asked to stop"
    text = str(job.get("text") or "")
    if not why and not text_ok(text):
        why = "job without a one-line /compact text"
    if why:
        log(pane, f"{session[:8]} not compacted: {why}")
        return 1
    marker = mark_sent(session, req, job)
    rc, _ = herdr("agent", "prompt", pane, text)
    try:
        ctx, ttl = int(job.get("ctx") or 0), int(job.get("ttl") or 0)
    except (TypeError, ValueError):
        ctx, ttl = 0, 0
    if rc == 0:
        log(pane, f"{session[:8]} /compact sent ({ctx // 1000}k of context, {ttl // 60} min cache)")
    else:
        log(pane, f"{session[:8]} /compact not accepted")
        if marker:
            try:
                os.remove(marker)                  # not sent: the next Stop may schedule it again
            except OSError:
                pass
    try:
        os.remove(jpath)
    except OSError:
        pass
    return 0 if rc == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        try:
            log("?", f"error {type(exc).__name__}")
        except Exception:
            pass
        sys.exit(1)
