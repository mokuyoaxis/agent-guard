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
import fnmatch
import hashlib
import json
import math
import os
import platform
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple
from urllib.parse import unquote

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
RULE_PYPI_TOKEN = "secret/pypi-token"
RULE_JWT = "secret/jwt"
RULE_PRIVATE_KEY_BLOCK = "secret/private-key-block"
RULE_CONNECTION_PASSWORD = "secret/connection-password"
RULE_NPM_TOKEN = "secret/npm-token"
RULE_GOOGLE_API_KEY = "secret/google-api-key"
RULE_HUGGINGFACE_TOKEN = "secret/huggingface-token"
RULE_SENDGRID_KEY = "secret/sendgrid-key"
RULE_VAULT_TOKEN = "secret/vault-token"
RULE_DIGITALOCEAN_TOKEN = "secret/digitalocean-token"
RULE_PERPLEXITY_KEY = "secret/perplexity-key"
RULE_HTTP_BASIC_AUTH = "secret/http-basic-auth"
# A value-free reference to a secret source (env var name / secret store).
RULE_SECRET_SOURCE = "secret/source-reference"

RULE_HOST_ABSOLUTE_PATH = "path/host-absolute"
RULE_WORKSPACE_PATH = "path/workspace-relative"
RULE_GENERIC_ABSOLUTE_PATH = "path/generic-absolute"
RULE_SYSTEM_PATH = "path/system"
RULE_DEVICE_PATH = "path/device"

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
    # Set by `scan_text`, never by a detector: the payload reads a secret
    # store the guard cannot see into, so nothing here can be certified.
    source_dump: bool = False
    # Set by policy-facing callers to record that the *context gate* fired
    # for a contextual (T3-class) match. Pure payload; no bytes.
    context_gate: bool = False

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


def verdict_rows(spans, verdicts):
    """Safe public span metadata for an assessed payload."""
    return [{**span.safe(), "context": span.context.partition("=")[0],
             "decision": verdict.decision, "code": verdict.code}
            for span, verdict in zip(spans, verdicts)]

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
    "dummy_value", "dummy_key", "dummy_token", "example_secret",
    "example_key", "example_token", "sample_key", "sample_token",
    "fake_key", "fake_token", "test_key", "test_token",
})
_PLACEHOLDER_NORMALIZED_WORDS = frozenset(word.replace("-", "_") for word in PLACEHOLDER_WORDS)
# Public AWS documentation examples, preserved as exact values rather than
# granting amnesty to every random credential ending in "example".
_PUBLIC_EXAMPLE_VALUES = frozenset({"AKIA" + "IOSFODNN7EXAMPLE", "ASIA" + "IOSFODNN7EXAMPLE"})
# Marker shapes: <REDACTED>, [REDACTED], ***, _<<REDACTED>>_ ...
_PLACEHOLDER_SHAPE_RE = re.compile(
    r"^[\W_]*(?:redacted|removed|hidden|scrubbed|elided|omitted)[\W_]*$",
    re.IGNORECASE)
_REDACTED_MARKER = "<REDACTED>"
# Only remove a public, reviewed prefix before checking a whole placeholder
# body. Words embedded in random credential bytes are never exemptions.
MODERN_VENDOR_PREFIX_PATTERN = r"npm_|AIza|hf_|SG\.|hv[sb]\.|do[por]_v1_|pplx-"
_PLACEHOLDER_VENDOR_PREFIX_RE = re.compile(
    r"^(?:sk-proj-|sk-ant-(?:api03-)?|sk-or-v1-|sk-live-|sk-|(?:sk|rk)_live_|"
    r"(?:ghp|gho|ghu|ghs|ghr)_|github_pat_|glpat-|xox[baprs]-|pypi-|"
    r"AKIA|ASIA|" + MODERN_VENDOR_PREFIX_PATTERN + r")")
_PLACEHOLDER_TEMPLATE_RE = re.compile(
    r"(?:<[A-Za-z_][A-Za-z0-9_]*>|\$\{[A-Za-z_][A-Za-z0-9_]*\})\Z")

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
    return len(lower) >= 4 and any(lower in alphabet for alphabet in _SEQUENTIAL_ALPHABETS)


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
    value = token.strip().strip("'\"`()").strip()
    if not value:
        return False
    if value in _PUBLIC_EXAMPLE_VALUES:
        return True
    prefix = _PLACEHOLDER_VENDOR_PREFIX_RE.match(value)
    if prefix:
        value = value[prefix.end():]
        if not value:
            return False
        if prefix.group() == "SG.":
            parts = value.split(".")
            if len(parts) == 2 and all(part and len(set(part)) == 1 for part in parts):
                return True
    if _PLACEHOLDER_SHAPE_RE.fullmatch(value) or _PLACEHOLDER_TEMPLATE_RE.fullmatch(value):
        return True                       # whole marker/template only
    normalized = value.lower().replace("-", "_")
    if normalized in _PLACEHOLDER_NORMALIZED_WORDS:
        return True                       # finite whole-word examples
    return _is_trivial_alphabet(value)


def _looks_placeholderish(token: str) -> bool:
    """Compatibility predicate with the same exact whole-value contract."""
    return is_placeholder(token)

# -------------------------------------------------------- T1 vendor patterns

# Each entry is (rule_id, compiled regex, validator). Vendor prefixes are
# distinct enough that an anchored prefix + shape gives near-zero false
# positives; where the format allows, a validator adds real structure
# (design 2.2 rule 1 - a JWT is only a JWT if its header decodes).
# ASCII identifier boundaries allow adjacent CJK prose, while still refusing
# substrings of English identifiers. Unicode \b conflates those two cases.
_TOKEN_START = r"(?<![A-Za-z0-9_])"
_TOKEN_END = r"(?![A-Za-z0-9_])"
_OPENAI_RE = re.compile(_TOKEN_START + r"sk-(?:proj|ant|live)?-?([A-Za-z0-9_\-]{20,})")
_GITHUB_RE = re.compile(_TOKEN_START + r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}" + _TOKEN_END
                        + r"|" + _TOKEN_START + r"github_pat_[A-Za-z0-9_]{22,255}" + _TOKEN_END)
# The vendor's ghs_APPID_JWT wrapper; app ID is an opaque ASCII field.
# No fixed total length, business claims or signature authentication.
_GITHUB_INSTALLATION_RE = re.compile(
    _TOKEN_START + r"ghs_[A-Za-z0-9-]+_(?P<header>[A-Za-z0-9_-]{4,})"
    r"\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}"
    r"(?![A-Za-z0-9_-]|\.[A-Za-z0-9_-])")
