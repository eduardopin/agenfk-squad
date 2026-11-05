# State, resumption and efficiency

## Minimum persistent state

Keep `run-state.json` with run ID, goal, mode, base commit, decisions, assumptions, tasks, blockers, observed capabilities, authorization references and evidence. Also record the revision of the resource/PR inventory, where the table was presented, changes since the baseline and the reconciliation with the IaC plan. Use references, not credentials. Reconcile with the repository and the tools when resuming; the file may be out of date.

Transitions: `todo → in_progress → done` or `blocked`. `done` requires criteria and evidence; a declaration is not automatic proof. A scope change goes back to task/contract/plan. Do not reuse a test from an old commit without assessing whether it is still relevant.

## Loop control

Record the failure, attempt, command/action, redacted error, assumption, change and next experiment. After two attempts without new information/progress, stop that approach, narrow the assumption or choose an alternative. Continue on the independent fronts. Do not repeat PR creation, scaffolding or spec rewriting expecting a different result.

Escalate with a concrete question and prepared evidence. Example: a reviewable deploy plan waiting for a specific authorization. Do not hand the user an investigation that can still be done with the available tools.

## Budget and context

Record the time/cost/tool call limits provided. If there are none, use short checkpoints and review before expanding the investigation. Load context on demand; keep decisions, constraints and evidence in the summaries. Smaller models get one task, one contract and one definition of done at a time. Do not assume equal reliability across all models.

## Useful metrics

- Time to the first verified flow, compared with the project baseline.
- Lead time, blockers, rework per contract and integration conflicts.
- Forecast versus observed duration for PR ready, resource verified and product milestone; record the reason for the deviation.
- PRs with a useful delivery, instead of the raw number of PRs.
- Failures after deploy, recovery and regressions.
- Human/LLM/cloud cost per accepted delivery.
- For AI: quality per dataset slice, latency and cost per completed task.

Do not set universal speed targets without data. Compare plans under identical assumptions and calibrate later with observed durations.

## Controlled learning

Turn recurring problems into a candidate improvement to a template, test, contract or golden path. Keep the case, proposal and evidence. Review before publishing a new version of the skill/standard. Do not change global policies/defaults silently because of a single run.
