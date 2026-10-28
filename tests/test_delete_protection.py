"""Deletion configuration validation and shared protection boundaries."""
import json
import ntpath
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace

from core import classifier, delete_config, policy
from tests.helpers import RepoFixture, git_available


class DeleteConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = tempfile.TemporaryDirectory(prefix="agent-guard-policy-test-")
        self.addCleanup(self.fixture.cleanup)
        self.root = self.fixture.name
        self.path = Path(self.root, ".agent-guard", "delete-policy.json")

    def configure(self, value):
        self.path.parent.mkdir(exist_ok=True)
        self.path.write_text(json.dumps(value), encoding="utf-8")

    def protected(self, *paths):
        return delete_config.protected_reason(list(paths), self.root)

    def test_missing_configuration_is_read_only_and_uses_defaults(self):
        before = list(Path(self.root).iterdir())
        config = delete_config.load_config(self.root)
        self.assertEqual(config.quarantine_max_bytes, 5 * 1024 ** 3)
        self.assertEqual(config.session_max_entries, 10_000)
        self.assertEqual(config.rate_max_operations, 60)
        self.assertEqual(config.repeat_max_operations, 5)
        self.assertEqual(config.batch_max_bytes, 256 * 1024 ** 2)
        self.assertEqual(config.protected_paths, [])
        self.assertEqual(list(Path(self.root).iterdir()), before)

    def test_effective_overrides_and_normalized_protected_paths(self):
        self.configure({"session_max_bytes": 0, "rate_max_operations": 2,
                        "protected_paths": ["data/../evidence", "evidence",
                                            str(Path(self.root, "archive"))]})
        config = delete_config.load_config(self.root)
        self.assertEqual(config.session_max_bytes, 0)
        self.assertEqual(config.rate_max_operations, 2)
        self.assertEqual(config.protected_paths, ["archive", "evidence"])
        self.assertEqual(len(config.fingerprint()), 64)
        self.assertEqual(config.fingerprint(), delete_config.fingerprint(config))
        self.assertNotEqual(config.fingerprint(), delete_config.DeleteConfig().fingerprint())

    def test_numeric_types_bounds_unknown_fields_and_path_types_are_rejected(self):
        invalid = [[], None, {"unexpected": 1}, {"rate_max_operations": True},
                   {"rate_window_seconds": 0}, {"session_max_entries": -1},
                   {"quarantine_max_bytes": -1}, {"batch_max_entries": 1.0},
                   {"session_max_bytes": "1024"},
                   {"session_max_bytes": 1 << 63}, {"protected_paths": "data"},
                   {"protected_paths": [None]}, {"protected_paths": [""]},
                   {"protected_paths": ["unsafe\0path"]},
                   {"protected_paths": ["../outside"]},
                   {"protected_paths": [str(Path(self.root).parent / "outside")]}]
        for value in invalid:
            with self.subTest(value=value):
                self.configure(value)
                with self.assertRaises(delete_config.DeleteConfigError):
                    delete_config.load_config(self.root)

    def test_every_integer_field_rejects_boolean_and_quantity_zero(self):
        for name in delete_config.DeleteConfig.__dataclass_fields__:
            if name == "protected_paths":
                continue
            for value in (False, -1, 1 << 63):
                with self.subTest(name=name, value=value):
                    self.configure({name: value})
                    with self.assertRaises(delete_config.DeleteConfigError):
                        delete_config.load_config(self.root)
            self.configure({name: 0})
            if name.endswith("_bytes"):
                self.assertEqual(getattr(delete_config.load_config(self.root), name), 0)
            else:
                with self.assertRaises(delete_config.DeleteConfigError):
                    delete_config.load_config(self.root)

    def test_malformed_duplicate_non_utf8_and_large_configuration_are_rejected(self):
        self.path.parent.mkdir()
        samples = [b"{", b'{"rate_max_operations":1,"rate_max_operations":2}',
                   b"\xff", b" " * (delete_config.MAX_CONFIG_BYTES + 1)]
        for raw in samples:
            with self.subTest(size=len(raw)):
                self.path.write_bytes(raw)
                with self.assertRaises(delete_config.DeleteConfigError):
                    delete_config.load_config(self.root)

    def test_unreadable_configuration_does_not_use_defaults(self):
        with mock.patch("core.delete_config.os.open", side_effect=PermissionError("private path")):
            with self.assertRaisesRegex(delete_config.DeleteConfigError,
                                        "cannot read deletion configuration") as caught:
                delete_config.load_config(self.root)
        self.assertNotIn("private path", str(caught.exception))

    def test_broken_configuration_link_does_not_use_defaults(self):
        self.path.parent.mkdir()
        self.path.symlink_to(self.path.parent / "missing.json")
        with self.assertRaises(delete_config.DeleteConfigError):
            delete_config.load_config(self.root)

    def test_configuration_link_to_regular_file_retains_existing_support(self):
        self.path.parent.mkdir()
        target = self.path.parent / "operator-policy.json"
        target.write_text('{"rate_max_operations":7}', encoding="utf-8")
        self.path.symlink_to(target)
        self.assertEqual(delete_config.load_config(self.root).rate_max_operations, 7)

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "mkfifo"), "POSIX FIFO required")
    def test_fifo_configuration_is_refused_without_waiting_for_a_writer(self):
        self.path.parent.mkdir()
        os.mkfifo(self.path)
        self.assert_fifo_refused()

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "mkfifo"), "POSIX FIFO required")
    def test_configuration_link_to_fifo_is_refused_without_waiting_for_a_writer(self):
        self.path.parent.mkdir()
        pipe = self.path.parent / "policy-pipe"
        os.mkfifo(pipe)
        self.path.symlink_to(pipe)
        self.assert_fifo_refused()

    def assert_fifo_refused(self):
        code = (
            "import sys\n"
            "from core.delete_config import DeleteConfigError, load_config\n"
            "try:\n"
            "    load_config(sys.argv[1])\n"
            "except DeleteConfigError as exc:\n"
            "    print(str(exc))\n"
            "else:\n"
            "    raise SystemExit(3)\n"
        )
        result = subprocess.run([sys.executable, "-B", "-c", code, self.root],
                                cwd=Path(__file__).resolve().parent.parent,
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "deletion configuration must be a regular file")
        self.assertEqual(result.stderr, "")

    def test_configuration_directory_and_future_files_are_always_protected(self):
        for path in (".agent-guard", ".agent-guard/delete-policy.json",
                     ".agent-guard/future-note", "."):
            with self.subTest(path=path):
                self.assertIsNotNone(self.protected(path))
        self.assertIsNone(self.protected(".agent-guard-copy"))
        self.assertIsNone(self.protected("ordinary"))

    def test_declared_subtree_and_ancestor_are_protected_without_prefix_collision(self):
        self.configure({"protected_paths": ["data/evidence"]})
        for path in ("data", "data/evidence", "data/evidence/run.json"):
            with self.subTest(path=path):
                self.assertIsNotNone(self.protected(path))
        self.assertIsNone(self.protected("data/evidence-copy"))
        self.assertIsNone(self.protected("data/other"))

    def test_windows_path_comparison_uses_host_case_normalization(self):
        with mock.patch.object(delete_config, "os", SimpleNamespace(path=ntpath)):
            self.assertTrue(delete_config._inside(r"c:\workspace\DATA\proof", r"C:\Workspace\data"))
            self.assertFalse(delete_config._inside(r"C:\workspace\data-copy", r"C:\workspace\data"))

    def test_protection_covers_directory_alias_in_both_directions(self):
        Path(self.root, "actual").mkdir()
        Path(self.root, "alias").symlink_to("actual", target_is_directory=True)
        self.configure({"protected_paths": ["alias/evidence"]})
        self.assertIsNotNone(self.protected("actual"))
        self.assertIsNotNone(self.protected("actual/evidence"))
        self.assertIsNotNone(self.protected("alias/evidence"))
        self.assertIsNone(self.protected("actual/other"))
        self.configure({"protected_paths": ["actual/evidence"]})
        self.assertIsNotNone(self.protected("alias"))

    def test_config_directory_alias_and_its_physical_parent_are_protected(self):
        Path(self.root, "actual").mkdir()
        Path(self.root, ".agent-guard").symlink_to("actual", target_is_directory=True)
        self.assertIsNotNone(self.protected("actual"))
        self.assertIsNotNone(self.protected("actual/future"))

    def test_final_outward_link_can_be_protected_but_traversal_cannot_be_declared(self):
        with tempfile.TemporaryDirectory(prefix="agent-guard-policy-outside-") as outside:
            Path(self.root, "outside-link").symlink_to(outside, target_is_directory=True)
            self.configure({"protected_paths": ["outside-link"]})
            self.assertIsNotNone(self.protected("outside-link"))
            self.configure({"protected_paths": ["outside-link/foreign"]})
            with self.assertRaises(delete_config.DeleteConfigError):
                delete_config.load_config(self.root)

    def test_quarantine_admission_state_and_lock_are_control_paths(self):
        trash = str(Path(self.root, ".agent-trash"))
        controls = ("admission.json", "admission.json.tmp", "admission.lock")
        for name in controls:
            with self.subTest(name=name):
                spec = classifier.classify_paths(
                    [str(Path(trash, name))], self.root, self.root, trash)[0]
                self.assertEqual(spec.protected, "quarantine-control")
        payload = classifier.classify_paths(
            [str(Path(trash, "transaction", "payload"))], self.root, self.root, trash)[0]
        self.assertIsNone(payload.protected)