# IAM group/user/role/policy IDs have separate public resource prefixes.
# They are not access-key IDs; other credential contexts still apply.
_AWS_KEY_ID_RE = re.compile(_TOKEN_START + r"(?:AKIA|ASIA)[0-9A-Z]{16}" + _TOKEN_END)
_GITLAB_RE = re.compile(_TOKEN_START + r"glpat-[A-Za-z0-9_\-]{20,}")
_SLACK_RE = re.compile(_TOKEN_START + r"xox[baprs]-[A-Za-z0-9\-]{10,}")
_STRIPE_RE = re.compile(_TOKEN_START + r"(?:sk|rk)_live_[A-Za-z0-9]{16,}")
# PyPI's documented scanner format allows arbitrary Macaroon caveat length.
# Capture the entire base64url body, rather than an 85-character prefix.
_PYPI_RE = re.compile(_TOKEN_START + r"pypi-[A-Za-z0-9_-]{85,}")
_MODERN_VENDOR_PATTERNS = (
    (RULE_NPM_TOKEN, re.compile(_TOKEN_START + r"npm_[A-Za-z0-9]{36}" + _TOKEN_END)),
    (RULE_GOOGLE_API_KEY, re.compile(_TOKEN_START + r"AIza[A-Za-z0-9_-]{35}(?![A-Za-z0-9_-])")),
    (RULE_HUGGINGFACE_TOKEN, re.compile(_TOKEN_START + r"hf_[A-Za-z0-9]{34,}" + _TOKEN_END)),
    (RULE_SENDGRID_KEY, re.compile(_TOKEN_START + r"SG\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}(?![A-Za-z0-9_-])")),
    (RULE_VAULT_TOKEN, re.compile(_TOKEN_START + r"hv[sb]\.[A-Za-z0-9_-]{24,}" + _TOKEN_END)),
    (RULE_DIGITALOCEAN_TOKEN, re.compile(_TOKEN_START + r"do[por]_v1_[0-9a-f]{64}" + _TOKEN_END)),
    (RULE_PERPLEXITY_KEY, re.compile(_TOKEN_START + r"pplx-[A-Za-z0-9]{40,}" + _TOKEN_END)),
)
_HTTP_BASIC_RE = re.compile(
    r"(?<![A-Za-z0-9_-])Authorization\s*:\s*Basic\s+"
    r"(?P<b64>[A-Za-z0-9+/]{8,}={0,2})(?![A-Za-z0-9+/=])", re.IGNORECASE | re.ASCII)
_JWT_RE = re.compile(
    _TOKEN_START + r"eyJ[A-Za-z0-9_\-]{4,}\.[A-Za-z0-9_\-]{4,}\.[A-Za-z0-9_\-]{4,}")
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----.*?"
    r"-----END (?:[A-Z0-9]+ )*PRIVATE KEY-----",
    re.DOTALL)
# URI userinfo is an explicit credential context (RFC 3986 section 3.2.1).
# Work on original coordinates instead of urlsplit: URL parsers normalize
# input and cannot provide exact offsets for a serialized JSON payload.
# A JSON-escaped slash is accepted only at the scheme separator. Other
# encoded delimiters stay in the password's original, replaceable span.
_CONNECTION_PREFIX_RE = re.compile(
    r"(?<![A-Za-z0-9+.-])[A-Za-z][A-Za-z0-9+.-]{0,63}:(?:/|\\/){2}")
_CONNECTION_AUTHORITY_RE = re.compile(r"[^\s/?#\"'`]+")
_CONNECTION_TEMPLATE_RE = re.compile(
    r"(?:<[^<>@]+>|\$\{[^{}@]+\}|%[A-Za-z_][A-Za-z0-9_]*%)\Z")


def _valid_basic_auth(token: str) -> bool:
    if len(token) % 4:
        return False
    try:
        decoded = base64.b64decode(token, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return False
    _, colon, password = decoded.partition(":")
    return (bool(colon and password) and all(ord(ch) >= 0x20 and ord(ch) != 0x7f for ch in decoded)
            and not (is_placeholder(password) or _CONNECTION_TEMPLATE_RE.fullmatch(password)
                     or re.fullmatch(r"\*{3,}", password)))


def _connection_passwords(text: str, channel: str) -> List[SpanSpec]:
    """Detect nonempty URI passwords; preserve account and host information.

    Plain weak passwords are still passwords. Unlike vendor test tokens,
    short/repeated literals and words such as 'test' are not exempt here.
    Only whole template expressions and redaction markers are exempt.
    Raw '/', '?', '#' and quoting delimit authority; they must be encoded
    within a URI password. This is not a general DSN or obfuscation parser.
    """
    spans = []
    for prefix in _CONNECTION_PREFIX_RE.finditer(text):
        match = _CONNECTION_AUTHORITY_RE.match(text, prefix.end())
        if match is None:
            continue
        authority = match.group(0)
        at = authority.rfind("@")
        colon = authority.find(":")
        if colon < 0 or at <= colon + 1 or at == len(authority) - 1:
            continue
        # Last @ also withholds the complete password in a common malformed
        # spelling containing a literal @; never leave a password suffix.
        start, end = match.start() + colon + 1, match.start() + at
        raw = text[start:end]
        # Decode only to recognize an explicit template; arbitrary byte
        # passwords need not be UTF-8 and are still redacted as raw spans.
        decoded = unquote(raw, errors="replace")
        if (_CONNECTION_TEMPLATE_RE.fullmatch(raw) or
                _CONNECTION_TEMPLATE_RE.fullmatch(decoded) or
                _PLACEHOLDER_SHAPE_RE.fullmatch(decoded) or
                re.fullmatch(r"\*{3,}", decoded)):
            continue
        spans.append(SpanSpec(
            raw=raw, start=start, end=end,
            rule_id=RULE_CONNECTION_PASSWORD, family=FAMILY_SECRET,
            confidence=CONFIDENCE_DETERMINISTIC,
            context="uri-userinfo", channel=channel,
            notes=["nonempty password in URI userinfo; account and host retained"]))
    return spans


# Secret-store file names: a *path* fact (design 2.4 item 3). Reading these
# on an outbound channel is refused rather than sanitized, because the
# guard cannot see the content it would be rewriting.
SECRET_STORE_NAMES = frozenset({
    ".env", ".npmrc", ".netrc", ".pgpass", ".htpasswd", "credentials",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "kubeconfig",
})
SECRET_STORE_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".tfvars",
                         ".keystore", ".jks")
# `.env` and its dotted variants (`.env.local`, `.env.production`): these
# are the same secret store, and `.env.local` is the file people actually
# commit by accident.
SECRET_STORE_ENV_RE = re.compile(r"(?<![\w.@])\.env(?:\.[A-Za-z0-9_-]+)*"
                                 r"(?![\w-])")
ENV_TEMPLATE_NAMES = frozenset({".env.example", ".env.sample", ".env.template", ".env.dist"})


def secret_store_name(name: str) -> bool:
    """Classify a basename before reading an export candidate."""
    name = name.lower()
    return (name not in ENV_TEMPLATE_NAMES and
            (name in SECRET_STORE_NAMES or name.endswith(SECRET_STORE_SUFFIXES)
             or name.startswith(".env.")))
# Whole-environment expansion: value-free detection (T2), ASK-only.
# "Read the WHOLE environment": the *shape* of the command, not the word.
# Design 2.4 item 4 - only the no-argument expansion forms are dumps:
#   `env`, `env | grep X`, `printenv`, `set`, `export -p`, /proc/self/environ
# `env FOO=bar cmd` and `printenv FOO` are *not* dumps (they name what they
# touch), and prose that merely contains the word "env" is not a command.
# A rule that fires on documentation is a rule that gets disabled.
# `env`/`printenv`/`set` must also be *invoked as a command*: at the start
# of the payload (optionally after a shell separator) or followed by a pipe.
# Without that anchor, any line of Python (`if env:`) or any regex that
# quotes the word would be read as a dump - and this repository's own source
# is exactly that. Only the whole-environment expansion forms count
# (design 2.4 item 4): `printenv FOO` and `env FOO=bar cmd` name what they
# touch and are not dumps.
_SHELL_AT_START = r"(?:^|[;&|]\s*|\$\s+)"
ENV_DUMP_RE = re.compile(
    _SHELL_AT_START + r"(?:env|printenv)\s*\|"
    r"|" + _SHELL_AT_START + r"(?:env|printenv|export\s+-p|set)\s*$"
    r"|(?<![\w.])cat\s+/proc/self/environ", re.MULTILINE)
