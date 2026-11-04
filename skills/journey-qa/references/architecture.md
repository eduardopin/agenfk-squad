# Architecture mode: ADR, RFC, proposals and infrastructure

QA enters before the code so that the properties that matter are **guaranteed by the design and verifiable later**. The output is not a generic opinion: it is a structured review (`architecture-review.json`), questions that unblock decisions and checks that become journey rules.

## 1. When to enter (without waiting for an invitation)

- `jqa arch scan` finds an ADR/RFC/proposal with status **proposed**, or an accepted ADR with no consequences, no alternatives or no numeric targets.
- A change to Terraform, manifests/Helm, deploy pipeline, API/event contract, database schema.
- The journey reveals behavior that contradicts a recorded decision (drift).
- Incident, postmortem or SLO change.

When a trigger is detected during another task, tell the user in one line and produce the review in parallel; do not interrupt the journey in progress.

## 2. Flow

```bash
jqa arch scan .                                              # inventory of decisions and infra
jqa arch init --title "ADR-0007 asynchronous payment" --source docs/adr/0007.md --out runs/arch/adr-0007.json
terraform show -json plano.out > plan.json && jqa infra terraform plan.json --run runs/x
kubectl create --dry-run=client -o json -f k8s/ > k8s.json && jqa infra k8s k8s.json --run runs/x
jqa arch validate runs/arch/adr-0007.json
jqa arch questions runs/arch/adr-0007.json --out perguntas.md     # message to the architect
jqa arch rules runs/arch/adr-0007.json --merge runs/x/product-profile.json
jqa arch render runs/arch/adr-0007.json
```

`arch init` extracts the decision, alternatives, consequences, context and sentences with quality attributes; everything is a draft to confirm by reading the source.

## 3. Read the decision as QA

For each decision (`decisions[]`), answer with a source:

| Question | What to look for |
| --- | --- |
| What problem and what forces? | Context with numbers (load, latency, cost, incidents). Without numbers, the decision has no success criterion. |
| What exactly changes? | Components, contracts, data, owners. Ambiguity here becomes a defect later. |
| What alternatives and why were they discarded? | Explicit discard criterion; without it, the decision cannot be reassessed. |
| Is it reversible? | `one_way` (migrated data, public contract, vendor, financial commitment) requires more evidence and an exit plan; `two_way` accepts an experiment with rollback. |
| What quality attributes are in play? | Scenario stimulus → environment → response → measure (e.g. "at 3× peak, checkout p95 ≤ 2 s"). Without a target, record a `gap`. |
| What assumptions support it? | Vendor capabilities, volume, user behavior, dependency SLA. An unvalidated assumption becomes an item with `risk_if_false`. |
| How does it fail? | Failure modes from section 4, per dependency and per step. |
| How does it operate? | Observability, alarms, runbook, on-call, deploy, rollback, migration, cost. |
| How is it tested? | Representative environment, doubles/sandboxes, fault injection, correlation identifiers, state endpoints. Lack of these = `testability_requirement`. |
| What negative consequences? | An ADR that lists only benefits is incomplete. |

## 4. Failure modes by architectural pattern

| Pattern | Questions that often find defects |
| --- | --- |
| Asynchronous messaging | At-least-once delivery → end-to-end idempotency (key with the vendor, dedupe in the consumer)? Order per entity? Do the poison message and DLQ have a **terminal state** visible to the user and an owner? Outbox to publish with the transaction? Age of the oldest message as an SLI? |
| Synchronous calls | Timeout shorter than the caller's? Retry with backoff + jitter and a retry budget? Circuit breaker and degraded behavior? Retry storm during an incident? |
| Eventual consistency | How does the interface show "pending"? Maximum deadline? What can the user do meanwhile (repeating = duplicating)? |
| Cache | Invalidation, acceptable stale data, stampede, per-tenant key. |
| Database and migration | Expand/contract, locks on a large table, backfill, schema rollback, instance replace, RPO/RTO and a **tested** restore. |
| Multi-tenant | Isolation in query, cache, queue, storage, logs and exports; test with two tenants. |
| Multi-region / multi-AZ | Exercised failover, split-brain, data residency, cross-zone traffic cost. |
| Kubernetes | Requests/limits, probes, minimum replicas, PDB, spread, scale-to-zero and cold start, image by digest, secrets outside the manifest, securityContext. |
| IaC | Destroy/replace of a stateful resource, module blast radius, drift, state locking, wildcard IAM, public exposure, cost tags. |
| Delivery | Feature flag and kill switch, canary with an automatic rollback criterion, compatibility between versions during rollout. |
| Security and privacy | STRIDE of the change, least privilege, secrets and rotation, encryption, LGPD (legal basis, minimization, deletion), audit trail. |
| Cost (FinOps) | Unit cost per transaction/tenant, NAT/egress, logs, maximum scale, commitment (Savings Plans/RI) compatible with reversibility. |
| AI components | Offline evaluation with criteria, guardrails, fallback, cost per call, data sent to the vendor, prompt injection through external content. |

