"""M3b restoration contracts, using only disposable recovery fixtures."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from adapters.mcp.operations import Operations
from core import AUDIT_NAME, messages
from core.classifier import PathSpec
from core.recovery import RecoveryEngine
from tests.helpers import git_available


SCRIPTS = Path(__file__).resolve().parents[1] / "skills/delete-guard/scripts"


class PartialRestoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="agent-guard-m3b-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / "workspace"
        self.root.mkdir()
        self.engine = RecoveryEngine(str(self.root))
        self.trash = Path(self.engine.trash_root)
        self.audit_path = self.trash / AUDIT_NAME
        self.ops = Operations(str(self.root))
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(("AGENT_GUARD_", "GIT_"))
                    and key not in ("PYTHONPATH", "PYTHONHOME")}
        self.env["PYTHONDONTWRITEBYTECODE"] = "1"

    def write(self, path, payload=b"synthetic copy"):
        result = self.root / path
        result.parent.mkdir(parents=True, exist_ok=True)
        result.write_bytes(payload)
        return result

    def relocate(self, *paths):
        result = self.engine.relocate([PathSpec(raw=path.name, resolved=str(path)) for path in paths])
        self.assertEqual(len(result["moved"]), len(paths), result)
        return result["txid"], {row["origin"]: Path(row["trash"]) for row in result["moved"]}

    def cli(self, script, *args, cwd=None):
        env = {**self.env, "AGENT_GUARD_WORKSPACE": str(self.root), "AGENT_GUARD_TRASH": str(self.trash)}
        proc = subprocess.run(
            [sys.executable, "-B", str(SCRIPTS / script), *args], cwd=cwd or self.root,
            env=env, capture_output=True, text=True, encoding="utf-8", timeout=45,
        )
        self.assertTrue(proc.stdout, proc.stderr)
        return proc.returncode, json.loads(proc.stdout)

    def restore_call(self, via, action, txid, paths=None, *, cwd=None, force=False):
        if via == "mcp":
            tool = "restore_preview" if action == "preview" else "restore"
            args = {"txid": txid, **({"paths": paths} if paths is not None else {})}
            response = self.ops.call(tool, args)
            return response["exit_code"], response["result"]

        flags = [f"--path={path}" for path in paths or ()]
        if force:
            flags.append("--force")
        return self.cli("restore.py", action, "--json", *flags, "--", txid, cwd=cwd)

    def tree(self, *, include_audit=True):
        return {
            path.relative_to(self.root).as_posix(): "directory" if path.is_dir() else path.read_bytes()
            for path in self.root.rglob("*")
            if include_audit or path != self.audit_path
        }

    def audit_event(self):
        records = [json.loads(line) for line in self.audit_path.read_text(encoding="utf-8").splitlines()]
        return records[-1]

    def transaction(self, txid):
        return self.engine.transactions()[txid]

    def journal(self):
        return Path(self.engine.manifest_path).read_bytes()

    def run_guidance(self, command):
        if os.name == "nt":
            shell = shutil.which("pwsh") or shutil.which("powershell")
            self.assertIsNotNone(shell, "Windows Core CI provides PowerShell")
            argv = [shell, "-NoProfile", "-NonInteractive", "-Command", command + "; exit $LASTEXITCODE"]
        else:
            argv = shlex.split(command)
        return subprocess.run(argv, cwd=self.base, env=self.env, capture_output=True,
                              text=True, encoding="utf-8", timeout=45)

    def check_cycle(self, via):
        first = self.write("sub/a.txt", b"nested bytes")
        directory = self.root / "whole dir"
        self.write("whole dir/inside.txt", b"directory bytes")
        remaining = self.write("-remaining.txt", b"remaining bytes")
        empty = self.root / "empty dir"
        empty.mkdir()
        txid, copies = self.relocate(first, directory, remaining, empty)

        before = self.tree()
        arguments = ["a.txt", "../whole dir", "a.txt"] if via == "cli" else [
            "sub/a.txt", "whole dir", str(first)]
        code, preview = self.restore_call(via, "preview", txid, arguments, cwd=first.parent)
        self.assertEqual(code, 0, preview)
        self.assertTrue(preview["can_restore"])
        self.assertEqual(preview["selected_paths"], [str(first), str(directory)])
        self.assertEqual(preview["omitted_relocate_count"], 2)
        self.assertEqual(preview["omitted_snapshot_count"], 0)
        self.assertEqual(self.tree(), before)
        command = preview["next_step"]["restore_command"]
        self.assertIn("--exact-txid", command)
        self.assertIn("AGENT_GUARD_WORKSPACE", command)
        self.assertIn("--path=" + str(first), command)
        if via == "cli":
            executed = self.run_guidance(command)
            self.assertEqual(executed.returncode, 0, executed.stderr)
        else:
            code, result = self.restore_call(via, "restore", txid, arguments)
            self.assertEqual(code, 0, result)
            self.assertEqual(result["restored"], [str(first), str(directory)])
        self.assertEqual(first.read_bytes(), b"nested bytes")
        self.assertEqual((directory / "inside.txt").read_bytes(), b"directory bytes")
        self.assertEqual(copies[str(remaining)].read_bytes(), b"remaining bytes")
        self.assertTrue(copies[str(empty)].is_dir())
        self.assertFalse(copies[str(first)].parent.exists())
        self.assertEqual(self.engine.read_manifest()[-1]["selected_paths"], [str(first), str(directory)])
        audit = self.audit_event()
        self.assertTrue(audit["selective"])
        self.assertEqual(audit["selected_count"], 2)
        self.assertNotIn("selected_paths", audit)
        self.assertNotIn(str(self.root), json.dumps(audit))

        if via == "cli":
            code, listed = self.cli("restore.py", "list", "--json")
            self.assertEqual(code, 0, listed)
        else:
            listed = self.ops.call("transactions", {})["result"]
        row = listed["transactions"][0]
        self.assertEqual((row["state"], row["restorable_items"], row["live_relocate_items"]), ("RESTORABLE", 2, 2))
        self.assertEqual(row["missing_relocate_items"], 0)
        self.assertEqual(row["pending_snapshot_items"], 0)
        self.assertEqual(row["retained_snapshot_items"], 0)
        _, remaining_preview = self.restore_call(via, "preview", txid)
        self.assertTrue(remaining_preview["can_restore"])
        self.assertEqual(remaining_preview["summary"]["already_restored_count"], 2)
        self.assertEqual(remaining_preview["summary"]["ready_count"], 2)

        for measure in (False, True):
            overview = self.ops.call("status", {"view": "overview", "measure_storage": measure})["result"]
            entry = overview["transactions"]["items"][0]
            self.assertEqual((entry["state"], entry["restorable_items"]), ("RESTORABLE", 2))
            if measure:
                self.assertEqual(overview["storage"]["top_transactions"][0]["state"], "RESTORABLE")
        self.assertEqual(self.ops.call("gc_plan", {})["result"]["total_bytes"], len(b"remaining bytes"))

        retry_paths = [remaining.name, empty.name] if via == "cli" else None
        code, result = self.restore_call(via, "restore", txid, retry_paths)
        self.assertEqual(code, 0, result)
        self.assertEqual(result["restored"], [str(remaining), str(empty)])
        if via == "mcp":
            self.assertEqual(result["already_restored"], [str(first), str(directory)])
        self.assertTrue(empty.is_dir())
        self.assertFalse((self.trash / txid).exists())
        self.assertEqual((self.transaction(txid)["state"], self.transaction(txid)["restorable_items"]),
                         ("RESTORED", 0))
        self.assertEqual(self.ops.call("gc_plan", {})["result"]["total_bytes"], 0)

    def test_cli_selection_query_retry_and_gc(self):
        self.check_cycle("cli")

    def test_mcp_selection_query_retry_and_gc(self):
        self.check_cycle("mcp")

    def test_invalid_selection_is_preflight_only_and_audited_by_cli(self):
        first = self.write("first.txt")
        second = self.write("second.txt")
        self.write("package/child.txt")
        package = self.root / "package"
        txid, copies = self.relocate(first, second, package)
        for paths in ([], [str(first), str(self.root / "unknown.txt")], [str(package / "child.txt")],
                      [str(self.base / "outside.txt")], [str(self.root / "*.txt")]):
            with self.subTest(paths=paths):
                before = self.tree()
                preview = self.engine.preview_restore(txid, paths=paths)
                self.assertFalse(preview["ok"], preview)
                self.assertFalse(preview["can_restore"])
                self.assertEqual(preview["selected_paths"], [])
                result = self.engine.restore(txid, paths=paths)
                self.assertFalse(result["ok"], result)
                self.assertEqual(result["restored"], [])
                self.assertEqual(self.tree(), before)

        self.assertTrue(self.engine.restore(txid, paths=[str(first)])["ok"])
        before = self.tree(include_audit=False)
        code, preview = self.restore_call("cli", "preview", txid, [second.name, first.name])
        self.assertEqual(code, 1, preview)
        self.assertEqual(preview["already_restored"], [str(first)])
        code, result = self.restore_call("cli", "restore", txid, [second.name, first.name])
        self.assertEqual(code, 1, result)
        self.assertEqual(result["restored"], [])
        self.assertEqual(result["selected_paths"], [])
        self.assertEqual(self.tree(include_audit=False), before)
        self.assertTrue(copies[str(second)].is_file())
        self.assertFalse(second.exists())
        record = self.audit_event()
        self.assertTrue(record["selective"])
        self.assertEqual(record["selected_count"], 0)
        self.assertNotIn(str(first), json.dumps(record))

    def test_known_conflict_stops_selection_and_cli_force_preserves_only_chosen_occupant(self):
        ready = self.write("ready.txt")
        conflict = self.write("conflict.txt", b"original")
        other = self.write("unselected.txt", b"old unselected")
        txid, copies = self.relocate(ready, conflict, other)
        self.write(conflict.name, b"current")
        self.write(other.name, b"current unselected")
        before = self.tree(include_audit=False)
        code, response = self.restore_call("mcp", "restore", txid, [ready.name, conflict.name])
        self.assertEqual(code, 3, response)
        self.assertEqual(response["conflicts"], [str(conflict)])
        self.assertEqual(response["restored"], [])
        self.assertEqual(self.tree(include_audit=False), before)

        self.assertEqual(self.restore_call("mcp", "restore", txid, [ready.name])[0], 0)
        code, forced = self.restore_call("cli", "restore", txid, [conflict.name], force=True)
        self.assertEqual(code, 0, forced)
        self.assertEqual(forced["restored"], [str(conflict)])
        self.assertEqual(conflict.read_bytes(), b"original")
        self.assertEqual(other.read_bytes(), b"current unselected")
        self.assertEqual(copies[str(other)].read_bytes(), b"old unselected")
        preserved = self.transaction(forced["backup_txids"][0])["items"]
        self.assertEqual([row["origin_path"] for row in preserved], [str(conflict)])
        self.assertEqual(Path(preserved[0]["trash_path"]).read_bytes(), b"current")

    def test_selected_missing_copy_stops_batch_but_unselected_missing_copy_remains_reportable(self):
        first = self.write("recoverable.txt")
        missing = self.write("missing.txt")
        txid, copies = self.relocate(first, missing)
        copies[str(missing)].rename(self.base / "held-missing-copy")
        before = self.tree()
        preview = self.engine.preview_restore(txid, paths=[str(first), str(missing)])
        self.assertFalse(preview["can_restore"])
        self.assertEqual(preview["summary"]["missing_copy_count"], 1)
        result = self.engine.restore(txid, paths=[str(first), str(missing)])
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["restored"], [])
        self.assertEqual(self.tree(), before)
        self.assertTrue(self.engine.restore(txid, paths=[str(first)])["ok"])
        tx = self.transaction(txid)
        self.assertEqual((tx["state"], tx["restorable_items"], tx["missing_relocate_items"]), ("FAILED", 0, 1))

    def test_manual_review_blocks_selection_and_preserves_legacy_partial_restore(self):
        confirmed = self.write("confirmed.txt")
        txid, copies = self.relocate(confirmed)
        residual = self.write(f".agent-trash/{txid}/unconfirmed.txt", b"unfinished copy")
        self.engine._manifest_append([{
            "type": "relocate-failed", "txid": txid, "origin_path": str(self.root / "unconfirmed.txt"),
            "trash_path": str(residual),
        }])
        before = self.tree(include_audit=False)
        code, preview = self.restore_call("mcp", "preview", txid, [confirmed.name])
        self.assertEqual(code, 0, preview)
        self.assertFalse(preview["can_restore"])
        self.assertEqual(preview["next_step"]["action"], "manual_review")
        self.assertIsNone(preview["next_step"]["restore_command"])
        code, result = self.restore_call("mcp", "restore", txid, [confirmed.name])
        self.assertEqual(code, 1, result)
        self.assertEqual(result["restored"], [])
        self.assertEqual(self.tree(include_audit=False), before)
        code, legacy = self.restore_call("mcp", "restore", txid)
        self.assertEqual(code, 1, legacy)
        self.assertEqual(legacy["execution_summary"]["status"], "PARTIAL")
        self.assertEqual(legacy["restored"], [str(confirmed)])
        self.assertFalse(copies[str(confirmed)].exists())
        self.assertEqual(residual.read_bytes(), b"unfinished copy")
        tx = self.transaction(txid)
        self.assertEqual(
            (tx["state"], tx["restorable_items"], tx["manual_review_items"]),
            ("FAILED", 0, 1),
        )

    def test_literal_names_case_folding_and_duplicate_selectors(self):
        file_name = "literal[1].txt" if os.name == "nt" else "literal*.txt"
        selected = self.write("sub/" + file_name)
        other = self.write("sub/literal1.txt")
        txid, copies = self.relocate(selected, other)
        path = "SUB/" + file_name.upper() if os.name == "nt" else selected.relative_to(self.root).as_posix()
        code, reply = self.restore_call("mcp", "restore", txid, [path, str(selected)])
        self.assertEqual(code, 0, reply)
        self.assertEqual(reply["selected_paths"], [str(selected)])
        self.assertEqual(reply["restored"], [str(selected)])
        self.assertEqual(reply["omitted_relocate_count"], 1)
        self.assertTrue(copies[str(other)].is_file())

    def test_alias_ambiguity_and_overlap_refuse_before_moving(self):
        first = self.write("first.txt")
        second = self.write("second.txt")
        txid, _ = self.relocate(first, second)
        from core.recovery import _physical_keep_final

        def same_location(path):
            return str(first) if path == str(second) else _physical_keep_final(path)

        before = self.tree()
        with mock.patch("core.recovery._physical_keep_final", side_effect=same_location):
            result = self.engine.restore(txid, paths=[str(first)])
        self.assertIn("more than one", result["error"])
        self.assertEqual(self.tree(), before)

        def descendant_location(path):
            return str(first / "nested") if path == str(second) else _physical_keep_final(path)

        with mock.patch("core.recovery._physical_keep_final", side_effect=descendant_location):
            preview = self.engine.preview_restore(txid, paths=[str(first)])
            result = self.engine.restore(txid, paths=[str(first)])
        self.assertFalse(preview["can_restore"])
        self.assertIn("overlaps an unselected", result["errors"][0])
        self.assertEqual(self.tree(), before)

    def test_intent_only_copy_survives_as_history_and_retry_is_idempotent(self):
        txid = "intent-copy"
        origin = self.root / "original.txt"
        copy = self.write(f".agent-trash/{txid}/original.txt")
        self.engine._manifest_append([
            {"type": "tx-start", "txid": txid},
            {"type": "relocate-intent", "txid": txid, "origin_path": str(origin), "trash_path": str(copy)},
        ])
        self.assertTrue(self.transaction(txid)["items"][0]["recovered_from_intent"])
        self.assertTrue(self.engine.restore(txid)["ok"])
        self.engine._manifest_append([{
            "type": "restore", "txid": txid, "restored": [], "errors": ["historical attempt failed"],
        }])
        tx = self.transaction(txid)
        self.assertEqual(len(tx["items"]), 1)
        self.assertTrue(tx["items"][0]["recovered_from_intent"])
        self.assertEqual((tx["state"], tx["restorable_items"]), ("RESTORED", 0))
        preview = self.engine.preview_restore(txid)
        self.assertEqual(preview["items"][0]["status"], "already_restored")
        self.assertFalse(preview["can_restore"])
        journal = self.journal()
        retry = self.engine.restore(txid)
        self.assertTrue(retry["ok"], retry)
        self.assertEqual(retry["restored"], [])
        self.assertEqual(retry["already_restored"], [str(origin)])
        self.assertEqual(self.journal(), journal)

    def test_success_receipt_and_purge_tombstone_do_not_claim_reappeared_copies(self):
        for purged in (False, True):
            with self.subTest(purged=purged):
                name = f"reappeared-{purged}.txt"
                origin = self.write(name, b"recorded copy")
                txid, copies = self.relocate(origin)
                self.assertTrue(self.engine.restore(txid)["ok"])
                copy = copies[str(origin)]
                copy.parent.mkdir(parents=True)
                copy.write_bytes(b"unrelated fixture object")
                if purged:
                    self.engine._manifest_append([{"type": "purged", "txid": txid}])
                tx = self.transaction(txid)
                self.assertEqual(tx["restorable_items"], 0)
                self.assertEqual(tx["state"], "PURGED" if purged else "FAILED")
                preview = self.engine.preview_restore(txid)
                self.assertFalse(preview.get("can_restore", False))
                if not purged:
                    self.assertEqual(preview["items"][0]["status"], "blocked")
                result = self.engine.restore(txid, paths=[str(origin)])
                self.assertFalse(result["ok"])
                self.assertEqual(origin.read_bytes(), b"recorded copy")
                self.assertEqual(copy.read_bytes(), b"unrelated fixture object")

    @unittest.skipUnless(git_available(), "git required for stash roundtrip")
    def test_real_snapshot_waits_for_unfiltered_restore_and_is_retained(self):
        def git(*argv):
            return subprocess.run(["git", "-C", str(self.root), *argv], env=self.env,
                                  capture_output=True, text=True, check=True).stdout.strip()

        git("init", "-q")
        git("config", "user.name", "synthetic")
        git("config", "user.email", "test@local")
        git("config", "commit.gpgsign", "false")
        git("config", "core.autocrlf", "false")
        git("config", "maintenance.auto", "false")
        git("config", "gc.auto", "0")
        self.write(".gitignore", b".agent-trash/\n")
        tracked = self.write("tracked.txt", b"base\n")
        git("add", ".gitignore", tracked.name)
        git("commit", "-qm", "baseline")

        isolated = self.write("isolated.txt")
        txid, _ = self.relocate(isolated)
        self.write(tracked.name, b"saved tracked content\n")
        snapshot = self.engine.snapshot_git(txid=txid)
        self.assertTrue(snapshot["stored"], snapshot)
        self.write(tracked.name, b"base\n")
        self.assertTrue(self.engine.restore(txid, paths=[str(isolated)])["ok"])
        self.assertEqual(tracked.read_bytes(), b"base\n")
        tx = self.transaction(txid)
        self.assertEqual((tx["state"], tx["pending_snapshot_items"]), ("RESTORABLE", 1))
        resumed = self.engine.restore(txid)
        self.assertTrue(resumed["ok"], resumed)
        self.assertEqual(resumed["restored"], [f"stash:{snapshot['sha']}"])
        self.assertEqual(tracked.read_bytes(), b"saved tracked content\n")
        self.assertIn(snapshot["sha"], git("stash", "list", "--format=%H").splitlines())
        tx = self.transaction(txid)
        self.assertEqual((tx["state"], tx["restorable_items"], tx["retained_snapshot_items"]), ("RESTORED", 0, 1))

    def test_snapshot_only_retries_and_failed_storage_are_not_actions(self):
        applied, pending = "a" * 40, "b" * 40
        self.engine._manifest_append([
            {"type": "snapshot", "txid": "snapshots", "sha": applied, "stored": True},
            {"type": "snapshot", "txid": "snapshots", "sha": pending},
            {"type": "restore", "txid": "snapshots", "restored": [f"stash:{applied}"]},
            {"type": "snapshot", "txid": "failed", "sha": "c" * 40, "stored": False},
            {"type": "snapshot", "txid": "clean", "sha": None},
        ])
        before = self.transaction("snapshots")
        self.assertEqual((before["pending_snapshot_items"], before["retained_snapshot_items"]), (1, 1))
        preview = self.engine.preview_restore("snapshots")
        self.assertEqual([row["status"] for row in preview["items"]], ["already_applied", "ready"])

        run = subprocess.run

        def apply(argv, **kwargs):
            if argv[3:5] == ["stash", "apply"]:
                self.assertEqual(argv[-1], pending)
                return subprocess.CompletedProcess(argv, 0, "", "")
            return run(argv, **kwargs)

        with mock.patch("core.recovery.subprocess.run", side_effect=apply):
            result = self.engine.restore("snapshots")
        self.assertEqual(result["restored"], [f"stash:{pending}"])
        self.assertEqual(result["already_applied"], [applied])
        journal = self.journal()
        with mock.patch("core.recovery.subprocess.run") as invoked:
            retry = self.engine.restore("snapshots")
        invoked.assert_not_called()
        self.assertTrue(retry["ok"])
        self.assertEqual(retry["already_applied"], [applied, pending])
        self.assertEqual(self.journal(), journal)
        self.assertEqual(self.transaction("snapshots")["state"], "RESTORED")

        failed = self.engine.preview_restore("failed")
        self.assertEqual(failed["items"][0]["status"], "failed")
        self.assertFalse(failed["can_restore"])
        with mock.patch("core.recovery.subprocess.run", wraps=run) as invoked:
            refused = self.engine.restore("failed")
        self.assertFalse(refused["ok"], refused)
        self.assertFalse(any(call.args[0][3:5] == ["stash", "apply"] for call in invoked.call_args_list))
        self.assertEqual(self.transaction("failed")["state"], "FAILED")
        self.assertEqual(self.transaction("clean")["state"], "EMPTY")

    def test_runtime_error_journals_successes_for_the_next_attempt(self):
        first = self.write("a/file.txt")
        second = self.write("b/file.txt")
        txid, copies = self.relocate(first, second)
        move = self.engine._move

        def fail_second(source, target):
            if target == str(second):
                raise OSError("synthetic move failure")
            return move(source, target)

        with mock.patch.object(self.engine, "_move", side_effect=fail_second):
            result = self.engine.restore(txid, paths=[str(first), str(second)])
        self.assertFalse(result["ok"])
        self.assertEqual(result["restored"], [str(first)])
        self.assertEqual(self.engine.read_manifest()[-1]["restored"], [str(first)])
        self.assertFalse(copies[str(first)].parent.exists())
        self.assertTrue(copies[str(second)].is_file())
        self.assertEqual(self.transaction(txid)["restorable_items"], 1)
        self.assertTrue(self.engine.restore(txid)["ok"])
        self.assertEqual(self.transaction(txid)["state"], "RESTORED")

    def test_pruning_stops_at_linked_parent_within_the_transaction(self):
        txid = "linked-parent"
        origin = self.root / "restored.txt"
        real_copy = self.write(f".agent-trash/{txid}/physical/file.txt")
        link = real_copy.parent.with_name("linked")
        if os.name == "nt":
            created = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(real_copy.parent)],
                                     capture_output=True, text=True, timeout=15)
            self.assertEqual(created.returncode, 0, created.stdout + created.stderr)
        else:
            link.symlink_to(real_copy.parent, target_is_directory=True)
        self.engine._manifest_append([
            {"type": "relocate", "txid": txid, "origin_path": str(origin), "trash_path": str(link / "file.txt")},
        ])
        result = self.engine.restore(txid, paths=[str(origin)])
        self.assertTrue(result["ok"], result)
        self.assertEqual(origin.read_bytes(), b"synthetic copy")
        self.assertTrue(os.path.lexists(link))
        self.assertTrue(real_copy.parent.is_dir())
        self.assertFalse(real_copy.exists())

    def test_powershell_guidance_quotes_a_literal_selected_path(self):
        path = r"C:\work\owner's file.txt"
        following = messages.restore_guidance(
            "tx-test", r"C:\work", r"C:\work\.agent-trash", platform="nt",
            selected_paths=[path], has_conflicts=True, can_restore_with_force=True,
        )
        self.assertIsNone(following["restore_command"])
        self.assertIn("'--exact-txid'", following["force_restore_command"])
        self.assertIn("'--path=" + path.replace("'", "''") + "'", following["force_restore_command"])
        self.assertIn("'--force' '--' 'tx-test'", following["force_restore_command"])

    def test_python_api_resolves_relative_paths_against_workspace_or_explicit_cwd(self):
        first = self.write("sub/first.txt", b"first")
        second = self.write("sub/second.txt", b"second")
        txid, _ = self.relocate(first, second)
        outside_cwd = os.getcwd()
        self.assertNotEqual(os.path.normcase(os.path.realpath(outside_cwd)),
                            os.path.normcase(os.path.realpath(str(self.root))))

        # Without cwd, relative paths resolve against self.workspace regardless of process cwd.
        preview = self.engine.preview_restore(txid, paths=["sub/first.txt"])
        self.assertTrue(preview["ok"], preview)
        self.assertEqual(preview["selected_paths"], [str(first)])
        restored = self.engine.restore(txid, paths=["sub/first.txt"])
        self.assertTrue(restored["ok"], restored)
        self.assertEqual(first.read_bytes(), b"first")
        self.assertFalse(second.exists())

        # With explicit cwd inside workspace, relative paths resolve against cwd.
        sub_preview = self.engine.preview_restore(txid, paths=["second.txt"], cwd=str(first.parent))
        self.assertTrue(sub_preview["ok"], sub_preview)
        self.assertEqual(sub_preview["selected_paths"], [str(second)])
        sub_restored = self.engine.restore(txid, paths=["second.txt"], cwd=str(first.parent))
        self.assertTrue(sub_restored["ok"], sub_restored)
        self.assertEqual(second.read_bytes(), b"second")

    def test_text_mode_reports_selection_and_omitted_counts_only_when_selective(self):
        first = self.write("alpha.txt", b"alpha")
        second = self.write("beta.txt", b"beta")
        txid, _ = self.relocate(first, second)
        self.engine._manifest_append([
            {"type": "snapshot", "txid": txid, "sha": "d" * 40, "stored": True},
        ])
        env = {**self.env, "AGENT_GUARD_WORKSPACE": str(self.root), "AGENT_GUARD_TRASH": str(self.trash)}

        plain_preview = subprocess.run(
            [sys.executable, "-B", str(SCRIPTS / "restore.py"), "preview", "--", txid],
            cwd=self.root, env=env, capture_output=True, text=True, encoding="utf-8", timeout=45,
        )
        self.assertEqual(plain_preview.returncode, 0, plain_preview.stderr)
        self.assertNotIn("selection      :", plain_preview.stdout)

        sel_preview = subprocess.run(
            [sys.executable, "-B", str(SCRIPTS / "restore.py"), "preview", "--path=alpha.txt", "--", txid],
            cwd=self.root, env=env, capture_output=True, text=True, encoding="utf-8", timeout=45,
        )
        self.assertEqual(sel_preview.returncode, 0, sel_preview.stderr)
        self.assertIn("selection      : 1 selected (omitted: 1 relocations, 1 snapshots)", sel_preview.stdout)

        sel_restore = subprocess.run(
            [sys.executable, "-B", str(SCRIPTS / "restore.py"), "restore", "--path=alpha.txt", "--", txid],
            cwd=self.root, env=env, capture_output=True, text=True, encoding="utf-8", timeout=45,
        )
        self.assertEqual(sel_restore.returncode, 0, sel_restore.stderr)
        self.assertIn("selection: 1 selected (omitted: 1 relocations, 1 snapshots)", sel_restore.stdout)
