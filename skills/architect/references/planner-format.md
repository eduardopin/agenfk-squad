# Plan format and planner usage v1

## Purpose

Validate the structure of a backlog and suggest a parallel execution schedule from declared dependencies and resources. The program does not run tasks, does not call LLMs, does not apply Terraform, does not change states and does not check that a test actually passed. Orchestration and evidence collection belong to the agent and to the environment's tools.

Requires Python 3.9 or later, standard library only.

```bash
python3 plan.py plan.example.json
python3 plan.py plan.example.json --json
python3 -m unittest -v test_plan.py
```

On Windows, use `python` or `py -3` depending on the installation. The three scripts write UTF-8 even with a cp1252 console or redirection, and run with `python -I`. Exit code `0` means structurally valid input, including when there are blockers and the schedule is partial. Code `2` means a read, JSON, schema or dependency error, or an inconsistent state. To require a complete plan, also check `complete_schedule == true`; to require tasks that can be dispatched now, check `start_now`.

## Top-level fields

| Field | Rule |
| --- | --- |
| `schema_version` | Optional; when present, integer `1`. |
| `project` | Non-empty name. |
| `max_workers` | Positive integer; total concurrency limit. |
| `contracts` | List of contracts; may be empty. |
| `tasks` | Non-empty list of tasks. |

Unknown fields are rejected to catch typos. Strings cannot contain control characters (C0 and C1), lone surrogates or the Unicode line and paragraph separators, because they break printing and table rows. They also cannot contain invisible format characters (Unicode category Cf: bidirectional overrides, zero-width space, byte order mark, soft hyphen, tag characters and the rest), which can reorder or hide text in a report; the zero-width joiner and non-joiner (U+200D, U+200C) stay allowed because real scripts and emoji need them. IDs use 1 to 128 letters, digits, periods, hyphens and underscores, starting with a letter or digit. Task IDs are unique among tasks; contract IDs are unique among contracts.

## Contracts

Each contract contains `id`, `version`, `status` and `owner`, all required. `status` accepts `draft` and `stable`. The version is non-empty text, preferably SemVer. `stable` is a declaration by the owner, which needs evidence outside this program.

Tasks reference contracts by ID. The applicable version is the one on the contract object in this snapshot of the plan. When changing a version, the agent must review compatibility, update the consumers and generate a new snapshot; this program does not compare versions or turn `draft` contracts into `stable` by inference.

## Tasks

| Field | Rule |
| --- | --- |
| `id`, `title`, `owner` | Identification, intended result and single owner. |
| `status` | `todo`, `in_progress`, `done` or `blocked`. |
| `depends_on` | IDs of the required dependencies; empty list when independent. |
| `writes` | Paths the task may change; empty list for an activity that does not write to the repository. |
| `locks` | Exclusive resources, for example `terraform:dev`, `environment:prod`, `db-migration-numbering`. |
| `requires_contracts` | IDs of contracts that must be `stable` to start. |
| `produces` | Non-empty list of the artifacts or results delivered. |
| `acceptance` | Non-empty list of the observable acceptance criteria. |
| `estimate_minutes` | Positive, finite number; for tasks in progress, the remaining time from now. |

All lists are required, even when empty. Duplicate values within a list are rejected. The program treats `produces` and `acceptance` as declarative text; it does not run content that looks like a command.

A `done` or `in_progress` task requires dependencies that are already `done`. Tasks in progress also require stable contracts, available capacity and no conflict among themselves. Inconsistencies in these declarations are errors, not automatic changes to the plan.

## Paths and resource exclusion

Use relative POSIX paths, with `/` even on Windows. Accepted forms are a literal target, such as `.github/workflows/ci.yml`, or a recursive prefix ending in `/**`, such as `infra/**`.

Absolute paths, `..`, `.`, empty components, backslashes, drive letters and arbitrary globs such as `src/*.py` are not accepted. `src/api/**` and `src/api-v2/**` do not overlap. `infra/**` and `infra/main.tf` overlap. As a precaution, a target declared as an ancestor of another also creates a conflict, since it may represent replacing the directory.

