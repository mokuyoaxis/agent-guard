"""exfil-guard redaction: format preservation and LEAK FREEDOM.

The leak-freedom test is the one that keeps the guard from becoming the
leak: no guard output - stdout, --json, the redaction plan, an explanation,
an audit line, or a traceback - may contain the matched bytes.
"""
import base64
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import policy, redaction
from core.redaction import get_channel, scan_text

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "skills", "exfil-guard", "scripts")
CHECK_SPAN = os.path.join(SCRIPTS, "check_span.py")
SANITIZE = os.path.join(SCRIPTS, "sanitize.py")

sys.path.insert(0, SCRIPTS)
import sanitize as sanitize_mod  # noqa: E402
from _payload import PayloadTooLarge, read_bounded_text  # noqa: E402

# Built by concatenation: this file must not itself contain a literal that
# the guard would flag (it is part of the repo's own corpus).
OPENAI = "sk-" + "AbCdEf0123456789AbCdEf0123456789"
GITHUB = "ghp_" + "012345678901234567890123456789012345"
SLACK = "xoxb-" + "1234567890-abcdefghijkl"
AWS = "AKIA" + "J7XQZ2M4PLRN8TWV"
STRIPE = "sk_live_" + "51H8xYzAbCdEfGhIjKlMnOpQr"
_ARMOR = "-----" + "BEGIN RSA PRIVATE KEY" + "-----"
_FOOTER = "-----" + "END RSA PRIVATE KEY" + "-----"
PRIVATE_KEY = _ARMOR + "\nMIIEowIBAAKCAQEA\n" + _FOOTER
INSTALLATION_TOKEN = ("ghs_" + "123456_" + "eyJhbGciOiJSUzI1NiJ9" + "."
                      + "eyJzdWIiOiJzeW50aGV0aWMifQ" + "."
                      + "Q7mN4pR2tVH8kL3bD6sF9wZ5aC0uE1xYz" * 18)
PYPI_TOKEN = "pypi-" + "Q7mN4pR2tV" + "xxx" + "H8kL3bD6sF9wZ5aC0uE1xYz_-" * 30

ALL_SECRETS = [OPENAI, GITHUB, SLACK, AWS, STRIPE, PRIVATE_KEY]


def run(script, channel, text, extra=(), env=None):
    child_env = {**os.environ, "AGENT_GUARD_WORKSPACE": ROOT}
    if env:
        child_env.update(env)
    proc = subprocess.run(
        [sys.executable, script, "--channel", channel, *extra],
        input=text, capture_output=True, text=True, timeout=60,
        env=child_env,
    )
    return proc


