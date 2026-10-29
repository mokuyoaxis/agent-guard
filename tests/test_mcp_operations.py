"""MCP's fixed-workspace operations exercise the existing CLIs, without SDK."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from adapters.mcp.operations import InvalidArguments, Operations
from tests.helpers import RepoFixture, git_available


@unittest.skipUnless(git_available(), "git required")
class McpOperations(RepoFixture):
    def setUp(self):
        # Keep fixture commits from launching detached Git maintenance.
        with mock.patch.dict(os.environ, {"GIT_CONFIG_COUNT": "1",
                                          "GIT_CONFIG_KEY_0": "maintenance.auto",
                                          "GIT_CONFIG_VALUE_0": "false"}):
            super().setUp()
        # macOS temporary paths can use /var as an alias of /private/var.
        # MCP binds the physical workspace, so compare against that identity.
        self.root = str(Path(self.root).resolve())
        self.operations = Operations(self.root)

    def configure(self, **values):
        self.write(".agent-guard/delete-policy.json", json.dumps(values))

    def delete(self, *paths, **extra):
        return self.operations.call("safe_delete", {"paths": list(paths), **extra})

    def files(self):
        return {str(path.relative_to(self.root)): path.read_bytes()
                for path in Path(self.root).rglob("*") if path.is_file()}

    def test_status_transactions_and_gc_plan_are_read_only(self):
        before = self.files()
        status = self.operations.call("status", {})
        listing = self.operations.call("transactions", {})
        plan = self.operations.call("gc_plan", {})
        self.assertTrue(status["ok"], status)
        self.assertEqual(status["result"]["workspace"], self.root)
        self.assertEqual(listing["result"]["transactions"], [])
        self.assertEqual(plan["result"]["mode"], "plan")
        self.assertEqual(plan["result"]["eligible"], [])
        after = self.files()
        self.assertEqual(after, before, {
            "added": sorted(after.keys() - before.keys()),
            "removed": sorted(before.keys() - after.keys()),
            "changed": sorted(name for name in before.keys() & after.keys()
                              if before[name] != after[name]),
        })

    def test_ordinary_delete_returns_actual_recovery_evidence_and_restores(self):
        self.write("valuable", "original bytes")
        deleted = self.delete("valuable")
        self.assertTrue(deleted["ok"], deleted)
        self.assertFalse(Path(self.root, "valuable").exists())
        txid = deleted["result"]["txid"]
        self.assertTrue(deleted["result"]["moved"])
        listing = self.operations.call("transactions", {})
        self.assertEqual(listing["result"]["transactions"][0]["state"], "RESTORABLE")
        restored = self.operations.call("restore", {"txid": txid})
        self.assertTrue(restored["ok"], restored)
        self.assertEqual(Path(self.root, "valuable").read_text(), "original bytes")
        listing = self.operations.call("transactions", {})
        self.assertEqual(listing["result"]["transactions"][0]["state"], "RESTORED")

    def test_literal_glob_characters_and_shell_metacharacters_keep_existing_core_refusal(self):
        literal = "-old[1]*?`$(never);.txt"
        neighbor = "-old1AB`$(never);.txt"
        self.write(literal, "literal")
        self.write(neighbor, "neighbor")
        deleted = self.delete(literal)
        self.assertFalse(deleted["ok"], deleted)
        self.assertEqual(deleted["result"]["verdict"]["code"], "BLOCK_UNDETERMINABLE_EFFECT")
        self.assertEqual(Path(self.root, literal).read_text(), "literal")
        self.assertEqual(Path(self.root, neighbor).read_text(), "neighbor")

    def test_ordinary_leading_dash_spaces_and_semicolon_are_literal(self):
        name = "-old file;still-a-name.txt"
        self.write(name, "literal")
        deleted = self.delete(name)
        self.assertTrue(deleted["ok"], deleted)
        self.assertFalse(Path(self.root, name).exists())
        self.assertTrue(self.operations.call("restore", {"txid": deleted["result"]["txid"]})["ok"])
        self.assertEqual(Path(self.root, name).read_text(), "literal")

    def test_glob_pattern_is_literal_and_does_not_expand(self):
        self.write("first.txt", "keep")
        result = self.delete("*.txt")
        self.assertTrue(result["ok"], result)
        self.assertEqual(Path(self.root, "first.txt").read_text(), "keep")
        self.assertEqual(self.operations.call("transactions", {})["result"]["transactions"], [])

    def test_trailing_whitespace_cannot_select_a_different_literal_file(self):
        self.write("valuable ", "intended literal")
        self.write("valuable", "neighbor")
        for target in ("valuable ", "valuable /", "valuable /."):
            with self.subTest(target=target), self.assertRaises(InvalidArguments):
                self.delete(target)
        self.assertEqual(Path(self.root, "valuable ").read_text(), "intended literal")
        self.assertEqual(Path(self.root, "valuable").read_text(), "neighbor")
        self.assertFalse(Path(self.root, ".agent-trash").exists())

    def test_quarantine_payload_and_aliases_cannot_be_used_to_execute_gc(self):
        self.write("valuable", "original")
        deleted = self.delete("valuable")
        trash_path = deleted["result"]["moved"][0]["trash"]
        os.symlink(self.operations.trash_root, Path(self.root, "trash-alias"))
        for target in (trash_path, ".agent-trash", "trash-alias", ".", self.root):
            with self.subTest(target=target):
                result = self.delete(target)
                self.assertFalse(result["ok"], result)
                self.assertEqual(result["error"]["code"], "MCP_QUARANTINE_PROTECTED")
                self.assertEqual(Path(trash_path).read_text(), "original")

    def test_quarantine_refusal_preflights_whole_batch_before_other_source(self):
        self.write("other", "keep")
        result = self.delete("other", ".agent-trash")
        self.assertFalse(result["ok"])
        self.assertEqual(Path(self.root, "other").read_text(), "keep")
        self.assertFalse(Path(self.root, ".agent-trash").exists())

    def test_external_target_is_refused_by_existing_core(self):
        with tempfile.TemporaryDirectory(prefix="agent-guard-mcp-outside-") as outside:
            sentinel = Path(outside, "keep")
            sentinel.write_text("outside")
            result = self.delete(str(sentinel))
            self.assertEqual(result["exit_code"], 2, result)
            self.assertEqual(result["result"]["verdict"]["code"], "BLOCK_OUT_OF_WORKSPACE")
            self.assertEqual(sentinel.read_text(), "outside")

    def test_configured_protection_and_large_batch_keep_sources(self):
        self.configure(batch_max_bytes=1, protected_paths=["evidence"])
        self.write("evidence", "protected")
        self.write("large", "large bytes")
        for path, code in (("evidence", "BLOCK_DELETE_PROTECTED"),
                           ("large", "BLOCK_DELETE_BATCH_CONFIRM")):
            with self.subTest(path=path):
                result = self.delete(path)
                self.assertEqual(result["exit_code"], 2, result)
                self.assertEqual(result["result"]["verdict"]["code"], code)
                self.assertTrue(Path(self.root, path).exists())

    def test_default_budget_survives_new_operations_instance_and_restore(self):
        self.configure(session_max_entries=1)
        self.write("first", "first")
        self.write("second", "second")
        deleted = self.delete("first")
        self.assertTrue(deleted["ok"], deleted)
        fresh = Operations(self.root)
        denied = fresh.call("safe_delete", {"paths": ["second"]})
        self.assertEqual(denied["result"]["verdict"]["code"], "BLOCK_DELETE_LIMIT")
        self.assertEqual(Path(self.root, "second").read_text(), "second")
        self.assertTrue(fresh.call("restore", {"txid": deleted["result"]["txid"]})["ok"])
        status = fresh.call("status", {})["result"]["delete_admission"]
        self.assertEqual(status["used_entries"], 1)

    def test_explicit_startup_session_is_stable_and_separate(self):
        self.configure(session_max_entries=1)
        self.write("first", "first")
        self.write("second", "second")
        first = Operations(self.root, session="operator-session")
        self.assertTrue(first.call("safe_delete", {"paths": ["first"]})["ok"])
        repeated = Operations(self.root, session="operator-session")
        self.assertEqual(repeated.call("status", {})["result"]["delete_admission"]["used_entries"], 1)
        self.assertEqual(self.operations.call("status", {})["result"]["delete_admission"]["used_entries"], 0)
        self.assertEqual(repeated.call("safe_delete", {"paths": ["second"]})["exit_code"], 2)

    def test_symlinked_workspace_shares_binding_and_persistent_budget(self):
        self.configure(session_max_entries=1)
        self.write("first", "first")
        self.write("second", "second")
        with tempfile.TemporaryDirectory(prefix="agent-guard-mcp-alias-") as parent:
            alias = Path(parent, "workspace")
            alias.symlink_to(self.root, target_is_directory=True)
            linked = Operations(alias)
            self.assertEqual(linked.workspace, self.root)
            self.assertEqual(linked.trash_root, self.operations.trash_root)
            self.assertEqual(linked._env["AGENT_GUARD_WORKSPACE"], self.root)
            self.assertEqual(linked._env["AGENT_GUARD_TRASH"], self.operations.trash_root)
            deleted = linked.call("safe_delete", {"paths": ["first"]})
            self.assertTrue(deleted["ok"], deleted)
            physical = Operations(self.root)
            denied = physical.call("safe_delete", {"paths": ["second"]})
            self.assertEqual(denied["result"]["verdict"]["code"], "BLOCK_DELETE_LIMIT")
            self.assertEqual(Path(self.root, "second").read_text(), "second")
            restored = physical.call("restore", {"txid": deleted["result"]["txid"]})
            self.assertTrue(restored["ok"], restored)
            self.assertEqual(Path(self.root, "first").read_text(), "first")
            self.assertEqual(linked.call("status", {})["result"]["delete_admission"]["used_entries"], 1)

    def test_restore_conflict_does_not_overwrite_occupant(self):
        self.write("valuable", "original")
        deleted = self.delete("valuable")
        self.write("valuable", "new occupant")
        restored = self.operations.call("restore", {"txid": deleted["result"]["txid"]})
        self.assertFalse(restored["ok"])
        self.assertEqual(restored["exit_code"], 3)
        self.assertTrue(restored["result"]["conflicts"])
        self.assertEqual(Path(self.root, "valuable").read_text(), "new occupant")

    def test_dry_run_keeps_targets_and_does_not_spend_budget(self):
        self.write("valuable", "original")
        result = self.delete("valuable", dry_run=True)
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["result"]["dry_run"])
        self.assertEqual(Path(self.root, "valuable").read_text(), "original")
        self.assertFalse(Path(self.root, ".agent-trash/admission.json").exists())

    def test_invalid_state_is_visible_in_status_without_hiding_gc(self):
        self.write(".agent-trash/admission.json", "invalid JSON")
        status = self.operations.call("status", {})
        self.assertTrue(status["ok"], status)
        self.assertEqual(status["result"]["delete_admission"]["code"], "BLOCK_DELETE_STATE_INVALID")
        self.assertEqual(self.operations.call("gc_plan", {})["result"]["mode"], "plan")

    def test_server_binding_ignores_inherited_workspace_trash_approval_and_session(self):
        hostile = {"AGENT_GUARD_WORKSPACE": "/wrong", "AGENT_GUARD_TRASH": "/wrong/trash",
                   "AGENT_GUARD_SESSION": "new-per-call", "AGENT_GUARD_CLEANUP_APPROVAL": "untrusted",
                   "AGENT_GUARD_ALLOW_REGENERABLE": "1", "GIT_DIR": "/wrong/git",
                   "GIT_CONFIG_COUNT": "1", "PYTHONPATH": "/wrong/python"}
        with mock.patch.dict(os.environ, hostile):
            operations = Operations(self.root)
        self.assertEqual(operations._env["AGENT_GUARD_WORKSPACE"], self.root)
        self.assertEqual(operations._env["AGENT_GUARD_TRASH"], str(Path(self.root, ".agent-trash")))
        for key in hostile:
            if key not in ("AGENT_GUARD_WORKSPACE", "AGENT_GUARD_TRASH"):
                self.assertNotIn(key, operations._env)
        self.assertEqual(operations.call("status", {})["result"]["workspace"], self.root)

    def test_root_and_non_directory_cannot_be_bound(self):
        with self.assertRaises(ValueError):
            Operations(Path(self.root).anchor)
        with self.assertRaises(ValueError):
            Operations(Path(self.root, "src/main.py"))

    def test_invalid_tool_arguments_never_start_cli(self):
        bad = [("safe_delete", {}), ("safe_delete", {"paths": []}),
               ("safe_delete", {"paths": ["x"], "force": True}),
               ("safe_delete", {"paths": ["x"], "session": "other"}),
               ("safe_delete", {"paths": ["x"], "approval": "fake"}),
               ("safe_delete", {"paths": ["x"], "dry_run": 1}),
               ("safe_delete", {"paths": ["x\0y"]}),
               ("restore", {"txid": "id", "force": True}),
               ("restore", {"txid": "../escape"}), ("restore", {"txid": "--force"}),
               ("status", {"reset_delete_budget": True}),
               ("status", {"tail": True}), ("status", {"tail": -1}),
               ("status", {"tail": 101}), ("gc_plan", {"execute": True}),
               ("transactions", {"workspace": "/"}), ("unknown", {}), ("status", None)]
        with mock.patch("adapters.mcp.operations.subprocess.run") as run:
            for name, args in bad:
                with self.subTest(name=name, args=args):
                    with self.assertRaises(InvalidArguments):
                        self.operations.call(name, args)
            run.assert_not_called()

    def test_cli_argv_has_no_shell_and_no_interactive_stdin(self):
        completed = subprocess.CompletedProcess([], 0, '{"mode":"plan"}', "")
        before = os.getcwd()
        with mock.patch("adapters.mcp.operations.subprocess.run", return_value=completed) as run:
            self.operations.call("gc_plan", {})
        args, kwargs = run.call_args
        self.assertIsInstance(args[0], list)
        self.assertFalse(kwargs["shell"])
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(kwargs["cwd"], self.root)
        self.assertNotIn("--execute", args[0])
        self.assertEqual(os.getcwd(), before)

    def test_partial_failure_retains_original_cli_json_and_txid(self):
        evidence = {"txid": "receipt", "moved": [{"origin": "first", "trash": "copy"}],
                    "skipped": ["second"], "exit": 2}
        completed = subprocess.CompletedProcess([], 2, json.dumps(evidence), "")
        with mock.patch("adapters.mcp.operations.subprocess.run", return_value=completed):
            result = self.delete("first", "second")
        self.assertFalse(result["ok"])
        self.assertEqual(result["result"], evidence)

    def test_timeout_is_not_retried_or_reported_as_no_change(self):
        with mock.patch("adapters.mcp.operations.subprocess.run",
                        side_effect=subprocess.TimeoutExpired(["synthetic"], 120)) as run:
            result = self.delete("valuable")
        self.assertEqual(run.call_count, 1)
        self.assertEqual(result["error"]["code"], "MCP_CLI_TIMEOUT")
        self.assertTrue(result["error"]["may_have_changed"])
        self.assertEqual(result["error"]["inspect_tools"], ["status", "transactions"])

    def test_non_json_failure_is_explicit_and_does_not_return_unprojected_output(self):
        completed = subprocess.CompletedProcess([], 1, "untrusted output", "untrusted stderr")
        with mock.patch("adapters.mcp.operations.subprocess.run", return_value=completed):
            result = self.operations.call("restore", {"txid": "id"})
        self.assertEqual(result["error"]["code"], "MCP_CLI_PROTOCOL_ERROR")
        self.assertTrue(result["error"]["may_have_changed"])
        self.assertNotIn("untrusted", json.dumps(result))

    def test_read_only_spawn_failure_has_no_mutation_claim(self):
        with mock.patch("adapters.mcp.operations.subprocess.run", side_effect=FileNotFoundError(2, "missing")):
            result = self.operations.call("status", {})
        self.assertEqual(result["error"]["code"], "MCP_CLI_UNAVAILABLE")
        self.assertFalse(result["error"]["may_have_changed"])


if __name__ == "__main__":
    unittest.main()
