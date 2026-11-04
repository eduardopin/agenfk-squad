# The watcher

`hooks/squad-watch.py` is one Python file, standard library only, registered on
seven Claude Code hook events. It reads the transcript of every session and
sub-session, does a fixed piece of arithmetic, and acts. It never crashes a
session: any error ends silently with exit code 0 and a line in
`~/.claude/squad/watch-errors.log`.

Everything it prints starts with `[squad-watch]` and contains only what it
measured. `SQUAD_WATCH=0` turns it off.

- [What runs when](#what-runs-when)
- [The handoff arithmetic](#the-handoff-arithmetic)
- [Sub-sessions: loops and cuts](#sub-sessions-loops-and-cuts)
- [The dispatch brake](#the-dispatch-brake)
- [The soft stop](#the-soft-stop)
- [The session switch](#the-session-switch)
- [Waking a stopped session](#waking-a-stopped-session)
- [Compacting before the cache goes cold](#compacting-before-the-cache-goes-cold)
- [The merge queue](#the-merge-queue)
- [Metrics](#metrics)

## What runs when

| Hook event | What the watcher does |
|---|---|
| `PostToolUse` | Records the call and warns on repetition. Every 60 seconds it reads what was appended to the transcript and redoes the arithmetic: context, burn, reread share, payback. A sub-session at its threshold is told to return its handoff; a main session is told to write one. |
| `PreToolUse` | In a sub-session: denies an identical repeated call (loop) and, above the context cut, denies everything except `git` and `agenfk`. In a main session: applies the dispatch brake to `Agent`/`Task` calls and the merge queue to merge and sync commands. |
| `PostToolUseFailure` | A tool call failed: if it was the merge that held the merge turn, gives the turn back at once instead of waiting for `SQUAD_TURN_HOLD` to expire. |
| `SubagentStop` | Writes the sub-session's metrics to `metrics.jsonl` and leaves a note for the parent when the sub-session ended badly. |
| `UserPromptSubmit` | Delivers pending notes to the main session. |
| `Stop` | Main session at handoff level: if it stopped without writing the handoff, the stop is blocked and the order repeated; if it stops again without one, the watcher writes a minimal handoff itself. With a handoff on disk, it launches the session switch; with a background task still running, the switch is scheduled instead (see [The session switch](#the-session-switch)). Main session outside handoff with a delivery still open: refuses the stop (see [Carrying a delivery to the end](#carrying-a-delivery-to-the-end)). Otherwise, with a large context: schedules a `/compact` just before the prompt cache goes cold (see [Compacting before the cache goes cold](#compacting-before-the-cache-goes-cold)). |
| `PreCompact` | Main session, `/compact` or auto-compact: before the conversation is summarized, writes the same minimal handoff to `~/.claude/squad/handoffs/<session>-compact.md`. It prints nothing, so it never blocks the compaction, and it never switches the session. A compaction the watcher ordered is labelled as such in that handoff. |

Reading the transcript is incremental: the watcher keeps a byte offset per
session and parses only whole lines added since the last look, so it costs the
same on a 5 MB transcript as on a 50 MB one.

## The handoff arithmetic

The watcher weighs tokens the way the API bills them, relative to one fresh
input token:

| Token kind | Weight |
|---|---|
| Input | 1 |
| Cache write | 1.25 |
| Cache read | 0.1 |
| Output | 5 |

From a session's running totals it computes (`handoff_math` in
`skills/session-context/scripts/session_context.py`):

| Name | Formula | Meaning |
|---|---|---|
| `new` | input + 1.25 × cache-write + 5 × output | What the session produced |
| `reread` | 0.1 × cache-read | What it paid to re-read itself |
| `share` | reread / (new + reread) | The reread share |
| `burn` | new + reread | Weighted tokens spent so far |
| `burn_pct` | burn / `CLAUDE_SESSION_BUDGET` | Share of the session budget (default 20M) |
| `toll` | 0.1 × context | What every further turn costs before it does anything |
| `restart` | 1.25 × `SQUAD_CTX_BASE` + 5 × 2,000 | Writing the handoff and rebuilding the cache in a fresh session |
| `payback` | restart / (0.1 × (context − `SQUAD_CTX_BASE`)) | Turns until a fresh session has paid for itself |
| `floor` | see below | The context below which a high reread share is not acted on |
| `due` | share ≥ `SQUAD_REREAD` and context ≥ floor | The reread rule fired |

### The rule

A main session is ordered to hand off (`PROACTIVE HANDOFF`) when either holds:

1. **context ≥ `SQUAD_CTX_PCT`% of the window** (default 90%), or
2. **reread share ≥ `SQUAD_REREAD`%** (default 60%) **and context ≥ floor**.

Rule 1 is the wall. Rule 2 is the interesting one.

### Why there is a floor, and why it moves

A high reread share with a small context is the cache doing its job, not
waste. So rule 2 only applies above a floor.

A fixed floor is still wrong. Picture a session that has just loaded 600k
tokens of context and done almost nothing with it. Its reread share is high
(it has produced little, so most of its spend is re-reading), and its context
is at 60% of the window. Cutting it now throws away what it just paid to load.

So the floor **weighs the burn**. With `window` the session's context window:

```
floor_pct = 90 − (90 − 50) × min(1, burn_pct / 50)
floor     = max(2 × SQUAD_CTX_BASE, window × floor_pct / 100)
```

where 90 is `SQUAD_CTX_PCT`, the first 50 is `SQUAD_REREAD_FLOOR` and the
divisor 50 is `SQUAD_BURN_FULL`. In words: the floor starts at 90% of the
window for a session that has burned nothing, and falls in a straight line to
50% once the session has burned half its budget.

For a 1M window and a 20M budget:

| Burn so far | `burn_pct` | Floor |
|---|---|---|
| 0 | 0% | 900k (90%) |
| 2M | 10% | 820k (82%) |
| 5M | 25% | 700k (70%) |
| 8M | 40% | 580k (58%) |
| 10M or more | ≥ 50% | 500k (50%) |

Until October 2026 these were 80% and 50%: the handoff went out early enough
to interrupt work that was still paying for itself. From then until November
2026 the lowest floor was 70%, and no main session ever reached it: live
sessions rereading 74–87% of their spend, the largest at 672k with 143% burn,
were never ordered to hand off.

### Worked examples

**A. Just loaded, barely used.** Context 600k, burn 2M (10%), reread share 70%.
Floor is 820k. Context is below the floor, so rule 2 does not fire. Rule 1
does not fire either (600k < 900k). The session keeps working. It gets at most
an informational notice, which is not an order to stop.

**B. Same context, long session.** Context 600k, burn 11M (55%), reread share 68%.
Floor is 500k. Context ≥ floor and share ≥ 60%: `PROACTIVE HANDOFF`. Each
further turn costs a toll of 60k weighted tokens. A restart costs
1.25 × 40k + 10k = 60k, and saves 0.1 × (600k − 40k) = 56k per turn, so the
fresh session pays for itself in 60 / 56 ≈ 1.1 turns.

**C. Productive session, low reread.** Context 820k, burn 12M (60%), reread share 41%.
Share is under 60%, so rule 2 does not fire. Context is under 900k, so rule 1
does not fire. The session continues, even though it is large: it is still
producing more than it re-reads.

**D. The wall.** Context 910k, any burn, any share. Rule 1: `PROACTIVE HANDOFF`.

The cut therefore always falls between 50% and 90% of the window. Above 50%
without the arithmetic closing, the session receives only an informational
notice.

Sub-sessions have no known window (`window = 0`), so their floor is
2 × `SQUAD_CTX_BASE` and they are governed by the absolute thresholds below.

### What the session must do

On `PROACTIVE HANDOFF` the session closes the step in progress, comments on
the card, writes the handoff through MODE A of the `clear-pickup` skill
**without asking for permission**, puts what is left of the request under
`## Next step`, and stops. It does not open a new front.

The reverse matters as much: a session must **not** write a handoff or stop
"because the context is high" without a `PROACTIVE HANDOFF`, a
`DISPATCH DENIED`, or a request from the owner. The decision is the watcher's
arithmetic, not the session's estimate. A handoff written without one of those
is flagged as `Handoff out of turn`.

## Sub-sessions: loops and cuts

| Signal | Threshold | Action |
|---|---|---|
| Same call (tool + arguments), in the last 30 calls | 3 times | Warning |
| Same call, in the last 30 calls | 5th time | Denied |
| Same call, back to back | 3 in a row | The next one is denied |
| Context | `SQUAD_SUB_CTX_WARN` (120k) | Told to wrap up and return its handoff |
| Context | `SQUAD_SUB_CTX_CUT` (180k) | Every call denied except `git` and `agenfk` |
| Weighted burn | `SQUAD_SUB_BURN` (2M) | Told to return its handoff |

Fields that change without changing the call (`description`, `timeout`,
`run_in_background`) are ignored when comparing calls.

A sub-session that is cut returns a **return handoff** of at most twelve
lines: DONE (commits and files, with evidence), MISSING, NEXT STEP, BLOCKER.
If its role writes code, it first runs `git add -u` and a `wip:` commit on the
card's branch, so nothing it did is lost. New untracked files are left out of
that commit on purpose and named under MISSING.

The parent then follows one rule: **do not redispatch the same brief.** Read
the return handoff, check in git what actually got done, and dispatch only
what is missing, with a short brief, in a new sub-session. One redispatch.

## The dispatch brake

Before any `Agent` or `Task` call from a main session, the watcher checks four
ceilings:

| Ceiling | Variable | Default |
|---|---|---|
| Context of the orchestrator | `SQUAD_CTX_CEILING` | 800,000 |
| Sub-sessions spawned by this session | `SQUAD_MAX_SUBS` | 60 |
| Sessions orchestrating at the same time, per repository | `SQUAD_MAX_ORCH` | 6 |
| Reviewer dispatches for the same card | `SQUAD_MAX_REVIEWS` | 6 (the 7th is denied) |

A session counts as orchestrating while it has a sub-session with activity in
the last ten minutes. A dispatch counts as a reviewer when the agent type or
description matches review, verify, security, audit or adversarial; the card
is recognised by its id in the brief.

A `DISPATCH DENIED` always says where the work continues. There are four exits:

| Situation | Exit | What the session does |
|---|---|---|
| Context ceiling, or sub-session ceiling | **Handoff with the plan** | Writes a handoff whose `## Next step` holds the plan of the denied dispatch (card, branch, worktree, files, steps, acceptance criteria, suite command) and stops. The session switch is armed; the pickup executes the plan. |
| No orchestrator slot, implementation, context < `SQUAD_ORCH_HERE` (500k) | **Do it right here** | Implements the card in the main session, without an agent and without a handoff. This is the one authorised exception to the dispatch rule. The review still belongs to the `verifier`. |
| No orchestrator slot, implementation, context ≥ `SQUAD_ORCH_HERE` | **Handoff with the plan** | As above; the fresh session, with a clean context, implements directly. |
| No orchestrator slot, reviewer | **Wait for the slot** | Carries on with whatever does not depend on the verdict. The watcher sends `SLOT FREE` when a slot opens. The session never approves its own code. |
| Seventh reviewer on the same card | **Owner's decision** | Comments on the card with what keeps failing and takes it to the owner. |

The session must not work around a denial with another tool or another agent
type, and must not repeat the dispatch.

## The soft stop

Nothing is interrupted mid-task.

- Before a ceiling is reached, the orchestrator receives a `DISPATCH NOTICE`.
  It is informational, not an order to stop.
- Only the **new** dispatch is denied. Sub-sessions already running finish,
  and so does the step the main session is in.
- The brief of each denied dispatch is saved in full in
  `~/.claude/squad/handoffs/<session>-dispatches.md`. The handoff points to
  that file instead of retyping the plan.
- If a session at handoff level stops without writing a handoff, the stop is
  blocked and the order repeated. If it stops again without one, the
  watcher writes a minimal handoff on its own, from what it can verify: git
  state, the owner's last request, the denied dispatches.
- Before a compaction (`/compact`, or auto-compact), the watcher writes the
  same minimal handoff to `<session>-compact.md`, with the trigger (`manual`
  or `auto`) and the instructions given to `/compact` in its header. The
  summary that replaces the conversation keeps the decisions only in summary
  form; the file keeps git, the request and the denied briefs as they were.
  Sub-sessions are skipped.

If the alert no longer applies (the context dropped after a compaction, for
example) the watcher says so with `ALERT CANCELLED`.

## The session switch

When a main session stops at handoff level, with a handoff written **by that
session after the alert and after the owner's last request**, the watcher
launches `hooks/squad-switch.py` as a detached process:

1. it waits for the agent in the pane to go idle;
2. it checks the pane still hosts the **same** session. If the owner switched
   sessions or is typing something else, it gives up;
3. it sends `/clear` and checks that the pane's session changed;
4. it sends `/clear-pickup pickup <copy>`, where `<copy>` is a read-only copy
   of the handoff, checked by SHA-256. Never "the most recent file in the
   folder", and never the original, which could change after the check.

The fresh session's pickup verifies the handoff against the repository and
continues from `## Next step`. The work carries on without the owner present.

One switch per session: the first process to create
`~/.claude/squad/switches/<session>.claim` sends the `/clear`, any other gives
up. A `/clear` that does not change the session (a dialog left open) gets an
Escape, unless the session is working. If the switch gives up before the
`/clear` (the session woke up, or the handoff changed on disk), it rearms the
watcher, which decides again on the session's next `Stop`.

Each step is logged in `~/.claude/squad/switches.log`. Any doubt means giving
up: the handoff is already on disk and the owner can switch by hand with
`/clear` followed by `/clear-pickup pickup`.

### Waking up after the handoff

A handoff can go stale between the `Stop` and the switch:

- **The owner wrote after it.** A handoff written before the owner's last
  request does not count: the session is told to write a new one.
- **A background task came back after it.** If a `<task-notification>`
  arrived after the last `Write` of the handoff, the session is told, once per
  handoff, to rewrite it with that result under DONE, and to stop. Rewritten
  or not, the switch goes ahead on the next `Stop`.

### Background tasks

The `Stop` event lists the session's background tasks. A task still running
(shell, monitor, sub-agent) will wake the session when it ends, so the switch
is not launched yet, but it is not dropped either: it is **scheduled**.
`squad-switch.py wait` keeps watch for `SQUAD_SWITCH_WAIT` seconds (600) when
only shells are running, or `SQUAD_SWITCH_WAIT_AGENT` (2400) when a sub-agent
is. Then:

- the task comes back, the session stops again and the normal switch goes out;
- or the deadline passes with the pane idle, and the switch goes out anyway.
  The copy handed to the pickup ends with a **watcher's note** listing the
  tasks that were still running (type, id, description): their result is not
  in the handoff, and the fresh session checks git or reruns them.

The wait leaves early if another switch already claimed the session or the
pane changed session. A session still busy, or a herdr that never answers
again, ends the wait `SQUAD_SWITCH_BUSY` seconds (1800) past the deadline.
One schedule is live per session at a time.

The switch needs a pane it can type into, which today means the session runs
inside **herdr** (`HERDR_PANE_ID` is set). Outside herdr the watcher still
orders the handoff, and the session tells the owner the two commands.
`SQUAD_SWITCH=0` disables only the switch.

## Waking a stopped session

A session can stop halfway through a delivery: the reply ended with nothing
to call it back, a background command died, an API error, a reboot. No hook
runs in a stopped session, so `hooks/squad-wake.py` runs from a clock outside
them (a systemd `--user` timer installed by `install.sh`, every 2 minutes) and
looks at every herdr pane hosting Claude Code:

    squad-wake.py              one scan: wakes whoever needs it
    squad-wake.py --dry-run    prints the decision for each pane, without acting

A pane idle for `SQUAD_WAKE_AFTER` seconds (600) gets one prompt, one line,
starting with `[squad-watch] WAKE`: check git status, the card, the merge queue
and the background tasks, relaunch what died and carry on until it is
delivered. The watcher skips that prefix: it is not the owner's request, and it
never counts as asking to stop. When the last `Stop` left a background task
alive (the watcher records `last_stop` with the time and the count of live
tasks), the deadline is `SQUAD_WAKE_AFTER_BG` (2700), because the task wakes the
session itself when it comes back.

It never wakes a pane that is working or whose session changed between the
decision and the send, a session whose owner asked to stop, a session at
handoff level or switching, a session idle for more than `SQUAD_WAKE_AGE`
(43200), or one whose last reply ends with a line `[squad] END: no card is left
that I can take` (`[squad] DONE:` is accepted too) or `[squad] BLOCKED: merge approval on PR #64 is the owner's` (the owner gets a herdr notification once). Unreadable watcher
state means no wake.

Each wake without progress doubles the deadline, up to `SQUAD_WAKE_CEILING`
(3600); after `SQUAD_WAKE_MAX` (6) wakes in a row without progress it only
notifies the owner, once. Progress is work that changes state (Edit/Write, an
agent dispatch, git commit/push/merge/rebase, `gh pr create|merge`, `agenfk
verify|update|create`, `squad-turn request`), never the checks the WAKE asks
for. A pane stuck on a dialog for `SQUAD_WAKE_BLOCKED` (900) is notified, never
answered. At most `SQUAD_WAKE_BATCH` (3) wakes per scan.

It also keeps the switch from getting stuck:

- **stuck switch**: the old session is still in the pane, marked as switching
  (`switched` with `switched_ts`, or a claim) and idle for `SQUAD_WAKE_SWITCH`
  (900). squad-wake removes the claim, hands the watcher a rearm `Stop` and then
  an expired-deadline `Stop`: the watcher redoes the switch with the handoff on
  disk. A session that moved after the switch (the owner resumed it) and a
  switch older than `SQUAD_WAKE_SWITCH_AGE` (21600) never get a `/clear`.
- **lost pickup**: `squad-switch.py` records
  `~/.claude/squad/switches/<new-session>.pickup` (the sealed copy's path)
  before sending the pickup. If after `SQUAD_WAKE_PICKUP` (120) the new session
  is idle and the pickup is not in its transcript, squad-wake sends
  `/clear-pickup pickup <copy>` again. Only a copy path under
  `~/.claude/squad/handoffs`, a regular file owned by the user, is ever sent.
  The record is written only for a new session id herdr reports in the shape of
  a session id (hex and dashes), never through a symlink; otherwise the pickup
  still goes out, unrecorded, and `switches.log` says so.

Each of the two tries twice, then notifies the owner once. State is kept in
`~/.claude/squad/wake/<session>.json`; every decision goes to
`~/.claude/squad/wakes.log` (`--dry-run` writes neither).

squad-wake finds a session's transcript under `SQUAD_WAKE_PROJECTS`
(default `~/.claude/projects`, the folder where Claude Code keeps
`<project>/<session>.jsonl`); set it in `wake.env` only if your Claude Code
keeps the transcripts elsewhere (`CLAUDE_CONFIG_DIR`).

The timer does not inherit the shell's environment: put `SQUAD_WAKE=0` or the
thresholds in `~/.claude/squad/wake.env`, not in the units: the installer
rewrites `squad-wake.service` and `.timer` only when they carry its marker on
the first line, and leaves a unit of your own alone (`SKIPPED`, non-zero exit,
timer not enabled). `systemctl --user disable --now
squad-wake.timer` stops it. Without a systemd user manager for your `HOME`
(macOS, a container) the installer prints the line to run every 2 minutes from
cron or launchd. Windows is not covered yet.

## Carrying a delivery to the end

Sessions delivering a large piece of work used to stop at each milestone with
"the next step is card X" and wait for a person who was not reading the pane.
The `Stop` of a main session outside handoff level now refuses that stop while
a defined delivery is open, and sends the session back with `[squad-watch] CARRY
ON`.

A delivery is open when either holds:

- **an explicit goal**: a line such as `[squad] GOAL: docs/architect/plan.json - 51d1c739-d0d2-400a-8037-f46b00d1668b` (what is delivered, then the card ids) in the
  owner's message or in one of the session's replies. The `architect` skill
  writes it when a plan goes to execution. AgEnFK card ids on that line, whole or the
  8-character prefix (`card 51d1c739`), are the goal's cards; an external id
  such as `INFRA-419` is not looked up, so name the AgEnFK id. A line whose text starts with `<` is read as a quoted template
  and opens no goal, so write the real text. An explicit goal survives the owner's side questions: the
  session answers and goes back to work. Writing the same line again changes
  nothing, a session's line never replaces the owner's goal, and a new session
  goal keeps the guard's counters.
- **an implicit one**: the last reply hands the work back. It names a next step
  or card ("o próximo passo é o card X", "next card"), offers to do it ("posso
  abrir essa frente agora, se você quiser", "quer que eu", "want me to", "shall
  I"), or says it is waiting for the owner ("esperando você", "waiting on
  you"). An offer or a wait counts only when its own line speaks of delivery work
  (a card, front, PR, merge, push, wave, deploy, worktree, `#12`) or follows
  work done in the same turn, so "want me to go deeper?" after an explanation
  is not a delivery; code blocks and quoted lines are not read. It lives only
  while replies keep doing that, and it ends at the owner's next message. Its first hold counts as no progress unless work was
  done since the previous stop, so a Q&A answer that only mentions a next step
  is held at most twice.

The session stops by ending a reply with one line:

| Line | Effect |
|---|---|
| `[squad] DONE: PR #64 merged, cards DONE` | Closes the goal. Refused (`DONE refused`) while a goal card is not DONE in AgEnFK (`agenfk get <id> --json`, the first 4 cards, in parallel, within 7 s); a CLI or server that does not answer in time counts as done. After `SQUAD_CARRY_MAX` (3) refused DONEs the line is accepted while a card is still open, so a CLI or server that stays down, or a card that cannot reach DONE, cannot hold the session. |
| `[squad] BLOCKED: merge approval on PR #64 is the owner's` | Lets go until the owner speaks again: merge approval, a production apply, an answer only the owner has. |
| `[squad] END: no card is left that I can take` | Closes the goal. |

The text after the colon is the session's own: a line whose text starts with `<`
is read as a quoted template, so a copied `<placeholder>` does not exit.
These are the same lines `squad-wake.py` reads: the pattern in `squad-watch.py`
is a copy of that file's `MARK`, kept so a Stop without a goal never loads
`squad-wake.py`, and `tests/test_carry_on.py` fails when the two differ. So a session that stopped
for a reason the hook cannot see (an API error, a reboot) is still woken by
the timer.

The order names what the session must do itself before it calls anything the
owner's: push, open or reopen a session or pane, merge where the project's
rules let the session merge (rule 11: a green gate and SHIP, through the merge
queue), start the next front. Only what only the owner can do goes in
`BLOCKED`, a merge approval the project reserves for the owner among them.

The hold never wins against the owner or the watcher:

- the owner asking to stop or pause (the same reading as the soft stop) lets go;
- handoff level keeps its own order, and the pickup carries the goal in the
  handoff's Next step;
- a sub-session's stop is never held;
- `SQUAD_CARRY_MAX` (3) stops in a row without progress let go and leave a note
  for the session and a line in `~/.claude/squad/carry.log` (and a herdr
  notification in a pane). Progress is what squad-wake counts: an edit, a
  dispatch, a commit, push or merge, a PR, an `agenfk verify/update/create`.
  Checking alone is not progress. Progress, or the owner speaking, re-arms it;
- `SQUAD_CARRY_CAP` (200) holds per goal let go even with progress;
- an explicit goal released twice in a row without progress in between is
  closed, and one with no progress for `SQUAD_CARRY_AGE` (43200 s, 12 h) has
  expired, so an abandoned goal does not hold the owner's later chat. A goal in
  `BLOCKED` ages for `SQUAD_CARRY_BLOCKED_AGE` (7 days) instead, and the owner's
  answer re-arms it. A closed goal does not come back by the session writing
  the same line again; the owner's next message lifts that;
- reaching `SQUAD_CARRY_CAP` closes the goal;
- `SQUAD_CARRY=0` turns it off.

The idea follows two public Stop hooks: [claude-goal](https://github.com/jthack/claude-goal)
(an active `/goal` holds the stop, with a cap per goal) and
[taskmaster](https://github.com/levi-openclaw/taskmaster) (the transcript is
re-read for unfinished work). Both are MIT; no code was taken from them.

## Compacting before the cache goes cold

A session that stopped with a large context still has its prompt cache warm
for a while: 1 hour or 5 minutes after its last request, depending on the
cache the session uses. Compacting while the cache is warm reads the context
at 0.1 of the price. A minute after it goes cold, the next message writes the
whole context again (1.25x on the 5-minute cache, 2x on the 1-hour one).

So, on the `Stop` of a main session outside handoff, with context at or above
`SQUAD_COMPACT_MIN` (150000), the watcher schedules `hooks/squad-compact.py`
as a detached process, due `SQUAD_COMPACT_BEFORE` seconds (120) before the
cache of the last request goes cold. The TTL is read from the transcript (the
`cache_creation` split of the last responses); `SQUAD_CACHE_TTL` overrides it.
With no TTL in the transcript nothing is scheduled: there is no telling when
the cache goes cold. The job is
`~/.claude/squad/compact/<session>.json`; every `Stop` reschedules it, and the
old process leaves on its own.

At the due time it sends one line to the pane, `/compact` followed by what the
summary must keep, in order: the owner's request verbatim and what was
decided, cards, repository, branch, worktree, PRs and merge-queue position,
decisions and why, DONE with evidence and REMAINING with the next step,
background tasks still open, traps already found. It adds, if there are
any, the absolute path of the denied briefs, and the owner's last request as
the watcher recorded it.

The line is at most 760 characters, counted in UTF-16 units, `/compact `
included. Claude Code turns any prompt longer than 800 characters into a paste
(`<pasted_content>`), and a pasted `/compact` does not run: it becomes an
ordinary message to the model. The 40 characters of margin cover a count in
bytes for accented text. The fixed instructions are short; the briefs path goes
in whole or not at all; the owner's request takes what is left, cut with an
ellipsis.

It sends it only if all of this still holds:

- the job is still the one it was launched for;
- the transcript has no request after the scheduled one (the cache was renewed);
- the pane hosts the same session, idle;
- the watcher's state for the session exists and is readable (missing or
  corrupt is doubt);
- the session is not at handoff level or switching (no `switched`, no switch
  claim, no scheduled switch wait), the owner did not ask to stop or pause,
  and no earlier `/compact` arrived in this session as a message;
- the job's text is one line starting with `/compact`, at most 760 characters;
- the transcript is readable.

Any doubt means not compacting. Before sending, it records
`~/.claude/squad/compact/<session>.sent` (request id and time): the
`PreCompact` handoff that follows says the watcher ordered the compaction, not
that the session stopped without a handoff, and the same request is never
compacted twice.

Sent is not done. It counts as compacted only when a `compact_boundary` with
the `manual` trigger shows up in the transcript after the point of sending,
within `SQUAD_COMPACT_CONFIRM` seconds (600). A `/compact` that runs as a
command never reaches `UserPromptSubmit`; if the watcher sees its own text
there, bare or wrapped in `<pasted_content>`, it was a message: the prompt is
not recorded as the owner's request, the session is marked, squad-compact logs
a failure and no `/compact` is scheduled again in that session. When it did
not compact (herdr refused, it arrived as a message, no boundary in time) the
`.sent` record is removed; the job is removed whenever squad-compact ends,
unless a newer `Stop` already rescheduled it. After a confirmed compaction the
`.sent` record stays: it is what keeps the same request from being compacted
twice. Each decision goes to `~/.claude/squad/compactions.log`.

It needs a herdr pane (`HERDR_PANE_ID`) and a Claude Code transcript.
`SQUAD_COMPACT=0` turns it off.

## The merge queue

With several sessions landing PRs at the same time, each merge moves the main
branch under the others, and the conflict a session just resolved comes back.
The queue makes landing on main one at a time, per repository. State lives in
`~/.claude/squad/turn/`. `SQUAD_TURN=0` disables it.

**Merging** (`gh pr merge`, a direct push to `main` or `master`, the
`merge_pull_request` tool):

| The turn is | Result |
|---|---|
| Free, or already yours | Goes through, and the turn stays with your session |
| Another session's | Denied. Your session joins the queue and is called when its turn comes: `MERGE TURN`, as a note in the session and a prompt in its herdr pane |

**Syncing with main** (`git merge|rebase|pull ... main`, `gh pr update-branch`)
while another session holds the turn is denied **once**, with the reason: main
is about to move, and a conflict resolved now will have to be resolved again.
Repeating the command within fifteen minutes goes through, because a session
that needs something already landed must not be stuck.

**The command**, linked into `~/.local/bin` by the installer:

```bash
squad-turn request [PR]   # ask for the turn, or join the queue
squad-turn release        # give the turn back, or leave the queue
squad-turn status         # who holds the turn, the queue, whether your branch
                          # conflicts with main, files in common with those ahead
```

The first `gh pr merge` requests the turn by itself.

**When `MERGE TURN` arrives:** `git fetch`, `git merge origin/main` **once**,
run the suite, push, merge.

**The turn is released** when the PR shows as MERGED or CLOSED (checked with
`gh`), by `squad-turn release`, after `SQUAD_TURN_IDLE` (900 s) without
activity from the holding session, after `SQUAD_TURN_MAX` (3600 s) in any
case, or after `SQUAD_TURN_CLAIM` (300 s) if a session that was called does
not show up. Sessions in the queue are told which files they have in common
with what just landed.

## Metrics

Every finished sub-session appends one JSON line to
`~/.claude/squad/metrics.jsonl`: agent type, context, weighted burn, calls,
reread share, duration, and how it ended. Read them with:

```bash
python3 skills/session-context/scripts/session_context.py --tasks
```

These numbers are how the thresholds in this file were chosen. If yours
suggest different ones, that is exactly the issue we want to read.