class FormatPreservation(unittest.TestCase):
    def test_native_windows_user_path_uses_exact_bytes_and_sanitizes(self):
        # Build the fixture in Python: Git Bash printf rewrites backslashes
        # and caused the withdrawn third finding in Issue #7.
        profile = "C:" + "\\Users\\sample-user"
        native_path = profile + "\\secret.txt"
        proc = run(
            CHECK_SPAN, "file-write", "home=" + native_path + "\n",
            ("--json",),
            {"USERPROFILE": profile, "USERNAME": "sample-user"},
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertEqual((out["decision"], out["code"]),
                         ("SANITIZE", "SANITIZE_PATH_REWRITE"))
        self.assertEqual(out["spans"][0]["end"] -
                         out["spans"][0]["start"], len(native_path))
        self.assertNotIn(native_path, proc.stdout + proc.stderr)

    def test_vendor_prefix_is_kept(self):
        spans = scan_text("k " + OPENAI, "file-write", ROOT).spans
        self.assertEqual(sanitize_mod.placeholder_for(spans[0]),
                         "sk-<REDACTED>")

    def test_aws_prefix_is_kept_too(self):
        # `AKIA` is the reserved AWS prefix: public, and it tells a reviewer
        # *which* credential kind leaked.
        spans = scan_text("k " + AWS, "file-write", ROOT).spans
        self.assertEqual(sanitize_mod.placeholder_for(spans[0]),
                         "AKIA<REDACTED>")

    def test_private_key_block_is_wholly_replaced(self):
        spans = scan_text(PRIVATE_KEY, "file-write", ROOT).spans
        self.assertEqual(len(spans), 1)
        self.assertEqual(sanitize_mod.placeholder_for(spans[0]), "<REDACTED>")

    def test_path_becomes_path_placeholder(self):
        spans = scan_text("see /home/bob/a/b", "file-write", ROOT).spans
        self.assertTrue(spans)
        self.assertEqual(sanitize_mod.placeholder_for(spans[0]), "<PATH>")

    def test_plan_carries_offsets_and_placeholder_only(self):
        span = scan_text("k " + OPENAI, "file-write", ROOT).spans[0]
        entry = sanitize_mod.plan_for_span(span)
        self.assertEqual(entry["placeholder"], "sk-<REDACTED>")
        self.assertEqual(entry["end"] - entry["start"], len(OPENAI))
        self.assertNotIn(OPENAI, json.dumps(entry))

    def test_apply_plan_rewrites_in_place(self):
        text = "before " + OPENAI + " after"
        plan = [{"start": 7, "end": 7 + len(OPENAI),
                 "placeholder": "sk-<REDACTED>"}]
        self.assertEqual(sanitize_mod.apply_plan(text, plan),
                         "before sk-<REDACTED> after")

    def test_apply_plan_is_order_independent(self):
        text = OPENAI + " mid " + GITHUB
        spans = scan_text(text, "file-write", ROOT).spans
        plan = [sanitize_mod.plan_for_span(s) for s in spans]
        result = sanitize_mod.apply_plan(text, list(reversed(plan)))
        self.assertNotIn(OPENAI, result)
        self.assertNotIn(GITHUB, result)
        self.assertIn(" mid ", result)

    def test_out_of_range_plan_is_refused(self):
        with self.assertRaises(ValueError):
            sanitize_mod.apply_plan("abc", [{"start": 0, "end": 99,
                                             "placeholder": "x"}])

    def test_sanitize_text_round_trip(self):
        result = sanitize_mod.sanitize_text(
            "config " + OPENAI, "file-write", ROOT)
        self.assertEqual(result["decision"], "SANITIZE")
        self.assertNotIn(OPENAI, result["text"])


class LeakFreedom(unittest.TestCase):
    """NON-NEGOTIABLE: guard output must never contain the matched bytes.

    Byte-level assertion against every output surface the guard has. A
    failure here means the guard recreated the leak it exists to prevent.
    """

    def assert_no_leak(self, blob, secret, label):
        self.assertNotIn(secret, blob, f"guard leaked {label}")
        # Also check the most dangerous fragments: a prefix that is enough
        # to pivot on should not survive either.
        if len(secret) > 24:
            self.assertNotIn(secret[:24], blob, f"guard leaked {label} prefix")

    def test_no_secret_in_stdout_or_json_for_every_channel(self):
        for secret in ALL_SECRETS:
            for channel in redaction.CHANNELS:
                for flags in ((), ("--json",)):
                    with self.subTest(secret=secret[:14], channel=channel,
                                      json=bool(flags)):
                        proc = run(CHECK_SPAN, channel,
                                   f"payload {secret} end\n", flags)
                        blob = proc.stdout + proc.stderr
                        self.assert_no_leak(
                            blob, secret, f"check_span {channel} {flags}")

    def test_no_secret_in_sanitize_output_or_plan(self):
        for secret in ALL_SECRETS:
            for flags in ((), ("--dry-run",)):
                with self.subTest(secret=secret[:14], dry=bool(flags)):
                    proc = run(SANITIZE, "file-write",
                               f"payload {secret} end\n", flags)
                    self.assert_no_leak(proc.stdout + proc.stderr, secret,
                                        f"sanitize {flags}")

    def test_no_secret_in_explanations_or_reasons(self):
        for secret in ALL_SECRETS:
            for channel in redaction.CHANNELS:
                proc = run(CHECK_SPAN, channel, secret + "\n", ("--json",))
                data = json.loads(proc.stdout)
                blob = json.dumps({"e": data["explanation"],
                                   "r": data["reasons"],
                                   "p": data["redaction_plan"]},
                                  ensure_ascii=False)
                self.assert_no_leak(blob, secret,
                                    f"explanation/reasons {channel}")

    def test_no_secret_in_audit_records(self):
        """Design 5.5: the audit record stores rule id + offsets + length."""
        from core import audit
        import tempfile
        for secret in ALL_SECRETS:
            with tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, "audit.jsonl")
                spans = scan_text(f"p {secret} q", "file-write", ROOT).spans
                verdicts = policy.decide_spans(
                    spans, channel=get_channel("file-write"))
                for span, verdict in zip(spans, verdicts):
                    record = {"event": "exfil-" + verdict.decision.lower()}
                    record.update(span.safe())
                    audit.append(record, path)
                with open(path, encoding="utf-8") as fh:
                    written = fh.read()
                self.assert_no_leak(written, secret, "audit record")

    def test_no_secret_in_bypassing_errors(self):
        """A wrong channel name must not echo the payload back."""
        for secret in ALL_SECRETS:
            with self.subTest(secret=secret[:14]):
                proc = run(CHECK_SPAN, "telepathy", secret + "\n")
                self.assert_no_leak(proc.stdout + proc.stderr, secret,
                                    "unknown-channel error")

    def test_no_secret_in_huge_payload_rejection(self):
        for secret in ALL_SECRETS:
            with self.subTest(secret=secret[:14]):
                proc = subprocess.run(
                    [sys.executable, CHECK_SPAN, "--channel", "file-write",
                     "--json", "--max-bytes", "4"],
                    input=secret + "\n", capture_output=True, text=True,
                    timeout=60,
                    env={**os.environ, "AGENT_GUARD_WORKSPACE": ROOT})
                self.assertEqual(proc.returncode, 2)
                self.assert_no_leak(proc.stdout + proc.stderr, secret,
                                    "over-cap rejection")

    def test_no_secret_in_success_metrics(self):
        """Latency/size metadata is still output: check it too."""
        for secret in ALL_SECRETS:
            proc = run(CHECK_SPAN, "file-write", secret + "\n", ("--json",))
            data = json.loads(proc.stdout)
            blob = json.dumps({k: v for k, v in data.items()
                               if k not in ("spans", "redaction_plan")})
            self.assert_no_leak(blob, secret, "metadata")


