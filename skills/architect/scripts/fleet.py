#!/usr/bin/env python3
"""Every live session at a glance, and which goals should be running in parallel.

    fleet.py           a table for a person
    fleet.py --json    the same rows for a script

One row per main session squad-watch has seen (~/.claude/squad/watch/<session>.main.json):
its repository, its open goal (`[squad] GOAL:`), the open cards under that goal in AgEnFK,
its live sub-sessions (a sub-session state file touched in the last 10 min), how long it has
been quiet, and a verdict:

    FAN OUT   a goal with 2 or more open cards and nothing running in parallel: run
              parallel-plan's waves on those cards and dispatch wave 1 together
    CARRY     a goal with work left that is already fanned out, or a single card
    BLOCKED   the session ended with `[squad] BLOCKED:`; it waits for the owner
    STOPPED   carry on let go (no progress, or the cap); the owner decides
    -         no goal

squad-watch and squad-wake import this file for the same reading. Read-only: it creates,
moves and dispatches nothing. Any doubt reading AgEnFK (CLI missing, server down, no answer
within the deadline) counts as no open card, so a doubt never orders a fan-out.
Python standard library only.
"""
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

HOME = os.path.expanduser("~")
# the hooks write here whatever CLAUDE_CONFIG_DIR says (squad-watch.py, squad-wake.py), so this reads here
STATE = os.path.join(HOME, ".claude", "squad", "watch")
AGENFK = os.environ.get("SQUAD_AGENFK") or "agenfk"
CLOSED = {"DONE", "ARCHIVED", "CLOSED", "CANCELLED", "CANCELED"}
# not open work: parked cards and ideas wait for the owner, as parallel-plan's waves.py takes them
PARKED = {"BLOCKED", "PAUSED", "TRASHED", "IDEAS"}
LIVE = 600             # seconds: a sub-session touched within this is running
FAN_MIN = 2            # open cards that make a goal worth fanning out
FANNED = 6 * 3600      # seconds: a card whose fanout.py session started within this is taken as running
TITLE_MAX = 80         # characters of a card title in the order: it is one line


def agenfk_argv(cmd=None, windows=os.name == "nt"):
    """SQUAD_AGENFK (or "agenfk") as argv, split as hooks/squad-watch.py agenfk_argv does: on Windows a
    backslash is a path separator, not an escape, and the quotes stay for us to strip; the first word
    goes through shutil.which so that an agenfk.cmd launcher runs by name. [] when it does not parse."""
    try:
        words = shlex.split(AGENFK if cmd is None else cmd, posix=not windows)
    except ValueError:
        return []
    if windows:
        words = [w[1:-1] if len(w) >= 2 and w[0] == w[-1] and w[0] in "\"'" else w for w in words]
    if words:
        words[0] = shutil.which(words[0]) or words[0]
    return words


