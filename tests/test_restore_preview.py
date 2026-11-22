"""Read-only restore preview, conflict comparison, short prefixes, and Windows handoff."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from core import messages, recovery
from tests.helpers import RepoFixture, git_available


SCRIPTS = Path(__file__).resolve().parents[1] / "skills/delete-guard/scripts"


def _run_script(name, *args, cwd):
    return subprocess.run(
        [sys.executable, "-B", str(SCRIPTS / name), *args],
        cwd=cwd, capture_output=True, text=True, timeout=60,
    )


@unittest.skipUnless(git_available(), "git required")
class RestorePreviewTests(RepoFixture):
    def _inventory(self):
        root = Path(self.root)
        return {
            str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        }

    def test_preview_ready_transaction_is_zero_write_and_resolves_short_prefix(self):
        self.write("doc.txt", "synthetic valuable text\n")
        deleted = _run_script("safe_delete.py", "--json", "doc.txt", cwd=self.root)
        self.assertEqual(deleted.returncode, 0, deleted.stderr)
        txid = json.loads(deleted.stdout)["txid"]

        before = self._inventory()
        for argv in (
            ("preview", txid, "--json"),
            ("preview", txid[:12], "--json"),
            (txid, "--preview", "--json"),
            (txid[:12], "--preview", "--json"),
        ):
            with self.subTest(argv=argv):
                proc = _run_script("restore.py", *argv, cwd=self.root)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                report = json.loads(proc.stdout)
                self.assertEqual(report["schema"], "restore_preview/v1")
                self.assertTrue(report["ok"])
                self.assertEqual(report["txid"], txid)
                self.assertTrue(report["can_restore"])
                self.assertTrue(report["can_restore_with_force"])
                self.assertEqual(report["summary"]["ready_count"], 1)
                self.assertEqual(report["summary"]["conflict_count"], 0)
                self.assertEqual(report["items"][0]["status"], "ready")
                self.assertEqual(report["next_step"]["action"], "restore")

        text_preview = _run_script("restore.py", "preview", txid[:12], cwd=self.root)
        self.assertEqual(text_preview.returncode, 0, text_preview.stderr)
        self.assertIn("restore preview:", text_preview.stdout)
        self.assertIn("[ready]", text_preview.stdout)
        self.assertIn("can_restore    : yes", text_preview.stdout)

        # Strictly read-only: no files, manifests or audit lines added/changed.
        self.assertEqual(self._inventory(), before)

        # Unknown txid exits 1 without writing audit.
        unknown = _run_script("restore.py", "preview", "nonexistent-tx", "--json", cwd=self.root)
        self.assertEqual(unknown.returncode, 1)
        self.assertFalse(json.loads(unknown.stdout)["ok"])
        self.assertEqual(self._inventory(), before)

        # Ambiguous prefix exits 2 without writing.
        engine = recovery.RecoveryEngine(self.root)
        engine._manifest_append([
            {"type": "tx-start", "txid": "dup-prefix-1"},
            {"type": "tx-start", "txid": "dup-prefix-2"},
        ])
        after_manifest = self._inventory()
        ambiguous = _run_script("restore.py", "preview", "dup-prefix", "--json", cwd=self.root)
        self.assertEqual(ambiguous.returncode, 2)
        self.assertIn("ambiguous", json.loads(ambiguous.stdout)["error"])
        ambiguous_text = _run_script("restore.py", "preview", "dup-prefix", cwd=self.root)
        self.assertEqual(ambiguous_text.returncode, 2)
        self.assertIn("ambiguous", ambiguous_text.stderr)
        self.assertEqual(self._inventory(), after_manifest)

    def test_preview_conflict_comparison_never_leaks_content_or_digest(self):
        same_body = b"synthetic-identical-secret-marker"
        same_digest = hashlib.sha256(same_body).hexdigest()
        Path(self.root, "same.txt").write_bytes(same_body)
        self.write("diff_size.txt", "short")
        self.write("diff_bytes.txt", "aaaa")
        self.write("kind_swap.txt", "was a file")
        self.write("dir_entry/nested.txt", "dir content")
        targets = ["same.txt", "diff_size.txt", "diff_bytes.txt", "kind_swap.txt", "dir_entry"]
        if os.name == "posix":
            Path(self.root, "link_entry").symlink_to("same.txt")
            targets.append("link_entry")

        deleted = _run_script("safe_delete.py", "--json", *targets, cwd=self.root)
        self.assertEqual(deleted.returncode, 0, deleted.stderr)
        txid = json.loads(deleted.stdout)["txid"]

        Path(self.root, "same.txt").write_bytes(same_body)
        self.write("diff_size.txt", "much longer occupant")
        self.write("diff_bytes.txt", "bbbb")
        Path(self.root, "kind_swap.txt").mkdir()
        self.write("dir_entry/other.txt", "new occupant")
        if os.name == "posix":
            Path(self.root, "link_entry").symlink_to("diff_bytes.txt")

        before = self._inventory()
        proc = _run_script("restore.py", "preview", txid, "--force", "--json", cwd=self.root)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = json.loads(proc.stdout)
        self.assertEqual(self._inventory(), before)
        self.assertTrue(report["ok"])
        self.assertTrue(report["force_requested"])
        self.assertFalse(report["can_restore"])
        self.assertTrue(report["can_restore_with_force"])
        self.assertEqual(report["summary"]["identical_conflict_count"], 1)
        self.assertEqual(report["summary"]["conflict_count"], len(targets))
        self.assertEqual(report["next_step"]["action"], "review_conflicts")

        serialized = json.dumps(report)
        self.assertNotIn("synthetic-identical-secret-marker", serialized)
        self.assertNotIn(same_digest, serialized)

        by_name = {Path(item["origin_path"]).name: item for item in report["items"]}
        self.assertEqual(by_name["same.txt"]["comparison"], {
            "same_kind": True, "same_size": True,
            "content_equal": True, "basis": "sha256_regular_file",
        })
        self.assertEqual(by_name["diff_size.txt"]["comparison"], {
            "same_kind": True, "same_size": False,
            "content_equal": False, "basis": "size_diff",
        })
        self.assertEqual(by_name["diff_bytes.txt"]["comparison"], {
            "same_kind": True, "same_size": True,
            "content_equal": False, "basis": "sha256_regular_file",
        })
        self.assertEqual(by_name["kind_swap.txt"]["comparison"], {
            "same_kind": False, "same_size": None,
            "content_equal": False, "basis": "kind_diff",
        })
        self.assertEqual(by_name["dir_entry"]["comparison"], {
            "same_kind": True, "same_size": None,
            "content_equal": None, "basis": "directory_uncompared",
        })
        if os.name == "posix":
            self.assertEqual(by_name["link_entry"]["comparison"], {
                "same_kind": True, "same_size": None,
                "content_equal": None, "basis": "symlink_target_uncompared",
            })

        # Small hash cap leaves regular file equality uncompared.
        capped = recovery.RecoveryEngine(self.root).preview_restore(txid, hash_max_bytes=4)
        capped_same = next(item for item in capped["items"] if Path(item["origin_path"]).name == "same.txt")
        self.assertEqual(capped_same["comparison"], {
            "same_kind": True, "same_size": True,
            "content_equal": None, "basis": "exceeds_hash_cap",
        })

    def test_preview_reports_missing_boundary_manual_review_force_overlap_and_snapshot(self):
        engine = recovery.RecoveryEngine(self.root)

        # 1. Missing quarantine copy.
        self.write("gone.txt", "bytes")
        deleted = _run_script("safe_delete.py", "--json", "gone.txt", cwd=self.root)
        tx_missing = json.loads(deleted.stdout)["txid"]
        trash_copy = json.loads(deleted.stdout)["moved"][0]["trash"]
        Path(trash_copy).unlink()
        preview_missing = engine.preview_restore(tx_missing)
        self.assertEqual(preview_missing["items"][0]["status"], "missing_copy")
        self.assertFalse(preview_missing["can_restore"])
        self.assertFalse(preview_missing["can_restore_with_force"])

        # 2. Boundary error (outside workspace origin recorded in manifest).
        outside = str(Path(self.root).parent / "outside-target.txt")
        tx_boundary = "boundary-case-1"
        engine._manifest_append([
            {"type": "tx-start", "txid": tx_boundary},
            {"type": "relocate", "txid": tx_boundary, "origin_path": outside,
             "trash_path": str(Path(self.root, ".agent-trash", tx_boundary, "outside.txt"))},
        ])
        preview_boundary = engine.preview_restore(tx_boundary)
        self.assertFalse(preview_boundary["ok"])
        self.assertEqual(preview_boundary["items"][0]["status"], "boundary_error")
        self.assertFalse(preview_boundary["can_restore"])

        # 3. Manual review item from failed relocation.
        tx_review = "manual-review-1"
        residual = Path(self.write(f".agent-trash/{tx_review}/partial.txt", "partial"))
        origin = str(Path(self.root, "partial.txt"))
        engine._manifest_append([
            {"type": "tx-start", "txid": tx_review},
            {"type": "relocate-intent", "txid": tx_review, "origin_path": origin, "trash_path": str(residual)},
            {"type": "relocate-failed", "txid": tx_review, "origin_path": origin, "trash_path": str(residual),
             "error": "storage unavailable"},
        ])
        preview_review = engine.preview_restore(tx_review)
        self.assertEqual(preview_review["summary"]["manual_review_count"], 1)
        self.assertTrue(preview_review["manual_review"][0]["payload_exists"])
        self.assertFalse(preview_review["can_restore"])
        self.assertFalse(preview_review["can_restore_with_force"])
        self.assertEqual(preview_review["next_step"]["action"], "manual_review")

        # 4. Force-preservation overlap with quarantine storage (origin is ancestor of trash_root).
        custom_trash = Path(self.root, "sub/custom-trash")
        custom_engine = recovery.RecoveryEngine(self.root, str(custom_trash))
        tx_overlap = "overlap-case-1"
        trash_copy_overlap = custom_trash / tx_overlap / "sub"
        trash_copy_overlap.parent.mkdir(parents=True, exist_ok=True)
        trash_copy_overlap.write_text("orig", encoding="utf-8")
        custom_engine._manifest_append([
            {"type": "tx-start", "txid": tx_overlap},
            {"type": "relocate", "txid": tx_overlap,
             "origin_path": str(Path(self.root, "sub")),
             "trash_path": str(trash_copy_overlap)},
        ])
        preview_overlap = custom_engine.preview_restore(tx_overlap, force=True)
        self.assertEqual(preview_overlap["items"][0]["status"], "conflict")
        self.assertFalse(preview_overlap["items"][0]["force_preservable"])
        self.assertFalse(preview_overlap["can_restore_with_force"])
        self.assertTrue(preview_overlap["errors"])

        # 5. Unreachable parent (origin ancestor directory replaced by a regular file).
        self.write("nested_dir/item.txt", "nested payload")
        del_nested = _run_script("safe_delete.py", "--json", "nested_dir/item.txt", cwd=self.root)
        self.assertEqual(del_nested.returncode, 0, del_nested.stderr)
        tx_parent_file = json.loads(del_nested.stdout)["txid"]
        Path(self.root, "nested_dir").rmdir()
        Path(self.root, "nested_dir").write_text("now a regular file", encoding="utf-8")
        preview_parent = engine.preview_restore(tx_parent_file, force=True)
        self.assertTrue(preview_parent["ok"])
        self.assertEqual(preview_parent["items"][0]["status"], "blocked")
        self.assertFalse(preview_parent["items"][0]["force_preservable"])
        self.assertEqual(preview_parent["summary"]["blocked_count"], 1)
        self.assertEqual(preview_parent["summary"]["conflict_count"], 0)
        self.assertFalse(preview_parent["can_restore"])
        self.assertFalse(preview_parent["can_restore_with_force"])
        self.assertEqual(preview_parent["next_step"]["action"], "inspect")
        self.assertIsNone(preview_parent["next_step"]["force_restore_command"])
        self.assertTrue(preview_parent["errors"])

        # 6. Snapshot transaction.
        self.write("tracked.txt", "v1\n")
        subprocess.run(["git", "-C", self.root, "add", "tracked.txt"], check=True, capture_output=True)
        subprocess.run(["git", "-C", self.root, "commit", "-q", "-m", "add tracked"], check=True, capture_output=True)
        self.write("tracked.txt", "v2 dirty\n")
        snap = engine.snapshot_git(txid="snapshot-case-1", meta={"label": "preview test"})
        self.assertTrue(snap["ok"], snap)
        preview_snap = engine.preview_restore("snapshot-case-1")
        self.assertTrue(preview_snap["ok"])
        self.assertEqual(preview_snap["summary"]["snapshot_count"], 1)
        self.assertTrue(preview_snap["can_restore"])
        self.assertEqual(preview_snap["items"][0]["type"], "snapshot")


class WindowsGuidanceFormatting(unittest.TestCase):
    def test_format_command_posix_vs_windows_powershell(self):
        argv = [
            r"C:\Program Files\Python313\python.exe",
            "C:\\O'Brien\\O\u2019Brien\\restore.py",
            "--txid", "a\u2018b\u201ac\u201bd",
        ]
        env_vars = {
            "AGENT_GUARD_WORKSPACE": r"C:\Work Space",
            "AGENT_GUARD_TRASH": "D:\\O'Trash\u2019Dir",
        }
        ps_plain = messages.format_command(argv, platform="nt")
        self.assertEqual(
            ps_plain,
            "& 'C:\\Program Files\\Python313\\python.exe' "
            "'C:\\O''Brien\\O\u2019\u2019Brien\\restore.py' '--txid' "
            "'a\u2018\u2018b\u201a\u201ac\u201b\u201bd'",
        )
        ps_env = messages.format_command(argv, env_vars, platform="nt")
        self.assertTrue(ps_env.startswith("& { $p=@{}; foreach($k in "))
        self.assertIn("try {", ps_env)
        self.assertIn("finally {", ps_env)
        self.assertIn(ps_plain, ps_env)
        self.assertIn("'D:\\O''Trash\u2019\u2019Dir'", ps_env)
        self.assertNotIn("env AGENT_GUARD_WORKSPACE", ps_env)
        self.assertNotIn("$env:AGENT_GUARD_WORKSPACE=", ps_env)

        posix_plain = messages.format_command(["/usr/bin/python3", "/tmp/a b/script.py"], platform="posix")
        self.assertEqual(posix_plain, "/usr/bin/python3 '/tmp/a b/script.py'")
        posix_env = messages.format_command(
            ["/usr/bin/python3", "/tmp/script.py"],
            {"AGENT_GUARD_WORKSPACE": "/tmp/ws", "AGENT_GUARD_TRASH": "/tmp/custom trash"},
            platform="posix",
        )
        self.assertTrue(posix_env.startswith("env AGENT_GUARD_WORKSPACE=/tmp/ws "))
        self.assertIn("'AGENT_GUARD_TRASH=/tmp/custom trash'", posix_env)

    def test_guidance_helpers_emit_powershell_commands_and_env_on_nt(self):
        workspace = os.path.abspath("ws-default")
        default_trash = os.path.join(workspace, ".agent-trash")
        custom_trash = os.path.abspath("custom-trash-root")
        preview = {"status": "READY", "code": None}

        for trash, expects_env in ((default_trash, False), (custom_trash, True)):
            with self.subTest(trash=trash):
                del_guide = messages.deletion_guidance(
                    preview, workspace, trash, workspace, ["src/a.txt"], platform="nt"
                )
                gc_guide = messages.gc_guidance(
                    workspace, trash, txid="tx-123", platform="nt"
                )
                res_guide = messages.restore_guidance(
                    "tx-123", workspace, trash,
                    can_restore=False, can_restore_with_force=True,
                    has_conflicts=True, platform="nt",
                )
                for entry in del_guide["commands"]:
                    if expects_env:
                        self.assertTrue(entry["command"].startswith("& { $p=@{}; "), entry)
                        self.assertIn("finally {", entry["command"])
                        self.assertEqual(entry["argv"][0], sys.executable)
                        self.assertIn("AGENT_GUARD_TRASH", entry["env"])
                    else:
                        self.assertTrue(entry["command"].startswith("& '"), entry)
                        self.assertNotIn("env", entry)
                cmd_strings = [
                    gc_guide["plan_command"],
                    gc_guide["purge_eligible_command"],
                    gc_guide["purge_transaction_command"],
                    res_guide["restore_command"],
                    res_guide["force_restore_command"],
                ]
                for rendered in cmd_strings:
                    if expects_env:
                        self.assertTrue(rendered.startswith("& { $p=@{}; "), rendered)
                        self.assertIn("finally {", rendered)
                    else:
                        self.assertTrue(rendered.startswith("& '"), rendered)

        # POSIX retains env argv and shlex string without extra env dict.
        posix_del = messages.deletion_guidance(
            preview, workspace, custom_trash, workspace, ["src/a.txt"], platform="posix"
        )
        self.assertEqual(posix_del["commands"][0]["argv"][0], "env")
        self.assertNotIn("env", posix_del["commands"][0])
        self.assertTrue(posix_del["commands"][0]["command"].startswith("env "))
        posix_gc = messages.gc_guidance(workspace, custom_trash, txid="tx-123", platform="posix")
        self.assertTrue(posix_gc["plan_command"].startswith("env "))

        # On real Windows, omitting platform defaults to PowerShell formatting.
        if os.name == "nt":
            native_res = messages.restore_guidance("tx-123", workspace, default_trash, can_restore=True)
            self.assertTrue(native_res["restore_command"].startswith("& '"))

    def test_formatted_command_executes_with_exact_argv_and_scoped_env(self):
        """Run a harmless echo helper through the native shell to verify argv and env scope."""
        with tempfile.TemporaryDirectory(prefix="ag-handoff-") as tmp:
            echo_script = Path(tmp) / "echo_probe.py"
            echo_script.write_text(
                "import json, os, sys\n"
                "print(json.dumps({'argv': sys.argv[1:], "
                "'ws': os.environ.get('AGENT_GUARD_WORKSPACE'), "
                "'trash': os.environ.get('AGENT_GUARD_TRASH')}, ensure_ascii=True))\n",
                encoding="utf-8",
            )
            sample_args = [
                "dir with spaces",
                "O'Brien",
                "curly\u2018\u2019\u201a\u201bquote",
                "$HOME",
                "$(echo literal); noop",
            ]
            env_vars = {
                "AGENT_GUARD_WORKSPACE": str(Path(tmp) / "ws O'Brien"),
                "AGENT_GUARD_TRASH": str(Path(tmp) / "trash O\u2019Dir"),
            }
            argv = [sys.executable, "-B", str(echo_script), *sample_args]
            rendered = messages.format_command(argv, env_vars)
            base_env = {k: v for k, v in os.environ.items()
                        if k not in ("AGENT_GUARD_WORKSPACE", "AGENT_GUARD_TRASH")}

            if os.name == "nt":
                shell_bin = shutil.which("pwsh") or shutil.which("powershell")
                self.assertIsNotNone(shell_bin, "PowerShell required on Windows")
                for preexisting in (None, "PREEXISTING_WS_VALUE"):
                    with self.subTest(preexisting=preexisting):
                        run_env = dict(base_env)
                        if preexisting is not None:
                            run_env["AGENT_GUARD_WORKSPACE"] = preexisting
                        script = (
                            "[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false); "
                            + rendered
                            + "; Write-Output ('AFTER=' + $env:AGENT_GUARD_WORKSPACE + '|' + $env:AGENT_GUARD_TRASH)"
                        )
                        proc = subprocess.run(
                            [shell_bin, "-NoProfile", "-NonInteractive", "-Command", script],
                            env=run_env, capture_output=True, text=True, encoding="utf-8", timeout=30,
                        )
                        self.assertEqual(proc.returncode, 0, proc.stderr)
                        lines = [line for line in proc.stdout.splitlines() if line.strip()]
                        payload = json.loads(lines[0])
                        self.assertEqual(payload["argv"], sample_args)
                        self.assertEqual(payload["ws"], env_vars["AGENT_GUARD_WORKSPACE"])
                        self.assertEqual(payload["trash"], env_vars["AGENT_GUARD_TRASH"])
                        expected_after = f"AFTER={preexisting or ''}|"
                        self.assertEqual(lines[-1], expected_after)
            else:
                for preexisting in (None, "PREEXISTING_WS_VALUE"):
                    with self.subTest(preexisting=preexisting):
                        run_env = dict(base_env)
                        if preexisting is not None:
                            run_env["AGENT_GUARD_WORKSPACE"] = preexisting
                        script = (
                            rendered
                            + '; printf "AFTER=%s|%s\\n" "$AGENT_GUARD_WORKSPACE" "$AGENT_GUARD_TRASH"'
                        )
                        proc = subprocess.run(
                            ["/bin/sh", "-c", script],
                            env=run_env, capture_output=True, text=True, encoding="utf-8", timeout=30,
                        )
                        self.assertEqual(proc.returncode, 0, proc.stderr)
                        lines = [line for line in proc.stdout.splitlines() if line.strip()]
                        payload = json.loads(lines[0])
                        self.assertEqual(payload["argv"], sample_args)
                        self.assertEqual(payload["ws"], env_vars["AGENT_GUARD_WORKSPACE"])
                        self.assertEqual(payload["trash"], env_vars["AGENT_GUARD_TRASH"])
                        expected_after = f"AFTER={preexisting or ''}|"
                        self.assertEqual(lines[-1], expected_after)


if __name__ == "__main__":
    unittest.main()
