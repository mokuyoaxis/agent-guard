"""Real stdio interruptions in the committed-but-unacknowledged window.

These scenarios establish recovery after a reply is lost or abandoned. They
do not claim atomicity when a process dies during a multi-file relocation.
"""
import importlib.util
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "adapters/mcp/server.py"
BARRIER_SERVER = ROOT / "tests/fixtures/mcp_interruption_server.py"
MCP_PYTHON = os.environ.get("AGENT_GUARD_MCP_PYTHON") or sys.executable
SDK_AVAILABLE = bool(os.environ.get("AGENT_GUARD_MCP_PYTHON") or importlib.util.find_spec("mcp"))


class Connection:
    """Small test transport with explicit request IDs and client deadlines."""
    def __init__(self, workspace, control=None):
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("AGENT_GUARD_", "GIT_"))
               and key not in ("PYTHONPATH", "PYTHONHOME")}
        env.update(PYTHONDONTWRITEBYTECODE="1", GIT_CONFIG_NOSYSTEM="1",
                   GIT_CONFIG_GLOBAL=os.devnull)
        command = [MCP_PYTHON, "-B", str(BARRIER_SERVER if control else SERVER),
                   "--workspace", str(workspace)]
        if control:
            command += ["--control", str(control)]
        self.stderr = tempfile.TemporaryFile()
        self.child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=self.stderr, text=True, encoding="utf-8", env=env)
        self.received = queue.Queue()
        self.counter = 0
        self.calls = []
        self.closed = False

        def read():
            for line in self.child.stdout:
                try:
                    self.received.put(json.loads(line))
                except ValueError as exc:
                    self.received.put(exc)
            self.received.put(EOFError("MCP server closed stdout"))

        self.reader = threading.Thread(target=read, daemon=True)
        self.reader.start()
        try:
            initialized = self.request("initialize", {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "synthetic-interruption-test", "version": "1"}})
            if initialized["result"]["serverInfo"]["name"] != "agent-guard":
                raise AssertionError("unexpected server identity")
            self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except BaseException:
            self.close()
            raise

    def send(self, value):
        self.child.stdin.write(json.dumps(value) + "\n")
        self.child.stdin.flush()

    def begin(self, method, params):
        self.counter += 1
        self.calls.append((method, params))
        self.send({"jsonrpc": "2.0", "id": self.counter, "method": method, "params": params})
        return self.counter

    def receive(self, request_id, timeout=20):
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("client deadline expired")
            try:
                value = self.received.get(timeout=remaining)
            except queue.Empty:
                raise TimeoutError("client deadline expired") from None
            if isinstance(value, BaseException):
                raise value
            if value.get("id") == request_id:
                return value

    def request(self, method, params):
        return self.receive(self.begin(method, params))

    def call(self, name, **arguments):
        return self.request("tools/call", {"name": name, "arguments": arguments})

    def close_input(self):
        if not self.child.stdin.closed:
            self.child.stdin.close()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.close_input()
        try:
            self.child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.child.terminate()
            try:
                self.child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.child.kill()
                self.child.wait(timeout=5)
        self.reader.join(timeout=2)
        self.child.stdout.close()
        self.stderr.close()