_BASH_ENV_DUMP_RE = re.compile(
    r"(?:^|[;&|()])[ \t]*(?:env|printenv|export[ \t]+-p|set)(?=[ \t]*(?:$|[;&|)<>]))"
    r"|(?<![\w.])cat[ \t]+/proc/self/environ", re.MULTILINE)


def _b64url_json_has_alg(segment: str) -> bool:
    """Structural JWS check: JSON object with a nonempty ASCII `alg` string."""
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
    if not isinstance(header, dict):
        return False
    alg = header.get("alg")
    # RFC 7515 4.1.1 declares ASCII StringOrURI. Do not infer authentication,
    # validate business claims or restrict the caller to a known enum.
    return isinstance(alg, str) and bool(alg) and alg.isascii()


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

    def add(match: "re.Match[str]", rule_id: str, notes: Iterable[str] = (), *, group=0):
        raw = match.group(group)
        if is_placeholder(raw):
            return
        start, end = match.span(group)
        spans.append(SpanSpec(
            raw=raw, start=start, end=end, rule_id=rule_id,
            family=FAMILY_SECRET, confidence=CONFIDENCE_DETERMINISTIC,
            context=_note_context(text, start), channel=channel,
            notes=list(notes)))

    for match in _PRIVATE_KEY_RE.finditer(text):
        add(match, RULE_PRIVATE_KEY_BLOCK,
            ["whole block withheld; header/trailer identify it unambiguously"])
    for match in _OPENAI_RE.finditer(text):
        add(match, RULE_OPENAI_KEY, ["vendor prefix + length + charset"])
    for match in _GITHUB_RE.finditer(text):
        add(match, RULE_GITHUB_TOKEN, ["fixed-format vendor prefix"])
    for match in _GITHUB_INSTALLATION_RE.finditer(text):
        if len(match.group(0)) >= 40 and _b64url_json_has_alg(match.group("header")):
            add(match, RULE_GITHUB_TOKEN, ["vendor installation wrapper + structural JWT header"])
    for match in _AWS_KEY_ID_RE.finditer(text):
        add(match, RULE_AWS_ACCESS_KEY_ID, ["reserved vendor prefix"])
    for match in _GITLAB_RE.finditer(text):
        add(match, RULE_GITLAB_TOKEN, ["vendor prefix"])
    for match in _SLACK_RE.finditer(text):
        add(match, RULE_SLACK_TOKEN, ["vendor prefix"])
    for match in _STRIPE_RE.finditer(text):
        add(match, RULE_STRIPE_KEY, ["prefix + live mode (test keys exempt)"])
    for match in _PYPI_RE.finditer(text):
        add(match, RULE_PYPI_TOKEN, ["vendor prefix + documented base64url minimum"])
    for rule_id, pattern in _MODERN_VENDOR_PATTERNS:
        for match in pattern.finditer(text):
            add(match, rule_id, ["vendor prefix + length + charset"])
    for match in _HTTP_BASIC_RE.finditer(text):
        if _valid_basic_auth(match.group("b64")):
            add(match, RULE_HTTP_BASIC_AUTH, ["structured HTTP Basic credentials"], group="b64")
    for match in _JWT_RE.finditer(text):
        header = match.group(0).split(".", 1)[0]
        if not _b64url_json_has_alg(header):
            continue
        add(match, RULE_JWT, ["header segment decodes to JSON containing alg"])
    spans.extend(_connection_passwords(text, channel))
    return merge_spans(spans)


# ------------------------------------------------------------ path detection
#
# Design 3.1: the workspace-relative exemption is the core rule. A path is
# only a host identifier if it is OUTSIDE the workspace - so the guard uses
# the one boundary it already computes confidently (delete-guard's
# `discover_workspace`) and treats everything else as suspect UNLESS it is a
# well-known system prefix. An absolute path with no prefix correlation at
# all is only `contextual`, which keeps the default quiet (design 3.4/5.6).

# POSIX absolute path. `~` is deliberately not a component character: it is
# left-trimmed by the boundary, so `~/x/y` is redacted rather than being
# lexed as a literal `~` directory name.
# The lookbehind excludes `.` and `~` as well as word chars: without them
# the pattern cuts a relative path in half (`./src/main.py` -> `/src/main.py`
# is a host-absolute-looking match for a path that never left the workspace).
# The group requires at least one separator, which is design 3.2's ">= 2
# components" floor - without it a bare `/` (which occurs in every sentence
# that contains a slash) would match.
_POSIX_PATH_RE = re.compile(
    r"(?<![\w:/~.])/(?:[A-Za-z0-9._@+-]+/)+[A-Za-z0-9._@+-]+")
# Windows drive-absolute, UNC and device paths: `C:\a\b`, `C:/a`, `\\\\h\\s`,
# `\\\\?\\C:\\x`, `\\\\.\\pipe\\p`.
_WINDOWS_PATH_RE = re.compile(
    r"(?<![\w:])[A-Za-z]:[\\/](?:[^\\/\s\"'<>|;:]+[\\/])*[^\\/\s\"'<>|;:]*"
    r"|\\\\[^\\/\s]{1,}[\\/][^\\/\s]+(?:[\\/][^\\/\s\"'<>|;]*)*")
# Windows environment interpolation: a path we cannot resolve (design 3.2).
# A `%VAR%`/`$env:X` token is a *path* fact only where it introduces one;
# `%dT%` in a strftime format string is not a path and must stay quiet.
_WINDOWS_ENV_RE = re.compile(
    r"(?:%[A-Za-z_][A-Za-z0-9_]*%|\$env:[A-Za-z_][A-Za-z0-9_]*)[\\/]")

# System prefixes that identify the operating system, not the host. ALLOW by
# default (design 3.1/exotic-last row), configurable via AGENT_GUARD_SYSTEM_PREFIXES.
DEFAULT_SYSTEM_PREFIXES = (
    "/usr", "/bin", "/sbin", "/lib", "/lib64", "/opt", "/etc", "/var",
    "/proc", "/sys", "/dev", "/srv", "/run", "/boot", "/snap", "/nix",
    "/Applications", "/System", "/Library", "/dev/null",
    "C:\\Windows", "C:\\Program Files", "C:\\Program Files (x86)",
    "C:\\ProgramData",
)
# Prefixes that host-qualify a location so strongly that they are treated as
# identifying even though they sit under a system-looking root (design 3.2
# "CI runners" / "Sandboxes" rows). Checked BEFORE the system prefixes:
# a CI workspace is `/home/runner/work/...`, and `/var/lib/jenkins` is a
# runner home, not a system directory.
_CI_ROOT_PREFIXES = ("/home/", "/Users/", "/root", "/Volumes/",
                     "/var/folders/", "/tmp/", "/workspace", "/builds",
                     "/runner/", "/github/workspace", "/var/lib/jenkins",
                     "/private/var/folders/", "/private/tmp", "/private/home",
                     "/private/Users", "/private/root", "/private/var/tmp")
# Locations that look like host paths but name a convention instead: macOS
# ships `/Users/Shared`, and `/opt`, `/usr/local` are install roots.
_EXEMPT_ABSOLUTE = ("/Users/Shared", "/usr/local", "/opt")


def _split_env_list(value: str) -> List[str]:
    return [item.strip() for item in re.split(r"[:;,]", value) if item.strip()]