class Idempotence(unittest.TestCase):
    """Design 2.5: scanning already-sanitized text yields zero spans."""

    def test_sanitized_text_scans_clean(self):
        for secret in ALL_SECRETS:
            with self.subTest(secret=secret[:14]):
                result = sanitize_mod.sanitize_text(
                    "payload " + secret + " end", "file-write", ROOT)
                rescan = scan_text(result["text"], "file-write", ROOT)
                self.assertEqual([s.rule_id for s in rescan.spans], [],
                                 result["text"])

    def test_marker_itself_is_not_a_match(self):
        self.assertEqual(
            scan_text("nothing <REDACTED> here", "file-write", ROOT).spans, [])


class CLIExitCodes(unittest.TestCase):
    def test_exit_codes(self):
        cases = [("file-write", "SANITIZE", 0), ("llm-request", "SANITIZE", 0),
                 ("git-push-payload", "BLOCK", 2),
                 ("git-commit-message", "BLOCK", 2),
                 ("shell-stdout", "ASK", 3)]
        for channel, decision, code in cases:
            with self.subTest(channel=channel):
                proc = run(CHECK_SPAN, channel, OPENAI + "\n")
                self.assertEqual(proc.returncode, code, proc.stderr)
                self.assertIn(decision, proc.stdout)

    def test_clean_payload_allows_and_exits_zero(self):
        proc = run(CHECK_SPAN, "file-write", "just some prose\n")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("ALLOW", proc.stdout)

    def test_json_schema_has_no_raw_field(self):
        proc = run(CHECK_SPAN, "file-write", OPENAI + "\n", ("--json",))
        data = json.loads(proc.stdout)
        for span in data["spans"]:
            self.assertNotIn("raw", span)
            self.assertIn("start", span)
        for entry in data["redaction_plan"]:
            self.assertNotIn("raw", entry)

    def test_sanitize_cli_applies_the_plan(self):
        proc = run(SANITIZE, "file-write", "k " + OPENAI + " z\n")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn(OPENAI, proc.stdout)
        self.assertIn("sk-<REDACTED>", proc.stdout)

    def test_unknown_channel_exits_two(self):
        proc = run(CHECK_SPAN, "nope", OPENAI + "\n", ("--json",))
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(json.loads(proc.stdout)["code"],
                         "BLOCK_OUTPUT_UNSCANNABLE")


