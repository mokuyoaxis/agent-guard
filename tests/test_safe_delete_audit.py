"""safe_delete audits retain facts without duplicating recovery payloads."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

from core import policy
from core.recovery import RecoveryEngine
from tests.helpers import RepoFixture, git_available

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/delete-guard/scripts'


@unittest.skipUnless(git_available(), 'git required')
class SafeDeleteAuditTests(RepoFixture):
    def setUp(self):
        super().setUp()
        self.secret = 'ghp_' + 'Q' * 36
        self.name = self.secret + '.txt'
        self.reason = 'arbitrary private deletion note\n' + self.secret
        self.trash = Path(self.root, '.agent-trash')
        # CLI cwd is physical even when TMPDIR uses an alias (macOS /var).
        self.cli_root = os.path.realpath(self.root)

    def cli(self, *args, injection=None, script='safe_delete.py', session=None):
        env = {k: v for k, v in os.environ.items() if not k.startswith('AGENT_GUARD_')}
        env['AGENT_GUARD_SESSION'] = session or 'synthetic-safe-delete-audit'
        env['GIT_OPTIONAL_LOCKS'] = '0'
        script_args = [*args, '--json'] if script == 'restore.py' else ['--json', *args]
        argv = [sys.executable, str(SCRIPTS / script), *script_args]
        if injection:
            loader = '''import importlib.util, json, pathlib, sys
sys.path.insert(0, str(pathlib.Path(sys.argv[1]).parent))
spec=importlib.util.spec_from_file_location('safe_delete_cli',sys.argv[1])
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
'''
            runner = '\nsys.argv=[sys.argv[1],*sys.argv[2:]]\nraise SystemExit(module.main())\n'
            argv = [sys.executable, '-c', loader + injection + runner, *argv[1:]]
        proc = subprocess.run(argv, cwd=self.root, env=env, capture_output=True,
                              text=True, timeout=30)
        self.assertTrue(proc.stdout.strip(), proc.stderr)
        return proc, json.loads(proc.stdout)

    def events(self):
        raw = (self.trash / 'audit.jsonl').read_text()
        self.assertNotIn(self.secret, raw)
        self.assertNotIn('arbitrary private deletion note', raw)
        self.assertNotIn(self.root, raw)
        self.assertNotIn(self.cli_root, raw)
        records = [json.loads(line) for line in raw.splitlines() if line]
        allowed = {'event', 'tool', 'decision', 'code', 'target_count', 'phase',
                   'txid', 'outcome', 'moved_count', 'skipped_count', 'ts', 'session'}
        for record in records:
            if record.get('tool') == 'safe_delete':
                self.assertFalse(set(record) - allowed)
                self.assertNotIn('targets', record)
                self.assertNotIn('reason', record)
                self.assertNotIn('reasons', record)
                self.assertIsInstance(record['target_count'], int)
        return records

    def restore(self, txid, name=None):
        listing, listed = self.cli('list', script='restore.py')
        self.assertEqual(listing.returncode, 0)
        transaction = next(row for row in listed['transactions'] if row['txid'] == txid)
        self.assertEqual(transaction['state'], 'RESTORABLE')
        proc, report = self.cli(txid, script='restore.py')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(report['restored'], [str(Path(self.cli_root, name or self.name))])
        self.assertEqual(Path(self.root, name or self.name).read_text(), 'recovery bytes')
        engine = RecoveryEngine(self.root)
        self.assertEqual(engine.transactions()[txid]['state'], 'RESTORED')

    def test_dry_run_keeps_exact_cli_target_but_audit_only_counts(self):
        target = Path(self.write(self.name, 'recovery bytes'))
        proc, result = self.cli('--dry-run', '--reason', self.reason, self.name)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(result['targets'], [self.name])
        self.assertEqual(target.read_text(), 'recovery bytes')
        event, = self.events()
        self.assertEqual((event['decision'], event['code'], event['phase'], event['target_count']),
                         ('RELOCATE', policy.CODE_RELOCATE_PATHS, 'advisory', 1))
        self.assertNotIn('txid', event)
        self.assertFalse((self.trash / 'manifest.jsonl').exists())

    def test_outside_block_keeps_cli_reason_without_audit_copy(self):
        outside = str(Path(self.root).parent / self.name)
        proc, result = self.cli('--reason', self.reason, outside)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(result['verdict']['code'], 'BLOCK_OUT_OF_WORKSPACE')
        self.assertTrue(any(self.secret in reason for reason in result['verdict']['reasons']))
        event, = self.events()
        self.assertEqual((event['decision'], event['code'], event['target_count']),
                         ('BLOCK', 'BLOCK_OUT_OF_WORKSPACE', 1))

    def test_policy_blocks_real_and_dry_run_without_changing_targets(self):
        for args in ([], ['--dry-run']):
            with self.subTest(args=args):
                proc, result = self.cli(*args, '--reason', self.reason, '.')
                self.assertEqual(proc.returncode, 2)
                self.assertEqual(result['verdict']['code'], 'BLOCK_PROTECTED_PATH')
                self.assertTrue(Path(self.root, 'src/main.py').is_file())
                self.assertEqual(self.events()[-1]['target_count'], 1)

    def test_relocation_keeps_paths_and_roundtrip_without_reason_metadata(self):
        self.write(self.name, 'recovery bytes')
        proc, result = self.cli('--reason', self.reason, self.name)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        origin = str(Path(self.cli_root, self.name))
        self.assertEqual(result['moved'][0]['origin'], origin)
        self.assertFalse(Path(origin).exists())
        self.assertEqual(Path(result['moved'][0]['trash']).read_text(), 'recovery bytes')
        intent, outcome = self.events()
        self.assertEqual(intent['phase'], 'intent')
        self.assertEqual((outcome['txid'], outcome['moved_count'], outcome['skipped_count']),
                         (result['txid'], 1, 0))
        self.assertEqual((outcome['event'], outcome['phase']), ('outcome', 'complete'))
        manifest = (self.trash / 'manifest.jsonl').read_text()
        self.assertIn(origin, manifest)
        self.assertNotIn('arbitrary private deletion note', manifest)
        start = next(json.loads(line) for line in manifest.splitlines()
                     if json.loads(line)['type'] == 'tx-start')
        self.assertEqual(start['meta'], {'tool': 'safe_delete', 'code': policy.CODE_RELOCATE_PATHS})
        self.restore(result['txid'])

    def test_partial_relocation_keeps_counts_txid_and_restorable_payload(self):
        self.write(self.name, 'recovery bytes')
        absent = self.secret + '-absent.txt'
        proc, result = self.cli('--reason', self.reason, self.name, absent)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertEqual(result['verdict']['code'], policy.CODE_BLOCK_COMPENSATION_FAILED)
        self.assertEqual(result['skipped'][0]['raw'], absent)
        intent, blocked = self.events()
        self.assertEqual(intent['target_count'], 2)
        self.assertEqual((blocked['txid'], blocked['moved_count'], blocked['skipped_count']),
                         (result['txid'], 1, 1))
        self.assertEqual(blocked['decision'], 'BLOCK')
        self.restore(result['txid'])

    def test_storage_failure_retains_audit_transaction_for_partial_recovery(self):
        self.write(self.name, 'recovery bytes')
        second = self.secret + '-second.txt'
        self.write(second, 'second bytes')
        injection = '''original_move=module.recovery.RecoveryEngine._move
calls=[]
def fail_second(src,dest):
    calls.append(src)
    if len(calls)==2: raise module.recovery.StorageUnavailable('synthetic storage failure')
    return original_move(src,dest)
module.recovery.RecoveryEngine._move=staticmethod(fail_second)
'''
        proc, result = self.cli('--reason', self.reason, self.name, second, injection=injection)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertEqual(result['verdict']['code'], policy.CODE_BLOCK_RELOCATE_FAILED_STORAGE)
        self.assertEqual(Path(self.root, second).read_text(), 'second bytes')
        intent, blocked = self.events()
        self.assertEqual((blocked['target_count'], blocked['moved_count'], blocked['skipped_count']), (2, 1, 0))
        self.assertTrue(blocked['txid'])
        self.restore(blocked['txid'])

    def test_regenerable_delete_has_durable_minimal_intent_before_mutation(self):
        name = 'node_modules/pkg/' + self.name
        self.write(name, 'synthetic generated bytes')
        injection = '''original_delete=module.delete_directly
def checked_delete(spec):
    entries=[json.loads(line) for line in pathlib.Path('.agent-trash/audit.jsonl').read_text().splitlines()]
    assert len(entries)==1 and entries[0]['phase']=='intent'
    assert entries[0]['target_count']==1 and 'targets' not in entries[0]
    assert pathlib.Path(spec.resolved).is_file()
    return original_delete(spec)
module.delete_directly=checked_delete
'''
        proc, result = self.cli('--reason', self.reason, name, injection=injection)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(result['verdict']['code'], 'ALLOW_REGENERABLE')
        self.assertEqual(result['deleted'], ['deleted'])
        self.assertFalse(Path(self.root, name).exists())
        intent, outcome = self.events()
        self.assertEqual(intent['code'], 'ALLOW_REGENERABLE')
        self.assertEqual(outcome['target_count'], 1)
        self.assertFalse((self.trash / 'manifest.jsonl').exists())

    def test_housekeeping_payload_audits_counts_without_payload_name(self):
        name = '.agent-trash/unmanaged/' + self.name
        self.write(name, 'synthetic disposable payload')
        proc, result = self.cli('--reason', self.reason, name)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(result['verdict']['code'], 'ALLOW_TRASH_GC')
        self.assertFalse(Path(self.root, name).exists())
        self.assertEqual([event['target_count'] for event in self.events()], [1, 1])

    def test_absent_literal_noop_keeps_early_return_and_dry_run_counts(self):
        proc, result = self.cli('--reason', self.reason, self.name)
        self.assertEqual((proc.returncode, result['verdict']['code']), (0, 'ALLOW_NOOP'))
        self.assertFalse(self.trash.exists())
        proc, result = self.cli('--dry-run', '--reason', self.reason, self.name)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(self.events()[0]['target_count'], 1)

    def test_unmatched_glob_only_still_creates_no_audit(self):
        proc, result = self.cli('--dry-run', '--reason', self.reason, self.secret + '-*.bin')
        self.assertEqual(proc.returncode, 0)
        self.assertIn('no matches', result['outcome'])
        self.assertFalse(self.trash.exists())

    def test_glob_counts_expanded_targets_and_excludes_unmatched_patterns(self):
        self.write(self.name, 'recovery bytes')
        second = self.secret + '-second.txt'
        self.write(second, 'second bytes')
        unmatched = [self.secret + '-*.bin', self.secret + '-*.missing']
        proc, result = self.cli('--dry-run', '--reason', self.reason,
                                '*.txt', *unmatched)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(sorted(result['targets']), sorted([self.name, second]))
        self.assertEqual(result['no_match'], unmatched)
        self.assertEqual(self.events()[0]['target_count'], 2)

    def test_duplicate_concrete_entries_are_not_deduplicated_in_audit_count(self):
        self.write(self.name, 'recovery bytes')
        proc, result = self.cli('--dry-run', '--reason', self.reason,
                                self.name, self.name)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(result['targets'], [self.name, self.name])
        self.assertEqual(self.events()[0]['target_count'], 2)

    def test_fsync_failure_blocks_real_deletion_even_if_intent_line_remains(self):
        name = 'node_modules/pkg/' + self.name
        target = Path(self.write(name, 'synthetic generated bytes'))
        injection = '''original_append=module.audit.append
def failed_append(entry,path):
    original_fsync=module.audit.os.fsync
    def fail(fd): raise OSError('synthetic durable audit failure')
    module.audit.os.fsync=fail
    try: return original_append(entry,path)
    finally: module.audit.os.fsync=original_fsync
module.audit.append=failed_append
'''
        proc, result = self.cli('--reason', self.reason, name, injection=injection)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertEqual(result['verdict']['code'], policy.CODE_BLOCK_COMPENSATION_FAILED)
        self.assertEqual(target.read_text(), 'synthetic generated bytes')
        event, = self.events()
        self.assertEqual((event['phase'], event['target_count']), ('intent', 1))
        self.assertFalse((self.trash / 'manifest.jsonl').exists())

    def test_outcome_audit_failure_keeps_cli_result_and_restorable_transaction(self):
        self.write(self.name, 'recovery bytes')
        injection = '''original_append=module.audit.append
def fail_outcome(entry,path):
    if entry['event']=='outcome': raise OSError('synthetic outcome audit failure')
    return original_append(entry,path)
module.audit.append=fail_outcome
'''
        proc, result = self.cli('--reason', self.reason, self.name, injection=injection)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('synthetic outcome audit failure', result['warnings'][0])
        self.assertEqual(self.events()[0]['phase'], 'intent')
        self.restore(result['txid'])

    def test_automatic_session_metadata_retains_existing_correlation(self):
        self.write(self.name, 'recovery bytes')
        session = 'caller-controlled-session-marker'
        proc, result = self.cli('--dry-run', '--reason', self.reason, self.name, session=session)
        self.assertEqual(proc.returncode, 0)
        event, = self.events()
        self.assertEqual(event['session'], session)
        self.assertTrue(event['ts'])


if __name__ == '__main__':
    unittest.main()