def host_markers() -> Dict[str, Any]:
    """Derive the deny-set of host prefixes (design 3.3) - a set of literals,
    never a pattern, and one that degrades portably when `getpass` fails.

    Reads *names and shapes* only: `os.environ` is consulted for HOME-like
    location prefixes (the actual things to redact), never for secret values.
    """
    names: Set[str] = set()
    prefixes: Set[str] = set()
    hostnames: Set[str] = set()

    for key in ("HOME", "USERPROFILE", "LOCALAPPDATA", "TEMP", "TMP"):
        value = os.environ.get(key)
        if value and os.path.isabs(value):
            prefixes.add(os.path.normpath(value))
    try:
        home = os.path.expanduser("~")
    except Exception:                     # pragma: no cover - exotic setups
        home = ""
    if home and os.path.isabs(home):
        prefixes.add(os.path.normpath(home))

    try:
        import getpass
        user = getpass.getuser()
    except Exception:                     # pragma: no cover - container/CI
        user = ""
    for candidate in (user, os.environ.get("USER"),
                      os.environ.get("LOGNAME"), os.environ.get("USERNAME")):
        if candidate:
            names.add(candidate)
    if home:
        names.add(os.path.basename(home))

    for key in ("AGENT_GUARD_HOSTNAMES", "AGENT_GUARD_HOME_MARKERS"):
        extra = os.environ.get(key)
        if extra:
            names.update(_split_env_list(extra))
    for key in ("AGENT_GUARD_HOME_PREFIXES",):
        extra = os.environ.get(key)
        if extra:
            prefixes.update(_split_env_list(extra))

    try:
        hostnames.add(platform.node())
        import socket
        hostnames.add(socket.gethostname())
    except Exception:                     # pragma: no cover - exotic setups
        pass
    hostnames.discard("")

    overrides = os.environ.get("AGENT_GUARD_SYSTEM_PREFIXES")
    system_prefixes = (tuple(_split_env_list(overrides)) if overrides
                       else DEFAULT_SYSTEM_PREFIXES)
    return {"prefixes": prefixes, "names": names, "hostnames": hostnames,
            "system_prefixes": system_prefixes}


def workspace_ancestors(workspace: Optional[str]) -> List[str]:
    """Ancestors of the workspace root: by definition not to be echoed raw.

    The filesystem root is excluded deliberately. It is *technically* an
    ancestor of every workspace, but treating `/` as identifying would make
    the rule a no-op (nothing is more generic than `/`), and the same is
    true of any single-component prefix such as `/home` or `C:\\` - those
    carry no identity and are the documented generic tier (design 3.3).
    """
    if not workspace:
        return []
    physical = os.path.normpath(os.path.realpath(os.path.abspath(workspace)))
    out: List[str] = []
    current = physical
    while True:
        parent = os.path.dirname(current)
        if parent == current:              # reached the root
            break
        if parent != os.path.dirname(parent) or os.sep not in parent:
            # parent is itself a root ("/" or "C:\"): not identifying.
            break
        out.append(parent)
        current = parent
    return [p for p in out if len(p.strip(os.sep)) > 1]


@dataclass
class PathVerdict:
    """One detected path with its boundary facts (facts, not decisions)."""

    raw: str
    start: int
    end: int
    path: str                             # normalized comparison form
    windows: bool = False
    absolute: bool = True
    inside_workspace: bool = False
    at_workspace_root: bool = False
    is_system: bool = False
    deterministic: bool = False           # matched the host deny-set
    notes: List[str] = field(default_factory=list)

    @property
    def length(self) -> int:
        return self.end - self.start


def _normalize_candidate(raw: str, windows: bool) -> str:
    """Normalize for comparison only; the raw spelling drives the rewrite.

    Returns a platform-appropriate normalized form: on Windows the result
    keeps ``\\`` separators (UNC/device detection depends on
    ``normalized.startswith('\\\\')``), on POSIX it keeps ``/``. Downstream
    callers must not assume one separator — ``_path_is_under`` handles
    that for its own comparisons, and UNC detection happens before any
    separator-sensitive prefix test.

    The reason we do NOT simply flip everything to ``/``: Windows
    ``os.path.normpath`` flips ``/`` to ``\\`` anyway, so on a Windows host
    any POSIX-style input like ``/usr/lib`` would end up as ``\\usr\\lib``
    and then every prefix test (all of which use ``"/usr"``) would fail —
    which is exactly what the classify layer's `windows` flag exists to
    avoid.
    """
    value = raw.rstrip(".,;:!?)\"'`")
    if len(value) > 1 and value.endswith(("/", "\\")):
        value = value[:-1]
    if windows:
        return os.path.normpath(value.replace("/", "\\"))
    return os.path.normpath(value.replace("\\", "/"))


def _to_posix(path: str) -> str:
    """Convert any platform path to forward-slash form for prefix tests."""
    return path.replace("\\", "/")


def _clean_candidate(text: str, span: "re.Match[str]") -> Optional[str]:
    """The match, minus sentence punctuation and balanced wrapping."""
    start, end = span.start(), span.end()
    segment = text[start:end]
    # A `(...)`-wrapped path (markdown link target) is redacted whole.
    if start > 0 and end < len(text) and text[start - 1] == "(" \
            and text[end] == ")":
        return "(" + segment + ")"
    return segment


def _path_is_under(path: str, prefix: str, windows: bool) -> bool:
    if windows:
        a = path.replace("\\", "/").lower()
        b = prefix.replace("\\", "/").lower()
    else:
        a, b = path, prefix
    if a == b:
        return True
    return a.startswith(b.rstrip("/") + "/")


