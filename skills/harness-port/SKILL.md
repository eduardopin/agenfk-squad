---
name: harness-port
description: Port the squad's rules to another agent harness, oh-my-pi (omp) today. Explains the two agent dialects, renders agents/*.md into the omp task-agent contract with scripts/omp_agents.py, and says which rule a harness enforces mechanically and which it can only advise. Use when running the squad on omp, when an agent file must be adapted, when a harness resolves the wrong model, or when starting a port to a new harness.
---

# harness-port — the squad on a harness that is not Claude Code

The agents are Markdown, but the frontmatter is a contract, and every harness
has its own. omp is close enough to reuse the prompts and far enough that the
contract has to be translated. This skill is that translation, and the record of
what the harness can and cannot enforce.

## The dialect gap

| | Claude Code | oh-my-pi (omp) |
|---|---|---|
| where agents live | `~/.claude/agents/`, `.claude/agents/` | `~/.omp/agent/agents/`, `.omp/agents/` |
| does it read the other's dir | — | no, by design (`TASK_AGENT_CONFIG_SOURCE = ".omp"`) |
| tool names | `Read`, `Grep`, `Bash`, `WebFetch` | `read`, `grep`, `bash`, `web_fetch` |
| model | `inherit`, `sonnet`, `opus`, `haiku` | a selector or a role alias (`@default`, `@smol`) |
| extra guardrails | `PreToolUse` hooks | `tool_call` / `before_subagent_spawn` / `session_stop` extensions |

## Rendering the agents

```
python3 skills/harness-port/scripts/omp_agents.py render \
  --src agents --out ~/.omp/agent/agents
python3 skills/harness-port/scripts/omp_agents.py render \
  --src agents --out ~/.omp/agent/agents --check   # fail on drift, write nothing
```

`install.sh` runs the render when `omp` is on PATH. A project checkout can use
`--out .omp/agents` instead. A tool name with no mapping stops the render unless
`--allow-unknown` is passed, so a lost capability is never silent.

The model rule is the one that is not negotiable: `inherit` is dropped (omp then
uses the parent's model), the Claude aliases become omp role aliases, and any
selector naming Anthropic is refused. The output frontmatter never contains
`claude` or `anthropic`. The install prints the roles it expects:

```yaml
modelRoles:
  default: dgx-spark/GLM-5.3-Flash-EXL3
  smol: dgx-spark/GLM-5.3-Flash-EXL3
```

## What omp enforces, and what it only advises

Mechanically, through an extension:

- **the write gate** — `tool_call` can block `edit`, `write` and the mutating
  tools omp adds (`ast_edit`, `apply_patch`, `move`, `delete`, `notebook`,
  `str_replace`), delegating the verdict to `~/.agenfk/bin/agenfk-gatekeeper.mjs`;
- **the dispatch caps** — `before_subagent_spawn` fires once per child, so the
  watcher's concurrency arithmetic can refuse a spawn;
- **the handoff** — `session_stop` runs before the session settles, so the
  proactive handoff can be written there.

Only advised, because there is no event to hang it on: the merge queue's
"do not sync with main while you wait", and the judgment calls in `context`
and `verifier`. Those stay prose in the agent body.

The arithmetic stays in `hooks/squad-watch.py` (or `handoff_math` in
`session-context`); the extension must call it and decide nothing on its own,
the same way the pi extension delegates to the shared `.mjs` scripts.

## AgEnFK parity on omp

The pi extension (`bin/agenfk-pi-extension.ts` upstream) loads in omp through its
legacy-pi compatibility layer, but on a Bun host the hook spawn must resolve a
real Node runtime — `process.execPath` is the omp binary there, not Node. That
fix is upstream, not in this repo.
