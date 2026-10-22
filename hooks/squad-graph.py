#!/usr/bin/env python3
"""squad-graph: a PreToolUse hook that points searches and reads at the code graph.

When the project has a graphify graph (graphify-out/graph.json, or $GRAPHIFY_OUT),
a Bash or Grep call is handed to `graphify hook-guard search` and a Read or Glob
call to `graphify hook-guard read`. graphify decides whether to nudge the session
toward `graphify query`; this wrapper prints its answer unchanged and holds the
controls the squad needs around a third-party hook:

  - SQUAD_GRAPHIFY=0 turns it off: nothing is spawned and nothing is printed;
  - the child runs without the variables graphify uses to pick a model backend
    (API keys, AWS_*, OLLAMA_*, AZURE_OPENAI_*) and with GRAPHIFY_HOOK_STRICT=0;
    graphify 0.9.91 picks Bedrock by itself whenever AWS_PROFILE is set, and does
    not read GRAPHIFY_BACKEND;
  - only graphify's additionalContext is passed on, in an answer this script
    builds: no decision, no rewritten input, no stop can come through;
  - without a graph, graphify is not started at all;
  - fail open: graphify missing, failing, slower than 2 s or printing anything
    that is not a PreToolUse answer leaves the tool call alone;
  - once per session, a notice when .git/index is newer than graph.json (the
    graph may describe code that changed) and one when the p95 of graphify's
    calls passes 500 ms over 20 or more calls.

Counters per session live in ~/.claude/squad/graph/<session>.json:

    python3 hooks/squad-graph.py --report <session>

prints {"search", "read", "nudged", "no-graph", "skipped"}. Standard library only.
"""
import json
import os
try:
    import fcntl
except ImportError:        # no POSIX locks: counters may undercount, nothing else changes
    fcntl = None
import re
import shutil
import subprocess
import sys
import time

STATE = os.path.join(os.path.expanduser("~"), ".claude", "squad", "graph")
KIND = {"Bash": "search", "Grep": "search", "Read": "read", "Glob": "read"}
COUNTERS = ("search", "read", "nudged", "no-graph", "skipped")
TIMEOUT = 2            # seconds graphify gets; the hook itself has 5 in the manifest
SLOW_MS = 500          # p95 above this raises the latency notice
SLOW_MIN = 20          # calls measured before the p95 means anything
KEEP_MS = 200          # most recent durations kept
# What graphify 0.9.91's llm.backend_detection_env_vars() lists, by family. None
# of it reaches the child. hook-guard makes no model call today; this keeps a
# later release from finding credentials, it does not make one impossible.
MODEL_ENV = re.compile(r"^(AWS_|OLLAMA_|AZURE_OPENAI_|GEMINI_|GOOGLE_|MOONSHOT_|ANTHROPIC_|"
                       r"OPENAI_|DEEPSEEK_)|_API_KEY$")


def state_path(session):
    return os.path.join(STATE, re.sub(r"[^A-Za-z0-9_-]", "_", session or "none") + ".json")


def load(session):
    """The session's state; a field of the wrong type starts over rather than
    silencing the hook for the rest of the session."""
    st = {k: 0 for k in COUNTERS}
    st.update(ms=[], said=[])
    try:
        with open(state_path(session), encoding="utf-8") as fh:
            old = json.load(fh)
    except (OSError, ValueError):
        return st
    if not isinstance(old, dict):
        return st
    for k in COUNTERS:
        if isinstance(old.get(k), int) and not isinstance(old[k], bool):
            st[k] = old[k]
    st["ms"] = [x for x in old.get("ms") or [] if isinstance(x, (int, float))] \
        if isinstance(old.get("ms"), list) else []
    st["said"] = [x for x in old["said"] if isinstance(x, str)] if isinstance(old.get("said"), list) else []
    return st


def update(session, change):
    """Load, change and save the state under a per-session lock, so parallel tool
    calls neither lose increments nor say a once-per-session notice twice.
    Returns what change() returns."""
    path = state_path(session)
    os.makedirs(STATE, exist_ok=True)
    with open(path + ".lock", "a") as lock:
        if fcntl:
            fcntl.flock(lock, fcntl.LOCK_EX)
        st = load(session)
        result = change(st)
        st["ms"] = st["ms"][-KEEP_MS:]
        tmp = f"{path}.tmp-{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(st, fh)
        os.replace(tmp, path)
    return result


