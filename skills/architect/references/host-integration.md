# Integration with a host that has an infra skill, a planner and a card board

Read this when the host has an infra skill (one that creates or changes cloud resources as a PR), the `product` agent and a card board. Without them, this file does not apply.

## Who does what

| Step | Owner | Output |
| --- | --- | --- |
| Resource → PR table, contracts, `plan.json`, `resource-plan.json`, forecast | Architect, in the main session | `docs/architect/` in the product repository, uncommitted while it is only a plan |
| Card for the planning itself | main session, under the host's board rule, without asking | the gatekeeper allows writing in `docs/architect/` |
| Cards | the `product` agent, with the `docs/architect/` path in the brief | one card in TODO per planned PR |
| Dispatch order | the `parallel-plan` skill (read-only waves from the cards' dependencies and touched files) | a forecast; nothing is dispatched by it |
| PR that creates or changes a cloud resource | the host's infra skill, one invocation per planned PR | PR in the repository its boundary rule picks, with module, tags and monthly cost in the body |
| Other PRs (app, CI, contracts) | the squad agent the front calls for, one worktree per card | PR through the host's PR skill |

The Architect does not create the PR cards or open a resource PR on its own: it delivers the plan, calls the `product` agent for the cards and the host's infra skill for each infra PR.

## Cloud resource rows

- In the table, the PR column of a `create` or `change` row names the infra skill as the route, for example `PR-04 (infra skill)`.
- The `repository` in `resource-plan.json` is an assumption until the infra skill applies its boundary rule. When it picks another repository, update the row, the PR and the dependencies, and present the table again before creating the resource.
- The monthly cost comes from the estimate the infra skill puts in the PR body. Before that estimate exists, the cost column says "not estimated"; do not invent a value.
- When the host applies infrastructure only through CI, apply after the merge for staging, or through the release for production. The provisioning task in `plan.json` stands for that CI run, not for a local command.
- One resource in an existing stack: the infra skill leads the whole change, and the Architect only adds the one-line table to the final answer (section 0 of `SKILL.md`).

## From plan to cards

The `product` agent creates stories under an EPIC and stops for human approval, and it never advances a card. Give it the id of the work's card and the `docs/architect/` path. It reads the files, runs `delivery_map.py` read-only and creates one card per `PR-NN`, with resources, tasks, acceptance criteria, dependencies between cards and the O/L/C range labelled as an estimate. From then on, dispatch order follows the card dependencies: run `parallel-plan` on the card export for the waves, not the waves of `plan.json`.

When the plan changes (a new resource, a repository moved by the infra skill, a PR split), regenerate the map and ask the `product` agent to reconcile the affected cards. Cards of unaffected PRs do not change.
