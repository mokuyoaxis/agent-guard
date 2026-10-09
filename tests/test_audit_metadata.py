"""Audit storage/read projections and actual status/GC metadata exits."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from core import audit, policy
from core.classifier import classify_paths
from core.recovery import RecoveryEngine
from tests.helpers import RepoFixture, git_available

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills/delete-guard/scripts'
MARKER = 'SYNTHETIC_PRIVATE_AUDIT_MARKER_7814'
TXID = '20261008-123456-0123abcd'


class AuditMetadataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='agent-guard-audit-')
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name, 'audit.jsonl')

    def test_unknown_payloads_and_field_names_are_not_stored(self):
        record = {'event': 'check', 'code': 'BLOCK_PROTECTED_PATH',
                  'payload': {'body': MARKER}, MARKER: MARKER,
                  'command': MARKER, 'error': MARKER, 'reasons': [MARKER],
                  'targets': [MARKER], 'raw': MARKER}
        original = json.dumps(record, sort_keys=True)
        stored = audit.append(record, str(self.path))
        self.assertNotIn(MARKER, self.path.read_text())
        self.assertEqual(json.dumps(record, sort_keys=True), original)
        self.assertEqual(stored['code'], 'BLOCK_PROTECTED_PATH')
        self.assertFalse({'payload', 'command', 'error', 'reasons', 'raw'} & stored.keys())

    def test_allowed_names_do_not_allow_free_text_or_nested_payloads(self):
        for key in ('event', 'action', 'decision', 'code', 'phase', 'outcome',
                    'tool', 'dialect', 'dialect_requested', 'channel', 'rule_id',
                    'family', 'confidence', 'error'):
            for value in (MARKER, {'body': MARKER}, [MARKER]):
                with self.subTest(key=key, value_type=type(value).__name__):
                    self.assertNotIn(key, audit.project({key: value}))

    def test_counts_are_nonnegative_integers_and_flags_are_booleans(self):
        for bad in (True, -1, 2.5, MARKER, {'body': MARKER}):
            self.assertNotIn('target_count', audit.project({'target_count': bad}))
        self.assertEqual(audit.project({'target_count': 0, 'force': False}),
                         {'target_count': 0, 'force': False})
        for bad in (0, 1, MARKER, []):
            self.assertNotIn('ok', audit.project({'ok': bad}))

    def test_latency_omits_nonfinite_negative_and_boolean_values(self):
        for bad in (float('nan'), float('inf'), -1, True, MARKER):
            self.assertEqual(audit.project({'guard_latency_ms': bad}), {})
        self.assertEqual(audit.project({'guard_latency_ms': 2.75}),
                         {'guard_latency_ms': 2.75})

    def test_compensation_preserves_links_counts_and_snapshot_sha(self):
        sha = 'a' * 40
        stored = audit.append({'event': 'enforce-proceed', 'compensations': [
            {'strategy': 'snapshot', 'txid': TXID, 'status': 'complete', 'sha': sha,
             'path': MARKER, 'error': MARKER, 'meta': {'body': MARKER}},
            {'strategy': 'relocate', 'txid': TXID, 'status': 'partial',
             'moved': 2, 'skipped': 1, 'enumerated': 3},
            {'strategy': {'body': MARKER}, 'status': MARKER}, MARKER,
        ]}, str(self.path))
        self.assertNotIn(MARKER, self.path.read_text())
        self.assertEqual(stored['compensations'][0], {
            'strategy': 'snapshot', 'txid': TXID, 'status': 'complete', 'sha': sha})
        self.assertEqual(stored['compensations'][1]['moved'], 2)
        self.assertEqual(stored['compensations'][1]['skipped'], 1)

    def test_gc_reason_map_preserves_reviewed_labels_and_correlates_other_ids(self):
        projected = audit.project({'plan_reasons': {
            TXID: 'age', MARKER: 'capacity', 'bad': MARKER,
        }})
        self.assertEqual(projected['plan_reasons'], {
            TXID: 'age', audit.correlation_id(MARKER): 'capacity'})
        self.assertNotIn(MARKER, json.dumps(projected))

    def test_span_evidence_keeps_offsets_but_not_source_key_names(self):
        stored = audit.append({'event': 'exfil-sanitize', 'rule_id': 'secret/openai-key',
                               'family': 'secret', 'confidence': 'deterministic',
                               'channel': 'file-write', 'context': 'key=' + MARKER,
                               'span': [104, 151], 'start': 104, 'end': 151,
                               'length': 47, 'exempted': False, 'raw': MARKER},
                              str(self.path))
        self.assertEqual(stored['span'], [104, 151])
        self.assertEqual(stored['context'], 'key')
        self.assertEqual(stored['rule_id'], 'secret/openai-key')
        self.assertNotIn(MARKER, self.path.read_text())

    def test_digest_timestamp_and_span_fields_cannot_contain_arbitrary_text(self):
        for record in ({'ts': MARKER}, {'txid_sha256': MARKER},
                       {'missing_txid_sha256': [MARKER]}, {'sha': MARKER},
                       {'span': [0, MARKER]}, {'span': [8, 3]}, {'span': [False, 2]}):
            self.assertNotIn(MARKER, json.dumps(audit.project(record)))
        self.assertEqual(audit.project({'ts': '2026-10-08T12:34:56Z', 'sha': None}),
                         {'ts': '2026-10-08T12:34:56Z', 'sha': None})

    def test_return_value_matches_stored_record_and_projected_read(self):
        stored = audit.append({'event': 'check', 'session': MARKER}, str(self.path))
        self.assertEqual(stored, json.loads(self.path.read_text()))
        self.assertEqual([stored], audit.tail(str(self.path)))
        self.assertRegex(stored['session'], r'^sha256:[0-9a-f]{64}$')
        self.assertTrue(stored['ts'])

    def test_explicit_session_overrides_environment_without_changing_it(self):
        with mock.patch.dict(os.environ, {'AGENT_GUARD_SESSION': 'other-session'}):
            stored = audit.append({'session': MARKER}, str(self.path))
            self.assertEqual(stored['session'], audit.correlation_id(MARKER))
            self.assertEqual(os.environ['AGENT_GUARD_SESSION'], 'other-session')

    def test_invalid_explicit_session_falls_back_to_environment(self):
        with mock.patch.dict(os.environ, {'AGENT_GUARD_SESSION': MARKER}):
            for bad in (None, [], {'body': MARKER}):
                self.assertEqual(audit.append({'session': bad}, str(self.path))['session'],
                                 audit.correlation_id(MARKER))

    def test_same_environment_session_correlates_across_processes(self):
        env = dict(os.environ, AGENT_GUARD_SESSION=MARKER)
        outputs = [subprocess.check_output(
            [sys.executable, '-c', 'from core import audit; print(audit.session_id())'],
            cwd=ROOT, env=env, text=True).strip() for _ in range(2)]
        self.assertEqual(outputs, [audit.correlation_id(MARKER)] * 2)

    def test_sessionless_identity_is_stable_in_process_without_host_identity(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch('getpass.getuser', side_effect=AssertionError('identity read')), \
                mock.patch('platform.node', side_effect=AssertionError('identity read')):
            first = audit.session_id()
            self.assertEqual(first, audit.session_id())
            self.assertRegex(first, r'^sha256:[0-9a-f]{64}$')

    def test_sessionless_processes_have_distinct_correlation(self):
        env = dict(os.environ)
        env.pop('AGENT_GUARD_SESSION', None)
        outputs = [subprocess.check_output(
            [sys.executable, '-c', 'from core import audit; print(audit.session_id())'],
            cwd=ROOT, env=env, text=True).strip() for _ in range(2)]
        self.assertNotEqual(*outputs)

    def test_surrogate_metadata_is_not_emitted_or_rejected(self):
        value = MARKER + '\udcff'
        stored = audit.append({'session': value, 'txid': value}, str(self.path))
        self.assertEqual(stored['txid'], audit.correlation_id(value))
        self.path.read_text(encoding='utf-8', errors='strict')
        self.assertNotIn(MARKER, self.path.read_text())

    def test_generated_ids_and_noncanonical_ids_both_remain_findable(self):
        for txid in (TXID, MARKER, 'legacy_1'):
            audit.append({'event': 'restore', 'txid': txid}, str(self.path))
            records = audit.find_by_txid(str(self.path), txid)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]['txid'],
                             TXID if txid == TXID else audit.correlation_id(txid))
        self.assertNotIn(MARKER, self.path.read_text())

    def test_projection_is_idempotent_when_reappending_and_reading(self):
        first = audit.append({'event': 'restore', 'txid': MARKER,
                              'session': MARKER, 'backup_txids': [MARKER, TXID]},
                             str(self.path))
        second = audit.append(first, str(self.path))
        self.assertEqual(first, second)
        self.assertEqual(audit.tail(str(self.path)), [first, first])

    def test_legacy_read_preserves_bytes_and_drops_payloads_and_malformed_values(self):
        self.path.write_text(json.dumps({'event': 'check', 'code': MARKER,
                                         'session': MARKER, 'payload': {'body': MARKER},
                                         'txid': 'legacy_1'}) + '\n' +
                             json.dumps({'event': 'check', 'code': {'body': MARKER},
                                         'guard_latency_ms': [MARKER]}) + '\n')
        original = self.path.read_bytes()
        records = audit.tail(str(self.path))
        self.assertNotIn(MARKER, json.dumps(records))
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(len(audit.find_by_txid(str(self.path), 'legacy_1')), 1)

    def test_nonobject_and_torn_lines_are_skipped_before_tail_limit(self):
        self.path.write_text('{}\n[]\nnull\n12\n"text"\n{broken\n' +
                             json.dumps({'event': 'restore', 'txid': TXID}) + '\n')
        self.assertEqual(audit.tail(str(self.path), 1), [{'event': 'restore', 'txid': TXID}])
        self.assertEqual(len(audit.tail(str(self.path), 10)), 2)

    def test_zero_and_negative_tail_do_not_read_the_log(self):
        self.path.write_text('{}\n{}\n')
        with mock.patch('builtins.open', side_effect=AssertionError('unexpected read')):
            self.assertEqual(audit.tail(str(self.path), 0), [])
            self.assertEqual(audit.tail(str(self.path), -2), [])

    def test_missing_log_and_unmatched_id_return_empty(self):
        self.assertEqual(audit.tail(str(self.path)), [])
        self.assertEqual(audit.find_by_txid(str(self.path), TXID), [])

    def test_nonobject_append_is_rejected_without_creating_a_log(self):
        with self.assertRaisesRegex(TypeError, 'must be an object'):
            audit.append([MARKER], str(self.path))
        self.assertFalse(self.path.exists())

    def test_fsync_failure_is_not_hidden_by_projection(self):
        with mock.patch.object(audit.os, 'fsync', side_effect=OSError('synthetic fsync')):
            with self.assertRaisesRegex(OSError, 'synthetic fsync'):
                audit.append({'event': 'intent', 'session': MARKER}, str(self.path))
        self.assertNotIn(MARKER, self.path.read_text())


@unittest.skipUnless(git_available(), 'git required')
class AuditMetadataCliTests(RepoFixture):
    def setUp(self):
        super().setUp()
        self.env = {k: v for k, v in os.environ.items() if not k.startswith('AGENT_GUARD_')}
        self.env['AGENT_GUARD_SESSION'] = MARKER
        self.trash = Path(self.root, '.agent-trash')

    def cli(self, script, *args, injection=None):
        argv = [sys.executable, str(SCRIPTS / script), *args]
        if injection:
            argv = [sys.executable, '-c', injection, str(SCRIPTS / script), *args]
        return subprocess.run(argv, cwd=self.root, env=self.env,
                              capture_output=True, text=True, timeout=60)

    def test_status_json_and_text_validate_legacy_values_without_rewriting(self):
        self.trash.mkdir()
        path = self.trash / 'audit.jsonl'
        path.write_text(json.dumps({'event': 'check', 'code': MARKER, 'ts': MARKER}) + '\n' +
                        json.dumps({'event': 'check', 'code': {'body': MARKER}}) + '\n' +
                        '[]\n{broken\n' + json.dumps({
                            'event': 'check', 'code': 'BLOCK_PROTECTED_PATH',
                            'command': MARKER}) + '\n')
        original = path.read_bytes()
        for args in ((), ('--json',)):
            proc = self.cli('status.py', *args)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertNotIn(MARKER, proc.stdout + proc.stderr)
        data = json.loads(self.cli('status.py', '--json', '--tail', '0').stdout)
        self.assertEqual(data['recent_audit'], [])
        self.assertEqual(data['decisions'], {'BLOCK_PROTECTED_PATH': 1})
        self.assertEqual(path.read_bytes(), original)

    def test_status_projects_actor_but_keeps_session_authorization_and_state(self):
        policy.request_mode(str(self.trash), policy.MODE_RESTRICTED,
                            actor=MARKER, session=MARKER)
        state_path = Path(policy.state_path(str(self.trash), session=MARKER))
        original = state_path.read_bytes()
        for args in ((), ('--json',)):
            proc = self.cli('status.py', *args)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertNotIn(MARKER, proc.stdout + proc.stderr)
            self.assertIn(audit.correlation_id(MARKER), proc.stdout)
        data = json.loads(self.cli('status.py', '--json').stdout)
        self.assertEqual(data['mode'], 'RESTRICTED')
        self.assertEqual(data['mode_set_by'], audit.correlation_id(MARKER))
        self.assertEqual(state_path.read_bytes(), original)
        self.assertEqual(policy.load_mode(str(self.trash), session=MARKER)['set_by'], MARKER)
        self.assertEqual(policy.load_mode(str(self.trash), session='other')['mode'], 'NORMAL')

    def test_gc_missing_id_is_only_hashed_in_intent_and_receipt_but_cli_is_exact(self):
        self.write('payload.txt', 'synthetic recovery bytes')
        proc = self.cli('safe_delete.py', '--json', 'payload.txt')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        txid = json.loads(proc.stdout)['txid']
        proc = self.cli('gc.py', '--execute', '--json', '--txid', txid, '--txid', MARKER)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)['missing'], [MARKER])
        self.assertEqual(json.loads(proc.stdout)['purged'], [txid])
        raw = (self.trash / 'audit.jsonl').read_text()
        self.assertNotIn(MARKER, raw)
        events = [r for r in map(json.loads, raw.splitlines()) if r['event'].startswith('gc')]
        self.assertEqual(events[0]['txids'], [txid])
        self.assertEqual(events[0]['target_count'], 2)
        self.assertEqual(events[1]['purged'], [txid])
        self.assertEqual(events[1]['missing'], [])
        self.assertEqual(events[1]['missing_count'], 1)
        digest = hashlib.sha256(MARKER.encode()).hexdigest()
        self.assertEqual(events[0]['missing_txid_sha256'], [digest])
        self.assertEqual(events[1]['missing_txid_sha256'], [digest])

    def test_gc_fsync_failure_keeps_quarantined_payload_and_has_no_purge_tombstone(self):
        self.write('payload.txt', 'synthetic recovery bytes')
        proc = self.cli('safe_delete.py', '--json', 'payload.txt')
        txid = json.loads(proc.stdout)['txid']
        before = (self.trash / 'manifest.jsonl').read_bytes()
        injection = '''import runpy, sys
sys.path.insert(0, str(__import__('pathlib').Path(sys.argv[1]).parent))
from core import audit
def fail(fd): raise OSError('synthetic fsync')
audit.os.fsync=fail
sys.argv=sys.argv[1:]
runpy.run_path(sys.argv[0],run_name='__main__')
'''
        proc = self.cli('gc.py', '--execute', '--txid', txid, injection=injection)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertEqual((self.trash / 'manifest.jsonl').read_bytes(), before)
        self.assertTrue((self.trash / txid).is_dir())
        restored = self.cli('restore.py', txid, '--json')
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertEqual(Path(self.root, 'payload.txt').read_text(), 'synthetic recovery bytes')

    def test_legacy_transaction_id_restores_exact_paths_with_audit_correlation(self):
        origin = self.write('legacy.txt', 'synthetic legacy bytes')
        engine = RecoveryEngine(self.root)
        paths = classify_paths(['legacy.txt'], self.root, self.root, engine.trash_root)
        engine.relocate(paths, txid='legacy_1')
        before = (self.trash / 'manifest.jsonl').read_bytes()
        proc = self.cli('restore.py', 'legacy_1', '--json')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # Relocation journals retain the caller's lexical origin, including aliases.
        self.assertEqual(json.loads(proc.stdout)['restored'], [origin])
        self.assertEqual(Path(origin).read_text(), 'synthetic legacy bytes')
        self.assertTrue((self.trash / 'manifest.jsonl').read_bytes().startswith(before))
        record, = audit.find_by_txid(str(self.trash / 'audit.jsonl'), 'legacy_1')
        self.assertEqual(record['txid'], audit.correlation_id('legacy_1'))
        self.assertTrue(record['ok'])


if __name__ == '__main__':
    unittest.main()
