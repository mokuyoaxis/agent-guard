"""Forced restores preserve conflicting versions using existing transactions."""
import contextlib
import errno
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from core.classifier import classify_paths
from core.recovery import RecoveryEngine, StorageUnavailable


RESTORE_CLI = Path(__file__).resolve().parents[1] / (
    "skills/delete-guard/scripts/restore.py")


class ForceRestoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="guard-restore-force-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "workspace"
        self.outside = self.base / "outside"
        self.root.mkdir()
        self.outside.mkdir()
        self.engine = RecoveryEngine(str(self.root))

    def write(self, path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def link(self, target, path):
        try:
            os.symlink(target, path, target_is_directory=target.is_dir())
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f"symlinks unavailable: {exc}")

    def conflict(self, kind="file", name=None):
        origin = self.root / (name or ("item-" + kind))
        if kind == "directory":
            self.write(origin / "nested" / "old.txt", "old version")
        else:
            self.write(origin, "old version")
        specs = classify_paths([str(origin)], str(self.root), str(self.root),
                               self.engine.trash_root)
        relocated = self.engine.relocate(specs)
        self.assertEqual(len(relocated["moved"]), 1, relocated)
        if kind == "directory":
            self.write(origin / "nested" / "current.txt", "current version")
        elif kind == "link":
            target = self.outside / origin.name
            self.write(target, "external target")
            self.link(target, origin)
        else:
            self.write(origin, "current version")
        return origin, relocated["txid"], Path(relocated["moved"][0]["trash"])

    def assert_old(self, path, kind="file"):
        if kind == "directory":
            self.assertEqual((path / "nested" / "old.txt").read_text(), "old version")
            self.assertFalse((path / "nested" / "current.txt").exists())
        else:
            self.assertFalse(path.is_symlink())
            self.assertEqual(path.read_text(), "old version")

    def assert_current(self, path, kind="file", origin=None):
        if kind == "directory":
            self.assertEqual((path / "nested" / "current.txt").read_text(),
                             "current version")
            self.assertFalse((path / "nested" / "old.txt").exists())
        elif kind == "link":
            self.assertTrue(path.is_symlink())
            target = self.outside / origin.name
            self.assertEqual(os.readlink(path).replace("\\\\?\\", ""), str(target))
            self.assertEqual(target.read_text(), "external target")
        else:
            self.assertEqual(path.read_text(), "current version")

    def backup(self, result, original_txid):
        self.assertEqual(len(result["backup_txids"]), 1, result)
        txid = result["backup_txids"][0]
        tx = self.engine.transactions()[txid]
        self.assertEqual(tx["meta"]["restoring_txid"], original_txid)
        self.assertEqual(tx["state"], "RESTORABLE")
        self.assertEqual(len(tx["items"]), 1)
        return txid, Path(tx["items"][0]["trash_path"])

    def test_success_preserves_files_directories_and_links(self):
        for kind in ("file", "directory", "link"):
            with self.subTest(kind=kind):
                origin, txid, source = self.conflict(kind)
                result = self.engine.restore(txid, force=True)
                self.assertTrue(result["ok"], result)
                self.assertEqual(result["restored"], [str(origin)])
                self.assert_old(origin, kind)
                self.assertFalse(os.path.lexists(source))
                _, current = self.backup(result, txid)
                self.assert_current(current, kind, origin)
                self.assertEqual(self.engine.transactions()[txid]["state"], "RESTORED")

    def test_failed_old_version_move_preserves_files_directories_and_links(self):
        real_move = self.engine._move
        for kind in ("file", "directory", "link"):
            with self.subTest(kind=kind):
                origin, txid, source = self.conflict(kind)

                def fail_old(source_path, destination):
                    if source_path == str(source):
                        raise OSError(errno.EIO, "synthetic restore failure")
                    return real_move(source_path, destination)

                with mock.patch.object(self.engine, "_move", side_effect=fail_old):
                    result = self.engine.restore(txid, force=True)
                self.assertFalse(result["ok"], result)
                self.assertTrue(result["errors"])
                self.assertFalse(os.path.lexists(origin))
                self.assert_old(source, kind)
                _, current = self.backup(result, txid)
                self.assert_current(current, kind, origin)

    def test_preserved_version_can_be_restored_and_replaced_version_is_kept(self):
        origin, txid, _ = self.conflict()
        first = self.engine.restore(txid, force=True)
        saved_txid, _ = self.backup(first, txid)
        second = self.engine.restore(saved_txid, force=True)
        self.assertTrue(second["ok"], second)
        self.assert_current(origin)
        _, old = self.backup(second, saved_txid)
        self.assert_old(old)

    def test_no_force_keeps_conflict_and_creates_no_preservation_transaction(self):
        origin, txid, source = self.conflict()
        before = set(self.engine.transactions())
        result = self.engine.restore(txid)
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["conflicts"], [str(origin)])
        self.assertNotIn("backup_txids", result)
        self.assertEqual(set(self.engine.transactions()), before)
        self.assert_current(origin)
        self.assert_old(source)

    def test_force_with_no_occupant_creates_no_preservation_transaction(self):
        origin, txid, _ = self.conflict()
        origin.rename(self.root / "current-before")
        before = set(self.engine.transactions())
        result = self.engine.restore(txid, force=True)
        self.assertTrue(result["ok"], result)
        self.assertNotIn("backup_txids", result)
        self.assertEqual(set(self.engine.transactions()), before)
        self.assert_old(origin)

    def test_start_and_intent_write_failures_leave_both_versions_untouched(self):
        real_append = self.engine._manifest_append
        for failed_type in ("tx-start", "relocate-intent"):
            with self.subTest(record=failed_type):
                origin, txid, source = self.conflict(name="failure-" + failed_type)

                def fail_record(records):
                    if records[0]["txid"] != txid and records[0]["type"] == failed_type:
                        raise PermissionError("synthetic journal refusal")
                    return real_append(records)

                with mock.patch.object(self.engine, "_manifest_append",
                                       side_effect=fail_record):
                    result = self.engine.restore(txid, force=True)
                self.assertFalse(result["ok"], result)
                self.assertEqual(result["restored"], [])
                self.assertEqual(len(result["backup_txids"]), 1)
                self.assert_current(origin)
                self.assert_old(source)

    def test_completion_write_failure_keeps_current_version_restorable_by_intent(self):
        origin, txid, source = self.conflict()
        real_append = self.engine._manifest_append

        def fail_completion(records):
            if records[0]["txid"] != txid and records[0]["type"] == "relocate":
                raise PermissionError("synthetic completion failure")
            return real_append(records)

        with mock.patch.object(self.engine, "_manifest_append",
                               side_effect=fail_completion):
            result = self.engine.restore(txid, force=True)
        self.assertFalse(result["ok"], result)
        self.assertFalse(origin.exists())
        self.assert_old(source)
        saved_txid, current = self.backup(result, txid)
        self.assert_current(current)
        self.assertTrue(self.engine.transactions()[saved_txid]["items"][0][
            "recovered_from_intent"])
        resumed = self.engine.restore(saved_txid)
        self.assertTrue(resumed["ok"], resumed)
        self.assert_current(origin)
        self.assert_old(source)

    def test_storage_or_move_failure_never_falls_back_to_deletion(self):
        real_move = self.engine._move
        for unavailable in (False, True):
            with self.subTest(storage_unavailable=unavailable):
                origin, txid, source = self.conflict(name="storage-" + str(unavailable))

                def fail_backup(source_path, destination):
                    if source_path == str(origin):
                        if unavailable:
                            raise StorageUnavailable(errno.ENOSPC, "synthetic capacity")
                        raise OSError(errno.EIO, "synthetic move failure")
                    return real_move(source_path, destination)

                with mock.patch.object(self.engine, "_move", side_effect=fail_backup):
                    result = self.engine.restore(txid, force=True)
                self.assertFalse(result["ok"], result)
                self.assertEqual(result["restored"], [])
                self.assert_current(origin)
                self.assert_old(source)

    def test_partial_cross_device_backup_copy_preserves_the_original_versions(self):
        origin, txid, source = self.conflict()
        real_rename = os.rename

        def cross_device(source_path, destination):
            if source_path == str(origin):
                raise OSError(errno.EXDEV, "synthetic separate filesystem")
            return real_rename(source_path, destination)

        def partial_copy(source_path, destination):
            Path(destination).write_text("partial", encoding="utf-8")
            raise OSError(errno.ENOSPC, "synthetic partial copy")

        with mock.patch("core.recovery.os.rename", side_effect=cross_device), \
                mock.patch("core.recovery.shutil.copy2", side_effect=partial_copy):
            result = self.engine.restore(txid, force=True)
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["restored"], [])
        self.assert_current(origin)
        self.assert_old(source)

    def test_partial_cross_device_old_restore_keeps_both_complete_versions(self):
        origin, txid, source = self.conflict()
        real_rename = os.rename

        def cross_device(source_path, destination):
            if source_path == str(source):
                raise OSError(errno.EXDEV, "synthetic separate filesystem")
            return real_rename(source_path, destination)

        def partial_copy(source_path, destination):
            Path(destination).write_text("partial", encoding="utf-8")
            raise OSError(errno.EIO, "synthetic partial restore")

        with mock.patch("core.recovery.os.rename", side_effect=cross_device), \
                mock.patch("core.recovery.shutil.copy2", side_effect=partial_copy):
            result = self.engine.restore(txid, force=True)
        self.assertFalse(result["ok"], result)
        self.assertEqual(origin.read_text(), "partial")
        self.assert_old(source)
        _, current = self.backup(result, txid)
        self.assert_current(current)

    def test_cross_device_directory_cleanup_failure_keeps_a_complete_backup(self):
        origin, txid, source = self.conflict("directory")
        self.write(origin / "second.txt", "second current file")
        real_rename = os.rename
        real_rmtree = shutil.rmtree

        def cross_device(source_path, destination):
            if source_path == str(origin):
                raise OSError(errno.EXDEV, "synthetic separate filesystem")
            return real_rename(source_path, destination)

        def partial_remove(path, *args, **kwargs):
            if path == str(origin):
                os.unlink(origin / "second.txt")
                raise OSError(errno.EIO, "synthetic directory cleanup failure")
            return real_rmtree(path, *args, **kwargs)

        with mock.patch("core.recovery.os.rename", side_effect=cross_device), \
                mock.patch("core.recovery.shutil.rmtree", side_effect=partial_remove):
            result = self.engine.restore(txid, force=True)
        self.assertFalse(result["ok"], result)
        self.assert_old(source, "directory")
        _, current = self.backup(result, txid)
        self.assert_current(current, "directory")
        self.assertEqual((current / "second.txt").read_text(), "second current file")

    def test_reappearing_occupant_is_not_overwritten(self):
        origin, txid, source = self.conflict()
        real_move = self.engine._move

        def move_and_reappear(source_path, destination):
            real_move(source_path, destination)
            if source_path == str(origin):
                self.write(origin, "concurrent version")

        with mock.patch.object(self.engine, "_move", side_effect=move_and_reappear):
            result = self.engine.restore(txid, force=True)
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["conflicts"], [str(origin)])
        self.assertEqual(origin.read_text(), "concurrent version")
        self.assert_old(source)
        _, current = self.backup(result, txid)
        self.assert_current(current)

    def test_changed_source_after_preservation_is_refused(self):
        origin, txid, source = self.conflict()
        real_move = self.engine._move
        saved_dir = source.parent.with_name(source.parent.name + "-before")
        self.write(self.outside / source.name, "external file")

        def move_and_redirect(source_path, destination):
            real_move(source_path, destination)
            if source_path == str(origin):
                source.parent.rename(saved_dir)
                self.link(self.outside, source.parent)

        with mock.patch.object(self.engine, "_move", side_effect=move_and_redirect):
            result = self.engine.restore(txid, force=True)
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["errors"], ["restore paths changed after preservation"])
        self.assert_old(saved_dir / source.name)
        self.assertEqual((self.outside / source.name).read_text(), "external file")
        _, current = self.backup(result, txid)
        self.assert_current(current)

    def test_changed_target_after_preservation_is_refused(self):
        origin, txid, source = self.conflict(name="sub/file.txt")
        real_move = self.engine._move

        def move_and_redirect(source_path, destination):
            real_move(source_path, destination)
            if source_path == str(origin):
                origin.parent.rename(self.root / "sub-before")
                self.link(self.outside, origin.parent)

        with mock.patch.object(self.engine, "_move", side_effect=move_and_redirect):
            result = self.engine.restore(txid, force=True)
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["errors"], ["restore paths changed after preservation"])
        self.assertEqual(list(self.outside.iterdir()), [])
        self.assert_old(source)
        _, current = self.backup(result, txid)
        self.assert_current(current)

    def test_backup_id_collision_cannot_overwrite_existing_transactions_or_data(self):
        origin, txid, source = self.conflict()
        occupied = Path(self.engine.trash_root, "occupied_1")
        self.write(occupied / "keep.txt", "unmanaged data")
        for collision in (txid, "occupied_1"):
            with self.subTest(collision=collision):
                with mock.patch("core.recovery.new_txid", return_value=collision):
                    result = self.engine.restore(txid, force=True)
                self.assertFalse(result["ok"], result)
                self.assertNotIn("backup_txids", result)
                self.assert_current(origin)
                self.assert_old(source)
                self.assertEqual((occupied / "keep.txt").read_text(), "unmanaged data")

    def test_quarantine_root_cannot_be_preserved_into_itself(self):
        _, txid, source = self.conflict()
        storage = Path(self.engine.trash_root)
        records = self.engine.read_manifest()
        for record in records:
            if record.get("origin_path"):
                record["origin_path"] = str(storage)
        Path(self.engine.manifest_path).write_text(
            "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
        result = self.engine.restore(txid, force=True)
        self.assertFalse(result["ok"], result)
        self.assertNotIn("backup_txids", result)
        self.assertTrue(storage.is_dir())
        self.assert_old(source)

    def test_configured_quarantine_alias_is_not_moved(self):
        store = self.outside / "store"
        store.mkdir()
        alias = self.root / "store-alias"
        self.link(store, alias)
        self.engine = RecoveryEngine(str(self.root), str(alias))
        _, txid, source = self.conflict()
        records = self.engine.read_manifest()
        for record in records:
            if record.get("origin_path"):
                record["origin_path"] = str(alias)
        Path(self.engine.manifest_path).write_text(
            "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
        result = self.engine.restore(txid, force=True)
        self.assertFalse(result["ok"], result)
        self.assertNotIn("backup_txids", result)
        self.assertTrue(alias.is_symlink())
        self.assert_old(source)

    def test_quarantine_ancestor_cannot_be_preserved_recursively(self):
        self.engine = RecoveryEngine(str(self.root), str(self.root / "sub" / "store"))
        _, txid, source = self.conflict()
        current = self.root / "sub" / "current.txt"
        self.write(current, "current tree data")
        records = self.engine.read_manifest()
        for record in records:
            if record.get("origin_path"):
                record["origin_path"] = str(self.root / "sub")
        Path(self.engine.manifest_path).write_text(
            "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
        result = self.engine.restore(txid, force=True)
        self.assertFalse(result["ok"], result)
        self.assertNotIn("backup_txids", result)
        self.assertEqual(current.read_text(), "current tree data")
        self.assert_old(source)

    def test_external_quarantine_alias_supports_normal_forced_restore(self):
        store = self.outside / "store"
        store.mkdir()
        alias = self.root / "store-alias"
        self.link(store, alias)
        self.engine = RecoveryEngine(str(self.root), str(alias))
        origin, txid, _ = self.conflict()
        result = self.engine.restore(txid, force=True)
        self.assertTrue(result["ok"], result)
        self.assert_old(origin)
        _, current = self.backup(result, txid)
        self.assert_current(current)
        self.assertTrue(alias.is_symlink())

    def test_restore_journal_failure_reports_error_and_preservation_ids(self):
        origin, txid, _ = self.conflict()
        real_append = self.engine._manifest_append

        def fail_restore_record(records):
            if records[0]["type"] == "restore":
                raise PermissionError("synthetic restore journal failure")
            return real_append(records)

        with mock.patch.object(self.engine, "_manifest_append",
                               side_effect=fail_restore_record):
            result = self.engine.restore(txid, force=True)
        self.assertFalse(result["ok"], result)
        self.assertTrue(result["errors"])
        self.assert_old(origin)
        _, current = self.backup(result, txid)
        self.assert_current(current)

    def test_cli_json_and_text_show_preservation_transactions(self):
        for as_json in (False, True):
            with self.subTest(json=as_json):
                origin, txid, _ = self.conflict(name="cli-" + str(as_json))
                env = dict(os.environ, AGENT_GUARD_WORKSPACE=str(self.root),
                           AGENT_GUARD_TRASH=self.engine.trash_root)
                args = [sys.executable, str(RESTORE_CLI), txid, "--force"]
                if as_json:
                    args.append("--json")
                proc = subprocess.run(args, cwd=self.root, env=env,
                                      capture_output=True, text=True, timeout=15)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                if as_json:
                    result = json.loads(proc.stdout)
                    self.assertTrue(result["ok"], result)
                    _, current = self.backup(result, txid)
                    self.assert_current(current)
                else:
                    self.assertIn("preservation transaction:", proc.stdout)
                self.assert_old(origin)

    def test_cli_audit_failure_still_reports_preservation_ids(self):
        origin, txid, _ = self.conflict()
        spec = importlib.util.spec_from_file_location("force_restore_cli", RESTORE_CLI)
        module = importlib.util.module_from_spec(spec)
        previous_path = list(sys.path)
        try:
            sys.path.insert(0, str(RESTORE_CLI.parent))
            spec.loader.exec_module(module)
        finally:
            sys.path[:] = previous_path
        output = io.StringIO()
        with mock.patch.object(module, "context", return_value=self.engine), \
                mock.patch.object(module.audit, "append",
                                  side_effect=PermissionError("synthetic audit failure")), \
                mock.patch.object(sys, "argv", [str(RESTORE_CLI), txid,
                                               "--force", "--json"]), \
                contextlib.redirect_stdout(output):
            exit_code = module.main()
        self.assertEqual(exit_code, 1)
        result = json.loads(output.getvalue())
        self.assertFalse(result["ok"], result)
        self.assertTrue(result["errors"])
        self.assert_old(origin)
        _, current = self.backup(result, txid)
        self.assert_current(current)


if __name__ == "__main__":
    unittest.main()