class SanitizerEnforcement(unittest.TestCase):
    def test_refusal_withholds_body_in_library_and_cli(self):
        cases = [("git-push-payload", OPENAI, "BLOCK", 2),
                 ("git-commit-message", OPENAI, "BLOCK", 2),
                 ("shell-stdout", OPENAI, "ASK", 3),
                 ("file-write", "echo $REVIEW_SECRET_TOKEN", "BLOCK", 2),
                 ("file-write", OPENAI + " /generic-review-root/file.txt", "ASK", 3)]
        for channel, text, decision, code in cases:
            result = sanitize_mod.sanitize_text(text, channel, ROOT)
            self.assertEqual((result["decision"], result["text"]), (decision, ""))
            for flags in ((), ("--dry-run",)):
                proc = run(SANITIZE, channel, text, flags)
                self.assertEqual(proc.returncode, code, proc.stderr)
                self.assertEqual(proc.stdout, "")
                self.assertIn(decision, proc.stderr)
                self.assertNotIn(text, proc.stderr)

    def test_safe_content_and_unicode_are_preserved(self):
        text = "# 中文 😀\r\nPORT=8080\nFEATURE_ENABLED=true"
        result = sanitize_mod.sanitize_text(text, "llm-request", ROOT)
        self.assertEqual(result["text"], text)
        proc = run(SANITIZE, "llm-request", "# 中文 😀\nPORT=8080\n")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "# 中文 😀\nPORT=8080\n")

    def test_restricted_host_mode_matches_checker_default(self):
        text = "see /generic-review-root/file.txt"
        with tempfile.TemporaryDirectory() as workspace:
            trash = os.path.join(workspace, ".agent-trash")
            env = {"AGENT_GUARD_TRASH": trash, "AGENT_GUARD_WORKSPACE": workspace,
                   "AGENT_GUARD_SESSION": "review-session"}
            with patch.dict(os.environ, env):
                policy.force_mode(trash, policy.MODE_RESTRICTED, "test")
                result = sanitize_mod.sanitize_text(text, "file-write")
                self.assertEqual(result["decision"], "SANITIZE")
                proc = run(CHECK_SPAN, "file-write", text, ("--json",), env)
                checked = json.loads(proc.stdout)
                self.assertEqual(checked["mode"], "RESTRICTED")
                self.assertEqual(checked["redaction_plan"], result["plan"])
                proc = run(SANITIZE, "file-write", text, env=env)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(proc.stdout, result["text"])

    def test_connection_password_roundtrip_and_channel_policy(self):
        uri = "postgresql" + "://" + "user:p%40ss%2Fword@db.example.invalid:5432/app"
        result = sanitize_mod.sanitize_text(uri, "llm-request", ROOT)
        expected = "postgresql" + "://" + "user:<REDACTED>@db.example.invalid:5432/app"
        self.assertEqual(result["text"], expected)
        self.assertEqual(sanitize_mod.sanitize_text(expected, "llm-request", ROOT)["decision"], "ALLOW")
        for channel, code in (("file-write", 0), ("shell-stdout", 3),
                              ("git-push-payload", 2)):
            proc = run(SANITIZE, channel, uri)
            self.assertEqual(proc.returncode, code, proc.stderr)
            self.assertNotIn("p%40ss%2Fword", proc.stdout + proc.stderr)

    def test_connection_literal_cannot_hide_a_secret_source_reference(self):
        text = "mysql" + "://" + "user:prefix${REVIEW_SECRET_TOKEN}@host/db"
        scanned = scan_text(text, "file-write", ROOT)
        self.assertTrue(any(span.source_dump for span in scanned.spans))
        proc = run(SANITIZE, "file-write", text)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertEqual(proc.stdout, "")

    def test_rescan_refuses_failed_or_incomplete_rewrite(self):
        with patch.object(sanitize_mod, "apply_plan", return_value=OPENAI):
            with self.assertRaises(RuntimeError):
                sanitize_mod.sanitize_text(OPENAI, "file-write", ROOT)
        first = scan_text(OPENAI, "file-write", ROOT)
        with patch.object(sanitize_mod, "scan_text", side_effect=[
                first, redaction.ScanResult(scanned=False, error="PRIVATE_DIAGNOSTIC")]):
            with self.assertRaises(RuntimeError) as caught:
                sanitize_mod.sanitize_text(OPENAI, "file-write", ROOT)
            self.assertNotIn("PRIVATE_DIAGNOSTIC", str(caught.exception))


class ExternalPlanValidation(unittest.TestCase):
    def setUp(self):
        self.text = "before " + OPENAI + " mid " + GITHUB + " after"
        self.plan = sanitize_mod.sanitize_text(self.text, "file-write", ROOT)["plan"]

    def test_checker_plan_can_be_replayed_out_of_order(self):
        proc = run(CHECK_SPAN, "file-write", self.text, ("--json",))
        plan = json.loads(proc.stdout)["redaction_plan"]
        result = sanitize_mod.sanitize_text(self.text, "file-write", ROOT,
                                           list(reversed(plan)))
        self.assertNotIn(OPENAI, result["text"])
        self.assertNotIn(GITHUB, result["text"])
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "plan.json")
            with open(path, "w", encoding="utf-8") as out:
                out.write(proc.stdout)
            applied = run(SANITIZE, "file-write", self.text, ("--plan", path))
            self.assertEqual(applied.returncode, 0, applied.stderr)
            self.assertEqual(applied.stdout, result["text"])

    def test_missing_extra_stale_or_echoing_plan_is_refused(self):
        bad = [[], self.plan[:1], self.plan + [dict(start=0, end=1, placeholder="x")],
               [dict(self.plan[0], start=0), self.plan[1]],
               [dict(self.plan[0], placeholder=OPENAI), self.plan[1]],
               [dict(self.plan[0], rule_id="secret/wrong"), self.plan[1]],
               [dict(self.plan[0], raw=OPENAI), self.plan[1]]]
        for plan in bad:
            with self.assertRaises(ValueError) as caught:
                sanitize_mod.sanitize_text(self.text, "file-write", ROOT, plan)
            self.assertNotIn(OPENAI, str(caught.exception))
        moved = "moved-prefix " + self.text
        with self.assertRaises(ValueError):
            sanitize_mod.sanitize_text(moved, "file-write", ROOT, self.plan)

    def test_structural_validation_cannot_be_disabled(self):
        invalid = ["invalid", [{}], [dict(start=True, end=2, placeholder="x")],
                   [dict(start=0.0, end=2, placeholder="x")],
                   [dict(start=0, end=4, placeholder="x")],
                   [dict(start=0, end=1, placeholder="")],
                   [dict(start=0, end=1, placeholder=1)],
                   [dict(start=0, end=2, placeholder="x"),
                    dict(start=1, end=3, placeholder="y")]]
        for plan in invalid:
            with self.assertRaises(ValueError):
                sanitize_mod.apply_plan("abc", plan, verify=False)

    def test_cli_invalid_plan_never_outputs_a_payload(self):
        documents = ["not-json PRIVATE_PLAN_DIAGNOSTIC", "{}", "[]",
                     '{"redaction_plan":[],"redaction_plan":[]}',
                     json.dumps({"redaction_plan": []}),
                     json.dumps({"redaction_plan": [dict(self.plan[0], placeholder=OPENAI)]})]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "plan.json")
            for document in documents:
                with open(path, "w", encoding="utf-8") as out:
                    out.write(document)
                proc = run(SANITIZE, "file-write", self.text, ("--plan", path))
                self.assertEqual(proc.returncode, 1, proc.stderr)
                self.assertEqual(proc.stdout, "")
                self.assertNotIn(OPENAI, proc.stderr)
                self.assertNotIn("PRIVATE_PLAN_DIAGNOSTIC", proc.stderr)


