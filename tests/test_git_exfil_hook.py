"""Opt-in hooks against real Git index and message files in owned repositories."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import unittest

from core import policy
from tests.helpers import RepoFixture, git_available
from tests.test_exfil_audit import SECRET


HOOK = Path(__file__).resolve().parents[1] / "adapters/git/hook.py"
GENERIC_PATH = "/generic-review-root" + "/config.yaml"


@unittest.skipUnless(git_available(), "Git is required for the installed hook contract")
class GitExfilHookTests(RepoFixture):
    def setUp(self):
        super().setUp()
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("AGENT_GUARD_", "GIT_"))}
        self.audit = Path(self.root, ".agent-trash/audit.jsonl")

    def git(self, *args, check=True, text=None):
        return subprocess.run(["git", *args], cwd=self.root, env=self.env, input=text,
                              encoding="utf-8", capture_output=True, timeout=40, check=check)

    def hook(self, name, *args):
        return subprocess.run([sys.executable, "-B", str(HOOK), name, "--json", *args],
                              cwd=self.root, env=self.env, encoding="utf-8", capture_output=True, timeout=40)

    def install(self, name):
        path = Path(self.root, ".git/hooks", name)
        command = shlex.join([Path(sys.executable).as_posix(), HOOK.as_posix(), name])
        path.write_bytes(("#!/bin/sh\nexec " + command + ' "$@"\n').encode("utf-8"))
        path.chmod(0o700)

    def test_pre_commit_scans_staged_bytes_and_keeps_files_and_index(self):
        self.install("pre-commit")
        path = "space\twith newline\nname.txt"
        self.write(path, "clean staged content\n")
        self.git("add", "--", path)
        self.write(path, SECRET)
        proc = self.git("commit", "-qm", "clean index", check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.git("show", "HEAD:" + path).stdout, "clean staged content\n")
        self.git("add", "--", path)
        self.write(path, "clean working tree\n")
        before = self.git("rev-parse", "HEAD").stdout
        index = self.git("ls-files", "--stage", "-z").stdout
        proc = self.git("commit", "-qm", "unsafe stage", check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("SANITIZE_SECRET_REDACT", proc.stderr)
        self.assertNotIn(SECRET, proc.stdout + proc.stderr)
        self.assertEqual(before, self.git("rev-parse", "HEAD").stdout)
        self.assertEqual(index, self.git("ls-files", "--stage", "-z").stdout)
        self.assertEqual(Path(self.root, path).read_text(), "clean working tree\n")
        self.assertFalse(self.audit.parent.exists())

    def test_message_comment_and_scissors_do_not_allow_persisted_secret(self):
        self.install("commit-msg")
        for prefix in ("# note ", "heading\n\n# ------------------------ >8 ------------------------\n"):
            proc = self.git("commit", "--allow-empty", "--cleanup=verbatim", "-m", prefix + SECRET, check=False)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("BLOCK_SECRET_EMISSION", proc.stderr)
            self.assertNotIn(SECRET, proc.stdout + proc.stderr)
        self.git("commit", "--allow-empty", "-qm", "clean subject")
        self.assertEqual(self.git("log", "-1", "--format=%s").stdout.strip(), "clean subject")

    def test_path_warning_strict_and_restricted_stop(self):
        self.write("config.txt", "destination " + GENERIC_PATH)
        self.git("add", "--", "config.txt")
        warn = self.hook("pre-commit")
        self.assertEqual(warn.returncode, 0, warn.stderr)
        self.assertEqual(json.loads(warn.stdout)["code"], "ASK_PATH_EMISSION")
        strict = self.hook("pre-commit", "--strict-paths")
        self.assertEqual(strict.returncode, 2, strict.stdout)
        policy.force_mode(str(self.audit.parent), policy.MODE_RESTRICTED, "owned test")
        restricted = self.hook("pre-commit")
        self.assertEqual(restricted.returncode, 2)
        self.assertEqual(json.loads(restricted.stdout)["code"], "SANITIZE_PATH_REWRITE")

    def test_nonregular_and_binary_are_skipped_and_other_unreadable_inputs_block(self):
        self.env["AGENT_GUARD_EXFIL_MAX_BYTES"] = "128"
        (Path(self.root) / "synthetic.bin").write_bytes(b"\0" + b"x" * 16384)
        (Path(self.root) / "link.txt").symlink_to(Path(self.root) / "missing.txt")
        self.git("add", "--", "synthetic.bin", "link.txt")
        ok = self.hook("pre-commit")
        self.assertEqual(ok.returncode, 0, ok.stderr)
        counts = json.loads(ok.stdout)
        self.assertEqual((counts["skipped_binary"], counts["skipped_non_regular"]), (1, 1))
        (Path(self.root) / "invalid.txt").write_bytes(b"\xff" + SECRET.encode())
        self.write("large.txt", "x" * 16384)
        self.git("add", "--", "invalid.txt", "large.txt")
        blocked = self.hook("pre-commit", "--audit")
        self.assertEqual(blocked.returncode, 2)
        findings = json.loads(blocked.stdout)["findings"]
        self.assertEqual([row["code"] for row in findings], ["BLOCK_OUTPUT_UNSCANNABLE"] * 2)
        self.assertNotIn(SECRET, blocked.stdout + blocked.stderr + self.audit.read_text())

    def test_type_change_and_rename_scan_destination_with_path_exemptions(self):
        path = Path(self.root, "linked.txt")
        path.symlink_to("absent")
        self.git("add", "--", "linked.txt")
        self.git("commit", "-qm", "link baseline")
        path.rename(Path(self.root, "untracked-link"))
        path.write_text(SECRET)
        self.git("add", "--", "linked.txt")
        blocked = self.hook("pre-commit", "--audit")
        self.assertEqual(blocked.returncode, 2)
        rows = [json.loads(line) for line in self.audit.read_text().splitlines()]
        self.assertEqual([row["event"] for row in rows], ["exfil-sanitize", "exfil-span", "exfil-sanitize"])
        self.assertTrue(all(row["tool"] == "git_pre_commit" for row in rows))
        self.assertNotIn(path.name, self.audit.read_text())
        self.write(".agent-guard/exfil-allow.toml", "[paths]\nignore = linked.txt\n")
        allowed = self.hook("pre-commit")
        self.assertEqual(allowed.returncode, 0)
        self.assertEqual(json.loads(allowed.stdout)["skipped_exempt"], 1)
        self.git("commit", "-qm", "owned exempt fixture")
        self.git("mv", "linked.txt", "moved.txt")
        moved = self.hook("pre-commit")
        self.assertEqual(moved.returncode, 2)
        self.assertIn("SANITIZE_SECRET_REDACT", moved.stdout)

    def test_message_audit_is_metadata_only(self):
        self.write("message.txt", SECRET)
        result = self.hook("commit-msg", "--audit", "message.txt")
        self.assertEqual(result.returncode, 2)
        rows = [json.loads(line) for line in self.audit.read_text().splitlines()]
        self.assertEqual([row["event"] for row in rows], ["exfil-block", "exfil-span"])
        self.assertTrue(all(row["tool"] == "git_commit_msg" for row in rows))
        self.assertNotIn(SECRET, result.stdout + result.stderr + self.audit.read_text())
