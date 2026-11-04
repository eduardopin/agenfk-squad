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
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.name == "nt":
    print("skipped: the doctor test drives install.sh and POSIX stubs; Windows installs with install.ps1")
    sys.exit(0)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.dont_write_bytecode = True
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


def test_a_hook_under_an_empty_or_missing_matcher_counts_as_star():
    base, home, config = wired()
    settings_path = os.path.join(config, "settings.json")
    with open(settings_path, encoding="utf-8") as fh:
        settings = json.load(fh)
    settings["hooks"]["PreToolUse"][0]["matcher"] = ""          # the manifest says "*"
    settings["hooks"]["PostToolUse"][0].pop("matcher")
    settings["hooks"]["Stop"][0]["matcher"] = "*"               # the manifest says none
    with open(settings_path, "w", encoding="utf-8") as fh:
        json.dump(settings, fh)
    data, checks = as_json(doctor(base, home, config, "--json"))
    assert [p for p in problems(checks, doc.WARN) if p[0] == "hooks"] == [], data
    assert checks["hooks"]["status"] == doc.OK, checks["hooks"]


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


# ---------------------------------------------------------------- round 1 of review

def edit_settings(config, change):
    path = os.path.join(config, "settings.json")
    with open(path, encoding="utf-8") as fh:
        settings = json.load(fh)
    change(settings)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(settings, fh)


def test_a_matcher_that_is_not_a_string_is_a_malformed_entry_not_a_crash():
    base, home, config = wired()

    def five(settings):
        for g in settings["hooks"]["PreToolUse"]:
            g["matcher"] = 5
    edit_settings(config, five)
    data, checks = as_json(doctor(base, home, config, "--json"))
    hooks = checks["hooks"]
    assert "the check itself failed" not in hooks["summary"], hooks
    bad = {i["name"]: i for i in hooks["items"]}["squad-watch.py on PreToolUse"]
    assert bad["status"] == doc.FAIL and "malformed" in bad["detail"] and "5" in bad["detail"], bad
    assert data["exit"] == 1


def test_a_clamped_threshold_shows_the_value_the_watcher_uses():
    by = {i["name"]: i for i in doc.watcher_env(environ={"SQUAD_CTX_PCT": "500"})}
    pct = by["SQUAD_CTX_PCT"]
    assert pct["status"] == doc.WARN and pct["effective"] == 100 and "uses 100" in pct["detail"], pct
    by = {i["name"]: i for i in doc.watcher_env(environ={"SQUAD_CTX_PCT": "3"})}
    assert by["SQUAD_CTX_PCT"]["status"] == doc.WARN and by["SQUAD_CTX_PCT"]["effective"] == 10
    by = {i["name"]: i for i in doc.watcher_env(environ={"SQUAD_CTX_PCT": "75"})}
    assert by["SQUAD_CTX_PCT"]["status"] == doc.OK and by["SQUAD_CTX_PCT"]["effective"] == 75
    by = {i["name"]: i for i in doc.watcher_env(environ={"SQUAD_CTX_PCT": "lots"})}
    assert by["SQUAD_CTX_PCT"]["status"] == doc.WARN and by["SQUAD_CTX_PCT"]["effective"] == 90


def test_squad_window_zero_reads_as_auto():
    for environ in ({}, {"SQUAD_WINDOW": "0"}):
        window = {i["name"]: i for i in doc.watcher_env(environ=environ)}["SQUAD_WINDOW"]
        assert window["status"] == doc.OK and "auto" in window["detail"], window
    window = {i["name"]: i for i in doc.watcher_env(environ={"SQUAD_WINDOW": "200000"})}["SQUAD_WINDOW"]
    assert "auto" not in window["detail"].split(";")[0] and window["effective"] == 200000, window


def test_the_kill_switches_warn_that_the_watcher_is_off():
    by = {i["name"]: i for i in doc.watcher_env(environ={"SQUAD_WATCH": "0", "SQUAD_TURN": "0"})}
    assert by["SQUAD_WATCH"]["status"] == doc.WARN and "watcher disabled" in by["SQUAD_WATCH"]["detail"]
    assert by["SQUAD_TURN"]["status"] == doc.WARN and "disabled" in by["SQUAD_TURN"]["detail"]
    check = doc.check_thresholds(environ={"SQUAD_WATCH": "0"})
    assert check["status"] == doc.WARN and "SQUAD_WATCH" in check["fix"], check
    by = {i["name"]: i for i in doc.watcher_env(environ={"SQUAD_WATCH": "1"})}
    assert by["SQUAD_WATCH"]["status"] == doc.OK


