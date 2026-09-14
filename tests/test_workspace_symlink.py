"""F9 regression: a workspace reached through a SYMBOLIC LINK (macOS CI).

Upstream GitHub PR #4 turned the whole macOS matrix red while Linux and
Windows stayed green. The failing cases were all
`tests/test_dialect_phase2.py::CheckCliDialect`, and the reported verdicts
were BLOCK_OUT_OF_WORKSPACE for targets that plainly live inside the
workspace:

    test_advisory_mode_does_not_mutate          expected RELOCATE
    test_default_posix_behaviour_unchanged      expected RELOCATE
    test_powershell_tree_delete_relocates       expected ALLOW/RELOCATE_TREE

Root cause: macOS hands out temp directories under /var/folders/... and
/var is a symlink to /private/var (just as /tmp is a symlink to
/private/tmp). `unittest`'s TemporaryDirectory keeps the SYMLINKED spelling,
`check.py` passes that spelling as the workspace root, and
`discover_workspace()` realpath-resolved it (F8). Target paths, however,
were compared LEXICALLY: `/var/folders/.../build` shares no `commonpath`
prefix with the physical `/private/var/folders/...` root, so every target
was judged out of bounds. The failure was latent on Linux/macOS CI because
those runners put temp directories on non-symlinked paths.

These tests reproduce the platform divergence on ANY host by running the
fixture from a symlinked directory (ln -s SK-<tmp> SK-link-<...) and pin
the contract:

  - a target inside the tree is inside, no matter which spelling the caller
    used for the workspace root (both `check.py` paths: --dialect and the
    default POSIX path);
  - the quarantine still round-trips the target (RELOCATE is a promise);
  - the hard boundaries are NOT loosened by the normalization (outside
    targets and the workspace root itself stay BLOCKed).

The tests FAIL against the pre-fix classifier (targets reported
outside-workspace) and pass after it.

F9b - the OTHER half of the same defect. Normalising the classifier fixed
the VERDICT but not the LAYOUT: `RecoveryEngine.relocate()` still computed
`relpath(spec.resolved, self.workspace)` with a LEXICAL origin and a
PHYSICAL root. Under a symlinked ancestor that produced a multi-segment
`..` relpath, so the destination escaped `trash_root/<txid>/` entirely -
on macOS CI it landed in a root-owned directory under
/private/var/folders/<2char>/ and `os.makedirs` raised PermissionError,
surfacing as BLOCK_COMPENSATION_FAILED for
`test_powershell_tree_delete_relocates`. The existing fixture above did not
catch it because its symlinks sit INSIDE the temp directory: the escape
still landed somewhere under the quarantine. `SymlinkedAncestorFixture`
therefore puts the link in the PARENT chain, which is the macOS shape, and
asserts the destination is inside `trash_root/<txid>/`.

These tests FAIL against the pre-fix relocation (the recorded trash_path is
`trash/alias/...`, missing the txid level) and pass after it.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.classifier import classify_command, classify_paths, discover_workspace
from core import TRASH_DIRNAME
from core.policy import PolicyContext, decide_ops, worst
from core.recovery import RecoveryEngine

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECK = os.path.join(ROOT, "skills", "delete-guard", "scripts", "check.py")


def symlink_supported() -> bool:
    """Some Windows/CI filesystems refuse symlinks (or need privileges)."""
    base = tempfile.mkdtemp(prefix="agent-guard-symprobe-")
    try:
        os.symlink(base, os.path.join(base, "link"))
        return True
    except (OSError, NotImplementedError):
        return False
    finally:
        shutil.rmtree(base, ignore_errors=True)


@unittest.skipUnless(symlink_supported(), "symlinks unavailable on this FS")
class SymlinkedWorkspaceFixture(unittest.TestCase):
    """A temp workspace whose parent is a symlink, i.e. the macOS layout."""

    def setUp(self):
        # The UNRESOLVED spelling is the point: on macOS this is
        # /var/folders/... while the physical tree is /private/var/folders/...
        self._tmp = tempfile.TemporaryDirectory(prefix="agent-guard-sym-")
        self.spelled = self._tmp.name
        self.real = os.path.realpath(self.spelled)
        # /tmp/SK-<rand>-link -> /tmp/SK-<rand>. The caller (fixture, shell,
        # harness) works from the link spelling; the kernel reports the real
        # one - exactly the /tmp vs /private/tmp split on macOS.
        self.link = os.path.join(
            os.path.dirname(self.real), os.path.basename(self.real) + "-link")
        os.symlink(self.real, self.link)
        self.addCleanup(self._drop_links)
        # An extra level of link indirection under the workspace itself.
        self.inner_link = os.path.join(self.link, "ws-link")
        os.symlink(self.real, self.inner_link)
        subprocess.run(["git", "init", "-q", self.real], check=False)
        self.write("build/nested/a.o", "artifact")
        # Linux temp dirs are usually canonical; on macOS they never are.
        # Either way the symlink below models the split.
        self.assertTrue(os.path.islink(self.link))
        self.assertEqual(os.path.realpath(self.link), self.real)

    def _drop_links(self):
        for path in (self.inner_link, self.link):
            if os.path.islink(path):
                os.unlink(path)
        self._tmp.cleanup()

    def write(self, rel: str, content: str = "x") -> str:
        path = os.path.join(self.real, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(content)
        return path

    def run_check(self, *argv):
        env = dict(os.environ)
        env.pop("AGENT_GUARD_DIALECT", None)
        env["GIT_CONFIG_GLOBAL"] = "/dev/null"
        proc = subprocess.run(
            [sys.executable, CHECK, "--cwd", self.link, "--json", *argv],
            capture_output=True, text=True, env=env, timeout=60)
        try:
            return json.loads(proc.stdout), proc
        except json.JSONDecodeError:
            self.fail(f"check.py emitted no JSON: {proc.stdout!r} "
                      f"{proc.stderr!r}")

    # -- the three upstream macOS failures, reproduced on any host -------

    def test_powershell_tree_delete_relocates_from_symlinked_workspace(self):
        """Upstream: powershell/tree delete expected RELOCATE_TREE."""
        out, proc = self.run_check("--dialect", "powershell", "--enforce",
                                   "--", "ri build -r -fo")
        self.assertEqual((out["decision"], out["code"]),
                         ("ALLOW", "RELOCATE_TREE"), (out["reasons"], proc.stderr))
        self.assertTrue(os.path.isdir(os.path.join(self.real, ".agent-trash")))

    def test_advisory_mode_does_not_mutate_from_symlinked_workspace(self):
        """Upstream: advisory powershell delete expected RELOCATE, no mutation."""
        out, _ = self.run_check("--dialect", "powershell", "--",
                                "ri build -r -fo")
        self.assertEqual(out["decision"], "RELOCATE")
        self.assertTrue(os.path.isdir(os.path.join(self.real, "build")))

    def test_default_posix_behaviour_unchanged_from_symlinked_workspace(self):
        """Upstream: plain `rm -rf build` with no --dialect expected RELOCATE."""
        out, _ = self.run_check("--", "rm -rf build")
        self.assertEqual(out["decision"], "RELOCATE")
        self.assertEqual(out["ops"][0]["op"], "rm")
        self.assertTrue(os.path.isdir(os.path.join(self.real, "build")))

    def test_enforce_relocation_survives_symlinked_workspace(self):
        """RELOCATE is only honest if restore can put the tree back."""
        out, proc = self.run_check("--dialect", "powershell", "--enforce",
                                   "--", "ri build -r -fo")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        txid = out["compensations"][0]["txid"]
        self.assertFalse(os.path.exists(os.path.join(self.real, "build")))
        engine = RecoveryEngine(self.link)
        restored = engine.restore(txid)
        self.assertTrue(restored["ok"], restored)
        with open(os.path.join(self.real, "build", "nested", "a.o")) as fh:
            self.assertEqual(fh.read(), "artifact")

    # -- boundary facts, independent of the CLI -------------------------

    def test_classify_paths_agrees_on_both_spellings(self):
        for root in (self.real, self.link, self.inner_link):
            workspace = discover_workspace(root)
            spec = classify_paths(["build"], self.link, workspace,
                                 os.path.join(workspace, ".agent-trash"))[0]
            self.assertTrue(spec.inside_workspace, root)
            self.assertIsNone(spec.protected, root)

    def test_lexical_spelling_of_target_is_preserved(self):
        """The comparison is physical; the reported path stays lexical."""
        workspace = discover_workspace(self.link)
        spec = classify_paths(["build"], self.link, workspace, None)[0]
        self.assertEqual(
            spec.resolved, os.path.normpath(os.path.abspath(
                os.path.join(self.link, "build"))))
        self.assertTrue(spec.resolved.startswith(self.link))

    def test_verdict_is_relocate_on_both_dialects(self):
        workspace = discover_workspace(self.link)
        ctx = PolicyContext(workspace=workspace,
                            trash_root=os.path.join(workspace, ".agent-trash"),
                            base_dir=self.link)
        for cmd, dialect in (("rm -rf build", "posix"),
                             ("ri build -r -fo", "powershell")):
            specs, err = classify_command(cmd, dialect)
            self.assertIsNone(err, cmd)
            verdict = worst(decide_ops(specs, ctx))
            self.assertEqual(verdict.decision, "RELOCATE", cmd)

    def test_trash_centre_stays_inside_and_never_relocated(self):
        workspace = discover_workspace(self.link)
        specs = [s for s in classify_command("rm -rf .agent-trash/x", "posix")[0]]
        ctx = PolicyContext(workspace=workspace,
                            trash_root=os.path.join(workspace, ".agent-trash"),
                            base_dir=self.link)
        verdict = worst(decide_ops(specs, ctx))
        self.assertEqual((verdict.decision, verdict.code),
                         ("ALLOW", "ALLOW_TRASH_GC"))

    # -- normalization must not loosen the hard boundaries ---------------

    def test_workspace_root_still_blocked(self):
        out, proc = self.run_check("--enforce", "--", "rm -rf .")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual((out["decision"], out["code"]),
                         ("BLOCK", "BLOCK_PROTECTED_PATH"))

    def test_genuinely_outside_target_still_blocked(self):
        outside = tempfile.mkdtemp(prefix="agent-guard-outside-")
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        victim = os.path.join(outside, "keep.txt")
        with open(victim, "w") as fh:
            fh.write("keep")
        out, proc = self.run_check("--enforce", "--", "rm -f", victim)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual((out["decision"], out["code"]),
                         ("BLOCK", "BLOCK_OUT_OF_WORKSPACE"))
        self.assertTrue(os.path.exists(victim))

    def test_escape_via_symlink_into_workspace_is_outside(self):
        """A path that is physically elsewhere stays outside, however spelled."""
        outside = tempfile.mkdtemp(prefix="agent-guard-outsidesym-")
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        with open(os.path.join(outside, "keep.txt"), "w") as fh:
            fh.write("keep")
        escape = os.path.join(self.link, "escape")
        os.symlink(outside, escape)
        specs = [s for s in classify_command("rm -rf escape/keep.txt",
                                             "posix")[0]]
        workspace = discover_workspace(self.link)
        ctx = PolicyContext(workspace=workspace,
                            trash_root=os.path.join(workspace, ".agent-trash"),
                            base_dir=self.link)
        verdict = worst(decide_ops(specs, ctx))
        self.assertEqual((verdict.decision, verdict.code),
                         ("BLOCK", "BLOCK_OUT_OF_WORKSPACE"))
        self.assertTrue(os.path.exists(os.path.join(outside, "keep.txt")))


@unittest.skipUnless(symlink_supported(), "symlinks unavailable on this FS")
class SymlinkedAncestorFixture(unittest.TestCase):
    """<tmp>/phys/w is the workspace; <tmp>/alias -> phys is the caller path.

    This is the macOS layout (/var -> /private/var): the SYMLINK is in the
    ANCESTOR chain of the workspace, so the caller's lexical spelling and
    the engine's physical workspace differ by more than a trailing segment.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="agent-guard-anc-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = os.path.realpath(self._tmp.name)  # canonical tmp
        self.phys = os.path.join(self.tmp, "phys")
        self.alias = os.path.join(self.tmp, "alias")
        self.root = os.path.join(self.phys, "w")
        os.makedirs(os.path.join(self.root, "build", "nested"))
        with open(os.path.join(self.root, "build", "nested", "a.o"), "w") as fh:
            fh.write("artifact")
        os.symlink(self.phys, self.alias)
        subprocess.run(["git", "init", "-q", self.root], check=False)
        # The caller (and the whole guard) speaks the alias spelling.
        self.spelled_root = os.path.join(self.alias, "w")
        self.assertTrue(os.path.islink(self.alias))
        self.assertNotEqual(os.path.realpath(self.spelled_root), self.spelled_root)

    @property
    def trash_root(self) -> str:
        # check.py derives it from the PHYSICAL discovered workspace.
        return os.path.join(os.path.realpath(self.spelled_root), TRASH_DIRNAME)

    def run_check(self, *argv):
        env = dict(os.environ)
        env.pop("AGENT_GUARD_DIALECT", None)
        env.pop("AGENT_GUARD_TRASH", None)
        env["GIT_CONFIG_GLOBAL"] = "/dev/null"
        proc = subprocess.run(
            [sys.executable, CHECK, "--cwd", self.spelled_root,
             "--json", *argv],
            capture_output=True, text=True, env=env, timeout=60)
        try:
            return json.loads(proc.stdout), proc
        except json.JSONDecodeError:
            self.fail(f"check.py emitted no JSON: {proc.stdout!r} "
                      f"{proc.stderr!r}")

    def relocate_records(self, txid):
        engine = RecoveryEngine(os.path.realpath(self.spelled_root),
                               self.trash_root)
        return [r for r in engine.read_manifest()
                if r.get("type") == "relocate" and r.get("txid") == txid]

    # -- the macOS matrix failure, end to end through the CLI ------------

    def test_relocate_lands_inside_the_transaction_directory(self):
        """Pre-fix: trash_path was `trash/alias/w/build` - no txid level."""
        out, proc = self.run_check("--dialect", "powershell", "--enforce",
                                   "--", "ri build -r -fo")
        self.assertEqual(
            (out["decision"], out["code"]), ("ALLOW", "RELOCATE_TREE"),
            (out.get("reasons"), proc.stderr))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        txid = out["compensations"][0]["txid"]
        records = self.relocate_records(txid)
        self.assertEqual(len(records), 1, records)
        record = records[0]
        tx_dir = os.path.join(self.trash_root, txid)
        trash_path = record["trash_path"]
        self.assertEqual(
            os.path.commonpath([os.path.realpath(trash_path),
                                os.path.realpath(tx_dir)]),
            os.path.realpath(tx_dir),
            f"quarantine escape: {trash_path} is outside {tx_dir}")
        self.assertTrue(
            trash_path.startswith(tx_dir + os.sep),
            f"missing txid level: {trash_path}")
        self.assertNotIn(os.pardir, trash_path.split(os.sep),
                         f"unresolved '..' in quarantine path: {trash_path}")
        self.assertEqual(os.path.relpath(trash_path, tx_dir),
                         os.path.join("build"))
        self.assertTrue(os.path.isdir(trash_path), trash_path)
        self.assertFalse(os.path.exists(
            os.path.join(self.root, "build")))

    def test_origin_path_keeps_the_callers_lexical_spelling(self):
        """Restore and messages must speak the spelling the caller gave us."""
        out, _ = self.run_check("--dialect", "powershell", "--enforce",
                                "--", "ri build -r -fo")
        txid = out["compensations"][0]["txid"]
        record = self.relocate_records(txid)[0]
        self.assertEqual(record["origin_path"],
                         os.path.join(self.spelled_root, "build"))
        self.assertTrue(record["origin_path"].startswith(self.alias))
        self.assertNotEqual(os.path.realpath(record["origin_path"]),
                            record["origin_path"])

    def test_restore_round_trip_through_the_symlinked_ancestor(self):
        out, _ = self.run_check("--dialect", "powershell", "--enforce",
                                "--", "ri build -r -fo")
        txid = out["compensations"][0]["txid"]
        engine = RecoveryEngine(os.path.realpath(self.spelled_root),
                               self.trash_root)
        restored = engine.restore(txid)
        self.assertTrue(restored["ok"], restored)
        with open(os.path.join(self.root, "build", "nested", "a.o")) as fh:
            self.assertEqual(fh.read(), "artifact")

    def test_relocation_never_writes_outside_the_quarantine(self):
        """Nothing may be created anywhere but inside the quarantine.

        On macOS CI the pre-fix escape created a directory in a parent it
        had no right to write to (root-owned /private/var/folders/<2char>/),
        which surfaced as BLOCK COMPENSATION_FAILED.
        """
        parent = os.path.dirname(self.root)          # <tmp>/phys
        grandparent = os.path.dirname(parent)        # <tmp>
        before = {parent: set(os.listdir(parent)),
                  grandparent: set(os.listdir(grandparent))}
        out, proc = self.run_check("--dialect", "powershell", "--enforce",
                                   "--", "ri build -r -fo")
        self.assertEqual(
            (out["decision"], out["code"]), ("ALLOW", "RELOCATE_TREE"),
            (out.get("reasons"), proc.stderr))
        for path, entries in before.items():
            self.assertEqual(set(os.listdir(path)) - entries, set(),
                             f"stray entries created in {path}")
        self.assertTrue(os.path.isdir(
            os.path.join(self.root, TRASH_DIRNAME)))
        # The whole quarantine lives under this one transaction directory.
        txid = out["compensations"][0]["txid"]
        tx_dir = os.path.join(self.root, TRASH_DIRNAME, txid)
        entries = [os.path.join(tx_dir, e) for e in os.listdir(tx_dir)]
        self.assertEqual(entries, [os.path.join(tx_dir, "build")])

    # -- the guard itself, independent of the CLI ------------------------

    def test_symlink_target_is_quarantined_at_its_own_location(self):
        """Physicalising must not follow the FINAL component.

        A target that is itself a symlink is judged by where the LINK lives.
        A naive realpath on the origin would compute a destination derived
        from the link's TARGET - outside the workspace - and the containment
        guard would then (correctly) refuse everything.
        """
        outside = os.path.join(self.tmp, "outside-target")
        with open(outside, "w") as fh:
            fh.write("far away")
        link = os.path.join(self.root, "link")
        os.symlink(outside, link)
        engine = RecoveryEngine(os.path.realpath(self.spelled_root),
                                self.trash_root)
        specs = classify_paths([link], self.spelled_root,
                               engine.workspace, engine.trash_root)
        report = engine.relocate(specs, txid="TXLINK")
        self.assertEqual(report["skipped"], [], report["skipped"])
        self.assertEqual(len(report["moved"]), 1, report["moved"])
        trash_link = report["moved"][0]["trash"]
        self.assertTrue(os.path.islink(trash_link), trash_link)
        self.assertEqual(os.readlink(trash_link), outside)
        self.assertTrue(os.path.exists(outside))  # target untouched

    def test_uncontained_destination_is_refused(self):
        """Defence in depth: layout helpers must not escape trash/<txid>/."""
        from core.recovery import RecoveryEngine as Engine
        tx_dir = os.path.join(self.trash_root, "sometxid")
        inside = Engine._contained_dest(
            os.path.join(tx_dir, "build", "nested", "a.o"), tx_dir)
        self.assertTrue(inside.startswith(os.path.realpath(tx_dir)))
        for bad in (os.path.join(tx_dir, os.pardir, "elsewhere"),
                    os.path.join(self.phys, "escape"),
                    tx_dir):
            with self.assertRaises(OSError, msg=bad):
                Engine._contained_dest(bad, tx_dir)

    # -- AGENT_GUARD_TRASH: same mixed-spelling risk in the git exclude ---

    def test_exclude_rule_survives_a_lexical_trash_root(self):
        """relpath(trash_root, workspace) must not mix spellings either.

        AGENT_GUARD_TRASH accepts any spelling, so the engine can hold a
        PHYSICAL workspace next to a trash_root still spelled through the
        symlinked alias. Mixed sides yield a `..`-laden pattern such as
        `/../../alias/w/.agent-trash/`, which Git matches against nothing:
        the quarantine would then show up as ordinary untracked data. Same
        defect as the relocation escape, in the git-exclude arithmetic.
        """
        alias_trash = os.path.join(self.alias, "w", TRASH_DIRNAME)
        engine = RecoveryEngine(os.path.realpath(self.spelled_root),
                                alias_trash)
        self.assertNotEqual(engine.trash_root, os.path.realpath(alias_trash))
        engine.ensure_layout()
        with open(os.path.join(self.spelled_root, ".git", "info",
                               "exclude")) as fh:
            exclude = fh.read().replace(os.sep, "/")
        self.assertIn(f"/{TRASH_DIRNAME}/", exclude)
        self.assertNotIn(os.pardir * 2, exclude.splitlines()[-1])
        # The rule must really cover the quarantine location.
        proc = subprocess.run(
            ["git", "-C", self.spelled_root, "check-ignore", "-q",
             "--no-index", "--", f"{TRASH_DIRNAME}/x"],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0,
                         f"quarantine is not git-ignored: {exclude}")
        proc = subprocess.run(
            ["git", "-C", self.spelled_root, "status", "--porcelain",
             "--untracked-files=all"],
            capture_output=True, text=True)
        self.assertEqual(proc.stdout.strip(), "?? build/nested/a.o",
                         proc.stdout)


class UnlinkedWorkspaceFixture(unittest.TestCase):
    """The canonical (non-symlinked) layout must not change at all."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="agent-guard-plain-")
        self.root = self._tmp.name
        os.makedirs(os.path.join(self.root, "build"))
        with open(os.path.join(self.root, "build", "a.o"), "w") as fh:
            fh.write("x")
        self.addCleanup(self._tmp.cleanup)

    def test_inside_still_inside_and_root_still_root(self):
        workspace = discover_workspace(self.root)
        inside = classify_paths(["build"], self.root, workspace, None)[0]
        self.assertTrue(inside.inside_workspace)
        self.assertIsNone(inside.protected)
        root = classify_paths(["."], self.root, workspace, None)[0]
        self.assertEqual(root.protected, "workspace-root")

    def test_nested_root_and_child_agree(self):
        workspace = discover_workspace(self.root)
        deep = os.path.join(self.root, "build")
        spec = classify_paths(["a.o"], deep, workspace, None)[0]
        self.assertTrue(spec.inside_workspace)
        self.assertIsNone(spec.protected)


if __name__ == "__main__":
    unittest.main()
