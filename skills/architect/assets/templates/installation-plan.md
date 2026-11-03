# Installation and verification plan

Status: draft, not executed.
Project/version:
Environment/account/region:
OS and architecture:
Owner:
Executing agents and reviewers:
Authorization scope:
Resource→PR baseline and publication evidence:
Resource IDs / PR IDs / task IDs:
Reference to the companion map and schedule:

## Published inventory and forecast

- Resources create/change/reuse, quantities per environment and shared dependencies:
- Planned PRs and real URLs; keep `null` while they do not exist:
- Milestones for PR ready to merge (implementation, checks and review complete) and runtime verified:
- Optimistic/likely/conservative scenarios, ranges/windows, unit and start:
- Execution/review capacity, critical path and assumptions:
- Existing authorizations reused and specific blockers:

Publish before the first implementation mutation; do not wait for new approval because of this table. Fit the size to the scope. Compute the forecast from the DAG and the exclusive resources, without adding up rows.

## Prerequisites

| Item | Version/limit | How to verify | Source/date | Observed status |
| --- | --- | --- | --- | --- |

## Configuration

| Input | Type/default | Source | Secret reference? | Owner |
| --- | --- | --- | --- | --- |

## Steps

| Task ID / resource / PR | Type authoring/apply/deploy/verification | Result and environment | Dependencies/contracts | Command/directory | Re-run | Executor/reviewer | Expected evidence |
| --- | --- | --- | --- | --- | --- | --- | --- |

## Reconciliation before apply

- Planned inventory and groups whose expansion depends on the provider:
- Revision, environment and evidence of the observed plan:
- Resources/addresses, actions and quantities revealed:
- New items, drift, replacements/removals and impact:
- Baseline updated/published before applying differences:
- Relevant locks, state, backup and authorization:

## Parallelism

- What can start together:
- Exclusive files/states/resources:
- Where to integrate:
- Application against mocks while infrastructure/CI move forward:
- Local preconditions for apply/deploy and real verification:

Avoid global phases; release tasks by contract, authorization and capacity. Read-only discovery stays free; creating an item that was not forecast waits for its inclusion in the published baseline.

## Minimal user flow

- Data preparation:
- User action:
- Expected result:
- Relevant failure:
- Observability:

## Resume and recovery

- Partial installation:
- Rollback/rollforward:
- Data/backup/restore:
- Cleanup of temporary resources:

## Actual result

- Commands run and environment:
- Commit/digest:
- Evidence:
- Not executed:
- Blockers:
- PRs ready to merge versus resources with runtime verified for real:
- Difference from the baseline and new milestone forecast:
