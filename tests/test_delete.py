"""End-to-end CLI tests through the skill scripts (subprocess)."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import policy
from tests.helpers import RepoFixture, git_available

SCRIPTS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "skills", "delete-guard", "scripts")


def run(script, *args, cwd):
    return subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, script), *args],
        capture_output=True, text=True, cwd=cwd, timeout=60)


@unittest.skipUnless(git_available(), "git required")
class SafeDeleteCLI(RepoFixture):
    def test_relocate_then_restore_roundtrip(self):
        self.write("report.txt", "data")
        proc = run("safe_delete.py", "--json", "report.txt", cwd=self.root)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertEqual(result["verdict"]["decision"], "RELOCATE")
        self.assertFalse(os.path.exists(os.path.join(self.root, "report.txt")))

        listing = json.loads(run("restore.py", "list", "--json",
                                 cwd=self.root).stdout)
        txid = listing["transactions"][-1]["txid"]
        back = run("restore.py", txid, cwd=self.root)   # bare-txid form
        self.assertEqual(back.returncode, 0, back.stderr)
        self.assertEqual(Path(self.root, "report.txt").read_text(),
                         "data")
        after = json.loads(run("restore.py", "list", "--json",
                               cwd=self.root).stdout)
        restored = next(t for t in after["transactions"]
                        if t["txid"] == txid)
        self.assertEqual(restored["state"], "RESTORED")
        self.assertEqual(restored["restorable_items"], 0)

    def test_blocked_outside_workspace_exit_2(self):
        outside = os.path.join(os.path.dirname(self.root), "keep-me.txt")
        self.addCleanup(
            lambda: os.path.exists(outside) and os.unlink(outside))
        with open(outside, "w") as fh:
            fh.write("do not touch")
        proc = run("safe_delete.py", "--json", outside, cwd=self.root)
        self.assertEqual(proc.returncode, 2)
        result = json.loads(proc.stdout)
        self.assertEqual(result["verdict"]["code"], "BLOCK_OUT_OF_WORKSPACE")
        self.assertTrue(os.path.exists(outside))
        status = subprocess.run(
            ["git", "-C", self.root, "status", "--porcelain"],
            capture_output=True, text=True, check=True)
        self.assertEqual(status.stdout, "")

    def test_blocked_request_with_readonly_git_metadata_does_not_pollute(self):
        outside = os.path.join(os.path.dirname(self.root), "still-safe.txt")
        self.addCleanup(
            lambda: os.path.exists(outside) and os.unlink(outside))
        Path(outside).write_text("keep")
        exclude = os.path.join(self.root, ".git", "info", "exclude")
        old_mode = os.stat(exclude).st_mode
        os.chmod(exclude, 0o444)
        try:
            proc = run("safe_delete.py", "--json", outside, cwd=self.root)
        finally:
            os.chmod(exclude, old_mode)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertIn("audit unavailable", result["warnings"][0])
        self.assertFalse(os.path.exists(os.path.join(
            self.root, ".agent-trash")))
        self.assertTrue(os.path.exists(outside))

    def test_dry_run_touches_nothing(self):
        self.write("dry.txt")
        proc = run("safe_delete.py", "--json", "--dry-run", "dry.txt",
                   cwd=self.root)
        result = json.loads(proc.stdout)
        self.assertIn("would", result["outcome"])
        self.assertTrue(os.path.exists(os.path.join(self.root, "dry.txt")))
        status = subprocess.run(
            ["git", "-C", self.root, "status", "--porcelain"],
            capture_output=True, text=True, check=True)
        self.assertEqual(status.stdout, "?? dry.txt\n")

    def test_regenerable_delete_blocks_if_audit_intent_cannot_persist(self):
        with open(os.path.join(self.root, ".gitignore"), "a") as fh:
            fh.write(".agent-trash/\n")
        trash = os.path.join(self.root, ".agent-trash")
        os.makedirs(trash)
        audit_path = os.path.join(trash, "audit.jsonl")
        Path(audit_path).write_text("")
        os.chmod(audit_path, 0o444)
        target = self.write("node_modules/pkg/index.js", "generated")
        try:
            proc = run("safe_delete.py", "--json", "node_modules",
                       cwd=self.root)
        finally:
            os.chmod(audit_path, 0o644)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertEqual(result["verdict"]["code"], "COMPENSATION_FAILED")
        self.assertTrue(os.path.exists(target))


@unittest.skipUnless(git_available(), "git required")
class CheckCLI(RepoFixture):
    def test_advisory_block_exit_0(self):
        proc = run("check.py", "--json", "--", "rm -rf /", cwd=self.root)
        self.assertEqual(proc.returncode, 0)          # advisory never fails
        out = json.loads(proc.stdout)
        self.assertEqual(out["decision"], "BLOCK")
        status = subprocess.run(
            ["git", "-C", self.root, "status", "--porcelain"],
            capture_output=True, text=True, check=True)
        self.assertEqual(status.stdout, "")

    def test_enforce_proceeds_after_relocation(self):
        self.write("tmpbuild/o.js")
        proc = run("check.py", "--enforce", "--json", "--", "rm -rf tmpbuild",
                   cwd=self.root)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertEqual(out["decision"], "ALLOW")
        self.assertFalse(os.path.exists(os.path.join(self.root, "tmpbuild")))
        self.assertGreaterEqual(out["compensations"][0]["moved"], 1)

    def test_enforce_blocked_leaves_fs_untouched(self):
        proc = run("check.py", "--enforce", "--", "rm -rf .", cwd=self.root)
        self.assertEqual(proc.returncode, 2)
        self.assertTrue(os.path.exists(os.path.join(self.root, ".gitignore")))
        status = subprocess.run(
            ["git", "-C", self.root, "status", "--porcelain"],
            capture_output=True, text=True, check=True)
        self.assertEqual(status.stdout, "")

    def test_enforce_block_with_readonly_git_metadata_stays_clean(self):
        exclude = os.path.join(self.root, ".git", "info", "exclude")
        old_mode = os.stat(exclude).st_mode
        os.chmod(exclude, 0o444)
        try:
            proc = run("check.py", "--enforce", "--json", "--",
                       "rm -rf .", cwd=self.root)
        finally:
            os.chmod(exclude, old_mode)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertEqual(result["code"], "BLOCK_PROTECTED_PATH")
        self.assertIn("audit unavailable", result["warnings"][0])
        self.assertFalse(os.path.exists(os.path.join(
            self.root, ".agent-trash")))
        status = subprocess.run(
            ["git", "-C", self.root, "status", "--porcelain"],
            capture_output=True, text=True, check=True)
        self.assertEqual(status.stdout, "")

    def test_wildcard_enforce_blocked(self):
        self.write("a.tmp")
        proc = run("check.py", "--enforce", "--", "rm *.tmp", cwd=self.root)
        self.assertEqual(proc.returncode, 2)
        self.assertTrue(os.path.exists(os.path.join(self.root, "a.tmp")))

    def test_git_clean_non_ascii_path_is_relocated_and_restorable(self):
        name = "中文笔记.txt"
        self.write(name, "valuable")
        subprocess.run(["git", "-C", self.root, "config",
                        "core.quotePath", "true"], check=True)
        proc = run("check.py", "--enforce", "--json", "--",
                   "git clean -fd", cwd=self.root)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertEqual(out["compensations"][0]["moved"], 1)
        self.assertFalse(os.path.exists(os.path.join(self.root, name)))

        subprocess.run(["git", "-C", self.root, "clean", "-fd"],
                       check=True, capture_output=True)
        txid = out["compensations"][0]["txid"]
        restored = run("restore.py", txid, "--json", cwd=self.root)
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertEqual(Path(self.root, name).read_text(), "valuable")

    def test_git_snapshot_failure_blocks_instead_of_allowing(self):
        with tempfile.TemporaryDirectory(prefix="agent-guard-nonrepo-") as root:
            proc = run("check.py", "--enforce", "--json", "--",
                       "git reset --hard", cwd=root)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertEqual(out["decision"], "BLOCK")
        self.assertEqual(out["code"], "COMPENSATION_FAILED")

    def test_git_clean_enumeration_failure_is_undeterminable_effect(self):
        """An unknowable target set is effect-uncertainty, not a broken
        compensation (policy.md row 11b). Nothing was mutated, so the
        verdict must not claim a compensation was attempted and failed."""
        with tempfile.TemporaryDirectory(prefix="agent-guard-nonrepo-") as root:
            proc = run("check.py", "--enforce", "--json", "--",
                       "git clean -fd", cwd=root)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertEqual(out["decision"], "BLOCK")
        self.assertEqual(out["code"], "BLOCK_UNDETERMINABLE_EFFECT")
        self.assertIn("enumeration failed", " ".join(out["reasons"]))

    def test_restated_verdict_never_keeps_the_proposed_explanation(self):
        """A refusal arm rewrites decision+code, so the explanation must be
        rewritten with it: leaving the policy's *proposed* explanation in
        place made a BLOCK describe the RELOCATE it never performed."""
        with tempfile.TemporaryDirectory(prefix="agent-guard-nonrepo-") as root:
            proc = run("check.py", "--enforce", "--json", "--",
                       "git clean -fd", cwd=root)
        out = json.loads(proc.stdout)
        self.assertEqual(out["explanation"],
                         policy.EXPLANATIONS[out["code"]])


@unittest.skipUnless(git_available(), "git required")
class StatusCLI(RepoFixture):
    def test_json_and_human_modes(self):
        run("safe_delete.py", "s.txt", cwd=self.root) if self.write("s.txt") else None
        human = run("status.py", cwd=self.root)
        self.assertEqual(human.returncode, 0)
        self.assertIn("mode      : NORMAL", human.stdout)
        js = json.loads(run("status.py", "--json", cwd=self.root).stdout)
        self.assertEqual(js["mode"], "NORMAL")
        self.assertIn("usage", js)


@unittest.skipUnless(git_available(), "git required")
class WindowsVerbNoDialect(RepoFixture):
    """A Windows delete verb must be guarded even with no dialect set.

    Regression: the default dialect is posix, and the adapter's fast path
    screened on the POSIX vocabulary only - so `del`, `rd`, `erase` and
    `ri` ran with no verdict and no audit record on any session that never
    configured AGENT_GUARD_DIALECT. Vocabulary is now dialect-independent;
    only the lexer follows the dialect.
    """

    def test_cmd_tree_delete_is_relocated_without_dialect(self):
        self.write("build/o.js", "x")
        proc = run("check.py", "--enforce", "--json", "--",
                   "del /s /q build", cwd=self.root)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["code"], "RELOCATE_TREE")
        self.assertFalse(os.path.exists(os.path.join(self.root, "build")))

    def test_backslash_target_is_not_swallowed_by_shlex(self):
        """`del build\o.js` must not lex as the single file `buildo.js`."""
        self.write("build/o.js", "x")
        proc = run("check.py", "--enforce", "--json", "--",
                   "del build\\o.js", cwd=self.root)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["code"], "RELOCATE_PATHS")
        self.assertFalse(os.path.exists(os.path.join(self.root, "build",
                                                     "o.js")))

    def test_powershell_prefix_fails_closed(self):
        self.write("build/o.js", "x")
        proc = run("check.py", "--enforce", "--json", "--",
                   "ri build -r -fo", cwd=self.root)
        self.assertEqual(proc.returncode, 2)
        # `ri` is recognised now, but the POSIX lexer cannot resolve `-fo`:
        # BLOCK is correct, silence is not.
        self.assertEqual(json.loads(proc.stdout)["code"],
                         "BLOCK_UNDETERMINABLE_EFFECT")
        self.assertTrue(os.path.exists(os.path.join(self.root, "build",
                                                    "o.js")))


if __name__ == "__main__":
    unittest.main()
