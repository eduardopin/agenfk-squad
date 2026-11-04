# Delivery and installation planning

## Enter before implementation

Invoke Architect at the start of every app, API, stack or resource. Do read-only discovery, gather constraints and publish the resource → PR map before the first implementation mutation in scope. Include existing and shared resources with action `reuse`, in addition to `create` and `change`. Apply `references/resource-pr-planning.md` and the table template.

Record the baseline and the evidence of publication. This gate checks scope/completeness and communication; it does not request a new human approval. Continue under the authorizations already granted. For a small scope, one line/group and one PR with resource, policies, outputs and verification are enough.

## Prioritize outcome and reduce uncertainty

Choose the first flow that proves value and crosses the highest-risk boundaries. Compare value, urgency, uncertainty, effort and cost of delay; do not invent precise scores without data. Bring a short proof forward when it avoids expensive rework, without turning every unknown into a spike.

Separate code/IaC authoring, apply, deploy and verification, allowing overlap per task where contracts and risk permit. Do not impose global phases of "all infrastructure first" or "all development first". Define milestones by observable capability: PR ready to merge, local flow, integrated environment and runtime verified. Do not use a produced document or an estimated percentage as proof that the product is ready.

## Real capacity

Map who writes, reviews, integrates and operates. Set concurrency by the minimum of worker capacity, review capacity, budget and exclusive resources. Agents do not remove the time for human decisions or external access. Reserve explicit room for integration, operational documentation and fixing the risks found.

Use the critical path and the resource schedule as a diagnosis, not as a promise. Publish optimistic, likely and conservative scenarios with ranges/windows, unit, reference start and assumptions. Use a point value in the planner only to compare scenarios. Do not label percentiles without calibration. Do not hide external blockers in arbitrarily short estimates.

Forecast PR ready to merge and runtime verified separately, per resource, stack and product. Consider a PR ready to merge only after the planned implementation, checks and review are completed; this milestone does not prove merge or apply. Take into account dependencies, review queues, merge, provisioning and the relevant verification. Do not add up the lines' durations: resources can share a PR, task, work window or dependency. Compute milestones from the DAG and the real capacity; declare the deadline unknown when there is an external blocker with no horizon.

## Risk, dependency and readiness

For each blocker, record cause, evidence, impact, owner, unblocking action and which tasks remain free. A missing contract blocks consumers; it does not block all inspection, independent authoring or test preparation.

Define Ready per task: goal, published resource → PR line, relevant authorization, inputs, required contract, write area, validation and available tools. Define Done by the specific deliverable and evidence. Differentiate ready to start, code ready, ready to integrate, ready to deploy and runtime verified. A completed authoring task does not prove apply/deploy.

## Executable installation plan

Before recommending commands, check OS/architecture, runtimes, versions, licenses, registries, access, network, ports, secrets, volumes, backups, resources and cost. Consult the official documentation of the adopted version. Do not use a mutable remote command as an installer without review.

Produce an installation runbook with:

1. Published baseline of the map, resource/PR/task IDs and the authorization being reused.
2. Verifiable prerequisites and a mechanism to test each one.
3. Inputs and defaults, without secrets in plain text; source and owner of each secret.
4. Steps with action type, dependencies, command, directory and expected result.
5. Operations that can run in parallel and resources that require serialization.
6. Reconciliation between the planned inventory and the real diff before apply.
7. Idempotency, version/digest, resumption and detection of a partial installation.
8. Test of the minimal flow, recovery and cleanup of temporary resources.
9. Evidence of installation and runtime in the declared environment, when performed.

If there is no access, deliver reviewable commands and mark validation as pending. Do not claim that the runbook was tested because it was written. Do not generate a bootstrap that destroys existing files, changes state, disables controls or replaces configuration without a check.

## Reduce the time to first run

Reuse an existing image/runtime; prefer managed services when they fit the context; prepare registry, identity, network and secrets early. Use synthetic data and fakes for development. Avoid depending on production to test basic code.

Use build and dependency caching with appropriate invalidation and integrity. Keep relevant equivalence between local, CI and runtime without forcing identical infrastructure in all of them. Automate the repeated path after proving the first run, not before understanding its dependencies.

## Plan changes

When scope, contract, budget or access changes, compute the impact only on the affected tasks. Add unplanned resources and publish a new baseline before creating them; reconcile changes revealed by the provider before the apply. Record the difference, the justification and the impact on the three scenarios, milestones and critical path.

Keep valid decisions. Do not restart the whole backlog, do not duplicate issues and do not create a competing plan without identifying which baseline is canonical. Preserve history so the PM can compare forecast and result. Update URLs only when the PRs exist, and mark runtime verified only with operational evidence.

Update the user with the result, bottleneck, decision and next step. If the user asks for execution, continue until the authorized scope is complete or a real blocker remains, described with evidence.