`jqa infra checklist [--topic resilience|data|security|observability|delivery|cost|governance]` prints the matching checklist.

## 5. Turn risk into verification

Every open `critical`/`high` item needs a `question_for_architect` or a `proposed_verification` (the validator requires it). Kinds:

| `kind` | Example | Where it runs |
| --- | --- | --- |
| `fitness_function` | CI policy: no payment workload with `minReplicas < 2`; no plan that destroys a database without approval | Pipeline |
| `journey` | A buyer double-clicks pay and reloads; history shows one order and one charge | Journey QA |
| `contract_test` | Same idempotency key → same charge | CI/staging |
| `load_test` | 3× peak with vendor latency injected; measure p95 until final state | Staging |
| `chaos_experiment` | Kill the worker between the charge and the commit | Staging |
| `security_test` | Tenant B cannot read A's resource through API, export or cache | Staging/CI |
| `monitoring_slo` | Alarm on the age of the oldest message and the confirmation rate | Production |
| `infra_check` | `jqa infra terraform` with no critical finding on the approved plan | CI |
| `review` | DLQ runbook reviewed with operations | People |

Link checks to rules with `linked_rule_id` (e.g. `ARCH-IDEMP`). `jqa arch rules --merge` creates those rules in the product profile; the journey scenario puts them in `mandatory_rule_ids`. This way the ADR's decision ends up proven (or refuted) by evidence in the ledger.

## 6. Severity and final reading

- **critical**: violates an essential property (money, third-party data, irreversible loss, security) with plausible probability.
- **high**: degrades the core promise or the operation with no defined mitigation.
- **medium/low/info**: improves quality, cost or clarity.

Reading (`summary.verdict`):

| Reading | When |
| --- | --- |
| `proceed` | No open critical/high (the validator refuses the opposite). |
| `proceed_with_conditions` | May proceed if the listed conditions are met (e.g. "idempotency key before go-live"). |
| `revise` | The proposal still does not guarantee the property that motivated the change, or essential information is missing. |
| `block` | There is an open critical item whose consequence is irreversible. |

Reversible decisions call for a light process: an experiment with rollback and metrics. Irreversible decisions call for evidence first: proof of concept, load test, exit plan.

## 7. Infrastructure: what the analyzers cover

`jqa infra terraform plan.json` (output of `terraform show -json`): destroy/replace of stateful resources (databases, buckets, queues, keys), ingress 0.0.0.0/0, public S3, IAM with wildcard action/resource, database that is public/unencrypted/without backup/without deletion protection/without final snapshot, EKS with a public endpoint and no logs, ALB without dropping invalid headers, resources without cost tags, new NAT and large instances.

`jqa infra k8s objetos.json` (JSON from `kubectl create --dry-run=client -o json`, `kubectl get -o json` or `helm template | yq -o=json`): image without a pinned version, literal secret in env or a versioned Secret, requests/limits, probes, privileged, privilege escalation, root, writable rootfs, hostNetwork/hostPath, minimum replicas (HPA and KEDA), scale-to-zero, PDB, spread, Recreate strategy, public LoadBalancer Service, missing namespace.

The findings are **static analysis of the artifact**: declared is not applied. Use them as evidence (`--run` writes to the ledger) and confirm in the environment when the decision depends on it. Nothing is applied, executed or changed.

## 8. Work with the architect

- **Partnership, not audit.** The architect decides; QA makes risk and uncertainty visible, with evidence and a proposal for a cheap verification.
- **Ready-made message:** `jqa arch questions` groups by severity, cites the source, asks the question that unblocks and shows how it will be verified. Send it through the channel the user indicates; do not contact people without authorization.
- **Record answers:** `status: answered` + `answer`; an accepted risk becomes `accepted` with an owner and a reassessment date in the conditions.
- **No blocking over preference.** Style, favorite tool and "I would do it differently" are `recommendation` `low`/`info`.
- **Close the loop:** when the answer changes the decision, ask for an ADR update (consequences, targets, alternatives) and update `traceability`.
- **Cadence:** review at the proposed status; conformance check after the first deploy to staging; reassessment when the main assumption changes (volume, vendor, SLO).

## 9. Conformance after implementation

For each decision, record in `conformance[]`:

- `conforms`: runtime evidence (journey, test, metric, infra inspection) shows the decision in force.
- `drift`: the system does something else. In the journey report, a `requirement_conflict` finding (or `confirmed_defect` if the derived rule fails), with both sources. The decision owner resolves it: update the ADR or fix the system.
- `not_verified`: no evidence yet; say which verification is missing.

A review never claims conformance without evidence (`conforms`/`drift` require `evidence_refs`).
