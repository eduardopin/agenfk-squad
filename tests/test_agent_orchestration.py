#!/usr/bin/env python3
"""The agent-orchestration skill: frontmatter, the rules, the criteria list and no private context.

Self-checking: the exit code is the verdict. Reads the skill only; writes nothing.

    python3 tests/test_agent_orchestration.py

To also check the skill for names that must not be published (a project, a
customer, a person), list them in SQUAD_PRIVATE_TERMS, comma-separated. The
list lives in your environment, never in the repository.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(ROOT, "skills", "agent-orchestration")
RULES = 14
# Names from the squad's earlier, non-English layout; the public map renames them all.
LOCAL_NAMES = (r"orquestracao", r"squad-vigia", r"squad-vez", r"squad-troca", r"DESPACHO",
               r"\bverificador\b", r"\bdesenvolvimento\b", r"\bprodutos?\b")
SESSION_ID = r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"


def read(*parts):
    with open(os.path.join(SKILL, *parts), encoding="utf-8") as fh:
        return fh.read()


def private_pattern(env_terms):
    terms = [re.escape(t.strip()) for t in env_terms.split(",") if t.strip()]
    return re.compile("|".join(["/" + r"home/[a-z][a-z0-9_-]*/", r"/Users/[A-Za-z]+/", SESSION_ID]
                               + list(LOCAL_NAMES) + terms), re.I)


PRIVATE = private_pattern(os.environ.get("SQUAD_PRIVATE_TERMS", ""))


def section(md, number):
    m = re.search(r"(?ms)^## %d\. .*?(?=^## \d+\. |\Z)" % number, md)
    assert m, "section %d missing" % number
    return m.group(0)


def rules(md):
    """{n: body} for every `### Rn.` heading, in document order."""
    parts = re.split(r"(?m)^### R(\d+)\. ", md)
    return [(int(parts[i]), parts[i + 1]) for i in range(1, len(parts), 2)]


def criteria(md):
    return re.findall(r"(?ms)^- \[ \] (.*?)(?=^- \[ \] |\Z)", section(md, 5))


def test_frontmatter_names_the_skill_and_its_triggers():
    md = read("SKILL.md")
    assert md.startswith("---\nname: agent-orchestration\n"), md[:80]
    front = md.split("---")[1]
    assert re.findall(r"(?m)^(\w+):", front) == ["name", "description"], front
    desc = re.search(r"(?m)^description: (.+)$", front).group(1)
    assert len(desc) <= 700, len(desc)
    assert ": " not in desc or desc.startswith(("'", '"')), "quote YAML colon-space"
    for trigger in ("coordinator", "transfer", "ask another agent", "A2A", "acceptance criteria"):
        assert trigger in desc, trigger


def test_skill_md_is_lean():
    assert len(read("SKILL.md").splitlines()) <= 260


def test_rules_run_from_r1_to_r14_in_order_and_each_says_why():
    found = rules(read("SKILL.md"))
    assert [n for n, _ in found] == list(range(1, RULES + 1)), [n for n, _ in found]
    for n, body in found:
        assert len(" ".join(body.split())) >= 150, "R%d is a heading with no reason" % n


def test_criteria_list_is_a_checklist_that_cites_existing_rules():
    md = read("SKILL.md")
    items = criteria(md)
    assert len(items) == 9, items
    defined = {n for n, _ in rules(md)}
    for item in items:
        cited = re.search(r"\((R\d+(?:/R\d+)*)\)\.?\s*$", item)
        assert cited, "criterion cites no rule: " + item
        for ref in cited.group(1).split("/"):
            assert int(ref[1:]) in defined, ref


def test_every_rule_a_criterion_cites_carries_its_proof():
    md = read("SKILL.md")
    body = dict(rules(md))
    cited = {int(r) for item in criteria(md) for r in re.findall(r"R(\d+)", item)}
    assert cited, "no criterion cites a rule"
    for n in sorted(cited):
        assert "*Proof:*" in body[n], "R%d is an acceptance criterion with no proof" % n


def test_failure_codes_are_closed_sets_of_english_identifiers():
    body = dict(rules(read("SKILL.md")))
    refusal = re.findall(r"`([^`]+)`", re.search(r"(?s)a closed set\s*\((.*?)\)", body[3]).group(1))
    consult = re.findall(r"`([^`]+)`", re.search(r"(?s)as a \*\*code\*\*\s*\((.*?)\)", body[5]).group(1))
    assert refusal == ["target_not_allowed", "ceiling", "ping_pong", "entry_denied",
                       "not_in_directory", "other"], refusal
    assert len(consult) == 10 and len(set(consult)) == 10, consult
    for code in refusal + consult:
        assert re.fullmatch(r"[a-z]+(_[a-z]+)*", code), code
    assert "`unknown_id`" in body[6], "R6 must refuse with the R5 code"


def test_states_use_the_a2a_names():
    body = dict(rules(read("SKILL.md")))[12]
    for state in ("working", "completed", "rejected", "input-required", "canceled", "failed"):
        assert "`%s`" % state in body, state


def test_english_throughout():
    md = read("SKILL.md")
    stray = sorted({ch for ch in md if ord(ch) > 127} - {"→", ">"})
    assert not stray, stray


def test_squad_components_it_names_exist():
    md = read("SKILL.md")
    named = set(re.findall(r"`(squad-[a-z-]+)`", md))
    assert "squad-watch" in named, named
    for name in named:
        assert os.path.exists(os.path.join(ROOT, "hooks", name + ".py")), name
    assert "DISPATCH DENIED" in md


def test_the_private_check_catches_what_it_is_given():
    planted = private_pattern("acme-internal, Jane Roe")
    home = "/" + "home/someone/x"  # built here so the tree never holds a home path verbatim
    for text in ("see " + home, "the acme-internal worker", "ask jane roe",
                 "session 12345678-abcd-4ef0-8123-456789abcdef", "the squad-vigia hook"):
        assert planted.search(text), text
    assert not planted.search("a coordinator consults a specialist")


def test_nothing_private_in_the_skill():
    for dirpath, _, files in os.walk(SKILL):
        for name in files:
            path = os.path.join(dirpath, name)
            assert not os.path.islink(path), path
            with open(path, encoding="utf-8") as fh:
                for i, line in enumerate(fh, 1):
                    hit = PRIVATE.search(line)
                    assert not hit, "%s:%d %r" % (os.path.relpath(path, ROOT), i, hit.group(0))


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("ok  ", name)
            except Exception as e:  # an AssertionError or any unexpected error fails
                failures += 1
                print("FAIL", name, "-", e)
    sys.exit(1 if failures else 0)
