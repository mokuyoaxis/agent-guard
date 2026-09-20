"""Span classifier for outbound text payloads (exfil-guard).

Mirrors `classifier.py`'s contract - **facts, not decisions**. It turns a
blob of text into `SpanSpec` facts (what matched, where, how confident,
on which channel); `policy.decide_spans()` decides, and `sanitize.py`
applies a redaction plan. This module never imports policy.

Two families, one pipeline:

  secret/*   credentials that must not be emitted (T1 known patterns)
  path/*     host-identifying absolute paths (workspace-relative is ALLOW)

Hard invariant (design 2.4): **the guard reads names and shapes, never
secret values.** Detection classifies a `$VAR`'s *name*, never its content,
and no function here resolves an environment variable. Everything this
module returns that is meant to leave the process (JSON, audit) is built
from `SpanSpec.safe()` - offsets, rule id, length - never `raw`.

`raw` exists only because redaction needs the bytes to replace; it is held
in memory by the caller and never serialised.

Zero third-party dependencies; Python 3.9+ (no `match`, no runtime `X | Y`).
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import os
import platform
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

# ------------------------------------------------------------- span types

FAMILY_SECRET = "secret"
FAMILY_PATH = "path"

CONFIDENCE_DETERMINISTIC = "deterministic"
CONFIDENCE_CONTEXTUAL = "contextual"

# Rule ids are payload, never verdict codes (design 2.2 rule 3).
RULE_OPENAI_KEY = "secret/openai-key"
RULE_GITHUB_TOKEN = "secret/github-token"
RULE_AWS_ACCESS_KEY_ID = "secret/aws-access-key-id"
RULE_GITLAB_TOKEN = "secret/gitlab-token"
RULE_SLACK_TOKEN = "secret/slack-token"
RULE_STRIPE_KEY = "secret/stripe-key"
RULE_JWT = "secret/jwt"
RULE_PRIVATE_KEY_BLOCK = "secret/private-key-block"

RULE_HOST_ABSOLUTE_PATH = "path/host-absolute"
RULE_GENERIC_ABSOLUTE_PATH = "path/generic-absolute"
RULE_SYSTEM_PATH = "path/system"
RULE_DEVICE_PATH = "path/device"

# Everything the placeholder allowlist can observe is ASCII; a payload
# containing any non-ASCII byte is therefore unscannable (fail closed,
# see scan_text).
_ASCII_ONLY = True


@dataclass
class SpanSpec:
    """One matched region of text - the fact layer's unit of exchange."""

    raw: str                    # matched substring, HELD IN MEMORY ONLY
    start: int                  # character offset into the scanned text
    end: int
    rule_id: str                # e.g. "secret/openai-key"
    family: str                 # "secret" | "path"
    confidence: str             # "deterministic" | "contextual"
    context: str                # "prose" | "key=..." | "env-var" | ...
    channel: str                # egress channel carrying the payload
    notes: List[str] = field(default_factory=list)

    @property
    def length(self) -> int:
        return self.end - self.start

    def safe(self) -> Dict[str, Any]:
        """The serialisable view: offsets and rule id, never the bytes.

        This is the single chokepoint that keeps the guard from leaking
        what it protects - `--json` output, audit records and explanations
        are all built from this. There is deliberately no field for the
        matched content.
        """
        return {
            "rule_id": self.rule_id,
            "family": self.family,
            "confidence": self.confidence,
            "context": self.context,
            "channel": self.channel,
            "start": self.start,
            "end": self.end,
            "length": self.length,
        }


def merge_spans(spans: Sequence[SpanSpec]) -> List[SpanSpec]:
    """Sort by position and drop spans that overlap an already-kept one.

    Kept deterministic: earlier start wins, longer span wins on a tie, so
    a private-key block can never be fragmented by a narrower match inside
    it. Returns a new list; inputs are not mutated.
    """
    ordered = sorted(spans, key=lambda s: (s.start, -(s.end - s.start)))
    kept: List[SpanSpec] = []
    for span in ordered:
        if kept and span.start < kept[-1].end:
            continue
        kept.append(span)
    return kept

# --------------------------------------------------- placeholder allowlist

