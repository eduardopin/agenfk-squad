#!/usr/bin/env python3
"""Skill wiring/fixtures, not a claim about live-model behavior or accuracy."""
import ast
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ('pr-evidence', 'ci-plan-evidence', 'parallel-plan', 'engineering-evidence')
PUBLIC = (ROOT / 'agents/developer.md').exists()
AGENTS = {
    'pr-evidence': ('github', 'verifier' if PUBLIC else 'verificador'),
    'ci-plan-evidence': ('infra', 'verifier' if PUBLIC else 'verificador'),
    'parallel-plan': ('product' if PUBLIC else 'produto', 'github'),
    'engineering-evidence': ('developer' if PUBLIC else 'desenvolvimento',
                             'tester' if PUBLIC else 'testes', 'finops'),
}


class ContractTests(unittest.TestCase):
    def test_skill_metadata_and_progressive_disclosure(self):
        for name in SKILLS:
            text = (ROOT / 'skills' / name / 'SKILL.md').read_text()
            self.assertTrue(text.startswith('---\nname: ' + name + '\n'), name)
            desc = re.search(r'^description: (.+)$', text, re.M)
            self.assertIsNotNone(desc, name)
            self.assertLessEqual(len(desc[1]), 700, name)
            if ': ' in desc[1]:
                self.assertTrue(desc[1].startswith(('"', "'")), 'quote YAML colon-space: ' + name)
            self.assertLess(len(text.splitlines()), 160, name)
            for link in re.findall(r'\]\((references/[^)]+)\)', text):
                self.assertTrue((ROOT / 'skills' / name / link).is_file(), (name, link))
            for link in re.findall(r'`(references/[^`]+\.md)`', text):
                self.assertTrue((ROOT / 'skills' / name / link).is_file(), (name, link))

    def test_agents_point_to_each_procedure_on_demand(self):
        for skill, agents in AGENTS.items():
            for agent in agents:
                self.assertIn('`' + skill + '`', (ROOT / 'agents' / (agent + '.md')).read_text(), (skill, agent))
        rules = (ROOT / 'CLAUDE.md').read_text()
        for skill in SKILLS:
            self.assertIn('`' + skill + '`', rules, skill)

    def test_eval_fixtures_include_positive_negative_and_assertions(self):
        for skill in SKILLS:
            data = json.loads((ROOT / 'skills' / skill / 'evals/evals.json').read_text())
            self.assertEqual(data['skill_name'], skill)
            self.assertEqual(len(data['evals']), 3)
            self.assertEqual(len({e['id'] for e in data['evals']}), 3)
            self.assertEqual({e['should_trigger'] for e in data['evals']}, {True, False})
            for e in data['evals']:
                self.assertGreater(len(e['prompt']), 65)
                self.assertTrue(e['expected_output'] and e['assertions'])

    def test_scripts_are_offline_and_stdlib_only(self):
        allowed = {'argparse', 'collections', 'fnmatch', 'heapq', 'json', 'pathlib',
                   'posixpath', 're', 'sys', 'unicodedata'}
        for skill in SKILLS:
            for path in (ROOT / 'skills' / skill).glob('scripts/*.py'):
                tree = ast.parse(path.read_text())
                imports = set()
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        imports.update(a.name.split('.')[0] for a in node.names)
                    elif isinstance(node, ast.ImportFrom):
                        imports.add(node.module.split('.')[0])
                self.assertLessEqual(imports, allowed, path)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                        self.assertNotIn(node.func.id, ('exec', 'eval', '__import__', 'open'), path)
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                        self.assertNotIn(node.func.attr, ('write_text', 'write_bytes', 'unlink', 'mkdir'), path)

    def test_dispatch_rules_follow_the_flow_and_the_watcher(self):
        rules = (ROOT / 'CLAUDE.md').read_text()
        # Rule 3: the parallel fronts ceiling is six, not four.
        self.assertIn('Ceiling ~6 fronts.', rules)
        self.assertNotRegex(rules, r'~4 fronts')
        # Rule 4 and the table: the review has no mutation step.
        self.assertNotIn('MUTATE', rules)
        self.assertIn('REVIEW / ADVERSARIAL / VERIFY', rules)
        self.assertRegex(rules, r'REVIEW, ADVERSARIAL and VERIFY go to the\s+`verifier`')
        # Rule 11: a red gate is work; no PR goes in with a defect or a failure.
        rule = re.search(r'^11\. \*\*No PR passes or is merged with a defect or a failure\*\*(.+?)(?=^\d+\. |\Z)',
                         rules, re.M | re.S)
        self.assertIsNotNone(rule)
        body = ' '.join(rule[1].split())
        for phrase in ('A red gate is work, not a decision for the owner',
                       'open a card in AgenFK for each cause',
                       'Never propose "accept with the failures explained"',
                       'through the merge queue, without asking for an OK'):
            self.assertIn(phrase, body)
        # Rule 7: the numbers are the watcher's own defaults, not older ones.
        watch = (ROOT / 'hooks/squad-watch.py').read_text()
        ctx = (ROOT / 'skills/session-context/scripts/session_context.py').read_text()

        def default(src, var):
            found = re.search(r'env_(?:int|num)\("' + var + r'", ([\d_]+)', src)
            self.assertIsNotNone(found, var)
            return int(found[1].replace('_', ''))

        flat = ' '.join(rules.split())
        self.assertIn('context ≥ %d%% of the window' % default(watch, 'SQUAD_CTX_PCT'), flat)
        self.assertIn('%d%% of the window with burn ≥ %d%% of the session budget'
                      % (default(ctx, 'SQUAD_REREAD_FLOOR'), default(ctx, 'SQUAD_BURN_FULL')), flat)
        self.assertIn('rising to %d%% with zero burn' % default(watch, 'SQUAD_CTX_PCT'), flat)
        self.assertIn('orchestrator context ≥ %dk' % (default(watch, 'SQUAD_CTX_CEILING') // 1000), flat)
        self.assertIn('%d sub-sessions in the session' % default(watch, 'SQUAD_MAX_SUBS'), flat)
        self.assertIn('%d sessions orchestrating at the same time' % default(watch, 'SQUAD_MAX_ORCH'), flat)
        self.assertIn('with a heavy session, ≥ %dk' % (default(watch, 'SQUAD_ORCH_HERE') // 1000), flat)
        self.assertIn('%dth reviewer on the same card' % (default(watch, 'SQUAD_MAX_REVIEWS') + 1), flat)

    def test_no_private_context_or_bundled_history_in_new_skills(self):
        forbidden = re.compile(r'/home/[a-z]+/|(?<!\d)\d{12}(?!\d)|-----BEGIN .*PRIVATE KEY-----', re.I)
        for skill in SKILLS:
            for path in (ROOT / 'skills' / skill).rglob('*'):
                if path.is_file():
                    self.assertNotIn(path.suffix, ('.bundle', '.zip', '.pyc'), path)
                    self.assertFalse(path.is_symlink(), path)
                    self.assertIsNone(forbidden.search(path.read_text()), path)


if __name__ == '__main__':
    unittest.main()
