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
    if cwd is None:
        cwd = os.path.join(home, "proj")          # a session is never started in HOME
        os.makedirs(cwd, exist_ok=True)
    return run(home, {"session_id": SID, "cwd": cwd, "hook_event_name": "PermissionRequest",
                      "tool_name": tool, "tool_input": {key: path}}, env)


def allowed(out):
    return bool(out) and json.loads(out) == ALLOW


def touch(path, text="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)
    return path


def project(h):
    p = os.path.join(h, "proj")
    os.makedirs(p, exist_ok=True)
    return p


def rel(base, r):
    return os.path.join(base, *r.split("/"))


def test_a_new_inert_file_in_the_project_is_allowed():
    h = tmp()
    p = project(h)
    for r in ("docs/notes.md", "a/b/data.csv", "d.tsv", "x.log", "i.png", "i.jpg", "i.jpeg",
              "r.pdf", "UPPER.MD"):
        assert allowed(ask(h, "Write", rel(p, r))), r


def test_a_relative_path_is_read_against_the_session_cwd():
    h = tmp()
    p = project(h)
    assert allowed(ask(h, "Write", "new.md", cwd=p))
    touch(os.path.join(p, "old.md"))
    assert ask(h, "Write", "old.md", cwd=p) == ""


def test_only_inert_extensions_are_allowed_as_new_files():
    h = tmp()
    p = project(h)
    for r in ("a.py", "a.js", "a.ts", "a.tf", "a.tfvars", "a.sh", "a.yml", "a.yaml", "a.json", "a.toml", "a.html", "a.svg",
              "a.ps1", "a.bat", "a.cmd", "a", "sitecustomize.py", "tests/__init__.py", "noxfile.py", "jest.config.js",
              "Rakefile", "Jenkinsfile", "build.ps1", "run.bat", "pom.xml", "docker-compose.yml", "override.tf",
              "x.auto.tfvars", "Makefile", "Dockerfile", "package.json", "conftest.py", "x.pth", "a.conf"):
        assert ask(h, "Write", rel(p, r)) == "", r


def test_components_that_run_or_hide_things_are_not_allowed():
    h = tmp()
    p = project(h)
    for r in ("node_modules/a.md", "vendor/a.md", ".terraform/a.md", "bin/a.md", "sbin/a.md", "hooks/a.md",
              "githooks/a.md", ".githooks/a.md", ".github/a.md", ".vscode/a.md", "src/.hidden/a.md", ".a.md",
              ".claude/notes.md", "CLAUDE.md", "claude.local.md", "CLAUDE.override.md", "AGENTS.md",
              "agents.local.md", "sub/Claude.md", "GEMINI.md", "gemini.local.md", "Copilot-instructions.md",
              "skills/a/SKILL.md", "CONVENTIONS.md"):
        assert ask(h, "Write", rel(p, r)) == "", r


def test_overwriting_a_file_is_left_to_the_person():
    h = tmp()
    p = project(h)
    f = touch(rel(p, "docs/notes.md"))
    assert ask(h, "Write", f) == ""
    for tool in ("Edit", "MultiEdit"):
        assert ask(h, tool, f) == "", tool


def test_a_dangling_symlink_is_not_a_new_file():
    h = tmp()
    p = project(h)
    os.symlink(os.path.join(p, "nowhere.md"), os.path.join(p, "link.md"))
    assert ask(h, "Write", os.path.join(p, "link.md")) == ""


def test_a_session_started_in_root_home_or_above_home_gets_nothing():
    h = tmp()
    project(h)
    top = os.path.dirname(h)
    cases = [("/", "/usr/local/bin/x.md"), ("/", "/etc/foo.conf"), ("/", "/tmp/z.md"), ("/", "/home/z.md"),
             (h, os.path.join(h, "notes.md")), (h, os.path.join(h, "bin", "ls")),
             (h, os.path.join(h, "docs", "architect", "p.md")), (h, os.path.join(h, ".claude", "handoffs", "a.md")),
             (top, os.path.join(h, "notes.md")), (top, os.path.join(top, "notes.md"))]
    for cwd, p in cases:
        assert ask(h, "Write", p, cwd=cwd) == "", (cwd, p)


def test_the_git_toplevel_does_not_widen_the_project():
    h = tmp()
    p = project(h)
    os.makedirs(os.path.join(p, "sub"))
    subprocess.run(["git", "init", "-q", p], check=True, capture_output=True)
    sub = os.path.join(p, "sub")
    assert allowed(ask(h, "Write", os.path.join(sub, "in.md"), cwd=sub))
    assert ask(h, "Write", os.path.join(p, "top.md"), cwd=sub) == ""


def test_a_new_file_outside_the_project_is_not_allowed():
    h = tmp()
    p = project(h)
    elsewhere = tmp()
    for f in (os.path.join(h, ".newrc"), os.path.join(h, "other.txt"), os.path.join(elsewhere, "x.txt"),
              os.path.join(elsewhere, ".claude", "handoffs", "a.md"), os.path.join(elsewhere, "docs", "architect", "p.md")):
        assert ask(h, "Write", f, cwd=p) == "", f


def test_a_new_handoff_is_allowed_and_an_existing_one_is_not_edited():
    h = tmp()
    p = project(h)
    owner = rel(p, ".claude/handoffs/20261104-135716-x.md")
    assert allowed(ask(h, "Write", owner))
    touch(owner)
    for tool in ("Write", "Edit", "MultiEdit"):
        assert ask(h, tool, owner) == "", tool
    assert ask(h, "Write", rel(p, ".claude/handoffs/x.sh")) == ""
    assert ask(h, "Write", rel(p, ".claude/handoffs/.hidden.md")) == ""
    assert ask(h, "Write", rel(p, ".claude/handoffs/CLAUDE.md")) == ""


def test_the_squad_handoff_folder_under_the_claude_dir_is_allowed_and_nothing_else_there():
    h = tmp()
    new = os.path.join(h, ".claude", "squad", "handoffs", "a.md")
    assert allowed(ask(h, "Write", new))
    touch(new)
    assert ask(h, "Edit", new) == ""
    for r in ("wake.env", "run.sh", "notes/n.md", "decisions/d.md", "allow.log", "handoffs/run.sh", "x.md"):
        assert ask(h, "Write", os.path.join(h, ".claude", "squad", *r.split("/"))) == "", r
    touch(os.path.join(h, ".claude", "squad", "wake.env"))
    assert ask(h, "Edit", os.path.join(h, ".claude", "squad", "wake.env")) == ""
    cfg = os.path.join(h, "cfgdir")
    assert allowed(ask(h, "Write", os.path.join(cfg, "squad", "handoffs", "a.md"), env={"CLAUDE_CONFIG_DIR": cfg}))


def test_architect_plan_files_are_allowed_to_create_and_edit():
    h = tmp()
    p = project(h)
    plan = touch(rel(p, "docs/architect/plan.json"))
    md = touch(rel(p, "docs/architect/plan.md"))
    for f in (plan, md):
        for tool in ("Edit", "MultiEdit"):
            assert allowed(ask(h, tool, f)), (f, tool)
        assert ask(h, "Write", f) == "", f
    assert allowed(ask(h, "Write", rel(p, "docs/architect/new.md")))
    assert allowed(ask(h, "Write", rel(p, "docs/architect/new.json")))
    assert ask(h, "Write", rel(p, "docs/architect/new.py")) == ""
    assert ask(h, "Write", rel(p, "docs/architect/new.sh")) == ""
    assert ask(h, "NotebookEdit", touch(rel(p, "docs/architect/n.ipynb")), key="notebook_path") == ""
    assert ask(h, "Edit", touch(rel(p, "docs/architect/run.py"))) == ""
    assert ask(h, "Edit", touch(rel(p, "docs/architect/settings.json"))) == ""


def test_build_and_dependency_files_with_an_inert_extension_are_not_allowed():
    h = tmp()
    for name in ("requirements.txt", "requirements-dev.txt", "Requirements.TXT", "dev-requirements.txt",
                 "constraints.txt", "constraints-py312.txt", "CMakeLists.txt", "cmakelists.txt",
                 "llms.txt", "llms-full.txt", "robots.txt", "dev_requirements.txt", "test.requirements.txt",
                 "requirements/base.txt", "Requirements/dev.txt", "constraints/py312.txt",
                 "pkg.egg-info/requires.txt", "pkg.egg-info/SOURCES.txt", "pkg-1.0.dist-info/METADATA.txt",
                 "build_constraints.txt"):
        p = os.path.join(h, "proj", "sub", *name.split("/"))
        assert ask(h, "Write", p) == "", name
    assert ask(h, "Write", os.path.join(h, "proj", "sub", "notes.md")) != ""


def test_no_txt_file_is_allowed_since_tools_read_them_as_code_or_specs():
    h = tmp()
    for name in ("notes.txt", "test_x.txt", "tests/test_notes.txt", "CMakeCache.txt", "conanfile.txt",
                 "packages.txt", "apt.txt", "runtime.txt"):
        assert ask(h, "Write", os.path.join(h, "proj", *name.split("/"))) == "", name


def test_agent_instruction_names_and_readme_are_not_allowed():
    h = tmp()
    for name in ("AGENT.md", "agent.md", "JULES.md", "QWEN.md", "CRUSH.md", "WARP.md", "README.md", "readme.md"):
        assert ask(h, "Write", os.path.join(h, "proj", "sub", name)) == "", name


def test_plans_and_handoffs_named_after_requirements_are_still_allowed():
    h = tmp()
    for r in ("proj/docs/architect/requirements.md", "proj/docs/architect/constraints.json",
              "proj/.claude/handoffs/requirements-notes.md", "proj/docs/requirements.md"):
        assert ask(h, "Write", os.path.join(h, *r.split("/"))) != "", r


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
        "proj/.claude/handoffs/settings.json", "proj/.claude/handoffs/.env", "proj/docs/architect/CLAUDE.md",
        "proj/docs/architect/settings.json", ".claude/squad/hooks/x.py", ".claude/squad/id_rsa",
        ".claude/squad/handoffs/CLAUDE.md",
    ]
    for r in rels:
        p = os.path.join(h, *r.split("/"))
        for tool in ("Write", "Edit"):
            assert ask(h, tool, p) == "", (tool, r)
        touch(p)
        for tool in ("Write", "Edit"):
            assert ask(h, tool, p) == "", (tool, r)


