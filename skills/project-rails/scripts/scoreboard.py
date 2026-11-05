#!/usr/bin/env python3
"""Project scoreboard: generated from committed files, with a ratchet.

Reads `scoreboard/metrics.toml` (numbers measured by a command, each with a floor or
a ceiling) and the other `scoreboard/*.toml` (checklists per area); writes
`scoreboard/measured.json` and `docs/scoreboard.md`. Limits move one way only:
a floor rises, a ceiling falls. Whoever loosens a limit to pass is caught by
`check --base`.

    scoreboard.py measure [--no-write] [id ...]  runs the commands and checks the limits
    scoreboard.py render                         rewrites docs/scoreboard.md
    scoreboard.py check [--base REF]             doc up to date, recorded values within
                                                 the limits and, with --base, no limit
                                                 loosened and no measurement swapped
                                                 since REF
    scoreboard.py tighten [id ...]               moves floor/ceiling up to the measured value

CI runs both: `check --base origin/main` (what was committed) and
`measure --no-write` (today's code still delivers the numbers; measured.json
alone may be stale or written by hand).

Exit 0 = ok, 1 = scoreboard failed, 2 = invalid configuration. Stdlib only
(Python 3.11+): copy this file into the project's repository and CI runs it
without installing anything. Metric commands run in a shell, at the root, with the
same trust as a Makefile: they come from a committed file.
"""
import argparse
import datetime
import glob
import json
import math
import os
import re
import subprocess
import sys

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11: say so, instead of a traceback on the import
    print(f"scoreboard: needs Python 3.11+ (tomllib); this is Python "
          f"{sys.version_info[0]}.{sys.version_info[1]}", file=sys.stderr)
    sys.exit(2)

STATUS = ("done", "partial", "missing")
# Every glued run that holds a digit ("1,820", "3.14.4", ".5", "1_000", "2024-01-05",
# "3.21s") is read whole: either it is a clean number, or it is ambiguous and becomes an
# error, never 820, 4, 5, 0, -5.
TOKEN = re.compile(r"[-+\w.,]*\d[-+\w.,]*")
CLEAN = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")
DOC = os.path.join("docs", "scoreboard.md")
METRICS = os.path.join("scoreboard", "metrics.toml")
MEASURED = os.path.join("scoreboard", "measured.json")


class ConfigError(Exception):
    """Invalid configuration: exit 2, no traceback."""


# ---------------------------------------------------------------- reading

def read_toml(path):
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: invalid TOML: {e}")
    except OSError as e:
        raise ConfigError(f"{path}: {e.strerror}")


def is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def validate_metrics(data, origin):
    entries = data.get("metric", [])
    if not isinstance(entries, list):
        raise ConfigError(f"{origin}: use [[metric]] for each metric")
    seen, result = set(), []
    for i, m in enumerate(entries, 1):
        if not isinstance(m, dict):
            raise ConfigError(f"{origin}: metric #{i} is not a table")
        mid = m.get("id")
        if not isinstance(mid, str) or not mid:
            raise ConfigError(f"{origin}: metric #{i} has no id")
        if mid in seen:
            raise ConfigError(f"{origin}: repeated id: {mid}")
        seen.add(mid)
        if ("floor" in m) == ("ceiling" in m):
            raise ConfigError(f"{origin}: {mid}: declare floor (higher is better) OR ceiling (lower is better)")
        direction = "floor" if "floor" in m else "ceiling"
        if not is_number(m[direction]):
            raise ConfigError(f"{origin}: {mid}: {direction} must be a number")
        command = m.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ConfigError(f"{origin}: {mid}: missing the command that measures it")
        regex = regex_txt = m.get("regex")
        if regex is not None:
            try:
                regex = re.compile(regex)
            except (re.error, TypeError) as e:
                raise ConfigError(f"{origin}: {mid}: invalid regex: {e}")
        timeout = m.get("timeout", 1800)
        if not is_number(timeout) or timeout <= 0:
            raise ConfigError(f"{origin}: {mid}: timeout must be a positive number")
        result.append({
            "id": mid,
            "name": str(m.get("name", mid)),
            "command": command,
            "regex": regex,
            "regex_txt": regex_txt,
            "direction": direction,
            "limit": m[direction],
            "timeout": timeout,
            "card": str(m.get("card", "")),
            "ignore_exit": m.get("ignore_exit") is True,
        })
    return result


