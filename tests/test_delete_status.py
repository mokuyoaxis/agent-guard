"""Read-only deletion admission status and explicit operator budget reset."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from core import admission, audit, recovery


SCRIPT = Path(__file__).resolve().parents[1] / "skills/delete-guard/scripts/status.py"
with mock.patch.object(sys, "path", [str(SCRIPT.parent), *sys.path]):
    spec = importlib.util.spec_from_file_location("delete_status_cli", SCRIPT)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)


class DeleteStatusTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="delete-status-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.trash = self.root / ".agent-trash"
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith("AGENT_GUARD_")}
        environment.update(AGENT_GUARD_WORKSPACE=str(self.root),
                           AGENT_GUARD_SESSION="SYNTHETIC_PRIVATE_STATUS_SESSION")
        patch = mock.patch.dict(os.environ, environment, clear=True)
        patch.start()
        self.addCleanup(patch.stop)

    def write(self, name, data=b"owned"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def configure(self, **values):
        self.write(".agent-guard/delete-policy.json", json.dumps(values).encode())

    def consume(self):
        source = self.write("owned.txt", b"1234")
        with admission.admit(str(self.root), str(self.trash), [str(source)], quarantine=False):
            pass

    def counters(self):
        return admission.describe(str(self.root), str(self.trash))

    def snapshot(self):
        result = {}
        for path in self.root.rglob("*"):
            if path.is_file():
                stat = path.stat()
                result[str(path.relative_to(self.root))] = (
                    hashlib.sha256(path.read_bytes()).hexdigest(), stat.st_mode, stat.st_mtime_ns)
            elif path.is_dir():
                result[str(path.relative_to(self.root))] = "directory"
        return result

    def invoke(self, *args):
        return subprocess.run([sys.executable, "-B", str(SCRIPT), *args],
                              cwd=self.root, env=dict(os.environ), capture_output=True,
                              text=True, timeout=20)

    def reset(self, answer="RESET\n", *, stdin_tty=True, stderr_tty=True):
        stdin, stderr = io.StringIO(answer), io.StringIO()
        stdin.isatty = lambda: stdin_tty
        stderr.isatty = lambda: stderr_tty
        engine = recovery.RecoveryEngine(str(self.root), str(self.trash))
        with mock.patch.object(cli.sys, "stdin", stdin), mock.patch.object(cli.sys, "stderr", stderr):
            result = cli.reset_delete_budget(str(self.root), str(self.trash), engine)
        return result, stderr.getvalue()

    def test_readonly_json_does_not_create_quarantine_or_state(self):
        self.write("owned.txt")
        before = self.snapshot()
        result = self.invoke("--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["delete_admission"]["used_entries"], 0)
        self.assertIn("gc", data)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.trash.exists())

    def test_readonly_status_reports_actual_usage_and_window(self):
        self.consume()
        before = self.snapshot()
        result = self.invoke("--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        deletion = json.loads(result.stdout)["delete_admission"]
        self.assertEqual((deletion["used_bytes"], deletion["used_entries"], deletion["window_operations"]),
                         (4, 1, 1))
        self.assertEqual(self.snapshot(), before)

    def test_text_reports_used_limits_and_rate_window(self):
        self.configure(session_max_bytes=7, session_max_entries=8,
                       rate_max_operations=9, rate_window_seconds=17)
        self.consume()
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("4/7 bytes", result.stdout)
        self.assertIn("1/8 entries", result.stdout)
        self.assertIn("1/9 operations in 17s", result.stdout)

    def test_corrupt_admission_is_visible_and_gc_remains_available(self):
        self.write(".agent-trash/admission.json", b"not JSON")
        before = self.snapshot()
        result = self.invoke("--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["delete_admission"]["status"], "BLOCKED")
        self.assertEqual(data["delete_admission"]["code"], "BLOCK_DELETE_STATE_INVALID")
        self.assertIn("gc", data)
        self.assertIn("retention", data)
        self.assertEqual(self.snapshot(), before)

    def test_corrupt_config_is_visible_without_silent_default(self):
        self.write(".agent-guard/delete-policy.json", b"{broken")
        before = self.snapshot()
        result = self.invoke("--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        deletion = json.loads(result.stdout)["delete_admission"]
        self.assertEqual(deletion["status"], "BLOCKED")
        self.assertEqual(deletion["code"], "BLOCK_DELETE_CONFIG_INVALID")
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.trash.exists())

    def test_noninteractive_reset_exits_two_without_creating_anything(self):
        self.write("owned.txt")
        before = self.snapshot()
        result = self.invoke("--reset-delete-budget", "--json")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("interactive stdin and stderr", result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_reset_cannot_be_combined_with_trash_index(self):
        before = self.snapshot()
        result = self.invoke("--reset-delete-budget", "--trash-index")
        self.assertEqual(result.returncode, 2)
        self.assertIn("cannot be combined", result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_both_terminal_streams_and_exact_token_are_required(self):
        for stdin_tty, stderr_tty, answer in (
                (False, True, "RESET\n"), (True, False, "RESET\n"),
                (True, True, "yes\n"), (True, True, " RESET \n")):
            with self.subTest(stdin=stdin_tty, stderr=stderr_tty, answer=answer):
                before = self.snapshot()
                result, _ = self.reset(answer, stdin_tty=stdin_tty, stderr_tty=stderr_tty)
                self.assertFalse(result)
                self.assertEqual(self.snapshot(), before)

    def test_confirmed_reset_has_durable_intent_before_mutation_and_preserves_data(self):
        self.consume()
        other_session = "owned-other-session"
        with mock.patch.dict(os.environ, {"AGENT_GUARD_SESSION": other_session}):
            self.consume()
        payload = self.write(".agent-trash/owned-recovery/valuable.bin", b"recovery bytes")
        journal = self.write(".agent-trash/manifest.jsonl", b"retained recovery metadata\n")
        original_payload, original_journal = payload.read_bytes(), journal.read_bytes()
        original_append = audit.append
        observations = []

        def append(record, path):
            stored = original_append(record, path)
            observations.append((record["phase"], self.counters()["used_entries"]))
            return stored

        with mock.patch.object(cli.audit, "append", side_effect=append):
            result, text = self.reset()
        self.assertTrue(result, text)
        self.assertEqual(observations, [("intent", 1), ("complete", 0)])
        self.assertEqual(self.counters()["window_operations"], 0)
        with mock.patch.dict(os.environ, {"AGENT_GUARD_SESSION": other_session}):
            self.assertEqual(self.counters()["used_entries"], 1)
        self.assertEqual(payload.read_bytes(), original_payload)
        self.assertEqual(journal.read_bytes(), original_journal)
        rows = audit.tail(str(self.trash / "audit.jsonl"))
        self.assertEqual([row["event"] for row in rows], ["delete-budget-reset"] * 2)
        self.assertEqual([row["phase"] for row in rows], ["intent", "complete"])
        self.assertEqual(rows[0]["check_id"], rows[1]["check_id"])
        self.assertNotIn(os.environ["AGENT_GUARD_SESSION"], (self.trash / "audit.jsonl").read_text())

    def test_audit_intent_failure_does_not_reset_budget(self):
        self.consume()
        with mock.patch.object(cli.audit, "append", side_effect=OSError("owned audit failure")):
            result, text = self.reset()
        self.assertFalse(result)
        self.assertIn("intent could not be persisted", text)
        self.assertEqual(self.counters()["used_entries"], 1)

    def test_reset_failure_keeps_intent_without_claiming_completion(self):
        self.consume()
        denial = admission.AdmissionDenied("BLOCK_DELETE_STATE_INVALID", "owned reset failure")
        with mock.patch.object(cli.admission, "reset_session", side_effect=denial):
            result, text = self.reset()
        self.assertFalse(result)
        self.assertIn("did not complete", text)
        self.assertEqual(self.counters()["used_entries"], 1)
        self.assertEqual([row["phase"] for row in audit.tail(str(self.trash / "audit.jsonl"))], ["intent"])

    def test_completion_audit_failure_reports_that_budget_was_reset(self):
        self.consume()
        original_append = audit.append

        def append(record, path):
            if record["phase"] == "complete":
                raise OSError("owned completion failure")
            return original_append(record, path)

        with mock.patch.object(cli.audit, "append", side_effect=append):
            result, text = self.reset()
        self.assertFalse(result)
        self.assertIn("budget was reset", text)
        self.assertEqual(self.counters()["used_entries"], 0)

    def test_help_parses_without_writing_and_reset_event_rejects_free_text(self):
        before = self.snapshot()
        result = self.invoke("--help")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.snapshot(), before)
        projected = audit.project({"event": "delete-budget-reset", "phase": "intent",
                                   "reason": "PRIVATE_FREE_TEXT", "limits": {"value": "PRIVATE_FREE_TEXT"}})
        self.assertEqual(projected, {"event": "delete-budget-reset", "phase": "intent"})


if __name__ == "__main__":
    unittest.main()
