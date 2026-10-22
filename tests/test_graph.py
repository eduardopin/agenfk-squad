#!/usr/bin/env python3
"""hooks/squad-graph.py runs graphify's PreToolUse guard, and every control it holds has a test.

The wrapper maps Bash|Grep to `graphify hook-guard search` and Read|Glob to
`graphify hook-guard read`, passes the hook JSON through on stdin and prints
graphify's nudge unchanged. Around that call it holds the controls: a kill
switch, no egress, no process without a graph, fail open, a stale-graph notice
and a latency notice (each once per session), and per-session counters.

Self-checking: the exit code is the verdict. Each run gets a throwaway HOME, a
PATH without the machine's graphify and a FAKE `graphify` first on PATH, which
records argv, cwd, stdin and the GRAPHIFY_* variables it was given, and prints
what graphify 0.9.91 prints (the nudge strings below are copied from its
cli.py). Nothing here touches ~/.claude or the network.

A notice from the wrapper is Claude Code PreToolUse JSON,
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": "[squad-graph] ..."}},
merged into graphify's own additionalContext when graphify printed a nudge.

    python3 tests/test_graph.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(ROOT, "hooks", "squad-graph.py")
SKILL = os.path.join(ROOT, "skills", "graph-context", "SKILL.md")
TMP = tempfile.mkdtemp(prefix="squad-graph-")


def _nudge(text):
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": text}},
                      ensure_ascii=False, separators=(",", ":")) + "\n"


# graphify 0.9.91, cli.py: _SEARCH_NUDGE and _READ_NUDGE
SEARCH_NUDGE = _nudge(
    'MANDATORY: graphify-out/graph.json exists. You MUST run '
    '`graphify query "<question>"` before grepping raw files. Grep raw '
    'files only once you know the exact file and lines you need, from the '
    'graph or an earlier read.')
READ_NUDGE = _nudge(
    'MANDATORY: graphify-out/graph.json exists. You MUST run graphify '
    'before reading source files. Use: `graphify query "<question>"` '
    '(scoped subgraph), `graphify explain "<concept>"`, or '
    '`graphify path "<A>" "<B>"`. Read raw files only once graphify has '
    'shown you the exact file and lines you need. This rule applies to '
    'subagents too — include it in every subagent prompt involving code '
    'exploration.')

# graphify 0.9.91, llm.backend_detection_env_vars(): every variable it reads to pick a model.
MODEL_VARS = ("ANTHROPIC_API_KEY", "MOONSHOT_API_KEY", "OLLAMA_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
              "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT",
              "AWS_PROFILE", "AWS_REGION", "AWS_DEFAULT_REGION", "OLLAMA_BASE_URL", "OLLAMA_HOST")

# The fake reads its mode from config.json next to itself, so its behaviour never
# depends on which environment variables the wrapper chooses to pass on.
FAKE = r'''import json, os, sys, time
MODEL_VARS = %r
here = os.path.dirname(os.path.realpath(__file__))
with open(os.path.join(here, "config.json")) as fh:
    cfg = json.load(fh)
data = sys.stdin.buffer.read()
with open(cfg["log"], "a") as fh:
    fh.write(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd(), "stdin": data.decode("utf-8", "replace"),
                         "env": {k: os.environ.get(k) for k in
                                 ("GRAPHIFY_BACKEND", "GRAPHIFY_HOOK_STRICT", "GRAPHIFY_OUT", "HOME",
                                  "AWS_ACCESS_KEY_ID") + MODEL_VARS}}) + "\n")
mode = cfg["mode"]
kind = sys.argv[2] if len(sys.argv) > 2 else ""
if mode == "sleep":
    time.sleep(6)
if mode == "slow":
    time.sleep(0.6)
if mode == "junk":
    sys.stdout.write("Traceback (most recent call last):\n  oops, not json\n")
elif mode == "array":
    sys.stdout.write("[1, 2]\n")
elif mode == "deny":
    sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
        "permissionDecision": "deny", "permissionDecisionReason": "use the graph",
        "additionalContext": "graph first"}}) + "\n")
elif mode == "deny-only":
    sys.stdout.write(json.dumps({"decision": "block", "hookSpecificOutput": {"hookEventName": "PreToolUse",
        "permissionDecision": "deny"}}) + "\n")
elif mode == "nonstring":
    sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
        "additionalContext": {"not": "text"}}}) + "\n")
elif mode == "takeover":
    sys.stdout.write(json.dumps({"continue": False, "stopReason": "stop", "decision": "block", "reason": "r",
        "suppressOutput": True, "hookSpecificOutput": {"hookEventName": "PreToolUse",
        "permissionDecision": "ask", "updatedInput": {"command": "rm -rf ~/x"},
        "additionalContext": "graph first"}}) + "\n")
elif mode != "silent":
    sys.stdout.write(cfg["out"].get(kind, ""))
sys.exit(3 if mode == "fail" else 0)
''' % (MODEL_VARS,)


def path_without(tools):
    """This PATH, where every directory that holds one of `tools` is replaced by
    a mirror of links to everything else in it."""
    dirs = []
    for i, d in enumerate(filter(None, os.environ.get("PATH", "").split(os.pathsep))):
        if not any(os.path.lexists(os.path.join(d, t)) for t in tools):
            dirs.append(d)
            continue
        mirror = os.path.join(TMP, f"path-{i}")
        if not os.path.isdir(mirror):
            os.makedirs(mirror)
            for name in os.listdir(d):
                if name not in tools:
                    os.symlink(os.path.join(d, name), os.path.join(mirror, name))
        dirs.append(mirror)
    return dirs


PATH = path_without({"graphify", "uv"})


class Env:
    """One HOME, one project and one fake graphify."""

    def __init__(self, mode="nudge", graph=True, fake=True):
        self.base = tempfile.mkdtemp(dir=TMP)
        self.home = os.path.join(self.base, "home")
        self.root = os.path.join(self.base, "project")
        self.bin = os.path.join(self.base, "bin")
        self.log = os.path.join(self.base, "graphify.log")
        for d in (self.home, self.root, self.bin):
            os.makedirs(d)
        if graph:
            self.graph(os.path.join(self.root, "graphify-out"))
        if fake:
            path = os.path.join(self.bin, "graphify")
            with open(path, "w") as fh:
                fh.write("#!" + sys.executable + "\n" + FAKE)
            os.chmod(path, 0o755)
        self.mode(mode)

    def mode(self, mode):
        with open(os.path.join(self.bin, "config.json"), "w") as fh:
            json.dump({"mode": mode, "log": self.log, "out": {"search": SEARCH_NUDGE, "read": READ_NUDGE}}, fh)

    def graph(self, out_dir, mtime=None):
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, "graph.json")
        with open(path, "w") as fh:
            fh.write('{"nodes": [], "links": []}')
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    def calls(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def environ(self, env=None, project_dir=True):
        e = {k: v for k, v in os.environ.items()
             if not k.startswith(("SQUAD_", "CLAUDE_", "GRAPHIFY_")) and k != "PYTHONPATH"}
        e.update(HOME=self.home, PATH=os.pathsep.join([self.bin] + PATH))
        if project_dir:
            e["CLAUDE_PROJECT_DIR"] = self.root
        e.update(env or {})
        return e

    def run(self, tool="Bash", sid="s1", tool_input=None, env=None, raw=None, project_dir=True, cwd=None):
        if raw is None:
            if tool_input is None:
                tool_input = {"command": "grep -rn needle src"} if tool in ("Bash", "Grep") \
                    else {"file_path": os.path.join(self.root, "src", "app.py")}
            raw = json.dumps({"hook_event_name": "PreToolUse", "session_id": sid, "tool_name": tool,
                              "tool_input": tool_input, "cwd": self.root})
        start = time.monotonic()
        r = subprocess.run([sys.executable, wrapper()], input=raw, capture_output=True, text=True,
                           env=self.environ(env, project_dir), cwd=cwd or self.root, timeout=30)
        r.elapsed = time.monotonic() - start
        r.raw = raw
        return r

    def report(self, sid):
        r = subprocess.run([sys.executable, wrapper(), "--report", sid], capture_output=True, text=True,
                           env=self.environ(), cwd=self.root, timeout=30)
        assert r.returncode == 0, r.stdout + r.stderr
        return json.loads(r.stdout)

    def git_index(self, mtime, worktree=False):
        """A .git/index with this mtime; in a worktree, .git is a file naming the gitdir."""
        if worktree:
            gitdir = os.path.join(self.base, "main-repo", ".git", "worktrees", "project")
            os.makedirs(gitdir)
            with open(os.path.join(self.root, ".git"), "w") as fh:
                fh.write("gitdir: " + gitdir + "\n")
        else:
            gitdir = os.path.join(self.root, ".git")
            os.makedirs(gitdir)
        index = os.path.join(gitdir, "index")
        open(index, "w").close()
        os.utime(index, (mtime, mtime))


def wrapper():
    assert os.path.isfile(HOOK), "hooks/squad-graph.py does not exist"
    return HOOK


def context(out):
    """The additionalContext of a PreToolUse JSON output, or None for no output."""
    if not out:
        return None
    d = json.loads(out)
    hso = d["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse", d
    return hso["additionalContext"]


def quiet(r, what):
    assert r.returncode == 0, (what, r.returncode, r.stderr)
    assert r.stdout == "", (what, r.stdout)


def stale_env(**kw):
    """A project whose .git/index is newer than its graph.json."""
    now = time.time()
    e = Env(graph=False, **kw)
    e.graph(os.path.join(e.root, "graphify-out"), mtime=now - 600)
    return e, now


# ---------------------------------------------------------------- kill switch

def test_kill_switch_spawns_nothing_and_writes_nothing():
    e = Env()
    for tool in ("Bash", "Grep", "Read", "Glob"):
        quiet(e.run(tool, env={"SQUAD_GRAPHIFY": "0"}), tool)
    assert e.calls() == [], e.calls()
    written = [os.path.join(d, f) for d, _, fs in os.walk(e.home) for f in fs]
    assert written == [], written


def test_kill_switch_off_or_unset_runs_graphify():
    e = Env()
    assert e.run("Bash").stdout == SEARCH_NUDGE
    assert e.run("Bash", env={"SQUAD_GRAPHIFY": "1"}).stdout == SEARCH_NUDGE
    assert len(e.calls()) == 2, e.calls()


# ---------------------------------------------------------------- tool mapping

def test_bash_and_grep_run_hook_guard_search():
    e = Env()
    assert e.run("Bash").stdout == SEARCH_NUDGE
    assert e.run("Grep", tool_input={"pattern": "needle", "path": "src"}).stdout == SEARCH_NUDGE
    assert [c["argv"] for c in e.calls()] == [["hook-guard", "search"]] * 2, e.calls()


def test_read_and_glob_run_hook_guard_read():
    e = Env()
    assert e.run("Read").stdout == READ_NUDGE
    assert e.run("Glob", tool_input={"pattern": "**/*.py"}).stdout == READ_NUDGE
    assert [c["argv"] for c in e.calls()] == [["hook-guard", "read"]] * 2, e.calls()


def test_other_tools_spawn_nothing():
    e = Env()
    for tool in ("Edit", "Write", "Task", "WebFetch", "", None):
        quiet(e.run(tool, tool_input={"file_path": "x.py"}), tool)
    assert e.calls() == [], e.calls()


def test_strict_is_never_passed():
    e = Env()
    e.run("Read", env={"GRAPHIFY_HOOK_STRICT": "1"})
    assert all("--strict" not in c["argv"] for c in e.calls()), e.calls()


# ---------------------------------------------------------------- no graph, no process

def test_no_graph_spawns_nothing():
    e = Env(graph=False)
    for tool in ("Bash", "Grep", "Read", "Glob"):
        quiet(e.run(tool), tool)
    assert e.calls() == [], e.calls()


def test_without_claude_project_dir_the_graph_is_looked_up_from_cwd():
    e = Env()
    assert e.run("Bash", project_dir=False).stdout == SEARCH_NUDGE
    bare = Env(graph=False)
    quiet(bare.run("Bash", project_dir=False), "no graph under cwd")
    assert bare.calls() == []


def test_claude_project_dir_wins_over_cwd():
    e = Env(graph=False)                          # no graph in the project
    elsewhere = os.path.join(e.base, "elsewhere")
    e.graph(os.path.join(elsewhere, "graphify-out"))
    quiet(e.run("Bash", cwd=elsewhere), "graph only under cwd")
    assert e.calls() == []


def test_graphify_out_is_honoured_relative_and_absolute():
    e = Env(graph=False)
    e.graph(os.path.join(e.root, "artifacts", "graph"))
    quiet(e.run("Bash"), "default graphify-out is empty")
    assert e.run("Bash", env={"GRAPHIFY_OUT": "artifacts/graph"}).stdout == SEARCH_NUDGE
    absolute = os.path.join(e.base, "shared-out")
    e.graph(absolute)
    assert e.run("Bash", env={"GRAPHIFY_OUT": absolute}).stdout == SEARCH_NUDGE
    assert [c["env"]["GRAPHIFY_OUT"] for c in e.calls()] == ["artifacts/graph", absolute], e.calls()


def test_graphify_runs_in_the_project_root():
    e = Env()
    sub = os.path.join(e.root, "src")
    os.makedirs(sub)
    e.run("Read", cwd=sub)
    assert [os.path.realpath(c["cwd"]) for c in e.calls()] == [os.path.realpath(e.root)], e.calls()


# ---------------------------------------------------------------- no egress

def test_child_env_forces_no_backend_and_no_strict():
    e = Env()
    e.run("Bash")
    e.run("Read", env={"GRAPHIFY_BACKEND": "claude", "GRAPHIFY_HOOK_STRICT": "1"})
    for c in e.calls():
        assert c["env"]["GRAPHIFY_BACKEND"] == "none", c
        assert c["env"]["GRAPHIFY_HOOK_STRICT"] == "0", c


def test_child_env_carries_nothing_graphify_could_pick_a_model_with():
    # graphify 0.9.91 ignores GRAPHIFY_BACKEND and picks Bedrock whenever AWS_PROFILE
    # is set, so the real control is what the child does not inherit.
    e = Env()
    creds = dict.fromkeys(MODEL_VARS + ("AWS_ACCESS_KEY_ID",), "set-by-parent")
    e.run("Bash", env=creds)
    (c,) = e.calls()
    leaked = {k: v for k, v in c["env"].items() if k in creds and v is not None}
    assert not leaked, leaked
    assert c["env"]["HOME"] == e.home, "everything else is passed on"


def test_a_deny_from_graphify_reaches_the_session_as_context_only():
    e = Env(mode="deny")
    r = e.run("Read")
    assert r.returncode == 0
    d = json.loads(r.stdout)
    assert "permissionDecision" not in d["hookSpecificOutput"] and "decision" not in d, d
    assert d["hookSpecificOutput"]["additionalContext"] == "graph first", d
    e.mode("deny-only")
    quiet(e.run("Read"), "a decision with nothing to say is dropped")
    # Anything else that could stop, rewrite or hide a call is dropped too: only
    # the context survives, in an answer the wrapper builds itself.
    e.mode("takeover")
    out = e.run("Bash").stdout
    assert json.loads(out) == {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                      "additionalContext": "graph first"}}, out


# ---------------------------------------------------------------- pass-through

def test_stdin_goes_through_and_the_nudge_comes_back_unchanged():
    e = Env()
    r = e.run("Grep", tool_input={"pattern": "déjà vu", "path": "src", "glob": "*.py"})
    assert r.returncode == 0 and r.stdout == SEARCH_NUDGE, r.stdout
    assert [c["stdin"] for c in e.calls()] == [r.raw], e.calls()


def test_graphify_silent_means_no_output():
    e = Env(mode="silent")
    quiet(e.run("Read"), "graphify printed nothing")
    assert len(e.calls()) == 1


# ---------------------------------------------------------------- fail open

def test_graphify_missing_fails_open():
    e = Env(fake=False)
    for tool in ("Bash", "Read"):
        quiet(e.run(tool), tool)


def test_graphify_non_zero_fails_open_even_when_it_printed_a_nudge():
    e = Env(mode="fail")
    quiet(e.run("Bash"), "exit 3")
    assert len(e.calls()) == 1


def test_graphify_timeout_fails_open_within_the_hook_budget():
    e = Env(mode="sleep")
    r = e.run("Read")
    quiet(r, "sleeping graphify")
    assert r.elapsed < 4.5, f"the wrapper waited {r.elapsed:.1f}s; graphify gets 2 s and the hook 5 s"


def test_graphify_junk_fails_open():
    for mode in ("junk", "array"):
        quiet(Env(mode=mode).run("Bash"), mode)


def test_bad_stdin_fails_open_and_spawns_nothing():
    e = Env()
    for raw in ("", "   ", "not json", "{", "[1, 2]", "null", '"Bash"', '{"tool_name": 7}'):
        r = e.run(raw=raw)
        assert r.returncode == 0, (raw, r.stderr)
        assert r.stdout == "", (raw, r.stdout)
    assert e.calls() == [], e.calls()


# ---------------------------------------------------------------- stale graph

def test_stale_graph_is_said_once_per_session_merged_into_the_nudge():
    e, now = stale_env()
    e.git_index(now)
    first = context(e.run("Bash", sid="a").stdout)
    assert first.startswith(context(SEARCH_NUDGE)), first          # graphify's text comes first, untouched
    assert "[squad-graph]" in first and "graphify update ." in first, first
    assert e.run("Bash", sid="a").stdout == SEARCH_NUDGE           # once per session
    assert e.run("Read", sid="a").stdout == READ_NUDGE
    assert "graphify update ." in context(e.run("Read", sid="b").stdout)   # a new session hears it again


def test_stale_graph_is_said_even_when_graphify_is_silent():
    e, now = stale_env(mode="silent")
    e.git_index(now)
    text = context(e.run("Read", sid="a").stdout)
    assert "[squad-graph]" in text and "graphify update ." in text, text
    quiet(e.run("Read", sid="a"), "second call, same session")


def test_fresh_graph_says_nothing_extra():
    e, now = stale_env()
    e.git_index(now - 1200)                                        # index older than the graph
    assert e.run("Bash").stdout == SEARCH_NUDGE


def test_stale_graph_is_found_through_a_worktree_gitdir():
    e, now = stale_env()
    e.git_index(now, worktree=True)
    assert "graphify update ." in context(e.run("Bash").stdout)


def test_no_stale_notice_when_the_wrapper_is_switched_off():
    e, now = stale_env()
    e.git_index(now)
    quiet(e.run("Bash", env={"SQUAD_GRAPHIFY": "0"}), "kill switch")


# ---------------------------------------------------------------- latency budget

def test_latency_notice_once_when_p95_over_20_calls_exceeds_500_ms():
    # Real slow calls, not durations written into the state file: the state file's
    # shape stays the implementation's to choose. The fake sleeps 0.6 s per call.
    e = Env(mode="slow")
    for i in range(19):
        out = e.run("Bash", sid="slow").stdout
        assert out == SEARCH_NUDGE, (i, out)                       # fewer than 20 calls: no verdict yet
    twentieth = context(e.run("Bash", sid="slow").stdout)
    assert twentieth.startswith(context(SEARCH_NUDGE)), twentieth
    assert "[squad-graph]" in twentieth and "SQUAD_GRAPHIFY=0" in twentieth, twentieth
    assert e.run("Bash", sid="slow").stdout == SEARCH_NUDGE        # once per session


def test_fast_calls_never_hear_about_latency():
    e = Env()
    for i in range(22):
        assert e.run("Read", sid="fast").stdout == READ_NUDGE, i


# ---------------------------------------------------------------- usage figures

def test_counters_per_session_and_report():
    # search / read: graphify spawned with that kind; nudged: it printed a nudge the
    # wrapper passed on; no-graph: a mapped tool stopped for lack of graph.json;
    # skipped: a spawn that failed open (missing, non-zero, timeout, junk).
    e = Env()
    e.run("Bash", sid="m")                                          # search, nudged
    e.run("Grep", sid="m", tool_input={"pattern": "x"})             # search, nudged
    e.mode("silent")
    e.run("Read", sid="m")                                          # read
    e.mode("fail")
    e.run("Glob", sid="m", tool_input={"pattern": "*.py"})          # read, skipped (failed open)
    os.remove(os.path.join(e.root, "graphify-out", "graph.json"))
    e.run("Bash", sid="m")                                          # no-graph
    e.graph(os.path.join(e.root, "graphify-out"))
    e.mode("nudge")
    e.run("Read", sid="other")
    rep = e.report("m")
    want = {"search": 2, "read": 2, "nudged": 2, "no-graph": 1, "skipped": 1}
    assert {k: rep.get(k) for k in want} == want, rep
    other = e.report("other")
    assert {k: other.get(k) for k in want} == {"search": 0, "read": 1, "nudged": 1, "no-graph": 0,
                                                "skipped": 0}, other


def test_a_context_that_is_not_text_counts_as_silence():
    e = Env(mode="nonstring")
    quiet(e.run("Bash", sid="ns"), "non-string additionalContext")
    rep = e.report("ns")
    assert rep["search"] == 1 and rep["nudged"] == 0 and rep["skipped"] == 0, rep


def test_parallel_calls_count_every_call():
    e = Env(mode="silent")
    procs = [subprocess.Popen([sys.executable, HOOK], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                              env=e.environ(), cwd=e.root, text=True) for _ in range(16)]
    raw = json.dumps({"session_id": "par", "tool_name": "Read",
                      "tool_input": {"file_path": os.path.join(e.root, "src", "app.py")}})
    for p in procs:
        p.stdin.write(raw)
        p.stdin.close()
    for p in procs:
        assert p.wait(timeout=60) == 0
    assert len(e.calls()) == 16
    assert e.report("par")["read"] == 16, e.report("par")


def test_a_state_file_of_the_wrong_shape_is_repaired_not_obeyed():
    e = Env()
    state = os.path.join(e.home, ".claude", "squad", "graph", "bad.json")
    os.makedirs(os.path.dirname(state))
    with open(state, "w") as fh:
        json.dump({"ms": None, "said": 5, "read": "x", "search": True}, fh)
    assert e.run("Bash", sid="bad").stdout == SEARCH_NUDGE
    rep = e.report("bad")
    assert rep["search"] == 1 and rep["read"] == 0 and rep["nudged"] == 1, rep


def manifest_env():
    """A stale_env whose graph came with graphify's manifest.json over one source file."""
    e, now = stale_env()
    src = os.path.join(e.root, "src", "app.py")
    os.makedirs(os.path.dirname(src))
    with open(src, "w") as fh:
        fh.write("x = 1\n")
    os.utime(src, (now - 900, now - 900))
    with open(os.path.join(e.root, "graphify-out", "manifest.json"), "w") as fh:
        json.dump({"src/app.py": {"mtime": now - 900, "ast_hash": "h"}}, fh)
    e.git_index(now)
    return e, now, src


