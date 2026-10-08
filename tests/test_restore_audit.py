"""Restore audit metadata does not duplicate recovery paths or error bodies."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

from core import AUDIT_NAME
from core.classifier import classify_paths
from core.recovery import RecoveryEngine
from tests.helpers import RepoFixture, git_available

ROOT = Path(__file__).resolve().parents[1]
RESTORE = ROOT / 'skills/delete-guard/scripts/restore.py'


@unittest.skipUnless(git_available(), 'git required')
class RestoreAuditTests(RepoFixture):
    def setUp(self):
        super().setUp()
        self.engine = RecoveryEngine(self.root)
        self.secret = 'ghp_' + 'R' * 36
        self.name = self.secret + '.txt'

    def quarantine(self):
        self.write(self.name, 'recovery bytes')
        paths = classify_paths([self.name], self.root, self.root, self.engine.trash_root)
        return self.engine.relocate(paths)['txid']

    def cli(self, *args, injection=None):
        env = {k:v for k,v in os.environ.items() if not k.startswith('AGENT_GUARD_')}
        env['AGENT_GUARD_SESSION'] = 'synthetic-audit-test'
        argv = [sys.executable, str(RESTORE), *args]
        if injection:
            argv = [sys.executable, '-c', injection, str(RESTORE), *args]
        return subprocess.run(argv, cwd=self.root, env=env, capture_output=True, text=True, timeout=30)

    def event(self):
        raw = Path(self.engine.trash_root, AUDIT_NAME).read_text()
        records = [json.loads(line) for line in raw.splitlines() if line]
        record = [r for r in records if r.get('event') == 'restore'][-1]
        self.assertNotIn(self.secret, json.dumps(record))
        self.assertNotIn(self.root, json.dumps(record))
        self.assertFalse({'restored', 'conflicts', 'errors'} & record.keys())
        return record

    def test_success_keeps_recovery_paths_but_audit_has_counts(self):
        txid = self.quarantine()
        proc = self.cli(txid, '--json')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = json.loads(proc.stdout)
        self.assertEqual(report['restored'], [str(Path(self.root, self.name))])
        self.assertEqual(Path(self.root, self.name).read_text(), 'recovery bytes')
        self.assertIn(self.secret, Path(self.engine.manifest_path).read_text())
        record = self.event()
        self.assertEqual(record['txid'], txid)
        self.assertEqual((record['restored_count'], record['conflict_count'], record['error_count']), (1,0,0))

    def test_conflict_keeps_cli_path_and_current_bytes_without_audit_copy(self):
        txid = self.quarantine()
        self.write(self.name, 'current bytes')
        proc = self.cli(txid, '--json')
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)['conflicts'], [str(Path(self.root, self.name))])
        self.assertEqual(Path(self.root, self.name).read_text(), 'current bytes')
        record = self.event()
        self.assertEqual((record['restored_count'], record['conflict_count'], record['error_count']), (0,1,0))

    def test_force_keeps_backup_transaction_correlation(self):
        txid = self.quarantine()
        self.write(self.name, 'current bytes')
        proc = self.cli(txid, '--force', '--json')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = json.loads(proc.stdout)
        record = self.event()
        self.assertTrue(record['force'])
        self.assertEqual(record['backup_txids'], report['backup_txids'])
        self.assertEqual(record['restored_count'], 1)

    def test_failure_body_remains_in_cli_not_audit(self):
        txid = self.quarantine()
        injection = '''import importlib.util, sys
sys.path.insert(0, str(__import__('pathlib').Path(sys.argv[1]).parent))
spec=importlib.util.spec_from_file_location('restore_cli',sys.argv[1])
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
def fail(self, src, dest):
    raise OSError('synthetic failure ' + 'ghp_' + 'R'*36)
module.recovery.RecoveryEngine._move=fail
sys.argv=[sys.argv[1],*sys.argv[2:]]
raise SystemExit(module.main())
'''
        proc = self.cli(txid, '--json', injection=injection)
        self.assertEqual(proc.returncode, 1)
        report = json.loads(proc.stdout)
        self.assertTrue(any(self.secret in value for value in report['errors']))
        record = self.event()
        self.assertEqual((record['restored_count'], record['conflict_count'], record['error_count']), (0,0,1))
        self.assertEqual(self.engine.transactions()[txid]['state'], 'RESTORABLE')

    def test_unknown_secret_shaped_id_is_hashed_in_audit(self):
        proc = self.cli(self.secret, '--json')
        self.assertEqual(proc.returncode, 1)
        self.assertFalse(json.loads(proc.stdout)['ok'])
        record = self.event()
        self.assertNotIn('txid', record)
        self.assertEqual(record['txid_sha256'], hashlib.sha256(self.secret.encode()).hexdigest())
        self.assertEqual(record['error_count'], 1)

    def test_invalid_pathlike_id_is_hashed_in_audit(self):
        value = '../' + self.secret
        proc = self.cli(value, '--json')
        self.assertEqual(proc.returncode, 1)
        record = self.event()
        self.assertNotIn('txid', record)
        self.assertEqual(record['txid_sha256'], hashlib.sha256(value.encode()).hexdigest())

    @unittest.skipIf(os.name == 'nt', 'POSIX surrogate-escaped argv')
    def test_invalid_non_utf8_id_still_returns_controlled_report(self):
        value = 'bad\udcff'
        proc = self.cli(value, '--json')
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(json.loads(proc.stdout)['error'], 'invalid quarantine transaction id')
        record = self.event()
        self.assertEqual(record['txid_sha256'], hashlib.sha256(value.encode('utf-8', 'surrogatepass')).hexdigest())


if __name__ == '__main__':
    unittest.main()
