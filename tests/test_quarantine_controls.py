"""Quarantine control paths win over ordinary trash housekeeping."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

from core import AUDIT_NAME, MANIFEST_NAME, STATE_NAME
from core.classifier import classify_command, classify_paths
from core.policy import PolicyContext, decide_ops, decide_path_batch, worst
from tests.helpers import RepoFixture, git_available

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(git_available(), 'git required')
class QuarantineControlTests(RepoFixture):
    def ctx(self, trash=None, mode='NORMAL', base=None):
        return PolicyContext(workspace=self.root, base_dir=base or self.root,
                             trash_root=str(trash or Path(self.root, '.agent-trash')), mode=mode)

    def verdict(self, names, ctx=None):
        ctx = ctx or self.ctx()
        specs = classify_paths([str(p) for p in names], ctx.base_dir,
                               ctx.workspace, ctx.trash_root)
        return decide_path_batch(specs, ctx, recursive=True)

    def assert_protected(self, names, ctx=None):
        verdict = self.verdict(names, ctx)
        self.assertEqual((verdict.decision, verdict.code), ('BLOCK', 'BLOCK_PROTECTED_PATH'))

    def test_quarantine_root_is_protected_before_housekeeping(self):
        self.write('.agent-trash/payload.txt', 'keep')
        self.assert_protected(['.agent-trash'])

    def test_control_files_and_state_temp_are_protected(self):
        for name in (MANIFEST_NAME, AUDIT_NAME, STATE_NAME, STATE_NAME + '.tmp'):
            with self.subTest(name=name):
                self.write('.agent-trash/' + name, 'keep')
                self.assert_protected(['.agent-trash/' + name])

    def test_session_state_tree_is_protected(self):
        self.write('.agent-trash/sessions/demo/state.json', 'keep')
        for name in ('sessions', 'sessions/demo', 'sessions/demo/state.json'):
            with self.subTest(name=name):
                self.assert_protected(['.agent-trash/' + name])

    def test_missing_control_paths_remain_protected(self):
        self.assert_protected(['.agent-trash/manifest.jsonl'])
        self.assert_protected(['.agent-trash'])

    def test_mixed_payload_and_control_batch_is_blocked(self):
        self.write('.agent-trash/tx1/payload', 'payload')
        self.write('.agent-trash/audit.jsonl', 'keep')
        self.assert_protected(['.agent-trash/tx1/payload', '.agent-trash/audit.jsonl'])

    def test_parent_of_configured_bucket_is_protected(self):
        self.write('archive/store/tx1/payload', 'keep')
        self.assert_protected(['archive'], self.ctx(Path(self.root, 'archive/store')))

    def test_regenerable_parent_cannot_remove_its_bucket(self):
        self.write('node_modules/store/tx1/payload', 'keep')
        self.assert_protected(['node_modules'], self.ctx(Path(self.root, 'node_modules/store')))

    def test_outside_workspace_parent_keeps_existing_boundary_reason(self):
        self.write('container/project/.agent-trash/tx1/payload', 'keep')
        workspace = Path(self.root, 'container/project')
        ctx = PolicyContext(workspace=str(workspace), base_dir=str(workspace),
                            trash_root=str(workspace / '.agent-trash'))
        verdict = self.verdict([Path(self.root, 'container')], ctx)
        self.assertEqual((verdict.decision, verdict.code), ('BLOCK', 'BLOCK_OUT_OF_WORKSPACE'))

    def test_quarantine_root_alias_is_protected(self):
        self.write('.agent-trash/tx1/payload', 'keep')
        Path(self.root, 'bucket-link').symlink_to(Path(self.root, '.agent-trash'), target_is_directory=True)
        self.assert_protected(['bucket-link'])

    def test_parent_of_configured_bucket_link_is_protected(self):
        self.write('storage/tx1/payload', 'keep')
        Path(self.root, 'archive').mkdir()
        alias = Path(self.root, 'archive/bucket')
        alias.symlink_to(Path(self.root, 'storage'), target_is_directory=True)
        self.assert_protected(['archive'], self.ctx(alias))

    def test_control_leaf_link_location_is_protected(self):
        self.write('other/sentinel', 'keep')
        Path(self.root, '.agent-trash').mkdir()
        Path(self.root, '.agent-trash/audit.jsonl').symlink_to(Path(self.root, 'other/sentinel'))
        self.assert_protected(['.agent-trash/audit.jsonl'])

    def test_workspace_parent_alias_preserves_control_protection(self):
        self.write('.agent-trash/audit.jsonl', 'keep')
        alias = Path(self.root, 'workspace-link')
        alias.symlink_to(self.root, target_is_directory=True)
        self.assert_protected(['.agent-trash/audit.jsonl'], self.ctx(base=str(alias)))

    def test_external_bucket_control_is_protected_and_payload_still_allowed(self):
        outside = Path(self.root).parent / (Path(self.root).name + '-external')
        outside.mkdir()
        self.addCleanup(outside.rmdir)
        # Paths need not exist for the classification/protection facts.
        self.assert_protected([outside / 'audit.jsonl'], self.ctx(outside))
        verdict = self.verdict([outside / 'tx1/payload'], self.ctx(outside))
        self.assertEqual((verdict.decision, verdict.code), ('ALLOW', 'ALLOW_TRASH_GC'))

    def test_payload_named_like_control_is_not_root_control(self):
        self.write('.agent-trash/tx1/state.json', 'payload')
        verdict = self.verdict(['.agent-trash/tx1/state.json'])
        self.assertEqual((verdict.decision, verdict.code), ('ALLOW', 'ALLOW_TRASH_GC'))

    def test_git_metadata_protection_wins_inside_trash(self):
        self.write('.agent-trash/tx1/.git/config', 'keep')
        self.assert_protected(['.agent-trash/tx1/.git'])

    def test_shell_dialects_share_root_control_refusal(self):
        self.write('.agent-trash/audit.jsonl', 'keep')
        for command, dialect in [('rm -rf .agent-trash', 'posix'),
                                 ('rd /s /q .agent-trash', 'cmd'),
                                 ('ri .agent-trash -r -fo', 'powershell')]:
            with self.subTest(dialect=dialect):
                specs, error = classify_command(command, dialect)
                self.assertIsNone(error)
                verdict = worst(decide_ops(specs, self.ctx()))
                self.assertEqual((verdict.decision, verdict.code), ('BLOCK', 'BLOCK_PROTECTED_PATH'))

    def test_quarantine_globs_do_not_bypass_control_protection(self):
        self.write('.agent-trash/audit.jsonl', 'keep')
        for command in ('rm -rf .agent-trash/*', 'rm -rf .agent-trash/manifest.*',
                        'rm -rf .agent-trash/tx1/*'):
            with self.subTest(command=command):
                specs, error = classify_command(command)
                self.assertIsNone(error)
                verdict = worst(decide_ops(specs, self.ctx()))
                self.assertEqual((verdict.decision, verdict.code), ('BLOCK', 'BLOCK_WILDCARD'))

    def test_enforced_cli_refuses_root_without_consuming_payload(self):
        filename = self.write('.agent-trash/tx1/payload', 'keep')
        env = {k:v for k,v in os.environ.items() if not k.startswith('AGENT_GUARD_')}
        result = subprocess.run([sys.executable, str(ROOT / 'skills/delete-guard/scripts/check.py'),
                                 '--enforce', '--json', '--', 'rm -rf .agent-trash'],
                                cwd=self.root, env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(json.loads(result.stdout)['code'], 'BLOCK_PROTECTED_PATH')
        self.assertEqual(Path(filename).read_text(), 'keep')


if __name__ == '__main__':
    unittest.main()
