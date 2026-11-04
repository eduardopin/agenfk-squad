# Project decisions

`hooks/squad-decisions.py` keeps the decisions a project has settled (which
database, what is never done on Fridays, which API is frozen) in one store that
every session reads before it works. It is one Python file, standard library
only, used two ways: as a command, `squad-decisions`, and as a `SessionStart`
hook that prints the index of the active decisions into the new session.

- [Where the store lives](#where-the-store-lives)
- [One file per decision](#one-file-per-decision)
- [The command](#the-command)
- [The SessionStart hook](#the-sessionstart-hook)
- [Variables](#variables)

## Where the store lives

The squad works in many worktrees of one repository at a time. A file inside
the checkout would be invisible to the other worktrees until it was merged, so
the store lives outside it:

```
${SQUAD_DECISIONS_DIR:-~/.claude/squad/decisions}/<repo key>/
```

`<repo key>` is a hash of `git rev-parse --git-common-dir`, which is the same
from every worktree of a repository and from any directory inside them. The
merge turn and the seal key their state the same way. A decision recorded in
one worktree shows up in the next session of every other worktree at once.
Two different repositories never share a store. `squad-decisions path` prints
the directory for the current repository.

## One file per decision

Each decision is its own file, `<YYYY-MM-DD>-<slug>.md`, created with an
exclusive open: two worktrees recording at the same moment write two files and
never touch the same one, so there is nothing to merge. The same title twice
on one day gets `-2`, `-3`.

```markdown
---
title: No deploys on Fridays
date: 2026-11-03
source: owner
status: active
supersedes: 2026-10-01-deploy-on-fridays-is-allowed.md
---
## What
Production deploys happen Monday to Thursday.

## Why
Two Friday incidents in October ran into the weekend with nobody on call.
```

`source` is `owner` or `card <id>`. `status` is `active` or `superseded`. A
decision is never deleted: superseding one sets its `status: superseded` and
`superseded_by: <new file>`, and leaves it out of the index. The old file stays
as the record of what was decided before, whole, not summarised.

The slug is ASCII letters and digits joined by `-`, cut at 60 characters, so a
title cannot name a directory (`../`, absolute paths, NUL and newlines all fold
into the slug or into spaces). Every `<file>` argument must be a bare
`<YYYY-MM-DD>-<slug>.md` as `list` prints it; a path is refused, and so is a
symbolic link inside the store.

## The command

`install.sh` links it as `~/.local/bin/squad-decisions`. Run it inside the
repository.

| Command | What it does |
|---|---|
| `squad-decisions add --title T --source S [--supersedes F]` | records a decision; the body (What / Why) comes on stdin; prints the new file name. With `--supersedes`, marks `F` superseded in the same call. |
| `squad-decisions list [--all]` | active decisions, newest first; `--all` adds the superseded ones and what replaced them |
| `squad-decisions show <file>` | prints one decision |
| `squad-decisions supersede <file> --by <file>` | marks the first as superseded by the second |
| `squad-decisions index` | the index of active decisions, with no budget |
| `squad-decisions path` | the store directory of this repository |

```bash
squad-decisions add --title "No deploys on Fridays" --source "card e71ede05" <<'EOF'
## What
Production deploys happen Monday to Thursday.

## Why
Two Friday incidents in October ran into the weekend with nobody on call.
EOF
```

A refused request (bad source, empty body, a `--supersedes` that does not
exist, a path as a file name) exits 2 and writes nothing.

## The SessionStart hook

With no arguments the script is the hook. It reads the hook's JSON on stdin,
uses its `cwd`, and prints one header line and one line per active decision
(date, title, file), newest first. The output is capped at
`SQUAD_DECISIONS_BUDGET` bytes (UTF-8, default 4000); when the cap cuts, the
last line says how many decisions were left out and that `squad-decisions
list` shows them all.

It prints nothing when the repository has no active decisions, outside a git
repository, when the store cannot be read, and on input that is not a JSON
object. It never fails the session: any error ends with exit code 0 and no
output. A decision file that cannot be read or has no frontmatter is skipped.

The entry is in `hooks/manifest.json`, so `get.sh` registers it; by hand:

```json
"SessionStart": [{"hooks": [{"type": "command", "command": "python3 ~/.claude/hooks/squad-decisions.py", "timeout": 5}]}]
```

## Variables

| Variable | Default | Effect |
|---|---|---|
| `SQUAD_DECISIONS_DIR` | `~/.claude/squad/decisions` | root of the stores, one subdirectory per repository |
| `SQUAD_DECISIONS_BUDGET` | `4000` | most bytes the hook prints |
| `SQUAD_DECISIONS` | on | `0` turns the hook off (the command still works) |