def load_metrics(root):
    path = os.path.join(root, METRICS)
    if not os.path.exists(path):
        return []
    return validate_metrics(read_toml(path), path)


def load_checklists(root):
    areas = []
    for path in sorted(glob.glob(os.path.join(root, "scoreboard", "*.toml"))):
        if os.path.basename(path) == "metrics.toml":
            continue
        data = read_toml(path)
        area = data.get("area")
        if not isinstance(area, str) or not area:
            raise ConfigError(f"{path}: missing `area = \"...\"`")
        items = data.get("item", [])
        if not isinstance(items, list):
            raise ConfigError(f"{path}: use [[item]] for each item")
        seen = set()
        for i, it in enumerate(items, 1):
            if not isinstance(it, dict) or not isinstance(it.get("id"), str) or not it["id"]:
                raise ConfigError(f"{path}: item #{i} has no id")
            if it["id"] in seen:
                raise ConfigError(f"{path}: repeated id: {it['id']}")
            seen.add(it["id"])
            if it.get("status") not in STATUS:
                raise ConfigError(f"{path}: {it['id']}: status must be {', '.join(STATUS)}")
        areas.append({"area": area, "items": items})
    return areas


def load_measured(root):
    path = os.path.join(root, MEASURED)
    if not os.path.exists(path):
        return {"measured_at": "", "commit": "", "values": {}}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise ConfigError(f"{path}: unreadable: {e}")
    values = data.get("values") if isinstance(data, dict) else None
    if not isinstance(values, dict) or not all(is_number(v) for v in values.values()):
        raise ConfigError(f"{path}: `values` must map id -> number")
    return {"measured_at": str(data.get("measured_at", "")),
            "commit": str(data.get("commit", "")), "values": values}


# ---------------------------------------------------------------- rules

def within(m, v):
    return v >= m["limit"] if m["direction"] == "floor" else v <= m["limit"]


def fmt(v):
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v)


def limit_txt(m):
    return ("\u2265 " if m["direction"] == "floor" else "\u2264 ") + fmt(m["limit"])


def measure_one(m, root):
    """(value, None) or (None, reason). Never raises."""
    try:
        r = subprocess.run(m["command"], shell=True, cwd=root, capture_output=True,
                           text=True, timeout=m["timeout"])
    except subprocess.TimeoutExpired:
        return None, f"took longer than {fmt(m['timeout'])} s"
    except OSError as e:
        return None, f"did not run: {e}"
    if r.returncode != 0 and not m["ignore_exit"]:
        tail = (r.stderr or r.stdout).strip().splitlines()[-1:] or [""]
        return None, f"exited with {r.returncode}: {tail[0][:160]}"
    if m["regex"] is not None:
        match = m["regex"].search(r.stdout)
        text = (match.group(1) if match and match.groups() else
                match.group(0) if match else None)
        if text is None:
            return None, "the regex did not match the output"
    else:
        text = r.stdout
    tokens = TOKEN.findall(text)
    if not tokens:
        return None, "no number in the output"
    raw = tokens[-1].rstrip(".,")  # a full stop ending a sentence is not a decimal point
    if not CLEAN.fullmatch(raw):
        return None, f"ambiguous number in the output: {tokens[-1]} (use a regex that captures only the number)"
    v = float(raw)
    if not math.isfinite(v):
        return None, f"non-finite number in the output: {raw}"
    return (int(v) if v.is_integer() else v), None


