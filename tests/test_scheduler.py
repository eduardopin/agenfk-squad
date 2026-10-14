#!/usr/bin/env python3
"""The scheduler turns a long plan into waves of cards that can run together.

Covers `skills/parallel-plan/scripts/waves.py` (the arithmetic), the router rule
that points a request at the `scheduler` agent, and the agent's wiring.

Self-checking: the exit code is the verdict.

    python3 tests/test_scheduler.py
"""
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "skills", "parallel-plan", "scripts", "waves.py")
ROUTER = os.path.join(ROOT, "hooks", "squad-router.py")
sys.path.insert(0, os.path.dirname(SCRIPT))
import waves  # noqa: E402


def card(cid, deps=(), touches=(), size=1, title=None):
    return {"id": cid, "title": title or cid, "depends_on": list(deps),
            "touches": list(touches), "size": size}


def plan(cards, **kw):
    return waves.plan(waves.load({"cards": cards}), **kw)


def wave_of(result, cid):
    for i, wave in enumerate(result["waves"]):
        if cid in wave:
            return i
    raise AssertionError(f"{cid} is in no wave: {result['waves']}")


def cli(data, *args):
    return subprocess.run([sys.executable, SCRIPT, *args, "-"], input=json.dumps(data),
                          capture_output=True, text=True)


def route(prompt):
    out = subprocess.run([sys.executable, ROUTER], input=json.dumps({"prompt": prompt}),
                         capture_output=True, text=True).stdout
    return [line for line in out.splitlines() if line.startswith("[squad route]")]


# --- waves.py ----------------------------------------------------------------

def test_disjoint_cards_share_the_first_wave():
    r = plan([card("a", touches=["hooks/a.py"]), card("b", touches=["docs/b.md"]),
              card("c", touches=["agents/c.md"])])
    assert r["waves"] == [["a", "b", "c"]], r["waves"]
    assert r["ready"] == ["a", "b", "c"]


def test_same_file_without_a_dependency_is_serialised_and_reported():
    r = plan([card("a", touches=["hooks/squad-watch.py"]),
              card("b", touches=["hooks/squad-watch.py"])])
    assert wave_of(r, "a") != wave_of(r, "b"), r["waves"]
    assert r["conflicts"] == [{"cards": ["a", "b"], "resource": "hooks/squad-watch.py"}], r["conflicts"]


def test_a_directory_or_a_glob_overlaps_the_files_inside_it():
    r = plan([card("dir", touches=["hooks/"]), card("file", touches=["hooks/squad-watch.py"])])
    assert wave_of(r, "dir") != wave_of(r, "file")
    r = plan([card("glob", touches=["docs/*.md"]), card("file", touches=["docs/agents.md"])])
    assert wave_of(r, "glob") != wave_of(r, "file")
    r = plan([card("x", touches=["hooks/a.py"]), card("y", touches=["hooks/ab.py"])])
    assert r["waves"] == [["x", "y"]], "a shared prefix is not a shared file"


def test_named_resources_match_exactly():
    r = plan([card("a", touches=["state:org/identity"]), card("b", touches=["state:org/identity-2"])])
    assert r["waves"] == [["a", "b"]]
    r = plan([card("a", touches=["state:org/identity"]), card("b", touches=["state:org/identity"])])
    assert len(r["waves"]) == 2


def test_a_dependency_puts_the_dependent_in_a_later_wave():
    r = plan([card("b", deps=["a"]), card("a"), card("c", deps=["b"])])
    assert r["waves"] == [["a"], ["b"], ["c"]], r["waves"]
    assert r["ready"] == ["a"]


def test_a_cycle_is_refused_and_named():
    try:
        plan([card("a", deps=["c"]), card("b", deps=["a"]), card("c", deps=["b"]), card("d")])
    except waves.PlanError as e:
        msg = str(e)
        assert "cycle" in msg and all(x in msg for x in ("a", "b", "c")), msg
        assert " d" not in msg.split("cycle", 1)[1], msg
    else:
        raise AssertionError("a cycle was accepted")
    p = cli({"cards": [card("a", deps=["b"]), card("b", deps=["a"])]})
    assert p.returncode == 2 and "cycle" in p.stderr, (p.returncode, p.stderr)


def test_a_dependency_on_a_card_that_does_not_exist_is_refused():
    try:
        plan([card("a", deps=["ghost"])])
    except waves.PlanError as e:
        assert "ghost" in str(e)
    else:
        raise AssertionError("an unknown dependency was accepted")


def test_width_is_capped_and_the_rest_waits():
    r = plan([card(c) for c in "abcdef"])
    assert [len(w) for w in r["waves"]] == [4, 2], r["waves"]
    r = plan([card(c) for c in "abcdef"], max_width=3)
    assert [len(w) for w in r["waves"]] == [3, 3]


def test_the_critical_path_is_the_longest_chain_by_size():
    r = plan([card("a"), card("b", deps=["a"]), card("c", deps=["b"]), card("d", size=2)])
    assert r["critical_path"] == ["a", "b", "c"] and r["critical_length"] == 3, r
    r = plan([card("a"), card("b", deps=["a"]), card("d", size=5)])
    assert r["critical_path"] == ["d"] and r["critical_length"] == 5, r


