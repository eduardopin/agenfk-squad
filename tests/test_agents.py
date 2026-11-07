#!/usr/bin/env python3
"""Every agent in agents/*.md carries the same standing-order sections, in the same order.

Self-checking: the exit code is the verdict.

    python3 tests/test_agents.py

Each agent answers the same six questions in a fixed place, as level-2
headings, in this order:

    ## Authority       what it may do
    ## Trigger         when it runs
    ## Approval gate   what needs someone else's approval
    ## Escalation      when it stops and asks
    ## Steps           how it does the work
    ## Never           what it never does

Other sections (Deliverable, Skills, a method of its own) may sit between or
after them. Each of the six has a body. The frontmatter keeps `name` (equal to
the file name), `description` and `tools`.
"""
import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENTS = os.path.join(ROOT, "agents")
SECTIONS = ("Authority", "Trigger", "Approval gate", "Escalation", "Steps", "Never")
FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)


def headings(text):
    """[(level, title, body)] for every Markdown heading outside fenced code."""
    out, fenced = [], False
    for line in text.splitlines():
        if line.startswith("```"):
            fenced = not fenced
        m = None if fenced else re.match(r"^(#{1,6}) (.+?)\s*$", line)
        if m:
            out.append([len(m.group(1)), m.group(2), []])
        elif out:
            out[-1][2].append(line)
    return [(lvl, title, "\n".join(body)) for lvl, title, body in out]


def section_problems(text):
    """What is wrong with an agent's standing-order sections; empty when nothing is."""
    hs = headings(text)
    found = [t for lvl, t, _ in hs if lvl == 2 and t in SECTIONS]
    problems = []
    missing = [s for s in SECTIONS if s not in found]
    if missing:
        problems.append("missing section(s): " + ", ".join(missing))
    if found != [s for s in SECTIONS if s in found]:                # a repeat also lands here
        problems.append("sections out of order or repeated: " + ", ".join(found)
                        + " (expected " + ", ".join(SECTIONS) + ")")
    for i, (lvl, title, body) in enumerate(hs):
        if lvl == 2 and title in SECTIONS:
            nested = any(l > 2 for l, _, _ in hs[i + 1:i + 2])     # a subsection counts as body
            if not body.strip() and not nested:
                problems.append(f"empty section: {title}")
    return problems


def agent_files():
    return sorted(glob.glob(os.path.join(AGENTS, "*.md")))


def test_there_are_agents_to_check():
    assert len(agent_files()) >= 11, agent_files()


def test_every_agent_has_the_standing_order_sections_in_order():
    failures = []
    for path in agent_files():
        with open(path, encoding="utf-8") as fh:
            for p in section_problems(fh.read()):
                failures.append(f"{os.path.relpath(path, ROOT)}: {p}")
    assert not failures, "\n" + "\n".join(failures)


def test_frontmatter_keeps_name_description_and_tools():
    failures = []
    for path in agent_files():
        with open(path, encoding="utf-8") as fh:
            m = FRONTMATTER.match(fh.read())
        rel = os.path.relpath(path, ROOT)
        if not m:
            failures.append(f"{rel}: no frontmatter")
            continue
        keys = dict(l.split(":", 1) for l in m.group(1).splitlines() if ":" in l)
        for k in ("name", "description", "tools"):
            if not keys.get(k, "").strip():
                failures.append(f"{rel}: frontmatter without {k}")
        if keys.get("name", "").strip() != os.path.basename(path)[:-3]:
            failures.append(f"{rel}: name {keys.get('name', '').strip()!r} is not the file name")
    assert not failures, "\n" + "\n".join(failures)


GOOD = "---\nname: x\n---\n# x\n\n" + "".join(f"## {s}\n\nbody\n\n" for s in SECTIONS)


def test_the_checker_names_a_missing_section():
    assert section_problems(GOOD) == [], section_problems(GOOD)
    bad = GOOD.replace("## Escalation\n\nbody\n\n", "")
    assert section_problems(bad) == ["missing section(s): Escalation"], section_problems(bad)


def test_the_checker_ignores_a_heading_inside_code_and_a_level_3_heading():
    bad = GOOD.replace("## Never\n", "```\n## Never\n```\n### Never\n")
    assert "missing section(s): Never" in section_problems(bad), section_problems(bad)


def test_the_checker_refuses_the_wrong_order_and_an_empty_section():
    swapped = GOOD.replace("## Steps", "## TMP").replace("## Never", "## Steps").replace("## TMP", "## Never")
    assert any(p.startswith("sections out of order") for p in section_problems(swapped)), section_problems(swapped)
    empty = GOOD.replace("## Trigger\n\nbody\n\n", "## Trigger\n\n")
    assert section_problems(empty) == ["empty section: Trigger"], section_problems(empty)
    sub = GOOD.replace("## Steps\n\nbody\n\n", "## Steps\n\n### First\n\nbody\n\n")
    assert section_problems(sub) == [], section_problems(sub)


