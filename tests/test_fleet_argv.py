#!/usr/bin/env python3
"""fleet.py reads SQUAD_AGENFK the way squad-watch does, Windows paths included.

Kept apart from test_fleet.py on purpose: that file imports test_wake.py, which exits 0 on
Windows, so the windows job never reaches it. This one imports nothing from the wake tests.

Self-checking: the exit code is the verdict.

    python3 tests/test_fleet_argv.py
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FLEET = os.path.join(ROOT, "skills", "architect", "scripts", "fleet.py")


def _load(name):
    spec = importlib.util.spec_from_file_location(name, FLEET)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_windows_agenfk_command_keeps_its_backslashes_and_drops_its_quotes():
    f = _load("fleet_argv_a")
    real_which = shutil.which
    shutil.which = lambda word: None              # the launcher lookup is the machine's, not the test's
    try:
        assert f.agenfk_argv(r"C:\hostedtoolcache\python.exe C:\tools\agenfk.py", windows=True) == \
            [r"C:\hostedtoolcache\python.exe", r"C:\tools\agenfk.py"]
        assert f.agenfk_argv(r'"C:\Program Files\Python\python.exe" "C:\a b\agenfk.py"', windows=True) == \
            [r"C:\Program Files\Python\python.exe", r"C:\a b\agenfk.py"]
        assert f.agenfk_argv("python3 '/tmp/a b/agenfk.py'", windows=False)[1:] == ["/tmp/a b/agenfk.py"]
        assert f.agenfk_argv('"unclosed', windows=False) == []
        assert f.agenfk_argv('"unclosed', windows=True) == []
    finally:
        shutil.which = real_which


def test_the_first_word_goes_through_which_so_a_cmd_launcher_runs_by_name():
    f = _load("fleet_argv_b")
    real_which = shutil.which
    shutil.which = lambda word: r"C:\npm\agenfk.CMD" if word == "agenfk" else None
    try:
        assert f.agenfk_argv("agenfk", windows=True) == [r"C:\npm\agenfk.CMD"]
        assert f.agenfk_argv("agenfk --x", windows=False) == [r"C:\npm\agenfk.CMD", "--x"]
    finally:
        shutil.which = real_which


def test_call_json_runs_the_windows_split_of_a_quoted_agenfk_path():
    f = _load("fleet_argv_c")
    seen = []

    class Done:
        returncode, stdout = 0, json.dumps({"id": "x"})

    real_which, real_run = shutil.which, subprocess.run
    f.AGENFK = r'"C:\Program Files\Python\python.exe" C:\tools\agenfk.py'
    f.agenfk_argv.__defaults__ = (None, True)     # the `windows` default, as on a Windows runner
    try:
        shutil.which = lambda word: None
        subprocess.run = lambda argv, **kw: seen.append(argv) or Done()
        assert f.call_json(["get", "x", "--json"], 5) == ({"id": "x"}, False)
    finally:
        shutil.which, subprocess.run = real_which, real_run
    assert seen == [[r"C:\Program Files\Python\python.exe", r"C:\tools\agenfk.py", "get", "x", "--json"]]


def test_an_agenfk_command_that_does_not_parse_is_a_doubt_not_a_crash():
    f = _load("fleet_argv_d")
    f.AGENFK = '"unclosed'
    assert f.call_json(["get", "x"], 5) == (None, False)


if __name__ == "__main__":
    import traceback
    failed = []
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception:
                traceback.print_exc()
                failed.append(name)
                print("FAIL", name)
                continue
            print("ok ", name)
    if failed:
        print("failed:", " ".join(failed))
        sys.exit(1)