def test_the_doctor_writes_no_bytecode_even_without_dash_b():
    clone = os.path.join(tempfile.mkdtemp(dir=TMP), "clone")
    for rel in ("scripts/squad_doctor.py", "scripts/squad_settings.py", "hooks/manifest.json",
                "hooks/squad-watch.py"):
        os.makedirs(os.path.dirname(os.path.join(clone, rel)), exist_ok=True)
        shutil.copy(os.path.join(ROOT, rel), os.path.join(clone, rel))
    empty = tempfile.mkdtemp(dir=TMP)                         # nothing on PATH: never the real agenfk
    env = {"HOME": clone, "PATH": empty}                      # no PYTHONDONTWRITEBYTECODE, no -B
    r = subprocess.run([sys.executable, os.path.join(clone, "scripts", "squad_doctor.py"), "--json",
                        "--config", os.path.join(clone, "config")], cwd=clone, env=env, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=120)
    assert r.returncode in (0, 1) and json.loads(r.stdout)["checks"], r.stdout + r.stderr
    written = [os.path.join(d, f) for d, _, fs in os.walk(clone) for f in fs if f.endswith(".pyc")]
    assert written == [] and not os.path.exists(os.path.join(clone, "scripts", "__pycache__")), written


def test_fix_commands_quote_paths_with_spaces():
    spaced = os.path.join(tempfile.mkdtemp(dir=TMP), "a dir")
    config, root, home = (os.path.join(spaced, n) for n in ("con fig", "clo ne", "ho me"))
    for d in (config, root, home):
        os.makedirs(d)
    hooks = doc.check_hooks(config)
    assert hooks["status"] == doc.FAIL
    assert shlex.split(hooks["fix"]) == ["python3", os.path.join(doc.ROOT, "scripts", "squad_settings.py"),
                                         "install", "--config", config], hooks["fix"]
    installer = os.path.join(root, "install.sh")
    links = doc.check_links(config, root=root)
    assert links["status"] == doc.FAIL and shlex.split(links["fix"].split(" (")[0]) == ["run", installer], links
    turn = doc.check_squad_turn(which=lambda _: None, root=root, home=home)
    assert shlex.split(turn["fix"]) == ["run", installer], turn


def test_agenfk_exiting_1_without_a_failed_line_quotes_its_first_line():
    bin_dir = os.path.join(tempfile.mkdtemp(dir=TMP), "bin")
    exe = stub(bin_dir, "agenfk", 'echo; echo "  Server unreachable at localhost:3000  "; echo more; exit 1')
    check = doc.check_agenfk(which=lambda _: exe)
    assert check["status"] == doc.WARN and check["summary"].endswith(": Server unreachable at localhost:3000"), check
    exe = stub(bin_dir, "agenfk", 'echo "token expired" >&2; exit 1')
    assert doc.check_agenfk(which=lambda _: exe)["summary"].endswith(": token expired")


def test_agenfk_that_cannot_start_or_hangs_warns_and_names_why():
    bin_dir = os.path.join(tempfile.mkdtemp(dir=TMP), "bin")
    broken = os.path.join(bin_dir, "agenfk-broken")
    os.makedirs(bin_dir)
    with open(broken, "w", encoding="utf-8") as fh:
        fh.write("#!/nonexistent/interpreter\n")
    os.chmod(broken, 0o755)
    check = doc.check_agenfk(which=lambda _: broken)
    assert check["status"] == doc.WARN and "did not answer" in check["summary"], check
    assert "Error" in check["summary"] and check["fix"], check
    slow = stub(bin_dir, "agenfk", "exec sleep 5")
    start = time.monotonic()
    check = doc.check_agenfk(which=lambda _: slow, timeout=0.3)
    assert time.monotonic() - start < 3, "the timeout did not cut the call"
    assert check["status"] == doc.WARN and "TimeoutExpired" in check["summary"], check


def test_a_dangling_squad_turn_link_on_path_is_named_as_dangling():
    base, home, config = wired()
    local = os.path.join(home, ".local", "bin", "squad-turn")
    os.unlink(local)
    os.symlink(os.path.join(base, "gone", "squad-turn.py"), local)
    _, checks = as_json(doctor(base, home, config, "--json"))      # ~/.local/bin is the whole PATH
    turn = checks["squad-turn"]
    assert turn["status"] == doc.FAIL and "dangling" in turn["summary"], turn
    assert "not on PATH" not in turn["summary"] and "install.sh" in turn["fix"], turn


