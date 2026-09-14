"""Dialect layer Phase 2: PowerShell short-parameter aliases + production
wiring (red -> green regression tests).

Phase 1 (core/dialects.py) classified Windows-native command lines but left
two gaps that a real Windows session exposed. Each gap gets its own test
class here, and each test is written to FAIL against the Phase 1 code:

Gap 1 - PowerShell resolves a parameter by *unambiguous prefix*
(`-r`/`-rec` -> `-Recurse`, `-fo` -> `-Force`). Phase 1 read `ri build -r -fo`
as `recursive=False, force=False, undeterminable=True`: not merely less
useful, but a *weaker* fact than the truth about a recursive forced delete.
The negative half matters just as much: an ambiguous prefix (`-c`, `-p`,
`-wi`) must stay UNKNOWN so policy BLOCKs it. The guard never guesses a
flag's meaning.

Gap 2 - `classify_command(cmd, dialect=...)` existed but nothing called it
with a dialect: check.py, the Claude hook and the DSH plugin were all
hard-wired to POSIX. These tests pin the wiring end to end: the CLI flag,
the environment fallback, the adapter prefilter (a POSIX regex cannot see
`ri build -r -fo`, so an unwired adapter skips the guard entirely), and the
explicit BLOCK for an unusable selector.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import dialects, policy
from core.classifier import classify_command
from core.policy import (
    CODE_BLOCK_DIALECT_INVALID, CODE_BLOCK_DIALECT_UNKNOWN,
    CODE_BLOCK_UNDETERMINABLE_EFFECT, CODE_RELOCATE_TREE,
    DECISION_BLOCK, DECISION_RELOCATE, MODE_NORMAL, PolicyContext, decide_ops,
    worst,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECK = os.path.join(ROOT, "skills", "delete-guard", "scripts", "check.py")
CLAUDE_HOOK = os.path.join(ROOT, "adapters", "claude", "pre_tool_use.py")


def one(specs):
    assert len(specs) == 1, f"expected 1 op, got {specs}"
    return specs[0]


def spec_for(cmd, dialect="powershell"):
    return one(classify_command(cmd, dialect)[0])


def ctx_for(root):
    os.makedirs(os.path.join(root, "build", "nested"), exist_ok=True)
    with open(os.path.join(root, "build", "nested", "a.o"), "w") as fh:
        fh.write("x")
    return PolicyContext(
        workspace=root, trash_root=os.path.join(root, ".agent-trash"),
        base_dir=root, mode=MODE_NORMAL)


def verdict_for(cmd, dialect, root):
    specs, err = classify_command(cmd, dialect)
    return worst(decide_ops(specs, ctx_for(root)))


# ---------------------------------------------------- Gap 1: short aliases


class PowerShellShortParameterAliases(unittest.TestCase):
    """Unambiguous prefixes expand; ambiguous ones fail closed."""

    def test_issue_repro_ri_build_r_fo(self):
        """The exact command from the issue: must be recursive AND forced."""
        spec = spec_for("ri build -r -fo")
        self.assertTrue(spec.recursive, spec.notes)
        self.assertTrue(spec.force, spec.notes)
        self.assertFalse(spec.undeterminable, spec.notes)

    def test_issue_repro_rm_build_r_fo(self):
        """`rm` is a PowerShell alias for Remove-Item and takes the same args."""
        spec = spec_for("rm build -r -fo")
        self.assertEqual(spec.op, "remove-item")
        self.assertTrue(spec.recursive and spec.force, spec.notes)
        self.assertFalse(spec.undeterminable, spec.notes)

    def test_full_names_still_work(self):
        spec = spec_for("Remove-Item build -Recurse -Force")
        self.assertTrue(spec.recursive and spec.force)
        self.assertFalse(spec.undeterminable)

    def test_case_insensitive_prefixes(self):
        spec = spec_for("Remove-Item build -RECURSE -Force")
        self.assertTrue(spec.recursive and spec.force)
        self.assertFalse(spec.undeterminable)

    def test_intermediate_prefixes(self):
        for token in ("-rec", "-recur", "-recurse"):
            spec = spec_for(f"ri build {token} -fo")
            self.assertTrue(spec.recursive, token)
            self.assertTrue(spec.force, token)
            self.assertFalse(spec.undeterminable, token)

    def test_force_intermediate_prefix(self):
        for token in ("-fo", "-for", "-forc", "-force"):
            spec = spec_for(f"ri build -r {token}")
            self.assertTrue(spec.force, token)
            self.assertFalse(spec.undeterminable, token)

    def test_short_alias_not_required_for_recurse_only(self):
        spec = spec_for("ri build -r")
        self.assertTrue(spec.recursive)
        self.assertFalse(spec.force)
        self.assertFalse(spec.undeterminable)

    def test_expansion_is_recorded_for_audit(self):
        spec = spec_for("ri build -r -fo")
        notes = " ".join(spec.notes)
        self.assertIn("-r expanded to -recurse", notes)
        self.assertIn("-fo expanded to -force", notes)

    def test_whatif_short_prefix_stays_conservative(self):
        """`-wi` is ambiguous (WhatIf / WarningAction) and is NOT expanded.

        The issue only requires that an unambiguous `-WhatIf` keeps working
        and that ambiguous abbreviations fail closed; `-wi` is the
        documented conservative exception (see docs/compatibility.md).
        """
        spec = spec_for("ri build -r -wi")
        self.assertFalse(spec.dry_run)
        self.assertTrue(spec.undeterminable, spec.notes)
        self.assertTrue(spec.recursive)  # the unambiguous part still applied

    def test_whatif_with_value_false_deletes(self):
        spec = spec_for("ri build -r -WhatIf:$false")
        self.assertFalse(spec.dry_run)
        self.assertTrue(spec.recursive)
        self.assertFalse(spec.undeterminable)

    def test_literalpath_prefix_is_undeterminable(self):
        """`-lp` resolves in PowerShell; the guard must treat it as unsafe."""
        spec = spec_for("ri -lp build")
        self.assertTrue(spec.undeterminable, spec.notes)

    # -- the ambiguous half: never guessed -------------------------------

    def test_ambiguous_c_confirm_credential_is_unknown(self):
        spec = spec_for("ri build -c")
        self.assertTrue(spec.undeterminable)
        self.assertTrue(any("unknown parameter -c" in n for n in spec.notes))

    def test_ambiguous_p_path_pspath_is_unknown(self):
        spec = spec_for("ri build -p x")
        self.assertTrue(spec.undeterminable)
        self.assertTrue(any("unknown parameter -p" in n for n in spec.notes))

    def test_ambiguous_wi_whatif_warningaction_is_unknown(self):
        """`-wi` is a WhatIf prefix on the host, but also WarningAction.

        One means "do not delete", the other means "delete anyway": the
        guard refuses to pick.
        """
        spec = spec_for("ri build -wi")
        self.assertTrue(spec.undeterminable)
        self.assertTrue(any("unknown parameter -wi" in n for n in spec.notes))

    def test_ambiguous_prefix_never_sets_facts(self):
        for token in ("-c", "-p", "-wi", "-w", "-i", "-e", "-a", "-v", "-d"):
            spec = spec_for(f"ri build {token}")
            self.assertFalse(spec.recursive, token)
            self.assertFalse(spec.force, token)
            self.assertTrue(spec.undeterminable, token)

    def test_unknown_long_parameter_is_unknown(self):
        spec = spec_for("ri build -NotAParameter")
        self.assertTrue(spec.undeterminable)


class PowerShellShortAliasVerdicts(unittest.TestCase):
    """The corrected facts must reach the unchanged policy table."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="agent-guard-ps2-")
        self.root = self._tmp.name
        ctx_for(self.root)
        self.addCleanup(self._tmp.cleanup)

    def test_recursive_delete_relocates_instead_of_blocking(self):
        """Phase 1 BLOCKed this as undeterminable; it is a plain tree delete."""
        v = verdict_for("ri build -r -fo", "powershell", self.root)
        self.assertEqual((v.decision, v.code),
                         (DECISION_RELOCATE, CODE_RELOCATE_TREE))

    def test_recursive_delete_with_cmd_alias_family(self):
        for cmd in ("rd build -r -fo", "del build -r -fo",
                    "Remove-Item build -r -fo"):
            v = verdict_for(cmd, "powershell", self.root)
            self.assertEqual(v.decision, DECISION_RELOCATE, cmd)

    def test_ambiguous_switch_blocks_undeterminable(self):
        for cmd in ("ri build -c", "ri build -p x", "ri build -wi"):
            v = verdict_for(cmd, "powershell", self.root)
            self.assertEqual((v.decision, v.code),
                             (DECISION_BLOCK,
                              CODE_BLOCK_UNDETERMINABLE_EFFECT), cmd)

    def test_posix_is_not_affected_by_prefix_expansion(self):
        """`-r`/`-fo` are not POSIX flags; the POSIX path is untouched."""
        specs, _ = classify_command("rm -rf build", "posix")
        self.assertEqual(one(specs).op, "rm")
        self.assertTrue(one(specs).recursive and one(specs).force)


