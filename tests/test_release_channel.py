"""Release-channel routing stays strict and keeps RCs off npm latest."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "release_channel.py"
SPEC = importlib.util.spec_from_file_location("release_channel", SCRIPT)
assert SPEC and SPEC.loader
release_channel = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release_channel)


class ReleaseChannelTests(unittest.TestCase):
    def test_stable_routes_to_latest(self):
        self.assertEqual(release_channel.release_channel("0.2.3"), {
            "RELEASE_KIND": "stable",
            "RELEASE_PRERELEASE": "false",
            "NPM_DIST_TAG": "latest",
        })

    def test_rc_routes_to_prerelease_tag(self):
        self.assertEqual(release_channel.release_channel("0.2.3-rc1"), {
            "RELEASE_KIND": "prerelease",
            "RELEASE_PRERELEASE": "true",
            "NPM_DIST_TAG": "rc",
        })

    def test_non_project_tag_shapes_are_rejected(self):
        for version in ("v0.2.3", "0.2.3-rc.1", "0.2.3-beta1",
                        "01.2.3", "0.02.3", "0.2.03", "0.2.3-rc01"):
            with self.subTest(version=version):
                with self.assertRaises(ValueError):
                    release_channel.release_channel(version)

    def test_cli_output_is_safe_for_github_env(self):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "0.2.3-rc1"],
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(set(proc.stdout.splitlines()), {
            "RELEASE_KIND=prerelease",
            "RELEASE_PRERELEASE=true",
            "NPM_DIST_TAG=rc",
        })


if __name__ == "__main__":
    unittest.main()
