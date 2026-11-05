# Launch post drafts

For the repository owner to review and post manually. These drafts make no
claim about adoption, performance beyond the measured week in the README, or
support that is still on the roadmap.

Landing page: <https://eduardopin.github.io/agenfk-squad/>

Repository: <https://github.com/eduardopin/agenfk-squad>

## Show HN

**Title:** Show HN: Guardrails for running several Claude Code sessions in parallel

I run multiple Claude Code sessions against different cards and worktrees. The
individual sessions were usually making reasonable choices, but the system
around them had four recurring failures: context kept being repaid on every
turn, sub-agent fan-out had no machine-wide limit, authors approved their own
work, and parallel PRs collided at merge time.

I published the setup I use to put boundaries around that: twelve Markdown
agents with explicit roles, a standard-library Python hook system, and an
AgenFK flow for cards and evidence.

The thresholds are based on one measured week, one machine, five projects. In
that sample the orchestrating sessions carried 420k–966k tokens of context,
249 sub-agents consumed 64% of all tokens, and one card was sent to review five
times. Those are not general benchmarks; they are the reason the current rules
exist.

What I would most value is contrary data: where the handoff math is wrong,
which parallel-session failure is missing, and what another harness can
actually enforce. The roadmap includes a dry/observe mode, terminal adapters
beyond herdr, worktree ownership checks, and ports to other agent harnesses.

Landing page: https://eduardopin.github.io/agenfk-squad/

Code: https://github.com/eduardopin/agenfk-squad

## r/ClaudeAI

**Title:** I published the guardrails I use when several Claude Code sessions run at once

Running one Claude Code session with a card and tests was manageable. Running
several exposed a different class of problems: no shared context budget,
sub-agents that looked free to the session spawning them, self-review, and PRs
all resolving against a moving default branch.

This repo is my current answer: twelve role-limited agents plus a Python watcher
hook. It can order a handoff from measured context/reread, deny another
dispatch when the orchestrator is too heavy, cut a looping sub-session, keep
the writer separate from the verifier, and serialize merges through a local
turn queue. AgenFK supplies the cards, flow, and evidence gates.

The numbers behind the defaults are deliberately narrow: one week, one
machine, five projects. The README includes the measured table and the watcher
math. I am looking for reports that prove a default wrong or expose a failure
the current rules miss, especially with 200k windows or without herdr.

Demo and architecture: https://eduardopin.github.io/agenfk-squad/

Repository: https://github.com/eduardopin/agenfk-squad

## r/programming

**Title:** A standard-library Python hook for context budgets and merge turns across coding-agent sessions

I published a small control layer around parallel Claude Code sessions. The
interesting part is not another agent prompt; it is the machine-wide behavior
that one session cannot see.

The watcher reads transcript token usage once a minute, uses context/burn/reread
arithmetic to decide when a handoff pays for itself, limits orchestrator
fan-out, detects repeated calls in sub-sessions, and keeps a per-repository
merge-turn queue. Every denial includes an exit: do the card in the current
session, write a verified handoff for a fresh session, wait for a reviewer
slot, or take the decision to the owner.

It is a standard-library Python hook system, twelve Markdown role definitions,
a JSON flow, and a no-dependency test suite. The agents and flow use AgenFK for
cards and evidence.

Current limitations are explicit: the Claude Code hooks do not drive other
harnesses (oh-my-pi has a separate port), the merge queue is local to one
machine, automatic wakeup depends on herdr, and the threshold sample is one
week on one machine. I would welcome review of the failure modes and the hook
protocol, not just the prompts.

Architecture: https://eduardopin.github.io/agenfk-squad/

Source: https://github.com/eduardopin/agenfk-squad

## dev.to

**Title:** What broke when I ran several Claude Code sessions—and the guardrails I added

**Description:** Context budgets, independent review, dispatch limits, and a local merge queue for parallel coding-agent work.

One capable coding-agent session can follow a card, run tests, and prepare a
clean branch. Several capable sessions create a systems problem.

In one measured week on one machine across five projects, the orchestrating
sessions carried 420k–966k tokens of context, spawned 249 sub-agents, and sent
64% of token consumption into those sub-agents. One card went to review five
times. None of the individual steps looked obviously irrational from inside
the session.

I published the setup after turning the failure pattern into four rules:

1. Treat context as a machine budget, not a feeling. A watcher calculates when
   rereading the current session costs more than a fresh handoff.
2. Put a ceiling on fan-out. A heavy orchestrator cannot keep creating work it
   will later need to reread.
3. Keep writers and approvers separate. The verifier has no Edit/Write tool,
   runs in clean context, and receives the card and branch, not the author's
   narrative. Because it has Bash, its no-writing boundary remains a prompt
   rule.
4. Serialize the last mile. A local merge queue gives one session at a time a
   turn to fetch, merge the default branch once, run the suite, push, and land.

The implementation is intentionally plain: twelve Markdown agents, a
standard-library Python hook system, and an AgenFK flow for cards and evidence.
A refusal always names where the work continues.

This is a working setup, not a general benchmark. The Claude Code hooks do not
drive other harnesses (oh-my-pi has a separate port), the queue does not
coordinate two machines, and automatic session wakeup depends on herdr. Those
gaps—and the questions behind the thresholds—are in the roadmap.

Read the diagrams and example hook output from a synthetic transcript:
https://eduardopin.github.io/agenfk-squad/

Source and contribution guide:
https://github.com/eduardopin/agenfk-squad

## X

I published the guardrails I use when several Claude Code sessions run in
parallel: context handoffs from measured reread, dispatch ceilings, independent
review, and one merge turn at a time.

12 Markdown agents + a standard-library Python hook system, built on AgenFK.

The defaults come from one week on one machine—not a universal benchmark. I’m
looking for the second data point.

https://eduardopin.github.io/agenfk-squad/

## LinkedIn

Running several coding-agent sessions is not just running one session several
times. The shared failures show up outside any one context window: fan-out,
self-review, repeated context cost, and merges against a moving branch.

I published the control layer I use around parallel Claude Code work. AgenFK
owns cards, flow, and evidence. Twelve role-limited agents own one job each. A
standard-library Python watcher measures context and reread, brakes dispatch,
cuts loops, switches sessions through verified handoffs, and gives merge turns
one at a time.

The rules are grounded in a narrow sample—one week, one machine, five
projects—and the measured table is in the README. I am not treating those
numbers as a benchmark. I am publishing them so other people can test the
assumptions against their own sessions.

The most useful contribution is a sanitized failure with numbers: a handoff
that fired too early, a loop the watcher missed, a denial with no useful exit,
or a collision the local queue cannot prevent.

Landing page: https://eduardopin.github.io/agenfk-squad/

Repository: https://github.com/eduardopin/agenfk-squad
