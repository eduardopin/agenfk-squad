#!/usr/bin/env python3
"""Validate and schedule a declarative task graph. Python 3 standard library only.

This program reads JSON and emits a plan; it never runs task instructions or
changes repositories, infrastructure, contract states, or the input file.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import re
import sys
import unicodedata
from collections import deque
from pathlib import Path


IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
TASK_FIELDS = {
    "id", "title", "owner", "status", "depends_on", "writes", "locks",
    "requires_contracts", "produces", "acceptance", "estimate_minutes",
}
CONTRACT_FIELDS = {"id", "version", "status", "owner"}
ASSUMPTIONS = [
    "Durations are user estimates, not measurements or delivery promises.",
    "For in_progress tasks, estimate_minutes means remaining time from now.",
    "done states and stable contracts are declared inputs; their evidence must be verified separately.",
    "Draft contracts and blocked tasks never become ready automatically in this simulation.",
    "Each owner runs at most one task; max_workers also limits all concurrent tasks.",
    "Writes and locks must declare every shared resource, including IaC state, migrations, and environments.",
    "ready_now lists individually eligible tasks; start_now is a mutually compatible dispatch set.",
    "The schedule is a deterministic critical-tail-first heuristic, not a proven optimal schedule.",
    "critical_path_minutes ignores resources and external waiting; it is a dependency-only lower bound.",
    "A null estimated_makespan_minutes means a complete schedule cannot be constructed from the current state.",
]


def empty_result():
    return {
        "schema_version": 1,
        "project": None,
        "valid": False,
        "errors": [],
        "warnings": [],
        "ready_now": [],
        "start_now": [],
        "not_ready_now": [],
        "resource_conflicts": [],
        "schedule": [],
        "unscheduled": [],
        "complete_schedule": False,
        "critical_path_minutes": None,
        "estimated_makespan_minutes": None,
        "scheduled_horizon_minutes": 0,
        "assumptions": ASSUMPTIONS.copy(),
    }


# Control (C0/C1), lone surrogates, and line/paragraph separators break printing and table rows.
UNSAFE_CATEGORIES = {"Cc", "Cs", "Zl", "Zp"}


def is_text(value):
    return (
        isinstance(value, str)
        and bool(value.strip())
        and not any(unicodedata.category(char) in UNSAFE_CATEGORIES for char in value)
    )


def validate_text(value, field, errors, identifier=False):
    if not is_text(value):
        errors.append(f"{field}: expected a nonempty string without control characters")
        return False
    if identifier and not IDENTIFIER.fullmatch(value):
        errors.append(f"{field}: use 1-128 letters, digits, periods, underscores or hyphens; start with a letter or digit")
        return False
    return True


def validate_keys(value, expected, field, errors, optional=()):
    if not isinstance(value, dict):
        errors.append(f"{field}: expected an object")
        return False
    for key in sorted(expected - value.keys()):
        errors.append(f"{field}.{key}: required field is missing")
    for key in sorted(value.keys() - expected - set(optional), key=str):
        errors.append(f"{field}.{key}: unknown field; check for a typo")
    return True


def validate_strings(value, field, errors, nonempty=False, identifiers=False):
    if not isinstance(value, list):
        errors.append(f"{field}: expected an array of strings")
        return False
    if nonempty and not value:
        errors.append(f"{field}: must contain at least one item")
    valid = True
    seen = set()
    for index, item in enumerate(value):
        if not validate_text(item, f"{field}[{index}]", errors, identifiers):
            valid = False
        elif item in seen:
            errors.append(f"{field}[{index}]: duplicate value {item!r}")
            valid = False
        else:
            seen.add(item)
    return valid


def path_error(value):
    """Allow relative POSIX paths and the single recursive suffix '/**'."""
    if not is_text(value):
        return "expected a nonempty relative path without control characters"
    if value.startswith(("/", "~")) or "\\" in value or ":" in value:
        return "use a repository-relative POSIX path; absolute paths, drives and backslashes are forbidden"
    base = value[:-3] if value.endswith("/**") else value
    if any(char in base for char in "*?[]{}"):
        return "only literal paths and the recursive suffix '/**' are supported"
    parts = base.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return "empty, '.' and '..' path components are forbidden"
    return None


def path_overlap(left, right):
    """Conservative: replacing an ancestor target also conflicts with children."""
    a = left[:-3] if left.endswith("/**") else left
    b = right[:-3] if right.endswith("/**") else right
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def conflict_reasons(left, right):
    reasons = []
    if left["owner"] == right["owner"]:
        reasons.append(f"owner:{left['owner']}")
    for lock in sorted(set(left["locks"]) & set(right["locks"])):
        reasons.append(f"lock:{lock}")
    for a, b in itertools.product(left["writes"], right["writes"]):
        if path_overlap(a, b):
            reasons.append(f"writes:{a} <> {b}")
    return reasons


def topological_order(tasks):
    children = {task_id: [] for task_id in tasks}
    indegree = {task_id: len(task["depends_on"]) for task_id, task in tasks.items()}
    for task_id, task in tasks.items():
        for parent in task["depends_on"]:
            children[parent].append(task_id)
    ready = deque(sorted(task_id for task_id, degree in indegree.items() if degree == 0))
    order = []
    while ready:
        current = ready.popleft()
        order.append(current)
        for child in sorted(children[current]):
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
    return order, children, sorted(task_id for task_id, degree in indegree.items() if degree)


def validate(data, result):
    errors = result["errors"]
    if not validate_keys(data, {"project", "max_workers", "contracts", "tasks"}, "plan", errors, {"schema_version"}):
        return None
    if "schema_version" in data and (type(data["schema_version"]) is not int or data["schema_version"] != 1):
        errors.append("plan.schema_version: only integer version 1 is supported")
    if validate_text(data.get("project"), "plan.project", errors):
        result["project"] = data["project"]
    if type(data.get("max_workers")) is not int or data["max_workers"] < 1:
        errors.append("plan.max_workers: expected an integer >= 1")
    contracts = {}
    contract_list = data.get("contracts")
    if not isinstance(contract_list, list):
        errors.append("plan.contracts: expected an array")
        contract_list = []
    for index, contract in enumerate(contract_list):
        location = f"plan.contracts[{index}]"
        if not validate_keys(contract, CONTRACT_FIELDS, location, errors):
            continue
        valid_id = validate_text(contract.get("id"), f"{location}.id", errors, True)
        for key in ("version", "owner"):
            validate_text(contract.get(key), f"{location}.{key}", errors)
        if contract.get("status") not in ("draft", "stable"):
            errors.append(f"{location}.status: expected 'draft' or 'stable'")
        if valid_id:
            if contract["id"] in contracts:
                errors.append(f"{location}.id: duplicate contract ID {contract['id']!r}")
            else:
                contracts[contract["id"]] = contract
    tasks = {}
    task_list = data.get("tasks")
    if not isinstance(task_list, list) or not task_list:
        errors.append("plan.tasks: expected a nonempty array")
        task_list = []
    for index, task in enumerate(task_list):
        location = f"plan.tasks[{index}]"
        if not validate_keys(task, TASK_FIELDS, location, errors):
            continue
        valid_id = validate_text(task.get("id"), f"{location}.id", errors, True)
        for key in ("title", "owner"):
            validate_text(task.get(key), f"{location}.{key}", errors)
        if task.get("status") not in ("todo", "in_progress", "done", "blocked"):
            errors.append(f"{location}.status: invalid status")
        for key in ("depends_on", "requires_contracts"):
            validate_strings(task.get(key), f"{location}.{key}", errors, identifiers=True)
        for key in ("locks", "writes"):
            validate_strings(task.get(key), f"{location}.{key}", errors)
        for key in ("produces", "acceptance"):
            validate_strings(task.get(key), f"{location}.{key}", errors, nonempty=True)
        if isinstance(task.get("writes"), list):
            for position, path in enumerate(task["writes"]):
                problem = path_error(path)
                if problem:
                    errors.append(f"{location}.writes[{position}]: {problem}")
        duration = task.get("estimate_minutes")
        try:
            valid_duration = type(duration) in (int, float) and math.isfinite(duration) and duration > 0
        except (OverflowError, TypeError, ValueError):
            valid_duration = False
        if not valid_duration:
            errors.append(f"{location}.estimate_minutes: expected a finite positive number")
        if valid_id:
            if task["id"] in tasks:
                errors.append(f"{location}.id: duplicate task ID {task['id']!r}")
            else:
                tasks[task["id"]] = task
    if errors:
        return None
    for task_id, task in tasks.items():
        for dependency in task["depends_on"]:
            if dependency not in tasks:
                errors.append(f"task {task_id}: unknown dependency {dependency!r}")
            elif dependency == task_id:
                errors.append(f"task {task_id}: cannot depend on itself")
        for contract in task["requires_contracts"]:
            if contract not in contracts:
                errors.append(f"task {task_id}: unknown contract {contract!r}")
    if errors:
        return None
    order, children, cyclic = topological_order(tasks)
    if cyclic:
        errors.append("dependency cycle detected; unresolved tasks: " + ", ".join(cyclic))
        return None
    try:
        total_duration = sum(float(t["estimate_minutes"]) for t in tasks.values())
        if not math.isfinite(total_duration):
            errors.append("plan.tasks: total duration exceeds finite numeric range")
    except (OverflowError, ValueError):
        errors.append("plan.tasks: total duration exceeds finite numeric range")
    active = [task for task in tasks.values() if task["status"] == "in_progress"]
    if len(active) > data["max_workers"]:
        errors.append("in_progress tasks exceed max_workers")
    for task_id, task in tasks.items():
        if task["status"] in ("in_progress", "done"):
            unfinished = [parent for parent in task["depends_on"] if tasks[parent]["status"] != "done"]
            if unfinished:
                errors.append(f"task {task_id} is {task['status']} but prerequisites are not done: {', '.join(unfinished)}")
        if task["status"] == "in_progress":
            draft = [cid for cid in task["requires_contracts"] if contracts[cid]["status"] != "stable"]
            if draft:
                errors.append(f"active task {task_id} requires draft contracts: {', '.join(draft)}")
    for left, right in itertools.combinations(active, 2):
        reasons = conflict_reasons(left, right)
        if reasons:
            errors.append(f"active tasks {left['id']} and {right['id']} conflict: {'; '.join(reasons)}")
    if errors:
        return None
    return tasks, contracts, order, children


def blockers(task, completed, contracts, active, max_workers):
    reasons = []
    if task["status"] == "blocked":
        reasons.append("status:blocked; requires an explicit state update")
    dependencies = sorted(set(task["depends_on"]) - completed)
    if dependencies:
        reasons.append("dependencies:" + ",".join(dependencies))
    draft = [cid for cid in task["requires_contracts"] if contracts[cid]["status"] != "stable"]
    if draft:
        reasons.append("draft_contracts:" + ",".join(sorted(draft)))
    if len(active) >= max_workers:
        reasons.append("worker_capacity")
    for other in active:
        collisions = conflict_reasons(task, other)
        if collisions:
            reasons.append(f"occupied_by:{other['id']} ({'; '.join(collisions)})")
    return reasons


def build_plan(data):
    result = empty_result()
    validated = validate(data, result)
    if validated is None:
        return result
    tasks, contracts, order, children = validated
    result["valid"] = True
    completed = {task_id for task_id, task in tasks.items() if task["status"] == "done"}
    active = sorted((task for task in tasks.values() if task["status"] == "in_progress"), key=lambda task: task["id"])

    ancestors = {}
    earliest_finish = {}
    for task_id in order:
        task = tasks[task_id]
        ancestors[task_id] = set(task["depends_on"])
        for dependency in task["depends_on"]:
            ancestors[task_id].update(ancestors[dependency])
        duration = 0 if task["status"] == "done" else task["estimate_minutes"]
        earliest_finish[task_id] = max((earliest_finish[parent] for parent in task["depends_on"]), default=0) + duration
    result["critical_path_minutes"] = max(earliest_finish.values(), default=0)
    tail = {}
    for task_id in reversed(order):
        duration = 0 if tasks[task_id]["status"] == "done" else tasks[task_id]["estimate_minutes"]
        tail[task_id] = duration + max((tail[child] for child in children[task_id]), default=0)

    unfinished = sorted(task_id for task_id, task in tasks.items() if task["status"] != "done")
    for left_id, right_id in itertools.combinations(unfinished, 2):
        if left_id in ancestors[right_id] or right_id in ancestors[left_id]:
            continue
        reasons = conflict_reasons(tasks[left_id], tasks[right_id])
        if reasons:
            result["resource_conflicts"].append({"tasks": [left_id, right_id], "reasons": reasons})
            result["warnings"].append(f"Independent tasks {left_id} and {right_id} must be serialized: {'; '.join(reasons)}")

    for task_id in unfinished:
        task = tasks[task_id]
        if task["status"] == "in_progress":
            continue
        reasons = blockers(task, completed, contracts, active, data["max_workers"])
        if not reasons and task["status"] == "todo":
            result["ready_now"].append(task_id)
        else:
            result["not_ready_now"].append({"id": task_id, "reasons": reasons})

    schedule = []
    running = {}
    for task in active:
        finish = task["estimate_minutes"]
        running[task["id"]] = finish
        schedule.append(schedule_entry(task, 0, finish))
    pending = {task_id for task_id, task in tasks.items() if task["status"] == "todo"}
    now = 0
    # Every pass of the outer loop must finish at least one task, so the loop cannot
    # run more than once per task; a ceiling turns a scheduler bug into an error
    # instead of a hang.
    passes_left = len(tasks) + 1
    while pending or running:
        passes_left -= 1
        if passes_left < 0:
            raise RuntimeError("scheduler made no progress; this is a bug in plan.py, not in the plan")
        while len(running) < data["max_workers"]:
            active_tasks = [tasks[task_id] for task_id in running]
            eligible = [
                task_id for task_id in pending
                if not blockers(tasks[task_id], completed, contracts, active_tasks, data["max_workers"])
            ]
            if not eligible:
                break
            task_id = min(eligible, key=lambda candidate: (-tail[candidate], candidate))
            task = tasks[task_id]
            finish = now + task["estimate_minutes"]
            running[task_id] = finish
            pending.remove(task_id)
            schedule.append(schedule_entry(task, now, finish))
            if now == 0:
                result["start_now"].append(task_id)
        if not running:
            break
        now = min(running.values())
        finished = sorted(task_id for task_id, finish in running.items() if finish <= now)
        for task_id in finished:
            del running[task_id]
            completed.add(task_id)

    result["schedule"] = sorted(schedule, key=lambda entry: (entry["start_minute"], entry["id"]))
    remaining = sorted(set(tasks) - completed)
    for task_id in remaining:
        reasons = blockers(tasks[task_id], completed, contracts, [], data["max_workers"])
        result["unscheduled"].append({"id": task_id, "reasons": reasons})
    result["scheduled_horizon_minutes"] = max((entry["end_minute"] for entry in schedule), default=0)
    result["complete_schedule"] = not remaining
    if result["complete_schedule"]:
        result["estimated_makespan_minutes"] = result["scheduled_horizon_minutes"]
    else:
        result["warnings"].append("This is a partial schedule. Resolve blocked tasks or draft contracts and generate the plan again.")
    return result


def schedule_entry(task, start, finish):
    return {
        "id": task["id"], "title": task["title"], "owner": task["owner"],
        "initial_status": task["status"], "start_minute": start,
        "end_minute": finish, "estimated_minutes": task["estimate_minutes"],
    }


def reject_duplicate_keys(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key!r}")
        value[key] = item
    return value


def reject_nonfinite(value):
    raise ValueError(f"nonfinite JSON number is not allowed: {value}")


def load_plan(path):
    return json.loads(
        Path(path).read_text(encoding="utf-8"),
        object_pairs_hook=reject_duplicate_keys,
        parse_constant=reject_nonfinite,
    )


def render_text(result):
    lines = [f"Project: {result['project'] or '(invalid input)'}", f"Valid: {result['valid']}"]
    for problem in result["errors"]:
        lines.append("ERROR: " + problem)
    for warning in result["warnings"]:
        lines.append("WARNING: " + warning)
    if result["valid"]:
        lines.append("Individually ready now: " + (", ".join(result["ready_now"]) or "none"))
        lines.append("Compatible dispatch now: " + (", ".join(result["start_now"]) or "none"))
        lines.append(f"Dependency lower bound: {result['critical_path_minutes']} estimated minutes")
        lines.append(f"Complete schedule: {result['complete_schedule']}")
        lines.append(f"Heuristic makespan: {result['estimated_makespan_minutes']} estimated minutes")
        for entry in result["schedule"]:
            lines.append(f"  {entry['start_minute']:.10g}-{entry['end_minute']:.10g} min | {entry['id']} | {entry['owner']} | {entry['title']}")
        for entry in result["unscheduled"]:
            lines.append(f"UNSCHEDULED {entry['id']}: {'; '.join(entry['reasons'])}")
    lines.append("Assumptions:")
    lines.extend("- " + item for item in result["assumptions"])
    return "\n".join(lines)


def utf8_stdout():
    # A cp1252 console or redirect cannot encode the arrows and accents in the reports.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", help="UTF-8 JSON plan file")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    arguments = parser.parse_args(argv)
    utf8_stdout()
    try:
        result = build_plan(load_plan(arguments.plan))
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        result = empty_result()
        result["errors"].append(f"Cannot read valid plan JSON: {exc}")
    if arguments.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    else:
        print(render_text(result))
    return 2 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
