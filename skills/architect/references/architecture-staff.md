# Architecture and Staff practice

Apply this reference when choosing the system structure, reducing risks and unblocking delivery. Match the depth to the impact of the change.

## 1. Establish the problem and the constraints

- Identify user, flow, business outcome, owners and success criterion.
- Read the repository and the available artifacts before proposing replacements; record the revision consulted.
- Separate observed facts, explicit requirements, assumptions and decisions still open.
- Record expected volume, concurrency, geographic distribution and growth as ranges, with source and confidence.
- Classify data, isolation requirements, integrations, availability, latency, RPO and RTO.
- Record budget, deadline, the team's operational capacity and technologies already supported.
- Distinguish a mandatory constraint from a preference; do not invent a regulatory or scale requirement.
- Resolve unknowns that would change the choice with a short investigation; continue the fronts that do not depend on them.

## 2. Eliminate unviable alternatives before scoring

- Exclude options that violate mandatory requirements, the maximum budget or capabilities proven unavailable.
- Mark an unverified requirement as pending; do not offset its absence with a high score on another criterion.
- Normally compare two or three real alternatives, including the simplest sufficient solution.
- Consider a modular monolith, a managed service or an isolated function before introducing operational distribution.
- Choose microservices when autonomy, scale, isolation or change cycles justify their added cost.
- Evaluate AI only where a deterministic solution does not deliver the outcome adequately.
- Apply weights only to the viable alternatives, using the user's priorities and justifying approximations.
- Consider initial effort, integration, operation, evolution, failures, total cost and reversibility.
- Present the winner, the trade-offs and the concrete conditions that would change the recommendation.
- Treat the matrix as decision support; do not use decimal places to hide uncertainty.

## 3. Fix shared decisions

- Define domain boundaries, data ownership and who may change each contract.
- Identify decisions that two implementers could make in incompatible ways.
- Record those decisions before releasing dependent tasks: identity, API, events, state, errors and runtime.
- Leave reversible internal choices with the task owner, within the existing standards.
- Make consistency, transactions, idempotency, ordering and behaviour during unavailability explicit.
- Avoid circular dependencies and direct access to another domain's database without a recorded decision.
- Distinguish a dependency to implement from a dependency to integrate or deploy.
- Use contracts, fixtures and simulators to allow development while the real resources are prepared.
- Reserve real integration as evidence of its own; an approved mock does not prove the infrastructure.

## 4. Produce proportional documentation

- Create context and container views when they explain boundaries or dependencies; use components only where needed.
- Identify in each relationship who calls whom, through which contract and with which responsibility.
- Add a sequence for the highest-risk flow and a deployment view when it changes the decision.
- Write an ADR only for a decision that is relevant, expensive, risky, controversial or hard to reverse.
- Include in each ADR: context, options, decision, consequences, evidence and review trigger.
- Keep one canonical reference per subject; relate documents by IDs and links, avoiding divergent copies.
- Define quality attributes as measurable scenarios, with load, condition, threshold and verification method.

## 5. Evolve existing systems

- Describe the current behaviour first, and the difference needed to reach the goal.
- Preserve useful conventions and interfaces; justify deviations with evidence of cost or risk.
- Map consumers, deployment dependencies, persisted data and rollback points.
- Split migrations into observable slices, keeping compatibility during coexistence.
- For a shared schema, plan expansion, consumer updates and contraction as separate stages.
- Define one owner for migrations and their execution order; parallelise only compatible, isolated changes.
- Separate application rollback from data recovery; do not assume a destructive migration is reversible.
- Prioritise an integrated functional slice early, to reveal incompatibilities before expanding the scope.

## 6. Coordinate as Staff

- Optimise the team's outcome, removing repeated decisions, queues and unnecessary dependencies.
- Assign explicit ownership of files, contracts, resources and integration; do not centralise every local decision.
- Give each executor a goal, minimal context, limits, expected evidence and a way to return blockers.
- Choose specialists by risk and need; avoid calling in every role for every change.
- Make the critical path visible and anticipate access, quotas, registries, DNS, identity and test data.
- Record a blocker with cause, evidence and resolving action; keep the independent tasks moving.
- Do integration review on contracts and behaviour; consolidate related fixes into useful deliveries.
- Measure time to integration, waiting, rework, failures and cost; do not use the number of tasks or PRs as success.

## 7. Distinguish planning from execution

- If the request is for analysis, deliver decisions and a plan without claiming the system was changed.
- If execution is authorised and tools are available, prepare and carry out the changes within the permitted scope.
- Separate desired state, generated change, verified plan, applied resource and validated behaviour.
- Cite evidence for each operational claim: revision, command, result, environment and date.
- Do not turn architecture approval into unrestricted authorisation for costs, destruction or publishing.
- Reuse existing authorisations; request an additional decision only when there is a concrete need.
- If a tool or access is missing, deliver the executable material that is available and state precisely which step is blocked.

## Exit criterion

Deliver an understandable decision, the necessary contracts, a plan with real dependencies, an integration owner and verifiable criteria. State the remaining assumptions and the limits of the evidence. Consult `references/sources.md` for the adopted foundations.
