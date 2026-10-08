"""Assessment leaves targets unchanged while its best-effort audit may write."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tests.helpers import RepoFixture, git_available

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/delete-guard/scripts'


@unittest.skipUnless(git_available(), 'git required')
class AssessmentSideEffects(RepoFixture):
    def setUp(self):
        super().setUp()
        self.target = Path(self.write('valuable/data.txt', 'synthetic original bytes\n'))
        self.trash = Path(self.root, '.agent-trash')
        self.exclude = Path(self.root, '.git/info/exclude')
        self.exclude_before = self.exclude.read_bytes()
        self.ignore_before = Path(self.root, '.gitignore').read_bytes()
        self.index_before = Path(self.root, '.git/index').read_bytes()
        self.head_before = self.git('rev-parse', 'HEAD')

    def git(self, *args):
        return subprocess.run(['git', '-C', self.root, *args], capture_output=True,
                              text=True, check=True, timeout=15).stdout

    def stamp(self, path=None):
        p = path or self.target
        st = p.lstat()
        return (hashlib.sha256(p.read_bytes()).hexdigest(), st.st_mode,
                st.st_size, st.st_mtime_ns)

    def cli(self, script, *args, environment=None, injection=None):
        env = {k: v for k, v in os.environ.items() if not k.startswith('AGENT_GUARD_')}
        env['GIT_OPTIONAL_LOCKS'] = '0'
        if environment:
            env.update(environment)
        argv = [sys.executable, str(SCRIPTS / script), '--json', *args]
        if injection:
            argv = [sys.executable, '-c', injection, str(SCRIPTS / script), '--json', *args]
        proc = subprocess.run(argv, cwd=self.root, env=env, text=True,
                              capture_output=True, timeout=30)
        return proc, json.loads(proc.stdout)

    def records(self, trash=None):
        return [json.loads(line) for line in (trash or self.trash).joinpath('audit.jsonl').read_text().splitlines()]

    def assert_no_compensation(self):
        self.assertFalse((self.trash / 'manifest.jsonl').exists())
        self.assertFalse((self.trash / 'state.json').exists())
        self.assertFalse((self.trash / 'sessions').exists())
        if self.trash.exists():
            self.assertEqual(sorted(p.name for p in self.trash.iterdir()), ['audit.jsonl'])
        self.assertEqual(self.git('stash', 'list'), '')
        self.assertEqual(self.git('rev-parse', 'HEAD'), self.head_before)
        self.assertEqual(Path(self.root, '.git/index').read_bytes(), self.index_before)
        self.assertEqual(Path(self.root, '.gitignore').read_bytes(), self.ignore_before)

    def assert_layout_written(self):
        self.assertTrue((self.trash / 'audit.jsonl').is_file())
        self.assertIn(b'/.agent-trash/\n', self.exclude.read_bytes())
        self.assertNotEqual(self.exclude.read_bytes(), self.exclude_before)

    def test_advisory_delete_reports_relocate_but_only_audits(self):
        before = self.stamp()
        proc, out = self.cli('check.py', '--', 'rm valuable/data.txt')
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(out['decision'], 'RELOCATE')
        self.assertFalse(out['enforced'])
        self.assertNotIn('compensations', out)
        self.assertEqual(self.stamp(), before)
        self.assert_layout_written()
        self.assertEqual(self.records()[0]['event'], 'check')
        self.assert_no_compensation()

    def test_advisory_benign_command_still_writes_audit(self):
        proc, out = self.cli('check.py', '--', 'printf harmless')
        self.assertEqual((proc.returncode, out['decision']), (0, 'ALLOW'))
        self.assert_layout_written()
        self.assertEqual(len(self.records()), 1)
        self.assert_no_compensation()

    def test_advisory_block_is_exit_zero_and_audited(self):
        before = self.stamp()
        proc, out = self.cli('check.py', '--', 'rm -rf .')
        self.assertEqual((proc.returncode, out['decision']), (0, 'BLOCK'))
        self.assertEqual(self.stamp(), before)
        self.assertEqual(self.records()[0]['decision'], 'BLOCK')
        self.assert_layout_written()
        self.assert_no_compensation()

    def test_advisory_ask_creates_no_snapshot_or_relocation(self):
        before = self.stamp()
        proc, out = self.cli('check.py', '--', 'cd valuable && rm data.txt')
        self.assertEqual((proc.returncode, out['decision']), (0, 'ASK'))
        self.assertEqual(self.stamp(), before)
        self.assert_layout_written()
        self.assert_no_compensation()

    def test_repeated_assessment_appends_audit_without_duplicate_exclude(self):
        before = self.stamp()
        self.cli('check.py', '--', 'rm valuable/data.txt')
        first_exclude = self.exclude.read_bytes()
        first_audit = (self.trash / 'audit.jsonl').read_bytes()
        self.cli('check.py', '--', 'rm valuable/data.txt')
        self.assertEqual(self.stamp(), before)
        self.assertEqual(self.exclude.read_bytes(), first_exclude)
        self.assertTrue((self.trash / 'audit.jsonl').read_bytes().startswith(first_audit))
        self.assertEqual(len(self.records()), 2)
        self.assert_no_compensation()

    def test_safe_delete_dry_run_preserves_targets_and_records_advisory(self):
        before = self.stamp()
        proc, out = self.cli('safe_delete.py', '--dry-run', 'valuable')
        self.assertEqual(proc.returncode, 0)
        self.assertTrue(out['dry_run'])
        self.assertEqual(out['verdict']['decision'], 'RELOCATE')
        self.assertEqual(self.stamp(), before)
        self.assertNotIn('txid', out)
        self.assert_layout_written()
        self.assertEqual(self.records()[0]['phase'], 'advisory')
        self.assert_no_compensation()

    def test_regenerable_dry_run_preserves_payload(self):
        p = Path(self.write('node_modules/a/file.js', 'synthetic generated bytes'))
        before = self.stamp(p)
        proc, out = self.cli('safe_delete.py', '--dry-run', 'node_modules')
        self.assertEqual(out['verdict']['code'], 'ALLOW_REGENERABLE')
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(self.stamp(p), before)
        self.assert_layout_written()
        self.assert_no_compensation()

    def test_dry_run_block_keeps_exit_two_and_can_write_audit(self):
        before = self.stamp()
        proc, out = self.cli('safe_delete.py', '--dry-run', '.')
        self.assertEqual((proc.returncode, out['verdict']['decision']), (2, 'BLOCK'))
        self.assertEqual(self.stamp(), before)
        self.assert_layout_written()
        self.assert_no_compensation()

    def test_unmatched_glob_returns_before_metadata_setup(self):
        before = self.stamp()
        proc, out = self.cli('safe_delete.py', '--dry-run', 'unmatched-*.bin')
        self.assertEqual(proc.returncode, 0)
        self.assertIn('no matches', out['outcome'])
        self.assertEqual(self.stamp(), before)
        self.assertFalse(self.trash.exists())
        self.assertEqual(self.exclude.read_bytes(), self.exclude_before)

    def test_missing_literal_real_noop_does_not_initialize_metadata(self):
        proc, out = self.cli('safe_delete.py', 'absent.txt')
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(out['verdict']['code'], 'ALLOW_NOOP')
        self.assertFalse(self.trash.exists())
        self.assertEqual(self.exclude.read_bytes(), self.exclude_before)

    def test_missing_literal_dry_run_is_audited(self):
        proc, out = self.cli('safe_delete.py', '--dry-run', 'absent.txt')
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(out['verdict']['code'], 'ALLOW_NOOP')
        self.assert_layout_written()
        self.assert_no_compensation()

    def test_tracked_ignore_avoids_exclude_write(self):
        p = Path(self.root, '.gitignore')
        p.write_text(p.read_text() + '.agent-trash/\n')
        self.ignore_before = p.read_bytes()
        proc, out = self.cli('check.py', '--', 'rm valuable/data.txt')
        self.assertEqual(proc.returncode, 0)
        self.assertTrue((self.trash / 'audit.jsonl').exists())
        self.assertEqual(self.exclude.read_bytes(), self.exclude_before)
        self.assert_no_compensation()

    def test_external_bucket_uses_no_workspace_exclude_or_bucket(self):
        with tempfile.TemporaryDirectory(prefix='assessment-external-') as external:
            bucket = Path(external, 'bucket')
            before = self.stamp()
            proc, out = self.cli('safe_delete.py', '--dry-run', 'valuable',
                                 environment={'AGENT_GUARD_TRASH': str(bucket)})
            self.assertEqual(proc.returncode, 0)
            self.assertEqual(self.stamp(), before)
            self.assertTrue((bucket / 'audit.jsonl').is_file())
            self.assertFalse(self.trash.exists())
            self.assertEqual(self.exclude.read_bytes(), self.exclude_before)
            self.assertEqual(sorted(p.name for p in bucket.iterdir()), ['audit.jsonl'])

    def test_audit_failure_after_layout_keeps_assessment_decision(self):
        self.trash.mkdir()
        (self.trash / 'audit.jsonl').mkdir()
        before = self.stamp()
        for script, args in [('check.py', ['--', 'rm valuable/data.txt']),
                             ('safe_delete.py', ['--dry-run', 'valuable'])]:
            with self.subTest(script=script):
                proc, out = self.cli(script, *args)
                self.assertEqual(proc.returncode, 0)
                self.assertTrue(out['warnings'])
                self.assertEqual(self.stamp(), before)
                self.assertIn(b'/.agent-trash/\n', self.exclude.read_bytes())
                self.assertFalse((self.trash / 'manifest.jsonl').exists())

    def test_layout_failure_is_warning_and_creates_no_bucket(self):
        injection = '''import importlib.util, pathlib, sys
sys.path.insert(0, str(pathlib.Path(sys.argv[1]).parent))
spec=importlib.util.spec_from_file_location('assessment_cli',sys.argv[1])
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
def fail(self): raise OSError('synthetic unavailable Git metadata')
module.recovery.RecoveryEngine._exclude_from_git=fail
sys.argv=[sys.argv[1],*sys.argv[2:]]
raise SystemExit(module.main())
'''
        before = self.stamp()
        for script, args in [('check.py', ['--', 'rm valuable/data.txt']),
                             ('safe_delete.py', ['--dry-run', 'valuable'])]:
            with self.subTest(script=script):
                proc, out = self.cli(script, *args, injection=injection)
                self.assertEqual(proc.returncode, 0)
                self.assertTrue(out['warnings'])
                self.assertEqual(self.stamp(), before)
                self.assertFalse(self.trash.exists())
                self.assertEqual(self.exclude.read_bytes(), self.exclude_before)


if __name__ == '__main__':
    unittest.main()
