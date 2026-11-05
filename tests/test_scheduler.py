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
sys.dont_write_bytecode = True   # no __pycache__ inside the skill, which ships as is
import waves  # noqa: E402


def card(cid, deps=(), touches=None, size=1, **extra):
    """A card with its own file by default: a card with no Touches never
    shares a wave, so tests about dependencies give every card one."""
    return {"id": cid, "depends_on": list(deps),
            "touches": [f"{cid}.py"] if touches is None else list(touches), "size": size, **extra}


def cli(data, *args):
    return subprocess.run([sys.executable, "-I", "-B", SCRIPT, *args, "-"], input=json.dumps(data),
                          capture_output=True, text=True, timeout=60)


def run(data, *args):
    """The forecast the CLI prints; the cards go in `{"cards": [...]}` unless
    already an export."""
    p = cli({"cards": data} if isinstance(data, list) and data and "touches" in data[0] else data, *args)
    assert p.returncode == 0, (p.returncode, p.stdout, p.stderr)
    return json.loads(p.stdout)


def plan(cards, max_width=None, done=()):
    args = (["--max-width", str(max_width)] if max_width else []) + (["--done", ",".join(done)] if done else [])
    return run({"cards": cards}, *args)


def refused(data, *args):
    """The error the CLI prints when it refuses to plan (exit 2)."""
    p = cli(data, *args)
    assert p.returncode == 2 and not p.stdout, (p.returncode, p.stdout, p.stderr)
    return p.stderr


def wave_of(result, cid):
    for i, wave in enumerate(result["waves"]):
        if cid in wave:
            return i
    raise AssertionError(f"{cid} is in no wave: {result['waves']}")


def route(prompt):
    out = subprocess.run([sys.executable, ROUTER], input=json.dumps({"prompt": prompt}),
                         capture_output=True, text=True).stdout
    return [line for line in out.splitlines() if line.startswith("[squad route]")]


def item(cid, desc="", status="TODO", parent=None, touches=True):
    """An `agenfk list --json` card. Its own file goes on a Touches line
    unless `touches` is False or the description already has one."""
    if touches and "touches:" not in desc.lower():
        desc = (desc + "\n" if desc else "") + f"Touches: {cid}.py"
    d = {"id": cid, "title": cid, "status": status, "description": desc}
    if parent:
        d["parentId"] = parent
    return d


def box(cid, desc="", **kw):
    """A container (an epic or a story) with no files of its own."""
    return item(cid, desc, touches=False, **kw)


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
    err = refused({"cards": [card("a", deps=["c"]), card("b", deps=["a"]), card("c", deps=["b"]), card("d")]})
    assert "cycle" in err and all(x in err for x in ("a", "b", "c")), err
    assert " d" not in err.split("cycle", 1)[1], err
    assert "cycle" in refused({"cards": [card("a", deps=["b"]), card("b", deps=["a"])]})


def test_a_dependency_on_a_card_that_does_not_exist_is_refused():
    assert "ghost" in refused({"cards": [card("a", deps=["ghost"])]})


def test_width_is_capped_and_the_rest_waits():
    # The squad's ceiling is six fronts (beta #33), so six is the default and the maximum.
    r = plan([card(c) for c in "abcdefg"])
    assert [len(w) for w in r["waves"]] == [6, 1], r["waves"]
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
    assert r["waves"] == [["b"]] and r["ready"] == ["b"] and r["done"] == ["a"], r


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
    assert b["deps"] == {"aaaa1111-0000-0000-0000-000000000000"}, b
    assert b["touches"] == ["api/models.py", "api/routes.py"], b
    assert cards["dddd4444-0000-0000-0000-000000000000"]["size"] == 3
    r = run(items)
    short = [[cid[:8] for cid in w] for w in r["waves"]]
    # cccc3333 has no Touches, so nothing proves it can run beside bbbb2222.
    assert short == [["bbbb2222"], ["cccc3333"], ["dddd4444"]], short
    assert [c[:8] for c in r["unknown_touches"]] == ["cccc3333"], "a card with no Touches line is flagged"
    assert r["critical_length"] == 4, r


