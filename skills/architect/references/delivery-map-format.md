# Delivery map: resources, PRs and forecast v1

## Interface

Keep `plan.json` in the planner's v1 schema. Create a companion `resource-plan.json` following the contract below. Publish the generated tables before the first creation or change of a resource. The script validates and projects the delivery; it does not grant authorization and does not apply changes.

```bash
python3 scripts/delivery_map.py plan.json resource-plan.json
python3 scripts/delivery_map.py plan.json resource-plan.json --json
```

Requires Python 3.9+ and `plan.py` in the same directory. Standard output contains Markdown or JSON. The Markdown map is in English by default; add `--lang pt-BR` to render it in Portuguese. The `--json` output does not change with `--lang`. Exit code `0` means valid input, including partial forecasts; `2` means an error. To require a complete forecast, check `forecast.scenarios.likely.complete`.

## Companion schema

Every field shown is required. Unknown fields, duplicate IDs or lists, and wrong types are rejected. Strings are non-empty, except `url`, which also accepts `null`.

```json
{
  "schema_version": 1,
  "project": "Exactly the project name from plan.json",
  "assumptions": ["Capacity, wait duration, scope and calendar considered"],
  "task_estimates": [
    {"task_id": "TASK-01", "optimistic_minutes": 20, "likely_minutes": 30, "conservative_minutes": 60}
  ],
  "contract_gates": [
    {"contract_id": "API", "task_ids": ["CONTRACT-REVIEW"], "condition": "Contract reviewed and accepted by the consumers"}
  ],
  "pull_requests": [
    {
      "id": "PR-01", "repository": "org/proposed-repo", "title": "Reviewable slice", "owner": "platform", "reviewer": "reviewer",
      "state": "planned", "url": null, "evidence": [], "task_ids": ["TASK-01"]
    }
  ],
  "resources": [
    {
      "id": "RES-01", "name": "Logical name", "kind": "Concrete resource type",
      "action": "create", "environment": "staging", "quantity": 1, "owner": "platform",
      "pr_ids": ["PR-01"], "provision_task_ids": ["APPLY-01"], "verify_task_ids": ["VERIFY-01"],
      "depends_on": [], "justification": "Reason to exist or to be reused",
      "expected_evidence": ["Observable proof of configuration and operation"], "observed_evidence": []
    }
  ],
  "milestones": [{"id": "M-01", "title": "Full flow verified", "task_ids": ["VERIFY-01"]}]
}
```

The snippet above shows the format and is not a complete plan: every task it cites must exist in the matching `plan.json`. `task_estimates` covers exactly all tasks, including completed ones. The lists of gates, PRs and milestones may be empty; the resource inventory must contain at least one item.

## Estimates and capacity

Each estimate contains positive, finite numbers with `optimistic_minutes <= likely_minutes <= conservative_minutes`. `likely_minutes` must equal the task's `estimate_minutes` in the base plan. These are three assumption scenarios; they are not percentiles or probabilities. Each scenario is scheduled separately, and the list heuristic can finish a task later when another one gets shorter. In that case the script reuses the start times of the next longer scenario, which stay valid with shorter durations, and records a warning in `warnings`. This way optimistic ≤ likely ≤ conservative holds task by task, and therefore for PRs, resources and milestones.

The planner applies the durations to the same dependencies, owners, locks and `max_workers`. Each owner still runs at most one task at a time. Completed tasks consume no remaining effort; for `in_progress`, use remaining minutes. Resources and PRs share the existing tasks: do not add effort again because a task appears in several lines.

Forecast time relative to now (`T+`), in minutes of continuous availability under the assumptions. Do not convert automatically into a date/time, business days or a commitment. Delays from review, quota, vendor, approval and credentials must appear in the estimates or as blockers. Waiting tasks also consume a slot in this version; declare that approximation when relevant. Real availability and locks belong to the executor.

## Planned PRs and real PRs

Use logical IDs in the format `PR-01`, `PR-02` and so on. `repository` identifies the target; if the repository does not exist yet, mark the proposed identifier as an assumption to confirm. A `planned` PR uses `url: null` and `evidence: []`. The `open` and `merged` states require an HTTPS URL and declared evidence of its existence/state. The script checks the format and does not query the provider; do not invent numbers, URLs or proof.

