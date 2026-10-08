import json
import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PackageManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(
            (ROOT / "package.json").read_text(encoding="utf-8"))

    def test_scoped_public_candidate_identity(self):
        self.assertEqual(self.manifest["name"], "@mokuyoaxis/agent-guard")
        self.assertEqual(self.manifest["version"], "0.2.5-rc1")
        self.assertNotIn("private", self.manifest)
        self.assertEqual(
            self.manifest.get("publishConfig"), {"access": "public"})

    def test_universal_assets_are_in_package_allowlist(self):
        files = set(self.manifest["files"])
        self.assertTrue({
            "adapters", "core", "skills", "docs", "doctor.py",
            "live_sentinel.py", "guard_lab.py", "README.md",
            "README.zh-CN.md", "LICENSE", "CONTRIBUTING.md",
        }.issubset(files))
        self.assertNotIn(".internal", files)

    def test_every_bin_target_is_present_and_executable(self):
        for command, relative in self.manifest["bin"].items():
            with self.subTest(command=command):
                target = ROOT / relative
                self.assertTrue(target.is_file())
                self.assertTrue(os.access(target, os.X_OK))
                self.assertEqual(
                    target.read_text(encoding="utf-8").splitlines()[0],
                    "#!/usr/bin/env python3",
                )

    def test_live_sentinel_bin_is_not_a_silent_noop(self):
        completed = subprocess.run(
            [sys.executable, str(ROOT / "live_sentinel.py"), "--help"],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("{kimi,claude}", completed.stdout)
        self.assertIn("Local agent-guard installation checks", completed.stdout)

    def test_guard_lab_bin_exposes_only_the_user_controller(self):
        self.assertEqual(
            self.manifest["bin"]["agent-guard-lab"], "./guard_lab.py")
        completed = subprocess.run(
            [sys.executable, str(ROOT / "guard_lab.py"), "--help"],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn(
            "prepare,arm,stop,record-host,scan,report,run,compare",
            completed.stdout,
        )
        self.assertNotIn("_observe", completed.stdout)

    def test_dsh_bundle_uses_the_scoped_package_name(self):
        dsh = self.manifest["dsh"]
        self.assertEqual(dsh["engines"]["dsh"], "0.1.5-rc.1 || 0.2.0-rc.2")
        patch_path = ROOT / dsh["bundle"]["patch"]
        patch = patch_path.read_text(encoding="utf-8")
        self.assertIn("name: '@mokuyoaxis/agent-guard'", patch)
        adapter = (ROOT / "adapters/dsh/lib/index.js").read_text(
            encoding="utf-8")
        self.assertNotIn('from "@deepseek-ai/dsh-tools"', adapter)

    def test_release_workflow_is_tag_gated_and_scoped(self):
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8")
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("tags:", workflow)
        self.assertIn("-rc[0-9]+", workflow)
        self.assertIn("@mokuyoaxis/agent-guard", workflow)
        self.assertIn("secrets.NPM_TOKEN", workflow)
        self.assertIn("npm whoami", workflow)
        self.assertIn('npm publish "$TARBALL"', workflow)
        self.assertIn("--access public", workflow)
        self.assertIn('--tag "$NPM_DIST_TAG"', workflow)
        self.assertIn("--provenance", workflow)
        self.assertIn("--prerelease", workflow)
        self.assertIn("release_channel.py", workflow)
        self.assertNotIn("pull_request:", workflow)

    def test_windows_core_rc_job_is_a_real_windows_gate(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8")
        self.assertIn("windows-core-rc:", workflow)
        self.assertIn("runs-on: windows-latest", workflow)
        self.assertIn("test_relocate_then_restore_roundtrip", workflow)
        self.assertIn("test_cmd_dialect_cannot_turn_rm_root_delete_into_noop",
                      workflow)
        self.assertNotIn("printf 'home=C:", workflow)


if __name__ == "__main__":
    unittest.main()
