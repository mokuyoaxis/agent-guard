"""Lifecycle calibration through the public controller and owned fixtures."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from core import export_scan, lab, lifecycle_calibration as calibration


class LifecycleCalibrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="owned-calibration-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def invoke(self, *args, env=None):
        return subprocess.run(
            [sys.executable, "-B", str(calibration.ROOT / "guard_lab.py"), *args],
            cwd=self.root, env=env, capture_output=True, encoding="utf-8", timeout=120,
        )

    def test_run_case_passes_supported_pairs_and_keeps_l0_contract(self):
        output = self.root / "run"
        unrelated = self.root / "unrelated"
        unrelated.mkdir()
        marker = unrelated / "keep.txt"
        marker.write_bytes(b"must remain in the caller's directory")
        env = dict(os.environ, AGENT_GUARD_WORKSPACE=str(unrelated),
                   AGENT_GUARD_TRASH=str(unrelated / "undesired"), GIT_DIR=str(unrelated / "git"),
                   GIT_TEMPLATE_DIR=str(unrelated / "template"))
        completed = self.invoke("run", "--case", lab.LIFECYCLE_CASE, "--output-dir", str(output), "--json", env=env)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        run = json.loads(completed.stdout)
        report = run["report"]
        self.assertEqual(report["report_type"], calibration.REPORT_TYPE)
        self.assertEqual((report["result"], report["evidence_level"], report["model_usage"],
                          report["security_outcome"]), ("COMPLETE", "L0", "NONE", "CALIBRATION_ONLY"))
        self.assertFalse(report["raw_values_retained"])
        summary = report["summary"]
        unsupported = 0 if export_scan.supports_descriptor_scan() else 2
        supported_pairs = 11 - unsupported // 2
        self.assertEqual((summary["passed"], summary["unsupported"], summary["total_checks"]), (22 - unsupported, unsupported, 22))
        self.assertEqual((summary["pairs_passed"], summary["pairs_supported"], summary["pairs_total"]),
                         (supported_pairs, supported_pairs, 11))
        self.assertEqual(len(report["checks"]), 22)
        unsupported_checks = {row["pair"] for row in report["checks"] if row["status"] == "UNSUPPORTED_PLATFORM"}
        self.assertEqual(unsupported_checks, {"exfil_export_preflight"} if unsupported else set())
        self.assertEqual(summary["block_checks"]["passed"], supported_pairs)
        self.assertEqual(summary["allow_checks"]["passed"], supported_pairs)
        self.assertEqual(list(unrelated.iterdir()), [marker])
        self.assertEqual(marker.read_bytes(), b"must remain in the caller's directory")

        evidence = list(output.iterdir())
        self.assertEqual([p.name for p in evidence], ["evidence"])
        self.assertEqual({p.name for p in evidence[0].iterdir()}, {"run.json", "report.json"})
        stored = evidence[0] / "report.json"
        self.assertEqual(json.loads(stored.read_text(encoding="utf-8")), report)
        self.assertNotIn(calibration.ORIGINAL.strip(), stored.read_text(encoding="utf-8"))
        before = stored.read_bytes(), stored.stat().st_mtime_ns
        recalled = self.invoke("report", "--evidence-dir", str(evidence[0]), "--json")
        self.assertEqual(recalled.returncode, 0, recalled.stderr)
        self.assertEqual(json.loads(recalled.stdout), report)
        self.assertEqual((stored.read_bytes(), stored.stat().st_mtime_ns), before)

    def test_prepare_compare_and_existing_output_cannot_reinterpret_calibration(self):
        output = self.root / "existing"
        output.mkdir()
        (output / "owned.txt").write_bytes(b"existing")
        rejected = self.invoke("run", "--case", lab.LIFECYCLE_CASE, "--output-dir", str(output))
        self.assertEqual(rejected.returncode, 1)
        self.assertEqual(list(p.name for p in output.iterdir()), ["owned.txt"])
        invalid = self.invoke("prepare", "--case", lab.LIFECYCLE_CASE, "--fixture", str(self.root / "unused"),
                              "--evidence-dir", str(self.root / "unused-evidence"))
        self.assertEqual(invalid.returncode, 2)
        self.assertFalse((self.root / "unused").exists())

        with mock.patch.object(calibration, "_isolated_checks", return_value=self.rows()):
            calibration_run = lab.run_case(lab.LIFECYCLE_CASE, self.root / "synthetic")
        normal = lab.prepare_run(self.root / "control", self.root / "control-evidence", "clean")
        with mock.patch.object(lab, "_build_observer_report") as rebuild:
            with self.assertRaisesRegex(lab.LabError, "lifecycle calibration"):
                lab.compare_runs(Path(normal["evidence_dir"]), Path(calibration_run["evidence_dir"]))
            rebuild.assert_not_called()

    def rows(self, first="PASS"):
        rows = [calibration._row(group, pair, polarity, "PASS", "CALIBRATION_PASS")
                for group, pair, polarity, _ in calibration.CHECKS]
        rows[0]["status"] = first
        return rows

    def test_failure_and_indeterminate_exit_codes_and_write_failure(self):
        for index, (status, exit_code) in enumerate((("FAIL", 2), ("ERROR", 4))):
            output = self.root / f"run-{index}"
            stdout = io.StringIO()
            with mock.patch.object(calibration, "_isolated_checks", return_value=self.rows(status)):
                with redirect_stdout(stdout):
                    actual = lab.cli_main(["run", "--case", lab.LIFECYCLE_CASE, "--output-dir", str(output), "--json"])
            self.assertEqual(actual, exit_code)
            report = json.loads(stdout.getvalue())["report"]
            self.assertEqual(report["summary"]["passed"], 21)
            self.assertEqual(lab.build_report(output / "evidence"), report)

        output = self.root / "write-error"
        stdout = io.StringIO()
        with mock.patch.object(calibration, "_isolated_checks", return_value=self.rows()):
            with mock.patch.object(lab, "_atomic_write_json", side_effect=OSError("fixture marker")):
                with redirect_stdout(stdout):
                    code = lab.cli_main(["run", "--case", lab.LIFECYCLE_CASE, "--output-dir", str(output), "--json"])
        self.assertEqual(code, 4)
        report = json.loads(stdout.getvalue())["report"]
        self.assertEqual(report["criterion"]["reason"], "EVIDENCE_WRITE_FAILED")
        self.assertNotIn("fixture marker", stdout.getvalue())

    def test_worker_timeout_and_interrupt_report_fixed_codes(self):
        for error, code in ((subprocess.TimeoutExpired(["worker"], 1), "CALIBRATION_WORKER_TIMEOUT"),
                            (KeyboardInterrupt(), "CALIBRATION_INTERRUPTED")):
            with self.subTest(code=code):
                with mock.patch.object(calibration.subprocess, "run", side_effect=error):
                    report = calibration.run_checks(1)
                self.assertEqual((report["result"], report["summary"]["errors"]), ("INCONCLUSIVE", 22))
                self.assertEqual({row["code"] for row in report["checks"]}, {code})

    def test_unsupported_export_pair_is_separate_from_the_supported_denominator(self):
        rows = self.rows()
        for row in rows:
            if row["pair"] == "exfil_export_preflight":
                row.update(status="UNSUPPORTED_PLATFORM", code="UNSUPPORTED_PLATFORM")
        projected = calibration.summarize(rows)
        self.assertEqual(projected["result"], "COMPLETE")
        summary = projected["summary"]
        self.assertEqual((summary["pairs_passed"], summary["pairs_supported"], summary["pairs_total"]), (10, 10, 11))
        self.assertEqual((summary["passed"], summary["unsupported"], summary["total_checks"]), (20, 2, 22))

    def test_invalid_duration_refuses_before_work(self):
        output = self.root / "unused-output"
        with redirect_stderr(io.StringIO()):
            code = lab.cli_main(["run", "--case", lab.LIFECYCLE_CASE, "--output-dir", str(output), "--duration", "0"])
        self.assertEqual(code, 1)
        self.assertFalse(output.exists())
