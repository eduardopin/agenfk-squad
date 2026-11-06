#!/usr/bin/env python3
"""The CI workflows keep running the tests that exist, on the systems the squad promises.

Self-checking: the exit code is the verdict. It reads .github/workflows as text (no YAML library needed) and
looks at steps, not at the file as a whole: what it guards is that a test file, a platform or a path filter
does not go missing without anyone noticing, and that a comment or a disabled step does not pass for a real one.
On Windows it also asks PowerShell's own parser whether install.ps1 and get.ps1 are valid scripts.

    python3 tests/test_ci_config.py
"""
import glob
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FLOWS = os.path.join(ROOT, ".github", "workflows")


def text(name):
    with open(os.path.join(FLOWS, name), encoding="utf-8") as fh:
        return fh.read()


TESTS = text("tests.yml")
INSTALLER = text("installer.yml")


def job(source, name):
    """The text of one job: from `  <name>:` to the next job (two-space indent) or the end."""
    m = re.search(rf"^  {re.escape(name)}:\n(.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)", source, re.S | re.M)
    assert m, f"no job {name}"
    return m.group(1)


def steps(source):
    """The steps that can run: comment lines gone, steps with `if: false` gone. A list of step texts."""
    body = source.split("\njobs:", 1)[1] if "\njobs:" in source else source
    body = "\n".join(line for line in body.split("\n") if not line.lstrip().startswith("#"))
    found = re.split(r"\n(?=      - )", body)
    return [step for step in found if "if: false" not in step and "if: ${{ false }}" not in step]


def test_the_windows_job_runs_the_whole_suite_on_a_windows_runner():
    win = job(TESTS, "windows")
    assert "runs-on: windows-latest" in win, win
    assert re.search(r"defaults:\n\s+run:\n\s+shell: bash", win), "the loop is POSIX shell syntax: Git Bash on the Windows runner"
    live = steps(win)
    loop = [st for st in live if "for t in tests/test_*.py; do" in st and 'python -B "$t"' in st]
    assert loop, "a live step loops over every test file by glob (the ones that are not for Windows skip themselves)"
    assert any("py_compile" in st and "hooks/*.py" in st for st in live), "the hooks are compiled on Windows"


def test_every_test_file_is_run_somewhere_on_linux():
    linux_steps = steps(job(TESTS, "watcher"))
    installer_steps = steps(INSTALLER)
    everywhere = [st for st in installer_steps if "runner.os == 'Windows'" not in st]
    ran = " ".join(linux_steps) + " " + " ".join(everywhere)
    # a live Linux step that runs tests/test_*.py by glob runs every file, the new ones too
    by_glob = any(re.search(r"(?:files=\(|for t in )tests/test_\*\.py\b", st) and 'python -B "$t"' in st
                  for st in linux_steps)
    missing = [] if by_glob else [os.path.basename(p) for p in sorted(glob.glob(os.path.join(ROOT, "tests", "test_*.py")))
                                  if os.path.basename(p) not in ran]
    # the PowerShell installers say so and exit 0 on Linux, and run for real in the Windows-only installer step
    assert not missing, f"not run by a live step of the Linux job or the installer workflow: {missing}"
    win_only = " ".join(st for st in installer_steps if "runner.os == 'Windows'" in st)
    assert "test_install_ps1.py" in win_only and "test_get_ps1.py" in win_only, win_only


def test_every_test_path_the_workflows_name_exists():
    named = set(re.findall(r"tests/[A-Za-z0-9_]+\.py", TESTS + INSTALLER))
    gone = sorted(n for n in named if not os.path.isfile(os.path.join(ROOT, *n.split("/"))))
    assert not gone, gone


def test_the_installer_workflow_covers_windows_and_the_files_that_matter_there():
    assert "windows-latest" in INSTALLER, INSTALLER
    assert "timeout-minutes: ${{ matrix.os == 'windows-latest' && 30 || 10 }}" in INSTALLER, "PowerShell is slower: its own budget"
    filters = [block.split("\n\n")[0] for block in INSTALLER.split("    paths:\n")[1:]]
    assert len(filters) == 2, "one path filter under push and one under pull_request"
    for block in filters:
        for path in ("install.ps1", "get.ps1", "install.sh", "get.sh", "scripts/squad_settings.py", "hooks/manifest.json",
                     "tests/test_install_ps1.py", "tests/test_get_ps1.py", ".gitattributes"):
            assert f"      - {path}\n" in block + "\n", f"a change to {path} should run the installer workflow"