class BoundedInput(unittest.TestCase):
    def test_short_reads_are_not_mistaken_for_eof(self):
        class ShortReader(io.BytesIO):
            def read(self, size=-1):
                return super().read(min(size, 1))
        self.assertEqual(read_bounded_text(ShortReader("中文".encode()), 6), "中文")
        with self.assertRaises(PayloadTooLarge):
            read_bounded_text(ShortReader(b"x" * 1000), 8)

    def test_reader_stops_at_cap_without_consuming_remainder(self):
        stream = io.BytesIO(b"x" * 1000)
        with self.assertRaises(PayloadTooLarge):
            read_bounded_text(stream, 8)
        self.assertEqual(stream.tell(), 9)
        self.assertEqual(read_bounded_text(io.BytesIO(b"PORT=80"), 7), "PORT=80")
        self.assertEqual(read_bounded_text(io.BytesIO(b""), 0), "")

    def test_utf8_byte_limits_and_invalid_encoding(self):
        for stream in (io.BytesIO("中文".encode()), io.StringIO("中文")):
            self.assertEqual(read_bounded_text(stream, 6), "中文")
        with self.assertRaises(PayloadTooLarge):
            read_bounded_text(io.BytesIO("中文".encode()), 5)
        with self.assertRaises(UnicodeError):
            read_bounded_text(io.BytesIO(b"\xff"), 1)

    def test_both_clis_refuse_oversize_input(self):
        env = {"AGENT_GUARD_EXFIL_MAX_BYTES": "4"}
        for script, flags in ((CHECK_SPAN, ("--json",)), (SANITIZE, ())):
            proc = run(script, "file-write", OPENAI, flags, env)
            self.assertEqual(proc.returncode, 2, proc.stderr)
            self.assertNotIn(OPENAI, proc.stdout + proc.stderr)
            if script == SANITIZE:
                self.assertEqual(proc.stdout, "")
            else:
                self.assertFalse(json.loads(proc.stdout)["scanned"])

    def test_oversize_open_pipe_is_refused_before_eof(self):
        env = {**os.environ, "AGENT_GUARD_EXFIL_MAX_BYTES": "8"}
        for script in (CHECK_SPAN, SANITIZE):
            with subprocess.Popen([sys.executable, script], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  env=env) as proc:
                proc.stdin.write(b"x" * 9)
                proc.stdin.flush()
                try:
                    code = proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                    self.fail("oversize input waited for EOF")
                output = proc.stdout.read()
            self.assertEqual(code, 2)
            if script == SANITIZE:
                self.assertEqual(output, b"")

    def test_external_plan_has_the_same_input_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "plan.json")
            with open(path, "w", encoding="utf-8") as out:
                out.write(json.dumps({"redaction_plan": [], "padding": "x" * 1000}))
            proc = run(SANITIZE, "file-write", "clean", ("--plan", path),
                       {"AGENT_GUARD_EXFIL_MAX_BYTES": "8"})
            self.assertEqual(proc.returncode, 2, proc.stderr)
            self.assertEqual(proc.stdout, "")

    def test_invalid_utf8_diagnostics_do_not_echo_input(self):
        for script in (CHECK_SPAN, SANITIZE):
            proc = subprocess.run([sys.executable, script],
                                  input=b"PRIVATE_INPUT_DIAGNOSTIC\xff",
                                  capture_output=True, timeout=30)
            self.assertEqual(proc.returncode, 1)
            self.assertEqual(proc.stdout, b"")
            self.assertNotIn(b"PRIVATE_INPUT_DIAGNOSTIC", proc.stderr)


