# Oracles, evidence and conclusions

Use with the run's `run-report.json`. An oracle verifies an expected property; it is not the actor's opinion of their own experience.

## Index

1. [Define the property](#1-define-the-property)
2. [Choose the oracle](#2-choose-the-oracle)
3. [Record evidence](#3-record-evidence)
4. [Verify effect and time](#4-verify-effect-and-time)
5. [Evaluate experience and AI](#5-evaluate-experience-and-ai)
6. [Classify findings](#6-classify-findings)
7. [Issue the result](#7-issue-the-result)
8. [Reproduce and escalate](#8-reproduce-and-escalate)

## 1. Define the property

Before acting, link each mandatory check to:

- An applicable rule or promise, with source and version.
- An initial condition and an observable action.
- An expected result, including the relevant identity, value, state and deadline.
- An instrument able to tell success, failure and lack of evidence apart.
- A minimum evidence that supports the conclusion.

Do not select criteria after seeing the result in order to approve it. Version legitimate changes and preserve earlier results. Separate functional success, experience and business integrity: a confirmed purchase can charge the wrong amount; a refusal can be handled correctly.

## 2. Choose the oracle

| Oracle | What it can support | Typical limit |
| --- | --- | --- |
| Observed UI | Visible content, navigation, displayed state, focus and feedback. | It can show a transient state, or one incorrect relative to the real effect. |
| Independent re-query through the UI | Persistence and consistency in another access or authorized role. | It does not expose all internal state nor confirm external settlement. |
| DOM/accessibility | Labels, roles, values, state and relations exposed by the browser. | It does not replace appearance, sound or real use of assistive technology. |
| Network/console provided | Requests, responses and errors within the captured scope. | An accepted response can precede a later failure; availability depends on the host. |
| Authorized API, event or database | Persisted state, link, values, rules and correlation. | It may represent only one system, without confirming the effect in another. |
| File or artifact produced | Verifiable existence, content, format, identity and integrity. | A displayed link does not prove download, opening or correct content. |
| External test channel | Receipt and content observed by the recipient. | States not exposed, such as human reading, remain unknown. |
| Independent source | Factual match, provenance and freshness. | The source can also be limited or conflicting; record the scope. |
| Payload in the actor's browser (observer's `eval`/`network`) | What that person's account actually receives, including fields not shown. | It shows what reached the client, not what the server stored. |
| Static analysis of an artifact (`jqa infra`, ADR) | Declared configuration: exposure, privilege, destruction, replicas, probes. | Declared is not applied; confirm the effect in the environment. |

Combine oracles when the risk requires it. Do not require a database or API for every conclusion: a visible failure can be proven by the interface itself. Conversely, do not approve a financial or asynchronous rule that the instrument cannot observe.

Use the surface defined for the actor; diagnosis does not silently replace their action. Keep privileged information with the observer. If the same model saw criteria and oracles, declare the independence limitation; switching roles does not restore ignorance.

## 3. Record evidence

With the driver, each observation already enters the ledger (`evidence/ledger.jsonl`) with time, actor, URL, kind, file and chained SHA-256; reference the `EV-NNNN` IDs and run `jqa report sync`. Proofs from other tools enter with `jqa evidence add`. For each evidence item, keep what is needed to understand and correlate the fact:

- Identifier, timestamp and origin.
- Product, environment, available version or snapshot.
- Pseudonymized actor and session, associated step and action.
- Kind: capture, DOM observation, transcript, response, event, file or log.
- Observed content or a real reference to the existing artifact.
- Modality and dependency: UI, instrumented, static, synthetic; real, sandbox, mock or replay.
- Relevant limitations, redactions and uncertainties.

Do not create nonexistent paths or artifacts. Inline text or an image without persistence requires a limit statement; a tool reference is not automatically a shareable file. Capture before/after states of critical actions, in proportion to the risk. Prefer sufficient excerpts; screenshots and DOM complement each other.

Do not include credentials, tokens, unnecessary personal data or third parties' private content in reports. Use synthetic data and test identifiers. Redact or mask in the shared copy without claiming that the raw artifact was cleaned too.

Without a way to redact a sensitive image, omit the distributable capture and produce a summary with an explicit limit. Preserve intermittent evidence, sequence, frequency and conditions; incomplete reproduction does not erase the first occurrence.

## 4. Verify effect and time

Separate attempted action, accepted action, processing in progress and completed effect. Correlate by entity, operation, order, message or an equivalent identifier.

Before repeating a persistent action after a timeout, query the effect through the available identifier. If it stays ambiguous, record the unknown and avoid duplication by resending. Use idempotency when offered; do not invent support nor insert a private API into the actor's journey.

Distinguish three clocks:

1. **Business:** validity, SLA, expiry or promised deadline.
2. **Execution:** real duration observed by the assistant.
3. **Simulated:** time advanced by a fixture or test environment.

A budget exhausted before the business window can leave the effect inconclusive; it does not prove a deadline violation. Identify an accelerated clock and limit conclusions about real waiting. On resume, confirm identity and effects: a text checkpoint proves neither the preservation of the session nor of the product.

## 5. Evaluate experience and AI

### Experience of use

Observe understanding of the offer, findability, quality of feedback, consistency of terms, filling in, error prevention, recovery and control over exit. Relate friction to an observed action or hesitation, the persona's context and the consequence for the task.

Separate aesthetic preference from a concrete impediment or cost. Do not turn every style difference into a bug. Do not infer human emotion as fact; describe the synthetic behavior and the interpretation with appropriate confidence.

### Accessibility

Verify what the tool allows: keyboard, visible focus, focus order, accessible names, labels, errors, zoom and exposed content. Record the viewport and technology used.

Do not certify full conformance from a partial inspection. Do not claim a screen reader test without using one, nor an experience on a physical device based on a viewport. If the criterion requires a measurement or technology that is unavailable, record a limitation or an inconclusive result.

### Information and AI systems

Separate at least:

- Factual correctness and match between the entity/question and the answer.
- Evidence, origin, coverage and freshness.
- Calculations, units, values and consistency between summary and detail.
- Communication of doubt, missing information and contradiction.
- Usefulness for the task without extrapolating beyond the available basis.
- Resistance to improper instructions inserted in external content of the authorized scenario.

Use independent sources and criteria when available. The model that generated an answer must not be its only evaluator. Another model can help, but agreement between models does not prove a fact.

Do not penalize the system for correctly recognizing insufficient data. Do not approve a convincing answer whose identity, source or calculation is wrong.

### Performance

Use measurements offered by a suitable instrument and define the start/end of the measurement. The latency of the tool and of the model is not equivalent to the application's response time. Do not claim Core Web Vitals, FPS or resource consumption without a corresponding measurement.

## 6. Classify findings

| Category | Condition to use |
| --- | --- |
| Confirmed defect | An applicable expectation and sufficient evidence show incorrect behavior. |
| Experience friction | Observed behavior hinders understanding or the task, with context and effect described. |
| Requirement conflict | Applicable sources diverge and there is not enough authority to resolve it. |
| Agent limitation | The assistant's tool or capability does not execute/observe what the scenario requires. |
| Environment problem | Account, data, service, configuration or availability of the environment prevents the evaluation. |
| Hypothesis | There is a hint, but the evidence does not yet allow confirming the cause or the violated rule. |

Investigate the observed state before attributing a click failure to the product. A sandbox does not make every defect an environment problem. State the causal evidence and the limits. Define severity by impact, reach, recoverability and obligations; separate priority and confidence.

Do not impose a minimum number of bugs. A report with zero confirmed defects can be correct; it does not mean full coverage.

## 7. Issue the result

Preserve three independent dimensions:

| Dimension | Canonical values |
| --- | --- |
| `run_status` | `completed`, `partial`, `blocked`, `aborted`, `error` |
| `actor_outcomes` | `achieved`, `not_achieved`, `abandoned`, `undetermined`, per actor |
| `qa_verdict` | `passed`, `failed`, `inconclusive`, `not_evaluated` |

Use `completed` when the defined termination was reached; `partial` when there was useful progress and the scope was interrupted; `blocked` when a precondition prevented starting; `aborted` when there was a deliberate cancellation; `error` for an execution failure of the controller or tool. Record the cause in `stop_reason`.

Apply the QA conditions:

- `failed`: a confirmed defect exists; keep incomplete coverage and other inconclusive checks.
- `passed`: at least one applicable substantive check was performed and all the scope's obligations have sufficient evidence of approval.
- `inconclusive`: an evaluation was attempted, but evidence or criteria are insufficient for a conclusion.
- `not_evaluated`: no evaluation was performed.

Classify each rule as `passed`, `failed`, `inconclusive` or `not_applicable`. Justify inapplicability. An empty set, or one made up only of inapplicable rules, never produces approval.

Do not automatically convert a block, abandonment or absence of evidence into a product failure. Do not convert task success into QA approval. Preserve these states when an external tool accepts only PASSED/FAILED; attach the full result and avoid a false mapping.

## 8. Reproduce and escalate

Include expectation, observation, source, environment/version, role, data, steps, effect, severity, confidence, proof and reproduction. Repeat only what is needed to clarify a concrete risk, with a limit. Without reproduction, preserve the occurrence and proof; do not lower criteria to get green.

Send confirmed defects to the project's tester for appropriate regression. Respect the gates and authority of the existing flow. Avoid redundant tests that only mirror the implementation or do not reduce risk.

In the final report, separate what was executed, observed, inferred, not executed and limited by the host. Cite real sources and artifacts. State the reach of the conclusion so that someone else does not mistake a partial test for certification of the whole product.