This check is lexical. It does not resolve symlinks, real permissions, repository identity or case differences between file systems. The executor must validate real paths and use isolated worktrees. In monorepos and in plans that cover several repositories, declare a consistent namespace for all paths; never mix paths from different roots without identifying them.

The same `owner` runs at most one task at a time. If two instances of a role work independently, record distinct identities, such as `backend-1` and `backend-2`, and keep the real owners during execution. A shared lock also prevents overlap, even without a file collision. Declare locks for Terraform states, environments, migrations and other exclusive external resources. Locks are declarative labels; the executor must acquire the real locks.

## Output and interpretation

| Field | Meaning |
| --- | --- |
| `errors` | Failures that prevent reliable scheduling. |
| `warnings` | Collisions between tasks without an explicit dependency, or schedule limitations. |
| `ready_now` | Tasks individually eligible in the current state, after considering work in progress. They may compete with each other for resources. |
| `start_now` | Subset that the heuristic would dispatch simultaneously now, without conflicts. |
| `not_ready_now` | Pending tasks and the reasons that prevent an immediate start. |
| `resource_conflicts` | Independent pairs that need serialization because of paths, locks or owner. |
| `schedule` | Estimated intervals, including tasks already in progress. |
| `unscheduled` | Tasks whose start could not be simulated, with reasons. |
| `critical_path_minutes` | Estimated lower bound considering only dependencies; ignores resources and external waits. |
| `estimated_makespan_minutes` | Estimated duration of the whole schedule; `null` when tasks are left to schedule. |
| `scheduled_horizon_minutes` | Horizon of the part that could be scheduled. |
| `complete_schedule` | Whether all remaining tasks could be scheduled. |

Priority uses the longest remaining work path to delivery, with a deterministic tie-break by ID. The schedule advances at each completion; a task can start as soon as its dependency and its resources are released, without waiting for a whole wave to finish. This heuristic does not prove that it found the optimal schedule.

A `draft` contract stays `draft`; a `blocked` task stays blocked. Even if a task has `produces: ["API contract"]`, the planner does not infer a status change. Update the plan only after the real review and run it again. `done` tasks stop consuming duration in the remaining calculation; their results must have been verified by the agent.

## Included example

`plan.example.json` is an illustrative snapshot taken after the minimum contracts were defined: `CONTRACTS` is already `done` and four contracts are `stable`. Application, frontend, platform, CI/CD and AI can start in parallel. The quality task starts when there is capacity; integration waits for the required artifacts and the prepared tests. No date or duration is a delivery commitment.

The AI track is optional in the product architecture. In this example it is included and is therefore part of the integration dependencies. For a product without AI, remove the task, the contract and the corresponding references; do not mark work that does not exist as completed.

The example declares criteria that a future executor must prove. The existence of these texts is not evidence of implementation or deployment.

## Compact package per task

Run `python3 scripts/handoff.py assets/plan.example.json APP-API` from the skill root. The output is compact JSON with the task, metadata of the required contracts, direct dependencies and return fields still empty. Optionally pass `--base-commit` with a full SHA of 40 or 64 characters; the program validates only the format and reports `base_commit_verified: false`.

The contract objects contain ID, version, status and owner. The schemas and the business spec are not embedded: `contract_payloads_included` stays false. The executor must load the relevant canonical definitions before writing. The handoff does not read paths cited in the plan or run their content, does not grant authorization and does not acquire real locks.

## Inventory and delivery forecast

The v1 schema of this planner stays unchanged. To link resources, PRs and three estimate scenarios, use the companion described in [delivery-map-format.md](delivery-map-format.md) and `scripts/delivery_map.py`. The map uses `plan.py` to compute each schedule and keeps real dispatch separate from the assumptions about future contract stability.

A valid schedule does not replace publishing the resource/PR inventory first, nor reconciling the diff before apply. Those steps belong to the Architect process and to the project state.
