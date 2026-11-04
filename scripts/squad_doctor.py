#!/usr/bin/env python3
"""Says whether the squad is wired: one line per check, ok / WARN / FAIL, and the fix.

    python3 scripts/squad_doctor.py [--config DIR] [--project DIR] [--json]

Run it from the clone you installed. --config defaults to
${CLAUDE_CONFIG_DIR:-~/.claude}; --project (default: the current directory) is
where the search for .agenfk/project.json starts.

Checks, in this order:
  python      this interpreter is Python 3.10 or newer
  agenfk      `agenfk` is on PATH and `agenfk health` answers (WARN otherwise:
              the links and hooks still work, the cards do not)
  links       every agents/*.md, skills/*/ and skills/vendor/*/ with a SKILL.md,
              CLAUDE.md and each hook of hooks/manifest.json sits in the config
              dir as a link back into this clone (FAIL on a missing entry, a real
              file, or a link elsewhere; a copy install.ps1 marked as its own in
              squad/copies.txt counts when it still matches the clone)
  hooks       each hook of hooks/manifest.json is registered in
              <config>/settings.json for each of its events (read and matched the
              way scripts/squad_settings.py does; WARN under another matcher)
  squad-turn  the merge-queue command is on PATH and runs this clone's script
  herdr       herdr is on PATH (WARN only: without it the session switch after a
              handoff needs the owner)
  project     a .agenfk/project.json from --project up (WARN outside a project)
  thresholds  every SQUAD_* name in hooks/squad-watch.py, with the value this
              shell gives it and whether that is the default. The names are read
              from the file, so a new variable shows up here without an edit;
              Claude Code's environment, which the hook sees, can differ.

Exit code 0 when no check FAILs, 1 otherwise. --json prints the same results as
one JSON object. Standard library only; reads, never writes.
"""
import argparse
import ast
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import squad_settings as ss  # noqa: E402  (defines functions and constants only)

WINDOWS = os.name == "nt"
WATCH = os.path.join(ROOT, "hooks", "squad-watch.py")
OK, WARN, FAIL = "ok", "WARN", "FAIL"
CHECKS = ("python", "agenfk", "links", "hooks", "squad-turn", "herdr", "project", "thresholds")
NAME = re.compile(r"\bSQUAD_[A-Z0-9_]*[A-Z0-9]")


def result(check, status, summary, fix=None, items=None):
    return {"check": check, "status": status, "summary": summary, "fix": fix, "items": items or []}


def item(name, status, detail, **extra):
    return dict({"name": name, "status": status, "detail": detail}, **extra)


def worst(statuses):
    statuses = list(statuses)
    return FAIL if FAIL in statuses else WARN if WARN in statuses else OK


# ---------------------------------------------------------------- python, agenfk

def check_python(version=None):
    version = tuple(version or sys.version_info)
    shown = ".".join(str(n) for n in version[:3])
    if version[:2] >= (3, 10):
        return result("python", OK, f"Python {shown} ({sys.executable})")
    return result("python", FAIL, f"Python {shown}: the hooks need 3.10 or newer",
                  "install Python 3.10+ and make it the `python3` on PATH")


