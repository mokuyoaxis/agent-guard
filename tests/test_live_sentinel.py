"""The live sentinel must alarm from independent, privacy-minimal evidence."""

import json
import os
from pathlib import Path
import sys
import tempfile
import textwrap
import unittest

from live_sentinel import run_live_sentinel


class LiveSentinelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="agent-guard-sentinel-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / "settings.json"
        self.config.write_text("{}\n", encoding="utf-8")

    def fake_host(self, behavior):
        executable = self.root / f"host-{behavior}"
        executable.write_text(textwrap.dedent(f"""\
            #!{sys.executable}
            import hashlib
            import json
            import os
            from pathlib import Path

            nonce = os.environ["AGENT_GUARD_SENTINEL_NONCE"]
            command = f"touch .agent-guard-live-sentinel-{{nonce}}"
            if {behavior!r} == "pass":
                receipt = {{
                    "schema_version": 1,
                    "nonce": nonce,
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Bash",
                    "command_sha256": hashlib.sha256(command.encode()).hexdigest(),
                    "cwd_sha256": hashlib.sha256(
                        os.path.realpath(os.getcwd()).encode()).hexdigest(),
                }}
                Path(os.environ["AGENT_GUARD_SENTINEL_RECEIPT"]).write_text(
                    json.dumps(receipt), encoding="utf-8")
                trash = Path(".agent-trash")
                trash.mkdir()
                (trash / "audit.jsonl").write_text(json.dumps({{
                    "event": "enforce-block",
                    "code": "BLOCK_DIALECT_UNKNOWN",
                    "command": "<redacted>",
                }}) + "\\n", encoding="utf-8")
                print("[agent-guard] BLOCKED [BLOCK_DIALECT_UNKNOWN]")
            elif {behavior!r} == "fail":
                Path(f".agent-guard-live-sentinel-{{nonce}}").touch()
            """), encoding="utf-8")
        executable.chmod(0o700)
        return executable

    def run_case(self, behavior, drift_status="CURRENT"):
        output = self.root / f"evidence-{behavior}"
        result = run_live_sentinel(
            "claude", self.config, ("unused",),
            host_executable=str(self.fake_host(behavior)),
            output_dir=output, timeout=15, drift_status=drift_status,
        )
        return result, output

    def test_pass_requires_receipt_audit_and_block_feedback(self):
        result, output = self.run_case("pass")
        self.assertEqual(result.status, "PASS")
        self.assertEqual(result.alarm, "NONE")
        self.assertTrue(result.receipt_match)
        self.assertTrue(result.audit_match)
        self.assertTrue(result.block_feedback_observed)
        self.assertFalse(result.sentinel_exists)
        report = json.loads((output / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["drift_status"], "CURRENT")
        self.assertFalse(report["host_output"]["retained"])
        self.assertEqual(output.stat().st_mode & 0o777, 0o700)
        self.assertEqual(
            (output / "result.json").stat().st_mode & 0o777, 0o600)
        self.assertFalse((output / "stdout.txt").exists())
        self.assertFalse((output / "stderr.txt").exists())

    def test_pass_on_stale_profile_raises_notice(self):
        result, _ = self.run_case("pass", drift_status="STALE")
        self.assertEqual(result.status, "PASS")
        self.assertEqual(result.alarm, "NOTICE")
        self.assertEqual(result.as_dict()["drift_status"], "STALE")

    def test_executed_marker_is_critical_failure(self):
        result, _ = self.run_case("fail")
        self.assertEqual(result.status, "FAIL")
        self.assertEqual(result.alarm, "CRITICAL")
        self.assertEqual(result.reason, "SENTINEL_EXECUTED")

    def test_missing_evidence_is_inconclusive(self):
        result, _ = self.run_case("inconclusive")
        self.assertEqual(result.status, "INCONCLUSIVE")
        self.assertEqual(result.alarm, "WARNING")
        self.assertEqual(result.reason, "EXACT_HOOK_CALL_NOT_OBSERVED")


if __name__ == "__main__":
    unittest.main()
