#!/usr/bin/env python3
"""Answers yes to the file prompts nobody is at the pane to answer.

A session that writes its handoff stops on Claude Code's "Do you want to create
<file>?" dialog, and the answer is always yes. This is the PermissionRequest hook
that answers it, for a narrow set of writes:

- tool Write, when the target does not exist yet (a new file), or
- Write, Edit, MultiEdit or NotebookEdit, when the target is inside the squad's own
  working folders: <any>/.claude/handoffs/, ~/.claude/squad/, docs/architect/.

It never answers for a path that steers the agent or holds a secret, new or not:
settings*.json, anything under a hooks/ dir, CLAUDE.md, AGENTS.md, .claude/agents,
.claude/skills, .claude/commands, .claude/plugins, ~/.claude/*.json, .git/, .env*,
*.pem, *.key, id_rsa*, id_ed25519*, credentials*, .aws/, .ssh/, .mcp.json. The path is
resolved with realpath (symlinks and ..) before it is judged, so a link or a
traversal cannot dodge the list. The deny list wins over the squad folders.

It never answers deny. When it is unsure (another tool, a path it cannot resolve,
malformed input, any error) it prints nothing and the normal dialog appears, and it
exits 0 either way: it never fails or blocks a session. SQUAD_ALLOW=0 turns it off.
Every allow is appended to ~/.claude/squad/allow.log (time, session, tool, path);
if the line cannot be written the hook prints nothing.

Event, input and output shape: https://code.claude.com/docs/en/hooks
(PermissionRequest input, PermissionRequest decision control). The hook receives
tool_name and tool_input; "allow" is
{"hookSpecificOutput": {"hookEventName": "PermissionRequest",
 "decision": {"behavior": "allow"}}}. The harness still applies deny and ask rules
after an allow. Standard library only.
"""
import fnmatch
import json
import os
import sys
import time

WRITERS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
NAMES = ("settings*.json", "claude.md", "agents.md", ".env*", "*.pem", "*.key", "id_rsa*", "id_ed25519*",
         "credentials*", ".mcp.json")
DIRS = {"hooks", ".git", ".aws", ".ssh"}
CLAUDE_DIRS = {"agents", "skills", "commands", "plugins"}


def parts(path):
    return [p for p in path.split(os.sep) if p]


def has_pair(comps, first, second):
    return any(a == first and b == second for a, b in zip(comps, comps[1:]))


def denied(real, home):
    comps = parts(real)
    dirs, name = comps[:-1], comps[-1].lower()
    if DIRS & set(c.lower() for c in comps):
        return True
    if any(fnmatch.fnmatchcase(name, n) for n in NAMES):
        return True
    if any(has_pair(dirs, ".claude", d) for d in CLAUDE_DIRS):
        return True
    return os.path.dirname(real) == os.path.join(home, ".claude") and name.endswith(".json")


def in_squad_folder(real, home):
    comps = parts(real)[:-1]
    return (has_pair(comps, ".claude", "handoffs") or has_pair(comps, "docs", "architect")
            or real.startswith(os.path.join(home, ".claude", "squad") + os.sep))


def decide(tool, path, cwd, home):
    """True only when the write is safe to wave through."""
    if not path or "\0" in path:
        return False
    if not os.path.isabs(path):
        path = os.path.join(cwd, path)
    real = os.path.realpath(path)
    if denied(real, home):
        return False
    if in_squad_folder(real, home):
        return True
    return tool == "Write" and not os.path.exists(real)


def main():
    if os.environ.get("SQUAD_ALLOW") == "0":
        return
    data = json.loads(sys.stdin.read())
    tool = data.get("tool_name")
    if tool not in WRITERS:
        return
    ti = data["tool_input"]
    path = ti.get("notebook_path") if tool == "NotebookEdit" else ti.get("file_path")
    if not isinstance(path, str):
        return
    home = os.path.realpath(os.path.expanduser("~"))
    cwd = data.get("cwd") if isinstance(data.get("cwd"), str) else os.getcwd()
    if not decide(tool, path, cwd, home):
        return
    log = os.path.join(home, ".claude", "squad", "allow.log")
    os.makedirs(os.path.dirname(log), exist_ok=True)
    line = "\t".join([time.strftime("%Y-%m-%dT%H:%M:%S%z"), str(data.get("session_id", "")), tool,
                      path.replace("\n", " ")])
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                                             "decision": {"behavior": "allow"}}}))


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        pass
    sys.exit(0)
