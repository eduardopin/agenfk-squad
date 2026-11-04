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


def test_a_new_file_must_be_a_plain_file_inside_the_project():
    h = tmp()
    proj = os.path.join(h, "proj")
    os.makedirs(proj)
    assert allowed(ask(h, "Write", os.path.join(proj, "src", "notes.md"), cwd=proj))
    refused = [".github/workflows/x.yml", ".vscode/tasks.json", ".husky/pre-commit", ".devcontainer/d.json",
               ".claude/notes.md", ".pre-commit-config.yaml", "src/.hidden", "Makefile", "GNUmakefile", "Justfile",
               "Dockerfile", "Dockerfile.dev", "run.sh", "src/RUN.SH", "package.json", "pyproject.toml", "setup.py",
               "setup.cfg", "conftest.py", "tests/conftest.py", "x.pth", ".envrc", ".bashrc", ".zprofile",
               "CLAUDE.local.md", "AGENTS.override.md"]
    for rel in refused:
        assert ask(h, "Write", os.path.join(proj, *rel.split("/")), cwd=proj) == "", rel


def test_a_new_file_outside_the_project_is_not_allowed():
    h = tmp()
    proj = os.path.join(h, "proj")
    os.makedirs(proj)
    elsewhere = tmp()
    for p in (os.path.join(h, ".newrc"), os.path.join(h, "other.txt"), os.path.join(elsewhere, "x.txt")):
        assert ask(h, "Write", p, cwd=proj) == "", p


def test_the_project_is_the_git_toplevel_of_the_cwd():
    h = tmp()
    proj = os.path.join(h, "proj")
    os.makedirs(os.path.join(proj, "sub"))
    subprocess.run(["git", "init", "-q", proj], check=True, capture_output=True)
    assert allowed(ask(h, "Write", os.path.join(proj, "top.md"), cwd=os.path.join(proj, "sub")))
    assert ask(h, "Write", os.path.join(h, "beside.md"), cwd=os.path.join(proj, "sub")) == ""


def test_the_squad_folders_outside_the_project_are_still_allowed():
    h = tmp()
    proj = os.path.join(h, "proj")
    os.makedirs(proj)
    other = tmp()
    assert allowed(ask(h, "Write", os.path.join(other, ".claude", "handoffs", "a.md"), cwd=proj))
    assert allowed(ask(h, "Write", os.path.join(h, ".claude", "squad", "n.md"), cwd=proj))
    assert allowed(ask(h, "Write", os.path.join(proj, "docs", "architect", "p.md"), cwd=proj))
    assert ask(h, "Write", os.path.join(other, "docs", "architect", "p.md"), cwd=proj) == ""


def test_a_symlink_to_the_squad_source_or_to_the_claude_folder_is_not_allowed():
    h = tmp()
    proj = os.path.join(h, "proj")
    os.makedirs(proj)
    os.makedirs(os.path.join(h, ".claude"))
    os.symlink(ROOT, os.path.join(proj, "squad"))
    os.symlink(os.path.join(h, ".claude"), os.path.join(proj, "cfg"))
    for rel in ("squad/new.md", "squad/docs/new.md", "cfg/new.txt", "cfg/agents/a.md"):
        assert ask(h, "Write", os.path.join(proj, *rel.split("/")), cwd=proj) == "", rel
    assert ask(h, "Write", os.path.join(ROOT, "docs", "new-file.md"), cwd=proj) == ""


def test_a_literal_path_through_a_link_named_like_a_denied_folder_is_not_allowed():
    h = tmp()
    proj = os.path.join(h, "proj")
    os.makedirs(os.path.join(proj, "plain"))
    os.symlink(os.path.join(proj, "plain"), os.path.join(proj, "hooks"))
    os.symlink(os.path.join(proj, "plain"), os.path.join(proj, ".git"))
    os.symlink(os.path.join(proj, "plain"), os.path.join(proj, ".ssh"))
    for d in ("hooks", ".git", ".ssh"):
        assert ask(h, "Write", os.path.join(proj, d, "new.txt"), cwd=proj) == "", d
    assert allowed(ask(h, "Write", os.path.join(proj, "plain", "new.txt"), cwd=proj))


def test_case_variants_of_denied_names_are_not_allowed():
    h = tmp()
    proj = os.path.join(h, "proj")
    os.makedirs(proj)
    for rel in ("Hooks/x.py", "SETTINGS.json", "Settings.Local.JSON", ".Git/config", ".ENV", "Id_RSA", "x.PEM",
                ".Claude/Agents/a.md", ".claude/SKILLS/s.md", ".AWS/c"):
        assert ask(h, "Write", os.path.join(proj, *rel.split("/")), cwd=proj) == "", rel
    touch(os.path.join(proj, ".claude", "handoffs", "A.md"))
    assert ask(h, "Write", os.path.join(proj, ".Claude", "Agents", "a.md"), cwd=proj) == ""


def test_claude_config_dir_is_guarded_like_the_claude_folder():
    h = tmp()
    cfg = os.path.join(h, "cfgdir")
    env = {"CLAUDE_CONFIG_DIR": cfg}
    for rel in ("agents/a.md", "skills/s/SKILL.md", "commands/c.md", "plugins/p.md", "keybindings.json",
                "hooks/x.py"):
        assert ask(h, "Write", os.path.join(cfg, *rel.split("/")), cwd=h, env=env) == "", rel
    assert allowed(ask(h, "Write", os.path.join(cfg, "squad", "n.md"), cwd=h, env=env))


def test_the_squad_source_tree_is_never_allowed():
    h = tmp()
    for rel in ("hooks/new.py", "agents/new.md", "skills/new/x.md", "scripts/new.py", "install-new.sh", "notes.md"):
        assert ask(h, "Write", os.path.join(ROOT, *rel.split("/")), cwd=ROOT) == "", rel
        assert ask(h, "Write", os.path.join(ROOT, *rel.split("/")), cwd=h) == "", rel


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
