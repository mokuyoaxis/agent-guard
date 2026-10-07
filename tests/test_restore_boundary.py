"""Restore destination checks using disposable, synthetic files only."""
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


class RestoreBoundaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="guard-restore-boundary-")
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

    def relocate(self, *relative):
        specs = classify_paths(list(relative), str(self.root), str(self.root),
                               self.engine.trash_root)
        result = self.engine.relocate(specs)
        self.assertEqual(len(result["moved"]), len(relative), result)
        self.assertEqual(self.engine.transactions()[result["txid"]]["state"],
                         "RESTORABLE")
        return result

    def link(self, target, path, directory=False):
        try:
            os.symlink(target, path, target_is_directory=directory)
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f"symlinks unavailable: {exc}")

    def replace_parent(self, target):
        (self.root / "sub").rename(self.root / "sub-before")
        self.link(target, self.root / "sub", directory=True)

    def assert_blocked(self, result, moved):
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["restored"], [])
        self.assertEqual(result["conflicts"], [])
        self.assertEqual(result["errors"], [
            "restore target is outside workspace or cannot be resolved"])
        self.assertEqual(Path(moved["trash"]).read_text(), "synthetic original")

    def test_changed_parent_cannot_restore_outside_workspace(self):
        self.write("sub/file.txt")
        relocated = self.relocate("sub/file.txt")
        self.replace_parent(self.outside)
        result = self.engine.restore(relocated["txid"])
        self.assert_blocked(result, relocated["moved"][0])
        self.assertEqual(list(self.outside.iterdir()), [])
        tx = self.engine.transactions()[relocated["txid"]]
        self.assertEqual(tx["state"], "RESTORABLE")
        self.assertEqual(tx["last_restore"]["errors"], result["errors"])

    def test_changed_ancestor_does_not_create_external_parent_directories(self):
        self.write("sub/missing/deep/file.txt")
        relocated = self.relocate("sub/missing/deep/file.txt")
        self.replace_parent(self.outside)
        result = self.engine.restore(relocated["txid"])
        self.assert_blocked(result, relocated["moved"][0])
        self.assertFalse((self.outside / "missing").exists())

    def test_force_cannot_remove_an_external_occupant(self):
        self.write("sub/file.txt")
        relocated = self.relocate("sub/file.txt")
        external = self.outside / "file.txt"
        external.write_text("synthetic current", encoding="utf-8")
        self.replace_parent(self.outside)
        result = self.engine.restore(relocated["txid"], force=True)
        self.assert_blocked(result, relocated["moved"][0])
        self.assertEqual(external.read_text(), "synthetic current")

    def test_unsafe_later_target_prevents_the_whole_batch_from_moving(self):
        first = self.write("first.txt")
        self.write("sub/file.txt")
        relocated = self.relocate("first.txt", "sub/file.txt")
        first.write_text("synthetic current", encoding="utf-8")
        self.replace_parent(self.outside)
        with mock.patch.object(self.engine, "_move") as move:
            result = self.engine.restore(relocated["txid"], force=True)
        move.assert_not_called()
        self.assert_blocked(result, relocated["moved"][0])
        self.assertEqual(first.read_text(), "synthetic current")
        self.assertEqual(Path(relocated["moved"][1]["trash"]).read_text(),
                         "synthetic original")

    def test_blocked_transaction_can_be_restored_after_parent_is_repaired(self):
        original = self.write("sub/file.txt")
        relocated = self.relocate("sub/file.txt")
        self.replace_parent(self.outside)
        self.assert_blocked(self.engine.restore(relocated["txid"]),
                            relocated["moved"][0])
        (self.root / "sub").rename(self.root / "sub-link-before")
        (self.root / "sub-before").rename(self.root / "sub")
        result = self.engine.restore(relocated["txid"])
        self.assertTrue(result["ok"], result)
        self.assertEqual(original.read_text(), "synthetic original")
        self.assertEqual(self.engine.transactions()[relocated["txid"]]["state"],
                         "RESTORED")

    def test_missing_in_workspace_parents_are_recreated(self):
        original = self.write("sub/deep/file.txt")
        relocated = self.relocate("sub/deep/file.txt")
        (self.root / "sub").rename(self.root / "sub-before")
        result = self.engine.restore(relocated["txid"])
        self.assertTrue(result["ok"], result)
        self.assertEqual(original.read_text(), "synthetic original")

    def test_parent_alias_staying_inside_workspace_is_supported(self):
        original = self.write("sub/file.txt")
        relocated = self.relocate("sub/file.txt")
        self.replace_parent(self.root / "sub-before")
        result = self.engine.restore(relocated["txid"])
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["restored"], [str(original)])
        self.assertEqual(original.read_text(), "synthetic original")

    def test_restoring_a_symlink_does_not_follow_its_external_target(self):
        for missing in (False, True):
            with self.subTest(dangling=missing):
                target = self.outside / ("missing" if missing else "existing")
                if not missing:
                    target.write_text("synthetic external", encoding="utf-8")
                original = self.root / ("dangling-link" if missing else "link")
                self.link(target, original)
                relocated = self.relocate(original.name)
                result = self.engine.restore(relocated["txid"])
                self.assertTrue(result["ok"], result)
                self.assertTrue(original.is_symlink())
                self.assertEqual(os.readlink(original).replace("\\\\?\\", ""),
                                 str(target))
                if not missing:
                    self.assertEqual(target.read_text(), "synthetic external")
                else:
                    self.assertFalse(target.exists())

    def test_force_replaces_the_leaf_link_without_touching_its_target(self):
        original = self.write("file.txt")
        relocated = self.relocate("file.txt")
        target = self.outside / "target.txt"
        target.write_text("synthetic external", encoding="utf-8")
        self.link(target, original)
        result = self.engine.restore(relocated["txid"], force=True)
        self.assertTrue(result["ok"], result)
        self.assertFalse(original.is_symlink())
        self.assertEqual(original.read_text(), "synthetic original")
        self.assertEqual(target.read_text(), "synthetic external")

    def test_another_workspace_cannot_restore_from_a_shared_trash(self):
        original = self.write("file.txt")
        relocated = self.relocate("file.txt")
        other = self.outside / "other-workspace"
        other.mkdir()
        engine = RecoveryEngine(str(other), self.engine.trash_root)
        result = engine.restore(relocated["txid"])
        self.assert_blocked(result, relocated["moved"][0])
        self.assertFalse(original.exists())
        self.assertEqual(list(other.iterdir()), [])

    def test_invalid_or_outside_recorded_targets_leave_the_copy_intact(self):
        self.write("file.txt")
        relocated = self.relocate("file.txt")
        txid = relocated["txid"]
        transaction = self.engine.transactions()[txid]
        invalid = [str(self.root), str(self.outside / "file.txt"),
                   str(self.base / "workspace-sibling" / "file.txt"),
                   "relative.txt", str(self.root / "sub" / ".." / "file.txt")]
        for origin in invalid:
            with self.subTest(origin=origin):
                changed = {**transaction, "items": [
                    {**transaction["items"][0], "origin_path": origin}]}
                with mock.patch.object(self.engine, "transactions",
                                       return_value={txid: changed}):
                    result = self.engine.restore(txid, force=True)
                self.assert_blocked(result, relocated["moved"][0])
        self.assertTrue(self.root.is_dir())
        self.assertEqual(list(self.outside.iterdir()), [])

    def test_resolution_failure_preserves_the_copy_and_current_version(self):
        current = self.write("file.txt")
        relocated = self.relocate("file.txt")
        current.write_text("synthetic current", encoding="utf-8")
        with mock.patch("core.recovery._physical_keep_final",
                        side_effect=OSError("synthetic resolver failure")):
            result = self.engine.restore(relocated["txid"], force=True)
        self.assert_blocked(result, relocated["moved"][0])
        self.assertEqual(current.read_text(), "synthetic current")

    def test_target_is_rechecked_after_an_earlier_restore(self):
        first = self.write("first.txt")
        self.write("sub/file.txt")
        relocated = self.relocate("first.txt", "sub/file.txt")
        real_move = self.engine._move

        def move_and_change_parent(source, destination):
            real_move(source, destination)
            self.replace_parent(self.outside)

        with mock.patch.object(self.engine, "_move",
                               side_effect=move_and_change_parent) as move:
            result = self.engine.restore(relocated["txid"])
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["restored"], [str(first)])
        self.assertTrue(result["errors"])
        self.assertEqual(move.call_count, 1)
        self.assertEqual(first.read_text(), "synthetic original")
        self.assertEqual(Path(relocated["moved"][1]["trash"]).read_text(),
                         "synthetic original")
        self.assertEqual(list(self.outside.iterdir()), [])

    def test_restore_cli_reports_boundary_failure_and_keeps_evidence(self):
        self.write("sub/file.txt")
        relocated = self.relocate("sub/file.txt")
        self.replace_parent(self.outside)
        env = dict(os.environ, AGENT_GUARD_WORKSPACE=str(self.root),
                   AGENT_GUARD_TRASH=self.engine.trash_root)
        proc = subprocess.run(
            [sys.executable, str(RESTORE_CLI), relocated["txid"], "--json"],
            cwd=self.root, env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assert_blocked(json.loads(proc.stdout), relocated["moved"][0])
        self.assertEqual(list(self.outside.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
