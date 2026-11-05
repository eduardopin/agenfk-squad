#!/usr/bin/env python3
"""Answers yes to the file prompts nobody is at the pane to answer.

A session that writes its handoff stops on Claude Code's "Do you want to create
<file>?" dialog, and the answer is always yes. This is the PermissionRequest hook
that answers it. It is default-deny: it prints allow in three cases only, and nothing
for everything else.

A. Write of a NEW file (lexists is false, so a dangling symlink is not new) with an
   inert extension (.md .csv .tsv .log .png .jpg .jpeg .pdf; no .txt), inside the
   project root, which is realpath(payload cwd) and nothing wider (no git toplevel).
   Refused outright when the root is "/", $HOME or an ancestor of $HOME. No path
   component below the root may start with "." or be named node_modules, vendor,
   .terraform, bin, sbin, hooks, githooks or .githooks, and the name may not be
   CLAUDE*.md, AGENT*.md, GEMINI*.md, COPILOT*.md, JULES*.md, QWEN*.md, CRUSH*.md, WARP*.md,
   SKILL.md, CONVENTIONS.md or README*.md. No
   component of the whole absolute path, root included, may start with ".", and the
   path may not sit under a claude dir or ~/.config. B has the same full-path checks.
B. Write (new) or Edit/MultiEdit of .md or .json under <root>/docs/architect/.
C. Write of a NEW .md file under <root>/.claude/handoffs/ or under
   <claude dir>/squad/handoffs/ (claude dir: ~/.claude and CLAUDE_CONFIG_DIR). Never
   Edit there, never another subfolder of squad/: a handoff is read as instructions.

Whatever the case, it never answers for a path that steers the agent or holds a secret, new or not:
settings*.json, anything under a hooks/ dir, CLAUDE.md, AGENTS.md, .claude/agents,
.claude/skills, .claude/commands, .claude/plugins, ~/.claude/*.json and the same under
CLAUDE_CONFIG_DIR, .git/, .env*, *.pem, *.key, id_rsa*, id_ed25519*, credentials*, .aws/,
.ssh/, .mcp.json, and the squad's own source tree (two levels above this file). The
deny list runs on the literal path (made absolute, no symlink followed) and on the
realpath, case-insensitively, and either one matching refuses; the new-file rule needs
both spellings inside the root. The deny list is checked first.

Known race: the hook looks at the disk before the write happens. Planting a symlink in
between needs a Bash call, which prompts.

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
# no .txt: pytest runs test*.txt as doctests, and pip, cmake, conan, apt and buildpacks read
# requirements, CMakeCache, conanfile, packages, apt and runtime .txt files as specs
INERT = {".md", ".csv", ".tsv", ".log", ".png", ".jpg", ".jpeg", ".pdf"}
# instruction files agents load, and READMEs that doctest and rustdoc run
STEER = ("claude*.md", "agent*.md", "gemini*.md", "copilot*.md", "jules*.md", "qwen*.md", "crush*.md",
         "warp*.md", "skill.md", "conventions.md", "readme*.md")
BAD = {"node_modules", "vendor", ".terraform", "bin", "sbin", "hooks", "githooks", ".githooks"}


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


def inside(path, root):
    return under(path, root) and path != root


def clean(path, base):
    """No component below base starts with a dot or names a place that runs or vendors code."""
    return all(not c.startswith(".") and c.lower() not in BAD for c in parts(os.path.relpath(path, base)))


def dotted(path):
    return any(c.startswith(".") for c in parts(path))


def in_config(path, homes):
    """Under a claude dir (~/.claude, CLAUDE_CONFIG_DIR) or ~/.config, however it is spelled."""
    p = low(path)
    return any(under(p, low(c)) for h in homes for c in claude_dirs(h) | {os.path.join(h, ".config")})


def steer(*names):
    return any(fnmatch.fnmatchcase(n.lower(), pat) for n in names for pat in STEER)


def decide(tool, path, cwd, homes):
    """True only when the write is one of the three cases the module docstring lists."""
    if not path or "\0" in path or tool not in WRITERS:
        return False
    literal = os.path.abspath(os.path.join(cwd, path))
    real = os.path.realpath(literal)
    if denied(literal, homes) or denied(real, homes):
        return False
    new = not os.path.lexists(literal) and not os.path.lexists(real)
    ext = os.path.splitext(literal)[1].lower(), os.path.splitext(real)[1].lower()
    names = os.path.basename(literal), os.path.basename(real)
    lroot, rroot = os.path.abspath(cwd), os.path.realpath(cwd)
    if rroot == os.sep or any(under(h, rroot) or under(h, lroot) for h in homes):
        project = False
    else:
        project = inside(literal, lroot) and inside(real, rroot)

    def both(lbase, rbase):
        return inside(literal, lbase) and inside(real, rbase) and clean(literal, lbase) and clean(real, rbase)

    if tool == "Write" and new and ext == (".md", ".md") and not steer(*names):          # C
        if project and both(os.path.join(lroot, ".claude", "handoffs"), os.path.join(rroot, ".claude", "handoffs")):
            return True
        for h in homes:
            for c in claude_dirs(h):
                if both(os.path.join(c, "squad", "handoffs"), os.path.join(os.path.realpath(c), "squad", "handoffs")):
                    return True
    # A and B: nothing in the whole path may be a dot folder, and no config folder of the user's
    if not project or dotted(literal) or dotted(real) or in_config(literal, homes) or in_config(real, homes):
        return False
    if ext[0] == ext[1] and ext[0] in (".md", ".json") and both(os.path.join(lroot, "docs", "architect"),   # B
                                                                os.path.join(rroot, "docs", "architect")):
        if tool in ("Edit", "MultiEdit") or (tool == "Write" and new):
            return True
    return (tool == "Write" and new and ext[0] in INERT and ext[1] in INERT and not steer(*names)   # A
            and clean(literal, lroot) and clean(real, rroot))


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
