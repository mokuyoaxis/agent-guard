"""MCP doctor: real local services, explicit failure boundaries and privacy."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

from adapters.mcp import doctor as mcp_doctor


ROOT = Path(__file__).resolve().parents[1]
MCP_PYTHON = os.environ.get("AGENT_GUARD_MCP_PYTHON") or sys.executable
SDK_AVAILABLE = bool(os.environ.get("AGENT_GUARD_MCP_PYTHON") or importlib.util.find_spec("mcp"))


class McpDoctorInputTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="guard-mcp-doctor-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_missing_workspace_and_root_fail_before_starting_a_process(self):
        for workspace in (self.root / "missing", self.root.anchor):
            with self.subTest(workspace=str(workspace)):
                with mock.patch.object(mcp_doctor.subprocess, "run") as run:
                    result = mcp_doctor.diagnose(sys.executable, workspace)
                self.assertEqual(result["code"], "MCP_DOCTOR_WORKSPACE")
                run.assert_not_called()

    def test_missing_server_is_a_setup_failure_without_private_path_output(self):
        result = mcp_doctor.diagnose(sys.executable, self.root,
                                     server=self.root / "private-marker-missing.py")
        self.assertEqual(result["code"], "MCP_DOCTOR_SERVER")
        self.assertNotIn("private-marker", json.dumps(result))

    def test_missing_python_is_reported_without_shell_execution(self):
        result = mcp_doctor.diagnose(str(self.root / "missing-python; private-marker"), self.root)
        self.assertEqual(result["code"], "MCP_DOCTOR_PYTHON")
        self.assertNotIn("private-marker", json.dumps(result))

    def test_deadline_rejects_boolean_nonfinite_and_out_of_range_values(self):
        for timeout in (True, 0, 4, 121, float("nan"), float("inf"), "30"):
            with self.subTest(timeout=timeout):
                with self.assertRaises(ValueError):
                    mcp_doctor.diagnose(sys.executable, self.root, timeout=timeout)

    def test_sdk_failures_have_distinct_codes_without_raw_child_output(self):
        cases = {10: "MCP_DOCTOR_SDK_MISSING", 11: "MCP_DOCTOR_SDK_VERSION",
                 12: "MCP_DOCTOR_SDK_IMPORT", 1: "MCP_DOCTOR_SDK_CHECK"}
        for exit_code, expected in cases.items():
            with self.subTest(exit_code=exit_code):
                with mock.patch.object(mcp_doctor.subprocess, "run", return_value=
                                       subprocess.CompletedProcess([], exit_code)):
                    result = mcp_doctor.diagnose(sys.executable, self.root)
                self.assertEqual(result["code"], expected)
                self.assertEqual(result["stage"], "sdk")
                self.assertEqual(result["host_interception"], "UNVERIFIED")

    def test_sdk_check_timeout_is_not_retried(self):
        with mock.patch.object(mcp_doctor.subprocess, "run", side_effect=
                               subprocess.TimeoutExpired(["private-marker"], 1)) as run:
            result = mcp_doctor.diagnose(sys.executable, self.root)
        self.assertEqual(result["code"], "MCP_DOCTOR_TIMEOUT")
        self.assertEqual(result["stage"], "sdk")
        self.assertEqual(run.call_count, 1)
        self.assertNotIn("private-marker", json.dumps(result))

    def test_help_is_available_without_site_packages(self):
        result = subprocess.run([sys.executable, "-B", "-S", str(ROOT / "doctor.py"),
                                 "mcp", "--help"], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--workspace", result.stdout)
        self.assertIn("--python", result.stdout)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_cli_failure_json_and_exit_code_are_independent_of_other_doctor_flags(self):
        result = subprocess.run([sys.executable, "-B", str(ROOT / "doctor.py"), "mcp",
                                 "--workspace", str(self.root), "--python",
                                 str(self.root / "private-marker-missing"), "--json"],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 1, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["code"], "MCP_DOCTOR_PYTHON")
        self.assertNotIn("private-marker", result.stdout + result.stderr)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_empty_python_is_a_cli_usage_error_without_a_traceback(self):
        result = subprocess.run([sys.executable, "-B", str(ROOT / "doctor.py"), "mcp",
                                 "--workspace", str(self.root), "--python", "", "--json"],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("--python must name a nonempty interpreter", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(list(self.root.iterdir()), [])


class McpDoctorBoundaryTests(unittest.TestCase):
    """Synthetic stdio peers exercise actual transport, not SDK acceptance."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="guard-mcp-doctor-peer-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.requests = self.root / "requests.jsonl"
        self.version = json.loads((ROOT / "package.json").read_text())["version"]

    def peer(self, mode):
        path = self.root / (mode + ".py")
        path.write_text(textwrap.dedent("""\
            import json, pathlib, sys, time
            mode = MODE
            version = VERSION
            workspace = sys.argv[-1]
            requests = pathlib.Path(REQUESTS)
            names = ["safe_delete", "restore", "transactions", "status", "gc_plan"]
            for line in sys.stdin:
                request = json.loads(line)
                with requests.open("a") as stream:
                    stream.write(json.dumps(request) + "\\n")
                if "id" not in request:
                    continue
                method = request["method"]
                if mode == "eof":
                    sys.exit(1)
                if mode == "timeout":
                    time.sleep(20)
                if mode == "bad-json":
                    print("PRIVATE-MARKER-not-json", flush=True)
                    continue
                if mode == "rpc-error":
                    print(json.dumps({"jsonrpc": "2.0", "id": request["id"],
                                      "error": {"code": -32000, "message": "PRIVATE-MARKER"}}), flush=True)
                    continue
                if mode == "stderr":
                    print("PRIVATE-MARKER-stderr", file=sys.stderr, flush=True)
                if method == "initialize":
                    result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                              "serverInfo": {"name": "agent-guard", "version": version}}
                    if mode == "identity":
                        result["serverInfo"]["version"] = "PRIVATE-MARKER-version"
                    if mode == "protocol":
                        result["protocolVersion"] = "PRIVATE-MARKER-protocol"
                elif method == "tools/list":
                    result = {"tools": [{"name": name, "annotations": {
                        "readOnlyHint": name in ("status", "transactions", "gc_plan")}}
                        for name in names]}
                    if mode == "tools":
                        result["tools"][0]["name"] = "PRIVATE-MARKER-tool"
                    if mode == "annotation":
                        result["tools"][-1]["annotations"]["readOnlyHint"] = "true"
                else:
                    name = request["params"]["name"]
                    if name == "status":
                        body = {"workspace": workspace, "trash_root": str(pathlib.Path(workspace) / ".agent-trash"),
                                "delete_admission": {}, "recent_audit": ["PRIVATE-MARKER-audit"]}
                        if mode == "binding":
                            body["workspace"] = "PRIVATE-MARKER-workspace"
                        if mode == "admission":
                            body["delete_admission"] = {"status": "BLOCKED", "reason": "PRIVATE-MARKER-admission"}
                    elif name == "transactions":
                        body = {"transactions": [{"txid": "PRIVATE-MARKER-txid"}]}
                    else:
                        body = {"mode": "plan", "eligible": []}
                    structured = {"tool": name, "ok": True, "exit_code": 0, "result": body}
                    result = {"isError": False, "structuredContent": structured,
                              "content": [{"type": "text", "text": "PRIVATE-MARKER-text"}]}
                    if mode == "query":
                        result["isError"] = True
                    if mode == "invalid-query":
                        structured["exit_code"] = False
                print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
            """).replace("MODE", repr(mode)).replace("VERSION", repr(self.version))
            .replace("REQUESTS", repr(str(self.requests))), encoding="utf-8")
        return path

    def diagnose_peer(self, mode, **kwargs):
        with mock.patch.object(mcp_doctor, "_check_sdk", return_value=None):
            return mcp_doctor.diagnose(sys.executable, self.workspace,
                                       server=self.peer(mode), **kwargs)

    def test_protocol_failure_categories_do_not_echo_peer_content(self):
        cases = {"eof": "MCP_DOCTOR_EOF", "bad-json": "MCP_DOCTOR_PROTOCOL",
                 "rpc-error": "MCP_DOCTOR_PROTOCOL", "identity": "MCP_DOCTOR_IDENTITY",
                 "protocol": "MCP_DOCTOR_PROTOCOL", "tools": "MCP_DOCTOR_TOOLS",
                 "annotation": "MCP_DOCTOR_TOOLS", "binding": "MCP_DOCTOR_BINDING",
                 "query": "MCP_DOCTOR_QUERY", "invalid-query": "MCP_DOCTOR_QUERY"}
        for mode, expected in cases.items():
            with self.subTest(mode=mode):
                result = self.diagnose_peer(mode)
                self.assertEqual(result["status"], "FAIL", result)
                self.assertEqual(result["code"], expected, result)
                self.assertNotIn("PRIVATE-MARKER", json.dumps(result) + mcp_doctor.render(result))

    def test_only_three_read_only_calls_are_sent_and_private_results_are_projected_out(self):
        result = self.diagnose_peer("stderr")
        self.assertEqual(result["status"], "PASS", result)
        self.assertGreater(result["diagnostic_stderr_bytes"], 0)
        requests = [json.loads(line) for line in self.requests.read_text().splitlines()]
        calls = [row["params"] for row in requests if row["method"] == "tools/call"]
        self.assertEqual(calls, [{"name": "status", "arguments": {"tail": 0}},
                                 {"name": "transactions", "arguments": {}},
                                 {"name": "gc_plan", "arguments": {}}])
        self.assertEqual(result["host_connection"], "UNVERIFIED")
        self.assertEqual(result["model_delivery"], "UNVERIFIED")
        self.assertNotIn("PRIVATE-MARKER", json.dumps(result) + mcp_doctor.render(result))
        self.assertEqual(list(self.workspace.iterdir()), [])

    def test_blocked_admission_does_not_hide_a_working_inspection_connection(self):
        result = self.diagnose_peer("admission")
        self.assertEqual(result["status"], "PASS", result)
        self.assertEqual(result["deletion_admission"], "BLOCKED")
        self.assertIn("DELETION_ADMISSION: BLOCKED", mcp_doctor.render(result))
        self.assertNotIn("PRIVATE-MARKER", json.dumps(result))

    def test_real_service_deadline_is_bounded_and_does_not_retry(self):
        result = self.diagnose_peer("timeout", timeout=5)
        self.assertEqual(result["code"], "MCP_DOCTOR_TIMEOUT", result)
        self.assertEqual(result["stage"], "initialize")
        requests = [json.loads(line) for line in self.requests.read_text().splitlines()]
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["method"], "initialize")


