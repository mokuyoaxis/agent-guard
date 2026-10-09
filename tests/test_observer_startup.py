"""Observer startup must preserve cancellation, diagnosis and the health gate."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from core import lab


class ObserverStartupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="guard-observer-startup-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        prepared = lab.prepare_run(self.root / "fixture", self.root / "evidence",
                                   "clean", model_usage="none")
        self.evidence = Path(prepared["evidence_dir"])
        self.fixture = Path(prepared["fixture_dir"])
        self.control = json.loads((self.evidence / "control.json").read_text())
        self.addCleanup(lab._CHILD_PROCESSES.pop, str(self.evidence), None)

    def stop(self):
        lab._write_new_json(self.evidence / "stop.request",
                            {"schema_version": lab.SCHEMA_VERSION, "requested_at": lab._now(),
                             "reason": "OBSERVER_STARTUP_TIMEOUT"})

    def run_child(self, duration=1):
        return lab.run_observer(self.evidence, duration, self.control["control_token"])

    def assert_failed_startup(self):
        state = lab._observer_state(self.evidence)
        self.assertEqual(state["status"], "FAILED")
        self.assertEqual(state["reason"], "OBSERVER_STARTUP_CANCELLED")
        report = lab.build_report(self.evidence)
        self.assertEqual(report["observer"]["health"], "INCONCLUSIVE")
        self.assertEqual(report["result"], "INCONCLUSIVE")
        return lab._load_events(self.evidence)[0]

    def test_stop_before_child_start_never_binds_or_publishes_endpoint(self):
        self.stop()
        with mock.patch.object(lab, "_LabHTTPServer", wraps=lab._LabHTTPServer) as server:
            self.assertEqual(self.run_child(), 1)
        server.assert_not_called()
        self.assertFalse((self.fixture / ".guard-lab/endpoint.json").exists())
        self.assertFalse((self.fixture / "bait/url.txt").exists())
        events = self.assert_failed_startup()
        self.assertNotIn("OBSERVER_READY", [event["type"] for event in events])

    def test_stop_during_endpoint_write_prevents_ready_event(self):
        original = lab._write_new_json
        def write(path, value, *args, **kwargs):
            result = original(path, value, *args, **kwargs)
            if Path(path).name == "endpoint.json":
                self.stop()
            return result
        with mock.patch.object(lab, "_write_new_json", side_effect=write):
            self.assertEqual(self.run_child(), 1)
        events = self.assert_failed_startup()
        self.assertNotIn("OBSERVER_READY", [event["type"] for event in events])

    def test_stop_during_ready_write_cannot_be_reported_healthy(self):
        original = lab._write_observer_state
        def write(evidence, state):
            if state["status"] == "READY":
                self.stop()
            return original(evidence, state)
        with mock.patch.object(lab, "_write_observer_state", side_effect=write):
            self.assertEqual(self.run_child(), 1)
        self.assert_failed_startup()

    def test_runtime_failure_logs_static_phase_without_error_or_secrets(self):
        marker = "SYNTHETIC_STARTUP_ERROR_PRIVATE_6942"
        stream = io.StringIO()
        with contextlib.redirect_stderr(stream), mock.patch.object(
                lab, "_LabHTTPServer", side_effect=OSError(marker)):
            with self.assertRaises(OSError):
                self.run_child()
        state = lab._observer_state(self.evidence)
        self.assertEqual(state["status"], "FAILED")
        self.assertEqual(state["reason"], "OBSERVER_RUNTIME_FAILURE")
        self.assertIn("stage=BINDING", stream.getvalue())
        self.assertIn("stage=FAILED", stream.getvalue())
        for value in [marker, self.control["control_token"], self.control["route_token"],
                      str(self.evidence), str(self.fixture)]:
            self.assertNotIn(value, stream.getvalue())

    def test_successful_startup_has_bounded_phase_trace(self):
        stream = io.StringIO()
        with contextlib.redirect_stderr(stream):
            self.assertEqual(self.run_child(), 0)
        notes = stream.getvalue().splitlines()
        self.assertLessEqual(len(notes), 10)
        for stage in ["VALIDATING", "VALIDATED", "BINDING", "BOUND", "ENDPOINT_WRITTEN",
                      "PUBLISHING_READY", "READY"]:
            self.assertTrue(any("stage=" + stage + " " in line for line in notes), stage)
        self.assertEqual(lab._observer_state(self.evidence)["status"], "EXPIRED")

    def test_timeout_reaps_local_child_and_keeps_original_error(self):
        process = mock.Mock()
        process.poll.return_value = None
        process.wait.return_value = 0
        now = [0.0]
        def sleep(seconds):
            now[0] += 6
        with mock.patch.object(lab.subprocess, "Popen", return_value=process), \
                mock.patch.object(lab.time, "monotonic", side_effect=lambda: now[0]), \
                mock.patch.object(lab.time, "sleep", side_effect=sleep):
            with self.assertRaisesRegex(lab.LabError, "^observer did not become ready in time$"):
                lab.arm_observer(self.evidence, 10)
        self.assertTrue((self.evidence / "stop.request").is_file())
        process.wait.assert_called_once_with(timeout=2)
        self.assertNotIn(str(self.evidence), lab._CHILD_PROCESSES)
        notes = (self.evidence / "observer.log").read_text()
        self.assertIn("stage=SPAWNING", notes)
        self.assertIn("stage=SPAWNED", notes)
        self.assertIn("stage=ARM_TIMEOUT", notes)
        self.assertEqual(lab.build_report(self.evidence)["result"], "INCONCLUSIVE")

    def test_failed_child_reap_timeout_preserves_lab_error_and_process(self):
        process = mock.Mock()
        process.wait.side_effect = subprocess.TimeoutExpired("synthetic observer", 2)
        with mock.patch.object(lab.subprocess, "Popen", return_value=process), \
                mock.patch.object(lab, "_observer_state", side_effect=[None, {"status": "FAILED"}]):
            with self.assertRaisesRegex(lab.LabError, "^observer failed its startup self-check$"):
                lab.arm_observer(self.evidence, 10)
        self.assertIs(lab._CHILD_PROCESSES[str(self.evidence)], process)


    def test_ready_write_crossing_duration_does_not_hide_startup_cancel(self):
        original = lab._write_observer_state
        now = [0.0]
        def write(evidence, state):
            if state["status"] == "READY":
                self.stop()
                now[0] = 20.0
            return original(evidence, state)
        with mock.patch.object(lab.time, "monotonic", side_effect=lambda: now[0]), \
                mock.patch.object(lab, "_write_observer_state", side_effect=write):
            self.assertEqual(self.run_child(), 1)
        self.assert_failed_startup()

    def test_ordinary_stop_after_ready_remains_healthy(self):
        original = lab._write_observer_state
        def write(evidence, state):
            result = original(evidence, state)
            if state["status"] == "READY":
                lab._write_new_json(self.evidence / "stop.request",
                                    {"schema_version": lab.SCHEMA_VERSION,
                                     "requested_at": lab._now()})
            return result
        with mock.patch.object(lab, "_write_observer_state", side_effect=write):
            self.assertEqual(self.run_child(), 0)
        self.assertEqual(lab._observer_state(self.evidence)["status"], "STOPPED")
        self.assertEqual(lab.build_report(self.evidence)["observer"]["health"], "HEALTHY")

    def test_diagnostic_stream_failure_does_not_fail_healthy_observer(self):
        stream = mock.Mock()
        stream.write.side_effect = OSError("synthetic diagnostic failure")
        with contextlib.redirect_stderr(stream):
            self.assertEqual(self.run_child(), 0)
        self.assertEqual(lab.build_report(self.evidence)["observer"]["health"], "HEALTHY")

    def test_unreadable_state_is_noted_once_and_remains_inconclusive(self):
        process = mock.Mock()
        process.poll.return_value = None
        process.wait.return_value = 0
        now = [0.0]
        def sleep(seconds):
            now[0] += 3
        with mock.patch.object(lab.subprocess, "Popen", return_value=process), \
                mock.patch.object(lab.time, "monotonic", side_effect=lambda: now[0]), \
                mock.patch.object(lab.time, "sleep", side_effect=sleep), \
                mock.patch.object(lab, "_observer_state", side_effect=[
                    None, lab.LabError("synthetic invalid state"),
                    lab.LabError("synthetic invalid state")]):
            with self.assertRaisesRegex(lab.LabError, "observer did not become ready in time"):
                lab.arm_observer(self.evidence, 10)
        notes = (self.evidence / "observer.log").read_text()
        self.assertEqual(notes.count("stage=ARM_STATE_UNREADABLE"), 1)
        self.assertNotIn("synthetic invalid state", notes)

    def test_timeout_keeps_live_child_reference_when_reap_is_bounded(self):
        process = mock.Mock()
        process.poll.return_value = None
        process.wait.side_effect = subprocess.TimeoutExpired("synthetic observer", 2)
        now = [0.0]
        def sleep(seconds):
            now[0] += 6
        with mock.patch.object(lab.subprocess, "Popen", return_value=process), \
                mock.patch.object(lab.time, "monotonic", side_effect=lambda: now[0]), \
                mock.patch.object(lab.time, "sleep", side_effect=sleep):
            with self.assertRaisesRegex(lab.LabError, "observer did not become ready in time"):
                lab.arm_observer(self.evidence, 10)
        self.assertIs(lab._CHILD_PROCESSES[str(self.evidence)], process)
        process.terminate.assert_not_called()
        process.kill.assert_not_called()


if __name__ == "__main__":
    unittest.main()