# -------------------------------------- Gap 2: dialect resolution contract


class DialectResolution(unittest.TestCase):
    """`resolve_dialect` never raises and never guesses."""

    def test_none_is_posix_and_ok(self):
        r = dialects.resolve_dialect(None)
        self.assertEqual((r.dialect, r.ok), ("posix", True))

    def test_known_aliases(self):
        for value, expected in [("posix", "posix"), ("sh", "posix"),
                                ("bash", "posix"), ("cmd", "cmd"),
                                ("cmd.exe", "cmd"), ("batch", "cmd"),
                                ("powershell", "powershell"), ("pwsh", "powershell"),
                                ("PS", "powershell"), ("  CMD  ", "cmd")]:
            r = dialects.resolve_dialect(value)
            self.assertTrue(r.ok, value)
            self.assertEqual(r.dialect, expected, value)

    def test_unknown_name_is_not_ok_and_not_swallowed(self):
        r = dialects.resolve_dialect("fish")
        self.assertFalse(r.ok)
        self.assertEqual(r.dialect, "posix")  # nominal fallback value only
        self.assertIn("unknown dialect", r.reason)

    def test_malformed_selector_is_invalid(self):
        for value in ("posix:cmd", "posix,cmd", "posix/cmd", "posix;cmd"):
            r = dialects.resolve_dialect(value)
            self.assertFalse(r.ok, value)
            self.assertIn("malformed", r.reason)

    def test_non_string_selector_is_not_ok(self):
        r = dialects.resolve_dialect(42)
        self.assertFalse(r.ok)
        self.assertIn("not a string", r.reason)

    def test_blank_selector_means_default(self):
        for value in ("", "   "):
            r = dialects.resolve_dialect(value)
            self.assertTrue(r.ok, repr(value))
            self.assertEqual(r.dialect, "posix")

    def test_requested_is_preserved_verbatim(self):
        r = dialects.resolve_dialect("  PoWeRsHeLl  ")
        self.assertTrue(r.ok)
        self.assertEqual(r.dialect, "powershell")
        self.assertEqual(r.requested, "  PoWeRsHeLl  ")


