"""Acceptance suite for M2 exfil-guard config-edit minimal CLI."""
from __future__ import annotations

from contextlib import nullcontext
import errno
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from core import audit, config_edit, exfil_audit, workspace_doctor as doctor


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/exfil-guard/scripts/edit_config.py"
DOTENV = "." + "env"

# Split high-risk synthetic literals so RepoCorpusNoise remains at zero hits.
SYNTH_GITHUB = "ghp_" + "9f8e7d6c5b4a3210fedcba9876543210abcd"
SYNTH_OPENAI = "sk-" + "AbCdEf0123456789AbCdEf0123456789"
SYNTH_URI = "postgresql" + "://svc:Alpha" + "Pass91@db.example.invalid/app"
SYNTH_ENV_REF = "${" + "API_KEY}"


def _node22_version() -> tuple[int, ...] | None:
    node = shutil.which("node")
    if not node:
        return None
    proc = subprocess.run([node, "-v"], capture_output=True, text=True, timeout=10, check=False)
    if proc.returncode != 0:
        return None
    raw = proc.stdout.strip().lstrip("v")
    try:
        parts = tuple(int(piece) for piece in raw.split(".")[:3])
    except ValueError:
        return None
    return parts if parts and parts[0] == 22 else None


def _node_parse_env(text: str) -> dict[str, str]:
    node = shutil.which("node")
    assert node is not None
    script = (
        'import { parseEnv } from "node:util"; '
        'const chunks = []; '
        'for await (const c of process.stdin) chunks.push(c); '
        'const raw = Buffer.concat(chunks).toString("utf8"); '
        'process.stdout.write(JSON.stringify(parseEnv(raw)));'
    )
    proc = subprocess.run(
        [node, "--input-type=module", "-e", script],
        input=text.encode("utf-8"),
        capture_output=True,
        timeout=15,
        check=True,
    )
    return json.loads(proc.stdout.decode("utf-8"))