def test_an_ambiguous_short_id_is_refused():
    err = refused([item("abcd0001"), item("abcd0002"), item("zzzz0000", "Depends on: abcd00")])
    assert "abcd00" in err and "ambiguous" in err, err


def test_cli_prints_json_that_round_trips():
    p = cli({"cards": [card("a"), card("b", deps=["a"])]})
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out["waves"] == [["a"], ["b"]] and out["ready"] == ["a"]
    assert {"critical_path", "conflicts", "blocked", "waiting_on", "unknown_touches", "done"} <= set(out)


def test_dependency_lines_written_as_markdown_are_read():
    for line in ("- Depends on: aaaa0000", "* Depends on: aaaa0000", "**Depends on:** aaaa0000",
                 "1. Depends on: aaaa0000", "2) Depends on: aaaa0000", "> Depends on: aaaa0000",
                 "Depends on: aaaa0000 (consumes the schema, and the API)"):
        r = run([item("aaaa0000"), item("bbbb0000", line)])
        assert r["waves"] == [["aaaa0000"], ["bbbb0000"]], (line, r["waves"])


def test_every_id_on_a_dependency_line_counts():
    for line in ("Depends on: aaaa0000 and cccc0000", "Depends on: aaaa0000 cccc0000",
                 "Depends on: aaaa0000; cccc0000", "Depends on: aaaa0000, cccc0000"):
        items = [item("aaaa0000"), item("dddd0000"), item("cccc0000", "Depends on: dddd0000"),
                 item("bbbb0000", line)]
        assert waves.load(items)["bbbb0000"]["deps"] == {"aaaa0000", "cccc0000"}, line
        r = run(items)
        assert r["waves"] == [["aaaa0000", "dddd0000"], ["cccc0000"], ["bbbb0000"]], (line, r["waves"])


def test_placeholders_mean_no_dependency_and_no_resource():
    for line in ("Depends on: none", "Depends on: N/A", "Depends on: -", "Depends on: nothing"):
        assert run([item("aaaa0000", line)])["waves"] == [["aaaa0000"]], line
    # 'none' is not a shared file; it is no Touches at all, which never runs beside others.
    r = run([item("a", "Touches: none"), item("b", "Touches: none")])
    assert r["unknown_touches"] == ["a", "b"] and len(r["waves"]) == 2, r


def test_an_unreadable_dependency_line_stops_the_plan():
    err = refused([item("aaaa0000"), item("bbbb0000", "Depends on: aaaa0000/v2")])
    assert "bbbb0000" in err and "aaaa0000/v2" in err, err


def test_a_comment_on_a_touches_entry_is_ignored():
    cards = waves.load([item("a", "Touches: hooks/x.py (router)"), item("b", "Touches: ./hooks/x.py")])
    assert cards["a"]["touches"] == ["hooks/x.py"]
    r = run([item("a", "Touches: hooks/x.py (router)"), item("b", "Touches: ./hooks/x.py")])
    assert len(r["waves"]) == 2 and r["conflicts"][0]["resource"] == "hooks/x.py", r


def test_a_directory_and_a_glob_overlap_when_their_fixed_parts_meet():
    for x, y in (("docs/", "**/*.md"), ("src/", "*.py"), ("a/b/", "a/*/c.py"), ("docs/*.md", "docs/a*.md")):
        assert waves.overlap(x, y) and waves.overlap(y, x), (x, y)
    for x, y in (("src/a", "src/ab.py"), ("docs/", "src/*.py"), ("contract:a*", "contract:ab")):
        assert not waves.overlap(x, y) and not waves.overlap(y, x), (x, y)
    r = run([item("a", "Touches: docs/"), item("b", "Touches: **/*.md")])
    assert r["conflicts"] == [{"cards": ["a", "b"], "resource": "docs/ ~ **/*.md"}], r["conflicts"]
    # Bold around the key or the line is markup; ** inside a path is a glob.
    for line in ("Touches: src/**/x.py", "**Touches:** src/**/x.py", "**Touches: src/**/x.py**"):
        r = run([item("a", line), item("b", "Touches: src/a/x.py")])
        assert len(r["waves"]) == 2, (line, r)


