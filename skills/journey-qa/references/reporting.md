# Report, evidence, resume and integration

## Contracts and versions

| Document | Version | Schema |
| --- | --- | --- |
| `run-report.json` | 0.3 (0.2 remains valid) | `assets/schemas/run-report.schema.json` |
| `product-profile.json` | 1.0 | `product-profile.schema.json` |
| `scenario.json` | 1.0 | `scenario.schema.json` |
| `actor-card.json` | — | `actor-card.schema.json` |
| `session-binding.json` | 1.1 (1.0 valid) | `session-binding.schema.json` |
| `resume-checkpoint.json` | 1.0 | `resume-checkpoint.schema.json` |
| architecture review | 1.0 | `architecture-review.schema.json` |

`jqa validate FILE` detects the type and applies schema + semantic rules. Templates in `assets/templates/` are starting points, never executions.

Report 0.3 requires `host` (`name`, `version`, `model`, `adapter`) and `captured_by` on each evidence item, so that results from different models and hosts are comparable.

## Directory of a run

```
runs/<run-id>/
  product-profile.json  scenario.json  actor-card.json  session-binding.json
  run-report.json       resume-checkpoint.json
  trace.jsonl           # one event per command (actor, role, target, result, URLs, evidence, budget)
  evidence/
    ledger.jsonl        # evidence chain (chained hash)
    EV-0001-dom.txt  EV-0002-screenshot.jpg  …
  actors/<id>/PACKET.md act.mjs transcript.md outcome.json
  .jqa/                 # driver state, logs, saved storage (do not share)
```

`.jqa/` holds the driver token and, if saved, session state with cookies: never attach it to a report or version it (`init` creates the `.gitignore`).

## Evidence ledger

Each line of `evidence/ledger.jsonl`: `seq`, `evidence_id`, `captured_at`, `kind`, `origin`, `captured_by` (`driver`, `tool`, `agent`, `user`, `fixture`), `actor_id`, `url`, `summary`, `file`, `sha256`, `prev`, `hash`.

- `hash = sha256(prev + canonical content of the entry)`: removing, reordering or editing an entry, or changing the file, breaks the chain. `jqa evidence verify` points to where.
- `captured_by: driver` = captured by the driver itself at the moment of the action. `agent` = recorded by the agent (e.g. a screenshot from another tool). The report shows this difference.
- The ledger proves integrity from the record onward; it does not prove that the machine or the operator was honest.

## Closing the report

1. Fill in `run-report.json`: `title`, `host`, `execution_mode`, `environment`, `isolation`, `scope` (with `mandatory_rule_ids` fixed before the run), `run_status`, `stop_reason`, `actor_outcomes`, `qa_verdict`, `rule_checks`, `findings`, `interventions`, `limitations`. Reference evidence by ID.
2. `jqa report sync run-report.json` fills `evidence[]` with the referenced items from the ledger (hash, kind, time, actor) and the `ledger` block.
3. `jqa report validate run-report.json [--strict]` applies the rules below + cross-checks with the ledger. `--strict` turns warnings into errors.
4. `jqa report render run-report.json` produces self-contained HTML, with screenshots embedded.

### Verified rules

- Verdict: `failed` ⇔ a `failed` rule exists; `passed` requires an approved check, a non-empty mandatory scope and all mandatory rules `passed`/`not_applicable`; `inconclusive` requires a started evaluation; `not_evaluated` does not coexist with a substantive check; `blocked` does not approve.
- `required` of each rule = presence in `mandatory_rule_ids` (the scope does not shrink silently).
- A conclusion in a real run (`human_ui`, `instrumented`, `deterministic_regression`) requires `observed` evidence of an execution kind; a normative source alone does not prove behavior.
- `confirmed_defect` requires a matching `failed` rule and shared evidence.
- An actor outcome other than `undetermined` requires proof.
- `isolated` isolation requires `checked: true` and evidence; `single_actor` does not coexist with several actors.
- `redaction: pending` blocks.
- With a ledger: every `observed` evidence item of the report exists in the ledger with the same hash, kind, origin and time; the ledger is intact.

### Execution modes

| `execution_mode` | Use | Evidence accepted to conclude |
| --- | --- | --- |
| `human_ui` | Personas through the interface | `observed` from execution |
| `instrumented` | Interface + instrumented oracles (network, storage, API) | `observed` from execution |
| `deterministic_regression` | Deterministic script (a replayable regression run) | `observed` from execution |
| `static_review` | Analysis of supplied traces/artifacts | `observed` or `provided` |
| `synthetic` | Fully simulated exercise | `synthetic` only |

## State and resume

- `run_status`: `completed` (termination defined, including a legitimate refusal), `partial`, `blocked`, `aborted`, `error`; cause in `stop_reason`.
- `jqa checkpoint --actor ID --next "…" [--effect "order SIM-0001 created"] [--uncertain "op-42|confirm order|timeout|query history by op-42"]` writes `resume-checkpoint.json` from the trace and the ledger.
- `jqa browser actor save ID` stores the session state in `.jqa/state/ID.json`; `jqa browser actor add ID --restore` reloads it after restarting the driver.
- On resume: confirm authorization, the displayed identity and the current state; resolve uncertain operations by checking the effect before repeating; declare a continuity break if the session was lost.

## Integration with CI, cards and AgEnFK

- Keep the full JSON and the HTML as artifacts. Never convert `blocked`, `inconclusive` or `not_evaluated` into PASSED; if the tool accepts only binary, publish `failed`/`not approved` with the JSON attached.
- In projects with `product`, `tester` and `verifier` roles, hand confirmed defects to the tester for deterministic regression and let the verifier rerun `jqa report validate`. Preserve the existing flow's gates and authority; do not change code or gates without a request.
- Deterministic regression complements the journey; it does not replace it.
- Gate example: `jqa report validate runs/x/run-report.json --strict --json | jq -e '.valid and .qa_verdict == "passed"'`.
