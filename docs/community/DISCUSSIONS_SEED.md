# GitHub Discussions seed drafts

These are drafts for the repository owner to post manually. Keep reports
sanitized: no transcripts, private paths, customer names, credentials, or real
session ids.

## Welcome: what should this project learn from your machine?

Welcome to AgenFK Squad. This repository is one person's working setup for
running several Claude Code sessions with explicit role limits, context and
dispatch brakes, independent review, and a local merge queue.

The current defaults came from one week on one machine across five projects.
That is enough to explain why the rules exist, not enough to say the rules are
universal. If you use parallel coding agents, this is a place to compare
failures, measurements, and enforceable ways forward.

When you post, please say which harness and context window you used, how you
measured the failure, and what the session was told to do next. Remove private
paths, project/customer names, session ids, and transcript content first.

## Question 1: what failure have you seen in parallel agent sessions?

Context that kept growing? Fan-out that looked cheap until the bill arrived?
A writer reviewing its own work? Two sessions resolving the same merge
conflict? Something this repository does not name yet?

Please describe the smallest reproducible sequence, the evidence you had, and
what a useful guardrail would have told the session to do next. A failure with
numbers is more useful than a feature request without one.

## Question 2: when does a handoff pay for itself on your context window?

The watcher uses context, weighted burn, and reread share. Its defaults were
tuned with a 1M window; the roadmap explicitly asks whether they hold at 200k.

What window do you use? At what context and reread share did the old session
become more expensive than a fresh one? What did the fresh session have to
rediscover? If you can, include sanitized output from
`session_context.py --detail` and say whether the handoff felt early, late, or
correct.

## Question 3: what can your agent harness actually enforce?

The current hooks speak Claude Code's protocol. Oh-my-pi is ported separately.
The agents themselves are Markdown, and the report can read Codex, pi and
oh-my-pi transcripts, but for other harnesses dispatch denials, loop cuts,
session wakeups, and merge turns are not ported.

For Codex, pi, Gemini CLI, OpenCode, or another harness: which events can block
an action, which can only advise, and how would a fresh session receive a
verified handoff? A useful answer can be a support matrix before it is code.
