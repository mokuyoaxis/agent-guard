"""Bounded read-only workspace queries; no payload walks, writes or grants."""
from __future__ import annotations

from collections import Counter
import json
import os
from pathlib import Path
import re
import stat
import time

from . import MANIFEST_NAME, TRASH_DIRNAME, admission, audit, policy, recovery
from .cleanup_saved import MAX_SAVED_BYTES
from .cleanup_tasks import _read_task, _store
from .messages import HOST_STATUS_NOTICE
from .trash_index import list_trash_locations


SCHEMA = "workspace_query/v1"
DEFAULT_LIMIT = 10
MAX_LIMIT = 50
MAX_TASK_ENTRIES = 1000
MAX_TASK_BYTES = 32 * 1024 * 1024
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_MANIFEST_LINES = 20_000
MAX_MANIFEST_LINE_BYTES = 64 * 1024
TASK_FILE = re.compile(r"([0-9a-f]{32})\.json\Z")
TXID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,254}\Z")
RECORD_TYPES = {"tx-start", "relocate", "relocate-intent", "relocate-failed", "snapshot", "restore", "purged"}


def _valid_record(record):
    """Validate external fields used by the existing transaction reducer."""
    if (not isinstance(record, dict) or not isinstance(record.get("txid"), str)
            or TXID.fullmatch(record["txid"]) is None
            or not isinstance(record.get("type"), str) or record["type"] not in RECORD_TYPES
            or (record.get("ts") is not None and not isinstance(record["ts"], str))):
        return False
    if record["type"] in ("relocate", "relocate-intent"):
        return all(isinstance(record.get(key), str) and os.path.isabs(record[key]) and "\0" not in record[key]
                   for key in ("origin_path", "trash_path"))
    if record["type"] == "snapshot":
        return record.get("sha") is None or isinstance(record["sha"], str)
    if record["type"] == "restore":
        return all(isinstance(record.get(key, []), list) for key in ("restored", "conflicts", "errors"))
    return True


def _manifest(path):
    records = []
    scope = {"complete": True, "lines_read": 0, "bytes_read": 0, "invalid_records": 0,
             "max_bytes": MAX_MANIFEST_BYTES, "max_lines": MAX_MANIFEST_LINES, "issues": []}
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(descriptor, "rb") as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise ValueError("manifest must be regular")
            while True:
                remaining = MAX_MANIFEST_BYTES - scope["bytes_read"]
                raw = source.readline(min(remaining, MAX_MANIFEST_LINE_BYTES) + 1)
                if not raw:
                    break
                scope["bytes_read"] += len(raw)
                if (scope["bytes_read"] > MAX_MANIFEST_BYTES or len(raw) > MAX_MANIFEST_LINE_BYTES
                        or scope["lines_read"] >= MAX_MANIFEST_LINES):
                    scope["complete"] = False
                    scope["issues"].append("MANIFEST_READ_LIMIT")
                    break
                scope["lines_read"] += 1
                if not raw.strip():
                    continue
                if not raw.endswith(b"\n"):
                    scope["complete"] = False
                    scope["issues"].append("MANIFEST_INCOMPLETE_LINE")
                    break
                try:
                    record = json.loads(raw)
                except (ValueError, RecursionError):
                    scope["invalid_records"] += 1
                    continue
                if _valid_record(record):
                    records.append(record)
                else:
                    scope["invalid_records"] += 1
    except FileNotFoundError:
        scope["missing"] = True
    except (OSError, ValueError):
        scope["complete"] = False
        scope["issues"].append("MANIFEST_UNREADABLE")
    if scope["invalid_records"]:
        scope["complete"] = False
        scope["issues"].append("MANIFEST_INVALID_RECORDS")
    return records, scope


def _tasks(workspace, trash_root, limit, now):
    scope = {"complete": True, "entries_examined": 0, "records_read": 0, "charged_bytes": 0,
             "excluded_other_budget": 0, "max_entries": MAX_TASK_ENTRIES,
             "max_bytes": MAX_TASK_BYTES, "issues": []}
    candidates, rows = [], []
    try:
        with os.scandir(_store(workspace)) as entries:
            for entry in entries:
                if scope["entries_examined"] >= MAX_TASK_ENTRIES:
                    scope["complete"] = False
                    scope["issues"].append("TASK_DIRECTORY_LIMIT")
                    break
                scope["entries_examined"] += 1
                match = TASK_FILE.fullmatch(entry.name)
                if match is None:
                    continue
                try:
                    info = entry.stat(follow_symlinks=False)
                    if not stat.S_ISREG(info.st_mode):
                        raise ValueError("task must be regular")
                    candidates.append((info.st_mtime, match.group(1), info.st_size))
                except (OSError, ValueError):
                    scope["complete"] = False
                    scope["issues"].append("TASK_METADATA_UNREADABLE")
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        scope["complete"] = False
        scope["issues"].append("TASK_DIRECTORY_UNREADABLE")
    sid = admission._session_key()
    for modified, task_id, size in sorted(candidates, reverse=True):
        if size > MAX_SAVED_BYTES:
            scope["complete"] = False
            scope["issues"].append("TASK_RECORD_TOO_LARGE")
            continue
        if scope["charged_bytes"] + size > MAX_TASK_BYTES:
            scope["complete"] = False
            scope["issues"].append("TASK_BYTE_LIMIT")
            break
        scope["charged_bytes"] += size
        try:
            # A concurrent growth is refused within the observed byte charge.
            task = _read_task(workspace, task_id, max_bytes=size)
            scope["records_read"] += 1
        except (OSError, ValueError):
            scope["complete"] = False
            scope["issues"].append("TASK_RECORD_UNREADABLE")
            continue
        if task["session"] != sid or task["trash_root"] != trash_root:
            scope["excluded_other_budget"] += 1
            continue
        rows.append({"task_id": task_id, "status": task["status"], "updated_at": modified,
                     "targets": sum(len(group["paths"]) for group in task["groups"]),
                     "confirmed": sum(len(group["confirmed"]) for group in task["groups"]),
                     "approval_expired": now > task["expires"], "approval_checked": False})
    scope["issues"] = sorted(set(scope["issues"]))
    more = True if len(rows) > limit else False if scope["complete"] else None
    return {"scope": "current workspace, quarantine and budget identity", "coverage": scope,
            "observed_count": len(rows), "returned": len(rows[:limit]), "has_more": more,
            "items": rows[:limit], "order": "updated descending within examined directory entries"}


