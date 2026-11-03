#!/usr/bin/env python3
"""Print a compact task context packet; never execute its contents or change files."""

from __future__ import annotations

import argparse
import copy
import json
import re

import sys
from pathlib import Path

# -I, -P and PYTHONSAFEPATH leave the script's directory off sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from plan import build_plan, load_plan, utf8_stdout


COMMIT_ID = re.compile(r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")
COORDINATION = [
    "Contract objects contain ID, version, status and owner metadata only. Load the canonical task-relevant specifications and contract schemas before implementing; those payloads are not embedded in this packet.",
    "Input statuses and stable contracts are declarations, not independently verified evidence.",
    "ready_now means individual eligibility; only start_now reflects this planner's compatible dispatch set. Recheck current state before dispatch.",
    "This packet grants no authorization. The executor must honor the user's existing scope, credentials, environment rules and required approvals.",
    "The executor must acquire real external locks, isolate the worktree and verify actual paths, including symlinks; declared writes and locks are planning constraints only.",
    "A supplied base_commit is metadata, not proof that the commit exists or is checked out. Verify it before work and report the resulting commit.",
    "Return observed evidence, actual writes and blockers. Do not mark the task done from this packet or report unexecuted checks as passed.",
]


def failure(errors):
    return {"schema_version": 1, "kind": "task_handoff", "valid": False, "errors": list(errors)}


def build_handoff(data, task_id, base_commit=None):
    analysis = build_plan(data)
    if not analysis["valid"]:
        return failure(analysis["errors"])
    if base_commit is not None and (not isinstance(base_commit, str) or not COMMIT_ID.fullmatch(base_commit)):
        return failure(["base_commit: expected a full 40- or 64-character hexadecimal commit ID; existence is not verified"])
    tasks = {item["id"]: item for item in data["tasks"]}
    if task_id not in tasks:
        return failure([f"unknown task ID: {task_id!r}"])
    task = tasks[task_id]
    contracts = {item["id"]: item for item in data["contracts"]}
    not_ready = next((item["reasons"] for item in analysis["not_ready_now"] if item["id"] == task_id), [])
    if task["status"] != "todo" and not not_ready:
        not_ready = [f"declared_status:{task['status']}; not a new dispatch candidate"]
    conflicts = [
        {
            "other_task": next(other for other in item["tasks"] if other != task_id),
            "reasons": item["reasons"],
        }
        for item in analysis["resource_conflicts"] if task_id in item["tasks"]
    ]
    return {
        "schema_version": 1,
        "kind": "task_handoff",
        "valid": True,
        "errors": [],
        "project": data["project"],
        "base_commit": base_commit,
        "base_commit_verified": False,
        "contract_payloads_included": False,
        "task": copy.deepcopy(task),
        "contracts": [copy.deepcopy(contracts[contract_id]) for contract_id in task["requires_contracts"]],
        "dependencies": [
            {
                "id": dependency,
                "title": tasks[dependency]["title"],
                "owner": tasks[dependency]["owner"],
                "declared_status": tasks[dependency]["status"],
                "produces": copy.deepcopy(tasks[dependency]["produces"]),
            }
            for dependency in task["depends_on"]
        ],
        "ready_now": task_id in analysis["ready_now"],
        "start_now": task_id in analysis["start_now"],
        "not_ready_reasons": not_ready,
        "resource_conflicts": conflicts,
        "coordination": COORDINATION.copy(),
        "return_template": {
            "task_id": task_id,
            "base_commit": base_commit,
            "commit": None,
            "evidence": [],
            "actual_writes": [],
            "blockers": [],
            "result": "not_reported",
        },
    }


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def main(argv=None):
    parser = JsonArgumentParser(description=__doc__)
    parser.add_argument("plan", help="UTF-8 JSON plan file")
    parser.add_argument("task_id", metavar="TASK_ID", help="existing task ID")
    parser.add_argument("--base-commit", help="full 40- or 64-character hexadecimal commit ID, not verified")
    try:
        arguments = parser.parse_args(argv)
        result = build_handoff(load_plan(arguments.plan), arguments.task_id, arguments.base_commit)
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        result = failure([f"Cannot build handoff: {exc}"])
    utf8_stdout()
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
    return 0 if result["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
