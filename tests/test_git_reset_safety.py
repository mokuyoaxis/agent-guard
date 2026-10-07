"""Hard reset: refuse untracked/ignored collisions before compensation.

Every repository and payload here is synthetic and owned by RepoFixture.
Only an allowed, snapshotted reset is actually executed.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from core import policy
from core.recovery import RecoveryEngine
from tests.helpers import RepoFixture, git_available
from tests.test_delete import run


@unittest.skipUnless(git_available(), "git required")
class HardResetSafetyTests(RepoFixture):
    def git(self, *args):
        return subprocess.run(["git", "-C", self.root, *args],
                              capture_output=True, text=True, check=True)

    def old_target(self, name="collision", directory=False):
        path = name + "/old.txt" if directory else name
        self.write(path, "old committed version")
        self.git("add", "-f", "--", path)
        self.git("commit", "-qm", "old target")
        sha = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("rm", "-r", "--", name)
        self.git("commit", "-qm", "remove target")
        return sha

    def check(self, command, enforce=True, cwd=None, dialect=None):
        args = ["--json"]
        if enforce:
            args.append("--enforce")
        if dialect:
            args.extend(["--dialect", dialect])
        proc = run("check.py", *args, "--", command,
                   cwd=cwd or self.root)
        self.assertEqual(proc.returncode, 2 if enforce else 0, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertEqual(result["decision"], "BLOCK")
        self.assertEqual(result["explanation"],
                         policy.EXPLANATIONS[result["code"]])
        self.assertNotIn("compensations", result)
        return result

    def assert_collision(self, directory=False, dirty=False, ignored=False):
        name = "collision.log" if ignored else "collision"
        sha = self.old_target(name)
        origin = self.write(name + "/personal.txt" if directory else name,
                            "synthetic valuable content")
        if dirty:
            self.write("src/main.py", "synthetic tracked modification\n")
        before = Path(origin).read_bytes()
        head = self.git("rev-parse", "HEAD").stdout
        status = self.git("status", "--porcelain").stdout
        for enforce in (False, True):
            result = self.check("git reset --hard " + sha, enforce=enforce)
            self.assertEqual(result["code"],
                             policy.CODE_BLOCK_GIT_RESET_COLLISION)
            self.assertEqual(Path(origin).read_bytes(), before)
            self.assertEqual(self.git("rev-parse", "HEAD").stdout, head)
            self.assertEqual(self.git("status", "--porcelain").stdout, status)
        self.assertEqual(self.git("stash", "list").stdout, "")
        self.assertEqual(RecoveryEngine(self.root).transactions(), {})

    def test_untracked_file_clean(self):
        self.assert_collision()

    def test_untracked_file_dirty(self):
        self.assert_collision(dirty=True)

    def test_untracked_directory_clean(self):
        self.assert_collision(directory=True)

    def test_untracked_directory_dirty(self):
        self.assert_collision(directory=True, dirty=True)

    def test_ignored_file_clean(self):
        self.assert_collision(ignored=True)

    def test_ignored_file_dirty(self):
        self.assert_collision(ignored=True, dirty=True)

    def test_ignored_directory_clean(self):
        self.assert_collision(directory=True, ignored=True)

    def test_ignored_directory_dirty(self):
        self.assert_collision(directory=True, dirty=True, ignored=True)

    def test_file_obstructing_target_directory(self):
        sha = self.old_target(directory=True)
        self.write("collision", "new personal file")
        result = self.check("git reset --hard " + sha)
        self.assertEqual(result["code"], policy.CODE_BLOCK_GIT_RESET_COLLISION)
        self.assertEqual(Path(self.root, "collision").read_text(),
                         "new personal file")

    def test_untracked_content_inside_currently_tracked_directory(self):
        sha = self.old_target()
        self.write("collision/tracked.txt", "committed directory content")
        self.git("add", "collision/tracked.txt")
        self.git("commit", "-qm", "tracked directory")
        personal = self.write("collision/personal.log", "ignored valuable")
        result = self.check("git reset --hard " + sha)
        self.assertEqual(result["code"], policy.CODE_BLOCK_GIT_RESET_COLLISION)
        self.assertEqual(Path(personal).read_text(), "ignored valuable")
        self.assertEqual(Path(self.root, "collision/tracked.txt").read_text(),
                         "committed directory content")

    def test_symlink_obstructing_target_directory_keeps_its_target(self):
        sha = self.old_target(directory=True)
        target = self.write("personal.txt", "link target")
        link = Path(self.root, "collision")
        os.symlink(target, link)
        result = self.check("git reset --hard " + sha)
        self.assertEqual(result["code"], policy.CODE_BLOCK_GIT_RESET_COLLISION)
        self.assertTrue(link.is_symlink())
        self.assertEqual(Path(target).read_text(), "link target")

    def test_head_default_detects_untracked_file_after_staged_deletion(self):
        self.write("collision", "committed")
        self.git("add", "collision")
        self.git("commit", "-qm", "add target")
        self.git("rm", "--cached", "collision")
        self.write("collision", "personal untracked replacement")
        result = self.check("git reset --hard")
        self.assertEqual(result["code"], policy.CODE_BLOCK_GIT_RESET_COLLISION)
        self.assertEqual(Path(self.root, "collision").read_text(),
                         "personal untracked replacement")

    def test_invalid_revision_is_blocked_before_snapshot(self):
        self.write("src/main.py", "dirty\n")
        result = self.check("git reset --hard does-not-exist")
        self.assertEqual(result["code"], policy.CODE_BLOCK_UNDETERMINABLE_EFFECT)
        self.assertEqual(self.git("stash", "list").stdout, "")
        self.assertEqual(Path(self.root, "src/main.py").read_text(), "dirty\n")

    def test_earlier_command_cannot_create_a_late_collision(self):
        for command in ("touch collision && git reset --hard",
                        "git reset --soft HEAD~1 && git reset --hard",
                        "echo hello && git reset --hard",
                        "git reset --hard & touch collision",
                        "git reset --hard | tee collision"):
            with self.subTest(command=command):
                result = self.check(command)
                self.assertEqual(result["code"],
                                 policy.CODE_BLOCK_UNDETERMINABLE_EFFECT)

    def test_wrapper_cannot_change_the_context_after_preflight(self):
        for command in ("GIT_WORK_TREE=elsewhere git reset --hard",
                        "env -i git reset --hard", "sudo git reset --hard",
                        "env GIT_INDEX_FILE=other git reset --hard"):
            with self.subTest(command=command):
                result = self.check(command)
                self.assertEqual(result["code"],
                                 policy.CODE_BLOCK_UNDETERMINABLE_EFFECT)

    def test_later_reset_blocks_before_an_earlier_relocation(self):
        sha = self.old_target()
        self.write("collision", "personal")
        self.write("keep.txt", "also personal")
        self.check("rm keep.txt && git reset --hard " + sha)
        self.assertEqual(Path(self.root, "keep.txt").read_text(),
                         "also personal")

    def test_target_cannot_replace_quarantine_before_layout_exists(self):
        sha = self.old_target(".agent-trash")
        self.assertFalse(Path(self.root, ".agent-trash").exists())
        result = self.check("git reset --hard " + sha)
        self.assertEqual(result["code"], policy.CODE_BLOCK_GIT_RESET_COLLISION)
        self.assertEqual(RecoveryEngine(self.root).transactions(), {})

    def test_target_cannot_write_inside_quarantine(self):
        self.write(".agent-trash/user.txt", "committed old storage")
        self.git("add", "-f", ".agent-trash/user.txt")
        self.git("commit", "-qm", "old storage")
        result = self.check("git reset --hard")
        self.assertEqual(result["code"], policy.CODE_BLOCK_GIT_RESET_COLLISION)

    def test_target_cannot_replace_tracked_link_to_external_quarantine(self):
        sha = self.old_target(".agent-trash", directory=True)
        with tempfile.TemporaryDirectory(prefix="guard-test-external-trash-") as trash:
            link = Path(self.root, ".agent-trash")
            os.symlink(trash, link, target_is_directory=True)
            self.git("add", "-f", ".agent-trash")
            self.git("commit", "-qm", "linked storage")
            result = self.check("git reset --hard " + sha)
            self.assertEqual(result["code"], policy.CODE_BLOCK_GIT_RESET_COLLISION)
            self.assertTrue(link.is_symlink())
            self.assertEqual(RecoveryEngine(self.root).transactions(), {})

    def test_collision_from_subdirectory_and_all_dialects(self):
        sha = self.old_target()
        self.write("collision", "personal")
        for dialect in ("posix", "cmd", "powershell"):
            with self.subTest(dialect=dialect):
                result = self.check("git reset --hard " + sha,
                                    cwd=str(Path(self.root, "src")),
                                    dialect=dialect)
                self.assertEqual(result["code"],
                                 policy.CODE_BLOCK_GIT_RESET_COLLISION if
                                 dialect == "posix" else
                                 policy.CODE_BLOCK_UNDETERMINABLE_EFFECT)

    def test_case_insensitive_git_names_are_compared_conservatively(self):
        sha = self.old_target("Collision")
        self.write("collision", "personal")
        self.git("config", "core.ignorecase", "true")
        result = self.check("git reset --hard " + sha)
        self.assertEqual(result["code"], policy.CODE_BLOCK_GIT_RESET_COLLISION)

    def test_nested_repository_obstructing_target_file_is_kept(self):
        sha = self.old_target()
        nested = Path(self.root, "collision")
        nested.mkdir()
        subprocess.run(["git", "-C", str(nested), "init", "-q"],
                       check=True, capture_output=True)
        self.write("collision/personal.txt", "nested repository work")
        result = self.check("git reset --hard " + sha)
        self.assertEqual(result["code"], policy.CODE_BLOCK_GIT_RESET_COLLISION)
        self.assertTrue((nested / ".git").is_dir())
        self.assertEqual((nested / "personal.txt").read_text(),
                         "nested repository work")

    def test_stash_relative_targets_cannot_change_during_compensation(self):
        for target in ("stash", "stash@{0}", "refs/stash", "stash~1"):
            with self.subTest(target=target):
                result = self.check("git reset --hard '" + target + "'")
                self.assertEqual(result["code"],
                                 policy.CODE_BLOCK_UNDETERMINABLE_EFFECT)

    def test_no_collision_keeps_untracked_and_restores_tracked_content(self):
        personal = self.write("unrelated/personal.txt", "untracked")
        ignored = self.write("unrelated/personal.log", "ignored")
        main = self.write("src/main.py", "precious tracked modification\n")
        proc = run("check.py", "--enforce", "--json", "--",
                   "git reset --hard", cwd=self.root)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertEqual(result["decision"], "ALLOW")
        txid = result["compensations"][0]["txid"]
        engine = RecoveryEngine(self.root)
        self.assertEqual(engine.transactions()[txid]["state"], "RESTORABLE")
        self.assertEqual(Path(main).read_text(), "precious tracked modification\n")
        self.git("reset", "--hard")
        self.assertEqual(Path(main).read_text(), "print('base')\n")
        restored = engine.restore(txid)
        self.assertTrue(restored["ok"], restored)
        self.assertEqual(Path(main).read_text(), "precious tracked modification\n")
        self.assertEqual(Path(personal).read_text(), "untracked")
        self.assertEqual(Path(ignored).read_text(), "ignored")

    def test_clean_no_collision_can_reset_to_other_commit(self):
        self.write("new.txt", "committed")
        self.git("add", "new.txt")
        self.git("commit", "-qm", "new version")
        self.write("personal.log", "ignored")
        proc = run("check.py", "--enforce", "--json", "--",
                   "git reset --hard -q HEAD~1", cwd=self.root)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertEqual(result["decision"], "ALLOW")
        self.assertIsNone(result["compensations"][0]["sha"])
        # Synthetic clean tracked data is retained in the committed history.
        self.git("reset", "--hard", "-q", "HEAD~1")
        self.assertEqual(Path(self.root, "personal.log").read_text(), "ignored")

    @unittest.skipIf(sys.platform == "win32", "NTFS forbids newline filenames")
    def test_nul_paths_preserve_newlines_unicode_and_quotes(self):
        name = '笔记\n"collision'
        sha = self.old_target(name)
        self.write(name, "private_marker_only_in_filename_and_file")
        result = self.check('git reset --hard ' + sha)
        self.assertEqual(result["code"], policy.CODE_BLOCK_GIT_RESET_COLLISION)
        self.assertEqual(Path(self.root, name).read_text(),
                         "private_marker_only_in_filename_and_file")

    def test_failure_to_enumerate_blocks_without_snapshot(self):
        engine = RecoveryEngine(self.root)
        for failure in (PermissionError("denied"),
                        subprocess.TimeoutExpired(["git"], 30)):
            with self.subTest(failure=type(failure).__name__):
                with mock.patch("core.recovery.subprocess.run",
                                side_effect=failure):
                    paths, error = engine.preflight_git_reset_hard()
                self.assertEqual(paths, [])
                self.assertTrue(error)
        self.assertEqual(engine.transactions(), {})

    def test_success_exit_with_warning_is_not_complete_enumeration(self):
        warned = subprocess.CompletedProcess(
            ["git"], 0, stdout=os.fsencode(self.root + "\n"),
            stderr=b"warning: could not open a directory: Permission denied")
        with mock.patch("core.recovery.subprocess.run", return_value=warned):
            paths, error = RecoveryEngine(self.root).preflight_git_reset_hard()
        self.assertEqual(paths, [])
        self.assertTrue(error)

    def test_workspace_must_cover_the_whole_repository(self):
        engine = RecoveryEngine(str(Path(self.root, "src")))
        paths, error = engine.preflight_git_reset_hard()
        self.assertEqual(paths, [])
        self.assertIn("workspace", error)

    def test_submodule_index_is_outside_tracked_stash_coverage(self):
        sha = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("update-index", "--add", "--cacheinfo", "160000," + sha +
                 ",module")
        paths, error = RecoveryEngine(self.root).preflight_git_reset_hard()
        self.assertEqual(paths, [])
        self.assertIn("Submodule", error)


if __name__ == "__main__":
    unittest.main()
