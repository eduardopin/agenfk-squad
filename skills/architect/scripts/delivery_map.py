#!/usr/bin/env python3
"""Validate a resource/PR ledger and print a capacity-aware delivery map.

Reads two JSON documents. Never executes instructions, changes their contents,
publishes pull requests, verifies remote evidence, or calls cloud services.
"""

from __future__ import annotations

import argparse
import copy
import html
import json
import math
import re
from urllib.parse import urlsplit

import sys
from pathlib import Path

# -I, -P and PYTHONSAFEPATH leave the script's directory off sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from plan import build_plan, load_plan, utf8_stdout, topological_order, validate_keys, validate_strings, validate_text


SCENARIOS = ("optimistic", "likely", "conservative")
TOP_FIELDS = {"schema_version", "project", "assumptions", "task_estimates", "contract_gates", "pull_requests", "resources", "milestones"}
ESTIMATE_FIELDS = {"task_id", "optimistic_minutes", "likely_minutes", "conservative_minutes"}
GATE_FIELDS = {"contract_id", "task_ids", "condition"}
PR_FIELDS = {"id", "repository", "title", "owner", "reviewer", "state", "url", "evidence", "task_ids"}
RESOURCE_FIELDS = {"id", "name", "kind", "action", "environment", "quantity", "owner", "pr_ids", "provision_task_ids", "verify_task_ids", "depends_on", "justification", "expected_evidence", "observed_evidence"}
MILESTONE_FIELDS = {"id", "title", "task_ids"}
LIMITATIONS = [
    "All times are relative T+ minutes under declared continuous availability, not calendar dates or delivery promises.",
    "Optimistic, likely and conservative are three input scenarios, not probabilities or percentiles.",
    "Effort is counted once per unfinished task; resource quantities and repeated PR/resource references do not multiply effort.",
    "Owners, locks and max_workers constrain the heuristic schedule. Waiting tasks also consume a worker in this version.",
    "Review, approvals, quota, provider latency and unavailable credentials must be represented in estimates or blockers; the tool cannot discover them.",
    "PR ready for merge is separate from merged, resource provisioned and runtime verified. Declared states/evidence are not remotely verified.",
    "Reviewer metadata alone reserves no capacity: model review tasks with owners and locks in plan.json.",
    "The map proves only declared links; inventory completeness and IaC internal ordering need repository/plan inspection by the executor.",
    "Conditional forecasts do not change real draft contracts, authorize dispatch, acquire external locks or execute any task.",
]


def empty_result():
    return {"schema_version": 1, "kind": "delivery_map", "valid": False, "errors": [], "warnings": [], "project": None,
            "actual_dispatch": {}, "forecast": {"conditional": False, "conditions": [], "scenarios": {}},
            "resources": [], "pull_requests": [], "milestones": [], "tasks": [], "assumptions": LIMITATIONS.copy()}


def finite_positive(value):
    try:
        return type(value) in (int, float) and value > 0 and math.isfinite(value)
    except (TypeError, ValueError, OverflowError):
        return False


def list_objects(data, key, fields, errors, nonempty=False):
    value = data.get(key)
    if not isinstance(value, list) or (nonempty and not value):
        errors.append(f"ledger.{key}: expected {'a nonempty' if nonempty else 'an'} array")
        return []
    return [(index, item) for index, item in enumerate(value)
            if validate_keys(item, fields, f"ledger.{key}[{index}]", errors)]


def unique_index(rows, key, label, errors):
    indexed = {}
    for item in rows:
        value = item[key]
        if value in indexed:
            errors.append(f"{label}: duplicate {key} {value!r}")
        indexed[value] = item
    return indexed


def ancestors_of(tasks):
    order, _, _ = topological_order(tasks)
    ancestors = {}
    for task_id in order:
        ancestors[task_id] = set(tasks[task_id]["depends_on"])
        for parent in tasks[task_id]["depends_on"]:
            ancestors[task_id].update(ancestors[parent])
    return ancestors


