"""Opt-in egress audit receipts with a closed metadata projection."""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import time

from . import AUDIT_NAME, TRASH_DIRNAME, audit


MAX_AUDIT_SPANS = 64
WRITE_WARNING = "exfil-guard warning: AUDIT_WRITE_FAILED: egress audit could not be persisted"


def enabled(flag=False):
    return flag or os.environ.get("AGENT_GUARD_EXFIL_AUDIT", "").lower() in {"1", "true", "yes"}


def trash_root(workspace):
    return os.environ.get("AGENT_GUARD_TRASH") or os.path.join(workspace, TRASH_DIRNAME)


def _append(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = (os.O_WRONLY | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0) |
             getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
    try:
        expected = path.lstat()
    except FileNotFoundError:
        try:
            fd = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            expected = path.lstat()
        else:
            expected = None
    if expected is not None:
        if not stat.S_ISREG(expected.st_mode):
            raise OSError("audit destination must be a regular file")
        fd = os.open(path, flags)
    try:
        opened = os.fstat(fd)
        current = path.lstat()
        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or not stat.S_ISREG(current.st_mode) or not os.path.samestat(current, opened)
                or (expected is not None and not os.path.samestat(expected, opened))):
            raise OSError("audit destination changed or is linked")
        data = "".join(json.dumps(audit.project(row), sort_keys=True) + "\n" for row in records).encode("utf-8")
        if os.write(fd, data) != len(data):
            raise OSError("audit append was incomplete")
        os.fsync(fd)
    finally:
        os.close(fd)


def record(workspace, tool, phase, result, *, started, active):
    if not active:
        return {"enabled": False, "recorded": False}
    check_id = audit.new_txid()
    plan_id = audit.new_txid() if result["decision"] == "SANITIZE" and result.get("redaction_plan") else None
    exempted = result.get("exempted", 0)
    spans = result.get("spans", [])
    span_count = result.get("span_count", len(spans))
    common = {"tool": tool, "check_id": check_id, "plan_id": plan_id,
              "channel": result.get("channel"), "exempted": bool(exempted),
              "ts": audit.utc_now_iso(), "session": audit.session_id()}
    summary = {**common, "event": "exfil-" + result["decision"].lower(), "phase": phase,
               "decision": result["decision"], "code": result["code"], "ok": result["exit"] == 0,
               "guard_latency_ms": (time.monotonic() - started) * 1000,
               "span_count": span_count, "redacted_count": len(result.get("redaction_plan", [])),
               "exempted_count": exempted, "omitted_span_count": span_count - min(len(spans), MAX_AUDIT_SPANS),
               "size_bytes": result.get("size_bytes")}
    rows = [summary]
    for span in spans[:MAX_AUDIT_SPANS]:
        rows.append({**common, "event": "exfil-span", "exempted": False,
                     **{key: span[key] for key in ("decision", "code", "rule_id", "family", "confidence", "context", "length")},
                     "span": [span["start"], span["end"]]})
    try:
        _append(Path(trash_root(workspace)) / AUDIT_NAME, rows)
    except OSError:
        return {"enabled": True, "recorded": False, "error": "AUDIT_WRITE_FAILED"}
    receipt = {"enabled": True, "recorded": True, "check_id": check_id, "records_written": len(rows)}
    if plan_id is not None:
        receipt["plan_id"] = plan_id
    return receipt