def test_the_critical_chain_starts_first_when_width_is_short():
    cards = [card("s1"), card("s2"), card("s3"), card("x"), card("y", deps=["x"]), card("z", deps=["y"])]
    r = plan(cards, max_width=2)
    assert "x" in r["waves"][0], r["waves"]


def test_done_cards_release_their_dependents():
    r = plan([card("a"), card("b", deps=["a"])], done={"a"})
    assert r["waves"] == [["b"]] and r["ready"] == ["b"], r


def test_reads_agenfk_list_json_and_the_block_in_the_description():
    items = [
        {"id": "aaaa1111-0000-0000-0000-000000000000", "title": "Schema", "status": "DONE",
         "description": "Create the table.\n\nTouches: db/migrations/"},
        {"id": "bbbb2222-0000-0000-0000-000000000000", "title": "API", "status": "TODO",
         "description": "Expose it.\nDepends on: aaaa1111 (consumes the schema)\nTouches: api/routes.py, api/models.py"},
        {"id": "cccc3333-0000-0000-0000-000000000000", "title": "Docs", "status": "TODO",
         "description": "No block at all."},
        {"id": "dddd4444-0000-0000-0000-000000000000", "title": "Client", "status": "TODO",
         "description": "Depends on: bbbb2222, cccc3333\nTouches: web/client.ts\nSize: 3"},
    ]
    cards = waves.load(items)
    b = cards["bbbb2222-0000-0000-0000-000000000000"]
    assert b["depends_on"] == ["aaaa1111-0000-0000-0000-000000000000"], b
    assert b["touches"] == ["api/routes.py", "api/models.py"], b
    assert cards["dddd4444-0000-0000-0000-000000000000"]["size"] == 3
    r = waves.plan(cards)
    short = [[cid[:8] for cid in w] for w in r["waves"]]
    assert short == [["bbbb2222", "cccc3333"], ["dddd4444"]], short
    assert any("cccc3333" in u for u in r["unplaced_touches"]), "a card with no Touches line is flagged"


def test_an_ambiguous_short_id_is_refused():
    try:
        waves.load([{"id": "abcd0001", "title": "x", "description": ""},
                    {"id": "abcd0002", "title": "y", "description": ""},
                    {"id": "zzzz0000", "title": "z", "description": "Depends on: abcd"}])
    except waves.PlanError as e:
        assert "abcd" in str(e) and "ambiguous" in str(e)
    else:
        raise AssertionError("an ambiguous id was accepted")


def test_cli_prints_json_that_round_trips():
    p = cli({"cards": [card("a"), card("b", deps=["a"])]}, "--json")
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out["waves"] == [["a"], ["b"]] and out["ready"] == ["a"]
    p = cli({"cards": [card("a"), card("b", deps=["a"])]})
    assert p.returncode == 0 and "Wave 1" in p.stdout and "Critical path" in p.stdout, p.stdout


# --- the router --------------------------------------------------------------

def test_the_owners_request_routes_to_the_scheduler_first():
    hints = route("Precisamos paralelizar as tarefas para entregar mais coisas ao mesmo tempo")
    assert hints and "`scheduler`" in hints[0], hints
    for prompt in ("qual o caminho crítico desse epic?", "monta o grafo de dependências dos cards",
                   "what can run in parallel in this plan?", "show me the critical path",
                   "parallelise the epic into waves"):
        hints = route(prompt)
        assert hints and "`scheduler`" in hints[0], (prompt, hints)


def test_the_scheduler_does_not_steal_github_or_product_requests():
    for prompt, owner in (("abre um worktree para esse card", "`github`"),
                          ("quanto falta para o MVP?", "`product`"),
                          ("can i merge this pr?", "`verifier`")):
        hints = route(prompt)
        assert not any("`scheduler`" in h for h in hints), (prompt, hints)
        assert any(owner in h for h in hints), (prompt, hints)


# --- the agent's wiring ------------------------------------------------------

def frontmatter(path):
    with open(path) as fh:
        text = fh.read()
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    assert m, path
    return dict(line.split(": ", 1) for line in m.group(1).splitlines() if ": " in line)


def test_the_agent_cannot_edit_files():
    fm = frontmatter(os.path.join(ROOT, "agents", "scheduler.md"))
    assert fm["name"] == "scheduler"
    tools = {t.strip() for t in fm["tools"].split(",")}
    assert not tools & {"Edit", "Write", "NotebookEdit"}, tools
    assert "Bash" in tools


def test_the_agent_is_listed_where_dispatch_is_decided():
    for rel in ("CLAUDE.md", "README.md", "docs/agents.md", "docs/agenfk-integration.md"):
        with open(os.path.join(ROOT, rel)) as fh:
            assert "`scheduler`" in fh.read(), rel
    with open(os.path.join(ROOT, "flows", "squad-delivery.json")) as fh:
        todo = json.load(fh)["steps"][0]
    assert todo["name"] == "TODO" and "scheduler" in todo["exitCriteria"]
    assert os.path.exists(os.path.join(ROOT, "skills", "parallel-plan", "SKILL.md"))


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok ", name)