def check_references(values, indexed, location, errors):
    for value in values:
        if value not in indexed:
            errors.append(f"{location}: unknown ID {value!r}")


def valid_url(value):
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
        return (parsed.scheme == "https" and bool(parsed.hostname) and parsed.username is None
                and parsed.password is None and bool(parsed.path.strip("/")) and not any(char.isspace() for char in value))
    except ValueError:
        return False


def validate_ledger(plan, ledger, result):
    errors = result["errors"]
    if not validate_keys(ledger, TOP_FIELDS, "ledger", errors):
        return None
    if type(ledger.get("schema_version")) is not int or ledger["schema_version"] != 1:
        errors.append("ledger.schema_version: expected integer 1")
    validate_text(ledger.get("project"), "ledger.project", errors)
    if ledger.get("project") != plan["project"]:
        errors.append("ledger.project: must match plan.project exactly")
    validate_strings(ledger.get("assumptions"), "ledger.assumptions", errors, nonempty=True)
    estimates = list_objects(ledger, "task_estimates", ESTIMATE_FIELDS, errors, True)
    gates = list_objects(ledger, "contract_gates", GATE_FIELDS, errors)
    prs = list_objects(ledger, "pull_requests", PR_FIELDS, errors)
    resources = list_objects(ledger, "resources", RESOURCE_FIELDS, errors, True)
    milestones = list_objects(ledger, "milestones", MILESTONE_FIELDS, errors)
    for index, item in estimates:
        prefix = f"ledger.task_estimates[{index}]"
        validate_text(item.get("task_id"), prefix + ".task_id", errors, True)
        values = [item.get(scenario + "_minutes") for scenario in SCENARIOS]
        if not all(finite_positive(value) for value in values):
            errors.append(prefix + ": each duration must be a finite positive number")
        elif not values[0] <= values[1] <= values[2]:
            errors.append(prefix + ": require optimistic <= likely <= conservative")
    for index, item in gates:
        prefix = f"ledger.contract_gates[{index}]"
        validate_text(item.get("contract_id"), prefix + ".contract_id", errors, True)
        validate_text(item.get("condition"), prefix + ".condition", errors)
        validate_strings(item.get("task_ids"), prefix + ".task_ids", errors, True, True)
    for index, item in prs:
        prefix = f"ledger.pull_requests[{index}]"
        for key in ("id", "repository", "title", "owner", "reviewer"):
            validate_text(item.get(key), prefix + "." + key, errors, key == "id")
        if not isinstance(item.get("id"), str) or not re.fullmatch(r"PR-[0-9]{2,}", item["id"]):
            errors.append(prefix + ".id: use a planned ID such as PR-01")
        if item.get("state") not in ("planned", "open", "merged"):
            errors.append(prefix + ".state: expected planned, open or merged")
        validate_strings(item.get("task_ids"), prefix + ".task_ids", errors, True, True)
        validate_strings(item.get("evidence"), prefix + ".evidence", errors)
        if item.get("state") == "planned":
            if item.get("url") is not None or item.get("evidence") != []:
                errors.append(prefix + ": planned PRs require url=null and evidence=[]; never invent real PR metadata")
        elif item.get("state") in ("open", "merged"):
            if not valid_url(item.get("url")):
                errors.append(prefix + ".url: existing PR requires an HTTPS URL without embedded credentials")
            if not isinstance(item.get("evidence"), list) or not item["evidence"]:
                errors.append(prefix + ".evidence: existing PR requires observed evidence")
    for index, item in resources:
        prefix = f"ledger.resources[{index}]"
        for key in ("id", "name", "kind", "environment", "owner", "justification"):
            validate_text(item.get(key), prefix + "." + key, errors, key == "id")
        if item.get("action") not in ("create", "change", "reuse"):
            errors.append(prefix + ".action: expected create, change or reuse")
        if type(item.get("quantity")) is not int or item["quantity"] < 1:
            errors.append(prefix + ".quantity: expected integer >= 1")
        for key in ("pr_ids", "provision_task_ids", "verify_task_ids", "depends_on"):
            validate_strings(item.get(key), prefix + "." + key, errors, identifiers=True)
        validate_strings(item.get("expected_evidence"), prefix + ".expected_evidence", errors, True)
        validate_strings(item.get("observed_evidence"), prefix + ".observed_evidence", errors)
        if item.get("action") in ("create", "change"):
            for key in ("pr_ids", "provision_task_ids", "verify_task_ids"):
                if isinstance(item.get(key), list) and not item[key]:
                    errors.append(prefix + "." + key + ": create/change requires at least one reference")
        if item.get("action") == "reuse":
            if isinstance(item.get("provision_task_ids"), list) and item["provision_task_ids"]:
                errors.append(prefix + ": reuse cannot provision; use change for mutations")
            if item.get("verify_task_ids") == [] and item.get("observed_evidence") == []:
                errors.append(prefix + ": reuse without verification tasks requires observed evidence")
    for index, item in milestones:
        prefix = f"ledger.milestones[{index}]"
        validate_text(item.get("id"), prefix + ".id", errors, True)
        validate_text(item.get("title"), prefix + ".title", errors)
        validate_strings(item.get("task_ids"), prefix + ".task_ids", errors, True, True)
    if errors:
        return None
    tasks = {item["id"]: item for item in plan["tasks"]}
    contracts = {item["id"]: item for item in plan["contracts"]}
    by_estimate = unique_index([item for _, item in estimates], "task_id", "task_estimates", errors)
    by_gate = unique_index([item for _, item in gates], "contract_id", "contract_gates", errors)
    by_pr = unique_index([item for _, item in prs], "id", "pull_requests", errors)
    by_resource = unique_index([item for _, item in resources], "id", "resources", errors)
    by_milestone = unique_index([item for _, item in milestones], "id", "milestones", errors)
    check_references(by_estimate, tasks, "task_estimates", errors)
    for task_id in tasks:
        if task_id not in by_estimate:
            errors.append(f"task_estimates: missing estimate for {task_id}")
        elif by_estimate[task_id]["likely_minutes"] != tasks[task_id]["estimate_minutes"]:
            errors.append(f"task_estimates.{task_id}: likely_minutes must equal plan estimate_minutes")
    for gate in by_gate.values():
        check_references([gate["contract_id"]], contracts, "contract_gates", errors)
        check_references(gate["task_ids"], tasks, "contract_gates." + gate["contract_id"], errors)
    for pr in by_pr.values():
        check_references(pr["task_ids"], tasks, "pull_requests." + pr["id"], errors)
    for resource in by_resource.values():
        check_references(resource["pr_ids"], by_pr, "resources." + resource["id"] + ".pr_ids", errors)
        check_references(resource["depends_on"], by_resource, "resources." + resource["id"] + ".depends_on", errors)
        check_references(resource["provision_task_ids"] + resource["verify_task_ids"], tasks, "resources." + resource["id"], errors)
    for milestone in by_milestone.values():
        check_references(milestone["task_ids"], tasks, "milestones." + milestone["id"], errors)
    if errors:
        return None
    ancestors = ancestors_of(tasks)
    for gate in by_gate.values():
        for gate_id in gate["task_ids"]:
            if gate["contract_id"] in tasks[gate_id]["requires_contracts"]:
                errors.append(f"contract gate {gate['contract_id']}: gate task {gate_id} consumes its own draft contract")
        for task_id, task in tasks.items():
            if task["status"] != "done" and gate["contract_id"] in task["requires_contracts"]:
                missing = set(gate["task_ids"]) - ancestors[task_id]
                if missing:
                    errors.append(f"contract gate {gate['contract_id']}: consumer {task_id} lacks gate ancestors {sorted(missing)}")
    runtime_ids = {task_id for resource in by_resource.values() for task_id in resource["provision_task_ids"] + resource["verify_task_ids"]}
    for pr in by_pr.values():
        overlap = set(pr["task_ids"]) & runtime_ids
        if overlap:
            errors.append(f"PR {pr['id']}: merge-readiness tasks cannot include resource provision/verify tasks {sorted(overlap)}")
        if pr["state"] == "merged" and any(tasks[task_id]["status"] != "done" for task_id in pr["task_ids"]):
            errors.append(f"PR {pr['id']}: merged state contradicts unfinished PR tasks")
        if not any(tasks[task_id]["owner"] == pr["reviewer"] for task_id in pr["task_ids"]):
            result["warnings"].append(f"PR {pr['id']}: reviewer has no task in this PR; review capacity/latency must be represented explicitly")
    _, _, cyclic_resources = topological_order(by_resource)
    if cyclic_resources:
        errors.append("resource dependency cycle: " + ", ".join(cyclic_resources))
    for resource in by_resource.values():
        code_tasks = {task_id for pr_id in resource["pr_ids"] for task_id in by_pr[pr_id]["task_ids"]}
        for task_id in resource["provision_task_ids"]:
            missing = code_tasks - ancestors[task_id]
            if missing:
                errors.append(f"resource {resource['id']}: provision task {task_id} lacks linked PR ancestors {sorted(missing)}")
        for task_id in resource["verify_task_ids"]:
            missing = set(resource["provision_task_ids"]) - ancestors[task_id]
            if missing:
                errors.append(f"resource {resource['id']}: verify task {task_id} lacks provision ancestors {sorted(missing)}")
        activation = resource["provision_task_ids"] or resource["verify_task_ids"]
        for dependency_id in resource["depends_on"]:
            dependency = by_resource[dependency_id]
            availability = dependency["provision_task_ids"] or dependency["verify_task_ids"]
            for task_id in activation:
                missing = set(availability) - ancestors[task_id] - {task_id}
                if missing:
                    errors.append(f"resource {resource['id']}: task {task_id} lacks resource dependency {dependency_id} task ancestors {sorted(missing)}")
                if task_id in availability:
                    result["warnings"].append(f"Resources {dependency_id} -> {resource['id']} share task {task_id}; internal resource ordering must be encoded and verified in IaC")
            if not activation and any(tasks[task_id]["status"] != "done" for task_id in availability):
                errors.append(f"resource {resource['id']}: declared existing reuse has an unfinished resource dependency {dependency_id}")
    if errors:
        return None
    return tasks, contracts, by_estimate, by_gate, by_pr, by_resource, by_milestone