def test_a_project_json_without_a_project_id_or_of_an_odd_shape_warns():
    for body in ("{}", '{"projectId": null}', '{"projectId": ""}', '{"projectId": "  "}', '{"projectId": 5}',
                 "[]", '"p-1"', "{ not json", ""):
        d = tempfile.mkdtemp(dir=TMP)
        os.makedirs(os.path.join(d, ".agenfk"))
        with open(os.path.join(d, ".agenfk", "project.json"), "w", encoding="utf-8") as fh:
            fh.write(body)
        check = doc.check_project(d)
        assert check["status"] == doc.WARN and check["fix"], (body, check)


def test_an_ascii_stdout_still_prints_a_non_ascii_path():
    base, home, config = wired()
    other = os.path.join(base, "cônfig")
    r = doctor(base, home, config, "--config", other, extra={"PYTHONIOENCODING": "ascii"})
    assert "Traceback" not in r.stderr and r.returncode == 1, r.stderr
    assert "c?nfig" in r.stdout, r.stdout


def test_a_settings_json_in_a_symlink_loop_is_unreadable():
    base, home, config = wired()
    path = os.path.join(config, "settings.json")
    os.unlink(path)
    os.symlink("settings.json", path)
    _, checks = as_json(doctor(base, home, config, "--json"))
    hooks = checks["hooks"]
    assert hooks["status"] == doc.FAIL and "cannot read" in hooks["summary"] and hooks["items"] == [], hooks


# ---------------------------------------------------------------- branches nothing pinned yet

def test_vendored_skills_are_expected_as_links_and_vendor_itself_is_not():
    root = tempfile.mkdtemp(dir=TMP)
    for rel in ("agents/x.md", "CLAUDE.md", "skills/a/SKILL.md", "skills/no-doc/notes.md",
                "skills/vendor/SKILL.md", "skills/vendor/b/SKILL.md", "skills/vendor/c/notes.md"):
        os.makedirs(os.path.dirname(os.path.join(root, rel)), exist_ok=True)
        open(os.path.join(root, rel), "w").close()
    config = os.path.join(root, "config")
    links = {name: (src, dst) for kind, name, src, dst in doc.expected_links(root, config) if kind == "skill"}
    assert sorted(links) == ["a", "b"], links
    assert links["b"] == (os.path.join(root, "skills", "vendor", "b"), os.path.join(config, "skills", "b"))
    vendored = {os.path.basename(os.path.dirname(p))
                for p in __import__("glob").glob(os.path.join(ROOT, "skills", "vendor", "*", "SKILL.md"))}
    real = {name for kind, name, _, _ in doc.expected_links(ROOT, config) if kind == "skill"}
    assert vendored <= real, vendored - real


def test_an_old_python_fails():
    old = doc.check_python((3, 9, 18))
    assert old["status"] == doc.FAIL and "3.9.18" in old["summary"] and old["fix"], old
    assert doc.check_python((3, 10, 0))["status"] == doc.OK


def test_the_config_flag_wins_over_claude_config_dir():
    base, home, config = wired()
    empty = os.path.join(base, "empty-config")
    os.makedirs(empty)
    data, checks = as_json(doctor(base, home, empty, "--json", "--config", config))
    assert data["config"] == config and checks["links"]["status"] == doc.OK, data
    assert checks["hooks"]["status"] == doc.OK
    data, checks = as_json(doctor(base, home, empty, "--json"))
    assert data["config"] == empty and checks["links"]["status"] == doc.FAIL


def test_no_settings_json_fails_every_hook_and_creates_nothing():
    base, home, config = wired()
    path = os.path.join(config, "settings.json")
    os.unlink(path)
    data, checks = as_json(doctor(base, home, config, "--json"))
    hooks = checks["hooks"]
    assert hooks["status"] == doc.FAIL and hooks["items"], hooks
    assert all(i["status"] == doc.FAIL and "not registered" in i["detail"] for i in hooks["items"]), hooks
    assert not os.path.lexists(path) and data["exit"] == 1



# ---------------------------------------------------------------- review round 2