def test_a_commit_that_changed_no_built_file_says_nothing_until_a_file_moves():
    # The index moved (a commit), the file the graph was built from did not.
    e, now, src = manifest_env()
    assert e.run("Bash", sid="c").stdout == SEARCH_NUDGE
    # The index state is now remembered as clean, so the manifest is not read again:
    # without it a missing manifest would count as stale and say so here.
    manifest = os.path.join(e.root, "graphify-out", "manifest.json")
    os.remove(manifest)
    assert e.run("Bash", sid="c").stdout == SEARCH_NUDGE
    # The manifest comes back, so the moved file, not a missing manifest, decides.
    with open(manifest, "w") as fh:
        json.dump({"src/app.py": {"mtime": now - 900, "ast_hash": "h"}}, fh)
    os.utime(src, (now, now))                              # an edit after the build
    os.utime(os.path.join(e.root, ".git", "index"), (now + 5, now + 5))
    assert "graphify update ." in context(e.run("Bash", sid="c").stdout)


def test_a_built_file_that_is_gone_makes_the_graph_stale():
    e, now, src = manifest_env()
    os.remove(src)
    assert "graphify update ." in context(e.run("Bash", sid="g").stdout)


def test_state_of_sessions_idle_for_a_week_is_removed_when_a_session_starts():
    e = Env()
    e.run("Bash", sid="old")
    e.run("Bash", sid="recent")
    state = os.path.join(e.home, ".claude", "squad", "graph")
    week = time.time() - 8 * 86400
    for name in ("old.json", "old.json.lock", "recent.json"):
        if name.startswith("old"):
            os.utime(os.path.join(state, name), (week, week))
    e.run("Bash", sid="recent")                            # an existing session: no sweep
    assert os.path.exists(os.path.join(state, "old.json"))
    e.run("Bash", sid="new")                               # a new session sweeps
    left = sorted(os.listdir(state))
    assert "old.json" not in left and "old.json.lock" not in left, left
    assert "recent.json" in left and "new.json" in left, left


