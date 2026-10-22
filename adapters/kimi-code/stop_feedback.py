#!/usr/bin/env python3
"""Optional POSIX Kimi Stop hook: report this session's recovery records once.

Reads existing guard audit metadata; never authorizes or executes a command.
Exit 2 asks Kimi for one additional answer after its initial response. The
cursor records emission, not acknowledgement by the model or the user.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import AUDIT_NAME, MANIFEST_NAME, TRASH_DIRNAME, audit, classifier  # noqa: E402
from core.messages import QUARANTINE_NOTICE  # noqa: E402

_TXID = re.compile(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{8}\Z")


def incomplete_compensations(trash, check_ids):
    """Failed audit records omit compensations; correlate existing journal IDs.

    Read only transaction headers and count journal events. Exact target paths,
    commands and failure reasons never enter the feedback projection.
    """
    if not check_ids:
        return {}
    try:
        manifest = (trash / MANIFEST_NAME).open("rb")
    except FileNotFoundError:
        return {}
    records = {}
    with manifest:
        for line in manifest:
            if not line.endswith(b"\n"):
                break
            try:
                row = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if not isinstance(row, dict):
                continue
            txid = row.get("txid")
            if not isinstance(txid, str) or not _TXID.fullmatch(txid):
                continue
            meta = row.get("meta")
            check_id = meta.get("check_id") if isinstance(meta, dict) else None
            related = isinstance(check_id, str) and check_id in check_ids
            if row.get("type") == "tx-start" and related and row.get("strategy") == "relocate":
                records[txid] = {"txid": txid, "strategy": "relocate", "status": "incomplete", "moved": 0}
            elif row.get("type") == "snapshot" and related:
                records[txid] = {"txid": txid, "strategy": "snapshot", "status": "incomplete"}
            elif txid in records:
                if row.get("type") == "relocate":
                    records[txid]["moved"] += 1
    return records


def recovery_message(records):
    """Only projected IDs/counts/statuses, never commands or target paths."""
    lines = ["[agent-guard] Recovery records for this session:"]
    has_files = False
    has_snapshot = False
    incomplete = False
    restore = [sys.executable, str(ROOT / "skills/delete-guard/scripts/restore.py")]
    gc = [sys.executable, str(ROOT / "skills/delete-guard/scripts/gc.py")]
    for item in records:
        snapshot = item["strategy"] == "snapshot"
        incomplete |= item.get("status") != "complete"
        label = "Git snapshot" if snapshot else "File relocation"
        line = f"- {label}: txid={item['txid']}; status={item.get('status', 'unknown')}"
        if not snapshot:
            line += f"; moved={item.get('moved', 0)}; skipped={item.get('skipped', 'not recorded')}"
            has_files |= item.get("moved", 0) > 0
        has_snapshot |= snapshot
        lines.append(line)
        lines.append("  Restore after checking its current state: " +
                     shlex.join(restore + [item["txid"]]))
        if not snapshot and item.get("moved", 0) > 0:
            lines.append("  GC permanent purge after preview: " +
                         shlex.join(gc + ["--execute", "--txid", item["txid"]]))
    lines += [
        "Recorded before Bash, not confirmation that Bash ran.",
        "Inspect current recovery state: " + shlex.join(restore + ["list", "--json"]),
    ]
    if incomplete:
        lines.append("Incomplete or failed records do not guarantee a recoverable copy.")
    if has_files:
        lines += [
            QUARANTINE_NOTICE,
            "Restoring files moves them back; it does not free total disk space.",
            "Already restored or purged records may no longer have a copy in quarantine.",
            "GC preview: " + shlex.join(gc),
        ]
    if has_snapshot:
        lines.append("GC does not remove Git stash snapshots.")
    lines += [
        "Use the same working directory and AGENT_GUARD_WORKSPACE / AGENT_GUARD_TRASH settings.",
        "Supplement or correct the previous answer in at most three short bullets: "
        "recovery result and txid, disk space, and the commands. "
        "Copy commands verbatim on separate lines; do not abbreviate paths or replace txids. "
        "Do not repeat the destructive command or restore/purge data for this feedback.",
    ]
    return "\n".join(lines)


def save_cursor(path, state):
    """Replace the checkpoint atomically while its separate lock stays held."""
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=path.stem + ".", suffix=".tmp", delete=False) as temporary:
        temporary_path = Path(temporary.name)
        try:
            json.dump(state, temporary)
            temporary.flush()
            os.fsync(temporary.fileno())
            os.replace(temporary_path, path)
        finally:
            temporary_path.unlink(missing_ok=True)


def pending_feedback(cwd, session):
    """Consume complete audit lines under a per-workspace/session cursor lock."""
    workspace = classifier.discover_workspace(cwd)
    trash = Path(os.environ.get("AGENT_GUARD_TRASH", str(Path(workspace) / TRASH_DIRNAME)))
    if not trash.is_absolute():
        trash = Path(cwd) / trash
    audit_path = trash / AUDIT_NAME
    try:
        source = audit_path.open("rb")
    except FileNotFoundError:
        return None

    session_key = audit.correlation_id(session)
    identity = json.dumps([os.path.realpath(workspace), str(trash.resolve()), session_key])
    name = hashlib.sha256(identity.encode("utf-8")).hexdigest() + ".json"
    home = Path(os.environ.get("KIMI_CODE_HOME", str(Path.home() / ".kimi-code")))
    cursors = home / "agent-guard-feedback"
    with source:
        # This optional integration is POSIX-only, matching its shell bridge.
        import fcntl

        cursors.mkdir(mode=0o700, parents=True, exist_ok=True)
        flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(cursors / (name + ".lock"), flags | os.O_CREAT, 0o600)
        with os.fdopen(descriptor, "r+", encoding="utf-8") as lock:
            if not stat.S_ISREG(os.fstat(lock.fileno()).st_mode):
                raise ValueError("feedback lock is not a regular file")
            fcntl.flock(lock, fcntl.LOCK_EX)
            info = os.fstat(source.fileno())
            try:
                descriptor = os.open(cursors / name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            except FileNotFoundError:
                offset = 0
            else:
                with os.fdopen(descriptor, "r", encoding="utf-8") as cursor:
                    if not stat.S_ISREG(os.fstat(cursor.fileno()).st_mode):
                        raise ValueError("feedback cursor is not a regular file")
                    saved = json.load(cursor)
                if (not isinstance(saved, dict)
                        or set(saved) != {"device", "inode", "offset"}
                        or any(type(saved[key]) is not int or saved[key] < 0 for key in saved)
                        or saved["device"] != info.st_dev or saved["inode"] != info.st_ino
                        or saved["offset"] > info.st_size):
                    raise ValueError("feedback cursor does not match the append-only audit")
                offset = saved["offset"]
            source.seek(offset)
            records = {}
            failed_checks = set()
            while True:
                line = source.readline()
                if not line or not line.endswith(b"\n"):
                    break  # An incomplete append must be read again next time.
                offset = source.tell()
                try:
                    raw = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue  # Same malformed-line rule as the shared audit reader.
                if not isinstance(raw, dict):
                    continue
                record = audit.project(raw)
                if (record.get("session") != session_key
                        or record.get("event") not in {"enforce-proceed", "enforce-error", "enforce-block"}):
                    continue
                if (not record.get("compensations") and record.get("check_id")
                        and record.get("event") in {"enforce-error", "enforce-block"}):
                    failed_checks.add(record["check_id"])
                for item in record.get("compensations", []):
                    txid = item.get("txid", "")
                    if not _TXID.fullmatch(txid):
                        continue
                    strategy = item.get("strategy")
                    if (strategy == "snapshot" and (item.get("sha") or item.get("status") != "complete")
                            or strategy in {"relocate", "clean-enumerate"}
                            and (item.get("moved", 0) > 0 or item.get("status") != "complete")):
                        records[txid] = item
            for txid, item in incomplete_compensations(trash, failed_checks).items():
                records.setdefault(txid, item)
            message = recovery_message(records.values()) if records else None
            save_cursor(cursors / name, {"device": info.st_dev, "inode": info.st_ino, "offset": offset})
            return message


def main():
    payload = json.load(sys.stdin)
    if (not isinstance(payload, dict) or payload.get("hook_event_name") != "Stop"
            or not isinstance(payload.get("cwd"), str) or not payload["cwd"].strip()
            or not isinstance(payload.get("session_id"), str) or not payload["session_id"].strip()
            or type(payload.get("stop_hook_active")) is not bool):
        raise ValueError("invalid Stop hook payload")
    if payload["stop_hook_active"]:
        return 0
    message = pending_feedback(payload["cwd"], payload["session_id"])
    if message:
        sys.stderr.write(message + "\n")
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, ImportError) as error:
        sys.stderr.write(
            f"[agent-guard] Recovery feedback unavailable ({type(error).__name__}). "
            "Inspect restore.py list from the original workspace; "
            "this does not mean no files were preserved.\n")
        sys.exit(2)
