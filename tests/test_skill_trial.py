#!/usr/bin/env python3
"""skill-trial: the stub gh never reaches the network, a dry run builds the
scenario and checks it without Claude, and the report fails on a FAIL."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / 'skills' / 'skill-trial'
TRIAL = SKILL / 'scripts' / 'trial.sh'
REPORT = SKILL / 'scripts' / 'report.py'
COMMANDS = SKILL / 'scripts' / 'commands.py'
GH = SKILL / 'scripts' / 'bin' / 'gh'
SCENARIO = SKILL / 'scenarios' / 'smoke'
def _bwrap_works():
    # On PATH is not enough: a runner can block user namespaces.
    if shutil.which('bwrap') is None:
        return False
    try:
        return subprocess.run(['bwrap', '--ro-bind', '/', '/', '--unshare-all', 'true'],
                              capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


HAS_BWRAP = _bwrap_works()
# Spelled in pieces so this file does not trip its own scan.
PRIVATE = re.compile('|'.join(['edu' + 'ardopin', 'cg' + 'lab', 'CG/' + 'lab', 'CG' + 'LAB',
                               'INFRA' + r'-\d', 'claude-' + 'squad', 'ji' + 'ra']), re.I)


def run(cmd, **kw):
    kw.setdefault('timeout', 120)
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True, **kw)


class Tmp(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.tmp = Path(d.name)

    def dry(self, scenario=SCENARIO, n='1'):
        out = self.tmp / 'out'
        r = run(['bash', TRIAL, '--scenario', scenario, '--n', n, '--dry', '--out', out])
        return out, r

    def gh(self, *args, rules=None):
        env = {**os.environ, 'GH_STUB_LOG': str(self.tmp / 'gh.log')}
        if rules is not None:
            (self.tmp / 'gh.json').write_text(json.dumps(rules))
            env['GH_STUB_RESPONSES'] = str(self.tmp / 'gh.json')
        return run([GH, *args], env=env, timeout=30)


class StubTests(Tmp):
    def test_stub_answers_the_first_matching_prefix_and_logs(self):
        r = self.gh('pr', 'list', '--json', 'number', rules=[{'match': 'pr list', 'stdout': [{'number': 7}]}])
        self.assertEqual(r.returncode, 0)
        self.assertEqual(json.loads(r.stdout), [{'number': 7}])
        self.assertIn('pr list --json number', (self.tmp / 'gh.log').read_text())

    def test_stub_fails_anything_unmatched(self):
        r = self.gh('api', 'repos/x/y/pulls', '-X', 'POST', rules=[])
        self.assertEqual(r.returncode, 1)
        self.assertIn('not available', r.stderr)

    def test_stub_answers_a_match_from_the_scenario_gh_json(self):
        r = self.gh('repo', 'view', '--json', 'name', rules=json.loads((SCENARIO / 'gh.json').read_text()))
        self.assertEqual(r.returncode, 0)
        self.assertEqual(json.loads(r.stdout)['defaultBranchRef']['name'], 'main')


class ReportTests(Tmp):
    def trial(self, i, rc, result, why='reason', cost=0.5):
        t = self.tmp / f'trial-{i}'
        t.mkdir()
        (t / 'check.rc').write_text(f'{rc}\n')
        (t / 'check.out').write_text(why + '\n')
        (t / 'seconds').write_text('12\n')
        (t / 'stream.jsonl').write_text(json.dumps({'type': 'result', 'total_cost_usd': cost,
                                                    'num_turns': 4, 'result': result}) + '\n')

    def test_report_exits_1_when_a_trial_failed(self):
        self.trial(1, 0, 'Done.')
        self.trial(2, 1, 'I opened the PR.')
        r = run(['python3', REPORT, self.tmp], timeout=30)
        self.assertEqual(r.returncode, 1)
        self.assertIn('| trial-2 | FAIL |', r.stdout)
        self.assertIn('$1.00', r.stdout)

    def test_report_builds_the_table_from_fixture_trials(self):
        self.trial(1, 0, 'Added the file.', 'ok')
        self.trial(2, 2, 'Could not.', 'never reached it')
        r = run(['python3', REPORT, self.tmp], timeout=30)
        self.assertEqual(r.returncode, 0)
        self.assertIn('| Trial | Verdict |', r.stdout)
        self.assertIn('| trial-1 | PASS | ok | $0.50 | 4 | 0 | 12s | Added the file. |', r.stdout)
        self.assertIn('| trial-2 | N/A | never reached it |', r.stdout)
        self.assertIn('2 trials, 0 FAIL, 1 N/A, 0 ERROR, 0 SETUP, $1.00', r.stdout)

    def test_report_marks_a_session_that_never_ran_as_error(self):
        self.trial(1, 2, 'API Error: Cannot reach the API server', 'never reached it', cost=0)
        r = run(['python3', REPORT, self.tmp], timeout=30)
        self.assertEqual(r.returncode, 1)
        self.assertIn('| trial-1 | ERROR |', r.stdout)
        self.assertIn('1 ERROR', r.stdout)

    def test_report_counts_a_failed_setup_and_exits_3(self):
        self.trial(1, 0, 'Done.')
        t = self.tmp / 'trial-2'
        t.mkdir()
        (t / 'check.rc').write_text('3\n')
        (t / 'check.out').write_text('setup failed\n')
        r = run(['python3', REPORT, self.tmp], timeout=30)
        self.assertEqual(r.returncode, 3)
        self.assertIn('| trial-2 | SETUP |', r.stdout)
        self.assertIn('2 trials, 0 FAIL, 0 N/A, 0 ERROR, 1 SETUP', r.stdout)
        self.assertIn('usage:', run(['python3', REPORT, '--help'], timeout=30).stdout)

    def test_commands_lists_the_bash_commands_of_a_stream(self):
        ev = {'type': 'assistant', 'message': {'content': [
            {'type': 'tool_use', 'name': 'Bash', 'input': {'command': 'gh  pr\ncreate'}},
            {'type': 'tool_use', 'name': 'Read', 'input': {'file_path': 'x'}}]}}
        (self.tmp / 's.jsonl').write_text('not json\n' + json.dumps(ev) + '\n')
        r = run(['python3', COMMANDS, self.tmp / 's.jsonl'], timeout=30)
        self.assertEqual(r.stdout.strip(), 'gh pr create')


class SkillDirLineTests(Tmp):
    def line(self):
        m = re.search(r'^SKILL_DIR=.*$', (SKILL / 'SKILL.md').read_text(), re.M)
        self.assertTrue(m)
        return m.group(0)

    def resolve(self, home, cwd):
        env = {k: v for k, v in os.environ.items() if k != 'CLAUDE_SKILL_DIR'}
        env['HOME'] = str(home)
        r = run(['bash', '-c', self.line() + '\necho "$SKILL_DIR"'], env=env, cwd=cwd, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return Path(r.stdout.strip())

    def test_resolves_through_the_install_symlink_from_any_cwd(self):
        home = self.tmp / 'home'
        (home / '.claude' / 'skills').mkdir(parents=True)
        (home / '.claude' / 'skills' / 'skill-trial').symlink_to(SKILL)
        self.assertEqual(self.resolve(home, self.tmp).resolve(), SKILL.resolve())

    def test_falls_back_to_the_checkout_from_its_root(self):
        home = self.tmp / 'home'
        (home / '.claude' / 'skills').mkdir(parents=True)  # present, skill not installed
        self.assertEqual(self.resolve(home, ROOT).resolve(), SKILL.resolve())


class SkillShapeTests(unittest.TestCase):
    def test_frontmatter_name_equals_directory(self):
        head = (SKILL / 'SKILL.md').read_text().split('---')[1]
        name = re.search(r'^name:\s*(\S+)', head, re.M).group(1)
        self.assertEqual(name, SKILL.name)
        self.assertEqual(name, 'skill-trial')

    def test_no_private_strings_in_the_skill_or_this_test(self):
        hits = []
        for p in [*SKILL.rglob('*'), Path(__file__)]:
            if p.is_file() and PRIVATE.search(p.read_text(errors='replace')):
                hits.append(str(p.relative_to(ROOT)))
        self.assertEqual(hits, [])

    def test_trial_help_exits_0(self):
        r = run(['bash', TRIAL, '--help'], timeout=30)
        self.assertEqual(r.returncode, 0)
        self.assertIn('trial.sh --scenario', r.stdout)

    def test_scripts_help_exit_0(self):
        for s in (REPORT, COMMANDS):
            r = run(['python3', s, '--help'], timeout=30)
            self.assertEqual(r.returncode, 0, s)
            self.assertIn('usage:', r.stdout)

    def test_scripts_use_the_standard_library_only(self):
        for s in (REPORT, COMMANDS):
            mods = set(re.findall(r'^(?:import|from)\s+(\w+)', s.read_text(), re.M))
            self.assertLessEqual(mods, {'argparse', 'json', 'sys', 'pathlib'}, s)


@unittest.skipUnless(HAS_BWRAP, 'bwrap not installed')
class TrialRunTests(Tmp):
    def test_smoke_check_passes_with_notes_and_fails_without(self):
        out, r = self.dry()
        self.assertEqual(r.returncode, 0, r.stderr)
        t = out / 'trial-1'
        self.assertTrue((t / 'repo' / 'README.md').exists())
        self.assertEqual((t / 'check.rc').read_text().strip(), '2')  # no NOTES.md yet: N/A
        check = SCENARIO / 'check.sh'
        self.assertEqual(run([check, t]).returncode, 2)
        (t / 'repo' / 'NOTES.md').write_text('')
        self.assertEqual(run([check, t]).returncode, 1)
        (t / 'repo' / 'NOTES.md').write_text('a line\n')
        r = run([check, t])
        self.assertEqual(r.returncode, 0)
        (t / 'gh.log').write_text('12:00:00\t/x\tpr create --title t\n')
        r = run([check, t])
        self.assertEqual(r.returncode, 1)
        self.assertIn('PR was created', r.stdout)

    def test_dry_run_reports_na_for_every_trial(self):
        out, r = self.dry(n='2')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('2 trials, 0 FAIL, 2 N/A, 0 ERROR', r.stdout)

    def test_a_trial_does_not_inherit_this_sessions_environment(self):
        fake = self.tmp / 'bin'
        fake.mkdir()
        (fake / 'claude').write_text('#!/usr/bin/env bash\nenv > env.txt\n')
        (fake / 'claude').chmod(0o755)
        out = self.tmp / 'out'
        env = {**os.environ, 'SKILL_TRIAL_CLAUDE': str(fake / 'claude'), 'SOME_HOOK_SWITCH': 'off',
               'AWS_PROFILE': 'some-profile', 'GH_TOKEN': 'x'}
        run(['bash', TRIAL, '--scenario', SCENARIO, '--n', '1', '--out', out], env=env)
        seen = (out / 'trial-1' / 'repo' / 'env.txt').read_text()
        for name in ('SOME_HOOK_SWITCH', 'AWS_PROFILE', 'GH_TOKEN'):
            self.assertNotIn(name, seen)
        self.assertIn('AWS_CONFIG_FILE=/dev/null', seen)
        self.assertIn('GH_STUB_LOG=', seen)

    def test_a_trial_cannot_read_credentials_or_write_home(self):
        home = self.tmp / 'home'
        (home / '.ssh').mkdir(parents=True)
        (home / '.ssh' / 'id_ed25519').write_text('secret')
        (home / '.aws').mkdir()
        (home / '.aws' / 'credentials').write_text('secret')
        for d in ('.terraform.d', '.config/glab-cli', '.config/op', '.config/atlassian'):
            (home / d).mkdir(parents=True)
            (home / d / 'token').write_text('secret')
        for f in ('.npmrc', '.terraformrc', '.pypirc'):
            (home / f).write_text('secret')
        fake = self.tmp / 'bin'
        fake.mkdir()
        (fake / 'claude').write_text(
            '#!/usr/bin/env bash\n'
            '{ ls "$HOME/.ssh" "$HOME/.aws" "$HOME/.terraform.d" "$HOME/.config/glab-cli" "$HOME/.config/op"'
            ' "$HOME/.config/atlassian"; cat "$HOME/.npmrc" "$HOME/.terraformrc" "$HOME/.pypirc"; touch "$HOME/planted" 2>/dev/null && echo HOME-WRITABLE;'
            '  touch ./ok && echo TRIAL-WRITABLE;'
            '  for d in /var/tmp /dev/shm /run/lock /etc; do touch $d/planted 2>/dev/null && echo "WRITABLE:$d"; done;'
            "  echo PIDS=$(ls /proc | grep -c '^[0-9]');"
            '  echo RUN=$(ls /run/user/$(id -u) | wc -l); echo MNT=$(ls /mnt/c 2>/dev/null | wc -l); } > probe.txt 2>&1\n')
        (fake / 'claude').chmod(0o755)
        out = self.tmp / 'out'
        env = {**os.environ, 'HOME': str(home), 'SKILL_TRIAL_CLAUDE': str(fake / 'claude')}
        run(['bash', TRIAL, '--scenario', SCENARIO, '--n', '1', '--out', out], env=env)
        probe = (out / 'trial-1' / 'repo' / 'probe.txt').read_text()
        self.assertNotIn('id_ed25519', probe)
        self.assertNotIn('credentials', probe)
        self.assertNotIn('token', probe)
        self.assertNotIn('secret', probe)
        self.assertNotIn('HOME-WRITABLE', probe)
        self.assertFalse((home / 'planted').exists())
        self.assertNotIn('WRITABLE:/var/tmp', probe)
        self.assertNotIn('WRITABLE:/run/lock', probe)
        self.assertNotIn('WRITABLE:/etc', probe)
        self.assertFalse(Path('/var/tmp/planted').exists())
        self.assertIn('TRIAL-WRITABLE', probe)
        # own PID namespace: the host's /proc/<pid>/root would lead back to the real HOME
        self.assertLess(int(probe.split('PIDS=')[1].split()[0]), 10)
        self.assertIn('RUN=0', probe)
        self.assertIn('MNT=0', probe)

    def test_refuses_an_output_folder_that_touches_a_steering_path(self):
        home = self.tmp / 'home'
        for d in ('.claude', '.config', '.local', '.ssh', '.aws', '.cache'):
            (home / d).mkdir(parents=True)
        env = {**os.environ, 'HOME': str(home), 'SKILL_TRIAL_CLAUDE': '/bin/true'}
        for out in ('.claude', '.config/x', '.local/share/y', '.ssh', '.aws/z'):
            r = run(['bash', TRIAL, '--scenario', SCENARIO, '--n', '1', '--dry', '--out', home / out],
                    env=env, timeout=60)
            self.assertEqual(r.returncode, 64, out)
            self.assertIn('refusing', r.stderr)
        r = run(['bash', TRIAL, '--scenario', SCENARIO, '--n', '1', '--dry', '--out', home / '.cache' / 'st'],
                env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_refuses_an_output_folder_that_contains_home(self):
        home = self.tmp / 'home'
        home.mkdir()
        env = {**os.environ, 'HOME': str(home), 'SKILL_TRIAL_CLAUDE': '/bin/true'}
        r = run(['bash', TRIAL, '--scenario', SCENARIO, '--n', '1', '--out', self.tmp], env=env, timeout=60)
        self.assertEqual(r.returncode, 64)
        self.assertIn('contains it', r.stderr)


if __name__ == '__main__':
    unittest.main()