def classify_path_candidate(raw: str, workspace: Optional[str],
                            markers: Optional[Dict[str, Any]] = None,
                            windows: Optional[bool] = None
                            ) -> Optional[PathVerdict]:
    """Fact extraction for one candidate path string."""
    markers = markers or host_markers()
    if windows is None:
        windows = bool(_WINDOWS_PATH_RE.fullmatch(raw))
    value = raw.rstrip(".,;:!?)\"'`")
    if not value:
        return None
    if value.startswith("(") and value.endswith(")"):
        value = value[1:-1]

    # Separator normalization is only sound when the string actually IS a
    # path: Windows accepts both separators, POSIX does not (a backslash is
    # an ordinary filename byte), and fabricating semantics is the exact
    # mistake `classifier._is_windows_absolute` documents.
    normalized = _normalize_candidate(value, windows)
    # POSIX form for every subsequent prefix test — handles mixed
    # platform inputs (a Windows host seeing "/usr/lib", or a Linux host
    # seeing a path that was realpath'd somewhere). UNC/device detection
    # (which needs the raw `\\` prefix) stays on `normalized`.
    posix = _to_posix(normalized)
    fact = PathVerdict(raw=raw, start=0, end=len(raw), path=posix,
                       windows=windows)

    prefix_pool = [_to_posix(p) for p in markers["prefixes"]]
    system = tuple(_to_posix(s) for s in markers["system_prefixes"])
    exempt_locations = [_to_posix(e) for e in _EXEMPT_ABSOLUTE]

    if windows:
        # A bare drive root (`C:\`) identifies the OS layout, not a host, and
        # carries no path at all. Design 3.2's component floor applies here
        # too; a real Windows path has at least two components.
        if re.fullmatch(r"[A-Za-z]:[\\/]?", normalized):
            fact.is_system = True
            return fact
        for system_prefix in system:
            if _path_is_under(posix, system_prefix, True):
                fact.is_system = True
                return fact

    # A path at or under a known host prefix (HOME/TEMP/USERPROFILE) or any
    # workspace ancestor is deterministic - a literal deny-set match, not a
    # guess. Workspace ancestors are checked first because they are the most
    # trusted source (design 3.3).
    #
    # IMPORTANT: we intentionally do NOT call os.path.realpath() or
    # os.path.abspath() on `workspace`. Those would prefix a drive letter
    # on Windows (turning "/home/alice/work/agent-guard" into
    # "C:\\home\\alice\\work\\agent-guard") and break cross-platform
    # workspace matching. Callers pass the workspace in canonical form.
    if workspace:
        ws_windows = bool(_WINDOWS_PATH_RE.fullmatch(workspace))
        physical_posix = _to_posix(_normalize_candidate(workspace, ws_windows))
        if _path_is_under(posix, physical_posix, False):
            fact.inside_workspace = True
            fact.at_workspace_root = posix == physical_posix
            return fact
        for ancestor in workspace_ancestors(workspace):
            anc_posix = _to_posix(ancestor)
            if _path_is_under(posix, anc_posix, False):
                fact.deterministic = True
                fact.notes.append("workspace ancestor")
                return fact

    for prefix in sorted(prefix_pool, key=len, reverse=True):
        if _path_is_under(posix, prefix, False):
            fact.deterministic = True
            fact.notes.append("home/temp prefix: " + prefix)
            return fact

    for safe in exempt_locations:
        if _path_is_under(posix, safe, False):
            fact.is_system = True
            fact.notes.append("documented non-identifying location")
            return fact

    for name in sorted(markers["names"], key=len, reverse=True):
        if len(name) < 3:
            continue
        if any(_path_is_under(posix, base + name, False)
               for base in ("/home/", "/Users/", "/Volumes/", "/root/")) \
                or _path_is_under(posix, "C:/Users/" + name, False):
            fact.deterministic = True
            fact.notes.append("interpolated home marker")
            return fact

    if windows:
        # UNC/device roots carry a host or device name — check on the raw
        # normalized string (preserves `\\` prefix).
        if normalized.startswith("\\\\"):
            fact.deterministic = True
            fact.notes.append("UNC/device root")
            return fact
        return fact                          # generic drive path: contextual

    # Single-component paths ("/project", "/tmp") name a *convention*, not a
    # host: `/home`, `/Users` and `/etc` occur in every document that talks
    # about paths. Design 3.2's ">= 2 components OR a known-root tie-in"
    # floor is enforced here, which is what keeps this repository's own prose
    # quiet.
    if posix.count("/") < 2:
        fact.notes.append("single-component path: no host identity")
        return fact
    for ci in _CI_ROOT_PREFIXES:
        ci_posix = _to_posix(ci)
        if _path_is_under(posix, ci_posix, False):
            fact.deterministic = True
            fact.notes.append("host-identifying root: " + ci)
            return fact
    for system_prefix in system:
        if _path_is_under(posix, system_prefix, False):
            fact.is_system = True
            return fact
    for hostname in markers["hostnames"]:
        if hostname and hostname in posix:
            fact.deterministic = True
            fact.notes.append("hostname in path")
            return fact
    return fact                              # generic absolute path


def detect_paths(text: str, workspace: Optional[str] = None,
                 channel: str = "") -> List[SpanSpec]:
    """Absolute-path spans in `text`, with boundary facts in `notes`.

    Fact layer only: ALLOW/SANITIZE/ASK is `policy.decide_spans`'s call.
    """
    markers = host_markers()
    spans: List[SpanSpec] = []
    occupied: List[Tuple[int, int]] = []

    def emit(fact: PathVerdict, notes: Iterable[str]):
        if "UNC/device root" in fact.notes:
            rule_id = RULE_DEVICE_PATH
        elif fact.inside_workspace:
            # Not a host identifier at all (design 3.1): the exemption is a
            # rule id of its own so the policy layer never has to infer it
            # from the notes list.
            rule_id = RULE_WORKSPACE_PATH
        elif fact.is_system:
            rule_id = RULE_SYSTEM_PATH
        elif fact.deterministic:
            rule_id = RULE_HOST_ABSOLUTE_PATH
        else:
            rule_id = RULE_GENERIC_ABSOLUTE_PATH
        if "UNC/device root" in fact.notes and rule_id == RULE_DEVICE_PATH:
            confidence = CONFIDENCE_DETERMINISTIC
        else:
            confidence = (CONFIDENCE_DETERMINISTIC
                          if (fact.deterministic or fact.is_system)
                          else CONFIDENCE_CONTEXTUAL)
        span = SpanSpec(
            raw=fact.raw, start=fact.start, end=fact.end, rule_id=rule_id,
            family=FAMILY_PATH, confidence=confidence,
            context=_note_context(text, fact.start), channel=channel,
            notes=list(notes) + list(fact.notes))
        spans.append(span)
        occupied.append((span.start, span.end))

    def overlaps(start: int, end: int) -> bool:
        return any(start < e and end > s for s, e in occupied)

    def consider(match: "re.Match[str]", windows: bool):
        raw = _clean_candidate(text, match)
        if raw is None:
            return
        start, end = match.start(), match.start() + len(raw)
        if overlaps(start, end):
            return
        fact = classify_path_candidate(raw, workspace, markers, windows)
        if fact is None:
            return
        fact.raw, fact.start, fact.end = raw, start, end
        emit(fact, [])

    for match in _WINDOWS_ENV_RE.finditer(text):
        if overlaps(match.start(), match.end()):
            continue
        emit(PathVerdict(raw=match.group(0), start=match.start(),
                         end=match.end(), path=match.group(0),
                         windows=True, absolute=False,
                         deterministic=False,
                         notes=["unresolvable Windows environment reference"]),
             ["interpolation: the path cannot be resolved"])
    # Windows paths first: a backslash spelling is unambiguous, so it must
    # not be nibbled at by the POSIX pattern.
    for match in _WINDOWS_PATH_RE.finditer(text):
        consider(match, True)
    for match in _POSIX_PATH_RE.finditer(text):
        consider(match, False)
    return merge_spans(spans)


# ------------------------------------------------------------ egress channels
#
# Design 4.1: a channel is defined by exactly two facts, and the decision
# table keys off both. `rewritable` is what makes SANITIZE meaningful (the
# guard can hand the caller a plan); `persistence` is what justifies BLOCK
# (the emission cannot be taken back). A channel that is neither can only
# ASK - it cannot un-print.

CHANNEL_UNREACHABLE = "unreachable"


@dataclass(frozen=True)
class Channel:
    name: str
    rewritable: bool
    persistence: str            # "none" | "local" | "workspace" | "remote" | "public"
    default_decision: str       # SANITIZE | ASK | BLOCK
    reachability: str = "hook"  # "hook" | "unreachable"


# The taxonomy is closed on purpose: an unknown channel name is a
# configuration defect (fail closed, design 6.3), never an implicit
# "no channel, no risk".
CHANNELS: Dict[str, Channel] = {
    "llm-request": Channel("llm-request", True, "remote", "SANITIZE"),
    "file-write": Channel("file-write", True, "workspace", "SANITIZE"),
    "forge-comment": Channel("forge-comment", True, "public", "SANITIZE"),
    "issue-body": Channel("issue-body", True, "public", "SANITIZE"),
    "pr-description": Channel("pr-description", True, "public", "SANITIZE"),
    "git-commit-message": Channel("git-commit-message", True, "remote",
                                  "BLOCK"),
    "git-push-payload": Channel("git-push-payload", False, "remote", "BLOCK"),
    "shell-stdout": Channel("shell-stdout", False, "local", "ASK"),
    "shell-file-redirect": Channel("shell-file-redirect", True, "local",
                                   "ASK"),
    "archive-upload": Channel("archive-upload", True, "remote", "ASK"),
    "process-argv": Channel("process-argv", True, "local", "ASK"),
}