def replay_if_later(scheduled, longer, estimates, scenario):
    """Swap in the longer scenario's start times when this schedule ends any task later.

    Every task is no longer than in the longer scenario, so starting each one at the same
    minute keeps dependencies, owners, locks and capacity satisfied. Mutates `scheduled`.
    """
    _, previous = longer
    if not (scheduled["complete_schedule"] and previous["complete_schedule"]):
        return False
    before = {entry["id"]: entry for entry in previous["schedule"]}
    own = {entry["id"]: entry["end_minute"] for entry in scheduled["schedule"]}
    if own.keys() != before.keys() or all(own[task_id] <= before[task_id]["end_minute"] for task_id in own):
        return False
    replayed = []
    for entry in previous["schedule"]:
        minutes = estimates[entry["id"]][scenario + "_minutes"]
        replayed.append(dict(entry, end_minute=entry["start_minute"] + minutes, estimated_minutes=minutes))
    scheduled["schedule"] = replayed
    horizon = max((entry["end_minute"] for entry in replayed), default=0)
    scheduled["scheduled_horizon_minutes"] = horizon
    scheduled["estimated_makespan_minutes"] = horizon
    return True


def endpoint(task_ids, timing, field="end_minute"):
    values = [timing.get(task_id, {}).get(field) for task_id in task_ids]
    return max(values, default=0) if all(value is not None for value in values) else None