def check_agenfk(which=shutil.which, run=subprocess.run):
    exe = which("agenfk")
    if not exe:
        return result("agenfk", WARN, "`agenfk` is not on PATH: every card, gate and verdict goes through it",
                      "install it from https://github.com/cglab-public/agenfk")
    try:
        r = run([exe, "health"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        return result("agenfk", WARN, f"`agenfk health` did not answer ({e.__class__.__name__})",
                      "start the server with `agenfk up`, then run `agenfk health`")
    if r.returncode != 0:
        failed = [line.strip() for line in (r.stdout or "").splitlines() if "FAILED" in line]
        why = f": {failed[0]}" if failed else ""
        return result("agenfk", WARN, f"`agenfk health` exited {r.returncode}{why}",
                      "start the server with `agenfk up`, then run `agenfk health`")
    return result("agenfk", OK, f"`agenfk health` answered ({exe})")


# ---------------------------------------------------------------- links

def expected_links(root, config, manifest=ss.MANIFEST):
    """(kind, name, source in the clone, entry in the config dir), as install.sh links them."""
    out = []
    for f in sorted(glob.glob(os.path.join(root, "agents", "*.md"))):
        name = os.path.basename(f)
        out.append(("agent", name, f, os.path.join(config, "agents", name)))
    for d in sorted(glob.glob(os.path.join(root, "skills", "*", ""))) + \
            sorted(glob.glob(os.path.join(root, "skills", "vendor", "*", ""))):
        d = d.rstrip("/\\")
        name = os.path.basename(d)
        if name == "vendor" or not os.path.isfile(os.path.join(d, "SKILL.md")):
            continue
        out.append(("skill", name, d, os.path.join(config, "skills", name)))
    out.append(("file", "CLAUDE.md", os.path.join(root, "CLAUDE.md"), os.path.join(config, "CLAUDE.md")))
    with open(manifest, encoding="utf-8") as fh:
        scripts = list(dict.fromkeys(h["script"] for h in json.load(fh)["hooks"]))
    for s in scripts:
        out.append(("hook", s, os.path.join(root, "hooks", s), os.path.join(config, "hooks", s)))
    return out


def marked_copies(config):
    """install.ps1's marks: <sha256>  <path>, one per line, keyed by the lowercased path."""
    marks = {}
    try:
        with open(os.path.join(config, "squad", "copies.txt"), encoding="utf-8-sig") as fh:
            for line in fh.read().splitlines():
                if len(line) > 66:
                    marks[os.path.normcase(line[66:]).lower()] = line[:64].lower()
    except OSError:
        pass
    return marks


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def link_state(src, dst, marks):
    """(status, detail) of one entry in the config dir."""
    if not os.path.lexists(dst):
        return FAIL, f"missing ({dst})"
    if os.path.normcase(os.path.realpath(dst)) == os.path.normcase(os.path.realpath(src)):                                 # a symlink, or a junction on Windows
        return OK, "linked"
    if os.path.islink(dst):
        target = os.readlink(dst)
        gone = "" if os.path.exists(dst) else ", which does not exist"
        return FAIL, f"{dst} points to {target}{gone}, not to {src}"
    if os.path.isfile(dst) and os.path.normcase(dst).lower() in marks:
        if os.path.isfile(src) and sha256(dst) == sha256(src):
            return OK, "marked copy (install.ps1), same as the clone"
        return WARN, f"{dst} is a marked copy that no longer matches {src}"
    kind = "directory" if os.path.isdir(dst) else "file"
    return FAIL, f"{dst} is a real {kind}, not a link into the clone"


def check_links(config, root=ROOT, manifest=ss.MANIFEST):
    marks = marked_copies(config)
    items = []
    for kind, name, src, dst in expected_links(root, config, manifest):
        status, detail = link_state(src, dst, marks)
        items.append(item(f"{kind} {name}", status, detail))
    good = sum(1 for i in items if i["status"] == OK)
    status = worst(i["status"] for i in items)
    installer = os.path.join(root, "install.ps1" if WINDOWS else "install.sh")
    fix = None if status == OK else (f"run {installer} (it never replaces a real file: move that one aside first)")
    return result("links", status, f"{good} of {len(items)} entries in {config} point into {root}", fix, items)


# ---------------------------------------------------------------- hooks

def check_hooks(config, manifest=ss.MANIFEST):
    path = os.path.join(config, "settings.json")
    fix = f"python3 {os.path.join(ROOT, 'scripts', 'squad_settings.py')} install --config {config}"
    try:
        plan, _ = ss.load_manifest(manifest)
    except (OSError, ValueError, KeyError, ss.BadManifest) as e:
        return result("hooks", FAIL, f"cannot read the hook manifest {manifest}: {e}", "restore hooks/manifest.json")
    try:
        settings = ss.load_settings(path)
    except ss.Refused as e:
        return result("hooks", FAIL, str(e), f"fix {path} by hand, then: {fix}")
    except OSError as e:
        return result("hooks", FAIL, f"cannot read {path}: {e}", f"fix {path}, then: {fix}")
    items, seen = [], set()
    for event, wanted in plan.items():
        groups = settings.get("hooks", {}).get(event, [])
        for script, _timeout, matcher in wanted:
            if (script, event) in seen:
                continue
            seen.add((script, event))
            mine = [g for g in groups if ss.runs(g, script, config)]
            if any(g.get("matcher") == matcher for g in mine):
                items.append(item(f"{script} on {event}", OK, "registered"))
            elif mine:
                found = ", ".join(sorted({g.get("matcher") or "none" for g in mine}))
                items.append(item(f"{script} on {event}", WARN,
                                  f"registered under matcher {found}; the manifest says {matcher or 'none'}"))
            else:
                items.append(item(f"{script} on {event}", FAIL, f"not registered in {path}"))
    good = sum(1 for i in items if i["status"] == OK)
    status = worst(i["status"] for i in items)
    return result("hooks", status, f"{good} of {len(items)} hook events registered in {path}",
                  None if status == OK else fix, items)


# ---------------------------------------------------------------- PATH, herdr, project

def check_squad_turn(which=shutil.which, root=ROOT):
    exe = which("squad-turn")
    script = os.path.join(root, "hooks", "squad-turn.py")
    installer = os.path.join(root, "install.ps1" if WINDOWS else "install.sh")
    if exe:
        if WINDOWS or os.path.normcase(os.path.realpath(exe)) == os.path.normcase(os.path.realpath(script)):
            return result("squad-turn", OK, f"on PATH ({exe})")
        return result("squad-turn", WARN, f"{exe} runs {os.path.realpath(exe)}, not {script}", f"run {installer}")
    local = os.path.join(os.path.expanduser("~"), ".local", "bin", "squad-turn")
    if os.path.lexists(local):
        return result("squad-turn", FAIL, f"{local} exists but its directory is not on PATH",
                      f"add {os.path.dirname(local)} to PATH")
    return result("squad-turn", FAIL, "not on PATH: the merge queue (`squad-turn request`) is called by name",
                  f"run {installer}")


def check_herdr(which=shutil.which):
    exe = which("herdr")
    if exe:
        return result("herdr", OK, f"on PATH ({exe})")
    return result("herdr", WARN, "not on PATH: after a handoff the owner switches the session "
                  "(`/clear` + `/clear-pickup pickup`)", "install herdr to let the watcher switch sessions itself")


def check_project(start):
    d = os.path.abspath(start)
    while True:
        p = os.path.join(d, ".agenfk", "project.json")
        if os.path.isfile(p):
            try:
                with open(p, encoding="utf-8") as fh:
                    pid = json.load(fh).get("projectId") or "no projectId"
            except (OSError, ValueError, AttributeError) as e:
                return result("project", WARN, f"{p} cannot be read: {e}", "run `agenfk init` in the project root")
            return result("project", OK, f"{p} ({pid})")
        parent = os.path.dirname(d)
        if parent == d:
            return result("project", WARN, f"no .agenfk/project.json from {os.path.abspath(start)} up: "
                          "not an AgenFK project", "run `agenfk init` in the project root")
        d = parent


# ---------------------------------------------------------------- thresholds

def literal(node):
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError):
        return None


def code_defaults(tree):
    """name -> (reader, default) for each SQUAD_* read in code: env_<kind>("SQUAD_X", default) or
    os.environ.get("SQUAD_X"[, default]); an env_* reader wins over a bare get."""
    found = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str) and NAME.fullmatch(node.args[0].value)):
            continue
        f = node.func
        reader = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
        if not reader or not (reader.startswith("env_") or reader == "get"):
            continue
        name = node.args[0].value
        default = literal(node.args[1]) if len(node.args) > 1 else None
        if name not in found or (found[name][0] == "get" and reader != "get"):
            found[name] = (reader, default)
    return found