class DialectPolicyDecisions(unittest.TestCase):
    """Unknown/illegal selectors must BLOCK with a dedicated code."""

    def test_unknown_name_blocks(self):
        r = dialects.resolve_dialect("fish")
        v = policy.decide_dialect_failure(r)
        self.assertEqual((v.decision, v.code),
                         (DECISION_BLOCK, CODE_BLOCK_DIALECT_UNKNOWN))

    def test_malformed_blocks_with_invalid_code(self):
        r = dialects.resolve_dialect("posix:cmd")
        v = policy.decide_dialect_failure(r)
        self.assertEqual((v.decision, v.code),
                         (DECISION_BLOCK, CODE_BLOCK_DIALECT_INVALID))

    def test_unknown_is_block_not_ask_or_allow(self):
        """Configuration defect, not one-off authorization."""
        r = dialects.resolve_dialect("nope")
        v = policy.decide_dialect_failure(r)
        self.assertTrue(v.blocked)
        self.assertFalse(v.asks)
        self.assertNotEqual(v.decision, "ALLOW")

    def test_explanations_exist_for_both_codes(self):
        for code in (CODE_BLOCK_DIALECT_UNKNOWN, CODE_BLOCK_DIALECT_INVALID):
            self.assertIn(code, policy.EXPLANATIONS)
            self.assertTrue(policy.EXPLANATIONS[code])

    def test_reason_names_the_selector(self):
        r = dialects.resolve_dialect("fish", source="flag --dialect")
        v = policy.decide_dialect_failure(r)
        self.assertTrue(any("fish" in reason for reason in v.reasons))
        self.assertTrue(any("not classified" in reason for reason in v.reasons))

    def test_calling_with_usable_dialect_is_a_programmer_error(self):
        r = dialects.resolve_dialect("posix")
        with self.assertRaises(ValueError):
            policy.decide_dialect_failure(r)