class StdinContract(unittest.TestCase):
    """A caller that pipes nothing must be refused, not left hanging.

    `sys.stdin.read()` blocks until EOF, so an open-but-unwritten pipe used
    to pin this CLI until the *host's* tool timeout killed the call (300 s
    in the DSH harness) - the guard presented as a hang instead of a
    refusal. The wait for a producer's first byte is now bounded.
    """

    def test_open_unwritten_stdin_fails_fast(self):
        env = {**os.environ, "AGENT_GUARD_WORKSPACE": ROOT,
               "AGENT_GUARD_STDIN_TIMEOUT": "0.5"}
        with subprocess.Popen(
                [sys.executable, CHECK_SPAN, "--json"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, env=env) as proc:
            try:
                # Never write and never close stdin: this is exactly the
                # shape a harness produces when the payload is omitted.
                rc = proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                self.fail("check_span.py hung on an open, unwritten stdin")
            out = proc.stdout.read()
        self.assertEqual(rc, 1, out)
        self.assertIn("no payload arrived on stdin", out)

    def test_sanitizer_open_unwritten_stdin_fails_fast(self):
        env = {**os.environ, "AGENT_GUARD_STDIN_TIMEOUT": "0.2"}
        with subprocess.Popen([sys.executable, SANITIZE], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, env=env) as proc:
            try:
                code = proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                self.fail("sanitize.py hung on an open, unwritten stdin")
            output = proc.stdout.read()
            error = proc.stderr.read()
        self.assertEqual(code, 1)
        self.assertEqual(output, "")
        self.assertIn("no payload arrived on stdin", error)

    def test_empty_stdin_is_an_empty_payload_not_an_error(self):
        """EOF without bytes is a legitimately empty payload: nothing can
        leak, so it scans clean instead of failing."""
        with open(os.devnull) as devnull:
            proc = subprocess.run(
                [sys.executable, CHECK_SPAN, "--json"],
                stdin=devnull, capture_output=True, text=True, timeout=60,
                env={**os.environ, "AGENT_GUARD_WORKSPACE": ROOT})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["decision"], "ALLOW")


