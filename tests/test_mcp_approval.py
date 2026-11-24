"""Owned synthetic host-confirmation tests; scripted replies are not human proof."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import stat
import threading
import time
import unittest
from unittest import mock

from adapters.mcp import approval
from adapters.mcp.operations import APPROVAL_TOOL, InvalidArguments, Operations
from core import admission, cleanup_saved, cleanup_tasks
from core.cleanup import plan_cleanup
from tests.helpers import RepoFixture, git_available
from tests.test_mcp_protocol import Connection, SDK_AVAILABLE


ACCEPT = {"action": "accept", "content": {"decision": "仅批准本次", "reviewed": True}}


class _ApprovalFixture(RepoFixture):
    def setUp(self):
        environment = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_GUARD_")}
        patch = mock.patch.dict(os.environ, environment, clear=True)
        patch.start()
        self.addCleanup(patch.stop)
        super().setUp()
        self.root = os.path.realpath(self.root)
        self.trash = str(Path(self.root, ".agent-trash"))
        self.write(".gitignore", ".agent-trash/\ndist/\n")
        os.environ.update(AGENT_GUARD_WORKSPACE=self.root, AGENT_GUARD_TRASH=self.trash)

    def save(self, *paths, **options):
        return cleanup_saved.save_plan(plan_cleanup(self.root, targets=paths, discard=paths, **options))["saved_plan_id"]

    def request(self, name="old.txt"):
        self.write(name, "synthetic recoverable payload")
        saved_id = self.save(name, quarantine=True)
        return saved_id, approval.prepare_request(self.root, saved_id)

    def configure(self, **fields):
        self.write(".agent-guard/delete-policy.json", json.dumps(fields))

    def finish(self, saved_id, prepared, outcome="approve"):
        return approval.finish_request(self.root, saved_id, prepared["request_id"], outcome)

    def tearDown(self):
        # Windows treats the read-only review attribute as an unlink refusal.
        # Only these owned fixture exports need writable cleanup permissions.
        if os.name == "nt":
            for path in Path(self.root, ".agent-guard/cleanup-reviews").glob("*.md"):
                path.chmod(stat.S_IWRITE)
        super().tearDown()


@unittest.skipUnless(git_available(), "git required")
class HostApprovalTests(_ApprovalFixture):
    def test_review_exports_all_71_rows_and_selection_and_is_read_only(self):
        targets = [f"sessions/{number:03d}.txt" for number in range(1, 72)]
        for path in targets:
            self.write(path, "synthetic")
        saved_id = self.save(*targets, quarantine=True)
        result = approval.inspect_request(self.root, saved_id, export=True)
        review = Path(result["review"]["file"])
        text = review.read_text(encoding="utf-8")
        rows = [json.loads(line) for line in text.split("```jsonl\n", 1)[1].split("\n```", 1)[0].splitlines()]
        self.assertEqual({row["path"] for row in rows}, set(targets))
        self.assertTrue(all(row["selected"] and row["effect"] == "quarantine" and row["reason"] for row in rows))
        self.assertEqual(result["review"]["overview"]["selected_roots"], 71)
        self.assertEqual(result["review"]["overview"]["permanent_delete_roots"], 0)
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(review.stat().st_mode), 0o400)
        before = {str(p): (p.stat().st_size, p.stat().st_mtime_ns) for p in Path(self.root).rglob("*")}
        approval.inspect_request(self.root, saved_id)
        after = {str(p): (p.stat().st_size, p.stat().st_mtime_ns) for p in Path(self.root).rglob("*")}
        self.assertEqual(before, after)

    def test_default_disabled_and_no_model_approval_parameters(self):
        self.assertEqual(Operations(self.root).host_approval("prepare", "a" * 64)["error"]["code"],
                         "MCP_APPROVAL_DISABLED")
        for extra in ({"approved": True}, {"confirm": True}, {"task_id": "b" * 32}, {"session": "other"}, {"targets": ["x"]}):
            with self.assertRaises(InvalidArguments):
                Operations.validate(APPROVAL_TOOL, {"saved_plan_id": "a" * 64, **extra})
        with self.assertRaises(InvalidArguments):
            Operations(self.root, approval_channel="codex").call(APPROVAL_TOOL, {"saved_plan_id": "a" * 64})

    def test_review_distinguishes_permanent_effect_and_unselected_candidates(self):
        self.write("dist/generated.txt", "regenerable")
        self.write("old.txt", "declared obsolete")
        saved = cleanup_saved.save_plan(plan_cleanup(self.root, targets=["dist", "old.txt", "src/main.py"],
                                                      discard=["old.txt"]))
        result = approval.prepare_request(self.root, saved["saved_plan_id"])
        overview = result["review"]["overview"]
        self.assertEqual(overview["selected_roots"], 2)
        self.assertEqual(overview["permanent_delete_roots"], 1)
        self.assertEqual(overview["quarantine_roots"], 1)
        text = Path(result["review"]["file"]).read_text(encoding="utf-8")
        rows = [json.loads(line) for line in text.split("```jsonl\n", 1)[1].split("\n```", 1)[0].splitlines()]
        self.assertEqual(len(rows), 3)
        self.assertFalse(next(row for row in rows if row["path"] == "src/main.py")["selected"])
        self.finish(saved["saved_plan_id"], result, "CANCELLED")

    def test_all_negative_outcomes_persist_without_grant_or_reprompt(self):
        for outcome in ("DECLINED", "CANCELLED", "TIMED_OUT", "UNAVAILABLE", "INVALID_RESPONSE"):
            with self.subTest(outcome=outcome):
                saved_id, prepared = self.request(outcome + ".txt")
                result = self.finish(saved_id, prepared, outcome)
                self.assertEqual(result["request_status"], outcome)
                self.assertNotIn("task_id", result)
                repeated = approval.prepare_request(self.root, saved_id)
                self.assertFalse(repeated["prompt_required"])
                self.assertEqual(repeated["request_id"], prepared["request_id"])
        self.assertEqual(admission._load(self.trash)["approvals"], {})
        self.assertFalse(Path(self.root, ".agent-guard/cleanup-tasks").exists())

    def test_positive_issuance_reconnect_execute_once_and_restore(self):
        self.configure(batch_max_entries=1)
        self.write("one.txt", "one synthetic payload")
        self.write("two.txt", "two synthetic payload")
        originals = {p: Path(self.root, p).read_bytes() for p in ("one.txt", "two.txt")}
        saved_id = self.save(*originals, quarantine=True)
        prepared = approval.prepare_request(self.root, saved_id)
        issued = self.finish(saved_id, prepared)
        self.assertEqual(issued["request_status"], "APPROVED")
        self.assertTrue(issued["task"]["can_execute"])
        self.assertEqual(len(admission._load(self.trash)["approvals"]), 1)
        repeated = self.finish(saved_id, prepared)
        self.assertEqual(issued["task_id"], repeated["task_id"])
        ops = Operations(self.root, approval_channel="codex")
        recovered = ops.call("cleanup_plan", {"saved_plan_id": saved_id})["result"]["host_approval"]
        self.assertEqual(recovered["task_id"], issued["task_id"])
        result = ops.call("cleanup_execute", {"task_id": issued["task_id"], "view": "full"})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["result"]["status"], "COMPLETED")
        self.assertEqual(admission.describe(self.root, self.trash)["window_operations"], 1)
        charged = admission.describe(self.root, self.trash)["used_entries"]
        replay = ops.call("cleanup_execute", {"task_id": issued["task_id"]})
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(replay["result"]["status"], "COMPLETED")
        self.assertEqual(admission.describe(self.root, self.trash)["used_entries"], charged)
        self.assertEqual(admission.describe(self.root, self.trash)["window_operations"], 1)
        txid = result["result"]["groups"][0]["receipt"]["txid"]
        self.assertTrue(ops.call("restore", {"txid": txid})["ok"])
        for path, expected in originals.items():
            self.assertEqual(Path(self.root, path).read_bytes(), expected)
        self.assertEqual(admission.describe(self.root, self.trash)["used_entries"], charged)
        self.assertFalse(approval.prepare_request(self.root, saved_id)["prompt_required"])

    def test_source_change_during_confirmation_issues_no_task(self):
        saved_id, prepared = self.request()
        self.write("old.txt", "changed while confirmation open")
        result = self.finish(saved_id, prepared)
        self.assertEqual(result["request_status"], "UNKNOWN")
        self.assertIsNone(result["task"])
        self.assertEqual(admission._load(self.trash)["approvals"], {})
        self.assertEqual(Path(self.root, "old.txt").read_text(), "changed while confirmation open")

    def test_changed_review_during_confirmation_issues_no_task(self):
        saved_id, prepared = self.request()
        path = Path(prepared["review"]["file"])
        path.chmod(0o600)
        path.write_text("synthetic edit during review", encoding="utf-8")
        result = self.finish(saved_id, prepared)
        self.assertEqual(result["request_status"], "UNKNOWN")
        self.assertIsNone(result["task"])
        self.assertEqual(admission._load(self.trash)["approvals"], {})

    def test_config_change_and_expiry_do_not_authorize(self):
        saved_id, prepared = self.request()
        self.configure(protected_paths=["old.txt"])
        result = self.finish(saved_id, prepared)
        self.assertEqual(result["request_status"], "UNKNOWN")
        self.assertIsNone(result["task"])
        self.assertEqual(admission._load(self.trash)["approvals"], {})
        # Separate owned request: no identity or policy reset to evade a refusal.
        self.write("other.txt", "another synthetic target")
        other = self.save("other.txt")
        pending = approval.prepare_request(self.root, other)
        with mock.patch.object(approval.time, "time", return_value=pending["expires"] + 1):
            expired = self.finish(other, pending)
        self.assertEqual(expired["request_status"], "EXPIRED")
        self.assertNotIn("task_id", expired)

    def test_concurrent_prepare_has_one_prompt_and_one_request(self):
        self.write("old.txt", "synthetic")
        saved_id = self.save("old.txt")
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: Operations(self.root, approval_channel="codex").host_approval("prepare", saved_id), range(2)))
        self.assertTrue(all(result["ok"] for result in results), results)
        self.assertEqual(sum(result["result"]["prompt_required"] for result in results), 1)
        self.assertEqual(len({result["result"]["request_id"] for result in results}), 1)

    def test_interrupted_issuance_is_queryable_and_never_reissued(self):
        saved_id, prepared = self.request()
        with mock.patch.object(approval, "approve_task", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.finish(saved_id, prepared)
        result = approval.inspect_request(self.root, saved_id)
        self.assertEqual(result["request_status"], "ISSUING")
        self.assertIsNone(result["task"])
        with mock.patch.object(approval, "approve_task") as issue:
            again = approval.prepare_request(self.root, saved_id)
            self.finish(saved_id, prepared)
        self.assertFalse(again["prompt_required"])
        issue.assert_not_called()

    def test_lost_final_receipt_keeps_existing_task_discoverable(self):
        saved_id, prepared = self.request()
        def issue_then_fail(*args, **kwargs):
            cleanup_tasks.approve_task(*args, **kwargs)
            raise OSError("synthetic failure after task persistence")
        with mock.patch.object(approval, "approve_task", side_effect=issue_then_fail):
            result = self.finish(saved_id, prepared)
        self.assertEqual(result["request_status"], "UNKNOWN")
        self.assertTrue(result["task"]["can_execute"])
        repeated = approval.prepare_request(self.root, saved_id)
        self.assertEqual(repeated["task_id"], result["task_id"])
        self.assertFalse(repeated["prompt_required"])
        self.assertEqual(len(admission._load(self.trash)["approvals"]), 1)

    def test_approval_keeps_session_budget_and_capacity_checks(self):
        for config in ({"session_max_entries": 1}, {"quarantine_max_entries": 1}):
            with self.subTest(config=config):
                self.configure(**config)
                first = str(len(config)) + next(iter(config)) + ".txt"
                second = "second-" + first
                self.write(first, "one")
                self.write(second, "two")
                saved_id = self.save(first, second)
                result = self.finish(saved_id, approval.prepare_request(self.root, saved_id))
                self.assertTrue(result["task"]["can_execute"])
                executed = Operations(self.root).call("cleanup_execute", {"task_id": result["task_id"], "view": "full"})
                self.assertFalse(executed["ok"], executed)
                self.assertTrue(Path(self.root, first).exists())
                self.assertTrue(Path(self.root, second).exists())
                self.assertEqual(admission.describe(self.root, self.trash)["used_entries"], 0)

    def test_empty_or_protected_selection_cannot_request_approval(self):
        self.write("unknown.txt", "not declared obsolete")
        saved = cleanup_saved.save_plan(plan_cleanup(self.root, targets=["unknown.txt"]))
        with self.assertRaisesRegex(ValueError, "no selection"):
            approval.prepare_request(self.root, saved["saved_plan_id"])
        self.configure(protected_paths=["unknown.txt"])
        protected = self.save("unknown.txt")
        with self.assertRaisesRegex(ValueError, "no selection"):
            approval.prepare_request(self.root, protected)

    def test_review_tampering_and_wrong_bucket_stop_before_prompt(self):
        self.write("old.txt", "synthetic")
        saved_id = self.save("old.txt")
        review = Path(approval.inspect_request(self.root, saved_id, export=True)["review"]["file"])
        review.chmod(0o600)
        review.write_text("synthetic changed review", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "review file changed"):
            approval.prepare_request(self.root, saved_id)
        wrong = Operations(self.root, trash_root=str(Path(self.root).parent / "different-synthetic-bucket"), approval_channel="codex")
        self.assertFalse(wrong.host_approval("prepare", saved_id)["ok"])


@unittest.skipUnless(git_available() and SDK_AVAILABLE, "git and optional MCP SDK required")
class HostApprovalProtocolTests(_ApprovalFixture):
    # These are actual SDK stdio requests with explicit scripted answerers.
    def start(self, handler=None, capabilities=None):
        connection = Connection(self.root, approval_channel="codex",
                                capabilities={"elicitation": {"form": {}}} if capabilities is None else capabilities,
                                elicitation_handler=handler)
        self.addCleanup(connection.close)
        return connection

    def content(self, reply):
        raw = reply["result"]
        result = json.loads(raw["content"][0]["text"])
        self.assertEqual(result, raw["structuredContent"])
        self.assertEqual(raw.get("isError", False), not result["ok"])
        return result

    def pending_prompt(self, connection, saved_id):
        connection.counter += 1
        request_id = connection.counter
        connection.send({"jsonrpc": "2.0", "id": request_id, "method": "tools/call", "params": {
            "name": APPROVAL_TOOL, "arguments": {"saved_plan_id": saved_id}}})
        while True:
            value = connection.received.get(timeout=20)
            self.assertNotIsInstance(value, Exception)
            if value.get("method") == "elicitation/create":
                return request_id

    def test_stdio_defaults_negative_and_explicit_review_required(self):
        cases = [{"decision": "暂不批准", "reviewed": False},
                 {"decision": "仅批准本次", "reviewed": False},
                 {"decision": "暂不批准", "reviewed": True}]
        connection = self.start(lambda _: {"action": "accept", "content": cases.pop(0)})
        for number in range(3):
            self.write(f"negative-{number}.txt", "synthetic")
            saved_id = self.save(f"negative-{number}.txt")
            result = self.content(connection.call(APPROVAL_TOOL, saved_plan_id=saved_id))
            self.assertEqual(result["result"]["request_status"], "DECLINED")
        schema = connection.elicitations[0]["requestedSchema"]
        self.assertEqual(schema["properties"]["decision"]["default"], "暂不批准")
        self.assertEqual(schema["properties"]["decision"]["enum"][0], "暂不批准")
        self.assertFalse(schema["properties"]["reviewed"]["default"])
        self.assertEqual(admission._load(self.trash)["approvals"], {})

    def test_stdio_decline_and_cancel_no_approval(self):
        for action in ("decline", "cancel"):
            self.write(action + ".txt", "synthetic")
            saved_id = self.save(action + ".txt")
            connection = self.start(lambda _, action=action: {"action": action, "content": None})
            result = self.content(connection.call(APPROVAL_TOOL, saved_plan_id=saved_id))
            self.assertEqual(result["result"]["request_status"], "DECLINED" if action == "decline" else "CANCELLED")
            repeated = self.content(connection.call(APPROVAL_TOOL, saved_plan_id=saved_id))
            self.assertEqual(len(connection.elicitations), 1)
            self.assertEqual(repeated["result"]["request_id"], result["result"]["request_id"])

    def test_stdio_capability_missing_or_url_only_uses_terminal_fallback(self):
        self.write("old.txt", "synthetic")
        saved_id = self.save("old.txt")
        for caps in ({}, {"elicitation": {"url": {}}}):
            connection = self.start(capabilities=caps)
            result = self.content(connection.call(APPROVAL_TOOL, saved_plan_id=saved_id))
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["code"], "MCP_APPROVAL_UNSUPPORTED")
            self.assertEqual(connection.elicitations, [])
        self.assertEqual(approval.inspect_request(self.root, saved_id)["request_status"], "NOT_REQUESTED")

    def test_stdio_approved_batch_execute_reconnect_query_and_restore(self):
        self.configure(batch_max_entries=1)
        originals = {"one.txt": "one synthetic", "two.txt": "two synthetic"}
        for path, text in originals.items():
            self.write(path, text)
        connection = self.start(lambda _: ACCEPT)
        tools = connection.request("tools/list", {})["result"]["tools"]
        self.assertEqual(len(tools), 16)
        saved = self.content(connection.call("cleanup_plan", targets=list(originals), discard_targets=True,
                                             quarantine=True, save=True))
        self.assertTrue(saved["ok"], saved)
        saved_id = saved["result"]["saved_plan_id"]
        self.assertTrue(Path(saved["result"]["host_approval"]["review"]["file"]).is_file())
        issued = self.content(connection.call(APPROVAL_TOOL, saved_plan_id=saved_id))
        self.assertEqual(issued["result"]["request_status"], "APPROVED")
        task_id = issued["task_id"]
        connection.close()
        reconnected = self.start(lambda _: self.fail("reconnection must not re-prompt"))
        found = self.content(reconnected.call("cleanup_plan", saved_plan_id=saved_id))
        self.assertEqual(found["result"]["host_approval"]["task_id"], task_id)
        again = self.content(reconnected.call(APPROVAL_TOOL, saved_plan_id=saved_id))
        self.assertEqual(again["task_id"], task_id)
        result = self.content(reconnected.call("cleanup_execute", task_id=task_id, view="full"))
        self.assertTrue(result["ok"], result)
        txid = result["result"]["groups"][0]["receipt"]["txid"]
        self.assertTrue(self.content(reconnected.call("restore", txid=txid))["ok"])
        for path, text in originals.items():
            self.assertEqual(Path(self.root, path).read_text(), text)
        before = admission.describe(self.root, self.trash)
        replay = self.content(reconnected.call("cleanup_execute", task_id=task_id))
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(replay["result"]["status"], "COMPLETED")
        self.assertEqual(admission.describe(self.root, self.trash)["used_entries"], before["used_entries"])
        self.assertEqual(admission.describe(self.root, self.trash)["window_operations"], before["window_operations"])
        self.assertEqual(len(self.content(reconnected.call("transactions"))["result"]["transactions"]), 1)
        for path, text in originals.items():
            self.assertEqual(Path(self.root, path).read_text(), text)

    def test_stdio_source_change_after_form_stops_issuance(self):
        self.write("old.txt", "synthetic")
        saved_id = self.save("old.txt")
        def changed(_):
            self.write("old.txt", "changed while form open")
            return ACCEPT
        connection = self.start(changed)
        result = self.content(connection.call(APPROVAL_TOOL, saved_plan_id=saved_id))
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["result"]["request_status"], "UNKNOWN")
        self.assertIsNone(result["result"]["task"])

    def test_stdio_host_task_retains_custom_storage_binding(self):
        self.write("old.txt", "synthetic external-storage payload")
        bucket = Path(self.root, "retained-store")
        connection = Connection(self.root, trash_root=bucket, approval_channel="codex",
                                capabilities={"elicitation": {"form": {}}}, elicitation_handler=lambda _: ACCEPT)
        self.addCleanup(connection.close)
        saved = self.content(connection.call("cleanup_plan", targets=["old.txt"], discard_targets=True,
                                             quarantine=True, save=True))
        result = self.content(connection.call(APPROVAL_TOOL, saved_plan_id=saved["result"]["saved_plan_id"]))
        self.assertTrue(result["ok"], result)
        task_id = result["task_id"]
        wrong = Operations(self.root).call("cleanup_task_status", {"task_id": task_id})
        self.assertFalse(wrong["ok"], wrong)
        executed = self.content(connection.call("cleanup_execute", task_id=task_id, view="full"))
        self.assertTrue(executed["ok"], executed)
        receipt = executed["result"]["groups"][0]["receipt"]
        self.assertTrue(all(Path(row["trash"]).is_relative_to(bucket) for row in receipt["moved"]))
        self.assertTrue(self.content(connection.call("restore", txid=receipt["txid"]))["ok"])
        self.assertEqual(Path(self.root, "old.txt").read_text(), "synthetic external-storage payload")

    def test_stdio_cancel_pending_confirmation_does_not_issue(self):
        self.write("old.txt", "synthetic")
        saved_id = self.save("old.txt")
        connection = self.start()
        request_id = self.pending_prompt(connection, saved_id)
        connection.send({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {
            "requestId": request_id, "reason": "owned synthetic cancellation"}})
        deadline = time.monotonic() + 10
        result = approval.inspect_request(self.root, saved_id)
        while result["request_status"] == "PENDING" and time.monotonic() < deadline:
            time.sleep(0.05)
            result = approval.inspect_request(self.root, saved_id)
        self.assertEqual(result["request_status"], "CANCELLED")
        self.assertEqual(admission._load(self.trash)["approvals"], {})
        self.assertNotIn("task_id", result)

    def test_stdio_disconnect_pending_confirmation_does_not_reprompt(self):
        self.write("old.txt", "synthetic")
        saved_id = self.save("old.txt")
        first = self.start()
        self.pending_prompt(first, saved_id)
        first.close()
        second = self.start(lambda _: self.fail("disconnection must not re-prompt"))
        result = self.content(second.call(APPROVAL_TOOL, saved_plan_id=saved_id))
        self.assertFalse(result["result"]["prompt_required"])
        self.assertNotIn("task_id", result["result"])
        self.assertEqual(admission._load(self.trash)["approvals"], {})
        self.assertEqual(Path(self.root, "old.txt").read_text(), "synthetic")

    def test_stdio_pending_request_reuses_receipt_without_second_prompt(self):
        self.write("old.txt", "synthetic")
        saved_id = self.save("old.txt")
        shown = threading.Event()
        release = threading.Event()
        def pause(_):
            shown.set()
            if not release.wait(timeout=20):
                raise AssertionError("synthetic second-client check did not finish")
            return {"action": "cancel", "content": None}
        first = self.start(pause)
        second = self.start(lambda _: self.fail("must not prompt twice"))
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(first.call, APPROVAL_TOOL, saved_plan_id=saved_id)
            try:
                self.assertTrue(shown.wait(timeout=20))
                result = self.content(second.call(APPROVAL_TOOL, saved_plan_id=saved_id))
                self.assertEqual(result["result"]["request_status"], "PENDING")
                self.assertFalse(result["result"]["prompt_required"])
            finally:
                release.set()
            self.assertEqual(self.content(pending.result(timeout=20))["result"]["request_status"], "CANCELLED")


if __name__ == "__main__":
    unittest.main()