# Design 5.1: the static allowlist. In-repo and versioned, so a deployment
# scanning this repository does not fail on its own test fixtures.
PLACEHOLDER_WORDS = frozenset({
    "your_api_key_here", "your_token_here", "your_secret_here",
    "your-api-key", "your_api_key",
    "changeme", "change_me", "change-me", "replaceme", "replace_me",
    "dummy", "example", "sample", "fake", "test", "testing", "todo",
    "placeholder", "redacted", "none", "null", "notset", "unset",
    "xxxx", "xxxxxxxx", "yourtoken", "mysupersecret", "hunter2",
})
# Marker shapes: <REDACTED>, [REDACTED], ***, _<<REDACTED>>_ ...
_PLACEHOLDER_SHAPE_RE = re.compile(
    r"^[\W_]*(?:redacted|removed|hidden|scrubbed|elided|omitted)[\W_]*$",
    re.IGNORECASE)
_REDACTED_MARKER = "<REDACTED>"

# A token is only a placeholder if its *alphabet* is trivial (one symbol,
# '*'/'x'/'.' runs, sequences like abcdef) or if it is a known word. The
# point is to stay in the low-entropy regime, where a real credential can
# never live.
_SEQUENTIAL_ALPHABETS = ("abcdefghijklmnopqrstuvwxyz",
                         "0123456789",
                         "abcdefghijklmnopqrstuvwxyz0123456789")


def _is_trivial_alphabet(token: str) -> bool:
    if len(set(token)) <= 1:
        return True                      # xxxx, ********, ....
    lower = token.lower()
    if lower in _SEQUENTIAL_ALPHABETS:
        return True
    for alphabet in _SEQUENTIAL_ALPHABETS:
        for start in range(len(alphabet)):
            for length in range(4, len(alphabet) - start + 1):
                if lower == alphabet[start:start + length]:
                    return True
    return False


def is_placeholder(token: str) -> bool:
    """True when a matched value is provably not a real credential.

    Never throws: the allowlist runs inside the detection hot path, and a
    classification failure there must not be able to fail *open*.
    """
    try:
        return _is_placeholder(token)
    except Exception:                     # pragma: no cover - defensive
        return False


def _is_placeholder(token: str) -> bool:
    value = token.strip().strip("'\"`()[]<>").strip()
    if not value:
        return False
    if value == _REDACTED_MARKER:
        return True                       # idempotence (design 2.5)
    if _PLACEHOLDER_SHAPE_RE.match(value):
        return True
    if value.startswith("<") and value.endswith(">"):
        return True                       # <YOUR_KEY>, <token>
    if value.startswith("${") and value.endswith("}"):
        return True                       # shell interpolation in a doc
    collapsed = re.sub(r"[^A-Za-z0-9]+", "", value).lower()
    if not collapsed:
        return True                       # "***", "---": no credential bytes
    for word in PLACEHOLDER_WORDS:
        flat = re.sub(r"[^A-Za-z0-9]+", "", word)
        if collapsed == flat:
            return True
        # Prefixed/suffixed word: "EXAMPLE_SECRET", "sk-test", "fake-key".
        # Only the documented words count, so ordinary credentials are
        # untouched (a real key never ends in "token").
        if collapsed.startswith(flat) or collapsed.endswith(flat):
            return True
    return _is_trivial_alphabet(value)


def _looks_placeholderish(token: str) -> bool:
    """Cheap pre-check used before running the full placeholder test."""
    if not token:
        return True
    if len(set(token)) <= 1:
        return True
    collapsed = re.sub(r"[^A-Za-z0-9]+", "", token).lower()
    if not collapsed:
        return True
    return any(w in collapsed for w in ("xxx", "redact", "placeholder",
                                        "changeme", "dummy"))

# -------------------------------------------------------- T1 vendor patterns

# Each entry is (rule_id, compiled regex, validator). Vendor prefixes are
# distinct enough that an anchored prefix + shape gives near-zero false
# positives; where the format allows, a validator adds real structure
# (design 2.2 rule 1 - a JWT is only a JWT if its header decodes).
_OPENAI_RE = re.compile(r"\bsk-(?:proj|ant|live)?-?([A-Za-z0-9_\-]{20,})")
_GITHUB_RE = re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}\b"
                        r"|\bgithub_pat_[A-Za-z0-9_]{22,255}\b")
