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
from core import policy, redaction
from core.recovery import RecoveryEngine
from tests.helpers import RepoFixture, git_available
from tests.test_exfil_audit import SECRET


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

    def test_exfil_validation_refuses_overrides_without_echoing(self):
        for name, args in (
                ("config_safe_view", {"path": "parent/../config.toml"}),
                ("config_safe_view", {"path": "config.toml "}),
                ("config_safe_view", {"path": "config.toml", "format": SECRET}),
                ("safe_read", {"path": "parent/../notes.txt"}),
                ("safe_read", {"path": "notes.txt", "start_line": 0}),
                ("safe_read", {"path": "notes.txt", "start_line": True}),
                ("safe_read", {"path": "notes.txt", "start_line": 5, "end_line": 4}),
                ("safe_read", {"path": "notes.txt", "start_line": 1, "end_line": 201}),
                ("safe_read", {"path": "notes.txt", "strict_paths": "yes"}),
                ("safe_read", {"path": "notes.txt", "fail_on_redaction": 1}),
                ("safe_read", {"path": "notes.txt", "audit": True}),
                ("exfil_check", {"text": SECRET, "channel": SECRET}),
                ("exfil_check", {"text": SECRET, "channel": "issue-body", "path": "/config.toml"}),
                ("exfil_sanitize", {"text": SECRET, "channel": "issue-body", "plan": SECRET}),
                ("exfil_sanitize", {"text": SECRET, "channel": "issue-body", "path": SECRET}),
                ("exfil_sanitize", {"text": SECRET}),
                ("exfil_check", {"text": SECRET, "channel": "file-write", "audit": True})):
            with self.subTest(name=name), self.assertRaises(InvalidArguments) as caught:
                self.operations.call(name, args)
            self.assertNotIn(SECRET, str(caught.exception))
        self.assertFalse(Path(self.root, ".agent-trash").exists())

    def test_exfil_uses_stdin_ignores_ambient_audit_and_does_not_wait_on_cleanup_lock(self):
        with mock.patch.dict(os.environ, {"AGENT_GUARD_EXFIL_AUDIT": "true",
                                          "AGENT_GUARD_EXFIL_MAX_BYTES": "1"}):
            operations = Operations(self.root)
        completed = subprocess.CompletedProcess([], 0, json.dumps({"decision": "ALLOW", "code": "ALLOW_NOOP",
                                                    "exit": 0, "workspace": self.root}), "")
        with operations._lock, mock.patch("adapters.mcp.operations.subprocess.run", return_value=completed) as runner:
            response = operations.call("exfil_check", {"text": SECRET, "channel": "file-write", "path": "-literal"})
        self.assertTrue(response["ok"])
        args, kwargs = runner.call_args
        self.assertNotIn(SECRET, " ".join(args[0]))
        self.assertEqual(kwargs["input"], SECRET)
        self.assertNotIn("stdin", kwargs)
        self.assertIn("--path=-literal", args[0])
        self.assertNotIn("--audit", args[0])
        self.assertNotIn("AGENT_GUARD_EXFIL_AUDIT", kwargs["env"])
        self.assertNotIn("AGENT_GUARD_EXFIL_MAX_BYTES", kwargs["env"])

    def test_exfil_over_cap_in_utf8_blocks_and_audits_in_selected_bucket(self):
        with tempfile.TemporaryDirectory() as bucket:
            operations = Operations(self.root, trash_root=bucket, audit_exfil=True)
            text = "界" * (redaction.DEFAULT_MAX_SCAN_BYTES // 3 + 1) + SECRET
            for name in ("exfil_check", "exfil_sanitize"):
                reply = operations.call(name, {"text": text, "channel": "file-write"})
                self.assertFalse(reply["ok"])
                self.assertEqual(reply["result"]["code"], "BLOCK_OUTPUT_UNSCANNABLE")
                self.assertEqual(reply["result"]["audit"]["records_written"], 1)
                self.assertNotIn(SECRET, json.dumps(reply))
            self.assertEqual([json.loads(line)["event"] for line in Path(bucket, "audit.jsonl").read_text().splitlines()],
                             ["exfil-block", "exfil-block"])
        self.assertFalse(Path(self.root, ".agent-trash").exists())

    def test_config_view_does_not_trust_raw_child_diagnostics(self):
        for body in ({"status": "error", "code": "UNSAFE_FILE", "message": SECRET},
                     {"status": "error", "code": SECRET, "message": "config cannot be opened safely"}):
            child = subprocess.CompletedProcess([], 2, SECRET, json.dumps(body))
            with mock.patch("adapters.mcp.operations.subprocess.run", return_value=child):
                result = self.operations.call("config_safe_view", {"path": "config.json"})
            self.assertEqual(result["error"]["code"], "MCP_CLI_PROTOCOL_ERROR")
            self.assertNotIn(SECRET, json.dumps(result))

    def test_safe_read_does_not_trust_raw_child_diagnostics_or_workspace_echo(self):
        for code, body in (
            (2, {"schema": "safe_read/v1", "status": "refused", "decision": "BLOCK",
                 "code": "BLOCK_OUTPUT_UNSCANNABLE", "error_code": "CONTROL_PATH",
                 "message": SECRET, "text": "", "spans": [], "exit": 2}),
            (0, {"schema": "safe_read/v1", "status": "ok", "decision": "ALLOW",
                 "code": "ALLOW_NOOP", "workspace": self.root, "text": "ok",
                 "spans": [], "page_redacted": False, "page_redaction_count": 0,
                 "page_redactions_by_rule": {}, "exit": 0}),
        ):
            child = subprocess.CompletedProcess([], code, json.dumps(body), "")
            with mock.patch("adapters.mcp.operations.subprocess.run", return_value=child):
                result = self.operations.call("safe_read", {"path": "notes.txt"})
            self.assertEqual(result["error"]["code"], "MCP_CLI_PROTOCOL_ERROR")
            self.assertNotIn(SECRET, json.dumps(result))

    def test_quarantine_artifact_roundtrip_and_default_still_deletes(self):
        self.write("node_modules/cache", "recoverable generated bytes")
        default = self.delete("node_modules", dry_run=True)
        self.assertEqual(default["result"]["verdict"]["code"], "ALLOW_REGENERABLE")
        self.assertIn("would be deleted directly", default["result"]["verdict"]["explanation"])
        self.assertIn("nothing has been deleted", default["result"]["verdict"]["explanation"])
        self.assertTrue(Path(self.root, "node_modules/cache").is_file())
        preview = self.delete("node_modules", dry_run=True, quarantine=True)
        self.assertTrue(preview["ok"], preview)
        self.assertEqual(preview["result"]["verdict"]["decision"], "RELOCATE")
        self.assertIn("would be relocated", preview["result"]["verdict"]["explanation"])
        self.assertIn("--quarantine", preview["result"]["admission"]["next_step"]["commands"][0]["argv"])
        deleted = self.delete("node_modules", quarantine=True)
        self.assertTrue(deleted["ok"], deleted)
        self.assertTrue(deleted["result"]["quarantine"])
        self.assertTrue(self.operations.call("restore", {"txid": deleted["result"]["txid"]})["ok"])
        self.assertEqual(Path(self.root, "node_modules/cache").read_text(), "recoverable generated bytes")
        ordinary = self.delete("node_modules", quarantine=False)
        self.assertTrue(ordinary["ok"], ordinary)
        self.assertEqual(ordinary["result"]["verdict"]["code"], "ALLOW_REGENERABLE")
        self.assertNotIn("txid", ordinary["result"])

    def test_quarantine_plan_is_saved_and_does_not_authorize_unknown_content(self):
        self.write("node_modules/cache", "generated")
        self.write("unknown/data", "preserve")
        saved = self.operations.call("cleanup_plan", {"targets": ["node_modules", "unknown"],
                                                      "quarantine": True, "save": True, "view": "full"})
        self.assertTrue(saved["ok"], saved)
        record = saved["result"]
        self.assertTrue(record["plan"]["options"]["quarantine"])
        self.assertEqual(record["selection"], ["node_modules"])
        query = self.operations.call("cleanup_plan", {"saved_plan_id": record["saved_plan_id"]})
        self.assertTrue(query["result"]["overview"]["quarantine_requested"])
        self.assertEqual(query["result"]["overview"]["selected_effects"], {"quarantine": 1, "delete": 0})
        self.assertEqual(Path(self.root, "unknown/data").read_text(), "preserve")

    def test_quarantine_boolean_and_execution_override_validation(self):
        for bad in (0, 1, "true", None):
            for name, args in (("safe_delete", {"paths": ["node_modules"]}),
                               ("cleanup_plan", {"targets": ["node_modules"]})):
                with self.subTest(name=name, value=bad), self.assertRaises(InvalidArguments):
                    self.operations.call(name, {**args, "quarantine": bad})
        for name, args in (("cleanup_plan", {"saved_plan_id": "a"*64}),
                           ("cleanup_execute", {"task_id": "a"*32}),
                           ("cleanup_measure", {"scopes": ["node_modules"]})):
            with self.assertRaises(InvalidArguments):
                self.operations.call(name, {**args, "quarantine": True})
        self.assertFalse(Path(self.root, ".agent-trash").exists())

    def test_quarantine_obeys_batch_budget_and_protected_paths(self):
        target = Path(self.write("node_modules/cache", "preserve"))
        for config, code in (({"batch_max_entries": 1}, "BLOCK_DELETE_BATCH_CONFIRM"),
                             ({"session_max_entries": 1}, "BLOCK_DELETE_LIMIT"),
                             ({"quarantine_max_bytes": 1}, "BLOCK_DELETE_LIMIT"),
                             ({"protected_paths": ["node_modules"]}, "BLOCK_DELETE_PROTECTED")):
            with self.subTest(config=config):
                self.configure(**config)
                if code == "BLOCK_DELETE_BATCH_CONFIRM":
                    preview = self.delete("node_modules", quarantine=True, dry_run=True)
                    self.assertTrue(preview["ok"], preview)
                    command = preview["result"]["admission"]["next_step"]["commands"][0]["argv"]
                    self.assertIn("--quarantine", command)
                    self.assertIn("--save-plan", command)
                blocked = self.delete("node_modules", quarantine=True)
                self.assertEqual(blocked["result"]["verdict"]["code"], code, blocked)
                self.assertEqual(target.read_text(), "preserve")

    def test_quarantine_keeps_restricted_directory_refusal(self):
        self.write("node_modules/cache", "retain")
        policy.request_mode(str(Path(self.root, ".agent-trash")), policy.MODE_RESTRICTED, actor="unit-fixture")
        denied = self.delete("node_modules", quarantine=True)
        self.assertEqual(denied["result"]["verdict"]["code"], "BLOCK_RESTRICTED_MODE")
        moved = self.delete("node_modules/cache", quarantine=True)
        self.assertTrue(moved["ok"], moved)
        self.assertEqual(moved["result"]["verdict"]["code"], "RELOCATE_NARROW")
        self.assertTrue(self.operations.call("restore", {"txid": moved["result"]["txid"]})["ok"])
        self.assertEqual(Path(self.root, "node_modules/cache").read_text(), "retain")

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

    def test_failed_legacy_copy_is_flagged_and_refused_in_restore(self):
        engine = RecoveryEngine(self.root)
        txid = "legacy-copy"
        origin = Path(self.root, "valuable")
        residual = Path(self.write(f".agent-trash/{txid}/valuable", "partial bytes"))
        paths = {"txid": txid, "origin_path": str(origin), "trash_path": str(residual)}
        engine._manifest_append([
            {"type": "tx-start", "txid": txid},
            {"type": "relocate-intent", **paths},
            {"type": "relocate-failed", **paths, "reason": "storage unavailable"},
        ])

        listing = self.operations.call("transactions", {})
        self.assertTrue(listing["ok"], listing)
        rows = listing["result"]["transactions"]
        overview = self.operations.call("status", {"view": "overview"})
        self.assertTrue(overview["ok"], overview)
        for row in (rows[0], overview["result"]["transactions"]["items"][0]):
            self.assertEqual(row["txid"], txid)
            self.assertEqual(row["state"], "FAILED")
            self.assertEqual(row["restorable_items"], 0)
            self.assertEqual(row["manual_review_items"], 1)
        full_status = self.operations.call("status", {})
        self.assertEqual(full_status["result"]["usage"]["manual_review_items"], 1)

        restored = self.operations.call("restore", {"txid": txid})
        self.assertFalse(restored["ok"], restored)
        self.assertEqual(restored["exit_code"], 1)
        self.assertIn("manual review", restored["result"]["errors"][0])
        self.assertEqual(restored["result"]["restored"], [])
        self.assertFalse(origin.exists())
        self.assertEqual(residual.read_text(), "partial bytes")

    def test_mixed_transaction_restores_confirmed_copy_and_keeps_residual_for_review(self):
        self.write("confirmed", "complete bytes")
        deletion = self.delete("confirmed")
        self.assertTrue(deletion["ok"], deletion)
        txid = deletion["result"]["txid"]
        origin = Path(self.root, "unconfirmed")
        residual = Path(self.write(f".agent-trash/{txid}/unconfirmed", "partial bytes"))
        paths = {"txid": txid, "origin_path": str(origin), "trash_path": str(residual)}
        RecoveryEngine(self.root)._manifest_append([
            {"type": "relocate-intent", **paths},
            {"type": "relocate-failed", **paths, "reason": "storage unavailable"},
        ])

        restored = self.operations.call("restore", {"txid": txid})
        self.assertFalse(restored["ok"], restored)
        self.assertEqual(restored["result"]["execution_summary"]["status"], "PARTIAL")
        self.assertIn("manual review", restored["result"]["errors"][0])
        self.assertEqual(Path(self.root, "confirmed").read_text(), "complete bytes")
        self.assertFalse(origin.exists())
        self.assertEqual(residual.read_text(), "partial bytes")

    def test_cleanup_observations_are_read_only_and_keep_workspace(self):
        self.write('.gitignore', 'build/\n.work/\n')
        self.write('build/out', 'generated')
        self.write('.work/cache/uv/CACHEDIR.TAG', 'Signature: 8a477f597d28d172789f06886806bc55')
        self.write('.work/cache/uv/.git', '')
        before = self.files()
        discovery = self.operations.call('cleanup_discover', {})
        self.assertTrue(discovery['ok'], discovery)
        self.assertEqual(discovery['result']['workspace'], self.root)
        measured = self.operations.call('cleanup_measure', {'scopes': ['build', '.work/cache/uv']})
        self.assertTrue(measured['ok'], measured)
        self.assertTrue(measured['partial'])
        self.assertEqual(measured['exit_code'], 0)
        self.assertFalse(measured['result']['execution_supported'])
        self.assertTrue(all(not row['selectable'] for row in measured['result']['scopes']))
        self.assertEqual(before, self.files())

    def test_partial_cleanup_observation_is_successful_data_without_retry(self):
        self.write('area/one', 'one')
        self.write('area/two', 'two')
        for name, args in [('cleanup_discover', {'scopes': ['area'], 'max_entries': 1}),
                           ('cleanup_measure', {'scopes': ['area'], 'max_entries': 1})]:
            with self.subTest(name=name):
                result = self.operations.call(name, args)
                self.assertTrue(result['ok'], result)
                self.assertTrue(result['partial'])
                self.assertEqual(result['exit_code'], 1)
                self.assertFalse(result['result']['execution_supported'])
        self.assertFalse(Path(self.root, '.agent-trash').exists())

    def test_cleanup_literal_option_scope_and_scope_refusals(self):
        self.write('--execute/file', 'literal')
        result = self.operations.call('cleanup_measure', {'scopes': ['--execute']})
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['result']['scopes'][0]['path'], '--execute')
        self.write('area/file', 'keep')
        Path(self.root, 'alias').symlink_to('area', target_is_directory=True)
        for scopes in [['.'], ['../outside'], ['.git'], ['alias'], ['area', 'area']]:
            with self.subTest(scopes=scopes):
                result = self.operations.call('cleanup_discover', {'scopes': scopes})
                self.assertFalse(result['ok'], result)
                self.assertEqual(result['exit_code'], 2)
        self.assertEqual(Path(self.root, '--execute/file').read_text(), 'literal')

    def test_cleanup_invalid_arguments_never_start_cli(self):
        cases = [('cleanup_measure', {}), ('cleanup_measure', {'scopes': []}),
                 ('cleanup_discover', {'scopes': '.'}), ('cleanup_discover', {'scopes': ['x\0y']}),
                 ('cleanup_discover', {'scopes': ['x'] * 65}), ('cleanup_measure', {'scopes': ['x'], 'execute': True}),
                 ('cleanup_discover', {'workspace': '/'}), ('cleanup_discover', {'max_depth': True}),
                 ('cleanup_discover', {'max_depth': 5}), ('cleanup_discover', {'max_entries': 10001}),
                 ('cleanup_measure', {'scopes': ['x'], 'max_entries': 20001}),
                 ('cleanup_measure', {'scopes': ['x'], 'max_depth': 33})]
        with mock.patch('adapters.mcp.operations.subprocess.run') as run:
            for name, args in cases:
                with self.subTest(name=name, args=args), self.assertRaises(InvalidArguments):
                    self.operations.call(name, args)
            run.assert_not_called()

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

    def test_restore_preview_validates_compares_conflicts_is_read_only_and_lock_free(self):
        for bad in ({}, {"txid": "id", "force": True}, {"txid": "../escape"}, {"txid": "--force"}):
            with self.subTest(bad=bad), self.assertRaises(InvalidArguments):
                self.operations.call("restore_preview", bad)

        self.write("same.txt", "synthetic-same-payload")
        self.write("diff.txt", "before")
        deleted = self.delete("same.txt", "diff.txt")
        self.assertTrue(deleted["ok"], deleted)
        txid = deleted["result"]["txid"]
        self.write("same.txt", "synthetic-same-payload")
        self.write("diff.txt", "after-different")

        before = self.files()
        with self.operations._lock:
            preview = self.operations.call("restore_preview", {"txid": txid})
        self.assertTrue(preview["ok"], preview)
        self.assertEqual(preview["exit_code"], 0)
        body = preview["result"]
        self.assertEqual(body["schema"], "restore_preview/v1")
        self.assertFalse(body["can_restore"])
        self.assertTrue(body["can_restore_with_force"])
        self.assertEqual(body["summary"]["conflict_count"], 2)
        self.assertEqual(body["summary"]["identical_conflict_count"], 1)
        self.assertNotIn("synthetic-same-payload", json.dumps(preview))
        self.assertEqual(self.files(), before)

        unknown = self.operations.call("restore_preview", {"txid": "unknown-tx-id"})
        self.assertFalse(unknown["ok"])
        self.assertEqual(unknown["exit_code"], 1)
        self.assertEqual(self.files(), before)

        # MCP restore_preview and restore MUST require exact full txid (never accept short prefix).
        short = txid[:12]
        self.assertNotEqual(short, txid)
        prefix_preview = self.operations.call("restore_preview", {"txid": short})
        self.assertFalse(prefix_preview["ok"], prefix_preview)
        self.assertEqual(prefix_preview["exit_code"], 1)
        self.assertIn("unknown transaction", prefix_preview["result"]["error"])
        self.assertEqual(self.files(), before)

        # Verify write-side MCP restore also rejects a unique short prefix without restoring.
        self.write("exact_only.txt", "keep in quarantine")
        del_exact = self.delete("exact_only.txt")
        self.assertTrue(del_exact["ok"], del_exact)
        exact_txid = del_exact["result"]["txid"]
        prefix_restore = self.operations.call("restore", {"txid": exact_txid[:12]})
        self.assertFalse(prefix_restore["ok"], prefix_restore)
        self.assertEqual(prefix_restore["exit_code"], 1)
        self.assertFalse(Path(self.root, "exact_only.txt").exists())
        full_restore = self.operations.call("restore", {"txid": exact_txid})
        self.assertTrue(full_restore["ok"], full_restore)
        self.assertEqual(Path(self.root, "exact_only.txt").read_text(), "keep in quarantine")

    def test_dry_run_keeps_targets_and_does_not_spend_budget(self):
        self.write("valuable", "original")
        result = self.delete("valuable", dry_run=True)
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["result"]["dry_run"])
        self.assertIn("nothing has been moved", result["result"]["verdict"]["explanation"])
        self.assertNotIn("txid", result["result"])
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
               ("status", {"tail": 101}),
               ("status", {"measure_storage": True}),
               ("status", {"measure_storage": False}),
               ("status", {"view": "full", "measure_storage": True}),
               ("status", {"view": "full", "measure_storage": False}),
               ("status", {"view": "overview", "measure_storage": "yes"}),
               ("status", {"view": "overview", "measure_storage": 1}),
               ("status", {"view": "overview", "max_measure_entries": 10}),
               ("status", {"view": "overview", "measure_storage": True, "max_measure_entries": True}),
               ("status", {"view": "overview", "measure_storage": True, "max_measure_entries": 0}),
               ("status", {"view": "overview", "measure_storage": True, "max_measure_entries": 200001}),
               ("gc_plan", {"execute": True}),
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
