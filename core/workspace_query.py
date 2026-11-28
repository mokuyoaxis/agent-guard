"""Bounded read-only workspace queries; no payload walks, writes or grants."""
from __future__ import annotations

from collections import Counter
import errno
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
DEFAULT_MEASURE_ENTRIES = 50_000
MAX_MEASURE_ENTRIES = admission.MAX_SCAN_ENTRIES
MAX_MEASURE_DEPTH = 128
TOP_TRANSACTION_LIMIT = 10
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


_DIR_OPEN_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
_CONCURRENT_ERRNOS = frozenset(
    code for code in (
        errno.ELOOP,
        errno.ENOTDIR,
        errno.ENOENT,
        getattr(errno, "EMLINK", None),
        getattr(errno, "EFTYPE", None),
    )
    if code is not None
)


_DESCRIPTOR_SCAN_SUPPORTED = (
    hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY")
    and {os.open, os.stat} <= os.supports_dir_fd
    and os.scandir in os.supports_fd
)


def _supports_descriptor_scan():
    return _DESCRIPTOR_SCAN_SUPPORTED


def _is_link(info):
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def _dir_signature(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _measure_quarantine(trash_root, *, max_entries=DEFAULT_MEASURE_ENTRIES,
                        max_depth=MAX_MEASURE_DEPTH):
    """Read-only, bounded lstat scan matching Core admission accounting.

    Only FileNotFoundError on the root itself counts as an uncreated empty
    quarantine. Budget counts every attempted entry (including unreadable or
    special entries) while streaming directly from os.scandir without
    per-directory pre-reading; observed_* tracks only entries verified and
    measured successfully.
    """
    result = {
        "complete": True,
        "truncated": False,
        "root_exists": False,
        "observed_logical_bytes": 0,
        "observed_entries": 0,
        "attempted_entries": 0,
        "max_entries": max_entries,
        "max_depth": max_depth,
        "direct_child_dirs": 0,
        "child_dir_totals": {},
        "issues": [],
    }
    issues = set()

    def mark_issue(code, *, truncated=False):
        issues.add(code)
        result["complete"] = False
        if truncated:
            result["truncated"] = True

    try:
        root_info = os.lstat(trash_root)
    except FileNotFoundError:
        return result
    except OSError:
        mark_issue("STORAGE_ROOT_UNREADABLE")
        result["issues"] = sorted(issues)
        return result

    result["root_exists"] = True
    scan_root = trash_root
    if _is_link(root_info):
        # A linked bucket owns its physical contents (admission.py:312).
        try:
            physical_root = os.path.realpath(trash_root)
            physical_info = os.lstat(physical_root)
        except OSError:
            mark_issue("STORAGE_ROOT_UNREADABLE")
            result["issues"] = sorted(issues)
            return result
        if _is_link(physical_info) or not stat.S_ISDIR(physical_info.st_mode):
            mark_issue("STORAGE_ROOT_UNREADABLE")
            result["issues"] = sorted(issues)
            return result
        scan_root = physical_root
        root_info = physical_info
    elif not stat.S_ISDIR(root_info.st_mode):
        mark_issue("STORAGE_ROOT_UNREADABLE")
        result["issues"] = sorted(issues)
        return result

    # Account for the quarantine root directory itself (matches admission._measure),
    # committing staged subtree counts only after directory verification passes.
    if result["attempted_entries"] >= max_entries:
        mark_issue("STORAGE_ENTRY_LIMIT", truncated=True)
        result["issues"] = sorted(issues)
        return result
    result["attempted_entries"] += 1

    if _supports_descriptor_scan():
        def verify_dir_fd(dir_fd, dir_info, *, parent_fd, entry_name, verify_path):
            try:
                after_fd = os.fstat(dir_fd)
                if parent_fd is not None:
                    after_link = os.stat(entry_name, dir_fd=parent_fd, follow_symlinks=False)
                else:
                    after_link = os.lstat(verify_path)
            except OSError:
                mark_issue("STORAGE_CONCURRENT_CHANGE")
                return False
            if (_dir_signature(after_fd) != _dir_signature(dir_info)
                    or _dir_signature(after_link) != _dir_signature(after_fd)):
                mark_issue("STORAGE_CONCURRENT_CHANGE")
                return False
            return True

        def scan_dir_fd(dir_fd, dir_info, depth, *, parent_fd, entry_name, verify_path):
            local_entries = 1
            local_bytes = dir_info.st_size
            local_direct_dirs = 0
            local_child_totals = {}
            try:
                with os.scandir(dir_fd) as listing:
                    if depth >= max_depth:
                        for _ in listing:
                            if result["attempted_entries"] >= max_entries:
                                mark_issue("STORAGE_ENTRY_LIMIT", truncated=True)
                                break
                            result["attempted_entries"] += 1
                            mark_issue("STORAGE_DEPTH_LIMIT", truncated=True)
                            break
                    else:
                        for child in listing:
                            if result["attempted_entries"] >= max_entries:
                                mark_issue("STORAGE_ENTRY_LIMIT", truncated=True)
                                break
                            result["attempted_entries"] += 1
                            try:
                                child_info = os.stat(child.name, dir_fd=dir_fd, follow_symlinks=False)
                            except OSError:
                                mark_issue("STORAGE_UNREADABLE_ENTRY")
                                continue
                            mode = child_info.st_mode
                            if _is_link(child_info):
                                # Internal symlinks and Windows junctions stay opaque.
                                local_entries += 1
                                local_bytes += child_info.st_size
                            elif stat.S_ISREG(mode):
                                local_entries += 1
                                local_bytes += child_info.st_size
                            elif stat.S_ISDIR(mode):
                                try:
                                    child_fd = os.open(child.name, _DIR_OPEN_FLAGS, dir_fd=dir_fd)
                                except OSError as exc:
                                    if exc.errno in _CONCURRENT_ERRNOS:
                                        mark_issue("STORAGE_CONCURRENT_CHANGE")
                                    else:
                                        mark_issue("STORAGE_UNREADABLE_ENTRY")
                                    continue
                                try:
                                    try:
                                        opened_info = os.fstat(child_fd)
                                    except OSError:
                                        mark_issue("STORAGE_UNREADABLE_ENTRY")
                                        continue
                                    if (_is_link(opened_info)
                                            or not stat.S_ISDIR(opened_info.st_mode)
                                            or _dir_signature(opened_info) != _dir_signature(child_info)):
                                        mark_issue("STORAGE_CONCURRENT_CHANGE")
                                        continue
                                    sub = scan_dir_fd(
                                        child_fd, opened_info, depth + 1,
                                        parent_fd=dir_fd, entry_name=child.name, verify_path=None,
                                    )
                                    if sub is not None:
                                        sub_entries, sub_bytes, sub_dirs, sub_totals = sub
                                        local_entries += sub_entries
                                        local_bytes += sub_bytes
                                        if depth == 0:
                                            local_direct_dirs += 1
                                            local_child_totals[child.name] = {
                                                "logical_bytes": sub_bytes,
                                                "entries": sub_entries,
                                            }
                                        else:
                                            local_direct_dirs += sub_dirs
                                            local_child_totals.update(sub_totals)
                                    if "STORAGE_ENTRY_LIMIT" in issues:
                                        break
                                finally:
                                    os.close(child_fd)
                            else:
                                mark_issue("STORAGE_SPECIAL_ENTRY")
            except OSError:
                mark_issue("STORAGE_UNREADABLE_ENTRY")
                return None
            # Always verify directory identity before committing staged subtree counts.
            if not verify_dir_fd(dir_fd, dir_info, parent_fd=parent_fd, entry_name=entry_name, verify_path=verify_path):
                return None
            return local_entries, local_bytes, local_direct_dirs, local_child_totals

        try:
            root_fd = os.open(scan_root, _DIR_OPEN_FLAGS)
        except OSError as exc:
            if exc.errno in _CONCURRENT_ERRNOS:
                mark_issue("STORAGE_CONCURRENT_CHANGE")
            else:
                mark_issue("STORAGE_ROOT_UNREADABLE")
            result["issues"] = sorted(issues)
            return result
        try:
            try:
                opened_root = os.fstat(root_fd)
            except OSError:
                mark_issue("STORAGE_ROOT_UNREADABLE")
                result["issues"] = sorted(issues)
                return result
            if (_is_link(opened_root)
                    or not stat.S_ISDIR(opened_root.st_mode)
                    or _dir_signature(opened_root) != _dir_signature(root_info)):
                mark_issue("STORAGE_CONCURRENT_CHANGE")
                result["issues"] = sorted(issues)
                return result
            root_totals = scan_dir_fd(
                root_fd, opened_root, 0,
                parent_fd=None, entry_name=None, verify_path=scan_root,
            )
            if root_totals is not None:
                entries_ok, bytes_ok, direct_dirs_ok, child_totals_ok = root_totals
                result["observed_entries"] = entries_ok
                result["observed_logical_bytes"] = bytes_ok
                result["direct_child_dirs"] = direct_dirs_ok
                result["child_dir_totals"] = child_totals_ok
        finally:
            os.close(root_fd)
    else:
        # Non-descriptor fallback (e.g. Windows): bind non-directory child metadata
        # to the open scandir handle (DirEntry.stat), use os.lstat() consistently
        # for directory identity, and commit staged subtree stats only after
        # post-scan verification succeeds (including on truncation).
        def scan_dir_path(path, expected_info, depth, *, is_root=False):
            local_entries = 0
            local_bytes = 0
            local_direct_dirs = 0
            local_child_totals = {}
            opened_info = None
            try:
                with os.scandir(path) as listing:
                    try:
                        opened_info = os.lstat(path)
                    except OSError:
                        mark_issue("STORAGE_CONCURRENT_CHANGE")
                        return None
                    if (_is_link(opened_info)
                            or not stat.S_ISDIR(opened_info.st_mode)
                            or _dir_signature(opened_info) != _dir_signature(expected_info)):
                        mark_issue("STORAGE_CONCURRENT_CHANGE")
                        return None
                    local_entries = 1
                    local_bytes = opened_info.st_size
                    if depth >= max_depth:
                        for _ in listing:
                            if result["attempted_entries"] >= max_entries:
                                mark_issue("STORAGE_ENTRY_LIMIT", truncated=True)
                                break
                            result["attempted_entries"] += 1
                            mark_issue("STORAGE_DEPTH_LIMIT", truncated=True)
                            break
                    else:
                        for child in listing:
                            if result["attempted_entries"] >= max_entries:
                                mark_issue("STORAGE_ENTRY_LIMIT", truncated=True)
                                break
                            result["attempted_entries"] += 1
                            try:
                                entry_info = child.stat(follow_symlinks=False)
                            except OSError:
                                mark_issue("STORAGE_UNREADABLE_ENTRY")
                                continue
                            mode = entry_info.st_mode
                            if _is_link(entry_info):
                                # Internal symlinks and Windows junctions stay opaque.
                                local_entries += 1
                                local_bytes += entry_info.st_size
                            elif stat.S_ISREG(mode):
                                local_entries += 1
                                local_bytes += entry_info.st_size
                            elif stat.S_ISDIR(mode):
                                try:
                                    parent_now = os.lstat(path)
                                except OSError:
                                    mark_issue("STORAGE_CONCURRENT_CHANGE")
                                    return None
                                if _dir_signature(parent_now) != _dir_signature(opened_info):
                                    mark_issue("STORAGE_CONCURRENT_CHANGE")
                                    return None
                                try:
                                    child_info = os.lstat(child.path)
                                except OSError as exc:
                                    if exc.errno in _CONCURRENT_ERRNOS:
                                        mark_issue("STORAGE_CONCURRENT_CHANGE")
                                    else:
                                        mark_issue("STORAGE_UNREADABLE_ENTRY")
                                    continue
                                if _is_link(child_info) or not stat.S_ISDIR(child_info.st_mode):
                                    mark_issue("STORAGE_CONCURRENT_CHANGE")
                                    continue
                                sub = scan_dir_path(child.path, child_info, depth + 1)
                                if sub is not None:
                                    sub_entries, sub_bytes, sub_dirs, sub_totals = sub
                                    local_entries += sub_entries
                                    local_bytes += sub_bytes
                                    if depth == 0:
                                        local_direct_dirs += 1
                                        local_child_totals[child.name] = {
                                            "logical_bytes": sub_bytes,
                                            "entries": sub_entries,
                                        }
                                    else:
                                        local_direct_dirs += sub_dirs
                                        local_child_totals.update(sub_totals)
                                if "STORAGE_ENTRY_LIMIT" in issues:
                                    break
                            else:
                                mark_issue("STORAGE_SPECIAL_ENTRY")
            except OSError as exc:
                if exc.errno in _CONCURRENT_ERRNOS:
                    mark_issue("STORAGE_CONCURRENT_CHANGE")
                else:
                    mark_issue("STORAGE_ROOT_UNREADABLE" if is_root else "STORAGE_UNREADABLE_ENTRY")
                return None
            # Always verify directory identity before committing staged subtree counts,
            # including when truncated by entry or depth bounds.
            try:
                after = os.lstat(path)
            except OSError:
                mark_issue("STORAGE_CONCURRENT_CHANGE")
                return None
            if _dir_signature(after) != _dir_signature(opened_info):
                mark_issue("STORAGE_CONCURRENT_CHANGE")
                return None
            return local_entries, local_bytes, local_direct_dirs, local_child_totals

        root_totals = scan_dir_path(scan_root, root_info, 0, is_root=True)
        if root_totals is not None:
            entries_ok, bytes_ok, direct_dirs_ok, child_totals_ok = root_totals
            result["observed_entries"] = entries_ok
            result["observed_logical_bytes"] = bytes_ok
            result["direct_child_dirs"] = direct_dirs_ok
            result["child_dir_totals"] = child_totals_ok

    result["issues"] = sorted(issues)
    return result


def workspace_query(workspace, trash_root=None, *, limit=DEFAULT_LIMIT,
                    measure_storage=False,
                    max_measure_entries=DEFAULT_MEASURE_ENTRIES):
    """Read one bounded metadata snapshot; limits do not authorize mutations."""
    if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
        raise ValueError("query limit must be an integer between 1 and 50")
    if type(measure_storage) is not bool:
        raise ValueError("measure_storage must be a boolean")
    if (type(max_measure_entries) is not int
            or not 1 <= max_measure_entries <= MAX_MEASURE_ENTRIES):
        raise ValueError(
            f"max_measure_entries must be an integer between 1 and {MAX_MEASURE_ENTRIES}")
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

    storage_complete = True
    if not measure_storage:
        storage = {
            "location": location,
            "measured": False,
            "logical_bytes": None,
            "physical_freed_bytes": None,
            "notice": "No recursive payload/size scan. Request the existing GC plan explicitly for its detailed best-effort inventory.",
        }
    else:
        measured = _measure_quarantine(trash_root, max_entries=max_measure_entries)
        storage_complete = measured["complete"]
        total_bytes = measured["observed_logical_bytes"] if storage_complete else None
        total_entries = measured["observed_entries"] if storage_complete else None

        admission_ratio = None
        ratio_reason = None
        if not storage_complete:
            ratio_reason = "INCOMPLETE_MEASUREMENT"
        elif budget.get("status") == "BLOCKED" or not isinstance(budget.get("limits"), dict):
            ratio_reason = "LIMITS_UNAVAILABLE"
        else:
            limits = budget["limits"]
            max_b = limits.get("quarantine_max_bytes")
            max_e = limits.get("quarantine_max_entries")
            if type(max_b) is not int or type(max_e) is not int:
                ratio_reason = "LIMITS_UNAVAILABLE"
            else:
                admission_ratio = {
                    "bytes_ratio": (round(total_bytes / max_b, 6) if max_b > 0 else None),
                    "entries_ratio": (round(total_entries / max_e, 6) if max_e > 0 else None),
                    "quarantine_max_bytes": max_b,
                    "quarantine_max_entries": max_e,
                }

        top_transactions = None
        top_reason = None
        if not storage_complete:
            top_reason = "INCOMPLETE_MEASUREMENT"
        elif not manifest["complete"]:
            top_reason = "MANIFEST_INCOMPLETE"
        elif manifest.get("missing"):
            if not measured["root_exists"] or measured["direct_child_dirs"] == 0:
                top_transactions = []
            else:
                top_reason = "MANIFEST_MISSING"
        else:
            attributed = []
            for txid, stats in measured["child_dir_totals"].items():
                tx = transactions.get(txid)
                if tx is None or tx.get("purged"):
                    continue
                attributed.append({
                    "txid": txid,
                    "state": tx["state"],
                    "logical_bytes": stats["logical_bytes"],
                    "entries": stats["entries"],
                })
            attributed.sort(key=lambda item: (-item["logical_bytes"], -item["entries"], item["txid"]))
            top_transactions = attributed[:TOP_TRANSACTION_LIMIT]

        storage = {
            "location": location,
            "measured": True,
            "complete": storage_complete,
            "truncated": measured["truncated"],
            "logical_bytes": total_bytes,
            "entries": total_entries,
            "observed_logical_bytes": measured["observed_logical_bytes"],
            "observed_entries": measured["observed_entries"],
            "attempted_entries": measured["attempted_entries"],
            "max_entries": measured["max_entries"],
            "max_depth": measured["max_depth"],
            "issues": measured["issues"],
            "admission_ratio": admission_ratio,
            "admission_ratio_unavailable_reason": ratio_reason,
            "top_transactions": top_transactions,
            "top_transactions_unavailable_reason": top_reason,
            "physical_freed_bytes": None,
            "notice": (
                "Read-only bounded lstat measurement matching Core admission accounting "
                "(logical bytes including directory metadata; hardlinks counted per entry; "
                "internal links not followed; not physical disk space)."
            ),
        }

    complete = (location["complete"] and manifest["complete"]
                and tasks["coverage"]["complete"] and budget.get("status") != "BLOCKED"
                and storage_complete)
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
            "storage": storage,
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
