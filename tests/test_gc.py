"""B4 retention policy: GC_ELIGIBLE marking, explicit purge, tombstones,
and the never-fallback-to-deletion storage principle."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.helpers import RepoFixture, git_available

from core import AUDIT_NAME
from core.recovery import RecoveryEngine, StorageUnavailable
from core.classifier import classify_paths

SCRIPTS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "skills", "delete-guard", "scripts")


def run(script, *args, cwd):
    return subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, script), *args],
        capture_output=True, text=True, cwd=cwd, timeout=60)


def specs_for(engine, rels):
    return classify_paths(rels, engine.workspace, engine.workspace,
                          engine.trash_root)


@unittest.skipUnless(git_available(), "git required")
class GcPlanTests(RepoFixture):
    def write_tx(self, rel, content="x", age_days=0.0):
        path = self.write(rel, content)
        engine = RecoveryEngine(self.root)
        report = engine.relocate(specs_for(engine, [rel]))
        if age_days:
            lines = Path(engine.manifest_path).read_text().splitlines()
            for i, line in enumerate(lines):
                rec = json.loads(line)
                if rec.get("txid") == report["txid"] and \
                        rec.get("type") == "tx-start":
                    old = time.time() - age_days * 86400
                    rec["ts"] = time.strftime(
                        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(old))
                    lines[i] = json.dumps(rec, sort_keys=True)
            Path(engine.manifest_path).write_text("\n".join(lines) + "\n")
        return engine, report["txid"]

    def test_fresh_tx_not_eligible(self):
        engine, _ = self.write_tx("a.txt")
        plan = engine.gc_plan()
        self.assertEqual(plan["eligible"], [])

    def test_age_makes_tx_eligible(self):
        engine, txid = self.write_tx("old.txt", age_days=31)
        plan = engine.gc_plan()
        reasons = {e["txid"]: e["reason"] for e in plan["eligible"]}
        self.assertEqual(reasons.get(txid), "age")

    def test_capacity_marks_oldest_first(self):
        engine, old_id = self.write_tx("big-old.bin", content="B" * 2048,
                                       age_days=1)
        self.write_tx("new.bin", content="N" * 4096)
        plan = engine.gc_plan(size_limit_bytes=4096)
        eligible_ids = [e["txid"] for e in plan["eligible"]]
        self.assertEqual(eligible_ids, [old_id])  # oldest first, cap met

    def test_execute_purges_dir_and_writes_tombstone(self):
        engine, txid = self.write_tx("gone.txt", age_days=31)
        report = engine.gc_execute([txid])
        self.assertEqual(report["purged"], [txid])
        self.assertFalse(os.path.isdir(
            os.path.join(engine.trash_root, txid)))
        types = [json.loads(line)["type"] for line in
                 Path(engine.manifest_path).read_text().splitlines()
                 if line.strip()]
        self.assertIn("purged", types)

    def test_execute_reports_unknown_txid(self):
        engine, _ = self.write_tx("x.txt")
        report = engine.gc_execute(["no-such-tx"])
        self.assertEqual(report["missing"], ["no-such-tx"])

    def test_invalid_id_rejects_entire_batch_before_purge(self):
        engine, txid = self.write_tx("x.txt")
        sentinel = self.write("sentinel/keep.txt", "keep")
        with self.assertRaisesRegex(ValueError, "invalid quarantine"):
            engine.gc_execute([txid, "../sentinel"])
        self.assertTrue(os.path.isdir(os.path.join(engine.trash_root, txid)))
        self.assertEqual(Path(sentinel).read_text(), "keep")

    def test_unmanaged_and_symlinked_directories_are_not_purged(self):
        engine, txid = self.write_tx("x.txt")
        unmanaged = Path(engine.trash_root, "unmanaged")
        unmanaged.mkdir()
        with self.assertRaisesRegex(ValueError, "unmanaged quarantine"):
            engine.gc_execute(["unmanaged"])
        self.assertTrue(unmanaged.is_dir())
        link = Path(engine.trash_root, "linked")
        link.symlink_to(Path(engine.trash_root, txid), target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlinked transaction"):
            engine.gc_execute(["linked"])
        self.assertTrue(link.is_symlink())

    def test_failed_remove_does_not_write_purged_tombstone(self):
        engine, txid = self.write_tx("x.txt")
        with mock.patch("core.recovery.shutil.rmtree",
                        side_effect=OSError("synthetic remove failure")):
            with self.assertRaisesRegex(OSError, "synthetic remove failure"):
                engine.gc_execute([txid])
        self.assertTrue(os.path.isdir(os.path.join(engine.trash_root, txid)))
        records = [json.loads(line) for line in
                   Path(engine.manifest_path).read_text().splitlines()]
        self.assertFalse(any(r.get("type") == "purged" for r in records))


@unittest.skipUnless(git_available(), "git required")
class GcExecuteCliTests(RepoFixture):
    """Regression: gc.py --execute must exit 0 and record the purge.

    The bug this guards against: gc.py read the audit filename off the
    `core.audit` module (`audit.AUDIT_NAME`) instead of `core`, so the
    purge committed and the audit append raised AttributeError, which the
    top-level handler turned into exit 1 - data destroyed, receipt lost,
    exit code inverted.
    """

    def audit_records(self):
        path = Path(self.root, ".agent-trash", AUDIT_NAME)
        return [json.loads(line) for line in
                path.read_text().splitlines() if line.strip()]

    def quarantine(self, rel="build/a.o", content="x"):
        self.write(rel, content)
        proc = run("safe_delete.py", "--json", os.path.dirname(rel) or rel,
                   cwd=self.root)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)["txid"]

    def test_execute_exits_zero_and_writes_audit_record(self):
        txid = self.quarantine()

        proc = run("gc.py", "--execute", "--txid", txid, cwd=self.root)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(f"purged: {txid}", proc.stdout)

        gc_events = [r for r in self.audit_records()
                     if r.get("event") == "gc"]
        self.assertEqual(len(gc_events), 1)
        self.assertEqual(gc_events[-1]["action"], "PURGED")
        self.assertEqual(gc_events[-1]["purged"], [txid])

    def test_execute_json_prints_report_and_audits(self):
        txid = self.quarantine()
        proc = run("gc.py", "--execute", "--json", "--txid", txid,
                   cwd=self.root)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["mode"], "execute")
        self.assertEqual(payload["purged"], [txid])

        gc_events = [r for r in self.audit_records()
                     if r.get("event") == "gc"]
        self.assertEqual(gc_events[-1]["purged"], [txid])

    def test_execute_audit_record_survives_unknown_txid(self):
        txid = self.quarantine()
        proc = run("gc.py", "--execute", "--txid", txid,
                   "--txid", "no-such-tx", cwd=self.root)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        gc_events = [r for r in self.audit_records()
                     if r.get("event") == "gc"]
        self.assertEqual(gc_events[-1]["purged"], [txid])
        self.assertEqual(gc_events[-1]["missing"], ["no-such-tx"])

    def test_cli_rejects_traversal_without_touching_workspace(self):
        txid = self.quarantine()
        sentinel = self.write("sentinel/keep.txt", "keep")
        proc = run("gc.py", "--execute", "--txid", txid,
                   "--txid", "../sentinel", cwd=self.root)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("invalid quarantine transaction id", proc.stderr)
        self.assertTrue(Path(sentinel).is_file())
        self.assertTrue(Path(self.root, ".agent-trash", txid).is_dir())
        self.assertFalse(any(r.get("event") == "gc-intent"
                             for r in self.audit_records()))


@unittest.skipUnless(git_available(), "git required")
class StorageFailureTests(RepoFixture):
    def test_relocate_reports_storage_failure_and_keeps_origin(self):
        engine = RecoveryEngine(self.root)
        self.write("precious.txt", "keep")
        specs = specs_for(engine, ["precious.txt"])

        def full_disk(_self, src, dest):
            raise StorageUnavailable(28, "No space left on device")

        with mock.patch.object(RecoveryEngine, "_move", full_disk):
            report = engine.relocate(specs)

        self.assertTrue(report["storage_failure"])
        self.assertEqual(report["moved"], [])
        # the hard principle: origin untouched, nothing fell back to deletion
        self.assertTrue(os.path.exists(
            os.path.join(self.root, "precious.txt")))
        self.assertEqual(Path(self.root, "precious.txt").read_text(), "keep")


if __name__ == "__main__":
    unittest.main()