# ------------------------------------------------ Gap 2: CLI production path


class CheckCliDialect(unittest.TestCase):
    """check.py must accept --dialect and keep posix as the default."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="agent-guard-cli2-")
        self.root = self._tmp.name
        ctx_for(self.root)
        subprocess.run(["git", "init", "-q", self.root], check=False)
        self.addCleanup(self._tmp.cleanup)

    def run_check(self, *argv, env=None):
        base_env = dict(os.environ)
        base_env.pop("AGENT_GUARD_DIALECT", None)
        base_env["GIT_CONFIG_GLOBAL"] = "/dev/null"
        if env:
            base_env.update(env)
        proc = subprocess.run(
            [sys.executable, CHECK, "--cwd", self.root, "--json", *argv],
            capture_output=True, text=True, env=base_env, timeout=60)
        try:
            return json.loads(proc.stdout), proc
        except json.JSONDecodeError:
            self.fail(f"check.py emitted no JSON: {proc.stdout!r} "
                      f"{proc.stderr!r}")

    def test_dialect_flag_is_accepted(self):
        out, proc = self.run_check("--dialect", "powershell",
                                   "--", "ri build -r -fo")
        self.assertNotIn("usage:", proc.stderr)
        self.assertEqual(out["dialect"], "powershell")

    def test_powershell_tree_delete_relocates(self):
        out, _ = self.run_check("--dialect", "powershell", "--enforce",
                                "--", "ri build -r -fo")
        self.assertEqual((out["decision"], out["code"]),
                         ("ALLOW", "RELOCATE_TREE"))
        self.assertTrue(os.path.isdir(
            os.path.join(self.root, ".agent-trash")))

    def test_dialect_is_reported_in_json(self):
        out, _ = self.run_check("--dialect", "pwsh", "--", "rd build")
        self.assertEqual(out["dialect"], "powershell")
        self.assertEqual(out["dialect_requested"], "pwsh")
        self.assertEqual(out["dialect_outcome"], "ok")

    def test_default_is_posix(self):
        out, _ = self.run_check("--", "rm -rf build")
        self.assertEqual(out["dialect"], "posix")
        self.assertIsNone(out["dialect_requested"])

    def test_default_posix_behaviour_unchanged(self):
        """The historical invocation still works with no --dialect."""
        out, _ = self.run_check("--", "rm -rf build")
        self.assertEqual(out["operator" if False else "decision"], "RELOCATE")
        self.assertEqual(out["ops"][0]["op"], "rm")

    def test_cmd_dialect_is_reported(self):
        out, _ = self.run_check("--dialect", "cmd", "--", "rd /s /q build")
        self.assertEqual(out["dialect"], "cmd")

    def test_env_var_is_honoured(self):
        out, _ = self.run_check("--", "ri build -r -fo",
                                env={"AGENT_GUARD_DIALECT": "powershell"})
        self.assertEqual(out["dialect"], "powershell")

    def test_flag_wins_over_env(self):
        out, _ = self.run_check("--dialect", "posix", "--", "rm -rf build",
                                env={"AGENT_GUARD_DIALECT": "powershell"})
        self.assertEqual(out["dialect"], "posix")

    def test_unknown_dialect_blocks_with_code(self):
        out, proc = self.run_check("--dialect", "fish", "--enforce",
                                   "--", "rm -rf build")
        self.assertEqual((out["decision"], out["code"]),
                         ("BLOCK", CODE_BLOCK_DIALECT_UNKNOWN))
        self.assertEqual(proc.returncode, 2)
        # The command must not have run: the tree is still in place.
        self.assertTrue(os.path.isdir(os.path.join(self.root, "build")))

    def test_malformed_dialect_blocks_with_invalid_code(self):
        out, proc = self.run_check("--dialect", "posix:cmd", "--enforce",
                                   "--", "rm -rf build")
        self.assertEqual((out["decision"], out["code"]),
                         ("BLOCK", CODE_BLOCK_DIALECT_INVALID))
        self.assertEqual(proc.returncode, 2)

    def test_unknown_dialect_is_not_an_argparse_usage_error(self):
        """Argparse `choices` would skip the decision entirely."""
        out, proc = self.run_check("--dialect", "fish", "--", "rm -rf build")
        self.assertNotIn("usage:", proc.stderr)
        self.assertEqual(out["decision"], "BLOCK")
        self.assertIn("dialect_error", out)

    def test_unknown_dialect_has_no_ops(self):
        out, _ = self.run_check("--dialect", "fish", "--", "rm -rf build")
        self.assertEqual(out["ops"], [])
        self.assertEqual(out["dialect_outcome"], "unusable")

    def test_advisory_mode_does_not_mutate(self):
        out, _ = self.run_check("--dialect", "powershell",
                                "--", "ri build -r -fo")
        self.assertEqual(out["decision"], "RELOCATE")
        self.assertTrue(os.path.isdir(os.path.join(self.root, "build")))


# --------------------------------------------- Gap 2: Claude hook end to end


@unittest.skipUnless(os.path.exists(CLAUDE_HOOK), "Claude adapter missing")
class ClaudeAdapterDialect(unittest.TestCase):
    """The hook must pass the dialect through; the prefilter must see it."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="agent-guard-hook2-")
        self.root = self._tmp.name
        ctx_for(self.root)
        subprocess.run(["git", "init", "-q", self.root], check=False)
        self.addCleanup(self._tmp.cleanup)

    def run_hook(self, payload, env=None):
        base_env = dict(os.environ)
        base_env.pop("AGENT_GUARD_DIALECT", None)
        base_env.pop("AGENT_GUARD_DEBUG", None)
        base_env["GIT_CONFIG_GLOBAL"] = "/dev/null"
        if env:
            base_env.update(env)
        return subprocess.run(
            [sys.executable, CLAUDE_HOOK], input=json.dumps(payload),
            capture_output=True, text=True, env=base_env, timeout=60)

    def payload(self, command, **extra):
        body = {"tool_name": "bash", "cwd": self.root,
                "tool_input": {"command": command}}
        body.update(extra)
        return body

    def test_payload_dialect_relocates_powershell_tree(self):
        proc = self.run_hook(self.payload("ri build -r -fo",
                                          dialect="powershell"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(os.path.isdir(os.path.join(self.root, ".agent-trash")))

    def test_dialect_from_environment(self):
        proc = self.run_hook(self.payload("ri build -r -fo"),
                             env={"AGENT_GUARD_DIALECT": "powershell"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(os.path.isdir(os.path.join(self.root, ".agent-trash")))

    def test_debug_output_reports_the_dialect(self):
        proc = self.run_hook(self.payload("ri build -r -fo",
                                          dialect="pwsh"),
                             env={"AGENT_GUARD_DEBUG": "1"})
        self.assertIn('"dialect": "powershell"', proc.stderr)

    def test_unknown_dialect_blocks(self):
        proc = self.run_hook(self.payload("rm -rf build", dialect="fish"))
        self.assertEqual(proc.returncode, 2)
        self.assertIn("BLOCK_DIALECT_UNKNOWN", proc.stderr)

    def test_posix_default_untouched(self):
        proc = self.run_hook(self.payload("rm -rf build"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(os.path.isdir(os.path.join(self.root, ".agent-trash")))

    def test_posix_prefilter_still_skips_noise(self):
        proc = self.run_hook(self.payload("dir /b"))
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")

    def test_windows_prefilter_not_used_for_posix(self):
        """A cmd-looking line under the POSIX dialect stays untouched."""
        proc = self.run_hook(self.payload("ri build -r -fo"))
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")
        self.assertFalse(os.path.isdir(
            os.path.join(self.root, ".agent-trash")))


if __name__ == "__main__":
    unittest.main()
