---
name: agent-orchestration
description: The squad's method for designing orchestration between agents INSIDE a product (who takes the turn, how one agent hands off to or consults another, what gets recorded, how to cancel and what goes to the log) with testable rules and no dependency on an agent framework. Use when designing or reviewing a coordinator of agents (a governor, a router, an agent that calls specialists), when giving an agent an "ask another agent" or "transfer" tool, when defining the event and session contract of a conversation with an agent, when thinking about A2A, and when writing the acceptance criteria of such a card.
---

# agent-orchestration: the model asks, the orchestrator decides

The failure this method prevents: an agent that **chooses on its own** whom to
hand the turn to, what to ask another agent and with which ids, and a
recording that keeps what the model **asked for**, not what the system
**accepted**. The result is ping-pong between agents, cost with no ceiling, an
invented id turning into a "valid" query, a refused draft reaching the
customer and a log with the model's text inside.

The second failure: coupling the product to an agent framework to get
"orchestration". The rules below fit in a small core, with no dependency, in
the project's own language.

## 0. The project decides, and the room is clean

- Read `CLAUDE.md` / `AGENTS.md` / the architecture doc first: the project's
  vocabulary, ledgers, queue and guard rails **replace** the names used here.
  A rule the project already meets does not become a new card.
- Origin: a reading of a third party's private package. **Nothing of it is
  copied**: no code, no schema, no type name, no documentation text. This
  skill describes ideas in our own words. When implementing, write from
  scratch from the rules and tests here; do not look for or paste the
  original.

## 1. Minimal vocabulary

| Term | What it is |
|---|---|
| **execution port** | the single interface through which any agent runs: `id`, declared capabilities, `run(task) → event stream`, `cancel(execution)` |
| **orchestrator** | whoever decides which agent runs and accepts or refuses a transfer or a consultation; it implements the port itself (so it nests) |
| **turn** | one user message up to the final answer; the unit of recording |
| **run** | one execution of one agent inside the turn; a turn can have several |
| **transfer** | the agent asks another to take over the turn |
| **consultation** | the agent asks another and gets the answer **inside its own run** |
| **reserved fact** | a context key that only the orchestrator writes |

## 2. The rules

Each rule has its reason and the test that proves it. A card that implements a
rule takes the test as an acceptance criterion (section 5).

### R1. One port only, and the core knows no framework

Every agent (your own LLM, a third-party SDK, a remote agent) enters through
the same port. The core knows routing, transfer, identity and state; it does
not know the provider, the screen protocol or the SDK. Adapters depend on the
core, never the other way round.
*Proof:* a boundary test that fails if the core imports a provider or
framework package.

### R2. A transfer is a request; the decision follows a fixed rule

The orchestrator accepts a transfer only if: the target is on the list of
targets allowed for **that** agent; the user/tenant may enter the target; the
turn's transfer ceiling has not been reached; and the target is not the agent
that just handed over (no ping-pong). When refused, the requesting agent runs
**once** more, knowing why; the second refusal ends the turn.
*Proof:* one test per refusal reason, and one showing the 2nd refusal ending
the turn.

### R3. A refusal explains with a closed code and a fixed sentence

The refused agent receives `{code, sentence}`: a code from a closed set
(`target_not_allowed`, `ceiling`, `ping_pong`, `entry_denied`,
`not_in_directory`, `other`) and a fixed sentence it may pass on to the
customer, ending in "answer with what you have or say what is missing". The
sentence **never names the target** (the name came from the model or from
outside). The final message of a turn ended by a refusal is neutral as well:
never an agent id on screen.
*Proof:* a test that looks for the target's id in the text delivered to the
customer.

### R4. What will be refused anyway, say up front

A refusal that cannot change during the run (the agent that just handed over;
everything, once past the ceiling) is published in the run's context so the
agent does not even offer that way out. Two locks: publish only what is
**stable** for the whole run, and **never** call the decision "to see": a
stateful policy would be consumed by the preview. The policy still decides
every request.
*Proof:* a test with a policy that counts calls: the preview does not change
the count.

### R5. A consultation has depth 1, a budget and a hard deadline

The consulted agent: receives only the question and the checked ids, **with no
history**; gets no tool to transfer or to consult (no consultation inside a
consultation); cannot suspend waiting for the user; has a deadline, and is
cancelled when it passes. The turn has a consultation budget (low by default,
2). Question and answer go through the same content filter in both
directions. A failure comes back as a **code** (`not_allowed`, `budget`,
`depth`, `unknown_id`, `blocked`, `deadline`, `refused`, `needs_customer`,
`error`, `canceled`), never as an exception that brings down the asking run.
*Proof:* one test per code; one test of a refused nested consultation; one of
the deadline with an agent that never finishes.

### R6. An id is valid only if a tool returned it

Ids passed in a consultation must have come out of the result of a
**successful** tool call (history, this turn or an earlier consultation),
under an id key (`id`, `*_id`, `*Ids`...). A value the tool only **echoed**
from its own argument does not count; otherwise the model invents an id,
passes it through a tool that returns what it received and "launders" it. A
tool that checks the id against the database declares so, and then the echo
counts. Ids travel in their own field; the question's text is not scanned for
ids. A number that is not under an id key (`limit`, `page`, `amount`) is
never an id.
*Proof:* invented id → `unknown_id`; id echoed by a non-verifying tool →
`unknown_id`; the same id through a verifying tool → ok.

### R7. Record what was accepted, not what was asked for