def resource_state(resource, tasks):
    verified_tasks = all(tasks[task_id]["status"] == "done" for task_id in resource["verify_task_ids"])
    if resource["observed_evidence"] and verified_tasks:
        return "verified_declared"
    if resource["action"] == "reuse":
        return "reuse_pending_verification"
    if all(tasks[task_id]["status"] == "done" for task_id in resource["provision_task_ids"]):
        return "provisioned_pending_evidence_or_verification"
    return "planned_or_in_progress"


def build_delivery_map(plan, ledger):
    result = empty_result()
    actual = build_plan(plan)
    if not actual["valid"]:
        result["errors"] = ["plan: " + problem for problem in actual["errors"]]
        return result
    result["project"] = plan["project"]
    validated = validate_ledger(plan, ledger, result)
    if validated is None:
        return result
    tasks, contracts, estimates, gates, prs, resources, milestones = validated
    result["valid"] = True
    result["assumptions"] = ledger["assumptions"] + result["assumptions"]
    result["actual_dispatch"] = {key: copy.deepcopy(actual[key]) for key in ("ready_now", "start_now", "not_ready_now", "resource_conflicts")}
    needed_contracts = {contract_id for task in tasks.values() if task["status"] != "done"
                        for contract_id in task["requires_contracts"]}
    draft = [contract_id for contract_id, contract in contracts.items()
             if contract["status"] == "draft" and contract_id in needed_contracts]
    conditioned = [contract_id for contract_id in draft if contract_id in gates]
    result["forecast"]["conditions"] = [copy.deepcopy(gates[contract_id]) for contract_id in conditioned]
    result["forecast"]["conditional"] = bool(conditioned)
    unresolved = [f"draft contract {contract_id} has no explicit gate" for contract_id in draft if contract_id not in gates]
    missing_runtime_evidence = {
        resource_id for resource_id, resource in resources.items()
        if not resource["observed_evidence"]
        and all(tasks[task_id]["status"] == "done" for task_id in resource["verify_task_ids"])
    }
    unresolved.extend(f"resource {resource_id} has completed verification tasks but no observed runtime evidence"
                      for resource_id in sorted(missing_runtime_evidence))
    timings = {}
    longer = None
    # Longest durations first: a shorter scenario may fall back on the next longer one's start times.
    for scenario in reversed(SCENARIOS):
        projected = copy.deepcopy(plan)
        for contract in projected["contracts"]:
            if contract["id"] in conditioned:
                contract["status"] = "stable"
        for task in projected["tasks"]:
            task["estimate_minutes"] = estimates[task["id"]][scenario + "_minutes"]
        scheduled = build_plan(projected)
        if not scheduled["valid"]:
            result["valid"] = False
            result["errors"].extend(f"{scenario} scenario: {problem}" for problem in scheduled["errors"])
            return result
        if longer is not None and replay_if_later(scheduled, longer, estimates, scenario):
            result["warnings"].append(
                f"{scenario} scenario reuses the {longer[0]} start times: the list heuristic finished a task later "
                "with shorter durations (scheduling anomaly), and those start times stay valid when tasks get shorter.")
        longer = (scenario, scheduled)
        complete = scheduled["complete_schedule"] and not unresolved
        timing = {task_id: {"start_minute": 0, "end_minute": 0} for task_id, task in tasks.items() if task["status"] == "done"}
        timing.update({entry["id"]: {"start_minute": entry["start_minute"], "end_minute": entry["end_minute"]} for entry in scheduled["schedule"]})
        timings[scenario] = timing
        result["forecast"]["scenarios"][scenario] = {
            "complete": complete,
            "makespan_minutes": scheduled["estimated_makespan_minutes"] if complete else None,
            "effort_minutes": sum(estimates[task_id][scenario + "_minutes"] for task_id, task in tasks.items() if task["status"] != "done"),
            "dependency_lower_bound_minutes": scheduled["critical_path_minutes"],
            "scheduled_horizon_minutes": scheduled["scheduled_horizon_minutes"],
            "schedule": scheduled["schedule"], "unscheduled": scheduled["unscheduled"],
            "blocking_conditions": unresolved.copy(),
        }
        if scenario == "likely":
            result["warnings"].extend(scheduled["warnings"])
    result["forecast"]["scenarios"] = {scenario: result["forecast"]["scenarios"][scenario] for scenario in SCENARIOS}
    for pr in prs.values():
        item = copy.deepcopy(pr)
        item["ready_declared"] = all(tasks[task_id]["status"] == "done" for task_id in pr["task_ids"])
        item["forecasts"] = {scenario: {"ready_at_minutes": endpoint(pr["task_ids"], timings[scenario])} for scenario in SCENARIOS}
        result["pull_requests"].append(item)
    for resource in resources.values():
        item = copy.deepcopy(resource)
        code_tasks = {task_id for pr_id in resource["pr_ids"] for task_id in prs[pr_id]["task_ids"]}
        item["task_ids"] = sorted(code_tasks | set(resource["provision_task_ids"] + resource["verify_task_ids"]))
        item["actual_state"] = resource_state(resource, tasks)
        item["forecasts"] = {}
        for scenario in SCENARIOS:
            provisioned = (0 if resource["observed_evidence"] else None) if resource["action"] == "reuse" else endpoint(resource["provision_task_ids"], timings[scenario])
            verified = None if resource["id"] in missing_runtime_evidence else endpoint(resource["verify_task_ids"], timings[scenario])
            item["forecasts"][scenario] = {"provisioned_at_minutes": provisioned, "verified_at_minutes": verified}
        result["resources"].append(item)
    for milestone in milestones.values():
        item = copy.deepcopy(milestone)
        item["complete_declared"] = all(tasks[task_id]["status"] == "done" for task_id in item["task_ids"])
        item["forecasts"] = {scenario: {"at_minutes": endpoint(item["task_ids"], timings[scenario])} for scenario in SCENARIOS}
        result["milestones"].append(item)
    for task_id, task in tasks.items():
        item = copy.deepcopy(task)
        item["estimates"] = {scenario + "_minutes": estimates[task_id][scenario + "_minutes"] for scenario in SCENARIOS}
        item["pr_ids"] = [pr_id for pr_id, pr in prs.items() if task_id in pr["task_ids"]]
        item["resource_ids"] = [resource["id"] for resource in result["resources"] if task_id in resource["task_ids"]]
        item["forecasts"] = {scenario: timings[scenario].get(task_id, {"start_minute": None, "end_minute": None}) for scenario in SCENARIOS}
        result["tasks"].append(item)
    result["warnings"] = list(dict.fromkeys(result["warnings"]))
    return result


