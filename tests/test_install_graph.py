#!/usr/bin/env python3
"""install.sh ships graphify: it links the wrapper, installs the pinned CLI with uv, and never fails for it.

The pin lives in one file, hooks/graphify.pin: a single version line (blank
lines and # comments allowed), bumped by pull request. install.sh reads it and
runs `uv tool install graphifyy[terraform]==<pin>`. Without uv, or with
SQUAD_GRAPHIFY=0, it prints SKIPPED with that command and carries on. It never
runs `graphify install`, which writes an always-on block into ~/.claude/CLAUDE.md.

Self-checking: the exit code is the verdict. Each run uses a copy of this tree,
a throwaway HOME and CLAUDE_CONFIG_DIR, and a PATH without the machine's uv,
graphify, omp and agenfk; fake uv and graphify record their arguments. Nothing
here touches ~/.claude or the network.

    python3 tests/test_install_graph.py
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PIN = os.path.join(ROOT, "hooks", "graphify.pin")
TMP = tempfile.mkdtemp(prefix="install-graph-")
VERSION = re.compile(r"^\d+\.\d+\.\d+$")


def path_without(tools):
    """This PATH, where every directory that holds one of `tools` is replaced by
    a mirror of links to everything else in it."""
    dirs = []
    for i, d in enumerate(filter(None, os.environ.get("PATH", "").split(os.pathsep))):
        if not any(os.path.lexists(os.path.join(d, t)) for t in tools):
            dirs.append(d)
            continue
        mirror = os.path.join(TMP, f"path-{i}")
        if not os.path.isdir(mirror):
            os.makedirs(mirror)
            for name in os.listdir(d):
                if name not in tools:
                    os.symlink(os.path.join(d, name), os.path.join(mirror, name))
        dirs.append(mirror)
    return dirs


PATH = path_without({"uv", "graphify", "omp", "agenfk", "claude"})


def read_pin(path):
    with open(path) as fh:
        lines = [ln.strip() for ln in fh if ln.strip() and not ln.strip().startswith("#")]
    assert len(lines) == 1, f"{path} must hold exactly one version line, found {lines}"
    return lines[0]


def repo_copy():
    dst = os.path.join(tempfile.mkdtemp(dir=TMP), "squad")
    shutil.copytree(ROOT, dst, symlinks=True, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    return dst


def recorder(bin_dir, name, log, rc=0):
    """A fake tool that writes its arguments, one per line, then a --- line."""
    path = os.path.join(bin_dir, name)
    with open(path, "w") as fh:
        fh.write(f"#!/bin/sh\nfor a in \"$@\"; do printf '%s\\n' \"$a\"; done >> '{log}'\n"
                 f"echo --- >> '{log}'\nexit {rc}\n")
    os.chmod(path, 0o755)


def calls(log):
    if not os.path.exists(log):
        return []
    with open(log) as fh:
        return [c.strip("\n").split("\n") for c in fh.read().split("---\n") if c.strip()]


def install(uv=True, uv_rc=0, env=None, repo=None):
    repo = repo or repo_copy()
    base = tempfile.mkdtemp(dir=TMP)
    h, bin_dir = os.path.join(base, "home"), os.path.join(base, "bin")
    os.makedirs(h)
    os.makedirs(bin_dir)
    uv_log, graphify_log = os.path.join(base, "uv.log"), os.path.join(base, "graphify.log")
    if uv:
        recorder(bin_dir, "uv", uv_log, uv_rc)
    recorder(bin_dir, "graphify", graphify_log)
    e = {k: v for k, v in os.environ.items()
         if not k.startswith(("SQUAD_", "CLAUDE_", "GRAPHIFY_")) and k != "PYTHONPATH"}
    e.update(HOME=h, CLAUDE_CONFIG_DIR=os.path.join(h, ".claude"), PATH=os.pathsep.join([bin_dir] + PATH))
    e.update(env or {})
    r = subprocess.run(["bash", os.path.join(repo, "install.sh")], capture_output=True, text=True,
                       env=e, cwd=h, timeout=120)
    r.out = r.stdout + r.stderr
    r.repo, r.cfg, r.uv, r.graphify = repo, e["CLAUDE_CONFIG_DIR"], calls(uv_log), calls(graphify_log)
    return r


def command(pin):
    return f"uv tool install graphifyy[terraform]=={pin}"


# ---------------------------------------------------------------- tests

def test_the_pin_file_holds_one_version():
    assert os.path.isfile(PIN), "hooks/graphify.pin does not exist"
    assert VERSION.match(read_pin(PIN)), read_pin(PIN)


def test_install_links_the_wrapper():
    r = install()
    assert r.returncode == 0, r.out
    link = os.path.join(r.cfg, "hooks", "squad-graph.py")
    assert os.path.islink(link), r.out
    assert os.path.realpath(link) == os.path.realpath(os.path.join(r.repo, "hooks", "squad-graph.py")), r.out


def test_install_links_the_graph_context_skill():
    r = install()
    link = os.path.join(r.cfg, "skills", "graph-context")
    assert os.path.realpath(link) == os.path.realpath(os.path.join(r.repo, "skills", "graph-context")), r.out


def test_install_runs_uv_tool_install_with_the_pin():
    pin = read_pin(PIN)
    r = install()
    assert r.returncode == 0, r.out
    wanted = [a for a in r.uv if a[:2] == ["tool", "install"]]
    assert len(wanted) == 1, r.uv
    assert f"graphifyy[terraform]=={pin}" in wanted[0], wanted


def test_the_pin_is_read_from_the_pin_file_not_from_install_sh():
    repo = repo_copy()
    with open(os.path.join(repo, "hooks", "graphify.pin"), "w") as fh:
        fh.write("# bumped by pull request\n0.0.1\n")
    r = install(repo=repo)
    assert any("graphifyy[terraform]==0.0.1" in a for a in r.uv), r.uv
    with open(os.path.join(ROOT, "install.sh")) as fh:
        assert read_pin(PIN) not in fh.read(), "the version is written into install.sh; it belongs in hooks/graphify.pin"


def test_without_uv_install_says_skipped_with_the_command_and_exits_zero():
    r = install(uv=False)
    assert r.returncode == 0, r.out
    lines = [ln for ln in r.out.splitlines() if "SKIPPED" in ln or command(read_pin(PIN)) in ln]
    assert any("SKIPPED" in ln for ln in lines), r.out
    assert command(read_pin(PIN)) in r.out, r.out


def test_kill_switch_at_install_skips_uv_and_names_the_command():
    r = install(env={"SQUAD_GRAPHIFY": "0"})
    assert r.returncode == 0, r.out
    assert r.uv == [], r.uv
    assert "SKIPPED" in r.out and command(read_pin(PIN)) in r.out, r.out


def test_a_failing_uv_does_not_fail_the_install():
    r = install(uv_rc=1)
    assert r.returncode == 0, r.out
    assert len(r.uv) >= 1, r.uv
    assert command(read_pin(PIN)) in r.out, r.out     # the user is told what to run by hand


def test_a_pin_file_without_a_version_warns_and_the_install_still_finishes():
    for text in (None, "# only a comment\n\n"):
        repo = repo_copy()
        pin = os.path.join(repo, "hooks", "graphify.pin")
        if text is None:
            os.remove(pin)
        else:
            with open(pin, "w") as fh:
                fh.write(text)
        r = install(repo=repo)
        assert r.returncode == 0, r.out
        assert "graphify.pin holds no version" in r.out, r.out
        assert r.uv == [], r.uv
        assert "New agents and skills" in r.out, "the install ran to its end"


def test_a_trailing_comment_after_the_version_is_ignored():
    repo = repo_copy()
    with open(os.path.join(repo, "hooks", "graphify.pin"), "w") as fh:
        fh.write("0.0.2   # reviewed 2026-10-21\r\n")
    r = install(repo=repo)
    assert any("graphifyy[terraform]==0.0.2" in a and "#" not in a for a in r.uv), r.uv
    assert "squad-graph.py on PreToolUse" in r.out, "the closing hint names the hook"


def test_install_never_runs_graphify_install():
    for kw in ({}, {"uv": False}, {"uv_rc": 1}):
        r = install(**kw)
        assert not any(a[:1] == ["install"] for a in r.graphify), (kw, r.graphify)
        assert not os.path.exists(os.path.join(r.cfg, "CLAUDE.md")) or os.path.islink(
            os.path.join(r.cfg, "CLAUDE.md")), "something wrote a real ~/.claude/CLAUDE.md"


if __name__ == "__main__":
    try:
        for name, fn in sorted(globals().items()):
            if name.startswith("test_") and callable(fn):
                fn()
                print("ok ", name)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
