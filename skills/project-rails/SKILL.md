---
name: project-rails
description: Rails that let any agent (Claude, Codex, pi) work on a project without human coordination. AGENTS.md as the single source with a single gate, a "touched X, run Y" table, hot files and how to see the system working; a scoreboard generated from committed files, with floors that only rise (`scripts/scoreboard.py`, checked in CI and against the base); a ranked `docs/gaps.md` as the work list; and a hostile sweep that covers every new endpoint or command on its own. Use when preparing a project for several agents in parallel, when writing or reviewing AGENTS.md, when "how much is left" rests on an estimate rather than a measured number, when an agent loosened a threshold to pass, or when adopting the method in a new repository.
---

# project-rails: the repository coordinates, not the conversation

The method comes from a group of open-source repositories that merged close to
two thousand pull requests in under two weeks, written by the agents of dozens
of people who never talk to each other. They have no special agent. They have a
repository that tells any agent **what to pick up, how to prove it and what must
not get worse**, and scripts that fail whoever makes it worse. The squad already
handles who does what (agents, worktrees, merge queue, context); this skill
handles what the project hands to them.

**Principle:** a rule a machine can check becomes a check (script, lint, test,
CI); prose is only for what cannot be checked. Every prose rule an agent has
already broken is a candidate to become a script.

## 1. AGENTS.md is the source; CLAUDE.md points to it

Claude reads `CLAUDE.md`; Codex and pi read `AGENTS.md`. Keep only one:
`AGENTS.md` with the content and `CLAUDE.md` with the line
`Read [AGENTS.md](AGENTS.md) first.` (or the reverse through a symlink, if the
project already depends on `.claude/CLAUDE.md`). Template: `templates/AGENTS.md`.
Sections every squad agent looks for:

| Section | What it answers | Who uses it |
|---|---|---|
| Orientation | what to read, in what order, in 5 minutes | everyone |
| Gate | **one** command that runs everything before a commit | `developer`, `tester`, `verifier` |
| Touched → run | table of conditional checks by path | `developer`, `tester`, `verifier` |
| Hot files | the few files every front edits, and the rule for them | `github`, `developer` |
| How to see it working | CLI, endpoint or headless capture, with the exact command | `frontend`, `verifier` |
| Where the work is | `docs/gaps.md`, `docs/scoreboard.md` | `product` |
| Never | the domain rules that fail a change on the spot | `verifier` |

## 2. Single gate and "touched → run"

One target (`make ci`, `pnpm ci`, `scripts/ci.sh`) that runs formatting, lint,
tests and `scoreboard.py check`. Nobody needs to remember the list. Next to it,
the table of what is expensive and only applies when the path changes:

