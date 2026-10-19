"""User-visible recovery, coverage and GC information; verdicts stay unchanged."""
import json
from pathlib import Path
import shlex
import subprocess
import sys
import unittest

from tests.helpers import RepoFixture, git_available


SCRIPTS = Path(__file__).resolve().parent.parent / "skills/delete-guard/scripts"


@unittest.skipUnless(git_available(), "git required")
class UserFeedbackTests(RepoFixture):
    def cli(self, script, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPTS / script), *args], cwd=self.root,
            capture_output=True, text=True, timeout=30)

    def assert_gc_guidance(self, result):
        commands = result["gc"]
        prefix = [sys.executable, str(SCRIPTS / "gc.py")]
        self.assertEqual(shlex.split(commands["plan_command"]), prefix)
        self.assertEqual(shlex.split(commands["purge_eligible_command"]),
                         prefix + ["--execute", "--all-eligible"])
        self.assertEqual(shlex.split(commands["purge_transaction_command"]),
                         prefix + ["--execute", "--txid", "<txid>"])
        self.assertIn("same working directory", commands["note"])

    def test_unrecognized_commands_keep_noop_with_honest_explanation(self):
        for command in ("npm run clean", "make clean", "git stash clear",
                        'python -c "import shutil; shutil.rmtree(\'src\')"'):
            with self.subTest(command=command):
                proc = self.cli("check.py", "--json", "--", command)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                report = json.loads(proc.stdout)
                self.assertEqual((report["decision"], report["code"]),
                                 ("ALLOW", "ALLOW_NOOP"))
                self.assertIn("Unrecognized command effects have not been checked",
                              report["explanation"])
                self.assertNotIn("Nothing to delete", report["explanation"])
                self.assertNotIn("storage_notice", report)
        self.assertTrue(Path(self.root, "src/main.py").is_file())

    def test_text_check_shows_explanation_without_echoing_command(self):
        marker = "private_input_do_not_echo"
        proc = self.cli("check.py", "--", "make clean " + marker)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Unrecognized command effects have not been checked", proc.stdout)
        self.assertNotIn(marker, proc.stdout + proc.stderr)

    def test_relocation_reports_retained_data_and_working_gc_plan(self):
        self.write("valuable.txt", "recover these bytes")
        proc = self.cli("safe_delete.py", "--json", "valuable.txt")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = json.loads(proc.stdout)
        self.assertEqual(report["verdict"]["decision"], "RELOCATE")
        self.assertIn("still use disk space", report["storage_notice"])
        self.assert_gc_guidance(report)
        manifest = Path(self.root, ".agent-trash/manifest.jsonl")
        before = manifest.read_bytes()
        plan = subprocess.run(shlex.split(report["gc"]["plan_command"]) + ["--json"],
                              cwd=self.root, capture_output=True, text=True, timeout=30)
        self.assertEqual(plan.returncode, 0, plan.stderr)
        self.assertEqual(json.loads(plan.stdout)["mode"], "plan")
        self.assertEqual(manifest.read_bytes(), before)
        restored = self.cli("restore.py", report["txid"])
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertEqual(Path(self.root, "valuable.txt").read_text(), "recover these bytes")

    def test_text_relocation_gives_plan_and_selected_purge(self):
        self.write("valuable.txt")
        proc = self.cli("safe_delete.py", "valuable.txt")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("still use disk space", proc.stdout)
        self.assertIn("--execute --all-eligible", proc.stdout)
        self.assertIn("--execute --txid", proc.stdout)
        self.assertIn("txid=", proc.stdout)

    def test_direct_artifact_delete_has_no_relocation_notice(self):
        self.write("node_modules/pkg/index.js")
        proc = self.cli("safe_delete.py", "--json", "node_modules")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = json.loads(proc.stdout)
        self.assertEqual(report["verdict"]["code"], "ALLOW_REGENERABLE")
        self.assertNotIn("storage_notice", report)
        self.assertFalse(Path(self.root, "node_modules").exists())

    def test_no_match_and_dry_run_do_not_claim_a_move(self):
        self.write("valuable.txt")
        for args in (("--json", "no-matches-*.bin"),
                     ("--json", "--dry-run", "valuable.txt")):
            with self.subTest(args=args):
                proc = self.cli("safe_delete.py", *args)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertNotIn("storage_notice", json.loads(proc.stdout))
                self.assertTrue(Path(self.root, "valuable.txt").is_file())

    def test_enforced_relocation_keeps_txid_and_storage_notice(self):
        self.write("valuable.txt")
        proc = self.cli("check.py", "--enforce", "--json", "--", "rm valuable.txt")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = json.loads(proc.stdout)
        self.assertEqual(report["decision"], "ALLOW")
        self.assertIn("still use disk space", report["storage_notice"])
        self.assert_gc_guidance(report)
        txid = report["compensations"][0]["txid"]
        self.assertEqual(self.cli("restore.py", txid).returncode, 0)
        self.assertTrue(Path(self.root, "valuable.txt").is_file())

    def test_partial_relocation_is_not_reported_as_success(self):
        self.write("valuable.txt", "recover")
        proc = self.cli("check.py", "--enforce", "--json", "--",
                        "rm valuable.txt missing.txt")
        self.assertEqual(proc.returncode, 2, proc.stderr)
        report = json.loads(proc.stdout)
        self.assertEqual(report["decision"], "BLOCK")
        self.assertEqual(report["compensations"][0]["status"], "partial")
        self.assertIn("still use disk space", report["storage_notice"])
        txid = report["compensations"][0]["txid"]
        self.assertEqual(self.cli("restore.py", txid).returncode, 0)
        self.assertEqual(Path(self.root, "valuable.txt").read_text(), "recover")

    def test_text_safe_delete_partial_result_shows_recovery_and_storage(self):
        self.write("valuable.txt", "recover")
        proc = self.cli("safe_delete.py", "valuable.txt", "missing.txt")
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("BLOCKED: partial relocation", proc.stdout)
        self.assertIn("txid=", proc.stdout)
        self.assertIn("still use disk space", proc.stdout)
        self.assertIn("--execute --all-eligible", proc.stdout)
        listing = self.cli("restore.py", "list", "--json")
        txid = json.loads(listing.stdout)["transactions"][-1]["txid"]
        self.assertIn(txid, proc.stdout)
        self.assertEqual(self.cli("restore.py", txid).returncode, 0)
        self.assertEqual(Path(self.root, "valuable.txt").read_text(), "recover")

    def test_status_does_not_certify_host_activation_or_auto_purge(self):
        self.write("valuable.txt", "retained")
        self.assertEqual(self.cli("safe_delete.py", "valuable.txt").returncode, 0)
        manifest = Path(self.root, ".agent-trash/manifest.jsonl")
        before = manifest.read_bytes()
        proc = self.cli("status.py", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = json.loads(proc.stdout)
        self.assertIn("not whether a host loaded the guard", report["host_status_notice"])
        self.assertGreaterEqual(report["usage"]["bytes"], len("retained"))
        self.assert_gc_guidance(report)
        text = self.cli("status.py")
        self.assertEqual(text.returncode, 0, text.stderr)
        self.assertIn("GC is manual", text.stdout)
        self.assertIn("still use disk space", text.stdout)
        self.assertEqual(manifest.read_bytes(), before)

    def test_gc_text_requires_selection_and_default_remains_plan(self):
        proc = self.cli("gc.py")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("No recovery copies were removed", proc.stdout)
        self.assertIn("--execute --all-eligible", proc.stdout)
        self.assertIn("--execute --txid", proc.stdout)
        proc = self.cli("gc.py", "--execute")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("nothing selected", proc.stderr)
