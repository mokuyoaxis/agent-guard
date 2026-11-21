"""Export preflight tests use only temporary files and synthetic tokens."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from core import export_scan, policy, redaction
from tests.test_exfil_audit import SECRET


SCRIPT = Path(__file__).resolve().parents[1] / "skills/exfil-guard/scripts/check_export.py"
DOTENV = "." + "env"


class ExportScanTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.bucket = self.root / ".agent-trash"
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("AGENT_GUARD_")}
        self.env["AGENT_GUARD_WORKSPACE"] = str(self.root)

    def write(self, path, payload):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        return target

    def check(self, *args, code=0):
        process = subprocess.run([sys.executable, "-B", str(SCRIPT), "--json", *args],
                                 cwd=self.root, env=self.env, encoding="utf-8", capture_output=True, timeout=25)
        self.assertEqual(process.returncode, code, process.stderr)
        self.assertNotIn(str(self.root), process.stdout + process.stderr)
        self.assertNotIn(SECRET, process.stdout + process.stderr)
        return json.loads(process.stdout)

    def test_sensitive_source_names_are_blocked_before_binary_size_or_content(self):
        for path in (DOTENV, DOTENV + ".production", "prod." + "p12", ".npm" + "rc"):
            self.write(path, b"\x00" * 100)
        result = self.check("--max-file-bytes", "1", ".", code=2)
        self.assertTrue(result["complete"])
        self.assertEqual(result["size_bytes"], 0)
        self.assertEqual(result["counts"]["files_blocked"], 4)
        self.assertEqual(result["counts"]["skipped_binary"], 0)
        self.assertEqual({f["code"] for f in result["findings"]}, {"BLOCK_SECRET_SOURCE_DUMP"})
        for suffix in ("example", "sample", "template", "dist"):
            candidate = self.write(DOTENV + "." + suffix, b"PORT=8080")
            self.assertEqual(self.check(str(candidate))["counts"]["files_scanned"], 1)
        self.write(DOTENV + ".example", SECRET.encode())
        refused = self.check(DOTENV + ".example", code=2)
        self.assertEqual(refused["findings"][0]["code"], "SANITIZE_SECRET_REDACT")
        self.assertFalse(self.bucket.exists())

    def test_symlinks_bounds_and_unreadable_special_files(self):
        self.write("dir/value.txt", b"ordinary")
        with tempfile.TemporaryDirectory() as outer:
            outside = Path(outer, "outside.txt")
            outside.write_text(SECRET)
            (self.root / "unsafe").symlink_to(outside)
            (self.root / "unsafe-dir").symlink_to(outside.parent, target_is_directory=True)
            (self.root / "inside").symlink_to("dir/value.txt")
            (self.root / "loop").symlink_to("loop")
            os.mkfifo(self.root / "fifo")
            result = self.check("inside", "unsafe", "unsafe-dir/outside.txt", "loop", "fifo",
                                str(outside), "dir/../dir/value.txt", code=2)
        self.assertFalse(result["complete"])
        self.assertEqual(result["size_bytes"], 0)
        self.assertEqual(result["counts"]["skipped_symlinks"], 1)
        self.assertEqual([f["reason"] for f in result["findings"]],
                         ["unsafe_symlink", "unsafe_symlink", "unsafe_symlink", "non_regular_file",
                          "outside_workspace", "outside_workspace"])
        self.assertIsNone(result["findings"][-2]["path"])

    def test_binary_and_non_utf8_and_limits_stop_expansion(self):
        self.write("binary.dat", b"\0" + b"x" * 10000)
        self.write("invalid", b"\xff" + SECRET.encode())
        self.write("large", b"x" * 100)
        self.write("ordinary", b"abcd")
        allowed = self.check("--max-file-bytes", "4", "binary.dat")
        self.assertEqual(allowed["counts"]["skipped_binary"], 1)
        invalid = self.check("invalid", code=2)
        self.assertEqual(invalid["findings"][0]["reason"], "invalid_utf8")
        for flags, candidates, reason in (
            (("--max-files", "1"), ("ordinary", "invalid"), "file_count_limit"),
            (("--max-file-bytes", "8"), ("large", "ordinary"), "file_byte_limit"),
            (("--max-total-bytes", "6"), ("ordinary", "large"), "total_byte_limit"),
        ):
            with self.subTest(reason=reason):
                result = self.check(*flags, *candidates, code=2)
                self.assertTrue(result["truncated"])
                self.assertFalse(result["complete"])
                self.assertEqual(result["findings"][0]["reason"], reason)

    def test_git_ignored_files_are_scanned_and_control_directories_can_be_explicit(self):
        self.write(".gitignore", b"dist/\n")
        self.write("dist/output.txt", b"ordinary")
        self.write(".git/synthetic.txt", SECRET.encode())
        self.write(".agent-trash/synthetic.txt", SECRET.encode())
        result = self.check(".")
        self.assertEqual(result["counts"]["files_scanned"], 2)
        self.assertEqual(result["counts"]["skipped_control_directories"], 2)
        refused = self.check(".git/synthetic.txt", code=2)
        self.assertEqual(refused["findings"][0]["path"], ".git/synthetic.txt")

    def test_exemptions_and_paths_file_use_workspace_and_never_exempt_escaping_links(self):
        self.write(redaction.DEFAULT_ALLOWFILE, b"[paths]\nignore = owned-reports\n[rules]\ndisable = secret/github-token\n")
        self.write("owned-reports/report", SECRET.encode())
        self.write("some data.txt", SECRET.encode())
        self.write("targets list", b"owned-reports\nsome data.txt\n")
        result = self.check("--paths-file", "targets list")
        self.assertEqual(result["counts"]["skipped_exempt"], 1)
        self.assertEqual(result["exempted"], 1)
        with tempfile.TemporaryDirectory() as outer:
            (self.root / "owned-reports" / "external").symlink_to(outer)
            refused = self.check("--paths-file", "targets list", code=2)
        self.assertEqual(refused["findings"][0]["reason"], "unsafe_symlink")

    def test_linked_exemption_is_not_loaded(self):
        self.write("proof.txt", SECRET.encode())
        with tempfile.TemporaryDirectory() as outer:
            external = Path(outer, "allow.toml")
            external.write_text("[paths]\nignore = proof.txt\n")
            allow = self.root / redaction.DEFAULT_ALLOWFILE
            allow.parent.mkdir()
            allow.symlink_to(external)
            result = self.check("proof.txt", code=2)
        self.assertEqual(result["counts"]["files_scanned"], 1)
        self.assertFalse(result["exemption"]["active"])

    def test_public_path_and_audit_project_away_sensitive_names(self):
        filename = "draft-" + SECRET
        self.write(filename, SECRET.encode())
        data = self.check("--audit", filename, code=2)
        self.assertEqual(data["workspace"], ".")
        self.assertIsNone(data["findings"][0]["path"])
        self.assertTrue(data["audit"]["recorded"])
        log = (self.bucket / "audit.jsonl").read_text()
        self.assertNotIn(str(self.root), log)
        self.assertNotIn(filename, log)
        self.assertNotIn(SECRET, log)
        summary, span = map(json.loads, log.splitlines())
        self.assertEqual((summary["tool"], summary["event"], summary["target_count"]),
                         ("exfil_export", "exfil-sanitize", 1))
        self.assertEqual(span["rule_id"], "secret/github-token")

    def test_path_warning_strict_and_restricted_keep_credential_refusal(self):
        self.write("warning.txt", b"reference /generic-review-root/config.yaml")
        self.assertEqual(self.check("warning.txt")["counts"]["files_warned"], 1)
        strict = self.check("--strict-paths", "warning.txt", code=2)
        self.assertEqual(strict["counts"]["files_blocked"], 1)
        self.write("credential.txt", SECRET.encode())
        mixed = self.check("warning.txt", "credential.txt", code=2)
        self.assertEqual(mixed["code"], "SANITIZE_SECRET_REDACT")
        policy.force_mode(str(self.bucket), policy.MODE_RESTRICTED, "owned test")
        restricted = self.check("warning.txt", code=2)
        self.assertEqual(restricted["code"], "SANITIZE_PATH_REWRITE")

    def test_empty_targets_unknown_channel_and_unsupported_descriptors_fail_closed(self):
        self.assertEqual(self.check(code=2)["findings"][0]["reason"], "empty_targets")
        unknown = self.check("--channel", SECRET, "--audit", ".", code=2)
        self.assertIsNone(unknown["channel"])
        self.assertEqual(unknown["findings"][0]["reason"], "unknown_channel")
        scan = export_scan.ExportScan(self.root, redaction.get_channel("archive-upload"),
                                     max_files=1, max_file_bytes=16, max_total_bytes=32, strict_paths=False)
        with patch.object(os, "supports_fd", set()):
            scan.scan((".",), None)
        self.assertEqual(scan.result()["findings"][0]["reason"], "unsupported_platform")
        self.assertFalse(scan.workspace_ready)
