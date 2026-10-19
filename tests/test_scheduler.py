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


def test_a_named_resource_never_matches_a_path_glob():
    assert not waves.overlap("state:prod", "s*") and not waves.overlap("s*", "state:prod")


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
                    {"id": "zzzz0000", "title": "z", "description": "Depends on: abcd00"}])
    except waves.PlanError as e:
        assert "abcd00" in str(e) and "ambiguous" in str(e)
    else:
        raise AssertionError("an ambiguous id was accepted")


def test_cli_prints_json_that_round_trips():
    p = cli({"cards": [card("a"), card("b", deps=["a"])]}, "--json")
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out["waves"] == [["a"], ["b"]] and out["ready"] == ["a"]
    p = cli({"cards": [card("a"), card("b", deps=["a"])]})
    assert p.returncode == 0 and "Wave 1" in p.stdout and "Critical path" in p.stdout, p.stdout


def item(cid, desc="", status="TODO", parent=None, title=None):
    d = {"id": cid, "title": title or cid, "status": status, "description": desc}
    if parent:
        d["parentId"] = parent
    return d


def test_dependency_lines_written_as_markdown_are_read():
    for line in ("- Depends on: aaaa0000", "* Depends on: aaaa0000", "**Depends on:** aaaa0000",
                 "1. Depends on: aaaa0000", "2) Depends on: aaaa0000",
                 "Depends on: aaaa0000 (consumes the schema, and the API)"):
        cards = waves.load([item("aaaa0000"), item("bbbb0000", line)])
        assert cards["bbbb0000"]["depends_on"] == ["aaaa0000"], line


def test_every_id_on_a_dependency_line_counts():
    for line in ("Depends on: aaaa0000 and cccc0000", "Depends on: aaaa0000 cccc0000",
                 "Depends on: aaaa0000; cccc0000"):
        cards = waves.load([item("aaaa0000"), item("cccc0000"), item("bbbb0000", line)])
        assert cards["bbbb0000"]["depends_on"] == ["aaaa0000", "cccc0000"], line
        r = waves.plan(cards)
        assert r["waves"] == [["aaaa0000", "cccc0000"], ["bbbb0000"]], (line, r["waves"])


def test_placeholders_mean_no_dependency_and_no_resource():
    for line in ("Depends on: none", "Depends on: N/A", "Depends on: -", "Depends on: nothing"):
        assert waves.load([item("aaaa0000", line)])["aaaa0000"]["depends_on"] == [], line
    cards = waves.load([item("a", "Touches: none"), item("b", "Touches: none")])
    assert waves.plan(cards)["waves"] == [["a", "b"]], "'none' is not a shared resource"


def test_an_unreadable_dependency_line_stops_the_plan():
    try:
        waves.load([item("aaaa0000"), item("bbbb0000", "Depends on: aaaa0000/v2")])
    except waves.PlanError as e:
        assert "bbbb0000" in str(e) and "aaaa0000/v2" in str(e), e
    else:
        raise AssertionError("a dependency that is not an id was dropped in silence")


def test_a_comment_on_a_touches_entry_is_ignored():
    cards = waves.load([item("a", "Touches: hooks/x.py (router)"), item("b", "Touches: ./hooks/x.py")])
    assert cards["a"]["touches"] == ["hooks/x.py"]
    assert len(waves.plan(cards)["waves"]) == 2


def test_a_directory_and_a_glob_overlap_when_their_fixed_parts_meet():
    for x, y in (("docs/", "**/*.md"), ("src/", "*.py"), ("a/b/", "a/*/c.py"), ("docs/*.md", "docs/a*.md")):
        assert waves.overlap(x, y) and waves.overlap(y, x), (x, y)
    for x, y in (("src/a", "src/ab.py"), ("docs/", "src/*.py"), ("contract:a*", "contract:ab")):
        assert not waves.overlap(x, y) and not waves.overlap(y, x), (x, y)


def test_parent_plans_the_leaves_under_an_epic():
    items = [item("e1"), item("s1", parent="e1"), item("t1", parent="s1"), item("t2", parent="s1"),
             item("s2", "Depends on: s1", parent="e1"), item("other", parent="e2")]
    cards = waves.load(items, parent="e1")
    r = waves.plan(cards)
    assert r["waves"] == [["t1", "t2"], ["s2"]], r["waves"]   # s2 waits on story s1 = on its tasks


def test_parent_accepts_a_done_dependency_in_another_epic():
    items = [item("aaaa0000", status="DONE", parent="e0e0e0e0"),
             item("bbbb0000", "Depends on: aaaa0000", parent="e1e1e1e1")]
    p = cli(items, "--parent", "e1e1e1e1", "--json")
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["waves"] == [["bbbb0000"]]


