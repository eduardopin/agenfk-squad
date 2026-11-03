# smoke

A scenario anyone can run to prove the harness works: `bwrap`, login, the stub
`gh` and the report. It asks a session to add one file and stop.

```bash
../../scripts/trial.sh --scenario . --dry      # no Claude, $0
../../scripts/trial.sh --scenario . --n 1      # one real trial
```

## The four files

- `setup.sh <trial dir>` builds `<trial dir>/repo` (a git repository with a
  README) and a local bare remote, `<trial dir>/remote.git`, then writes
  `<trial dir>/remote.before`, a snapshot of the remote's refs. It runs once per
  trial.
- `prompt.txt` is the request, in the words an engineer would type. It never
  names the skill, the hook or the behaviour being tested.
- `gh.json` is the stub `gh`'s script: a list of
  `{"match": "<argv prefix>", "stdout": <text or JSON>, "exit": 0}`. The first
  prefix that matches the arguments wins. A call nothing matches exits 1 and is
  still written to `gh.log`.
- `check.sh <trial dir>` reads the evidence and exits with the verdict.
- Optional `env` holds `NAME=value` lines the trial gets in its environment.
  Values must contain no spaces: `trial.sh` expands them unquoted.

## How the verdict is decided

| Exit | Verdict | Meaning |
|---|---|---|
| 0 | PASS | the behaviour under test happened |
| 1 | FAIL | the session did what the change should prevent, or skipped what it should do |
| 2 | N/A | the session never reached the behaviour (here: it wrote no `NOTES.md`) |

The report adds ERROR when the session itself never ran (API, auth, jail), and
SETUP when `setup.sh` failed. More N/A than PASS means the scenario is
unrealistic, not that the change works.

## Write one for a hook or a SKILL.md change

1. Copy this directory. Make `setup.sh` build a repository real enough that the
   session has no reason to refuse the task.
2. Write `prompt.txt` as the user would, without hinting at the expected
   behaviour.
3. Put in `gh.json` only the `gh` calls the session needs to get to the point
   under test.
4. Make `check.sh` read evidence: `gh.log`, `scripts/commands.py stream.jsonl`
   (the Bash commands the session ran), the remote's refs against
   `remote.before`, files under `repo`. Not the wording of the answer.
5. Run it with `--dry` first, then `--n 3 --plugin-dir <path to the change>`. To
   see that the check can fail, run it once against the base without the change.