def test_a_symlink_into_a_denied_folder_is_not_allowed():
    h = tmp()
    p = project(h)
    hooks = os.path.join(p, "hooks")
    os.makedirs(hooks)
    handoffs = os.path.join(p, ".claude", "handoffs")
    os.makedirs(handoffs)
    os.symlink(hooks, os.path.join(handoffs, "dir"))
    assert ask(h, "Write", os.path.join(handoffs, "dir", "new.md")) == ""
    os.symlink(os.path.join(hooks, "dangling.py"), os.path.join(handoffs, "file.md"))
    assert ask(h, "Write", os.path.join(handoffs, "file.md")) == ""
    os.symlink(os.path.join(p, "CLAUDE.md"), os.path.join(p, "notes.md"))
    assert ask(h, "Write", os.path.join(p, "notes.md")) == ""


def test_dot_dot_traversal_is_not_allowed():
    h = tmp()
    p = project(h)
    os.makedirs(os.path.join(p, ".claude", "handoffs"))
    assert ask(h, "Write", rel(p, ".claude/handoffs/../../hooks/x.md")) == ""
    assert ask(h, "Write", rel(p, "docs/architect/../../.claude/settings.json")) == ""
    assert ask(h, "Write", rel(p, "docs/architect/../../../x.md")) == ""
    # a traversal that lands in a place that is allowed is judged by where it lands
    assert allowed(ask(h, "Write", rel(p, "src/../.claude/handoffs/a.md")))