# Explicitly out of reach (design 4.4 "unreachable channel"): the guard
# never claims coverage here, and never implies a verdict it cannot see.
UNREACHABLE_CHANNELS = frozenset({"hosted-llm-no-proxy", "agent-tool-call",
                                  "program-internal-output", "human-clipboard"})

# A channel with no persistence at all can only ASK when it cannot rewrite:
# there is nothing to refuse *for* once the bytes are already visible.
_NON_PERSISTENT = frozenset({"none", "local"})


def get_channel(name: str) -> Optional[Channel]:
    """Look up a channel by name; None means the configuration is invalid."""
    if not name:
        return None
    return CHANNELS.get(name.strip().lower())


# --------------------------------------------------------------- scan entry

# Explicit size cap (design 6.3): a payload we did not scan must never be
# reported as clean, so exceeding the cap is a BLOCK-class fact, not a
# silent truncation.
DEFAULT_MAX_SCAN_BYTES = 4 * 1024 * 1024


@dataclass
class ScanResult:
    """The outcome of scanning one payload - facts plus scanner status."""

    spans: List[SpanSpec] = field(default_factory=list)
    scanned: bool = True
    error: str = ""
    truncated: bool = False
    size_bytes: int = 0
    exempted: int = 0
    exemption_info: Dict[str, Any] = field(default_factory=dict)

    @property
    def found(self) -> bool:
        return bool(self.spans)


def max_scan_bytes() -> int:
    raw = os.environ.get("AGENT_GUARD_EXFIL_MAX_BYTES")
    if raw:
        try:
            value = int(raw)
            if value > 0:
                return value
        except ValueError:
            pass
    return DEFAULT_MAX_SCAN_BYTES


# The secret-store *names* are data (SECRET_STORE_NAMES/SUFFIXES); the
# regexes below only find them in a read position.
_STORE_NAME_ALT = "|".join(re.escape(n) for n in
                           sorted(SECRET_STORE_NAMES, key=len, reverse=True))
_STORE_SUFFIX_ALT = "|".join(re.escape(s.lstrip(".")) for s in
                             sorted(SECRET_STORE_SUFFIXES, key=len,
                                    reverse=True))
# A secret-store file: a path ending in a known name/suffix, optionally
# quoted, in any position (reading it is the whole point of the payload).
# `process.env` / `config.credentials` are property accesses, not files:
# require the name to start a word (not follow a `.`) and to be either
# path-qualified, quoted, or standalone. Backtick/quote/space/`=`/`/` are
# the positions a file name really appears in.
SECRET_STORE_RE = re.compile(
    r"(?<![.\w])(?:[\w@~$%{}-]*[\\/])?(?:" + _STORE_NAME_ALT + r")"
    r"(?![\w-]|\.[A-Za-z])"
    r"|(?<![.\w])[\w@~$%{}-]*(?:[\\/][\w.-]+)*\.(?:"
    + _STORE_SUFFIX_ALT + r")(?![\w])")


# A variable *name* that marks its value as secret-bearing. Classification
# is by name only; the guard never reads the value (design 2.4 item 1).
SECRET_NAME_RE = re.compile(
    r"\$env:([A-Za-z_][A-Za-z0-9_]*)"
    r"|\$\{([A-Za-z_][A-Za-z0-9_]*)(?:\}|(?=[:/#%+?=\-]))"
    r"|\$([A-Za-z_][A-Za-z0-9_]*)"
    r"|%([A-Za-z_][A-Za-z0-9_]*)%", re.IGNORECASE)
_SECRET_NAME_COMPONENTS = frozenset({
    "KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CRED", "AUTH", "APIKEY",
    "CREDENTIAL", "CREDENTIALS",
})
_SECRET_NAME_METADATA = frozenset({"COUNT", "LENGTH", "ENABLED"})


def _secret_bearing_name(name: str, *, explicit_environment: bool = False) -> bool:
    # Preserve acronym/camelCase components: ApiKey, APIKey, accessToken.
    separated = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    separated = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", separated)
    components = [part.upper() for part in separated.split("_") if part]
    # A lone lower/title-case `key` is also a common dictionary/schema key
    # in template expressions. Do not infer a credential from that word
    # alone. KEY, qualified apiKey and explicit env syntax stay protected.
    # This is a name ambiguity, not a JavaScript/file-extension exemption.
    if (components == ["KEY"] and name.strip("_") != "KEY"
            and not explicit_environment):
        return False
    return (bool(components) and components[-1] not in _SECRET_NAME_METADATA
            and any(part in _SECRET_NAME_COMPONENTS for part in components))


def _secret_source_reference(text: str, *, bash=False, include_store_paths=True) -> Optional[Tuple[str, int, int, str]]:
    """(matched expression, start, end, reason) for a secret source, or None.

    Value-free by construction: the match is an environment-variable *name*,
    a whole-environment expansion, or a secret-store *file name*. Nothing
    here resolves a value; that invariant is what keeps the guard's own
    output from becoming the leak (design 2.4, "anti-loop rule").
    """
    match = (_BASH_ENV_DUMP_RE if bash else ENV_DUMP_RE).search(text)
    if match:
        # Trim the pipe/whitespace the expansion regex uses to anchor the
        # read position: the fact is the command, not the separator.
        raw = match.group(0).rstrip(" |\t")
        return raw, match.start(), match.start() + len(raw), \
            "environment expansion"
    if include_store_paths:
        for match in SECRET_STORE_ENV_RE.finditer(text):
            if not bash or match.group(0) not in ENV_TEMPLATE_NAMES:
                return match.group(0), match.start(), match.end(), \
                    "secret store: " + match.group(0)
        match = SECRET_STORE_RE.search(text)
        if match:
            return match.group(0), match.start(), match.end(), \
                "secret store: " + match.group(0)
    for match in SECRET_NAME_RE.finditer(text):
        name = next(g for g in match.groups() if g)
        explicit_environment = match.group(1) is not None or match.group(4) is not None
        if _secret_bearing_name(name, explicit_environment=explicit_environment):
            return match.group(0), match.start(), match.end(), \
                "secret-bearing name: " + name
    return None


def secret_source_span(text, channel, *, bash=False, include_store_paths=True):
    """Return the first value-free secret source fact in a payload."""
    reference = _secret_source_reference(text, bash=bash, include_store_paths=include_store_paths)
    if reference is None:
        return None
    raw, start, end, reason = reference
    return SpanSpec(raw=raw, start=start, end=end, rule_id=RULE_SECRET_SOURCE, family=FAMILY_SECRET,
                    confidence=CONFIDENCE_DETERMINISTIC, context="env-var", channel=channel,
                    source_dump=True, notes=["value-free reference detection", reason])


# ------------------------------------------------- repo-local exemptions
#
# Design 5.2: `.agent-guard/exfil-allow.toml` (or `.agent-guardignore`)
# disables rules and exempts paths. Value exemptions are **by hash only** -
# a human who needs a specific literal exempted provides `sha256:...`, never
# the value, because writing the value into a tracked file is itself the bug
# this feature exists to prevent.