class _ConfigEditFixture(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="config-edit-test-")
        self.addCleanup(tmp.cleanup)
        self.outer = Path(tmp.name).resolve()
        self.root = self.outer / "workspace"
        self.root.mkdir()
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_GUARD_")}
        self.env["AGENT_GUARD_WORKSPACE"] = str(self.root)
        self.env["AGENT_GUARD_SESSION"] = "config-edit-test-session"

    def put(self, relative: str, payload: str | bytes, *, mode: int | None = None) -> Path:
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(payload, bytes):
            target.write_bytes(payload)
        else:
            target.write_text(payload, encoding="utf-8")
        if mode is not None:
            target.chmod(mode)
        return target

    def write_policy(self, data: dict | str | bytes) -> Path:
        if isinstance(data, dict):
            payload: str | bytes = json.dumps(data, indent=2) + "\n"
        else:
            payload = data
        return self.put(config_edit.POLICY_RELPATH, payload)

    def run_cli(
        self,
        *args: str,
        env_extra: dict[str, str] | None = None,
        stdin_text: str | None = None,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess:
        env = dict(self.env)
        if env_extra:
            env.update(env_extra)
        return subprocess.run(
            [sys.executable, "-B", str(SCRIPT), *args],
            input=stdin_text,
            cwd=cwd or self.root,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=25,
        )

    def run_cli_pty(
        self,
        *args: str,
        reply: str | None = "APPLY\n",
        env_extra: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        """Run CLI with real POSIX PTY on stdin & stderr and piped stdout for clean JSON."""
        import pty
        import select

        env = dict(self.env)
        if env_extra:
            env.update(env_extra)
        master_fd, slave_fd = pty.openpty()
        try:
            proc = subprocess.Popen(
                [sys.executable, "-B", str(SCRIPT), *args],
                cwd=self.root,
                env=env,
                stdin=slave_fd,
                stdout=subprocess.PIPE,
                stderr=slave_fd,
                close_fds=True,
            )
        finally:
            os.close(slave_fd)

        pty_chunks: list[bytes] = []
        sent_reply = False
        deadline = time.monotonic() + 20.0
        try:
            while True:
                if time.monotonic() > deadline:
                    proc.kill()
                    raise TimeoutError("PTY CLI invocation timed out")
                ready, _, _ = select.select([master_fd], [], [], 0.05)
                if ready:
                    try:
                        chunk = os.read(master_fd, 4096)
                    except OSError:
                        chunk = b""
                    if chunk:
                        pty_chunks.append(chunk)
                        if not sent_reply and b"Type APPLY to replace:" in b"".join(pty_chunks):
                            sent_reply = True
                            if reply is None:
                                os.write(master_fd, b"\x04")  # Ctrl-D EOF on TTY
                            else:
                                os.write(master_fd, reply.encode("utf-8"))
                    else:
                        if proc.poll() is not None:
                            break
                elif proc.poll() is not None:
                    # Drain any remaining bytes on the PTY master.
                    while True:
                        r2, _, _ = select.select([master_fd], [], [], 0.02)
                        if not r2:
                            break
                        try:
                            tail = os.read(master_fd, 4096)
                        except OSError:
                            break
                        if not tail:
                            break
                        pty_chunks.append(tail)
                    break
            stdout_bytes = proc.stdout.read() if proc.stdout is not None else b""
            proc.wait(timeout=5)
        finally:
            if proc.stdout is not None:
                proc.stdout.close()
            os.close(master_fd)

        return (
            proc.returncode,
            stdout_bytes.decode("utf-8", errors="replace"),
            b"".join(pty_chunks).decode("utf-8", errors="replace"),
        )

    def snapshot_tree(self) -> dict[str, bytes]:
        snap: dict[str, bytes] = {}
        for base, dirs, files in os.walk(self.root):
            dirs.sort()
            for name in sorted(files):
                full = Path(base) / name
                rel = full.relative_to(self.root).as_posix()
                snap[rel] = full.read_bytes()
        return snap


class ConfigEditPlatformAndDoctorTests(_ConfigEditFixture):
    """Cross-platform tests that run unconditionally on Linux, macOS, and Windows."""

    def test_unsupported_platform_gate_runs_before_policy_lock_and_prompt(self):
        native_unsupported = sys.platform != "linux"
        ctx = (
            nullcontext() if native_unsupported
            else mock.patch.object(config_edit.os, "listxattr", None, create=True)
        )
        scripts_dir = str(ROOT / "skills/exfil-guard/scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        import edit_config as edit_cli

        with ctx:
            for mode_flag in ("--preview", "--apply"):
                with self.subTest(mode=mode_flag):
                    out = io.StringIO()
                    err = io.StringIO()
                    code = edit_cli.main(
                        [
                            "set", "config/app.json", "--key", "/app/log_level",
                            "--value", "debug", "--workspace", str(self.root),
                            mode_flag, "--json",
                        ],
                        stdin=io.StringIO(""),
                        stdout=out,
                        stderr=err,
                    )
                    self.assertEqual(code, 1)
                    body = json.loads(out.getvalue())
                    self.assertEqual(body["status"], "error")
                    self.assertEqual(body["code"], "UNSUPPORTED_PLATFORM")
                    self.assertFalse(body["replaced"])
                    self.assertFalse(body["durability_confirmed"])
                    self.assertEqual(err.getvalue(), "")
        if native_unsupported:
            for mode_flag in ("--preview", "--apply"):
                proc = self.run_cli(
                    "set", "config/app.json", "--key", "/app/log_level",
                    "--value", "debug", "--workspace", str(self.root),
                    mode_flag, "--json",
                )
                self.assertEqual(proc.returncode, 1, proc.stderr)
                body = json.loads(proc.stdout)
                self.assertEqual(body["code"], "UNSUPPORTED_PLATFORM")
                self.assertFalse(body["replaced"])
                self.assertFalse(body["durability_confirmed"])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_doctor_config_edit_policy_independent_of_write_capability(self):
        with mock.patch.object(config_edit, "supports_safe_config_edit", return_value=False):
            absent = doctor.diagnose(self.root)
            self.assertEqual(absent["checks"]["config_edit_policy"], {"status": "NOT_CONFIGURED"})
            self.assertEqual(absent["checks"]["exfil_allow"]["status"], "NOT_CONFIGURED")

            self.write_policy({
                "version": 1,
                "files": {
                    "config/app.json": {
                        "format": "json",
                        "editable_keys": ["/app/log_level", "/server/port"],
                        "locked_keys": ["/database/host"],
                    },
                    DOTENV + ".example": {
                        "format": "dotenv",
                        "editable_keys": ["APP_ENV", "LOG_LEVEL"],
                        "locked_keys": ["API_TOKEN"],
                    },
                },
            })
            valid = doctor.diagnose(self.root)
            self.assertEqual(valid["status"], "PASS")
            self.assertEqual(valid["checks"]["config_edit_policy"]["status"], "PASS")
            self.assertEqual(valid["checks"]["config_edit_policy"]["file_count"], 2)
            # Only config-edit-policy.json exists -> exfil_allow MUST stay NOT_CONFIGURED.
            self.assertEqual(valid["checks"]["exfil_allow"]["status"], "NOT_CONFIGURED")

            self.write_policy({
                "version": 1,
                "files": {
                    "config/app.json": {
                        "format": "json",
                        "editable_keys": ["/app/databaseUrl"],
                    }
                },
            })
            bad = doctor.diagnose(self.root)
            self.assertEqual(bad["status"], "FAIL")
            self.assertEqual(bad["checks"]["config_edit_policy"], {
                "status": "FAIL",
                "code": "POLICY_SENSITIVE_KEY_FORBIDDEN",
            })

            # Over 1 MiB policy fails read-only doctor.
            self.write_policy(b" " * (config_edit.MAX_POLICY_BYTES + 1))
            oversize = doctor.diagnose(self.root)
            self.assertEqual(oversize["status"], "FAIL")
            self.assertEqual(oversize["checks"]["config_edit_policy"], {
                "status": "FAIL",
                "code": "POLICY_INVALID",
            })

    def test_invalid_policy_numbers_are_reported_by_core_and_doctor(self):
        long_number = b"9" * 5000
        invalid_version = b'{"version":' + long_number + b',"files":{}}'
        invalid_key = (
            b'{"version":1,"files":{"app.json":{"format":"json","editable_keys":['
            + long_number + b']}}}'
        )
        for raw in (invalid_version, invalid_key, b'{"version":1.0,"files":{}}',
                    b'{"version":true,"files":{}}'):
            with self.assertRaises(config_edit.ConfigEditError) as caught:
                config_edit.parse_policy_bytes(raw)
            self.assertEqual(caught.exception.code, "POLICY_INVALID")

        self.write_policy(invalid_version)
        proc = subprocess.run(
            [sys.executable, "-B", str(ROOT / "doctor.py"), "workspace", "--json",
             "--workspace", str(self.root)],
            cwd=self.root, env=self.env, capture_output=True, text=True, encoding="utf-8", timeout=25,
        )
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertEqual(proc.stderr, "")
        report = json.loads(proc.stdout)
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["checks"]["config_edit_policy"], {
            "status": "FAIL", "code": "POLICY_INVALID",
        })

    def test_json_pointer_escapes_and_sensitive_key_wordlist(self):
        self.assertEqual(config_edit.parse_json_pointer("/a~1b/c~0d"), ("a/b", "c~d"))
        self.assertEqual(config_edit.parse_json_pointer("/~01/~10"), ("~1", "/0"))
        self.assertEqual(config_edit.parse_json_pointer("/items/0"), ("items", "0"))
        for bad_ptr in ("", "no/leading/slash", "/bad~2escape", "/trailing~", "/a/b~\x00"):
            with self.assertRaises(ValueError):
                config_edit.parse_json_pointer(bad_ptr)

        sensitive_samples = [
            ("SECRET",), ("my_secret_name",), ("accessToken",), ("dbPassword",),
            ("user_passwd",), ("svc_pwd",), ("api_key",), ("apiKey",), ("apikey",),
            ("auth", "timeout"), ("credential_id",), ("private_mode",), ("tls_cert",),
            ("cookie_domain",), ("session", "ttl"), ("jwt_issuer",), ("bearer_prefix",),
            ("signing_alg",), ("sentry_dsn",), ("DATABASE_URL",), ("databaseUrl",),
            ("app_databaseurl",), ("db_connstr",), ("dbConnStr",),
            ("CONNECTION_STRING",), ("connectionString",), ("connectionstring",),
            ("api-key",), ("token2",),
        ]
        for sample in sensitive_samples:
            with self.subTest(sensitive=sample):
                self.assertTrue(config_edit.is_sensitive_key(sample))

        benign_samples = [
            ("app", "log_level"), ("server", "port"), ("features", "dark_mode"),
            ("monkey_count",), ("authors",), ("keyboard_layout",),
            ("max_tokens",), ("timeout_ms",), ("database", "host"),
        ]
        for sample in benign_samples:
            with self.subTest(benign=sample):
                self.assertFalse(config_edit.is_sensitive_key(sample))


class ConfigEditDotenvNode22ReferenceTests(_ConfigEditFixture):
    """Cross-check strict dotenv subset against Node 22 node:util.parseEnv."""

    def _require_or_skip_node22(self):
        ver = _node22_version()
        if ver is None:
            if os.environ.get("AGENT_GUARD_REQUIRE_NODE22") == "1":
                self.fail("Node 22 is required when AGENT_GUARD_REQUIRE_NODE22=1")
            self.skipTest("Node 22 unavailable")

    def test_supported_dotenv_fixtures_match_node22_before_and_after_edit(self):
        self._require_or_skip_node22()
        fixtures = [
            ("plain_lf", "APP_ENV=dev\nLOG_LEVEL=info\n", "LOG_LEVEL", "debug"),
            ("export_and_spaces", "  export APP_ENV =  dev\nLOG_LEVEL\t=\tinfo\n", "APP_ENV", "staging"),
            ("crlf_comments_no_eof_nl", "# header\r\n\r\nAPP_ENV=dev\r\nLOG_LEVEL=info", "APP_ENV", "prod"),
            ("bare_empty_to_value", "APP_ENV=\nLOG_LEVEL=info\n", "APP_ENV", "qa"),
            ("bare_empty_at_eof", "LOG_LEVEL=info\nAPP_ENV=", "APP_ENV", "prod"),
            ("export_quoted_empty", "export APP_ENV=''\r\nLOG_LEVEL=info\r\n", "APP_ENV", "qa"),
            ("bare_value_to_quoted_empty", "APP_ENV=dev\nLOG_LEVEL=info\n", "APP_ENV", ""),
            ("double_quoted_empty_preserved", 'APP_ENV=""\nLOG_LEVEL=info\n', "APP_ENV", ""),
            ("single_quoted_empty_preserved", "APP_ENV=''\nLOG_LEVEL=info\n", "APP_ENV", ""),
            ("leading_ws_before_quoted_and_bare", 'APP_ENV =  "dev #1"\nLOG_LEVEL =  info\n', "APP_ENV", "prod #2"),
            ("quote_flip_single_to_double", "APP_ENV='dev'\nLOG_LEVEL=info\n", "APP_ENV", "it's live"),
            ("quote_flip_double_to_single", 'APP_ENV="dev"\nLOG_LEVEL=info\n', "APP_ENV", 'say "ok"'),
            ("quoted_tab_and_utf8", 'APP_ENV="a\tb"\nLOG_LEVEL="中文"\n', "APP_ENV", "中文\t标签 #ok"),
            ("bare_to_quoted_when_spaces", "APP_ENV=dev\nLOG_LEVEL=info\n", "APP_ENV", "two words"),
        ]
        for name, raw_text, target_key, new_val in fixtures:
            with self.subTest(fixture=name):
                raw_bytes = raw_text.encode("utf-8")
                order, entries = config_edit._parse_dotenv_bytes(raw_bytes)
                ours_before = {k: entries[k].value for k in order}
                node_before = _node_parse_env(raw_text)
                self.assertEqual(ours_before, node_before)

                target = entries[target_key]
                _, _, new_bytes = config_edit._plan_dotenv_replacement(raw_bytes, target_key, new_val)
                # Bytes outside target span are byte-identical.
                prefix_len = target.val_start
                suffix_len = len(raw_bytes) - target.val_end
                self.assertEqual(new_bytes[:prefix_len], raw_bytes[:prefix_len])
                if suffix_len:
                    self.assertEqual(new_bytes[-suffix_len:], raw_bytes[-suffix_len:])

                new_text = new_bytes.decode("utf-8")
                new_order, new_entries = config_edit._parse_dotenv_bytes(new_bytes)
                ours_after = {k: new_entries[k].value for k in new_order}
                node_after = _node_parse_env(new_text)
                self.assertEqual(ours_after, node_after)
                expected = dict(node_before)
                expected[target_key] = new_val
                self.assertEqual(node_after, expected)

    def test_node22_whitespace_swallow_boundary_and_out_of_subset_forms_rejected(self):
        self._require_or_skip_node22()
        # Confirm Node 22 actually swallows the next line on unquoted whitespace-only RHS.
        swallowed = _node_parse_env("APP_ENV=   \nLOG_LEVEL=info\n")
        self.assertEqual(swallowed, {"APP_ENV": "LOG_LEVEL=info"})

        rejected_files = [
            b"APP_ENV=   \nLOG_LEVEL=info\n",
            b"APP_ENV=\t\n",
            b"export APP_ENV=\nLOG_LEVEL=info\n",
            b" export APP_ENV=\r\n",
            b"export APP_ENV=",
            b"__proto__=dev\nLOG_LEVEL=info\n",
            b"APP_ENV=dev # inline comment\n",
            b'APP_ENV="dev" # inline comment\n',
            b"APP_ENV=dev#hash\n",
            b"export\tAPP_ENV=dev\n",
            b"APP_ENV=dev\nAPP_ENV=prod\n",
            b'APP_ENV="unclosed\n',
            b'APP_ENV="has' + b"\x5c" + b'escape"\n',
            b'APP_ENV="has$var"\n',
            b'APP_ENV="has`cmd`"\n',
            b"\xef\xbb\xbfAPP_ENV=dev\n",
            b"APP_ENV=dev\rLOG_LEVEL=info\n",
            b"APP_ENV=two words\n",
            b"APP_ENV=dev\x00\n",
        ]
        for payload in rejected_files:
            with self.subTest(payload=payload):
                with self.assertRaises(config_edit.ConfigEditError) as caught:
                    config_edit._parse_dotenv_bytes(payload)
                self.assertEqual(caught.exception.code, "INVALID_FORMAT")

        with self.assertRaises(config_edit.ConfigEditError) as caught:
            config_edit.parse_policy_bytes(
                b'{"version":1,"files":{"settings":{"format":"dotenv","editable_keys":["__proto__"]}}}'
            )
        self.assertEqual(caught.exception.code, "POLICY_INVALID")

        # Out-of-subset new values for dotenv.
        for bad_val in ("a" + chr(92) + "b", "a$b", "a`b", "line1\nline2", "cr\rhere", "bell\x07", "both ' and \""):
            with self.subTest(new_val=bad_val):
                with self.assertRaises(config_edit.ConfigEditError) as caught:
                    config_edit.validate_new_value(bad_val, "string", "dotenv", str(self.root))
                self.assertEqual(caught.exception.code, "VALUE_INVALID")


@unittest.skipUnless(config_edit.supports_safe_config_edit(), "POSIX dir_fd required")
class ConfigEditPolicyAndBoundaryTests(_ConfigEditFixture):
    """Matrix item 1: policy validation, JSON Pointer/array rules, and filesystem boundaries."""

    def test_policy_failures_and_authorization_order(self):
        target = self.put("config/app.json", '{"app": {"log_level": "info"}}\n')
        orig = target.read_bytes()

        # Missing policy.
        proc = self.run_cli(
            "set", "config/app.json", "--key", "/app/log_level", "--value", "debug", "--json",
        )
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(json.loads(proc.stdout)["code"], "POLICY_MISSING")

        # Corrupt, duplicate key, unknown field, bad version, overlap, sensitive key.
        bad_policies = [
            ("{not json", "POLICY_INVALID"),
            ('{"version": 1, "version": 1, "files": {}}', "POLICY_INVALID"),
            ('{"version": 1, "files": {}, "extra": true}', "POLICY_INVALID"),
            ('{"version": 2, "files": {}}', "POLICY_INVALID"),
            (b'{"version":' + b"9" * 5000 + b',"files":{}}', "POLICY_INVALID"),
            (json.dumps({"version": 1, "files": {"config/app.json": {
                "format": "json", "editable_keys": ["/a", "/a"],
            }}}), "POLICY_INVALID"),
            (json.dumps({"version": 1, "files": {"config/app.json": {
                "format": "json", "editable_keys": ["/a"], "locked_keys": ["/a"],
            }}}), "POLICY_INVALID"),
            (json.dumps({"version": 1, "files": {"config/app.json": {
                "format": "json", "editable_keys": ["/auth/timeout"],
            }}}), "POLICY_SENSITIVE_KEY_FORBIDDEN"),
            (json.dumps({"version": 1, "files": {DOTENV + ".local": {
                "format": "dotenv", "editable_keys": ["DATABASE_URL"],
            }}}), "POLICY_SENSITIVE_KEY_FORBIDDEN"),
            (json.dumps({"version": 1, "files": {"config/app.json": {
                "format": "json", "editable_keys": ["/app/databaseUrl"],
            }}}), "POLICY_SENSITIVE_KEY_FORBIDDEN"),
            (json.dumps({"version": 1, "files": {".git/config": {
                "format": "dotenv", "editable_keys": ["APP_ENV"],
            }}}), "POLICY_INVALID"),
            (json.dumps({"version": 1, "files": {".GIT/config": {
                "format": "dotenv", "editable_keys": ["APP_ENV"],
            }}}), "POLICY_INVALID"),
            (json.dumps({"version": 1, "files": {".agent-guard/x.json": {
                "format": "json", "editable_keys": ["/a"],
            }}}), "POLICY_INVALID"),
            (json.dumps({"version": 1, "files": {".agent-trash/x.json": {
                "format": "json", "editable_keys": ["/a"],
            }}}), "POLICY_INVALID"),
            (json.dumps({"version": 1, "files": {".AGENT-TRASH/x.json": {
                "format": "json", "editable_keys": ["/a"],
            }}}), "POLICY_INVALID"),
        ]
        for payload, expected_code in bad_policies:
            with self.subTest(expected=expected_code, payload=payload[:48]):
                self.write_policy(payload)
                proc = self.run_cli(
                    "set", "config/app.json", "--key", "/app/log_level", "--value", "debug", "--json",
                )
                self.assertEqual(proc.returncode, 2)
                self.assertEqual(json.loads(proc.stdout)["code"], expected_code)

        # Custom quarantine path inside workspace refused by policy.
        custom_trash = self.root / "my-trash"
        self.write_policy({
            "version": 1,
            "files": {"my-trash/app.json": {"format": "json", "editable_keys": ["/a"]}},
        })
        proc = self.run_cli(
            "set", "my-trash/app.json", "--key", "/a", "--value", "x", "--json",
            env_extra={"AGENT_GUARD_TRASH": str(custom_trash)},
        )
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(json.loads(proc.stdout)["code"], "POLICY_INVALID")

        # Valid policy: test FILE_NOT_ALLOWED, FORMAT_MISMATCH, KEY_LOCKED, KEY_NOT_ALLOWED.
        self.write_policy({
            "version": 1,
            "files": {
                "config/app.json": {
                    "format": "json",
                    "editable_keys": ["/app/log_level"],
                    "locked_keys": ["/auth/token"],
                }
            },
        })
        cases = [
            (("other.json", "--key", "/app/log_level", "--value", "x"), "FILE_NOT_ALLOWED"),
            (("config/app.json", "--key", "/app/log_level", "--value", "x", "--format", "dotenv"), "FORMAT_MISMATCH"),
            (("config/app.json", "--key", "/auth/token", "--value", "x"), "KEY_LOCKED"),
            (("config/app.json", "--key", "/app/other", "--value", "x"), "KEY_NOT_ALLOWED"),
        ]
        for extra_args, expected in cases:
            with self.subTest(expected=expected):
                proc = self.run_cli("set", *extra_args, "--json")
                self.assertEqual(proc.returncode, 2)
                self.assertEqual(json.loads(proc.stdout)["code"], expected)
        self.assertEqual(target.read_bytes(), orig)

    def test_json_pointer_escapes_numeric_object_key_and_array_refusal(self):
        self.write_policy({
            "version": 1,
            "files": {
                "cfg.json": {
                    "format": "json",
                    "editable_keys": ["/a~1b/c~0d", "/obj/0", "/arr/0", "/arr", "/nested"],
                }
            },
        })
        target = self.put(
            "cfg.json",
            '{"a/b": {"c~d": "v1"}, "obj": {"0": "zero"}, "arr": ["first"], "nested": {"x": 1}}\n',
        )
        orig = target.read_bytes()

        # Escaped pointer and numeric object key succeed in preview & apply.
        for ptr, val in (("/a~1b/c~0d", "v2"), ("/obj/0", "one")):
            rc, out, _ = self.run_cli_pty(
                "set", "cfg.json", "--key", ptr, "--value", val, "--apply", "--json",
            )
            self.assertEqual(rc, 0, out)
            self.assertEqual(json.loads(out)["code"], "APPLIED")
        updated = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(updated["a/b"]["c~d"], "v2")
        self.assertEqual(updated["obj"]["0"], "one")
        self.assertEqual(updated["arr"], ["first"])

        # Array element access and non-scalar targets refused without modifying file.
        after_good = target.read_bytes()
        for ptr, expected in (
            ("/arr/0", "TARGET_NOT_FOUND"),
            ("/arr", "TARGET_NOT_SCALAR"),
            ("/nested", "TARGET_NOT_SCALAR"),
        ):
            proc = self.run_cli("set", "cfg.json", "--key", ptr, "--value", "x", "--json")
            self.assertEqual(proc.returncode, 2)
            self.assertEqual(json.loads(proc.stdout)["code"], expected)
        self.assertEqual(target.read_bytes(), after_good)
        self.assertNotEqual(after_good, orig)

    def test_symlinks_and_hardlinks_are_rejected(self):
        self.write_policy({
            "version": 1,
            "files": {"config/app.json": {"format": "json", "editable_keys": ["/a"]}},
        })
        # Symlink target refused.
        outside = self.outer / "outside.json"
        outside.write_text('{"a": "old"}\n', encoding="utf-8")
        (self.root / "config").mkdir()
        link = self.root / "config/app.json"
        link.symlink_to(outside)
        proc = self.run_cli("set", "config/app.json", "--key", "/a", "--value", "new", "--json")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(json.loads(proc.stdout)["code"], "UNSAFE_FILE")
        self.assertEqual(outside.read_text(encoding="utf-8"), '{"a": "old"}\n')
        link.unlink()

        # Hardlinked target refused.
        real = self.put("config/app.json", '{"a": "old"}\n')
        hlink = self.root / "config/hardlink.json"
        os.link(real, hlink)
        proc = self.run_cli("set", "config/app.json", "--key", "/a", "--value", "new", "--json")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(json.loads(proc.stdout)["code"], "UNSAFE_FILE")

        # A hardlinked policy must fail before the target is opened.
        policy_path = self.root / config_edit.POLICY_RELPATH
        policy_copy = self.root / ".agent-guard/policy-copy.json"
        os.link(policy_path, policy_copy)
        proc = self.run_cli("set", "config/app.json", "--key", "/a", "--value", "new", "--json")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(json.loads(proc.stdout)["code"], "POLICY_INVALID")

    def test_encoding_limits_and_metadata_boundaries(self):
        self.write_policy({
            "version": 1,
            "files": {"config/app.json": {"format": "json", "editable_keys": ["/a"]}},
        })
        real = self.put("config/app.json", b"")
        # Invalid encoding, oversize, depth > 16, nodes > 2048, duplicate JSON key, lone surrogate, NaN.
        bad_targets = [
            (b'{"a": "\xff"}\n', "INVALID_ENCODING"),
            (b'{"a": "' + b"x" * config_edit.MAX_FILE_BYTES + b'"}\n', "FILE_TOO_LARGE"),
            (('{"a": "ok", "d": ' + "[" * 17 + "0" + "]" * 17 + "}").encode(), "STRUCTURE_TOO_DEEP"),
            (('{"a": "ok", "n": [' + "0," * config_edit.MAX_NODES + "0]}").encode(), "STRUCTURE_TOO_LARGE"),
            (b'{"a": "1", "' + b"\x5c" + b'u0061": "2"}\n', "INVALID_FORMAT"),
            (b'{"a": "ok", "b": "' + b"\x5c" + b'ud800"}\n', "INVALID_FORMAT"),
            (b'{"a": "ok", "b": NaN}\n', "INVALID_FORMAT"),
            (b'{"a": "ok",}\n', "INVALID_FORMAT"),
            (b'/* comment */ {"a": "ok"}\n', "INVALID_FORMAT"),
        ]
        for payload, expected in bad_targets:
            with self.subTest(expected=expected):
                real.write_bytes(payload)
                proc = self.run_cli("set", "config/app.json", "--key", "/a", "--value", "new", "--json")
                self.assertEqual(proc.returncode, 2)
                self.assertEqual(json.loads(proc.stdout)["code"], expected)
                self.assertEqual(real.read_bytes(), payload)

        real.write_text('{"a": "old"}\n', encoding="utf-8")

        # Special mode bits (sticky) and xattrs refused with UNSUPPORTED_FILE_METADATA.
        real.chmod(0o1644)
        try:
            if stat.S_IMODE(real.stat().st_mode) & 0o7000:
                proc = self.run_cli("set", "config/app.json", "--key", "/a", "--value", "new", "--json")
                self.assertEqual(proc.returncode, 2)
                self.assertEqual(json.loads(proc.stdout)["code"], "UNSUPPORTED_FILE_METADATA")
        finally:
            real.chmod(0o644)

        for bad_attrs in (["system.posix_acl_access"], ["user.custom"], ["security.selinux", "user.x"]):
            with mock.patch.object(config_edit.os, "listxattr", return_value=bad_attrs):
                with self.assertRaises(config_edit.ConfigEditError) as caught:
                    config_edit.preview_edit(str(self.root), "config/app.json", key="/a", value="new")
                self.assertEqual(caught.exception.code, "UNSUPPORTED_FILE_METADATA")

        with mock.patch.object(config_edit.os, "listxattr", return_value=["security.selinux"]), \
                mock.patch.object(
                    config_edit.os, "getxattr",
                    side_effect=[b"u:object_r:custom_t:s0\x00", b"u:object_r:default_t:s0\x00"],
                ):
            with self.assertRaises(config_edit.ConfigEditError) as caught:
                config_edit.preview_edit(str(self.root), "config/app.json", key="/a", value="new")
            self.assertEqual(caught.exception.code, "UNSUPPORTED_FILE_METADATA")

        with mock.patch.object(config_edit.os, "listxattr", side_effect=OSError(errno.ENOTSUP, "xattrs unavailable")):
            with self.assertRaises(config_edit.ConfigEditError) as caught:
                config_edit.preview_edit(str(self.root), "config/app.json", key="/a", value="new")
            self.assertEqual(caught.exception.code, "UNSUPPORTED_FILE_METADATA")

        with mock.patch.object(config_edit.os, "geteuid", return_value=real.stat().st_uid + 1):
            with self.assertRaises(config_edit.ConfigEditError) as caught:
                config_edit.preview_edit(str(self.root), "config/app.json", key="/a", value="new")
            self.assertEqual(caught.exception.code, "UNSUPPORTED_FILE_METADATA")


@unittest.skipUnless(config_edit.supports_safe_config_edit(), "POSIX dir_fd required")
class ConfigEditConfidentialityAndInteractionTests(_ConfigEditFixture):
    """Matrix item 2: byte-for-byte confidentiality regressions, fixed arg errors, and zero-write checks."""

    def test_unregistered_and_locked_keys_three_state_byte_indistinguishable(self):
        policy = {
            "version": 1,
            "files": {
                "app.json": {
                    "format": "json",
                    "editable_keys": ["/app/mode"],
                    "locked_keys": ["/secret_slot"],
                },
                DOTENV + ".sample": {
                    "format": "dotenv",
                    "editable_keys": ["APP_MODE"],
                    "locked_keys": ["LOCKED_SLOT"],
                },
            },
        }
        json_states = [
            '{"app": {"mode": "dev"}}\n',
            '{"app": {"mode": "dev"}, "secret_slot": "", "unreg_slot": ""}\n',
            f'{{"app": {{"mode": "dev"}}, "secret_slot": "{SYNTH_GITHUB}", "unreg_slot": "{SYNTH_OPENAI}"}}\n',
        ]
        dotenv_states = [
            "APP_MODE=dev\n",
            'APP_MODE=dev\nLOCKED_SLOT=""\nUNREG_SLOT=""\n',
            f'APP_MODE=dev\nLOCKED_SLOT="{SYNTH_GITHUB}"\nUNREG_SLOT="{SYNTH_OPENAI}"\n',
        ]
        for rel, key, states in (
            ("app.json", "/secret_slot", json_states),
            ("app.json", "/unreg_slot", json_states),
            (DOTENV + ".sample", "LOCKED_SLOT", dotenv_states),
            (DOTENV + ".sample", "UNREG_SLOT", dotenv_states),
        ):
            for mode in ("--preview", "--apply"):
                observed: list[tuple[int, str]] = []
                for content in states:
                    self.write_policy(policy)
                    self.put(rel, content)
                    before_tree = self.snapshot_tree()
                    if mode == "--preview":
                        proc = self.run_cli("set", rel, "--key", key, "--value", "benign", "--preview", "--json")
                        rc, out, err = proc.returncode, proc.stdout, proc.stderr
                    else:
                        rc, out, err = self.run_cli_pty(
                            "set", rel, "--key", key, "--value", "benign", "--apply", "--json",
                        )
                    self.assertEqual(rc, 2)
                    self.assertEqual(self.snapshot_tree(), before_tree)
                    self.assertNotIn(SYNTH_GITHUB, out + err)
                    self.assertNotIn(SYNTH_OPENAI, out + err)
                    observed.append((rc, out))
                self.assertEqual(observed[0], observed[1], (rel, key, mode))
                self.assertEqual(observed[1], observed[2], (rel, key, mode))

    def test_whitelisted_key_same_vs_different_old_value_indistinguishable(self):
        policy = {
            "version": 1,
            "files": {
                "app.json": {
                    "format": "json",
                    "editable_keys": ["/app/log_level"],
                    "locked_keys": ["/adjacent/secret"],
                }
            },
        }
        # Two workspaces: Workspace A has old == new ("info"); Workspace B has old != new ("warn-old-val").
        # Adjacent locked sibling holds distinct high-risk synthetic secrets.
        files = [
            (f'{{"app": {{"log_level": "info"}}, "adjacent": {{"secret": "{SYNTH_GITHUB}"}}}}\n', "info"),
            (f'{{"app": {{"log_level": "warn-old-val"}}, "adjacent": {{"secret": "{SYNTH_OPENAI}"}}}}\n', "warn-old-val"),
        ]
        preview_receipts: list[tuple[int, str]] = []
        apply_receipts: list[tuple[int, str]] = []
        apply_stderrs: list[str] = []
        audit_projections: list[str] = []

        for idx, (content, old_val) in enumerate(files):
            ws = self.outer / f"conf-ws-{idx}"
            ws.mkdir()
            (ws / config_edit.POLICY_DIR).mkdir()
            (ws / config_edit.POLICY_RELPATH).write_text(json.dumps(policy), encoding="utf-8")
            (ws / "app.json").write_text(content, encoding="utf-8")
            env_extra = {"AGENT_GUARD_WORKSPACE": str(ws)}

            # Preview
            p = self.run_cli(
                "set", "app.json", "--key", "/app/log_level", "--value", "info",
                "--workspace", str(ws), "--preview", "--json", env_extra=env_extra,
            )
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertFalse((ws / ".agent-guard" / config_edit.LOCK_FILE).exists())
            self.assertFalse((ws / ".agent-trash").exists())
            preview_receipts.append((p.returncode, p.stdout))

            # Apply via PTY
            rc, out, err = self.run_cli_pty(
                "set", "app.json", "--key", "/app/log_level", "--value", "info",
                "--workspace", str(ws), "--apply", "--json", env_extra=env_extra,
            )
            self.assertEqual(rc, 0, out + err)
            receipt = json.loads(out)
            self.assertIn("audit_id", receipt)
            stripped_receipt = dict(receipt)
            stripped_receipt.pop("audit_id")
            apply_receipts.append((rc, json.dumps(stripped_receipt, sort_keys=True)))
            apply_stderrs.append(err)

            audit_path = ws / ".agent-trash/audit.jsonl"
            rows = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 1)
            row = dict(rows[0])
            self.assertEqual(row["tool"], "exfil_config_edit")
            row.pop("ts")
            row.pop("check_id")
            audit_projections.append(json.dumps(row, sort_keys=True))

            surface = p.stdout + p.stderr + out + err + audit_path.read_text(encoding="utf-8")
            for forbidden in (old_val, "info", SYNTH_GITHUB, SYNTH_OPENAI):
                if forbidden != "info" or idx == 1:
                    self.assertNotIn(forbidden, surface)
            # Neither new value nor comparison/offset/fingerprint fields leak anywhere.
            self.assertNotIn("info", surface)
            for forbidden_field in ("changed", "unchanged", "line", "offset", "span", "fingerprint", "hash"):
                self.assertNotIn(forbidden_field, receipt)
                self.assertNotIn(forbidden_field, rows[0])
            for audit_forbidden in ("file", "path", "key", "value", "before_type", "after_type"):
                self.assertNotIn(audit_forbidden, rows[0])

        self.assertEqual(preview_receipts[0], preview_receipts[1])
        self.assertEqual(apply_receipts[0], apply_receipts[1])
        self.assertEqual(apply_stderrs[0], apply_stderrs[1])
        self.assertEqual(audit_projections[0], audit_projections[1])

    def test_fixed_arg_errors_non_tty_cancel_and_unsafe_label_are_read_only(self):
        self.write_policy({
            "version": 1,
            "files": {
                "app.json": {"format": "json", "editable_keys": ["/a"]},
                "bad\u202ename.json": {"format": "json", "editable_keys": ["/a"]},
            },
        })
        self.put("app.json", '{"a": "old"}\n')
        self.put("bad\u202ename.json", '{"a": "old"}\n')
        before = self.snapshot_tree()

        # Invalid arguments never echo secret values.
        bad_invocations = [
            ("set", "app.json", "--key", "/a", "--unknown", SYNTH_GITHUB, "--json"),
            ("set", "app.json", "--key", "/a", "--type", "badtype", "--value", SYNTH_GITHUB, "--json"),
            ("set", "app.json", "--key", "/a", "--value", "x", "--preview", "--apply", "--json"),
            ("set", "app.json", "--key", "/a", "--type", "string", "--json"),
            ("unknown-sub", SYNTH_GITHUB, "--json"),
        ]
        for args in bad_invocations:
            proc = self.run_cli(*args)
            self.assertEqual(proc.returncode, 2)
            self.assertEqual(json.loads(proc.stdout)["code"], "INVALID_ARGUMENT")
            self.assertNotIn(SYNTH_GITHUB, proc.stdout + proc.stderr)

        # Non-TTY --apply fails with TTY_REQUIRED.
        non_tty = self.run_cli(
            "set", "app.json", "--key", "/a", "--value", "new", "--apply", "--json", stdin_text="APPLY\n",
        )
        self.assertEqual(non_tty.returncode, 2)
        self.assertEqual(json.loads(non_tty.stdout)["code"], "TTY_REQUIRED")

        # PTY cancellation variants must all fail with APPROVAL_CANCELLED.
        for reply in ("apply\n", "APPLY \n", " APPLY\n", "yes\n", "\n", None):
            rc, out, _ = self.run_cli_pty(
                "set", "app.json", "--key", "/a", "--value", "new", "--apply", "--json", reply=reply,
            )
            self.assertEqual(rc, 2)
            self.assertEqual(json.loads(out)["code"], "APPROVAL_CANCELLED")

        # Unsafe preview label refused.
        unsafe = self.run_cli(
            "set", "bad\u202ename.json", "--key", "/a", "--value", "new", "--json",
        )
        self.assertEqual(unsafe.returncode, 2)
        self.assertEqual(json.loads(unsafe.stdout)["code"], "UNSAFE_PREVIEW_LABEL")

        # Zero lock, zero audit, zero target modification across all read-only paths.
        self.assertEqual(self.snapshot_tree(), before)
        self.assertFalse((self.root / ".agent-guard" / config_edit.LOCK_FILE).exists())
        self.assertFalse((self.root / ".agent-trash").exists())


@unittest.skipUnless(config_edit.supports_safe_config_edit(), "POSIX dir_fd required")
class ConfigEditFidelityAndNewValueTests(_ConfigEditFixture):
    """Matrix items 3 & 4: surgical fidelity, re-parse verification, and two-layer new-value rejection."""

    def test_json_byte_type_precision_preservation_and_reparse_fault_injection(self):
        self.write_policy({
            "version": 1,
            "files": {
                "cfg.json": {
                    "format": "json",
                    "editable_keys": ["/num", "/flag", "/label", "/opt"],
                }
            },
        })
        raw = (
            '{\r\n'
            '  "keep": "中文 🚀",\r\n'
            '  "num":  9007199254740992,\r\n'
            '  "flag": 1,\r\n'
            '  "label": "",\r\n'
            '  "opt": "set-now"\r\n'
            '}\r\n'
        )
        target = self.put("cfg.json", raw.encode("utf-8"))

        # High-precision integer beyond IEEE-754 float53 preserved literally.
        rc, out, _ = self.run_cli_pty(
            "set", "cfg.json", "--key", "/num", "--value", "9007199254740993",
            "--type", "number", "--apply", "--json",
        )
        self.assertEqual(rc, 0, out)
        # Switch 1 (number) -> true (boolean): explicit true vs 1 distinction.
        rc, out, _ = self.run_cli_pty(
            "set", "cfg.json", "--key", "/flag", "--value", "true",
            "--type", "boolean", "--apply", "--json",
        )
        self.assertEqual(rc, 0, out)
        # Empty string -> non-empty string.
        rc, out, _ = self.run_cli_pty(
            "set", "cfg.json", "--key", "/label", "--value", "你好", "--apply", "--json",
        )
        self.assertEqual(rc, 0, out)
        # Set string -> null with omitted --value.
        rc, out, _ = self.run_cli_pty(
            "set", "cfg.json", "--key", "/opt", "--type", "null", "--apply", "--json",
        )
        self.assertEqual(rc, 0, out)

        expected = (
            '{\r\n'
            '  "keep": "中文 🚀",\r\n'
            '  "num":  9007199254740993,\r\n'
            '  "flag": true,\r\n'
            '  "label": "你好",\r\n'
            '  "opt": null\r\n'
            '}\r\n'
        ).encode("utf-8")
        self.assertEqual(target.read_bytes(), expected)

        # Fault-inject re-parse divergence (boolean true misread as number 1) -> refuse write.
        orig_parse = config_edit._parse_json_bytes
        calls = {"n": 0}

        def diverging_parse(data: bytes, **kwargs):
            calls["n"] += 1
            node = orig_parse(data, **kwargs)
            if not kwargs.get("policy_mode") and calls["n"] >= 2:
                # Tamper the re-parsed /flag node to number "1" instead of boolean True.
                members = dict(node.value)
                old_flag = members["flag"]
                members["flag"] = config_edit.JsonNode("number", "1", old_flag.start, old_flag.end)
                return config_edit.JsonNode("object", members, node.start, node.end)
            return node

        with mock.patch.object(config_edit, "_parse_json_bytes", side_effect=diverging_parse):
            with self.assertRaises(config_edit.ConfigEditError) as caught:
                config_edit.preview_edit(
                    str(self.root), "cfg.json", key="/flag", value="true", value_type="boolean",
                )
            self.assertEqual(caught.exception.code, "VERIFICATION_FAILED")
        self.assertEqual(target.read_bytes(), expected)

    def test_new_value_two_layer_rejection_ignores_workspace_exemption(self):
        self.write_policy({
            "version": 1,
            "files": {
                "cfg.json": {"format": "json", "editable_keys": ["/a"]},
                DOTENV + ".cfg": {"format": "dotenv", "editable_keys": ["APP_VAL"]},
            },
        })
        self.put("cfg.json", '{"a": "safe"}\n')
        self.put(DOTENV + ".cfg", "APP_VAL=safe\n")

        host_path = os.path.join(os.path.sep, "home", "alice", "private", "export.txt")
        digest = hashlib.sha256(host_path.encode("utf-8")).hexdigest()
        # Add a workspace exemption attempting to exempt path rules & this exact path.
        self.put(
            ".agent-guard/exfil-allow.toml",
            f'[rules]\ndisable = ["path/host-absolute", "path/generic-absolute"]\n'
            f'[values]\nsha256 = ["sha256:{digest}"]\n',
        )

        # Layer 1: T1 secrets, URI password, source ref, and exempted host path -> VALUE_REJECTED_BY_SCAN.
        for bad in (SYNTH_GITHUB, SYNTH_OPENAI, SYNTH_URI, SYNTH_ENV_REF, host_path):
            with self.subTest(scan_bad=bad[:12]):
                proc = self.run_cli("set", "cfg.json", "--key", "/a", "--value", bad, "--json")
                self.assertEqual(proc.returncode, 2)
                self.assertEqual(json.loads(proc.stdout)["code"], "VALUE_REJECTED_BY_SCAN")
                self.assertNotIn(bad, proc.stdout + proc.stderr)

        # Layer 2: redaction placeholder literals -> VALUE_REJECTED_PLACEHOLDER.
        for placeholder in (
            "<REDACTED>", "<PATH>", "[REDACTED]", "<redacted>",
            "sk-<REDACTED>", "prefix **** suffix", "••••",
        ):
            with self.subTest(placeholder=placeholder):
                proc = self.run_cli("set", "cfg.json", "--key", "/a", "--value", placeholder, "--json")
                self.assertEqual(proc.returncode, 2)
                self.assertEqual(json.loads(proc.stdout)["code"], "VALUE_REJECTED_PLACEHOLDER")
                self.assertNotIn(placeholder, proc.stdout + proc.stderr)

        # Invalid typed literals & dotenv non-string rejection.
        for extra in (
            ("cfg.json", "--key", "/a", "--type", "number", "--value", "01"),
            ("cfg.json", "--key", "/a", "--type", "number", "--value", "+1"),
            ("cfg.json", "--key", "/a", "--type", "number", "--value", "NaN"),
            ("cfg.json", "--key", "/a", "--type", "boolean", "--value", "yes"),
            ("cfg.json", "--key", "/a", "--type", "null", "--value", "nope"),
            (DOTENV + ".cfg", "--key", "APP_VAL", "--type", "number", "--value", "1"),
        ):
            proc = self.run_cli("set", *extra, "--json")
            self.assertEqual(proc.returncode, 2)
            self.assertEqual(json.loads(proc.stdout)["code"], "INVALID_ARGUMENT")


@unittest.skipUnless(config_edit.supports_safe_config_edit(), "POSIX dir_fd required")
class ConfigEditConcurrencyAndFaultTests(_ConfigEditFixture):
    """Matrix items 5 & 6: stale previews, two-writer serialization, POSIX metadata, and fault injection."""

    def _setup_basic_json(self, mode: int = 0o640) -> Path:
        self.write_policy({
            "version": 1,
            "files": {
                "sub/app.json": {
                    "format": "json",
                    "editable_keys": ["/a", "/b"],
                }
            },
        })
        return self.put("sub/app.json", '{\n  "a": "a1",\n  "b": "b1"\n}\n', mode=mode)

    def test_stale_preview_target_policy_and_parent_changes_and_two_writers(self):
        target = self._setup_basic_json()

        # 1. Target modified after preview -> FILE_CHANGED.
        snap, _ = config_edit.preview_edit(str(self.root), "sub/app.json", key="/a", value="a2")
        target.write_text('{\n  "a": "a1",\n  "b": "external"\n}\n', encoding="utf-8")
        receipt, exit_code = config_edit.apply_preview(snap)
        self.assertEqual((exit_code, receipt["code"], receipt["replaced"]), (1, "FILE_CHANGED", False))
        self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["b"], "external")
        self.assertEqual([p.name for p in target.parent.iterdir()], ["app.json"])

        # 2. Policy modified after preview -> POLICY_CHANGED.
        snap, _ = config_edit.preview_edit(str(self.root), "sub/app.json", key="/a", value="a2")
        self.write_policy({
            "version": 1,
            "files": {"sub/app.json": {"format": "json", "editable_keys": ["/a", "/b"]}},
        })
        # Ensure mtime/content differs by adding trailing space.
        pol_path = self.root / config_edit.POLICY_RELPATH
        pol_path.write_text(pol_path.read_text(encoding="utf-8") + " ", encoding="utf-8")
        receipt, exit_code = config_edit.apply_preview(snap)
        self.assertEqual((exit_code, receipt["code"], receipt["replaced"]), (1, "POLICY_CHANGED", False))

        # 3. Parent directory replaced (rename + recreate) after preview -> FILE_CHANGED.
        snap, _ = config_edit.preview_edit(str(self.root), "sub/app.json", key="/a", value="a2")
        saved_bytes = target.read_bytes()
        moved = self.root / "sub-moved"
        target.parent.rename(moved)
        (self.root / "sub").mkdir()
        (self.root / "sub/app.json").write_bytes(saved_bytes)
        receipt, exit_code = config_edit.apply_preview(snap)
        self.assertEqual((exit_code, receipt["code"], receipt["replaced"]), (1, "FILE_CHANGED", False))

        # 4. Pre-commit target modification right before os.replace -> FILE_CHANGED + temp cleanup.
        target = self.root / "sub/app.json"
        snap, _ = config_edit.preview_edit(str(self.root), "sub/app.json", key="/a", value="a2")
        orig_reverify = config_edit._pre_commit_reverify

        def race_before_commit(s, pfd):
            target.write_text('{\n  "a": "a1",\n  "b": "late-race"\n}\n', encoding="utf-8")
            return orig_reverify(s, pfd)

        with mock.patch.object(config_edit, "_pre_commit_reverify", side_effect=race_before_commit):
            receipt, exit_code = config_edit.apply_preview(snap)
        self.assertEqual((exit_code, receipt["code"], receipt["replaced"]), (1, "FILE_CHANGED", False))
        self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["b"], "late-race")
        self.assertEqual([p.name for p in target.parent.iterdir()], ["app.json"])

        # 5. Two writers sharing stale snapshots: second must re-preview; both edits survive.
        target.write_text('{\n  "a": "a1",\n  "b": "b1"\n}\n', encoding="utf-8")
        snap_a, _ = config_edit.preview_edit(str(self.root), "sub/app.json", key="/a", value="a2")
        snap_b, _ = config_edit.preview_edit(str(self.root), "sub/app.json", key="/b", value="b2")
        rec_a, code_a = config_edit.apply_preview(snap_a)
        self.assertEqual((code_a, rec_a["code"]), (0, "APPLIED"))
        rec_b_stale, code_b_stale = config_edit.apply_preview(snap_b)
        self.assertEqual((code_b_stale, rec_b_stale["code"], rec_b_stale["replaced"]), (1, "FILE_CHANGED", False))
        snap_b2, _ = config_edit.preview_edit(str(self.root), "sub/app.json", key="/b", value="b2")
        rec_b2, code_b2 = config_edit.apply_preview(snap_b2)
        self.assertEqual((code_b2, rec_b2["code"]), (0, "APPLIED"))
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"a": "a2", "b": "b2"})

    def test_final_quarantine_check_refusal_preserves_target(self):
        target = self._setup_basic_json()
        before = target.read_bytes()
        snapshot, _ = config_edit.preview_edit(
            str(self.root), "sub/app.json", key="/a", value="a2",
            trash_root=str(self.outer / "quarantine"),
        )
        reverify = config_edit._pre_commit_reverify

        def fail_boundary_at_commit(plan, parent_fd):
            refusal = config_edit.ConfigEditError("FILE_CHANGED")
            with mock.patch.object(config_edit, "_check_path_boundaries", side_effect=refusal) as check:
                try:
                    reverify(plan, parent_fd)
                finally:
                    check.assert_called_once_with(
                        plan.workspace_root, plan.parts, plan.trash_root, error_code="FILE_CHANGED",
                    )

        with mock.patch.object(config_edit, "_pre_commit_reverify", side_effect=fail_boundary_at_commit):
            result, code = config_edit.apply_preview(snapshot)
        self.assertEqual((code, result["code"], result["replaced"]), (1, "FILE_CHANGED", False))
        self.assertEqual(target.read_bytes(), before)
        self.assertEqual(list(target.parent.iterdir()), [target])

    def test_lock_timeout_and_serialization(self):
        import fcntl

        self._setup_basic_json()
        snap, _ = config_edit.preview_edit(str(self.root), "sub/app.json", key="/a", value="a2")
        lock_path = self.root / config_edit.POLICY_DIR / config_edit.LOCK_FILE
        lock_path.touch()
        fd = os.open(lock_path, os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            receipt, code = config_edit.apply_preview(snap, lock_timeout=0.08)
            self.assertEqual((code, receipt["code"], receipt["replaced"]), (1, "LOCK_TIMEOUT", False))

            # Release lock shortly from a background thread so bounded wait succeeds.
            def release_soon():
                time.sleep(0.08)
                fcntl.flock(fd, fcntl.LOCK_UN)

            t = threading.Thread(target=release_soon)
            t.start()
            try:
                receipt2, code2 = config_edit.apply_preview(snap, lock_timeout=2.0)
            finally:
                t.join(timeout=2.0)
            self.assertEqual((code2, receipt2["code"], receipt2["replaced"]), (0, "APPLIED", True))
        finally:
            os.close(fd)

    def test_real_replace_under_restrictive_umask_preserves_metadata_and_fault_injections(self):
        target = self._setup_basic_json(mode=0o640)
        orig_stat = target.stat()
        old_umask = os.umask(0o077)
        try:
            snap, _ = config_edit.preview_edit(str(self.root), "sub/app.json", key="/a", value="updated")
            receipt, exit_code = config_edit.apply_preview(snap)
        finally:
            os.umask(old_umask)
        self.assertEqual(exit_code, 0)
        self.assertEqual(receipt["status"], "applied")
        self.assertEqual(receipt["code"], "APPLIED")
        self.assertTrue(receipt["replaced"])
        self.assertTrue(receipt["durability_confirmed"])
        new_stat = target.stat()
        self.assertEqual(new_stat.st_uid, orig_stat.st_uid)
        self.assertEqual(new_stat.st_gid, orig_stat.st_gid)
        self.assertEqual(stat.S_IMODE(new_stat.st_mode), 0o640)
        self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["a"], "updated")

        # Fault 1: fchmod failure leaves target untouched and removes temp file.
        before = target.read_bytes()
        snap1, _ = config_edit.preview_edit(str(self.root), "sub/app.json", key="/a", value="never")
        with mock.patch.object(config_edit.os, "fchmod", side_effect=OSError("synthetic fchmod")):
            r1, c1 = config_edit.apply_preview(snap1)
        self.assertEqual((c1, r1["code"], r1["replaced"], r1["durability_confirmed"]),
                         (1, "METADATA_RESTORE_FAILED", False, False))
        self.assertEqual(target.read_bytes(), before)
        self.assertEqual([p.name for p in target.parent.iterdir()], ["app.json"])

        # Fault 2: temp file fsync failure leaves target untouched and removes temp file.
        snap2, _ = config_edit.preview_edit(str(self.root), "sub/app.json", key="/a", value="never2")
        real_fsync = os.fsync
        fsync_state = {"calls": 0}

        def fail_first_fsync(fd):
            fsync_state["calls"] += 1
            if fsync_state["calls"] == 1:
                raise OSError("synthetic tmp fsync")
            return real_fsync(fd)

        with mock.patch.object(config_edit.os, "fsync", side_effect=fail_first_fsync):
            r2, c2 = config_edit.apply_preview(snap2)
        self.assertEqual((c2, r2["code"], r2["replaced"], r2["durability_confirmed"]),
                         (1, "IO_ERROR", False, False))
        self.assertEqual(target.read_bytes(), before)
        self.assertEqual([p.name for p in target.parent.iterdir()], ["app.json"])

        # Fault 3: parent dir fsync failure -> replaced=True, durability_confirmed=False, warning, exit 1.
        snap3, _ = config_edit.preview_edit(str(self.root), "sub/app.json", key="/a", value="after-replace")
        dir_fsync_state = {"calls": 0}

        def fail_second_fsync(fd):
            dir_fsync_state["calls"] += 1
            if dir_fsync_state["calls"] == 2:
                raise OSError("synthetic dir fsync")
            return real_fsync(fd)

        with mock.patch.object(config_edit.os, "fsync", side_effect=fail_second_fsync):
            r3, c3 = config_edit.apply_preview(snap3)
        self.assertEqual(c3, 1)
        self.assertEqual(r3["status"], "applied_with_warning")
        self.assertEqual(r3["code"], "DIR_FSYNC_UNCONFIRMED")
        self.assertEqual(r3["warning"], "DIR_FSYNC_UNCONFIRMED")
        self.assertTrue(r3["replaced"])
        self.assertFalse(r3["durability_confirmed"])
        self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["a"], "after-replace")
        last_audit = audit.tail(str(self.root / ".agent-trash/audit.jsonl"), 1)[0]
        self.assertEqual(last_audit["warning"], "DIR_FSYNC_UNCONFIRMED")
        self.assertTrue(last_audit["replaced"])
        self.assertFalse(last_audit["durability_confirmed"])

        # Fault 4a: audit write failure AFTER successful replace -> applied_with_warning + exit 1 + primary code preserved.
        blocker = self.outer / "not-a-dir"
        blocker.write_text("file-blocker", encoding="utf-8")
        snap4, _ = config_edit.preview_edit(
            str(self.root), "sub/app.json", key="/b", value="b-updated", trash_root=str(blocker),
        )
        r4, c4 = config_edit.apply_preview(snap4)
        self.assertEqual(c4, 1)
        self.assertEqual(r4["status"], "applied_with_warning")
        self.assertEqual(r4["code"], "APPLIED")
        self.assertEqual(r4["audit_error"], "AUDIT_WRITE_FAILED")
        self.assertTrue(r4["replaced"])
        self.assertTrue(r4["durability_confirmed"])
        self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["b"], "b-updated")

        # Fault 4b: audit write failure on pre-replace failure -> primary code & replaced=False preserved.
        snap5, _ = config_edit.preview_edit(
            str(self.root), "sub/app.json", key="/b", value="b3", trash_root=str(blocker),
        )
        target.write_text('{\n  "a": "after-replace",\n  "b": "changed-externally"\n}\n', encoding="utf-8")
        r5, c5 = config_edit.apply_preview(snap5)
        self.assertEqual(c5, 1)
        self.assertEqual(r5["status"], "error")
        self.assertEqual(r5["code"], "FILE_CHANGED")
        self.assertEqual(r5["audit_error"], "AUDIT_WRITE_FAILED")
        self.assertFalse(r5["replaced"])
        self.assertFalse(r5["durability_confirmed"])


if __name__ == "__main__":
    unittest.main()