_AWS_KEY_ID_RE = re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA|ANPA)[0-9A-Z]{16}\b")
_GITLAB_RE = re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}")
_SLACK_RE = re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}")
_STRIPE_RE = re.compile(r"\b(?:sk|rk)_live_[A-Za-z0-9]{16,}")
_JWT_RE = re.compile(
    r"\beyJ[A-Za-z0-9_\-]{4,}\.[A-Za-z0-9_\-]{4,}\.[A-Za-z0-9_\-]{4,}")
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----.*?"
    r"-----END (?:[A-Z0-9]+ )*PRIVATE KEY-----",
    re.DOTALL)
# Secret-store file names: a *path* fact (design 2.4 item 3). Reading these
# on an outbound channel is refused rather than sanitized, because the
# guard cannot see the content it would be rewriting.
SECRET_STORE_NAMES = frozenset({
    ".env", ".npmrc", ".netrc", ".pgpass", ".htpasswd", "credentials",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "kubeconfig",
})
SECRET_STORE_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".tfvars",
                         ".keystore", ".jks")
# Whole-environment expansion: value-free detection (T2), ASK-only.
ENV_DUMP_RE = re.compile(
    r"(?<![\w.])(?:printenv|env)\s+\|\s*\w+"
    r"|(?<![\w.])printenv\s+[A-Za-z_][A-Za-z0-9_]*"
    r"|(?<![\w.])cat\s+/proc/self/environ"
    r"|(?<![\w.])export\s+-p\b")


def _b64url_json_has_alg(segment: str) -> bool:
    """Structural JWT check: the header segment must decode to JSON+`alg`."""
    if len(segment) % 4 == 1:
        return False
    padded = segment + "=" * (-len(segment) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
    except (binascii.Error, ValueError, UnicodeEncodeError):
        return False
    try:
        header = json.loads(decoded.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return False
    return isinstance(header, dict) and "alg" in header


def _note_context(text: str, index: int) -> str:
    """The introduction of a match: 'prose' or 'key=<word>'."""
    line_start = text.rfind("\n", 0, index) + 1
    prefix = text[line_start:index]
    match = re.search(r"([A-Za-z_][A-Za-z0-9_\-]*)\s*[:=]\s*[\"']?\s*$", prefix)
    return "key=" + match.group(1) if match else "prose"


def detect_secrets(text: str, channel: str = "") -> List[SpanSpec]:
    """T1 known-pattern detection: deterministic, near-zero false positives.

    Placeholder and already-redacted matches are dropped here (they are
    facts the policy layer would otherwise have to re-litigate): the
    allowlist is deterministic and needs no config (design 5.1).
    """
    spans: List[SpanSpec] = []

    def add(match: "re.Match[str]", rule_id: str, notes: Iterable[str] = ()):
        raw = match.group(0)
        if is_placeholder(raw) or _looks_placeholderish(raw):
            return
        spans.append(SpanSpec(
            raw=raw, start=match.start(), end=match.end(), rule_id=rule_id,
            family=FAMILY_SECRET, confidence=CONFIDENCE_DETERMINISTIC,
            context=_note_context(text, match.start()), channel=channel,
            notes=list(notes)))

    for match in _PRIVATE_KEY_RE.finditer(text):
        add(match, RULE_PRIVATE_KEY_BLOCK,
            ["whole block withheld; header/trailer identify it unambiguously"])
    for match in _OPENAI_RE.finditer(text):
        add(match, RULE_OPENAI_KEY, ["vendor prefix + length + charset"])
    for match in _GITHUB_RE.finditer(text):
        add(match, RULE_GITHUB_TOKEN, ["fixed-format vendor prefix"])
    for match in _AWS_KEY_ID_RE.finditer(text):
        add(match, RULE_AWS_ACCESS_KEY_ID, ["reserved vendor prefix"])
    for match in _GITLAB_RE.finditer(text):
        add(match, RULE_GITLAB_TOKEN, ["vendor prefix"])
    for match in _SLACK_RE.finditer(text):
        add(match, RULE_SLACK_TOKEN, ["vendor prefix"])
    for match in _STRIPE_RE.finditer(text):
        add(match, RULE_STRIPE_KEY, ["prefix + live mode (test keys exempt)"])
    for match in _JWT_RE.finditer(text):
        header = match.group(0).split(".", 1)[0]
        if not _b64url_json_has_alg(header):
            continue
        add(match, RULE_JWT, ["header segment decodes to JSON containing alg"])
    return merge_spans(spans)
