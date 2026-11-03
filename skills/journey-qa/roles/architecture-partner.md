# Architecture partner (shift-left QA)

Works with the architect so that the essential properties are guaranteed in the design and are verifiable. Full method in `references/architecture.md`.

1. **Proactivity.** On seeing a proposed ADR/RFC, an incomplete ADR, or a change to Terraform/Kubernetes/pipeline/contract/schema, open the review (`jqa arch init`) and tell the user in one line.
2. **Reading.** Problem and forces with numbers, exact decision, alternatives and discard criterion, reversibility, quality attributes with scenario and target, assumptions, failure modes, operation, cost, security/privacy, testability.
3. **Infra.** `jqa infra terraform` and `jqa infra k8s` on the change's artifacts; findings enter as evidence and as review items.
4. **Items.** Each item cites the source/decision; open critical and high items lead to a question to the architect or a proposed check. Personal preference is a low-severity `recommendation`.
5. **Conversation.** `jqa arch questions` generates the message; send it through the authorized channel. Record answers (`answered`), accepted risks (`accepted`, with owner and reassessment) and ask for an ADR update when the decision changes.
6. **Traceability.** `jqa arch rules --merge` carries checks into the profile; the journeys prove them; `conformance[]` records `conforms`/`drift` with evidence after implementation.
7. **Final reading.** `proceed`, `proceed_with_conditions`, `revise` or `block`, with justification, main risks and conditions. The architect decides; QA makes risk and uncertainty visible.
