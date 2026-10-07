"""Restore source containment with disposable, synthetic files only."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from core.classifier import classify_paths
from core.recovery import RecoveryEngine


RESTORE_CLI = Path(__file__).resolve().parents[1] / (
    "skills/delete-guard/scripts/restore.py")
SOURCE_ERROR = "restore source is outside its transaction or cannot be resolved"


class RestoreSourceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="guard-restore-source-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "workspace"
        self.outside = self.base / "outside"
        self.root.mkdir()
        self.outside.mkdir()
        self.engine = RecoveryEngine(str(self.root))

    def write(self, relative, content="synthetic original"):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def relocate(self, *relative, txid=None):
        specs = classify_paths(list(relative), str(self.root), str(self.root),
                               self.engine.trash_root)
        result = self.engine.relocate(specs, txid=txid)
        self.assertEqual(len(result["moved"]), len(relative), result)
        self.assertEqual(self.engine.transactions()[result["txid"]]["state"],
                         "RESTORABLE")
        return result

    def link(self, target, path):
        try:
            os.symlink(target, path, target_is_directory=True)
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f"symlinks unavailable: {exc}")

    def rewrite_source(self, old, new):
        records = self.engine.read_manifest()
        for record in records:
            if record.get("trash_path") == old:
                record["trash_path"] = new
        Path(self.engine.manifest_path).write_text(
            "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")

    def assert_blocked(self, result):
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["restored"], [])
        self.assertEqual(result["conflicts"], [])
        self.assertEqual(result["errors"], [SOURCE_ERROR])

    def redirect_source_parent(self, moved):
        source = Path(moved["trash"])
        saved = source.parent.with_name(source.parent.name + "-before")
        source.parent.rename(saved)
        self.link(self.outside, source.parent)
        return saved / source.name

    def test_redirected_source_parent_preserves_both_files(self):
        origin = self.write("sub/file.txt")
        relocated = self.relocate("sub/file.txt")
        saved = self.redirect_source_parent(relocated["moved"][0])
        external = self.outside / "file.txt"
        external.write_text("synthetic external", encoding="utf-8")
        result = self.engine.restore(relocated["txid"])
        self.assert_blocked(result)
        self.assertFalse(origin.exists())
        self.assertEqual(saved.read_text(), "synthetic original")
        self.assertEqual(external.read_text(), "synthetic external")

    def test_transaction_link_to_external_directory_is_refused(self):
        origin = self.write("file.txt")
        relocated = self.relocate("file.txt")
        tx_dir = Path(self.engine.trash_root, relocated["txid"])
        saved = tx_dir.with_name(tx_dir.name + "-before")
        tx_dir.rename(saved)
        self.link(self.outside, tx_dir)
        external = self.outside / "file.txt"
        external.write_text("synthetic external", encoding="utf-8")
        result = self.engine.restore(relocated["txid"])
        self.assert_blocked(result)
        self.assertFalse(origin.exists())
        self.assertEqual((saved / "file.txt").read_text(), "synthetic original")
        self.assertEqual(external.read_text(), "synthetic external")
        self.assertTrue(tx_dir.is_symlink())

    def test_transaction_link_to_another_transaction_is_refused(self):
        origin = self.write("file.txt")
        first = self.relocate("file.txt")
        self.write("file.txt", "synthetic second transaction")
        second = self.relocate("file.txt")
        first_dir = Path(self.engine.trash_root, first["txid"])
        saved = first_dir.with_name(first_dir.name + "-before")
        first_dir.rename(saved)
        self.link(Path(self.engine.trash_root, second["txid"]), first_dir)
        result = self.engine.restore(first["txid"])
        self.assert_blocked(result)
        self.assertFalse(origin.exists())
        self.assertEqual((saved / "file.txt").read_text(), "synthetic original")
        self.assertEqual(Path(second["moved"][0]["trash"]).read_text(),
                         "synthetic second transaction")

    def test_invalid_ids_are_refused_before_lookup_or_cleanup(self):
        # Before validation, a manifest-owned traversal ID could reach the
        # tidy-up rmdir even when its transaction contained no items.
        self.engine._manifest_append([
            {"type": "tx-start", "txid": "../../outside"}])
        before = Path(self.engine.manifest_path).read_bytes()
        for txid in ("", ".", "..", "../../outside", "/outside", "a/b",
                     "a\\b", "C:outside", "a\nb", None, ["id"]):
            with self.subTest(txid=txid):
                with mock.patch.object(self.engine, "transactions") as lookup:
                    result = self.engine.restore(txid)
                lookup.assert_not_called()
                self.assertEqual(result, {
                    "ok": False, "error": "invalid quarantine transaction id"})
                self.assertTrue(self.outside.is_dir())
                self.assertEqual(Path(self.engine.manifest_path).read_bytes(), before)

    def test_recorded_source_must_belong_to_its_transaction(self):
        current = self.write("file.txt")
        relocated = self.relocate("file.txt")
        moved = relocated["moved"][0]
        current.write_text("synthetic current", encoding="utf-8")
        external = self.outside / "file.txt"
        external.write_text("synthetic external", encoding="utf-8")
        other_dir = Path(self.engine.trash_root, "another-transaction")
        other_dir.mkdir()
        other_file = other_dir / "file.txt"
        other_file.write_text("synthetic other", encoding="utf-8")
        relative = self.base.name + "-missing-relative.txt"
        self.assertFalse(Path(relative).exists())
        manifest = Path(self.engine.manifest_path)
        original_records = manifest.read_bytes()
        invalid = [str(external), str(other_file), self.engine.manifest_path,
                   str(Path(moved["trash"]).parent), relative,
                   str(Path(moved["trash"]).parent / "sub" / ".." / "file.txt")]
        for source in invalid:
            with self.subTest(source=source):
                manifest.write_bytes(original_records)
                self.rewrite_source(moved["trash"], source)
                result = self.engine.restore(relocated["txid"], force=True)
                self.assert_blocked(result)
                self.assertEqual(Path(moved["trash"]).read_text(),
                                 "synthetic original")
                self.assertEqual(current.read_text(), "synthetic current")
                self.assertEqual(external.read_text(), "synthetic external")
                self.assertEqual(other_file.read_text(), "synthetic other")

    def test_force_source_refusal_preserves_the_current_version(self):
        current = self.write("sub/file.txt")
        relocated = self.relocate("sub/file.txt")
        saved = self.redirect_source_parent(relocated["moved"][0])
        external = self.outside / "file.txt"
        external.write_text("synthetic external", encoding="utf-8")
        current.write_text("synthetic current", encoding="utf-8")
        result = self.engine.restore(relocated["txid"], force=True)
        self.assert_blocked(result)
        self.assertEqual(current.read_text(), "synthetic current")
        self.assertEqual(saved.read_text(), "synthetic original")
        self.assertEqual(external.read_text(), "synthetic external")

    def test_unsafe_later_source_prevents_the_whole_batch_from_moving(self):
        first = self.write("first.txt")
        self.write("second.txt")
        relocated = self.relocate("first.txt", "second.txt")
        first.write_text("synthetic current", encoding="utf-8")
        external = self.outside / "second.txt"
        external.write_text("synthetic external", encoding="utf-8")
        self.rewrite_source(relocated["moved"][1]["trash"], str(external))
        with mock.patch.object(self.engine, "_move") as move:
            result = self.engine.restore(relocated["txid"], force=True)
        move.assert_not_called()
        self.assert_blocked(result)
        self.assertEqual(first.read_text(), "synthetic current")
        for moved in relocated["moved"]:
            self.assertEqual(Path(moved["trash"]).read_text(), "synthetic original")
        self.assertEqual(external.read_text(), "synthetic external")

    def test_source_is_rechecked_after_an_earlier_restore(self):
        first = self.write("first.txt")
        second = self.write("sub/file.txt")
        relocated = self.relocate("first.txt", "sub/file.txt")
        second.write_text("synthetic current", encoding="utf-8")
        external = self.outside / "file.txt"
        external.write_text("synthetic external", encoding="utf-8")
        real_move = self.engine._move
        saved = []

        def move_then_redirect(source, destination):
            real_move(source, destination)
            saved.append(self.redirect_source_parent(relocated["moved"][1]))

        with mock.patch.object(self.engine, "_move",
                               side_effect=move_then_redirect) as move:
            result = self.engine.restore(relocated["txid"], force=True)
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["restored"], [str(first)])
        self.assertEqual(result["errors"], [SOURCE_ERROR])
        self.assertEqual(move.call_count, 1)
        self.assertEqual(second.read_text(), "synthetic current")
        self.assertEqual(saved[0].read_text(), "synthetic original")
        self.assertEqual(external.read_text(), "synthetic external")

    def test_source_parent_alias_inside_transaction_is_supported(self):
        original = self.write("sub/file.txt")
        relocated = self.relocate("sub/file.txt")
        source = Path(relocated["moved"][0]["trash"])
        saved = source.parent.with_name(source.parent.name + "-before")
        source.parent.rename(saved)
        self.link(saved, source.parent)
        result = self.engine.restore(relocated["txid"])
        self.assertTrue(result["ok"], result)
        self.assertEqual(original.read_text(), "synthetic original")

    def test_external_quarantine_alias_and_historical_id_are_supported(self):
        store = self.outside / "store"
        store.mkdir()
        alias = self.base / "store-alias"
        self.link(store, alias)
        self.engine = RecoveryEngine(str(self.root), str(alias))
        original = self.write("file.txt")
        relocated = self.relocate("file.txt", txid="legacy_1")
        result = self.engine.restore(relocated["txid"])
        self.assertTrue(result["ok"], result)
        self.assertEqual(original.read_text(), "synthetic original")
        self.assertFalse(Path(relocated["moved"][0]["trash"]).exists())

    def test_source_resolution_failure_preserves_both_versions(self):
        current = self.write("file.txt")
        relocated = self.relocate("file.txt")
        current.write_text("synthetic current", encoding="utf-8")
        with mock.patch.object(self.engine, "_validate_restore_source",
                               side_effect=OSError("synthetic resolver failure")):
            result = self.engine.restore(relocated["txid"], force=True)
        self.assert_blocked(result)
        self.assertEqual(current.read_text(), "synthetic current")
        self.assertEqual(Path(relocated["moved"][0]["trash"]).read_text(),
                         "synthetic original")

    def test_missing_source_does_not_overwrite_the_current_version(self):
        current = self.write("file.txt")
        relocated = self.relocate("file.txt")
        source = Path(relocated["moved"][0]["trash"])
        saved = source.with_name("saved-copy.txt")
        source.rename(saved)
        current.write_text("synthetic current", encoding="utf-8")
        result = self.engine.restore(relocated["txid"], force=True)
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["restored"], [])
        self.assertEqual(current.read_text(), "synthetic current")
        self.assertEqual(saved.read_text(), "synthetic original")
        self.assertEqual(result["errors"], [f"quarantine copy vanished: {source}"])

    def test_unknown_well_formed_id_retains_the_existing_response(self):
        result = self.engine.restore("unknown_1")
        self.assertEqual(result, {"ok": False, "error": "unknown transaction: unknown_1"})
        self.assertFalse(Path(self.engine.trash_root).exists())

    def test_cli_refuses_the_source_without_consuming_any_files(self):
        origin = self.write("sub/file.txt")
        relocated = self.relocate("sub/file.txt")
        saved = self.redirect_source_parent(relocated["moved"][0])
        external = self.outside / "file.txt"
        external.write_text("synthetic external", encoding="utf-8")
        env = dict(os.environ, AGENT_GUARD_WORKSPACE=str(self.root),
                   AGENT_GUARD_TRASH=self.engine.trash_root)
        proc = subprocess.run(
            [sys.executable, str(RESTORE_CLI), relocated["txid"], "--json"],
            cwd=self.root, env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assert_blocked(json.loads(proc.stdout))
        self.assertFalse(origin.exists())
        self.assertEqual(saved.read_text(), "synthetic original")
        self.assertEqual(external.read_text(), "synthetic external")


if __name__ == "__main__":
    unittest.main()
