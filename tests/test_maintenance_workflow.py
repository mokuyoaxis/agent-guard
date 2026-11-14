"""0.3 maintenance regressions in owned fixtures.

approve_task is injected at the unit-test boundary only. These tests do not
establish real operator approval or real model/host acceptance.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from unittest import mock

from adapters.mcp.operations import InvalidArguments, Operations
from core import admission, cleanup_saved, cleanup_tasks, recovery
from core.cleanup import plan_cleanup
from core.cleanup_views import plan_view, read_options, task_view
from core.operation_summary import execution_summary
from core.query_text import format_workspace_query
from core.workspace_query import workspace_query
from core import workspace_query as query_module
from tests.helpers import RepoFixture, git_available


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/delete-guard/scripts"


@unittest.skipUnless(git_available(), "git required")
class MaintenanceWorkflowTests(RepoFixture):
    def setUp(self):
        environment = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_GUARD_")}
        patch = mock.patch.dict(os.environ, environment, clear=True)
        patch.start()
        self.addCleanup(patch.stop)
        super().setUp()
        self.root = os.path.realpath(self.root)
        self.trash = str(Path(self.root) / ".agent-trash")
        self.write(".gitignore", "dist/\n.agent-trash/\n")
        os.environ["AGENT_GUARD_WORKSPACE"] = self.root

    def configure(self, **values):
        self.write(".agent-guard/delete-policy.json", json.dumps(values))

    def save(self, *paths):
        return cleanup_saved.save_plan(plan_cleanup(self.root, targets=paths, discard=paths))

    def approved_fixture(self, *paths):
        # Deliberate unit injection, never a real operator acceptance result.
        return cleanup_tasks.approve_task(self.save(*paths))

    def invoke(self, script, *arguments):
        return subprocess.run([sys.executable, "-B", str(SCRIPTS / (script + ".py")), *arguments],
                              cwd=self.root, env=dict(os.environ), stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=30)

    def snapshot(self):
        return {str(p.relative_to(self.root)): (p.lstat().st_mode, p.lstat().st_mtime_ns,
                    hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None)
                for p in Path(self.root).rglob("*") if not p.is_symlink()}

    def test_quarantine_saved_task_inherits_effect_and_restores_without_refund(self):
        self.configure(batch_max_entries=1)
        self.write("dist/cache", "generated with recovery")
        self.write("old.txt", "explicitly discarded ordinary bytes")
        saved = cleanup_saved.save_plan(plan_cleanup(self.root, targets=["dist", "old.txt"],
                                                    discard=["old.txt"], quarantine=True))
        fresh, selected = cleanup_saved.refresh_saved_plan(
            cleanup_saved.load_saved_plan(self.root, saved["saved_plan_id"]))
        self.assertTrue(fresh["options"]["quarantine"])
        self.assertEqual({row["effect"] for row in selected}, {"quarantine"})
        overview = plan_view(saved, read_options("plan"))
        self.assertTrue(overview["overview"]["quarantine_requested"])
        self.assertEqual(overview["overview"]["selected_effects"], {"quarantine": 2, "delete": 0})
        # Explicit unit approval injection in this owned fixture, not host acceptance.
        approved = cleanup_tasks.approve_task(saved)
        operations = Operations(self.root)
        executed = operations.call("cleanup_execute", {"task_id": approved["task_id"], "view": "full"})
        self.assertTrue(executed["ok"], executed)
        task = executed["result"]
        self.assertEqual(task["status"], "COMPLETED")
        self.assertEqual(len(task["groups"]), 1)
        receipt = task["groups"][0]["receipt"]
        self.assertTrue(receipt["quarantine"])
        self.assertEqual(receipt["verdict"]["decision"], "RELOCATE")
        before = admission.describe(self.root, self.trash)
        self.assertEqual(before["window_operations"], 1)
        self.assertGreater(before["used_bytes"], 0)
        back = operations.call("restore", {"txid": receipt["txid"]})
        self.assertTrue(back["ok"], back)
        after = admission.describe(self.root, self.trash)
        self.assertEqual((before["used_bytes"], before["used_entries"]),
                         (after["used_bytes"], after["used_entries"]))
        self.assertEqual(Path(self.root, "dist/cache").read_text(), "generated with recovery")
        self.assertEqual(Path(self.root, "old.txt").read_text(), "explicitly discarded ordinary bytes")

    def test_quarantine_saved_options_cannot_be_overridden_by_followup_calls(self):
        self.write("dist/cache", "preserve")
        saved = cleanup_saved.save_plan(plan_cleanup(self.root, targets=["dist"], quarantine=True))
        before = self.snapshot()
        for arguments in (("--from-plan", saved["saved_plan_id"]), ("--task", "a"*32),
                          ("--discover",), ("--measure", "--scope", "dist")):
            result = self.invoke("cleanup", *arguments, "--quarantine", "--json")
            self.assertEqual(result.returncode, 2, result.stderr)
        operations = Operations(self.root)
        for name, args in (("cleanup_plan", {"saved_plan_id": saved["saved_plan_id"], "quarantine": False}),
                           ("cleanup_execute", {"task_id": "a"*32, "quarantine": False})):
            with self.assertRaises(InvalidArguments):
                operations.call(name, args)
        self.assertEqual(self.snapshot(), before)

    def test_quarantine_task_rejects_replaced_plan_before_dispatch(self):
        self.write("dist/cache", "preserve")
        saved = cleanup_saved.save_plan(plan_cleanup(self.root, targets=["dist"], quarantine=True))
        approved = cleanup_tasks.approve_task(saved)  # Owned-fixture unit injection only.
        replacement = cleanup_saved.save_plan(plan_cleanup(self.root, targets=["dist"]))
        runner = mock.Mock()
        with mock.patch("core.cleanup_tasks.load_saved_plan", return_value=replacement):
            result = cleanup_tasks.execute_task(self.root, approved["task_id"], runner=runner)
        self.assertEqual(result["status"], "STOPPED")
        runner.assert_not_called()
        self.assertEqual(Path(self.root, "dist/cache").read_text(), "preserve")

    def test_literal_targets_saved_views_and_unrelated_changes(self):
        self.write("-old name.txt", "old")
        self.write("other.txt", "keep")
        saved = self.save("-old name.txt")
        self.assertEqual(saved["selection"], ["-old name.txt"])
        self.assertFalse(Path(self.trash).exists())
        self.write("other.txt", "changed unrelated source")
        fresh, selection = cleanup_saved.refresh_saved_plan(saved)
        self.assertEqual(fresh["plan_id"], saved["plan"]["plan_id"])
        before = json.dumps(saved, sort_keys=True)
        overview = plan_view(saved, read_options("plan"))
        details = plan_view(saved, read_options("plan", view="details", limit=1))
        self.assertEqual(overview["overview"]["selected_roots"], 1)
        self.assertEqual(details["page"]["returned"], 1)
        self.assertEqual(selection[0]["path"], "-old name.txt")
        self.assertEqual(json.dumps(saved, sort_keys=True), before)

    def test_glob_shaped_name_is_observed_literally_and_remains_blocked(self):
        self.write("old[1].txt", "literal")
        self.write("old1.txt", "different; preserve")
        saved = self.save("old[1].txt")
        self.assertEqual(saved["plan"]["targets"], ["old[1].txt"])
        self.assertEqual([row["path"] for row in saved["plan"]["entries"]], ["old[1].txt"])
        # The existing Core path API still refuses glob-shaped spellings.
        self.assertEqual(saved["selection"], [])
        self.assertFalse(saved["plan"]["entries"][0]["selectable"])

    def test_changed_source_rejected_before_terminal_confirmation(self):
        self.write("old.txt", "before")
        saved = self.save("old.txt")
        self.write("old.txt", "after; retain")
        result = self.invoke("cleanup", "--root", self.root, "--from-plan", saved["saved_plan_id"], "--approve", "--json")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("saved plan changed", result.stdout)
        self.assertNotIn("requires an interactive terminal", result.stdout)
        self.assertFalse(Path(self.trash).exists())

    def test_noninteractive_approval_rejected_without_state(self):
        self.write("old.txt")
        saved = self.save("old.txt")
        before = self.snapshot()
        result = self.invoke("cleanup", "--root", self.root, "--from-plan", saved["saved_plan_id"], "--approve", "--json")
        self.assertEqual(result.returncode, 2)
        self.assertIn("interactive terminal", result.stdout)
        self.assertEqual(self.snapshot(), before)

    def test_dry_preview_preserves_budget_and_reports_batch_gate(self):
        path = self.write("old.txt", "0123456789")
        self.configure(batch_max_bytes=5)
        before = self.snapshot()
        result = self.invoke("safe_delete", "--dry-run", "--json", path)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["admission"]["status"], "REQUIRES_APPROVAL")
        self.assertEqual(report["admission"]["code"], "BLOCK_DELETE_BATCH_CONFIRM")
        self.assertEqual(admission.describe(self.root, self.trash)["used_bytes"], 0)
        self.assertEqual(Path(path).read_text(), "0123456789")
        # A legacy advisory audit is permitted; the target itself stays intact.
        self.assertEqual(before["old.txt"], self.snapshot()["old.txt"])

    def test_mixed_groups_share_one_rate_event_and_replay_never_dispatches(self):
        self.configure(rate_max_operations=1)
        paths = [self.write("-old name.txt", "normal"), self.write("dist/cache.bin", "generated")]
        measured = admission._measure([paths[0], str(Path(self.root) / "dist")])
        task = self.approved_fixture("-old name.txt", "dist")
        runner = mock.Mock(wraps=subprocess.run)
        result = cleanup_tasks.execute_task(self.root, task["task_id"], runner=runner)
        self.assertEqual(result["status"], "COMPLETED", result)
        self.assertEqual(result["counts"]["confirmed"], 2)
        self.assertEqual(runner.call_count, 2)
        budget = admission.describe(self.root, self.trash)
        self.assertEqual((budget["used_bytes"], budget["used_entries"], budget["window_operations"]),
                         (measured["bytes"], measured["entries"], 1))
        summary = execution_summary("cleanup", result)
        self.assertEqual(summary["actions"], {"quarantined": 1, "regenerable_deleted": 1})
        self.assertIsNone(summary["space"]["physical_freed_bytes"])
        before = self.snapshot()
        replay = cleanup_tasks.execute_task(self.root, task["task_id"], runner=runner)
        self.assertEqual(replay["status"], "COMPLETED")
        self.assertEqual(runner.call_count, 2)
        self.assertEqual(self.snapshot(), before)

    def test_partial_launch_failure_keeps_first_receipt_and_stops(self):
        self.write("old.txt", "normal")
        self.write("dist/cache", "generated")
        task = self.approved_fixture("old.txt", "dist")
        calls = []
        def runner(*args, **kwargs):
            calls.append(args)
            if len(calls) == 2:
                raise OSError("injected launch failure")
            return subprocess.run(*args, **kwargs)
        result = cleanup_tasks.execute_task(self.root, task["task_id"], runner=runner)
        self.assertEqual(result["status"], "PARTIAL", result)
        self.assertEqual(result["counts"]["confirmed"], 1)
        self.assertTrue((Path(self.root) / "dist/cache").exists())
        self.assertEqual(len(recovery.RecoveryEngine(self.root, self.trash).transactions()), 1)
        self.assertEqual(cleanup_tasks.task_status(self.root, task["task_id"])["exit"], 0)

    def test_lost_response_is_unknown_query_finds_recovery_and_no_retry(self):
        self.write("old.txt", "normal")
        task = self.approved_fixture("old.txt")
        def lose_response(*args, **kwargs):
            proc = subprocess.run(*args, **kwargs)
            return subprocess.CompletedProcess(proc.args, proc.returncode, "lost-json", "")
        result = cleanup_tasks.execute_task(self.root, task["task_id"], runner=lose_response)
        self.assertEqual(result["status"], "UNKNOWN", result)
        self.assertEqual(result["counts"]["confirmed"], 0)
        self.assertFalse((Path(self.root) / "old.txt").exists())
        overview = workspace_query(self.root)
        self.assertEqual(overview["transactions"]["states"], {"RESTORABLE": 1})
        with mock.patch.object(cleanup_tasks.subprocess, "run", side_effect=AssertionError("must not retry")):
            self.assertEqual(cleanup_tasks.execute_task(self.root, task["task_id"])["status"], "UNKNOWN")

    def test_concurrent_claim_dispatches_once(self):
        self.write("old.txt", "normal")
        task = self.approved_fixture("old.txt")
        entered, release = threading.Event(), threading.Event()
        results = []
        def runner(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(15))
            return subprocess.run(*args, **kwargs)
        thread = threading.Thread(target=lambda: results.append(cleanup_tasks.execute_task(self.root, task["task_id"], runner=runner)))
        thread.start()
        try:
            self.assertTrue(entered.wait(15))
            with mock.patch.object(cleanup_tasks.subprocess, "run", side_effect=AssertionError("second dispatch")):
                second = cleanup_tasks.execute_task(self.root, task["task_id"])
            self.assertEqual(second["status"], "RUNNING")
        finally:
            release.set()
            thread.join(20)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results[0]["status"], "COMPLETED")

    def test_expired_config_changed_and_stale_tasks_stop_before_dispatch(self):
        for cause in ("expired", "config", "source"):
            with self.subTest(cause=cause):
                name = cause + ".txt"
                self.write(name, "before")
                task = self.approved_fixture(name)
                if cause == "expired":
                    now = cleanup_tasks.time.time()
                    patch = mock.patch.object(cleanup_tasks.time, "time", return_value=now + 601)
                else:
                    patch = mock.patch.object(cleanup_tasks.time, "time", wraps=cleanup_tasks.time.time)
                    if cause == "config":
                        self.configure(rate_max_operations=59)
                    else:
                        self.write(name, "after")
                runner = mock.Mock(side_effect=AssertionError("must not dispatch"))
                with patch:
                    result = cleanup_tasks.execute_task(self.root, task["task_id"], runner=runner)
                runner.assert_not_called()
                self.assertEqual(result["status"], "STOPPED", result)
                self.assertEqual(result["counts"]["not_dispatched"], 1)
                self.assertTrue((Path(self.root) / name).exists())
        self.assertEqual(admission.describe(self.root, self.trash)["used_entries"], 0)

    def test_overview_and_task_views_are_readonly_and_hide_private_approval(self):
        self.write("old.txt")
        task = self.approved_fixture("old.txt")
        raw = cleanup_tasks._load(self.root, task["task_id"])
        before = self.snapshot()
        with mock.patch.object(recovery.RecoveryEngine, "gc_plan", side_effect=AssertionError("no payload scan")):
            overview = workspace_query(self.root)
        text = json.dumps(overview)
        self.assertTrue(overview["complete"], overview)
        self.assertEqual(overview["tasks"]["returned"], 1)
        self.assertNotIn(raw["approval_token"], text)
        self.assertNotIn(raw["session"], text)
        self.assertIsNone(overview["storage"]["logical_bytes"])
        self.assertEqual(self.snapshot(), before)
        details = task_view(task, read_options("task", view="details", state="not_dispatched"))
        self.assertEqual(details["page"]["matched_rows"], 1)
        with mock.patch.dict(os.environ, {"AGENT_GUARD_SESSION": "different-fixture-session"}):
            with self.assertRaisesRegex(ValueError, "session settings"):
                cleanup_tasks.task_status(self.root, task["task_id"])
            other = workspace_query(self.root)
            self.assertEqual(other["tasks"]["returned"], 0)

    def test_empty_status_defaults_and_pipe_output_make_no_files(self):
        before = self.snapshot()
        full = self.invoke("status", "--json")
        overview = self.invoke("status", "--json", "--view", "overview")
        text = self.invoke("status")
        self.assertEqual((full.returncode, overview.returncode, text.returncode), (0, 0, 0))
        self.assertIn("gc", json.loads(full.stdout))
        self.assertEqual(json.loads(overview.stdout)["schema"], "workspace_query/v1")
        self.assertIn("READ-ONLY OVERVIEW", text.stdout)
        self.assertNotIn("\x1b", text.stdout)
        self.assertEqual(self.snapshot(), before)

    def test_incomplete_manifest_does_not_claim_empty_or_zero_gc_candidates(self):
        self.write(".agent-trash/manifest.jsonl", '{"type":"tx-start","txid":"fixture-tx"}\n{"partial":')
        before = self.snapshot()
        result = workspace_query(self.root)
        self.assertFalse(result["complete"])
        self.assertEqual(result["transactions"]["items"][0]["state"], "UNKNOWN")
        self.assertIsNone(result["gc_candidates"]["count"])
        self.assertEqual(self.snapshot(), before)
        cli = self.invoke("status", "--json", "--view", "overview")
        self.assertEqual(cli.returncode, 1, cli.stderr)

    def test_execution_rejects_view_filters_without_deleting(self):
        self.write("old.txt")
        saved = self.save("old.txt")
        before = self.snapshot()
        result = self.invoke("cleanup", "--root", self.root, "--from-plan", saved["saved_plan_id"],
                             "--execute", "--view", "summary", "--limit", "1", "--json")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.snapshot(), before)

    def test_metadata_limits_and_nonregular_manifest_report_partial(self):
        path = Path(self.write(".agent-trash/manifest.jsonl", '\n'.join(
            json.dumps({"type": "tx-start", "txid": "fixture-" + str(i)}) for i in range(2)) + "\n"))
        with mock.patch.object(query_module, "MAX_MANIFEST_LINES", 1):
            result = workspace_query(self.root)
        self.assertFalse(result["complete"])
        self.assertIn("MANIFEST_READ_LIMIT", result["transactions"]["coverage"]["issues"])
        self.assertIsNone(result["transactions"]["has_more"])
        # Preserve the fixture journal, then place a FIFO at its known location.
        path.rename(path.with_suffix(".preserved"))
        if hasattr(os, "mkfifo"):
            os.mkfifo(path)
            result = workspace_query(self.root)
            self.assertFalse(result["complete"])
            self.assertIn("MANIFEST_UNREADABLE", result["transactions"]["coverage"]["issues"])

    def test_mcp_saved_summary_details_and_strict_arguments(self):
        self.write("--old name.txt", "old")
        operations = Operations(self.root)
        result = operations.call("cleanup_plan", {"targets": ["--old name.txt"], "discard_targets": True, "save": True})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["result"]["view"], "summary")
        self.assertNotIn("entries", result["result"])
        saved_id = result["result"]["saved_plan_id"]
        before = self.snapshot()
        details = operations.call("cleanup_plan", {"saved_plan_id": saved_id, "view": "details", "limit": 1})
        self.assertTrue(details["ok"], details)
        self.assertEqual(details["result"]["entries"][0]["path"], "--old name.txt")
        self.assertEqual(details["result"]["page"]["returned"], 1)
        self.assertEqual(self.snapshot(), before)
        for name, args in (("cleanup_plan", {"targets": ["--old name.txt"], "approve": True}),
                           ("cleanup_execute", {"task_id": "a" * 32, "approval_token": "not-allowed"}),
                           ("cleanup_execute", {"task_id": "a" * 32, "limit": 1}),
                           ("status", {"view": "overview", "limit": True})):
            with self.subTest(name=name, args=args), self.assertRaises(InvalidArguments):
                operations.validate(name, args)

    def test_mcp_task_execution_partial_status_and_compact_receipts(self):
        self.write("old.txt", "normal")
        task = self.approved_fixture("old.txt")
        operations = Operations(self.root)
        executed = operations.call("cleanup_execute", {"task_id": task["task_id"]})
        self.assertTrue(executed["ok"], executed)
        self.assertEqual(executed["result"]["status"], "COMPLETED")
        self.assertNotIn("groups", executed["result"])
        queried = operations.call("cleanup_task_status", {"task_id": task["task_id"], "view": "details", "state": "confirmed"})
        self.assertTrue(queried["ok"], queried)
        self.assertEqual(queried["result"]["page"]["returned"], 1)
        self.write(".agent-trash/manifest.jsonl", "{torn\n")
        partial = operations.call("status", {"view": "overview"})
        self.assertTrue(partial["ok"], partial)
        self.assertTrue(partial["partial"])
        self.assertEqual(partial["exit_code"], 1)

    def test_approval_does_not_waive_session_budget(self):
        self.write("old.txt", "ten bytes.")
        self.configure(session_max_bytes=1)
        task = self.approved_fixture("old.txt")
        runner = mock.Mock(side_effect=AssertionError("over-budget dispatch"))
        result = cleanup_tasks.execute_task(self.root, task["task_id"], runner=runner)
        runner.assert_not_called()
        self.assertEqual(result["status"], "STOPPED")
        self.assertEqual(result["preflight"][0]["code"], "BLOCK_DELETE_LIMIT")
        self.assertEqual(admission.describe(self.root, self.trash)["used_bytes"], 0)

    def test_narrow_terminal_retains_task_id_and_escapes_controls(self):
        self.write("old.txt")
        task = self.approved_fixture("old.txt")
        with mock.patch.object(subprocess, "run", side_effect=AssertionError("overview must not invoke Git")):
            data = workspace_query(self.root)
        data["workspace"] = "示例项目\n\x1b[31m"
        text = format_workspace_query(data, width=40, color=False)
        self.assertIn(task["task_id"], text)
        self.assertIn("示例项目\\n", text)
        self.assertIn("\\u001b", text)
        self.assertNotIn("\x1b", text)

    def test_dispatch_timeout_leaves_other_group_pending_and_does_not_retry(self):
        self.write("old.txt", "normal")
        self.write("dist/cache", "generated")
        task = self.approved_fixture("old.txt", "dist")
        runner = mock.Mock(side_effect=subprocess.TimeoutExpired("owned-safe-delete", 120))
        result = cleanup_tasks.execute_task(self.root, task["task_id"], runner=runner)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["counts"], {"targets": 2, "confirmed": 0, "unconfirmed": 2, "not_dispatched": 1})
        runner.assert_called_once()
        summary = execution_summary("cleanup", result)
        self.assertEqual(summary["counts"]["dispatched_unconfirmed"], 1)
        self.assertEqual(cleanup_tasks.execute_task(self.root, task["task_id"], runner=runner)["status"], "UNKNOWN")
        runner.assert_called_once()

    def test_receipt_persistence_failure_keeps_intent_and_recovery_evidence(self):
        self.write("old.txt", "normal")
        task = self.approved_fixture("old.txt")
        persist = cleanup_tasks._persist
        calls = []
        def fail_after_dispatch(record, *, dispatched):
            calls.append(dispatched)
            if dispatched:
                raise cleanup_tasks.TaskStateError(task["task_id"], True)
            return persist(record, dispatched=dispatched)
        with mock.patch.object(cleanup_tasks, "_persist", side_effect=fail_after_dispatch):
            with self.assertRaises(cleanup_tasks.TaskStateError) as caught:
                cleanup_tasks.execute_task(self.root, task["task_id"])
        self.assertTrue(caught.exception.may_have_changed)
        self.assertEqual(caught.exception.task_id, task["task_id"])
        current = cleanup_tasks.task_status(self.root, task["task_id"])
        self.assertEqual(current["status"], "RUNNING")
        self.assertFalse(current["can_execute"])
        self.assertFalse((Path(self.root) / "old.txt").exists())
        self.assertEqual(workspace_query(self.root)["transactions"]["states"], {"RESTORABLE": 1})


if __name__ == "__main__":
    unittest.main()
