#!/usr/bin/env python3
"""Says whether the squad is wired: one line per check, ok / WARN / FAIL, and the fix.

    python3 scripts/squad_doctor.py [--config DIR] [--project DIR] [--json]

Run it from the clone you installed. --config defaults to
${CLAUDE_CONFIG_DIR:-~/.claude}; --project (default: the current directory) is
where the search for .agenfk/project.json starts.

Checks, in this order:
  python      this interpreter is Python 3.10 or newer
  agenfk      `agenfk` is on PATH and `agenfk health` answers with no FAILED or
              "potential issue" line, whatever its exit code (WARN otherwise:
              the links and hooks still work, the cards do not)
  links       every agents/*.md, skills/*/ and skills/vendor/*/ with a SKILL.md,
              CLAUDE.md and each hook of hooks/manifest.json sits in the config
              dir as a link back into this clone (FAIL on a missing entry, a real
              file, or a link elsewhere; a copy install.ps1 marked as its own in
              squad/copies.txt counts when it still matches the clone)
  hooks       each hook of hooks/manifest.json is registered in
              <config>/settings.json for each of its events (read and matched the
              way scripts/squad_settings.py does; WARN under another matcher, FAIL
              on a matcher that is not a string or a settings.json that cannot
              be read)
  squad-turn  the merge-queue command is on PATH and runs this clone's script
              (FAIL also names a dangling ~/.local/bin/squad-turn)
  herdr       herdr is on PATH (WARN only: without it the session switch after a
              handoff needs the owner)
  project     a .agenfk/project.json from --project up (WARN outside a project,
              or when it has no projectId)
  thresholds  every SQUAD_* name in hooks/squad-watch.py, with the value this
              shell gives it and whether that is the default. The names are read
              from the file, so a new variable shows up here without an edit;
              Claude Code's environment, which the hook sees, can differ. WARN
              when a number is not one, when the watcher clamps it (the value it
              really uses is shown), or when SQUAD_WATCH=0 / SQUAD_TURN=0 turns
              part of the squad off.

Exit code 0 when no check FAILs, 1 otherwise. --json prints the same results as
one JSON object. Standard library only; reads, never writes (not even the
bytecode cache of the module it imports).
"""
import argparse
import ast
import glob
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True       # importing squad_settings must not leave scripts/__pycache__ behind
import squad_settings as ss  # noqa: E402  (defines functions and constants only)

WINDOWS = os.name == "nt"
WATCH = os.path.join(ROOT, "hooks", "squad-watch.py")
OK, WARN, FAIL = "ok", "WARN", "FAIL"
CHECKS = ("python", "agenfk", "links", "hooks", "squad-turn", "herdr", "project", "thresholds")
NAME = re.compile(r"\bSQUAD_[A-Z0-9_]*[A-Z0-9]")
# Values the watcher reads as a mode, not as a number (squad-watch.py main_window(), MAIN_WARN).
SENTINELS = {"SQUAD_WINDOW": {0: "auto: the model's window, 1000000 for Claude"},
             "SQUAD_CTX_WARN": {0: "off"}, "SQUAD_CTX_HANDOFF": {0: "off"}}
# "0" turns a part of the squad off (squad-watch.py main() and the merge-turn guard).
KILL_SWITCHES = {"SQUAD_WATCH": "watcher disabled: no handoff order, no dispatch limit, no loop guard",
                 "SQUAD_TURN": "merge queue disabled: no turn, no conflict check, no seal"}


def q(path):
    """A path as it pastes into the shell the fix is run from."""
    if WINDOWS:
        return f'"{path}"' if re.search(r"[\s'&;()]", path) else path
    return shlex.quote(path)


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


