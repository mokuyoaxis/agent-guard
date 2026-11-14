"""Installation preview and recovery, using owned Skill targets only."""
import errno
import os
from pathlib import Path
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
        self.assertEqual(plan["action"], "link")
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
        self.assertEqual(backup.parent, self.directory / ".agent-guard-skill-backups")
        self.assertEqual((backup / "owned.txt").read_bytes(), b"preserve exactly")
        self.assertTrue(self.target.is_symlink())

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


if __name__ == "__main__":
    unittest.main()