def current_commit(root):
    try:
        r = subprocess.run(["git", "-C", root, "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


# ---------------------------------------------------------------- doc

def cell(v):
    return str(v).replace("|", "\\|").replace("\n", " ").strip()


def render_doc(metrics, areas, measured):
    values = measured["values"]
    lines = [
        "# Scoreboard",
        "",
        "Generated by `scoreboard.py` from `scoreboard/`. Do not edit by hand: change the sources",
        "and run `scoreboard.py render` (CI runs `scoreboard.py check`). The numbers here outweigh",
        "any estimate in another document.",
        "",
    ]
    if measured["measured_at"]:
        sha = f" on commit `{measured['commit']}`" if measured["commit"] else ""
        lines += [f"Measured at {measured['measured_at']}{sha}.", ""]
    else:
        lines += ["No measurement yet: run `scoreboard.py measure`.", ""]

    ok = sum(1 for m in metrics if m["id"] in values and within(m, values[m["id"]]))
    unmeasured = sum(1 for m in metrics if m["id"] not in values)
    lines += ["## Summary", "", "| Area | Done | Partial | Missing |", "|---|---:|---:|---:|"]
    if metrics:
        lines.append(f"| [Metrics](#metrics) | {ok} within the limit | "
                     f"{len(metrics) - ok - unmeasured} outside | {unmeasured} not measured |")
    for a in areas:
        n = {s: sum(1 for it in a["items"] if it["status"] == s) for s in STATUS}
        lines.append(f"| {cell(a['area'])} | {n['done']} | {n['partial']} | {n['missing']} |")
    lines.append("")

    if metrics:
        lines += ["## Metrics", "",
                  "| Id | Metric | Value | Limit | Status | Card |",
                  "|---|---|---:|---|---|---|"]
        for m in metrics:
            v = values.get(m["id"])
            if v is None:
                value, state = "\u2013", "**not measured**"
            else:
                value, state = fmt(v), "ok" if within(m, v) else "**outside the limit**"
            lines.append(f"| {cell(m['id'])} | {cell(m['name'])} | {value} | "
                         f"{limit_txt(m)} | {state} | {cell(m['card'])} |")
        lines.append("")

    for a in areas:
        n = {s: sum(1 for it in a["items"] if it["status"] == s) for s in STATUS}
        lines += [f"## {cell(a['area'])}", "",
                  f"{n['done']} done, {n['partial']} partial, {n['missing']} missing.", "",
                  "| Id | Target | Status | Evidence | Card |", "|---|---|---|---|---|"]
        for it in a["items"]:
            lines.append(f"| {cell(it['id'])} | {cell(it.get('target', ''))} | "
                         f"{it['status']} | {cell(it.get('note', ''))} | "
                         f"{cell(it.get('card', ''))} |")
        lines.append("")
    return "\n".join(lines)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def rewrite_doc(root, metrics=None):
    metrics = load_metrics(root) if metrics is None else metrics
    write(os.path.join(root, DOC),
          render_doc(metrics, load_checklists(root), load_measured(root)))


# ---------------------------------------------------------------- commands

def select(metrics, ids):
    if not ids:
        return metrics
    known = {m["id"] for m in metrics}
    unknown = [i for i in ids if i not in known]
    if unknown:
        raise ConfigError(f"unknown metric: {', '.join(unknown)}")
    return [m for m in metrics if m["id"] in ids]


def cmd_measure(root, ids, no_write):
    metrics = load_metrics(root)
    load_checklists(root)  # validate before spending time measuring
    targets = select(metrics, ids)
    if not targets:
        print("scoreboard: no metric in scoreboard/metrics.toml")
        return 0
    measured = load_measured(root)
    failed = False
    for m in targets:
        v, reason = measure_one(m, root)
        if v is None:
            failed = True
            print(f"ERROR  {m['id']}: {reason}")
            continue
        measured["values"][m["id"]] = v
        if within(m, v):
            print(f"ok     {m['id']}: {fmt(v)} ({limit_txt(m)})")
        else:
            failed = True
            print(f"OUT    {m['id']}: {fmt(v)} outside {limit_txt(m)}")
    if not no_write:
        measured["measured_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        measured["commit"] = current_commit(root)
        write(os.path.join(root, MEASURED),
              json.dumps(measured, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
        rewrite_doc(root, metrics)
    return 1 if failed else 0


def cmd_render(root):
    rewrite_doc(root)
    print(f"scoreboard: {DOC} regenerated")
    return 0


def limits_at_base(root, base):
    r = subprocess.run(["git", "-C", root, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise ConfigError(f"unknown base: {base}")
    r = subprocess.run(["git", "-C", root, "show", f"{base}:./{METRICS}"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return None  # the base had no scoreboard yet: nothing to compare
    try:
        data = tomllib.loads(r.stdout)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{base}:{METRICS}: invalid TOML: {e}")
    return validate_metrics(data, f"{base}:{METRICS}")


HOW_IT_MEASURES = ("command", "regex_txt", "ignore_exit")


def cmd_check(root, base, accept_measurement=()):
    metrics = load_metrics(root)
    measured = load_measured(root)
    problems = []
    for m in metrics:
        v = measured["values"].get(m["id"])
        if v is None:
            problems.append(f"{m['id']}: not measured (run scoreboard.py measure {m['id']})")
        elif not within(m, v):
            problems.append(f"{m['id']}: {fmt(v)} outside {limit_txt(m)}")
    expected = render_doc(metrics, load_checklists(root), measured)
    try:
        with open(os.path.join(root, DOC), encoding="utf-8") as f:
            current = f.read()
    except OSError:
        current = None
    if current is None:
        problems.append(f"{DOC} does not exist (run scoreboard.py render)")
    elif current != expected:
        problems.append(f"{DOC} out of date or edited by hand (run scoreboard.py render)")
    for mid in sorted(set(accept_measurement) - {m["id"] for m in metrics}):
        print(f"warning --accept-measurement {mid}: no metric with that id")
    if base:
        before = limits_at_base(root, base)
        if before is None:
            print(f"scoreboard: {base} had no {METRICS}; nothing to compare")
        else:
            now = {m["id"]: m for m in metrics}
            for b in before:
                a = now.get(b["id"])
                if a is None:
                    problems.append(f"{b['id']}: metric removed since {base}")
                elif a["direction"] != b["direction"]:
                    problems.append(f"{b['id']}: switched {b['direction']} to {a['direction']} since {base}")
                elif a["direction"] == "floor" and a["limit"] < b["limit"]:
                    problems.append(f"{b['id']}: floor dropped from {fmt(b['limit'])} to {fmt(a['limit'])} since {base}")
                elif a["direction"] == "ceiling" and a["limit"] > b["limit"]:
                    problems.append(f"{b['id']}: ceiling rose from {fmt(b['limit'])} to {fmt(a['limit'])} since {base}")
                if a is not None and b["id"] not in accept_measurement:
                    # the same limit with another yardstick also loosens it: `echo 999` passes any floor
                    changed = [c.replace("_txt", "") for c in HOW_IT_MEASURES if a[c] != b[c]]
                    if changed:
                        problems.append(f"{b['id']}: changed how it measures ({', '.join(changed)}) since {base}; "
                                        f"if the new measurement is honest, the owner approves it with "
                                        f"--accept-measurement {b['id']}")
    for p in problems:
        print(f"FAIL   {p}")
    if not problems:
        print(f"scoreboard: ok ({len(metrics)} metrics)")
    return 1 if problems else 0


BLOCK = re.compile(r"^\s*\[\[\s*metric\s*\]\]\s*(#.*)?$")
ID = re.compile(r"""^\s*(["']?)id\1\s*=\s*["']([^"']*)["']""")
LIMIT = re.compile(r"""^(\s*(["']?)(?:floor|ceiling)\2\s*=\s*)([-+0-9.eE_]+)(.*)$""")


def cmd_tighten(root, ids):
    metrics = select(load_metrics(root), ids)
    values = load_measured(root)["values"]
    new_limits, notices = {}, []
    for m in metrics:
        v = values.get(m["id"])
        if v is None:
            print(f"skipped {m['id']}: not measured")
            continue
        new = max(m["limit"], v) if m["direction"] == "floor" else min(m["limit"], v)
        if new != m["limit"]:
            new_limits[m["id"]] = new
            notices.append(f"{m['direction']} of {m['id']}: {fmt(m['limit'])} -> {fmt(new)}")
    if not new_limits:
        print("scoreboard: no limit to tighten")
        return 0
    path = os.path.join(root, METRICS)
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines(keepends=True)
    blocks, current = [], None
    for i, ln in enumerate(lines):
        if BLOCK.match(ln):
            current = {"id": None, "lines": []}
            blocks.append(current)
        if current is not None:
            current["lines"].append(i)
            found = ID.match(ln)
            if found and current["id"] is None:
                current["id"] = found.group(2)
    for b in blocks:
        if b["id"] not in new_limits:
            continue
        for i in b["lines"]:
            found = LIMIT.match(lines[i])
            if found:
                end = "\n" if lines[i].endswith("\n") else ""
                lines[i] = (found.group(1) + fmt(new_limits[b["id"]])
                            + found.group(4).rstrip("\n") + end)
                break
    text = "".join(lines)
    try:
        after = {m["id"]: m["limit"] for m in validate_metrics(tomllib.loads(text), path)}
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: the rewrite would break the TOML ({e}); nothing written")
    # the rewrite works line by line: an inline table or an odd layout does not match, and
    # that must not turn into "tightened" without having tightened anything
    missed = sorted(mid for mid, new in new_limits.items() if after.get(mid) != new)
    if missed:
        raise ConfigError(f"{path}: could not rewrite the limit of {', '.join(missed)} "
                          f"(use [[metric]] with `floor = N` / `ceiling = N` on a line of its own); "
                          f"nothing written")
    write(path, text)
    rewrite_doc(root)
    for notice in notices:  # only after writing: before that it would be a promise
        print(notice)
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="scoreboard.py", description=__doc__.split("\n\n")[0])
    p.add_argument("--root", default=".", help="project root (where scoreboard/ lives)")
    sub = p.add_subparsers(dest="cmd", required=True)
    pm = sub.add_parser("measure", help="runs the metric commands")
    pm.add_argument("ids", nargs="*")
    pm.add_argument("--no-write", action="store_true",
                    help="only checks the limits (CI); writes neither measured.json nor the doc")
    sub.add_parser("render", help="rewrites docs/scoreboard.md")
    pc = sub.add_parser("check", help="doc up to date, values within the limits, no limit loosened")
    pc.add_argument("--base", help="git ref to compare the limits against (e.g. origin/main)")
    pc.add_argument("--accept-measurement", nargs="+", default=[], metavar="ID",
                    help="metrics whose way of measuring changed with the owner's approval "
                         "(does not allow a loosened limit)")
    pt = sub.add_parser("tighten", help="moves floor/ceiling up to the measured value; never loosens")
    pt.add_argument("ids", nargs="*")
    a = p.parse_args(argv)
    root = os.path.abspath(a.root)
    try:
        if a.cmd == "measure":
            return cmd_measure(root, a.ids, a.no_write)
        if a.cmd == "render":
            return cmd_render(root)
        if a.cmd == "check":
            return cmd_check(root, a.base, set(a.accept_measurement))
        return cmd_tighten(root, a.ids)
    except ConfigError as e:
        print(f"scoreboard: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