def check_agenfk(which=shutil.which, run=subprocess.run, timeout=30):
    exe = which("agenfk")
    if not exe:
        return result("agenfk", WARN, "`agenfk` is not on PATH: every card, gate and verdict goes through it",
                      "install it from https://github.com/cglab-public/agenfk")
    try:
        r = run([exe, "health"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return result("agenfk", WARN, f"`agenfk health` did not answer ({e.__class__.__name__})",
                      "start the server with `agenfk up`, then run `agenfk health`")
    # `agenfk health` exits 0 even with the server down: it prints "FAILED" per check and
    # "Found N potential issue(s)" at the end, so the output decides, whatever the exit code.
    lines = [line.strip() for line in ((r.stdout or "") + "\n" + (r.stderr or "")).splitlines() if line.strip()]
    failed = [line for line in lines if "FAILED" in line] or [line for line in lines if "potential issue" in line]
    if r.returncode != 0 or failed:
        why = f": {(failed or lines)[0]}" if failed or lines else ""
        said = f"exited {r.returncode}" if r.returncode != 0 else "reports a problem"
        server = any("API Server" in line for line in failed) or not failed
        return result("agenfk", WARN, f"`agenfk health` {said}{why}",
                      "start the server with `agenfk up`, then run `agenfk health`" if server
                      else "run `agenfk health` and fix what it reports")
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
    fix = None if status == OK else (f"run {q(installer)} (it never replaces a real file: move that one aside first)")
    return result("links", status, f"{good} of {len(items)} entries in {config} point into {root}", fix, items)


# ---------------------------------------------------------------- hooks

def check_hooks(config, manifest=ss.MANIFEST):
    path = os.path.join(config, "settings.json")
    fix = f"python3 {q(os.path.join(ROOT, 'scripts', 'squad_settings.py'))} install --config {q(config)}"
    try:
        plan, _ = ss.load_manifest(manifest)
    except (OSError, ValueError, KeyError, ss.BadManifest) as e:
        return result("hooks", FAIL, f"cannot read the hook manifest {manifest}: {e}", "restore hooks/manifest.json")
    if os.path.lexists(path) and not os.path.exists(path):      # a dangling link or a symlink loop
        try:
            open(path, encoding="utf-8").close()
        except OSError as e:
            return result("hooks", FAIL, f"cannot read {path}: {e}", f"fix {q(path)}, then: {fix}")
    try:
        settings = ss.load_settings(path)
    except ss.Refused as e:
        return result("hooks", FAIL, str(e), f"fix {q(path)} by hand, then: {fix}")
    except OSError as e:
        return result("hooks", FAIL, f"cannot read {path}: {e}", f"fix {q(path)}, then: {fix}")
    items, seen = [], set()
    for event, wanted in plan.items():
        groups = settings.get("hooks", {}).get(event, [])
        for script, _timeout, matcher in wanted:
            if (script, event) in seen:
                continue
            seen.add((script, event))
            mine = [g for g in groups if ss.runs(g, script, config)]
            if any(ss.same_matcher(g.get("matcher"), matcher) for g in mine):
                items.append(item(f"{script} on {event}", OK, "registered"))
            elif any(not isinstance(g.get("matcher"), (str, type(None))) for g in mine):
                bad = ", ".join(json.dumps(g.get("matcher")) for g in mine
                                if not isinstance(g.get("matcher"), (str, type(None))))
                items.append(item(f"{script} on {event}", FAIL,
                                  f"malformed entry: its matcher {bad} is not a string; the manifest says "
                                  f"{matcher or 'none'}"))
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

def check_squad_turn(which=shutil.which, root=ROOT, home=None):
    exe = which("squad-turn")
    script = os.path.join(root, "hooks", "squad-turn.py")
    installer = os.path.join(root, "install.ps1" if WINDOWS else "install.sh")
    if exe:
        if WINDOWS or os.path.normcase(os.path.realpath(exe)) == os.path.normcase(os.path.realpath(script)):
            return result("squad-turn", OK, f"on PATH ({exe})")
        return result("squad-turn", WARN, f"{exe} runs {os.path.realpath(exe)}, not {script}", f"run {q(installer)}")
    local = os.path.join(home or os.path.expanduser("~"), ".local", "bin", "squad-turn")
    if os.path.islink(local) and not os.path.exists(local):
        return result("squad-turn", FAIL, f"{local} is a dangling link (to {os.readlink(local)})",
                      f"run {q(installer)}")
    if os.path.lexists(local):
        on_path = os.path.normcase(os.path.dirname(local)) in {
            os.path.normcase(d) for d in os.environ.get("PATH", "").split(os.pathsep) if d}
        if on_path:
            return result("squad-turn", FAIL, f"{local} is in a directory on PATH but is not an executable file",
                          f"run {q(installer)}")
        return result("squad-turn", FAIL, f"{local} exists but its directory is not on PATH",
                      f"add {q(os.path.dirname(local))} to PATH")
    return result("squad-turn", FAIL, "not on PATH: the merge queue (`squad-turn request`) is called by name",
                  f"run {q(installer)}")


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
                    pid = json.load(fh).get("projectId")
            except (OSError, ValueError, AttributeError) as e:
                return result("project", WARN, f"{p} cannot be read: {e}", "run `agenfk init` in the project root")
            if not isinstance(pid, str) or not pid.strip():
                return result("project", WARN, f"{p} has no projectId (found {json.dumps(pid)}): "
                              "agenfk cannot tell which project the cards belong to",
                              "run `agenfk init` in the project root")
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


def reader_name(node):
    """The SQUAD_* name when `node` is env_<kind>("SQUAD_X", ...), else None."""
    if not (isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str) and NAME.fullmatch(node.args[0].value)):
        return None
    f = node.func
    reader = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
    return node.args[0].value if reader.startswith("env_") else None


def clamped_name(node):
    """The SQUAD_* name a min()/max() chain wraps, e.g. min(max(env_int("SQUAD_X", 90), 10), 100)."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("min", "max")):
        return reader_name(node)
    for arg in node.args:
        name = clamped_name(arg)
        if name:
            return name
    return None


def code_bounds(tree):
    """name -> (low, high) for each env_* read the code clamps with min()/max() and constant bounds."""
    out = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("min", "max")):
            continue
        name = clamped_name(node)
        consts = [v for v in (literal(a) for a in node.args if isinstance(a, (ast.Constant, ast.UnaryOp)))
                  if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if not name or not consts:
            continue
        low, high = out.get(name, (None, None))
        if node.func.id == "max":
            low = max(consts) if low is None else max(low, max(consts))
        else:
            high = min(consts) if high is None else min(high, min(consts))
        out[name] = (low, high)
    return out


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
    code, doc, bounds = code_defaults(tree), documented_defaults(ast.get_docstring(tree)), code_bounds(tree)
    items = []
    for name in sorted(set(NAME.findall(text))):
        reader, default = code.get(name, (None, doc.get(name)))
        raw = environ.get(name)
        shown = label(name, default)
        if raw is None:
            items.append(item(name, OK, f"{shown} (default)", value=default, default=default, is_default=True,
                              effective=default))
            continue
        effective = raw
        status, detail = OK, (f"{shown} (set; same as the default)" if label(name, raw) == shown else
                              f"{label(name, raw)} (set; default {shown})")
        if reader in ("env_int", "env_float", "env_num"):
            try:
                effective, parsed = (float if reader != "env_int" else int)(raw), True
            except ValueError:
                effective, parsed = default, False
                status, detail = WARN, f"{raw!r} is not a number: the watcher uses the default {shown}"
            low, high = bounds.get(name, (None, None))
            if parsed and (
                    (low is not None and effective < low) or (high is not None and effective > high)):
                effective = min(max(effective, low if low is not None else effective),
                                high if high is not None else effective)
                status, detail = WARN, (f"{raw} (set; out of range {'' if low is None else low}.."
                                        f"{'' if high is None else high}: the watcher uses {label(name, effective)})")
        if raw == "0" and name in KILL_SWITCHES:      # exactly "0", as the watcher compares it
            status, detail = WARN, f"0 (set): {KILL_SWITCHES[name]}"
        items.append(item(name, status, detail, value=raw, default=default, is_default=False, effective=effective))
    return items


def label(name, value):
    """A value as the watcher reads it: 0 is "auto" for SQUAD_WINDOW, "off" for the absolute thresholds."""
    if value is None:
        return "unset"
    try:
        mode = SENTINELS.get(name, {}).get(int(str(value).strip()))
    except ValueError:
        mode = None
    return f"{value} ({mode})" if mode else str(value)


def check_thresholds(path=WATCH, environ=None):
    items = watcher_env(path, environ)
    set_here = sum(1 for i in items if not i["is_default"])
    status = worst(i["status"] for i in items)
    off = [i["name"] for i in items if i["status"] == WARN and i["name"] in KILL_SWITCHES]
    fixes = [f"unset {' and '.join(off)} unless you turned that off on purpose"] if off else []
    if any(i["status"] == WARN and i["name"] not in KILL_SWITCHES for i in items):
        fixes.insert(0, "set each WARN variable to a whole number in its range, or unset it")
    return result("thresholds", status,
                  f"{len(items)} SQUAD_* variables in {os.path.relpath(path, ROOT)}, {set_here} set in this shell "
                  "(the hook sees Claude Code's environment)",
                  "; ".join(fixes) or None, items)


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
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")      # a non-ASCII path on an ASCII terminal prints, with a ?
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
