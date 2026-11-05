# Project decisions

`hooks/squad-decisions.py` keeps the decisions a project has settled (which
database, what is never done on Fridays, which API is frozen) in one store that
every session reads before it works. It is one Python file, standard library
only, used two ways: as a command, `squad-decisions`, and as a `SessionStart`
hook that prints the index of the active decisions into the new session.

When the owner states a standing rule in a prompt ("always…", "from now on…",
"nunca…"), `squad-router` adds a `[squad decision]` line asking the main
session to record it here (rule 12 of `CLAUDE.md`).

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

`source` is `owner` or `card <id>`. `status` is `active` or `superseded`,
read without regard to case; a file edited by hand with any other value is
left out of the index and shown as written by `list --all`, and a leading
byte order mark is ignored. A
decision is never deleted: superseding one sets its `status: superseded` and
`superseded_by: <new file>`, and leaves it out of the index. The old file stays
as the record of what was decided before, whole, not summarised.

The slug is ASCII letters and digits joined by `-`, cut at 60 characters, so a
title cannot name a directory (`../`, absolute paths, NUL and newlines all fold
into the slug or into spaces). Every `<file>` argument must be a bare
`<YYYY-MM-DD>-<slug>.md` as `list` prints it; a path is refused, and so is a
symbolic link inside the store.

## The command

`install.sh` links it as `~/.local/bin/squad-decisions`; on Windows,
`install.ps1` writes `%USERPROFILE%\.local\bin\squad-decisions.cmd`, the same
kind of shim as `squad-turn.cmd`. Run it inside the repository.

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

`add`, `supersede` and `add --supersedes` hold an exclusive lock on the store
(`.lock` in it) while they check a status and change it, so two sessions
superseding the same decision at once cannot both win: the second one is
refused. With `--supersedes` (and in `supersede`), the new decision is written
in full first and the old one is marked last. A single failure rolls back to
where the command started: `add --supersedes` removes the new decision and the
old one stays active; `supersede` puts the new decision back as it was, and
both stay active as before. A crash between the two steps leaves both
decisions active, visible in the index. A second fault while rolling back
leaves either both active or the old one marked as superseded by the new one,
which is complete and active; it never leaves the old one pointing at a
decision that is not there, and it says "could not undo" on stderr.

What to run next depends on what is left, so check `squad-decisions list --all`:
both decisions active means the supersede did not finish, and
`squad-decisions supersede <old> --by <new>` finishes it; the old one already
superseded by the new one means there is nothing left to do (a second
`supersede` is refused); after `add --supersedes` rolled back, the new decision
is gone, so run the `add` again.

**Who can write.** `--source owner` is self-declared: nothing checks that the
owner typed it. Any session that can run Bash can record a decision, and every
later session of that repository reads it at start. A pull request cannot plant
one, because the store lives outside the checkout; to audit, `squad-decisions
list --all` shows every decision with its source, and `show` the whole file.

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

The entry is in `hooks/manifest.json`, so `get.sh` and `get.ps1` register it,
and `install.sh` and `install.ps1` link the file it runs; by hand:

```json
"SessionStart": [{"hooks": [{"type": "command", "command": "python3 ~/.claude/hooks/squad-decisions.py", "timeout": 5}]}]
```

## Variables

| Variable | Default | Effect |
|---|---|---|
| `SQUAD_DECISIONS_DIR` | `~/.claude/squad/decisions` | root of the stores, one subdirectory per repository |
| `SQUAD_DECISIONS_BUDGET` | `4000` | most bytes the hook prints |
| `SQUAD_DECISIONS` | on | `0` turns the hook off (the command still works) |
