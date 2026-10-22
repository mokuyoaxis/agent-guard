"""The local doctor must not promote a config or bridge test to host proof."""

import io
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
from unittest import mock

import doctor


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

    def stop_hook(self, **overrides):
        hook = {"event": "Stop", "command": self.command(KIMI_BRIDGE) + " --stop-feedback",
                "timeout": 30}
        hook.update(overrides)
        return hook

    def write_kimi(self, stops=()):
        hooks = [{"event": "PreToolUse", "matcher": "^Bash$",
                  "command": self.command(KIMI_BRIDGE), "timeout": 120}, *stops]
        config = self.cwd / "config.toml"
        config.write_text("".join("[[hooks]]\n" + "".join(
            f"{key} = {json.dumps(value)}\n" for key, value in hook.items())
            for hook in hooks), encoding="utf-8")
        return config

    @unittest.skipIf(sys.version_info < (3, 11), "Kimi TOML parsing needs Python 3.11")
    def test_kimi_feedback_is_optional_and_ignores_unrelated_stop(self):
        for stops in ((), ({"event": "Stop", "command": "printf unrelated"},)):
            with self.subTest(stops=stops):
                proc = self.run_doctor("kimi", self.write_kimi(stops))
                self.assertEqual(proc.returncode, 0, proc.stderr)
                report = json.loads(proc.stdout)
                self.assertEqual(report["configuration"], "PASS")
                self.assertEqual(report["feedback"], {
                    "configured": False, "configuration": "NOT_CONFIGURED",
                    "local_probe": "NOT_RUN", "model_delivery": "UNVERIFIED", "problems": []})

    @unittest.skipIf(sys.version_info < (3, 11), "Kimi TOML parsing needs Python 3.11")
    def test_kimi_feedback_configuration_and_runtime_are_separate(self):
        config = self.write_kimi([self.stop_hook()])
        for flags, expected in (((), "NOT_RUN"), (("--probe",), "PASS")):
            with self.subTest(probe=expected):
                proc = self.run_doctor("kimi", config, *flags)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                report = json.loads(proc.stdout)
                self.assertEqual(report["configuration"], "PASS")
                self.assertEqual(report["feedback"]["configuration"], "PASS")
                self.assertEqual(report["feedback"]["local_probe"], expected)
                self.assertEqual(report["feedback"]["model_delivery"], "UNVERIFIED")
                self.assertEqual(report["host_interception"], "UNVERIFIED")

    @unittest.skipIf(sys.version_info < (3, 11), "Kimi TOML parsing needs Python 3.11")
    def test_kimi_feedback_rejects_wrong_event_filters_commands_and_duplicates(self):
        command = self.command(KIMI_BRIDGE)
        marker = self.cwd / "injected-command"
        unsafe_command = command + " --stop-feedback; touch " + str(marker)
        invalid = [
            [self.stop_hook(event="PostToolUse")], [self.stop_hook(matcher="^Bash$")],
            [self.stop_hook(timeout=True)], [self.stop_hook(timeout=0)], [self.stop_hook(timeout=601)],
            [self.stop_hook(command=command)], [self.stop_hook(command=command + " --unknown")],
            [self.stop_hook(command=unsafe_command)],
            [self.stop_hook(command=self.command(CLAUDE_BRIDGE) + " --stop-feedback")],
            [self.stop_hook(command=f"{sys.executable} {doctor.KIMI_FEEDBACK}")],
            [self.stop_hook(), self.stop_hook()],
        ]
        for stops in invalid:
            with self.subTest(stops=stops):
                # Configuration cases do not need to rerun every PreToolUse
                # probe. Keep --probe for injection to verify it cannot run.
                flags = ("--probe",) if stops[0]["command"] == unsafe_command else ()
                proc = self.run_doctor("kimi", self.write_kimi(stops), *flags)
                self.assertEqual(proc.returncode, 1)
                report = json.loads(proc.stdout)
                self.assertEqual(report["configuration"], "PASS")
                self.assertEqual(report["local_probe"], "PASS" if flags else "NOT_RUN")
                self.assertEqual(report["problems"], [])
                self.assertTrue(report["feedback"]["configured"])
                self.assertEqual(report["feedback"]["configuration"], "FAIL")
                self.assertEqual(report["feedback"]["local_probe"], "NOT_RUN")
                self.assertTrue(report["feedback"]["problems"])
                self.assertFalse(marker.exists())

    @unittest.skipIf(sys.version_info < (3, 11), "Kimi TOML parsing needs Python 3.11")
    def test_kimi_unreadable_or_invalid_config_does_not_claim_feedback_disabled(self):
        config = self.cwd / "invalid.toml"
        for contents in (None, b"hooks = [", b"\xff", b'hooks = "wrong-shape"'):
            with self.subTest(contents=contents):
                if contents is not None:
                    config.write_bytes(contents)
                proc = self.run_doctor("kimi", config, "--probe")
                self.assertEqual(proc.returncode, 1)
                report = json.loads(proc.stdout)
                self.assertEqual(report["configuration"], "FAIL")
                self.assertIsNone(report["feedback"]["configured"])
                self.assertEqual(report["feedback"]["configuration"], "UNVERIFIED")
                self.assertEqual(report["feedback"]["local_probe"], "NOT_RUN")

    @unittest.skipIf(sys.version_info < (3, 11), "Kimi TOML parsing needs Python 3.11")
    def test_kimi_missing_feedback_helper_is_configuration_failure(self):
        config = self.write_kimi([self.stop_hook()])
        with mock.patch.dict(os.environ, self.env, clear=True), \
                mock.patch.object(doctor, "KIMI_FEEDBACK", self.cwd / "missing-feedback.py"), \
                mock.patch.object(doctor, "_probe_feedback") as probe:
            report = doctor._diagnose("kimi", config, False)
        self.assertEqual(report.configuration, "PASS")
        self.assertEqual(report.feedback.configuration, "FAIL")
        self.assertIn("stop_feedback.py is missing", report.feedback.problems[0])
        probe.assert_not_called()

    @unittest.skipIf(sys.version_info < (3, 11), "Kimi TOML parsing needs Python 3.11")
    def test_kimi_executable_interpreter_does_not_prove_feedback_runs(self):
        interpreter = self.cwd / "broken-python"
        for body in ("exit 0\n", "echo PRIVATE_RUNTIME_OUTPUT >&2\nexit 17\n"):
            with self.subTest(body=body):
                interpreter.write_text("#!/bin/sh\n" + body, encoding="utf-8")
                interpreter.chmod(0o700)
                config = self.write_kimi([self.stop_hook(
                    command=self.command(KIMI_BRIDGE, interpreter) + " --stop-feedback")])
                proc = self.run_doctor("kimi", config, "--probe")
                self.assertEqual(proc.returncode, 1)
                report = json.loads(proc.stdout)
                self.assertEqual(report["local_probe"], "PASS")
                self.assertEqual(report["feedback"]["configuration"], "PASS")
                self.assertEqual(report["feedback"]["local_probe"], "FAIL")
                self.assertEqual(report["feedback"]["model_delivery"], "UNVERIFIED")
                self.assertTrue(report["feedback"]["problems"])
                self.assertNotIn("PRIVATE_RUNTIME_OUTPUT", proc.stdout + proc.stderr)

    @unittest.skipIf(sys.version_info < (3, 11), "Kimi TOML parsing needs Python 3.11")
    def test_kimi_probes_leave_inherited_home_and_recovery_data_untouched(self):
        protected = self.cwd / "existing-user-data"
        protected.mkdir()
        for name in ("audit.jsonl", "manifest.json", "feedback-cursor.json", "config.toml"):
            (protected / name).write_bytes(b"PRIVATE_EXISTING_DATA\n")
        before = {path: (path.read_bytes(), path.stat().st_mtime_ns)
                  for path in protected.iterdir()}
        self.env.update(HOME=str(protected), KIMI_CODE_HOME=str(protected),
                        AGENT_GUARD_WORKSPACE=str(protected), AGENT_GUARD_TRASH=str(protected),
                        AGENT_GUARD_SESSION_ID="existing-session",
                        AGENT_GUARD_SENTINEL_RECEIPT=str(protected / "receipt.json"))
        proc = self.run_doctor("kimi", self.write_kimi([self.stop_hook()]), "--probe")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["feedback"]["local_probe"], "PASS")
        self.assertEqual({path: (path.read_bytes(), path.stat().st_mtime_ns)
                          for path in protected.iterdir()}, before)

    @unittest.skipIf(sys.version_info < (3, 11), "Kimi TOML parsing needs Python 3.11")
    def test_kimi_stop_configuration_and_helper_are_covered_by_drift(self):
        config = self.write_kimi([self.stop_hook()])
        host = self.host_executable("Kimi Code CLI 2.1.1", "kimi-feedback")
        baseline = self.cwd / "feedback-baseline.json"
        proc = self.run_doctor("kimi", config, "--probe", "--host-executable", str(host),
                               "--write-baseline", str(baseline))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        initial = json.loads(proc.stdout)
        self.assertEqual(initial["feedback"]["local_probe"], "PASS")
        self.assertTrue(initial["baseline_written"])
        self.assertEqual(baseline.stat().st_mode & 0o777, 0o600)
        self.assertEqual(set(json.loads(baseline.read_text())), {
            "schema_version", "harness", "profile", "host_version",
            "configuration_fingerprint", "runtime_fingerprint"})
        for stops in ([self.stop_hook(timeout=31)], []):
            with self.subTest(stops=stops):
                self.write_kimi(stops)
                proc = self.run_doctor("kimi", config, "--host-executable", str(host),
                                       "--baseline", str(baseline))
                self.assertEqual(proc.returncode, 1)
                report = json.loads(proc.stdout)
                self.assertEqual(report["drift_status"], "DRIFTED")
                self.assertIn("hook configuration changed since baseline", report["drift_problems"])
                if not stops:
                    self.assertIn("guard runtime changed since baseline", report["drift_problems"])
        # Track the optional helper's bytes, without changing the checkout.
        original = Path.read_bytes
        def changed_helper(path):
            content = original(path)
            return content + b"\n# changed helper\n" if path == doctor.KIMI_FEEDBACK else content
        profile = doctor._load_profile("kimi")
        original_pre = doctor._runtime_fingerprint("kimi", profile)
        original_stop = doctor._runtime_fingerprint("kimi", profile, True)
        with mock.patch.object(Path, "read_bytes", changed_helper):
            self.assertEqual(doctor._runtime_fingerprint("kimi", profile), original_pre)
            self.assertNotEqual(doctor._runtime_fingerprint("kimi", profile, True), original_stop)

    @unittest.skipIf(sys.version_info < (3, 11), "Kimi TOML parsing needs Python 3.11")
    def test_kimi_pretooluse_wrapper_and_unconfigured_fingerprints_stay_compatible(self):
        config = self.write_kimi()
        with mock.patch.dict(os.environ, self.env, clear=True):
            original = doctor._check_kimi_config(config)[2]
            report = doctor._diagnose("kimi", config, False)
            self.assertEqual(report.configuration_fingerprint, original)
            self.write_kimi([{"event": "Stop", "command": "printf unrelated"}])
            self.assertEqual(doctor._diagnose("kimi", config, False).configuration_fingerprint, original)
            self.write_kimi([self.stop_hook(matcher="^Bash$")])
            with mock.patch.object(doctor, "_probe_feedback") as probe:
                self.assertEqual(doctor.check_kimi(config, run_probe=True), [])
                probe.assert_not_called()
        claude = self.run_doctor("claude", self.write_claude(self.claude_config()))
        self.assertEqual(claude.returncode, 0, claude.stderr)
        self.assertNotIn("feedback", json.loads(claude.stdout))

    @unittest.skipIf(sys.version_info < (3, 11), "Kimi TOML parsing needs Python 3.11")
    def test_kimi_feedback_failure_blocks_baseline_and_live_model_call(self):
        config = self.write_kimi([self.stop_hook(matcher="^Bash$")])
        host = self.host_executable("Kimi Code CLI 2.1.1", "kimi-bad-feedback")
        baseline = self.cwd / "refused-baseline.json"
        proc = self.run_doctor("kimi", config, "--probe", "--host-executable", str(host),
                               "--write-baseline", str(baseline))
        self.assertEqual(proc.returncode, 1)
        report = json.loads(proc.stdout)
        self.assertEqual(report["drift_status"], "BROKEN")
        self.assertFalse(report["baseline_written"])
        self.assertFalse(baseline.exists())
        output = io.StringIO()
        with mock.patch.dict(os.environ, self.env, clear=True), \
                mock.patch("sys.stdout", output), \
                mock.patch("live_sentinel.run_live_sentinel") as live:
            code = doctor.main(["kimi", "--config", str(config), "--json",
                                "--host-executable", str(host), "--live-sentinel"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.getvalue())["live_sentinel"]["reason"], "LOCAL_PREFLIGHT_FAILED")
        live.assert_not_called()

    @unittest.skipIf(sys.version_info < (3, 11), "Kimi TOML parsing needs Python 3.11")
    def test_kimi_human_output_separates_optional_feedback_from_pretooluse(self):
        for stops, state, code in (([], "NOT_CONFIGURED", 0),
                                   ([self.stop_hook()], "PASS", 0),
                                   ([self.stop_hook(matcher="Bash")], "FAIL", 1)):
            with self.subTest(state=state):
                config = self.write_kimi(stops)
                proc = subprocess.run([sys.executable, str(DOCTOR), "kimi", "--config", str(config)],
                                      capture_output=True, text=True, cwd=self.cwd, env=self.env, timeout=30)
                self.assertEqual(proc.returncode, code)
                self.assertIn("PASS: kimi PreToolUse configuration", proc.stdout)
                self.assertIn("FEEDBACK_CONFIGURATION: " + state, proc.stdout)
                self.assertIn("FEEDBACK_LOCAL_PROBE: NOT_RUN", proc.stdout)
                self.assertIn("MODEL_DELIVERY: UNVERIFIED", proc.stdout)
                if code:
                    self.assertIn("FEEDBACK_FAIL:", proc.stderr)

    @unittest.skipIf(sys.version_info < (3, 11), "Kimi TOML parsing needs Python 3.11")
    def test_kimi_feedback_python_path_with_spaces(self):
        python = self.cwd / "python with spaces"
        python.symlink_to(sys.executable)
        config = self.write_kimi([self.stop_hook(
            command=self.command(KIMI_BRIDGE, python) + " --stop-feedback")])
        proc = self.run_doctor("kimi", config, "--probe")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["feedback"]["local_probe"], "PASS")

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

        versions = (
            ("0.42.0", "kimi, version 0.42.0", "CURRENT", 0),
            ("2.1.1", "Kimi Code CLI 2.1.1", "CURRENT", 0),
            ("2.3.0", "2.3.0", "CURRENT", 0),
            ("2.3.1", "2.3.1", "STALE", 1),
        )
        for version, output, status, exit_code in versions:
            with self.subTest(version=version):
                host = self.host_executable(output, f"kimi-{version}")
                proc = self.run_doctor(
                    "kimi", config, "--check-drift",
                    "--host-executable", str(host))
                self.assertEqual(proc.returncode, exit_code, proc.stderr)
                report = json.loads(proc.stdout)
                self.assertEqual(report["profile"], "kimi-code-pretooluse-bash-v1")
                self.assertEqual(report["host_version"], version)
                self.assertEqual(report["drift_status"], status)
                self.assertEqual(report["host_interception"], "UNVERIFIED")
                self.assertEqual(report["feedback"]["model_delivery"], "UNVERIFIED")


if __name__ == "__main__":
    unittest.main()