When a run is refused and rerun, its text is **withdrawn** (a replacement
event with empty text and a reason) before any recording; its tool calls and
their results stay (do not repeat the search). There are two projections of
the turn: the **model's** (the next turn's history), which contains neither
the refused transfer nor the draft; and the **audit/screen** one, which shows
what was displayed and marks what was withdrawn. The live screen, the
recording and the reload show the same thing.
*Proof:* a turn with a refusal: the next turn sees neither the draft nor the
refused call; the audit record sees both, marked.

### R8. Only the orchestrator writes reserved facts

Keys such as "refusal reason", "published refusals", "who asked" are erased
from any context that comes from a proposal, an agent or an outside layer
before it reaches the run. Otherwise the model plants a "you were already
refused" or an "anything goes".
*Proof:* a proposal that tries to write the reserved key → the key does not
arrive.

### R9. A turn is one recording

A conversation has **one turn at a time** (an admission lock, released on
every exit path). The recording is atomic, idempotent by the turn id and
checks the expected revision. A new message abandons pending calls; a result
that arrives late, was not asked for, repeats an id or answers only part of
the batch is refused without consuming anything. The result of an external
call cites the operation that asked for it.
*Proof:* redelivering the same turn does not record twice; stale revision →
conflict; late result → refused.

### R10. Cancelling is signal first, then wait with a deadline

When cancelling the turn: signal the running consultations **before**
waiting for any agent; cancel all of them in parallel, each with a short
deadline; cancel each execution **only once**; what did not finish in time is
left loose, but with a strong reference kept (otherwise the garbage collector
drops the task) and a log of when it finished. A cancelled turn closes as
cancelled, even if an agent answers later.
*Proof:* an agent that ignores the cancellation does not hold the turn past
the deadline.

### R11. Logs and metrics without content

Each decision (transfer, consultation) becomes **one** log line with `from`,
`to`, `result` and `code`, never the model's text or a tool argument. A
metric label comes from a bounded set (no conversation or tenant id). A value
controlled by third parties and potentially huge (a call id) appears
summarised: prefix + hash + length. A failure of the telemetry backend is
logged with a rate limit and **never** brings down the turn.
*Proof:* a test that looks for the model's text in the decision lines.

### R12. A versioned JSON contract at every boundary

Every document (task, event, recording) carries the contract version. A reader
accepts the same major and **skips an event type it does not know**; a new
type and an optional field are a minor change; removing or renaming is a
major one. The schema is published, and a test pins the schema copy against
the code's. Task and consultation states use the A2A names (`working`,
`completed`, `rejected`, `input-required`, `canceled`, `failed`) from the
start: it costs nothing and leaves the A2A door ready.
*Proof:* the build reads a turn recorded by a newer version of the same major.

### R13. Every port has a conformance kit

Session storage, telemetry, filter: each port comes with a behavioural test
kit that **every** implementation runs (the in-memory one, the file one, the
database one). A new implementation only gets in by passing the kit.

### R14. History cut at whole exchanges

When summarising history for another agent, cut at a complete exchange (call
+ result). There is never a result left without the call that produced it,
and never the user's message twice.

## 3. What not to adopt

- **Rule-based routing on every message** when the product has a front agent
  in the conversation. Routing is for the coordinator choosing a specialist,
  not for the screen.
- **Coupling to a framework** to get these rules. They fit in a small core;
  the framework comes in, if at all, as an adapter of the port.
- **Consultation with depth > 1** or a **remote agent as the consulted one**
  without its own isolation: depth is only guaranteed inside the process.
- **Tools that run on the screen** (suspending the turn waiting for the
  customer to click) only if the product really has that case.

## 4. Where it fits

**A product that already has part of it.** Reading an existing agent product
usually finds some of this in place: a per-session lock plus optimistic
revision (R9); neutralising customer data, and "what the model writes does
not choose the tenant or the version" (neighbours of R6 and R8); closed
reasons and an explicit `retryable` (R3/R11); a record of denials per agent
run (R11). What tends to be missing, and where this skill comes in: a
coordinator consulting specialist agents (R5, R6), a capability registry as
the list of targets and of per-tenant entry (R2), a decision ledger recording
what was accepted (R7), and A2A on the roadmap (R12).

**The squad itself** already follows the same logic between sessions:
DISPATCH DENIED is a refusal with a reason and a way forward (R2/R3); the
ceilings on reviewers and sub-sessions are the transfer ceiling (R2); "a
sub-session brief, never the conversation" is the consultation without
history (R5). When changing `squad-watch`, the rules apply.

**Any project with more than one product agent**: read section 2 before the
first coordination card.

## 5. Acceptance criteria ready for the card

Copy the ones the card touches, swapping in the project's names:

- [ ] A transfer is accepted only with an allowed target, allowed entry, the
      ceiling not reached and no ping-pong; one test per refusal reason (R2).
- [ ] A refusal reaches the agent as a closed code + a fixed sentence without
      the target's name; the 2nd refusal ends with a neutral message (R3).
- [ ] Consultation: depth 1, a budget per turn, a deadline with cancellation,
      no history, no transfer/consult tool; a failure is a code (R5).
- [ ] An id in a consultation only if a successful tool returned it; an echo
      does not count; tests for an invented id and an echo-laundered id (R6).
- [ ] The draft of a refused run enters neither the model's history nor the
      delivered text; the audit shows it and marks it (R7).
- [ ] Reserved keys erased from every proposal (R8).
- [ ] Recording idempotent by the turn id, with the expected revision; a late
      result is refused (R9).
- [ ] A decision line without the model's text; bounded metric labels (R11).
- [ ] Documents carry a version; the reader skips an unknown type; the schema
      is pinned by a test (R12).
