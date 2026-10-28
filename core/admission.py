"""Shared, recoverable deletion admission; no automatic purging or refunds.

Limits describe admitted work, not successful deletions or physical disk
quotas. The portable files and session identity remain advisory to same-UID
processes. A lock covers measurement, durable debit and the guarded mutation.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
import errno
import hashlib
import json
import math
import os
import secrets
import stat
import time

from . import classifier
from .delete_config import DeleteConfigError, fingerprint, load_config, protected_reason


STATE_NAME = "admission.json"
LOCK_NAME = "admission.lock"
MAX_SCAN_ENTRIES = 200_000
MAX_STATE_BYTES = 8 * 1024 * 1024
MAX_SESSIONS = 1024
APPROVAL_SECONDS = 600


class AdmissionDenied(OSError):
    def __init__(self, code, reason):
        super().__init__(code + ": " + reason)
        self.code, self.reason = code, reason


def _digest(value):
    raw = json.dumps(value, ensure_ascii=True, sort_keys=True,
                     separators=(",", ":")).encode("ascii")
    return hashlib.sha256(raw).hexdigest()


def _session_key():
    return _digest(os.environ.get("AGENT_GUARD_SESSION") or "<shared-cli-session>")


def _location(path):
    absolute = os.path.normpath(os.path.abspath(path))
    parent, leaf = os.path.split(absolute)
    return os.path.normcase(os.path.join(os.path.realpath(parent), leaf))


def _roots(paths):
    result = []
    for path in sorted({_location(path) for path in paths}, key=lambda p: (len(p), p)):
        if not any(classifier.inside_path(path, parent) for parent in result):
            result.append(path)
    return result


def _signature(info):
    return [info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns]


def _measure(paths, *, max_entries=MAX_SCAN_ENTRIES):
    """Bounded lstat inventory; links are entries, never traversal edges."""
    entries, size = 0, 0
    trees = {}

    def visit(path, depth):
        nonlocal entries, size
        if entries >= max_entries or depth > 128:
            raise OSError("deletion inventory exceeds its scan bound")
        info = os.lstat(path)
        entries += 1
        # Logical lengths, including directory metadata. Hardlinks are
        # deliberately counted per entry; this is a conservative admission.
        size += info.st_size
        children = []
        if stat.S_ISDIR(info.st_mode):
            with os.scandir(path) as listing:
                bounded = []
                for child in listing:
                    if len(bounded) >= max_entries - entries:
                        raise OSError("deletion inventory exceeds its scan bound")
                    bounded.append(child)
                for child in sorted(bounded, key=lambda row: row.name):
                    children.append([child.name, visit(child.path, depth + 1)])
            if _signature(os.lstat(path)) != _signature(info):
                raise OSError("deletion source changed during inventory")
        elif not (stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)):
            raise OSError("special filesystem entries require manual review")
        return _digest([_signature(info), children])

    for path in _roots(paths):
        if os.path.lexists(path):
            trees[_digest(path)] = visit(path, 0)
    return {"entries": entries, "bytes": size,
            "fingerprint": _digest(trees), "trees": trees}


def _config(workspace):
    try:
        return load_config(workspace)
    except DeleteConfigError as exc:
        raise AdmissionDenied("BLOCK_DELETE_CONFIG_INVALID", str(exc)) from exc


def _check_protected(paths, workspace, config):
    reason = protected_reason(paths, workspace, config)
    if reason:
        raise AdmissionDenied("BLOCK_DELETE_PROTECTED", reason)


@contextmanager
def _locked(trash_root):
    """Portable process lock, with a bounded wait and no third-party module."""
    os.makedirs(trash_root, exist_ok=True)
    path = os.path.join(trash_root, LOCK_NAME)
    if os.path.islink(path):
        raise OSError("deletion admission lock must not be a link")
    fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    acquired = False
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("deletion admission lock must be a regular file")
        if os.name == "nt":
            import msvcrt
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\0")

            def lock():
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

            def unlock():
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            def lock():
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

            def unlock():
                fcntl.flock(fd, fcntl.LOCK_UN)
        deadline = time.monotonic() + 10
        while True:
            try:
                lock()
                acquired = True
                break
            except OSError as exc:
                if exc.errno not in (errno.EAGAIN, errno.EACCES, errno.EDEADLK):
                    raise
                if time.monotonic() >= deadline:
                    raise OSError("deletion admission lock timed out") from exc
                time.sleep(0.05)
        yield
    finally:
        if acquired:
            unlock()
        os.close(fd)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate admission state field")
        result[key] = value
    return result


def _load(trash_root):
    path = os.path.join(trash_root, STATE_NAME)
    if not os.path.lexists(path):
        return {"version": 1, "sessions": {}, "approvals": {}}
    if os.path.islink(path) or not os.path.isfile(path):
        raise ValueError("admission state must be a regular file")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
                 | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise ValueError("admission state must be a regular file")
        raw = handle.read(MAX_STATE_BYTES + 1)
    if len(raw) > MAX_STATE_BYTES:
        raise ValueError("admission state is too large")
    data = json.loads(raw, object_pairs_hook=_unique_object)
    if (not isinstance(data, dict) or data.get("version") != 1
            or not isinstance(data.get("sessions"), dict)
            or not isinstance(data.get("approvals"), dict)
            or len(data["sessions"]) > MAX_SESSIONS or len(data["approvals"]) > 32):
        raise ValueError("invalid admission state")
    for key, record in data["sessions"].items():
        if not isinstance(key, str) or not isinstance(record, dict):
            raise ValueError("invalid session budget")
        if any(type(record.get(field)) is not int or record[field] < 0
               for field in ("used_bytes", "used_entries")):
            raise ValueError("invalid session counters")
        if not isinstance(record.get("events"), list) or len(record["events"]) > 10_000:
            raise ValueError("invalid admission window")
        for event in record["events"]:
            if (not isinstance(event, dict) or type(event.get("at")) not in (int, float)
                    or not math.isfinite(event["at"]) or event["at"] < 0
                    or not isinstance(event.get("targets"), str)):
                raise ValueError("invalid admission event")
    for key, record in data["approvals"].items():
        if (not isinstance(key, str) or not isinstance(record, dict)
                or not isinstance(record.get("session"), str)
                or not isinstance(record.get("config"), str)
                or type(record.get("expires")) not in (int, float)
                or not math.isfinite(record["expires"])
                or not isinstance(record.get("paths"), dict)
                or len(record["paths"]) > 10_000
                or any(not isinstance(k, str) or not isinstance(v, str)
                       for k, v in record["paths"].items())):
            raise ValueError("invalid cleanup approval")
    return data


def _save(trash_root, state):
    raw = (json.dumps(state, ensure_ascii=True, sort_keys=True) + "\n").encode("ascii")
    if len(raw) > MAX_STATE_BYTES:
        raise OSError("admission state exceeds its storage bound")
    temporary = os.path.join(trash_root, STATE_NAME + ".tmp")
    if os.path.islink(temporary):
        raise OSError("admission temporary state must not be a link")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC
                 | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise OSError("admission temporary state must be a regular file")
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, os.path.join(trash_root, STATE_NAME))


def _empty_session():
    return {"used_bytes": 0, "used_entries": 0, "events": []}


def check_batch(workspace, paths):
    """Read-only combined shell-request preflight, before any compensation."""
    config = _config(workspace)
    paths = _roots(paths)
    _check_protected(paths, workspace, config)
    try:
        measured = _measure(paths)
    except OSError as exc:
        raise AdmissionDenied("BLOCK_DELETE_STATE_INVALID", "cannot safely inventory the deletion batch") from exc
    if (measured["bytes"] > config.batch_max_bytes
            or measured["entries"] > config.batch_max_entries):
        raise AdmissionDenied("BLOCK_DELETE_BATCH_CONFIRM", "large deletion requires a reviewed Cleanup plan and interactive execution confirmation")
    return measured


@contextmanager
def admit(workspace, trash_root, paths, *, quarantine=True, approval=None):
    """Debit once before yielding; a failed guarded operation is not refunded."""
    config = _config(workspace)
    paths = _roots(paths)
    _check_protected(paths, workspace, config)
    executing = False
    try:
        with _locked(trash_root):
            state = _load(trash_root)
            measured = _measure(paths)
            if not measured["entries"]:
                executing = True
                yield measured
                return
            now, sid = time.time(), _session_key()
            record = state["sessions"].get(sid, _empty_session())
            events = [event for event in record["events"]
                      if event["at"] > now - config.rate_window_seconds]
            targets = _digest(paths)
            if len(events) >= min(config.rate_max_operations, 10_000):
                raise AdmissionDenied("BLOCK_DELETE_RATE", "deletion admission rate exceeded; wait for the window to expire")
            if sum(event["targets"] == targets for event in events) >= config.repeat_max_operations:
                raise AdmissionDenied("BLOCK_DELETE_REPEAT", "repeated admission of the same target set; inspect the delete/recreate loop")
            if (record["used_bytes"] + measured["bytes"] > config.session_max_bytes
                    or record["used_entries"] + measured["entries"] > config.session_max_entries):
                raise AdmissionDenied("BLOCK_DELETE_LIMIT", "session deletion budget exhausted; inspect status and explicitly reset or adjust policy")
            if sid not in state["sessions"] and len(state["sessions"]) >= MAX_SESSIONS:
                raise AdmissionDenied("BLOCK_DELETE_LIMIT", "session budget inventory is full; operator review is required")
            if quarantine:
                usage = _measure([trash_root])
                if (usage["bytes"] + measured["bytes"] > config.quarantine_max_bytes
                        or usage["entries"] + measured["entries"] > config.quarantine_max_entries):
                    raise AdmissionDenied("BLOCK_DELETE_LIMIT", "quarantine admission capacity exceeded; inspect existing recovery copies and the explicit GC plan")
            token = approval or os.environ.get("AGENT_GUARD_CLEANUP_APPROVAL")
            authorized = False
            if token:
                grant = state["approvals"].get(_digest(token))
                authorized = bool(grant and grant["expires"] >= now
                                  and grant["session"] == sid
                                  and grant["config"] == fingerprint(config)
                                  and all(grant["paths"].get(key) == value
                                          for key, value in measured["trees"].items()))
                if not authorized:
                    raise AdmissionDenied("BLOCK_DELETE_BATCH_CONFIRM", "cleanup approval expired, changed or already consumed; review a fresh plan")
            if (measured["bytes"] > config.batch_max_bytes
                    or measured["entries"] > config.batch_max_entries) and not authorized:
                raise AdmissionDenied("BLOCK_DELETE_BATCH_CONFIRM", "large deletion requires a reviewed Cleanup plan and interactive execution confirmation")
            if authorized:
                for key in measured["trees"]:
                    del grant["paths"][key]
            record = {"used_bytes": record["used_bytes"] + measured["bytes"],
                      "used_entries": record["used_entries"] + measured["entries"],
                      "events": events + [{"at": now, "targets": targets}]}
            state["sessions"][sid] = record
            state["approvals"] = {key: value for key, value in state["approvals"].items()
                                  if value["expires"] >= now and value["paths"]}
            _save(trash_root, state)
            # Keep the process lock while executing. It does not isolate
            # unrelated filesystem writers or authenticated host approval.
            executing = True
            yield measured
    except AdmissionDenied:
        raise
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        if executing:
            raise
        raise AdmissionDenied("BLOCK_DELETE_STATE_INVALID", "cannot safely measure, lock or persist deletion admission") from exc


def approve_cleanup(workspace, trash_root, paths, plan_id):
    """Called only by Cleanup after its terminal confirmation and rescan."""
    config = _config(workspace)
    paths = _roots(paths)
    _check_protected(paths, workspace, config)
    try:
        with _locked(trash_root):
            state = _load(trash_root)
            measured = _measure(paths)
            now = time.time()
            state["approvals"] = {key: value for key, value in state["approvals"].items()
                                  if value["expires"] >= now and value["paths"]}
            if len(state["approvals"]) >= 32:
                raise AdmissionDenied("BLOCK_DELETE_LIMIT", "too many outstanding cleanup confirmations")
            token = secrets.token_hex(32)
            state["approvals"][_digest(token)] = {
                "session": _session_key(), "config": fingerprint(config),
                "expires": now + APPROVAL_SECONDS, "paths": measured["trees"],
                "plan_id": plan_id}
            _save(trash_root, state)
            return token
    except AdmissionDenied:
        raise
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        raise AdmissionDenied("BLOCK_DELETE_STATE_INVALID", "cannot persist cleanup confirmation") from exc


def describe(workspace, trash_root):
    """Read-only current-session counters; never creates a lock or state."""
    config = _config(workspace)
    try:
        state = _load(trash_root)
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        raise AdmissionDenied("BLOCK_DELETE_STATE_INVALID", "cannot read deletion admission state") from exc
    record = state["sessions"].get(_session_key(), _empty_session())
    limits = asdict(config)
    limits.pop("protected_paths")
    return {"used_bytes": record["used_bytes"], "used_entries": record["used_entries"],
            "window_operations": sum(event["at"] > time.time() - config.rate_window_seconds
                                     for event in record["events"]),
            "limits": limits, "config_fingerprint": fingerprint(config),
            "protected_path_count": len(config.protected_paths),
            "accounting": "admitted work; failures and restores do not refund session budget"}


def reset_session(workspace, trash_root):
    """Operator entry point; CLI requires explicit interactive confirmation."""
    _config(workspace)
    try:
        with _locked(trash_root):
            state = _load(trash_root)
            sid = _session_key()
            state["sessions"].pop(sid, None)
            state["approvals"] = {key: value for key, value in state["approvals"].items()
                                  if value["session"] != sid}
            _save(trash_root, state)
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        raise AdmissionDenied("BLOCK_DELETE_STATE_INVALID", "cannot reset deletion admission state") from exc
