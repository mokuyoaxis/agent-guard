"""Safe redacted file reader: CLI and MCP parity, exemptions, quarantine and pagination."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from adapters.mcp.operations import Operations
from core import policy, redaction, safe_read
from tests.test_exfil_audit import SECRET


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/exfil-guard/scripts/safe_read.py"
DOTENV = "." + "env"
OPENAI_TOKEN = "sk-" + "AbCdEf0123456789AbCdEf0123456789"
PEM_HEADER = "-----" + "BEGIN RSA PRIVATE KEY" + "-----"
PEM_FOOTER = "-----" + "END RSA PRIVATE KEY" + "-----"
PEM_BLOCK = PEM_HEADER + "\nMIIEowIBAAKCAQEA\n" + PEM_FOOTER
GENERIC_PATH = "/" + "data/exports/report.csv"
SYSTEM_PATH = "/" + "usr/bin/python3"


def _host_path() -> str:
    return os.path.join(os.path.expanduser("~"), "owned-outside-dir", "trace.log")


class SafeReadFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="safe-read-test-")
        self.addCleanup(temporary.cleanup)
        self.outer = Path(temporary.name).resolve()
        self.root = self.outer / "workspace"
        self.root.mkdir()
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_GUARD_")}
        self.env["AGENT_GUARD_WORKSPACE"] = str(self.root)

    def put(self, relative: str, payload: str | bytes) -> Path:
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(payload, bytes):
            target.write_bytes(payload)
        else:
            target.write_text(payload, encoding="utf-8")
        return target

    def run_cli(self, relative: str, *flags: str, trash_root: Path | None = None,
                as_json: bool = True) -> subprocess.CompletedProcess:
        env = dict(self.env)
        if trash_root is not None:
            env["AGENT_GUARD_TRASH"] = str(trash_root)
        cmd = [sys.executable, "-B", str(SCRIPT), "--workspace", str(self.root)]
        if as_json:
            cmd.append("--json")
        cmd.extend(flags)
        cmd.extend(["--", relative])
        return subprocess.run(
            cmd, cwd=self.root, env=env,
            capture_output=True, text=True, encoding="utf-8", timeout=25,
        )

    def run_both(self, relative: str, *, start_line: int = 1, end_line: int | None = None,
                 strict_paths: bool = False, fail_on_redaction: bool = False,
                 trash_root: Path | None = None, audit: bool = False):
        """Invoke CLI (--json) and MCP Operations.call on the exact same fixture and assert parity."""
        cli_flags = ["--start-line", str(start_line)]
        mcp_args = {"path": relative, "start_line": start_line}
        if end_line is not None:
            cli_flags += ["--end-line", str(end_line)]
            mcp_args["end_line"] = end_line
        if strict_paths:
            cli_flags.append("--strict-paths")
            mcp_args["strict_paths"] = True
        if fail_on_redaction:
            cli_flags.append("--fail-on-redaction")
            mcp_args["fail_on_redaction"] = True
        if audit:
            cli_flags.append("--audit")

        cli_proc = self.run_cli(relative, *cli_flags, trash_root=trash_root, as_json=True)
        cli_json = json.loads(cli_proc.stdout)
        self.assertEqual(cli_proc.returncode, cli_json["exit"], cli_proc.stderr)
        self.assertNotIn("workspace", cli_json)

        ops = Operations(
            str(self.root),
            trash_root=str(trash_root) if trash_root is not None else None,
            audit_exfil=audit,
        )
        mcp_resp = ops.call("safe_read", mcp_args)
        self.assertEqual(mcp_resp["exit_code"], cli_proc.returncode, mcp_resp)
        self.assertEqual(mcp_resp["ok"], cli_proc.returncode == 0, mcp_resp)
        mcp_json = mcp_resp["result"]
        self.assertNotIn("workspace", mcp_json)

        for key in ("schema", "status", "decision", "code", "exit", "path", "text", "spans"):
            self.assertEqual(cli_json.get(key), mcp_json.get(key), f"mismatch on {key}")
        if cli_proc.returncode == 0:
            for key in ("start_line", "end_line", "returned_lines", "total_lines",
                        "returned_bytes", "truncated", "truncated_by", "has_more",
                        "next_line", "page_redacted", "page_redaction_count",
                        "page_redactions_by_rule"):
                self.assertEqual(cli_json[key], mcp_json[key], f"mismatch on {key}")
        else:
            self.assertEqual(cli_json["error_code"], mcp_json["error_code"])
            self.assertEqual(cli_json["message"], mcp_json["message"])
            self.assertEqual(cli_json["text"], "")
            self.assertEqual(cli_json["spans"], [])
        return cli_json, mcp_resp


class SafeReadPlatformAndBoundaryTests(SafeReadFixture):
    """Tests that run unconditionally on every platform, including Windows."""

    def test_unsupported_platform_returns_fixed_error_on_windows_or_missing_dir_fd(self):
        self.put("notes.txt", "hello world\n")
        if not safe_read.supports_descriptor_read():
            direct = safe_read.safe_read(str(self.root), "notes.txt")
            self.assertEqual(direct["status"], "error")
            self.assertEqual(direct["error_code"], "UNSUPPORTED_PLATFORM")
            self.assertEqual(direct["exit"], 1)
            self.assertEqual(direct["text"], "")
            self.assertEqual(direct["spans"], [])
            cli_json, _ = self.run_both("notes.txt")
            self.assertEqual(cli_json["error_code"], "UNSUPPORTED_PLATFORM")
        else:
            with mock.patch.object(safe_read, "supports_descriptor_read", return_value=False):
                direct = safe_read.safe_read(str(self.root), "notes.txt")
            self.assertEqual(direct["status"], "error")
            self.assertEqual(direct["decision"], "BLOCK")
            self.assertEqual(direct["code"], "BLOCK_OUTPUT_UNSCANNABLE")
            self.assertEqual(direct["error_code"], "UNSUPPORTED_PLATFORM")
            self.assertEqual(direct["exit"], 1)
            self.assertEqual(direct["text"], "")
            self.assertEqual(direct["spans"], [])
            self.assertNotIn("workspace", direct)

    def test_control_dirs_dual_quarantine_roots_and_secret_stores_refused(self):
        self.put(DOTENV, "API_KEY=" + OPENAI_TOKEN + "\n")
        self.put(DOTENV + ".local", "TOKEN=" + SECRET + "\n")
        self.put("cert." + "pem", PEM_BLOCK + "\n")
        self.put(".git/config", "[core]\n\trepositoryformatversion = 0\n")
        self.put(".agent-guard/delete-policy.json", "{}\n")
        self.put(".agent-trash/tx1/item.txt", "quarantined payload\n")
        self.put(".GIT/config", "case variant\n")
        self.put(".Agent-Guard/policy.json", "case variant\n")
        self.put(".Agent-Trash/tx2/item.txt", "case variant\n")
        custom_in_ws = self.root / "custom-quarantine"
        self.put("custom-quarantine/tx3/item.txt", "custom quarantined\n")

        for secret_path, expect_hint in (
            (DOTENV, True),
            (DOTENV + ".local", True),
            ("cert." + "pem", False),
        ):
            with self.subTest(secret_path=secret_path):
                cli_json, _ = self.run_both(secret_path)
                self.assertEqual(cli_json["exit"], 2)
                self.assertEqual(cli_json["status"], "refused")
                self.assertEqual(cli_json["error_code"], "SECRET_STORE_REFUSED")
                self.assertEqual("config_safe_view" in cli_json["message"], expect_hint)
                self.assertNotIn(OPENAI_TOKEN, json.dumps(cli_json))
                self.assertNotIn(SECRET, json.dumps(cli_json))

        # Control directories (including case variants) refused.
        for ctrl in (".git/config", ".agent-guard/delete-policy.json",
                     ".agent-trash/tx1/item.txt", ".GIT/config",
                     ".Agent-Guard/policy.json", ".Agent-Trash/tx2/item.txt"):
            with self.subTest(ctrl=ctrl):
                cli_json, _ = self.run_both(ctrl)
                self.assertEqual(cli_json["exit"], 2)
                self.assertEqual(cli_json["error_code"], "CONTROL_PATH")

        # When custom quarantine is configured inside the workspace, BOTH the
        # custom quarantine AND the default .agent-trash remain refused.
        for protected in ("custom-quarantine/tx3/item.txt", ".agent-trash/tx1/item.txt"):
            with self.subTest(custom_trash_target=protected):
                cli_json, _ = self.run_both(protected, trash_root=custom_in_ws)
                self.assertEqual(cli_json["exit"], 2)
                self.assertEqual(cli_json["error_code"], "CONTROL_PATH")

        # External custom quarantine also keeps default .agent-trash refused.
        external_trash = self.outer / "external-quarantine"
        external_trash.mkdir()
        cli_json, _ = self.run_both(".agent-trash/tx1/item.txt", trash_root=external_trash)
        self.assertEqual(cli_json["exit"], 2)
        self.assertEqual(cli_json["error_code"], "CONTROL_PATH")

    def test_invalid_arguments_and_secret_shaped_missing_filename_never_echo(self):
        self.put("ok.txt", "plain\n")
        bad_cli = self.run_cli("ok.txt", "--start-line", "1", "--end-line", "205")
        self.assertEqual(bad_cli.returncode, 1)
        payload = json.loads(bad_cli.stdout)
        self.assertEqual(payload["error_code"], "INVALID_ARGUMENT")
        self.assertEqual(payload["text"], "")

        missing = safe_read.safe_read(str(self.root), SECRET + ".txt")
        self.assertEqual(missing["exit"], 1)
        self.assertIsNone(missing["path"])
        self.assertNotIn(SECRET, json.dumps(missing))


@unittest.skipUnless(safe_read.supports_descriptor_read(), "POSIX dir_fd required")
class SafeReadPosixTests(SafeReadFixture):
    def test_quarantine_boundaries_follow_directory_identity(self):
        retained = self.put("retained-bucket/tx/item.txt", "retained fixture\n")
        (self.root / ".agent-trash").symlink_to(
            retained.parent.parent, target_is_directory=True,
        )
        active = self.put("active-bucket/tx/item.txt", "active fixture\n")
        configured = active.parent.parent
        result, _ = self.run_both("retained-bucket/tx/item.txt", trash_root=configured)
        self.assertEqual((result["error_code"], result["text"]), ("CONTROL_PATH", ""))

        alternate = self.root / "Active-Bucket" / "tx" / "item.txt"
        if alternate.exists():
            self.assertTrue(alternate.samefile(active))
            result, _ = self.run_both("Active-Bucket/tx/item.txt", trash_root=configured)
            self.assertEqual((result["error_code"], result["text"]), ("CONTROL_PATH", ""))
        else:
            self.put("Active-Bucket/tx/item.txt", "ordinary fixture\n")
            result, _ = self.run_both("Active-Bucket/tx/item.txt", trash_root=configured)
            self.assertEqual((result["exit"], result["text"]), (0, "ordinary fixture\n"))

    def test_redaction_and_path_behavior_table_cli_and_mcp_parity(self):
        host = _host_path()
        ws_file = str(self.root / "src/app.py")
        content = (
            f"token={OPENAI_TOKEN}\n"
            f"host={host}\n"
            f"generic={GENERIC_PATH}\n"
            f"workspace_file={ws_file}\n"
            f"system={SYSTEM_PATH}\n"
        )
        self.put("logs/run.log", content)

        # Normal mode: T1 secret and host path redacted; generic/workspace/system kept.
        normal, _ = self.run_both("logs/run.log")
        self.assertEqual(normal["exit"], 0)
        self.assertEqual(normal["decision"], "SANITIZE")
        self.assertEqual(normal["code"], "SANITIZE_SECRET_REDACT")
        self.assertTrue(normal["page_redacted"])
        self.assertEqual(normal["page_redaction_count"], 2)
        self.assertEqual(normal["page_redactions_by_rule"], {
            redaction.RULE_HOST_ABSOLUTE_PATH: 1,
            redaction.RULE_OPENAI_KEY: 1,
        })
        self.assertIn("token=sk-<REDACTED>\n", normal["text"])
        self.assertIn("host=<PATH>\n", normal["text"])
        self.assertIn(f"generic={GENERIC_PATH}\n", normal["text"])
        self.assertIn(f"workspace_file={ws_file}\n", normal["text"])
        self.assertIn(f"system={SYSTEM_PATH}\n", normal["text"])
        self.assertNotIn(OPENAI_TOKEN, json.dumps(normal))
        self.assertNotIn(host, json.dumps(normal))
        # Span coordinates match post-redaction page text exactly.
        for row in normal["spans"]:
            snippet = normal["text"][row["start"]:row["end"]]
            self.assertEqual(row["length"], len(snippet))
            expected = "sk-<REDACTED>" if row["family"] == "secret" else "<PATH>"
            self.assertEqual(snippet, expected)

        # --strict-paths: generic absolute path is ALSO rewritten to <PATH>.
        strict, _ = self.run_both("logs/run.log", strict_paths=True)
        self.assertEqual(strict["exit"], 0)
        self.assertEqual(strict["page_redaction_count"], 3)
        self.assertEqual(strict["page_redactions_by_rule"][redaction.RULE_GENERIC_ABSOLUTE_PATH], 1)
        self.assertIn("generic=<PATH>\n", strict["text"])
        self.assertNotIn(GENERIC_PATH, strict["text"])

        # --fail-on-redaction refuses when redactions are needed.
        refused, _ = self.run_both("logs/run.log", fail_on_redaction=True)
        self.assertEqual(refused["exit"], 2)
        self.assertEqual(refused["error_code"], "REDACTION_REQUIRED")
        self.assertEqual(refused["text"], "")

        # Generic-only file: normal + --fail-on-redaction ALLOWS (no redaction needed);
        # --strict-paths + --fail-on-redaction REFUSES; RESTRICTED mode rewrites it.
        self.put("logs/generic.log", f"generic={GENERIC_PATH}\nsystem={SYSTEM_PATH}\n")
        ok_generic, _ = self.run_both("logs/generic.log", fail_on_redaction=True)
        self.assertEqual(ok_generic["exit"], 0)
        self.assertEqual(ok_generic["decision"], "ALLOW")
        self.assertEqual(ok_generic["code"], "ALLOW_NOOP")
        self.assertFalse(ok_generic["page_redacted"])

        strict_fail, _ = self.run_both("logs/generic.log", strict_paths=True, fail_on_redaction=True)
        self.assertEqual(strict_fail["exit"], 2)
        self.assertEqual(strict_fail["error_code"], "REDACTION_REQUIRED")
        self.assertEqual(strict_fail["code"], "BLOCK_PATH_EMISSION")

        restricted_trash = self.outer / "restricted-trash"
        policy.request_mode(str(restricted_trash), policy.MODE_RESTRICTED, actor="unit-test")
        restricted, _ = self.run_both("logs/generic.log", trash_root=restricted_trash)
        self.assertEqual(restricted["exit"], 0)
        self.assertEqual(restricted["decision"], "SANITIZE")
        self.assertEqual(restricted["code"], "SANITIZE_PATH_REWRITE")
        self.assertIn("generic=<PATH>\n", restricted["text"])

    def test_exempted_residual_secret_and_exempted_path_only_are_blocked(self):
        # 1. Path-, rule-, and sha256-exempted secrets are all independently blocked.
        self.put("notes/secret.txt", f"line1\nkey={OPENAI_TOKEN}\n")
        token_hash = hashlib.sha256(OPENAI_TOKEN.encode("utf-8")).hexdigest()
        allow_configs = (
            'paths = ["notes/*"]\n',
            f'rules = ["{redaction.RULE_OPENAI_KEY}"]\n',
            f'hashes = ["sha256:{token_hash}"]\n',
        )
        for cfg in allow_configs:
            with self.subTest(allowfile=cfg.strip()):
                self.put(redaction.DEFAULT_ALLOWFILE, cfg)
                cli_json, _ = self.run_both("notes/secret.txt")
                self.assertEqual(cli_json["exit"], 2)
                self.assertEqual(cli_json["error_code"], "RESIDUAL_SECRET_BLOCKED")
                self.assertEqual(cli_json["code"], "BLOCK_OUTPUT_UNSCANNABLE")
                self.assertEqual(cli_json["text"], "")
                self.assertNotIn(OPENAI_TOKEN, json.dumps(cli_json))

        # 2. Path-only hit exempted in .agent-guard/exfil-allow.toml is ALSO blocked
        # by the independent unfiltered final check.
        host = _host_path()
        self.put("notes/path_only.txt", f"trace at {host}\n")
        for cfg in ('paths = ["notes/*"]\n', f'rules = ["{redaction.RULE_HOST_ABSOLUTE_PATH}"]\n'):
            with self.subTest(path_allow=cfg.strip()):
                self.put(redaction.DEFAULT_ALLOWFILE, cfg)
                cli_json, _ = self.run_both("notes/path_only.txt")
                self.assertEqual(cli_json["exit"], 2)
                self.assertEqual(cli_json["error_code"], "RESIDUAL_PATH_BLOCKED")
                self.assertEqual(cli_json["code"], "BLOCK_OUTPUT_UNSCANNABLE")
                self.assertEqual(cli_json["text"], "")
                self.assertNotIn(host, json.dumps(cli_json))

        # Generic absolute path exempted under --strict-paths is blocked, while
        # normal mode allows generic paths untouched.
        self.put("notes/generic_only.txt", f"location={GENERIC_PATH}\n")
        self.put(redaction.DEFAULT_ALLOWFILE, f'rules = ["{redaction.RULE_GENERIC_ABSOLUTE_PATH}"]\n')
        normal_gen, _ = self.run_both("notes/generic_only.txt")
        self.assertEqual(normal_gen["exit"], 0)
        self.assertEqual(normal_gen["code"], "ALLOW_NOOP")
        strict_gen, _ = self.run_both("notes/generic_only.txt", strict_paths=True)
        self.assertEqual(strict_gen["exit"], 2)
        self.assertEqual(strict_gen["error_code"], "RESIDUAL_PATH_BLOCKED")
        self.assertEqual(strict_gen["text"], "")

    def test_any_unscanned_pass_refuses_without_emitting_text(self):
        self.put("plain.txt", "clean text\n")
        real_scan = redaction.scan_text
        for fail_on_call in (1, 2, 3):
            with self.subTest(fail_on_call=fail_on_call):
                calls = {"n": 0}

                def flaky_scan(*args, **kwargs):
                    calls["n"] += 1
                    if calls["n"] == fail_on_call:
                        return redaction.ScanResult(scanned=False, error="injected")
                    return real_scan(*args, **kwargs)

                with mock.patch.object(redaction, "scan_text", side_effect=flaky_scan):
                    res = safe_read.safe_read(str(self.root), "plain.txt")
                self.assertEqual(res["exit"], 2)
                self.assertEqual(res["error_code"], "UNSCANNABLE_CONTENT")
                self.assertEqual(res["code"], "BLOCK_OUTPUT_UNSCANNABLE")
                self.assertEqual(res["text"], "")
                self.assertEqual(res["spans"], [])

    def test_cross_page_secret_page_local_decision_audit_and_pem_refusal(self):
        # Out-of-page PEM private key block (line 250) refuses page 1..10.
        pem_lines = [f"line {i}\n" for i in range(1, 249)] + [PEM_BLOCK + "\n"]
        self.put("logs/has_pem.log", "".join(pem_lines))
        pem_res, _ = self.run_both("logs/has_pem.log", start_line=1, end_line=10)
        self.assertEqual(pem_res["exit"], 2)
        self.assertEqual(pem_res["error_code"], "SECRET_BLOCK_REFUSED")
        self.assertEqual(pem_res["text"], "")

        # Out-of-page single-line secret on line 220; lines 1..10 are clean.
        lines = [f"line {i}\n" for i in range(1, 251)]
        lines[219] = f"secret_line={OPENAI_TOKEN}\n"
        self.put("logs/cross_page.log", "".join(lines))

        bucket = self.outer / "audit-bucket"
        bucket.mkdir()
        clean_page, _ = self.run_both("logs/cross_page.log", start_line=1, end_line=10,
                                      trash_root=bucket, audit=True)
        self.assertEqual(clean_page["exit"], 0)
        self.assertEqual(clean_page["decision"], "ALLOW")
        self.assertEqual(clean_page["code"], "ALLOW_NOOP")
        self.assertFalse(clean_page["page_redacted"])
        self.assertEqual(clean_page["page_redaction_count"], 0)
        self.assertEqual(clean_page["page_redactions_by_rule"], {})
        self.assertEqual(clean_page["spans"], [])
        self.assertEqual(clean_page["next_line"], 11)

        # Audit log reflects ONLY the clean page (ALLOW / ALLOW_NOOP, 0 spans).
        audit_rows = [json.loads(line) for line in (bucket / "audit.jsonl").read_text().splitlines()]
        self.assertEqual(len(audit_rows), 2)  # 1 CLI + 1 MCP, both summary-only
        for row in audit_rows:
            self.assertEqual(row["tool"], "exfil_safe_read")
            self.assertEqual(row["decision"], "ALLOW")
            self.assertEqual(row["code"], "ALLOW_NOOP")
            self.assertEqual(row["span_count"], 0)
            self.assertEqual(row["redacted_count"], 0)

        # --fail-on-redaction still refuses page 1..10 because line 220 needs redaction.
        fail_res, _ = self.run_both("logs/cross_page.log", start_line=1, end_line=10,
                                    fail_on_redaction=True)
        self.assertEqual(fail_res["exit"], 2)
        self.assertEqual(fail_res["error_code"], "REDACTION_REQUIRED")

        # Reading the page containing line 220 returns SANITIZE with page-local coords.
        secret_page, _ = self.run_both("logs/cross_page.log", start_line=215, end_line=225,
                                       trash_root=bucket, audit=True)
        self.assertEqual(secret_page["exit"], 0)
        self.assertEqual(secret_page["decision"], "SANITIZE")
        self.assertEqual(secret_page["code"], "SANITIZE_SECRET_REDACT")
        self.assertEqual(secret_page["page_redaction_count"], 1)
        span = secret_page["spans"][0]
        self.assertEqual(secret_page["text"][span["start"]:span["end"]], "sk-<REDACTED>")
        self.assertEqual(span["length"], len("sk-<REDACTED>"))
        self.assertNotIn(OPENAI_TOKEN, (bucket / "audit.jsonl").read_text())

    def test_pagination_default_200_32kib_complete_lines_single_line_limit_and_empty(self):
        # 250 short lines: omitting end_line returns 200 lines + next_line=201.
        self.put("many.txt", "".join(f"row-{i:03d}\n" for i in range(1, 251)))
        page1, _ = self.run_both("many.txt", start_line=1)
        self.assertEqual(page1["returned_lines"], 200)
        self.assertEqual(page1["end_line"], 200)
        self.assertTrue(page1["truncated"])
        self.assertEqual(page1["truncated_by"], "lines")
        self.assertTrue(page1["has_more"])
        self.assertEqual(page1["next_line"], 201)

        page2, _ = self.run_both("many.txt", start_line=201)
        self.assertEqual(page2["returned_lines"], 50)
        self.assertEqual(page2["end_line"], 250)
        self.assertFalse(page2["truncated"])
        self.assertFalse(page2["has_more"])
        self.assertIsNone(page2["next_line"])

        # 32 KiB byte cap returns complete lines only and accurate next_line.
        line_payload = "x" * 9999 + "\n"  # 10,000 bytes per line
        self.put("wide.txt", line_payload * 5)  # 50,000 bytes (5 lines)
        bpage, _ = self.run_both("wide.txt", start_line=1)
        self.assertEqual(bpage["exit"], 0)
        self.assertEqual(bpage["returned_lines"], 3)
        self.assertEqual(bpage["returned_bytes"], 30000)
        self.assertEqual(bpage["end_line"], 3)
        self.assertTrue(bpage["truncated"])
        self.assertEqual(bpage["truncated_by"], "bytes")
        self.assertTrue(bpage["has_more"])
        self.assertEqual(bpage["next_line"], 4)
        self.assertTrue(bpage["text"].endswith("\n"))

        # Single line > 32 KiB returns fixed LINE_TOO_LONG error.
        self.put("giant_line.txt", "short\n" + ("y" * (safe_read.MAX_RETURN_BYTES + 10)) + "\n")
        first_ok, _ = self.run_both("giant_line.txt", start_line=1)
        self.assertEqual(first_ok["returned_lines"], 1)
        self.assertEqual(first_ok["next_line"], 2)
        too_long, _ = self.run_both("giant_line.txt", start_line=2)
        self.assertEqual(too_long["exit"], 1)
        self.assertEqual(too_long["status"], "error")
        self.assertEqual(too_long["error_code"], "LINE_TOO_LONG")
        self.assertEqual(too_long["text"], "")

        # Empty file and past-EOF range.
        self.put("empty.txt", "")
        empty, _ = self.run_both("empty.txt")
        self.assertEqual((empty["exit"], empty["text"], empty["returned_lines"], empty["next_line"]),
                         (0, "", 0, None))
        past, _ = self.run_both("many.txt", start_line=300)
        self.assertEqual((past["exit"], past["text"], past["returned_lines"], past["next_line"]),
                         (0, "", 0, None))

    def test_file_safety_symlinks_hardlinks_binary_encoding_size_and_plain_cli(self):
        target = self.put("real.txt", "plain text\n")
        nested = self.put("subdir/inside.txt", "nested\n")
        link = self.root / "sym.txt"
        link.symlink_to(target)
        symdir = self.root / "symdir"
        symdir.symlink_to(nested.parent, target_is_directory=True)
        hard = self.root / "hard.txt"
        os.link(target, hard)
        self.put("bin.dat", b"abc\x00def")
        self.put("bad_utf8.txt", b"\xff\xfe\xfd")
        self.put("huge.txt", b"a" * (safe_read.MAX_FILE_BYTES + 1))

        for rel, expected in (
            ("sym.txt", "UNSAFE_FILE"),
            ("symdir/inside.txt", "UNSAFE_FILE"),
            ("hard.txt", "UNSAFE_FILE"),
            ("bin.dat", "BINARY_FILE"),
            ("bad_utf8.txt", "INVALID_ENCODING"),
            ("huge.txt", "FILE_TOO_LARGE"),
        ):
            with self.subTest(rel=rel):
                res, _ = self.run_both(rel)
                self.assertEqual(res["exit"], 1)
                self.assertEqual(res["error_code"], expected)
                self.assertEqual(res["text"], "")

        # Concurrent modification during read triggers FILE_CHANGED.
        real_fstat = os.fstat
        calls = {"n": 0}

        def shifting_fstat(fd):
            info = real_fstat(fd)
            calls["n"] += 1
            if calls["n"] == 2:
                return os.stat_result((
                    info.st_mode, info.st_ino, info.st_dev, info.st_nlink,
                    info.st_uid, info.st_gid, info.st_size + 1,
                    info.st_atime, info.st_mtime, info.st_ctime,
                ))
            return info

        with mock.patch("core.safe_read.os.fstat", side_effect=shifting_fstat):
            changed = safe_read.safe_read(str(self.root), "subdir/inside.txt")
        self.assertEqual(changed["exit"], 1)
        self.assertEqual(changed["error_code"], "FILE_CHANGED")
        self.assertEqual(changed["text"], "")

        # Plain (non-JSON) CLI writes text directly on success and fixed error on refusal.
        self.put("plain_cli.txt", f"hi {OPENAI_TOKEN}\n")
        plain = self.run_cli("plain_cli.txt", as_json=False)
        self.assertEqual(plain.returncode, 0, plain.stderr)
        self.assertEqual(plain.stdout, "hi sk-<REDACTED>\n")
        self.assertNotIn(OPENAI_TOKEN, plain.stdout + plain.stderr)

        refused_plain = self.run_cli(DOTENV, as_json=False)
        self.assertEqual(refused_plain.returncode, 2)
        self.assertEqual(refused_plain.stdout, "")
        self.assertIn("SECRET_STORE_REFUSED", refused_plain.stderr)

    def test_redaction_expansion_near_256kib_cap_rescan_succeeds(self):
        # Short password 'a' -> '<REDACTED>' expands length past 256 KiB;
        # rescan cap accommodates expansion without spurious refusal.
        prefix = "postgres://" + "user" + ":" + "a" + "@db.example.internal/app\n"
        filler = "z" * (safe_read.MAX_FILE_BYTES - len(prefix) - 1) + "\n"
        self.put("near_cap.log", prefix + filler)
        res = safe_read.safe_read(str(self.root), "near_cap.log", start_line=1, end_line=1)
        self.assertEqual(res["exit"], 0, res)
        self.assertEqual(res["text"], "postgres://" + "user:<REDACTED>@db.example.internal/app\n")


if __name__ == "__main__":
    unittest.main()