def test_an_open_dependency_outside_the_epic_blocks_the_card_and_its_dependents():
    items = [item("aaaa0000", parent="e0e0e0e0"),
             item("bbbb0000", "Depends on: aaaa0000", parent="e1e1e1e1"),
             item("cccc0000", "Depends on: bbbb0000", parent="e1e1e1e1"),
             item("dddd0000", parent="e1e1e1e1")]
    r = waves.plan(waves.load(items, parent="e1e1e1e1"))
    assert r["waves"] == [["dddd0000"]], r["waves"]
    assert [b["card"] for b in r["blocked"]] == ["bbbb0000", "cccc0000"], r["blocked"]
    assert r["blocked"][0]["waiting_on"] == ["aaaa0000"]


def test_a_parent_that_matches_nothing_is_an_error_not_an_empty_plan():
    for args in (("--parent", "zzzz"), ("--parent", "e")):
        p = cli([item("a", parent="e1"), item("b", parent="e2")], *args)
        assert p.returncode == 2 and "--parent" in p.stderr, (args, p.returncode, p.stdout, p.stderr)
    p = cli([])
    assert p.returncode == 2 and "no cards" in p.stderr, p.stderr
    p = cli([item("a", status="DONE")])
    assert p.returncode == 0 and "every card is done" in p.stdout


def test_bad_cards_are_refused():
    for cards, words in (([card("a"), card("a")], "twice"), ([card("a", size=0)], "at least 1"),
                         ([card("a", size="big")], "whole number")):
        try:
            plan(cards)
        except waves.PlanError as e:
            assert words in str(e), (words, e)
        else:
            raise AssertionError(f"accepted: {cards}")
    try:
        plan([card("a")], max_width=0)
    except waves.PlanError:
        pass
    else:
        raise AssertionError("width 0 was accepted")
    r = waves.plan(waves.load({"cards": [{"id": 1, "depends_on": [2]}, {"id": 2}]}))
    assert r["waves"] == [["2"], ["1"]], "numeric ids are read as text"


def test_the_cycle_named_is_the_cycle_even_after_a_tail():
    try:
        plan([card("d", deps=["a"]), card("a", deps=["b"]), card("b", deps=["a"])])
    except waves.PlanError as e:
        assert str(e) == "dependency cycle: a -> b -> a", e
    else:
        raise AssertionError("cycle accepted")


def test_the_critical_path_follows_the_heavier_branch():
    r = plan([card("light"), card("heavy", size=4), card("join", deps=["light", "heavy"])])
    assert r["critical_path"] == ["heavy", "join"] and r["critical_length"] == 5, r


def test_a_large_card_starts_before_a_small_one_when_width_is_short():
    r = plan([card("small"), card("large", size=5)], max_width=1)
    assert r["waves"] == [["large"], ["small"]], r["waves"]
    r = plan([card("small"), card("large", size=5)])
    assert r["waves"] == [["small", "large"]], "inside a wave, cards keep the plan's order"


def test_cards_that_depend_on_each_other_are_not_reported_as_colliding():
    r = plan([card("a", touches=["x.py"]), card("b", deps=["a"], touches=["x.py"])])
    assert r["conflicts"] == [], r["conflicts"]


def test_a_short_word_is_never_taken_for_an_id_prefix():
    try:
        waves.load([item("be0000aa"), item("cccc0000", "Depends on: be")])
    except waves.PlanError as e:
        assert "be" in str(e) and "at least 6" in str(e), e
    else:
        raise AssertionError("'be' was read as a prefix of be0000aa")


def test_a_block_on_a_story_binds_its_tasks():
    items = [item("e1e1e1e1"), item("51515151", parent="e1e1e1e1"),
             item("52525252", "Depends on: 51515151\nTouches: api/", parent="e1e1e1e1"),
             item("t1t1t1t1", parent="51515151"), item("t2t2t2t2", parent="52525252"),
             item("t3t3t3t3", "Touches: api/routes.py", parent="52525252")]
    cards = waves.load(items, parent="e1e1e1e1")
    assert cards["t2t2t2t2"]["depends_on"] == ["t1t1t1t1"], cards["t2t2t2t2"]
    assert cards["t2t2t2t2"]["touches"] == ["api/"]
    r = waves.plan(cards)
    assert r["waves"] == [["t1t1t1t1"], ["t2t2t2t2"], ["t3t3t3t3"]], r["waves"]   # t2, t3 share api/
    r = waves.plan(waves.load(items))                                              # no --parent: same
    assert r["waves"] == [["t1t1t1t1"], ["t2t2t2t2"], ["t3t3t3t3"]], r["waves"]


def test_a_block_on_an_epic_binds_every_card_under_it():
    items = [item("x0x0x0x0"), item("e1e1e1e1", "Depends on: x0x0x0x0"),
             item("a1a1a1a1", parent="e1e1e1e1"), item("b1b1b1b1", parent="e1e1e1e1")]
    r = waves.plan(waves.load(items))
    assert r["waves"] == [["x0x0x0x0"], ["a1a1a1a1", "b1b1b1b1"]], r["waves"]
    assert "e1e1e1e1" not in sum(r["waves"], []), "a container is never scheduled"