@unittest.skipUnless(SDK_AVAILABLE, "optional MCP SDK required for real interruption tests")
class McpInterruptionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="agent-guard-mcp-interruption-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.workspace, self.control = self.root / "workspace", self.root / "control"
        self.workspace.mkdir()
        self.control.mkdir()
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        for args in (["init", "-q"], ["config", "maintenance.auto", "false"]):
            subprocess.run(["git", "-C", str(self.workspace), *args],
                           check=True, capture_output=True, env=env)
        (self.workspace / ".gitignore").write_text(".agent-trash/\n")
        config = self.workspace / ".agent-guard"
        config.mkdir()
        (config / "delete-policy.json").write_text(json.dumps({"session_max_entries": 1}))
        self.first, self.second = self.workspace / "first.txt", self.workspace / "second.txt"
        self.original = b"Synthetic committed recovery evidence.\n"
        self.first.write_bytes(self.original)
        self.second.write_bytes(b"must survive budget refusal\n")
        self.connection = Connection(self.workspace, self.control)
        self.addCleanup(self.connection.close)
        # Release a surviving worker before closing its connection on assertion failure.
        self.addCleanup(self.release)

    def release(self):
        (self.control / "release").touch(exist_ok=True)

    def committed(self):
        request_id = self.connection.begin("tools/call", {
            "name": "safe_delete", "arguments": {"paths": [self.first.name]}})
        marker = self.control / "ready"
        deadline = time.monotonic() + 20
        while not marker.exists():
            self.assertIsNone(self.connection.child.poll(), "barrier server exited before mutation")
            if time.monotonic() >= deadline:
                self.fail("actual mutation did not reach the reply barrier")
            time.sleep(0.01)
        receipt = json.loads((self.control / "committed.json").read_text())
        self.assertEqual(receipt["stage"], "mutation_complete_before_reply")
        self.assertFalse(self.first.exists())
        self.assertEqual((self.workspace / ".agent-trash" / receipt["txid"] / self.first.name).read_bytes(),
                         self.original)
        self.assertEqual(sum(method == "tools/call" for method, _ in self.connection.calls), 1)
        return request_id, receipt["txid"]

    def content(self, reply):
        result = reply["result"]
        parsed = json.loads(result["content"][0]["text"])
        self.assertEqual(parsed, result["structuredContent"])
        self.assertEqual(result.get("isError", False), not parsed["ok"])
        return parsed

    def verify_recovery(self, txid):
        self.connection.close()
        self.assertIsNotNone(self.connection.child.poll())
        self.assertFalse(self.connection.reader.is_alive())
        fresh = Connection(self.workspace)
        self.addCleanup(fresh.close)
        transactions = self.content(fresh.call("transactions"))["result"]["transactions"]
        self.assertEqual([(row["txid"], row["state"]) for row in transactions], [(txid, "RESTORABLE")])
        admission = self.content(fresh.call("status", tail=0))["result"]["delete_admission"]
        self.assertEqual((admission["used_entries"], admission["used_bytes"]), (1, len(self.original)))
        denied = self.content(fresh.call("safe_delete", paths=[self.second.name]))
        self.assertFalse(denied["ok"])
        self.assertEqual(denied["result"]["verdict"]["code"], "BLOCK_DELETE_LIMIT")
        self.assertEqual(self.second.read_bytes(), b"must survive budget refusal\n")
        self.assertTrue(self.content(fresh.call("restore", txid=txid))["ok"])
        self.assertEqual(self.first.read_bytes(), self.original)
        admission = self.content(fresh.call("status", tail=0))["result"]["delete_admission"]
        self.assertEqual((admission["used_entries"], admission["used_bytes"]), (1, len(self.original)))
        transactions = self.content(fresh.call("transactions"))["result"]["transactions"]
        self.assertEqual([(row["txid"], row["state"]) for row in transactions], [(txid, "RESTORED")])
        self.assertEqual(sum(row["used_entries"] for row in
                             json.loads((self.workspace / ".agent-trash/admission.json").read_text())["sessions"].values()), 1)
        manifest = [json.loads(line) for line in (self.workspace / ".agent-trash/manifest.jsonl").read_text().splitlines()]
        self.assertEqual(sum(row["type"] == "tx-start" for row in manifest), 1)
        fresh.close()
        self.assertIsNotNone(fresh.child.poll())
        self.assertFalse(fresh.reader.is_alive())

    def test_client_deadline_does_not_rollback_or_retry_committed_delete(self):
        request_id, txid = self.committed()
        with self.assertRaises(TimeoutError):
            self.connection.receive(request_id, timeout=0.05)
        # The client abandons its wait, without sending safe_delete again.
        self.connection.close_input()
        self.release()
        self.verify_recovery(txid)

    def test_cancel_notification_preserves_committed_transaction_and_budget(self):
        request_id, txid = self.committed()
        self.connection.send({"jsonrpc": "2.0", "method": "notifications/cancelled",
                              "params": {"requestId": request_id, "reason": "synthetic client cancellation"}})
        self.assertEqual(self.connection.request("ping", {})["result"], {})
        self.release()
        self.verify_recovery(txid)

    def test_stdin_eof_preserves_unacknowledged_transaction(self):
        _request_id, txid = self.committed()
        self.connection.close_input()
        self.release()
        self.verify_recovery(txid)

    def test_server_termination_preserves_unacknowledged_transaction(self):
        _request_id, txid = self.committed()
        self.connection.child.terminate()
        self.connection.child.wait(timeout=10)
        self.release()
        self.verify_recovery(txid)


if __name__ == "__main__":
    unittest.main()