class RuleBoundaryIntegration(unittest.TestCase):
    def test_pypi_cli_complete_redaction_and_metadata_only_plan(self):
        text = "令牌" + PYPI_TOKEN + "请保密\nPORT=8080\n"
        checked = run(CHECK_SPAN, "file-write", text, ("--json",))
        self.assertEqual(checked.returncode, 0)
        facts = json.loads(checked.stdout)
        self.assertEqual(facts["decision"], "SANITIZE")
        self.assertEqual(facts["spans"][0]["rule_id"], redaction.RULE_PYPI_TOKEN)
        self.assertEqual(facts["spans"][0]["length"], len(PYPI_TOKEN))
        self.assertNotIn(PYPI_TOKEN, checked.stdout + checked.stderr)
        proc = run(SANITIZE, "file-write", text)
        expected = "令牌pypi-<REDACTED>请保密\nPORT=8080\n"
        self.assertEqual((proc.returncode, proc.stdout), (0, expected))
        again = run(SANITIZE, "file-write", proc.stdout)
        self.assertEqual((again.returncode, again.stdout), (0, expected))

    def test_pypi_documented_placeholders_and_prose_are_unchanged(self):
        text = "pypi-client-library\npypi-<REDACTED>\n" + "pypi-" + "x" * 100 + "\nPORT=8080\n"
        proc = run(SANITIZE, "file-write", text)
        self.assertEqual((proc.returncode, proc.stdout), (0, text))

    def test_pypi_uses_existing_channel_and_source_reference_decisions(self):
        for channel, decision, code in (("file-write", "SANITIZE", 0),
                                        ("llm-request", "SANITIZE", 0),
                                        ("git-push-payload", "BLOCK", 2),
                                        ("shell-stdout", "ASK", 3)):
            checked = run(CHECK_SPAN, channel, PYPI_TOKEN, ("--json",))
            self.assertEqual((checked.returncode, json.loads(checked.stdout)["decision"]), (code, decision))
            self.assertNotIn(PYPI_TOKEN, checked.stdout + checked.stderr)
            proc = run(SANITIZE, channel, PYPI_TOKEN)
            self.assertEqual(proc.returncode, code)
            self.assertEqual(proc.stdout, "" if code else "pypi-<REDACTED>")
        proc = run(SANITIZE, "file-write", "echo ${PYPI_" + "TOKEN}")
        self.assertEqual((proc.returncode, proc.stdout), (2, ""))

    def test_resource_ids_and_invalid_jws_are_preserved_without_value_exemptions(self):
        header = base64.urlsafe_b64encode(json.dumps({"alg": None}).encode()).decode().rstrip("=")
        invalid = header + "." + "eyJzdWIiOiJzeW50aGV0aWMifQ" + "." + "Q7mN4pR2tVH8kL3bD6sF9wZ5aC0uE1xYz"
        text = "\n".join(prefix + "J7XQZ2M4PLRN8TWV" for prefix in ("AGPA", "AIDA", "AROA", "ANPA")) + "\n" + invalid + "\nPORT=8080\n"
        proc = run(SANITIZE, "file-write", text)
        self.assertEqual((proc.returncode, proc.stdout), (0, text))
        checked = run(CHECK_SPAN, "file-write", text, ("--json",))
        self.assertEqual((checked.returncode, json.loads(checked.stdout)["decision"]), (0, "ALLOW"))

    def test_resource_spelling_cannot_exempt_a_uri_credential(self):
        password = "AIDA" + "J7XQZ2M4PLRN8TWV"
        text = "postgresql" + "://account:" + password + "@db.example.invalid/app\n"
        proc = run(SANITIZE, "file-write", text)
        self.assertEqual(proc.returncode, 0)
        self.assertNotIn(password, proc.stdout + proc.stderr)
        self.assertIn("account:<REDACTED>@db.example.invalid/app", proc.stdout)

    def test_cli_whole_long_token_and_marker_body_keep_unicode_and_config(self):
        body = "Q7mN4pR2tV" + "xxx" + ("H8kL3bD6sF9wZ5aC0uE1" * 2)[:23]
        token = "ghp_" + body
        text = "令牌" + token + "请保密\nAPP=" + INSTALLATION_TOKEN + "\nPORT=8080\n"
        checked = run(CHECK_SPAN, "file-write", text, ("--json",))
        self.assertEqual(checked.returncode, 0)
        data = json.loads(checked.stdout)
        self.assertEqual(data["decision"], "SANITIZE")
        proc = run(SANITIZE, "file-write", text)
        self.assertEqual(proc.returncode, 0)
        for target in (token, body, INSTALLATION_TOKEN):
            self.assertNotIn(target, proc.stdout + proc.stderr + checked.stdout + checked.stderr)
        self.assertIn("令牌ghp_<REDACTED>请保密", proc.stdout)
        self.assertIn("APP=ghs_<REDACTED>\nPORT=8080", proc.stdout)
        again = run(SANITIZE, "file-write", proc.stdout)
        self.assertEqual((again.returncode, again.stdout), (0, proc.stdout))

    def test_benign_reference_and_exact_placeholder_are_unchanged(self):
        text = ("echo ${MONKEY} ${API_KEYBOARD} ${TOKEN_COUNT}\n"
                + "throw new TypeError(`${name}: missing argument ${key}`)\n"
                + "API=" + "sk-" + "x" * 40 + "\nPORT=8080\n")
        proc = run(SANITIZE, "file-write", text)
        self.assertEqual((proc.returncode, proc.stdout), (0, text))
        checked = run(CHECK_SPAN, "file-write", text, ("--json",))
        self.assertEqual(json.loads(checked.stdout)["decision"], "ALLOW")

    def test_exact_secret_name_refuses_without_resolving_environment(self):
        text = "echo ${MONKEY} ${" + "TOKEN}"
        runtime_value = "SYNTHETIC_RUNTIME_VALUE_NOT_FOR_OUTPUT"
        for script in (CHECK_SPAN, SANITIZE):
            proc = run(script, "file-write", text, ("--json",) if script == CHECK_SPAN else (),
                       env={"TOKEN": runtime_value})
            self.assertEqual(proc.returncode, 2)
            self.assertNotIn(runtime_value, proc.stdout + proc.stderr)
            if script == SANITIZE:
                self.assertEqual(proc.stdout, "")
            else:
                self.assertEqual(json.loads(proc.stdout)["code"], "BLOCK_SECRET_SOURCE_DUMP")

    def test_new_vendor_span_keeps_existing_channel_decisions(self):
        for channel, decision, code in (("file-write", "SANITIZE", 0),
                                        ("llm-request", "SANITIZE", 0),
                                        ("git-push-payload", "BLOCK", 2),
                                        ("shell-stdout", "ASK", 3)):
            checked = run(CHECK_SPAN, channel, INSTALLATION_TOKEN, ("--json",))
            self.assertEqual(checked.returncode, code)
            self.assertEqual(json.loads(checked.stdout)["decision"], decision)
            self.assertNotIn(INSTALLATION_TOKEN, checked.stdout + checked.stderr)
            proc = run(SANITIZE, channel, INSTALLATION_TOKEN)
            self.assertEqual(proc.returncode, code)
            self.assertNotIn(INSTALLATION_TOKEN, proc.stdout + proc.stderr)
            if code:
                self.assertEqual(proc.stdout, "")

    def test_external_plan_cannot_truncate_new_long_token(self):
        result = sanitize_mod.sanitize_text(INSTALLATION_TOKEN, "file-write", ROOT)
        self.assertEqual(len(result["plan"]), 1)
        entry = result["plan"][0]
        self.assertEqual((entry["start"], entry["end"]), (0, len(INSTALLATION_TOKEN)))
        partial = [dict(entry, end=len(INSTALLATION_TOKEN)-36)]
        with self.assertRaises(ValueError):
            sanitize_mod.sanitize_text(INSTALLATION_TOKEN, "file-write", ROOT, plan=partial)


