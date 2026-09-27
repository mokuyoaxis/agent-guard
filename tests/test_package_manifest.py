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
        self.assertEqual(self.manifest["version"], "0.2.2")
        self.assertNotIn("private", self.manifest)
        self.assertEqual(
            self.manifest.get("publishConfig"), {"access": "public"})

    def test_universal_assets_are_in_package_allowlist(self):
        files = set(self.manifest["files"])
        self.assertTrue({
            "adapters", "core", "skills", "docs", "doctor.py",
            "live_sentinel.py", "README.md", "README.zh-CN.md", "LICENSE",
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

    def test_dsh_bundle_uses_the_scoped_package_name(self):
        dsh = self.manifest["dsh"]
        self.assertEqual(dsh["engines"]["dsh"], "0.1.5-rc.1")
        patch_path = ROOT / dsh["bundle"]["patch"]
        patch = patch_path.read_text(encoding="utf-8")
        self.assertIn("name: '@mokuyoaxis/agent-guard'", patch)
        adapter = (ROOT / "adapters/dsh/lib/index.js").read_text(
            encoding="utf-8")
        self.assertNotIn('from "@deepseek-ai/dsh-tools"', adapter)


if __name__ == "__main__":
    unittest.main()