DEFAULT_ALLOWFILE = ".agent-guard/exfil-allow.toml"
DEFAULT_IGNOREFILE = ".agent-guardignore"
EXEMPTION_DIAGNOSTIC_LIMIT = 32
EXEMPTION_DIAGNOSTICS = {
    "EXEMPTION_IGNORED_LINE": "Line has no assignment or preceding value key; gitignore syntax is unsupported.",
    "EXEMPTION_UNKNOWN_KEY": "Unknown top-level key; its values are ignored.",
    "EXEMPTION_UNKNOWN_SECTION": "Unknown section; recognized legacy keys may still apply.",
    "EXEMPTION_LEGACY_SECTION_KEY": "Nonstandard key uses the legacy section fallback; its values still apply.",
    "EXEMPTION_MALFORMED_SECTION": "Malformed section header uses legacy parsing; inspect the configuration.",
    "EXEMPTION_INVALID_HASH": "Value is not a lowercase sha256 digest of 64 hexadecimal characters; it cannot match.",
    "EXEMPTION_UNSUPPORTED_NEGATION": "Leading ! is a literal pattern character, not gitignore negation.",
    "EXEMPTION_READ_FAILED": "Configuration could not be read; the next candidate is tried.",
    "EXEMPTION_ENCODING_INVALID": "Configuration is not UTF-8; the next candidate is tried.",
    "EXEMPTION_SHADOWED": "This file is not loaded because the preferred file was readable, even if empty or malformed.",
}
_EXEMPTION_RULE_KEYS = frozenset({"rule", "rules", "disable"})
_EXEMPTION_PATH_KEYS = frozenset({"path", "paths", "ignore"})
_EXEMPTION_HASH_KEYS = frozenset({"sha256", "hash", "hashes"})


def _safe_exemption_source(source: str) -> Optional[str]:
    """Only fixed workspace-relative filenames may leave the process."""
    if source in (DEFAULT_ALLOWFILE, DEFAULT_IGNOREFILE):
        return source
    return "<provided>" if source else None


@dataclass
class Exemption:
    """Parsed exemption config. Rule/path globs plus sha256 value hashes."""

    rules: List[str] = field(default_factory=list)
    paths: List[str] = field(default_factory=list)
    hashes: List[str] = field(default_factory=list)
    source: str = ""
    diagnostics: List[Dict[str, Any]] = field(default_factory=list)
    diagnostics_omitted: int = 0

    def diagnose(self, code: str, source: str, line: int = 0) -> None:
        """Bounded, static diagnostics; never copy keys, values or exceptions."""
        if code not in EXEMPTION_DIAGNOSTICS:
            return
        if len(self.diagnostics) >= EXEMPTION_DIAGNOSTIC_LIMIT:
            self.diagnostics_omitted += 1
            return
        row = {"code": code, "source": _safe_exemption_source(source)}
        if type(line) is int and line > 0:
            row["line"] = line
        self.diagnostics.append(row)

    def safe(self) -> Dict[str, Any]:
        """Provenance and counts, never the exemption patterns or hashes."""
        rows = []
        for row in self.diagnostics[:EXEMPTION_DIAGNOSTIC_LIMIT]:
            code = row.get("code")
            if code not in EXEMPTION_DIAGNOSTICS:
                continue
            safe = {"code": code,
                    "source": _safe_exemption_source(row.get("source", ""))}
            if type(row.get("line")) is int and row["line"] > 0:
                safe["line"] = row["line"]
            rows.append(safe)
        return {
            "source": _safe_exemption_source(self.source),
            "active": self.active,
            "counts": {"rules": len(self.rules), "paths": len(self.paths),
                       "hashes": len(self.hashes)},
            "diagnostics": rows,
            "diagnostics_omitted": self.diagnostics_omitted,
        }

    @property
    def active(self) -> bool:
        return bool(self.rules or self.paths or self.hashes)

    def rule_disabled(self, rule_id: str) -> bool:
        return any(fnmatch.fnmatch(rule_id, pat) for pat in self.rules)

    def path_exempt(self, path: str, root: Optional[str] = None) -> bool:
        if not self.paths or not path:
            return False
        candidates = [path]
        if root:
            try:
                rel = os.path.relpath(os.path.abspath(path),
                                      os.path.abspath(root))
                candidates.append(rel)
                # A `docs/*.md` pattern means "under the workspace docs
                # directory", regardless of how the caller spelled the path.
                parts = rel.split(os.sep)
                candidates.extend(os.sep.join(parts[i:])
                                  for i in range(1, len(parts)))
            except ValueError:                # different drives (Windows)
                pass
            except ValueError:                # different drives (Windows)
                pass
        return any(fnmatch.fnmatch(cand, pat)
                   for cand in candidates for pat in self.paths)

    def value_exempt(self, raw: str) -> bool:
        """Hash comparison only - the guard never stores the value."""
        if not self.hashes or not raw:
            return False
        digest = "sha256:" + hashlib.sha256(
            raw.encode("utf-8", errors="surrogatepass")).hexdigest()
        return digest in self.hashes


def _parse_allowfile(text: str, source: str) -> Exemption:
    """Minimal `key = value` parser - no third-party TOML dependency.

    Continuation lines are supported: a key whose value starts on its own
    line keeps consuming the indented lines that follow, which is how the
    lists in `.agent-guard/exfil-allow.toml` stay readable. Preserve legacy
    matching while diagnosing ignored or nonstandard constructs. Warnings
    are not whole-file rejection; recognized entries can still apply.
    """
    exemption = Exemption(source=source)
    section = ""
    key = ""
    values: List[Tuple[str, int]] = []
    key_line = 0

    def flush():
        if not key:
            return
        if key in _EXEMPTION_RULE_KEYS or section == "rules":
            target = exemption.rules
        elif key in _EXEMPTION_PATH_KEYS or section == "paths":
            target = exemption.paths
        elif key in _EXEMPTION_HASH_KEYS or section == "values":
            target = exemption.hashes
        else:
            exemption.diagnose("EXEMPTION_UNKNOWN_KEY", source, key_line)
            return
        if key not in (_EXEMPTION_RULE_KEYS | _EXEMPTION_PATH_KEYS |
                       _EXEMPTION_HASH_KEYS):
            exemption.diagnose("EXEMPTION_LEGACY_SECTION_KEY", source, key_line)
        for value, line in values:
            if not value.strip("'\""):
                continue
            item = value.strip().strip("'\"")
            target.append(item)
            if target is exemption.hashes:
                if not re.fullmatch(r"sha256:[0-9a-f]{64}", item):
                    exemption.diagnose("EXEMPTION_INVALID_HASH", source, line)
            elif item.startswith("!"):
                exemption.diagnose("EXEMPTION_UNSUPPORTED_NEGATION", source, line)

    def push(raw: str, line: int):
        """Split one value chunk on commas/whitespace, ignoring [] and ''."""
        chunk = raw.strip().strip("[]")
        for item in re.split(r"[,\s]+", chunk):
            item = item.strip().strip("'\"")
            if item:
                values.append((item, line))

    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if line.lstrip().startswith("["):
            flush()
            key, values = "", []
            section = line.strip().strip("[]").strip().lower()
            if not re.fullmatch(r"\[[^\[\]]+\]", line.strip()):
                exemption.diagnose("EXEMPTION_MALFORMED_SECTION", source, line_number)
            if section not in ("rules", "paths", "values"):
                exemption.diagnose("EXEMPTION_UNKNOWN_SECTION", source, line_number)
            continue
        if "=" in line:
            flush()
            values = []
            key, _, value = line.partition("=")
            key = key.strip().lower()
            key_line = line_number
            if not key:
                exemption.diagnose("EXEMPTION_UNKNOWN_KEY", source, line_number)
            push(value, line_number)
            continue
        if key:
            push(line, line_number)
        else:
            exemption.diagnose("EXEMPTION_IGNORED_LINE", source, line_number)
    flush()
    return exemption


