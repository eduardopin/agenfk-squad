# Good first issue drafts

These are drafts for the repository owner to review and post manually. They
are intentionally small slices of items in `ROADMAP.md`, `CONTRIBUTING.md`, and
the current documentation. Check open Issues and Pull Requests before posting
any of them.

## 1. Add a regression test for an empty session report

**Suggested labels:** `good first issue`, `tests`, `session-context`

`ROADMAP.md` asks for more coverage around
`skills/session-context/scripts/session_context.py`. The empty-selection path
is a small place to start and should not require changing report behavior.

**Acceptance criteria**

- Add a test that runs the report against a throwaway home with no transcripts.
- Assert the exit status and the existing `no session in the selection` output.
- Do not read or write the contributor's real Claude or Codex directories.
- Document the new test command next to the existing suite commands if it is a
  separate file.

## 2. Add the missing malformed-transcript regression test

**Suggested labels:** `good first issue`, `tests`, `robustness`

The `jsonl()` reader in `skills/session-context/scripts/session_context.py`
already skips malformed JSONL records and continues with the usable records.
That safe behavior is not covered by a focused regression test.

**Acceptance criteria**

- Build a temporary transcript with one valid record and one truncated record.
- Assert that `session_context.py` ignores the truncated record and still
  reports the usable record.
- Keep the production behavior unchanged unless the test exposes a regression.
- Keep the fixture synthetic: no copied session ids, paths, prompts, or project
  names.
- Run the existing watcher suite to prove the shared arithmetic still passes.

## 3. Add a macOS smoke-test report template

**Suggested labels:** `good first issue`, `documentation`, `macOS`

The roadmap says macOS is untested even though the hooks use POSIX file
locking and `get.sh` names macOS. A repeatable report is useful before changing
code.

**Acceptance criteria**

- Add `docs/community/MACOS_SMOKE_TEST.md` with the headings `Environment`,
  `Isolated install`, `Hook registration`, `Watcher check`, `Uninstall`, and
  `Results`.
- Under `Environment`, include fields for the macOS, Python, and shell versions;
  under each remaining check, include the exact command and an exit-status field.
- Use a throwaway `HOME` and `CLAUDE_CONFIG_DIR`; do not ask contributors to
  alter their normal Claude Code setup.
- Include the exact repository test commands and spaces for their exit status.
- Link `docs/community/MACOS_SMOKE_TEST.md` from the macOS item in `ROADMAP.md`,
  and verify that the new relative link resolves.

## 4. Document the terminal-adapter contract behind herdr

**Suggested labels:** `good first issue`, `documentation`, `architecture`

`ROADMAP.md` asks for tmux, Zellij, WezTerm, and kitty adapters. Before an
implementation, the current herdr calls can be reduced to a small interface:
identify a pane, determine whether it is ready, and deliver a line.

**Acceptance criteria**

- Trace only the current calls in `hooks/squad-switch.py` and
  `hooks/squad-wake.py`.
- Add a concise contract to `docs/architecture.md`: inputs, outputs, timeouts,
  and failure behavior.
- Mark open design questions as open; do not invent support for another
  terminal.
- Keep code behavior unchanged.

## 5. Add one language to the stop and resume prompt regexes

**Suggested labels:** `good first issue`, `internationalization`, `tests`

The two owner-prompt regexes accept English and Portuguese. The roadmap asks
for more languages, while warning that broad patterns create routing noise.

**Acceptance criteria**

- Propose one language and list the exact stop/pause and resume phrases before
  changing the regexes.
- Add positive tests for those phrases and at least two nearby phrases that
  must not match.
- Change only `ASKED_TO_STOP` / `RESUME` and their tests; do not translate the
  agent prompts in the same pull request.
- Document the added language where the bilingual behavior is described.

## 6. Add a 200k-window data collection worksheet

**Suggested labels:** `good first issue`, `documentation`, `research`

The watcher defaults were tuned with a 1M window. The roadmap asks whether the
absolute ceilings make sense at 200k, and needs comparable data rather than a
new guess.

**Acceptance criteria**

- Add `docs/community/200K_WINDOW_WORKSHEET.md` with sections named
  `Collection method`, `Observations`, and `Sanitization checklist`.
- Under `Collection method`, use a transcript whose model/window resolves to
  200k and record the value `session_context.py` passes to `handoff_math()`;
  alternatively, drive the watcher with `SQUAD_WINDOW=200000` and record the
  value it passes to `handoff_math()`. Do not present `SQUAD_WINDOW` as an input
  to `session_context.py`. Note that `session_context.py` resolves a Claude
  transcript to a window of at least 1M, so for Claude sessions use the watcher
  route; a transcript that really reports 200k comes from Codex.
- Give the `Observations` table these columns: window actually used, context,
  weighted burn, reread share, `handoff_math()` decision, timing (early/late),
  and usefulness.
- Copy the no-private-context requirements from `CONTRIBUTING.md` into the
  `Sanitization checklist`, and verify all relative links in the new file.
- Do not propose new defaults without collected rows.

## 7. Add a documented schema example for `metrics.jsonl`

**Suggested labels:** `good first issue`, `documentation`, `metrics`

`docs/watcher.md` lists the sub-session metrics, and the roadmap wants a report
that can reproduce the README table. A synthetic, field-by-field example makes
that later report easier to implement.

**Acceptance criteria**

- Add a `metrics.jsonl example` section to `docs/watcher.md` containing exactly
  one single-line, valid JSON object with the fields emitted by
  `subagent_stop()` in `hooks/squad-watch.py` and obviously synthetic values.
- Follow it with a field table that names every key written by `subagent_stop()`.
- State which fields are measurements and which are derived flags.
- Verify the example parses with `python3 -m json.tool` and that the repository's
  relative-link check passes.
- Add no new metric and change no runtime behavior.

## 8. Add a WSL smoke-test checklist

**Suggested labels:** `good first issue`, `documentation`, `WSL`

`get.sh` names WSL and `ROADMAP.md` says it should behave like Linux, but there
is no focused verification checklist.

**Acceptance criteria**

- Add `docs/community/WSL_SMOKE_TEST.md` with sections named `Environment`,
  `Prerequisites`, `Isolated install`, `Hook registration`, `Watcher check`,
  `Uninstall`, and `Results`.
- Under `Environment`, include fields for Windows, WSL, distribution, shell,
  and Python versions, explicitly excluding username and host name.
- Under each check, include the exact command, an expected result, and an
  exit-status field; use `get.sh` for the isolated install and uninstall paths.
- State that every command runs inside the WSL distribution, not PowerShell,
  link the checklist from the WSL item in `ROADMAP.md`, and verify the link.
- Report results as evidence; do not change support claims from one report.

## 9. Implement installer dry-run behavior with contract tests

**Suggested labels:** `help wanted`, `tests`, `installer`

`ROADMAP.md` lists `install.sh --dry-run` as missing. This is a feature, not a
starter-sized test addition: it needs a design decision about which planned
actions, conflicts, and exit statuses the dry run reports. The existing
installer tests already use throwaway homes, so the implementation and its
no-write contract can land together without making the suite red.

**Acceptance criteria**

- Implement the proposed dry-run mode and add tests that run it in a throwaway
  home and config directory in the same pull request.
- Assert that it reports the links/settings changes it would make and changes
  no files.
- Cover one destination occupied by a real file and assert that it is reported
  as skipped.
- Keep every committed revision green; if the test must land separately, mark
  it skipped with a reason until the implementation lands. Do not run the
  installer against a real home.
