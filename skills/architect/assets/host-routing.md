# Architect routing in the host

This file holds an instruction for host integration. Its presence in the package does not modify `AGENTS.md`, `CLAUDE.md`, IDE configuration or another assistant's instructions, and does not prove that an automatic trigger is installed. Integrate it in the authorized destination, preserving the existing rules, and verify the behavior in the real host.

## Snippet to integrate into the existing instructions

```text
At the start of a request to create or evolve an app, API, stack or resource,
invoke the Architect skill available in this environment. If the host has no
skills mechanism, load its SKILL.md explicitly and only the references
needed. Do not assume tools, credentials, workers or permissions.

Do read-only discovery of the scope and dependencies. Before the first
implementation mutation, present a baseline of the resource→PR table: resources
create/change/reuse, including shared ones, quantities per environment,
planned PRs with local IDs and URL null until real, tasks, contracts,
dependencies, owner agents and reviewers. Show milestones for PR ready to merge and
runtime verified, optimistic/likely/conservative scenarios with assumptions, capacity
and critical path. For a simple change, one row/group and one coherent PR
are enough; one PR can bundle several resources.

Record evidence of publication and completeness. This gate does not request new
human approval: reuse the authorization in force and continue the covered actions.
Do not add up the durations of the rows or promise dates from uncertain estimates.
Do not invent PR links or deployment claims.

After publication, coordinate the other agents through contracts and ownership.
Release application development, infrastructure, CI and tests in parallel
when there is capacity, authorization and independence. Separate authoring,
apply, deploy and verification by local preconditions, without global phases.

Before creating an item that was not forecast, add it to the map and publish the
updated baseline. If the provider reveals implicit resources in the plan, reconcile
addresses, actions, counts, risks and forecast before apply. Preserve
history and continue independent tasks the plan already covers.

Update the PM/user through the authorized channel with results, baseline
changes, blockers and forecasts. PR ready to merge requires the planned
implementation, checks and review to be complete; it does not mean merge or apply.
Merge does not mean runtime verified either; declare operational completion only with evidence.
```

## Integration and verification

- In a host that reads `AGENTS.md`, incorporate the snippet in the appropriate repository scope, without replacing local instructions.
- In a host that reads `CLAUDE.md`, use that project's current instruction mechanism and reference the real path of the skill.
- In another assistant, integrate into the supported project/routing instructions; do not invent configuration or an auto-trigger key.
- When the host supports invocation by name, use the name that is installed. Otherwise, load the content through the authorized path.
- Verify with a small request: read-only discovery, table before the first mutation, planned PR without an invented URL and task released without redundant confirmation.
- Record the file/configuration changed and the observed result. If only this snippet was delivered, report "routing prepared, host integration pending".

Always apply the real policies and authorizations of the environment. This snippet guides the flow; it does not create access, bypass, approval, concurrent execution or installation by itself.
