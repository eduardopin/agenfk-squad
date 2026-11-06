#!/usr/bin/env python3
"""Ground truth for the clear-pickup skill, and the handoff written when a session is
cleared without one.

    handoff.py facts                         this session: id, transcript, the cards it ran agenfk on
    handoff.py check <file>                  is this handoff still the one to pick up? (JSON)
    handoff.py claim <file>                  record that this session picked it up
    handoff.py auto <session> <transcript> <cwd>
                                             write the automatic handoff; prints its path and sha256

The session id comes from CLAUDE_CODE_SESSION_ID. Reads transcripts and handoff files; writes
only under ~/.claude/squad (0700). The sealed, read-only copy is squad-switch.py's
sealed_copy, so the watcher and this script share one write.
"""
import argparse
import datetime
import glob
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HOME = os.path.expanduser("~")
SQUAD = os.path.join(HOME, ".claude", "squad")
SWAPS = os.path.join(SQUAD, "swaps.log")
PICKUPS = os.path.join(SQUAD, "pickups.log")
PROJECTS = os.path.join(HOME, ".claude", "projects")
LIVE = 900             # seconds: a transcript written within this interval is a live session

def load_switch():
    """squad-switch.py by path (its name has a hyphen): the repository's hooks/ when this
    script runs from a checkout or a skill link, else the installed copy."""
    here = os.path.dirname(os.path.realpath(__file__))
    for base in (os.path.join(here, "..", "..", "..", "hooks"), os.path.join(HOME, ".claude", "hooks")):
        path = os.path.join(base, "squad-switch.py")
        if os.path.isfile(path):
            spec = importlib.util.spec_from_file_location("squad_switch", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    sys.exit("handoff.py: squad-switch.py not found (run install.sh)")


SWITCH = load_switch()
COPIES = SWITCH.COPIES

ID = r"[0-9a-f]{8}(?:-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})?"
# the card id right after the subcommand: a --project or --parent id is not the card worked on
AGENFK_CARD = re.compile(r"\bagenfk\s+(?:verify|comment|update|pause-work|resume-work|"
                         r"review\s+\w+|add-context|move|delete)\s+(" + ID + r")\b")
SESSION_LINE = re.compile(r"\*\*Session:\*\*\s*`?(" + ID + r")")
# what a user record holds that the owner did not type: any harness tag (<bash-stdout>,
# <task-notification>, <persisted-output>...), skill bodies, agent messages
HARNESS_TAG = re.compile(r"<[a-z][a-z0-9-]*[ >]")
NOT_ASKED = ("Base directory for this skill", "Another Claude session sent a message",
             "Caveat:", "[Request interrupted")


def records(transcript, tail=6_000_000):
    """Main-thread records of a transcript, oldest first (the last `tail` bytes)."""
    try:
        with open(transcript, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - tail))
            blob = fh.read()
    except OSError:
        return []
    out = []
    for raw in blob.split(b"\n"):
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        if isinstance(rec, dict) and not rec.get("isSidechain"):
            out.append(rec)
    return out


def blocks(rec):
    c = (rec.get("message") or {}).get("content")
    if isinstance(c, str):
        return [{"type": "text", "text": c}]
    return [b for b in c or [] if isinstance(b, dict)]


def session_cards(transcript, limit=8):
    """AgEnFK cards this session acted on, newest first. Another session's cards
    never show up here: only this transcript's own agenfk commands count."""
    seen, out, full = set(), [], {}
    for rec in reversed(records(transcript)):
        if rec.get("type") != "assistant":
            continue
        for b in reversed(blocks(rec)):
            if b.get("type") != "tool_use" or b.get("name") != "Bash":
                continue
            inp = b.get("input") if isinstance(b.get("input"), dict) else {}
            for card in reversed(AGENFK_CARD.findall(str(inp.get("command") or ""))):
                if len(card) > 8:
                    full[card[:8]] = card
                if card[:8] not in seen:
                    seen.add(card[:8])
                    out.append(card)
    # a short id and its full form are one card: keep the full form
    return [full.get(c[:8], c) for c in out][:limit]


def epoch(ts):
    try:
        return datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def last_request(transcript):
    """The owner's last request (text, epoch seconds), or ("", 0)."""
    for rec in reversed(records(transcript)):
        if rec.get("type") != "user" or rec.get("isMeta"):
            continue
        parts = [b.get("text", "") for b in blocks(rec) if b.get("type") == "text"]
        if any(b.get("type") == "tool_result" for b in blocks(rec)):
            continue
        text = "\n".join(p for p in parts if p).strip()
        if text and not text.startswith(NOT_ASKED) and not HARNESS_TAG.match(text):
            return text, epoch(rec.get("timestamp"))
    return "", 0.0