```markdown
| Touched                       | Also run                                     |
|-------------------------------|----------------------------------------------|
| `backend/src/providers/**`    | `pytest -m contract`                         |
| `frontend/**`                 | `pnpm build && pnpm ux:shots`                |
| `migrations/**`               | `scripts/verify-db.sh` (and it is an `infra` card) |
| new endpoint or command       | the hostile sweep (§5)                       |
| any scoreboard number         | `scoreboard.py measure <id> && scoreboard.py tighten <id>` |
```

## 3. Scoreboard with a ratchet (`scripts/scoreboard.py`)

The scoreboard is **generated** from committed files; it is never edited by
hand, and CI fails if the doc is stale. Limits move one way only.

```
scoreboard/metrics.toml    [[metric]] id, name, command, regex?, floor|ceiling, card?, timeout?, ignore_exit?
scoreboard/<area>.toml     area = "..."; [[item]] id, target, status = done|partial|missing, note (file:line), card
scoreboard/measured.json   written by `scoreboard.py measure` (values, measured_at, commit)
docs/scoreboard.md         written by `scoreboard.py render` / `measure` / `tighten`
```

```bash
python3 scripts/scoreboard.py measure             # runs the commands, records, fails outside the limit
python3 scripts/scoreboard.py tighten tests       # moves the floor up to the measured value; never loosens
python3 scripts/scoreboard.py check --base origin/main   # CI: doc up to date, recorded values within
                                                         # the limit, no floor dropped, ceiling raised,
                                                         # metric removed or way of measuring swapped
python3 scripts/scoreboard.py measure --no-write  # CI: measures again, writes nothing
```

- **CI runs both.** `check` verifies what was committed and measures nothing:
  a stale or hand-written `measured.json` would pass on its own.
  `measure --no-write` proves today's code still delivers the numbers.
- **Swapping the yardstick is loosening too.** Changing a metric's `command`,
  `regex` or `ignore_exit` fails `check --base`, even with the same limit
  (`echo 999` passes any `floor`). If the new measurement is honest, the owner
  approves it in that PR with `check --base origin/main --accept-measurement <id>`;
  that never lets a loosened limit through.
- **Copy the script into the project** (`scripts/scoreboard.py`, standard
  library only, Python 3.11+; an older Python gets exit 2 and a one-line
  message): CI cannot see `~/.claude`. The source lives in this skill.
- **Metric commands run in a POSIX shell** (`sh`, as a Makefile would): `;`,
  `&&`, pipes and `exit` mean what they mean there. On Windows, where the
  shell is `cmd.exe`, the script is not yet verified.
- **`floor`** for what should grow (tests passing, routes covered by the sweep,
  `done` items); **`ceiling`** for what should shrink (lint errors,
  `# type: ignore`, open critical bugs, p95 in ms, bundle size).
- **Use `regex`** when the output holds more than one number (`"(\\d+) passed"`);
  without it the last number of stdout counts. An ambiguous number (`1,820`,
  `3.14.4`, `.5`, `1_000`, `2024-01-05`, `3.21s`) or an infinite one is an error,
  never a piece of it. A command exiting ≠ 0 is a measurement error, not a
  number, unless `ignore_exit = true`.
- **`tighten` rewrites line by line**: each `[[metric]]` with `floor = N` or
  `ceiling = N` on a line of its own. If it cannot rewrite, it exits 2 and writes
  nothing; it never says it tightened without having tightened.
- **Start from today's number**, not from the goal: `floor` = the measured
  value. The goal goes in the card's note; the ratchet prevents getting worse,
  and every card that improves the number runs `tighten`.
- A checklist status is `done` only if it is true on the trunk, with a
  `file:line` evidence `note`. When in doubt, `partial`. Judge behaviour, not
  the existence of a button; err downwards.

`check --base` turns into a machine the rule the `developer` already had in
prose ("no swapped thresholds"): loosening a limit to pass fails CI and is a
REJECT from the `verifier`.

## 4. `docs/gaps.md`: the ranked work list

One gap per entry, ranked by impact on the user, with evidence and an
estimate. It is where `product` cuts cards from: **take the highest one nobody
is working on**. It replaces ROADMAP, PLAN, STATUS and BACKLOG scattered at the
root; the ones that exist become links to it or go away. Template:
`templates/gaps.md`.

- Status line at the top: `Reviewed on · Updated on · Change`. Whoever closes
  part of a gap updates the entry and the line **in the same commit**.
- Estimates calibrated on history, not on a guess: real time of closed cards of
  the same kind (`agenfk list --status DONE --json`). State the basis.
- Mark **Human** what an agent cannot close on its own (account, contract,
  owner's decision, content).
- A number the scoreboard measures comes from the scoreboard, never rewritten
  here by hand.

## 5. Hostile sweep: the test that covers what does not exist yet

A test that **iterates the registry** (routes, commands, handlers, jobs) and
sends absurd input to each one, requiring a handled error (4xx, `Err`, a
message) and never a 500, a traceback or a crash. A new endpoint joins the
sweep without anyone writing a test. In FastAPI:

```python
# tests/test_hostile_sweep.py
import pytest
from fastapi.routing import APIRoute
from app.main import app

JUNK = [None, "", "x" * 100_000, -1, 2**63, [], {"__proto__": 1}, "../../etc/passwd", "' OR 1=1 --"]
ROUTES = [(r.path, m) for r in app.routes if isinstance(r, APIRoute) for m in r.methods]

@pytest.mark.parametrize("path,method", ROUTES)
def test_route_survives_junk(authenticated_client, path, method):
    url = path.replace("{", "").replace("}", "")  # path parameters become arbitrary text
    for junk in JUNK:
        r = authenticated_client.request(method, url, json=junk, params={"q": str(junk)[:2000]})
        assert r.status_code < 500, f"{method} {path} with {junk!r:.60} -> {r.status_code}"
```

Put the number of swept routes on the scoreboard as a `floor` metric: a new
route without coverage does not go unnoticed.

## 6. Parallelism through the architecture

The squad already separates fronts by worktree. What really reduces collisions
is the shape of the code:

- **New functionality in a new module**, registered on one line of the central
  list, rather than growing a shared file.
- **Hot files** listed in AGENTS.md: surgical edits, reread before editing,
  never reformat. `github` uses the list to decide series vs. parallel.
- **Layers checked by a script** when the project has layers (who may import
  whom): the rule stops depending on someone remembering it.

## Adopting it in a project

One AgenFK card per step, in this order (each step is useful on its own):

1. AGENTS.md with Gate, Touched → run and Hot files (`templates/AGENTS.md`).
2. A scoreboard with 2–4 metrics measured today, `check --base` in CI (an
   `infra` card). Start from `templates/scoreboard/`.
3. `docs/gaps.md` from what is already scattered around; archive the rest.
4. The hostile sweep and the swept-routes metric.

Do not bring over from those repositories: pushing straight to main, a stack of
hundreds of open PRs (their bottleneck is the merge, which the squad's queue
already solves), contributor credits and clean-room rules. Not worth it for a
single-script repository.
