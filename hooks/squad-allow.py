#!/usr/bin/env python3
"""Answers yes to the file prompts nobody is at the pane to answer.

A session that writes its handoff stops on Claude Code's "Do you want to create
<file>?" dialog, and the answer is always yes. This is the PermissionRequest hook
that answers it, for a narrow set of writes:

- Write, Edit, MultiEdit or NotebookEdit inside the squad's own folders:
  <any>/.claude/handoffs/, ~/.claude/squad/ (and $CLAUDE_CONFIG_DIR/squad/), and
  docs/architect/ inside the session's project;
- Write creating a plain new file inside the session's project: the payload cwd
  and its git toplevel (git asked for at most 2 s), no path component below that
  root starting with a dot, and a basename that is not run or read later
  (CLAUDE*.md, AGENTS*.md, Makefile, Dockerfile*, *.sh, package.json, pyproject.toml,
  setup.py, setup.cfg, conftest.py, *.pth, .envrc, .*rc, .*profile and the like).

It never answers for a path that steers the agent or holds a secret, new or not:
settings*.json, anything under a hooks/ dir, CLAUDE.md, AGENTS.md, .claude/agents,
.claude/skills, .claude/commands, .claude/plugins, ~/.claude/*.json and the same under
CLAUDE_CONFIG_DIR, .git/, .env*, *.pem, *.key, id_rsa*, id_ed25519*, credentials*, .aws/,
.ssh/, .mcp.json, and the squad's own source tree (two levels above this file). The
deny list runs on the literal path (made absolute, no symlink followed) and on the
realpath, case-insensitively, and either one matching refuses; the new-file rule needs
both spellings inside the project. The deny list wins over the squad folders.

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
import subprocess
import sys
import time

WRITERS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
NAMES = ("settings*.json", "claude.md", "agents.md", ".env*", "*.pem", "*.key", "id_rsa*", "id_ed25519*",
         "credentials*", ".mcp.json")
DIRS = {"hooks", ".git", ".aws", ".ssh"}
CLAUDE_DIRS = {"agents", "skills", "commands", "plugins"}
# a new file with one of these names is read or run by something later: not a plain file
RUNS = ("claude*.md", "agents*.md", "makefile", "gnumakefile", "justfile", "dockerfile*", "*.sh", "package.json",
        "pyproject.toml", "setup.py", "setup.cfg", "conftest.py", "*.pth", ".envrc", ".*rc", ".*profile")


def parts(path):
    return [p for p in path.split(os.sep) if p]


def low(path):
    return os.path.normcase(path).lower()


def has_pair(comps, first, second):
    return any(a == first and b == second for a, b in zip(comps, comps[1:]))


def under(path, root):
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def source_tree():
    """The squad clone this hook runs from, two levels above hooks/squad-allow.py."""
    return os.path.dirname(os.path.dirname(os.path.realpath(__file__)))


def claude_dirs(home):
    dirs = {os.path.join(home, ".claude")}
    cfg = os.environ.get("CLAUDE_CONFIG_DIR")
    if cfg:
        dirs.add(os.path.abspath(os.path.expanduser(cfg)))
        dirs.add(os.path.realpath(os.path.expanduser(cfg)))
    return dirs


def denied(path, homes):
    """One spelling of the path (literal or resolved) against the deny list, case-insensitively."""
    comps = parts(low(path))
    dirs, name = comps[:-1], comps[-1]
    if DIRS & set(comps):
        return True
    if any(fnmatch.fnmatchcase(name, n) for n in NAMES):
        return True
    if any(has_pair(dirs, ".claude", d) for d in CLAUDE_DIRS):
        return True
    if under(low(path), low(source_tree())):
        return True
    for home in homes:
        for c in claude_dirs(home):
            lc = low(c)
            if under(low(path), lc):
                rest = parts(low(path)[len(lc):])
                if rest and rest[0] in CLAUDE_DIRS or (len(rest) == 1 and rest[0].endswith(".json")):
                    return True
    return False


def squad_folder(real, homes):
    comps = parts(real)[:-1]
    if has_pair(comps, ".claude", "handoffs"):
        return True
    return any(under(real, os.path.join(c, "squad")) for h in homes for c in claude_dirs(h))


def project_roots(cwd):
    """The session's project: the resolved cwd, widened to its git toplevel when git answers in time."""
    roots = [os.path.realpath(cwd)]
    try:
        r = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                           timeout=2, stdin=subprocess.DEVNULL)
        top = r.stdout.strip()
        if r.returncode == 0 and top:
            roots.append(os.path.realpath(top))
    except (OSError, subprocess.TimeoutExpired, ValueError):
        pass
    return roots


def inside(path, roots):
    """The root that holds path, or None; commonpath on resolved paths."""
    for root in sorted(roots, key=len):
        try:
            if os.path.commonpath([path, root]) == root:
                return root
        except ValueError:
            pass
    return None


def plain_new_file(literal, real, roots):
    root = inside(real, roots)
    if root is None or inside(literal, roots) is None:
        return False
    for p, r in ((real, root), (literal, inside(literal, roots))):
        rel = parts(os.path.relpath(p, r))
        if not rel or rel[0] == ".." or any(c.startswith(".") for c in rel):
            return False
    name = os.path.basename(real).lower()
    return not any(fnmatch.fnmatchcase(name, n) for n in RUNS) and \
        not any(fnmatch.fnmatchcase(os.path.basename(literal).lower(), n) for n in RUNS)


def decide(tool, path, cwd, homes):
    """True only when the write is safe to wave through."""
    if not path or "\0" in path:
        return False
    literal = os.path.abspath(os.path.join(cwd, path))
    real = os.path.realpath(literal)
    if denied(literal, homes) or denied(real, homes):
        return False
    roots = project_roots(cwd)
    if squad_folder(real, homes):
        return True
    root = inside(real, roots)
    if root is not None and has_pair(parts(real)[:-1], "docs", "architect"):
        return True
    return tool == "Write" and not os.path.exists(real) and plain_new_file(literal, real, roots)


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
    raw = os.path.abspath(os.path.expanduser("~"))
    home = os.path.realpath(raw)
    homes = {raw, home}
    cwd = data.get("cwd") if isinstance(data.get("cwd"), str) else os.getcwd()
    if not decide(tool, path, cwd, homes):
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
