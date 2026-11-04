#!/usr/bin/env python3
"""Opens one card in its own Claude session, for a card that will not fit a sub-agent.

    fanout.py --card <agenfk id> --worktree <path> [--title <text>]

The default parallel front is an in-process sub-agent, which squad-watch measures and caps.
A card sized over about 120k of context gets a session of its own instead: a new herdr tab
in the card's worktree, Claude Code started in it, and the goal handed over as the first
prompt, so that session's carry on works the card to the end by itself.

Outside herdr (no HERDR_PANE_ID) it opens nothing and prints the command to run by hand,
exit 2. It refuses a folder that is not a git checkout (exit 1). Every launch goes to
~/.claude/squad/fanout.log. Python standard library only.
"""
import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time

HOME = os.path.expanduser("~")
BASE = os.path.join(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(HOME, ".claude"), "squad")
HERDR = os.environ.get("HERDR_BIN_PATH") or "herdr"


AGENFK_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def brief(card, title):
    title = " ".join(str(title or "").split())[:120]      # the goal is one line: a newline would end it
    what = f" ({title})" if title else ""
    return (f"[squad] GOAL: deliver card {card}{what}\n"
            f"Work AgEnFK card {card} in this worktree through its flow, to the end: read it with "
            f"`agenfk get {card} --json` and the flow with `agenfk flow show --json`, pass the gatekeeper with "
            "--item-id before editing, and fan out where its own files allow. Stop only by ending a reply "
            "with `[squad] DONE: <evidence>`, `[squad] BLOCKED: <what only the owner can do>` or "
            "`[squad] END: <why nothing is left>`.")


def herdr(*args, timeout=60):
    try:
        r = subprocess.run([HERDR, *args], capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
        return r.returncode, r.stdout
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""


def log(msg):
    try:
        os.makedirs(BASE, exist_ok=True)
        with open(os.path.join(BASE, "fanout.log"), "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except OSError:
        pass


def is_checkout(path):
    try:
        r = subprocess.run(["git", "-C", path, "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                           timeout=5, stdin=subprocess.DEVNULL)
        return r.returncode == 0 and os.path.realpath(r.stdout.strip()) == os.path.realpath(path)
    except (OSError, subprocess.TimeoutExpired):
        return False


def is_main_checkout(path):
    """True when `path` is the repository's own checkout, not a linked worktree of it."""
    try:
        out = [subprocess.run(["git", "-C", path, "rev-parse", flag], capture_output=True, text=True, timeout=5,
                              stdin=subprocess.DEVNULL).stdout.strip() for flag in ("--git-dir", "--git-common-dir")]
    except (OSError, subprocess.TimeoutExpired):
        return False
    return bool(out[0]) and os.path.realpath(os.path.join(path, out[0])) == os.path.realpath(os.path.join(path, out[1]))


def main(argv):
    ap = argparse.ArgumentParser(description="Open one AgEnFK card in its own Claude session in herdr.")
    ap.add_argument("--card", required=True)
    ap.add_argument("--worktree", required=True)
    ap.add_argument("--title", default="")
    a = ap.parse_args(argv)
    if not AGENFK_ID.match(a.card):
        print(f"{a.card!r} is not an AgEnFK card id (a uuid): `agenfk list --json` has it.")
        return 1
    wt = os.path.abspath(a.worktree)
    if not is_checkout(wt):
        print(f"{wt} is not the top of a git checkout: create the card's worktree first (github agent).")
        return 1
    if is_main_checkout(wt):
        print(f"warning: {wt} is the repository's main checkout, not a worktree of the card: the new session "
              "would edit it next to your own. Create the card's worktree first (github agent).")
    text = brief(a.card, a.title)
    if not os.environ.get("HERDR_PANE_ID"):
        print("Not inside herdr: open the session by hand, in a new terminal:\n"
              f"  cd {shlex.quote(wt)} && claude {shlex.quote(text)}")
        return 2
    label = f"card-{a.card[:8]}"
    rc, out = herdr("tab", "create", "--cwd", wt, "--label", label, "--no-focus")
    try:
        pane = json.loads(out)["result"]["root_pane"]["pane_id"]
    except (ValueError, KeyError, TypeError):
        pane = ""
    if rc != 0 or not pane:
        log(f"{a.card} {wt}: tab create failed")
        print("herdr did not open the tab; open the session by hand:\n"
              f"  cd {shlex.quote(wt)} && claude {shlex.quote(text)}")
        return 1
    rc, _ = herdr("agent", "start", label, "--kind", "claude", "--pane", pane, timeout=90)
    if rc != 0:
        log(f"{a.card} {wt}: claude did not start in {pane}")
        print(f"Claude did not start in herdr pane {pane}; start it there and paste the goal:\n{text}")
        return 1
    rc, _ = herdr("agent", "prompt", label, text)
    log(f"{a.card} {wt}: session {label} in {pane}" + ("" if rc == 0 else ", the goal prompt failed"))
    print(f"opened {label} in herdr pane {pane}, worktree {wt}" + ("" if rc == 0 else
          f"; the goal prompt failed, paste it:\n{text}"))
    return 0 if rc == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
