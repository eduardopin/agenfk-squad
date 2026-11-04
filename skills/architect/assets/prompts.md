# Entry prompts

## New project
Use $architect. Turn the idea below into a first working slice.
Read the repository and the available context; record constraints and assumptions.
Choose a proportionate architecture and golden path. Define minimal contracts,
create a backlog with dependencies, owners, paths and locks. Before creating resources,
present the complete table resource → planned PR → owner → dependencies
→ duration range. Show PR ready, resource verified and product milestone
separately; identify what can start in parallel. In this request,
produce the design and the reviewable artifacts.
Idea: [goal/user flow]. Repo: [local/link]. Constraints: [deadline/cost/stack].

## Authorized implementation in a development environment
Use $architect in execute mode in this repository and development scope.
First present the baseline of resources, PRs and delivery forecast.
Implement the slice [description], prepare infrastructure and CI/CD in parallel
where contracts and permissions allow. Preserve existing instructions and
changes. Reuse authorizations already established; ask only for what
is not authorized. Deliver the diff, evidence and real state.

## Predictability for the manager
Use $architect to plan the delivery of [app, stack or resource].
Record new, changed and reused resources; group PRs by capability.
Show the owner, the reviewer, the gates, the available capacity and the milestones.
Compute optimistic, likely and conservative scenarios with explicit assumptions;
do not turn a scenario into a promise or add up durations of shared tasks.
If a resource outside the inventory appears, update the table before creating it.

## Break a loop
Use $architect to analyze these attempts and the current state.
Identify the proven blocker, consolidate related changes and produce
the smallest useful verifiable delivery. Do not reopen decisions without new facts.
Files/logs: [references]. Desired result: [behavior].

## Staff review
Use $architect in review mode. Assess the requirements, domain, data, security,
operability and cost of this plan. Point out artificial and real dependencies,
missing contracts, collisions, migration risks and a simpler alternative.
Do not mark a proposal as validated without evidence.

## Portability
In assistants that do not discover Agent Skills, provide the whole folder as
context/project and ask it to read SKILL.md, then only the modules needed.
Access to files, terminal, cloud, permissions and multi-agent work depends on the assistant.
Do not paste only the skill name expecting it to be installed.
