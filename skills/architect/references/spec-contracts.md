# Specs, contracts and traceability

Use this reference to turn intent into units of work that can move forward independently and integrate with evidence.

## 1. Write the smallest sufficient spec

- Give the change a stable identifier and relate it to the code revision examined.
- Record problem, user, main flow, expected outcome, scope and explicit exclusions.
- Write observable requirements with IDs; separate functional behaviour from quality attributes.
- Identify the assumption, owner, impact and means of confirmation for each piece of information that is still uncertain.
- Describe empty states, failures, interruptions, duplicates and recovery when they affect the flow.
- Define acceptance criteria in terms of input, condition, behaviour and verifiable evidence.
- Reuse existing specs and standards; avoid creating another source of truth for the same subject.
- Detail first the slice that will be implemented; go deeper into the following ones when real decisions require it.

## 2. Publish the minimum contract that unblocks execution

- Identify producer, consumers, owner, version, compatibility, and valid and invalid examples.
- Choose the right format: OpenAPI, JSON Schema, Protobuf, event schema or language interface.
- Use versioned files that tools can verify when the environment allows.
- Define the change policy before depending on the contract; do not call a draft stable.
- Release tasks when their required inputs are defined, without waiting for every contract in the project.
- Keep the contract small; do not fix internal details that no consumer uses.

## 3. Cover the relevant interfaces

| Interface | Specify before depending on it |
| --- | --- |
| API | Operations, authentication, authorisation, schema, errors, pagination, idempotency, timeout and limits. |
| Events | Name and version, producer, payload, correlation key, delivery, duplicates, ordering, retries and discard. |
| Data | Ownership, keys, types, nullability, invariants, tenancy, retention, migration and recovery strategy. |
| Runtime | Image or artifact, ports, health/readiness, configuration, references to secrets, identity and resources. |
| IaC | Inputs, outputs, preconditions, defaults, module version, state, locks and owner of the apply. |
| AI | Input, structured output, evidence, tool limits, budget, latency, fallback and evaluation. |
| Observability | Trace/run/task IDs, operational events, metrics, classification, retention and removal of sensitive data. |

## 4. Decouple application and platform

- Define configuration names and expected types; allow local or simulated values during development.
- Specify how to obtain endpoints, identities and references to secrets from the infrastructure outputs.
- Allow the application to be implemented against fixtures while modules and pipelines are prepared.
- Distinguish generated IaC code, static validation, a plan against the real account and completed provisioning.
- Do not run two writers on the same state or shared resource just because the files are different.
- Define who produces the image, who publishes the artifact and who configures its consumption in the environment.
- Integrate a minimal version with a healthcheck before making the whole application depend on the final environment.
- Confirm real behaviour after integration: connectivity, identity, configuration, data and telemetry.

## 5. Design mocks and contract tests

- Derive fixtures and simulators from the same canonical schema that the consumers use.
- Cover the valid scenario, error, timeout, partial response and repetition where there is relevant risk.
- Version test data and use synthetic content when real data is not needed.
- Verify consumers against the contract and the real producer against the published obligations.
- Detect divergence between mock and producer; fix the source instead of adjusting only the test so it passes.
- Keep integration tests for properties that a contract does not prove, such as IAM and transactions.
- Identify in each result whether the evidence came from simulation, a local environment, staging or production.

## 6. Evolve contracts with impact control

- Apply the versioning policy the project has adopted and verify compatibility against the real consumers.
- Consider that a new field can break a strict consumer; do not assume compatibility just because a change is additive.
- Map tasks in progress, clients, schemas and affected environments before changing a shared interface.
- Keep versions during the transition and define a deadline or condition for deprecation.
- Publish one canonical change and update only the affected specs, tasks and tests.
- Revalidate affected consumers and invalidate old evidence when it no longer matches the contract.
- Document changes in semantics, limits and permissions even when the schema stays the same.

## 7. Connect requirement, work and proof

- Keep the chain `requirement → contract/decision → task → test/check → evidence`.
- Give each task a concrete deliverable, inputs, outputs, real dependencies and an exclusive write area.
- Record the start criterion and the completion criterion; do not complete a task just because a file was created.
- Separate information, implementation, integration and deployment dependencies, justifying the edges that are needed.
- Do not require one task per file; group changes that deliver a verifiable, reviewable outcome.
- Record tests not run and the reasons; never fill in forecast evidence as if it were observed.
- Verify coverage of critical requirements and detect tasks unrelated to the approved scope.
- Tie evidence to the revision, contract, environment and date so it can be repeated and audited.

## 8. Treat AI contracts as probabilistic boundaries

- Validate the output schema deterministically; evaluate semantic quality on separate reference cases.
- Define abstention or escalation when data, evidence or sufficient operational confidence is missing.
- Restrict sources and tools according to the scope; treat external content as data, not as authorisation.
- Define budgets, timeout, retry limit and idempotency of external effects.
- Evaluate retrieval and generation separately in RAG; test isolation between users and the absence of evidence.
- Fix the model profile, prompts, data and rubric scoring when comparing versions; record the variability across attempts.
- Do not mistake valid JSON, a score from another LLM or a saved checkpoint for task success.

## Exit criterion

Deliver traceable requirements, contracts with examples, tasks that can start and consistent tests. Show what depends on real infrastructure and which evidence is still missing. Consult `references/sources.md` for the adopted foundations.