def test_agenfk_health_that_prints_failed_but_exits_0_warns():
    # The real `agenfk health` prints FAILED per check and exits 0 with the server down.
    bin_dir = os.path.join(tempfile.mkdtemp(dir=TMP), "bin")
    exe = stub(bin_dir, "agenfk", 'printf "Checking API Server... "; echo FAILED; '
                                  'echo "   - Error: Could not connect to http://127.0.0.1:9"; '
                                  'echo "Checking Database... FAILED"; '
                                  'echo "Found 2 potential issue(s). Run ./agenfk up to fix."; exit 0')
    check = doc.check_agenfk(which=lambda _: exe)
    assert check["status"] == doc.WARN and check["summary"].endswith(": Checking API Server... FAILED"), check
    assert "agenfk up" in check["fix"], check
    exe = stub(bin_dir, "agenfk", 'echo "Checking for duplicate project roots... OK"; '
                                  'echo "Found 1 potential issue(s)." >&2; exit 0')
    check = doc.check_agenfk(which=lambda _: exe)
    assert check["status"] == doc.WARN and check["summary"].endswith(": Found 1 potential issue(s)."), check
    assert "agenfk up" not in check["fix"] and "fix what it reports" in check["fix"], check
    exe = stub(bin_dir, "agenfk", 'echo "Checking API Server... OK"; echo "All systems healthy!"; exit 0')
    assert doc.check_agenfk(which=lambda _: exe)["status"] == doc.OK


def test_a_kill_switch_is_only_exactly_zero_as_the_watcher_compares_it():
    for raw in (" 0", "0 ", "\t0\n"):
        by = {i["name"]: i for i in doc.watcher_env(environ={"SQUAD_WATCH": raw, "SQUAD_TURN": raw})}
        assert by["SQUAD_WATCH"]["status"] == doc.OK and "disabled" not in by["SQUAD_WATCH"]["detail"], (raw, by)
        assert by["SQUAD_TURN"]["status"] == doc.OK and "disabled" not in by["SQUAD_TURN"]["detail"], (raw, by)


def test_paths_in_prose_fix_lines_are_quoted_too():
    spaced = os.path.join(tempfile.mkdtemp(dir=TMP), "a dir")
    install = ["python3", os.path.join(doc.ROOT, "scripts", "squad_settings.py"), "install", "--config"]
    # not JSON: Refused -> "fix <path> by hand, then: ..."
    config = os.path.join(spaced, "not json")
    os.makedirs(config)
    path = os.path.join(config, "settings.json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("{ not json")
    hooks = doc.check_hooks(config)
    head, tail = hooks["fix"].split(", then: ")
    assert shlex.split(head) == ["fix", path, "by", "hand"] and shlex.split(tail) == install + [config], hooks
    # a directory where the file belongs: OSError -> "fix <path>, then: ..."
    config = os.path.join(spaced, "a dir there")
    path = os.path.join(config, "settings.json")
    os.makedirs(path)
    head, tail = doc.check_hooks(config)["fix"].split(", then: ")
    assert shlex.split(head) == ["fix", path] and shlex.split(tail) == install + [config], head
    # a symlink loop: the early OSError branch
    config = os.path.join(spaced, "a loop")
    os.makedirs(config)
    path = os.path.join(config, "settings.json")
    os.symlink("settings.json", path)
    head, _ = doc.check_hooks(config)["fix"].split(", then: ")
    assert shlex.split(head) == ["fix", path], head
    # squad-turn exists but its directory is not on PATH -> "add <dir> to PATH"
    home = os.path.join(spaced, "ho me")
    local_bin = os.path.join(home, ".local", "bin")
    stub(local_bin, "squad-turn", "exit 0")
    saved = os.environ.get("PATH")
    os.environ["PATH"] = "/usr/bin:/bin"
    try:
        turn = doc.check_squad_turn(which=lambda _: None, root=spaced, home=home)
    finally:
        os.environ["PATH"] = saved
    assert turn["status"] == doc.FAIL and shlex.split(turn["fix"]) == ["add", local_bin, "to", "PATH"], turn


def test_a_squad_turn_on_path_that_is_not_executable_says_so():
    base, home, config = wired()
    local = os.path.join(home, ".local", "bin", "squad-turn")
    os.unlink(local)
    with open(local, "w", encoding="utf-8") as fh:
        fh.write("#!/bin/sh\nexit 0\n")
    os.chmod(local, 0o644)
    _, checks = as_json(doctor(base, home, config, "--json"))      # ~/.local/bin is the whole PATH
    turn = checks["squad-turn"]
    assert turn["status"] == doc.FAIL and "not an executable file" in turn["summary"], turn
    assert "not on PATH" not in turn["summary"] and shlex.split(turn["fix"])[0] == "run", turn
    assert turn["fix"].endswith("install.sh"), turn

if __name__ == "__main__":
    try:
        for name, fn in sorted(globals().items()):
            if name.startswith("test_") and callable(fn):
                fn()
                print("ok ", name)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
