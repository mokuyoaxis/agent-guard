"""Regression suite for the 2026-09-20 incident — P1 (F1 masks hard boundaries).

Public context: `docs/development-note-unguarded-deletion.md`. The detailed
incident record and its evidence remain outside the published source tree.

The defect: the F1 shape rule returned *before* the hard boundaries were
evaluated, so `cd /tmp && rm -rf "$PWD/../home"` classified as
`ASK / COMPOUND_CWD_DELETE` instead of a hard `BLOCK`. An ASK is granted
silently by any auto-approving host, which turns a guaranteed refusal into
an execution — the incident's own host did exactly that.

These cases exist so the behaviour cannot be re-described away in prose:
`skills/delete-guard/references/policy.md` once documented the degraded
verdict as "single-execution authorization", and the fix sat unimplemented
for weeks while the documentation asserted it was intended.
"""
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.helpers import git_available

from core import classifier
from core.classifier import classify_command
from core.policy import (
    CODE_ASK_COMPOUND_CWD_DELETE, CODE_BLOCK_OUT_OF_WORKSPACE,
    CODE_BLOCK_FORCE_PUSH,
    CODE_BLOCK_PROTECTED_ANCESTOR, CODE_BLOCK_PROTECTED_PATH,
    CODE_BLOCK_UNDETERMINABLE_EFFECT, DECISION_ASK, DECISION_BLOCK,
    MODE_NORMAL, PolicyContext, decide_ops, worst,
)


