"""Installation preview and recovery, using owned Skill targets only."""
from contextlib import redirect_stdout
import errno
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import install_skill


class SkillInstallationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="owned-skill-install-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.target = self.directory / "host-skills/delete-guard"

    def old_skill(self):
        self.target.mkdir(parents=True)
        (self.target / "SKILL.md").write_text("Owned previous Skill.\n")
        (self.target / "owned.txt").write_bytes(b"preserve exactly")

    def test_preview_creates_nothing_and_same_install_is_idempotent(self):
        plan = install_skill.installation_plan(str(self.target))
        self.assertEqual(plan["skill"], "delete-guard")
        self.assertEqual(plan["action"], "link")
        self.assertEqual(
            set(plan["entrypoints"]),
            {"safe_delete", "cleanup", "delete_policy", "restore", "status"},
        )
        self.assertFalse(self.target.parent.exists())
        install_skill.execute_plan(plan)
        self.assertTrue(self.target.is_symlink())
        self.assertEqual(self.target.resolve(), install_skill.SKILL.resolve())
        same = install_skill.installation_plan(str(self.target))
        self.assertEqual(same["action"], "unchanged")
        before = self.target.lstat().st_mtime_ns
        install_skill.execute_plan(same)
        self.assertEqual(self.target.lstat().st_mtime_ns, before)

    def test_update_retains_previous_skill_outside_discovery(self):
        self.old_skill()
        plan = install_skill.installation_plan(str(self.target))
        install_skill.execute_plan(plan)
        backup = Path(plan["backup"])
        self.assertEqual(backup.parent, self.directory.resolve() / ".agent-guard-skill-backups")
        self.assertEqual((backup / "owned.txt").read_bytes(), b"preserve exactly")
        self.assertTrue(self.target.is_symlink())

    def test_update_through_parent_alias_retains_backup_outside_discovery(self):
        self.old_skill()
        alias = self.directory / "host-skills-alias"
        alias.symlink_to(self.target.parent, target_is_directory=True)
        plan = install_skill.installation_plan(str(alias / "delete-guard"))
        install_skill.execute_plan(plan)
        backup = Path(plan["backup"])
        self.assertEqual(backup.parent, self.directory.resolve() / ".agent-guard-skill-backups")
        self.assertFalse(backup.is_relative_to(alias.resolve()))
        self.assertEqual((backup / "owned.txt").read_bytes(), b"preserve exactly")
        self.assertEqual((alias / "delete-guard").resolve(), install_skill.SKILL.resolve())

    def test_link_creation_failure_restores_previous_target(self):
        self.old_skill()
        plan = install_skill.installation_plan(str(self.target))
        with mock.patch.object(Path, "symlink_to", side_effect=OSError(errno.EPERM, "fixture link refusal")):
            with self.assertRaises(OSError):
                install_skill.execute_plan(plan)
        self.assertFalse(self.target.is_symlink())
        self.assertEqual((self.target / "owned.txt").read_bytes(), b"preserve exactly")
        self.assertFalse(os.path.lexists(plan["backup"]))

    def test_unrelated_target_is_never_replaced(self):
        self.target.mkdir(parents=True)
        (self.target / "important.txt").write_bytes(b"unrelated")
        with self.assertRaisesRegex(ValueError, "not an existing Skill"):
            install_skill.installation_plan(str(self.target))
        self.assertEqual((self.target / "important.txt").read_bytes(), b"unrelated")

    def test_delete_guard_requires_delete_policy_script(self):
        self.assertIn("scripts/delete_policy.py", install_skill.REQUIRED)
        self.assertTrue((install_skill.SKILL / "scripts/delete_policy.py").is_file())

    def test_exfil_guard_link_resolves_core_and_detached_copy_explains_setup(self):
        exfil_target = self.directory / "host-skills/exfil-guard"
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = install_skill.main(["--skill", "exfil-guard", "--target", str(exfil_target)])
        self.assertEqual(code, 0)
        self.assertIn("--skill exfil-guard", stdout.getvalue())
        self.assertFalse(exfil_target.parent.exists())

        plan = install_skill.installation_plan(str(exfil_target), skill="exfil-guard")
        self.assertEqual(plan["skill"], "exfil-guard")
        self.assertEqual(set(plan["entrypoints"]),
                         {"check_span", "check_export", "check_bash_exfil", "sanitize", "view"})
        self.assertTrue({"scripts/check_export.py", "scripts/check_bash_exfil.py"}
                        <= set(install_skill.SKILLS["exfil-guard"]["required"]))
        install_skill.execute_plan(plan)
        self.assertTrue(exfil_target.is_symlink())

        (self.directory / "sample.txt").write_text("ordinary text\n", encoding="utf-8")
        env = {name: value for name, value in os.environ.items() if not name.startswith("AGENT_GUARD_")}
        for entry, args, payload in (
            ("check_span", ("--channel", "file-write"), "ordinary text\n"),
            ("check_export", ("--workspace", str(self.directory), "sample.txt"), ""),
            ("check_bash_exfil", ("--workspace", str(self.directory)), "git status\n"),
        ):
            with self.subTest(entry=entry):
                completed = subprocess.run(
                    [sys.executable, "-B", plan["entrypoints"][entry], "--json", *args],
                    input=payload, cwd=self.directory, env=env,
                    capture_output=True, text=True, timeout=30,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(json.loads(completed.stdout)["decision"], "ALLOW")

        detached = self.directory / "detached/exfil-guard"
        shutil.copytree(install_skill.ROOT / "skills/exfil-guard", detached)
        failed = subprocess.run(
            [sys.executable, "-B", str(detached / "scripts/check_span.py"), "--help"],
            cwd=self.directory,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("shared Core not found", failed.stderr)
        self.assertIn("--skill exfil-guard", failed.stderr)

    def test_recovery_audit_link_and_target_name_mismatch_refusal(self):
        recovery_target = self.directory / "host-skills/recovery-audit"
        plan = install_skill.installation_plan(str(recovery_target), skill="recovery-audit")
        self.assertEqual(plan["skill"], "recovery-audit")
        self.assertEqual(plan["entrypoints"], {})
        install_skill.execute_plan(plan)
        self.assertTrue(recovery_target.is_symlink())
        self.assertEqual(
            recovery_target.resolve(),
            (install_skill.ROOT / "skills/recovery-audit").resolve(),
        )
        with self.assertRaisesRegex(ValueError, "exfil-guard Skill directory"):
            install_skill.installation_plan(str(self.target), skill="exfil-guard")
        with self.assertRaisesRegex(ValueError, "unsupported Skill"):
            install_skill.installation_plan(str(self.target), skill="unknown-skill")


if __name__ == "__main__":
    unittest.main()