def cell(value):
    if isinstance(value, list):
        value = ", ".join(str(item) for item in value) or "—"
    return html.escape(str(value), quote=False).replace("\\", "\\\\").replace("|", "\\|")


# Rendered text by language. Only the Markdown changes; --json keys and enum values stay language-neutral.
TEXT = {
    "en": {
        "title": "# Delivery map", "project": "Project: ", "invalid_input": "invalid input", "errors": "## Errors",
        "conditional": "conditional on the declared future gates", "unconditional": "based on the current declared states",
        "intro": "Forecast {condition}. Relative T+ times; optimistic / likely / conservative scenarios. No date or authorization is inferred.",
        "capacity": "## Capacity, effort and duration",
        "capacity_columns": ["Scenario", "Unique remaining effort", "Dependency lower bound", "Full duration", "Complete schedule"],
        "resources": "## Resources → PRs → tasks",
        "resource_columns": ["Resource / kind", "Action / environment / qty.", "PRs", "Tasks", "Owner", "Resource dependencies", "Provisioned O/L/C", "Verified O/L/C", "Declared state"],
        "prs": "## Planned and existing PRs",
        "pr_columns": ["PR / repository", "Title", "Owner / reviewer", "Tasks until ready", "Ready to merge O/L/C", "State / real URL"],
        "no_real_pr": "no real PR yet",
        "tasks": "## Tasks and estimates",
        "task_columns": ["Task", "Owner", "State", "Dependencies", "Effort O/L/C (min)", "Likely start", "Likely end", "Locks"],
        "milestones": "## Milestones",
        "milestone_columns": ["Milestone", "Tasks", "Forecast O/L/C", "Declared complete"],
        "dispatch": "## Actual dispatch now", "ready_now": "- Individually eligible: ", "start_now": "- Compatible set to start: ",
        "future_condition": "- Future condition {contract}: {condition}; tasks {tasks}.",
        "blocking": "- Blocker of the complete forecast: ", "unscheduled": "- Unscheduled {id}: {reasons}",
        "evidence": "## Expected and observed evidence",
        "evidence_columns": ["Resource", "Justification", "Expected evidence", "Observed evidence"],
        "assumptions": "## Assumptions and limitations", "warnings": "## Coordination warnings",
        "not_forecast": "not forecast",
    },
    "pt-BR": {
        "title": "# Mapa de entrega", "project": "Projeto: ", "invalid_input": "entrada inválida", "errors": "## Erros",
        "conditional": "condicionada aos gates futuros declarados", "unconditional": "baseada nos estados declarados atuais",
        "intro": "Previsão {condition}. Tempos relativos T+; cenários otimista / provável / conservador. Nenhuma data ou autorização é inferida.",
        "capacity": "## Capacidade, esforço e prazo",
        "capacity_columns": ["Cenário", "Esforço restante único", "Limite por dependências", "Prazo integral", "Agenda completa"],
        "resources": "## Recursos → PRs → tarefas",
        "resource_columns": ["Recurso / tipo", "Ação / ambiente / qtd.", "PRs", "Tarefas", "Responsável", "Dependências de recurso", "Provisionado O/P/C", "Verificado O/P/C", "Estado declarado"],
        "prs": "## PRs planejadas e existentes",
        "pr_columns": ["PR / repositório", "Título", "Owner / reviewer", "Tarefas até prontidão", "Pronta para merge O/P/C", "Estado / URL real"],
        "no_real_pr": "ainda sem PR real",
        "tasks": "## Tarefas e estimativas",
        "task_columns": ["Task", "Owner", "Estado", "Dependências", "Esforço O/P/C (min)", "Início provável", "Fim provável", "Locks"],
        "milestones": "## Marcos",
        "milestone_columns": ["Marco", "Tarefas", "Previsão O/P/C", "Concluído declarado"],
        "dispatch": "## Despacho real neste momento", "ready_now": "- Elegíveis individualmente: ", "start_now": "- Conjunto compatível para início: ",
        "future_condition": "- Condição futura {contract}: {condition}; tasks {tasks}.",
        "blocking": "- Bloqueio da previsão completa: ", "unscheduled": "- Sem agenda {id}: {reasons}",
        "evidence": "## Evidência prevista e observada",
        "evidence_columns": ["Recurso", "Justificativa", "Evidência esperada", "Evidência observada"],
        "assumptions": "## Premissas e limitações", "warnings": "## Avisos de coordenação",
        "not_forecast": "não previsto",
    },
}
LANGUAGES = tuple(TEXT)