def load_exemption(workspace: Optional[str], *, read_text=None) -> Exemption:
    """Read the repo-local exemption file; absent means "no exemptions"."""
    result = Exemption()
    if not workspace:
        return result
    for relative in (DEFAULT_ALLOWFILE, DEFAULT_IGNOREFILE):
        path = os.path.join(workspace, relative)
        try:
            if read_text is None:
                with open(path, "r", encoding="utf-8") as fh:
                    text = fh.read()
            else:
                text = read_text(relative)
            parsed = _parse_allowfile(text, relative)
        except FileNotFoundError:
            continue
        except UnicodeDecodeError:
            result.diagnose("EXEMPTION_ENCODING_INVALID", relative)
            continue
        except OSError:
            result.diagnose("EXEMPTION_READ_FAILED", relative)
            continue
        result.rules, result.paths, result.hashes = parsed.rules, parsed.paths, parsed.hashes
        result.source = parsed.source
        for row in parsed.diagnostics:
            result.diagnose(row["code"], relative, row.get("line", 0))
        result.diagnostics_omitted += parsed.diagnostics_omitted
        if relative == DEFAULT_ALLOWFILE and os.path.lexists(
                os.path.join(workspace, DEFAULT_IGNOREFILE)):
            result.diagnose("EXEMPTION_SHADOWED", DEFAULT_IGNOREFILE)
        return result
    return result


def apply_exemption(spans: List[SpanSpec], exemption: Exemption,
                    path: Optional[str] = None,
                    root: Optional[str] = None) -> List[SpanSpec]:
    """Drop spans a reviewed exemption covers. Never restores a value."""
    if not exemption.active:
        return spans
    if exemption.path_exempt(path, root):
        return []
    return [s for s in spans
            if not exemption.rule_disabled(s.rule_id)
            and not exemption.value_exempt(s.raw)]


def scan_text(text: str, channel: str = "",
              workspace: Optional[str] = None,
              max_bytes: Optional[int] = None,
              path: Optional[str] = None,
              exemption: Optional[Exemption] = None) -> ScanResult:
    """Detect every secret/path span in `text` (facts only).

    Never returns a partial result as clean: an over-cap payload or an
    internal scanner error is reported via
    `scanned=False`, which `policy.decide_spans` turns into
    BLOCK_OUTPUT_UNSCANNABLE (design 6.3, fail closed).
    """
    limit = max_bytes if max_bytes is not None else max_scan_bytes()
    size = len(text.encode("utf-8", errors="replace"))
    result = ScanResult(size_bytes=size)
    if size > limit:
        result.scanned = False
        result.truncated = True
        result.error = f"payload exceeds scan cap ({size} > {limit} bytes)"
        return result
    # Note on non-ASCII payloads: they are scanned normally. The vendor
    # patterns are ASCII-anchored and `re` matches on characters, so a
    # non-ASCII byte cannot smuggle a match past them, and the placeholder
    # allowlist only ever needs to see the (ASCII) token it was handed.
    # Refusing every document that contains an em-dash or CJK prose would
    # make the guard unusable without buying any detection.

    workspace = workspace or os.environ.get("AGENT_GUARD_WORKSPACE")
    try:
        spans = detect_secrets(text, channel=channel)
        spans.extend(detect_paths(text, workspace=workspace, channel=channel))
    except Exception as exc:                # pragma: no cover - defensive
        result.scanned = False
        result.error = f"scanner error: {type(exc).__name__}"
        return result

    spans = merge_spans(spans)
    source = secret_source_span(text, channel)
    if source is not None:
        # The dump makes the whole payload unattributable. It gets a span of
        # its own - not just a flag on an existing one - because the
        # dangerous case (`echo "$KEY"`) frequently matches no pattern at
        # all, and "no match" must not be reported as "clean" when the
        # guard never saw the content. The span records the *reference
        # expression*, which is value-free by construction (design 2.4).
        spans.append(source)
    if exemption is None:
        exemption = load_exemption(workspace)
    result.exemption_info = exemption.safe()
    # A source-reference fact must survive even inside a redaction span:
    # masking a literal URI password is not permission to resolve an env
    # secret embedded there. The aggregate BLOCK prevents plan application.
    spans.sort(key=lambda span: (span.start, -span.length))
    result.spans = apply_exemption(spans, exemption, path, workspace)
    result.exempted = len(spans) - len(result.spans)
    return result


# --------------------------------------------------- shared redaction plan

REDACTED = "<REDACTED>"
PATH_PLACEHOLDER = "<PATH>"
_REDACTION_PREFIX_RE = re.compile(
    r"^(sk-|rk_|ghp_|gho_|ghu_|ghs_|ghr_|github_pat_|"
    r"glpat-|xox[baprs]-|pypi-|AKIA|ASIA|sk_live_|rk_live_|"
    + MODERN_VENDOR_PREFIX_PATTERN + r")")


def placeholder_for(span: SpanSpec) -> str:
    """Replacement text for one span; derived from shape, never content."""
    if span.family != FAMILY_SECRET:
        return PATH_PLACEHOLDER
    if span.rule_id in (RULE_PRIVATE_KEY_BLOCK,
                        RULE_CONNECTION_PASSWORD, RULE_HTTP_BASIC_AUTH):
        return REDACTED
    match = _REDACTION_PREFIX_RE.match(span.raw)
    if match:
        return match.group(1) + REDACTED
    return REDACTED


def plan_for_span(span: SpanSpec, channel: Any = None) -> Dict[str, Any]:
    """One plan entry: offsets + placeholder. Never the matched bytes."""
    return {
        "start": span.start,
        "end": span.end,
        "rule_id": span.rule_id,
        "family": span.family,
        "confidence": span.confidence,
        "placeholder": placeholder_for(span),
    }


def _plan_pieces(text: str, plan: Any) -> List[Tuple[int, int, str]]:
    """Validate every range before constructing any output."""
    if not isinstance(plan, list):
        raise ValueError("redaction plan must be a list")
    pieces: List[Tuple[int, int, str]] = []
    for entry in plan:
        if not isinstance(entry, dict) or not {"start", "end", "placeholder"}.issubset(entry):
            raise ValueError("invalid redaction plan entry")
        start, end = entry["start"], entry["end"]
        if type(start) is not int or type(end) is not int:
            raise ValueError("redaction offsets must be integers")
        if start < 0 or end > len(text) or start >= end:
            raise ValueError("redaction plan has an out-of-range span")
        if not isinstance(entry["placeholder"], str) or not entry["placeholder"]:
            raise ValueError("invalid redaction placeholder")
        pieces.append((start, end, entry["placeholder"]))
    pieces.sort()
    for previous, current in zip(pieces, pieces[1:]):
        if current[0] < previous[1]:
            raise ValueError("redaction plan has overlapping spans")
    return pieces


def apply_plan(text: str, plan: Any, verify: bool = True) -> str:
    """Use validated original coordinates to assemble output in one pass."""
    pieces = _plan_pieces(text, plan)
    chunks: List[str] = []
    cursor = 0
    for start, end, placeholder in pieces:
        chunks.extend((text[cursor:start], placeholder))
        cursor = end
    chunks.append(text[cursor:])
    return "".join(chunks)
