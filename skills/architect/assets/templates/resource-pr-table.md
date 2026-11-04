# Resources, PRs and delivery forecast

Project/product:
Scope and limits:
Baseline/date:
Previous baseline and difference:
Authority/existing authorizations:
Location and publication evidence:
Architecture/integration owner:
Linked companion map file and task plan:

Publish this table before the first implementation mutation of the scope. This record verifies publication and completeness; it does not ask for new human approval. For a small change, fill in one row/group and one coherent PR. `PR-01` is a planned ID; URL `null` means the PR does not exist yet.

## Inventory and resource→PR link

| Resource ID | Capability/resource or bounded group | Action create/change/reuse | Environment/account/region | Quantity and unit per environment | Shared/existing reference | PR ID | Task IDs | Dependencies/contracts | Owner agent | PR ready to merge milestone | Runtime verified milestone |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| R-001 |  |  |  |  |  | PR-01 |  |  |  | M-01 | M-02 |

Include reused dependencies. Bound groups and units; do not use a generic row to hide resources without a forecast. Link `reuse` to the consuming PR when relevant, without fabricating a PR only for an existing resource. Identify groups whose physical expansion only the provider will reveal.

## Delivery units per PR

| PR ID | Existing/proposed repository | Real URL | Result and included resources | Task IDs | Executing/consolidating agent | Reviewer | Real dependencies/external waits | PR ready to merge criterion | Runtime verified criterion |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| PR-01 |  | null |  |  |  |  |  |  |  |

One PR can deliver several resources. Declare a proposed destination as an assumption to confirm. PR ready to merge means the planned implementation, checks and review are complete; it does not mean merge, apply, deploy or confirmed runtime.

## Milestone forecast

Start reference, time unit, timezone/calendar:
Concurrent execution, review and integration capacity:
Assumptions about access, quotas, budget and external availability:

| Milestone ID | Product/stack/resource covered | Expected state | Required PRs/tasks/verifications | Optimistic: range/window | Likely: range/window | Conservative: range/window | Assumptions/risk | Critical path/limiting resource |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| M-01 |  | PR ready to merge |  |  |  |  |  |  |
| M-02 |  | Runtime verified |  |  |  |  |  |  |

Estimate from the DAG and the capacity; do not add up the rows. Scenarios express uncertainty, not a promise or calibrated percentiles. If there is no horizon for an external dependency, mark the affected forecast as unknown and record the condition for re-estimating. The companion `resource-plan.json` and `delivery_map.py` use relative `T+` scenarios; converting to a calendar requires explicit availability. See `references/delivery-map-format.md` for the format that the script validates.

## Reconciliation before apply

| Group/resource ID | Planned inventory/quantity | Plan revision and evidence | Observed addresses/actions/quantities | Difference and impact | Baseline republished before apply |
| --- | --- | --- | --- | --- | --- |

Record implicit resources the provider reveals, drift, replacements or removals. Add rows and update the forecast before the mutation that would materialize any item that was not forecast.

## Gate and update

- Proven publication and canonical baseline:
- Coverage of resources, environments, quantities, dependencies and tasks:
- Next mapped mutations and the matching authorization:
- Contracts/capacity/locks needed for the released set:
- Local gaps or blockers; fronts that remain free:
- Changes since the previous baseline and justification:
- Next forecast review by event/evidence:

Do not mark publication from the mere creation of the file. Preserve history; fill in the real URL and operational state only after observation.