def test_a_symlink_to_the_squad_source_or_to_the_claude_folder_is_not_allowed():
    h = tmp()
    p = project(h)
    os.makedirs(os.path.join(h, ".claude"))
    os.symlink(ROOT, os.path.join(p, "squad"))
    os.symlink(os.path.join(h, ".claude"), os.path.join(p, "cfg"))
    for r in ("squad/new.md", "squad/docs/new.md", "cfg/new.txt", "cfg/agents/a.md", "cfg/squad/handoffs/a.md"):
        assert ask(h, "Write", rel(p, r)) == "", r
    assert ask(h, "Write", os.path.join(ROOT, "docs", "new-file.md")) == ""


def test_a_literal_path_through_a_link_named_like_a_denied_folder_is_not_allowed():
    h = tmp()
    p = project(h)
    os.makedirs(os.path.join(p, "plain"))
    for d in ("hooks", ".git", ".ssh"):
        os.symlink(os.path.join(p, "plain"), os.path.join(p, d))
        assert ask(h, "Write", os.path.join(p, d, "new.md")) == "", d
    assert allowed(ask(h, "Write", os.path.join(p, "plain", "new.md")))


def test_case_variants_of_denied_names_are_not_allowed():
    h = tmp()
    p = project(h)
    for r in ("Hooks/x.md", "SETTINGS.json", "Settings.Local.JSON", ".Git/config", ".ENV", "Id_RSA", "x.PEM",
              ".Claude/Agents/a.md", ".claude/SKILLS/s.md", ".AWS/c", "Node_Modules/a.md", "BIN/a.md"):
        assert ask(h, "Write", rel(p, r)) == "", r


