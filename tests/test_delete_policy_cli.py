"""Operator limit settings and their effect on the shared deletion policy."""
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from adapters.mcp.operations import Operations
from core import classifier, cleanup_saved, cleanup_tasks, policy as guard_policy
from core.cleanup import plan_cleanup
from core.delete_config import MAX_REGENERABLE_PATTERNS, config_path


SCRIPT = Path(__file__).resolve().parents[1] / "skills/delete-guard/scripts/delete_policy.py"
with mock.patch.object(sys, "path", [str(SCRIPT.parent), *sys.path]):
    spec = importlib.util.spec_from_file_location("delete_policy_cli", SCRIPT)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)


class DeletePolicyCLITests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="delete-policy-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.policy = config_path(self.root)
        self.trash = self.root / ".agent-trash"
        env = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_GUARD_")}
        env["AGENT_GUARD_WORKSPACE"] = str(self.root)
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def configure(self, raw):
        self.policy.parent.mkdir(exist_ok=True)
        self.policy.write_bytes(raw)

    def invoke(self, *args, script=SCRIPT, input_text=""):
        return subprocess.run([sys.executable, "-B", str(script), *args], cwd=self.root,
                              input=input_text, capture_output=True, text=True, timeout=30)

    def operator(self, *args, answer="APPLY\n", during_confirmation=None):
        # Simulate terminal I/O for the unit test in its owned fixture.
        stdin, stderr, stdout = io.StringIO(answer), io.StringIO(), io.StringIO()
        stdin.isatty = stderr.isatty = lambda: True
        if during_confirmation:
            readline = stdin.readline

            def edit_then_confirm():
                during_confirmation()
                return readline()

            stdin.readline = edit_then_confirm
        with mock.patch.object(sys, "stdin", stdin), redirect_stderr(stderr), redirect_stdout(stdout):
            code = cli.main([*args, "--json"])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_show_missing_and_reset_noop_are_read_only(self):
        result = self.invoke("--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["workspace"], str(self.root))
        self.assertEqual(report["limits"]["session_max_bytes"], {
            "value": 1024 ** 3, "default": 1024 ** 3, "source": "default",
        })
        self.assertEqual(len(report["limits"]), 9)
        reset = self.invoke("reset-limits", "--json")
        self.assertEqual(reset.returncode, 0, reset.stderr)
        self.assertEqual(json.loads(reset.stdout), report)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_size_preview_reports_exact_bytes_without_creating_config(self):
        result = self.invoke("set", "session_max_bytes=1.5GiB", "quarantine_max_bytes=2 GB",
                             "batch_max_bytes=0B", "--preview", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["mode"], "preview")
        self.assertTrue(report["requires_confirmation"])
        self.assertTrue(report["effective_policy_changed"])
        self.assertEqual(report["workspace"], str(self.root))
        changes = report["changes"]
        self.assertEqual(changes["session_max_bytes"]["before"]["value"], 1024 ** 3)
        self.assertEqual(changes["session_max_bytes"]["after"]["value"], 1536 * 1024 ** 2)
        self.assertEqual(changes["quarantine_max_bytes"]["after"]["value"], 2_000_000_000)
        self.assertEqual(changes["batch_max_bytes"]["after"]["value"], 0)
        self.assertEqual(list(self.root.iterdir()), [])

        text = self.invoke("set", "session_max_bytes=2GiB", "--preview")
        self.assertEqual(text.returncode, 0, text.stderr)
        self.assertIn("1 GiB (default) -> 2 GiB (workspace)", text.stdout)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_reset_preview_distinguishes_source_changes_and_a_noop(self):
        absent = self.invoke("reset-limits", "--preview", "--json")
        self.assertEqual(absent.returncode, 0, absent.stderr)
        report = json.loads(absent.stdout)
        self.assertFalse(report["requires_confirmation"])
        self.assertFalse(report["effective_policy_changed"])
        self.assertEqual(report["changes"], {})
        self.assertEqual(list(self.root.iterdir()), [])

        original = b'{"session_max_bytes":1073741824,"protected_paths":["keep"]}'
        self.configure(original)
        present = self.invoke("reset-limits", "--preview", "--json")
        self.assertEqual(present.returncode, 0, present.stderr)
        report = json.loads(present.stdout)
        self.assertTrue(report["requires_confirmation"])
        self.assertFalse(report["effective_policy_changed"])
        change = report["changes"]["session_max_bytes"]
        self.assertEqual((change["before"]["source"], change["after"]["source"]), ("workspace", "default"))
        self.assertEqual(self.policy.read_bytes(), original)

    def test_set_uses_selected_root_and_reset_preserves_protected_declarations(self):
        workspace = self.root / "workspace with spaces"
        workspace.mkdir()
        policy = config_path(workspace)
        policy.parent.mkdir()
        original_paths = ["keep", "data/../keep"]
        policy.write_text(json.dumps({"protected_paths": original_paths, "batch_max_entries": 5}))
        policy.chmod(0o640)
        code, stdout, stderr = self.operator("set", "session_max_bytes=4KiB", "--root", str(workspace),
                                             "rate_max_operations=3")
        self.assertEqual(code, 0, stderr)
        result = json.loads(stdout)
        self.assertEqual(result["workspace"], str(workspace))
        self.assertEqual(result["limits"]["session_max_bytes"]["value"], 4096)
        self.assertEqual(result["limits"]["batch_max_entries"]["value"], 5)
        self.assertEqual(result["protected_path_count"], 1)
        self.assertIn("1 GiB (default) -> 4 KiB (workspace)", stderr)
        self.assertIn("Pending Cleanup approvals", stderr)
        self.assertFalse(self.policy.exists())
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(policy.stat().st_mode), 0o640)
        before_reset = (policy.read_bytes(), policy.stat().st_mtime_ns)
        preview = self.invoke("reset-limits", "--root", str(workspace), "--preview", "--json")
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertEqual((policy.read_bytes(), policy.stat().st_mtime_ns), before_reset)
        code, stdout, stderr = self.operator("reset-limits", "--root", str(workspace))
        self.assertEqual(code, 0, stderr)
        self.assertEqual(json.loads(policy.read_bytes()), {"protected_paths": original_paths})
        applied = json.loads(stdout)
        self.assertEqual(applied["changes"], json.loads(preview.stdout)["changes"])
        self.assertEqual(set(applied["changes"]), {"session_max_bytes", "batch_max_entries", "rate_max_operations"})
        mtime = policy.stat().st_mtime_ns
        no_op = self.invoke("reset-limits", "--root", str(workspace), "--json")
        self.assertEqual(no_op.returncode, 0, no_op.stderr)
        self.assertEqual(json.loads(no_op.stdout)["changes"], {})
        self.assertEqual(policy.stat().st_mtime_ns, mtime)

    def test_cancel_noninteractive_and_invalid_input_preserve_policy(self):
        original = b'{"rate_max_operations":3,"protected_paths":["keep"]}'
        self.configure(original)
        noninteractive = self.invoke("set", "rate_max_operations=4", input_text="APPLY\n")
        self.assertEqual(noninteractive.returncode, 2)
        self.assertIn("interactive operator terminal", noninteractive.stderr)
        code, _, stderr = self.operator("reset-limits", answer="\n")
        self.assertEqual(code, 2)
        self.assertIn("Cancelled", stderr)
        for args in (("session_max_bytes=4096", "rate_max_operations=0"),
                     ("protected_paths=0",), ("session_max_bytes=true",),
                     ("session_max_bytes=2G",), ("session_max_bytes=0.1B",),
                     ("quarantine_max_bytes=8388608TiB",), ("rate_max_operations=2MiB",),
                     ("rate_max_operations=4", "rate_max_operations=4")):
            with self.subTest(args=args):
                code, _, stderr = self.operator("set", *args)
                self.assertEqual(code, 2, stderr)
                self.assertNotIn("Type APPLY", stderr)
                self.assertEqual(self.policy.read_bytes(), original)
        malformed = b'{"rate_max_operations":3,"rate_max_operations":4}'
        self.configure(malformed)
        code, _, stderr = self.operator("reset-limits")
        self.assertEqual(code, 2)
        self.assertIn("duplicate", stderr)
        self.assertEqual(self.policy.read_bytes(), malformed)
        self.assertEqual(list(self.policy.parent.iterdir()), [self.policy])

    def test_edit_during_review_is_preserved(self):
        self.configure(b'{"session_max_bytes":4096}')
        edited = b'{"session_max_bytes":8192,"protected_paths":["keep"]}'
        code, _, stderr = self.operator("set", "rate_max_operations=2",
                                        during_confirmation=lambda: self.configure(edited))
        self.assertEqual(code, 2)
        self.assertIn("policy changed during review", stderr)
        self.assertEqual(self.policy.read_bytes(), edited)
        self.assertEqual(list(self.policy.parent.iterdir()), [self.policy])

    @unittest.skipUnless(os.name == "posix", "POSIX symlink test")
    def test_linked_policy_is_readable_but_writer_preserves_link_and_target(self):
        self.policy.parent.mkdir()
        target = self.root / "linked-policy.json"
        original = b'{"session_max_bytes":4096}'
        target.write_bytes(original)
        self.policy.symlink_to(target)
        result = self.invoke("show", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["limits"]["session_max_bytes"]["value"], 4096)
        code, _, stderr = self.operator("reset-limits")
        self.assertEqual(code, 2)
        self.assertIn("unlinked workspace configuration", stderr)
        self.assertTrue(self.policy.is_symlink())
        self.assertEqual(target.read_bytes(), original)

    def test_shared_status_budget_and_recovery_follow_new_limits(self):
        self.configure(b'{"protected_paths":["keep"]}')
        (self.root / "first").write_text("1234")
        (self.root / "second").write_text("5678")
        (self.root / "keep").write_text("evidence")
        operations = Operations(self.root)
        deleted = operations.call("safe_delete", {"paths": ["first"], "quarantine": True})
        self.assertTrue(deleted["ok"], deleted)
        state = self.trash / "admission.json"
        manifest = self.trash / "manifest.jsonl"
        before = (state.read_bytes(), manifest.read_bytes())

        code, _, stderr = self.operator("set", "session_max_bytes=1")
        self.assertEqual(code, 0, stderr)
        blocked = operations.call("safe_delete", {"paths": ["second"], "dry_run": True})
        self.assertEqual(blocked["result"]["admission"]["code"], "BLOCK_DELETE_LIMIT")
        self.assertEqual((self.root / "second").read_text(), "5678")
        code, stdout, stderr = self.operator("reset-limits")
        self.assertEqual(code, 0, stderr)
        limits = {key: row["value"] for key, row in json.loads(stdout)["limits"].items()}
        self.assertEqual((state.read_bytes(), manifest.read_bytes()), before)
        local = self.invoke("--view", "overview", "--json", script=SCRIPT.with_name("status.py"))
        self.assertEqual(local.returncode, 0, local.stderr)
        query = operations.call("status", {"view": "overview"})["result"]["delete_admission"]
        self.assertEqual(query["limits"], limits)
        self.assertEqual(query["limits"], json.loads(local.stdout)["delete_admission"]["limits"])
        self.assertEqual((query["used_bytes"], query["used_entries"]), (4, 1))
        protected = operations.call("safe_delete", {"paths": ["keep"], "dry_run": True})
        self.assertEqual(protected["result"]["verdict"]["code"], "BLOCK_DELETE_PROTECTED")
        txid = deleted["result"]["txid"]
        self.assertTrue(operations.call("restore", {"txid": txid})["ok"])
        self.assertEqual((self.root / "first").read_text(), "1234")
        self.assertEqual(state.read_bytes(), before[0])

    def test_pending_approval_stops_before_dispatch_after_effective_change(self):
        file = self.root / "old.txt"
        file.write_text("owned")
        saved = cleanup_saved.save_plan(plan_cleanup(str(self.root), targets=["old.txt"], discard=["old.txt"]))
        # Unit-level grant fixture for the existing approval contract.
        task = cleanup_tasks.approve_task(saved)
        self.assertEqual(task["approval_status"], "CURRENT")
        code, _, stderr = self.operator("set", "session_max_entries=9")
        self.assertEqual(code, 0, stderr)
        operations = Operations(self.root)
        args = {"task_id": task["task_id"]}
        query = operations.call("cleanup_task_status", args)["result"]
        self.assertEqual(query["approval_status"], "CONFIG_CHANGED")
        self.assertFalse(query["can_execute"])
        result = operations.call("cleanup_execute", args)["result"]
        self.assertEqual((result["status"], result["counts"]["not_dispatched"]), ("STOPPED", 1))
        self.assertEqual(file.read_text(), "owned")
        self.assertFalse((self.trash / "manifest.jsonl").exists())

    def test_protect_add_remove_preview_apply_and_core_boundary_refusal(self):
        preview = self.invoke("protect", "add", "evidence", "backups/current", "--preview", "--json")
        self.assertEqual(preview.returncode, 0, preview.stderr)
        report = json.loads(preview.stdout)
        self.assertEqual(report["mode"], "preview")
        self.assertTrue(report["requires_confirmation"])
        self.assertTrue(report["effective_policy_changed"])
        self.assertEqual(report["changes"]["protected_paths"]["added"], ["backups/current", "evidence"])
        self.assertEqual(list(self.root.iterdir()), [])

        noninteractive = self.invoke("protect", "add", "evidence", input_text="APPLY\n")
        self.assertEqual(noninteractive.returncode, 2)
        self.assertIn("interactive operator terminal", noninteractive.stderr)

        for forbidden in (".", ".git", ".git/HEAD", ".agent-guard", ".agent-guard/x",
                          ".agent-trash", ".agent-trash/x", "../outside"):
            with self.subTest(forbidden=forbidden):
                code, _, stderr = self.operator("protect", "add", forbidden)
                self.assertEqual(code, 2, stderr)
                self.assertNotIn("Type APPLY", stderr)
                self.assertFalse(self.policy.exists())

        code, stdout, stderr = self.operator("protect", "add", "evidence", "backups/current")
        self.assertEqual(code, 0, stderr)
        saved = json.loads(stdout)
        self.assertEqual(saved["protected_paths"], ["backups/current", "evidence"])
        self.assertEqual(saved["protected_path_count"], 2)

        text_show = self.invoke("show")
        self.assertEqual(text_show.returncode, 0, text_show.stderr)
        self.assertIn("Protected paths:", text_show.stdout)
        self.assertIn('"evidence"', text_show.stdout)
        self.assertIn("Regenerable patterns (workspace):", text_show.stdout)

        (self.root / "evidence").write_text("keep")
        blocked = Operations(self.root).call("safe_delete", {"paths": ["evidence"], "dry_run": True})
        self.assertEqual(blocked["result"]["verdict"]["code"], "BLOCK_DELETE_PROTECTED")

        missing = self.invoke("protect", "remove", "not-configured", "--preview", "--json")
        self.assertEqual(missing.returncode, 2)
        self.assertIn("not configured", missing.stderr)

        rm_preview = self.invoke("protect", "remove", "evidence", "--preview", "--json")
        self.assertEqual(rm_preview.returncode, 0, rm_preview.stderr)
        rm_report = json.loads(rm_preview.stdout)
        self.assertIn("warning", rm_report)
        self.assertEqual(rm_report["changes"]["protected_paths"]["removed"], ["evidence"])
        self.assertEqual(json.loads(self.policy.read_bytes())["protected_paths"], ["backups/current", "evidence"])

        code, stdout, stderr = self.operator("protect", "remove", "evidence")
        self.assertEqual(code, 0, stderr)
        self.assertIn("WARNING:", stderr)
        self.assertEqual(json.loads(stdout)["protected_paths"], ["backups/current"])
        self.assertEqual(json.loads(self.policy.read_bytes())["protected_paths"], ["backups/current"])

    def test_regenerable_add_remove_guardrails_secret_protection_and_quarantine(self):
        subprocess.run(["git", "init", "-q", str(self.root)], check=True, capture_output=True)
        secret_file = "." + "env.local"
        (self.root / ".gitignore").write_text(".venv-wheel-*\n" + secret_file + "\n", encoding="utf-8")
        wheel_dir = self.root / ".venv-wheel-313"
        wheel_dir.mkdir()
        (wheel_dir / "pkg.whl").write_text("synthetic wheel")
        (self.root / secret_file).write_text("SYNTHETIC_VALUE=placeholder\n")

        for bad in ("*", "**", ".*", "src/*", "*.a", "foo/**/bar", "/abs", "a\\b", "a:b",
                    "foo/", "." + "env*", "." + "env.*", ".git*", "*." + "pem", "*." + "key",
                    "id_" + "*", "[unclosed", "a*", " .venv-wheel-*",
                    "build/" + "." + "env*", "build/*." + "pem", ".git/cache-*", "apps/[unclosed/cache"):
            with self.subTest(bad=bad):
                code, _, stderr = self.operator("regenerable", "add", bad)
                self.assertEqual(code, 2, stderr)
                self.assertNotIn("Type APPLY", stderr)
                self.assertFalse(self.policy.exists())

        too_many = [f"cache-slot-{i:02d}" for i in range(MAX_REGENERABLE_PATTERNS + 1)]
        code, _, stderr = self.operator("regenerable", "add", *too_many)
        self.assertEqual(code, 2, stderr)
        self.assertIn(str(MAX_REGENERABLE_PATTERNS), stderr)

        preview = self.invoke("regenerable", "add", ".venv-wheel-*", "--preview", "--json")
        self.assertEqual(preview.returncode, 0, preview.stderr)
        pre_report = json.loads(preview.stdout)
        self.assertTrue(pre_report["requires_confirmation"])
        self.assertIn("PERMANENTLY DELETED", pre_report["warning"])
        self.assertFalse(self.policy.exists())

        # Approve a Cleanup task before updating regenerable_patterns to verify CONFIG_CHANGED.
        discard_file = self.root / "old.txt"
        discard_file.write_text("owned")
        saved = cleanup_saved.save_plan(plan_cleanup(str(self.root), targets=["old.txt"], discard=["old.txt"]))
        task = cleanup_tasks.approve_task(saved)
        self.assertEqual(task["approval_status"], "CURRENT")

        code, stdout, stderr = self.operator("regenerable", "add", ".venv-wheel-*")
        self.assertEqual(code, 0, stderr)
        self.assertIn("PERMANENTLY DELETED", stderr)
        self.assertEqual(json.loads(stdout)["regenerable_patterns"], [".venv-wheel-*"])

        operations = Operations(self.root)
        self.assertEqual(operations.call("cleanup_task_status", {"task_id": task["task_id"]})["result"]["approval_status"],
                         "CONFIG_CHANGED")

        # Workspace pattern makes Git-ignored .venv-wheel-313 regenerable, while --quarantine overrides it.
        default_dry = operations.call("safe_delete", {"paths": [".venv-wheel-313"], "dry_run": True})
        self.assertEqual(default_dry["result"]["verdict"]["code"], "ALLOW_REGENERABLE")
        quarantine_dry = operations.call("safe_delete", {"paths": [".venv-wheel-313"], "dry_run": True, "quarantine": True})
        self.assertEqual(quarantine_dry["result"]["verdict"]["decision"], "RELOCATE")

        plan_default = plan_cleanup(str(self.root), targets=[".venv-wheel-313"])
        self.assertEqual(plan_default["entries"][0]["category"], "regenerable")
        self.assertEqual(plan_default["entries"][0]["effect"], "delete")
        plan_quarantine = plan_cleanup(str(self.root), targets=[".venv-wheel-313"], quarantine=True)
        self.assertEqual(plan_quarantine["entries"][0]["category"], "regenerable")
        self.assertEqual(plan_quarantine["entries"][0]["effect"], "quarantine")

        # Secret-store paths are NEVER regenerable even if an extra pattern tries to match them.
        ctx = guard_policy.PolicyContext(
            workspace=str(self.root), trash_root=str(self.trash), base_dir=str(self.root),
            config=guard_policy.GuardConfig(artifact_patterns=["*.local", "*.example"]),
        )
        secret_specs = classifier.classify_paths(
            [secret_file], str(self.root), str(self.root), str(self.trash)
        )
        self.assertFalse(guard_policy.regenerable(secret_specs, ctx))

        # reset-limits preserves BOTH protected_paths and regenerable_patterns.
        self.assertEqual(self.operator("protect", "add", "old.txt")[0], 0)
        self.assertEqual(self.operator("set", "session_max_bytes=4KiB")[0], 0)
        code, stdout, stderr = self.operator("reset-limits")
        self.assertEqual(code, 0, stderr)
        after_reset = json.loads(stdout)
        self.assertEqual(after_reset["protected_paths"], ["old.txt"])
        self.assertEqual(after_reset["regenerable_patterns"], [".venv-wheel-*"])

        # Remove regenerable pattern and verify unconfigured removal fails.
        code, stdout, stderr = self.operator("regenerable", "remove", ".venv-wheel-*")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(json.loads(stdout)["regenerable_patterns"], [])
        missing = self.invoke("regenerable", "remove", ".venv-wheel-*", "--preview", "--json")
        self.assertEqual(missing.returncode, 2)
        self.assertIn("not configured", missing.stderr)

    def test_regenerable_directory_with_secret_descendant_incomplete_scan_and_segment_matching(self):
        subprocess.run(["git", "init", "-q", str(self.root)], check=True, capture_output=True)
        secret_name = "." + "env.local"
        cert_name = "server." + "pem"
        (self.root / ".gitignore").write_text(
            "dist/\n.venv-wheel-*\napps/\n", encoding="utf-8"
        )
        # 1. Built-in artifact dir containing a nested secret-store file must quarantine, never delete.
        (self.root / "dist/nested").mkdir(parents=True)
        (self.root / "dist/bundle.js").write_text("generated")
        (self.root / "dist/nested" / secret_name).write_text("SYNTHETIC_VALUE=placeholder\n")

        # 2. Custom workspace regenerable dir containing a nested secret-store file must also quarantine.
        code, _, stderr = self.operator("regenerable", "add", ".venv-wheel-*", "apps/*-cache")
        self.assertEqual(code, 0, stderr)
        (self.root / ".venv-wheel-313/sub").mkdir(parents=True)
        (self.root / ".venv-wheel-313/sub" / cert_name).write_text("placeholder cert")

        operations = Operations(self.root)
        for target in ("dist", ".venv-wheel-313"):
            with self.subTest(target=target):
                dry = operations.call("safe_delete", {"paths": [target], "dry_run": True})
                self.assertTrue(dry["ok"], dry)
                self.assertEqual(dry["result"]["verdict"]["decision"], "RELOCATE")
                self.assertNotEqual(dry["result"]["verdict"]["code"], "ALLOW_REGENERABLE")

                # Cleanup without --discard must NEVER propose the secret-containing dir for direct delete.
                plan = plan_cleanup(str(self.root), targets=[target])
                self.assertFalse(
                    any(e["path"] == target and e["effect"] == "delete" for e in plan["entries"]),
                    plan["entries"],
                )
                # Cleanup with explicit --discard must route the dir to quarantine, never delete.
                discard_plan = plan_cleanup(str(self.root), targets=[target], discard=[target])
                self.assertEqual(discard_plan["entries"][0]["path"], target)
                self.assertEqual(discard_plan["entries"][0]["effect"], "quarantine")

        # 3. Incomplete scan (entry bound or unreadable subdir) falls back to quarantine, never direct delete.
        clean_dist = self.root / "out"
        (self.root / ".gitignore").write_text("dist/\n.venv-wheel-*\napps/\nout/\n", encoding="utf-8")
        (clean_dist / "sub").mkdir(parents=True)
        (clean_dist / "sub/a.txt").write_text("gen")
        (clean_dist / "sub/b.txt").write_text("gen")
        self.assertTrue(guard_policy.tree_safe_for_direct_delete(str(clean_dist), "out"))
        self.assertFalse(guard_policy.tree_safe_for_direct_delete(str(clean_dist), "out", max_entries=1))
        self.assertFalse(guard_policy.tree_safe_for_direct_delete(str(clean_dist), "out", max_depth=1))
        out_specs = classifier.classify_paths(["out"], str(self.root), str(self.root), str(self.trash))
        out_ctx = guard_policy.PolicyContext(workspace=str(self.root), trash_root=str(self.trash), base_dir=str(self.root))
        with mock.patch.object(guard_policy, "MAX_REGENERABLE_TREE_ENTRIES", 1):
            bounded_verdict = guard_policy.decide_path_batch(out_specs, out_ctx, recursive=True)
            self.assertEqual(bounded_verdict.decision, "RELOCATE")
        with mock.patch.object(guard_policy, "MAX_REGENERABLE_TREE_DEPTH", 1):
            deep_verdict = guard_policy.decide_path_batch(out_specs, out_ctx, recursive=True)
            self.assertEqual(deep_verdict.decision, "RELOCATE")
        incomplete_plan = plan_cleanup(str(self.root), targets=["out"], max_entries=1)
        self.assertFalse(incomplete_plan["complete"])
        self.assertEqual(incomplete_plan["entries"][0]["category"], "uncertain")
        self.assertNotEqual(incomplete_plan["entries"][0]["effect"], "delete")

        if os.name == "posix" and os.geteuid() != 0:
            locked = clean_dist / "locked"
            locked.mkdir()
            (locked / "inside.txt").write_text("hidden")
            locked.chmod(0o000)
            self.addCleanup(lambda: locked.chmod(0o755))
            unreadable_dry = operations.call("safe_delete", {"paths": ["out"], "dry_run": True})
            self.assertEqual(unreadable_dry["result"]["verdict"]["decision"], "RELOCATE")

        # 4. Segment-by-segment matching: '*' in 'apps/*-cache' must NOT cross '/'.
        (self.root / "apps/web-cache").mkdir(parents=True)
        (self.root / "apps/web-cache/item.bin").write_text("ok")
        (self.root / "apps/foo/bar-cache").mkdir(parents=True)
        (self.root / "apps/foo/bar-cache/keep.bin").write_text("must not cross slash")

        direct = operations.call("safe_delete", {"paths": ["apps/web-cache"], "dry_run": True})
        self.assertEqual(direct["result"]["verdict"]["code"], "ALLOW_REGENERABLE")
        cross_slash = operations.call("safe_delete", {"paths": ["apps/foo/bar-cache"], "dry_run": True})
        self.assertEqual(cross_slash["result"]["verdict"]["decision"], "RELOCATE")
        self.assertNotEqual(cross_slash["result"]["verdict"]["code"], "ALLOW_REGENERABLE")
        plan_cross = plan_cleanup(str(self.root), targets=["apps/foo/bar-cache"])
        self.assertEqual(plan_cross["entries"][0]["category"], "uncertain")
        self.assertNotEqual(plan_cross["entries"][0]["effect"], "delete")