`task_ids` contains the work needed to make the PR ready to merge: implementation, checks and review. Do not include resource provisioning or verification tasks in that list. Declare review as a task with its own owner/lock when it affects capacity. The `reviewer` field identifies responsibility, but on its own it reserves no time.

The PR-ready forecast is the latest finish among its tasks. It does not claim that a merge happened. When the merge depends on a wait or a separate run, model that step in the DAG before provisioning. The `merged` state in the ledger is declared history and requires evidence; it is not inferred from the schedule.

## Inventory and dependencies

`action` accepts `create`, `change` or `reuse`; `quantity` is a positive integer. Each line identifies the environment, owner, PRs, provisioning, verification and evidence. `create` and `change` require at least one PR, one provisioning task and one verification task.

Each provisioning task must have all tasks of the associated PRs as ancestors in the DAG. Each verification task must have all provisioning tasks as ancestors. Citing IDs in the ledger is not enough. This rule makes it possible to verify the link between reviewable code and operational effect.

`reuse` does not need a PR or provisioning. Require a justification and expected evidence. If there is no `observed_evidence` yet, declare at least one verification task and treat the reuse as a pending assumption. Without verification tasks, require evidence already observed. Do not disguise a change as reuse; use `change` when the resource has to be modified.

Dependencies between resources use `depends_on` and must form a DAG. For distinct tasks, reflect the dependency's availability in the task DAG as well. Resources managed in the same apply task can share that task; in that case the internal dependency must be in the IaC, and it is an explicit assumption, not an ordering proven by the planner.

## Draft contracts: conditional forecast and real dispatch

`contract_gates` declares which tasks and which condition can make a contract stable in the future. A gate references an existing contract, contains existing tasks and cannot consume its own contract. Every not-yet-completed consumer of that contract must have all gate tasks as ancestors. A gate task keeps its original status and duration.

`actual_dispatch` always uses the original plan. A draft contract stays draft and prevents the real dispatch of its consumers. Only in copies made for the forecast are contracts with a valid gate considered available after the ancestor tasks. The report lists the conditions and labels that projection as conditional. Do not use the simulation as authorization or as a statement that a contract was approved.

A draft contract consumed by work not yet completed and without a gate, or an explicitly `blocked` task, prevents a complete forecast; the full duration is `null`. Draft contracts not used by remaining tasks neither block the forecast nor make it conditional. The executable part and the blockers stay visible. Stability must be updated in the real plan only after the corresponding review and evidence.

## Milestones and distinct states

The report separates PR ready to merge, resource provisioned, resource verified and product milestones. Custom milestones have an ID, a title and tasks; the estimated finish is the maximum of those tasks' finishes, without adding effort again.

Historical states are declarations of the plan/ledger. Resource verified requires the verification tasks completed, when they exist, and evidence observed in the ledger. A task marked `done` without runtime evidence is not enough to claim resource verified: in that case the complete forecast and the resource's verification stay without a deadline until the missing evidence is resolved. Projected relative dates remain projections.

## Limitations

The report knows only the declared inventory and tasks. It does not discover omitted resources, does not interpret Terraform, does not measure code maturity, does not validate URLs remotely, does not verify evidence externally and does not acquire locks. Quantity serves the inventory's scope; the effort must be in the tasks, not in an automatic multiplication by quantity.

The schedule heuristic is the same as in `plan.py`: it respects the declared capacity and starts tasks when their resources are released. It does not prove a global optimum. Changes to scope, contracts, capacity, approvals or resources require regenerating and republishing the map before the corresponding change.

To create the first repository or control plane, prefer a PR in a control repository that already exists. If there is none, record an explicit bootstrap exception outside this schema: publish the resource and its planned PR ID before the creation, record the owner, existing authorization, steps and expected evidence, and reconcile the real PR when the repository exists. Do not invent a PR that is already ready or a cyclic dependency to satisfy the validator. Validation of this companion covers the normal path, in which the tasks up to PR readiness can precede provisioning.
