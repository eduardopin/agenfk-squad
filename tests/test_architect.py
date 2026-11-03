#!/usr/bin/env python3
"""Tests for the architect skill: scripts, package layout and public-repo hygiene.

Standard library only. Run: python3 tests/test_architect.py
"""

import copy
import io
import os
import json
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "architect"
SCRIPTS = SKILL / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"  # subprocesses inherit it

from plan import build_plan, path_overlap, render_text, utf8_stdout  # noqa: E402
from delivery_map import build_delivery_map, render_markdown  # noqa: E402
from handoff import build_handoff  # noqa: E402


def task(task_id, **changes):
    value = {
        "id": task_id,
        "title": f"Deliver {task_id}",
        "owner": f"owner-{task_id}",
        "status": "todo",
        "depends_on": [],
        "writes": [f"components/{task_id}/**"],
        "locks": [],
        "requires_contracts": [],
        "produces": [f"artifact-{task_id}"],
        "acceptance": [f"Independent acceptance evidence for {task_id}"],
        "estimate_minutes": 10,
    }
    value.update(changes)
    return value


def plan(*tasks, **changes):
    value = {"schema_version": 1, "project": "Planner test", "max_workers": 3, "contracts": [], "tasks": list(tasks)}
    value.update(changes)
    return value


def intervals(result):
    return {item["id"]: (item["start_minute"], item["end_minute"]) for item in result["schedule"]}


