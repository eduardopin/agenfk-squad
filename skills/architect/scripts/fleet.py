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
import subprocess
import sys
import time

HOME = os.path.expanduser("~")
STATE = os.path.join(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(HOME, ".claude"), "squad", "watch")
AGENFK = os.environ.get("SQUAD_AGENFK") or "agenfk"
CLOSED = {"DONE", "ARCHIVED", "CLOSED", "CANCELLED", "CANCELED"}
LIVE = 600             # seconds: a sub-session touched within this is running
FAN_MIN = 2            # open cards that make a goal worth fanning out
MAX_CARDS = 4          # goal cards read per session


def run_json(args, timeout):
    try:
        r = subprocess.run([*shlex.split(AGENFK), *args], capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
        return json.loads(r.stdout) if r.returncode == 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def open_leaves(cards, agenfk=None, deadline=6.0):
    """The open cards at the bottom of the goal: for each goal card, its open descendants
    that have no open child of their own, or the card itself when it has none. A list of
    {id, type, status, title}; [] on any doubt."""
    global AGENFK
    if agenfk:
        AGENFK = agenfk
    end = time.time() + deadline
    leaves, seen_projects, items = {}, set(), {}
    for card in list(cards)[:MAX_CARDS]:
        left = end - time.time()
        if left <= 0:
            break
        root = run_json(["get", card, "--json"], min(left, 5))
        if not isinstance(root, dict) or not root.get("id"):
            continue
        items[root["id"]] = root
        pid = root.get("projectId")
        if pid and pid not in seen_projects and end - time.time() > 0:
            seen_projects.add(pid)
            listed = run_json(["list", "--project", pid, "--json"], min(end - time.time(), 5))
            for it in listed if isinstance(listed, list) else []:
                if isinstance(it, dict) and it.get("id"):
                    items[it["id"]] = it
    children = {}
    for it in items.values():
        if it.get("parentId"):
            children.setdefault(it["parentId"], []).append(it)

    def is_open(it):
        return str(it.get("status") or "").upper() not in CLOSED and bool(it.get("status"))

    def walk(it):
        kids = [k for k in children.get(it["id"], []) if is_open(k)]
        if not kids:
            if is_open(it):
                leaves[it["id"]] = {k: it.get(k) for k in ("id", "type", "status", "title")}
            return
        for k in kids:
            walk(k)

    for card in list(cards)[:MAX_CARDS]:
        if card in items:
            walk(items[card])
    return list(leaves.values())


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


def fan_out_order(goal, leaves):
    """The order squad-watch gives when the verdict is FAN OUT."""
    ids = ", ".join(f"{c['id']} ({c.get('title') or c.get('type') or ''})".strip() for c in leaves[:8])
    return (f"[squad-watch] FAN OUT: the goal has {len(leaves)} open cards and nothing runs in parallel: {ids}. "
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
        leaves = open_leaves(goal.get("cards") or []) if goal and not goal.get("blocked") else []
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