def test_claude_config_dir_is_guarded_like_the_claude_folder():
    h = tmp()
    cfg = os.path.join(h, "cfgdir")
    env = {"CLAUDE_CONFIG_DIR": cfg}
    for r in ("agents/a.md", "skills/s/SKILL.md", "commands/c.md", "plugins/p.md", "keybindings.json", "hooks/x.md",
              "squad/wake.env", "squad/notes/n.md"):
        assert ask(h, "Write", rel(cfg, r), env=env) == "", r


def test_the_squad_source_tree_is_never_allowed():
    h = tmp()
    for r in ("hooks/new.md", "agents/new.md", "skills/new/x.md", "scripts/new.md", "docs/architect/new.md", "notes.md"):
        assert ask(h, "Write", rel(ROOT, r), cwd=ROOT) == "", r
        assert ask(h, "Write", rel(ROOT, r)) == "", r


def test_a_session_whose_root_is_or_sits_in_a_dot_folder_gets_nothing():
    h = tmp()
    p = project(h)
    cfg = os.path.join(h, ".claude")
    cases = [(cfg, "rules/x.md"), (cfg, "output-styles/x.md"), (os.path.join(cfg, "squad"), "x.log"),
             (os.path.join(cfg, "squad"), "notes/x.md"), (os.path.join(p, ".claude"), "rules/x.md"),
             (os.path.join(p, ".claude"), "x.md"), (os.path.join(h, ".config"), "app/x.md"),
             (os.path.join(p, ".hidden"), "docs/architect/p.md")]
    for cwd, r in cases:
        os.makedirs(cwd, exist_ok=True)
        assert ask(h, "Write", rel(cwd, r), cwd=cwd) == "", (cwd, r)
    for r in ("rules/x.md", "projects/p/memory/x.md", "output-styles/x.md", "x.md"):
        assert ask(h, "Write", rel(cfg, r)) == "", r
        assert ask(h, "Write", rel(cfg, r), cwd=cfg) == "", r
    cd = os.path.join(h, "cfgdir")
    env = {"CLAUDE_CONFIG_DIR": cd}
    for r in ("rules/x.md", "projects/p/memory/x.md", "x.md"):
        assert ask(h, "Write", rel(cd, r), cwd=cd, env=env) == "", r
        assert ask(h, "Write", rel(cd, r), env=env) == "", r
    # the explicit handoff folders are their own rule and still answer
    assert allowed(ask(h, "Write", rel(p, ".claude/handoffs/20261104-135716-x.md")))
    assert allowed(ask(h, "Write", rel(cfg, "squad/handoffs/a.md")))
    assert allowed(ask(h, "Write", rel(p, "docs/notes.md")))


def test_other_tools_print_nothing():
    h = tmp()
    p = os.path.join(h, "proj", "new.md")
    for tool in ("Bash", "WebFetch", "Read", "mcp__x__write"):
        assert run(h, {"session_id": SID, "cwd": project(h), "tool_name": tool,
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