@unittest.skipUnless(git_available(), "git required")
class SharedDeletionPolicyTests(RepoFixture):
    def decide(self, *paths):
        trash = os.path.join(self.root, ".agent-trash")
        specs = classifier.classify_paths(list(paths), self.root, self.root, trash)
        return policy.decide_path_batch(
            specs, policy.PolicyContext(self.root, trash, self.root), recursive=True)

    def test_protected_configured_artifact_is_blocked_before_regenerable_exemption(self):
        self.write("node_modules/keep/evidence", "retain")
        self.write(".agent-guard/delete-policy.json",
                   json.dumps({"protected_paths": ["node_modules/keep"]}))
        verdict = self.decide("node_modules")
        self.assertEqual(verdict.code, policy.CODE_BLOCK_DELETE_PROTECTED)
        self.assertEqual(verdict.decision, policy.DECISION_BLOCK)
        self.assertTrue(Path(self.root, "node_modules/keep/evidence").exists())

    def test_invalid_configuration_blocks_named_deletion_and_housekeeping(self):
        self.write("ordinary", "retain")
        self.write(".agent-trash/transaction/payload", "retain")
        self.write(".agent-guard/delete-policy.json", "{invalid}")
        for path in ("ordinary", ".agent-trash/transaction/payload"):
            with self.subTest(path=path):
                verdict = self.decide(path)
                self.assertEqual(verdict.code, policy.CODE_BLOCK_DELETE_CONFIG_INVALID)
                self.assertTrue(Path(self.root, path).exists())

    def test_default_configuration_preserves_ordinary_and_regenerable_decisions(self):
        self.write("ordinary", "retain")
        self.write("node_modules/generated", "regenerable")
        self.assertEqual(self.decide("ordinary").code, policy.CODE_RELOCATE_PATHS)
        self.assertEqual(self.decide("node_modules").code, policy.CODE_ALLOW_REGENERABLE)


if __name__ == "__main__":
    unittest.main()
