---
name: parallel-plan
description: Compute the waves of a plan - which AgenFK cards can run at the same time, which wait for which, the critical path and the cards kept apart by a shared file - from a `Depends on:` / `Touches:` / `Size:` block in each card's description, with the stdlib script `waves.py`. Use when ordering an epic or a long plan, before dispatching several cards from one plan, when a card closes and the next wave is needed, and on "what can run in parallel", "critical path", "o que dá para paralelizar".
---

# parallel-plan: waves from dependencies and touched files

Two rules decide a wave:

1. A card starts once every card it depends on is done.
2. Two cards that touch the same file or the same named resource never share
   a wave, even when neither depends on the other. Two worktrees writing one
   file meet again as a merge conflict.

Inside those rules, the cards on the longest remaining chain go first, so the
critical path is never the one left waiting.

## 1. The block on each card

```text
Depends on: a1b2c3d4 (consumes the billing schema), e5f6a7b8
Touches: src/billing/, docs/*.md, state:prod-network, contract:billing-api
Size: 2
```

| Line | Format | Matching |
|---|---|---|
| `Depends on:` | comma-separated ids, any unique prefix; text after the id is a comment | an unknown or ambiguous id stops the script |
| `Touches:` | comma-separated paths, directories ending in `/`, globs, or `kind:name` | paths overlap by equality, directory prefix or glob; `kind:name` only by equality |
| `Size:` | whole number, default 1 | weight on the critical path; 1 = one session |

The `scheduler` agent writes the block. Section 2 of `agents/scheduler.md`
says which dependencies are real.

## 2. Run

```bash
agenfk list --project <id> --json | python3 scripts/waves.py --parent <epic-id> -
python3 scripts/waves.py --json --max-width 3 --done a1b2c3d4 plan.json
```

| Option | Effect |
|---|---|
| `--parent <id>` | only the cards whose `parentId` starts with this id |
| `--max-width N` | at most N cards per wave (default 4, the squad's front ceiling) |
| `--done a,b` | treat these ids as done (merged, not yet closed on the board) |
| `--json` | waves, ready, critical path, conflicts, cards without `Touches`, as JSON |

The input can also be `{"cards": [{"id", "title", "depends_on", "touches",
"size", "status"}]}` for a plan that is not on a board yet.

Exit 0 prints the plan. Exit 2 names the problem: a `dependency cycle: a -> b
-> a`, an id that is not in the plan, an ambiguous prefix, bad JSON.

## 3. Read the output

| Section | What to do |
|---|---|
| `Wave 1 (ready now)` | brief these cards to the main session; `github` opens one worktree each |
| `Critical path (length N)` | the shortest the plan can take with any number of sessions; its cards get reviewers first |
| `Kept apart by a shared resource` | if the shared file is a changelog, registry or lockfile, one card owns it and the others hand back text |
| `No Touches line` | those cards were not checked for collisions; fill the line before dispatching them alongside others |

Run it again every time a card closes. A plan is a forecast; the waves after
the first one change as cards land.
