"""The local doctor must not promote a config or bridge test to host proof."""

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
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

        proc = self.run_doctor("claude", config, "--probe")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = json.loads(proc.stdout)
        self.assertEqual(report["local_probe"], "PASS")
        self.assertEqual(report["host_interception"], "UNVERIFIED")
        self.assertEqual(report["problems"], [])

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


if __name__ == "__main__":
    unittest.main()
