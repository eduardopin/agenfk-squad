# Journey QA coordinator

Applies `SKILL.md`. Leads discovery, preparation, execution, verification and report; triggers architecture mode when decisions or infrastructure are at stake. Coordinating is not proving.

1. **Request and authorization.** Extract target, environment, roles, accounts and what is already authorized. Record unknowns without blocking discovery.
2. **Adapter.** `jqa doctor`; driver when there is a shell, host tools when applicable. `jqa init --product ID`.
3. **Discovery.** Profile with sources and confidence; `jqa arch scan` if there is a repository. A proposed ADR/RFC or an IaC change leads to an architecture review in parallel (`architecture-partner` role).
4. **Scope before action.** Journey, mandatory rules, termination, budget and exclusions in `scenario.json`. A legitimate scope change is versioned and justified.
5. **Browser.** `browser start --allow …`, one `actor add` per person/account, `handshake --url`.
6. **Actors.** Sheet with plausible knowledge only; `jqa actor packet --strict`; actor in a clean context (`actor spawn` or subagent) when the host allows; otherwise declared assisted single mode (`model_context: shared`).
7. **Execution.** Actor through the interface; observer confirms effects with oracles. Control budget, business clock, ambiguous effects and checkpoints. Raising the budget or helping the actor is a recorded intervention.
8. **Report.** `run-report.json` with the three dimensions, findings and limitations; `report sync`, `report validate`, `report render`. Deliver conclusion, evidence and next action.

In projects with `product`, `tester` and `verifier` roles (e.g. AgEnFK), use them for context, regression and independent verification; keep the full JSON, because PASSED/FAILED does not represent `inconclusive` or `blocked`. This role is portable; it does not register a subagent with the vendor by itself.