def read_agent(name):
    with open(os.path.join(AGENTS, name + ".md"), encoding="utf-8") as fh:
        return fh.read()


def flat(text):
    """Lowercase with whitespace collapsed, so a rule survives being re-wrapped."""
    return re.sub(r"\s+", " ", text).lower()


def section(name, title):
    """The body of one level-2 section of an agent, flattened (subsections included)."""
    return section_of(read_agent(name), title)


def section_of(text, title):
    """The body of one level-2 section of a text; any level-1 or level-2 heading ends it."""
    hs, out, on = headings(text), [], False
    for lvl, t, body in hs:
        if lvl <= 2:
            on = lvl == 2 and t == title
        if on:
            out.append(body)
    return flat(" ".join(out))


# Each ported rule is pinned by a short phrase, in the section it belongs to.
PORTED = (
    # (agent, section, phrase)
    ("context", "Steps", "only a handoff the main session wrote"),
    ("context", "Steps", "no commit today"),
    ("finops", "Steps", "resource arn"),
    ("finops", "Steps", "metric over 14 days"),
    ("finops", "Steps", "owner tag"),
    ("finops", "Steps", "risk of removing it"),
    ("finops", "Steps", "removal is a card for `infra`"),
    ("github", "Steps", "fails in seconds"),
    ("github", "Steps", "billing or permissions"),
    ("infra", "Steps", "ignoring the os certificate bundle"),
    ("infra", "Steps", "aws_ca_bundle"),
    ("infra", "Steps", "never disable verification"),
    ("infra", "Steps", "observability in the same pr"),
    ("infra", "Steps", "provisional"),
    ("infra", "Never", "-upgrade"),
    ("infra", "Never", "tf_plugin_cache_dir"),
    ("verifier", "Steps", "run the project's terraform gate"),
    ("verifier", "Steps", "must be replaced"),
    ("verifier", "Steps", "terraform plan -lock=false"),
    ("verifier", "Steps", "read-only role cannot write the lock"),
    ("verifier", "Steps", "unless the owner asks in writing"),
    ("verifier", "Steps", "was not compared with real state"),
    ("product", "Steps", "one card per planned pull request"),
    ("product", "Steps", "optimistic / likely / conservative"),
    ("product", "Steps", "unapplied is half done"),
    ("developer", "Steps", "agenfk verify <id> --plan"),
    ("developer", "Steps", "do not run the full suite yourself"),
    ("developer", "Never", "hooks fail open"),
    ("developer", "Never", "empty stdin"),
    ("developer", "Never", "invalid json"),
)


def test_every_ported_standing_order_is_pinned_in_its_section():
    missing = [f"{a} / {s}: {p!r}" for a, s, p in PORTED if p not in section(a, s)]
    assert not missing, "\n" + "\n".join(missing)


def test_the_squad_rules_say_production_is_read_only_and_one_state_means_series():
    rules = flat(open(os.path.join(ROOT, "CLAUDE.md"), encoding="utf-8").read())
    assert "production is read-only for every agent" in rules
    assert "same terraform state in two fronts" in rules
    assert "run them in series" in rules


# Home paths are built from parts: written out whole, scripts/check_private_paths.py flags this file.
_USERS = "/Us" + "ers/"
PRIVATE = re.compile(r"(?i:zscaler|upwind|herdr|/home/|~/dev/|cglab)|\b[A-Z]{2,}-\d+\b|\b\d{12}\b"
                     r"|" + _USERS + r"|/mnt/[a-z]" + _USERS
                     + r"|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----"
                     r"|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}")


def test_the_ported_rules_carry_no_private_context():
    private = PRIVATE
    hits = []
    for path in agent_files():
        for n, line in enumerate(open(path, encoding="utf-8"), 1):
            if private.search(line):
                hits.append(f"{os.path.relpath(path, ROOT)}:{n}: {line.strip()}")
    assert not hits, "\n" + "\n".join(hits)


def test_the_verifier_stays_without_a_mutation_round():
    assert "mutation" not in flat(read_agent("verifier"))


def test_a_level_1_heading_ends_a_section():
    text = "## Steps\n\nfirst phrase\n\n# Stray\n\nsecond phrase\n"
    body = section_of(text, "Steps")
    assert "first phrase" in body and "second phrase" not in body, body


def test_the_private_pattern_catches_each_kind_and_spares_a_route():
    hits = ["/Us" + "ers/bob/x", "/mnt/c" + "/Us" + "ers/bob", "AKIA" + "ABCDEFGHIJKLMNOP",
            "-----BEGIN RSA " + "PRIVATE KEY-----", "ghp_" + "a" * 36, "github_pat_" + "a" * 40]
    for text in hits:
        assert PRIVATE.search(text), text
    for text in ("GET:/users/42/orders", "never commit a private key"):
        assert not PRIVATE.search(text), text

if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok ", name)
