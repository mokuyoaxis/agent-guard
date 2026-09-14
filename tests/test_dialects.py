"""Command dialect layer: cmd.exe and PowerShell (pure logic, Phase 1).

Phase 1 is deliberately host-independent: no Windows needed, no real
execution, no adapter change. These tests pin the two things that matter:

1. **Lexical fidelity** - cmd and PowerShell quoting/separation rules are
   not POSIX rules, and getting them wrong changes the *target set* (a
   guard that mis-splits a target list is worse than no guard).
2. **Fail-closed coverage** - every construct the layer cannot resolve
   mechanically (variables, subexpressions, piped target sets, wildcards
   expanded by the tool, interactive confirmations, nested hosts) must end
   as an undeterminable/UNKNOWN fact so policy BLOCKs it. Never a guess.

Each dialect gets well over the 15 required shapes. Existing POSIX
behaviour is pinned separately by the default-path tests at the bottom and
by the untouched legacy suites.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import dialects
from core.classifier import KIND_FS_DELETE, KIND_UNKNOWN, classify_command
from core.policy import (
    CODE_ALLOW_NOOP, CODE_BLOCK_OUT_OF_WORKSPACE, CODE_BLOCK_PROTECTED_PATH,
    CODE_BLOCK_UNDETERMINABLE_EFFECT, CODE_BLOCK_WILDCARD,
    CODE_RELOCATE_TREE, DECISION_ALLOW, DECISION_BLOCK, DECISION_RELOCATE,
    MODE_NORMAL, PolicyContext, decide_ops, worst,
)

WORKSPACE = os.environ.get("AGENT_GUARD_TEST_WORKSPACE", "/tmp/agent-guard-dialect-fixture")


def ctx_for(root):
    os.makedirs(os.path.join(root, "build", "nested"), exist_ok=True)
    with open(os.path.join(root, "build", "nested", "a.o"), "w") as fh:
        fh.write("x")
    return PolicyContext(
        workspace=root, trash_root=os.path.join(root, ".agent-trash"),
        base_dir=root, mode=MODE_NORMAL)


def one(specs):
    assert len(specs) == 1, f"expected 1 op, got {specs}"
    return specs[0]


def spec_for(cmd, dialect):
    return one(classify_command(cmd, dialect)[0])


def verdict_for(cmd, dialect, root):
    specs, err = classify_command(cmd, dialect)
    return worst(decide_ops(specs, ctx_for(root)))


# --------------------------------------------------------------- cmd shapes


class CmdDialect(unittest.TestCase):
    """cmd.exe lexical rules, aliases and destructive flag mapping."""

    def test_del_with_flags(self):
        spec = spec_for("del /q /f build\\nested\\a.o", "cmd")
        self.assertEqual(spec.kind, KIND_FS_DELETE)
        self.assertEqual(spec.op, "del")
        self.assertTrue(spec.force)
        self.assertFalse(spec.recursive)
        self.assertEqual(spec.targets, ["build/nested/a.o"])  # normalized

    def test_del_without_flags(self):
        spec = spec_for("del build\\nested\\a.o", "cmd")
        self.assertEqual(spec.targets, ["build/nested/a.o"])
        self.assertFalse(spec.force)

    def test_del_slash_f_is_force(self):
        self.assertTrue(spec_for("del /F a.txt", "cmd").force)

    def test_del_multiple_targets(self):
        spec = spec_for("del a.txt b.txt c.txt", "cmd")
        self.assertEqual(spec.targets, ["a.txt", "b.txt", "c.txt"])

    def test_erase_is_del(self):
        spec = spec_for("erase /q a.txt", "cmd")
        self.assertEqual(spec.kind, KIND_FS_DELETE)
        self.assertEqual(spec.op, "erase")

    def test_rd_recursive_quiet(self):
        spec = spec_for("rd /s /q build", "cmd")
        self.assertTrue(spec.recursive)
        self.assertTrue(spec.force)

    def test_rmdir_recursive(self):
        spec = spec_for("rmdir /S /Q build", "cmd")  # flags are case-insensitive
        self.assertTrue(spec.recursive)
        self.assertTrue(spec.force)

    def test_rd_joined_flags(self):
        spec = spec_for("rd /sq build", "cmd")
        self.assertTrue(spec.recursive and spec.force)

    def test_quoted_target_keeps_spaces(self):
        spec = spec_for('rd /s /q "C:\\Program Files\\build"', "cmd")
        self.assertEqual(spec.targets, ["C:/Program Files/build"])

    def test_quoted_target_with_trailing_backslash(self):
        spec = spec_for('rmdir /s /q "C:\\proj\\build\\"', "cmd")
        self.assertEqual(spec.targets, ["C:/proj/build/"])

    def test_double_ampersand_separates_commands(self):
        specs, err = classify_command("cd build && del /q a.txt", "cmd")
        self.assertIsNone(err)
        spec = one(specs)
        self.assertEqual(spec.targets, ["a.txt"])
        self.assertEqual(spec.shape, "F1")  # shared compound-cwd shape rule

    def test_ampersand_only_separator(self):
        specs, err = classify_command("echo x&del a.txt", "cmd")
        self.assertIsNone(err)
        self.assertEqual(one(specs).targets, ["a.txt"])

    def test_pipe_separates_commands(self):
        specs, err = classify_command("dir | del a.txt", "cmd")
        self.assertIsNone(err)
        self.assertEqual(one(specs).targets, ["a.txt"])

    def test_caret_escapes_separator(self):
        specs, err = classify_command("del a^&b.txt", "cmd")
        self.assertIsNone(err)
        self.assertEqual(one(specs).targets, ["a&b.txt"])

    def test_creation_then_delete_is_shape_f2(self):
        specs, err = classify_command("copy src.txt a.tmp && del a.tmp", "cmd")
        self.assertEqual(one(specs).shape, "F2")

    def test_percent_variable_is_undeterminable(self):
        spec = spec_for("del /s /q %BUILD_DIR%", "cmd")
        self.assertTrue(spec.undeterminable)
        self.assertTrue(any("%BUILD_DIR%" in n for n in spec.notes))

    def test_unbalanced_quote_blocks(self):
        specs, err = classify_command('del /q "unterminated', "cmd")
        self.assertIsNotNone(err)
        self.assertEqual(one(specs).kind, KIND_UNKNOWN)
        self.assertTrue(one(specs).undeterminable)

    def test_unknown_flag_is_undeterminable(self):
        spec = spec_for("del /z a.txt", "cmd")
        self.assertTrue(spec.undeterminable)

    def test_targetless_del_is_undeterminable(self):
        spec = spec_for("del /s /q", "cmd")
        self.assertTrue(spec.undeterminable)

    def test_wildcard_target_is_flagged(self):
        self.assertTrue(spec_for("del /s *.log", "cmd").wildcard)

    def test_noise_is_not_destructive(self):
        for cmd in ("git status", "dir /b", "echo del", "where del"):
            self.assertEqual(classify_command(cmd, "cmd")[0], [])

    def test_rm_is_not_cmd_delete(self):
        # `rm` is not a cmd builtin: recognising it would be a guess, and
        # cmd would just report "not recognized".
        self.assertEqual(classify_command("rm -rf build", "cmd")[0], [])

    def test_dialect_recorded_on_spec(self):
        self.assertEqual(spec_for("del /q a.txt", "cmd").dialect, "cmd")


class CmdVerdicts(unittest.TestCase):
    """cmd facts through the unchanged policy table."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="agent-guard-cmd-")
        self.root = self._tmp.name
        ctx_for(self.root)
        self.addCleanup(self._tmp.cleanup)

    def test_recursive_tree_relocates(self):
        v = verdict_for("rd /s /q build", "cmd", self.root)
        self.assertEqual((v.decision, v.code), (DECISION_RELOCATE, CODE_RELOCATE_TREE))

    def test_wildcard_blocks(self):
        v = verdict_for("del /s *.log", "cmd", self.root)
        self.assertEqual((v.decision, v.code), (DECISION_BLOCK, CODE_BLOCK_WILDCARD))

    def test_absolute_windows_path_outside_workspace_blocks(self):
        v = verdict_for("rd /s /q C:\\Windows", "cmd", self.root)
        self.assertEqual((v.decision, v.code),
                         (DECISION_BLOCK, CODE_BLOCK_OUT_OF_WORKSPACE))

    def test_dot_target_blocks_as_workspace_root(self):
        v = verdict_for("rd /s /q .", "cmd", self.root)
        self.assertEqual((v.decision, v.code),
                         (DECISION_BLOCK, CODE_BLOCK_PROTECTED_PATH))

    def test_variable_blocks(self):
        v = verdict_for("del /s /q %BUILD_DIR%", "cmd", self.root)
        self.assertEqual((v.decision, v.code),
                         (DECISION_BLOCK, CODE_BLOCK_UNDETERMINABLE_EFFECT))