def test_parent_plans_the_leaves_under_an_epic():
    items = [box("e1"), box("s1", parent="e1"), item("t1", parent="s1"), item("t2", parent="s1"),
             item("s2", "Depends on: s1", parent="e1"), box("e2"), item("other", parent="e2")]
    r = run(items, "--parent", "e1")
    assert r["waves"] == [["t1", "t2"], ["s2"]], r["waves"]   # s2 waits on story s1 = on its tasks


def test_parent_accepts_a_done_dependency_in_another_epic():
    items = [box("e0e0e0e0"), box("e1e1e1e1"), item("aaaa0000", status="DONE", parent="e0e0e0e0"),
             item("bbbb0000", "Depends on: aaaa0000", parent="e1e1e1e1")]
    assert run(items, "--parent", "e1e1e1e1")["waves"] == [["bbbb0000"]]


def test_an_open_dependency_outside_the_epic_blocks_the_card_and_its_dependents():
    items = [box("e0e0e0e0"), box("e1e1e1e1"), item("aaaa0000", parent="e0e0e0e0"),
             item("bbbb0000", "Depends on: aaaa0000", parent="e1e1e1e1"),
             item("cccc0000", "Depends on: bbbb0000", parent="e1e1e1e1"),
             item("dddd0000", parent="e1e1e1e1")]
    r = run(items, "--parent", "e1e1e1e1")
    assert r["waves"] == [["dddd0000"]], r["waves"]
    assert r["blocked"] == ["bbbb0000", "cccc0000"], r["blocked"]
    assert r["waiting_on"]["bbbb0000"] == ["aaaa0000"], r["waiting_on"]


def test_a_parent_that_matches_nothing_is_an_error_not_an_empty_plan():
    items = [box("e1"), box("e2"), item("a", parent="e1"), item("b", parent="e2")]
    for args in (("--parent", "zzzz"), ("--parent", "e")):
        assert "--parent" in refused(items, *args), args
    # An empty export or a finished plan is a forecast with nothing in it (beta's contract).
    assert run([])["waves"] == []
    r = run([item("a", status="DONE")])
    assert r["waves"] == [] and r["done"] == ["a"] and r["blocked"] == [], r


def test_bad_cards_are_refused():
    for cards, words in (([card("a"), card("a")], "duplicate"), ([card("a", size=0)], "positive integer"),
                         ([card("a", size="big")], "positive integer")):
        assert words in refused({"cards": cards}), (words, cards)
    assert cli({"cards": [card("a")]}, "--max-width", "0").returncode == 2, "width 0 was accepted"
    assert "string id" in refused({"cards": [{"id": 1, "depends_on": [2]}, {"id": 2}]})


def test_the_cycle_named_is_the_cycle_even_after_a_tail():
    err = refused({"cards": [card("d", deps=["a"]), card("a", deps=["b"]), card("b", deps=["a"])]})
    assert err.strip() == "PLAN_ERROR: dependency cycle: a -> b -> a", err


def test_the_critical_path_follows_the_heavier_branch():
    r = plan([card("light"), card("heavy", size=4), card("join", deps=["light", "heavy"])])
    assert r["critical_path"] == ["heavy", "join"] and r["critical_length"] == 5, r


def test_a_large_card_starts_before_a_small_one_when_width_is_short():
    r = plan([card("small"), card("large", size=5)], max_width=1)
    assert r["waves"] == [["large"], ["small"]], r["waves"]
    r = plan([card("small"), card("large", size=5)])
    assert r["waves"] == [["large", "small"]], "inside a wave, cards sort by id, whatever the export order"


def test_cards_that_depend_on_each_other_are_not_reported_as_colliding():
    r = plan([card("a", touches=["x.py"]), card("b", deps=["a"], touches=["x.py"])])
    assert r["conflicts"] == [], r["conflicts"]


def test_a_short_word_is_never_taken_for_an_id_prefix():
    err = refused([item("be0000aa"), item("cccc0000", "Depends on: be")])
    assert "be" in err and "at least 6" in err, err


