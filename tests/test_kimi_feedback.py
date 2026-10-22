"""Kimi Stop feedback consumes recovery audit metadata without changing policy."""
import json
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from core import audit, classifier
from core.recovery import RecoveryEngine
from tests.helpers import RepoFixture, git_available


ROOT = Path(__file__).resolve().parents[1]
STOP = ROOT / "adapters/kimi-code/stop_feedback.py"
PRE = ROOT / "adapters/kimi-code/pre_tool_use.py"
BRIDGE = ROOT / "adapters/kimi-code/hook_bridge.sh"
RESTORE = ROOT / "skills/delete-guard/scripts/restore.py"


@unittest.skipUnless(os.name == "posix" and git_available(), "POSIX and Git required")
class KimiFeedback(RepoFixture):
    def setUp(self):
        super().setUp()
        self.home = tempfile.TemporaryDirectory(prefix="guard-feedback-home-")
        self.addCleanup(self.home.cleanup)
        self.env = {**os.environ, "AGENT_GUARD_WORKSPACE": self.root,
                    "KIMI_CODE_HOME": self.home.name}
        self.env.pop("AGENT_GUARD_TRASH", None)
        self.env.pop("AGENT_GUARD_DEBUG", None)

    def run_hook(self, script, payload, *, env=None):
        return subprocess.run([sys.executable, str(script)], input=json.dumps(payload),
                              cwd=payload["cwd"], env=env or self.env,
                              capture_output=True, text=True, timeout=30)

    def preserve(self, command, session="test-session", *, env=None):
        return self.run_hook(PRE, {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                                  "tool_input": {"command": command}, "cwd": self.root,
                                  "session_id": session, "tool_call_id": "test-call"}, env=env)

    def stop(self, session="test-session", *, active=False, env=None):
        return self.run_hook(STOP, {"hook_event_name": "Stop", "cwd": self.root,
                                   "session_id": session, "stop_hook_active": active}, env=env)

    def txids(self, trash=None):
        path = Path(trash or Path(self.root) / ".agent-trash") / "manifest.jsonl"
        return [row["txid"] for row in map(json.loads, path.read_text().splitlines())
                if row["type"] in {"tx-start", "snapshot"}]

    def restore(self, txid):
        result = subprocess.run([sys.executable, str(RESTORE), txid, "--json"],
                                cwd=self.root, env=self.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_no_audit_is_silent_and_creates_no_cursor(self):
        result = self.stop()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout + result.stderr, "")
        self.assertEqual(list(Path(self.home.name).iterdir()), [])
        self.assertFalse(Path(self.root, ".agent-trash").exists())

    def test_relocation_feedback_has_real_txid_space_gc_and_no_target_text(self):
        target = "private_target_do_not_echo.txt"
        self.write(target, "restore exact bytes")
        self.assertEqual(self.preserve("rm -f " + target).returncode, 0)
        txid, = self.txids()
        manifest = Path(self.root, ".agent-trash/manifest.jsonl")
        before = manifest.read_bytes()
        result = self.stop()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn(txid, result.stderr)
        self.assertIn("moved=1", result.stderr)
        self.assertIn("still use disk space", result.stderr)
        command = next(line.strip().removeprefix("GC permanent purge after preview: ")
                       for line in result.stderr.splitlines() if "GC permanent purge after preview: " in line)
        self.assertEqual(shlex.split(command), [sys.executable,
                         str(ROOT / "skills/delete-guard/scripts/gc.py"), "--execute", "--txid", txid])
        self.assertNotIn("<txid>", result.stderr)
        self.assertIn("does not free total disk space", result.stderr)
        self.assertIn("Inspect current recovery state", result.stderr)
        self.assertNotIn(target, result.stderr)
        self.assertNotIn("rm -f", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(manifest.read_bytes(), before)
        self.restore(txid)
        self.assertEqual(Path(self.root, target).read_text(), "restore exact bytes")

    def test_repeated_stop_sends_once_and_next_transaction_still_arrives(self):
        self.write("first.txt")
        self.assertEqual(self.preserve("rm first.txt").returncode, 0)
        first, = self.txids()
        self.assertEqual(self.stop().returncode, 2)
        repeated = self.stop()
        self.assertEqual((repeated.returncode, repeated.stdout + repeated.stderr), (0, ""))
        self.write("second.txt")
        self.assertEqual(self.preserve("rm second.txt").returncode, 0)
        second = self.txids()[-1]
        result = self.stop()
        self.assertIn(second, result.stderr)
        self.assertNotIn(first, result.stderr)

    def test_active_stop_does_not_consume_pending_feedback(self):
        self.write("file.txt")
        self.assertEqual(self.preserve("rm file.txt").returncode, 0)
        active = self.stop(active=True)
        self.assertEqual((active.returncode, active.stderr), (0, ""))
        self.assertEqual(self.stop().returncode, 2)

    def test_concurrent_stop_emits_one_notice(self):
        self.write("file.txt")
        self.assertEqual(self.preserve("rm file.txt").returncode, 0)
        txid, = self.txids()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: self.stop(), range(2)))
        self.assertEqual(sorted(result.returncode for result in results), [0, 2])
        self.assertEqual(sum(txid in result.stderr for result in results), 1)

    def test_all_transactions_and_session_separation(self):
        for filename in ["first.txt", "second.txt"]:
            self.write(filename)
            self.assertEqual(self.preserve("rm " + filename, session="owner").returncode, 0)
        other = self.stop(session="other")
        self.assertEqual((other.returncode, other.stderr), (0, ""))
        result = self.stop(session="owner")
        for txid in self.txids():
            self.assertIn(txid, result.stderr)
            self.assertIn("--execute --txid " + txid, result.stderr)
        self.assertEqual(self.stop(session="owner").returncode, 0)

    def test_mixed_records_offer_gc_only_for_relocated_files(self):
        self.write("file.txt", "relocation bytes")
        self.assertEqual(self.preserve("rm file.txt").returncode, 0)
        relocation, = self.txids()
        self.write("src/main.py", "unsaved tracked bytes\n")
        self.assertEqual(self.preserve("git restore src/main.py").returncode, 0)
        snapshot = self.txids()[-1]
        result = self.stop()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("--execute --txid " + relocation, result.stderr)
        self.assertNotIn("--execute --txid " + snapshot, result.stderr)
        self.assertIn("Git snapshot: txid=" + snapshot, result.stderr)
        self.assertIn("GC does not remove Git stash", result.stderr)

    def test_failed_compensation_without_moves_does_not_claim_retained_file_space(self):
        self.write("good.txt", "unmoved bytes")
        engine = RecoveryEngine(self.root)
        check_id = audit.new_txid()
        specs = classifier.classify_paths(["good.txt"], self.root, self.root, engine.trash_root)
        with mock.patch.object(engine, "_move", side_effect=PermissionError("test move refused")):
            with self.assertRaises(PermissionError):
                engine.relocate(specs, meta={"check_id": check_id})
        audit.append({"event": "enforce-error", "check_id": check_id,
                      "session": audit.correlation_id("test-session")},
                     str(Path(engine.trash_root, "audit.jsonl")))
        result = self.stop()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("status=incomplete", result.stderr)
        self.assertIn("do not guarantee a recoverable copy", result.stderr)
        self.assertNotIn("still use disk space", result.stderr)
        self.assertNotIn("GC permanent purge", result.stderr)
        self.assertEqual(Path(self.root, "good.txt").read_text(), "unmoved bytes")

    def test_partial_compensation_keeps_recovery_information(self):
        self.write("good.txt", "partial recovery bytes")
        result = self.preserve("rm good.txt missing.txt")
        self.assertEqual(result.returncode, 2, result.stderr)
        txid, = self.txids()
        result = self.stop()
        self.assertEqual(result.returncode, 2, result.stderr)
        # The failure audit omits compensations; the journal proves an incomplete move.
        self.assertIn("status=incomplete", result.stderr)
        self.assertIn("moved=1", result.stderr)
        self.assertIn("not confirmation that Bash ran", result.stderr)
        self.assertIn("still use disk space", result.stderr)
        self.assertIn("do not guarantee a recoverable copy", result.stderr)
        self.restore(txid)
        self.assertEqual(Path(self.root, "good.txt").read_text(), "partial recovery bytes")

    def test_same_session_in_other_workspace_does_not_share_cursor(self):
        self.write("first.txt")
        self.assertEqual(self.preserve("rm first.txt").returncode, 0)
        first, = self.txids()
        other = Path(self.home.name) / "other workspace"
        other.mkdir()
        (other / "second.txt").write_text("other bytes")
        env = {**self.env, "AGENT_GUARD_WORKSPACE": str(other)}
        result = self.run_hook(PRE, {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                                    "tool_input": {"command": "rm second.txt"}, "cwd": str(other),
                                    "session_id": "test-session", "tool_call_id": "other-call"}, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        second, = self.txids(other / ".agent-trash")
        result = self.stop()
        self.assertIn(first, result.stderr)
        self.assertNotIn(second, result.stderr)
        result = self.run_hook(STOP, {"hook_event_name": "Stop", "cwd": str(other),
                                     "session_id": "test-session", "stop_hook_active": False}, env=env)
        self.assertIn(second, result.stderr)
        self.assertNotIn(first, result.stderr)

    def test_snapshot_is_not_described_as_file_relocation(self):
        self.write("src/main.py", "unsaved tracked bytes\n")
        result = self.preserve("git restore src/main.py")
        self.assertEqual(result.returncode, 0, result.stderr)
        txid, = self.txids()
        result = self.stop()
        self.assertEqual(result.returncode, 2)
        self.assertIn("Git snapshot", result.stderr)
        self.assertIn(txid, result.stderr)
        self.assertIn("GC does not remove Git stash", result.stderr)
        self.assertNotIn("File relocation", result.stderr)
        self.assertNotIn("still use disk space", result.stderr)
        self.assertNotIn("GC permanent purge", result.stderr)

    def test_direct_artifact_delete_has_no_recovery_notice(self):
        self.write("node_modules/pkg/index.js")
        self.assertEqual(self.preserve("rm -rf node_modules").returncode, 0)
        result = self.stop()
        self.assertEqual((result.returncode, result.stdout + result.stderr), (0, ""))

    def test_clean_git_snapshot_has_no_recovery_notice(self):
        result = self.preserve("git restore src/main.py")
        self.assertEqual(result.returncode, 0, result.stderr)
        records = audit.tail(str(Path(self.root, ".agent-trash/audit.jsonl")))
        snapshot = records[-1]["compensations"][0]
        self.assertEqual(snapshot["strategy"], "snapshot")
        self.assertIsNone(snapshot["sha"])
        result = self.stop()
        self.assertEqual((result.returncode, result.stdout + result.stderr), (0, ""))

    def test_empty_clean_enumeration_has_no_recovery_notice(self):
        self.assertEqual(self.preserve("git clean -fd").returncode, 0)
        result = self.stop()
        self.assertEqual((result.returncode, result.stdout + result.stderr), (0, ""))

    def test_external_trash_and_working_directory_settings_are_respected(self):
        trash = Path(self.home.name) / "external trash"
        env = {**self.env, "AGENT_GUARD_TRASH": str(trash)}
        self.write("file.txt")
        self.assertEqual(self.preserve("rm file.txt", env=env).returncode, 0)
        result = self.stop(env=env)
        self.assertEqual(result.returncode, 2, result.stderr)
        txid, = self.txids(trash)
        self.assertIn(txid, result.stderr)
        self.assertIn("same working directory", result.stderr)
        # Generated guidance must preserve paths containing spaces as one argument.
        command = next(line.removeprefix("GC preview: ") for line in result.stderr.splitlines()
                       if line.startswith("GC preview: "))
        preview = subprocess.run(shlex.split(command) + ["--json"], cwd=self.root, env=env,
                                 capture_output=True, text=True, timeout=30)
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertEqual(json.loads(preview.stdout)["mode"], "plan")

    def test_corrupt_cursor_reports_failure_without_replaying_or_echoing(self):
        self.write("file.txt")
        self.assertEqual(self.preserve("rm file.txt").returncode, 0)
        self.assertEqual(self.stop().returncode, 2)
        cursor, = (Path(self.home.name) / "agent-guard-feedback").glob("*.json")
        cursor.write_text("private_invalid_cursor")
        result = self.stop()
        self.assertEqual(result.returncode, 2)
        self.assertIn("Recovery feedback unavailable", result.stderr)
        self.assertNotIn("private_invalid_cursor", result.stderr)
        self.assertNotIn(self.txids()[0], result.stderr)

    def test_incomplete_audit_append_is_not_acknowledged(self):
        self.write("file.txt")
        self.assertEqual(self.preserve("rm file.txt").returncode, 0)
        path = Path(self.root, ".agent-trash/audit.jsonl")
        lines = path.read_bytes().splitlines(keepends=True)
        self.assertIn(b'"enforce-proceed"', lines[-1])
        split = len(lines[-1]) // 2
        path.write_bytes(b"".join(lines[:-1]) + lines[-1][:split])
        self.assertEqual(self.stop().returncode, 0)
        with path.open("ab") as output:
            output.write(lines[-1][split:])
        self.assertEqual(self.stop().returncode, 2)
        self.assertEqual(self.stop().returncode, 0)

    def test_cursor_symlink_is_not_followed(self):
        self.write("file.txt")
        self.assertEqual(self.preserve("rm file.txt").returncode, 0)
        self.assertEqual(self.stop().returncode, 2)
        cursor, = (Path(self.home.name) / "agent-guard-feedback").glob("*.json")
        cursor.rename(cursor.with_suffix(".saved"))
        target = Path(self.home.name) / "untouched.txt"
        target.write_text("do not overwrite")
        cursor.symlink_to(target)
        result = self.stop()
        self.assertEqual(result.returncode, 2)
        self.assertIn("feedback unavailable", result.stderr)
        self.assertEqual(target.read_text(), "do not overwrite")

    def test_projected_audit_does_not_copy_free_text(self):
        path = Path(self.root, ".agent-trash/audit.jsonl")
        path.parent.mkdir()
        record = {"session": audit.correlation_id("test-session"), "event": "enforce-proceed",
                  "command": "private_command", "error": "private_error",
                  "compensations": [{"strategy": "relocate", "txid": "private_txid",
                                     "status": "private_status", "moved": "private_count"}]}
        path.write_text(json.dumps(record) + "\n")
        result = self.stop()
        self.assertEqual((result.returncode, result.stdout + result.stderr), (0, ""))

    def test_bridge_dispatches_stop_and_maps_python_startup_failure(self):
        shell = shutil.which("sh")
        if not shell:
            self.skipTest("POSIX sh unavailable")
        payload = json.dumps({"hook_event_name": "Stop", "cwd": self.root,
                              "session_id": "test-session", "stop_hook_active": False})
        for interpreter, expected in [(sys.executable, 0), (str(Path(self.root) / "missing-python"), 2)]:
            result = subprocess.run([shell, str(BRIDGE), interpreter, "--stop-feedback"],
                                    input=payload, cwd=self.root, env=self.env,
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, expected, result.stderr)
        result = subprocess.run([shell, str(BRIDGE), sys.executable, "--unknown"],
                                input=payload, cwd=self.root, env=self.env,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown option", result.stderr)


if __name__ == "__main__":
    unittest.main()
