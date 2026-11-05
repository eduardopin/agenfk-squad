# AGENTS.md: <project>

<One sentence: what it is, for whom, and the rule that matters most.> Applies to
any agent (Claude Code, Codex, pi) and to people. `CLAUDE.md` only points here.

## Orientation (5 minutes)

| Read                    | Why                                             |
|-------------------------|-------------------------------------------------|
| `docs/gaps.md`          | the ranked work list: pick from here            |
| `docs/scoreboard.md`    | measured numbers; they outweigh any estimate    |
| `docs/architecture.md`  | modules, layers, who may import whom            |

## Gate (before every commit)

```bash
make ci        # formatting, lint, tests, scoreboard.py check --base origin/main and measure --no-write
```

## Touched → run

| Touched                   | Also run                                          |
|---------------------------|---------------------------------------------------|
| `<path/**>`               | `<command>`                                       |
| new endpoint or command   | `pytest tests/test_hostile_sweep.py`              |
| a scoreboard number       | `python3 scripts/scoreboard.py measure <id> && python3 scripts/scoreboard.py tighten <id>` |

## Hot files

Every front ends up editing these. Surgical edits, reread before editing,
never reformat; two fronts on the same file run in series.

- `<route/command registry file>`: only the line that registers the new module.
- `<central config file>`

New functionality goes in a **new module** registered on one line, not by
growing a shared file.

## How to see it working

```bash
<command that starts the system headless / calls the endpoint / takes the capture>
```

An interface change is only done once the capture has been read. A green build
is not visual evidence.

## Never

- <domain rule that fails a change on the spot, with the reason>
- Loosen a scoreboard `floor` or `ceiling` to pass (CI fails it with `check --base`).

## When you finish

Update the `docs/gaps.md` entry the work touched (evidence, estimate, status
line) and, if it moved a number, the scoreboard: in the same commit.
