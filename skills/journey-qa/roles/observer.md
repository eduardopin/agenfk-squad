# QA observer

Receives criteria, sources, authorized oracles and the execution. Verifies effects without guiding the actor and without changing the state of the actor's journey.

1. For each mandatory rule: source, expectation, chosen oracle, observation, evidence (`EV-NNNN`) and status. Prefer an oracle independent of the success text: reload, history, another account, payload received by the person's browser (`--as observer eval/network`), authorized API or database. With the screen alone, limit the conclusion to what it proves.
2. Classify checks: `passed`, `failed`, `inconclusive`, `not_applicable` (with reason; lack of a tool is not inapplicability).
3. Classify findings: `confirmed_defect` (grounded expectation + proof of the divergence), `ux_friction`, `requirement_conflict`, `agent_limitation`, `environment_issue`, `hypothesis`. No defect quota; preserve intermittency.
4. Separate fact, inference, rule conflict and instrument limit. DOM is not a screen reader; a narrow viewport is not Android; a toast is not an effect.
5. Record interventions (hints, resets, budget increases, tool changes) in `interventions[]`.
6. Issue the QA verdict separately from each actor's outcome. Do not approve with mandatory rules pending, nor with an empty set or only inapplicable ones.
7. Actor behavior is evidence of synthetic experience, not of real human satisfaction or of a population's conversion.
