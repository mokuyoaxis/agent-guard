"""Kimi Code 0.42.0 hook contract: ASK must never become an allow."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "adapters" / "kimi-code" / "pre_tool_use.py"
CONFIG = ROOT / "adapters" / "kimi-code" / "config.example.toml"
BRIDGE = ROOT / "adapters" / "kimi-code" / "hook_bridge.sh"
DOCTOR = ROOT / "doctor.py"
STARTUP_FAILURE = ROOT / "tests" / "fixtures" / "python_startup_failure"


class KimiAdapter(unittest.TestCase):
    def raw_hook(self, raw, cwd, extra_env=None):
        env = {**os.environ, "AGENT_GUARD_WORKSPACE": cwd}
        env.update(extra_env or {})
        return subprocess.run(
            [sys.executable, str(HOOK)], input=raw,
            capture_output=True, text=True, cwd=cwd, timeout=30,
            env=env)

    def hook(self, command, cwd):
        payload = {
            "hook_event_name": "PreToolUse", "session_id": "test-session",
            "cwd": cwd, "tool_name": "Bash",
            "tool_input": {"command": command}, "tool_call_id": "test-call",
        }
        return self.raw_hook(json.dumps(payload), cwd)

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

    def test_inert_force_push_probe_is_blocked(self):
        # `false &&` makes the remote operation unreachable even without a hook.
        # The sentinel would be created by the `||` branch if the host ran Bash.
        with tempfile.TemporaryDirectory(prefix="agent-guard-kimi-block-probe-") as cwd:
            sentinel = Path(cwd) / ".guard-block-sentinel"
            proc = self.hook(
                "false && git push --force origin main || touch .guard-block-sentinel",
                cwd,
            )
            self.assertFalse(sentinel.exists())
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertIn("BLOCK_FORCE_PUSH", proc.stderr)
        self.assertEqual(proc.stdout, "")

    def test_inert_ask_probe_is_hard_refusal(self):
        with tempfile.TemporaryDirectory(prefix="agent-guard-kimi-ask-probe-") as cwd:
            sentinel = Path(cwd) / ".guard-ask-sentinel"
            proc = self.hook(
                "false && touch .ask-create && rm .ask-create || "
                "touch .guard-ask-sentinel",
                cwd,
            )
            self.assertFalse(sentinel.exists())
            self.assertFalse((Path(cwd) / ".ask-create").exists())
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertIn("COMPOUND_CREATE_DELETE", proc.stderr)
        self.assertIn("host cannot enforce ASK", proc.stderr)
        self.assertEqual(proc.stdout, "")

    def test_malformed_hook_payload_is_refused(self):
        with tempfile.TemporaryDirectory(prefix="agent-guard-kimi-shape-") as cwd:
            for raw in (
                    "{", "[]", "{}",
                    json.dumps({"hook_event_name": "PreToolUse",
                                "tool_name": "", "cwd": cwd,
                                "tool_input": {"command": "ls"}}),
                    json.dumps({"hook_event_name": "ChangedEvent",
                                "tool_name": "Bash", "cwd": cwd,
                                "tool_input": {"command": "ls"}}),
                    json.dumps({"hook_event_name": "PreToolUse",
                                "tool_name": "Bash",
                                "tool_input": {"command": "ls"}}),
                    json.dumps({"hook_event_name": "PreToolUse",
                                "tool_name": "Bash", "cwd": cwd,
                                "tool_input": ["SENSITIVE_CANARY"]}),
                    json.dumps({"hook_event_name": "PreToolUse",
                                "tool_name": "Bash", "cwd": cwd,
                                "tool_input": {}}),
            ):
                proc = self.raw_hook(raw, cwd)
                self.assertEqual(proc.returncode, 2, raw)
                self.assertEqual(proc.stdout, "", raw)
                self.assertIn("malformed hook payload", proc.stderr, raw)
                self.assertNotIn("SENSITIVE_CANARY", proc.stderr, raw)

    def test_valid_non_bash_payload_is_ignored(self):
        with tempfile.TemporaryDirectory(prefix="agent-guard-kimi-other-") as cwd:
            proc = self.raw_hook(json.dumps({
                "hook_event_name": "PreToolUse", "tool_name": "Glob",
                "tool_input": {}, "cwd": cwd,
            }), cwd)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(proc.stderr, "")

    def test_live_sentinel_receipt_is_hashed_and_create_only(self):
        with tempfile.TemporaryDirectory(prefix="agent-guard-kimi-receipt-") as cwd:
            receipt = Path(cwd) / "receipt.json"
            nonce = "test-nonce"
            command = "touch .agent-guard-live-sentinel-test-nonce"
            payload = json.dumps({
                "hook_event_name": "PreToolUse", "tool_name": "Bash",
                "tool_input": {"command": command}, "cwd": cwd,
            })
            proc = self.raw_hook(payload, cwd, {
                "AGENT_GUARD_DIALECT": "live-sentinel-test-nonce",
                "AGENT_GUARD_SENTINEL_RECEIPT": str(receipt),
                "AGENT_GUARD_SENTINEL_NONCE": nonce,
            })
            self.assertEqual(proc.returncode, 2, proc.stderr)
            self.assertIn("BLOCK_DIALECT_UNKNOWN", proc.stderr)
            data = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertEqual(
                data["command_sha256"], hashlib.sha256(command.encode()).hexdigest())
            self.assertNotIn(command, receipt.read_text(encoding="utf-8"))
            self.assertEqual(receipt.stat().st_mode & 0o777, 0o600)

    def test_example_config_keeps_skills_at_top_level(self):
        # TOML's [[hooks]] table owns every later key until the next table.
        # Keep this assertion compatible with the supported Python 3.9 CI.
        config = CONFIG.read_text(encoding="utf-8")
        self.assertLess(config.index("\nextraSkillDirs ="),
                        config.index("\n[[hooks]]"))
        self.assertIn('matcher = "^Bash$"', config)
        self.assertIn("hook_bridge.sh", config)

    def test_shell_bridge_maps_interpreter_failure_to_block(self):
        shell = shutil.which("sh")
        if not shell:
            self.skipTest("POSIX sh unavailable")
        with tempfile.TemporaryDirectory(prefix="agent-guard-kimi-bridge-") as cwd:
            payload = json.dumps({
                "hook_event_name": "PreToolUse", "tool_name": "Glob",
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
                [shell, str(BRIDGE), sys.executable], input="{",
                capture_output=True, text=True, cwd=cwd, timeout=30,
            )
            self.assertEqual(proc.returncode, 2, proc.stderr)
            self.assertIn("malformed hook payload", proc.stderr)

    def test_shell_bridge_maps_pre_adapter_startup_failure_to_block(self):
        shell = shutil.which("sh")
        if not shell:
            self.skipTest("POSIX sh unavailable")
        with tempfile.TemporaryDirectory(prefix="agent-guard-kimi-startup-") as cwd:
            payload = json.dumps({
                "hook_event_name": "PreToolUse", "tool_name": "Bash",
                "tool_input": {"command": "touch .guard-startup-sentinel"},
                "cwd": cwd,
            })
            proc = subprocess.run(
                [shell, str(BRIDGE), sys.executable], input=payload,
                capture_output=True, text=True, cwd=cwd, timeout=30,
                env={**os.environ,
                     "PYTHONPATH": str(STARTUP_FAILURE),
                     "AGENT_GUARD_TEST_FAIL_PYTHON_STARTUP": "1"},
            )
            self.assertEqual(proc.returncode, 2, proc.stderr)
            self.assertIn("hook process failed (exit 1)", proc.stderr)
            self.assertFalse((Path(cwd) / ".guard-startup-sentinel").exists())

    @unittest.skipIf(sys.version_info < (3, 11), "doctor needs tomllib")
    def test_doctor_checks_config_and_local_bridge(self):
        shell = shutil.which("sh")
        if not shell:
            self.skipTest("POSIX sh unavailable")
        with tempfile.TemporaryDirectory(prefix="agent-guard-kimi-doctor-") as cwd:
            config_path = Path(cwd) / "config.toml"
            command = " ".join(shlex.quote(part) for part in (
                shell, str(BRIDGE), sys.executable))
            config_path.write_text(
                "[[hooks]]\n"
                'event = "PreToolUse"\n'
                'matcher = "^Bash$"\n'
                f"command = {json.dumps(command)}\n"
                "timeout = 90\n", encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(DOCTOR), "kimi", "--config",
                 str(config_path), "--probe"],
                capture_output=True, text=True, cwd=cwd, timeout=60,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("Core policy refusal", proc.stdout)

            proc = subprocess.run(
                [sys.executable, str(DOCTOR), "kimi", "--config",
                 str(config_path)],
                capture_output=True, text=True, cwd=cwd, timeout=30,
                env={**os.environ, "AGENT_GUARD_DIALECT": "fish"},
            )
            self.assertEqual(proc.returncode, 1, proc.stdout)
            self.assertIn("AGENT_GUARD_DIALECT is invalid", proc.stderr)

            config_path.write_text(
                "[[hooks]]\n"
                'event = "PreToolUse"\n'
                'matcher = "Bash"\n'
                f"command = {json.dumps(f'{sys.executable} {HOOK}')}\n",
                encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(DOCTOR), "kimi", "--config",
                 str(config_path)],
                capture_output=True, text=True, cwd=cwd, timeout=30,
            )
            self.assertEqual(proc.returncode, 1, proc.stdout)
            self.assertIn("matcher must be exactly", proc.stderr)
            self.assertIn("ABSOLUTE_SH", proc.stderr)


if __name__ == "__main__":
    unittest.main()
