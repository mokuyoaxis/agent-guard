"""Real stdio protocol tests; SDK remains optional for the default Core suite."""
import importlib.util
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import unittest


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "adapters/mcp/server.py"
MCP_PYTHON = os.environ.get("AGENT_GUARD_MCP_PYTHON") or sys.executable
SDK_AVAILABLE = bool(os.environ.get("AGENT_GUARD_MCP_PYTHON") or importlib.util.find_spec("mcp"))


class Connection:
    def __init__(self, workspace):
        self.stderr = tempfile.TemporaryFile()
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        self.child = subprocess.Popen([MCP_PYTHON, "-B", str(SERVER), "--workspace", str(workspace)],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=self.stderr, env=env, text=True, encoding="utf-8")
        self.received = queue.Queue()
        self.counter = 0
        self.closed = False

        def read():
            for line in self.child.stdout:
                try:
                    self.received.put(json.loads(line))
                except ValueError:
                    self.received.put(AssertionError("non-protocol stdout: " + repr(line)))
            self.received.put(EOFError("server closed protocol output"))

        self.reader = threading.Thread(target=read, daemon=True)
        self.reader.start()
        result = self.request("initialize", {"protocolVersion": "2025-06-18",
                               "capabilities": {}, "clientInfo": {"name": "synthetic-test", "version": "1"}})
        self.initialized = result["result"]
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def send(self, value):
        self.child.stdin.write(json.dumps(value) + "\n")
        self.child.stdin.flush()

    def request(self, method, params):
        self.counter += 1
        self.send({"jsonrpc": "2.0", "id": self.counter, "method": method, "params": params})
        while True:
            value = self.received.get(timeout=150)
            if isinstance(value, Exception):
                self.stderr.seek(0)
                raise AssertionError(str(value) + ": " + self.stderr.read().decode("utf-8", errors="replace"))
            if value.get("id") == self.counter:
                return value

    def call(self, name, **arguments):
        return self.request("tools/call", {"name": name, "arguments": arguments})

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.child.stdin.close()
        try:
            self.child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.child.kill()
            self.child.wait(timeout=10)
        self.child.stdout.close()
        self.reader.join(timeout=2)
        self.stderr.close()


class McpBootstrapTests(unittest.TestCase):
    def test_help_needs_no_optional_sdk_or_workspace_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, "-B", "-S", str(SERVER), "--help"],
                                    cwd=directory, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("--workspace", result.stdout)
            self.assertNotIn("--transport", result.stdout)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_missing_sdk_is_clear_and_does_not_create_guard_state(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, "-B", "-S", str(SERVER), "--workspace", directory],
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, "")
            self.assertIn("SDK is optional", result.stderr)
            self.assertEqual(list(Path(directory).iterdir()), [])


