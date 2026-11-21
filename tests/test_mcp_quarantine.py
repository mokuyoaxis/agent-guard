"""Trusted MCP storage binding, using owned fixtures and actual CLIs/stdio.

Task approval below is explicit unit-test injection, not human acceptance.
"""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from adapters.mcp import doctor as mcp_doctor
from adapters.mcp.operations import InvalidArguments, Operations
from core.cleanup_saved import load_saved_plan
from core.cleanup_tasks import approve_task
from tests.helpers import RepoFixture, git_available
from tests.test_mcp_protocol import Connection, MCP_PYTHON, ROOT, SDK_AVAILABLE, SERVER


@unittest.skipUnless(git_available(), "git required")
class ExternalQuarantineTests(RepoFixture):
    def setUp(self):
        environment = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_GUARD_")}
        patch = mock.patch.dict(os.environ, environment, clear=True)
        patch.start()
        self.addCleanup(patch.stop)
        super().setUp()
        self.root = str(Path(self.root).resolve())
        temporary = tempfile.TemporaryDirectory(prefix="owned external bucket ")
        self.addCleanup(temporary.cleanup)
        self.parent = Path(temporary.name).resolve()
        self.bucket = self.parent / "quarantine with spaces"
        self.operations = Operations(self.root, trash_root=self.bucket)

    def delete(self, *paths, **extra):
        return self.operations.call("safe_delete", {"paths": list(paths), **extra})

    def configure(self, **values):
        self.write(".agent-guard/delete-policy.json", json.dumps(values))

    def approve_unit_fixture(self, saved_id):
        # This deliberately replaces the operator only within the unit fixture.
        with mock.patch.dict(os.environ, self.operations._env, clear=True):
            return approve_task(load_saved_plan(self.root, saved_id))

    def test_startup_and_readonly_queries_do_not_create_storage(self):
        self.assertEqual(self.operations.trash_root, str(self.bucket))
        self.assertFalse(self.bucket.exists())
        for name, args in (("status", {}), ("status", {"view": "overview"}),
                           ("transactions", {}), ("gc_plan", {})):
            with self.subTest(tool=name, args=args):
                result = self.operations.call(name, args)
                self.assertTrue(result["ok"], result)
                if name == "status":
                    self.assertEqual(result["result"]["trash_root"], str(self.bucket))
        self.assertFalse(self.bucket.exists())
        self.assertFalse(Path(self.root, ".agent-trash").exists())

    def test_invalid_locations_fail_without_fallback_or_creation(self):
        file = self.parent / "ordinary-file"
        file.write_text("preserve")
        for path in ("relative", "", "/", self.root, str(Path(self.root).parent), str(file), str(file / "child"), "bad\0path", "~agent-guard-nonexistent-fixture-user/bucket"):
            with self.subTest(path=path), self.assertRaises((ValueError, OSError)):
                Operations(self.root, trash_root=path)
        self.assertEqual(file.read_text(), "preserve")
        self.assertFalse(self.bucket.exists())
        self.assertFalse(Path(self.root, ".agent-trash").exists())

    def test_explicit_aliases_canonicalize_and_ambient_override_is_ignored(self):
        self.bucket.mkdir()
        alias = self.parent / "bucket-alias"
        alias.symlink_to(self.bucket, target_is_directory=True)
        linked = Operations(self.root, trash_root=alias)
        self.assertEqual(linked.trash_root, str(self.bucket))
        with mock.patch.dict(os.environ, {"AGENT_GUARD_TRASH": str(self.parent / "ignored")}):
            default = Operations(self.root)
            explicit = Operations(self.root, trash_root=alias)
        self.assertEqual(default.trash_root, str(Path(self.root) / ".agent-trash"))
        self.assertEqual(explicit._env["AGENT_GUARD_TRASH"], str(self.bucket))

    def test_reconnect_restore_and_protection_use_the_selected_ledger(self):
        self.configure(session_max_entries=1)
        self.write("first.txt", "original")
        self.write("second.txt", "preserve")
        legacy = Path(self.write(".agent-trash/audit.jsonl", "retained legacy ledger\n"))
        deleted = self.delete("first.txt")
        self.assertTrue(deleted["ok"], deleted)
        txid = deleted["result"]["txid"]
        self.assertEqual(legacy.read_text(), "retained legacy ledger\n")
        payload = Path(deleted["result"]["moved"][0]["trash"])
        self.assertTrue(payload.is_relative_to(self.bucket))
        alias = Path(self.root) / "bucket-alias"
        alias.symlink_to(self.bucket, target_is_directory=True)
        for path in (str(payload), str(self.bucket), "bucket-alias", ".agent-trash", ".agent-trash/audit.jsonl"):
            refused = self.delete(path)
            self.assertFalse(refused["ok"], refused)
            self.assertEqual(refused["error"]["code"], "MCP_QUARANTINE_PROTECTED")
        repeated = Operations(self.root, trash_root=self.bucket)
        denied = repeated.call("safe_delete", {"paths": ["second.txt"]})
        self.assertFalse(denied["ok"], denied)
        self.assertEqual(denied["result"]["verdict"]["code"], "BLOCK_DELETE_LIMIT")
        restored = repeated.call("restore", {"txid": txid})
        self.assertTrue(restored["ok"], restored)
        self.assertEqual(Path(self.root, "first.txt").read_text(), "original")
        status = repeated.call("status", {})["result"]
        self.assertEqual(status["delete_admission"]["used_entries"], 1)
        self.assertEqual(legacy.read_text(), "retained legacy ledger\n")

    def test_tool_arguments_cannot_rebind_storage(self):
        cases = {"safe_delete": {"paths": ["owned.txt"]}, "restore": {"txid": "owned"},
                 "status": {}, "transactions": {}, "gc_plan": {},
                 "cleanup_discover": {}, "cleanup_measure": {"scopes": ["src"]},
                 "cleanup_plan": {"targets": ["owned.txt"]},
                 "cleanup_execute": {"task_id": "a" * 32}, "cleanup_task_status": {"task_id": "a" * 32}}
        for name, args in cases.items():
            with self.subTest(tool=name), self.assertRaises(InvalidArguments):
                self.operations.call(name, {**args, "trash_root": str(self.parent / "other")})
        self.assertFalse(self.bucket.exists())

    def test_external_capacity_refusal_keeps_source_and_never_falls_back(self):
        self.configure(quarantine_max_bytes=0)
        self.write("old.txt", "preserve")
        result = self.delete("old.txt")
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["result"]["verdict"]["code"], "BLOCK_DELETE_LIMIT")
        self.assertEqual(Path(self.root, "old.txt").read_text(), "preserve")
        self.assertFalse(Path(self.root, ".agent-trash").exists())

    def test_default_bucket_symlink_checks_physical_capacity(self):
        self.bucket.mkdir()
        for index in range(16):
            (self.bucket / f"retained-{index}.bin").write_bytes(b"x" * 8192)
        legacy = Path(self.root, ".agent-trash")
        legacy.symlink_to(self.bucket, target_is_directory=True)
        default = Operations(self.root)
        self.assertEqual(default.trash_root, str(legacy))
        self.write("old.txt", "preserve")
        for limits in ({"quarantine_max_bytes": 64 * 1024}, {"quarantine_max_entries": 8}):
            with self.subTest(limits=limits):
                self.configure(**limits)
                preview = default.call("safe_delete", {"paths": ["old.txt"], "dry_run": True})
                self.assertEqual(preview["result"]["admission"]["code"], "BLOCK_DELETE_LIMIT")
                denied = default.call("safe_delete", {"paths": ["old.txt"]})
                self.assertFalse(denied["ok"], denied)
                self.assertEqual(denied["result"]["verdict"]["code"], "BLOCK_DELETE_LIMIT")
                self.assertEqual(Path(self.root, "old.txt").read_text(), "preserve")
        self.assertTrue(legacy.is_symlink())
        self.assertFalse((self.bucket / "admission.json").exists())
        for index in range(16):
            self.assertEqual((self.bucket / f"retained-{index}.bin").read_bytes(), b"x" * 8192)

    def test_saved_operator_command_retains_storage_before_confirmation(self):
        self.write("old.txt", "owned")
        result = self.operations.call("cleanup_plan", {"targets": ["old.txt"], "discard_targets": True, "save": True})
        self.assertTrue(result["ok"], result)
        plan = result["result"]
        command = plan["next_step"]["argv"]
        self.assertEqual(command[:3], ["env", "AGENT_GUARD_WORKSPACE=" + self.root,
                                      "AGENT_GUARD_TRASH=" + str(self.bucket)])
        proc = subprocess.run([*command, "--json"], cwd=self.parent, env=dict(os.environ),
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("interactive terminal", proc.stdout)
        self.assertNotIn("saved plan changed", proc.stdout)
        self.assertFalse(self.bucket.exists())

    def test_task_query_and_execution_keep_binding_and_reject_other_bucket(self):
        self.write("old.txt", "owned")
        result = self.operations.call("cleanup_plan", {"targets": ["old.txt"], "discard_targets": True, "save": True})
        task = self.approve_unit_fixture(result["result"]["saved_plan_id"])
        queried = self.operations.call("cleanup_task_status", {"task_id": task["task_id"]})
        self.assertTrue(queried["ok"], queried)
        hint = queried["result"]["next_step"]
        self.assertEqual(hint["status_argv"][:3], ["env", "AGENT_GUARD_WORKSPACE=" + self.root,
                                                 "AGENT_GUARD_TRASH=" + str(self.bucket)])
        wrong = Operations(self.root, trash_root=self.parent / "other")
        refused = wrong.call("cleanup_execute", {"task_id": task["task_id"]})
        self.assertFalse(refused["ok"], refused)
        self.assertTrue(Path(self.root, "old.txt").exists())
        self.assertFalse((self.parent / "other").exists())
        executed = self.operations.call("cleanup_execute", {"task_id": task["task_id"]})
        self.assertTrue(executed["ok"], executed)
        self.assertEqual(executed["result"]["status"], "COMPLETED")
        self.assertEqual(executed["result"]["counts"]["confirmed"], 1)
        query = subprocess.run(hint["status_argv"], cwd=self.parent, env=dict(os.environ),
                               stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
        self.assertEqual(query.returncode, 0, query.stderr)
        self.assertEqual(json.loads(query.stdout)["status"], "COMPLETED")

    def test_dryrun_and_gc_instructions_retain_custom_storage(self):
        self.write("old.txt", "owned")
        preview = self.delete("old.txt", dry_run=True)
        command = preview["result"]["admission"]["next_step"]["commands"][0]["argv"]
        self.assertEqual(command[2], "AGENT_GUARD_TRASH=" + str(self.bucket))
        deleted = self.delete("old.txt")
        self.assertTrue(deleted["ok"], deleted)
        commands = deleted["result"]["gc"]
        for name in ("plan_command", "purge_eligible_command", "purge_transaction_command"):
            argv = shlex.split(commands[name])
            self.assertEqual(argv[:3], ["env", "AGENT_GUARD_WORKSPACE=" + self.root,
                                       "AGENT_GUARD_TRASH=" + str(self.bucket)])
        txid = deleted["result"]["txid"]
        self.assertEqual(shlex.split(commands["purge_transaction_command"])[-2:], ["--txid", txid])
        # Only the read-only plan is invoked; never run the purge instructions.
        plan = subprocess.run([*shlex.split(commands["plan_command"]), "--json"], cwd=self.parent,
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(plan.returncode, 0, plan.stderr)
        self.assertEqual(json.loads(plan.stdout)["selected_txids"], [txid])
        self.assertFalse(Path(self.root, ".agent-trash").exists())

    def test_invalid_server_storage_is_rejected_before_optional_sdk(self):
        proc = subprocess.run([sys.executable, "-B", "-S", str(SERVER), "--workspace", self.root,
                               "--trash-root", "relative"], capture_output=True, text=True, timeout=20)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("absolute", proc.stderr)
        self.assertNotIn("SDK", proc.stderr)
        self.assertFalse(self.bucket.exists())

    def test_doctor_invalid_storage_is_private_and_starts_no_child(self):
        with mock.patch.object(mcp_doctor.subprocess, "run") as run:
            result = mcp_doctor.diagnose(sys.executable, self.root, trash_root="PRIVATE-MARKER-relative")
        run.assert_not_called()
        self.assertEqual(result["code"], "MCP_DOCTOR_TRASH_ROOT")
        self.assertNotIn("PRIVATE-MARKER", json.dumps(result))


@unittest.skipUnless(SDK_AVAILABLE, "optional MCP SDK required")
class ExternalQuarantineProtocolTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="owned-mcp-storage-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.workspace = self.directory / "workspace"
        self.workspace.mkdir()
        self.bucket = self.directory / "external bucket"

    def connect(self):
        conn = Connection(self.workspace, trash_root=self.bucket)
        self.addCleanup(conn.close)
        return conn

    def content(self, value):
        return value["result"]["structuredContent"]

    def test_stdio_external_binding_reconnect_restore_and_budget(self):
        target = self.workspace / "owned.txt"
        target.write_bytes(b"original")
        conn = self.connect()
        status = self.content(conn.call("status", view="overview"))
        self.assertEqual(status["result"]["trash_root"], str(self.bucket))
        self.assertFalse(self.bucket.exists())
        deleted = self.content(conn.call("safe_delete", paths=["owned.txt"]))
        self.assertTrue(deleted["ok"], deleted)
        self.assertTrue((self.bucket / "manifest.jsonl").is_file())
        self.assertFalse((self.workspace / ".agent-trash").exists())
        conn.close()
        repeated = self.connect()
        restored = self.content(repeated.call("restore", txid=deleted["result"]["txid"]))
        self.assertTrue(restored["ok"], restored)
        self.assertEqual(target.read_bytes(), b"original")
        status = self.content(repeated.call("status"))["result"]
        self.assertEqual(status["delete_admission"]["used_entries"], 1)

    def test_doctor_uses_same_storage_without_creating_it(self):
        result = mcp_doctor.diagnose(MCP_PYTHON, self.workspace, trash_root=self.bucket)
        self.assertEqual(result["status"], "PASS", result)
        self.assertIn({"stage": "quarantine", "status": "PASS"}, result["checks"])
        self.assertNotIn(str(self.bucket), json.dumps(result))
        self.assertFalse(self.bucket.exists())
        self.assertFalse((self.workspace / ".agent-trash").exists())


if __name__ == "__main__":
    unittest.main()
