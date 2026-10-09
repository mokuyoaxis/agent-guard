"""Auditability pillar: append-only JSONL evidence log.

Records contain supported metadata, not arbitrary caller payloads. Reading
legacy records applies the same projection without rewriting the log.
Durability and append-only storage remain separate from recovery manifests,
which retain the exact paths required to restore data.
"""
from __future__ import annotations

from collections import deque
from functools import lru_cache
import hashlib
import json
import math
import os
import re
import threading
import time
import uuid
from typing import Any, Dict, List

_APPEND_LOCK = threading.Lock()
_LOCAL_SESSION = uuid.uuid4().hex
_CORRELATION_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_TXID_RE = re.compile(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{8}\Z")
_TIMESTAMP_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")
_HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
_GIT_SHA_RE = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")
_COUNTS = frozenset({
    "target_count", "moved_count", "skipped_count", "restored_count",
    "conflict_count", "error_count", "missing_count", "moved", "skipped",
    "enumerated", "start", "end", "length",
})
_IDENTIFIERS = frozenset({"txid", "check_id", "plan_id"})
_ID_LISTS = frozenset({"txids", "backup_txids", "purged", "missing"})
_COMPENSATION_FIELDS = frozenset({
    "strategy", "txid", "status", "moved", "skipped", "enumerated", "sha",
})


def correlation_id(value: str) -> str:
    """Opaque, deterministic correlation; not encryption or authentication.

    Already-projected identifiers remain stable when read or re-appended.
    Surrogate escapes in POSIX metadata are hashed without emitting them.
    """
    if _CORRELATION_RE.fullmatch(value):
        return value
    return "sha256:" + hashlib.sha256(
        value.encode("utf-8", errors="surrogatepass")).hexdigest()


def _identifier(value: str) -> str:
    # Generated recovery/check IDs stay directly usable. Other spellings
    # retain correlation without becoming an arbitrary text channel.
    return value if _TXID_RE.fullmatch(value) else correlation_id(value)


@lru_cache(maxsize=1)
def _enums():
    # Import after module initialization: recovery imports the ID/time
    # helpers above, and needs no policy or redaction decisions here.
    from . import dialects, policy, redaction
    decisions = frozenset({"ALLOW", "RELOCATE", "SNAPSHOT", "SANITIZE", "ASK", "BLOCK"})
    return {
        "event": frozenset({
            "check", "ask", "decision", "advisory", "intent", "outcome",
            "enforce-block", "enforce-intent", "enforce-error", "enforce-proceed",
            "restore", "gc-intent", "gc",
        }) | {"exfil-" + decision.lower() for decision in decisions},
        "decision": decisions, "action": decisions | {"PURGED"},
        "code": frozenset(policy.EXPLANATIONS),
        "tool": frozenset({"safe_delete", "check", "restore", "gc"}),
        "phase": frozenset({"advisory", "intent", "complete"}),
        "outcome": frozenset({
            "relocated to quarantine", "deleted directly (provably regenerable)",
            "deleted directly (quarantine housekeeping)",
        }),
        "dialect": frozenset(dialects.DIALECT_ALIASES.values()),
        "dialect_requested": frozenset(dialects.DIALECT_ALIASES) | {"<redacted>"},
        "dialect_outcome": frozenset({"ok", "unusable"}),
        "strategy": frozenset({"relocate", "clean-enumerate", "snapshot"}),
        "status": frozenset({"started", "complete", "partial", "storage-failed",
                             "failed", "incomplete"}),
        "rule_id": frozenset(value for key, value in vars(redaction).items()
                             if key.startswith("RULE_") and isinstance(value, str)),
        "family": frozenset({"secret", "path"}),
        "confidence": frozenset({"deterministic", "contextual"}),
        "channel": frozenset(redaction.CHANNELS),
        "context": frozenset({"prose", "key", "uri-userinfo", "env-var"}),
        "command": frozenset({"<redacted>"}),
        "error": frozenset({"compensation failed"}),
    }


def project(record: Dict[str, Any]) -> Dict[str, Any]:
    """Supported metadata only; unknown fields/types/free text are omitted.

    This is a closed record projection, not a general text sanitizer. Nested
    compensation records and GC reason maps have explicit shapes. Recovery
    journals, authorization state, and caller objects are never modified.
    """
    if not isinstance(record, dict):
        raise TypeError("audit record must be an object")
    stored: Dict[str, Any] = {}
    for key, allowed in _enums().items():
        value = record.get(key)
        if key == "context" and isinstance(value, str) and value.startswith("key="):
            value = "key"
        if isinstance(value, str) and value in allowed:
            stored[key] = value
    for key in _COUNTS:
        value = record.get(key)
        if type(value) is int and value >= 0:
            stored[key] = value
    for key in ("force", "ok", "exempted"):
        if type(record.get(key)) is bool:
            stored[key] = record[key]
    latency = record.get("guard_latency_ms")
    if (type(latency) is int and latency >= 0 or
            type(latency) is float and math.isfinite(latency) and latency >= 0):
        stored["guard_latency_ms"] = latency
    ts = record.get("ts")
    if isinstance(ts, str) and _TIMESTAMP_RE.fullmatch(ts):
        stored["ts"] = ts
    for key in _IDENTIFIERS:
        value = record.get(key)
        if isinstance(value, str) and value:
            stored[key] = _identifier(value)
    for key in _ID_LISTS:
        values = record.get(key)
        if isinstance(values, list):
            stored[key] = [_identifier(v) for v in values if isinstance(v, str) and v]
    session = record.get("session")
    if isinstance(session, str) and session:
        stored["session"] = correlation_id(session)
    digest = record.get("txid_sha256")
    if isinstance(digest, str) and _HASH_RE.fullmatch(digest):
        stored["txid_sha256"] = digest
    digests = record.get("missing_txid_sha256")
    if isinstance(digests, list):
        stored["missing_txid_sha256"] = [
            v for v in digests if isinstance(v, str) and _HASH_RE.fullmatch(v)]
    sha = record.get("sha")
    if "sha" in record and (sha is None or isinstance(sha, str) and _GIT_SHA_RE.fullmatch(sha)):
        stored["sha"] = sha
    span = record.get("span")
    if (isinstance(span, list) and len(span) == 2 and
            all(type(v) is int and v >= 0 for v in span) and span[1] >= span[0]):
        stored["span"] = list(span)
    if record.get("reasons") == []:
        stored["reasons"] = []
    compensations = record.get("compensations")
    if isinstance(compensations, list):
        stored["compensations"] = [
            project({k: v for k, v in item.items() if k in _COMPENSATION_FIELDS})
            for item in compensations if isinstance(item, dict)]
    reasons = record.get("plan_reasons")
    if isinstance(reasons, dict):
        stored["plan_reasons"] = {
            _identifier(txid): reason for txid, reason in reasons.items()
            if isinstance(txid, str) and txid and isinstance(reason, str)
            and reason in {"age", "capacity"}}
    return stored


def utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def new_txid() -> str:
    """Compensation transaction id: sortable timestamp + random suffix."""
    return time.strftime("%Y%m%d-%H%M%S", time.gmtime()) + "-" + uuid.uuid4().hex[:8]


def session_id() -> str:
    """Opaque correlation for the acting session.

    An agent harness may identify itself through AGENT_GUARD_SESSION.
    This is correlation metadata, not authentication.
    """
    env = os.environ.get("AGENT_GUARD_SESSION")
    if env:
        return correlation_id(env)
    return correlation_id(f"{_LOCAL_SESSION}:{os.getpid()}")


def append(record: Dict[str, Any], audit_path: str) -> Dict[str, Any]:
    """Append projected metadata; return exactly what was stored."""
    stored = project(record)
    stored.setdefault("ts", utc_now_iso())
    stored.setdefault("session", session_id())
    line = json.dumps(stored, ensure_ascii=False, sort_keys=True)
    directory = os.path.dirname(audit_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with _APPEND_LOCK:
        with open(audit_path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    return stored


def tail(audit_path: str, n: int = 20) -> List[Dict[str, Any]]:
    """Last n projected objects; malformed/non-object lines are skipped.

    A non-positive limit reads nothing. Old bytes are never rewritten.
    """
    if n <= 0 or not os.path.exists(audit_path):
        return []
    records = deque(maxlen=n)
    with open(audit_path, "r", encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                records.append(project(record))
    return list(records)


def find_by_txid(audit_path: str, txid: str) -> List[Dict[str, Any]]:
    """All audit records belonging to one compensation transaction."""
    key = _identifier(txid)
    return [r for r in tail(audit_path, n=100_000)
            if r.get("txid") == key]