def documented_defaults(doc):
    """name -> default, from `SQUAD_X (60)` in the module docstring."""
    out = {}
    for name, default in re.findall(r"\b(SQUAD_[A-Z0-9_]*[A-Z0-9])\s*\(([^)]*)\)", doc or ""):
        out.setdefault(name, default.strip())
    return out


def watcher_env(path=WATCH, environ=None):
    """One item per SQUAD_* name in the watcher's source, with its effective value in `environ`."""
    environ = os.environ if environ is None else environ
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    tree = ast.parse(text)
    code, doc = code_defaults(tree), documented_defaults(ast.get_docstring(tree))
    items = []
    for name in sorted(set(NAME.findall(text))):
        reader, default = code.get(name, (None, doc.get(name)))
        raw = environ.get(name)
        shown = "unset" if default is None else str(default)
        if raw is None:
            items.append(item(name, OK, f"{shown} (default)", value=default, default=default, is_default=True))
            continue
        status, detail = OK, f"{raw} (set; default {shown})"
        if reader in ("env_int", "env_float", "env_num"):
            try:
                (float if reader != "env_int" else int)(raw)
            except ValueError:
                status, detail = WARN, f"{raw!r} is not a number: the watcher uses the default {shown}"
        items.append(item(name, status, detail, value=raw, default=default, is_default=False))
    return items


