"""Deletion admission limits across processes and recovery boundaries."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from core import admission


ROOT = Path(__file__).resolve().parents[1]
CHILD = """
import json
import sys
from core.admission import AdmissionDenied, admit
if len(sys.argv) > 4:
    print('READY', flush=True)
    sys.stdin.readline()
try:
    with admit(sys.argv[1], sys.argv[2], [sys.argv[3]], quarantine=False):
        print(json.dumps({'allowed': True}), flush=True)
except AdmissionDenied as exc:
    print(json.dumps({'allowed': False, 'code': exc.code}), flush=True)
    sys.exit(2)
"""


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        clean_env = {name: value for name, value in os.environ.items()
                     if not name.startswith("AGENT_GUARD_")}
        self.environment = mock.patch.dict(os.environ, clean_env, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.directory = tempfile.TemporaryDirectory(prefix="delete-admission-")
        self.addCleanup(self.directory.cleanup)
        self.root = self.directory.name
        self.trash = str(Path(self.root, ".agent-trash"))

    def write(self, name, contents=b"x"):
        path = Path(self.root, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
        return str(path)

    def configure(self, **settings):
        path = Path(self.root, ".agent-guard", "delete-policy.json")
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(settings), encoding="utf-8")

    def consume(self, *paths, quarantine=False, approval=None):
        with admission.admit(self.root, self.trash, list(paths),
                             quarantine=quarantine, approval=approval):
            pass

    def assert_denied(self, code, *paths, quarantine=False, approval=None):
        with self.assertRaises(admission.AdmissionDenied) as caught:
            self.consume(*paths, quarantine=quarantine, approval=approval)
        self.assertEqual(caught.exception.code, code)
        self.assertTrue(caught.exception.reason)

    def describe(self):
        return admission.describe(self.root, self.trash)

    def child_environment(self):
        result = dict(os.environ)
        result["PYTHONPATH"] = str(ROOT)
        result["PYTHONDONTWRITEBYTECODE"] = "1"
        return result

    def run_child(self, path):
        result = subprocess.run(
            [sys.executable, "-B", "-c", CHILD, self.root, self.trash, path],
            cwd=str(ROOT), env=self.child_environment(), capture_output=True,
            text=True, timeout=20)
        self.assertIn(result.returncode, (0, 2), result.stderr)
        return result.returncode, json.loads(result.stdout)

    def test_readonly_description_does_not_create_state_or_storage(self):
        before = set(Path(self.root).rglob("*"))
        result = self.describe()
        self.assertEqual(result["used_bytes"], 0)
        self.assertEqual(result["used_entries"], 0)
        self.assertEqual(result["window_operations"], 0)
        self.assertEqual(set(Path(self.root).rglob("*")), before)

    def test_byte_budget_accepts_exact_limit_and_denies_next_operation(self):
        self.configure(session_max_bytes=8)
        first = self.write("first", b"12345678")
        second = self.write("second", b"x")
        self.consume(first)
        self.assertEqual(self.describe()["used_bytes"], 8)
        self.assert_denied("BLOCK_DELETE_LIMIT", second)
        self.assertEqual(Path(first).read_bytes(), b"12345678")
        self.assertEqual(Path(second).read_bytes(), b"x")
        self.assertEqual(self.describe()["used_bytes"], 8)

    def test_full_batch_is_rejected_before_context_or_source_mutation(self):
        self.configure(session_max_bytes=7)
        first = self.write("first", b"1234")
        second = self.write("second", b"5678")
        entered = False
        with self.assertRaises(admission.AdmissionDenied) as caught:
            with admission.admit(self.root, self.trash, [first, second],
                                 quarantine=False):
                entered = True
                Path(first).write_bytes(b"changed")
        self.assertEqual(caught.exception.code, "BLOCK_DELETE_LIMIT")
        self.assertFalse(entered)
        self.assertEqual(Path(first).read_bytes(), b"1234")
        self.assertEqual(Path(second).read_bytes(), b"5678")
        self.assertEqual(self.describe()["used_bytes"], 0)

    def test_failed_execution_conservatively_keeps_reserved_budget(self):
        self.configure(session_max_bytes=4)
        path = self.write("first", b"1234")
        with self.assertRaisesRegex(RuntimeError, "execution failed"):
            with admission.admit(self.root, self.trash, [path], quarantine=False):
                raise RuntimeError("execution failed")
        self.assertEqual(self.describe()["used_bytes"], 4)
        self.assert_denied("BLOCK_DELETE_LIMIT", path)

    def test_execution_errors_keep_the_original_exception_and_budget_debit(self):
        path = self.write("source", b"keep")
        for error in (OSError("guarded storage failure"),
                      ValueError("guarded execution invariant")):
            with self.subTest(error=type(error).__name__):
                with self.assertRaises(type(error)) as caught:
                    with admission.admit(self.root, self.trash, [path],
                                         quarantine=False):
                        raise error
                self.assertIs(caught.exception, error)
        self.assertEqual(self.describe()["used_bytes"], 8)
        self.assertEqual(Path(path).read_bytes(), b"keep")

    def test_default_session_accumulates_across_independent_processes(self):
        self.configure(session_max_bytes=7)
        first = self.write("first", b"1234")
        second = self.write("second", b"5678")
        self.assertEqual(self.run_child(first), (0, {"allowed": True}))
        self.assertEqual(self.run_child(second),
                         (2, {"allowed": False, "code": "BLOCK_DELETE_LIMIT"}))
        self.assertEqual(self.describe()["used_bytes"], 4)

    def test_two_processes_cannot_both_spend_the_last_budget(self):
        self.configure(session_max_bytes=7)
        paths = [self.write("first", b"1234"), self.write("second", b"5678")]
        processes = []
        try:
            for path in paths:
                process = subprocess.Popen(
                    [sys.executable, "-B", "-c", CHILD, self.root, self.trash,
                     path, "barrier"], cwd=str(ROOT), env=self.child_environment(),
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True)
                processes.append(process)
                self.assertEqual(process.stdout.readline().strip(), "READY")
            for process in processes:
                process.stdin.write("start\n")
                process.stdin.flush()
            reports = []
            for process in processes:
                output, error = process.communicate(timeout=20)
                self.assertIn(process.returncode, (0, 2), error)
                reports.append(json.loads(output))
            self.assertEqual(sum(report["allowed"] for report in reports), 1)
            self.assertEqual([report["code"] for report in reports
                              if not report["allowed"]], ["BLOCK_DELETE_LIMIT"])
            self.assertEqual(self.describe()["used_bytes"], 4)
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.communicate()

    def test_directory_budget_counts_descendants_including_root(self):
        self.configure(session_max_entries=3)
        self.write("tree/first", b"a")
        self.write("tree/nested/second", b"b")
        self.assert_denied("BLOCK_DELETE_LIMIT", str(Path(self.root, "tree")))
        self.assertEqual(self.describe()["used_entries"], 0)
        self.assertTrue(Path(self.root, "tree/nested/second").is_file())

    def test_hardlinks_are_conservatively_counted_per_path(self):
        original = self.write("first", b"1234")
        alias = str(Path(self.root, "second"))
        os.link(original, alias)
        self.consume(original, alias)
        result = self.describe()
        self.assertEqual(result["used_bytes"], 8)
        self.assertEqual(result["used_entries"], 2)

    def test_symlink_target_contents_are_not_scanned_or_budgeted(self):
        self.configure(session_max_bytes=65536)
        target = self.write("large-target", b"x" * 1048576)
        link = Path(self.root, "tree/link")
        link.parent.mkdir()
        link.symlink_to(target)
        self.consume(str(link.parent))
        result = self.describe()
        self.assertEqual(result["used_entries"], 2)
        self.assertLess(result["used_bytes"], 65536)
        self.assertEqual(Path(target).stat().st_size, 1048576)

    def test_quarantine_capacity_does_not_block_direct_regenerable_deletion(self):
        self.configure(quarantine_max_bytes=1)
        path = self.write("generated", b"1234")
        self.assert_denied("BLOCK_DELETE_LIMIT", path, quarantine=True)
        self.consume(path, quarantine=False)
        self.assertEqual(self.describe()["used_bytes"], 4)

    def test_rate_limit_blocks_new_targets_without_spending_budget(self):
        self.configure(rate_max_operations=2)
        paths = [self.write(str(number)) for number in range(3)]
        self.consume(paths[0])
        self.consume(paths[1])
        self.assert_denied("BLOCK_DELETE_RATE", paths[2])
        self.assertEqual(self.describe()["used_entries"], 2)
        self.assertEqual(self.describe()["window_operations"], 2)

    def test_repeated_target_limit_detects_recreated_or_unchanged_target(self):
        self.configure(repeat_max_operations=2)
        path = self.write("repeat")
        self.consume(path)
        self.consume(path)
        self.assert_denied("BLOCK_DELETE_REPEAT", path)
        self.assertEqual(self.describe()["used_entries"], 2)

    def test_corrupt_state_blocks_and_preserves_source(self):
        path = self.write("source", b"keep")
        self.consume(path)
        Path(self.trash, "admission.json").write_text("{invalid", encoding="utf-8")
        self.assert_denied("BLOCK_DELETE_STATE_INVALID", path)
        self.assertEqual(Path(path).read_bytes(), b"keep")
        self.assertEqual(Path(self.trash, "admission.json").read_text(), "{invalid")

    def test_duplicate_state_keys_are_rejected_without_repair_or_source_mutation(self):
        path = self.write("source", b"keep")
        state = Path(self.trash, "admission.json")
        state.parent.mkdir()
        contents = '{"version":1,"version":1,"sessions":{},"approvals":{}}'
        state.write_text(contents, encoding="utf-8")
        self.assert_denied("BLOCK_DELETE_STATE_INVALID", path)
        self.assertEqual(state.read_text(encoding="utf-8"), contents)
        self.assertEqual(Path(path).read_bytes(), b"keep")

    def test_explicit_session_reset_preserves_other_sessions(self):
        path = self.write("source", b"keep")
        with mock.patch.dict(os.environ, {"AGENT_GUARD_SESSION": "first"}):
            self.consume(path)
        with mock.patch.dict(os.environ, {"AGENT_GUARD_SESSION": "second"}):
            self.consume(path)
            admission.reset_session(self.root, self.trash)
            self.assertEqual(self.describe()["used_bytes"], 0)
        with mock.patch.dict(os.environ, {"AGENT_GUARD_SESSION": "first"}):
            self.assertEqual(self.describe()["used_bytes"], 4)

    def test_configured_protection_is_checked_before_admission(self):
        self.configure(protected_paths=["evidence"])
        path = self.write("evidence/keep", b"private")
        self.assert_denied("BLOCK_DELETE_PROTECTED", path)
        self.assertEqual(self.describe()["used_entries"], 0)
        self.assertEqual(Path(path).read_bytes(), b"private")

    def test_protected_descendant_blocks_whole_parent_operation(self):
        self.configure(protected_paths=["tree/evidence"])
        self.write("tree/evidence/keep")
        self.write("tree/generated/output")
        self.assert_denied("BLOCK_DELETE_PROTECTED", str(Path(self.root, "tree")))
        self.assertTrue(Path(self.root, "tree/generated/output").is_file())

    def test_batch_byte_threshold_requires_confirmation(self):
        self.configure(batch_max_bytes=3)
        path = self.write("large", b"1234")
        self.assert_denied("BLOCK_DELETE_BATCH_CONFIRM", path)
        self.assertEqual(self.describe()["used_bytes"], 0)

    def test_batch_entry_threshold_counts_directory_descendants(self):
        self.configure(batch_max_entries=2)
        self.write("tree/first")
        self.write("tree/second")
        self.assert_denied("BLOCK_DELETE_BATCH_CONFIRM", str(Path(self.root, "tree")))
        self.assertEqual(self.describe()["used_entries"], 0)

    def test_confirmed_cleanup_subset_can_be_consumed_once_per_target(self):
        self.configure(batch_max_bytes=3)
        paths = [self.write("first", b"1234"), self.write("second", b"5678")]
        token = admission.approve_cleanup(self.root, self.trash, paths, "plan-123")
        self.consume(paths[0], approval=token)
        self.consume(paths[1], approval=token)
        self.assertEqual(self.describe()["used_bytes"], 8)
        self.assert_denied("BLOCK_DELETE_BATCH_CONFIRM", paths[0], approval=token)
        self.assertEqual(self.describe()["used_bytes"], 8)

    def test_confirmation_is_bound_to_the_selected_paths(self):
        self.configure(batch_max_bytes=3)
        first = self.write("first", b"1234")
        second = self.write("second", b"5678")
        token = admission.approve_cleanup(self.root, self.trash, [first], "plan-123")
        self.assert_denied("BLOCK_DELETE_BATCH_CONFIRM", second, approval=token)
        self.consume(first, approval=token)
        self.assertEqual(self.describe()["used_bytes"], 4)

    def test_target_changed_after_confirmation_is_rejected(self):
        self.configure(batch_max_bytes=3)
        path = self.write("source", b"1234")
        token = admission.approve_cleanup(self.root, self.trash, [path], "plan-123")
        old_stat = os.stat(path)
        Path(path).write_bytes(b"5678")
        os.utime(path, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns + 1000000000))
        self.assert_denied("BLOCK_DELETE_BATCH_CONFIRM", path, approval=token)
        self.assertEqual(self.describe()["used_bytes"], 0)
        self.assertEqual(Path(path).read_bytes(), b"5678")

    def test_confirmation_never_overrides_session_or_quarantine_budget(self):
        self.configure(session_max_bytes=3, quarantine_max_bytes=0,
                       batch_max_bytes=3)
        path = self.write("source", b"1234")
        token = admission.approve_cleanup(self.root, self.trash, [path], "plan-123")
        self.assert_denied("BLOCK_DELETE_LIMIT", path, approval=token)
        self.configure(session_max_bytes=100, quarantine_max_bytes=0,
                       batch_max_bytes=3)
        token = admission.approve_cleanup(self.root, self.trash, [path], "plan-456")
        self.assert_denied("BLOCK_DELETE_LIMIT", path, quarantine=True,
                           approval=token)
        self.assertEqual(self.describe()["used_bytes"], 0)
        self.assertEqual(Path(path).read_bytes(), b"1234")

    def test_configuration_change_invalidates_confirmation(self):
        self.configure(batch_max_bytes=3)
        path = self.write("source", b"1234")
        token = admission.approve_cleanup(self.root, self.trash, [path], "plan-123")
        self.configure(batch_max_bytes=2)
        self.assert_denied("BLOCK_DELETE_BATCH_CONFIRM", path, approval=token)
        self.assertEqual(self.describe()["used_bytes"], 0)

    def test_scan_failure_is_explicit_and_does_not_consume_budget(self):
        path = self.write("source", b"keep")
        with mock.patch.object(admission, "_measure",
                               side_effect=PermissionError("cannot inspect source")):
            self.assert_denied("BLOCK_DELETE_STATE_INVALID", path)
        self.assertEqual(self.describe()["used_bytes"], 0)
        self.assertEqual(Path(path).read_bytes(), b"keep")

    def test_scan_bound_also_limits_directory_entries_buffered_before_sorting(self):
        tree = Path(self.root, "tree")
        for number in range(30):
            self.write("tree/entry-%02d" % number)
        real_scandir = os.scandir
        consumed = []

        class CountingListing:
            def __init__(self, path):
                self.inner = real_scandir(path)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.inner.close()

            def __iter__(self):
                for entry in self.inner:
                    consumed.append(entry.name)
                    yield entry

        with mock.patch.object(admission.os, "scandir", side_effect=CountingListing):
            with self.assertRaises(OSError):
                admission._measure([str(tree)], max_entries=2)
        # One entry is the root; at most one child plus the first excess
        # child may be read before the bounded scanner refuses the tree.
        self.assertLessEqual(len(consumed), 2)
        self.assertEqual(len(list(tree.iterdir())), 30)

    def test_rate_window_expires_without_resetting_cumulative_budget(self):
        self.configure(rate_window_seconds=60, rate_max_operations=1)
        first = self.write("first")
        second = self.write("second")
        with mock.patch("core.admission.time.time", return_value=1000):
            self.consume(first)
        with mock.patch("core.admission.time.time", return_value=1061):
            self.consume(second)
            result = self.describe()
        self.assertEqual(result["used_bytes"], 2)
        self.assertEqual(result["window_operations"], 1)

    def test_normal_restore_remains_available_when_delete_budget_is_exhausted(self):
        from core.classifier import classify_paths
        from core.recovery import RecoveryEngine

        self.configure(session_max_bytes=4)
        source = self.write("source", b"keep")
        engine = RecoveryEngine(self.root, self.trash)
        specs = classify_paths([source], self.root, self.root, self.trash)
        report = engine.relocate(specs)
        self.assertEqual(len(report["moved"]), 1)
        self.assertFalse(Path(source).exists())
        self.assertEqual(self.describe()["used_bytes"], 4)
        self.assert_denied("BLOCK_DELETE_LIMIT", self.write("next"))
        restored = engine.restore(report["txid"])
        self.assertTrue(restored["ok"], restored)
        self.assertEqual(Path(source).read_bytes(), b"keep")
        self.assertEqual(self.describe()["used_bytes"], 4)


if __name__ == "__main__":
    unittest.main()
