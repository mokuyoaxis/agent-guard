"""Cleanup proposals and explicitly confirmed execution in synthetic repos."""
import contextlib
import errno
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

from core import policy
from core.cleanup import plan_cleanup, select_entries
from tests.helpers import RepoFixture, git_available


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/delete-guard/scripts/cleanup.py"
with mock.patch.object(sys, "path", [str(SCRIPT.parent), *sys.path]):
    spec = importlib.util.spec_from_file_location("cleanup_cli", SCRIPT)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)


@unittest.skipUnless(git_available(), "git required")
class CleanupTests(RepoFixture):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {
            k: v for k, v in os.environ.items() if not k.startswith("AGENT_GUARD_")
        }, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        super().setUp()
        self.physical_root = os.path.realpath(self.root)
        self.write(".gitignore", "build/\nnode_modules/\n*.log\n.internal/\n.agent-trash/\n")

    def plan(self, **kwargs):
        return plan_cleanup(self.root, **kwargs)

    def rows(self, plan):
        return {e["path"]: e for e in plan["entries"]}

    def invoke(self, *args):
        return subprocess.run([sys.executable, "-B", str(SCRIPT), "--root", self.root,
                               "--json", *args], cwd=self.root, capture_output=True,
                              text=True, timeout=30)

    def execute(self, paths, *, discard=(), confirmation=True):
        plan = self.plan(discard=discard)
        args = ["--root", self.root, "--json", "--execute", "--plan-id", plan["plan_id"]]
        for path in discard:
            args.extend(["--discard", path])
        for path in paths:
            args.extend(["--select", path])
        output = io.StringIO()
        with mock.patch.object(cli, "confirm", return_value=confirmation), contextlib.redirect_stdout(output):
            exit_code = cli.main(args)
        return exit_code, json.loads(output.getvalue())

    def test_four_categories_and_actual_policy_projection(self):
        self.write("build/output.bin", "generated")
        self.write("old.txt", "discarded")
        self.write("backups/keep.txt", "evidence")
        rows = self.rows(self.plan(discard=["old.txt"], protect=["backups"]))
        self.assertEqual(rows["build"]["category"], "regenerable")
        self.assertEqual(rows["build"]["effect"], "delete")
        self.assertEqual(rows["old.txt"]["category"], "discarded")
        self.assertEqual(rows["old.txt"]["effect"], "quarantine")
        self.assertEqual(rows["src"]["category"], "uncertain")
        self.assertEqual(rows["backups"]["category"], "protected")
        self.assertIsNone(rows["backups"]["size_bytes"])
        self.assertFalse(rows[".git"]["selectable"])

    def test_ignored_unknown_and_old_untracked_files_are_uncertain(self):
        self.write("notes.log", "valuable")
        path = self.write("old.txt", "valuable")
        os.utime(path, (1, 1))
        rows = self.rows(self.plan())
        self.assertEqual(rows["notes.log"]["category"], "uncertain")
        self.assertEqual(rows["old.txt"]["category"], "uncertain")

    def test_artifact_name_without_git_ignore_is_not_regeneration_proof(self):
        self.write("dist/output", "valuable")
        rows = self.rows(self.plan())
        self.assertEqual(rows["dist"]["category"], "uncertain")
        row = self.rows(self.plan(discard=["dist"]))["dist"]
        self.assertEqual((row["category"], row["effect"]), ("discarded", "quarantine"))

    def test_tracked_descendants_prevent_generated_directory_proposal(self):
        self.write("build/important.txt", "tracked")
        subprocess.run(["git", "-C", self.root, "add", "-f", "build/important.txt"],
                       capture_output=True, check=True)
        self.assertEqual(self.rows(self.plan())["build"]["category"], "uncertain")

    def test_protected_subtrees_never_scanned_even_under_artifact_names(self):
        self.write(".internal/backups/build/evidence", "private")
        self.write("build/.internal/evidence", "private")
        self.write("build/output", "generated")
        real_scandir = os.scandir

        def checked(path):
            relative = Path(path).relative_to(self.physical_root)
            self.assertFalse(set(relative.parts) & {".internal", ".git", ".agent-trash"})
            return real_scandir(path)

        with mock.patch("core.cleanup.os.scandir", side_effect=checked):
            plan = self.plan(discard=["build", ".internal"])
        rows = self.rows(plan)
        self.assertEqual(rows[".internal"]["category"], "protected")
        self.assertEqual(rows["build/.internal"]["category"], "protected")
        self.assertNotIn("build", rows)
        self.assertTrue(rows["build/output"]["selectable"])
        with self.assertRaises(ValueError):
            select_entries(plan, ["build"], plan["plan_id"])

    def test_workspace_symlink_alias_keeps_plan_and_protected_scans_consistent(self):
        import tempfile

        self.write("build/output", "generated")
        self.write(".internal/evidence", "private")
        original = self.plan()
        with tempfile.TemporaryDirectory(prefix="cleanup-alias-") as base:
            alias = Path(base, "workspace-alias")
            try:
                alias.symlink_to(self.root, target_is_directory=True)
            except OSError as exc:
                self.skipTest(str(exc))
            real_scandir = os.scandir

            def checked(path):
                relative = Path(path).relative_to(self.physical_root)
                self.assertFalse(set(relative.parts) & {".internal", ".git", ".agent-trash"})
                return real_scandir(path)

            with mock.patch("core.cleanup.os.scandir", side_effect=checked):
                aliased = plan_cleanup(str(alias))
            self.assertEqual(original, aliased)

    def test_custom_recovery_bucket_is_protected_before_parent_selection(self):
        self.write("build/recovery/manifest.jsonl", "evidence")
        self.write("build/output", "generated")
        with mock.patch.dict(os.environ, {"AGENT_GUARD_TRASH": str(Path(self.root, "build/recovery"))}):
            rows = self.rows(self.plan())
        # Existing Core protects the bucket's parent too, so the whole
        # branch is pruned before the scanner reaches its contents.
        self.assertEqual(rows["build"]["category"], "protected")
        self.assertNotIn("build/recovery", rows)

    def test_declared_protected_descendant_excludes_ancestor_even_if_absent(self):
        self.write("build/output", "generated")
        rows = self.rows(self.plan(protect=["build/future-evidence"]))
        self.assertNotIn("build", rows)
        self.assertTrue(rows["build/output"]["selectable"])

    def test_declared_protection_through_link_covers_physical_subtree_and_link(self):
        self.write("build/actual/evidence/keep.txt", "protected synthetic bytes")
        self.write("build/actual/output", "generated")
        try:
            Path(self.root, "build/alias").symlink_to("actual", target_is_directory=True)
        except OSError as exc:
            self.skipTest(str(exc))
        plan = self.plan(protect=["build/alias/evidence"])
        rows = self.rows(plan)
        self.assertNotIn("build/actual", rows)
        self.assertEqual(rows["build/actual/evidence"]["category"], "protected")
        self.assertEqual(rows["build/alias"]["category"], "protected")
        self.assertTrue(rows["build/actual/output"]["selectable"])
        for path in ("build/actual", "build/alias", "build/actual/evidence"):
            with self.assertRaises(ValueError):
                select_entries(plan, [path], plan["plan_id"])

    def test_default_internal_link_protects_its_physical_evidence(self):
        self.write("build/evidence/keep.txt", "protected synthetic bytes")
        self.write("build/output", "generated")
        try:
            Path(self.root, ".internal").symlink_to("build/evidence", target_is_directory=True)
        except OSError as exc:
            self.skipTest(str(exc))
        rows = self.rows(self.plan())
        self.assertNotIn("build", rows)
        self.assertEqual(rows[".internal"]["category"], "protected")
        self.assertEqual(rows["build/evidence"]["category"], "protected")
        self.assertTrue(rows["build/output"]["selectable"])

    def test_directory_symlink_does_not_scan_external_contents(self):
        with contextlib.ExitStack() as stack:
            import tempfile
            external = Path(stack.enter_context(tempfile.TemporaryDirectory()))
            (external / "private.txt").write_text("outside")
            try:
                Path(self.root, "link").symlink_to(external, target_is_directory=True)
            except OSError as exc:
                self.skipTest(str(exc))
            row = self.rows(self.plan(discard=["link"]))["link"]
            self.assertEqual(row["category"], "protected")
            self.assertFalse(row["selectable"])

    def test_nested_protected_alias_requires_upfront_physical_protection(self):
        self.write("build/evidence/keep.txt", "protected synthetic bytes")
        self.write("build/output", "generated")
        Path(self.root, "archive").mkdir()
        try:
            Path(self.root, "archive/.internal").symlink_to(
                "../build/evidence", target_is_directory=True)
        except OSError as exc:
            self.skipTest(str(exc))
        plan = self.plan()
        self.assertFalse(plan["complete"])
        self.assertIn({"path": "archive/.internal",
                       "code": "PROTECTED_ALIAS_REQUIRES_EXPLICIT_PROTECT"}, plan["errors"])
        with self.assertRaises(ValueError):
            select_entries(plan, ["build"], plan["plan_id"])
        declared = self.plan(protect=["archive/.internal"])
        rows = self.rows(declared)
        self.assertTrue(declared["complete"])
        self.assertNotIn("build", rows)
        self.assertEqual(rows["build/evidence"]["category"], "protected")
        self.assertTrue(rows["build/output"]["selectable"])

    def test_nested_generated_child_remains_visible_under_unknown_parent(self):
        self.write("src/node_modules/pkg/index.js", "generated")
        rows = self.rows(self.plan())
        self.assertEqual(rows["src/node_modules"]["category"], "regenerable")
        self.assertIn("src/main.py", rows)
        self.assertNotIn("src", rows)

    def test_hardlinks_and_parent_child_sizes_are_deduplicated(self):
        first = self.write("build/a", "12345")
        try:
            os.link(first, Path(self.root, "build/b"))
        except OSError as exc:
            self.skipTest(str(exc))
        plan = self.plan()
        self.assertEqual(self.rows(plan)["build"]["size_bytes"], 5)
        self.assertEqual(plan["summary"]["regenerable"]["size_bytes"], 5)
        self.assertNotIn("build/a", self.rows(plan))

    def test_stable_plan_id_and_no_metadata_creation(self):
        self.write("build/output", "generated")
        first = self.plan()
        second = self.plan()
        self.assertEqual(first, second)
        self.assertFalse(Path(self.root, ".agent-trash").exists())
        self.assertNotIn(".agent-trash", Path(self.root, ".git/info/exclude").read_text())

    def test_plan_id_tracks_contents_metadata_membership_and_replacement(self):
        target = Path(self.write("build/output", "generated"))
        before = self.plan()["plan_id"]
        target.write_text("new bytes")
        after_write = self.plan()["plan_id"]
        self.assertNotEqual(before, after_write)
        self.write("build/new", "new member")
        after_member = self.plan()["plan_id"]
        self.assertNotEqual(after_write, after_member)
        target.rename(target.with_name("renamed"))
        self.assertNotEqual(after_member, self.plan()["plan_id"])

    def test_policy_configuration_and_restricted_mode_are_respected(self):
        self.write("build/output", "generated")
        original = self.plan()
        with mock.patch.dict(os.environ, {"AGENT_GUARD_ALLOW_REGENERABLE": "0"}):
            disabled = self.plan()
        self.assertNotEqual(original["plan_id"], disabled["plan_id"])
        self.assertEqual(self.rows(disabled)["build"]["effect"], "quarantine")
        with mock.patch("core.cleanup.policy.load_mode", return_value={"mode": policy.MODE_RESTRICTED}):
            restricted = self.plan()
        self.assertFalse(self.rows(restricted)["build"]["selectable"])
        self.assertEqual(self.rows(restricted)["build"]["policy_code"], policy.CODE_BLOCK_RESTRICTED_MODE)

    def test_entry_and_depth_limits_report_partial_and_refuse_execution(self):
        self.write("build/nested/output", "generated")
        for options, code in (({"max_entries": 1}, "ENTRY_LIMIT_REACHED"),
                              ({"max_depth": 1}, "DEPTH_LIMIT_REACHED")):
            with self.subTest(options=options):
                plan = self.plan(**options)
                self.assertFalse(plan["complete"])
                self.assertIn(code, [e["code"] for e in plan["errors"]])
                with self.assertRaises(ValueError):
                    select_entries(plan, ["build"], plan["plan_id"])

    def test_permission_error_keeps_partial_results_and_error(self):
        self.write("unreadable", "private")
        original = os.lstat

        def fail(path, *args, **kwargs):
            if os.fspath(path) == os.path.join(self.physical_root, "unreadable"):
                raise PermissionError(errno.EACCES, "synthetic failure")
            return original(path, *args, **kwargs)

        with mock.patch("core.cleanup.os.lstat", side_effect=fail):
            plan = self.plan()
        self.assertFalse(plan["complete"])
        self.assertIn({"path": "unreadable", "code": "OS_ERROR_13"}, plan["errors"])

    def test_git_unavailable_supplies_no_artifact_proof(self):
        self.write("build/output", "valuable")
        with mock.patch("core.cleanup.subprocess.run", side_effect=FileNotFoundError):
            plan = self.plan()
        self.assertEqual(self.rows(plan)["build"]["category"], "uncertain")
        self.assertEqual(plan["warnings"], ["GIT_UNAVAILABLE"])

    def test_ordinary_paths_use_two_batched_git_queries(self):
        for i in range(20):
            self.write("ordinary/" + str(i), "valuable")
        with mock.patch("core.cleanup.subprocess.run", wraps=subprocess.run) as run:
            self.plan()
        self.assertEqual(run.call_count, 2)

    def test_unsafe_or_unreviewed_selections_are_rejected(self):
        self.write("build/output", "generated")
        plan = self.plan()
        for paths, plan_id in (([], plan["plan_id"]), (["src"], plan["plan_id"]),
                               ([".git"], plan["plan_id"]), (["../outside"], plan["plan_id"]),
                               (["build/output"], plan["plan_id"]), (["build"], "stale")):
            with self.subTest(paths=paths, plan_id=plan_id):
                with self.assertRaises(ValueError):
                    select_entries(plan, paths, plan_id)

    def test_magic_and_trailing_whitespace_names_remain_blocked(self):
        for name in ("old[1]", "old$var", "old "):
            self.write(name, "valuable")
            plan = self.plan(discard=[name])
            self.assertFalse(self.rows(plan)[name]["selectable"])

    def test_invalid_scan_limits_and_root_discard_are_rejected(self):
        for options in ({"max_entries": 0}, {"max_entries": True}, {"max_depth": 129},
                        {"discard": ["."]}, {"discard": ["../outside"]}):
            with self.subTest(options=options):
                with self.assertRaises(ValueError):
                    self.plan(**options)

    def test_cli_plan_is_read_only_even_without_python_B(self):
        self.write("build/output", "generated")
        before = {str(p.relative_to(self.root)): p.stat().st_mtime_ns
                  for p in Path(self.root).rglob("*")}
        env = dict(os.environ)
        env.pop("PYTHONDONTWRITEBYTECODE", None)
        proc = subprocess.run([sys.executable, str(SCRIPT), "--root", self.root, "--json"],
                              env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(json.loads(proc.stdout)["complete"])
        after = {str(p.relative_to(self.root)): p.stat().st_mtime_ns
                 for p in Path(self.root).rglob("*")}
        self.assertEqual(before, after)
        self.assertFalse(Path(self.root, ".agent-trash").exists())

    def test_cli_plan_passes_with_all_python_mutation_calls_forbidden(self):
        self.write("build/output", "generated")
        loader = '''import os, runpy, stat, sys
def guard(event, args):
    if event == 'open' and args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
        # Git stdin uses an already-open pipe; this is IPC, not a file write.
        if isinstance(args[0], int) and stat.S_ISFIFO(os.fstat(args[0]).st_mode):
            return
        raise RuntimeError('unexpected write attempt')
    if event in ('os.mkdir', 'os.remove', 'os.rmdir', 'os.rename', 'os.chmod', 'os.link', 'os.symlink', 'os.truncate', 'os.utime'):
        raise RuntimeError('unexpected mutation attempt')
sys.addaudithook(guard)
sys.path.insert(0, os.path.dirname(sys.argv[1]))
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name='__main__')
'''
        proc = subprocess.run([sys.executable, "-B", "-c", loader, str(SCRIPT),
                               "--root", self.root, "--json"], capture_output=True,
                              text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(json.loads(proc.stdout)["complete"])

    def _terminal_delete_generated(self, script):
        import pty
        import select
        import time

        self.write("build/output", "generated")
        self.write("notes.txt", "keep")
        plan = self.plan()
        master, slave = pty.openpty()
        proc = subprocess.Popen(
            [sys.executable, "-B", str(script), "--root", self.root, "--json", "--execute",
             "--plan-id", plan["plan_id"], "--select", "build"], cwd=self.root,
            stdin=slave, stderr=slave, stdout=subprocess.PIPE, text=True)
        os.close(slave)
        prompt = b""
        try:
            deadline = time.monotonic() + 20
            while b"to execute this selection: " not in prompt and time.monotonic() < deadline:
                ready, _, _ = select.select([master], [], [], 0.2)
                if ready:
                    prompt += os.read(master, 65536)
                if proc.poll() is not None:
                    break
            self.assertIn(b"to execute this selection: ", prompt)
            os.write(master, b"EXECUTE\n")
            output, _ = proc.communicate(timeout=30)
            self.assertEqual(proc.returncode, 0, output)
            self.assertEqual(json.loads(output)["outcome"], "executed")
            self.assertFalse(Path(self.root, "build").exists())
            self.assertEqual(Path(self.root, "notes.txt").read_text(), "keep")
        finally:
            os.close(master)
            if proc.poll() is None:
                proc.terminate()
                proc.communicate(timeout=10)

    @unittest.skipUnless(os.name == "posix", "POSIX terminal required")
    def test_real_terminal_confirmation_executes_only_selected_artifact(self):
        self._terminal_delete_generated(SCRIPT)

    @unittest.skipUnless(os.name == "posix", "POSIX terminal required")
    def test_terminal_execution_through_npm_style_bin_symlink(self):
        import tempfile

        with tempfile.TemporaryDirectory(prefix="cleanup-bin-") as base:
            alias = Path(base, "agent-guard-cleanup")
            try:
                alias.symlink_to(SCRIPT)
            except OSError as exc:
                self.skipTest(str(exc))
            self._terminal_delete_generated(alias)

    def test_noninteractive_execution_and_missing_plan_id_make_no_changes(self):
        self.write("build/output", "generated")
        plan = self.plan()
        for args in (("--execute", "--select", "build"),
                     ("--execute", "--plan-id", plan["plan_id"], "--select", "build")):
            with self.subTest(args=args):
                proc = self.invoke(*args)
                self.assertEqual(proc.returncode, 2)
                self.assertEqual(json.loads(proc.stdout)["outcome"], "refused")
                self.assertTrue(Path(self.root, "build/output").exists())
                self.assertFalse(Path(self.root, ".agent-trash").exists())

    def test_cancel_confirmation_leaves_source_and_recovery_layout_untouched(self):
        self.write("old.txt", "valuable")
        code, result = self.execute(["old.txt"], discard=["old.txt"], confirmation=False)
        self.assertEqual((code, result["outcome"]), (0, "cancelled"))
        self.assertTrue(Path(self.root, "old.txt").exists())
        self.assertFalse(Path(self.root, ".agent-trash").exists())

    def test_change_during_confirmation_refuses_before_safe_delete(self):
        target = Path(self.write("build/output", "generated"))
        plan = self.plan()

        def changed(*args):
            target.write_text("new unreviewed bytes")
            return True

        output = io.StringIO()
        with mock.patch.object(cli, "confirm", side_effect=changed), contextlib.redirect_stdout(output):
            code = cli.main(["--root", self.root, "--json", "--execute", "--plan-id",
                             plan["plan_id"], "--select", "build"])
        self.assertEqual(code, 2)
        self.assertIn("plan changed", json.loads(output.getvalue())["error"])
        self.assertTrue(target.exists())
        self.assertFalse(Path(self.root, ".agent-trash").exists())

    def test_ordinary_discard_relocates_and_restores_with_exact_bytes(self):
        name = "-old file.txt"
        self.write(name, "valuable synthetic bytes")
        code, result = self.execute([name], discard=[name])
        self.assertEqual(code, 0, result)
        report = result["reports"][0]["report"]
        self.assertEqual(report["outcome"], "relocated to quarantine")
        self.assertIn("still use disk space", report["storage_notice"])
        self.assertFalse(Path(self.root, name).exists())
        proc = subprocess.run([sys.executable, "-B", str(SCRIPT.with_name("restore.py")),
                               report["txid"], "--json"], cwd=self.root,
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(Path(self.root, name).read_text(), "valuable synthetic bytes")

    def test_regenerable_is_physically_deleted_via_existing_safe_delete(self):
        self.write("build/output", "generated bytes")
        code, result = self.execute(["build"])
        self.assertEqual(code, 0, result)
        self.assertFalse(Path(self.root, "build").exists())
        report = result["reports"][0]["report"]
        self.assertEqual(report["verdict"]["code"], policy.CODE_ALLOW_REGENERABLE)
        self.assertNotIn("txid", report)
        events = [json.loads(s) for s in Path(self.root, ".agent-trash/audit.jsonl").read_text().splitlines()]
        self.assertEqual([e["phase"] for e in events], ["intent", "complete"])
        self.assertTrue(all(e["tool"] == "safe_delete" for e in events))

    def test_mixed_selection_preserves_per_entry_effects(self):
        self.write("build/output", "generated")
        self.write("old.txt", "valuable")
        code, result = self.execute(["old.txt", "build"], discard=["old.txt"])
        self.assertEqual(code, 0, result)
        self.assertEqual([r["report"]["verdict"]["code"] for r in result["reports"]],
                         [policy.CODE_ALLOW_REGENERABLE, policy.CODE_RELOCATE_PATHS])
        self.assertNotIn("txid", result["reports"][0]["report"])
        self.assertIn("txid", result["reports"][1]["report"])

    def test_execution_stops_on_child_failure_and_retains_prior_reports(self):
        self.write("old-a", "valuable")
        self.write("old-b", "valuable")
        self.write("old-c", "valuable")
        plan = self.plan(discard=["old-a", "old-b", "old-c"])
        selected = select_entries(plan, ["old-a", "old-b", "old-c"], plan["plan_id"])
        run = mock.Mock(side_effect=[
            subprocess.CompletedProcess([], 0, '{"outcome":"relocated","txid":"synthetic-first"}'),
            subprocess.CompletedProcess([], 2, '{"outcome":"partial failure","txid":"synthetic-partial"}'),
        ])
        result = cli.execute_selection(plan, selected, runner=run)
        self.assertEqual(result["outcome"], "stopped_after_failure")
        self.assertEqual(run.call_count, 2)
        self.assertEqual([r["report"]["txid"] for r in result["reports"]],
                         ["synthetic-first", "synthetic-partial"])

    def test_confirmation_requires_exact_token_and_escapes_names(self):
        self.write("old.txt", "valuable")
        plan = self.plan(discard=["old.txt"])
        selected = select_entries(plan, ["old.txt"], plan["plan_id"])
        for text, expected in (("yes\n", False), ("EXECUTE\n", True)):
            stdin, stderr = io.StringIO(text), io.StringIO()
            stdin.isatty = lambda: True
            stderr.isatty = lambda: True
            with mock.patch.object(cli.sys, "stdin", stdin), mock.patch.object(cli.sys, "stderr", stderr):
                self.assertEqual(cli.confirm(plan, selected), expected)
            self.assertIn("quarantine retains", stderr.getvalue())

    def test_child_launch_and_protocol_errors_stop_without_losing_prior_reports(self):
        self.write("old-a", "valuable")
        self.write("old-b", "valuable")
        plan = self.plan(discard=["old-a", "old-b"])
        selected = select_entries(plan, ["old-a", "old-b"], plan["plan_id"])
        first = subprocess.CompletedProcess([], 0, '{"txid":"synthetic-prior"}')
        for failure in (OSError("synthetic startup failure"),
                        subprocess.CompletedProcess([], 0, "[]"),
                        subprocess.CompletedProcess([], 1, "invalid JSON")):
            with self.subTest(failure=type(failure).__name__):
                result = cli.execute_selection(plan, selected, runner=mock.Mock(side_effect=[first, failure]))
                self.assertEqual(result["outcome"], "stopped_after_failure")
                self.assertEqual(result["reports"][0]["report"]["txid"], "synthetic-prior")
                self.assertIn("error", result["reports"][1])


if __name__ == "__main__":
    unittest.main()