# -------------------------------------------------------- PowerShell shapes


class PowerShellDialect(unittest.TestCase):
    """PowerShell lexical rules, alias expansion and fail-closed safety."""

    def test_remove_item_recurse_force(self):
        spec = spec_for("Remove-Item -Recurse -Force .\\build", "powershell")
        self.assertEqual(spec.kind, KIND_FS_DELETE)
        self.assertEqual(spec.op, "remove-item")
        self.assertTrue(spec.recursive and spec.force)
        self.assertEqual(spec.targets, ["./build"])

    def test_del_alias_expands(self):
        spec = spec_for("del a.txt", "powershell")
        self.assertEqual(spec.op, "remove-item")
        self.assertTrue(any("alias" in n for n in spec.notes))

    def test_ri_alias_expands(self):
        self.assertEqual(spec_for("ri -Force a.txt", "powershell").op, "remove-item")

    def test_rd_alias_expands(self):
        self.assertEqual(spec_for("rd -Recurse build", "powershell").op, "remove-item")

    def test_rmdir_alias_expands(self):
        self.assertEqual(
            spec_for("rmdir -Recurse -Force build", "powershell").op,
            "remove-item")

    def test_erase_alias_expands(self):
        self.assertEqual(spec_for("erase a.txt", "powershell").op, "remove-item")

    def test_colon_switch_syntax(self):
        spec = spec_for("Remove-Item -Recurse:$true -Force:$true build", "powershell")
        self.assertTrue(spec.recursive and spec.force)

    def test_negated_switch_is_not_recursive(self):
        spec = spec_for("Remove-Item -Recurse:$false build", "powershell")
        self.assertFalse(spec.recursive)
        self.assertFalse(spec.undeterminable)

    def test_whatif_marks_dry_run(self):
        spec = spec_for("Remove-Item -Recurse -WhatIf build", "powershell")
        self.assertTrue(spec.dry_run)
        self.assertFalse(spec.undeterminable)

    def test_whatif_false_is_a_real_delete(self):
        spec = spec_for("Remove-Item -Recurse -WhatIf:$false build", "powershell")
        self.assertFalse(spec.dry_run)
        self.assertTrue(spec.recursive)

    def test_whatif_variable_is_undeterminable(self):
        spec = spec_for("Remove-Item -WhatIf:$flag build", "powershell")
        self.assertTrue(spec.undeterminable)
        self.assertFalse(spec.dry_run)

    def test_confirm_prompt_is_undeterminable(self):
        self.assertTrue(
            spec_for("Remove-Item -Confirm build", "powershell").undeterminable)

    def test_confirm_false_still_deletes(self):
        spec = spec_for("Remove-Item -Confirm:$false build", "powershell")
        self.assertFalse(spec.undeterminable)

    def test_literalpath_is_undeterminable(self):
        self.assertTrue(
            spec_for("Remove-Item -LiteralPath build", "powershell").undeterminable)

    def test_filter_is_undeterminable(self):
        self.assertTrue(
            spec_for("Remove-Item -Filter *.log build", "powershell").undeterminable)

    def test_include_is_undeterminable(self):
        self.assertTrue(
            spec_for("Remove-Item -Include *.log build", "powershell").undeterminable)

    def test_exclude_is_undeterminable(self):
        self.assertTrue(
            spec_for("Remove-Item -Exclude keep build", "powershell").undeterminable)

    def test_unknown_parameter_is_undeterminable(self):
        self.assertTrue(
            spec_for("Remove-Item -Bogus build", "powershell").undeterminable)

    def test_variable_target_is_undeterminable(self):
        spec = spec_for("Remove-Item -Recurse -Force $target", "powershell")
        self.assertTrue(spec.undeterminable)

    def test_subexpression_target_is_undeterminable(self):
        spec = spec_for("Remove-Item -Recurse (Join-Path $PWD build)", "powershell")
        self.assertTrue(spec.undeterminable)

    def test_pipeline_target_set_is_undeterminable(self):
        spec = spec_for("Get-ChildItem -Recurse | Remove-Item -Force", "powershell")
        self.assertTrue(spec.undeterminable)
        self.assertEqual(spec.targets, [])

    def test_multi_target_with_quotes(self):
        spec = spec_for('Remove-Item -Recurse "a b" "c d"', "powershell")
        self.assertEqual(spec.targets, ["a b", "c d"])

    def test_multiline_script_separates_commands(self):
        specs, err = classify_command(
            "Set-Location build\nRemove-Item -Recurse -Force x", "powershell")
        self.assertIsNone(err)
        spec = one(specs)
        self.assertEqual(spec.targets, ["x"])
        self.assertEqual(spec.shape, "F1")

    def test_semicolon_separates_commands(self):
        specs, err = classify_command("Write-Host hi; Remove-Item a.txt", "powershell")
        self.assertIsNone(err)
        self.assertEqual(one(specs).targets, ["a.txt"])

    def test_unbalanced_quote_blocks(self):
        specs, err = classify_command('Remove-Item "unterminated', "powershell")
        self.assertIsNotNone(err)
        self.assertEqual(one(specs).kind, KIND_UNKNOWN)

    def test_nested_host_with_smell_is_unknown(self):
        specs, err = classify_command(
            'powershell -Command "Remove-Item -Recurse .\\build"', "powershell")
        self.assertIsNone(err)
        self.assertEqual(one(specs).kind, KIND_UNKNOWN)
        self.assertTrue(one(specs).undeterminable)

    def test_nested_host_without_smell_is_ignored(self):
        specs, _ = classify_command(
            'powershell -Command "Write-Host hi"', "powershell")
        self.assertEqual(specs, [])

    def test_other_delete_cmdlets_are_out_of_scope(self):
        # Clear-Content / Remove-ItemProperty mutate non-filesystem
        # providers whose compensation does not exist yet: out of the
        # recognised vocabulary rather than mapped onto an fs relocation.
        for cmd in ("Clear-Content a.txt", "Remove-ItemProperty -Path x -Name y"):
            self.assertEqual(classify_command(cmd, "powershell")[0], [])

    def test_wildcard_target_is_flagged(self):
        self.assertTrue(spec_for("Remove-Item -Recurse *.log", "powershell").wildcard)

    def test_unc_path_is_noted(self):
        spec = spec_for("Remove-Item -Recurse \\\\server\\share\\x", "powershell")
        self.assertTrue(any("UNC" in n for n in spec.notes))

    def test_noise_is_not_destructive(self):
        for cmd in ("Get-ChildItem -Recurse", "Write-Host del", "git status"):
            self.assertEqual(classify_command(cmd, "powershell")[0], [])


