"""Minimal local config scalar editor with policy allowlist and byte-span preservation.

This is a cooperative local-operator CLI engine, not an OS access-control
boundary. All reads and writes traverse POSIX directory descriptors with
O_DIRECTORY | O_NOFOLLOW, verify regular single-link files, preserve every byte
outside the target scalar span, and re-parse both old and new files before
atomic replacement.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import time
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Set, Tuple
import unicodedata

from . import AUDIT_NAME, TRASH_DIRNAME, audit, carrier_unicode, delete_config, exfil_audit, redaction, safe_read, safe_view


SCHEMA = "config_edit/v1"
POLICY_DIR = ".agent-guard"
POLICY_FILE = "config-edit-policy.json"
POLICY_RELPATH = f"{POLICY_DIR}/{POLICY_FILE}"
LOCK_FILE = "config-edit.lock"

MAX_POLICY_BYTES = delete_config.MAX_CONFIG_BYTES
MAX_POLICY_NODES = 65536
MAX_FILE_BYTES = safe_view.MAX_VIEW_BYTES
MAX_DEPTH = safe_view.MAX_VIEW_DEPTH
MAX_NODES = safe_view.MAX_VIEW_NODES
MAX_PREVIEW_LABEL_LEN = 512
LOCK_TIMEOUT_SECONDS = 10.0
LOCK_POLL_SECONDS = 0.02

FORMATS = ("auto", "json", "dotenv")
POLICY_FORMATS = ("json", "dotenv")
VALUE_TYPES = ("string", "number", "boolean", "null")
CONTROL_DIRS = safe_read.CONTROL_DIRS

_JSON_NUMBER_RE = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z")
_JSON_NUMBER_BYTES_RE = re.compile(rb"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")
_JSON_WS_BYTES = frozenset(b" \t\r\n")
_JSON_HEX_BYTES = frozenset(b"0123456789abcdefABCDEF")

_DOTENV_PREFIX_RE = re.compile(
    rb"^[ \t]*(?P<export>export [ \t]*)?(?P<key>[A-Za-z_][A-Za-z0-9_]*)[ \t]*\Z"
)
_DOTENV_BARE_BYTES_RE = re.compile(rb"[-A-Za-z0-9_./:@%+=,]+\Z")
_DOTENV_BARE_TEXT_RE = re.compile(r"[-A-Za-z0-9_./:@%+=,]+\Z")
_DOTENV_UNSUPPORTED_KEYS = frozenset({"__proto__"})

_NON_ALNUM_SPLIT_RE = re.compile(r"[^A-Za-z0-9]+")
_CAMEL_ACRONYM_RE = re.compile(r"([A-Z]+)([A-Z][a-z])")
_CAMEL_BOUNDARY_RE = re.compile(r"([a-z0-9])([A-Z])")
_ALPHA_DIGIT_RE = re.compile(r"([A-Za-z])([0-9])")
_DIGIT_ALPHA_RE = re.compile(r"([0-9])([A-Za-z])")

_SENSITIVE_SINGLE_TOKENS = frozenset({
    "secret", "token", "password", "passwd", "pwd", "key",
    "apikey", "auth", "credential", "private", "cert",
    "cookie", "session", "jwt", "bearer", "signing",
    "dsn", "databaseurl", "connstr", "connectionstring",
})
_SENSITIVE_TOKEN_SEQUENCES = frozenset({
    ("database", "url"),
    ("connection", "string"),
    ("api", "key"),
    ("conn", "str"),
})

_PLACEHOLDER_SUBSTRINGS = (
    "<redacted>",
    "[redacted]",
    "<path>",
)
_PLACEHOLDER_RUNS = (
    "*" * 4,
    "\u2022" * 4,
)

MESSAGES: Dict[str, str] = {
    "PREVIEW_OK": "preview verified; no files were modified",
    "APPLIED": "configuration updated and persisted",
    "DIR_FSYNC_UNCONFIRMED": "configuration replaced, but parent directory fsync failed",
    "INVALID_ARGUMENT": "invalid arguments",
    "INVALID_PATH": "use a workspace-relative path",
    "POLICY_MISSING": "config-edit policy is missing",
    "POLICY_INVALID": "config-edit policy is invalid or unreadable",
    "POLICY_SENSITIVE_KEY_FORBIDDEN": "sensitive key names cannot be registered in editable_keys",
    "FILE_NOT_ALLOWED": "target file is not registered in config-edit policy",
    "FORMAT_MISMATCH": "requested format does not match config-edit policy",
    "KEY_LOCKED": "target key is locked by config-edit policy",
    "KEY_NOT_ALLOWED": "target key is not registered in editable_keys",
    "VALUE_REJECTED_BY_SCAN": "new value rejected by secret or path scan",
    "VALUE_REJECTED_PLACEHOLDER": "new value contains a rejected redaction placeholder",
    "VALUE_INVALID": "new value is invalid or unsupported for the target format",
    "TARGET_NOT_FOUND": "target key was not found as an existing object member",
    "TARGET_NOT_SCALAR": "target key is not a scalar value",
    "UNSAFE_FILE": "target file cannot be opened safely",
    "FILE_TOO_LARGE": "target file exceeds the 256 KiB size limit",
    "INVALID_ENCODING": "target file is not valid UTF-8",
    "INVALID_FORMAT": "target file violates strict format rules",
    "STRUCTURE_TOO_LARGE": "target file exceeds the maximum node count",
    "STRUCTURE_TOO_DEEP": "target file exceeds the maximum nesting depth",
    "UNSUPPORTED_FILE_METADATA": "target file has unsupported ownership, mode bits, or extended attributes",
    "UNSAFE_PREVIEW_LABEL": "preview label cannot be displayed safely",
    "TTY_REQUIRED": "interactive TTY on stdin and stderr is required for --apply",
    "APPROVAL_CANCELLED": "confirmation did not match APPLY; no changes were made",
    "UNSUPPORTED_PLATFORM": "safe descriptor-relative config editing is unavailable on this platform",
    "POLICY_CHANGED": "config-edit policy changed after preview",
    "FILE_CHANGED": "target file or directory changed after preview",
    "LOCK_TIMEOUT": "could not acquire cooperative config-edit lock within wait budget",
    "METADATA_RESTORE_FAILED": "could not restore or verify file ownership and permissions",
    "VERIFICATION_FAILED": "pre-write re-parse verification failed",
    "IO_ERROR": "file operation failed",
    "INTERNAL_ERROR": "config edit failed",
}

REFUSAL_CODES = frozenset({
    "INVALID_ARGUMENT",
    "INVALID_PATH",
    "POLICY_MISSING",
    "POLICY_INVALID",
    "POLICY_SENSITIVE_KEY_FORBIDDEN",
    "FILE_NOT_ALLOWED",
    "FORMAT_MISMATCH",
    "KEY_LOCKED",
    "KEY_NOT_ALLOWED",
    "VALUE_REJECTED_BY_SCAN",
    "VALUE_REJECTED_PLACEHOLDER",
    "VALUE_INVALID",
    "TARGET_NOT_FOUND",
    "TARGET_NOT_SCALAR",
    "UNSAFE_FILE",
    "FILE_TOO_LARGE",
    "INVALID_ENCODING",
    "INVALID_FORMAT",
    "STRUCTURE_TOO_LARGE",
    "STRUCTURE_TOO_DEEP",
    "UNSUPPORTED_FILE_METADATA",
    "UNSAFE_PREVIEW_LABEL",
    "TTY_REQUIRED",
    "APPROVAL_CANCELLED",
})

AUDIT_CODES = frozenset(MESSAGES)


class ConfigEditError(Exception):
    """Fixed, input-free diagnostic safe to show to callers."""

    def __init__(self, code: str, *, status: Optional[str] = None, exit_code: Optional[int] = None):
        if code not in MESSAGES:
            raise ValueError(f"unknown config_edit error code: {code}")
        if status is None:
            status = "refused" if code in REFUSAL_CODES else "error"
        if exit_code is None:
            exit_code = 2 if status == "refused" else 1
        super().__init__(MESSAGES[code])
        self.code = code
        self.status = status
        self.exit_code = exit_code
        self.message = MESSAGES[code]


def supports_safe_config_edit() -> bool:
    """Require Linux descriptor operations and ACL/extended attribute inspection."""
    return (
        sys.platform == "linux"
        and callable(getattr(os, "listxattr", None))
        and callable(getattr(os, "getxattr", None))
        and hasattr(os, "O_NOFOLLOW")
        and hasattr(os, "O_DIRECTORY")
        and {os.open, os.stat, os.unlink, os.rename} <= os.supports_dir_fd
        and os.stat in os.supports_follow_symlinks
    )


def make_receipt(
    *,
    status: str,
    code: str,
    replaced: bool = False,
    durability_confirmed: bool = False,
    file_label: Optional[str] = None,
    key_label: Optional[str] = None,
    format_name: Optional[str] = None,
    before_type: Optional[str] = None,
    before_state: Optional[str] = None,
    after_type: Optional[str] = None,
    warning: Optional[str] = None,
    audit_id: Optional[str] = None,
    audit_error: Optional[str] = None,
) -> Dict[str, Any]:
    receipt: Dict[str, Any] = {
        "schema": SCHEMA,
        "status": status,
        "code": code,
        "message": MESSAGES[code],
        "replaced": replaced,
        "durability_confirmed": durability_confirmed,
    }
    if file_label is not None:
        receipt["file"] = file_label
    if key_label is not None:
        receipt["key"] = key_label
    if format_name is not None:
        receipt["format"] = format_name
    if before_type is not None:
        receipt["before_type"] = before_type
    if before_state is not None:
        receipt["before_state"] = before_state
    if after_type is not None:
        receipt["after_type"] = after_type
    if warning is not None:
        receipt["warning"] = warning
    if audit_id is not None:
        receipt["audit_id"] = audit_id
    if audit_error is not None:
        receipt["audit_error"] = audit_error
    return receipt


def failure_receipt(exc: ConfigEditError, *, replaced: bool = False,
                    durability_confirmed: bool = False) -> Tuple[Dict[str, Any], int]:
    return (
        make_receipt(
            status=exc.status,
            code=exc.code,
            replaced=replaced,
            durability_confirmed=durability_confirmed,
        ),
        exc.exit_code,
    )


# --------------------------------------------------- key / pointer helpers

def _has_lone_surrogate(text: str) -> bool:
    return any(0xD800 <= ord(ch) <= 0xDFFF for ch in text)


def parse_json_pointer(pointer: str) -> Tuple[str, ...]:
    """Decode an RFC 6901 JSON Pointer requiring a leading '/'."""
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ValueError("JSON Pointer must start with '/'")
    if _has_lone_surrogate(pointer) or "\x00" in pointer:
        raise ValueError("JSON Pointer contains unsupported characters")
    raw_segments = pointer[1:].split("/")
    decoded: List[str] = []
    for seg in raw_segments:
        out: List[str] = []
        i = 0
        n = len(seg)
        while i < n:
            ch = seg[i]
            if ch == "~":
                if i + 1 >= n or seg[i + 1] not in ("0", "1"):
                    raise ValueError("invalid JSON Pointer escape")
                out.append("~" if seg[i + 1] == "0" else "/")
                i += 2
            else:
                out.append(ch)
                i += 1
        decoded.append("".join(out))
    return tuple(decoded)


def parse_dotenv_key(key: str) -> Tuple[str, ...]:
    if (not isinstance(key, str) or not safe_view._DOTENV_KEY.fullmatch(key)
            or key in _DOTENV_UNSUPPORTED_KEYS):
        raise ValueError("invalid dotenv key")
    return (key,)


def parse_key(format_name: str, key: str) -> Tuple[str, ...]:
    if format_name == "json":
        return parse_json_pointer(key)
    if format_name == "dotenv":
        return parse_dotenv_key(key)
    raise ValueError("unsupported format")


def _tokenize_segment(segment: str) -> List[str]:
    tokens: List[str] = []
    for chunk in _NON_ALNUM_SPLIT_RE.split(segment):
        if not chunk:
            continue
        split = _CAMEL_ACRONYM_RE.sub(r"\1 \2", chunk)
        split = _CAMEL_BOUNDARY_RE.sub(r"\1 \2", split)
        split = _ALPHA_DIGIT_RE.sub(r"\1 \2", split)
        split = _DIGIT_ALPHA_RE.sub(r"\1 \2", split)
        for piece in split.split():
            if piece:
                tokens.append(piece.lower())
    return tokens


def is_sensitive_segment(segment: str) -> bool:
    tokens = _tokenize_segment(segment)
    if not tokens:
        return False
    for token in tokens:
        if token in _SENSITIVE_SINGLE_TOKENS:
            return True
    for i in range(len(tokens) - 1):
        if (tokens[i], tokens[i + 1]) in _SENSITIVE_TOKEN_SEQUENCES:
            return True
    return False


def is_sensitive_key(segments: Sequence[str]) -> bool:
    return any(is_sensitive_segment(seg) for seg in segments)


# --------------------------------------------------- strict RFC 8259 JSON

@dataclass(frozen=True)
class JsonNode:
    kind: str  # "object" | "array" | "string" | "number" | "boolean" | "null"
    value: Any
    start: int
    end: int


class _JsonByteParser:
    """Bounded strict RFC 8259 parser operating directly on validated UTF-8 bytes."""

    def __init__(self, raw: bytes, *, max_depth: int, max_nodes: int, error_code: str = "INVALID_FORMAT"):
        self.raw = raw
        self.length = len(raw)
        self.pos = 0
        self.max_depth = max_depth
        self.remaining_nodes = max_nodes
        self.error_code = error_code

    def _fail(self, code: Optional[str] = None) -> None:
        raise ConfigEditError(code or self.error_code)

    def _skip_ws(self) -> None:
        raw = self.raw
        n = self.length
        p = self.pos
        while p < n and raw[p] in _JSON_WS_BYTES:
            p += 1
        self.pos = p

    def parse(self) -> JsonNode:
        self._skip_ws()
        if self.pos >= self.length:
            self._fail()
        node = self._parse_value(0)
        self._skip_ws()
        if self.pos != self.length:
            self._fail()
        return node

    def _parse_value(self, depth: int) -> JsonNode:
        self.remaining_nodes -= 1
        if self.remaining_nodes < 0:
            self._fail("STRUCTURE_TOO_LARGE" if self.error_code == "INVALID_FORMAT" else self.error_code)
        if depth > self.max_depth:
            self._fail("STRUCTURE_TOO_DEEP" if self.error_code == "INVALID_FORMAT" else self.error_code)
        if self.pos >= self.length:
            self._fail()
        b = self.raw[self.pos]
        if b == 0x7B:  # '{'
            return self._parse_object(depth)
        if b == 0x5B:  # '['
            return self._parse_array(depth)
        if b == 0x22:  # '"'
            return self._parse_string()
        if b == 0x74:  # 't'
            return self._parse_literal(b"true", "boolean", True)
        if b == 0x66:  # 'f'
            return self._parse_literal(b"false", "boolean", False)
        if b == 0x6E:  # 'n'
            return self._parse_literal(b"null", "null", None)
        if b == 0x2D or (0x30 <= b <= 0x39):
            return self._parse_number()
        self._fail()
        raise AssertionError("unreachable")

    def _parse_literal(self, expected: bytes, kind: str, value: Any) -> JsonNode:
        start = self.pos
        end = start + len(expected)
        if self.raw[start:end] != expected:
            self._fail()
        self.pos = end
        return JsonNode(kind, value, start, end)

    def _parse_number(self) -> JsonNode:
        start = self.pos
        match = _JSON_NUMBER_BYTES_RE.match(self.raw, start)
        if match is None:
            self._fail()
        end = match.end()
        self.pos = end
        return JsonNode("number", self.raw[start:end].decode("ascii"), start, end)

    def _parse_hex4(self, offset: int) -> int:
        if offset + 4 > self.length:
            self._fail()
        chunk = self.raw[offset:offset + 4]
        if any(byte not in _JSON_HEX_BYTES for byte in chunk):
            self._fail()
        return int(chunk.decode("ascii"), 16)

    def _parse_string(self) -> JsonNode:
        raw = self.raw
        n = self.length
        start = self.pos
        p = start + 1
        while p < n:
            b = raw[p]
            if b == 0x22:  # closing '"'
                end = p + 1
                self.pos = end
                try:
                    decoded = json.loads(raw[start:end].decode("utf-8", errors="strict"))
                except (ValueError, UnicodeDecodeError):
                    self._fail()
                if not isinstance(decoded, str) or _has_lone_surrogate(decoded):
                    self._fail()
                return JsonNode("string", decoded, start, end)
            if b < 0x20:
                self._fail()
            if b == 0x5C:  # '\'
                p += 1
                if p >= n:
                    self._fail()
                esc = raw[p]
                if esc in (0x22, 0x5C, 0x2F, 0x62, 0x66, 0x6E, 0x72, 0x74):
                    p += 1
                elif esc == 0x75:  # 'u'
                    code = self._parse_hex4(p + 1)
                    p += 5
                    if 0xD800 <= code <= 0xDBFF:
                        if p + 6 > n or raw[p:p + 2] != b"\\u":
                            self._fail()
                        low = self._parse_hex4(p + 2)
                        if not (0xDC00 <= low <= 0xDFFF):
                            self._fail()
                        p += 6
                    elif 0xDC00 <= code <= 0xDFFF:
                        self._fail()
                else:
                    self._fail()
            else:
                p += 1
        self._fail()
        raise AssertionError("unreachable")

    def _parse_object(self, depth: int) -> JsonNode:
        start = self.pos
        self.pos += 1  # consume '{'
        self._skip_ws()
        members: Dict[str, JsonNode] = {}
        if self.pos < self.length and self.raw[self.pos] == 0x7D:  # '}'
            self.pos += 1
            return JsonNode("object", members, start, self.pos)
        while True:
            self._skip_ws()
            if self.pos >= self.length or self.raw[self.pos] != 0x22:
                self._fail()
            key_node = self._parse_string()
            key = key_node.value
            if key in members:
                self._fail()
            self._skip_ws()
            if self.pos >= self.length or self.raw[self.pos] != 0x3A:  # ':'
                self._fail()
            self.pos += 1
            self._skip_ws()
            members[key] = self._parse_value(depth + 1)
            self._skip_ws()
            if self.pos >= self.length:
                self._fail()
            sep = self.raw[self.pos]
            if sep == 0x7D:  # '}'
                self.pos += 1
                return JsonNode("object", members, start, self.pos)
            if sep != 0x2C:  # ','
                self._fail()
            self.pos += 1

    def _parse_array(self, depth: int) -> JsonNode:
        start = self.pos
        self.pos += 1  # consume '['
        self._skip_ws()
        items: List[JsonNode] = []
        if self.pos < self.length and self.raw[self.pos] == 0x5D:  # ']'
            self.pos += 1
            return JsonNode("array", items, start, self.pos)
        while True:
            self._skip_ws()
            items.append(self._parse_value(depth + 1))
            self._skip_ws()
            if self.pos >= self.length:
                self._fail()
            sep = self.raw[self.pos]
            if sep == 0x5D:  # ']'
                self.pos += 1
                return JsonNode("array", items, start, self.pos)
            if sep != 0x2C:  # ','
                self._fail()
            self.pos += 1


def _parse_json_bytes(
    raw: bytes,
    *,
    max_bytes: int = MAX_FILE_BYTES,
    max_depth: int = MAX_DEPTH,
    max_nodes: int = MAX_NODES,
    policy_mode: bool = False,
) -> JsonNode:
    if len(raw) > max_bytes:
        raise ConfigEditError("POLICY_INVALID" if policy_mode else "FILE_TOO_LARGE")
    if raw.startswith(b"\xef\xbb\xbf"):
        raise ConfigEditError("POLICY_INVALID" if policy_mode else "INVALID_FORMAT")
    try:
        raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise ConfigEditError("POLICY_INVALID" if policy_mode else "INVALID_ENCODING") from None
    parser = _JsonByteParser(
        raw,
        max_depth=max_depth,
        max_nodes=max_nodes,
        error_code="POLICY_INVALID" if policy_mode else "INVALID_FORMAT",
    )
    return parser.parse()


# --------------------------------------------------- strict single-line dotenv

@dataclass(frozen=True)
class DotenvEntry:
    key: str
    value: str
    quote: Optional[str]  # None | "'" | '"'
    val_start: int
    val_end: int


def _valid_dotenv_quoted_char(ch: str) -> bool:
    if ch in ("\\", "$", "`", "\r", "\n", "\u2028", "\u2029"):
        return False
    code = ord(ch)
    if code < 0x20:
        return ch == "\t"
    if code == 0x7F or (0x80 <= code <= 0x9F) or (0xD800 <= code <= 0xDFFF):
        return False
    return True


def _parse_dotenv_bytes(raw: bytes, *, max_bytes: int = MAX_FILE_BYTES) -> Tuple[List[str], Dict[str, DotenvEntry]]:
    """Parse strict Node-22-aligned single-line dotenv subset with exact value byte spans."""
    if len(raw) > max_bytes:
        raise ConfigEditError("FILE_TOO_LARGE")
    if raw.startswith(b"\xef\xbb\xbf"):
        raise ConfigEditError("INVALID_FORMAT")
    if b"\x00" in raw:
        raise ConfigEditError("INVALID_FORMAT")
    try:
        raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise ConfigEditError("INVALID_ENCODING") from None

    order: List[str] = []
    entries: Dict[str, DotenvEntry] = {}
    n = len(raw)
    pos = 0
    while pos < n:
        nl = raw.find(b"\n", pos)
        if nl == -1:
            line_end = n
            next_pos = n
        else:
            if nl > pos and raw[nl - 1] == 0x0D:
                line_end = nl - 1
            else:
                line_end = nl
            next_pos = nl + 1

        line = raw[pos:line_end]
        if b"\r" in line:
            raise ConfigEditError("INVALID_FORMAT")

        stripped_left = line.lstrip(b" \t")
        if not stripped_left or stripped_left.startswith(b"#"):
            pos = next_pos
            continue

        eq_rel = line.find(b"=")
        if eq_rel == -1:
            raise ConfigEditError("INVALID_FORMAT")

        lhs = line[:eq_rel]
        match = _DOTENV_PREFIX_RE.match(lhs)
        if match is None:
            raise ConfigEditError("INVALID_FORMAT")
        key = match.group("key").decode("ascii")
        if key in entries or key in _DOTENV_UNSUPPORTED_KEYS:
            raise ConfigEditError("INVALID_FORMAT")
        if len(entries) >= MAX_NODES:
            raise ConfigEditError("STRUCTURE_TOO_LARGE")

        rhs_start = pos + eq_rel + 1
        rhs = line[eq_rel + 1:]
        lead_len = 0
        rhs_len = len(rhs)
        while lead_len < rhs_len and rhs[lead_len] in (0x20, 0x09):
            lead_len += 1

        if lead_len == rhs_len:
            # Node 22 can absorb a line after RHS whitespace or retain the
            # export prefix in the key of a bare empty assignment.
            if lead_len or match.group("export"):
                raise ConfigEditError("INVALID_FORMAT")
            entries[key] = DotenvEntry(key, "", None, rhs_start, rhs_start)
            order.append(key)
            pos = next_pos
            continue

        val_start = rhs_start + lead_len
        rest = rhs[lead_len:]
        first = rest[0]
        if first in (0x27, 0x22):  # '\'' or '"'
            trimmed = rest.rstrip(b" \t")
            if len(trimmed) < 2 or trimmed[-1] != first:
                raise ConfigEditError("INVALID_FORMAT")
            inner = trimmed[1:-1]
            if first in inner:
                raise ConfigEditError("INVALID_FORMAT")
            value = inner.decode("utf-8", errors="strict")
            if any(not _valid_dotenv_quoted_char(ch) for ch in value):
                raise ConfigEditError("INVALID_FORMAT")
            val_end = val_start + len(trimmed)
            entries[key] = DotenvEntry(key, value, chr(first), val_start, val_end)
            order.append(key)
        else:
            trimmed = rest.rstrip(b" \t")
            if not _DOTENV_BARE_BYTES_RE.fullmatch(trimmed):
                raise ConfigEditError("INVALID_FORMAT")
            val_end = val_start + len(trimmed)
            entries[key] = DotenvEntry(key, trimmed.decode("ascii"), None, val_start, val_end)
            order.append(key)

        pos = next_pos

    return order, entries


def _encode_dotenv_value(new_value: str, orig_quote: Optional[str]) -> bytes:
    if any(not _valid_dotenv_quoted_char(ch) for ch in new_value):
        raise ConfigEditError("VALUE_INVALID")
    if "'" in new_value and '"' in new_value:
        raise ConfigEditError("VALUE_INVALID")
    if new_value == "":
        quote = orig_quote if orig_quote in ("'", '"') else '"'
        return (quote + quote).encode("ascii")
    if orig_quote == "'":
        if "'" not in new_value:
            return ("'" + new_value + "'").encode("utf-8")
        return ('"' + new_value + '"').encode("utf-8")
    if orig_quote == '"':
        if '"' not in new_value:
            return ('"' + new_value + '"').encode("utf-8")
        return ("'" + new_value + "'").encode("utf-8")
    if _DOTENV_BARE_TEXT_RE.fullmatch(new_value):
        return new_value.encode("ascii")
    if '"' not in new_value:
        return ('"' + new_value + '"').encode("utf-8")
    return ("'" + new_value + "'").encode("utf-8")


# --------------------------------------------------- quarantine & policy

def _inside(path: str, root: str) -> bool:
    try:
        return os.path.commonpath((os.path.normcase(path), os.path.normcase(root))) == os.path.normcase(root)
    except ValueError:
        return False


def _locations(path: str) -> Tuple[str, str, str]:
    absolute = os.path.normpath(os.path.abspath(path))
    parent, leaf = os.path.split(absolute)
    return (absolute, os.path.realpath(absolute), os.path.join(os.path.realpath(parent), leaf))


def _check_path_boundaries(
    workspace: Optional[str],
    parts: Sequence[str],
    trash_root: Optional[str],
    *,
    error_code: str,
) -> Set[Tuple[int, int]]:
    if any(part.lower() in CONTROL_DIRS for part in parts):
        raise ConfigEditError(error_code)
    if workspace is None:
        return set()
    ws_abs = os.path.normpath(os.path.abspath(workspace))
    ws_real = os.path.realpath(ws_abs)
    effective_trash = trash_root if trash_root is not None else exfil_audit.trash_root(ws_real)
    buckets = {
        os.path.join(ws_abs, TRASH_DIRNAME),
        os.path.join(ws_real, TRASH_DIRNAME),
        os.path.normpath(os.path.abspath(effective_trash)),
    }
    boundaries = [loc for bucket in buckets for loc in _locations(bucket)]
    candidates = {
        os.path.join(ws_abs, *parts),
        os.path.join(ws_real, *parts),
    }
    targets = [loc for candidate in candidates for loc in _locations(candidate)]
    if any(_inside(target, boundary) or _inside(boundary, target)
           for target in targets for boundary in boundaries):
        raise ConfigEditError(error_code)

    identities: Set[Tuple[int, int]] = set()
    for bucket in buckets:
        try:
            info = os.stat(bucket)
        except (FileNotFoundError, NotADirectoryError):
            continue
        except OSError:
            raise ConfigEditError(error_code) from None
        if stat.S_ISDIR(info.st_mode) and info.st_ino:
            identities.add((info.st_dev, info.st_ino))
    return identities


@dataclass(frozen=True)
class FilePolicy:
    relative_path: str
    format_name: str
    editable_keys: FrozenSet[Tuple[str, ...]]
    locked_keys: FrozenSet[Tuple[str, ...]]


@dataclass(frozen=True)
class ConfigEditPolicy:
    version: int
    files: Dict[str, FilePolicy]
    fingerprint: str


def _validate_relative_path(raw_path: str, *, error_code: str) -> Tuple[str, ...]:
    if not isinstance(raw_path, str):
        raise ConfigEditError(error_code)
    if any(ord(ch) < 32 or ord(ch) == 127 or _has_lone_surrogate(ch) for ch in raw_path):
        raise ConfigEditError(error_code)
    try:
        parts = safe_view._components(raw_path)
    except safe_view.SafeViewError:
        raise ConfigEditError(error_code) from None
    return tuple(parts)


def parse_policy_bytes(
    raw: Optional[bytes],
    workspace: Optional[str] = None,
    *,
    trash_root: Optional[str] = None,
) -> ConfigEditPolicy:
    """Validate .agent-guard/config-edit-policy.json bytes on all platforms."""
    if raw is None:
        raise ConfigEditError("POLICY_MISSING")
    root_node = _parse_json_bytes(
        raw,
        max_bytes=MAX_POLICY_BYTES,
        max_depth=MAX_DEPTH,
        max_nodes=MAX_POLICY_NODES,
        policy_mode=True,
    )
    if root_node.kind != "object":
        raise ConfigEditError("POLICY_INVALID")
    data = root_node.value
    if set(data.keys()) != {"version", "files"}:
        raise ConfigEditError("POLICY_INVALID")
    version_node = data["version"]
    if version_node.kind != "number" or version_node.value != "1":
        raise ConfigEditError("POLICY_INVALID")
    version = 1
    files_node = data["files"]
    if files_node.kind != "object":
        raise ConfigEditError("POLICY_INVALID")

    parsed_files: Dict[str, FilePolicy] = {}
    canonical_files: Dict[str, Any] = {}
    for rel_path, entry in files_node.value.items():
        parts = _validate_relative_path(rel_path, error_code="POLICY_INVALID")
        normalized_rel = "/".join(parts)
        if normalized_rel != rel_path or normalized_rel in parsed_files:
            raise ConfigEditError("POLICY_INVALID")
        _check_path_boundaries(workspace, parts, trash_root, error_code="POLICY_INVALID")

        if entry.kind != "object":
            raise ConfigEditError("POLICY_INVALID")
        fields = entry.value
        if not {"format", "editable_keys"}.issubset(fields):
            raise ConfigEditError("POLICY_INVALID")
        if not fields.keys() <= {"format", "editable_keys", "locked_keys"}:
            raise ConfigEditError("POLICY_INVALID")

        format_node = fields["format"]
        if format_node.kind != "string" or format_node.value not in POLICY_FORMATS:
            raise ConfigEditError("POLICY_INVALID")
        fmt = format_node.value
        editable_node = fields["editable_keys"]
        locked_node = fields.get("locked_keys")
        if editable_node.kind != "array" or (locked_node is not None and locked_node.kind != "array"):
            raise ConfigEditError("POLICY_INVALID")

        editable_set: Set[Tuple[str, ...]] = set()
        for key_node in editable_node.value:
            if key_node.kind != "string":
                raise ConfigEditError("POLICY_INVALID")
            try:
                parsed_key = parse_key(fmt, key_node.value)
            except ValueError:
                raise ConfigEditError("POLICY_INVALID") from None
            if parsed_key in editable_set:
                raise ConfigEditError("POLICY_INVALID")
            if is_sensitive_key(parsed_key):
                raise ConfigEditError("POLICY_SENSITIVE_KEY_FORBIDDEN")
            editable_set.add(parsed_key)

        locked_set: Set[Tuple[str, ...]] = set()
        for key_node in locked_node.value if locked_node is not None else ():
            if key_node.kind != "string":
                raise ConfigEditError("POLICY_INVALID")
            try:
                parsed_key = parse_key(fmt, key_node.value)
            except ValueError:
                raise ConfigEditError("POLICY_INVALID") from None
            if parsed_key in locked_set:
                raise ConfigEditError("POLICY_INVALID")
            locked_set.add(parsed_key)

        if editable_set & locked_set:
            raise ConfigEditError("POLICY_INVALID")

        parsed_files[normalized_rel] = FilePolicy(
            relative_path=normalized_rel,
            format_name=fmt,
            editable_keys=frozenset(editable_set),
            locked_keys=frozenset(locked_set),
        )
        canonical_files[normalized_rel] = {
            "format": fmt,
            "editable_keys": sorted(list(k) for k in editable_set),
            "locked_keys": sorted(list(k) for k in locked_set),
        }

    digest = hashlib.sha256(
        json.dumps(
            {"version": version, "files": canonical_files},
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    ).hexdigest()
    return ConfigEditPolicy(version=version, files=parsed_files, fingerprint=digest)


# --------------------------------------------------- new value & label validation

def validate_new_value(
    raw_value: Optional[str],
    value_type: str,
    format_name: str,
    workspace: str,
) -> Tuple[Any, Optional[str]]:
    """Validate type, two-layer secret/path/placeholder rejection, and format rules."""
    if value_type not in VALUE_TYPES:
        raise ConfigEditError("INVALID_ARGUMENT")
    if format_name == "dotenv" and value_type != "string":
        raise ConfigEditError("INVALID_ARGUMENT")

    if value_type == "null":
        if raw_value is not None and raw_value != "null":
            raise ConfigEditError("INVALID_ARGUMENT")
        return None, None

    if raw_value is None or not isinstance(raw_value, str):
        raise ConfigEditError("INVALID_ARGUMENT")
    if "\x00" in raw_value or _has_lone_surrogate(raw_value):
        raise ConfigEditError("VALUE_INVALID")

    if value_type == "boolean":
        if raw_value == "true":
            return True, None
        if raw_value == "false":
            return False, None
        raise ConfigEditError("INVALID_ARGUMENT")

    if value_type == "number":
        if len(raw_value) > MAX_FILE_BYTES or not _JSON_NUMBER_RE.fullmatch(raw_value):
            raise ConfigEditError("INVALID_ARGUMENT")
        return raw_value, raw_value

    # value_type == "string": enforce two-layer rejection + format subset.
    if len(raw_value.encode("utf-8", errors="strict")) > MAX_FILE_BYTES:
        raise ConfigEditError("FILE_TOO_LARGE")

    # Layer 1: redaction scanner with EMPTY exemptions (ignores workspace allowlists).
    scan = redaction.scan_text(
        raw_value,
        channel="file-write",
        workspace=workspace,
        max_bytes=MAX_FILE_BYTES,
        exemption=redaction.Exemption(),
    )
    if not scan.scanned or scan.spans:
        raise ConfigEditError("VALUE_REJECTED_BY_SCAN")

    # Layer 2: hard-reject redaction placeholder literals.
    lowered = raw_value.lower()
    if any(marker in lowered for marker in _PLACEHOLDER_SUBSTRINGS) or any(
        run in raw_value for run in _PLACEHOLDER_RUNS
    ):
        raise ConfigEditError("VALUE_REJECTED_PLACEHOLDER")

    if format_name == "dotenv":
        if any(not _valid_dotenv_quoted_char(ch) for ch in raw_value):
            raise ConfigEditError("VALUE_INVALID")
        if "'" in raw_value and '"' in raw_value:
            raise ConfigEditError("VALUE_INVALID")

    return raw_value, None


def _safe_preview_label(label: str, workspace: str, *, is_json_pointer: bool = False) -> str:
    """Escape control characters and scan for secrets/host paths before display."""
    if not isinstance(label, str) or not label or len(label) > MAX_PREVIEW_LABEL_LEN:
        raise ConfigEditError("UNSAFE_PREVIEW_LABEL")
    if "\x00" in label or _has_lone_surrogate(label) or carrier_unicode.MARKERS.search(label):
        raise ConfigEditError("UNSAFE_PREVIEW_LABEL")
    escaped_chars: List[str] = []
    for ch in label:
        cat = unicodedata.category(ch)
        if cat in ("Cf", "Cs", "Zl", "Zp"):
            raise ConfigEditError("UNSAFE_PREVIEW_LABEL")
        if cat == "Cc":
            escaped_chars.append(f"\\u{ord(ch):04x}")
        else:
            escaped_chars.append(ch)
    escaped = "".join(escaped_chars)
    if len(escaped) > MAX_PREVIEW_LABEL_LEN:
        raise ConfigEditError("UNSAFE_PREVIEW_LABEL")
    for candidate in (label, escaped):
        if redaction.detect_secrets(candidate, channel="file-write"):
            raise ConfigEditError("UNSAFE_PREVIEW_LABEL")
        if redaction.secret_source_span(candidate, "file-write") is not None:
            raise ConfigEditError("UNSAFE_PREVIEW_LABEL")
        path_spans = redaction.detect_paths(candidate, workspace=workspace, channel="file-write")
        if is_json_pointer:
            if any(
                span.rule_id in (redaction.RULE_HOST_ABSOLUTE_PATH, redaction.RULE_DEVICE_PATH)
                for span in path_spans
            ):
                raise ConfigEditError("UNSAFE_PREVIEW_LABEL")
        elif path_spans:
            raise ConfigEditError("UNSAFE_PREVIEW_LABEL")
    return escaped


# --------------------------------------------------- surgical edit & re-parse

def _scalar_state(kind: str, value: Any) -> str:
    if kind == "null":
        return "empty"
    if kind == "string" and value == "":
        return "empty"
    return "set"


def _verify_json_trees(
    old_node: JsonNode,
    new_node: JsonNode,
    target_segments: Tuple[str, ...],
    expected_kind: str,
    expected_value: Any,
    expected_span: Tuple[int, int],
    current_path: Optional[Tuple[str, ...]] = (),
) -> None:
    if current_path == target_segments:
        if (
            new_node.kind != expected_kind
            or type(new_node.value) is not type(expected_value)
            or new_node.value != expected_value
            or (new_node.start, new_node.end) != expected_span
        ):
            raise ConfigEditError("VERIFICATION_FAILED")
        return

    if old_node.kind != new_node.kind:
        raise ConfigEditError("VERIFICATION_FAILED")

    if old_node.kind == "object":
        if list(old_node.value.keys()) != list(new_node.value.keys()):
            raise ConfigEditError("VERIFICATION_FAILED")
        for key, old_child in old_node.value.items():
            next_path = None if current_path is None else (*current_path, key)
            _verify_json_trees(
                old_child,
                new_node.value[key],
                target_segments,
                expected_kind,
                expected_value,
                expected_span,
                next_path,
            )
        return

    if old_node.kind == "array":
        if len(old_node.value) != len(new_node.value):
            raise ConfigEditError("VERIFICATION_FAILED")
        for old_item, new_item in zip(old_node.value, new_node.value):
            _verify_json_trees(
                old_item,
                new_item,
                target_segments,
                expected_kind,
                expected_value,
                expected_span,
                None,
            )
        return

    if type(old_node.value) is not type(new_node.value) or old_node.value != new_node.value:
        raise ConfigEditError("VERIFICATION_FAILED")


def _plan_json_replacement(
    old_bytes: bytes,
    target_segments: Tuple[str, ...],
    value_type: str,
    new_value: Any,
    number_literal: Optional[str],
) -> Tuple[str, str, bytes]:
    old_tree = _parse_json_bytes(old_bytes)
    cur = old_tree
    for seg in target_segments[:-1]:
        if cur.kind != "object" or seg not in cur.value:
            raise ConfigEditError("TARGET_NOT_FOUND")
        cur = cur.value[seg]
    if cur.kind != "object" or target_segments[-1] not in cur.value:
        raise ConfigEditError("TARGET_NOT_FOUND")
    target = cur.value[target_segments[-1]]
    if target.kind not in ("string", "number", "boolean", "null"):
        raise ConfigEditError("TARGET_NOT_SCALAR")

    before_type = target.kind
    before_state = _scalar_state(target.kind, target.value)

    if value_type == "string":
        repl_bytes = json.dumps(new_value, ensure_ascii=False).encode("utf-8")
        expected_val: Any = new_value
    elif value_type == "number":
        assert number_literal is not None
        repl_bytes = number_literal.encode("ascii")
        expected_val = number_literal
    elif value_type == "boolean":
        repl_bytes = b"true" if new_value else b"false"
        expected_val = new_value
    else:
        repl_bytes = b"null"
        expected_val = None

    new_bytes = old_bytes[:target.start] + repl_bytes + old_bytes[target.end:]
    if len(new_bytes) > MAX_FILE_BYTES:
        raise ConfigEditError("FILE_TOO_LARGE")

    # Pre-write full re-parse and byte-outer-span verification.
    if (
        new_bytes[:target.start] != old_bytes[:target.start]
        or new_bytes[target.start + len(repl_bytes):] != old_bytes[target.end:]
    ):
        raise ConfigEditError("VERIFICATION_FAILED")
    new_tree = _parse_json_bytes(new_bytes)
    _verify_json_trees(
        old_tree,
        new_tree,
        target_segments,
        value_type,
        expected_val,
        (target.start, target.start + len(repl_bytes)),
    )
    return before_type, before_state, new_bytes


def _plan_dotenv_replacement(
    old_bytes: bytes,
    target_key: str,
    new_value: str,
) -> Tuple[str, str, bytes]:
    old_order, old_entries = _parse_dotenv_bytes(old_bytes)
    if target_key not in old_entries:
        raise ConfigEditError("TARGET_NOT_FOUND")
    target = old_entries[target_key]
    before_type = "string"
    before_state = "empty" if target.value == "" else "set"

    repl_bytes = _encode_dotenv_value(new_value, target.quote)
    new_bytes = old_bytes[:target.val_start] + repl_bytes + old_bytes[target.val_end:]
    if len(new_bytes) > MAX_FILE_BYTES:
        raise ConfigEditError("FILE_TOO_LARGE")

    if (
        new_bytes[:target.val_start] != old_bytes[:target.val_start]
        or new_bytes[target.val_start + len(repl_bytes):] != old_bytes[target.val_end:]
    ):
        raise ConfigEditError("VERIFICATION_FAILED")

    new_order, new_entries = _parse_dotenv_bytes(new_bytes)
    if new_order != old_order:
        raise ConfigEditError("VERIFICATION_FAILED")
    for key in old_order:
        expected = new_value if key == target_key else old_entries[key].value
        if new_entries[key].value != expected:
            raise ConfigEditError("VERIFICATION_FAILED")
    return before_type, before_state, new_bytes


# --------------------------------------------------- POSIX descriptor helpers

def _dir_identity(st: os.stat_result) -> Tuple[int, int]:
    return (st.st_dev, st.st_ino)


def _file_version(st: os.stat_result) -> Tuple[int, int, int, int, int]:
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def _read_fd_bounded(fd: int, max_bytes: int, too_large_code: str) -> bytes:
    chunks: List[bytes] = []
    total = 0
    while True:
        chunk = os.read(fd, min(65536, max_bytes + 1 - total))
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise ConfigEditError(too_large_code)
        chunks.append(chunk)
    return b"".join(chunks)


def _list_xattrs_fd(fd: int) -> List[str]:
    try:
        return os.listxattr(fd)
    except OSError:
        raise ConfigEditError("UNSUPPORTED_FILE_METADATA") from None


def _check_supported_metadata(
    fd: int, st: os.stat_result, parent_fd: int,
) -> Optional[bytes]:
    if st.st_uid != os.geteuid():
        raise ConfigEditError("UNSUPPORTED_FILE_METADATA")
    if stat.S_IMODE(st.st_mode) & 0o7000 != 0:
        raise ConfigEditError("UNSUPPORTED_FILE_METADATA")
    attrs = _list_xattrs_fd(fd)
    if not attrs:
        return None
    # On SELinux-enabled hosts, ordinary files automatically inherit their
    # parent directory's default security.selinux label. Allow only a single
    # inherited security.selinux label matching the parent directory (and
    # re-verified against the newly created temp file before replace).
    if attrs != ["security.selinux"]:
        raise ConfigEditError("UNSUPPORTED_FILE_METADATA")
    try:
        file_label = os.getxattr(fd, "security.selinux")
        parent_label = os.getxattr(parent_fd, "security.selinux")
    except OSError:
        raise ConfigEditError("UNSUPPORTED_FILE_METADATA") from None
    if file_label != parent_label:
        raise ConfigEditError("UNSUPPORTED_FILE_METADATA")
    return file_label


def _verify_temp_xattrs(tmp_fd: int, expected_selinux: Optional[bytes]) -> None:
    attrs = _list_xattrs_fd(tmp_fd)
    if expected_selinux is None:
        if attrs:
            raise ConfigEditError("UNSUPPORTED_FILE_METADATA")
        return
    if attrs != ["security.selinux"]:
        raise ConfigEditError("UNSUPPORTED_FILE_METADATA")
    try:
        tmp_label = os.getxattr(tmp_fd, "security.selinux")
    except OSError:
        raise ConfigEditError("UNSUPPORTED_FILE_METADATA") from None
    if tmp_label != expected_selinux:
        raise ConfigEditError("UNSUPPORTED_FILE_METADATA")


def _read_policy_under_dir(
    policy_dir_fd: int,
    *,
    changed_code: str = "POLICY_CHANGED",
) -> Tuple[bytes, Tuple[int, int, int, int, int], str]:
    file_flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)
    try:
        pfd = os.open(POLICY_FILE, file_flags, dir_fd=policy_dir_fd)
    except FileNotFoundError:
        raise ConfigEditError("POLICY_MISSING") from None
    except OSError:
        raise ConfigEditError("POLICY_INVALID") from None
    try:
        before = os.fstat(pfd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ConfigEditError("POLICY_INVALID")
        if before.st_size > MAX_POLICY_BYTES:
            raise ConfigEditError("POLICY_INVALID")
        raw = _read_fd_bounded(pfd, MAX_POLICY_BYTES, "POLICY_INVALID")
        after = os.fstat(pfd)
        linked = os.stat(POLICY_FILE, dir_fd=policy_dir_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(linked.st_mode)
            or linked.st_nlink != 1
            or _file_version(before) != _file_version(after)
            or _file_version(after) != _file_version(linked)
        ):
            raise ConfigEditError(changed_code)
    except ConfigEditError:
        raise
    except OSError:
        raise ConfigEditError("POLICY_INVALID") from None
    finally:
        os.close(pfd)
    return raw, _file_version(after), hashlib.sha256(raw).hexdigest()


def _open_parent_chain(
    stack: ExitStack,
    root_fd: int,
    parent_parts: Sequence[str],
    quarantine_dirs: Set[Tuple[int, int]],
    *,
    error_code: str,
) -> Tuple[int, Tuple[Tuple[int, int], ...]]:
    dir_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    root_id = _dir_identity(os.fstat(root_fd))
    if root_id in quarantine_dirs:
        raise ConfigEditError(error_code)
    chain: List[Tuple[int, int]] = [root_id]
    current = root_fd
    for part in parent_parts:
        try:
            current = os.open(part, dir_flags, dir_fd=current)
        except OSError:
            raise ConfigEditError(error_code) from None
        stack.callback(os.close, current)
        cur_id = _dir_identity(os.fstat(current))
        if cur_id in quarantine_dirs:
            raise ConfigEditError(error_code)
        chain.append(cur_id)
    return current, tuple(chain)


def _read_target_under_dir(
    parent_fd: int,
    filename: str,
    *,
    missing_or_unsafe_code: str = "UNSAFE_FILE",
) -> Tuple[bytes, os.stat_result, str, Optional[bytes]]:
    file_flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)
    try:
        tfd = os.open(filename, file_flags, dir_fd=parent_fd)
    except OSError:
        raise ConfigEditError(missing_or_unsafe_code) from None
    try:
        before = os.fstat(tfd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ConfigEditError(missing_or_unsafe_code)
        if before.st_size > MAX_FILE_BYTES:
            raise ConfigEditError("FILE_TOO_LARGE")
        selinux_label = _check_supported_metadata(tfd, before, parent_fd)
        raw = _read_fd_bounded(tfd, MAX_FILE_BYTES, "FILE_TOO_LARGE")
        after = os.fstat(tfd)
        linked = os.stat(filename, dir_fd=parent_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(linked.st_mode)
            or linked.st_nlink != 1
            or _file_version(before) != _file_version(after)
            or _file_version(after) != _file_version(linked)
        ):
            raise ConfigEditError("FILE_CHANGED")
    except ConfigEditError:
        raise
    except OSError:
        raise ConfigEditError(missing_or_unsafe_code) from None
    finally:
        os.close(tfd)
    return raw, after, hashlib.sha256(raw).hexdigest(), selinux_label


@contextmanager
def _acquire_policy_lock(policy_dir_fd: int, timeout: float = LOCK_TIMEOUT_SECONDS):
    """Acquire bounded-wait cooperative flock on .agent-guard/config-edit.lock."""
    import fcntl  # Lazy import so Windows can still import this module for Doctor/platform gate.

    flags = os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)
    try:
        lock_fd = os.open(LOCK_FILE, flags, 0o600, dir_fd=policy_dir_fd)
    except OSError:
        raise ConfigEditError("IO_ERROR") from None
    acquired = False
    try:
        opened = os.fstat(lock_fd)
        linked = os.stat(LOCK_FILE, dir_fd=policy_dir_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or not stat.S_ISREG(linked.st_mode)
            or not os.path.samestat(opened, linked)
        ):
            raise ConfigEditError("IO_ERROR")
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError as exc:
                if exc.errno not in (errno.EAGAIN, errno.EACCES, errno.EDEADLK):
                    raise ConfigEditError("IO_ERROR") from None
                if time.monotonic() >= deadline:
                    raise ConfigEditError("LOCK_TIMEOUT") from None
                time.sleep(LOCK_POLL_SECONDS)
        yield lock_fd
    finally:
        if acquired:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(lock_fd)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    written = 0
    total = len(data)
    while written < total:
        n = os.write(fd, view[written:])
        if n <= 0:
            raise OSError("short write")
        written += n


def _append_config_edit_audit(
    trash_root: str,
    *,
    status: str,
    code: str,
    replaced: bool,
    durability_confirmed: bool,
    warning: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    check_id = audit.new_txid()
    row: Dict[str, Any] = {
        "event": "exfil-config-edit",
        "tool": "exfil_config_edit",
        "phase": "complete",
        "status": status,
        "code": code,
        "ok": status == "applied",
        "replaced": replaced,
        "durability_confirmed": durability_confirmed,
        "check_id": check_id,
        "ts": audit.utc_now_iso(),
        "session": audit.session_id(),
    }
    if warning is not None:
        row["warning"] = warning
    try:
        exfil_audit._append(Path(trash_root) / AUDIT_NAME, [row])
    except OSError:
        return None, "AUDIT_WRITE_FAILED"
    return check_id, None


# --------------------------------------------------- preview & apply API

@dataclass(frozen=True)
class PreviewSnapshot:
    workspace_root: str
    trash_root: str
    relative_path: str
    parts: Tuple[str, ...]
    format_name: str
    key_raw: str
    key_segments: Tuple[str, ...]
    value_type: str
    new_value: Any
    number_literal: Optional[str]
    before_type: str
    before_state: str
    after_type: str
    safe_file_label: str
    safe_key_label: str
    root_id: Tuple[int, int]
    policy_dir_id: Tuple[int, int]
    policy_version: Tuple[int, int, int, int, int]
    policy_hash: str
    parent_chain_ids: Tuple[Tuple[int, int], ...]
    target_version: Tuple[int, int, int, int, int]
    target_hash: str
    target_uid: int
    target_gid: int
    target_mode: int
    target_selinux: Optional[bytes] = None


def preview_edit(
    workspace: str,
    relative_path: str,
    *,
    key: str,
    value: Optional[str] = None,
    value_type: str = "string",
    format_name: str = "auto",
    trash_root: Optional[str] = None,
) -> Tuple[PreviewSnapshot, Dict[str, Any]]:
    """Read-only preview that validates policy, value, target, and binds snapshot identity."""
    if not supports_safe_config_edit():
        raise ConfigEditError("UNSUPPORTED_PLATFORM")

    if (
        not isinstance(workspace, str)
        or not workspace
        or not isinstance(key, str)
        or format_name not in FORMATS
        or value_type not in VALUE_TYPES
    ):
        raise ConfigEditError("INVALID_ARGUMENT")

    parts = _validate_relative_path(relative_path, error_code="INVALID_PATH")
    normalized_rel = "/".join(parts)

    root_path = os.path.realpath(os.path.abspath(workspace))
    effective_trash = trash_root if trash_root is not None else exfil_audit.trash_root(root_path)

    dir_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    with ExitStack() as stack:
        try:
            root_fd = os.open(root_path, dir_flags)
        except OSError:
            raise ConfigEditError("UNSAFE_FILE") from None
        stack.callback(os.close, root_fd)
        root_id = _dir_identity(os.fstat(root_fd))

        try:
            policy_dir_fd = os.open(POLICY_DIR, dir_flags, dir_fd=root_fd)
        except FileNotFoundError:
            raise ConfigEditError("POLICY_MISSING") from None
        except OSError:
            raise ConfigEditError("POLICY_INVALID") from None
        stack.callback(os.close, policy_dir_fd)
        policy_dir_id = _dir_identity(os.fstat(policy_dir_fd))

        policy_raw, policy_version, policy_hash = _read_policy_under_dir(policy_dir_fd)
        policy_obj = parse_policy_bytes(policy_raw, root_path, trash_root=effective_trash)

        # Policy checks run BEFORE opening the target file.
        if normalized_rel not in policy_obj.files:
            raise ConfigEditError("FILE_NOT_ALLOWED")
        file_policy = policy_obj.files[normalized_rel]
        effective_format = file_policy.format_name if format_name == "auto" else format_name
        if effective_format != file_policy.format_name:
            raise ConfigEditError("FORMAT_MISMATCH")

        try:
            key_segments = parse_key(effective_format, key)
        except ValueError:
            raise ConfigEditError("INVALID_ARGUMENT") from None

        if key_segments in file_policy.locked_keys:
            raise ConfigEditError("KEY_LOCKED")
        if key_segments not in file_policy.editable_keys:
            raise ConfigEditError("KEY_NOT_ALLOWED")

        # Validate new value before opening target file.
        parsed_value, number_literal = validate_new_value(
            value, value_type, effective_format, root_path,
        )

        # Validate display labels before opening target file or returning preview.
        safe_file_label = _safe_preview_label(normalized_rel, root_path, is_json_pointer=False)
        safe_key_label = _safe_preview_label(
            key, root_path, is_json_pointer=(effective_format == "json"),
        )

        # Verify path and quarantine boundaries before walking target parent chain.
        quarantine_dirs = _check_path_boundaries(
            root_path, parts, effective_trash, error_code="FILE_NOT_ALLOWED",
        )
        parent_fd, parent_chain_ids = _open_parent_chain(
            stack, root_fd, parts[:-1], quarantine_dirs, error_code="UNSAFE_FILE",
        )
        target_raw, target_stat, target_hash, target_selinux = _read_target_under_dir(
            parent_fd, parts[-1],
        )

    if effective_format == "json":
        before_type, before_state, _ = _plan_json_replacement(
            target_raw, key_segments, value_type, parsed_value, number_literal,
        )
    else:
        before_type, before_state, _ = _plan_dotenv_replacement(
            target_raw, key_segments[0], parsed_value,
        )

    snapshot = PreviewSnapshot(
        workspace_root=root_path,
        trash_root=effective_trash,
        relative_path=normalized_rel,
        parts=parts,
        format_name=effective_format,
        key_raw=key,
        key_segments=key_segments,
        value_type=value_type,
        new_value=parsed_value,
        number_literal=number_literal,
        before_type=before_type,
        before_state=before_state,
        after_type=value_type,
        safe_file_label=safe_file_label,
        safe_key_label=safe_key_label,
        root_id=root_id,
        policy_dir_id=policy_dir_id,
        policy_version=policy_version,
        policy_hash=policy_hash,
        parent_chain_ids=parent_chain_ids,
        target_version=_file_version(target_stat),
        target_hash=target_hash,
        target_uid=target_stat.st_uid,
        target_gid=target_stat.st_gid,
        target_mode=stat.S_IMODE(target_stat.st_mode),
        target_selinux=target_selinux,
    )
    receipt = make_receipt(
        status="preview",
        code="PREVIEW_OK",
        replaced=False,
        durability_confirmed=False,
        file_label=safe_file_label,
        key_label=safe_key_label,
        format_name=effective_format,
        before_type=before_type,
        before_state=before_state,
        after_type=value_type,
    )
    return snapshot, receipt


def _pre_commit_reverify(
    snapshot: PreviewSnapshot,
    parent_fd: int,
) -> None:
    """Recheck the current policy, quarantine boundaries, and target before replace."""
    dir_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    with ExitStack() as check_stack:
        try:
            root_fd = os.open(snapshot.workspace_root, dir_flags)
        except OSError:
            raise ConfigEditError("FILE_CHANGED") from None
        check_stack.callback(os.close, root_fd)
        if _dir_identity(os.fstat(root_fd)) != snapshot.root_id:
            raise ConfigEditError("FILE_CHANGED")

        try:
            pdir_fd = os.open(POLICY_DIR, dir_flags, dir_fd=root_fd)
        except OSError:
            raise ConfigEditError("POLICY_CHANGED") from None
        check_stack.callback(os.close, pdir_fd)
        if _dir_identity(os.fstat(pdir_fd)) != snapshot.policy_dir_id:
            raise ConfigEditError("POLICY_CHANGED")
        try:
            _, pol_ver, pol_hash = _read_policy_under_dir(pdir_fd, changed_code="POLICY_CHANGED")
        except ConfigEditError:
            raise ConfigEditError("POLICY_CHANGED") from None
        if pol_ver != snapshot.policy_version or pol_hash != snapshot.policy_hash:
            raise ConfigEditError("POLICY_CHANGED")

        quarantine_dirs = _check_path_boundaries(
            snapshot.workspace_root, snapshot.parts, snapshot.trash_root, error_code="FILE_CHANGED",
        )
        _, chain_ids = _open_parent_chain(
            check_stack, root_fd, snapshot.parts[:-1], quarantine_dirs, error_code="FILE_CHANGED",
        )
        if chain_ids != snapshot.parent_chain_ids:
            raise ConfigEditError("FILE_CHANGED")

        try:
            cur_target = os.stat(snapshot.parts[-1], dir_fd=parent_fd, follow_symlinks=False)
        except OSError:
            raise ConfigEditError("FILE_CHANGED") from None
        if (
            not stat.S_ISREG(cur_target.st_mode)
            or cur_target.st_nlink != 1
            or _file_version(cur_target) != snapshot.target_version
        ):
            raise ConfigEditError("FILE_CHANGED")


def apply_preview(
    snapshot: PreviewSnapshot,
    *,
    lock_timeout: float = LOCK_TIMEOUT_SECONDS,
) -> Tuple[Dict[str, Any], int]:
    """Execute one confirmed preview under cooperative lock with honest write/audit status."""
    replaced = False
    durability_confirmed = False
    warning: Optional[str] = None
    primary_status = "applied"
    primary_code = "APPLIED"
    primary_exit = 0

    try:
        if not supports_safe_config_edit():
            raise ConfigEditError("UNSUPPORTED_PLATFORM")

        dir_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        with ExitStack() as stack:
            try:
                root_fd = os.open(snapshot.workspace_root, dir_flags)
            except OSError:
                raise ConfigEditError("FILE_CHANGED") from None
            stack.callback(os.close, root_fd)
            if _dir_identity(os.fstat(root_fd)) != snapshot.root_id:
                raise ConfigEditError("FILE_CHANGED")

            try:
                policy_dir_fd = os.open(POLICY_DIR, dir_flags, dir_fd=root_fd)
            except OSError:
                raise ConfigEditError("POLICY_CHANGED") from None
            stack.callback(os.close, policy_dir_fd)
            if _dir_identity(os.fstat(policy_dir_fd)) != snapshot.policy_dir_id:
                raise ConfigEditError("POLICY_CHANGED")

            with _acquire_policy_lock(policy_dir_fd, timeout=lock_timeout):
                # Re-load and verify policy under lock.
                try:
                    pol_raw, pol_ver, pol_hash = _read_policy_under_dir(
                        policy_dir_fd, changed_code="POLICY_CHANGED",
                    )
                except ConfigEditError:
                    raise ConfigEditError("POLICY_CHANGED") from None
                if pol_ver != snapshot.policy_version or pol_hash != snapshot.policy_hash:
                    raise ConfigEditError("POLICY_CHANGED")
                policy_obj = parse_policy_bytes(
                    pol_raw, snapshot.workspace_root, trash_root=snapshot.trash_root,
                )
                file_policy = policy_obj.files.get(snapshot.relative_path)
                if (
                    file_policy is None
                    or file_policy.format_name != snapshot.format_name
                    or snapshot.key_segments in file_policy.locked_keys
                    or snapshot.key_segments not in file_policy.editable_keys
                ):
                    raise ConfigEditError("POLICY_CHANGED")

                # Re-walk parent chain and verify quarantine boundaries + directory identities.
                quarantine_dirs = _check_path_boundaries(
                    snapshot.workspace_root,
                    snapshot.parts,
                    snapshot.trash_root,
                    error_code="FILE_CHANGED",
                )
                parent_fd, chain_ids = _open_parent_chain(
                    stack, root_fd, snapshot.parts[:-1], quarantine_dirs, error_code="FILE_CHANGED",
                )
                if chain_ids != snapshot.parent_chain_ids:
                    raise ConfigEditError("FILE_CHANGED")

                # Re-read target file and verify version, content digest, and metadata.
                target_raw, target_stat, target_hash, target_selinux = _read_target_under_dir(
                    parent_fd, snapshot.parts[-1], missing_or_unsafe_code="FILE_CHANGED",
                )
                if (
                    _file_version(target_stat) != snapshot.target_version
                    or target_hash != snapshot.target_hash
                    or target_stat.st_uid != snapshot.target_uid
                    or target_stat.st_gid != snapshot.target_gid
                    or stat.S_IMODE(target_stat.st_mode) != snapshot.target_mode
                    or target_selinux != snapshot.target_selinux
                ):
                    raise ConfigEditError("FILE_CHANGED")

                # Re-compute & re-parse replacement bytes under lock.
                if snapshot.format_name == "json":
                    _, _, new_bytes = _plan_json_replacement(
                        target_raw,
                        snapshot.key_segments,
                        snapshot.value_type,
                        snapshot.new_value,
                        snapshot.number_literal,
                    )
                else:
                    _, _, new_bytes = _plan_dotenv_replacement(
                        target_raw,
                        snapshot.key_segments[0],
                        snapshot.new_value,
                    )

                # Create hidden 0600 temp file in target parent_fd, restore & verify metadata, fsync.
                tmp_name = f".agent-guard-edit-{os.getpid()}-{secrets.token_hex(8)}.tmp"
                tmp_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                try:
                    tmp_fd = os.open(tmp_name, tmp_flags, 0o600, dir_fd=parent_fd)
                except OSError:
                    raise ConfigEditError("IO_ERROR") from None
                tmp_created = True
                try:
                    try:
                        _write_all(tmp_fd, new_bytes)
                        tmp_stat = os.fstat(tmp_fd)
                        if tmp_stat.st_gid != snapshot.target_gid:
                            try:
                                os.fchown(tmp_fd, -1, snapshot.target_gid)
                            except OSError:
                                raise ConfigEditError("METADATA_RESTORE_FAILED") from None
                        try:
                            os.fchmod(tmp_fd, snapshot.target_mode)
                        except OSError:
                            raise ConfigEditError("METADATA_RESTORE_FAILED") from None
                        verified = os.fstat(tmp_fd)
                        if (
                            verified.st_uid != snapshot.target_uid
                            or verified.st_gid != snapshot.target_gid
                            or stat.S_IMODE(verified.st_mode) != snapshot.target_mode
                            or verified.st_size != len(new_bytes)
                            or verified.st_nlink != 1
                        ):
                            raise ConfigEditError("METADATA_RESTORE_FAILED")
                        _verify_temp_xattrs(tmp_fd, snapshot.target_selinux)
                        try:
                            os.fsync(tmp_fd)
                        except OSError:
                            raise ConfigEditError("IO_ERROR") from None
                    finally:
                        os.close(tmp_fd)

                    # Final pre-commit re-verification of root, policy, parent chain, target, and temp entry.
                    _pre_commit_reverify(snapshot, parent_fd)
                    try:
                        tmp_linked = os.stat(tmp_name, dir_fd=parent_fd, follow_symlinks=False)
                    except OSError:
                        raise ConfigEditError("IO_ERROR") from None
                    if not stat.S_ISREG(tmp_linked.st_mode) or not os.path.samestat(tmp_linked, verified):
                        raise ConfigEditError("IO_ERROR")

                    try:
                        os.replace(tmp_name, snapshot.parts[-1], src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
                    except OSError:
                        raise ConfigEditError("IO_ERROR") from None
                    tmp_created = False
                    replaced = True
                finally:
                    if tmp_created:
                        try:
                            os.unlink(tmp_name, dir_fd=parent_fd)
                        except OSError:
                            pass

                # Honest parent directory fsync after successful replace.
                try:
                    os.fsync(parent_fd)
                    durability_confirmed = True
                except OSError:
                    durability_confirmed = False
                    warning = "DIR_FSYNC_UNCONFIRMED"
                    primary_status = "applied_with_warning"
                    primary_code = "DIR_FSYNC_UNCONFIRMED"
                    primary_exit = 1
    except ConfigEditError as exc:
        primary_status = exc.status
        primary_code = exc.code
        primary_exit = exc.exit_code
    except OSError:
        primary_status = "error"
        primary_code = "IO_ERROR"
        primary_exit = 1

    audit_id, audit_error = _append_config_edit_audit(
        snapshot.trash_root,
        status=primary_status,
        code=primary_code,
        replaced=replaced,
        durability_confirmed=durability_confirmed,
        warning=warning,
    )
    if audit_error is not None and replaced:
        primary_status = "applied_with_warning"
        primary_exit = 1

    receipt = make_receipt(
        status=primary_status,
        code=primary_code,
        replaced=replaced,
        durability_confirmed=durability_confirmed,
        file_label=snapshot.safe_file_label if replaced else None,
        key_label=snapshot.safe_key_label if replaced else None,
        format_name=snapshot.format_name if replaced else None,
        before_type=snapshot.before_type if replaced else None,
        before_state=snapshot.before_state if replaced else None,
        after_type=snapshot.after_type if replaced else None,
        warning=warning,
        audit_id=audit_id,
        audit_error=audit_error,
    )
    return receipt, primary_exit