def test_each_test_file_runs_with_its_own_empty_tmpdir_and_fails_when_it_leaves_entries():
    """The leak check lives inside the existing per-file loops (no second pass of the suite)."""
    for name, loop_head in (("watcher", 'for t in "${files[@]}"; do'), ("windows", "for t in tests/test_*.py; do")):
        live = steps(job(TESTS, name))
        loop = [st for st in live if loop_head in st and 'python -B "$t"' in st]
        assert len(loop) == 1, (name, "one live loop step runs every file")
        body = loop[0]
        assert "tmp=$(mktemp -d)" in body, (name, "a fresh empty directory per file")
        assert re.search(r'TMPDIR="\$\w+" TEMP="\$\w+" TMP="\$\w+" python -B "\$t"', body), \
            (name, "the file runs with TMPDIR, TEMP and TMP on that directory (Python reads TMPDIR first, then TEMP, TMP)")
        run = body.index('python -B "$t"')
        check = body.index('ls -A "$tmp"')
        assert run < check < body.index('rm -rf "$tmp"'), (name, "run, then look at what is left, then remove the directory")
        # GitHub runs `shell: bash` steps with -eo pipefail: neither the cleanup nor the listing may abort the step
        assert re.search(r'rm -rf "\$tmp" \|\| (?:true|echo )', body), (name, "a failed rm (locked dir, open file on Windows) must not stop later files")
        assert "mapfile -t left < <(ls -A" in body and "${left[@]:0:5}" in body, (name, "the listing is an array slice, not a pipe")
        assert not re.search(r"\|\s*(?:head|paste|sed|wc)\b", body), (name, "no `| head`: SIGPIPE (141) under pipefail on a big leak")
        assert re.search(r"::error file=\$t::\$t left \$n entries in TMPDIR \(\$names\)", body), (name, "the annotation names the file and the leftovers")
    win = steps(job(TESTS, "windows"))
    assert any("cygpath -m" in st for st in win), "Windows hands native Python a Windows-form path, not Git Bash's /tmp/..."
    linux = [st for st in steps(job(TESTS, "watcher")) if 'for t in "${files[@]}"' in st][0]
    assert "failed+=(\"$t\")" in linux and 'echo "ran ${#files[@]} test files, ${#failed[@]} failed"' in linux, \
        "a leaking file counts in the failed total and the summary line is kept"
    assert tests_run_count(TESTS) == 2, "no second loop over the suite for the leak check"


def tests_run_count(source):
    """How many times the workflow runs the test files (one `python -B "$t"` per job loop)."""
    return source.count('python -B "$t"')


def test_the_private_path_check_is_the_script_not_a_one_line_grep():
    live = " ".join(steps(job(TESTS, "watcher")) + steps(job(TESTS, "windows")))
    assert live.count("python scripts/check_private_paths.py") == 2, "on Linux and on Windows"
    assert "grep -rnE '/home/" not in TESTS


def test_the_powershell_scripts_parse():
    if os.name != "nt":
        return        # PowerShell's own parser, on the platform that runs them
    shell = shutil.which("powershell") or shutil.which("pwsh")
    if not shell:
        return
    for script in ("install.ps1", "get.ps1"):
        path = os.path.join(ROOT, script).replace("'", "''")
        cmd = ("$e = $null; [void][System.Management.Automation.Language.Parser]::ParseFile('" + path + "', [ref]$null, [ref]$e); "
               "if ($e) { $e | ForEach-Object { $_.Message }; exit 1 }")
        r = subprocess.run([shell, "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, (script, r.stdout, r.stderr)


def test_the_workflows_are_valid_yaml_when_a_yaml_library_is_at_hand():
    try:
        import yaml
    except ImportError:
        print("skipped: no PyYAML here")
        return
    for name in ("tests.yml", "installer.yml"):
        doc = yaml.safe_load(text(name))
        assert isinstance(doc.get("jobs"), dict) and doc["jobs"], name


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok ", name)
