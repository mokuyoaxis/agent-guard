"""Workspace diagnostics on owned, synthetic workspaces."""
from pathlib import Path
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import install_skill
from core import redaction, workspace_doctor as doctor


class WorkspaceFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="workspace-doctor-test-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.root = self.directory / "workspace"
        self.root.mkdir()

    def put(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(exist_ok=True, parents=True)
        path.write_text(text, encoding="utf-8")
        return path

    def invoke(self, *args):
        return subprocess.run(
            [sys.executable, "-B", str(install_skill.ROOT / "doctor.py"),
             "workspace", "--workspace", str(self.root), "--json", *args],
            cwd=self.root, capture_output=True, encoding="utf-8", timeout=20,
        )


class UnicodeCarrierHygieneTests(WorkspaceFixture):
    def test_native_workspace_cli_default_warn_strict_warn_and_fail(self):
        instructions = self.put("AGENTS.md", "ordinary instructions\n")
        trash = self.directory / "uncreated-trash"
        first = self.invoke("--trash-root", str(trash))
        self.assertEqual(first.returncode, 0, first.stderr)
        report = json.loads(first.stdout)
        self.assertEqual((report["schema"], report["status"]), ("workspace_doctor/v1", "PASS"))
        self.assertEqual(report["checks"]["delete_policy"]["status"], "DEFAULT")
        self.assertEqual(report["checks"]["exfil_allow"]["status"], "NOT_CONFIGURED")
        self.assertEqual((report["host_interception"], report["hook_enforcement"]), ("UNVERIFIED", "UNVERIFIED"))

        self.put("AGENTS.md", "line one\nformat\u200B here\n")
        warning = self.invoke()
        strict = self.invoke("--strict")
        for completed, expected_code in ((warning, 0), (strict, 1)):
            self.assertEqual(completed.returncode, expected_code, completed.stderr)
            report = json.loads(completed.stdout)
            self.assertEqual(report["status"], "WARN")
            finding, = report["checks"]["carrier_hygiene"]["findings"]
            self.assertEqual(finding, {"path": "AGENTS.md", "line": 2, "column": 7, "count": 1,
                                       "severity": "WARN", "code": "UNICODE_INVISIBLE_FORMAT"})
            self.assertNotIn("format", completed.stdout)

        self.put("AGENTS.md", "line one\nformat\u202E here\n")
        failure = self.invoke()
        self.assertEqual(failure.returncode, 1, failure.stderr)
        self.assertEqual(json.loads(failure.stdout)["status"], "FAIL")
        self.assertEqual(list(self.root.iterdir()), [instructions])
        self.assertFalse(trash.exists())

    def test_registered_emoji_variants_and_join_controls(self):
        benign = (
            "\uFEFF"
            "\U0001F3F4\U000E0067\U000E0062\U000E0065\U000E006E\U000E0067\U000E007F\n"
            "\U0001F469\u200D\U0001F4BB \U0001F9D1\U0001F3FD\u200D\U0001F680 "
            "\U0001F3F3\uFE0F\u200D\U0001F308 \u2764\uFE0F\u200D\U0001F525\n"
            "\u9089\U000E0100 \u0645\u06CC\u200C\u062A\u0648\u0627\u0646\u062F "
            "\u0915\u094D\u200D\u0937\n"
        )
        self.put(".claude/rules/typesetting.md", benign)
        check = doctor.diagnose(self.root)["checks"]["carrier_hygiene"]
        self.assertEqual((check["status"], check["files_scanned"], check["findings"]), ("PASS", 1, []))

    def test_literal_markers_are_bounded_and_value_free(self):
        marker = "fixture-private-text"
        self.put("AGENTS.md", marker + "\U000E0061\U000E007F\n"
                 "\u2067ordinary direction\u2069\n\u2066unclosed\n"
                 "x\uFE00 y\uFE00\uFE01 z\u200B\u2060")
        with mock.patch.object(doctor, "MAX_FINDINGS", 2):
            check = doctor.diagnose(self.root)["checks"]["carrier_hygiene"]
        self.assertEqual(check["status"], "FAIL")
        self.assertTrue(check["complete"])
        self.assertEqual(len(check["findings"]), 2)
        self.assertGreater(check["findings_omitted"], 0)
        self.assertEqual(check["finding_counts"], {
            "UNICODE_TAG_SMUGGLING": 2, "UNICODE_BIDI_ISOLATE": 2,
            "UNICODE_BIDI_ISOLATE_UNPAIRED": 1, "UNICODE_VARIATION_UNVERIFIED": 1,
            "UNICODE_VS_SMUGGLING": 2, "UNICODE_INVISIBLE_RUN": 2,
        })
        output = json.dumps(check)
        for forbidden in (marker, "ordinary direction", "codepoints", r"\udb40"):
            self.assertNotIn(forbidden, output)

    def test_non_cjk_supplemental_variation_selector_fails_while_cjk_ivs_warns(self):
        self.put("AGENTS.md", "H\U000E0100e\U000E0101l\U000E0102l\U000E0103o\n\U000E0100sol\n")
        smuggled = doctor.diagnose(self.root)["checks"]["carrier_hygiene"]
        self.assertEqual(smuggled["status"], "FAIL")
        self.assertEqual(smuggled["finding_counts"], {"UNICODE_VS_SMUGGLING": 5})

        self.put("AGENTS.md", "\u4E00\U000E01EF\n")
        unregistered_ivs = doctor.diagnose(self.root)["checks"]["carrier_hygiene"]
        self.assertEqual(unregistered_ivs["status"], "WARN")
        self.assertEqual(unregistered_ivs["finding_counts"], {"UNICODE_VARIATION_UNVERIFIED": 1})

    def test_whitelist_excludes_session_files_and_suppresses_unsafe_labels(self):
        self.put("src/ordinary.py", "\u202E")
        self.put(".codex/sessions/ignored.json", "\u202E")
        self.put(".agents/skills/example/log.md", "\u202E")
        self.put(".agents/skills/example/SKILL.md", "plain")
        self.put(".github/copilot-instructions.md", "plain")
        self.put(".claude/rules/note\u200B.md", "\u202E")
        check = doctor.diagnose(self.root)["checks"]["carrier_hygiene"]
        self.assertEqual((check["status"], check["files_scanned"]), ("FAIL", 3))
        finding, = check["findings"]
        self.assertIsNone(finding["path"])
        self.assertNotIn("note", json.dumps(check))

    def test_file_byte_and_walk_budgets_show_incomplete_coverage(self):
        self.put("AGENTS.md", "long text")
        self.put(".cursor/rules/one.mdc", "plain")
        self.put(".cursor/rules/two.mdc", "plain")
        with mock.patch.object(doctor, "MAX_CARRIER_TOTAL_BYTES", 1):
            limited = doctor.diagnose(self.root)["checks"]["carrier_hygiene"]
        self.assertEqual((limited["status"], limited["complete"]), ("WARN", False))
        self.assertIn("CARRIER_SCAN_TRUNCATED", limited["diagnostics"])
        with mock.patch.object(doctor, "MAX_CARRIER_ENTRIES", 1):
            limited = doctor.diagnose(self.root)["checks"]["carrier_hygiene"]
        self.assertEqual(limited["entries_seen"], 1)
        self.assertFalse(limited["complete"])


class WorkspaceDoctorTests(WorkspaceFixture):
    def test_config_source_fallback_and_distinct_input_errors(self):
        self.put(doctor.DELETE_POLICY, '{"protected_paths":["."]}')
        preferred = self.put(redaction.DEFAULT_ALLOWFILE, "")
        preferred.write_bytes(b"\xff")
        self.put(redaction.DEFAULT_IGNOREFILE, "build/*.tmp\n")
        report = doctor.diagnose(self.root)
        self.assertEqual(report["checks"]["delete_policy"]["code"], "DELETE_POLICY_ROOT_SELF_LOCK")
        allow = report["checks"]["exfil_allow"]
        self.assertEqual((allow["status"], allow["source"]), ("FAIL", redaction.DEFAULT_IGNOREFILE))
        self.assertEqual(allow["diagnostics"][0]["code"], "EXEMPTION_ENCODING_INVALID")
        self.assertEqual(self.invoke("--trash-root", "relative").returncode, 2)
        self.assertEqual(self.invoke("--trash-root", str(self.directory)).returncode, 2)
        self.assertEqual(self.invoke("--workspace", str(self.directory / "missing")).returncode, 2)
        self.put(doctor.DELETE_POLICY, '{"unknown":true}')
        self.assertEqual(doctor.diagnose(self.root)["checks"]["delete_policy"]["status"], "FAIL")

    def test_refuses_fifo_and_external_directory_alias(self):
        external = self.directory / "external"
        external.mkdir()
        (external / "delete-policy.json").write_text("{}", encoding="utf-8")
        (self.root / ".agent-guard").symlink_to(external, target_is_directory=True)
        os.mkfifo(self.root / "AGENTS.md")
        completed = self.invoke()
        self.assertEqual(completed.returncode, 1, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual(report["checks"]["delete_policy"]["status"], "FAIL")
        hygiene = report["checks"]["carrier_hygiene"]
        self.assertFalse(hygiene["complete"])
        self.assertEqual(hygiene["files_scanned"], 0)

    def test_skill_links_validate_known_installation_and_scope(self):
        directory = self.directory / "chosen-skills"
        target = directory / "exfil-guard"
        plan = install_skill.installation_plan(str(target), skill="exfil-guard")
        install_skill.execute_plan(plan)
        (directory / "delete-guard").symlink_to(self.directory / "absent", target_is_directory=True)
        unrelated = self.directory / "foreign" / "recovery-audit"
        unrelated.mkdir(parents=True)
        (unrelated / "SKILL.md").write_text("\u202E", encoding="utf-8")
        (directory / "recovery-audit").symlink_to(unrelated, target_is_directory=True)
        absent = doctor.diagnose(self.root)["checks"]["skills"]
        self.assertEqual(absent["status"], "NOT_CONFIGURED")
        selected = doctor.diagnose(self.root, skill_dirs=[directory])
        entries = {row["skill"]: row for row in selected["checks"]["skills"]["entries"]}
        self.assertEqual(entries["exfil-guard"]["status"], "PASS")
        self.assertEqual(entries["delete-guard"]["code"], "SKILL_BROKEN_LINK")
        self.assertEqual(entries["recovery-audit"]["code"], "SKILL_OTHER_INSTALLATION")
        self.assertEqual(selected["checks"]["carrier_hygiene"]["files_scanned"], 1)
        self.assertEqual(selected["checks"]["carrier_hygiene"]["findings"], [])

    def test_git_wrapper_requires_live_static_entry_and_checks_scope(self):
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        subprocess.run(["git", "init", "--quiet", "--template="], cwd=self.root, env=env, check=True)
        subprocess.run(["git", "config", "--local", "core.hooksPath", ".hooks"],
                       cwd=self.root, env=env, check=True)
        adapter = install_skill.ROOT / "adapters/git/hook.py"
        self.put(doctor.PRE_COMMIT_CONFIG, f"entry: python3 {adapter} pre-commit\n")

        def check():
            return doctor.diagnose(self.root)["checks"]["git_hooks"]

        self.assertEqual(check()["code"], "GIT_HOOK_DECLARATION_UNVERIFIED")
        staged = self.put(".hooks/pre-commit",
                          f"#!/bin/sh\nexec python3 {shlex.quote(str(adapter))} pre-commit\n")
        staged.chmod(0o700)
        self.assertEqual(check()["code"], "GIT_HOOK_PARTIAL")
        commit = self.put(".hooks/commit-msg",
                          f'#!/bin/sh\nexec python3 {shlex.quote(str(adapter))} commit-msg -- "$1"\n')
        commit.chmod(0o700)
        with mock.patch.dict(os.environ, {"GIT_DIR": str(self.directory / "unrelated-git")}):
            self.assertEqual(check()["status"], "PASS")
        staged.write_text(
            f"#!/bin/sh\nexec python3 {shlex.quote(str(adapter))} pre-commit --strict-paths --audit\n",
            encoding="utf-8",
        )
        commit.write_text(
            f'#!/bin/sh\nexec python3 {shlex.quote(str(adapter))} commit-msg --audit -- "$1"\n',
            encoding="utf-8",
        )
        self.assertEqual(check()["status"], "PASS")
        staged.write_text(
            f"#!/bin/sh\nexec python3 {shlex.quote(str(adapter))} pre-commit --unknown\n",
            encoding="utf-8",
        )
        self.assertEqual(check()["hooks"][0]["code"], "GIT_HOOK_UNVERIFIED")
        staged.write_text(
            f"#!/bin/sh\nexec python3 {shlex.quote(str(adapter))} pre-commit --strict-paths\n",
            encoding="utf-8",
        )
        commit.chmod(0o600)
        failed = check()
        self.assertEqual(failed["status"], "FAIL")
        self.assertEqual(failed["hooks"][1]["code"], "GIT_HOOK_NOT_EXECUTABLE")
        nested = self.root / "src"
        nested.mkdir()
        self.assertEqual(doctor.diagnose(nested)["checks"]["git_hooks"]["code"], "GIT_WORKSPACE_MISMATCH")