class PlannerBehavior(unittest.TestCase):
    def test_independent_work_is_parallel(self):
        result = build_plan(plan(task("API"), task("INFRA"), task("WEB")))
        self.assertTrue(result["valid"])
        self.assertEqual(set(result["start_now"]), {"API", "INFRA", "WEB"})
        self.assertEqual(result["estimated_makespan_minutes"], 10)
        self.assertEqual(result["critical_path_minutes"], 10)

    def test_event_schedule_has_no_global_wave_barrier(self):
        result = build_plan(plan(task("A", estimate_minutes=2), task("B", estimate_minutes=10), task("C", depends_on=["A"], estimate_minutes=3), max_workers=2))
        self.assertEqual(intervals(result)["C"], (2, 5))
        self.assertEqual(result["estimated_makespan_minutes"], 10)

    def test_cycle_is_invalid(self):
        result = build_plan(plan(task("A", depends_on=["B"]), task("B", depends_on=["A"])))
        self.assertFalse(result["valid"])
        self.assertTrue(any("cycle" in error for error in result["errors"]))
        self.assertEqual(result["schedule"], [])

    def test_unknown_dependency_and_contract_are_invalid(self):
        result = build_plan(plan(task("A", depends_on=["MISSING"], requires_contracts=["NOPE"])))
        self.assertEqual(len(result["errors"]), 2)

    def test_write_prefix_conflict_is_reported_and_serialized(self):
        result = build_plan(plan(task("A", writes=["infra/**"]), task("B", writes=["infra/modules/api.tf"])))
        self.assertTrue(result["resource_conflicts"])
        self.assertEqual(result["estimated_makespan_minutes"], 20)
        self.assertEqual(result["ready_now"], ["A", "B"])
        self.assertEqual(result["start_now"], ["A"])

    def test_adjacent_names_do_not_conflict(self):
        self.assertFalse(path_overlap("src/api/**", "src/api-v2/**"))
        self.assertFalse(path_overlap("src/api.py", "src/api.pyi"))
        self.assertTrue(path_overlap("src/api", "src/api/routes.py"))

    def test_shared_owner_and_lock_serialize_even_without_file_overlap(self):
        for modifications in ({"owner": "platform"}, {"locks": ["terraform:prod"]}):
            with self.subTest(modifications=modifications):
                result = build_plan(plan(task("A", **modifications), task("B", **modifications)))
                self.assertEqual(result["estimated_makespan_minutes"], 20)

    def test_worker_capacity_is_respected(self):
        result = build_plan(plan(task("A"), task("B"), task("C"), max_workers=1))
        self.assertEqual(result["estimated_makespan_minutes"], 30)
        self.assertEqual(len(result["start_now"]), 1)

    def test_done_dependency_does_not_make_a_draft_contract_ready(self):
        contract = {"id": "C1", "version": "1.0.0", "owner": "architect", "status": "draft"}
        result = build_plan(plan(task("A", status="done"), task("B", depends_on=["A"], requires_contracts=["C1"]), contracts=[contract]))
        self.assertEqual(result["ready_now"], [])
        self.assertFalse(result["complete_schedule"])
        self.assertIsNone(result["estimated_makespan_minutes"])
        self.assertIn("draft_contracts:C1", result["unscheduled"][0]["reasons"])

    def test_blocked_task_and_its_dependents_remain_unscheduled(self):
        result = build_plan(plan(task("A", status="blocked"), task("B", depends_on=["A"]), task("C")))
        self.assertEqual([item["id"] for item in result["unscheduled"]], ["A", "B"])
        self.assertEqual(result["scheduled_horizon_minutes"], 10)
        self.assertIsNone(result["estimated_makespan_minutes"])

    def test_in_progress_resources_are_reserved_then_released(self):
        result = build_plan(plan(task("A", status="in_progress", estimate_minutes=3, locks=["db"]), task("B", locks=["db"]), task("C")))
        self.assertEqual(result["ready_now"], ["C"])
        self.assertEqual(intervals(result)["B"], (3, 13))
        self.assertEqual(intervals(result)["C"], (0, 10))

    def test_in_progress_at_capacity_does_not_dispatch_new_work(self):
        result = build_plan(plan(task("A", status="in_progress"), task("B"), max_workers=1))
        self.assertEqual(result["ready_now"], [])
        self.assertEqual(result["start_now"], [])
        self.assertEqual(intervals(result)["B"], (10, 20))

    def test_inconsistent_active_states_are_errors(self):
        cases = [
            plan(task("A", status="in_progress"), task("B", status="in_progress"), max_workers=1),
            plan(task("A", status="in_progress", owner="same"), task("B", status="in_progress", owner="same")),
            plan(task("A"), task("B", status="in_progress", depends_on=["A"])),
            plan(task("A"), task("B", status="done", depends_on=["A"])),
            plan(task("A", status="in_progress", requires_contracts=["C1"]), contracts=[{"id": "C1", "version": "1", "owner": "owner", "status": "draft"}]),
        ]
        for case in cases:
            with self.subTest(case=case):
                self.assertFalse(build_plan(case)["valid"])

    def test_schema_rejects_wrong_types_without_crashing(self):
        mutations = [
            ("max_workers", True), ("max_workers", 0), ("max_workers", "3"),
            ("contracts", {}), ("tasks", []), ("schema_version", True),
            ("project", "\x1b[31mBad"),
        ]
        for field, value in mutations:
            with self.subTest(field=field, value=value):
                data = plan(task("A"))
                data[field] = value
                self.assertFalse(build_plan(data)["valid"])
        for field, value in [("depends_on", "A"), ("writes", [None]), ("owner", ""), ("status", []), ("estimate_minutes", True), ("estimate_minutes", float("inf")), ("estimate_minutes", -1), ("produces", []), ("acceptance", [])]:
            with self.subTest(field=field, value=value):
                self.assertFalse(build_plan(plan(task("A", **{field: value})))["valid"])

    def test_missing_duplicate_and_extra_fields(self):
        data = plan(task("A"))
        del data["tasks"][0]["owner"]
        self.assertFalse(build_plan(data)["valid"])
        self.assertFalse(build_plan(plan(task("A"), task("A")))["valid"])
        self.assertFalse(build_plan(plan(task("A", depends_on=["B", "B"]), task("B")))["valid"])
        self.assertFalse(build_plan(plan(task("A", filez=[])))["valid"])

    def test_paths_cannot_escape_repository_and_arbitrary_globs_are_rejected(self):
        for path in ["/etc/passwd", "../other", "src/../../other", "C:/data", "C:\\data", "src/*.py", "src/**/a.py", "src//a.py", "./src/a.py", "~/keys", "**", ""]:
            with self.subTest(path=path):
                self.assertFalse(build_plan(plan(task("A", writes=[path])))["valid"])

    def test_original_input_is_not_mutated(self):
        data = plan(task("A"), task("B", depends_on=["A"]))
        original = copy.deepcopy(data)
        build_plan(data)
        self.assertEqual(data, original)

    def test_all_done_has_zero_remaining_time(self):
        result = build_plan(plan(task("A", status="done"), task("B", status="done", depends_on=["A"])))
        self.assertTrue(result["complete_schedule"])
        self.assertEqual(result["estimated_makespan_minutes"], 0)
        self.assertEqual(result["critical_path_minutes"], 0)

    def test_cli_rejects_malformed_and_duplicate_json(self):
        script = SCRIPTS / "plan.py"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            for content in ["{broken", '{"project":"a","project":"b"}', '{"max_workers":NaN}']:
                path.write_text(content, encoding="utf-8")
                completed = subprocess.run([sys.executable, "-B", str(script), str(path), "--json"], capture_output=True, text=True, encoding="utf-8", check=False)
                self.assertEqual(completed.returncode, 2)
                self.assertTrue(json.loads(completed.stdout)["errors"])
                self.assertEqual(path.read_text(encoding="utf-8"), content)

    def test_command_like_text_is_not_executed(self):
        with tempfile.TemporaryDirectory() as directory:
            sentinel = Path(directory) / "should-not-exist"
            data = plan(task("A", acceptance=[f"$(touch {sentinel})"], produces=[f"; touch {sentinel}"]))
            result = build_plan(data)
            self.assertTrue(result["valid"])
            self.assertFalse(sentinel.exists())

    def test_deterministic_schedule_and_valid_resource_intervals(self):
        data = plan(task("A", estimate_minutes=6), task("B", estimate_minutes=4), task("C", depends_on=["A"], writes=["db/**"], estimate_minutes=2), task("D", depends_on=["B"], writes=["db/schema.sql"], estimate_minutes=3), max_workers=2)
        result = build_plan(data)
        self.assertEqual(result, build_plan(data))
        times = intervals(result)
        self.assertTrue(times["C"][1] <= times["D"][0] or times["D"][1] <= times["C"][0])
        self.assertGreaterEqual(times["C"][0], times["A"][1])
        self.assertGreaterEqual(times["D"][0], times["B"][1])


    def test_unsafe_unicode_is_a_validation_error_not_a_crash(self):
        for character in ["\ud800", "\u0085", "\u2028", "\u2029", "\u202e", "\u200b", "\u2066", "\u2060"]:
            result = build_plan(plan(task("A"), project="Planner" + character))
            self.assertFalse(result["valid"], repr(character))
            self.assertTrue(result["errors"], repr(character))

    def test_invisible_format_characters_are_refused_in_titles(self):
        # BOM, soft hyphen, a deprecated format control, the Mongolian vowel separator and tag characters
        # are all category Cf: invisible in a report, so a title carrying one is a validation error.
        for character in ["﻿", "­", "⁪", "᠎", "\U000e0001", "\U000e0041", "\U000e007f"]:
            result = build_plan(plan(task("A", title="Family" + character + "plan")))
            self.assertFalse(result["valid"], repr(character))
            self.assertTrue(any("title" in error for error in result["errors"]), (repr(character), result["errors"]))

    def test_joiners_stay_valid_in_titles(self):
        # ZWJ and ZWNJ are needed by real scripts and emoji; only bidi and invisible separators are refused.
        for character in ["\u200d", "\u200c"]:
            result = build_plan(plan(task("A", title="Family" + character + "plan")))
            self.assertTrue(result["valid"], repr(character))

    def test_utf8_stdout_reconfigures_a_cp1252_stream(self):
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding="cp1252", newline="\n")
        with mock.patch.object(sys, "stdout", stream):
            utf8_stdout()
            print("Booking → room")
            sys.stdout.flush()
        self.assertEqual(raw.getvalue(), "Booking → room\n".encode("utf-8"))

    def test_text_output_keeps_exact_minutes(self):
        self.assertIn("0-1234567 min", render_text(build_plan(plan(task("A", estimate_minutes=1234567)))))


