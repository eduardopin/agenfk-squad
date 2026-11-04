# Parallelism with explicit contracts and resources

## Goal

Minimize the time to a correct delivery within cost and capacity limits. Maximizing the number of agents is a different goal and can make integration worse. Parallelize independent work; preserve real dependencies and invariants.

## Common plan before mutations

Start Architect with the app, API, stack or resource request. Read-only discovery can run in parallel freely. Before implementation mutations, publish a common resource → PR baseline with quantities/environments, `create/change/reuse` actions, tasks, dependencies, owners, reviewers and delivery scenarios. Use `references/resource-pr-planning.md`.

Treat publication/completeness as a traceable gate, without adding human approval. After it, release compatible tasks according to the contracts, authorization and capacity observed. Do not create a global barrier between development and infrastructure. A new line must enter the published baseline before the corresponding item is created; this does not block independent fronts that are already covered.

## Two kinds of dependency

- Information/contract: stabilizing the interface makes it possible to build against a mock or fixture.
- Execution: an image, applied schema, account, deploy or real result must exist before the action.

Make this distinction explicit in the task. Reserve `depends_on` for actual task prerequisites. `requires_contracts` points to stable interfaces. Writing Terraform does not need to depend on the implemented API; applying a migration needs the database and the sequence control.

Separate authoring, apply, deploy and verification when they have different preconditions. The application agent can implement against the contract while the platform agent writes IaC, CI prepares the pipeline and QA builds tests. Apply depends on the reconciled diff, access and authorization; deploy depends on the environment and the artifact; verification depends on the behavior actually activated. Do not use the completion of authoring as evidence of these steps.

Do not turn priority into a technical dependency. "Deliver the basic flow first" does not mean that an optional adapter can only be written after the cloud deploy. Record an order preference in the narrative plan or split plans by milestone; add an edge only when there is a real prerequisite or an explicit business gate. Test the counterfactual: if the dependency is late, could this task still produce its correct deliverable against the contract? If so, the edge may be artificial.

## Exclusive resources

The planner uses `owner`, `writes` and `locks` to avoid predicted collisions. Declare root files and resources outside the repo. Examples: `terraform:dev:payments`, `database:migrations:orders`, `release:prod:api`. Distinct Terraform states can contend for the same resource: represent that exclusivity explicitly.

Only literals and `/**` prefixes are supported in paths. Enumerate paths when needed. The script does not discover imports, hidden state, concurrent SQL, generated files or semantic risk. Review the plan and update `writes` at every scope change.

## Delegation

Pass the task, resource/planned PR IDs, published baseline, contracts/versioning, relevant spec, read/write paths, base commit, validations and budget. Do not pass the whole project to everyone. Reserve an integration owner. Identify a reviewer with available capacity; use independent review for high risk, without imposing double review on trivial tasks.

Reserve resources before starting and release them on completion/cancellation. In a distributed runtime, use a real atomic lease/lock and heartbeat; this package does not provide that mechanism. Two coordinators cannot dispatch the same task based on old snapshots. `ready_now` is not a safe list for simultaneous dispatch; use `start_now` as a proposal and confirm the real reservation.

## Git and PRs

Create an isolated branch/worktree per concurrent unit when supported. Do not let several agents write in the same working tree. Generated files and lockfiles need a single writer or exclusive regeneration at integration. Contract changes go back to their owner.

Integrate incrementally: finish a slice, update the base, resolve conflicts without discarding other people's work and verify the combined commit. Worktrees do not guarantee compatibility at merge. Define the order for migrations and deploys.

Concentrate a PR by capability or risk boundary. Bucket example: resource, required policies, outputs, integration in the consumer and the relevant validation. An empty PR is not a delivery. Do not create five PRs for the five files of the same change.

One PR can contain several resources and tasks; prevent each executor from opening a redundant PR. Define who consolidates the delivery unit. Use `PR-01` as the planned ID and URL `null` until a real PR exists. Distinguish waiting for review/merge from a technical dependency, and record both in the timeline according to their real effect.

## Schedule and limits

Use `plan.py` for static validation and an event-based heuristic schedule. The tool considers declared exclusive resources and dependencies; it does not globally optimize all combinations or predict tool latency. The critical path is a lower bound of the dependencies, not a committed deadline.

Link its output to `resource-plan.json`, following `references/delivery-map-format.md`, without adding unsupported fields to the `plan.py` schema. Use `delivery_map.py` for the relative `T+` scenarios; distinguish the real eligible dispatch from the forecast conditional on future contracts. Do not promote a draft contract to stable only to get a schedule.

Forecast product, stack and resource from the DAG milestones, exclusive resources, review and capacity. Do not add up table lines or treat the heuristic makespan as a guaranteed date. Record optimistic/likely/conservative ranges and assumptions; recompute the affected part when there is new evidence. Calendar dates require a real start and availability, which plain `T+` does not provide.

After each task, update state/evidence and recompute. A draft contract blocks consumers until a real review. Do not mark `done` because the estimated date has passed. A plan with blocked tasks does not make the delivery complete. If `complete_schedule` is false, the complete makespan is unknown, even if part of the work can start.

## Example

With stable HTTP/runtime contracts, release API (`services/api/**`), UI (`apps/web/**`), IaC (`infra/**`) and CI (`.github/workflows/**`) if there are owners and capacity. AI evaluation (`evals/**`) can also start against a fake. Integrate the real runtime when the image/environment are ready; validate the flow when UI/API/data are integrated.

Many tasks editing the same file or waiting for the same approver reveal coupling. Fix the boundary or concentrate the work in that owner; do not hide collisions by removing paths from the plan.