def test_a_block_reaches_grandchildren_and_a_task_keeps_its_own_files():
    items = [item("x0x0x0x0"), item("e1e1e1e1", "Depends on: x0x0x0x0"),
             item("s1s1s1s1", "Touches: a.py, b.py", parent="e1e1e1e1"),
             item("t1t1t1t1", "Touches: a.py", parent="s1s1s1s1"),
             item("t2t2t2t2", "Touches: b.py", parent="s1s1s1s1"),
             item("t3t3t3t3", parent="s1s1s1s1")]
    cards = waves.load(items)
    assert cards["t1t1t1t1"]["depends_on"] == ["x0x0x0x0"], "the epic's wait reaches a grandchild"
    assert cards["t3t3t3t3"]["touches"] == ["a.py", "b.py"], "a task with no files takes the story's"
    r = waves.plan(cards)
    assert r["waves"] == [["x0x0x0x0"], ["t1t1t1t1", "t2t2t2t2"], ["t3t3t3t3"]], r["waves"]


def test_parent_links_that_loop_do_not_hang():
    items = [item("aaaaaaaa", parent="bbbbbbbb"), item("bbbbbbbb", parent="aaaaaaaa"),
             item("cccccccc", "Depends on: aaaaaaaa")]
    p = subprocess.run([sys.executable, SCRIPT, "-"], input=json.dumps(items),
                       capture_output=True, text=True, timeout=10)
    assert p.returncode in (0, 2), p.stderr


def test_a_card_waiting_on_its_own_container_is_named():
    try:
        waves.load([item("51515151"), item("t1t1t1t1", "Depends on: 51515151", parent="51515151")])
    except waves.PlanError as e:
        assert "t1t1t1t1" in str(e) and "contains it" in str(e), e
    else:
        raise AssertionError("a task waiting on its own story was accepted")


def test_a_parent_id_that_exists_wins_over_a_longer_one():
    items = [item("e1"), item("e10"), item("a", parent="e1"), item("b", parent="e10")]
    assert waves.plan(waves.load(items, parent="e1"))["waves"] == [["a"]]


def test_blocked_cards_say_which_wait_is_outside_and_are_not_reported_done():
    items = [item("o0o0o0o0", parent="e0e0e0e0"),
             item("t1t1t1t1", "Depends on: o0o0o0o0", parent="e1e1e1e1"),
             item("t2t2t2t2", "Depends on: t1t1t1t1", parent="e1e1e1e1")]
    p = cli(items, "--parent", "e1e1e1e1")
    assert p.returncode == 0, p.stderr
    assert "every card is done" not in p.stdout, p.stdout
    assert "o0o0o0o0 (outside, open)" in p.stdout and "t1t1t1t1 (blocked)" in p.stdout, p.stdout
    r = waves.plan(waves.load(items, parent="e1e1e1e1"))
    assert r["blocked"][1] == {"card": "t2t2t2t2", "waiting_on": ["t1t1t1t1"], "outside": [False]}


def test_a_parent_with_no_cards_under_it_is_an_error():
    p = cli([item("a", parent="e1"), item("b", parent="e1")], "--parent", "a")
    assert p.returncode == 2 and "no cards to plan" in p.stderr, (p.returncode, p.stderr)


def test_an_id_of_zero_is_an_id():
    r = waves.plan(waves.load({"cards": [{"id": 0}, {"id": 1, "depends_on": [0]}]}))
    assert r["waves"] == [["0"], ["1"]], r["waves"]


# --- the router --------------------------------------------------------------

def test_the_owners_request_routes_to_the_scheduler_first():
    hints = route("Precisamos paralelizar as tarefas para entregar mais coisas ao mesmo tempo")
    assert hints and "`scheduler`" in hints[0], hints
    for prompt in ("qual o caminho crítico desse epic?", "monta o grafo de dependências dos cards",
                   "what can run in parallel in this plan?", "show me the critical path of this plan",
                   "parallelise the epic into waves", "split this roadmap into waves", "quais cards podem rodar em paralelo?",
                   "which cards can run in parallel?", "what can we run in parallel?",
                   "o que podemos paralelizar?"):
        hints = route(prompt)
        assert hints and "`scheduler`" in hints[0], (prompt, hints)


def test_the_scheduler_ignores_graphs_and_paths_that_are_not_a_plan():
    for prompt in ("show the npm dependency graph", "dependency graph of this python package",
                   "critical path of the request latency", "critical path rendering in the browser",
                   "é um projeto longo, quanto falta?"):
        assert not any("`scheduler`" in h for h in route(prompt)), prompt


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
