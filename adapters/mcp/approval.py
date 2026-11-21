"""Internal worker for an operator-selected host confirmation channel.

The MCP server calls this worker with its fixed environment, never model
approval arguments. Local receipts are advisory against same-UID processes.
No action here deletes targets; execution remains a separate Core task.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import time

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core import admission, recovery
from core.cleanup import _digest
from core.cleanup_saved import _read_json, load_saved_plan, refresh_saved_plan
from core.cleanup_review import review_path, write_review
from core.cleanup_tasks import approve_task, task_status
from core.cleanup_views import read_options, task_view
from core.delete_config import CONFIG_DIRECTORY


SCHEMA = "cleanup_host_approval/v1"
REQUEST_SECONDS = 300
STATES = {"PENDING", "ISSUING", "APPROVED", "DECLINED", "CANCELLED", "TIMED_OUT",
          "UNAVAILABLE", "INVALID_RESPONSE", "EXPIRED", "UNKNOWN"}


def _directory(workspace, name):
    path = Path(workspace) / CONFIG_DIRECTORY / name
    if path.resolve() != path:
        raise ValueError("host approval storage must not use directory links")
    return path


def _binding(workspace, saved_id):
    saved = load_saved_plan(workspace, saved_id)
    trash = os.environ["AGENT_GUARD_TRASH"]
    if saved["plan"]["trash_root"] != trash:
        raise ValueError("keep the saved plan's quarantine binding")
    binding = {"workspace": workspace, "trash_root": trash,
               "session": admission._session_key(), "saved_plan_id": saved_id}
    path = _directory(workspace, "cleanup-approvals") / (_digest(binding) + ".json")
    return saved, binding, path


def _read(path, binding):
    if not os.path.lexists(path):
        return None
    record = _read_json(path)
    if (not isinstance(record, dict) or record.get("schema") != SCHEMA
            or any(record.get(key) != value for key, value in binding.items())
            or not isinstance(record.get("status"), str) or record["status"] not in STATES
            or any(not isinstance(record.get(key), str) or not re.fullmatch(r"[0-9a-f]{32}", record[key])
                   for key in ("request_id", "task_id"))
            or any(type(record.get(key)) not in (int, float) or not math.isfinite(record[key])
                   for key in ("created", "expires"))):
        raise ValueError("invalid host approval receipt; inspect state before requesting new work")
    return record


def _write(path, record, *, creating=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    ignore = path.parent / ".gitignore"
    if not os.path.lexists(ignore):
        with ignore.open("x", encoding="utf-8") as output:
            output.write("*\n")
    target = path if creating else path.with_suffix(".json.tmp")
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if creating else os.O_TRUNC)
    descriptor = os.open(target, flags | getattr(os, "O_NOFOLLOW", 0)
                         | getattr(os, "O_NONBLOCK", 0), 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        if not stat.S_ISREG(os.fstat(output.fileno()).st_mode):
            raise OSError("host approval receipt must be a regular file")
        json.dump(record, output, ensure_ascii=True, indent=2)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    if not creating:
        os.replace(target, path)
    if os.name == "posix":
        descriptor = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _review(saved, *, create=False):
    """Export the whole saved snapshot, never a filtered or truncated page."""
    plan = saved["plan"]
    path = review_path(plan["workspace"], saved["saved_plan_id"])
    selection = set(saved["selection"])
    selected = [row for row in plan["entries"] if row["path"] in selection]
    overview = {"selected_roots": len(selected),
                "quarantine_roots": sum(row["effect"] == "quarantine" for row in selected),
                "permanent_delete_roots": sum(row["effect"] == "delete" for row in selected),
                "known_logical_bytes": sum(row["size_bytes"] or 0 for row in selected),
                "unknown_size_roots": sum(row["size_bytes"] is None for row in selected),
                "complete": plan["complete"]}
    if create:
        header = {"saved_plan_id": saved["saved_plan_id"], "plan_id": plan["plan_id"],
                  "workspace": plan["workspace"], "trash_root": plan["trash_root"],
                  "requested_targets": plan["targets"], "selection": saved["selection"],
                  "overview": overview, "warnings": plan["warnings"], "errors": plan["errors"]}
        rows = [{**row, "selected": row["path"] in selection} for row in plan["entries"]]
        text = ("# Cleanup plan review\n\nSaved snapshot; not live readiness or approval.\n"
                "Only rows marked selected are proposed for execution.\n"
                "Quarantine retains data on disk. Permanent deletion has no recovery copy.\n"
                "Logical bytes are estimates, not freed space or admission charges.\n\n"
                "```json\n" + json.dumps(header, ensure_ascii=False, indent=2)
                + "\n```\n\n## Complete candidate list\n\n```jsonl\n"
                + "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n```\n")
        write_review(path, text)
    return {"file": str(path) if path.is_file() and not path.is_symlink() else None,
            "overview": overview,
            "details": {"name": "cleanup_plan", "arguments": {
                "saved_plan_id": saved["saved_plan_id"], "view": "details", "offset": 0, "limit": 200}}}


def _public(saved, record):
    result = {"schema": SCHEMA, "channel": "codex", "saved_plan_id": saved["saved_plan_id"],
              "request_status": record["status"] if record else "NOT_REQUESTED",
              "review": _review(saved), "human_verified": False, "operation": "approval_only",
              "notice": "Operator-selected host channel, not independent proof of a human action. "
                        "Repeated requests reuse this receipt; no automatic re-prompt or execution. "
                        "Inspect the task after response loss. Terminal approval remains available."}
    if record:
        result.update({key: record[key] for key in ("request_id", "created", "expires")})
        if record["status"] == "PENDING" and time.time() > record["expires"]:
            result["request_status"] = "EXPIRED"
        if record["status"] in {"ISSUING", "APPROVED", "UNKNOWN"}:
            result["task_id"] = record["task_id"]
            result["query"] = {"name": "cleanup_task_status", "arguments": {"task_id": record["task_id"]}}
            try:
                result["task"] = task_view(task_status(saved["plan"]["workspace"], record["task_id"]),
                                           read_options("task"))
            except FileNotFoundError:
                result["task"] = None  # May have failed between grant and task persistence.
        if "error" in record:
            result["error"] = record["error"]
    return result


def inspect_request(workspace, saved_id, *, export=False):
    saved, binding, path = _binding(workspace, saved_id)
    if export:
        recovery.RecoveryEngine(workspace, binding["trash_root"]).ensure_layout()
        with admission._locked(binding["trash_root"]):
            _review(saved, create=True)
    return _public(saved, _read(path, binding))


def prepare_request(workspace, saved_id):
    saved, binding, path = _binding(workspace, saved_id)
    existing = _read(path, binding)
    if existing:
        return {**_public(saved, existing), "prompt_required": False}
    refresh_saved_plan(saved)
    recovery.RecoveryEngine(workspace, binding["trash_root"]).ensure_layout()
    with admission._locked(binding["trash_root"]):
        existing = _read(path, binding)
        if existing:
            return {**_public(saved, existing), "prompt_required": False}
        _review(saved, create=True)
        now = time.time()
        record = {"schema": SCHEMA, **binding, "status": "PENDING",
                  "request_id": secrets.token_hex(16), "task_id": secrets.token_hex(16),
                  "created": now, "expires": now + REQUEST_SECONDS}
        _write(path, record, creating=True)
    return {**_public(saved, record), "prompt_required": True}


def finish_request(workspace, saved_id, request_id, outcome):
    """Only the async MCP handler supplies an outcome, after its own request."""
    saved, binding, path = _binding(workspace, saved_id)
    if outcome not in {"approve", "DECLINED", "CANCELLED", "TIMED_OUT", "UNAVAILABLE", "INVALID_RESPONSE"}:
        raise ValueError("unsupported host confirmation outcome")
    with admission._locked(binding["trash_root"]):
        record = _read(path, binding)
        if record is None or record["request_id"] != request_id:
            raise ValueError("host confirmation does not match the pending request")
        if record["status"] != "PENDING":
            return _public(saved, record)
        record["status"] = ("EXPIRED" if time.time() > record["expires"] else
                            "ISSUING" if outcome == "approve" else outcome)
        _write(path, record)
    if record["status"] == "ISSUING":
        try:
            if _review(saved)["file"] is None:
                raise ValueError("the complete review file is missing; review a fresh proposal")
            _review(saved, create=True)
            # Refreshes the very same saved selection after the host reply.
            # A pre-associated task ID makes interrupted issuance queryable.
            approve_task(saved, task_id=record["task_id"])
            record["status"] = "APPROVED"
        except (OSError, ValueError) as exc:
            record.update(status="UNKNOWN", error={"code": "MCP_APPROVAL_RECHECK_FAILED", "message": str(exc)})
        with admission._locked(binding["trash_root"]):
            _write(path, record)
    return _public(saved, record)


def main():
    action, saved_id, *remaining = sys.argv[1:]
    workspace = os.environ["AGENT_GUARD_WORKSPACE"]
    try:
        if action in {"inspect", "review"}:
            result = inspect_request(workspace, saved_id, export=action == "review")
        elif action == "prepare":
            result = prepare_request(workspace, saved_id)
        elif action == "finish":
            result = finish_request(workspace, saved_id, *remaining)
        else:
            raise ValueError("unknown internal host approval operation")
    except (OSError, ValueError) as exc:
        result = {"error": {"code": "MCP_APPROVAL_STATE", "message": str(exc)}}
    print(json.dumps(result, ensure_ascii=True))
    return 1 if "error" in result else 0


if __name__ == "__main__":
    raise SystemExit(main())