class SourceDumpDiscipline(unittest.TestCase):
    def test_env_reference_blocks_without_reading_a_value(self):
        # The variable is never resolved: the guard classifies the NAME.
        os.environ.pop("OPENAI_API_KEY", None)
        proc = run(CHECK_SPAN, "file-write", "echo $OPENAI_API_KEY\n",
                   ("--json",))
        data = json.loads(proc.stdout)
        self.assertEqual(data["decision"], "BLOCK")
        self.assertEqual(data["code"], "BLOCK_SECRET_SOURCE_DUMP")

    def test_env_reference_is_detected_even_when_unset(self):
        for text in ("cat .env", "printenv | grep TOKEN", "env | sort"):
            with self.subTest(text=text):
                proc = run(CHECK_SPAN, "file-write", text + "\n", ("--json",))
                self.assertEqual(
                    json.loads(proc.stdout)["code"],
                    "BLOCK_SECRET_SOURCE_DUMP")


class ExemptionFile(unittest.TestCase):
    """Design 5.2: repo-local exemptions, value-exemptions by hash only."""

    def test_rule_and_path_globs(self):
        from core.redaction import Exemption, apply_exemption
        exemption = Exemption(rules=["secret/source-reference"],
                              paths=["docs/*.md", "tests/fixtures/*"])
        spans = scan_text("cat .env", "file-write", ROOT).spans
        self.assertTrue(spans)
        self.assertEqual(apply_exemption(spans, exemption, "docs/a.md", ROOT),
                         [])
        self.assertEqual(apply_exemption(spans, exemption, "other.py", ROOT),
                         [])

    def test_relative_path_matching_is_workspace_scoped(self):
        from core.redaction import Exemption
        exemption = Exemption(paths=["docs/*.md"])
        self.assertTrue(exemption.path_exempt(os.path.join(ROOT, "docs/a.md"),
                                              ROOT))
        self.assertFalse(exemption.path_exempt(
            os.path.join(ROOT, "src/a.md"), ROOT))

    def test_value_exemption_is_by_hash_only(self):
        import hashlib
        from core.redaction import Exemption
        value = "EXEMPT-" + "abcdef0123456789"
        digest = "sha256:" + hashlib.sha256(value.encode()).hexdigest()
        exemption = Exemption(hashes=[digest])
        self.assertTrue(exemption.value_exempt(value))
        self.assertFalse(exemption.value_exempt(value + "x"))

    def test_allowfile_parser_handles_sections_and_lists(self):
        from core.redaction import _parse_allowfile
        parsed = _parse_allowfile("""
[paths]
ignore = docs/*.md, tests/fixtures/*
          more/x.py
[rules]
disable =
[values]
sha256 = sha256:aaaa
""", "x")
        self.assertEqual(parsed.rules, [])
        self.assertEqual(parsed.paths,
                         ["docs/*.md", "tests/fixtures/*", "more/x.py"])
        self.assertEqual(parsed.hashes, ["sha256:aaaa"])

    def test_broken_allowfile_never_disables_the_rules(self):
        from core.redaction import _parse_allowfile
        parsed = _parse_allowfile("this is not = = a config\n\x00", "x")
        self.assertFalse(parsed.active)

    def test_repository_own_corpus_has_zero_blocks(self):
        """Design 5.6: the noise budget, measured rather than asserted."""
        from core.redaction import get_channel
        from core import policy
        blockers = []
        for base, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs
                       if d not in {".git", ".internal", "__pycache__",
                                    "node_modules", ".agent-trash"}]
            for name in files:
                path = os.path.join(base, name)
                try:
                    with open(path, encoding="utf-8") as fh:
                        text = fh.read()
                except (OSError, UnicodeDecodeError):
                    continue
                scan = scan_text(text, "file-write", ROOT, path=path)
                if not scan.scanned:
                    blockers.append((path, "unscannable"))
                    continue
                for verdict in policy.decide_spans(
                        scan.spans, channel=get_channel("file-write")):
                    if verdict.decision == policy.DECISION_BLOCK:
                        blockers.append((os.path.relpath(path, ROOT),
                                         verdict.code))
        self.assertEqual(blockers, [], f"noise budget exceeded: {blockers}")


if __name__ == "__main__":
    unittest.main()
