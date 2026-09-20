"""exfil-guard policy: facts -> decision (decide_spans)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import policy, redaction
from core.policy import (
    CODE_ALLOW_PATH_IN_WORKSPACE, CODE_ALLOW_SECRET_PLACEHOLDER,
    CODE_ASK_PATH_EMISSION, CODE_ASK_SECRET_EMISSION,
    CODE_BLOCK_OUTPUT_UNSCANNABLE, CODE_BLOCK_PATH_EMISSION,
    CODE_BLOCK_SECRET_EMISSION, CODE_SANITIZE_PATH_REWRITE,
    CODE_SANITIZE_SECRET_REDACT, DECISION_ALLOW, DECISION_ASK, DECISION_BLOCK,
    DECISION_SANITIZE, MODE_NORMAL, MODE_RESTRICTED, PolicyContext,
    decide_spans, worst,
)
from core.redaction import (
    CONFIDENCE_CONTEXTUAL, CONFIDENCE_DETERMINISTIC, FAMILY_PATH,
    FAMILY_SECRET, RULE_GENERIC_ABSOLUTE_PATH, RULE_HOST_ABSOLUTE_PATH,
    RULE_OPENAI_KEY, RULE_SYSTEM_PATH, RULE_WORKSPACE_PATH, SpanSpec,
    get_channel,
)


def span(rule_id=RULE_OPENAI_KEY, family=FAMILY_SECRET,
         confidence=CONFIDENCE_DETERMINISTIC, channel="file-write", **kw):
    spec = SpanSpec(raw="REDACTED-FIXTURE", start=10, end=29, rule_id=rule_id,
                    family=family, confidence=confidence, context="prose",
                    channel=channel)
    for key, value in kw.items():
        setattr(spec, key, value)
    return spec


def decide(spec, channel="file-write", mode=MODE_NORMAL):
    ch = get_channel(channel)
    ctx = PolicyContext(workspace="/ws", trash_root="/ws/.agent-trash",
                        base_dir="/ws", mode=mode)
    return decide_spans([spec], ctx=ctx, channel=ch)[0]


class SecretDecisions(unittest.TestCase):
    def test_deterministic_secret_sanitizes_on_rewritable_channel(self):
        for channel in ("file-write", "llm-request", "forge-comment",
                        "issue-body", "pr-description"):
            with self.subTest(channel=channel):
                verdict = decide(span(channel=channel), channel)
                self.assertEqual(verdict.decision, DECISION_SANITIZE)
                self.assertEqual(verdict.code, CODE_SANITIZE_SECRET_REDACT)

    def test_blocked_channel_blocks_on_persistent_history(self):
        for channel in ("git-commit-message", "git-push-payload"):
            with self.subTest(channel=channel):
                verdict = decide(span(channel=channel), channel)
                self.assertEqual(verdict.decision, DECISION_BLOCK)
                self.assertEqual(verdict.code, CODE_BLOCK_SECRET_EMISSION)

    def test_unrewritable_unpersistent_channel_asks(self):
        verdict = decide(span(channel="shell-stdout"), "shell-stdout")
        self.assertEqual(verdict.decision, DECISION_ASK)
        self.assertEqual(verdict.code, CODE_ASK_SECRET_EMISSION)

    def test_contextual_secret_asks_without_context_gate(self):
        spec = span(confidence=CONFIDENCE_CONTEXTUAL)
        self.assertEqual(decide(spec).decision, DECISION_ASK)

    def test_contextual_secret_sanitizes_with_context_gate(self):
        spec = span(confidence=CONFIDENCE_CONTEXTUAL, context_gate=True)
        self.assertEqual(decide(spec).decision, DECISION_SANITIZE)

    def test_placeholder_allows(self):
        spec = span(placeholder=True)
        verdict = decide(spec)
        self.assertEqual(verdict.decision, DECISION_ALLOW)
        self.assertEqual(verdict.code, CODE_ALLOW_SECRET_PLACEHOLDER)

    def test_secret_source_dump_blocks_everywhere(self):
        for channel in ("file-write", "shell-stdout", "llm-request"):
            with self.subTest(channel=channel):
                spec = span(channel=channel, source_dump=True)
                verdict = decide(spec, channel)
                self.assertEqual(verdict.decision, DECISION_BLOCK)
                self.assertEqual(verdict.code,
                                 policy.CODE_BLOCK_SECRET_SOURCE_DUMP)


class PathDecisions(unittest.TestCase):
    def test_workspace_relative_path_allows(self):
        spec = span(rule_id=RULE_WORKSPACE_PATH, family=FAMILY_PATH,
                    confidence=CONFIDENCE_CONTEXTUAL)
        verdict = decide(spec)
        self.assertEqual(verdict.decision, DECISION_ALLOW)
        self.assertEqual(verdict.code, CODE_ALLOW_PATH_IN_WORKSPACE)

    def test_workspace_root_sanitizes(self):
        spec = span(rule_id=RULE_WORKSPACE_PATH, family=FAMILY_PATH,
                    confidence=CONFIDENCE_CONTEXTUAL, at_workspace_root=True)
        verdict = decide(spec)
        self.assertEqual(verdict.decision, DECISION_SANITIZE)

    def test_system_path_allows(self):
        spec = span(rule_id=RULE_SYSTEM_PATH, family=FAMILY_PATH,
                    confidence=CONFIDENCE_DETERMINISTIC)
        self.assertEqual(decide(spec).decision, DECISION_ALLOW)

    def test_host_absolute_path_sanitizes(self):
        spec = span(rule_id=RULE_HOST_ABSOLUTE_PATH, family=FAMILY_PATH,
                    confidence=CONFIDENCE_DETERMINISTIC)
        verdict = decide(spec)
        self.assertEqual(verdict.decision, DECISION_SANITIZE)
        self.assertEqual(verdict.code, CODE_SANITIZE_PATH_REWRITE)

    def test_generic_path_asks_normally_sanitizes_in_restricted(self):
        spec = span(rule_id=RULE_GENERIC_ABSOLUTE_PATH, family=FAMILY_PATH,
                    confidence=CONFIDENCE_CONTEXTUAL)
        self.assertEqual(decide(spec).decision, DECISION_ASK)
        self.assertEqual(decide(spec, mode=MODE_RESTRICTED).decision,
                         DECISION_SANITIZE)

    def test_path_on_blocked_channel_blocks(self):
        spec = span(rule_id=RULE_HOST_ABSOLUTE_PATH, family=FAMILY_PATH,
                    confidence=CONFIDENCE_DETERMINISTIC,
                    channel="git-commit-message")
        verdict = decide(spec, "git-commit-message")
        self.assertEqual(verdict.decision, DECISION_BLOCK)
        self.assertEqual(verdict.code, CODE_BLOCK_PATH_EMISSION)

    def test_path_on_stdout_asks(self):
        spec = span(rule_id=RULE_HOST_ABSOLUTE_PATH, family=FAMILY_PATH,
                    confidence=CONFIDENCE_DETERMINISTIC,
                    channel="shell-stdout")
        verdict = decide(spec, "shell-stdout")
        self.assertEqual(verdict.decision, DECISION_ASK)
        self.assertEqual(verdict.code, CODE_ASK_PATH_EMISSION)


class UnknownChannel(unittest.TestCase):
    def test_no_channel_at_all_fails_closed(self):
        spec = span(channel="")
        verdict = decide_spans([spec], channel=None)[0]
        self.assertEqual(verdict.decision, DECISION_BLOCK)
        self.assertEqual(verdict.code, CODE_BLOCK_OUTPUT_UNSCANNABLE)

    def test_unrecognised_channel_name_fails_closed(self):
        spec = span(channel="telepathy")
        verdict = decide_spans([spec], channel=None)[0]
        self.assertEqual(verdict.decision, DECISION_BLOCK)
        self.assertEqual(verdict.code, CODE_BLOCK_OUTPUT_UNSCANNABLE)

    def test_span_channel_name_is_resolved_when_valid(self):
        # check_span.py sets the channel on the span itself; a *valid* name
        # must resolve rather than degrade to the fail-closed branch.
        self.assertEqual(decide_spans([span(channel="file-write")])[0].decision,
                         DECISION_SANITIZE)

    def test_all_channels_are_classified(self):
        for name in redaction.CHANNELS:
            with self.subTest(channel=name):
                verdict = decide(span(channel=name), name)
                self.assertIn(verdict.decision,
                              (DECISION_ALLOW, DECISION_SANITIZE,
                               DECISION_ASK, DECISION_BLOCK))


class Aggregation(unittest.TestCase):
    def test_sanitize_ranks_below_ask(self):
        sanitize = policy.Verdict(DECISION_SANITIZE,
                                  CODE_SANITIZE_SECRET_REDACT)
        allow = policy.Verdict(DECISION_ALLOW, CODE_ALLOW_SECRET_PLACEHOLDER)
        ask = policy.Verdict(DECISION_ASK, CODE_ASK_SECRET_EMISSION)
        block = policy.Verdict(DECISION_BLOCK, CODE_BLOCK_SECRET_EMISSION)
        self.assertEqual(worst([allow, sanitize]).decision, DECISION_SANITIZE)
        self.assertEqual(worst([sanitize, ask]).decision, DECISION_ASK)
        self.assertEqual(worst([ask, block]).decision, DECISION_BLOCK)
        self.assertEqual(worst([allow]).decision, DECISION_ALLOW)

    def test_mixed_payload_asks_when_part_is_uninspectable(self):
        # A sanitizable secret plus an un-rewritable askable shape must ASK:
        # you cannot silently proceed when part of the emission is opaque.
        secret = span(confidence=CONFIDENCE_DETERMINISTIC, channel="file-write")
        path = span(rule_id=RULE_HOST_ABSOLUTE_PATH, family=FAMILY_PATH,
                    confidence=CONFIDENCE_CONTEXTUAL, channel="file-write")
        ctx = PolicyContext(workspace="/ws", trash_root="",
                            base_dir="/ws", mode=MODE_NORMAL)
        verdicts = decide_spans([secret, path], ctx=ctx,
                               channel=get_channel("file-write"))
        self.assertEqual([v.decision for v in verdicts],
                         [DECISION_SANITIZE, DECISION_ASK])
        self.assertEqual(worst(verdicts).decision, DECISION_ASK)

    def test_empty_span_list_is_not_a_verdict(self):
        self.assertEqual(decide_spans([], channel=get_channel("file-write")), [])


class VerdictPayloadHasNoBytes(unittest.TestCase):
    """The payload is what gets serialised; it must never carry the match."""

    SECRET = "sk-" + "AbCdEf0123456789AbCdEf0123456789"

    def test_payload_carries_offsets_only(self):
        spec = span()
        spec.raw = self.SECRET
        verdict = decide(spec)
        blob = repr(verdict.payload) + repr(verdict.reasons) + \
            verdict.explanation
        self.assertNotIn(self.SECRET, blob)
        self.assertEqual(verdict.payload["span"], [10, 29])
        self.assertEqual(verdict.payload["rule_id"], RULE_OPENAI_KEY)
        self.assertNotIn("raw", verdict.payload)


if __name__ == "__main__":
    unittest.main()