def fixture():
    data = plan(
        task("CODE", estimate_minutes=10),
        task("REVIEW", owner="reviewer", depends_on=["CODE"], estimate_minutes=5),
        task("APPLY", depends_on=["REVIEW"], estimate_minutes=10),
        task("VERIFY", depends_on=["APPLY"], estimate_minutes=5),
        max_workers=2,
    )
    ledger = {
        "schema_version": 1, "project": data["project"],
        "assumptions": ["Continuous availability; review duration included; repository destination proposed."],
        "task_estimates": [
            {"task_id": item["id"], "optimistic_minutes": item["estimate_minutes"] / 2,
             "likely_minutes": item["estimate_minutes"], "conservative_minutes": item["estimate_minutes"] * 2}
            for item in data["tasks"]
        ],
        "contract_gates": [],
        "pull_requests": [{"id": "PR-01", "repository": "example/proposed", "title": "Deliver a coherent slice",
                           "owner": "platform", "reviewer": "reviewer", "state": "planned",
                           "url": None, "evidence": [], "task_ids": ["CODE", "REVIEW"]}],
        "resources": [{"id": "R-01", "name": "Private object store", "kind": "S3 bucket", "action": "create",
                       "environment": "staging", "quantity": 1, "owner": "platform", "pr_ids": ["PR-01"],
                       "provision_task_ids": ["APPLY"], "verify_task_ids": ["VERIFY"], "depends_on": [],
                       "justification": "Store application artifacts privately.",
                       "expected_evidence": ["Read/write policy and private access observed"], "observed_evidence": []}],
        "milestones": [{"id": "M-01", "title": "Runtime verified", "task_ids": ["VERIFY"]}],
    }
    return data, ledger


def add_estimate(ledger, item):
    ledger["task_estimates"].append({"task_id": item["id"], "optimistic_minutes": item["estimate_minutes"] / 2,
                                   "likely_minutes": item["estimate_minutes"], "conservative_minutes": item["estimate_minutes"] * 2})