@unittest.skipUnless(SDK_AVAILABLE, "optional MCP SDK required for real stdio protocol tests")
class McpProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name)
        subprocess.run(["git", "init", "-q", str(self.workspace)], check=True, capture_output=True)
        (self.workspace / ".gitignore").write_text(".agent-trash/\nnode_modules/\n")
        self.connection = None

    def start(self):
        self.connection = Connection(self.workspace)
        self.addCleanup(self.connection.close)
        return self.connection

    def config(self, **fields):
        directory = self.workspace / ".agent-guard"
        directory.mkdir(exist_ok=True)
        (directory / "delete-policy.json").write_text(json.dumps(fields))

    def content(self, reply):
        result = reply["result"]
        parsed = json.loads(result["content"][0]["text"])
        self.assertEqual(parsed, result["structuredContent"])
        self.assertEqual(result.get("isError", False), not parsed["ok"])
        return parsed

    def test_classic_handshake_discovery_and_annotations(self):
        connection = self.start()
        self.assertEqual(connection.initialized["protocolVersion"], "2025-06-18")
        self.assertEqual(connection.initialized["serverInfo"]["version"],
                         json.loads((ROOT / "package.json").read_text())["version"])
        tools = connection.request("tools/list", {})["result"]["tools"]
        self.assertEqual({row["name"] for row in tools},
                         {"safe_delete", "restore", "transactions", "status", "gc_plan"})
        for tool in tools:
            self.assertFalse(tool["inputSchema"]["additionalProperties"])
            self.assertFalse(tool["annotations"]["openWorldHint"])
            self.assertEqual(tool["annotations"]["readOnlyHint"],
                             tool["name"] in ("status", "transactions", "gc_plan"))
        self.assertTrue(next(row for row in tools if row["name"] == "safe_delete")["annotations"]["destructiveHint"])

    def test_read_only_tools_do_not_initialize_workspace_state(self):
        connection = self.start()
        before = sorted(str(path.relative_to(self.workspace)) for path in self.workspace.rglob("*"))
        for name in ("status", "transactions", "gc_plan"):
            self.assertTrue(self.content(connection.call(name))["ok"])
        after = sorted(str(path.relative_to(self.workspace)) for path in self.workspace.rglob("*"))
        self.assertEqual(before, after)

    def test_actual_delete_transactions_restore_and_gc_preview(self):
        path = self.workspace / "valuable.txt"
        original = b"synthetic recovery evidence\n"
        path.write_bytes(original)
        connection = self.start()
        deleted = self.content(connection.call("safe_delete", paths=[path.name]))
        self.assertTrue(deleted["ok"], deleted)
        self.assertFalse(path.exists())
        transactions = self.content(connection.call("transactions"))["result"]["transactions"]
        self.assertEqual(len(transactions), 1)
        self.assertEqual(transactions[0]["state"], "RESTORABLE")
        self.assertTrue(self.content(connection.call("gc_plan"))["ok"])
        restored = self.content(connection.call("restore", txid=transactions[0]["txid"]))
        self.assertTrue(restored["ok"], restored)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.content(connection.call("transactions"))["result"]["transactions"][0]["state"], "RESTORED")

    def test_budget_survives_reconnect_and_restore_does_not_refund(self):
        self.config(session_max_entries=1)
        first, second = self.workspace / "first.txt", self.workspace / "second.txt"
        first.write_text("first")
        second.write_text("second")
        connection = self.start()
        self.assertTrue(self.content(connection.call("safe_delete", paths=[first.name]))["ok"])
        txid = self.content(connection.call("transactions"))["result"]["transactions"][0]["txid"]
        connection.close()
        connection = self.start()
        denied = self.content(connection.call("safe_delete", paths=[second.name]))
        self.assertFalse(denied["ok"])
        self.assertEqual(denied["result"]["verdict"]["code"], "BLOCK_DELETE_LIMIT")
        self.assertEqual(second.read_text(), "second")
        self.assertTrue(self.content(connection.call("restore", txid=txid))["ok"])
        self.assertEqual(first.read_text(), "first")
        self.assertEqual(self.content(connection.call("status"))["result"]["delete_admission"]["used_entries"], 1)

    def test_protection_and_batch_gate_remain_blocks(self):
        protected, batch = self.workspace / "protected.txt", self.workspace / "batch.txt"
        protected.write_text("protected")
        batch.write_text("batch")
        self.config(protected_paths=[protected.name], batch_max_bytes=0)
        connection = self.start()
        for path, code in ((protected, "BLOCK_DELETE_PROTECTED"), (batch, "BLOCK_DELETE_BATCH_CONFIRM")):
            value = self.content(connection.call("safe_delete", paths=[path.name]))
            self.assertFalse(value["ok"])
            self.assertEqual(value["result"]["verdict"]["code"], code)
            self.assertTrue(path.exists())

    def test_unknown_override_and_coerced_arguments_never_mutate(self):
        path = self.workspace / "valuable.txt"
        path.write_text("preserve")
        connection = self.start()
        for name, args in (("safe_delete", {"paths": [path.name], "confirm": True}),
                           ("safe_delete", {"paths": [path.name], "workspace": "/"}),
                           ("safe_delete", {"paths": path.name}),
                           ("safe_delete", {"paths": [path.name], "dry_run": "false"}),
                           ("status", {"tail": True}), ("status", {"tail": 101}),
                           ("gc_plan", {"execute": True}), ("restore", {"txid": "x", "force": True})):
            with self.subTest(name=name, args=args):
                reply = connection.call(name, **args)
                self.assertTrue("error" in reply or reply["result"].get("isError"), reply)
                self.assertEqual(path.read_text(), "preserve")
        self.assertFalse((self.workspace / ".agent-trash").exists())

    def test_quarantine_payload_cannot_be_deleted_through_safe_delete(self):
        path = self.workspace / "valuable.txt"
        path.write_text("preserve")
        connection = self.start()
        self.assertTrue(self.content(connection.call("safe_delete", paths=[path.name]))["ok"])
        payloads = [p for p in (self.workspace / ".agent-trash").rglob("*") if p.is_file() and p.read_bytes() == b"preserve"]
        self.assertTrue(payloads)
        reply = self.content(connection.call("safe_delete", paths=[str(payloads[0])]))
        self.assertFalse(reply["ok"])
        self.assertEqual(reply["error"]["code"], "MCP_QUARANTINE_PROTECTED")
        self.assertEqual(payloads[0].read_bytes(), b"preserve")


if __name__ == "__main__":
    unittest.main()
