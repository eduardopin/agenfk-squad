# Resource inventory and PR plan

## Mandatory, proportional entry

Invoke Architect at the start of every app, API, stack or resource change to make scope, deliverables and dependencies visible before implementation. Inspect code, inventory, configuration and available state read-only; this discovery does not depend on publishing the plan.

Before the first implementation mutation in scope, publish the resource → PR table and record its baseline. Producing or correcting the planning artifacts themselves is part of that preparation. The gate checks publication and traceable completeness; it does not create a round of human approval. Reuse the authorization in force and continue the actions it covers.

For a small change, use one line/group and one coherent PR. Do not turn a bucket into five PRs because there is a policy, outputs, integration and tests. One PR can deliver several resources; several PRs are justified only by independent capabilities, risk, ownership or a real integration sequence.

## Inventory the whole scope

- Cover application, APIs, data, queues, storage, compute, network, identities, secrets by reference, registries, pipelines, observability and temporary resources when relevant.
- Include the shared or existing resources required, marked `reuse`, instead of hiding dependencies because nothing is created.
- Mark each item with action `create`, `change` or `reuse`; separate a destructive item from the routine case and check the matching authorization.
- Identify each resource or coherent group with a stable ID, the product/capability served, the owner and explicit environments.
- State the quantity per environment and the counting unit. Do not use "dev/prod: several" or mix a logical group with a count of physical instances.
- Record the group boundary and dependencies. Do not assume that resources with the same name in different accounts are the same object.
- For `reuse`, record the existing reference, the owner and the availability/compatibility check. Link it to the consuming PR; reuse without a change of its own does not require an artificial PR.
- Link each line to the planned tasks and PRs, to the contracts needed and to the availability milestones.

## Plan PRs before a URL exists

Use local IDs `PR-01`, `PR-02` and so on. Keep the URL `null` until there is a real PR; the planned ID is not a remote number and does not prove that the PR was opened. Fill in the link when the tool returns the URL. Do not invent an issue, branch, review or link.

For each PR, state the repository, the capability delivered, the resources included, tasks, executing agent, reviewer, dependencies and responsibility boundary. If the target is still proposed, identify it as an assumption to confirm; do not claim that the repository exists. Record who integrates changes that span several PRs. Define the observable output and the evidence needed for review, without using a file count as a goal.

Separate at least these two forecasts:

- **PR ready to merge:** planned implementation, checks and review completed, with evidence; state any external pending items for merge or activation. This does not mean merge, apply or deploy.
- **Runtime verified:** change activated in the declared environment and operational criterion confirmed with evidence. This forecast includes the relevant dependencies, review/merge, capacity, provisioning and verification.

## Estimate to enable a product decision

Publish three deadline scenarios, optimistic, likely and conservative, as explicit ranges or windows in the same unit and with a start reference. State assumptions about access, agent capacity, review, integrations, quotas, budget and external dependencies. Distinguish execution effort from elapsed time.

Do not call the scenarios P50/P90 without history and calibration that support those percentiles. Do not promise the likely scenario's date as a commitment. When a dependency without a deadline prevents a complete forecast, mark the runtime as not estimable until it is resolved and show the parts that can still move forward.

Compute the product, stack and resource forecast from the DAG, the critical path, exclusive resources and the real calendar/capacity. Do not add up the durations of the table's lines or assume that they all start together. Identify milestones and record which PRs and verifications enable them; update the projection after the first relevant evidence.

## Publication and completeness gate

Before starting mutations, check:

1. Table accessible in the authorized channel or project destination, with baseline, date, scope and evidence of publication.
2. Resources and dependencies in scope covered, including `reuse`, environments, quantities and explicitly bounded groups.
3. Each resource linked to the tasks and PRs that create, change or consume it; each PR with an executor and a reviewer.
4. Dependencies, contracts, three scenarios, capacity assumptions and milestones documented.
5. Next mutations mapped and covered by the existing authorization; new or undefined items block only the implementation that depends on them.

Publishing means actually presenting the table and its version to the user/PM through the authorized flow; a private file that was not presented, or a `published=true` field written by the agent, does not prove communication. Use a real path, message or revision as the reference, without sending notifications to third parties outside the authorized scope.

After the gate is satisfied, dispatch the compatible set of authorized tasks. Do not wait for a new confirmation only because the table was published. If there is a specific authorization blocker, finish the useful preparation and continue the independent fronts.

## Reconcile inventory and diff before apply

Separate the **planned inventory**, with expected components and quantities, from the **observed plan diff**, with the addresses and actions the provider actually reveals. State in advance which groups depend on that expansion and the count assumption; do not claim a complete physical inventory before observing it.

Run inspection/plan without apply according to the capabilities and authorizations available. If the plan reveals a creation, change, replacement, removal or dependency that was not planned, reconcile the lines, counts, environments, PRs, risks and estimates. Publish the updated baseline before the apply that would materialize the difference. Do not use "the provider created it automatically" to skip this analysis.

Also compare drift and existing resources to avoid duplication. Update the recovery plan when the diff reveals impact on data or shared resources. Do not treat static IaC validation as proof of the diff of a real account.

## Update without losing the baseline

When you discover a new item, add its line and links before creating it. Record what changed, why, the impact and the new baseline; keep the previous one so the PM can compare. Keep IDs when the item's identity stays the same, and explain splits or groupings.

Update URLs and states according to real evidence; do not automatically turn an open PR, a merge or a `done` task into runtime verified. Replan only the affected part and preserve independent tasks. Use `resource-plan.json` in the companion format defined in `references/delivery-map-format.md`; `plan.py` remains the model of the tasks and of their schedule.

Run `python3 scripts/delivery_map.py plan.json resource-plan.json --json` when both files exist. Treat the output as a check of the declared data and a relative `T+` forecast, not as proof of publication, access or runtime. Convert to a calendar only with an explicit start, working hours, timezone and availability. Preserve the difference between the dispatch that is eligible now and the forecast that is conditional on future contracts.
