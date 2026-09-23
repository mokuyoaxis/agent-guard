"""exfil-guard redaction: format preservation and LEAK FREEDOM.

The leak-freedom test is the one that keeps the guard from becoming the
leak: no guard output - stdout, --json, the redaction plan, an explanation,
an audit line, or a traceback - may contain the matched bytes.
"""
import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import policy, redaction
from core.redaction import get_channel, scan_text

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "skills", "exfil-guard", "scripts")
CHECK_SPAN = os.path.join(SCRIPTS, "check_span.py")
SANITIZE = os.path.join(SCRIPTS, "sanitize.py")

sys.path.insert(0, SCRIPTS)
import sanitize as sanitize_mod  # noqa: E402

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

ALL_SECRETS = [OPENAI, GITHUB, SLACK, AWS, STRIPE, PRIVATE_KEY]


def run(script, channel, text, extra=()):
    proc = subprocess.run(
        [sys.executable, script, "--channel", channel, *extra],
        input=text, capture_output=True, text=True, timeout=60,
        env={**os.environ, "AGENT_GUARD_WORKSPACE": ROOT},
    )
    return proc


class FormatPreservation(unittest.TestCase):
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