def test_a_block_on_a_story_binds_its_tasks():
    items = [box("e1e1e1e1"), box("51515151", parent="e1e1e1e1"),
             item("52525252", "Depends on: 51515151\nTouches: api/", parent="e1e1e1e1"),
             item("t1t1t1t1", parent="51515151"), item("t2t2t2t2", parent="52525252", touches=False),
             item("t3t3t3t3", "Touches: api/routes.py", parent="52525252")]
    cards = waves.load(items, root="e1e1e1e1")
    assert cards["t2t2t2t2"]["deps"] == {"t1t1t1t1"}, cards["t2t2t2t2"]
    assert cards["t2t2t2t2"]["touches"] == ["api/"]
    r = run(items, "--parent", "e1e1e1e1")
    assert r["waves"] == [["t1t1t1t1"], ["t2t2t2t2"], ["t3t3t3t3"]], r["waves"]   # t2, t3 share api/
    r = run(items)                                                                 # no --parent: same
    assert r["waves"] == [["t1t1t1t1"], ["t2t2t2t2"], ["t3t3t3t3"]], r["waves"]


def test_a_block_on_an_epic_binds_every_card_under_it():
    items = [item("x0x0x0x0"), box("e1e1e1e1", "Depends on: x0x0x0x0"),
             item("a1a1a1a1", parent="e1e1e1e1"), item("b1b1b1b1", parent="e1e1e1e1")]
    r = run(items)
    assert r["waves"] == [["x0x0x0x0"], ["a1a1a1a1", "b1b1b1b1"]], r["waves"]
    assert "e1e1e1e1" not in sum(r["waves"], []), "a container is never scheduled"


def test_a_block_reaches_grandchildren_and_a_story_touches_bind_its_tasks():
    # A story's Touches add to each task's own instead of being replaced by
    # them: the story may name a shared contract no task repeats.
    items = [item("x0x0x0x0"), box("e1e1e1e1", "Depends on: x0x0x0x0"),
             item("s1s1s1s1", "Touches: a.py, b.py", parent="e1e1e1e1"),
             item("t1t1t1t1", "Touches: a.py", parent="s1s1s1s1"),
             item("t2t2t2t2", "Touches: b.py", parent="s1s1s1s1"),
             item("t3t3t3t3", parent="s1s1s1s1", touches=False)]
    cards = waves.load(items)
    assert cards["t1t1t1t1"]["deps"] == {"x0x0x0x0"}, "the epic's wait reaches a grandchild"
    assert cards["t3t3t3t3"]["touches"] == ["a.py", "b.py"], "a task with no files takes the story's"
    assert cards["t1t1t1t1"]["touches"] == ["a.py", "b.py"], "a task's files never drop the story's"
    r = run(items)
    assert r["waves"] == [["x0x0x0x0"], ["t1t1t1t1"], ["t2t2t2t2"], ["t3t3t3t3"]], r["waves"]
    items[2]["description"] = "Touches: contract:billing"
    items[3]["description"], items[4]["description"] = "Touches: a.py", "Touches: b.py"
    r = run(items)
    assert len(r["waves"]) == 4, "the story's contract still keeps its tasks apart"


def test_parent_links_that_loop_do_not_hang():
    items = [item("aaaaaaaa", parent="bbbbbbbb"), item("bbbbbbbb", parent="aaaaaaaa"),
             item("cccccccc", "Depends on: aaaaaaaa")]
    assert "parent cycle" in refused(items)


def test_a_card_waiting_on_its_own_container_is_named():
    err = refused([box("51515151"), item("t1t1t1t1", "Depends on: 51515151", parent="51515151")])
    assert "t1t1t1t1" in err and "cycle" in err, err


def test_a_parent_id_that_exists_wins_over_a_longer_one():
    items = [box("e1"), box("e10"), item("a", parent="e1"), item("b", parent="e10")]
    assert run(items, "--parent", "e1")["waves"] == [["a"]]


def test_blocked_cards_say_which_wait_is_outside_and_are_not_reported_done():
    items = [box("e0e0e0e0"), box("e1e1e1e1"), item("o0o0o0o0", parent="e0e0e0e0"),
             item("t1t1t1t1", "Depends on: o0o0o0o0", parent="e1e1e1e1"),
             item("t2t2t2t2", "Depends on: t1t1t1t1", parent="e1e1e1e1")]
    r = run(items, "--parent", "e1e1e1e1")
    assert r["waves"] == [] and r["done"] == [], r
    assert r["waiting_on"] == {"t1t1t1t1": ["o0o0o0o0"], "t2t2t2t2": ["t1t1t1t1"]}, r["waiting_on"]


