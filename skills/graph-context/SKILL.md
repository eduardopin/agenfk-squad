---
name: graph-context
description: Answer "where is X, what calls it, what breaks if it changes" from the project's graphify code graph instead of grep and file-by-file reads - build it code-only and offline, query it with a token budget, trace paths, explain one node, and know when it is stale. Use before a broad search of a large repository, when a squad-graph notice appears, when judging the blast radius of a change, and when asked "graphify", "code graph", "o que depende disso", "what depends on this".
---

# graph-context: ask the code graph before grepping

A grep returns lines; the graph returns relations: which resource depends on
which, who calls a function, what a module imports. graphify builds it from
tree-sitter parsing of the code, locally, with no model involved. The
`squad-graph` hook nudges a session toward it once a graph exists; this skill
is how to build one and read it well.

## Build it: code only, offline

From the project root:

```bash
graphify extract . --code-only    # first build
graphify update .                 # after code changes, no model
```

- `--code-only` is what keeps the build on the machine. Without it graphify
  sends docs, PDFs and images to a model for a semantic pass, and picks the
  model from the environment by itself: Bedrock whenever `AWS_PROFILE` or
  `AWS_REGION` is set, or whichever API key it finds. graphify 0.9.91 does not
  read `GRAPHIFY_BACKEND`, whatever its README says. Run a semantic pass only
  when the owner of the repository asks for it.
- The output lands in `graphify-out/` (`graph.json`, `manifest.json`, a cache).
  `graphify-out/` is never committed: add it to `.gitignore` if the repository
  does not ignore it already. Writing it into somebody else's repository is a
  change to their tree; when that is not yours to make, build it elsewhere with
  `--out <dir>` and pass `--graph <dir>/graphify-out/graph.json` to queries.
- Markdown, YAML and other prose are skipped in code-only mode. A repository
  that is mostly prose (prompts, docs) gets little from the graph.

## Read it

```bash
graphify query "what depends on the cloudtrail bucket" --budget 800
graphify path "UserService" "DatabasePool"
graphify explain "squad-watch.py"
```

- `graphify query` walks the graph from the nodes the question names. Keep
  `--budget` low (500 to 1500 tokens) and narrow the question when the answer
  says it was truncated, rather than raising the budget to the whole graph.
- `graphify path A B` gives the shortest chain between two things, each edge
  tagged `EXTRACTED` (read from the source) or `INFERRED` (resolved by graphify).
  Treat `INFERRED` as a lead to confirm in the file, not as a fact.
- `graphify explain X` lists one node's connections with file and line, which
  is where to open the file, instead of reading every file that mentions X.
- Exit code 3 means the name matches several nodes and the candidates are
  listed; pick one. Exit code 4 means no node matched.

The graph narrows where to look. A finding, a review verdict or a blast-radius
claim still cites the file and line it came from.

## The notices

The `squad-graph` hook can add two lines to a Bash, Grep, Read or Glob call,
each once per session:

- **stale**: `.git/index` is newer than `graph.json`, so the graph may describe
  code that has moved. Run `graphify update .` before relying on it; until then
  read the file directly.
- **slow**: graphify's hook took more than 500 ms at p95 over 20 or more calls.
  The work is not blocked, only slower. `SQUAD_GRAPHIFY=0` in the environment
  turns the hook off without uninstalling anything.

The hook never blocks a call. When graphify is missing, fails or is slow
beyond 2 seconds, the call goes through as if the hook were not there.

## Figures

The hook counts, per session, the searches and reads it saw, how many graphify
answered with a nudge, how many found no graph and how many it skipped:

```bash
python3 ~/.claude/hooks/squad-graph.py --report <session-id>
```

These are the numbers that say whether the graph is earning its place. Report
them as measured; do not claim a saving the numbers do not show.
