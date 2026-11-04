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
  4. no switch in progress (claim, scheduled wait or switched), no ordered
     handoff, no request from the owner to stop and no earlier /compact that
     arrived as a message, in the watcher's state; and that state exists and is
     readable (missing or corrupt is doubt);
  5. the job's text is one line starting with /compact, at most LIMIT
     characters (above 800 Claude Code pastes the prompt and the /compact
     becomes an ordinary message);
  6. the transcript is readable (without it the compaction cannot be confirmed).
Before sending, it records compact/<session>.sent (request id and time): the
watcher's PreCompact reads it to label the handoff as a compaction the watcher
ordered, and the watcher does not schedule a second one for the same request.
Once sent, it counts as compacted only when a manual compact_boundary shows up
in the transcript after the point of sending (within SQUAD_COMPACT_CONFIRM s,
600). If the watcher saw the text arrive at UserPromptSubmit, it was a message,
not a command: a failure. When it did not compact, the .sent record goes; the
job goes whenever it ends, unless a newer Stop already rescheduled it.
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
LIMIT = 760            # same ceiling as COMPACT_LIMIT in squad-watch.py, in UTF-16 units: margin over the
                       # measured paste limit (800), in case an accent is counted in bytes


def env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


CONFIRM = env_int("SQUAD_COMPACT_CONFIRM", 600)   # s waiting for the manual compact_boundary


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


def state(path):
    """The watcher's state, or None when missing, corrupt or not a dict: doubt."""
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


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
    except (OSError, ValueError, subprocess.TimeoutExpired):   # ValueError: text the OS cannot encode
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


def size(transcript):
    """Bytes in the transcript now, or None when it cannot be read."""
    try:
        with open(transcript, "rb") as fh:
            fh.seek(0, 2)
            return fh.tell()
    except (OSError, ValueError):
        return None


def manual_boundary(transcript, since):
    """Is there a compact_boundary with the manual trigger written after `since` bytes?"""
    try:
        with open(transcript, "rb") as fh:
            fh.seek(since)
            new = fh.read()
    except (OSError, ValueError):
        return False
    for raw in new.split(b"\n"):
        if b"compact_boundary" not in raw:
            continue
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(rec, dict) or rec.get("subtype") != "compact_boundary":
            continue
        meta = rec.get("compactMetadata")
        if isinstance(meta, dict) and meta.get("trigger") == "manual":
            return True
    return False


def confirm(transcript, st_path, since, sent_at):
    """Waits for the proof that /compact ran as a command: 'ok', 'message' (the
    watcher saw the text at UserPromptSubmit) or 'none' (nothing in time)."""
    end = time.time() + CONFIRM
    while True:
        if manual_boundary(transcript, since):
            return "ok"
        mark = (state(st_path) or {}).get("compact_as_text")
        if isinstance(mark, (int, float)) and mark >= sent_at:
            return "message"
        if time.time() >= end:
            return "none"
        time.sleep(1)


def u16(text):
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


def job_path_ok(path):
    return (os.path.dirname(os.path.abspath(path)) == os.path.abspath(JOBS)
            and re.fullmatch(r"[A-Za-z0-9_-]+\.json", os.path.basename(path)) is not None)


def text_ok(text):
    return (text.startswith("/compact ") and u16(text) <= LIMIT
            and not any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in text))


def drop(path):
    try:
        os.remove(path)
    except OSError:
        pass


def drop_job(jpath, req):
    """Removes the job unless a newer Stop already rescheduled it: that one is not ours."""
    if load_job(jpath).get("req") == req:
        drop(jpath)


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
    transcript = str(job.get("transcript") or "")
    since = size(transcript)
    if since is None:
        why = "transcript unreadable: the compaction could not be confirmed"
    elif last_request(transcript) != req:
        why = "the session moved on after it was scheduled (cache renewed)"
    sid, status = agent(pane) if not why else ("", "")
    if not why and sid != session:
        why = f"pane hosts session {sid[:8] or '?'}, expected {session[:8]}"
    elif not why and status not in ("idle", "done"):
        why = f"session is {status or '?'}"
    st_path = os.path.join(STATE, f"{session}.main.json")
    st = state(st_path)
    if not why and st is None:
        why = "watcher state missing or unreadable"
    elif not why:
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
        elif st.get("compact_as_text"):
            why = "a /compact already arrived as a message in this session"
    text = str(job.get("text") or "")
    if not why and not text_ok(text):
        why = f"job without a one-line /compact text of at most {LIMIT} characters"
    if why:
        log(pane, f"{session[:8]} not compacted: {why}")
        drop_job(jpath, req)
        return 1
    marker = mark_sent(session, req, job)
    sent_at = time.time()
    rc, _ = herdr("agent", "prompt", pane, text)
    try:
        ctx, ttl = int(job.get("ctx") or 0), int(job.get("ttl") or 0)
    except (TypeError, ValueError):
        ctx, ttl = 0, 0
    if rc != 0:
        log(pane, f"{session[:8]} not compacted: /compact not accepted by herdr")
        result = "refused"
    else:
        log(pane, f"{session[:8]} /compact sent ({ctx // 1000}k of context, {ttl // 60} min cache); "
                  f"{u16(text)} characters, waiting for a manual compact_boundary")
        result = confirm(transcript, st_path, since, sent_at)
        log(pane, f"{session[:8]} " + {
            "ok": "compacted: manual compact_boundary in the transcript",
            "message": "not compacted: the /compact arrived as a message (UserPromptSubmit), not as a command",
            "none": f"not compacted: no manual compact_boundary within {CONFIRM} s of sending"}[result])
    if result != "ok" and marker:
        drop(marker)                               # not compacted: the record must not label or block anything
    drop_job(jpath, req)
    return 0 if result == "ok" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        try:
            log("?", f"error {type(exc).__name__}")
        except Exception:
            pass
        sys.exit(1)
