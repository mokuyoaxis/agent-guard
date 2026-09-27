"""The local doctor must not promote a config or bridge test to host proof."""

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
DOCTOR = ROOT / "doctor.py"
CLAUDE_BRIDGE = ROOT / "adapters" / "claude" / "hook_bridge.sh"
KIMI_BRIDGE = ROOT / "adapters" / "kimi-code" / "hook_bridge.sh"


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.shell = shutil.which("sh")
        if not self.shell:
            self.skipTest("POSIX sh unavailable")
        self.temp = tempfile.TemporaryDirectory(prefix="agent-guard-doctor-test-")
        self.addCleanup(self.temp.cleanup)
        self.cwd = Path(self.temp.name)
        self.env = dict(os.environ)
        self.env.pop("AGENT_GUARD_DIALECT", None)

    def command(self, bridge, python=None):
        return " ".join(shlex.quote(str(part)) for part in (
            self.shell, bridge, python or sys.executable))

    def run_doctor(self, harness, config, *args):
        return subprocess.run(
            [sys.executable, str(DOCTOR), harness, "--config", str(config),
             "--json", *args], capture_output=True, text=True,
            cwd=self.cwd, env=self.env, timeout=60)

    def host_executable(self, version_output, name="fake-host"):
        executable = self.cwd / name
        executable.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' {shlex.quote(version_output)}\n",
            encoding="utf-8")
        executable.chmod(0o700)
        return executable

    def live_host_executable(self):
        executable = self.cwd / "claude-live"
        executable.write_text(textwrap.dedent(f"""\
            #!{sys.executable}
            import hashlib
            import json
            import os
            from pathlib import Path
            import sys

            if "--version" in sys.argv:
                print("2.1.273 (Claude Code)")
                raise SystemExit(0)
            nonce = os.environ["AGENT_GUARD_SENTINEL_NONCE"]
            command = f"touch agent-guard-live-sentinel-{{nonce}}.txt"
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
                "event": "enforce-block", "code": "BLOCK_DIALECT_UNKNOWN",
                "command": "<redacted>",
            }}) + "\\n", encoding="utf-8")
            print("[agent-guard] BLOCKED [BLOCK_DIALECT_UNKNOWN]")
            """), encoding="utf-8")
        executable.chmod(0o700)
        return executable

    def claude_config(self, **handler_overrides):
        handler = {
            "type": "command",
            "command": self.command(CLAUDE_BRIDGE),
            "timeout": 120,
        }
        handler.update(handler_overrides)
        return {"hooks": {"PreToolUse": [
            {"matcher": "Bash", "hooks": [handler]},
        ]}}

    def write_claude(self, data):
        config = self.cwd / "settings.json"
        config.write_text(json.dumps(data), encoding="utf-8")
        return config

    def test_claude_selected_config_and_probe_stay_unverified(self):
        config = self.write_claude(self.claude_config())
        proc = self.run_doctor("claude", config)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = json.loads(proc.stdout)
        self.assertEqual(report["configuration"], "PASS")
        self.assertEqual(report["local_probe"], "NOT_RUN")
        self.assertEqual(report["host_interception"], "UNVERIFIED")
        self.assertEqual(report["drift_status"], "NOT_CHECKED")
        self.assertIsNone(report["host_version"])

        proc = self.run_doctor("claude", config, "--probe")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = json.loads(proc.stdout)
        self.assertEqual(report["local_probe"], "PASS")
        self.assertEqual(report["host_interception"], "UNVERIFIED")
        self.assertEqual(report["problems"], [])

    def test_drift_check_distinguishes_current_stale_and_unverified(self):
        config = self.write_claude(self.claude_config())
        current = self.host_executable("2.1.273 (Claude Code)", "claude-current")
        proc = self.run_doctor(
            "claude", config, "--check-drift", "--host-executable", str(current))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = json.loads(proc.stdout)
        self.assertEqual(report["profile"], "claude-code-pretooluse-bash-v1")
        self.assertEqual(report["host_version"], "2.1.273")
        self.assertEqual(report["drift_status"], "CURRENT")
        self.assertRegex(
            report["configuration_fingerprint"], r"^sha256:[0-9a-f]{64}$")
        self.assertRegex(report["runtime_fingerprint"], r"^sha256:[0-9a-f]{64}$")

        future = self.host_executable("Claude Code 2.2.0", "claude-future")
        proc = self.run_doctor(
            "claude", config, "--check-drift", "--host-executable", str(future))
        self.assertEqual(proc.returncode, 1)
        report = json.loads(proc.stdout)
        self.assertEqual(report["host_version"], "2.2.0")
        self.assertEqual(report["drift_status"], "STALE")
        self.assertIn(
            "host version is outside the tested profile", report["drift_problems"])

        proc = self.run_doctor(
            "claude", config, "--check-drift", "--host-executable",
            str(self.cwd / "missing-host"))
        self.assertEqual(proc.returncode, 1)
        report = json.loads(proc.stdout)
        self.assertIsNone(report["host_version"])
        self.assertEqual(report["drift_status"], "UNVERIFIED")

    def test_baseline_is_private_minimal_and_detects_config_drift(self):
        data = self.claude_config()
        config = self.write_claude(data)
        host = self.host_executable("2.1.273 (Claude Code)", "claude-baseline")
        baseline = self.cwd / "baseline.json"
        proc = self.run_doctor(
            "claude", config, "--probe", "--check-drift",
            "--host-executable", str(host), "--write-baseline", str(baseline))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = json.loads(proc.stdout)
        self.assertTrue(report["baseline_written"])
        self.assertEqual(baseline.stat().st_mode & 0o777, 0o600)
        baseline_text = baseline.read_text(encoding="utf-8")
        self.assertNotIn(str(ROOT), baseline_text)
        self.assertNotIn("hook_bridge.sh", baseline_text)
        self.assertEqual(
            set(json.loads(baseline_text)),
            {"schema_version", "harness", "profile", "host_version",
             "configuration_fingerprint", "runtime_fingerprint"})

        data["hooks"]["PreToolUse"][0]["hooks"][0]["timeout"] = 121
        self.write_claude(data)
        proc = self.run_doctor(
            "claude", config, "--check-drift", "--host-executable", str(host),
            "--baseline", str(baseline))
        self.assertEqual(proc.returncode, 1)
        report = json.loads(proc.stdout)
        self.assertEqual(report["configuration"], "PASS")
        self.assertEqual(report["drift_status"], "DRIFTED")
        self.assertIn(
            "hook configuration changed since baseline", report["drift_problems"])

    def test_broken_config_has_broken_drift_status(self):
        config = self.write_claude(self.claude_config(**{"async": True}))
        host = self.host_executable("2.1.273 (Claude Code)", "claude-broken")
        proc = self.run_doctor(
            "claude", config, "--check-drift", "--host-executable", str(host))
        self.assertEqual(proc.returncode, 1)
        report = json.loads(proc.stdout)
        self.assertEqual(report["configuration"], "FAIL")
        self.assertEqual(report["drift_status"], "BROKEN")

    def test_invalid_baseline_is_unverified_not_current(self):
        config = self.write_claude(self.claude_config())
        host = self.host_executable("2.1.273 (Claude Code)", "claude-invalid-base")
        baseline = self.cwd / "baseline.json"
        baseline.write_text("{}", encoding="utf-8")
        proc = self.run_doctor(
            "claude", config, "--check-drift", "--host-executable", str(host),
            "--baseline", str(baseline))
        self.assertEqual(proc.returncode, 1)
        report = json.loads(proc.stdout)
        self.assertEqual(report["drift_status"], "UNVERIFIED")
        self.assertTrue(report["drift_problems"])

    def test_live_sentinel_implies_local_probe_and_reports_alarm(self):
        config = self.write_claude(self.claude_config())
        host = self.live_host_executable()
        evidence = self.cwd / "live-evidence"
        proc = self.run_doctor(
            "claude", config, "--live-sentinel", "--host-executable", str(host),
            "--sentinel-output", str(evidence))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = json.loads(proc.stdout)
        self.assertEqual(report["local_probe"], "PASS")
        self.assertEqual(report["drift_status"], "CURRENT")
        self.assertEqual(report["live_sentinel"]["status"], "PASS")
        self.assertEqual(report["live_sentinel"]["alarm"], "NONE")
        self.assertTrue((evidence / "result.json").is_file())

    def test_claude_default_is_project_settings_not_user_settings(self):
        project_config = self.cwd / ".claude" / "settings.json"
        project_config.parent.mkdir()
        project_config.write_text(
            json.dumps(self.claude_config()), encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(DOCTOR), "claude", "--json"],
            capture_output=True, text=True, cwd=self.cwd,
            env=self.env, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["configuration"], "PASS")

    def test_claude_rejects_disabled_async_and_filtered_hooks(self):
        cases = (
            ({"disableAllHooks": True}, "disableAllHooks=true"),
            ({"handler": {"async": True}}, "must be synchronous"),
            ({"handler": {"if": "Bash(rm *)"}}, "must not have an `if`"),
            ({"group": {"matcher": "Edit"}}, "matcher must be exactly Bash"),
            ({"handler": {"command": (
                f"{sys.executable} {ROOT / 'adapters' / 'claude' / 'pre_tool_use.py'}")}},
             "ABSOLUTE_SH"),
        )
        for change, expected in cases:
            with self.subTest(expected=expected):
                data = self.claude_config()
                data.update({key: value for key, value in change.items()
                             if key not in ("handler", "group")})
                group = data["hooks"]["PreToolUse"][0]
                group.update(change.get("group", {}))
                group["hooks"][0].update(change.get("handler", {}))
                proc = self.run_doctor("claude", self.write_claude(data), "--probe")
                self.assertEqual(proc.returncode, 1)
                report = json.loads(proc.stdout)
                self.assertEqual(report["configuration"], "FAIL")
                self.assertEqual(report["local_probe"], "NOT_RUN")
                self.assertEqual(report["host_interception"], "UNVERIFIED")
                self.assertTrue(any(expected in problem for problem in report["problems"]))

    def test_claude_bad_json_and_missing_hook_are_not_configured(self):
        config = self.cwd / "settings.json"
        config.write_text("{", encoding="utf-8")
        proc = self.run_doctor("claude", config)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(json.loads(proc.stdout)["configuration"], "FAIL")
        config.write_text("{}", encoding="utf-8")
        proc = self.run_doctor("claude", config)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(json.loads(proc.stdout)["configuration"], "FAIL")

    def test_claude_bridge_failure_is_local_probe_failure_not_config_failure(self):
        false_bin = shutil.which("false")
        if not false_bin:
            self.skipTest("false unavailable")
        config = self.write_claude(self.claude_config(
            command=self.command(CLAUDE_BRIDGE, false_bin)))
        proc = self.run_doctor("claude", config, "--probe")
        self.assertEqual(proc.returncode, 1)
        report = json.loads(proc.stdout)
        self.assertEqual(report["configuration"], "PASS")
        self.assertEqual(report["local_probe"], "FAIL")
        self.assertEqual(report["host_interception"], "UNVERIFIED")

    @unittest.skipIf(sys.version_info < (3, 11), "Kimi TOML parsing needs Python 3.11")
    def test_kimi_and_claude_share_status_vocabulary(self):
        config = self.cwd / "config.toml"
        config.write_text(
            "[[hooks]]\n"
            'event = "PreToolUse"\n'
            'matcher = "^Bash$"\n'
            f"command = {json.dumps(self.command(KIMI_BRIDGE))}\n"
            "timeout = 90\n", encoding="utf-8")
        proc = self.run_doctor("kimi", config, "--probe")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = json.loads(proc.stdout)
        self.assertEqual(report["configuration"], "PASS")
        self.assertEqual(report["local_probe"], "PASS")
        self.assertEqual(report["host_interception"], "UNVERIFIED")

        host = self.host_executable("kimi, version 0.42.0", "kimi-current")
        proc = self.run_doctor(
            "kimi", config, "--check-drift", "--host-executable", str(host))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = json.loads(proc.stdout)
        self.assertEqual(report["profile"], "kimi-code-pretooluse-bash-v1")
        self.assertEqual(report["host_version"], "0.42.0")
        self.assertEqual(report["drift_status"], "CURRENT")

        current_host = self.host_executable(
            "Kimi Code CLI 2.1.1", "kimi-current-2")
        proc = self.run_doctor(
            "kimi", config, "--check-drift",
            "--host-executable", str(current_host))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = json.loads(proc.stdout)
        self.assertEqual(report["host_version"], "2.1.1")
        self.assertEqual(report["drift_status"], "CURRENT")


if __name__ == "__main__":
    unittest.main()
