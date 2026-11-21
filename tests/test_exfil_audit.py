"""Observed egress audit and structured output contracts on owned fixtures."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from core import audit, exfil_audit


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/exfil-guard/scripts"
SECRET = "ghp_" + "8a7b6c5d4e3f0123456789abcdef01234567"
PASSWORD = "orchid-" + "four"


class ExfilAuditTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve()
        self.bucket = self.workspace / ".agent-trash"
        self.log = self.bucket / "audit.jsonl"
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("AGENT_GUARD_")}
        self.env["AGENT_GUARD_WORKSPACE"] = str(self.workspace)

    def invoke(self, script, text, *flags, channel="file-write"):
        return subprocess.run([sys.executable, str(SCRIPTS / script), "--channel", channel, *flags],
                              input=text, cwd=self.workspace, env=self.env, encoding="utf-8",
                              capture_output=True, timeout=25)

    def raw_records(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_defaults_do_not_create_state_and_structured_rewrite_is_rechecked(self):
        for script in ("check_span.py", "sanitize.py"):
            proc = self.invoke(script, "中文 " + SECRET, "--json")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertNotIn(SECRET, proc.stdout + proc.stderr)
            body = json.loads(proc.stdout)
            self.assertEqual(body["decision"], "SANITIZE")
            self.assertEqual(body["audit"], {"enabled": False, "recorded": False})
            if script == "sanitize.py":
                self.assertEqual(body["text"], "中文 ghp_<REDACTED>")
        self.assertEqual(list(self.workspace.iterdir()), [])

    def test_closed_audit_summary_bounds_details_and_counts_decisions_once(self):
        secret_path = "review-" + SECRET
        uri = "postgresql" + "://guest:" + PASSWORD + "@localhost/example"
        self.env.update(AGENT_GUARD_EXFIL_AUDIT="YeS", AGENT_GUARD_SESSION="owned-fixture-session")
        payload = "\n".join([SECRET] * 67 + [uri])
        proc = self.invoke("check_span.py", payload, "--json", "--path=" + secret_path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        reply = json.loads(proc.stdout)
        self.assertEqual(reply["audit"]["records_written"], 65)
        rows = self.raw_records()
        summary = rows[0]
        self.assertEqual(summary["event"], "exfil-sanitize")
        self.assertEqual(summary["tool"], "exfil_check")
        self.assertEqual(summary["phase"], "advisory")
        self.assertEqual(summary["span_count"], 68)
        self.assertEqual(summary["redacted_count"], 68)
        self.assertEqual(summary["omitted_span_count"], 4)
        self.assertEqual(summary["size_bytes"], len(payload.encode()))
        for row in rows[1:]:
            self.assertEqual(row["event"], "exfil-span")
            self.assertEqual(row["check_id"], summary["check_id"])
            self.assertEqual(row["plan_id"], summary["plan_id"])
            self.assertEqual(row["session"], audit.correlation_id("owned-fixture-session"))
        self.assertEqual(list(self.bucket.iterdir()), [self.log])
        surface = self.log.read_text() + proc.stdout + proc.stderr
        for forbidden in (SECRET, PASSWORD, secret_path, hashlib.sha256(PASSWORD.encode()).hexdigest()):
            self.assertNotIn(forbidden, surface)
        status = subprocess.run([sys.executable, "-B", str(ROOT / "skills/delete-guard/scripts/status.py"),
                                 "--json", "--tail", "3"], cwd=self.workspace, env=self.env,
                                capture_output=True, encoding="utf-8", timeout=25, check=True)
        self.assertEqual(json.loads(status.stdout)["decisions"]["SANITIZE_SECRET_REDACT"], 1)

    def test_unknown_channel_and_cap_log_block_without_payload(self):
        for script in ("check_span.py", "sanitize.py"):
            for channel, cap in ((SECRET, "1024"), ("file-write", "4")):
                self.env["AGENT_GUARD_EXFIL_MAX_BYTES"] = cap
                proc = self.invoke(script, SECRET, "--json", "--audit", channel=channel)
                self.assertEqual(proc.returncode, 2, proc.stderr)
                body = json.loads(proc.stdout)
                self.assertEqual(body["code"], "BLOCK_OUTPUT_UNSCANNABLE")
                self.assertEqual(body["audit"]["records_written"], 1)
                self.assertNotIn(SECRET, proc.stdout + proc.stderr + self.log.read_text())
                row = self.raw_records()[-1]
                self.assertEqual(row["event"], "exfil-block")
                self.assertEqual(row["span_count"], 0)
                self.assertFalse(row["ok"])
                if channel == SECRET:
                    self.assertNotIn("channel", row)

    def test_plain_sanitize_preserves_validation_failure_and_audits_the_refusal(self):
        proc = self.invoke("sanitize.py", SECRET, "--audit", channel=SECRET)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(proc.stderr, "sanitize: payload or plan validation failed\n")
        summary, = self.raw_records()
        self.assertEqual((summary["event"], summary["code"], summary["ok"]),
                         ("exfil-block", "BLOCK_OUTPUT_UNSCANNABLE", False))
        self.assertNotIn(SECRET, self.log.read_text())

    def test_audit_refuses_symlink_and_io_failure_without_changing_verdict(self):
        self.bucket.mkdir()
        sentinel = self.workspace / "keeper.txt"
        sentinel.write_text("preserved")
        self.log.symlink_to(sentinel)
        proc = self.invoke("sanitize.py", SECRET, "--json", "--audit", channel="shell-stdout")
        self.assertEqual(proc.returncode, 3)
        body = json.loads(proc.stdout)
        self.assertEqual((body["text"], body["redaction_plan"]), ("", []))
        expected = {"enabled": True, "recorded": False, "error": "AUDIT_WRITE_FAILED"}
        self.assertEqual(body["audit"], expected)
        self.assertIn(exfil_audit.WRITE_WARNING, proc.stderr)
        self.assertNotIn(str(sentinel), proc.stderr)
        self.assertEqual(sentinel.read_text(), "preserved")
        self.env["AGENT_GUARD_TRASH"] = str(sentinel)
        blocked = self.invoke("sanitize.py", SECRET, "--json", "--audit", channel="git-commit-message")
        self.assertEqual(blocked.returncode, 2)
        self.assertEqual(json.loads(blocked.stdout)["audit"], expected)
        self.assertEqual(sentinel.read_text(), "preserved")

    def test_structured_sanitize_withholds_even_a_known_exempted_secret(self):
        allow = self.workspace / ".agent-guard/exfil-allow.toml"
        allow.parent.mkdir()
        digest = hashlib.sha256(SECRET.encode()).hexdigest()
        allow.write_text("[values]\nsha256 = sha256:" + digest + "\n")
        proc = self.invoke("sanitize.py", SECRET, "--json", "--audit")
        self.assertEqual(proc.returncode, 2)
        body = json.loads(proc.stdout)
        self.assertEqual(body["text"], "")
        self.assertEqual(body["redaction_plan"], [])
        self.assertEqual(self.raw_records()[0]["exempted_count"], 1)
        self.assertNotIn(SECRET, proc.stdout + proc.stderr + self.log.read_text())
        self.assertNotIn(digest, self.log.read_text())

    def test_invalid_flags_and_output_modes_report_static_json(self):
        for script, flags in (("sanitize.py", ("--path", SECRET)), ("sanitize.py", ("--mode", SECRET)),
                              ("check_span.py", ("--mode", SECRET)), ("sanitize.py", ("--dry-run",))):
            proc = self.invoke(script, SECRET, "--json", *flags)
            self.assertEqual(proc.returncode, 1)
            self.assertEqual(json.loads(proc.stdout), {"error": "invalid arguments", "exit": 1})
            self.assertNotIn(SECRET, proc.stderr)
        for script in ("sanitize.py", "check_span.py"):
            proc = self.invoke(script, SECRET, "--unsupported", SECRET)
            self.assertEqual(proc.returncode, 2)
            self.assertEqual(proc.stdout, "")
            self.assertNotIn(SECRET, proc.stderr)
        self.assertFalse(self.bucket.exists())
