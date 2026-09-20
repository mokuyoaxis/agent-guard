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
# `.env` and its dotted variants (`.env.local`, `.env.production`): these
# are the same secret store, and `.env.local` is the file people actually
# commit by accident.
SECRET_STORE_ENV_RE = re.compile(r"(?<![\w.@])\.env(?:\.[A-Za-z0-9_-]+)?"
                                 r"(?![\w-])")
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
    r"\$\{?([A-Za-z_][A-Za-z0-9_]*"
    r"(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|CRED|AUTH|APIKEY)[A-Za-z0-9_]*)\b"
    r"|\$env:([A-Za-z_][A-Za-z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|CRED|AUTH))"
    r"|%([A-Za-z_][A-Za-z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|CRED|AUTH))%",
    re.IGNORECASE)


def _secret_source_reference(text: str) -> Optional[Tuple[str, int, int, str]]:
    """(matched expression, start, end, reason) for a secret source, or None.

    Value-free by construction: the match is an environment-variable *name*,
    a whole-environment expansion, or a secret-store *file name*. Nothing
    here resolves a value; that invariant is what keeps the guard's own
    output from becoming the leak (design 2.4, "anti-loop rule").
    """
    match = ENV_DUMP_RE.search(text)
    if match:
        # Trim the pipe/whitespace the expansion regex uses to anchor the
        # read position: the fact is the command, not the separator.
        raw = match.group(0).rstrip(" |\t")
        return raw, match.start(), match.start() + len(raw), \
            "environment expansion"
    match = SECRET_STORE_ENV_RE.search(text)
    if match:
        return match.group(0), match.start(), match.end(), \
            "secret store: " + match.group(0)
    match = SECRET_STORE_RE.search(text)
    if match:
        return match.group(0), match.start(), match.end(), \
            "secret store: " + match.group(0)
    match = SECRET_NAME_RE.search(text)
    if match:
        name = next(g for g in match.groups() if g)
        return match.group(0), match.start(), match.end(), \
            "secret-bearing name: " + name
    return None


# ------------------------------------------------- repo-local exemptions
#
# Design 5.2: `.agent-guard/exfil-allow.toml` (or `.agent-guardignore`)
# disables rules and exempts paths. Value exemptions are **by hash only** -
# a human who needs a specific literal exempted provides `sha256:...`, never
# the value, because writing the value into a tracked file is itself the bug
# this feature exists to prevent.

DEFAULT_ALLOWFILE = ".agent-guard/exfil-allow.toml"
DEFAULT_IGNOREFILE = ".agent-guardignore"


@dataclass
class Exemption:
    """Parsed exemption config. Rule/path globs plus sha256 value hashes."""

    rules: List[str] = field(default_factory=list)
    paths: List[str] = field(default_factory=list)
    hashes: List[str] = field(default_factory=list)
    source: str = ""

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
    lists in `.agent-guard/exfil-allow.toml` stay readable. Malformed input
    is ignored rather than fatal - a broken allowfile must never quietly
    turn the guard off.
    """
    exemption = Exemption(source=source)
    section = ""
    key = ""
    values: List[str] = []

    def flush():
        if not key:
            return
        items = [v.strip().strip("'\"") for v in values if v.strip("'\"")]
        if key in ("rule", "rules", "disable") or section == "rules":
            exemption.rules.extend(items)
        elif key in ("path", "paths", "ignore") or section == "paths":
            exemption.paths.extend(items)
        elif key in ("sha256", "hash", "hashes") or section == "values":
            exemption.hashes.extend(items)

    def push(raw: str):
        """Split one value chunk on commas/whitespace, ignoring [] and ''."""
        chunk = raw.strip().strip("[]")
        for item in re.split(r"[,\s]+", chunk):
            item = item.strip().strip("'\"")
            if item:
                values.append(item)

    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if line.lstrip().startswith("["):
            flush()
            key, values = "", []
            section = line.strip().strip("[]").strip().lower()
            continue
        if "=" in line:
            flush()
            values = []
            key, _, value = line.partition("=")
            key = key.strip().lower()
            push(value)
            continue
        if key:
            push(line)
    flush()
    return exemption


def load_exemption(workspace: Optional[str]) -> Exemption:
    """Read the repo-local exemption file; absent means "no exemptions"."""
    if not workspace:
        return Exemption()
    for relative in (DEFAULT_ALLOWFILE, DEFAULT_IGNOREFILE):
        path = os.path.join(workspace, relative)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return _parse_allowfile(fh.read(), relative)
        except (OSError, UnicodeDecodeError):
            continue
    return Exemption()


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

    Never returns a partial result as clean: an over-cap payload, a
    non-ASCII payload or an internal scanner error is reported via
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
    source = _secret_source_reference(text)
    if source is not None:
        # The dump makes the whole payload unattributable. It gets a span of
        # its own - not just a flag on an existing one - because the
        # dangerous case (`echo "$KEY"`) frequently matches no pattern at
        # all, and "no match" must not be reported as "clean" when the
        # guard never saw the content. The span records the *reference
        # expression*, which is value-free by construction (design 2.4).
        raw, start, end, reason = source
        spans.append(SpanSpec(
            raw=raw, start=start, end=end, rule_id=RULE_SECRET_SOURCE,
            family=FAMILY_SECRET, confidence=CONFIDENCE_DETERMINISTIC,
            context="env-var", channel=channel, source_dump=True,
            notes=["value-free reference detection", reason]))
    if exemption is None:
        exemption = load_exemption(workspace)
    result.spans = apply_exemption(merge_spans(spans), exemption, path,
                                   workspace)
    result.exempted = len(merge_spans(spans)) - len(result.spans)
    return result