def workspace_query(workspace, trash_root=None, *, limit=DEFAULT_LIMIT):
    """Read one bounded metadata snapshot; limits do not authorize mutations."""
    if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
        raise ValueError("query limit must be an integer between 1 and 50")
    root = Path(workspace).resolve(strict=True)
    if not root.is_dir() or root.parent == root:
        raise ValueError("query workspace must be an existing non-root directory")
    workspace = str(root)
    trash_root = os.path.abspath(trash_root or os.path.join(workspace, TRASH_DIRNAME))
    now = time.time()
    mode = policy.load_mode(trash_root)
    mode_metadata = audit.project({"ts": mode["since"]})
    try:
        budget = admission.describe(workspace, trash_root)
    except admission.AdmissionDenied as exc:
        budget = {"status": "BLOCKED", "code": exc.code, "reason": exc.reason}
    try:
        os.lstat(trash_root)
    except FileNotFoundError:
        location = {"complete": True, "entries": [], "status": "NOT_CREATED", "errors": []}
    except OSError:
        location = {"complete": False, "entries": [], "status": "UNREADABLE", "errors": [{"code": "QUARANTINE_UNREADABLE"}]}
    else:
        location = list_trash_locations(trash_paths=[trash_root])
    records, manifest = _manifest(os.path.join(trash_root, MANIFEST_NAME))
    # Shared reconciliation checks only recorded copy paths, never walks a
    # transaction directory. Default legacy callers still read their manifest.
    transactions = recovery.RecoveryEngine(workspace, trash_root).transactions(records=records)
    rows, age_candidates, unknown_age = [], [], 0
    for tx in transactions.values():
        created = recovery.RecoveryEngine._parse_ts(tx.get("ts"))
        age_hint = None
        if manifest["complete"]:
            if tx.get("purged"):
                age_hint = False
            elif created is not None:
                age_hint = created < now - recovery.SOFT_RETENTION_DAYS * 86400
        if age_hint is None:
            unknown_age += 1
        row = {"txid": tx["txid"], "created_at": audit.project({"ts": tx.get("ts")}).get("ts"),
               "state": tx["state"] if manifest["complete"] else "UNKNOWN",
               "items": len(tx["items"]),
               "restorable_items": tx["restorable_items"] if manifest["complete"] else None,
               "manual_review_items": tx["manual_review_items"] if manifest["complete"] else None,
               "age_candidate_hint": age_hint}
        rows.append(row)
        if age_hint:
            age_candidates.append(tx["txid"])
    rows.sort(key=lambda row: (row["created_at"] or "", row["txid"]), reverse=True)
    tasks = _tasks(workspace, trash_root, limit, now)
    complete = location["complete"] and manifest["complete"] and tasks["coverage"]["complete"] and budget.get("status") != "BLOCKED"
    return {"schema": SCHEMA, "read_at": now, "complete": complete,
            "workspace": workspace, "trash_root": trash_root, "mode": mode["mode"],
            "mode_since": mode_metadata.get("ts"), "mode_semantics": "existing Core policy.load_mode including its default/fallback behavior",
            "delete_admission": budget, "tasks": tasks,
            "transactions": {"observed_count": len(rows), "returned": len(rows[:limit]),
                             "has_more": True if len(rows) > limit else False if manifest["complete"] else None,
                             "items": rows[:limit],
                             "manual_review_items": sum(row["manual_review_items"] for row in rows)
                             if manifest["complete"] else None,
                             "states": dict(Counter(row["state"] for row in rows)), "coverage": manifest,
                             "notice": "Existing Core best-effort reconciliation of recorded paths. EMPTY is not verified absence; path-probe errors, payload integrity, Git snapshot existence and restore permission are not established."},
            "storage": {"location": location, "measured": False, "logical_bytes": None,
                        "physical_freed_bytes": None, "notice": "No recursive payload/size scan. Request the existing GC plan explicitly for its detailed best-effort inventory."},
            "gc_candidates": {"age_only": True, "count": len(age_candidates) if manifest["complete"] else None,
                              "scope": "observed transaction metadata",
                              "unknown_age_transactions": unknown_age,
                              "age_coverage_complete": manifest["complete"] and unknown_age == 0,
                              "sample": age_candidates[:limit], "capacity_candidates": None,
                              "soft_retention_days": recovery.SOFT_RETENTION_DAYS,
                              "soft_limit_bytes": recovery.SOFT_SIZE_LIMIT_BYTES,
                              "notice": "Age hints from metadata only; not a full GC selection or permission to purge. Capacity eligibility is unmeasured."},
            "host_status_notice": HOST_STATUS_NOTICE,
            "notice": "Read-only, non-atomic metadata snapshot. Partial coverage is not absence; task rows do not validate grants. No raw tokens, session identity, manifest content or payload-origin paths are returned."}
