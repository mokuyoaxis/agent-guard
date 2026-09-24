"""Claude Code's POSIX hook entrypoint and fail-closed startup boundary."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "adapters" / "claude" / "pre_tool_use.py"
BRIDGE = ROOT / "adapters" / "claude" / "hook_bridge.sh"
CONFIG = ROOT / "adapters" / "claude" / "settings.example.json"


class ClaudeAdapter(unittest.TestCase):
    def test_malformed_event_is_refused_without_echoing_input(self):
        with tempfile.TemporaryDirectory(prefix="agent-guard-claude-shape-") as cwd:
            payloads = (
                "{SENSITIVE_CANARY",
                "{}",
                json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash",
                            "cwd": cwd, "tool_input": {}}),
                json.dumps({"hook_event_name": "Other", "tool_name": "Bash",
                            "cwd": cwd,
                            "tool_input": {"command": "SENSITIVE_CANARY"}}),
            )
            for raw in payloads:
                proc = subprocess.run(
                    [sys.executable, str(HOOK)], input=raw,
                    capture_output=True, text=True, cwd=cwd, timeout=30,
                )
                self.assertEqual(proc.returncode, 2, raw)
                self.assertIn("malformed hook payload", proc.stderr)
                self.assertNotIn("SENSITIVE_CANARY", proc.stderr)

    def test_valid_non_bash_event_is_out_of_scope(self):
        with tempfile.TemporaryDirectory(prefix="agent-guard-claude-other-") as cwd:
            proc = subprocess.run(
                [sys.executable, str(HOOK)], input=json.dumps({
                    "hook_event_name": "PreToolUse", "tool_name": "Read",
                    "tool_input": {}, "cwd": cwd,
                }), capture_output=True, text=True, cwd=cwd, timeout=30,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout + proc.stderr, "")

    def test_posix_bridge_maps_python_failure_to_exit_2(self):
        shell = shutil.which("sh")
        if not shell:
            self.skipTest("POSIX sh unavailable")
        with tempfile.TemporaryDirectory(prefix="agent-guard-claude-bridge-") as cwd:
            payload = json.dumps({
                "hook_event_name": "PreToolUse", "tool_name": "Read",
                "tool_input": {}, "cwd": cwd,
            })
            cases = [(sys.executable, 0),
                     (str(Path(cwd) / "missing-python"), 2)]
            false_bin = shutil.which("false")
            if false_bin:
                cases.append((false_bin, 2))
            for interpreter, expected in cases:
                proc = subprocess.run(
                    [shell, str(BRIDGE), interpreter], input=payload,
                    capture_output=True, text=True, cwd=cwd, timeout=30,
                )
                self.assertEqual(proc.returncode, expected, proc.stderr)
            proc = subprocess.run(
                [shell, str(BRIDGE), "python3"], input=payload,
                capture_output=True, text=True, cwd=cwd, timeout=30,
            )
            self.assertEqual(proc.returncode, 2)
            self.assertIn("must be absolute", proc.stderr)

    def test_example_uses_posix_bridge(self):
        command = json.loads(CONFIG.read_text(encoding="utf-8"))["hooks"][
            "PreToolUse"][0]["hooks"][0]["command"]
        self.assertIn("/bin/sh ", command)
        self.assertIn("/adapters/claude/hook_bridge.sh ", command)


if __name__ == "__main__":
    unittest.main()