class DeliveryMapBehavior(unittest.TestCase):
    def test_separates_pr_readiness_provision_and_verification(self):
        data, ledger = fixture()
        result = build_delivery_map(data, ledger)
        self.assertTrue(result["valid"], result["errors"])
        self.assertEqual(result["pull_requests"][0]["forecasts"]["likely"]["ready_at_minutes"], 15)
        self.assertEqual(result["resources"][0]["forecasts"]["likely"]["provisioned_at_minutes"], 25)
        self.assertEqual(result["resources"][0]["forecasts"]["likely"]["verified_at_minutes"], 30)
        self.assertEqual(result["milestones"][0]["forecasts"]["likely"]["at_minutes"], 30)
        self.assertEqual([result["forecast"]["scenarios"][s]["makespan_minutes"] for s in ("optimistic", "likely", "conservative")], [15, 30, 60])
        self.assertEqual(result["resources"][0]["actual_state"], "planned_or_in_progress")
        self.assertIsNone(result["pull_requests"][0]["url"])

    def test_shared_pr_and_tasks_do_not_duplicate_effort_for_resources(self):
        data, ledger = fixture()
        second = copy.deepcopy(ledger["resources"][0])
        second.update({"id": "R-02", "action": "change", "quantity": 50, "depends_on": ["R-01"]})
        ledger["resources"].append(second)
        result = build_delivery_map(data, ledger)
        self.assertTrue(result["valid"], result["errors"])
        self.assertEqual(result["forecast"]["scenarios"]["likely"]["effort_minutes"], 30)
        self.assertEqual(result["forecast"]["scenarios"]["likely"]["makespan_minutes"], 30)
        self.assertTrue(any("internal resource ordering" in item for item in result["warnings"]))

    def test_blocked_task_retains_partial_schedule_and_null_total(self):
        data, ledger = fixture()
        data["tasks"][2]["status"] = "blocked"
        result = build_delivery_map(data, ledger)
        self.assertTrue(result["valid"])
        for scenario in result["forecast"]["scenarios"].values():
            self.assertFalse(scenario["complete"])
            self.assertIsNone(scenario["makespan_minutes"])
        self.assertEqual(result["pull_requests"][0]["forecasts"]["likely"]["ready_at_minutes"], 15)
        self.assertIsNone(result["resources"][0]["forecasts"]["likely"]["provisioned_at_minutes"])

    def test_reuse_with_real_declared_evidence_needs_no_pr(self):
        data = plan(task("COORD", estimate_minutes=3))
        _, ledger = fixture()
        ledger["task_estimates"] = []
        add_estimate(ledger, data["tasks"][0])
        ledger["pull_requests"] = []
        ledger["milestones"] = []
        ledger["resources"][0].update({"action": "reuse", "pr_ids": [], "provision_task_ids": [],
                                        "verify_task_ids": [], "observed_evidence": ["Inventory inspected in this fixture"]})
        result = build_delivery_map(data, ledger)
        self.assertTrue(result["valid"], result["errors"])
        self.assertEqual(result["resources"][0]["actual_state"], "verified_declared")
        self.assertEqual(result["resources"][0]["forecasts"]["likely"]["verified_at_minutes"], 0)
        ledger["resources"][0]["observed_evidence"] = []
        self.assertFalse(build_delivery_map(data, ledger)["valid"])
        ledger["resources"][0]["verify_task_ids"] = ["COORD"]
        result = build_delivery_map(data, ledger)
        self.assertTrue(result["valid"])
        self.assertEqual(result["resources"][0]["actual_state"], "reuse_pending_verification")
        self.assertEqual(result["resources"][0]["forecasts"]["likely"]["verified_at_minutes"], 3)

    def test_create_requires_pr_and_runtime_ancestry(self):
        for mutation in ("no_pr", "apply_not_after_pr", "verify_not_after_apply", "runtime_in_pr"):
            data, ledger = fixture()
            if mutation == "no_pr":
                ledger["resources"][0]["pr_ids"] = []
            elif mutation == "apply_not_after_pr":
                data["tasks"][2]["depends_on"] = []
            elif mutation == "verify_not_after_apply":
                data["tasks"][3]["depends_on"] = ["REVIEW"]
            else:
                ledger["pull_requests"][0]["task_ids"].append("VERIFY")
            with self.subTest(mutation=mutation):
                self.assertFalse(build_delivery_map(data, ledger)["valid"])

    def test_estimate_ranges_coverage_and_strict_types(self):
        for mutation in ("range", "likely_mismatch", "missing", "duplicate", "bool", "extra", "quantity"):
            data, ledger = fixture()
            if mutation == "range":
                ledger["task_estimates"][0]["optimistic_minutes"] = 999
            elif mutation == "likely_mismatch":
                ledger["task_estimates"][0]["likely_minutes"] = 11
            elif mutation == "missing":
                ledger["task_estimates"].pop()
            elif mutation == "duplicate":
                ledger["task_estimates"].append(copy.deepcopy(ledger["task_estimates"][0]))
            elif mutation == "bool":
                ledger["task_estimates"][0]["optimistic_minutes"] = True
            elif mutation == "extra":
                ledger["resources"][0]["silent_field"] = "ignored?"
            else:
                ledger["resources"][0]["quantity"] = True
            with self.subTest(mutation=mutation):
                self.assertFalse(build_delivery_map(data, ledger)["valid"])

    def test_conditional_contract_forecast_preserves_real_dispatch_and_inputs(self):
        data, ledger = fixture()
        gate = task("GATE", estimate_minutes=4)
        data["tasks"].insert(0, gate)
        add_estimate(ledger, gate)
        data["contracts"] = [{"id": "API", "version": "1.0", "status": "draft", "owner": "architect"}]
        data["tasks"][1]["depends_on"] = ["GATE"]
        data["tasks"][1]["requires_contracts"] = ["API"]
        ledger["contract_gates"] = [{"contract_id": "API", "task_ids": ["GATE"], "condition": "Consumer review approves the API"}]
        original = copy.deepcopy((data, ledger))
        result = build_delivery_map(data, ledger)
        self.assertTrue(result["valid"], result["errors"])
        self.assertTrue(result["forecast"]["conditional"])
        self.assertEqual(result["forecast"]["scenarios"]["likely"]["makespan_minutes"], 34)
        self.assertEqual(result["actual_dispatch"]["start_now"], ["GATE"])
        self.assertNotIn("CODE", result["actual_dispatch"]["ready_now"])
        self.assertEqual((data, ledger), original)
        ledger["contract_gates"] = []
        result = build_delivery_map(data, ledger)
        self.assertIsNone(result["forecast"]["scenarios"]["likely"]["makespan_minutes"])
        self.assertTrue(result["forecast"]["scenarios"]["likely"]["blocking_conditions"])

    def test_contract_gate_cannot_be_bypassed_or_consume_itself(self):
        data, ledger = fixture()
        data["contracts"] = [{"id": "API", "version": "1", "status": "draft", "owner": "architect"}]
        data["tasks"][0]["requires_contracts"] = ["API"]
        ledger["contract_gates"] = [{"contract_id": "API", "task_ids": ["REVIEW"], "condition": "Review accepts contract"}]
        result = build_delivery_map(data, ledger)
        self.assertFalse(result["valid"])
        self.assertTrue(any("lacks gate ancestors" in item for item in result["errors"]))
        ledger["contract_gates"][0]["task_ids"] = ["CODE"]
        result = build_delivery_map(data, ledger)
        self.assertTrue(any("consumes its own" in item for item in result["errors"]))

    def test_capacity_and_shared_owner_change_forecast(self):
        data, ledger = fixture()
        other = task("OTHER", owner=data["tasks"][0]["owner"], estimate_minutes=40)
        data["tasks"].append(other)
        add_estimate(ledger, other)
        serialized = build_delivery_map(data, ledger)
        data["tasks"][-1]["owner"] = "independent"
        parallel = build_delivery_map(data, ledger)
        self.assertGreater(serialized["forecast"]["scenarios"]["likely"]["makespan_minutes"],
                           parallel["forecast"]["scenarios"]["likely"]["makespan_minutes"])
        data["max_workers"] = 1
        limited = build_delivery_map(data, ledger)
        self.assertEqual(limited["forecast"]["scenarios"]["likely"]["makespan_minutes"], 70)

    def test_unused_draft_contract_does_not_block_or_condition_forecast(self):
        data, ledger = fixture()
        data["contracts"] = [{"id": "OPTIONAL", "version": "0.1", "status": "draft", "owner": "architect"}]
        for gates in ([], [{"contract_id": "OPTIONAL", "task_ids": ["CODE"], "condition": "Optional future review"}]):
            with self.subTest(gates=gates):
                ledger["contract_gates"] = gates
                result = build_delivery_map(data, ledger)
                self.assertTrue(result["valid"])
                self.assertTrue(result["forecast"]["scenarios"]["likely"]["complete"])
                self.assertEqual(result["forecast"]["scenarios"]["likely"]["makespan_minutes"], 30)
                self.assertFalse(result["forecast"]["conditional"])
                self.assertEqual(result["forecast"]["conditions"], [])

    def test_resource_cycles_unknown_refs_and_fake_pr_metadata_are_rejected(self):
        for mutation in ("cycle", "unknown", "planned_url", "open_no_evidence"):
            data, ledger = fixture()
            if mutation == "cycle":
                ledger["resources"][0]["depends_on"] = ["R-01"]
            elif mutation == "unknown":
                ledger["resources"][0]["pr_ids"] = ["PR-99"]
            elif mutation == "planned_url":
                ledger["pull_requests"][0]["url"] = "https://example.test/pr/1"
            else:
                ledger["pull_requests"][0].update({"state": "open", "url": "https://example.test/pr/1"})
            with self.subTest(mutation=mutation):
                self.assertFalse(build_delivery_map(data, ledger)["valid"])

    def test_done_tasks_without_runtime_evidence_do_not_claim_verified(self):
        data, ledger = fixture()
        for item in data["tasks"]:
            item["status"] = "done"
        result = build_delivery_map(data, ledger)
        self.assertTrue(result["valid"])
        self.assertTrue(result["pull_requests"][0]["ready_declared"])
        self.assertNotEqual(result["resources"][0]["actual_state"], "verified_declared")
        self.assertIsNone(result["resources"][0]["forecasts"]["likely"]["verified_at_minutes"])
        self.assertIsNone(result["forecast"]["scenarios"]["likely"]["makespan_minutes"])
        ledger["resources"][0]["observed_evidence"] = ["Observed runtime fixture evidence"]
        result = build_delivery_map(data, ledger)
        self.assertEqual(result["resources"][0]["actual_state"], "verified_declared")
        self.assertEqual(result["forecast"]["scenarios"]["likely"]["makespan_minutes"], 0)

    def test_markdown_tables_escape_cells_and_cli_preserves_inputs(self):
        data, ledger = fixture()
        ledger["resources"][0]["name"] = "Store | <script>"
        markdown = render_markdown(build_delivery_map(data, ledger))
        self.assertIn("| Resource / kind |", markdown)
        self.assertIn("Store \\| &lt;script&gt;", markdown)
        self.assertIn("example/proposed", markdown)
        self.assertIn("T+15 min", markdown)
        self.assertIn("Ready to merge", markdown)
        with tempfile.TemporaryDirectory() as directory:
            plan_path = Path(directory) / "plan.json"
            ledger_path = Path(directory) / "resources.json"
            originals = [json.dumps(data), json.dumps(ledger)]
            plan_path.write_text(originals[0], encoding="utf-8")
            ledger_path.write_text(originals[1], encoding="utf-8")
            completed = subprocess.run([sys.executable, "-B", str(SCRIPTS / "delivery_map.py"),
                                        str(plan_path), str(ledger_path), "--json"], capture_output=True, text=True, encoding="utf-8", check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue(json.loads(completed.stdout)["valid"])
            self.assertEqual(completed.stderr, "")
            self.assertEqual([plan_path.read_text(encoding="utf-8"), ledger_path.read_text(encoding="utf-8")], originals)


    def test_a_shorter_scenario_never_finishes_later(self):
        # List scheduling can finish later when one task gets shorter (a scheduling anomaly).
        data = plan(
            task("A", owner="o0", estimate_minutes=1),
            task("B", owner="o2", depends_on=["A"], estimate_minutes=2),
            task("C", owner="o0", estimate_minutes=3),
            task("P", owner="o3", depends_on=["B", "C"], estimate_minutes=1),
            task("V", owner="o3", depends_on=["P"], estimate_minutes=1),
        )
        _, ledger = fixture()
        ledger["project"] = data["project"]
        ledger["task_estimates"] = [
            {"task_id": item["id"], "optimistic_minutes": item["estimate_minutes"],
             "likely_minutes": item["estimate_minutes"], "conservative_minutes": item["estimate_minutes"]}
            for item in data["tasks"]]
        ledger["task_estimates"][1]["optimistic_minutes"] = 1
        ledger["pull_requests"][0]["task_ids"] = ["A", "B", "C"]
        ledger["resources"][0].update(provision_task_ids=["P"], verify_task_ids=["V"])
        ledger["milestones"][0]["task_ids"] = ["V"]
        result = build_delivery_map(data, ledger)
        self.assertTrue(result["valid"], result["errors"])
        scenarios = ("optimistic", "likely", "conservative")
        series = [
            [result["forecast"]["scenarios"][s]["makespan_minutes"] for s in scenarios],
            [result["pull_requests"][0]["forecasts"][s]["ready_at_minutes"] for s in scenarios],
            [result["resources"][0]["forecasts"][s]["verified_at_minutes"] for s in scenarios],
            [result["milestones"][0]["forecasts"][s]["at_minutes"] for s in scenarios],
        ]
        for values in series:
            self.assertEqual(values, sorted(values), series)
        self.assertEqual(list(result["forecast"]["scenarios"]), list(scenarios))
        self.assertTrue(any("reuses the likely start times" in warning for warning in result["warnings"]))

    def test_markdown_cli_survives_isolated_mode_and_a_cp1252_stdout(self):
        data, ledger = fixture()
        with tempfile.TemporaryDirectory() as directory:
            plan_path = Path(directory) / "plan.json"
            ledger_path = Path(directory) / "resources.json"
            plan_path.write_text(json.dumps(data), encoding="utf-8")
            ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
            arguments = [str(SCRIPTS / "delivery_map.py"), str(plan_path), str(ledger_path)]
            # -I ignores PYTHONIOENCODING, so isolated mode and the cp1252 stdout are two runs.
            isolated = subprocess.run([sys.executable, "-I", "-B", *arguments], capture_output=True, check=False)
            self.assertEqual(isolated.returncode, 0, isolated.stderr)
            self.assertIn("Resources → PRs", isolated.stdout.decode("utf-8"))
            cp1252 = subprocess.run([sys.executable, "-B", *arguments], capture_output=True, check=False,
                                    env=dict(os.environ, PYTHONIOENCODING="cp1252"))
            self.assertEqual(cp1252.returncode, 0, cp1252.stderr)
            self.assertIn("Resources → PRs", cp1252.stdout.decode("utf-8"))

    def test_pt_br_renders_the_portuguese_map(self):
        data, ledger = fixture()
        result = build_delivery_map(data, ledger)
        markdown = render_markdown(result, "pt-BR")
        self.assertTrue(markdown.startswith("# Mapa de entrega\n"), markdown[:40])
        for text in ("| Recurso / tipo |", "## Recursos → PRs → tarefas", "Pronta para merge O/P/C", "ainda sem PR real"):
            self.assertIn(text, markdown)
        self.assertNotIn("Delivery map", markdown)
        self.assertIn("não previsto", render_markdown(build_delivery_map(*self.blocked()), "pt-BR"))
        self.assertIn("not forecast", render_markdown(build_delivery_map(*self.blocked())))
        self.assertEqual(render_markdown(result), render_markdown(result, "en"))

    @staticmethod
    def blocked():
        data, ledger = fixture()
        data["tasks"][2]["status"] = "blocked"
        return data, ledger

    def test_cli_rejects_an_unknown_lang_and_keeps_json_language_neutral(self):
        data, ledger = fixture()
        script = str(SCRIPTS / "delivery_map.py")
        with tempfile.TemporaryDirectory() as directory:
            plan_path = Path(directory) / "plan.json"
            ledger_path = Path(directory) / "resources.json"
            plan_path.write_text(json.dumps(data), encoding="utf-8")
            ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
            paths = [str(plan_path), str(ledger_path)]
            rejected = subprocess.run([sys.executable, "-I", "-B", script, *paths, "--lang", "pt"],
                                      capture_output=True, text=True, encoding="utf-8", check=False)
            self.assertEqual(rejected.returncode, 2)
            self.assertEqual(rejected.stderr, "")
            self.assertIn("invalid choice: 'pt'", rejected.stdout)
            outputs = [subprocess.run([sys.executable, "-I", "-B", script, *paths, "--json", *lang], capture_output=True,
                                      text=True, encoding="utf-8", check=False).stdout for lang in ([], ["--lang", "pt-BR"])]
            self.assertTrue(json.loads(outputs[0])["valid"])
            self.assertEqual(outputs[0], outputs[1])


class HandoffBehavior(unittest.TestCase):
    def test_packet_contains_only_required_context_and_empty_return_fields(self):
        contract = {"id": "API", "version": "2.3.0", "status": "stable", "owner": "architect"}
        data = plan(task("SETUP", status="done"), task("API", depends_on=["SETUP"], requires_contracts=["API"]), task("UNRELATED"), contracts=[contract, {"id": "OTHER", "version": "1.0.0", "status": "draft", "owner": "other"}])
        before = copy.deepcopy(data)
        commit = "a" * 40
        packet = build_handoff(data, "API", commit)
        self.assertTrue(packet["valid"])
        self.assertEqual(packet["task"], data["tasks"][1])
        self.assertEqual(packet["contracts"], [contract])
        self.assertEqual([item["id"] for item in packet["dependencies"]], ["SETUP"])
        self.assertEqual(packet["base_commit"], commit)
        self.assertFalse(packet["base_commit_verified"])
        self.assertTrue(packet["ready_now"])
        self.assertTrue(packet["start_now"])
        self.assertIsNone(packet["return_template"]["commit"])
        self.assertEqual(packet["return_template"]["evidence"], [])
        self.assertEqual(packet["return_template"]["actual_writes"], [])
        self.assertEqual(packet["return_template"]["blockers"], [])
        self.assertEqual(packet["return_template"]["result"], "not_reported")
        self.assertEqual(data, before)

    def test_individual_readiness_does_not_claim_simultaneous_dispatch(self):
        data = plan(task("A", locks=["terraform:dev"]), task("B", locks=["terraform:dev"]))
        packet = build_handoff(data, "B")
        self.assertTrue(packet["ready_now"])
        self.assertFalse(packet["start_now"])
        self.assertEqual(packet["resource_conflicts"][0]["other_task"], "A")
        data["tasks"][0]["status"] = "in_progress"
        packet = build_handoff(data, "B")
        self.assertFalse(packet["ready_now"])
        self.assertTrue(any("occupied_by:A" in reason for reason in packet["not_ready_reasons"]))

    def test_invalid_schema_unknown_task_and_bad_commit_are_rejected(self):
        cases = [
            ({"project": "incomplete"}, "A", None),
            (plan(task("A")), "MISSING", None),
            (plan(task("A")), "A", "main"),
            (plan(task("A", depends_on=["B"]), task("B", depends_on=["A"])), "A", None),
        ]
        for data, task_id, commit in cases:
            with self.subTest(task_id=task_id, commit=commit):
                packet = build_handoff(data, task_id, commit)
                self.assertFalse(packet["valid"])
                self.assertTrue(packet["errors"])

    def test_cli_emits_json_only_and_does_not_execute_text_or_change_input(self):
        script = SCRIPTS / "handoff.py"
        with tempfile.TemporaryDirectory() as directory:
            sentinel = Path(directory) / "must-not-exist"
            source = Path(directory) / "plan.json"
            data = plan(task("A", acceptance=[f"$(touch {sentinel})"], produces=[str(sentinel)]))
            original = json.dumps(data)
            source.write_text(original, encoding="utf-8")
            completed = subprocess.run([sys.executable, "-B", str(script), str(source), "A"], capture_output=True, text=True, encoding="utf-8", check=False)
            self.assertEqual(completed.returncode, 0)
            self.assertEqual(completed.stderr, "")
            self.assertEqual(len(completed.stdout.splitlines()), 1)
            self.assertTrue(json.loads(completed.stdout)["valid"])
            self.assertFalse(sentinel.exists())
            self.assertEqual(source.read_text(encoding="utf-8"), original)
            rejected = subprocess.run([sys.executable, "-B", str(script), str(source), "NOPE"], capture_output=True, text=True, encoding="utf-8", check=False)
            self.assertEqual(rejected.returncode, 2)
            self.assertEqual(rejected.stderr, "")
            self.assertFalse(json.loads(rejected.stdout)["valid"])


    def test_cli_imports_the_planner_in_isolated_mode(self):
        script = SCRIPTS / "handoff.py"
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "plan.json"
            source.write_text(json.dumps(plan(task("A", title="Booking → room"))), encoding="utf-8")
            completed = subprocess.run([sys.executable, "-I", "-B", str(script), str(source), "A"],
                                       capture_output=True, check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue(json.loads(completed.stdout.decode("utf-8"))["valid"])
            # -I ignores PYTHONIOENCODING; a separate run proves the arrow survives a cp1252 stdout.
            cp1252 = subprocess.run([sys.executable, "-B", str(script), str(source), "A"], capture_output=True,
                                    check=False, env=dict(os.environ, PYTHONIOENCODING="cp1252"))
            self.assertEqual(cp1252.returncode, 0, cp1252.stderr)
            self.assertIn("Booking → room", cp1252.stdout.decode("utf-8"))


# Private strings must never ship in the public repository. Built from pieces so
# this file does not trip the same scan it runs.
PRIVATE = [
    "/home/" + "eduardo" + "pin", "~/dev/" + "cg" + "lab", "cg" + "lab", "CG" + "/lab",
    "claude-" + "squad", "INFRA" + "-",
]


def skill_files():
    return [p for p in SKILL.rglob("*") if p.is_file() and "__pycache__" not in p.parts]


class PackageLayout(unittest.TestCase):
    def read_skill(self):
        return (SKILL / "SKILL.md").read_text(encoding="utf-8")

    def frontmatter(self):
        match = re.match(r"---\n(.*?)\n---\n", self.read_skill(), re.S)
        self.assertIsNotNone(match, "SKILL.md has no frontmatter")
        name = re.search(r"^name:\s*(\S+)\s*$", match.group(1), re.M)
        desc = re.search(r"^description:\s*(.+)$", match.group(1), re.M)
        self.assertIsNotNone(name)
        self.assertIsNotNone(desc)
        return name.group(1), json.loads(desc.group(1)) if desc.group(1).startswith('"') else desc.group(1)

    def test_name_equals_directory_and_description_fits(self):
        name, description = self.frontmatter()
        self.assertEqual(name, SKILL.name)
        self.assertLess(len(description), 1024)

    def test_skill_md_under_500_lines(self):
        self.assertLess(len(self.read_skill().splitlines()), 500)

    def test_links_from_skill_md_exist(self):
        links = re.findall(r"\]\(((?:references|assets)/[^)#\s]+)\)", self.read_skill())
        links += re.findall(r"`((?:references|assets)/[\w./-]+\.\w+)`", self.read_skill())
        self.assertTrue(links)
        for link in links:
            self.assertTrue((SKILL / link).is_file(), link)

    def test_every_reference_is_linked(self):
        text = self.read_skill()
        for ref in (SKILL / "references").glob("*.md"):
            self.assertIn("references/" + ref.name, text, ref.name)

    def test_no_private_strings_under_skill(self):
        for path in skill_files():
            text = path.read_text(encoding="utf-8", errors="ignore")
            for needle in PRIVATE:
                self.assertNotIn(needle.lower(), text.lower(), f"{needle!r} in {path.relative_to(ROOT)}")

    def test_no_bytecode_committed(self):
        # Ask git, not the working tree: a local py_compile leaves __pycache__ behind
        # and that is fine as long as it is not tracked or staged.
        done = subprocess.run(["git", "ls-files", "--", "skills/architect"], cwd=ROOT,
                              capture_output=True, text=True, encoding="utf-8")
        if done.returncode != 0:
            self.skipTest("not a git checkout")
        bad = [f for f in done.stdout.splitlines() if "__pycache__" in f or f.endswith((".pyc", ".pyo"))]
        self.assertEqual([], bad)

    def test_json_assets_parse(self):
        for path in SKILL.rglob("*.json"):
            json.loads(path.read_text(encoding="utf-8"))


class ScriptCli(unittest.TestCase):
    def test_each_script_answers_help(self):
        for name in ("plan.py", "delivery_map.py", "handoff.py"):
            done = subprocess.run([sys.executable, "-I", "-B", str(SCRIPTS / name), "--help"],
                                  capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(done.returncode, 0, name)
            self.assertIn("usage", done.stdout.lower(), name)

    def test_scripts_use_only_the_standard_library(self):
        stdlib = set(sys.stdlib_module_names) if hasattr(sys, "stdlib_module_names") else None
        local = {"plan", "delivery_map", "handoff"}
        for name in ("plan.py", "delivery_map.py", "handoff.py"):
            text = (SCRIPTS / name).read_text(encoding="utf-8")
            for mod in re.findall(r"^\s*(?:import|from)\s+([A-Za-z_]\w*)", text, re.M):
                if mod in local:
                    continue
                if stdlib is not None:
                    self.assertIn(mod, stdlib, f"{name} imports {mod}")

    def test_example_plan_round_trips_through_plan_and_delivery_map(self):
        assets = SKILL / "assets"
        plan_json = assets / "app-bootstrap.plan.json"
        ledger = assets / "app-bootstrap.resources.json"
        done = subprocess.run([sys.executable, "-I", "-B", str(SCRIPTS / "plan.py"), str(plan_json), "--json"],
                              capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(json.loads(done.stdout)["valid"])
        done = subprocess.run([sys.executable, "-I", "-B", str(SCRIPTS / "delivery_map.py"),
                               str(plan_json), str(ledger), "--json"], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(json.loads(done.stdout)["valid"])
        done = subprocess.run([sys.executable, "-I", "-B", str(SCRIPTS / "delivery_map.py"),
                               str(plan_json), str(ledger)], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(done.stdout.startswith("# Delivery map"))

    def test_plan_example_json_is_a_valid_plan(self):
        done = subprocess.run([sys.executable, "-I", "-B", str(SCRIPTS / "plan.py"),
                               str(SKILL / "assets" / "plan.example.json"), "--json"], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(json.loads(done.stdout)["valid"])


if __name__ == "__main__":
    unittest.main(verbosity=0)