@unittest.skipUnless(git_available(), "git required")
class IncidentP1F1HardBoundaries(unittest.TestCase):
    """A shape rule describes compensation difficulty, never effect scope."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="agent-guard-incident-")
        # The incident's layout: the workspace sits *under* the cwd, so a
        # `projects/..` target escapes it.
        self.ws = os.path.join(self.root, "projects", "agent-guard")
        for rel in ("build", "sub"):
            os.makedirs(os.path.join(self.ws, rel), exist_ok=True)
        for rel, text in (("build/o.js", "x"), ("sub/keep.txt", "k")):
            with open(os.path.join(self.ws, rel), "w") as fh:
                fh.write(text)
        subprocess.run(["git", "init", "-q", "."], cwd=self.ws, check=True)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def verdict(self, cmd, base_dir=None):
        ws = classifier.discover_workspace(self.ws)
        ctx = PolicyContext(
            workspace=ws, trash_root=os.path.join(ws, ".agent-trash"),
            base_dir=base_dir or self.ws, mode=MODE_NORMAL)
        specs, _ = classify_command(cmd)
        verdicts = decide_ops(specs, ctx)
        return worst(verdicts) if verdicts else None

    # --- the cases §6.5 specified -------------------------------------

    def test_resolved_out_of_workspace_target_is_blocked(self):
        # A plain out-of-workspace target keeps the boundary code; the
        # filesystem-root cases below carry their own, stronger code.
        v = self.verdict("cd /tmp && rm -rf /srv/data")
        self.assertEqual((v.decision, v.code),
                         (DECISION_BLOCK, CODE_BLOCK_OUT_OF_WORKSPACE))

    def test_variable_target_after_cd_is_blocked(self):
        v = self.verdict("cd /tmp && rm -rf $HOME")
        self.assertEqual((v.decision, v.code),
                         (DECISION_BLOCK, CODE_BLOCK_UNDETERMINABLE_EFFECT))

    def test_parent_traversal_after_cd_is_blocked(self):
        """The exact shape of the incident command."""
        v = self.verdict("cd /tmp && rm -rf $PWD/../home")
        self.assertEqual((v.decision, v.code),
                         (DECISION_BLOCK, CODE_BLOCK_UNDETERMINABLE_EFFECT))

    def test_traversal_target_escaping_workspace_is_blocked(self):
        v = self.verdict("cd " + shlex.quote(self.root) + " && rm -rf projects/..",
                         base_dir=self.root)
        self.assertEqual((v.decision, v.code),
                         (DECISION_BLOCK, CODE_BLOCK_OUT_OF_WORKSPACE))

    def test_ordinary_f1_still_asks(self):
        """The legitimate single-execution ASK must survive the fix."""
        v = self.verdict("cd sub && rm -rf build")
        self.assertEqual((v.decision, v.code),
                         (DECISION_ASK, CODE_ASK_COMPOUND_CWD_DELETE))

    # --- §6.6 hardening directions ------------------------------------

    def test_workspace_root_after_cd_is_blocked(self):
        v = self.verdict("cd sub && rm -rf " + self.ws)
        self.assertEqual((v.decision, v.code),
                         (DECISION_BLOCK, CODE_BLOCK_PROTECTED_PATH))

    def test_protected_ancestor_after_cd_is_blocked(self):
        """`rm -rf /home` must block as policy, not as a config accident."""
        v = self.verdict("cd sub && rm -rf /home")
        self.assertEqual((v.decision, v.code),
                         (DECISION_BLOCK, CODE_BLOCK_PROTECTED_ANCESTOR))

    def test_no_cd_prefix_is_unchanged(self):
        """Without the shape, these were already hard BLOCKs."""
        v = self.verdict('rm -rf "$CHIMERA_HOME" "$PWD/../home"')
        self.assertEqual((v.decision, v.code),
                         (DECISION_BLOCK, CODE_BLOCK_UNDETERMINABLE_EFFECT))

    def test_cd_cannot_downgrade_force_push(self):
        v = self.verdict("cd sub && git push --force origin main")
        self.assertEqual((v.decision, v.code),
                         (DECISION_BLOCK, CODE_BLOCK_FORCE_PUSH))

    def test_cd_cannot_downgrade_opaque_git_clean(self):
        v = self.verdict("cd sub && git clean -fdx -- '*.tmp'")
        self.assertEqual((v.decision, v.code),
                         (DECISION_BLOCK, "BLOCK_WILDCARD"))

    def test_attached_separator_exposes_protected_delete(self):
        for cmd in ("echo ok;rm -rf /home", "echo ok&&rm -rf /home",
                    "echo ok\nrm -rf /home"):
            with self.subTest(cmd=cmd):
                v = self.verdict(cmd)
                self.assertEqual((v.decision, v.code),
                                 (DECISION_BLOCK, CODE_BLOCK_PROTECTED_ANCESTOR))

    def test_attached_suffix_does_not_change_delete_target(self):
        specs, _ = classify_command("rm -rf build;echo done")
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0].targets, ["build"])

    def test_windows_word_in_echo_does_not_rewrite_posix_escapes(self):
        specs, _ = classify_command("echo del && rm -rf a\\ b")
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0].targets, ["a b"])

    def test_mixed_windows_backslash_and_posix_commands_fail_closed(self):
        v = self.verdict("del build\\o.js && rm -rf a\\ b")
        self.assertEqual((v.decision, v.code),
                         (DECISION_BLOCK, CODE_BLOCK_UNDETERMINABLE_EFFECT))


@unittest.skipUnless(git_available(), "git required")
class ProtectedAncestorRoots(unittest.TestCase):
    """Filesystem roots are refused by *identity*, not by workspace geometry.

    §6.6: "Today `rm -rf /home` blocks only because it happens to fall
    outside the workspace root; that is an accident of configuration, not a
    policy." These cases pin the policy half - including the degenerate
    workspace setting where the old behaviour would have allowed it.
    """

    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="agent-guard-ancestor-")
        os.makedirs(os.path.join(self.ws, "build"), exist_ok=True)
        with open(os.path.join(self.ws, "build", "o.js"), "w") as fh:
            fh.write("x")
        subprocess.run(["git", "init", "-q", "."], cwd=self.ws, check=True)

    def tearDown(self):
        shutil.rmtree(self.ws, ignore_errors=True)

    def verdict(self, cmd, workspace=None):
        ws = workspace or classifier.discover_workspace(self.ws)
        ctx = PolicyContext(
            workspace=ws, trash_root=os.path.join(ws, ".agent-trash"),
            base_dir=self.ws, mode=MODE_NORMAL)
        specs, _ = classify_command(cmd)
        verdicts = decide_ops(specs, ctx)
        return worst(verdicts) if verdicts else None

    def test_system_roots_are_blocked(self):
        for root in ("/", "/home", "/usr", "/etc", "/var", "/tmp", "/opt",
                     "/boot", "/bin", "/root"):
            with self.subTest(root=root):
                v = self.verdict("rm -rf " + root)
                self.assertEqual((v.decision, v.code),
                                 (DECISION_BLOCK,
                                  CODE_BLOCK_PROTECTED_ANCESTOR))

    def test_home_is_blocked_even_when_workspace_is_root(self):
        """The degenerate config the old rule could not survive."""
        v = self.verdict("rm -rf /home", workspace="/")
        self.assertEqual((v.decision, v.code),
                         (DECISION_BLOCK, CODE_BLOCK_PROTECTED_ANCESTOR))

    def test_matching_is_exact_not_prefix(self):
        """Ordinary paths under a protected root keep the ordinary code."""
        for path in ("/home/alice/build", "/tmp/work", "/etc/nginx",
                     "/usr/local/share/x"):
            with self.subTest(path=path):
                v = self.verdict("rm -rf " + path)
                self.assertEqual((v.decision, v.code),
                                 (DECISION_BLOCK,
                                  CODE_BLOCK_OUT_OF_WORKSPACE))

    def test_lookalike_names_are_not_matched(self):
        v = self.verdict("rm -rf /homework")
        self.assertEqual(v.code, CODE_BLOCK_OUT_OF_WORKSPACE)

    def test_in_workspace_targets_unaffected(self):
        v = self.verdict("rm -rf build")
        self.assertEqual((v.decision, v.code), ("RELOCATE", "RELOCATE_TREE"))

    def test_workspace_root_keeps_its_own_code(self):
        v = self.verdict("rm -rf " + self.ws)
        self.assertEqual((v.decision, v.code),
                         (DECISION_BLOCK, CODE_BLOCK_PROTECTED_PATH))


if __name__ == "__main__":
    unittest.main()
