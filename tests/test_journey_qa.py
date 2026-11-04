#!/usr/bin/env python3
"""Offline contract checks for skills/journey-qa, plus its Node tests.

Self-checking: the exit code is the verdict.

    python3 tests/test_journey_qa.py

The skill ships a Node CLI (scripts/jqa.mjs, no required dependency). The Node
tests in tests/journey_qa/ run through this file when node >= 20 is on PATH
(setup-node puts it there on every CI leg, Linux and Windows) and are skipped
with a reason otherwise.
No browser, no network, no model.
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(ROOT, "skills", "journey-qa")
NODE_TESTS = os.path.join(ROOT, "tests", "journey_qa")
FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)
PRIVATE = re.compile(r"eduardopin|cglab|cg/lab|INFRA-\d+|claude-squad|/home/[a-z]+|C:\\Users", re.I)


def node_version():
    node = shutil.which("node")
    if not node:
        return None
    try:
        out = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=30).stdout
        return int(out.strip().lstrip("v").split(".")[0])
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


NODE_MAJOR = node_version()
NODE_REASON = "node >= 20 not found on PATH"
# Node tests in tests/journey_qa/ today (41 in core, 6 in driver, 9 in origin, 4 in origin.browser); a lower count
# means one was lost. A test may skip itself where the system cannot do what it needs (symlinks on Windows without
# Developer Mode, POSIX modes on Windows, a real browser unless JQA_BROWSER_TESTS=1), so passed and skipped count
# together.
NODE_TESTS_FLOOR = 65


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def check(value, schema, path="$"):
    """Minimal in-house JSON Schema check: type, required, properties, items, enum, const.

    Enough to catch a template that drifted from its schema; not a validator.
    Returns a list of problems.
    """
    problems = []
    kinds = {"object": dict, "array": list, "string": str, "boolean": bool,
             "null": type(None), "integer": int, "number": (int, float)}
    t = schema.get("type")
    if t is not None:
        names = t if isinstance(t, list) else [t]
        ok = False
        for n in names:
            py = kinds.get(n)
            if py is None:
                continue
            if isinstance(value, bool) and n in ("integer", "number"):
                continue
            if isinstance(value, py):
                ok = True
        if not ok:
            return [f"{path}: expected {names}, got {type(value).__name__}"]
    if "const" in schema and value != schema["const"]:
        problems.append(f"{path}: expected const {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        problems.append(f"{path}: {value!r} not in enum")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                problems.append(f"{path}: missing required key {key!r}")
        for key, sub in schema.get("properties", {}).items():
            if key in value:
                problems += check(value[key], sub, f"{path}.{key}")
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for i, item in enumerate(value):
            problems += check(item, schema["items"], f"{path}[{i}]")
    return problems


class SkillContract(unittest.TestCase):
    def test_frontmatter_name_equals_directory(self):
        m = FRONTMATTER.match(read(os.path.join(SKILL, "SKILL.md")))
        self.assertIsNotNone(m, "SKILL.md has no frontmatter")
        fields = dict(re.findall(r"^(\w+):\s*(.*)$", m.group(1), re.M))
        self.assertEqual(fields.get("name"), os.path.basename(SKILL))
        self.assertTrue(fields.get("description"), "description is empty")

    def test_version_file_matches_provenance(self):
        version = read(os.path.join(SKILL, "VERSION")).strip()
        self.assertRegex(version, r"^\d+\.\d+\.\d+$")
        self.assertIn(version, read(os.path.join(SKILL, "PROVENANCE.md")))

    def test_every_linked_file_exists(self):
        text = read(os.path.join(SKILL, "SKILL.md"))
        links = {l.split("#")[0] for l in re.findall(r"\]\(([^)\s]+)\)", text)}
        links = {l for l in links if l and not re.match(r"[a-z]+:", l)}
        self.assertTrue(links, "SKILL.md links nothing; the check would pass on nothing")
        for link in sorted(links):
            self.assertTrue(os.path.isfile(os.path.join(SKILL, link)), f"broken link: {link}")

    def test_schemas_parse_and_templates_validate(self):
        schemas = sorted(glob.glob(os.path.join(SKILL, "assets", "schemas", "*.schema.json")))
        self.assertGreaterEqual(len(schemas), 7)
        by_name = {}
        for path in schemas:
            by_name[os.path.basename(path)[: -len(".schema.json")]] = json.loads(read(path))
        templates = sorted(glob.glob(os.path.join(SKILL, "assets", "templates", "*.json")))
        self.assertEqual(
            sorted(os.path.basename(t)[:-5] for t in templates), sorted(by_name),
            "every schema has a template and the other way round")
        for path in templates:
            name = os.path.basename(path)[:-5]
            problems = check(json.loads(read(path)), by_name[name])
            self.assertEqual(problems, [], f"{name}: {problems}")

    def test_template_check_can_fail(self):
        # The in-house check must reject a template missing a required key,
        # otherwise the test above proves nothing.
        schema = json.loads(read(os.path.join(SKILL, "assets", "schemas", "actor-card.schema.json")))
        template = json.loads(read(os.path.join(SKILL, "assets", "templates", "actor-card.json")))
        self.assertTrue(schema.get("required"), "schema declares no required keys")
        broken = {k: v for k, v in template.items() if k != schema["required"][0]}
        self.assertTrue(check(broken, schema))
        self.assertTrue(check(["not", "an", "object"], schema))

    def test_no_private_strings_in_the_skill(self):
        paths = [p for p in glob.glob(os.path.join(SKILL, "**", "*"), recursive=True) if os.path.isfile(p)]
        paths += [p for p in glob.glob(os.path.join(NODE_TESTS, "**", "*"), recursive=True) if os.path.isfile(p)]
        paths.append(os.path.abspath(__file__))
        hits = []
        for path in paths:
            if path == os.path.abspath(__file__):
                continue  # this file names the patterns it rejects
            for n, line in enumerate(read(path).splitlines(), 1):
                if PRIVATE.search(line):
                    hits.append(f"{os.path.relpath(path, ROOT)}:{n}")
        self.assertEqual(hits, [])

    def test_references_name_only_files_that_ship(self):
        # portable/ and evals/ belong to the upstream development repository and are not part of the skill.
        hits = []
        for path in glob.glob(os.path.join(SKILL, "references", "*.md")):
            for n, line in enumerate(read(path).splitlines(), 1):
                if re.search(r"\b(portable|evals)/", line):
                    hits.append(f"{os.path.basename(path)}:{n}")
        self.assertEqual(hits, [])

    def test_node_tests_import_local_files_by_url(self):
        # import() of a bare absolute path works on POSIX and fails on Windows, where D:\... reads as the
        # scheme "d:". Relative specifiers, node: builtins and pathToFileURL(...) work everywhere.
        hits = []
        for path in sorted(glob.glob(os.path.join(NODE_TESTS, "*.mjs"))):
            for n, line in enumerate(read(path).splitlines(), 1):
                for arg in re.findall(r"\bimport\(\s*([^)]*)", line):
                    if not re.match(r"$|[\"'`](\.\.?/|node:)|pathToFileURL\(", arg.strip()):
                        hits.append(f"{os.path.basename(path)}:{n}: import({arg.strip()}")
        self.assertEqual(hits, [])

    def test_no_required_dependency(self):
        pkg = json.loads(read(os.path.join(SKILL, "scripts", "package.json")))
        self.assertFalse(pkg.get("dependencies"), "scripts/package.json must not require dependencies")


class NodeSide(unittest.TestCase):
    @unittest.skipIf(NODE_MAJOR is None or NODE_MAJOR < 20, NODE_REASON)
    def test_help_exits_zero(self):
        # Node writes UTF-8 to a pipe; the locale default (cp1252 on Windows) cannot decode every byte of it.
        r = subprocess.run(["node", os.path.join(SKILL, "scripts", "jqa.mjs"), "--help"],
                           capture_output=True, encoding="utf-8", errors="replace", timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("jqa", r.stdout + r.stderr)

    @unittest.skipIf(NODE_MAJOR is None or NODE_MAJOR < 20, NODE_REASON)
    def test_node_suite(self):
        files = sorted(glob.glob(os.path.join(NODE_TESTS, "*.test.mjs")))
        self.assertTrue(files, "no Node test files found")
        r = subprocess.run(["node", "--test", "--test-reporter=tap", *files],
                           capture_output=True, encoding="utf-8", errors="replace", timeout=600)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-1000:])
        passed = re.search(r"^# pass (\d+)", r.stdout, re.M)
        skipped = re.search(r"^# skipped (\d+)", r.stdout, re.M)
        self.assertIsNotNone(passed)
        ran = int(passed.group(1)) + (int(skipped.group(1)) if skipped else 0)
        self.assertGreaterEqual(ran, NODE_TESTS_FLOOR, "fewer Node tests ran than exist")


if __name__ == "__main__":
    result = unittest.main(exit=False, verbosity=1).result
    if result.wasSuccessful():
        print("ok")
    sys.exit(0 if result.wasSuccessful() else 1)