def relative(value, text):
    return text["not_forecast"] if value is None else f"T+{value:.10g} min"


def scenario_times(item, key, text):
    return " / ".join(relative(item["forecasts"][scenario][key], text) for scenario in SCENARIOS)


def table(headers, rows):
    return ["| " + " | ".join(cell(item) for item in headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"] + ["| " + " | ".join(cell(item) for item in row) + " |" for row in rows]


def render_markdown(result, lang="en"):
    text = TEXT[lang]
    lines = [text["title"], "", text["project"] + cell(result["project"] or text["invalid_input"]), ""]
    if not result["valid"]:
        return "\n".join(lines + [text["errors"], ""] + ["- " + cell(problem) for problem in result["errors"]])
    condition = text["conditional"] if result["forecast"]["conditional"] else text["unconditional"]
    lines += [text["intro"].format(condition=condition), "", text["capacity"], ""]
    lines += table(text["capacity_columns"], [
        [scenario, f"{data['effort_minutes']:.10g} min", f"{data['dependency_lower_bound_minutes']:.10g} min", relative(data["makespan_minutes"], text), str(data["complete"]).lower()]
        for scenario, data in result["forecast"]["scenarios"].items()])
    lines += ["", text["resources"], ""]
    lines += table(text["resource_columns"], [
        [f"{r['id']} {r['name']} ({r['kind']})", f"{r['action']} / {r['environment']} / {r['quantity']}", r["pr_ids"], r["task_ids"], r["owner"], r["depends_on"], scenario_times(r, "provisioned_at_minutes", text), scenario_times(r, "verified_at_minutes", text), r["actual_state"]]
        for r in result["resources"]])
    lines += ["", text["prs"], ""]
    lines += table(text["pr_columns"], [
        [f"{pr['id']} / {pr['repository']}", pr["title"], f"{pr['owner']} / {pr['reviewer']}", pr["task_ids"], scenario_times(pr, "ready_at_minutes", text), f"{pr['state']} / {pr['url'] or text['no_real_pr']}"]
        for pr in result["pull_requests"]])
    lines += ["", text["tasks"], ""]
    lines += table(text["task_columns"], [
        [task["id"], task["owner"], task["status"], task["depends_on"], " / ".join(f"{task['estimates'][scenario + '_minutes']:.10g}" for scenario in SCENARIOS), relative(task["forecasts"]["likely"]["start_minute"], text), relative(task["forecasts"]["likely"]["end_minute"], text), task["locks"]]
        for task in result["tasks"]])
    lines += ["", text["milestones"], ""]
    lines += table(text["milestone_columns"], [
        [f"{item['id']} {item['title']}", item["task_ids"], scenario_times(item, "at_minutes", text), str(item["complete_declared"]).lower()]
        for item in result["milestones"]])
    lines += ["", text["dispatch"], "", text["ready_now"] + cell(result["actual_dispatch"]["ready_now"]), text["start_now"] + cell(result["actual_dispatch"]["start_now"])]
    for gate in result["forecast"]["conditions"]:
        lines.append(text["future_condition"].format(contract=cell(gate["contract_id"]), condition=cell(gate["condition"]), tasks=cell(gate["task_ids"])))
    likely = result["forecast"]["scenarios"]["likely"]
    for problem in likely["blocking_conditions"]:
        lines.append(text["blocking"] + cell(problem))
    for item in likely["unscheduled"]:
        lines.append(text["unscheduled"].format(id=cell(item["id"]), reasons=cell(item["reasons"])))
    lines += ["", text["evidence"], ""]
    lines += table(text["evidence_columns"], [[item["id"], item["justification"], item["expected_evidence"], item["observed_evidence"]] for item in result["resources"]])
    lines += ["", text["assumptions"], ""] + ["- " + cell(item) for item in result["assumptions"]]
    if result["warnings"]:
        lines += ["", text["warnings"], ""] + ["- " + cell(item) for item in result["warnings"]]
    return "\n".join(lines)


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def main(argv=None):
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("plan", help="task plan JSON, schema v1")
    parser.add_argument("ledger", help="resource/PR companion JSON, schema v1")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of Markdown")
    parser.add_argument("--lang", choices=LANGUAGES, default="en", help="language of the Markdown map; JSON is language-neutral")
    json_output = False
    lang = "en"
    try:
        arguments = parser.parse_args(argv)
        json_output = arguments.json
        lang = arguments.lang
        result = build_delivery_map(load_plan(arguments.plan), load_plan(arguments.ledger))
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        result = empty_result()
        result["errors"].append(f"Cannot build delivery map: {exc}")
    utf8_stdout()
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) if json_output else render_markdown(result, lang))
    return 0 if result["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
