"""A successful publish command is independent of observable artifact correctness."""
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".github/scripts"
with patch.object(sys, "path", [str(SCRIPTS), *sys.path]):
    SPEC = importlib.util.spec_from_file_location("guard_verify_release", SCRIPTS / "verify_release.py")
    release = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(release)


class Clock:
    value = 0.0

    def __call__(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


class FixtureSources:
    def __init__(self, expected):
        self.calls = []
        self.pending = []
        self.tarball_bytes = expected["payload"]
        version, package, filename = expected["version"], expected["package"], expected["filename"]
        self.metadata = {"dist-tags": {expected["npm_tag"]: version}, "versions": {version: {
            "name": package, "version": version, "dist": {
                "shasum": expected["sha1"], "integrity": expected["integrity"],
                "tarball": release.REGISTRY + package + "/-/agent-guard-" + version + ".tgz"}}}}
        checksum = f"{expected['sha256']}  {filename}\n".encode()
        self.assets = {1: expected["payload"], 2: checksum}
        self.endpoints = {
            "releases/tags/" + version: {"tag_name": version, "draft": False,
                "prerelease": expected["prerelease"], "body": expected["notes"], "assets": [
                    {"id": i, "name": filename + (".sha256" if i == 2 else ""),
                     "size": len(body), "digest": "sha256:" + hashlib.sha256(body).hexdigest()}
                    for i, body in self.assets.items()]},
            "git/ref/tags/" + version: {"object": {"type": "tag", "sha": "f" * 40}},
            "git/tags/" + "f" * 40: {"object": {"type": "commit", "sha": expected["commit"]}}}

    def registry(self, package):
        self.calls.append("registry")
        if self.pending:
            event = self.pending.pop(0)
            if isinstance(event, Exception):
                raise event
            return event
        return copy.deepcopy(self.metadata)

    def github(self, endpoint):
        self.calls.append(endpoint)
        return copy.deepcopy(self.endpoints[endpoint])

    def asset(self, asset_id):
        self.calls.append("asset")
        return self.assets[asset_id]

    def tarball(self, url, size):
        self.calls.append("tarball")
        return self.tarball_bytes


class ReleaseVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def fixture(self, version="0.3.0"):
        tarball = self.root / (version + ".tgz")
        manifest = json.dumps({"name": "@mokuyoaxis/agent-guard", "version": version}).encode()
        with tarfile.open(tarball, "w:gz") as archive:
            entry = tarfile.TarInfo("package/package.json")
            entry.size = len(manifest)
            archive.addfile(entry, io.BytesIO(manifest))
        notes = self.root / "notes.md"
        notes.write_text("# Fixed notes\n")
        expected = release.expected_release(tarball, notes, "owner/project", version, "a" * 40)
        return expected, FixtureSources(expected)

    def verify(self, expected, sources, timeout=3):
        output = self.root / "evidence"
        output.mkdir()
        clock = Clock()
        with patch("builtins.print"):
            result = release.verify(expected, sources, output, timeout=timeout, interval=2,
                                    clock=clock, sleep=clock.sleep)
        self.assertEqual(json.loads((output / "report.json").read_text()), result)
        return result

    def test_matching_channel_archives_tag_and_notes_pass(self):
        expected, sources = self.fixture()
        report = self.verify(expected, sources)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(len(report["checks"]), 7)
        self.assertEqual(len(report["attempts"]), 1)
        self.assertIn("registry_signature", report["unverified"])

    def test_rc_and_hotfix_use_their_own_channels(self):
        for version, tag in (("0.3.1-rc1", "rc"), ("0.3.0-hotfix.1", "hotfix")):
            with self.subTest(version=version):
                expected, sources = self.fixture(version)
                directory = self.root / tag
                directory.mkdir()
                checks = {}
                release.verify_once(expected, sources, directory, checks)
                self.assertEqual(expected["npm_tag"], tag)
                self.assertTrue(expected["prerelease"])

    def test_delayed_metadata_preserves_first_observation_and_eventually_passes(self):
        expected, sources = self.fixture()
        sources.pending = [{"dist-tags": {}, "versions": {}}]
        report = self.verify(expected, sources)
        self.assertEqual([a["status"] for a in report["attempts"]], ["NOT_READY", "PASS"])
        self.assertTrue((self.root / "evidence/attempt-001/result.json").is_file())

    def test_absent_channel_times_out_as_not_ready(self):
        expected, sources = self.fixture()
        sources.metadata["dist-tags"]["latest"] = "0.2.9"
        report = self.verify(expected, sources)
        self.assertEqual(report["status"], "NOT_READY")
        self.assertEqual(report["attempts"][-1]["elapsed_seconds"], 3)
        self.assertNotIn("tarball", sources.calls)

    def test_read_errors_keep_distinct_status_at_the_deadline(self):
        expected, sources = self.fixture()
        sources.pending = [release.Observation("READ_ERROR", "HTTP_503", "npm") for _ in range(3)]
        report = self.verify(expected, sources)
        self.assertEqual(report["status"], "READ_ERROR")
        self.assertEqual(report["observation"]["code"], "HTTP_503")
        self.assertEqual(len(report["attempts"]), 3)

    def test_npm_integrity_mismatch_stops_without_retry(self):
        expected, sources = self.fixture()
        sources.metadata["versions"]["0.3.0"]["dist"]["integrity"] = "wrong"
        report = self.verify(expected, sources)
        self.assertEqual(report["status"], "MISMATCH")
        self.assertEqual(report["observation"]["code"], "NPM_INTEGRITY")
        self.assertEqual(len(report["attempts"]), 1)

    def test_registry_archive_bytes_are_independent_of_metadata(self):
        expected, sources = self.fixture()
        sources.tarball_bytes = b"different archive"
        report = self.verify(expected, sources)
        self.assertEqual(report["observation"]["code"], "NPM_ARTIFACT_BYTES")
        self.assertNotIn("github_release", report["checks"])

    def test_unexpected_tarball_host_is_rejected_before_fetch(self):
        expected, sources = self.fixture()
        sources.metadata["versions"]["0.3.0"]["dist"]["tarball"] = "https://other.invalid/archive"
        report = self.verify(expected, sources)
        self.assertEqual(report["observation"]["code"], "NPM_TARBALL_URL")
        self.assertNotIn("tarball", sources.calls)

    def test_wrong_github_notes_or_commit_fail(self):
        expected, sources = self.fixture()
        sources.endpoints["releases/tags/0.3.0"]["body"] = "wrong notes"
        report = self.verify(expected, sources)
        self.assertEqual(report["observation"]["code"], "GITHUB_RELEASE_NOTES")

    def test_tag_commit_is_peeled_and_verified(self):
        expected, sources = self.fixture()
        sources.endpoints["git/tags/" + "f" * 40]["object"]["sha"] = "b" * 40
        report = self.verify(expected, sources)
        self.assertEqual(report["observation"]["code"], "GITHUB_TAG_COMMIT")

    def test_lightweight_tag_is_supported(self):
        expected, sources = self.fixture()
        sources.endpoints["git/ref/tags/0.3.0"]["object"] = {"type": "commit", "sha": expected["commit"]}
        self.assertEqual(self.verify(expected, sources)["status"], "PASS")

    def test_asset_missing_is_not_ready_and_never_claims_pass(self):
        expected, sources = self.fixture()
        sources.endpoints["releases/tags/0.3.0"]["assets"] = []
        report = self.verify(expected, sources)
        self.assertEqual(report["status"], "NOT_READY")
        self.assertNotIn("github_artifacts", report["checks"])

    def test_github_digest_mismatch_fails(self):
        expected, sources = self.fixture()
        sources.endpoints["releases/tags/0.3.0"]["assets"][0]["digest"] = "sha256:wrong"
        self.assertEqual(self.verify(expected, sources)["observation"]["code"], "GITHUB_ASSET_DIGEST")

    def test_consistent_github_digest_still_requires_the_same_archive(self):
        expected, sources = self.fixture()
        sources.assets[1] = b"different github archive"
        asset = sources.endpoints["releases/tags/0.3.0"]["assets"][0]
        asset.update(size=len(sources.assets[1]), digest="sha256:" + hashlib.sha256(sources.assets[1]).hexdigest())
        self.assertEqual(self.verify(expected, sources)["observation"]["code"], "GITHUB_ARTIFACT_BYTES")

    def test_github_prerelease_channel_must_match_the_requested_tag(self):
        expected, sources = self.fixture()
        sources.endpoints["releases/tags/0.3.0"]["prerelease"] = True
        self.assertEqual(self.verify(expected, sources)["observation"]["code"], "GITHUB_RELEASE_CHANNEL")

    def test_valid_digest_does_not_make_wrong_checksum_text_valid(self):
        expected, sources = self.fixture()
        sources.assets[2] = b"wrong checksum\n"
        asset = sources.endpoints["releases/tags/0.3.0"]["assets"][1]
        asset.update(size=len(sources.assets[2]), digest="sha256:" + hashlib.sha256(sources.assets[2]).hexdigest())
        self.assertEqual(self.verify(expected, sources)["observation"]["code"], "GITHUB_CHECKSUM_FILE")

    def test_http_404_and_transport_errors_have_different_classifications(self):
        sources = release.Sources("owner/project")
        sources.deadline = release.time.monotonic() + 30
        for error, status in ((HTTPError("https://example.invalid", 404, "missing", {}, None), "NOT_READY"),
                              (URLError("offline"), "READ_ERROR")):
            with self.subTest(status=status), patch.object(release, "urlopen", side_effect=error):
                with self.assertRaises(release.Observation) as raised:
                    sources.http("https://example.invalid", 20, "npm")
                self.assertEqual(raised.exception.status, status)

    def test_github_error_summary_does_not_copy_signed_urls(self):
        sources = release.Sources("owner/project")
        sources.deadline = release.time.monotonic() + 30
        result = subprocess.CompletedProcess([], 1, b"", b"download https://example.invalid/?signature=PRIVATE_PROBE EOF")
        with patch.object(release.subprocess, "run", return_value=result):
            with self.assertRaises(release.Observation) as raised:
                sources.gh("releases/tags/0.3.0")
        self.assertEqual(raised.exception.status, "READ_ERROR")
        self.assertNotIn("PRIVATE_PROBE", str(raised.exception))

    def test_invalid_local_archive_is_input_error_without_network(self):
        self.fixture()
        output = self.root / "bad-input"
        result = subprocess.run([sys.executable, "-B", str(SCRIPTS / "verify_release.py"),
            "--repository", "owner/project", "--version", "0.3.0", "--commit", "a" * 40,
            "--tarball", str(self.root / "missing.tgz"), "--notes", str(self.root / "notes.md"),
            "--output-dir", str(output), "--summary", str(self.root / "ci-summary.md")],
            capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 4)
        self.assertEqual(json.loads((output / "report.json").read_text())["status"], "INPUT_ERROR")
        self.assertIn("Release verification: INPUT_ERROR", (output / "summary.txt").read_text())
        self.assertIn("Release verification: INPUT_ERROR", (self.root / "ci-summary.md").read_text())

    def test_nonfinite_wait_values_are_rejected_before_creating_evidence(self):
        for value in ("nan", "inf", "-1"):
            with self.subTest(value=value):
                output = self.root / "invalid-wait"
                result = subprocess.run([sys.executable, "-B", str(SCRIPTS / "verify_release.py"),
                    "--repository", "owner/project", "--version", "0.3.0", "--commit", "a" * 40,
                    "--tarball", "unused.tgz", "--notes", "unused.md", "--output-dir", str(output),
                    "--timeout", value], capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 2)
                self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
