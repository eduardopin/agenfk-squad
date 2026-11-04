# Simulating people and interactions

Use with `assets/templates/actor-card.json`. The simulation must reveal the product's experience and rules without turning the actor into an executor of the QA script. Section 8 shows how to run each actor in a clean context on Claude Code, Codex, pi or another host.

## Index

1. [Define the actor](#1-define-the-actor)
2. [Separate knowledge and session](#2-separate-knowledge-and-session)
3. [Make plausible choices](#3-make-plausible-choices)
4. [Talk and socialize](#4-talk-and-socialize)
5. [Coordinate several people](#5-coordinate-several-people)
6. [Refuse, abandon and return](#6-refuse-abandon-and-return)
7. [Preserve validity](#7-preserve-validity)
8. [Run isolated actors per host](#8-run-isolated-actors-per-host)

## 1. Define the actor

Create behavioral profiles, without relying on demographic stereotypes. Select differences that can change the experience:

| Dimension | What to specify |
| --- | --- |
| Role | Buyer, visitor, organizer, guest, contributor, curator, player, operator or another real role. |
| Goal | Progress the person wants to reach and the reason to pursue it now. |
| Knowledge | Familiarity with the domain and the product; information already known. |
| Skill | Digital habits, understanding of terms and recovery strategies. |
| Constraints | Budget, deadline, language, device, relevant privacy or accessibility needs. |
| Trust | Evidence the person needs to see before buying, sharing or acting. |
| Acceptable effort | What the person tolerates learning, filling in, repeating or waiting for. |
| Alternatives | What the person will do if the offer does not meet the need. |
| Termination and return | Conditions for success, refusal, giving up, suspension and resumption. |

Give the actor an intent and a context, without supplying the correct paths, locators, seeded bugs, secret criteria or solutions. A buyer may compare options, ask about terms and decide not to buy. An experienced person may consult documentation and APIs if that is part of their normal work.

Avoid personas so extreme that every failure is predetermined. Choose plausible variability and explain each profile's relevance to the risk. Do not invent statistical representativeness.

## 2. Separate knowledge and session

Keep three contexts when the host allows:

1. **Analyst/coordinator:** knows sources, rules, budget and environment.
2. **Actor:** knows only their initial context and what they perceive or legitimately receive.
3. **QA observer:** knows the criteria and may consult authorized instruments without guiding the actor.

Use separate model contexts and independent browser sessions for different actors. In the driver, each `jqa browser actor add` creates its own context and the isolation is recorded in the ledger. Tabs that share cookies and storage do not guarantee an isolated identity.

Do not transfer between actors private data, answers not yet received, another participant's internal reasons or tenant secrets. Share only facts conveyed by the product or an authorized channel and observed by the recipient.

If there is only one conversation/model, record the limitation: switching names does not remove knowledge already seen. Run an assisted exploration or a role evaluation with this limitation declared; do not label it an independent simulation. Do not pretend a context was erased.

If the host does not isolate sessions, limit the scenario to one identity, use sessions the user explicitly provides, or record the multi-actor part as blocked. An observed logout/login can serve a sequential interface test; it does not prove simultaneity or full isolation.

The actor must not use the console, database, private API, internal storage, answer key or test hook to win an interface journey. The observer may use an authorized instrument as an oracle and must record that modality separately.

## 3. Make plausible choices

Repeat a short cycle:

1. Observe the current interface and the messages received.
2. Choose an action consistent with the goal, knowledge and constraints.
3. Execute through the surface available to the role.
4. Observe whether the action produced a response or effect.
5. Record the action, the observation and a short justification of the choice, without asking for detailed internal reasoning.
6. Continue, recover, wait, refuse or end according to the context.

Allow selective reading, doubts, going back to the previous page, reviewing data, correcting an error, comparing and asking for help. Do not force typos, hesitations or pauses in every scenario. When they are part of the scenario, keep the original intent and evaluate the recovery.

Prefer normal text input and accessible controls. Do not paste ready-made answers obtained from the evaluator. Use paste when it is plausible behavior, for example copying a received code or an address, and record the origin.

Do not confuse randomness with realism. An artificial wait does not show human reading; slang does not show personality; clicking everything does not show useful exploration. Keep actions consistent with constraints.

Respect the operational budget. If it ends before the persona's exit condition, record a test interruption; do not convert it into human abandonment.

Do not create bugs to reach a quota. If the actor completes a coherent experience with no observed problem, keep that result with its scope.

## 4. Talk and socialize

Use communication to reach an intent: understand availability, clarify price, agree on a time, ask for help, contest information or negotiate an allowed alternative. Write natural messages specific to the observed context.

Cover, when relevant:

- An incomplete first question, followed by clarification.
- Confirmation of understanding before an action with a persistent effect.
- A change of preference communicated explicitly.
- Correction of a misunderstanding without handing the solution to support.
- Disagreement, refusal or counteroffer.
- Change of agent or channel with continuity of context.
- Pause and return, including a message answered later.
- Ending without a purchase, without a reply or because of unsuitability.

Separate consumer, seller and store owner. Separate lead, sales agent and person responsible for the business. Each person has their own goals and limits; do not make everyone cooperate to prove the journey.

Use test recipients and accounts or channels already authorized. Authorization to browse does not automatically authorize contacting real people. Reuse an explicit authorization that already covers the action, recipient and environment.

Send and receive through the real channel available. Record the necessary content, pseudonymized identity, time, correlation and observed state. Distinguish a message drafted, sent, accepted, delivered, read and answered; do not invent states the channel does not expose.

If there is no counterpart, record the absence of a reply. A reply created by the test itself is a simulation and must be identified as such. If the recipient is a real bot in a sandbox, evaluate the conversation as that bot's execution in that environment, without attributing humanity to it.

Do not pose as an identifiable real person. Use test identities and respect the environment's rules. The persona's fluency does not require hiding automation or bypassing anti-bot controls.

## 5. Coordinate several people

Define roles, relationships, channels and visibility rules before the journey. Give each actor their own goal and initial state. The coordinator may trigger scenario events but must not secretly decide the participants' answers to guarantee success.

| Context | Required separation |
| --- | --- |
| Store | The customer knows the offer and their need; the seller knows the permitted operation; the owner knows the business rules. |
| Scheduling | The organizer proposes; guests decide according to their constraints; private calendar information stays private. |
| Content | The contributor submits; the curator evaluates; the visitor sees content only in the authorized state. |
| SaaS | The administrator configures; the member uses; another tenant inherits neither data nor permissions. |
| Support | The customer communicates; the operator receives; a transfer happens only when the destination actually takes over. |

Allow plausible conflict and cooperation. A guest may refuse; a curator may ask for a revision; an agent may lack authority. Evaluate whether the product handles the outcome correctly, without forcing agreement.

Record the order of events and the clocks. A future appointment does not require keeping the model blocked until the date; it may require an authorized simulated event, a resume, or declaring a limit. Identify clearly any simulated clock advance.

Do not run actors in parallel in the same shared session. If the instrument only allows sequential execution, record that condition and avoid conclusions about concurrency.

## 6. Refuse, abandon and return

Define the relevant exit conditions before execution, such as maximum price, unavailable date, missing essential information or excessive effort. Allow adjustments based on perceived facts, without rewriting the persona to justify a convenient result.

Record the point and the observable cause of the decision. Separate:

- **Informed refusal:** the product was understood and does not meet the need.
- **Abandonment from friction:** the person gives up in the face of effort, doubt or a barrier.
- **Suspension:** the person intends to return after an event or deadline.
- **Technical interruption:** the tool, environment or test budget prevents continuing.

Only the first cases can characterize a persona's decision. A technical interruption proves neither user friction nor a product defect.

On return, check what the person would remember, what was persisted and what must be relearned. Do not restore state hidden by the evaluator. Validate drafts, cart, session, preferences, invitations, history or progress according to the product.

A journey can end with an `actor_outcomes` of abandonment and a `qa_verdict` of approval within the evaluated scope. Another can reach the task and fail QA because of an incorrect charge or permission. Use the canonical definitions; do not collapse these dimensions.

## 7. Preserve validity

Record any coordinator help, hint, reset, data change, tool switch or privileged query. Separate the assisted part from the independent experience. Do not hide assistance to improve the success rate.

Declare the instrument's limits:

- A mobile viewport is not equivalent to a physical Android device.
- Inspecting labels is not equivalent to using a screen reader.
- Isolated keyboard events are not equivalent to continuous or simultaneous game controls.
- The state of an audio API is not equivalent to hearing and understanding the sound.
- Navigation in Chrome does not cover windows and permissions of native apps.

Use authorized human measurements, separate from calibration, when the goal is to estimate representativeness. Without that comparison, present findings as evidence from synthetic actors within the executed scope.

Keep scenarios that do not end in conversion. Product quality includes letting the person understand, refuse, leave and return in control.

## 8. Run isolated actors per host

The actor packet is the only material the actor receives:

```bash
jqa actor packet fichas/comprador.json            # creates actors/comprador/PACKET.md + act.mjs
jqa actor packet fichas/comprador.json --strict   # fails if the sheet leaks a rule, answer key or rule ID
```

`act.mjs` is a wrapper with an allowlist: only interface commands and flags, always as the right actor (`node act.mjs snapshot`, `click e3`, `note "…"`, `done achieved "reason"`); `--as`, `--run`, `--actor`, oracle commands and coordination commands are refused with code 3. The leak check compares the sheet with the run's profile and scenario (5-word excerpts, rule IDs, evaluator terms). It is a heuristic: review the sheet yourself.

Run in a new process (clean model context, no coordinator history). `spawn` copies `PACKET.md` and `act.mjs` to a private directory **outside the repository** (`<root>/journey-qa-actors/<run>/<actor>`), so that the host does not discover the project's AGENTS.md, CLAUDE.md or skills, and delivers the packet as a system instruction (Claude, pi) or via stdin (Codex), never as a command-line argument:

```bash
jqa actor spawn comprador --host claude            # shows the command without running it
jqa actor spawn comprador --host claude --exec     # runs it and records the transcript in the ledger
jqa actor spawn comprador --host pi --exec --background && jqa actor wait comprador
```

| Host | Generated command | What it isolates | Caution |
| --- | --- | --- | --- |
| Claude Code | `claude -p --setting-sources project --no-session-persistence --disable-slash-commands --strict-mcp-config --tools Bash --allowedTools "Bash(node act.mjs *)" --permission-mode dontAsk --append-system-prompt-file PACKET.md --output-format stream-json --verbose "<start prompt>"` | No skills, MCP, user hooks or user settings; can only run `node act.mjs` (plus read-only commands inside its own folder); directory outside the project (no project CLAUDE.md). The stream-json session becomes `commands.json`: every command the actor asked for, the ones outside `act.mjs` and the ones denied, recorded in the ledger | The user's `~/.claude/CLAUDE.md` is still read. `--bare` (via `actor spawn --bare`) ignores it too, but requires `ANTHROPIC_API_KEY` (it does not use OAuth login) |
| Codex | `codex exec --ephemeral --skip-git-repo-check --sandbox workspace-write -c sandbox_workspace_write.network_access=true -` (packet via stdin) | New process and session, outside the repository | Skills in `~/.agents/skills` (user) remain visible: for blind evaluation, install Journey QA in the project. The sandbox needs local network access to reach the driver |
| pi | `bwrap <jail> -- pi -p --no-session --no-context-files --no-skills --no-extensions --no-prompt-templates --tools bash --append-system-prompt PACKET.md "<start prompt>"` | No AGENTS.md/CLAUDE.md, skills, extensions or prompt templates. pi has no command allowlist, so on Linux the actor runs in a bubblewrap jail built as an allowlist: system read-only, HOME an empty tmpfs with only the node install and a throwaway overlay of `~/.pi/agent` bound back, `/run`, `/mnt` and `/tmp` empty, environment cleared to locale and CA bundle (plus the one provider key the `--model` prefix names), own PID, IPC and UTS namespaces, writable only its folder. The run folder is masked with an empty tmpfs wherever it lives | Without Linux and `bwrap` the spawn is refused (exit 3); `--unsandboxed` runs it with the user's shell and must be declared in `limitations`. The pi provider key stays readable: pi needs it. The network is shared (model API and loopback driver) |
| Other | `--host custom --cmd "my-agent --prompt-file {PROMPT_FILE}"` | Same bubblewrap jail as pi | Declare what was isolated; `--unsandboxed` as for pi |

The actor folder holds the actor's driver token and, in the background, a runner that is executed, so it belongs to you alone. `<root>` is `$XDG_RUNTIME_DIR` when it is a folder of yours that no one else can write; else the temporary folder when it is yours alone (macOS, Windows); else `<tmp>/journey-qa-<uid>` inside a shared temporary folder such as `/tmp`, created with mode 0700 and used only when it is a real directory of yours with no group or other bits (one someone else planted first is skipped, never repaired); else `$XDG_CACHE_HOME` or `~/.cache`. Never the shared `/tmp` itself. Each folder on the way (`journey-qa-actors`, the run, the actor) is created with mode 0700 and checked with `lstat`: a real directory, not a symlink, owned by you, with no group or other permission bits. Anything else is refused with `unsafe_workdir` (exit 2); remove that folder and spawn again. The base above them is yours, not journey-qa's: when it is refused (for example `~/.cache` created 0775 under umask 002), run `chmod go-w <base>` rather than removing it, or point `XDG_RUNTIME_DIR` or `XDG_CACHE_HOME` at a folder only you can write.

The cache folder comes last because it puts your home among the actor folder's parents, and some hosts read instruction files from the folders above the one they start in. Claude Code reads `CLAUDE.md` from its folder and every folder above it, so under `~/.cache` it reads `~/CLAUDE.md` (and `~/.claude/CLAUDE.md`, which it reads from any folder unless `--bare`). Codex reads `AGENTS.md` from the root of the git repository it is in down to its folder, so under `~/.cache` it reads `~/AGENTS.md` only when `~` is itself a git repository (and `~/.codex/AGENTS.md` from any folder). pi with `--no-context-files` reads neither. On Linux and macOS, `$XDG_RUNTIME_DIR` (`/run/user/<uid>`) and the system temporary folder (`/tmp`, or `/var/folders/…` on macOS) are outside your home, so there no folder above the actor's is in it. That stops holding when you point `TMPDIR` or `XDG_RUNTIME_DIR` at a folder inside your home: your home is then above the actor folder, as with the cache folder. On Windows the temporary folder is `%LOCALAPPDATA%\Temp`, inside your profile, so `C:\Users\<you>` is always above the actor folder: Claude Code reads `C:\Users\<you>\CLAUDE.md` when there is one, and Codex reads `C:\Users\<you>\AGENTS.md` when your profile is a git repository. There, keep those files out of your profile folder, or spawn Claude Code actors with `--bare`.

`spawn` hands the actor folder to the driver together with the actor's token, and the driver checks it the same way (the path ends in `journey-qa-actors/<run>/<actor>` for that run and actor, under a base only you can write, each folder 0700 and yours) before it issues the token, and again on every upload. The run is named by its real path, so `--run` through a symlink to the run the driver serves names the same folder. The driver never derives the folder from its own environment, so a driver started with a different `XDG_RUNTIME_DIR`, `TMPDIR` or `HOME` still accepts the actor's uploads. `actor wait` removes the folder spawn recorded in `spawn.json`, after the same check, and removes nothing when the check fails (it says why instead). The files are written fresh (the old one removed, then created exclusively): `PACKET.md`, `.driver.json` and `.runner.mjs` with mode 0600, `act.mjs` with 0700. On Windows there is no POSIX owner or mode: the folder stays under your temporary folder (`%LOCALAPPDATA%\Temp`), protected by your profile's permissions, and only the symlink check applies.

On Claude Code you can also use a subagent per role (see `hosts.md`): subagents start with their own context and receive only the delegation prompt. Pass them **only** the content of `PACKET.md` and the path of `act.mjs`.

Every browser action is traced with `via`: `act` when it came through the actor's own `act.mjs`, `direct` otherwise. If the coordinator types into an actor's context (to unblock it, or to try a variant), `jqa report validate` warns until that is declared in `interventions[]`. Prefer a separate observer or helper actor for variants: the actor's outcome should be the actor's.

The actor must finish with `node act.mjs done <outcome> "reason"`; this writes `actors/ID/outcome.json` and a `transcript` evidence item. The coordinator still verifies: the actor's statement is data, not a verdict.

Honest limits: the actor runs on the same machine and as the same user. A spawned actor never needs the run folder: `spawn` asks the driver for a token valid only for that actor, writes it to `.driver.json` in the actor's folder, and `act.mjs` sends interface commands, `note` and `done` straight to the driver, which refuses oracle commands, `--as`, `--actor`, other actors and uploads from outside the actor's folder for that token, and revokes it when the actor ends. pi and custom actors also run jailed (above), so they can read neither the run, the driver's full token nor the user's credentials. A Codex actor runs in Codex's own sandbox with a free shell inside it; it could still read files the sandbox shows. These measures remove shortcuts and make deviations visible in the trace (`via`), but they are not a hardened sandbox: the network is shared and the kernel is the user's.

When there is no way to create a clean context (single chat), use **assisted single mode**: the same model coordinates, acts and observes; declare `model_context: shared` and do not use the round to measure spontaneous discovery.