def call_json(args, timeout):
    """(parsed JSON or None, cut): cut is True when the call ran out of time, not when it failed."""
    if timeout <= 0:
        return None, True
    try:
        argv = agenfk_argv()
        if not argv:
            return None, False
        r = subprocess.run([*argv, *args], capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
        return (json.loads(r.stdout) if r.returncode == 0 else None), False
    except subprocess.TimeoutExpired:
        return None, True
    except (OSError, ValueError):
        return None, False


def run_json(args, timeout):
    return call_json(args, timeout)[0]


def goal_leaves(cards, agenfk=None, deadline=6.0):
    """(leaves, read, total): the open cards at the bottom of the goal and how many of the `total`
    named cards were read. For each goal card, its open descendants that have no open child of
    their own, or the card itself when it has none. Every named card is read, in parallel, with
    `deadline` seconds as the only bound; `read` < `total` means the deadline cut the read short.
    Leaves are {id, type, status, title}; [] on any doubt."""
    global AGENFK
    if agenfk:
        AGENFK = agenfk
    cards = list(dict.fromkeys(cards))
    end = time.time() + deadline
    if not cards:
        return [], 0, 0

    def left():
        return min(end - time.time(), 5)

    def timed(args):
        return call_json(args, left())      # the time left when the call starts, not when it was queued

    leaves, items, unread = {}, {}, set()
    with ThreadPoolExecutor(max_workers=min(len(cards), 8)) as pool:
        # a project's list starts as soon as the first card of it is read, so a slow card does not
        # eat the time of the others' lists
        pending = {pool.submit(timed, ["get", c, "--json"]): ("get", c) for c in cards}
        listed_projects, project_of = set(), {}
        while pending:
            for fut in as_completed(list(pending)):
                kind, key = pending.pop(fut)
                got, cut = fut.result()
                if kind == "get":
                    if cut:
                        unread.add(key)
                    if isinstance(got, dict) and got.get("id"):
                        items[got["id"]] = got
                        project_of[key] = pid = got.get("projectId")
                        if pid and pid not in listed_projects:
                            listed_projects.add(pid)
                            pending[pool.submit(timed, ["list", "--project", pid, "--json"])] = ("list", pid)
                else:
                    if cut:    # without the project's cards a goal card looks like a leaf: it is not read
                        unread.update(c for c, pid in project_of.items() if pid == key)
                    for it in got if isinstance(got, list) else []:
                        if isinstance(it, dict) and it.get("id"):
                            items.setdefault(it["id"], it)
                break          # as_completed was built before the new list calls: rebuild it
    children = {}
    for it in items.values():
        if it.get("parentId"):
            children.setdefault(it["parentId"], []).append(it)

    def is_open(it):
        return str(it.get("status") or "").upper() not in CLOSED | PARKED and bool(it.get("status"))

    def walk(it):
        if str(it.get("status") or "").upper() in PARKED:
            return                      # a parked card takes its whole subtree out, as waves.py does
        kids = [k for k in children.get(it["id"], []) if is_open(k)]
        if not kids:
            if is_open(it):
                leaves[it["id"]] = {k: it.get(k) for k in ("id", "type", "status", "title")}
            return
        for k in kids:
            walk(k)

    for card in cards:
        if card in items:
            walk(items[card])
    return list(leaves.values()), len(cards) - len(unread), len(cards)


def open_leaves(cards, agenfk=None, deadline=6.0):
    """goal_leaves without the count."""
    return goal_leaves(cards, agenfk, deadline)[0]


def live_subs(session, state=None, now=None):
    """Sub-sessions of `session` whose state file moved in the last LIVE seconds."""
    state, now, n = state or STATE, now or time.time(), 0
    prefix = f"{session}."
    try:
        for name in os.listdir(state):
            if (name.startswith(prefix) and name.endswith(".json") and not name.endswith(".main.json")
                    and now - os.path.getmtime(os.path.join(state, name)) < LIVE):
                n += 1
    except OSError:
        pass
    return n


def fanned_cards(state=None, now=None):
    """Cards fanout.py opened a session for in the last FANNED seconds (~/.claude/squad/fanout.log).
    Those sessions are not `<parent>.<agent>` sub-sessions, so live_subs never sees them."""
    state, now, out = state or STATE, now or time.time(), set()
    try:
        with open(os.path.join(os.path.dirname(state), "fanout.log"), encoding="utf-8") as fh:
            lines = fh.read().splitlines()[-500:]
    except OSError:
        return out
    for ln in lines:
        parts = ln.split(" ", 3)
        if len(parts) < 4 or ": session card-" not in parts[3]:
            continue                                  # a failed launch is not a running session
        try:
            when = time.mktime(time.strptime(parts[0] + " " + parts[1], "%Y-%m-%d %H:%M:%S"))
        except (ValueError, OverflowError):
            continue
        if 0 <= now - when < FANNED:
            out.add(parts[2])
    return out


def unfanned(leaves, state=None, now=None):
    """The leaves that do not already have a session of their own."""
    done = fanned_cards(state, now)
    return [c for c in leaves if c.get("id") not in done] if done else list(leaves)


def verdict(goal, leaves, subs):
    if not isinstance(goal, dict):
        return "-"
    if goal.get("blocked"):
        return "BLOCKED"
    if goal.get("released"):
        return "STOPPED"
    if len(leaves) >= FAN_MIN and subs == 0:
        return "FAN OUT"
    return "CARRY"


def fan_out_order(goal, leaves, read=None, total=None):
    """The order squad-watch gives when the verdict is FAN OUT. `read` of `total` named cards, when
    the deadline cut the read short, is said in the order."""
    def label(c):
        t = " ".join(str(c.get("title") or c.get("type") or "").split())     # one line, whatever the title holds
        return f"{c['id']} ({t[:TITLE_MAX].rstrip()})"
    ids = ", ".join(label(c) for c in leaves[:8])
    part = f" (read {read} of the {total} named cards)" if None not in (read, total) and read < total else ""
    return (f"[squad-watch] FAN OUT: the goal has {len(leaves)} open cards{part} and nothing runs in parallel: {ids}. "
            "Parallel is the default: run parallel-plan's waves on these cards (skills/parallel-plan) and "
            "dispatch wave 1 in one message, one front per card in its own worktree (`github` draws the map), "
            "at most 6. Pick the agent and the skills by card type: infra for Terraform and CI, developer or "
            "frontend for code, writer for documents, with the project's own agent first. A card that will "
            "not fit a sub-agent (over 120k of context) gets its own session: `python3 "
            "~/.claude/skills/architect/scripts/fanout.py --card <id> --worktree <path>`. Keep integrating "
            "here while the fronts run.")


def rows(state=None, now=None):
    state, now, out = state or STATE, now or time.time(), []
    try:
        names = sorted(n for n in os.listdir(state) if n.endswith(".main.json"))
    except OSError:
        return out
    for name in names:
        path = os.path.join(state, name)
        try:
            with open(path, encoding="utf-8") as fh:
                st = json.load(fh)
        except (OSError, ValueError):
            continue
        if not isinstance(st, dict):
            continue
        session = name[: -len(".main.json")]
        goal = st.get("goal") if isinstance(st.get("goal"), dict) else None
        leaves = unfanned(open_leaves(goal.get("cards") or [])) if goal and not goal.get("blocked") else []
        subs = live_subs(session, state, now)
        out.append({
            "session": session,
            "repo": st.get("repo") or st.get("cwd") or "",
            "goal": (goal or {}).get("text", ""),
            "open_cards": len(leaves),
            "cards": [c["id"] for c in leaves],
            "live_subs": subs,
            "quiet_s": int(now - os.path.getmtime(path)),
            "verdict": verdict(goal, leaves, subs),
        })
    return out


def table(rs):
    head = ["session", "verdict", "open", "subs", "quiet", "repo", "goal"]
    body = [[r["session"][:8], r["verdict"], str(r["open_cards"]), str(r["live_subs"]),
             f"{r['quiet_s'] // 60} min", os.path.basename(os.path.dirname(r["repo"].rstrip("/"))
                                                          if r["repo"].endswith(".git") else r["repo"]),
             r["goal"][:60]] for r in rs]
    widths = [max(len(x) for x in col) for col in zip(head, *body)]
    lines = ["  ".join(c.ljust(w) for c, w in zip(row, widths)).rstrip() for row in [head, *body]]
    return "\n".join(lines) if body else "no session seen by squad-watch"


def main(argv):
    rs = rows()
    print(json.dumps(rs, indent=2) if "--json" in argv else table(rs))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
