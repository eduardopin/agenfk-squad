#!/usr/bin/env python3
"""squad-allow answers yes to the file prompts that nobody is at the pane to answer, and to no others.

Self-checking: the exit code is the verdict. Runs in a throwaway HOME and never touches ~/.claude.

    python3 tests/test_allow.py
"""
import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(ROOT, "hooks", "squad-allow.py")
SID = "aaaaaaaa-1111-2222-3333-444444444444"
ALLOW = {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": {"behavior": "allow"}}}

_DIRS = []


def tmp():
    d = os.path.realpath(tempfile.mkdtemp(prefix="allow-"))
    _DIRS.append(d)
    return d


@atexit.register
def _cleanup():
    for d in _DIRS:
        shutil.rmtree(d, ignore_errors=True)


def run(home, payload, env=None, raw=None):
    e = dict(os.environ, HOME=home, USERPROFILE=home)
    e.pop("SQUAD_ALLOW", None)
    e.update(env or {})
    r = subprocess.run([sys.executable, HOOK], input=raw if raw is not None else json.dumps(payload),
                       capture_output=True, text=True, env=e, timeout=30)
    assert r.returncode == 0, r
    return r.stdout.strip()


def ask(home, tool, path, cwd=None, env=None, key="file_path"):
    return run(home, {"session_id": SID, "cwd": cwd or home, "hook_event_name": "PermissionRequest",
                      "tool_name": tool, "tool_input": {key: path}}, env)


def allowed(out):
    return bool(out) and json.loads(out) == ALLOW


def touch(path, text="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)
    return path


def test_creating_a_new_file_is_allowed():
    h = tmp()
    assert allowed(ask(h, "Write", os.path.join(h, "proj", "notes.md")))


def test_a_relative_path_is_read_against_the_session_cwd():
    h = tmp()
    assert allowed(ask(h, "Write", "new.md", cwd=os.path.join(h, "proj")))
    touch(os.path.join(h, "proj", "old.md"))
    assert ask(h, "Write", "old.md", cwd=os.path.join(h, "proj")) == ""


def test_overwriting_an_existing_file_outside_the_squad_folders_is_left_to_the_person():
    h = tmp()
    assert ask(h, "Write", touch(os.path.join(h, "proj", "src", "app.py"))) == ""


def test_overwriting_in_the_squad_folders_is_allowed():
    h = tmp()
    for rel in (("proj", ".claude", "handoffs", "a.md"), (".claude", "squad", "notes", "n.md"),
                ("proj", "docs", "architect", "plan.md")):
        p = touch(os.path.join(h, *rel))
        assert allowed(ask(h, "Write", p)), rel
        assert allowed(ask(h, "Edit", p)), rel
    assert allowed(ask(h, "NotebookEdit", touch(os.path.join(h, "proj", ".claude", "handoffs", "n.ipynb")),
                       key="notebook_path"))


def test_edit_outside_the_squad_folders_is_left_to_the_person():
    h = tmp()
    for tool in ("Edit", "MultiEdit"):
        assert ask(h, tool, touch(os.path.join(h, "proj", "src", "app.py"))) == ""


def test_steering_and_secret_paths_are_never_allowed_even_when_new():
    h = tmp()
    rels = [
        "settings.json", "settings.local.json", ".claude/settings.json", "proj/.claude/settings.local.json",
        "proj/hooks/x.py", "proj/.claude/hooks/x.sh", "CLAUDE.md", "proj/CLAUDE.md", "proj/AGENTS.md",
        "proj/.claude/agents/a.md", "proj/.claude/skills/s/SKILL.md", "proj/.claude/commands/c.md",
        "proj/.claude/plugins/p.json", ".claude/keybindings.json", "proj/.git/config", "proj/.git/hooks/pre-commit",
        "proj/.env", "proj/.env.local", "proj/cert.pem", "proj/server.key", "proj/id_rsa", "proj/id_rsa.pub",
        "proj/id_ed25519", "proj/credentials", "proj/credentials.json", ".aws/config", "proj/.aws/x",
        ".ssh/authorized_keys", "proj/.ssh/config", "proj/.mcp.json",
        # inside a squad folder, the deny list still wins
        "proj/.claude/handoffs/settings.json", "proj/.claude/handoffs/.env", "proj/docs/architect/CLAUDE.md",
        ".claude/squad/hooks/x.py", ".claude/squad/id_rsa",
    ]
    for rel in rels:
        p = os.path.join(h, *rel.split("/"))
        assert ask(h, "Write", p) == "", rel
        touch(p)
        assert ask(h, "Write", p) == "", rel


def test_a_symlink_into_a_denied_folder_is_not_allowed():
    h = tmp()
    hooks = os.path.join(h, "proj", "hooks")
    os.makedirs(hooks)
    handoffs = os.path.join(h, "proj", ".claude", "handoffs")
    os.makedirs(handoffs)
    os.symlink(hooks, os.path.join(handoffs, "dir"))
    assert ask(h, "Write", os.path.join(handoffs, "dir", "new.py")) == ""
    os.symlink(os.path.join(hooks, "dangling.py"), os.path.join(handoffs, "file.md"))
    assert ask(h, "Write", os.path.join(handoffs, "file.md")) == ""
    os.symlink(os.path.join(h, "proj", "CLAUDE.md"), os.path.join(h, "proj", "notes.md"))
    assert ask(h, "Write", os.path.join(h, "proj", "notes.md")) == ""


def test_dot_dot_traversal_is_not_allowed():
    h = tmp()
    os.makedirs(os.path.join(h, "proj", ".claude", "handoffs"))
    p = os.path.join(h, "proj", ".claude", "handoffs", "..", "..", "hooks", "x.py")
    assert ask(h, "Write", p) == ""
    p = os.path.join(h, "proj", "docs", "architect", "..", "..", ".claude", "settings.json")
    assert ask(h, "Write", p) == ""
    # a traversal that lands in a squad folder is judged by where it lands
    touch(os.path.join(h, "proj", ".claude", "handoffs", "a.md"))
    assert allowed(ask(h, "Write", os.path.join(h, "proj", "src", "..", ".claude", "handoffs", "a.md")))


def test_other_tools_print_nothing():
    h = tmp()
    p = os.path.join(h, "proj", "new.md")
    for tool in ("Bash", "WebFetch", "Read", "mcp__x__write"):
        assert run(h, {"session_id": SID, "cwd": h, "tool_name": tool,
                       "tool_input": {"file_path": p, "command": "echo hi > " + p}}) == "", tool


def test_squad_allow_zero_turns_it_off_and_logs_nothing():
    h = tmp()
    assert ask(h, "Write", os.path.join(h, "proj", "new.md"), env={"SQUAD_ALLOW": "0"}) == ""
    assert not os.path.exists(os.path.join(h, ".claude", "squad", "allow.log"))


def test_every_allow_is_logged_and_a_refusal_is_not():
    h = tmp()
    p = os.path.join(h, "proj", "new.md")
    assert allowed(ask(h, "Write", p))
    assert ask(h, "Write", os.path.join(h, "proj", "CLAUDE.md")) == ""
    with open(os.path.join(h, ".claude", "squad", "allow.log"), encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    assert len(lines) == 1, lines
    assert SID in lines[0] and "Write" in lines[0] and p in lines[0], lines


def test_bad_input_prints_nothing_and_exits_zero():
    h = tmp()
    for raw in ("", "not json", "[]", "null", '{"tool_name": "Write"}', '{"tool_name": "Write", "tool_input": []}',
                '{"tool_name": "Write", "tool_input": {"file_path": 7}}',
                '{"tool_name": "Write", "tool_input": {"file_path": "a\\u0000b"}}'):
        assert run(h, None, raw=raw) == "", raw


def main():
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        fn()
        print("ok", name)
    print(f"{len(tests)} passed")


if __name__ == "__main__":
    main()