def test_a_parent_with_no_cards_under_it_is_an_error():
    err = refused([box("e1"), item("a", parent="e1"), item("b", parent="e1")], "--parent", "a")
    assert "no cards to plan" in err, err


def test_an_id_must_be_text():
    assert "string id" in refused({"cards": [{"id": 0}, {"id": 1, "depends_on": [0]}]})


# --- the review findings on the first version (each one reproduced) ----------

def test_a_paused_card_is_blocked_not_ready():
    for status in ("PAUSED", "BLOCKED"):
        r = run([item("aaaa0000", status=status), item("bbbb0000")])
        assert r["ready"] == ["bbbb0000"] and r["blocked"] == ["aaaa0000"], (status, r)
        assert r["waiting_on"] == {"aaaa0000": []}, "its own status is the block"
    # under a paused story, and inside --parent
    items = [box("e1e1e1e1"), box("s1s1s1s1", status="PAUSED", parent="e1e1e1e1"),
             item("t1t1t1t1", parent="s1s1s1s1"), item("t2t2t2t2", parent="e1e1e1e1")]
    r = run(items, "--parent", "e1e1e1e1")
    assert r["waves"] == [["t2t2t2t2"]] and r["blocked"] == ["t1t1t1t1"], r


def test_an_archived_prerequisite_is_not_done():
    for status in ("ARCHIVED", "CANCELLED", "TRASHED"):
        r = run([item("aaaa0000", status=status), item("bbbb0000", "Depends on: aaaa0000")])
        assert r["waves"] == [] and r["blocked"] == ["aaaa0000", "bbbb0000"], (status, r)
        assert r["done"] == [], (status, r)
    r = run([item("aaaa0000", status="ARCHIVED"), item("bbbb0000", "Depends on: aaaa0000")],
            "--done", "aaaa0000")
    assert r["waves"] == [["bbbb0000"]], "--done is how the owner says an archived card was delivered"


def test_a_description_dependency_counts_in_every_input_shape():
    a = {"id": "a", "touches": ["a.py"]}
    b = {"id": "b", "touches": ["b.py"], "description": "Depends on: a"}
    for data in ([a, b], {"items": [a, b]}, {"cards": [a, b]}):
        assert run(data)["waves"] == [["a"], ["b"]], data
    c = {"id": "c", "depends_on": ["b"], "description": "Depends on: a\nTouches: c.py"}
    cards = waves.load({"cards": [a, b, c]})
    assert cards["c"]["deps"] == {"a", "b"} and cards["c"]["touches"] == ["c.py"], "fields and text combine"


def test_cards_without_touches_never_share_a_wave():
    r = run([item("a", touches=False), item("b", touches=False), item("c")])
    assert [len(w) for w in r["waves"]] == [1, 1, 1], r["waves"]
    assert r["unknown_touches"] == ["a", "b"], r


def test_a_parent_traversal_in_touches_is_refused():
    # src/../hooks/a.py is hooks/a.py; refusing it is how the collision cannot be missed.
    for path in ("src/../hooks/a.py", "../hooks/a.py", "./src/../../a.py"):
        err = refused([item("a", f"Touches: {path}"), item("b", "Touches: hooks/a.py")])
        assert "relative" in err, (path, err)
    r = run([item("a", "Touches: ./hooks//a.py"), item("b", "Touches: hooks/a.py")])
    assert len(r["waves"]) == 2, "a path written another way is still the same file"


def test_a_parent_cycle_is_refused_not_a_dropped_prerequisite():
    data = {"cards": [card("a", parentId="b"), card("b", parentId="a"), card("c", deps=["a"])]}
    assert "parent cycle" in refused(data)
    data["cards"][1]["parentId"] = None
    assert run(data)["waves"] == [["a"], ["c"]], "with the loop broken, c waits on a again"


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