class PowerShellVerdicts(unittest.TestCase):
    """PowerShell facts through the unchanged policy table."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="agent-guard-ps-")
        self.root = self._tmp.name
        ctx_for(self.root)
        self.addCleanup(self._tmp.cleanup)

    def test_recursive_tree_relocates(self):
        v = verdict_for("Remove-Item -Recurse -Force .\\build", "powershell",
                        self.root)
        self.assertEqual((v.decision, v.code), (DECISION_RELOCATE, CODE_RELOCATE_TREE))

    def test_whatif_allows(self):
        v = verdict_for("Remove-Item -Recurse -WhatIf build", "powershell",
                        self.root)
        self.assertEqual((v.decision, v.code), (DECISION_ALLOW, CODE_ALLOW_NOOP))

    def test_nested_host_blocks(self):
        v = verdict_for('powershell -Command "Remove-Item -Recurse .\\build"',
                        "powershell", self.root)
        self.assertEqual((v.decision, v.code),
                         (DECISION_BLOCK, CODE_BLOCK_UNDETERMINABLE_EFFECT))

    def test_wildcard_blocks(self):
        v = verdict_for("Remove-Item -Recurse *.log", "powershell", self.root)
        self.assertEqual((v.decision, v.code), (DECISION_BLOCK, CODE_BLOCK_WILDCARD))

    def test_parent_escape_blocks(self):
        v = verdict_for("Remove-Item -Recurse ..\\elsewhere", "powershell",
                        self.root)
        self.assertEqual((v.decision, v.code),
                         (DECISION_BLOCK, CODE_BLOCK_OUT_OF_WORKSPACE))


# -------------------------------------------------------- shared behaviour


class SharedWindowsShapes(unittest.TestCase):
    """Cross-dialect invariants: both Windows dialects get the same effects."""

    PAIRS = [
        ("rd /s /q build", "cmd"),
        ("del /s /q build", "cmd"),
        ("Remove-Item -Recurse -Force build", "powershell"),
        ("del -Recurse -Force build", "powershell"),
    ]

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="agent-guard-shared-")
        self.root = self._tmp.name
        ctx_for(self.root)
        self.addCleanup(self._tmp.cleanup)

    def test_recursive_windows_deletes_relocate_the_tree(self):
        for cmd, dialect in self.PAIRS:
            with self.subTest(cmd=cmd, dialect=dialect):
                v = verdict_for(cmd, dialect, self.root)
                self.assertEqual(v.decision, DECISION_RELOCATE)
                self.assertEqual(v.code, CODE_RELOCATE_TREE)

    def test_unknown_dialect_name_is_rejected(self):
        # Silently defaulting to POSIX would lex a Windows line wrongly -
        # the exact failure this layer prevents.
        with self.assertRaises(ValueError):
            classify_command("del /q a.txt", "fish")

    def test_dialect_aliases_resolve(self):
        self.assertEqual(dialects.normalize_dialect("pwsh"), "powershell")
        self.assertEqual(dialects.normalize_dialect("cmd.exe"), "cmd")
        self.assertEqual(dialects.normalize_dialect(""), "posix")
        self.assertEqual(dialects.normalize_dialect(None), "posix")

    def test_detection_is_advisory_and_conservative(self):
        self.assertEqual(dialects.detect_dialect("del /s /q build"), "cmd")
        self.assertEqual(dialects.detect_dialect("Remove-Item -Recurse x"),
                         "powershell")
        self.assertIsNone(dialects.detect_dialect("rm -rf build"))
        self.assertIsNone(dialects.detect_dialect("git status"))


class DefaultPathUnchanged(unittest.TestCase):
    """The POSIX default path keeps its pre-dialect behaviour exactly."""

    def test_default_dialect_facts(self):
        spec = spec_for("rm -rf build", "posix")
        self.assertEqual((spec.op, spec.kind), ("rm", KIND_FS_DELETE))
        self.assertTrue(spec.recursive and spec.force)
        self.assertEqual(spec.dialect, "posix")

    def test_omitting_dialect_uses_posix(self):
        with_dialect, _ = classify_command("rm -rf build", "posix")
        without, _ = classify_command("rm -rf build")
        self.assertEqual([s.targets for s in with_dialect],
                         [s.targets for s in without])

    def test_cmd_vocabulary_is_not_posix(self):
        self.assertEqual(classify_command("del /s /q build")[0], [])

    def test_posix_alias_expansion_absent(self):
        specs, _ = classify_command("rd /s /q build", "powershell")
        self.assertEqual(specs[0].op, "remove-item")

    def test_posix_parse_error_still_blocks(self):
        specs, err = classify_command('rm -rf "unterminated')
        self.assertIsNotNone(err)
        self.assertEqual(specs[0].kind, KIND_UNKNOWN)
        self.assertEqual(specs[0].dialect, "posix")

    def test_posix_shapes_unchanged(self):
        specs, _ = classify_command("cd sub && rm -rf build")
        self.assertEqual(specs[0].shape, "F1")
        specs, _ = classify_command("touch a.tmp && rm a.tmp")
        self.assertEqual(specs[0].shape, "F2")


if __name__ == "__main__":
    unittest.main()
