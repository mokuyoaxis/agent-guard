"""Classifier facts and policy verdicts (pure logic, no mutation)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.helpers import RepoFixture, git_available

from core import classifier
from core.classifier import (
    KIND_FS_DELETE, KIND_GIT_CLEAN, KIND_GIT_DISCARD, KIND_GIT_PUSH_FORCE,
    KIND_GIT_RESET_HARD, KIND_UNKNOWN, classify_command, classify_paths,
)
from core.policy import (
    CODE_ASK_COMPOUND_CREATE_DELETE, CODE_ASK_COMPOUND_CWD_DELETE,
    CODE_BLOCK_OUT_OF_WORKSPACE, CODE_BLOCK_PROTECTED_PATH,
    CODE_BLOCK_RESTRICTED_MODE, CODE_BLOCK_UNDETERMINABLE_EFFECT,
    CODE_BLOCK_WILDCARD, CODE_SNAPSHOT_GIT_STASH, DECISION_ASK,
    DECISION_ALLOW, DECISION_BLOCK, DECISION_SNAPSHOT, MODE_NORMAL,
    MODE_RESTRICTED, PolicyContext, decide_ops,
)


def one(specs):
    assert len(specs) == 1, f"expected 1 op, got {specs}"
    return specs[0]


def verdict_for(cmd, root, mode=MODE_NORMAL):
    from core.classifier import discover_workspace
    ctx = PolicyContext(
        workspace=root, trash_root=os.path.join(root, ".agent-trash"),
        base_dir=root, mode=mode)
    specs, err = classify_command(cmd)
    vs = decide_ops(specs, ctx)
    return vs[0] if vs else None


class ClassifierFacts(unittest.TestCase):
    def test_rm_combined_flags(self):
        spec = one(classify_command("rm -rf build")[0])
        self.assertEqual(spec.kind, KIND_FS_DELETE)
        self.assertTrue(spec.recursive and spec.force)

    def test_wildcard_detected(self):
        spec = one(classify_command("rm *.log")[0])
        self.assertTrue(spec.wildcard)

    def test_variable_undeterminable(self):
        spec = one(classify_command("rm -rf $DIR")[0])
        self.assertTrue(spec.undeterminable)

    def test_indirect_shell_smell(self):
        spec = one(classify_command('bash -c "rm -rf /tmp/x"')[0])
        self.assertEqual(spec.kind, KIND_UNKNOWN)

    def test_common_wrappers_do_not_hide_delete(self):
        commands = (
            "env FLAG=1 rm -rf .",
            "FLAG=1 rm -rf .",
            "sudo -u root rm -rf .",
            "nice -n 5 rm -rf .",
            "nice -5 rm -rf .",
            "command -- rm -rf .",
            "exec rm -rf .",
            "stdbuf -o0 rm -rf .",
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(one(classify_command(command)[0]).kind,
                                 KIND_FS_DELETE)

    def test_xargs_options_keep_stdin_effect_undeterminable(self):
        for command in ("xargs -0 rm -rf", "xargs -I{} rm -rf {}"):
            with self.subTest(command=command):
                spec = one(classify_command(command)[0])
                self.assertEqual(spec.kind, KIND_FS_DELETE)
                self.assertTrue(spec.undeterminable)

    def test_substitution_and_stdin_script_fail_closed(self):
        for command in ('echo "$(rm -rf .)"',
                        "printf 'rm -rf .' | sh"):
            with self.subTest(command=command):
                spec = one(classify_command(command)[0])
                self.assertEqual(spec.kind, KIND_UNKNOWN)
                self.assertTrue(spec.undeterminable)
        self.assertEqual(classify_command("echo '$(rm -rf .)'")[0], [])

    def test_env_split_string_is_opaque(self):
        spec = one(classify_command("env -S 'rm -rf .'")[0])
        self.assertEqual(spec.kind, KIND_UNKNOWN)
        self.assertTrue(spec.undeterminable)

    def test_wrapper_terminal_options_do_not_trigger_compensation(self):
        for command in ("env --help rm", "sudo --help rm", "sudo -e rm",
                        "nice --version rm", "nohup --help rm",
                        "time --version rm", "stdbuf --help rm",
                        "xargs --show-limits rm"):
            with self.subTest(command=command):
                self.assertEqual(classify_command(command)[0], [])

    def test_find_delete(self):
        spec = one(classify_command("find . -name '*.pyc' -delete")[0])
        self.assertEqual(spec.kind, KIND_FS_DELETE)
        self.assertTrue(spec.undeterminable)

    def test_git_clean_dry_run_vs_force(self):
        self.assertTrue(one(classify_command("git clean -nd")[0]).dry_run)
        forced = one(classify_command("git clean -fdx")[0])
        self.assertTrue(forced.force)
        self.assertEqual(sorted(forced.extra_flags), ["-d", "-x"])

    def test_git_clean_double_force_is_undeterminable(self):
        specs, err = classify_command("git clean -ffd")
        self.assertIsNone(err)
        self.assertTrue(specs[0].undeterminable)
        self.assertIn("nested repositories", " ".join(specs[0].notes))

    def test_git_clean_interactive_and_excludes_are_undeterminable(self):
        for cmd in ("git clean -ifd", "git clean -fd -e keep*",
                    "git clean -fd --exclude=keep*"):
            with self.subTest(cmd=cmd):
                specs, err = classify_command(cmd)
                self.assertIsNone(err)
                self.assertTrue(specs[0].undeterminable)

    def test_git_reset_hard(self):
        self.assertEqual(one(classify_command("git reset --hard")[0]).kind,
                         KIND_GIT_RESET_HARD)

    def test_git_global_options_cannot_hide_reset(self):
        changed_context = one(classify_command(
            "git -C . reset --hard")[0])
        self.assertEqual(changed_context.kind, KIND_GIT_RESET_HARD)
        self.assertTrue(changed_context.undeterminable)
        harmless = one(classify_command(
            "git --no-pager reset --hard")[0])
        self.assertEqual(harmless.kind, KIND_GIT_RESET_HARD)
        self.assertFalse(harmless.undeterminable)

    def test_prefilter_sees_wrapped_and_quoted_destructive_forms(self):
        for command in ("git -C . reset --hard",
                        "printf 'rm -rf .' | sh",
                        "env FLAG=1 rm -rf ."):
            with self.subTest(command=command):
                self.assertIsNotNone(
                    classifier.DESTRUCTIVE_PREFILTER_RE.search(command))

    def test_restore_staged_only_not_destructive(self):
        specs, _ = classify_command("git restore --staged src/main.py")
        self.assertEqual(specs, [])

    def test_checkout_dd_is_discard(self):
        spec = one(classify_command("git checkout -- src/main.py")[0])
        self.assertEqual(spec.kind, KIND_GIT_DISCARD)

    def test_push_force_variants(self):
        for cmd in ("git push --force origin main",
                    "git push -f origin main",
                    "git push origin +main",
                    "git push origin :feature/x",
                    "git push --mirror"):
            self.assertEqual(one(classify_command(cmd)[0]).kind,
                             KIND_GIT_PUSH_FORCE, cmd)

    def test_benign_command_yields_nothing(self):
        self.assertEqual(classify_command("ls -la && echo hi")[0], [])

    def test_f1_cd_before_destructive_marks_shape(self):
        spec = one(classify_command("cd sub && rm -rf build")[0])
        self.assertEqual(spec.shape, "F1")
        self.assertFalse(spec.undeterminable)  # effect IS determinable

    def test_f1_destructive_before_cd_is_fine(self):
        spec = one(classify_command("rm -rf build && cd sub")[0])
        self.assertIsNone(spec.shape)

    def test_f1_applies_to_git_clean(self):
        spec = one(classify_command("cd sub && git clean -fd")[0])
        self.assertEqual(spec.shape, "F1")

    def test_f2_create_then_delete_marks_shape(self):
        spec = one(classify_command("touch a.tmp && rm a.tmp")[0])
        self.assertEqual(spec.shape, "F2")

    def test_f2_redirect_counts_as_creation(self):
        spec = one(classify_command("echo x > f.txt && rm f.txt")[0])
        self.assertEqual(spec.shape, "F2")

    def test_f2_delete_then_create_is_fine(self):
        spec = one(classify_command("rm -rf build && mkdir build")[0])
        self.assertIsNone(spec.shape)

    def test_f2_exempts_position_independent_kinds(self):
        spec = one(classify_command("touch f && git reset --hard")[0])
        self.assertIsNone(spec.shape)

    def test_subshell_parens_do_not_hide_operations(self):
        spec = one(classify_command("(rm -rf build)")[0])
        self.assertEqual(spec.kind, KIND_FS_DELETE)

    def test_f8_discover_workspace_resolves_symlinks(self):
        # macOS /var -> /private/var: an unresolved boundary root never
        # matches the physical cwd children report.
        import os
        import tempfile
        real = tempfile.mkdtemp(prefix="ag-f8-real-")
        os.makedirs(os.path.join(real, ".git"))
        link = os.path.join(tempfile.gettempdir(), f"ag-f8-link-{os.getpid()}")
        if os.path.islink(link):
            os.unlink(link)
        os.symlink(real, link)
        try:
            found = classifier.discover_workspace(link)
            self.assertEqual(found, os.path.realpath(real))
        finally:
            os.unlink(link)

    def test_f7_long_body_cut_must_not_damage_trailing_command(self):
        # friction F7: double-offset cut landed inside the trailing command
        # when the heredoc body was long, manufacturing unbalanced quotes.
        body_lines = "\n".join(f"line {i} of a fairly long patch body" for i in range(12))
        cmd = ("cd /workspace && python3 - <<'PYEOF'\n" + body_lines +
               "\nPYEOF\n"
               "git commit -qm \"ci: upload test logs as artifacts\" && "
               "export VAR='ssh -4 -i /somewhere/keys -o IdentitiesOnly=yes'")
        stripped = classifier.strip_heredocs(cmd)
        import shlex as _shlex
        _shlex.split(stripped, posix=True)  # must not raise
        self.assertIn("ci: upload test logs as artifacts", stripped)
        self.assertIn("IdentitiesOnly=yes", stripped)
        specs, err = classify_command(cmd)
        self.assertEqual((specs, err), ([], None))

    def test_heredoc_operator_cannot_rematch_and_trailing_lines_survive(self):
        # friction F6: operator retention caused an infinite re-match, and
        # the old truncating fallback cut a trailing sed line mid-quote.
        cmd = ("cat > README.zh-CN.md <<'MDEOF'\n"
               'body with "quotes" and (parens)\n'
               "MDEOF\n"
               "sed -i 's/old.name/new value/' README.md\n"
               'grep -rn "3\\.8" . || echo none')
        specs, err = classify_command(cmd)
        self.assertEqual((specs, err), ([], None))

    def test_two_heredocs_both_stripped(self):
        cmd = ("cat > a.md <<'E1'\nrm -rf inside-a\nE1\n"
               "cat > b.md <<'E2'\ngit clean -fd inside-b\nE2\n"
               "echo done")
        specs, err = classify_command(cmd)
        self.assertEqual((specs, err), ([], None))

    def test_unterminated_heredoc_keeps_later_lines(self):
        cmd = "cat > f.md <<'EOF'\nunterminated body rm -rf x\n"
        specs, err = classify_command(cmd)
        self.assertEqual((specs, err), ([], None))

    def test_heredoc_body_is_payload_not_syntax(self):
        # friction.md F5: quoted destructive text inside a heredoc is file
        # content, not an executed command.
        cmd = "cat > notes.md <<'EOF'\n" + \
              "run rm -rf build to clean\n" + \
              "and git clean -fd\n" + \
              "EOF\necho done"
        specs, err = classify_command(cmd)
        self.assertEqual((specs, err), ([], None))


class PolicyVerdicts(RepoFixture):
    def test_out_of_workspace_blocked(self):
        v = verdict_for("rm /etc/passwd", self.root)
        self.assertEqual((v.decision, v.code),
                         (DECISION_BLOCK, CODE_BLOCK_OUT_OF_WORKSPACE))

    def test_workspace_root_blocked(self):
        v = verdict_for("rm -rf .", self.root)
        self.assertEqual(v.code, CODE_BLOCK_PROTECTED_PATH)

    def test_git_metadata_blocked(self):
        v = verdict_for("rm -rf .git", self.root)
        self.assertEqual(v.code, CODE_BLOCK_PROTECTED_PATH)

    def test_wildcard_blocked(self):
        v = verdict_for("rm *.log", self.root)
        self.assertEqual(v.code, CODE_BLOCK_WILDCARD)

    def test_variable_blocked(self):
        v = verdict_for("rm -rf $UNSET_DIR/", self.root)
        self.assertEqual(v.code, CODE_BLOCK_UNDETERMINABLE_EFFECT)

    def test_rooted_recursion_relocates(self):
        self.write("build/cache/o.js")
        v = verdict_for("rm -rf build", self.root)
        self.assertEqual(v.decision, "RELOCATE")

    @unittest.skipUnless(git_available(), "git required")
    def test_regenerable_allowed(self):
        os.makedirs(os.path.join(self.root, "node_modules"), exist_ok=True)
        self.write("node_modules/pkg/index.js")
        v = verdict_for("rm -rf node_modules", self.root)
        self.assertEqual(v.decision, DECISION_ALLOW)

    def test_restricted_narrow_file_ok(self):
        path = self.write("notes.txt")
        # The command is parsed as a POSIX shell would see it: on Windows
        # fixtures that means forward slashes (an unquoted backslash is an
        # escape character in the shell, so a backslashed path would name a
        # different, nonexistent file for both the shell and the classifier).
        path = path.replace(os.sep, "/")
        v = verdict_for(f"rm {path}", self.root, mode=MODE_RESTRICTED)
        self.assertEqual(v.decision, "RELOCATE")

    def test_restricted_recursion_blocked(self):
        self.write("dir/inner.txt")
        v = verdict_for("rm -rf dir", self.root, mode=MODE_RESTRICTED)
        self.assertEqual((v.decision, v.code),
                         (DECISION_BLOCK, CODE_BLOCK_RESTRICTED_MODE))

    def test_restricted_git_blocked(self):
        v = verdict_for("git reset --hard", self.root, mode=MODE_RESTRICTED)
        self.assertEqual(v.code, CODE_BLOCK_RESTRICTED_MODE)

    def test_f1_asks_once_with_compound_code(self):
        v = verdict_for("cd sub && rm -rf build", self.root)
        self.assertEqual((v.decision, v.code),
                         (DECISION_ASK, CODE_ASK_COMPOUND_CWD_DELETE))

    def test_f2_asks_once_with_compound_code(self):
        self.write("a.tmp")
        v = verdict_for("touch a.tmp && rm a.tmp", self.root)
        self.assertEqual((v.decision, v.code),
                         (DECISION_ASK, CODE_ASK_COMPOUND_CREATE_DELETE))

    def test_reset_hard_snapshots_despite_creation_earlier(self):
        v = verdict_for("touch f && git reset --hard", self.root)
        self.assertEqual((v.decision, v.code),
                         (DECISION_SNAPSHOT, CODE_SNAPSHOT_GIT_STASH))

    def test_ask_degrades_to_block_in_restricted_mode(self):
        # A vetoed capability cannot escalate to the human either.
        v = verdict_for("cd sub && rm -rf build", self.root,
                        mode=MODE_RESTRICTED)
        self.assertEqual((v.decision, v.code),
                         (DECISION_BLOCK, CODE_BLOCK_RESTRICTED_MODE))

    def test_explanations_present_on_all_verdicts(self):
        for cmd in ("rm -rf .", "rm *.log", "rm -rf $V/", "cd x && rm y",
                    "git push --force origin main"):
            v = verdict_for(cmd, self.root)
            self.assertTrue(v.explanation, cmd)


if __name__ == "__main__":
    unittest.main()
