"""Core-backed native read worker: format, failure closure, private fixtures."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "adapters" / "dsh" / "lib" / "read_result_guard.py"


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


worker = load("dsh_read_worker", WORKER)
fixture = load("dsh_read_fixture", ROOT / "adapters" / "dsh" / "harness" / "read_fixture.py")


def request(text="PORT=8080", filename="config.ini"):
    lines = text.split("\n")
    return {"workspace": str(ROOT), "value": {
        "path": filename, "offset": 1, "totalLines": len(lines),
        "lines": [{"number": i + 1, "text": line} for i, line in enumerate(lines)]}}


class ReadRedaction(unittest.TestCase):
    def test_benign_unicode_and_empty_are_preserved(self):
        data = request("# 中文 😀\nPORT=8080\nFEATURE_ENABLED=true")
        result = worker.protect(data)
        self.assertEqual(result["decision"], "ALLOW")
        self.assertEqual(result["value"], data["value"])
        data["value"].update(lines=[], totalLines=0)
        self.assertEqual(worker.protect(data)["value"], data["value"])

    def test_fixture_tokens_and_multiline_key_removed_preserving_layout(self):
        with tempfile.TemporaryDirectory() as parent:
            root = Path(parent) / "fixture"
            prepared = fixture.prepare(root)
            self.assertEqual(prepared["model_calls"], 0)
            control_path = root.with_name(root.name + ".control.json")
            control = json.loads(control_path.read_text())
            self.assertFalse((root / "control.json").exists())
            data = request((root / "redaction.ini").read_text().rstrip("\n"), "redaction.ini")
            result = worker.protect(data)
            self.assertEqual(result["decision"], "SANITIZE")
            self.assertEqual(result["redactions"], 2)
            serialized = json.dumps(result, ensure_ascii=False)
            for value in control["protected_values"]:
                self.assertNotIn(value, serialized)
            self.assertIn(control["ordinary_marker"], serialized)
            self.assertIn("ghp_<REDACTED>", serialized)
            self.assertIn("PORT=8080", serialized)
            self.assertIn("FEATURE_ENABLED=true", serialized)
            self.assertEqual([line["number"] for line in result["value"]["lines"]],
                             [line["number"] for line in data["value"]["lines"]])
            self.assertEqual(worker.protect(dict(data, value=result["value"]))["decision"], "ALLOW")
            if os.name == "posix":
                self.assertEqual(root.stat().st_mode & 0o777, 0o700)
                self.assertEqual(control_path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                fixture.prepare(root)

    def test_absolute_host_path_is_sanitized(self):
        result = worker.protect(request(filename="/home/private-read-owner/config.ini"))
        self.assertEqual(result["decision"], "SANITIZE")
        self.assertNotIn("private-read-owner", json.dumps(result))

    def test_uri_password_uses_shared_core_and_preserves_config(self):
        password = "Review" + "72"
        uri = "postgresql" + "://" + "account:" + password + "@db.example.invalid/app"
        data = request("DSN=" + uri + "\nPORT=8080")
        result = worker.protect(data)
        self.assertEqual(result["decision"], "SANITIZE")
        self.assertNotIn(password, json.dumps(result))
        self.assertIn("account:<REDACTED>@db.example.invalid", result["value"]["lines"][0]["text"])
        self.assertEqual(result["value"]["lines"][1]["text"], "PORT=8080")
        self.assertEqual(worker.protect(dict(data, value=result["value"]))["decision"], "ALLOW")

    def test_new_boundaries_preserve_layout_and_remove_full_tokens(self):
        body = "Q7mN4pR2tV" + "xxx" + ("H8kL3bD6sF9wZ5aC0uE1" * 2)[:23]
        token = ("ghs_" + "app-ID42_" + "eyJhbGciOiJSUzI1NiJ9" + "."
                 + "eyJzdWIiOiJzeW50aGV0aWMifQ" + "." + "Q7mN4pR2tVH8kL3bD6sF9" * 32)
        data = request("令牌" + "ghp_" + body + "结束\nAPP=" + token + "\nPORT=8080")
        result = worker.protect(data)
        self.assertEqual(result["decision"], "SANITIZE")
        self.assertEqual(result["redactions"], 2)
        self.assertNotIn(body, json.dumps(result))
        self.assertNotIn(token, json.dumps(result))
        lines = result["value"]["lines"]
        self.assertEqual([line["number"] for line in lines], [1, 2, 3])
        self.assertEqual([line["text"] for line in lines],
                         ["令牌ghp_<REDACTED>结束", "APP=ghs_<REDACTED>", "PORT=8080"])
        self.assertEqual(worker.protect(dict(data, value=result["value"]))["decision"], "ALLOW")

    def test_name_boundaries_preserve_benign_and_withhold_secret_reference(self):
        data = request("echo ${MONKEY} ${API_KEYBOARD} ${TOKEN_COUNT}\n"
                       "throw new TypeError(`${name}: missing argument ${key}`)\nPORT=8080")
        self.assertEqual(worker.protect(data)["value"], data["value"])
        for name in ("TOKEN", "PASSWORD", "API_KEY", "KEY", "apiKey"):
            data = request("PORT=8080\necho ${" + name + "}")
            result = worker.protect(data)
            self.assertEqual(result, worker.blocked("READ_RESULT_POLICY"))
            self.assertNotIn("PORT=8080", json.dumps(result))

    def test_partial_windows_are_blocked(self):
        for changes in [{"totalLines": 2}, {"offset": 2, "lines": [{"number": 2, "text": "data"}], "totalLines": 2}]:
            data = request()
            data["value"].update(changes)
            self.assertEqual(worker.protect(data), worker.blocked("READ_RESULT_INCOMPLETE"))

    def test_native_line_truncation_is_blocked(self):
        self.assertEqual(worker.protect(request("text... (line truncated to 10000 chars)")),
                         worker.blocked("READ_RESULT_INCOMPLETE"))

    def test_malformed_values_and_surrogates_are_blocked(self):
        for field, value in [("offset", True), ("offset", -1), ("totalLines", 1.0),
                             ("totalLines", 2 ** 53), ("path", "bad\npath"),
                             ("path", "\ud800"), ("lines", [{"number": 1, "text": "\ud800"}]),
                             ("lines", [{"number": 1, "text": "a\nb"}]),
                             ("lines", [{"number": 2, "text": "data"}])]:
            with self.subTest(field=field, value=repr(value)):
                data = request()
                data["value"][field] = value
                self.assertEqual(worker.protect(data)["code"], "READ_RESULT_SCHEMA")
        data = request()
        data["value"]["privateExtra"] = "never echo"
        self.assertEqual(worker.protect(data)["code"], "READ_RESULT_SCHEMA")

    def test_ask_and_block_are_not_accepted_as_sanitized(self):
        for decision in ("ASK", "BLOCK"):
            with patch.object(worker, "sanitize_text", return_value={"decision": decision, "plan": [], "text": "raw"}):
                self.assertEqual(worker.protect(request()), worker.blocked("READ_RESULT_POLICY"))

    def test_scanner_exception_does_not_echo_diagnostics(self):
        with patch.object(worker, "sanitize_text", side_effect=RuntimeError("PRIVATE_SCAN_DIAGNOSTIC")):
            result = worker.protect(request("PRIVATE_PAYLOAD"))
        self.assertEqual(result, worker.blocked("READ_RESULT_SCAN_FAILED"))

    def test_core_scan_size_failure_blocks(self):
        with patch.dict(os.environ, {"AGENT_GUARD_EXFIL_MAX_BYTES": "1"}):
            self.assertEqual(worker.protect(request()), worker.blocked("READ_RESULT_SCAN_FAILED"))

    def test_worker_ipc_bounds_encoding_and_json_fail_closed(self):
        for payload, code in [(b"x" * (worker.MAX_INPUT_BYTES + 1), "READ_RESULT_SIZE"),
                              (b"\xff", "READ_RESULT_SCAN_FAILED"),
                              (b'{"workspace":1,"workspace":2}', "READ_RESULT_SCAN_FAILED"),
                              (b'{"value":NaN}', "READ_RESULT_SCAN_FAILED"),
                              (b'{"private":"SECRET_DIAGNOSTIC"', "READ_RESULT_SCAN_FAILED")]:
            result = subprocess.run([sys.executable, "-I", str(WORKER)], input=payload,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stderr, b"")
            self.assertEqual(json.loads(result.stdout), worker.blocked(code))

    def test_worker_real_ipc_uses_same_core_policy(self):
        data = request("# 普通中文\nPORT=8080")
        result = subprocess.run([sys.executable, "-I", str(WORKER)],
                                input=json.dumps(data).encode("utf-8"), stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=15)
        self.assertEqual(json.loads(result.stdout)["value"], data["value"])
        self.assertEqual(result.stderr, b"")


if __name__ == "__main__":
    unittest.main()
