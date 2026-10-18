"""Release-channel routing keeps RC and hotfix streams off npm latest."""
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
                        "01.2.3", "0.02.3", "0.2.03", "0.2.3-rc01",
                        "0.2.4-hotfix", "0.2.4-hotfix1", "0.2.4-hotfix.01",
                        "0.02.4-hotfix.1", "0.2.4-hotfix.1.extra",
                        "0.2.4+hotfix.1", "0.2.4-hotfix.1\nNPM_DIST_TAG=latest"):
            with self.subTest(version=version):
                with self.assertRaises(ValueError):
                    release_channel.release_channel(version)

    def test_hotfix_routes_to_its_own_prerelease_channel(self):
        for version in ("0.2.4-hotfix.1", "0.2.4-hotfix.2"):
            with self.subTest(version=version):
                self.assertEqual(release_channel.release_channel(version), {
                    "RELEASE_KIND": "hotfix",
                    "RELEASE_PRERELEASE": "true",
                    "NPM_DIST_TAG": "hotfix",
                })

    def test_cli_output_is_safe_for_github_env(self):
        for version, kind, prerelease, tag in (
            ("0.2.4", "stable", "false", "latest"),
            ("0.2.5-rc3", "prerelease", "true", "rc"),
            ("0.2.4-hotfix.1", "hotfix", "true", "hotfix"),
        ):
            with self.subTest(version=version):
                proc = subprocess.run(
                    [sys.executable, str(SCRIPT), version],
                    text=True, capture_output=True, check=False,
                )
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(set(proc.stdout.splitlines()), {
                    "RELEASE_KIND=" + kind,
                    "RELEASE_PRERELEASE=" + prerelease,
                    "NPM_DIST_TAG=" + tag,
                })


if __name__ == "__main__":
    unittest.main()
