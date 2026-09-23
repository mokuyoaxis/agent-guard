"""Kimi Code 0.42.0 hook contract: ASK must never become an allow."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "adapters" / "kimi-code" / "pre_tool_use.py"
CONFIG = ROOT / "adapters" / "kimi-code" / "config.example.toml"


class KimiAdapter(unittest.TestCase):
    def hook(self, command, cwd):
        payload = {
            "hook_event_name": "PreToolUse", "session_id": "test-session",
            "cwd": cwd, "tool_name": "Bash",
            "tool_input": {"command": command}, "tool_call_id": "test-call",
        }
        return subprocess.run(
            [sys.executable, str(HOOK)], input=json.dumps(payload),
            capture_output=True, text=True, cwd=cwd, timeout=30,
            env={**os.environ, "AGENT_GUARD_WORKSPACE": cwd})

    def test_ask_is_a_hard_refusal(self):
        with tempfile.TemporaryDirectory(prefix="agent-guard-kimi-ask-") as cwd:
            proc = self.hook("touch new && rm new", cwd)
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertIn("host cannot enforce ASK", proc.stderr)
        self.assertEqual(proc.stdout, "")

    def test_force_push_after_cd_is_blocked(self):
        with tempfile.TemporaryDirectory(prefix="agent-guard-kimi-push-") as cwd:
            proc = self.hook("cd sub && git push --force origin main", cwd)
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertIn("BLOCK_FORCE_PUSH", proc.stderr)

    def test_example_config_keeps_skills_at_top_level(self):
        # TOML's [[hooks]] table owns every later key until the next table.
        # Keep this assertion compatible with the supported Python 3.9 CI.
        config = CONFIG.read_text(encoding="utf-8")
        self.assertLess(config.index("\nextraSkillDirs ="),
                        config.index("\n[[hooks]]"))


if __name__ == "__main__":
    unittest.main()