def git_index(root):
    """.git/index of the project, through the gitdir line when .git is a file (a worktree)."""
    dot = os.path.join(root, ".git")
    if os.path.isfile(dot):
        with open(dot, encoding="utf-8") as fh:
            line = fh.read().strip()
        if not line.startswith("gitdir:"):
            return None
        dot = os.path.join(root, line[len("gitdir:"):].strip())
    return os.path.join(dot, "index")


def stale(root, graph):
    try:
        index = git_index(root)
        return bool(index) and os.stat(index).st_mtime > os.stat(graph).st_mtime
    except OSError:
        return False


def p95(ms):
    s = sorted(ms)
    return s[int(0.95 * (len(s) - 1))]


def guard(exe, kind, raw, root):
    """graphify's additionalContext, '' when it has nothing to say. Raises on anything else."""
    env = {k: v for k, v in os.environ.items() if not MODEL_ENV.search(k)}
    env.update(GRAPHIFY_BACKEND="none", GRAPHIFY_HOOK_STRICT="0")   # in case a later graphify reads them
    r = subprocess.run([exe, "hook-guard", kind], input=raw, capture_output=True, text=True,
                       env=env, cwd=root, timeout=TIMEOUT)
    if r.returncode != 0:
        raise RuntimeError(f"graphify exit {r.returncode}")
    if not r.stdout.strip():
        return ""
    answer = json.loads(r.stdout)
    out = answer.get("hookSpecificOutput") if isinstance(answer, dict) else None
    if not isinstance(out, dict):
        raise ValueError("not a PreToolUse answer")
    text = out.get("additionalContext")
    return text if isinstance(text, str) else ""


def answer(context):
    """The only shape this hook prints: context for the session, nothing that decides."""
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": context}},
                      ensure_ascii=False, separators=(",", ":")) + "\n"


def run(raw):
    if os.environ.get("SQUAD_GRAPHIFY") == "0":
        return ""
    try:
        call = json.loads(raw)
    except ValueError:
        return ""
    if not isinstance(call, dict):
        return ""
    tool = call.get("tool_name")
    kind = KIND.get(tool) if isinstance(tool, str) else None
    if not kind:
        return ""
    session = str(call.get("session_id") or "")
    root = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    out_dir = os.environ.get("GRAPHIFY_OUT", "").strip() or "graphify-out"
    graph = os.path.join(root, out_dir, "graph.json")
    if not os.path.isfile(graph):
        update(session, lambda st: st.__setitem__("no-graph", st["no-graph"] + 1))
        return ""
    exe = shutil.which("graphify")
    start = time.monotonic()
    text, failed = "", False
    try:
        if not exe:
            raise FileNotFoundError("graphify")
        text = guard(exe, kind, raw, root)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        failed = True
    elapsed = (time.monotonic() - start) * 1000
    is_stale = not failed and stale(root, graph)

    def record(st):
        st[kind] += 1
        if exe:
            st["ms"].append(elapsed)
        if failed:
            st["skipped"] += 1
            return []
        if text:
            st["nudged"] += 1
        notes = []
        if "stale" not in st["said"] and is_stale:
            st["said"].append("stale")
            notes.append("[squad-graph] The code graph is older than the last git change here, so it may "
                         "describe code that moved. Run `graphify update .` (code only, no model) before "
                         "trusting it.")
        if "slow" not in st["said"] and len(st["ms"]) >= SLOW_MIN and p95(st["ms"]) > SLOW_MS:
            st["said"].append("slow")
            notes.append(f"[squad-graph] graphify's hook takes {p95(st['ms']):.0f} ms at p95 in this "
                         "session. SQUAD_GRAPHIFY=0 turns it off.")
        return notes

    notes = update(session, record)
    context = "\n\n".join(filter(None, [text] + notes))
    return answer(context) if context else ""


def report(session):
    st = load(session)
    return json.dumps({k: st[k] for k in COUNTERS})


def main(argv):
    if argv[1:2] == ["--report"]:
        print(report(argv[2] if len(argv) > 2 else ""))
        return
    try:
        sys.stdout.write(run(sys.stdin.read()))
    except Exception:      # a hook bug must never stop the tool call
        pass


if __name__ == "__main__":
    main(sys.argv)
