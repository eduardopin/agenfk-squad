#!/usr/bin/env python3
"""scripts/squad_doctor.py says whether the squad is wired, and exits 1 only on a FAIL.

Each test builds a throwaway HOME and CLAUDE_CONFIG_DIR with the real install.sh
and scripts/squad_settings.py, then breaks it on purpose. Nothing here reads
~/.claude, starts a server or touches the network: the doctor runs with a PATH
that holds only what the test put there.

    python3 tests/test_doctor.py
"""
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.name == "nt":
    print("skipped: the doctor test drives install.sh and POSIX stubs; Windows installs with install.ps1")
    sys.exit(0)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import squad_doctor as doc  # noqa: E402

DOCTOR = os.path.join(ROOT, "scripts", "squad_doctor.py")
WATCH = os.path.join(ROOT, "hooks", "squad-watch.py")
TMP = tempfile.mkdtemp(prefix="squad-doctor-")


def wired():
    """A HOME and config dir as install.sh and `squad_settings.py install` leave them."""
    base = tempfile.mkdtemp(dir=TMP)
    home, config = os.path.join(base, "home"), os.path.join(base, "config")
    os.makedirs(home)
    env = {"HOME": home, "CLAUDE_CONFIG_DIR": config, "PATH": "/usr/bin:/bin", "LC_ALL": "C"}
    r = subprocess.run(["bash", os.path.join(ROOT, "install.sh")], env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    assert r.returncode == 0, r.stdout + r.stderr
    r = subprocess.run([sys.executable, "-B", os.path.join(ROOT, "scripts", "squad_settings.py"), "install",
                        "--config", config], capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert r.returncode == 0, r.stdout + r.stderr
    return base, home, config


def doctor(base, home, config, *args, path=None, extra=None):
    """Runs the doctor from `base` (no .agenfk above it) with a PATH of only `path`."""
    env = {"HOME": home, "CLAUDE_CONFIG_DIR": config, "PYTHONDONTWRITEBYTECODE": "1",
           "PATH": path if path is not None else os.path.join(home, ".local", "bin")}
    env.update(extra or {})
    return subprocess.run([sys.executable, "-B", DOCTOR, *args], cwd=base, env=env, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=120)


def as_json(r):
    data = json.loads(r.stdout)
    return data, {c["check"]: c for c in data["checks"]}


def problems(checks, status):
    return sorted((c["check"], i["name"]) for c in checks.values() for i in c["items"] if i["status"] == status)


def stub(directory, name, body):
    os.makedirs(directory, exist_ok=True)
    p = os.path.join(directory, name)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("#!/bin/sh\n" + body + "\n")
    os.chmod(p, 0o755)
    return p


# ---------------------------------------------------------------- acceptance 1

def test_a_fully_wired_config_exits_0_with_nothing_failing():
    base, home, config = wired()
    r = doctor(base, home, config)
    assert r.returncode == 0, r.stdout + r.stderr
    data, checks = as_json(doctor(base, home, config, "--json"))
    assert data["exit"] == 0 and problems(checks, doc.FAIL) == [], data
    for name in ("python", "links", "hooks", "squad-turn", "thresholds"):
        assert checks[name]["status"] == doc.OK, checks[name]
    agents = sorted(f for f in os.listdir(os.path.join(ROOT, "agents")) if f.endswith(".md"))
    linked = [i["name"] for i in checks["links"]["items"]]
    assert [f"agent {a}" for a in agents] == [n for n in linked if n.startswith("agent ")], linked
    with open(os.path.join(ROOT, "hooks", "manifest.json"), encoding="utf-8") as fh:
        pairs = [f"{h['script']} on {e}" for h in json.load(fh)["hooks"] for e in h["events"]]
    assert sorted(i["name"] for i in checks["hooks"]["items"]) == sorted(pairs)


def test_a_missing_agent_an_unregistered_hook_and_a_link_elsewhere_each_fail_by_name():
    base, home, config = wired()
    agent = sorted(os.listdir(os.path.join(config, "agents")))[0]
    os.unlink(os.path.join(config, "agents", agent))
    elsewhere = os.path.join(base, "elsewhere", "writing")
    os.makedirs(elsewhere)
    os.unlink(os.path.join(config, "skills", "writing"))
    os.symlink(elsewhere, os.path.join(config, "skills", "writing"))
    settings_path = os.path.join(config, "settings.json")
    with open(settings_path, encoding="utf-8") as fh:
        settings = json.load(fh)
    for g in settings["hooks"]["UserPromptSubmit"]:
        g["hooks"] = [h for h in g["hooks"] if "squad-router.py" not in h["command"]]
    with open(settings_path, "w", encoding="utf-8") as fh:
        json.dump(settings, fh)

    r = doctor(base, home, config)
    assert r.returncode == 1, r.stdout
    for name in (agent, "squad-router.py on UserPromptSubmit", "skill writing", elsewhere):
        assert name in r.stdout, (name, r.stdout)
    data, checks = as_json(doctor(base, home, config, "--json"))
    assert data["exit"] == 1
    assert problems(checks, doc.FAIL) == sorted([("hooks", "squad-router.py on UserPromptSubmit"),
                                                 ("links", f"agent {agent}"), ("links", "skill writing")]), data


def test_a_real_file_where_a_link_belongs_fails():
    base, home, config = wired()
    dst = os.path.join(config, "agents", "developer.md")
    os.unlink(dst)
    shutil.copy(os.path.join(ROOT, "agents", "developer.md"), dst)
    data, checks = as_json(doctor(base, home, config, "--json"))
    assert data["exit"] == 1
    assert problems(checks, doc.FAIL) == [("links", "agent developer.md")]
    assert "real file" in checks["links"]["items"][[i["name"] for i in checks["links"]["items"]]
                                                   .index("agent developer.md")]["detail"]


def test_a_copy_install_ps1_marked_counts_while_it_matches_the_clone():
    base, home, config = wired()
    src, dst = os.path.join(ROOT, "agents", "developer.md"), os.path.join(config, "agents", "developer.md")
    os.unlink(dst)
    shutil.copy(src, dst)
    os.makedirs(os.path.join(config, "squad"))
    with open(os.path.join(config, "squad", "copies.txt"), "w", encoding="utf-8") as fh:
        fh.write(doc.sha256(src) + "  " + dst + "\n")
    r = doctor(base, home, config)
    assert r.returncode == 0, r.stdout
    with open(dst, "a", encoding="utf-8") as fh:
        fh.write("edited\n")
    data, checks = as_json(doctor(base, home, config, "--json"))
    assert data["exit"] == 0 and problems(checks, doc.WARN)[:1] == [("links", "agent developer.md")], data


def test_a_hook_under_another_matcher_warns_and_does_not_fail():
    base, home, config = wired()
    settings_path = os.path.join(config, "settings.json")
    with open(settings_path, encoding="utf-8") as fh:
        settings = json.load(fh)
    for g in settings["hooks"]["PreToolUse"]:
        g["matcher"] = "Bash"
    with open(settings_path, "w", encoding="utf-8") as fh:
        json.dump(settings, fh)
    data, checks = as_json(doctor(base, home, config, "--json"))
    assert data["exit"] == 0
    assert ("hooks", "squad-watch.py on PreToolUse") in problems(checks, doc.WARN), data


def test_a_settings_file_that_is_not_json_fails_the_hooks_and_is_left_alone():
    base, home, config = wired()
    settings_path = os.path.join(config, "settings.json")
    with open(settings_path, "w", encoding="utf-8") as fh:
        fh.write("{ not json")
    data, checks = as_json(doctor(base, home, config, "--json"))
    assert data["exit"] == 1 and checks["hooks"]["status"] == doc.FAIL, data
    with open(settings_path, encoding="utf-8") as fh:
        assert fh.read() == "{ not json"


def test_squad_turn_off_path_fails_and_says_which_directory_to_add():
    base, home, config = wired()
    data, checks = as_json(doctor(base, home, config, "--json", path=os.path.join(base, "empty")))
    assert data["exit"] == 1 and checks["squad-turn"]["status"] == doc.FAIL
    assert os.path.join(home, ".local", "bin") in checks["squad-turn"]["fix"], checks["squad-turn"]


# ---------------------------------------------------------------- acceptance 2

def test_the_threshold_list_is_every_squad_name_in_the_watcher():
    with open(WATCH, encoding="utf-8") as fh:
        text = fh.read()
    in_code = {n.value for n in ast.walk(ast.parse(text))
               if isinstance(n, ast.Constant) and isinstance(n.value, str) and re.fullmatch(r"SQUAD_\w+", n.value)}
    in_text = set(re.findall(r"(?<![A-Za-z0-9_])SQUAD_[A-Z0-9_]*[A-Z0-9]", text))
    base, home, config = wired()
    data, checks = as_json(doctor(base, home, config, "--json", extra={"SQUAD_SUB_CTX_WARN": "5"}))
    shown = {i["name"] for i in checks["thresholds"]["items"]}
    assert shown == in_text and in_code <= shown, (shown ^ in_text, in_code - shown)
    by = {i["name"]: i for i in checks["thresholds"]["items"]}
    assert by["SQUAD_SUB_CTX_WARN"]["value"] == "5" and not by["SQUAD_SUB_CTX_WARN"]["is_default"]
    assert by["SQUAD_CTX_CEILING"]["is_default"] and by["SQUAD_CTX_CEILING"]["value"] == 800_000


def test_a_new_variable_in_the_watcher_shows_up_whatever_reads_it():
    copy = os.path.join(tempfile.mkdtemp(dir=TMP), "squad-watch.py")
    with open(WATCH, encoding="utf-8") as fh:
        text = fh.read()
    with open(copy, "w", encoding="utf-8") as fh:
        fh.write(text + '\nPROBE_INT = env_int("SQUAD_DOCTOR_PROBE_INT", 7)\n'
                 'PROBE_STR = os.environ.get("SQUAD_DOCTOR_PROBE_STR", "on")\n'
                 'PROBE_ANY = read_setting("SQUAD_DOCTOR_PROBE_ANY")\n')
    by = {i["name"]: i for i in doc.watcher_env(copy, environ={})}
    assert by["SQUAD_DOCTOR_PROBE_INT"]["default"] == 7
    assert by["SQUAD_DOCTOR_PROBE_STR"]["default"] == "on"
    assert "SQUAD_DOCTOR_PROBE_ANY" in by
    bad = {i["name"]: i for i in doc.watcher_env(copy, environ={"SQUAD_DOCTOR_PROBE_INT": "lots"})}
    assert bad["SQUAD_DOCTOR_PROBE_INT"]["status"] == doc.WARN
    assert bad["SQUAD_DOCTOR_PROBE_STR"]["status"] == doc.OK


# ---------------------------------------------------------------- acceptance 3

def test_json_parses_and_has_one_entry_per_check():
    base, home, config = wired()
    data, _ = as_json(doctor(base, home, config, "--json"))
    names = [c["check"] for c in data["checks"]]
    assert names == list(doc.CHECKS), names
    for c in data["checks"]:
        assert c["status"] in (doc.OK, doc.WARN, doc.FAIL) and set(c) == {"check", "status", "summary", "fix", "items"}
        assert c["status"] == doc.OK or c["fix"], c


# ---------------------------------------------------------------- acceptance 4

def test_no_agenfk_and_no_network_is_a_warning_not_a_crash():
    base, home, config = wired()
    data, checks = as_json(doctor(base, home, config, "--json"))
    assert checks["agenfk"]["status"] == doc.WARN and "not on PATH" in checks["agenfk"]["summary"]
    assert checks["herdr"]["status"] == doc.WARN and checks["project"]["status"] == doc.WARN
    assert data["exit"] == 0


def test_an_agenfk_whose_server_is_down_warns():
    base, home, config = wired()
    bin_dir = os.path.join(base, "bin")
    stub(bin_dir, "agenfk", 'echo "Checking API Server... FAILED"; exit 1')
    path = bin_dir + os.pathsep + os.path.join(home, ".local", "bin")
    data, checks = as_json(doctor(base, home, config, "--json", path=path))
    assert checks["agenfk"]["status"] == doc.WARN and "FAILED" in checks["agenfk"]["summary"], checks["agenfk"]
    assert data["exit"] == 0
    stub(bin_dir, "agenfk", "exit 0")
    stub(bin_dir, "herdr", "exit 0")
    _, checks = as_json(doctor(base, home, config, "--json", path=path))
    assert checks["agenfk"]["status"] == doc.OK and checks["herdr"]["status"] == doc.OK


def test_the_project_is_found_from_a_subdirectory():
    base, home, config = wired()
    os.makedirs(os.path.join(base, ".agenfk"))
    with open(os.path.join(base, ".agenfk", "project.json"), "w", encoding="utf-8") as fh:
        json.dump({"projectId": "p-1"}, fh)
    sub = os.path.join(base, "a", "b")
    os.makedirs(sub)
    _, checks = as_json(doctor(base, home, config, "--json", "--project", sub))
    assert checks["project"]["status"] == doc.OK and "p-1" in checks["project"]["summary"]


if __name__ == "__main__":
    try:
        for name, fn in sorted(globals().items()):
            if name.startswith("test_") and callable(fn):
                fn()
                print("ok ", name)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
