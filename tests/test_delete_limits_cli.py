"""Admission enforced by real CLI entrypoints, not just the accounting API."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

from core import admission
from core.cleanup import plan_cleanup, select_entries
from core.recovery import RecoveryEngine
from tests.helpers import RepoFixture, git_available


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/delete-guard/scripts"


@unittest.skipUnless(git_available(), "git required")
class DeletionEntrypointTests(RepoFixture):
    def setUp(self):
        super().setUp()
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith("AGENT_GUARD_")}
        self.env.update(AGENT_GUARD_WORKSPACE=self.root, PYTHONDONTWRITEBYTECODE="1")

    def configure(self, **values):
        self.write(".agent-guard/delete-policy.json", json.dumps(values))

    def run_cli(self, name, *args, cwd=None):
        result = subprocess.run([sys.executable, "-B", str(SCRIPTS / name), *args],
                                cwd=cwd or self.root, env=self.env, capture_output=True,
                                text=True, timeout=30)
        self.assertTrue(result.stdout, result.stderr)
        return result.returncode, json.loads(result.stdout)

    def delete(self, *paths):
        return self.run_cli("safe_delete.py", "--json", "--", *paths)

    def check(self, command, *, cwd=None):
        return self.run_cli("check.py", "--cwd", self.root, "--enforce", "--json", "--", command, cwd=cwd)

    def assert_block(self, result, code):
        status, body = result
        self.assertEqual(status, 2, body)
        verdict = body.get("verdict", body)
        self.assertEqual((verdict["decision"], verdict["code"]), ("BLOCK", code), body)

    def test_safe_delete_cumulative_budget_retains_second_source_and_one_transaction(self):
        self.configure(session_max_bytes=7)
        self.write("first", "1234")
        self.write("second", "5678")
        status, first = self.delete("first")
        self.assertEqual(status, 0, first)
        self.assert_block(self.delete("second"), "BLOCK_DELETE_LIMIT")
        self.assertEqual(Path(self.root, "second").read_text(), "5678")
        transactions = RecoveryEngine(self.root).transactions()
        self.assertEqual(set(transactions), {first["txid"]})

    def test_host_check_charges_shared_budget_and_rejects_before_next_compensation(self):
        self.configure(session_max_entries=1)
        self.write("first", "1")
        self.write("second", "2")
        self.assertEqual(self.check("rm first")[0], 0)
        self.assert_block(self.check("rm second"), "BLOCK_DELETE_LIMIT")
        self.assertEqual(Path(self.root, "second").read_text(), "2")
        self.assertEqual(len(RecoveryEngine(self.root).transactions()), 1)

    def test_compound_shell_batch_is_aggregated_before_any_target_moves(self):
        self.configure(batch_max_bytes=3)
        self.write("first", "12")
        self.write("second", "34")
        self.assert_block(self.check("rm first; rm second"), "BLOCK_DELETE_BATCH_CONFIRM")
        self.assertEqual(Path(self.root, "first").read_text(), "12")
        self.assertEqual(Path(self.root, "second").read_text(), "34")
        self.assertEqual(RecoveryEngine(self.root).transactions(), {})

    def test_one_large_rm_batch_requires_cleanup_confirmation(self):
        self.configure(batch_max_bytes=3)
        self.write("first", "12")
        self.write("second", "34")
        self.assert_block(self.delete("first", "second"), "BLOCK_DELETE_BATCH_CONFIRM")
        self.assertEqual(RecoveryEngine(self.root).transactions(), {})

    def test_git_clean_preflight_resolves_paths_against_requested_cwd(self):
        self.configure(batch_max_bytes=3)
        self.write("first", "12")
        self.write("second", "34")
        self.assert_block(self.check("git clean -f -- first second", cwd=str(ROOT)),
                          "BLOCK_DELETE_BATCH_CONFIRM")
        self.assertTrue(Path(self.root, "first").exists())
        self.assertTrue(Path(self.root, "second").exists())
        self.assertEqual(RecoveryEngine(self.root).transactions(), {})

    def test_git_clean_cannot_relocate_parent_of_configured_protected_path(self):
        self.configure(protected_paths=["build/evidence"])
        self.write("build/evidence", "keep")
        self.write("build/other", "also keep")
        self.assert_block(self.check("git clean -fd -- build"), "BLOCK_DELETE_PROTECTED")
        self.assertEqual(Path(self.root, "build/evidence").read_text(), "keep")
        self.assertTrue(Path(self.root, "build/other").exists())
        self.assertEqual(RecoveryEngine(self.root).transactions(), {})

    def test_regenerable_physical_delete_obeys_recursive_entry_budget(self):
        self.configure(session_max_entries=1)
        self.write("node_modules/package", "generated")
        self.assert_block(self.delete("node_modules"), "BLOCK_DELETE_LIMIT")
        self.assertEqual(Path(self.root, "node_modules/package").read_text(), "generated")

    def test_host_regenerable_authorization_obeys_same_budget(self):
        self.configure(session_max_entries=1)
        self.write("node_modules/package", "generated")
        self.assert_block(self.check("rm -rf node_modules"), "BLOCK_DELETE_LIMIT")
        self.assertTrue(Path(self.root, "node_modules/package").exists())

    def test_force_restore_budget_refusal_preserves_occupant_and_old_copy(self):
        self.configure(session_max_entries=1)
        self.write("valuable", "old")
        status, moved = self.delete("valuable")
        self.assertEqual(status, 0, moved)
        self.write("valuable", "current")
        engine = RecoveryEngine(self.root)
        result = engine.restore(moved["txid"], force=True)
        self.assertFalse(result["ok"], result)
        self.assertIn("BLOCK_DELETE_LIMIT", str(result))
        self.assertEqual(Path(self.root, "valuable").read_text(), "current")
        self.assertEqual(Path(moved["moved"][0]["trash"]).read_text(), "old")

    def test_normal_restore_and_explicit_gc_remain_available_after_budget_exhaustion(self):
        self.configure(session_max_entries=1)
        self.write("valuable", "original")
        status, moved = self.delete("valuable")
        self.assertEqual(status, 0, moved)
        engine = RecoveryEngine(self.root)
        self.assertTrue(engine.restore(moved["txid"])["ok"])
        self.assertEqual(Path(self.root, "valuable").read_text(), "original")
        status, purged = self.run_cli("gc.py", "--json", "--execute", "--txid", moved["txid"])
        self.assertEqual(status, 0, purged)
        self.assertEqual(Path(self.root, "valuable").read_text(), "original")

    def test_dry_run_does_not_spend_session_budget(self):
        self.configure(session_max_entries=1)
        self.write("valuable", "original")
        status, result = self.run_cli("safe_delete.py", "--json", "--dry-run", "valuable")
        self.assertEqual(status, 0, result)
        self.assertFalse(Path(self.root, ".agent-trash/admission.json").exists())
        self.assertEqual(self.delete("valuable")[0], 0)

    def test_existing_config_limit_change_invalidates_reviewed_cleanup_plan(self):
        self.configure(batch_max_bytes=3)
        self.write("old", "discarded")
        first = plan_cleanup(self.root, discard=["old"])
        self.configure(batch_max_bytes=300)
        current = plan_cleanup(self.root, discard=["old"])
        self.assertNotEqual(first["plan_id"], current["plan_id"])
        with self.assertRaisesRegex(ValueError, "plan changed"):
            select_entries(current, ["old"], first["plan_id"])

    def test_cleanup_confirmation_can_pass_only_batch_threshold(self):
        self.configure(batch_max_bytes=1)
        self.write("old", "valuable bytes")
        plan = plan_cleanup(self.root, discard=["old"])
        spec = importlib.util.spec_from_file_location("cleanup_limit_test", SCRIPTS / "cleanup.py")
        module = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(SCRIPTS))
        try:
            spec.loader.exec_module(module)
        finally:
            sys.path.remove(str(SCRIPTS))
        selected = select_entries(plan, ["old"], plan["plan_id"])
        result = module.execute_selection(plan, selected)
        self.assertEqual(result["exit"], 0, result)
        self.assertFalse(Path(self.root, "old").exists())
        self.assertEqual(len(RecoveryEngine(self.root).transactions()), 1)

    @unittest.skipUnless(os.name == "posix", "POSIX terminal required")
    def test_real_terminal_cleanup_confirmation_passes_large_operation_gate(self):
        import pty
        import select
        import time

        self.configure(batch_max_bytes=1)
        self.write("node_modules/package", "generated")
        self.assert_block(self.delete("node_modules"), "BLOCK_DELETE_BATCH_CONFIRM")
        plan = plan_cleanup(self.root)
        master, slave = pty.openpty()
        child = subprocess.Popen(
            [sys.executable, "-B", str(SCRIPTS / "cleanup.py"), "--root", self.root,
             "--json", "--execute", "--plan-id", plan["plan_id"], "--select", "node_modules"],
            cwd=self.root, env=self.env, stdin=slave, stderr=slave,
            stdout=subprocess.PIPE, text=True)
        os.close(slave)
        prompt = b""
        try:
            deadline = time.monotonic() + 20
            while b"to execute this selection: " not in prompt and time.monotonic() < deadline:
                ready, _, _ = select.select([master], [], [], 0.2)
                if ready:
                    prompt += os.read(master, 65536)
                if child.poll() is not None:
                    break
            self.assertIn(b"to execute this selection: ", prompt)
            os.write(master, b"EXECUTE\n")
            output, _ = child.communicate(timeout=30)
            self.assertEqual(child.returncode, 0, output)
            self.assertEqual(json.loads(output)["outcome"], "executed")
            self.assertFalse(Path(self.root, "node_modules").exists())
        finally:
            os.close(master)
            if child.poll() is None:
                child.terminate()
                child.communicate(timeout=10)

    def test_recreate_delete_loop_stops_with_repeated_target_code(self):
        self.configure(repeat_max_operations=2)
        for _ in range(2):
            self.write("recreated", "version")
            status, body = self.delete("recreated")
            self.assertEqual(status, 0, body)
        self.write("recreated", "keep current")
        self.assert_block(self.delete("recreated"), "BLOCK_DELETE_REPEAT")
        self.assertEqual(Path(self.root, "recreated").read_text(), "keep current")
        self.assertEqual(len(RecoveryEngine(self.root).transactions()), 2)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO required")
    def test_fifo_temporary_budget_file_refuses_without_hanging_or_deleting(self):
        self.write("node_modules/package", "generated")
        trash = Path(self.root, ".agent-trash")
        trash.mkdir()
        os.mkfifo(str(trash / "admission.json.tmp"))
        self.assert_block(self.delete("node_modules"), "BLOCK_DELETE_STATE_INVALID")
        self.assertTrue(Path(self.root, "node_modules/package").exists())


if __name__ == "__main__":
    unittest.main()