@unittest.skipUnless(SDK_AVAILABLE, "optional MCP SDK required for native doctor protocol tests")
class McpDoctorProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="guard-mcp-doctor-native-")
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name).resolve()
        (self.workspace / "valuable.txt").write_bytes(b"Synthetic valuable original.\n")

    def inventory(self):
        return {str(path.relative_to(self.workspace)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in self.workspace.rglob("*") if path.is_file()}

    def test_real_service_binding_five_tools_and_read_only_queries(self):
        before = self.inventory()
        result = mcp_doctor.diagnose(MCP_PYTHON, self.workspace)
        self.assertEqual(result["status"], "PASS", result)
        self.assertEqual(result["server"]["version"], json.loads((ROOT / "package.json").read_text())["version"])
        self.assertEqual(result["sdk_version"], "2.3.0")
        self.assertEqual(set(result["tools"]), mcp_doctor.TOOLS)
        self.assertEqual(result["deletion_admission"], "READABLE")
        self.assertEqual(self.inventory(), before)
        self.assertFalse((self.workspace / ".agent-trash").exists())
        self.assertNotIn(str(self.workspace), json.dumps(result))

    def test_real_invalid_admission_remains_visible_without_changing_state(self):
        directory = self.workspace / ".agent-guard"
        directory.mkdir()
        (directory / "delete-policy.json").write_text("{invalid", encoding="utf-8")
        before = self.inventory()
        result = mcp_doctor.diagnose(MCP_PYTHON, self.workspace)
        self.assertEqual(result["status"], "PASS", result)
        self.assertEqual(result["deletion_admission"], "BLOCKED")
        self.assertEqual(self.inventory(), before)
        self.assertFalse((self.workspace / ".agent-trash").exists())

    def test_real_cli_json_and_interpreter_symlink_work(self):
        before = self.inventory()
        proc = subprocess.run([sys.executable, "-B", str(ROOT / "doctor.py"), "mcp",
                               "--python", MCP_PYTHON, "--workspace", str(self.workspace), "--json"],
                              capture_output=True, text=True, timeout=40)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertEqual(result["status"], "PASS", result)
        self.assertEqual(result["host_interception"], "UNVERIFIED")
        self.assertEqual(self.inventory(), before)

    def test_real_cli_text_keeps_blocked_admission_visible_after_successful_connection(self):
        directory = self.workspace / ".agent-guard"
        directory.mkdir()
        (directory / "delete-policy.json").write_text("{invalid", encoding="utf-8")
        before = self.inventory()
        proc = subprocess.run([sys.executable, "-B", str(ROOT / "doctor.py"), "mcp",
                               "--python", MCP_PYTHON, "--workspace", str(self.workspace)],
                              capture_output=True, text=True, timeout=40)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("PASS: MCP_DOCTOR_READY", proc.stdout)
        self.assertIn("DELETION_ADMISSION: BLOCKED", proc.stdout)
        self.assertIn("HOST_CONNECTION: UNVERIFIED", proc.stdout)
        self.assertNotIn(str(self.workspace), proc.stdout + proc.stderr)
        self.assertEqual(self.inventory(), before)
        self.assertFalse((self.workspace / ".agent-trash").exists())


if __name__ == "__main__":
    unittest.main()
