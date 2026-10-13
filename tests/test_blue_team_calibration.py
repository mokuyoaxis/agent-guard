"""Failure gates in the portable example; no observer or model is started."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "blue_team_calibration_example", ROOT / "docs/lab/examples/blue-team-calibration.py")
example = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(example)


class CalibrationFailureGates(unittest.TestCase):
    def run_example(self, output):
        with patch.object(sys, "argv", ["calibration", "--output-dir", str(output)]):
            with contextlib.redirect_stdout(io.StringIO()):
                return example.main()

    def test_fourth_report_failed_criterion_cannot_pass_overall(self):
        def result(case, destination):
            return {"report": {"result": "COMPLETE", "observer": {"health": "HEALTHY"},
                               "security_outcome": "CALIBRATION_ONLY",
                               "criterion": {"status": "FAIL" if case == "snapshot-positive" else "PASS"}}}

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "run"
            with patch.object(example, "run_case", side_effect=result) as run:
                self.assertEqual(self.run_example(output), 4)
            summary = json.loads((output / "summary.json").read_text())
            self.assertEqual(run.call_count, 4)
            self.assertEqual(summary["result"], "INCONCLUSIVE")
            self.assertEqual(summary["controls"][-1]["criterion"], "FAIL")

    def test_startup_error_stops_next_control_and_retains_partial_evidence(self):
        def fail(case, destination):
            destination.mkdir()
            (destination / "observer.log").write_text("synthetic startup failure\n")
            raise example.LabError("observer did not become ready in time")

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "run"
            with patch.object(example, "run_case", side_effect=fail) as run:
                self.assertEqual(self.run_example(output), 4)
            self.assertEqual(run.call_count, 1)
            self.assertEqual((output / "clean/observer.log").read_text(), "synthetic startup failure\n")
            self.assertEqual(json.loads((output / "summary.json").read_text())["result"], "INCONCLUSIVE")

    def test_existing_directory_is_refused_without_running_core_or_overwriting(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            marker = output / "summary.json"
            marker.write_text("existing user output\n")
            with patch.object(example, "run_case") as run:
                with self.assertRaises(FileExistsError):
                    self.run_example(output)
            run.assert_not_called()
            self.assertEqual(marker.read_text(), "existing user output\n")


if __name__ == "__main__":
    unittest.main()