def check_thresholds(path=WATCH, environ=None):
    items = watcher_env(path, environ)
    set_here = sum(1 for i in items if not i["is_default"])
    status = worst(i["status"] for i in items)
    return result("thresholds", status,
                  f"{len(items)} SQUAD_* variables in {os.path.relpath(path, ROOT)}, {set_here} set in this shell "
                  "(the hook sees Claude Code's environment)",
                  None if status == OK else "set it to a whole number, or unset it", items)


# ---------------------------------------------------------------- report

def run_checks(config, project):
    steps = (("python", check_python), ("agenfk", check_agenfk), ("links", lambda: check_links(config)),
             ("hooks", lambda: check_hooks(config)), ("squad-turn", check_squad_turn), ("herdr", check_herdr),
             ("project", lambda: check_project(project)), ("thresholds", check_thresholds))
    out = []
    for name, fn in steps:
        try:
            out.append(fn())
        except Exception as e:               # one broken check must not hide the others
            out.append(result(name, FAIL, f"the check itself failed: {e.__class__.__name__}: {e}",
                              "report this with the output of the doctor"))
    return out


def render(checks):
    lines = []
    for c in checks:
        lines.append(f"{c['status']:<5} {c['check']:<11} {c['summary']}")
        for i in c["items"]:
            if c["check"] == "thresholds" or i["status"] != OK:
                lines.append(f"{'':<6}{i['status']:<5} {i['name']}: {i['detail']}")
        if c["status"] != OK and c["fix"]:
            lines.append(f"{'':<6}fix:  {c['fix']}")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", default=None, help="Claude Code config dir (default: $CLAUDE_CONFIG_DIR or ~/.claude)")
    ap.add_argument("--project", default=os.curdir, help="where to look for .agenfk/project.json (default: here)")
    ap.add_argument("--json", action="store_true", help="print the results as JSON")
    a = ap.parse_args(argv)
    config = os.path.abspath(a.config or ss.default_config())
    checks = run_checks(config, a.project)
    status = worst(c["status"] for c in checks)
    code = 1 if status == FAIL else 0
    if a.json:
        print(json.dumps({"clone": ROOT, "config": config, "status": status, "exit": code, "checks": checks},
                         indent=2))
    else:
        print(render(checks))
        print(f"\n{status}: " + ("the squad is wired." if code == 0 and status == OK else
                                 "nothing is broken; the WARN lines say what is missing." if code == 0 else
                                 "fix the FAIL lines above, then run this again."))
    return code


if __name__ == "__main__":
    sys.exit(main())
