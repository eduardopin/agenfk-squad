---
name: skill-trial
description: Test a skill or hook change with blind headless trials before shipping it - N fresh `claude -p` sessions in a sandbox repository with a stub gh, no cloud credentials, no user hooks and no MCP, each given the request an engineer would type, then a check per trial and one table of PASS/FAIL/N/A, cost and wall time. Use after changing anything that steers behaviour (a SKILL.md, a hook, a contract), before the pull request, and whenever a "test it in a new session" or "blind test" is about to be done by hand.
---

# skill-trial

A behaviour test of a skill or hook runs here, in minutes, from the session that
made the change. Never in a second interactive session someone has to open and
type into, and never with another session "coordinating" it.

Why: a live test session waits for questions and gets contaminated by whoever
coordinates it, while a headless trial cannot be asked anything and cannot be
coached.

## Run

The script lives in this skill's `scripts/` directory. Use `$CLAUDE_SKILL_DIR`
when the harness sets it; otherwise find the skill directory by name, or from
the path you read this `SKILL.md` at. The line below follows the symlink
`install.sh` makes, and falls back to the checkout when run from its root.

```bash
SKILL_DIR="${CLAUDE_SKILL_DIR:-$(cd "$(dirname "$(readlink -e ~/.claude/skills/skill-trial/SKILL.md || echo skills/skill-trial/SKILL.md)")" && pwd)}"
T="$SKILL_DIR/scripts/trial.sh"
$T --scenario "$SKILL_DIR/scenarios/smoke" --dry     # setup and check only, $0
$T --scenario <dir> --n 3 --plugin-dir <path to the plugin or repo under test>
```

Run the smoke scenario first: it proves your harness, `bwrap` and login work
before you spend money on a scenario of your own.

`--plugin-dir` loads the plugin from the working tree, so the trial runs the
change on the branch, not the installed copy. Defaults: 3 trials in parallel,
`claude-opus-5-5`, $3 cap per trial, output under `$SKILL_TRIAL_OUT` (else
`~/.cache/skill-trial/`). The report exits 1 when any trial FAILED.

A hook that `install.sh` puts into a Claude config directory is tried first the
way CONTRIBUTING.md describes, with `CLAUDE_CONFIG_DIR` and `HOME` pointed at
scratch folders. The trial itself starts from a clean environment and does not
pass `CLAUDE_CONFIG_DIR` through; it loads the change with `--plugin-dir`.

## Write a scenario (budget: 10 minutes)

A scenario is a directory with four files. `scenarios/smoke/` is a complete
one; copy it. Its README explains each file.

| File | Holds |
|---|---|
| `setup.sh <trial dir>` | builds `<trial dir>/repo` and whatever it pushes to (a local bare repo as the push URL). Runs once per trial |
| `prompt.txt` | the request in the words an engineer would use. Never name the skill, the hook or the expected behaviour |
| `gh.json` | the stub's answers: `[{"match": "<argv prefix>", "stdout": <text or JSON>, "exit": 0}]`. Unmatched calls fail and are logged |
| `check.sh <trial dir>` | exit 0 PASS, 1 FAIL, 2 N/A (never reached the behaviour), one line of reason |

A check reads evidence, never the answer's wording: `gh.log` (every gh call),
`scripts/commands.py stream.jsonl` (every Bash command the session ran), the
remote's refs against a snapshot taken at the end of setup, the files in `repo`.

Make the repository real enough to pass review. If a stack's files hold only
comments, the session may refuse to open any PR and never reach the hook. A
report with more N/A than PASS says the scenario is wrong, not that the skill
works.

## Read and ship

- Open two or three `stream.jsonl` files of different verdicts and read the final
  answer: the check proves what happened, the answer shows why.
- The report's table goes in the PR body under verification, as it is.
- A FAIL is fixed on the same branch and the trials rerun. A finding outside the
  change is one line of suggestion in the PR.

## Limits

- A trial runs with `acceptEdits` and Bash inside a bubblewrap jail: its own PID
  namespace and `/proc`, a private `/tmp` and `/run/user`, no `/mnt` except WSL's
  `resolv.conf`, HOME read-only with `~/.ssh`, `~/.aws`, `~/.config/gh`, `~/infra`
  and the other credential paths emptied, and git with no global config and no
  SSH. A copy of `~/.claude.json` is bound in and deleted after the trial. Only
  the output folder and a private `/tmp`, `/dev/shm` and `/run/user` are
  writable; the rest of the filesystem, `/var/tmp` included, is read-only.
  The credential paths are a fixed list in `trial.sh` (`jail()`), not a rule: anything else under HOME, such as a token
  in a file nobody listed, is readable, and the network is open. `--out` is
  refused when it is, holds or sits inside `~/.claude`, `~/.config`, `~/.local`,
  `~/.ssh` or `~/.aws`. The network stays open for the API, and the
  Claude OAuth file stays readable because the claude process needs it.
- A scenario lives under HOME, not `/tmp`: the jail's `/tmp` is empty.
- ERROR in the report means the session never ran (API, auth, jail). Read
  `stderr.txt` before anything else.
- A trial starts from `env -i` plus HOME, PATH, the CA bundle and the stub's
  variables. The calling session's environment can carry the `env` block of
  `~/.claude/settings.json`, and a variable in it that switches the hook under
  test off would turn trials into false FAILs. A scenario that needs a variable
  puts `NAME=value` lines in `<scenario>/env`.
- `--setting-sources project` drops user hooks, but `~/.claude/CLAUDE.md` still
  loads, as it would for a real user.
- Headless sessions cannot answer questions. A trial that ends asking the
  requester something has stopped, which is often the behaviour under test.
