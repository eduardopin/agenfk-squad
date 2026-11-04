# Hosts: Claude, Claude Code, Codex, pi and others

The format is the Agent Skills standard (`SKILL.md` + folders). What changes between hosts is where the folder lives, how to invoke it, whether there is a shell for the driver and how to create a clean context for actors. Install by copying the `journey-qa/` folder into the host's skills directory (table below).

## Matrix

| Host | Where to install | Invoke | Browser | Actor in a clean context |
| --- | --- | --- | --- | --- |
| **Claude Code** | `.claude/skills/journey-qa/` (project) or `~/.claude/skills/journey-qa/` | `/journey-qa` or a natural-language request | Driver (`jqa browser`) or Claude Code's own Chrome integration | Subagents `jqa-actor`/`jqa-observer` or `jqa actor spawn --host claude` |
| **Codex** (CLI/IDE) | `.agents/skills/journey-qa/` (repo) or `~/.agents/skills/journey-qa/` | `$journey-qa`, `/skills` or implicit | Driver; the sandbox must allow the browser and localhost | `jqa actor spawn --host codex` (`codex exec --ephemeral`) |
| **pi** (any provider/model) | `.pi/skills/` or `.agents/skills/` (trusted project) or `~/.pi/agent/skills/` | `/skill:journey-qa`, implicit, or `pi --skill <folder>` | Driver | `jqa actor spawn --host pi` (`pi -p --no-session --no-context-files --no-skills --no-extensions` inside a bubblewrap jail on Linux) |
| **Claude Desktop and claude.ai** | Chat: upload the skill ZIP (`journey-qa/` folder at the root) in Settings > Capabilities > Skills; the upload syncs to the account, so Desktop and web both get it. Desktop's Code tab is Claude Code: it reads `~/.claude/skills/` of the machine it runs on | Natural-language request | Claude in Chrome (`host_tools`) when available; the driver requires the execution environment to have Node, Playwright and a browser | New conversation with only `PACKET.md`; otherwise declared single mode |
| **Others** (Cursor, Gemini CLI, custom agents) | `.agents/skills/journey-qa/` or a directory the host documents | As the host defines | Driver if there is a shell | `jqa actor spawn --host custom --cmd "…{PROMPT_FILE}"` |
| **Chat only** | Paste `SKILL.md` and the files in `roles/` into the instructions | — | Host tools, if any | Declared single mode |

Paths and flags checked against the vendors' documentation on 2026-10-23; versions change. If a path is not discovered, use what the host documents.

## Claude Code

- Permission for the tool (in the project's `.claude/settings.json`, if you want to avoid prompts):
  ```json
  { "permissions": { "allow": ["Bash(node .claude/skills/journey-qa/scripts/jqa.mjs *)"] } }
  ```
- Subagents: define one for the actor (it receives only the actor packet and uses `act.mjs`) and one for the observer (it verifies rules with oracles). Subagents start with their own context; pass them only what is needed. Do not *fork* the conversation for actors (it inherits everything).
- `actor spawn --host claude` uses `--disable-slash-commands` (no skills), `--strict-mcp-config` (no MCP), `--tools Bash` with permission only for `node act.mjs` and a directory outside the project. With `--bare` (a spawn option) not even the user's CLAUDE.md is read, but authentication then requires `ANTHROPIC_API_KEY`.
- If you prefer the user's real Chrome, Claude Code's Chrome integration works as `host_tools` (record proofs with `jqa evidence add`).

## Codex

- The default sandbox may prevent Playwright from opening the browser or the CLI from reaching the driver at `127.0.0.1`. Options, in order of preference: approve the execution when Codex asks; run with `--sandbox workspace-write -c sandbox_workspace_write.network_access=true`; use `danger-full-access` only in a disposable environment.
- Skills installed in `~/.agents/skills` are also visible to actors launched with `codex exec`. For blind evaluation, install the skill in the repository and run actors outside it (`actor spawn` uses `actors/ID/` as the working directory).
- `agents/openai.yaml` carries the display name and default prompt for the Codex UI.

## pi

- Works with any provider configured in pi (`--provider`, `--model`). For smaller models: use the driver (compact snapshot with refs), short journeys per actor, `act.mjs` instead of long commands, and compare runs by hand against a known result until an evaluation lab exists, before trusting it.
- Projects must be marked as trusted for pi to load skills from `.pi/skills`/`.agents/skills`.
- `pi -p --no-session --no-context-files --no-skills --tools bash` is the most isolated actor: no AGENTS.md, no skills, only a shell.

## Claude (app/web) and Claude in Chrome

- The upload requires a ZIP whose root is the `journey-qa/` folder with `SKILL.md`. Zip only the `journey-qa/` folder, not the whole repository.
- The skill provides the method; browser control comes from Claude in Chrome or another integration. At the start, confirm which tools exist in this session.
- If the code execution environment has Node, `validate`, `report`, `arch` and `infra` work even without a browser.

## Evaluating a new host/model

This skill does not ship an evaluation lab. To compare a host or model, build a small local web app with a seeded
defect and keep the answer in the evaluator's own file, then:

1. Give the agent the skill, the app's URL and a product profile as the contract. Nothing else.
2. At the end, score the agent's `run-report.json` against the seeded answer.
3. Repeat across several cases and models; compare detection, false positives, verdict, coverage and contract validity.
