#!/usr/bin/env python3
"""The scoreboard is generated, checks limits, and the ratchet only tightens.

Self-checking: the exit code is the verdict. Each case builds a throwaway project in a
temporary directory and runs scoreboard.py the way CI would. Every project, and every
temporary file the script or a metric command makes (TMPDIR points there), lives under
one directory that is removed at the end; the last cases prove nothing is left behind.

scoreboard.py needs Python 3.11+ (tomllib). Under an older Python this file checks the
script's one-line refusal with that interpreter and runs every other case with a 3.11+
one found on PATH; with none found, that is a failure, not a skip.

    python3 tests/test_scoreboard.py

To also check the skill for names that must not be published (a project, a customer, a
person), list them in SQUAD_PRIVATE_TERMS, comma-separated. The list lives in your
environment, never in the repository.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile


if os.name == "nt":
    # metric commands run through shell=True, which is cmd.exe there (`;` does not separate
    # commands), and the 3.10 leg has no python3.NN on PATH to run the tomllib (3.11+) script
    print("skipped: scoreboard metrics are POSIX shell commands and need tomllib (3.11+); "
          "not yet verified on Windows")
    sys.exit(0)

ROOT =os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(ROOT, "skills", "project-rails")
SCOREBOARD = os.path.join(SKILL, "scripts", "scoreboard.py")

_terms = [re.escape(t.strip()) for t in os.environ.get("SQUAD_PRIVATE_TERMS", "").split(",") if t.strip()]
PRIVATE = re.compile("|".join([r"/(?:home/[a-z]+|Users/[A-Za-z]+)/", r"trilhos-de-projeto", r"\bmodelos/"]
                              + _terms), re.I)

FAILURES = []
BASE = None      # the one temporary directory of the run, set in main
PROJECTS = []    # every project root made, to prove each one is gone at the end
PY = None        # a Python 3.11+ that runs scoreboard.py


def modern_python():
    """sys.executable if it is 3.11+, else the first 3.11+ interpreter on PATH, else None."""
    candidates = [sys.executable] + [shutil.which(n) for n in
                                     ("python3.14", "python3.13", "python3.12", "python3.11",
                                      "python3", "python")]
    for exe in candidates:
        if not exe:
            continue
        try:
            r = subprocess.run([exe, "-I", "-c", "import sys; print(sys.version_info >= (3, 11))"],
                               capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if r.stdout.strip() == "True":
            return exe
    return None


def env():
    return {**os.environ, "TMPDIR": BASE, "PYTHONDONTWRITEBYTECODE": "1"}


def project(metrics, checklists=None):
    root = tempfile.mkdtemp(prefix="project-", dir=BASE)
    PROJECTS.append(root)
    os.makedirs(os.path.join(root, "scoreboard"))
    with open(os.path.join(root, "scoreboard", "metrics.toml"), "w") as f:
        f.write(metrics)
    for name, text in (checklists or {}).items():
        with open(os.path.join(root, "scoreboard", name), "w") as f:
            f.write(text)
    return root


def scoreboard(root, *args):
    r = subprocess.run([PY, "-B", SCOREBOARD, "--root", root, *args],
                       capture_output=True, text=True, timeout=60, env=env())
    return r.returncode, r.stdout + r.stderr


def read(root, *parts):
    with open(os.path.join(root, *parts)) as f:
        return f.read()


def git(root, *args):
    subprocess.run(["git", "-C", root, *args], check=True, capture_output=True,
                   env={**env(), "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


def case(name, cond, detail=""):
    print(("ok    " if cond else "FAIL  ") + name)
    if not cond:
        FAILURES.append(name)
        if detail:
            print("      " + detail.replace("\n", "\n      "))


def metric(mid, command, limit, direction="floor", extra=""):
    return (f'[[metric]]\nid = "{mid}"\nname = "{mid} measured"\n'
            f"command = '{command}'\n{direction} = {limit}\n{extra}\n")


TESTS = metric("tests", "echo 12 failed; echo 1820 passed", 1800, extra='regex = "(\\\\d+) passed"')
LINT = metric("lint", "echo errors: 3", 5, direction="ceiling")
AREA = ('area = "Reports"\n'
        '[[item]]\nid = "REP-1"\ntarget = "Exports PDF"\nstatus = "done"\nnote = "api/pdf.py:40"\n'
        '[[item]]\nid = "REP-2"\ntarget = "Schedules delivery | weekly"\nstatus = "missing"\n')


def t_measure_render_and_check():
    root = project(TESTS + LINT, {"reports.toml": AREA})
    rc, out = scoreboard(root, "measure")
    case("measure within the limits exits 0", rc == 0, out)
    measured = json.loads(read(root, "scoreboard", "measured.json"))
    case("the regex takes the group, not the last number on the line",
         measured["values"].get("tests") == 1820, str(measured))
    case("without a regex the last number of the output counts", measured["values"].get("lint") == 3,
         str(measured))
    doc = read(root, "docs", "scoreboard.md")
    case("the doc shows limit, value and checklist",
         "≥ 1800" in doc and "| 1820 |" in doc and "REP-2" in doc and "1 done, 0 partial, 1 missing" in doc,
         doc)
    case("a pipe inside a cell is escaped", "Schedules delivery \\| weekly" in doc, doc)
    rc, out = scoreboard(root, "render")
    case("render is deterministic", rc == 0 and read(root, "docs", "scoreboard.md") == doc, out)
    rc, out = scoreboard(root, "check")
    case("check passes with everything up to date", rc == 0, out)


def t_doc_edited_by_hand():
    root = project(LINT)
    scoreboard(root, "measure")
    with open(os.path.join(root, "docs", "scoreboard.md"), "a") as f:
        f.write("\ntouch-up\n")
    rc, out = scoreboard(root, "check")
    case("a doc edited by hand fails check", rc == 1 and "out of date" in out, out)
    os.remove(os.path.join(root, "docs", "scoreboard.md"))
    rc, out = scoreboard(root, "check")
    case("a missing doc fails check", rc == 1 and "does not exist" in out, out)


def t_limits_in_both_directions():
    root = project(metric("tests", "echo 1790", 1800))
    rc, out = scoreboard(root, "measure")
    case("below the floor fails measure", rc == 1 and "OUT" in out, out)
    rc, out = scoreboard(root, "check")
    case("below the floor fails check", rc == 1 and "outside" in out, out)
    root = project(metric("lint", "echo 6", 5, direction="ceiling"))
    rc, out = scoreboard(root, "measure")
    case("above the ceiling fails measure", rc == 1 and "OUT" in out, out)
    root = project(metric("lint", "echo 5", 5, direction="ceiling"))
    rc, out = scoreboard(root, "measure")
    case("exactly at the limit passes", rc == 0, out)


def t_no_write():
    root = project(LINT)
    rc, out = scoreboard(root, "measure", "--no-write")
    case("--no-write checks and writes nothing",
         rc == 0 and not os.path.exists(os.path.join(root, "scoreboard", "measured.json"))
         and not os.path.exists(os.path.join(root, "docs", "scoreboard.md")), out)


def t_not_measured():
    root = project(LINT)
    scoreboard(root, "render")
    rc, out = scoreboard(root, "check")
    case("a metric never measured fails check", rc == 1 and "not measured" in out, out)


def t_command_that_fails():
    root = project(metric("tests", "echo 1900 passed; exit 1", 1800))
    rc, out = scoreboard(root, "measure")
    case("a command exiting != 0 is an error, not a number", rc == 1 and "exited with 1" in out
         and "Traceback" not in out, out)
    root = project(metric("tests", "echo nothing", 1800))
    rc, out = scoreboard(root, "measure")
    case("output with no number is a readable error", rc == 1 and "no number" in out, out)
    root = project(metric("tests", "echo 1900 passed; exit 1", 1800, extra="ignore_exit = true"))
    rc, out = scoreboard(root, "measure")
    case("ignore_exit takes the number even with exit != 0", rc == 0, out)


def t_tighten_only_tightens():
    root = project("# header\n" + metric("tests", "echo 1850", 1800)
                   + metric("lint", "echo 2", 5, direction="ceiling"))
    scoreboard(root, "measure")
    rc, out = scoreboard(root, "tighten")
    toml = read(root, "scoreboard", "metrics.toml")
    case("tighten moves floor and ceiling to the measured value",
         rc == 0 and "floor = 1850" in toml and "ceiling = 2" in toml and "# header" in toml, out + toml)
    rc, out = scoreboard(root, "check")
    case("after tighten the scoreboard is still up to date", rc == 0, out)
    root = project(metric("tests", "echo 1700", 1800))
    scoreboard(root, "measure")
    rc, out = scoreboard(root, "tighten")
    case("tighten never loosens", "floor = 1800" in read(root, "scoreboard", "metrics.toml"), out)


def t_ratchet_against_the_base():
    root = project(metric("tests", "echo 1850", 1800) + LINT)
    git(root, "init", "-q", "-b", "main")
    scoreboard(root, "measure")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "base")
    path = os.path.join(root, "scoreboard", "metrics.toml")
    original = read(root, "scoreboard", "metrics.toml")
    rc, out = scoreboard(root, "check", "--base", "main")
    case("limits equal to the base pass", rc == 0, out)
    with open(path, "w") as f:
        f.write(original.replace("floor = 1800", "floor = 1700"))
    scoreboard(root, "render")
    rc, out = scoreboard(root, "check", "--base", "main")
    case("a floor lowered since the base fails", rc == 1 and "floor dropped" in out, out)
    with open(path, "w") as f:
        f.write(original.replace("ceiling = 5", "ceiling = 9"))
    scoreboard(root, "render")
    rc, out = scoreboard(root, "check", "--base", "main")
    case("a ceiling raised since the base fails", rc == 1 and "ceiling rose" in out, out)
    with open(path, "w") as f:
        f.write(metric("tests", "echo 1850", 1800))
    scoreboard(root, "render")
    rc, out = scoreboard(root, "check", "--base", "main")
    case("a metric removed since the base fails", rc == 1 and "removed" in out, out)
    with open(path, "w") as f:
        f.write(original.replace("floor = 1800", "floor = 1850"))
    scoreboard(root, "render")
    rc, out = scoreboard(root, "check", "--base", "main")
    case("a floor tightened since the base passes", rc == 0, out)
    with open(path, "w") as f:
        f.write(original.replace("floor = 1800", "ceiling = 1800"))
    scoreboard(root, "render")
    rc, out = scoreboard(root, "check", "--base", "main")
    case("switching floor to ceiling since the base fails", rc == 1
         and "switched floor to ceiling" in out, out)
    with open(path, "w") as f:
        f.write(original.replace("echo 1850", "echo 999999"))
    scoreboard(root, "render")
    rc, out = scoreboard(root, "check", "--base", "main")
    case("swapping the command with the same floor fails", rc == 1
         and "changed how it measures (command)" in out, out)
    rc, out = scoreboard(root, "check", "--base", "main", "--accept-measurement", "tests")
    case("--accept-measurement lets the approved swap through", rc == 0, out)
    with open(path, "w") as f:
        f.write(original.replace("echo 1850", "echo 999999").replace("floor = 1800", "floor = 1"))
    scoreboard(root, "render")
    rc, out = scoreboard(root, "check", "--base", "main", "--accept-measurement", "tests")
    case("--accept-measurement does not let a lowered floor through", rc == 1 and "floor dropped" in out, out)
    with open(path, "w") as f:
        f.write(original.replace("floor = 1800", "floor = 1800\nregex = '(\\d+)'"))
    scoreboard(root, "render")
    rc, out = scoreboard(root, "check", "--base", "main")
    case("swapping only the regex with the same floor fails", rc == 1
         and "changed how it measures (regex)" in out, out)
    rc, out = scoreboard(root, "check", "--base", "main", "--accept-measurement", "tests")
    case("--accept-measurement lets the approved regex through", rc == 0, out)
    rc, out = scoreboard(root, "check", "--base", "main", "--accept-measurement", "tests", "tset")
    case("--accept-measurement with an unknown id warns", rc == 0 and "warning" in out and "tset" in out, out)
    with open(path, "w") as f:
        f.write(original.replace("floor = 1800", 'floor = 1800\nignore_exit = true'))
    scoreboard(root, "render")
    rc, out = scoreboard(root, "check", "--base", "main")
    case("turning on ignore_exit since the base fails", rc == 1 and "ignore_exit" in out, out)
    rc, out = scoreboard(root, "check", "--base", "does-not-exist")
    case("an unknown base is an invalid configuration", rc == 2 and "unknown base" in out, out)


def t_tighten_with_another_layout():
    root = project('[[metric]]\n"id" = "b"\n"floor" = 1 # goal 10\ncommand = "echo 9"\n')
    scoreboard(root, "measure")
    rc, out = scoreboard(root, "tighten")
    toml = read(root, "scoreboard", "metrics.toml")
    case("tighten rewrites a quoted key and keeps the comment",
         rc == 0 and '"floor" = 9 # goal 10' in toml, out + toml)
    text = 'metric = [{id = "a", command = "echo 9", floor = 1}]\n'
    root = project(text)
    scoreboard(root, "measure")
    rc, out = scoreboard(root, "tighten")
    case("tighten that cannot rewrite exits 2 and promises nothing",
         rc == 2 and read(root, "scoreboard", "metrics.toml") == text and "->" not in out
         and "could not rewrite" in out, out)


def t_numbers_in_the_output():
    for name, command, expected in [
        ("scientific notation counts as the whole number", "echo 1e3", 1000),
        ("without a regex the last number counts, not the first", "echo 5 of 10", 10),
        ("a full stop ending a sentence does not become a decimal", "echo errors: 3.", 3),
        ("a symbol glued after the number does not get in the way", "echo coverage 87%", 87),
    ]:
        root = project(metric("x", command, 0))
        rc, out = scoreboard(root, "measure")
        value = json.loads(read(root, "scoreboard", "measured.json"))["values"].get("x")
        case(name, rc == 0 and value == expected, f"{out} value={value}")
    for name, command in [("thousands separator", "echo 1,820"),
                          ("version with two dots", "echo 3.14.4"),
                          ("dot with no digit before it", "echo .5"),
                          ("thousands underscore", "echo 1_000"),
                          ("date", "echo 2024-01-05"),
                          ("glued unit", "echo 1820 passed in 3.21s")]:
        rc, out = scoreboard(project(metric("x", command, 0)), "measure")
        case(f"{name} is ambiguous: an error, not a piece of it", rc == 1 and "ambiguous" in out, out)
    rc, out = scoreboard(project(metric("x", "echo 1e999", 0)), "measure")
    case("an infinite number is an error, it does not pass any floor", rc == 1 and "non-finite" in out, out)


def t_areas_in_file_order():
    # eight files created back to front. On ext4 the listing follows the hash and the test
    # catches a missing sorted; on tmpfs (/tmp) it already comes in order and the test cannot tell
    names = [f"{c}-area.toml" for c in "hgfedcba"]
    root = project(LINT, {n: f'area = "Area {n[0].upper()}"\n' for n in names})
    scoreboard(root, "render")
    doc = read(root, "docs", "scoreboard.md")
    positions = [doc.find(f"## Area {c}") for c in "ABCDEFGH"]
    case("areas come out in file name order", -1 not in positions and positions == sorted(positions), doc)


def t_invalid_configuration():
    for name, text in [
        ("broken toml", "[[metric]\nid = 'x'"),
        ("floor and ceiling together", metric("x", "echo 1", 1) + "ceiling = 2\n"),
        ("no command", '[[metric]]\nid = "x"\nfloor = 1\n'),
        ("invalid regex", metric("x", "echo 1", 1, extra='regex = "("')),
        ("repeated id", metric("x", "echo 1", 1) + metric("x", "echo 1", 1)),
    ]:
        rc, out = scoreboard(project(text), "measure")
        case(f"{name}: exit 2 with no traceback", rc == 2 and "Traceback" not in out, out)
    root = project(LINT, {"bad.toml": 'area = "X"\n[[item]]\nid = "A"\nstatus = "almost"\n'})
    rc, out = scoreboard(root, "measure")
    case("invalid checklist status: exit 2", rc == 2 and "status" in out, out)


def t_python_without_tomllib():
    # the script is copied into projects whose CI may run an older Python: it must say
    # what it needs, not die with a traceback on `import tomllib`
    hide = ("import runpy, sys; sys.modules['tomllib'] = None; "
            f"sys.argv = [{SCOREBOARD!r}, '--root', {BASE!r}, 'render']; "
            f"runpy.run_path({SCOREBOARD!r}, run_name='__main__')")
    r = subprocess.run([PY, "-I", "-B", "-c", hide], capture_output=True, text=True,
                       timeout=60, env=env())
    out = r.stdout + r.stderr
    case("without tomllib: exit 2 with one line naming Python 3.11+, no traceback",
         r.returncode == 2 and "needs Python 3.11+" in out and "Traceback" not in out, out)
    if sys.version_info < (3, 11):
        r = subprocess.run([sys.executable, "-B", SCOREBOARD, "--root", BASE, "render"],
                           capture_output=True, text=True, timeout=60, env=env())
        out = r.stdout + r.stderr
        case(f"on this Python {sys.version_info[0]}.{sys.version_info[1]}: exit 2 with the message",
             r.returncode == 2 and "needs Python 3.11+" in out and "Traceback" not in out, out)


def t_skill_is_public():
    found = []
    for folder, _, files in os.walk(SKILL):
        for name in files:
            path = os.path.join(folder, name)
            with open(path, encoding="utf-8", errors="replace") as f:
                for n, line in enumerate(f, 1):
                    if PRIVATE.search(line):
                        found.append(f"{os.path.relpath(path, ROOT)}:{n}")
    case("the skill holds no private path, private term or untranslated name", not found,
         "\n".join(found))


def main():
    global BASE, PY
    PY = modern_python()
    if PY is None:
        print("FAIL  no Python 3.11+ found (scoreboard.py needs tomllib): install one or put it on PATH")
        return 1
    with tempfile.TemporaryDirectory(prefix="scoreboard-") as base:
        BASE = base
        for name, fn in sorted(globals().items()):
            if name.startswith("t_") and callable(fn):
                fn()
    case("every throwaway project was made inside the run's temporary directory",
         bool(PROJECTS) and all(os.path.dirname(p) == BASE for p in PROJECTS), "\n".join(PROJECTS))
    left = [p for p in [BASE] + PROJECTS if os.path.exists(p)]
    case("nothing is left in the temporary directory after the run", not left, "\n".join(left))
    print(f"\n{len(FAILURES)} failure(s)" if FAILURES else "\nall ok")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
