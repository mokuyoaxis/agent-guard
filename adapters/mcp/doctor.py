"""Read-only diagnostics for one explicitly selected local MCP service.

Only the selected child interpreter needs the optional SDK. This module is
stdlib-only so the ordinary doctor and its help remain usable without it.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time


ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "adapters/mcp/server.py"
SDK_VERSION = "2.3.0"
PROTOCOL_VERSION = "2025-06-18"
TOOLS = frozenset(("safe_delete", "restore", "transactions", "status", "gc_plan", "cleanup_discover", "cleanup_measure",
                   "cleanup_plan", "cleanup_execute", "cleanup_task_status",
                   "config_safe_view", "exfil_check", "exfil_sanitize"))
READ_ONLY_PROBES = {"status": {"tail": 0}, "transactions": {}, "gc_plan": {},
                    "exfil_check": {"text": "agent-guard doctor probe", "channel": "file-write"}}
READ_ONLY_TOOLS = frozenset(READ_ONLY_PROBES) | {"cleanup_discover", "cleanup_measure", "cleanup_task_status",
                                               "config_safe_view", "exfil_sanitize"}
MAX_MESSAGE_BYTES = 2 * 1024 * 1024

PROBLEMS = {
    "MCP_DOCTOR_WORKSPACE": ("Select an existing non-root workspace directory.",
                             "Check the requested workspace path before reconnecting."),
    "MCP_DOCTOR_TRASH_ROOT": ("The selected quarantine directory is invalid.",
                              "Use an absolute directory path that does not contain the workspace."),
    "MCP_DOCTOR_SERVER": ("The selected server script is unavailable.",
                          "Select the reviewed agent-guard adapters/mcp/server.py file."),
    "MCP_DOCTOR_PYTHON": ("The selected Python executable could not start.",
                          "Check the selected interpreter path and execution permission."),
    "MCP_DOCTOR_SDK_MISSING": ("The optional MCP SDK is not installed in the selected interpreter.",
                               "Use a separate environment with adapters/mcp/requirements.txt."),
    "MCP_DOCTOR_SDK_VERSION": ("The selected interpreter has an unsupported MCP SDK version.",
                               "Use the pinned SDK from adapters/mcp/requirements.txt."),
    "MCP_DOCTOR_SDK_IMPORT": ("The selected interpreter could not import the MCP SDK dependencies.",
                              "Check that the selected SDK environment is complete and runnable."),
    "MCP_DOCTOR_SDK_CHECK": ("The selected interpreter did not complete the SDK check.",
                             "Check the interpreter and its installed environment directly."),
    "MCP_DOCTOR_STARTUP": ("The selected service could not start.",
                           "Check the reviewed server script and selected interpreter."),
    "MCP_DOCTOR_TIMEOUT": ("The diagnostic deadline expired.",
                           "Inspect the failed stage and service startup; reconnect explicitly after investigation."),
    "MCP_DOCTOR_EOF": ("The service closed its protocol output before completing the check.",
                       "Inspect the selected service startup and optional SDK environment."),
    "MCP_DOCTOR_PROTOCOL": ("The service returned an invalid or unsupported protocol response.",
                            "Use the reviewed agent-guard stdio service; stdout must contain protocol messages only."),
    "MCP_DOCTOR_IDENTITY": ("The service identity or version does not match this installation.",
                            "Select a server script from the same agent-guard version as this doctor."),
    "MCP_DOCTOR_TOOLS": ("The service tool set or read-only annotations do not match this installation's contract.",
                         "Check the selected service version and tool definitions."),
    "MCP_DOCTOR_QUERY": ("A read-only tool returned an unsuccessful or invalid result.",
                         "Inspect the failed tool and local recovery state before any mutation."),
    "MCP_DOCTOR_BINDING": ("The service status does not match the requested workspace and quarantine.",
                           "Correct the trusted service workspace binding before using its tools."),
}


class _Failure(Exception):
    def __init__(self, code):
        self.code = code


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise _Failure("MCP_DOCTOR_TIMEOUT")
    return remaining


def _environment():
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("AGENT_GUARD_", "GIT_"))
           and key not in ("PYTHONPATH", "PYTHONHOME")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _check_sdk(python, deadline, env, diagnostics):
    # Fixed exit codes avoid exposing import exceptions, paths or environment
    # contents. This is a trusted interpreter, not a sandbox for hostile code.
    code = (
        "import importlib.metadata, sys\n"
        "try: version = importlib.metadata.version('mcp')\n"
        "except importlib.metadata.PackageNotFoundError: sys.exit(10)\n"
        "if version != '2.3.0': sys.exit(11)\n"
        "try: import mcp.server, mcp.server.stdio, mcp_types, anyio\n"
        "except (ImportError, OSError): sys.exit(12)\n"
    )
    try:
        child = subprocess.run([python, "-B", "-c", code], cwd=ROOT, env=env,
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=diagnostics, timeout=_remaining(deadline), shell=False)
    except subprocess.TimeoutExpired:
        raise _Failure("MCP_DOCTOR_TIMEOUT") from None
    except OSError:
        raise _Failure("MCP_DOCTOR_PYTHON") from None
    if child.returncode:
        raise _Failure({10: "MCP_DOCTOR_SDK_MISSING", 11: "MCP_DOCTOR_SDK_VERSION",
                        12: "MCP_DOCTOR_SDK_IMPORT"}.get(child.returncode, "MCP_DOCTOR_SDK_CHECK"))


class _Connection:
    def __init__(self, python, server, workspace, env, deadline, diagnostics, *, trash_root=None,
                 approval_channel="terminal"):
        self.deadline = deadline
        self.counter = 0
        self.messages = queue.Queue(maxsize=8)
        self.stopped = threading.Event()
        try:
            command = [python, "-B", str(server)]
            if trash_root is not None:
                command += ["--trash-root", trash_root]
            if approval_channel != "terminal":
                command += ["--approval-channel", approval_channel]
            command += ["--workspace", str(workspace)]
            self.child = subprocess.Popen(command,
                                          cwd=workspace, env=env, stdin=subprocess.PIPE,
                                          stdout=subprocess.PIPE, stderr=diagnostics, shell=False)
        except OSError:
            raise _Failure("MCP_DOCTOR_STARTUP") from None

        def enqueue(value):
            while not self.stopped.is_set():
                try:
                    self.messages.put(value, timeout=0.1)
                    return
                except queue.Full:
                    continue

        def read():
            while not self.stopped.is_set():
                line = self.child.stdout.readline(MAX_MESSAGE_BYTES + 1)
                if not line:
                    enqueue(_Failure("MCP_DOCTOR_EOF"))
                    return
                if len(line) > MAX_MESSAGE_BYTES or not line.endswith(b"\n"):
                    enqueue(_Failure("MCP_DOCTOR_PROTOCOL"))
                    return
                try:
                    value = json.loads(line.decode("utf-8"))
                except (UnicodeError, ValueError, RecursionError):
                    enqueue(_Failure("MCP_DOCTOR_PROTOCOL"))
                    return
                enqueue(value)

        self.reader = threading.Thread(target=read, daemon=True)
        self.reader.start()

    def send(self, value):
        _remaining(self.deadline)
        try:
            self.child.stdin.write((json.dumps(value) + "\n").encode("utf-8"))
            self.child.stdin.flush()
        except (OSError, ValueError):
            raise _Failure("MCP_DOCTOR_EOF") from None

    def request(self, method, params):
        self.counter += 1
        self.send({"jsonrpc": "2.0", "id": self.counter, "method": method, "params": params})
        # The selected service does not need client-side tools, sampling or
        # approval. Notifications may occur but cannot extend the deadline.
        for _ in range(32):
            try:
                value = self.messages.get(timeout=_remaining(self.deadline))
            except queue.Empty:
                raise _Failure("MCP_DOCTOR_TIMEOUT") from None
            if isinstance(value, _Failure):
                raise value
            if not isinstance(value, dict) or value.get("jsonrpc") != "2.0":
                raise _Failure("MCP_DOCTOR_PROTOCOL")
            if "id" not in value and isinstance(value.get("method"), str):
                if value["method"].startswith("notifications/"):
                    continue
            if type(value.get("id")) is not int or value["id"] != self.counter or "error" in value:
                raise _Failure("MCP_DOCTOR_PROTOCOL")
            if "method" in value or not isinstance(value.get("result"), dict):
                raise _Failure("MCP_DOCTOR_PROTOCOL")
            return value["result"]
        raise _Failure("MCP_DOCTOR_PROTOCOL")

    def close(self):
        self.stopped.set()
        try:
            self.child.stdin.close()
        except OSError:
            pass
        try:
            self.child.wait(timeout=1)
        except subprocess.TimeoutExpired:
            self.child.terminate()
            try:
                self.child.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.child.kill()
                self.child.wait(timeout=1)
        self.child.stdout.close()
        self.reader.join(timeout=1)


def _tool_result(value, name):
    content = value.get("structuredContent")
    if (value.get("isError", False) is not False or not isinstance(content, dict)
            or content.get("tool") != name or content.get("ok") is not True
            or type(content.get("exit_code")) is not int or content["exit_code"] != 0
            or not isinstance(content.get("result"), dict)):
        raise _Failure("MCP_DOCTOR_QUERY")
    return content["result"]


def diagnose(python, workspace, *, server=None, trash_root=None, timeout=30, approval_channel="terminal"):
    """Check a trusted local service, without returning its private responses.

    ``timeout`` is the total diagnostic deadline (5..120 seconds); closing
    this invocation's own service can add three seconds of process waits
    and one second waiting for its reader thread.
    Python paths retain their symlink spelling so virtual environments work.
    """
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 5 <= timeout <= 120:
        raise ValueError("MCP doctor timeout must be from 5 to 120 seconds")
    if approval_channel not in ("terminal", "codex"):
        raise ValueError("MCP approval channel must be terminal or codex")
    expected_tools = TOOLS | ({"cleanup_request_approval"} if approval_channel == "codex" else set())
    python = os.fspath(python)
    if not python or "\0" in python:
        raise ValueError("MCP doctor needs a nonempty interpreter")
    python = os.path.expanduser(python)
    if os.path.dirname(python):
        python = os.path.abspath(python)
    else:
        python = shutil.which(python) or python
    expected_version = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"]
    report = {"schema_version": 1, "harness": "mcp", "status": "FAIL", "stage": "workspace",
              "checks": [], "host_connection": "UNVERIFIED", "model_delivery": "UNVERIFIED",
              "host_interception": "UNVERIFIED", "approval_channel": approval_channel,
              "host_confirmation": "UNVERIFIED"}
    stage = "workspace"
    connection = None
    deadline = time.monotonic() + timeout
    with tempfile.TemporaryFile() as diagnostics:
        try:
            try:
                selected = Path(workspace).expanduser().resolve(strict=True)
                valid = selected.is_dir() and selected.parent != selected
            except (OSError, ValueError):
                valid = False
            if not valid:
                raise _Failure("MCP_DOCTOR_WORKSPACE")
            report["checks"].append({"stage": stage, "status": "PASS"})
            from adapters.mcp.operations import resolve_trash_root
            try:
                selected_trash = resolve_trash_root(str(selected), trash_root)
            except (OSError, ValueError):
                stage = "quarantine"
                raise _Failure("MCP_DOCTOR_TRASH_ROOT") from None
            if trash_root is not None:
                report["checks"].append({"stage": "quarantine", "status": "PASS"})
            stage = "server"
            try:
                selected_server = Path(server if server is not None else SERVER).expanduser().absolute()
                valid = selected_server.is_file()
            except (OSError, ValueError):
                valid = False
            if not valid:
                raise _Failure("MCP_DOCTOR_SERVER")
            report["checks"].append({"stage": stage, "status": "PASS"})
            env = _environment()
            stage = "sdk"
            _check_sdk(python, deadline, env, diagnostics)
            report["sdk_version"] = SDK_VERSION
            report["checks"].append({"stage": stage, "status": "PASS"})
            stage = "initialize"
            connection = _Connection(python, selected_server, selected, env, deadline, diagnostics,
                                     trash_root=selected_trash if trash_root is not None else None,
                                     approval_channel=approval_channel)
            initialized = connection.request("initialize", {"protocolVersion": PROTOCOL_VERSION,
                "capabilities": {}, "clientInfo": {"name": "agent-guard-doctor", "version": expected_version}})
            identity = initialized.get("serverInfo")
            if (not isinstance(identity, dict) or identity.get("name") != "agent-guard"
                    or identity.get("version") != expected_version):
                raise _Failure("MCP_DOCTOR_IDENTITY")
            if initialized.get("protocolVersion") != PROTOCOL_VERSION:
                raise _Failure("MCP_DOCTOR_PROTOCOL")
            connection.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            report["server"] = {"name": "agent-guard", "version": expected_version,
                                "protocol_version": PROTOCOL_VERSION}
            report["checks"].append({"stage": stage, "status": "PASS"})
            stage = "tools"
            listing = connection.request("tools/list", {})
            tools = listing.get("tools")
            if (not isinstance(tools, list) or len(tools) != len(expected_tools)
                    or any(not isinstance(tool, dict) or not isinstance(tool.get("name"), str) for tool in tools)
                    or {tool["name"] for tool in tools} != expected_tools or listing.get("nextCursor") is not None):
                raise _Failure("MCP_DOCTOR_TOOLS")
            for tool in tools:
                annotations = tool.get("annotations")
                if (not isinstance(annotations, dict) or
                        annotations.get("readOnlyHint") is not (tool["name"] in READ_ONLY_TOOLS)):
                    raise _Failure("MCP_DOCTOR_TOOLS")
            report["tools"] = sorted(expected_tools)
            report["checks"].append({"stage": stage, "status": "PASS"})
            for name, arguments in READ_ONLY_PROBES.items():
                stage = name
                result = _tool_result(connection.request("tools/call", {
                    "name": name, "arguments": arguments}), name)
                if name == "status":
                    if (result.get("workspace") != str(selected)
                            or result.get("trash_root") != selected_trash):
                        raise _Failure("MCP_DOCTOR_BINDING")
                    admission = result.get("delete_admission")
                    if not isinstance(admission, dict):
                        raise _Failure("MCP_DOCTOR_QUERY")
                    report["deletion_admission"] = "BLOCKED" if admission.get("status") == "BLOCKED" else "READABLE"
                elif name == "transactions" and not isinstance(result.get("transactions"), list):
                    raise _Failure("MCP_DOCTOR_QUERY")
                elif name == "gc_plan" and (result.get("mode") != "plan" or not isinstance(result.get("eligible"), list)):
                    raise _Failure("MCP_DOCTOR_QUERY")
                elif name == "exfil_check" and (result.get("decision") != "ALLOW"
                                               or result.get("audit") != {"enabled": False, "recorded": False}):
                    raise _Failure("MCP_DOCTOR_QUERY")
                report["checks"].append({"stage": stage, "status": "PASS"})
            report.update(status="PASS", stage="complete", code="MCP_DOCTOR_READY",
                          message="The selected local service passed handshake, binding and read-only tool checks.",
                          guidance="Inspect the actual client's MCP connection and permissions before using its tools.")
        except _Failure as failure:
            message, guidance = PROBLEMS[failure.code]
            report.update(stage=stage, code=failure.code, message=message, guidance=guidance)
            report["checks"].append({"stage": stage, "status": "FAIL", "code": failure.code})
        finally:
            if connection is not None:
                connection.close()
            diagnostics.flush()
            report["diagnostic_stderr_bytes"] = diagnostics.tell()
    return report


def render(report):
    """Render only fixed report fields; private MCP tool content is absent."""
    lines = [report["status"] + ": " + report["code"], report["message"]]
    lines.extend("  " + check["stage"] + ": " + check["status"] for check in report["checks"])
    if report.get("deletion_admission") == "BLOCKED":
        lines.append("DELETION_ADMISSION: BLOCKED (connection checks do not grant deletion permission)")
    lines.extend([report["guidance"],
                  "HOST_CONNECTION: UNVERIFIED; MODEL_DELIVERY: UNVERIFIED; HOST_INTERCEPTION: UNVERIFIED"])
    return "\n".join(lines) + "\n"
