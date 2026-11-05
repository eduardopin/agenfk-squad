#!/usr/bin/env python3
"""No file claims a licence for claudekit, whose repository ships no licence file.

`mrgoonie/claudekit-skills` has no LICENSE at its root (the GitHub licence API
answers 404), so the skills that took an idea from it say "no licence claimed"
and restate it in their own words. This test walks the shipped tree and fails
when a sentence, a front matter or a notices section that names claudekit also
names a licence.

    python3 tests/test_licence_claims.py
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# tests/ holds the forbidden wording on purpose (in negative assertions), so it is not scanned.
SKIP_DIRS = {".git", "tests", "node_modules", "__pycache__", ".venv"}
TEXT = (".md", ".txt", ".py", ".html", ".css", ".js", ".ts", ".json", ".toml",
        ".yaml", ".yml", ".sh", ".ps1", ".csv")
BARE = ("NOTICE", "LICENSE", "COPYING")
SOURCE = re.compile(r"claudekit", re.I)
LICENCE = re.compile(r"\b(?:MIT|Apache|BSD|[AL]?GPL|MPL|ISC|EPL|CC[- ]?BY|CC0|Unlicense|WTFPL|Zlib)\b"
                     r"|licen[cs]ed under", re.I)
HEADING = re.compile(r"(?m)^#{1,6} ")


def claims(text):
    """The clauses (and the markdown sections) that name claudekit together with a licence."""
    text = text.replace("\r\n", "\n")
    found = []
    # A clause ends at a full stop, a semicolon or a blank line; a wrapped line does not end it.
    for clause in re.split(r"[.;](?:\s|$)|\n\s*\n", text):
        flat = " ".join(clause.split())
        if SOURCE.search(flat) and LICENCE.search(flat):
            found.append(flat)
    # A notices section headed by claudekit must not carry a licence anywhere in its body.
    starts = [m.start() for m in HEADING.finditer(text)] + [len(text)]
    for a, b in zip(starts, starts[1:]):
        head, _, body = text[a:b].partition("\n")
        if SOURCE.search(head) and LICENCE.search(body):
            found.append(" ".join(head.split()))
    return found


def shipped_files():
    for d, dirs, files in os.walk(ROOT):
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS)
        for name in sorted(files):
            if name.endswith(TEXT) or name in BARE:
                yield os.path.join(d, name)


def read(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def test_the_detector_flags_a_licence_claim_for_claudekit():
    for text in ("summarised from the claudekit `slides` skill (MIT), rewritten",
                 "Summary of the claudekit `brand` (visual part) and `banner-design` skills\n(MIT), rewritten.",
                 "license: MIT\nmetadata:\n  author: claudekit\n",
                 "## claudekit `brand`\n\nMIT License. Copyright (c) 2026 someone.\n",
                 "From claudekit, licensed under the Apache License."):
        assert claims(text), text


def test_the_detector_lets_an_idea_without_a_licence_through():
    for text in ("the idea of the brand-voice axes from claudekit `brand` (no licence claimed, own words)",
                 "from `caveman` (Apache-2.0); the idea of the brand-voice axes from claudekit `brand`",
                 "## claudekit `brand`\n\nNo licence file was found, so no licence is claimed.\n\n## caveman\n\nApache License, Version 2.0.\n",
                 "`humanizer` (MIT) and `no-ai-slop` (MIT).\n\nThe outlines came from claudekit, in own words."):
        assert not claims(text), (text, claims(text))


def test_the_scan_covers_skills_agents_docs_and_the_notices():
    seen = {os.path.relpath(p, ROOT).replace(os.sep, "/") for p in shipped_files()}
    for rel in ("skills/writing/THIRD_PARTY_NOTICES.md", "skills/design-reference/SKILL.md",
                "skills/presentation/SKILL.md", "README.md", "NOTICE"):
        assert rel in seen, rel
    assert any(r.startswith("agents/") for r in seen) and any(r.startswith("docs/") for r in seen)
    assert not any(r.startswith("tests/") for r in seen)


def test_no_shipped_file_claims_a_licence_for_claudekit():
    bad = []
    for path in shipped_files():
        for clause in claims(read(path)):
            bad.append(f"{os.path.relpath(path, ROOT)}: {clause[:160]}")
    assert not bad, "claudekit ships no licence; say 'no licence claimed' instead:\n  " + "\n  ".join(bad)


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