def last_reply(transcript):
    for rec in reversed(records(transcript)):
        if rec.get("type") == "assistant":
            texts = [b.get("text", "") for b in blocks(rec) if b.get("type") == "text"]
            text = "\n".join(t for t in texts if t).strip()
            if text:
                return text
    return ""


def run(cwd, *args):
    try:
        # a cloned repository's config must not run anything here (core.fsmonitor runs on
        # status), and the live session keeps its index lock
        r = subprocess.run(["git", "-c", "core.fsmonitor=false", "--no-optional-locks", "-C", cwd, *args],
                           capture_output=True, text=True, timeout=5,
                           env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1"})
        return r.stdout.rstrip() if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def git_facts(cwd):
    root = run(cwd, "rev-parse", "--show-toplevel")
    if root is None:
        return None
    return {"root": root, "branch": run(cwd, "branch", "--show-current") or "(detached)",
            "head": run(cwd, "rev-parse", "--short", "HEAD") or "(no commit)",
            "status": run(cwd, "status", "--short") or "",
            "log": run(cwd, "log", "--oneline", "-5") or ""}


def clip(text, n):
    text = text.strip()
    return text if len(text) <= n else text[:n].rstrip() + " [...]"


def quote(text):
    return "\n".join("> " + line for line in text.splitlines()) or "> (none)"


def write_auto(session, transcript, cwd):
    """Writes the handoff of a session cleared without one, from its transcript
    and git, to ~/.claude/squad/handoffs (read-only file). Returns (path, sha256)."""
    asked, _ = last_request(transcript)
    reply = last_reply(transcript)
    cards = session_cards(transcript)
    g = git_facts(cwd)
    now = time.localtime()
    lines = [f"# Handoff: {clip(asked.splitlines()[0] if asked else 'session cleared', 80)} (automatic)", "",
             f"- **When:** {time.strftime('%Y-%m-%dT%H:%M:%S%z', now)}",
             f"- **Project:** {g['root'] if g else cwd} " + ("" if g else "(not a git repository)"),
             f"- **Branch:** {g['branch']} @ {g['head']}" if g else "- **Branch:** none (not a git repository)",
             f"- **Card:** {', '.join(cards) if cards else 'none run by this session'}",
             f"- **Session:** {session}", "",
             "Written automatically when the session was cleared without a handoff. Nothing "
             "here was decided by the session: the request and the last reply are quoted from "
             "its transcript, the rest comes from git.", "",
             "## Goal", "The owner's last request, verbatim:", "", quote(clip(asked, 1500)), "",
             "## Current state",
             "Where the session stopped (its last reply):", "", quote(clip(reply, 1500)), ""]
    if g:
        lines += ["Dirty in git:", "```", clip(g["status"], 2000) or "nothing", "```",
                  "Last commits:", "```", g["log"], "```", ""]
    lines += ["## Next step",
              "1. Check the cards above with `agenfk get <id> --json` and the branch with "
              "`git status`; mark anything that does not match as UNKNOWN.",
              "2. Continue the owner's last request from where the last reply stopped.", ""]
    data = "\n".join(lines).encode("utf-8")
    safe = re.sub(r"[^0-9a-zA-Z-]", "", session)[:64]
    # squad-switch's sealed_copy reads a file and rechecks its sha256 before writing the 0400
    # copy named <session>-<sha256[:12]>.md; the bytes are staged in a private directory first
    stage = tempfile.mkdtemp(prefix="handoff-")
    try:
        staged = os.path.join(stage, "auto.md")
        fd = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        digest = hashlib.sha256(data).hexdigest()
        sealed = SWITCH.sealed_copy(staged, digest, safe)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    if not sealed:
        raise OSError("could not write the automatic handoff")
    return sealed


# ---------------------------------------------------------------- is it still the one?
def handoff_session(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            m = SESSION_LINE.search(fh.read(20_000))
    except OSError:
        return ""
    if m:
        return m.group(1)
    m = re.match(r"(" + ID + r")-", os.path.basename(path))    # sealed and automatic copies
    return m.group(1) if m else ""


def digest(path):
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return ""


def picked_by(session, me):
    """Sessions (8-char prefixes) that already picked up a handoff of `session`."""
    out = []
    try:
        with open(SWAPS, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                m = re.search(r"\b" + re.escape(session[:8]) + r" -> ([0-9a-f]{8}) pickup sent", line)
                if m:
                    out.append(m.group(1))
    except OSError:
        pass
    try:
        with open(PICKUPS, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                f = line.rstrip("\n").split("\t")
                if len(f) >= 3 and f[1] == session[:8]:
                    out.append(f[2])
    except OSError:
        pass
    seen = []
    for s in out:
        if s != me[:8] and s != session[:8] and s not in seen:
            seen.append(s)
    return seen


def candidates(path):
    dirs = {os.path.dirname(os.path.abspath(path)), COPIES}
    return [f for d in dirs for f in glob.glob(os.path.join(d, "*.md"))]


def newest_of(session, files, after, skip):
    best = None
    for f in files:
        if f in skip or not handoff_session(f).startswith(session[:8]):
            continue
        t = os.path.getmtime(f)
        if t > after and (best is None or t > best[0]):
            best = (t, f)
    return best


def transcript_age(session):
    found = glob.glob(os.path.join(PROJECTS, "*", f"{session[:8]}*.jsonl"))
    return min((time.time() - os.path.getmtime(f) for f in found), default=None)


def check(path, me):
    """current: nobody picked it up and nothing newer exists; superseded: another
    session picked it up, or its author wrote a later one (`newest` is the end of
    that chain, `live` a successor still running); stale: newer handoffs of other
    sessions sit in the same project."""
    path = os.path.abspath(path)
    session = handoff_session(path)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {"status": "missing", "session": session}
    same = digest(path)
    files = [f for f in candidates(path) if os.path.abspath(f) != path and digest(f) != same]
    result = {"status": "current", "session": session, "picked_by": [], "newest": None,
              "live": None, "newer_in_project": []}
    if not session:
        return result
    chain, author, after, steps = [], session, mtime, 0
    while steps < 10:
        steps += 1
        later = newest_of(author, files, after, set(chain))
        if later:                                    # the author handed off again
            chain.append(later[1])
            after = later[0]
            continue
        nxt = picked_by(author, me)
        if not nxt:
            break
        if author == session:
            result["picked_by"] = nxt
        author = nxt[-1]
        age = transcript_age(author)
        if age is not None and age < LIVE:
            result["live"] = author
        found = newest_of(author, files, 0, set(chain))
        if not found:
            break
        chain.append(found[1])
        after = found[0]
    if result["picked_by"] or chain:
        result["status"] = "superseded"
        result["newest"] = chain[-1] if chain else None
        return result
    here = os.path.dirname(path)
    if here == os.path.abspath(COPIES):       # every session's copies share it: no project to compare
        return result
    newer = sorted((f for f in files if os.path.dirname(f) == here and os.path.getmtime(f) > mtime),
                   key=os.path.getmtime)
    if newer:
        result["status"] = "stale"
        result["newer_in_project"] = newer[-3:]
    return result


def claim(path, me):
    session = handoff_session(path)
    os.umask(0o077)
    os.makedirs(SQUAD, exist_ok=True)
    with open(PICKUPS, "a", encoding="utf-8") as fh:
        clean = re.sub(r"[\x00-\x1f]", "?", os.path.abspath(path))    # one row per pickup, always
        fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{session[:8]}\t{me[:8]}\t{clean}\n")


def find_transcript(session):
    found = glob.glob(os.path.join(PROJECTS, "*", f"{session}.jsonl"))
    return max(found, key=os.path.getmtime) if found else ""


def parser():
    p = argparse.ArgumentParser(prog="handoff.py", description=__doc__.split("\n\n")[0],
                                epilog="The session id comes from CLAUDE_CODE_SESSION_ID.")
    sub = p.add_subparsers(dest="cmd", metavar="{facts,check,claim,auto}")
    sub.add_parser("facts", help="this session: id, transcript, the cards it ran agenfk on")
    sub.add_parser("check", help="is this handoff still the one to pick up? (JSON)").add_argument("file")
    sub.add_parser("claim", help="record that this session picked the handoff up").add_argument("file")
    a = sub.add_parser("auto", help="write the automatic handoff of a session cleared without one")
    a.add_argument("session")
    a.add_argument("transcript")
    a.add_argument("cwd")
    return p


def main(argv):
    args = parser().parse_args(argv[1:])
    me = os.environ.get("CLAUDE_CODE_SESSION_ID") or ""
    if args.cmd == "facts":
        t = find_transcript(me) if me else ""
        print(json.dumps({"session": me or "UNKNOWN", "transcript": t or "UNKNOWN",
                          "cards": session_cards(t) if t else [],
                          "git": git_facts(os.getcwd())}, indent=2))
        return 0
    if args.cmd == "check":
        print(json.dumps(check(args.file, me), indent=2))
        return 0
    if args.cmd == "claim":
        if not me:
            print("handoff.py: no CLAUDE_CODE_SESSION_ID, the pickup stays unrecorded", file=sys.stderr)
            return 1
        claim(args.file, me)
        return 0
    if args.cmd == "auto":
        path, digest = write_auto(args.session, args.transcript, args.cwd)
        print(path, digest)
        return 0
    parser().print_usage(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
