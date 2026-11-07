#!/usr/bin/env python3
"""The parallel-git skill keeps the rules that stop two fronts sharing one state."""
from pathlib import Path
import unittest

SKILL = Path(__file__).resolve().parents[1] / 'skills/parallel-git/SKILL.md'


class ParallelGitRules(unittest.TestCase):
    def setUp(self):
        self.text = SKILL.read_text(encoding='utf-8')

    def test_terraform_lockfile_and_shared_variables_are_contested(self):
        self.assertIn('`.terraform.lock.hcl`', self.text)
        self.assertIn('shared `variables.tf`', self.text)

    def test_same_terraform_state_is_one_front(self):
        self.assertIn('same root and state are one front', self.text)

    def test_force_with_lease_needs_the_owner(self):
        self.assertIn("`--force-with-lease` only on the owner's word", self.text)

    def test_a_merge_that_applies_names_the_stack(self):
        self.assertIn('a merge is also an apply', self.text)
        self.assertIn('say which stack each merge applies', self.text)


if __name__ == '__main__':
    unittest.main()