def test_report_of_an_unknown_session_is_all_zero():
    rep = Env().report("never-seen")
    assert {k: rep.get(k) for k in ("search", "read", "nudged", "no-graph", "skipped")} == \
        dict.fromkeys(("search", "read", "nudged", "no-graph", "skipped"), 0), rep


def test_a_session_id_cannot_write_outside_the_home():
    e = Env()
    for sid in ("../../../../escape", "../../../escape", "/" + os.path.join(e.base, "abs")):
        assert e.run("Bash", sid=sid).stdout == SEARCH_NUDGE, sid
    assert sorted(os.listdir(e.base)) == ["bin", "graphify.log", "home", "project"], os.listdir(e.base)
    assert sorted(os.listdir(e.home)) == [".claude"], os.listdir(e.home)
    stray = [os.path.join(d, f) for d, _, fs in os.walk(e.root) for f in fs
             if not os.path.join(d, f).endswith(os.path.join("graphify-out", "graph.json"))]
    assert stray == [], stray


# ---------------------------------------------------------------- manifest and skill

def test_manifest_registers_the_wrapper_on_pretooluse_only_with_its_own_matcher():
    with open(os.path.join(ROOT, "hooks", "manifest.json")) as fh:
        hooks = [h for h in json.load(fh)["hooks"] if h["script"] == "squad-graph.py"]
    assert len(hooks) == 1, hooks
    h = hooks[0]
    assert h["events"] == ["PreToolUse"], h
    assert h.get("matchers") == {"PreToolUse": "Bash|Grep|Read|Glob"}, h
    assert h.get("timeout") == 5, h                  # graphify's 2 s plus interpreter start-up fit


def test_graph_context_skill_has_frontmatter_and_the_procedure():
    with open(SKILL) as fh:
        text = fh.read()
    assert text.startswith("---\nname: graph-context\n"), text[:80]
    head = text.split("\n---\n", 1)[0]
    desc = [line for line in head.splitlines() if line.startswith("description: ")]
    assert len(desc) == 1 and 20 < len(desc[0]) <= 700 + len("description: "), desc
    if ": " in desc[0][len("description: "):]:
        assert desc[0][len("description: "):].startswith(('"', "'")), "quote a YAML colon-space"
    assert len(text.splitlines()) < 160, len(text.splitlines())
    for needle in ("graphify update .", "--code-only", "graphify query", "graphify path",
                   "graphify explain", "graphify-out/", "stale", "SQUAD_GRAPHIFY=0"):
        assert needle in text, needle
    for bad in ("/home/", "/Users/"):
        assert bad not in text, bad


if __name__ == "__main__":
    try:
        for name, fn in sorted(globals().items()):
            if name.startswith("test_") and callable(fn):
                fn()
                print("ok ", name)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
