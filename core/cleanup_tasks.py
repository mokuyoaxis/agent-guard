"""Operator-approved, single-attempt cleanup tasks over the safe_delete CLI.

Task files are local control receipts, not a same-UID security boundary.
Query never rescans sources, creates state, or retries unfinished work.
"""
from __future__ import annotations

import glob
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import time

from . import TRASH_DIRNAME, admission, recovery
from .cleanup_saved import MAX_SAVED_BYTES, _read_json, load_saved_plan, refresh_saved_plan
from .delete_config import CONFIG_DIRECTORY, fingerprint, load_config


SCHEMA = "cleanup_task/v1"
STATES = {"APPROVED", "RUNNING", "COMPLETED", "PARTIAL", "STOPPED", "UNKNOWN"}
GROUP_STATES = {"PENDING", "RUNNING", "COMPLETED", "STOPPED", "UNKNOWN"}


class TaskStateError(OSError):
    def __init__(self, task_id, may_have_changed):
        super().__init__("cannot persist cleanup task " + task_id
                         + "; inspect its status and recovery transactions before any new attempt")
        self.task_id = task_id
        self.may_have_changed = may_have_changed


def _store(workspace):
    store = Path(os.path.realpath(workspace)) / CONFIG_DIRECTORY / "cleanup-tasks"
    if store.resolve() != store:
        raise ValueError("the task directory must not use directory links")
    return store


def _path(workspace, task_id):
    if re.fullmatch(r"[0-9a-f]{32}", task_id) is None:
        raise ValueError("--task requires a cleanup task ID")
    return _store(workspace) / (task_id + ".json")


def _read_task(workspace, task_id, *, max_bytes=MAX_SAVED_BYTES):
    """Validate stored data; callers must enforce the budget/quarantine binding."""
    task = _read_json(_path(workspace, task_id), max_bytes=max_bytes)
    if (not isinstance(task, dict) or task.get("schema") != SCHEMA
            or task.get("task_id") != task_id or not isinstance(task.get("status"), str)
            or task["status"] not in STATES
            or task.get("workspace") != os.path.realpath(workspace)
            or not all(isinstance(task.get(k), str) for k in (
                "trash_root", "session", "config", "approval_token", "saved_plan_id", "plan_id"))
            or type(task.get("expires")) not in (int, float)
            or not math.isfinite(task["expires"])
            or not isinstance(task.get("groups"), list) or not 1 <= len(task["groups"]) <= 2):
        raise ValueError("invalid cleanup task")
    for group in task["groups"]:
        if (not isinstance(group, dict) or group.get("effect") not in ("quarantine", "delete")
                or not isinstance(group.get("status"), str) or group["status"] not in GROUP_STATES
                or not isinstance(group.get("paths"), list) or not group["paths"]
                or not all(isinstance(p, str) and p for p in group["paths"])
                or not isinstance(group.get("confirmed"), list)
                or not all(isinstance(p, str) for p in group["confirmed"])
                or not set(group["confirmed"]).issubset(group["paths"])):
            raise ValueError("invalid cleanup task group")
    return task


def _load(workspace, task_id):
    task = _read_task(workspace, task_id)
    trash = os.environ.get("AGENT_GUARD_TRASH", os.path.join(task["workspace"], TRASH_DIRNAME))
    if (os.path.abspath(trash) != task["trash_root"]
            or admission._session_key() != task["session"]):
        raise ValueError("keep the approved workspace, quarantine and session settings")
    return task


def _write(task, *, creating=False):
    """Caller holds admission's lock; persist before dispatch and after receipt."""
    path = _path(task["workspace"], task["task_id"])
    raw = (json.dumps(task, ensure_ascii=True, indent=2) + "\n").encode("utf-8")
    if len(raw) > MAX_SAVED_BYTES:
        raise OSError("cleanup task exceeds its size limit")
    path.parent.mkdir(parents=True, exist_ok=True)
    ignore = path.parent / ".gitignore"
    if not os.path.lexists(ignore):
        with ignore.open("x", encoding="utf-8") as output:
            output.write("*\n")
    destination = path if creating else path.with_suffix(".json.tmp")
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if creating else os.O_TRUNC)
    descriptor = os.open(destination, flags | getattr(os, "O_NOFOLLOW", 0)
                         | getattr(os, "O_NONBLOCK", 0), 0o600)
    with os.fdopen(descriptor, "wb") as output:
        if not stat.S_ISREG(os.fstat(output.fileno()).st_mode):
            raise OSError("cleanup task must be a regular file")
        output.write(raw)
        output.flush()
        os.fsync(output.fileno())
    if not creating:
        os.replace(destination, path)
    if os.name == "posix":
        descriptor = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _groups(selected):
    return [{"effect": effect, "paths": [r["path"] for r in selected if r["effect"] == effect],
             "status": "PENDING", "confirmed": []}
            for effect in ("quarantine", "delete") if any(r["effect"] == effect for r in selected)]


def approve_task(saved):
    """Operator entry only: CLI has just obtained real terminal confirmation."""
    plan, selected = refresh_saved_plan(saved)
    task_id = secrets.token_hex(16)
    effects = {os.path.join(plan["workspace"], r["path"]): r["effect"] for r in selected}
    recovery.RecoveryEngine(plan["workspace"], plan["trash_root"]).ensure_layout()
    token = admission.approve_cleanup(plan["workspace"], plan["trash_root"],
                                     list(effects), plan["plan_id"], task_id=task_id, effects=effects)
    with admission._locked(plan["trash_root"]):
        grant = admission._load(plan["trash_root"])["approvals"].get(admission._digest(token))
        if grant is None or grant["expires"] < time.time():
            raise ValueError("confirmation expired before task creation; review the plan again")
        task = {"schema": SCHEMA, "task_id": task_id, "saved_plan_id": saved["saved_plan_id"],
                "plan_id": plan["plan_id"], "workspace": plan["workspace"],
                "trash_root": plan["trash_root"], "session": grant["session"], "config": grant["config"],
                "approval_token": token, "expires": grant["expires"], "created": time.time(),
                "status": "APPROVED", "groups": _groups(selected)}
        _write(task, creating=True)
    return task_status(plan["workspace"], task_id)


def _approval_status(task):
    if time.time() > task["expires"]:
        return "EXPIRED"
    try:
        if fingerprint(load_config(task["workspace"])) != task["config"]:
            return "CONFIG_CHANGED"
        grant = admission._load(task["trash_root"])["approvals"].get(admission._digest(task["approval_token"]))
    except (OSError, ValueError):
        return "STATE_UNAVAILABLE"
    if not grant or not grant["paths"]:
        return "MISSING_OR_CONSUMED"
    matches = (grant.get("task") == task["task_id"] and grant["session"] == task["session"]
               and grant["config"] == task["config"] and grant["expires"] >= time.time()
               and grant.get("plan_id") == task["plan_id"])
    return "CURRENT" if matches else "INVALID"


def _public(task):
    approval = _approval_status(task)
    confirmed = sum(len(g["confirmed"]) for g in task["groups"])
    total = sum(len(g["paths"]) for g in task["groups"])
    groups = []
    for group in task["groups"]:
        confirmed_paths = set(group["confirmed"])
        groups.append({**group, "unconfirmed": [p for p in group["paths"] if p not in confirmed_paths]})
    return {"tool": "cleanup", "task_id": task["task_id"], "saved_plan_id": task["saved_plan_id"],
            "plan_id": task["plan_id"], "workspace": task["workspace"], "trash_root": task["trash_root"],
            "status": task["status"], "approval_status": approval, "expires": task["expires"],
            "can_execute": task["status"] == "APPROVED" and approval == "CURRENT",
            "counts": {"targets": total, "confirmed": confirmed, "unconfirmed": total - confirmed,
                       "not_dispatched": sum(len(g["paths"]) for g in task["groups"] if g["status"] == "PENDING")},
            "groups": groups, "error": task.get("error"), "preflight": task.get("preflight"),
            "may_have_changed": task["status"] in {"RUNNING", "PARTIAL", "UNKNOWN"} or confirmed > 0,
            "notice": "Last durable receipts, not a live-process check. can_execute means claimable approval; sources, budgets, rate and capacity are checked at execution. One attempt per task; inspect unresolved results before planning remaining targets. No automatic resume or retry.",
            "exit": 0 if task["status"] in {"APPROVED", "COMPLETED"} else 2}


def task_status(workspace, task_id):
    """Read only, including when a dispatcher died after writing its intent."""
    result = _public(_load(workspace, task_id))
    result["exit"] = 0  # Successful reading does not mean successful cleanup.
    return result


def _persist(task, *, dispatched):
    try:
        with admission._locked(task["trash_root"]):
            _write(task)
    except (OSError, ValueError) as exc:
        raise TaskStateError(task["task_id"], dispatched) from exc


def _receipt(group, proc, workspace):
    try:
        report = json.loads(proc.stdout)
        if not isinstance(report, dict) or report.get("tool") != "safe_delete":
            raise ValueError("not a safe_delete receipt")
        if report.get("dry_run") or report.get("exit") != proc.returncode:
            raise ValueError("not an execution receipt")
        expected = {admission._location(os.path.join(workspace, p)): p for p in group["paths"]}
        if group["effect"] == "quarantine":
            moved = [admission._location(row["origin"]) for row in report.get("moved", [])]
            if not set(moved).issubset(expected) or (moved and not report.get("txid")):
                raise ValueError("relocation receipt does not match the approved targets")
            confirmed = [expected[path] for path in moved]
        else:
            observed = {admission._location(os.path.join(workspace, p)) for p in report.get("targets", [])}
            deleted = report.get("deleted", [])
            complete = (proc.returncode == 0 and report.get("verdict", {}).get("code") == "ALLOW_REGENERABLE"
                        and observed == set(expected) and len(deleted) == len(expected)
                        and all(value in ("deleted", "deleted-tree") for value in deleted))
            confirmed = list(group["paths"]) if complete else []
    except (ValueError, TypeError, KeyError, AttributeError):
        group.update(status="UNKNOWN", error="safe_delete returned no usable receipt; inspect task and recovery transactions",
                     stdout_bytes=len(proc.stdout.encode("utf-8")), stderr_bytes=len(proc.stderr.encode("utf-8")))
        return
    group.update(receipt=report, exit=proc.returncode, confirmed=sorted(set(confirmed)))
    group["status"] = ("COMPLETED" if proc.returncode == 0 and len(group["confirmed"]) == len(group["paths"])
                       else "STOPPED")


def execute_task(workspace, task_id, *, runner=None):
    """Claim once, revalidate, dispatch at most two groups, stop on uncertainty."""
    runner = runner or subprocess.run
    task = _load(workspace, task_id)
    workspace = task["workspace"]
    with admission._locked(task["trash_root"]):
        task = _load(workspace, task_id)
        if task["status"] != "APPROVED":
            return _public(task)
        task.update(status="RUNNING", started=time.time())
        _write(task)
    try:
        saved = load_saved_plan(workspace, task["saved_plan_id"])
        plan, selected = refresh_saved_plan(saved)
        if (plan["plan_id"] != task["plan_id"]
                or _groups(selected) != task["groups"]):
            raise ValueError("approved task no longer matches its saved selection")
        all_paths = [os.path.join(workspace, p) for g in task["groups"] for p in g["paths"]]
        checks = [admission.preview(workspace, task["trash_root"], all_paths, quarantine=None,
                                    approval=task["approval_token"], task_id=task_id)]
        for group in task["groups"]:
            checks.append(admission.preview(workspace, task["trash_root"],
                          [os.path.join(workspace, p) for p in group["paths"]],
                          quarantine=group["effect"] == "quarantine", approval=task["approval_token"], task_id=task_id))
        task["preflight"] = checks
        if any(check["status"] != "READY" for check in checks):
            raise ValueError("current admission refused the approved task; inspect preflight")
    except (OSError, ValueError) as exc:
        task.update(status="STOPPED", error=str(exc))
        _persist(task, dispatched=False)
        return _public(task)
    env = dict(os.environ, AGENT_GUARD_WORKSPACE=task["workspace"], AGENT_GUARD_TRASH=task["trash_root"],
               AGENT_GUARD_CLEANUP_APPROVAL=task["approval_token"], AGENT_GUARD_CLEANUP_TASK=task_id,
               PYTHONDONTWRITEBYTECODE="1")
    script = str(Path(__file__).resolve().parent.parent / "skills/delete-guard/scripts/safe_delete.py")
    deletion_options = ["--quarantine"] if plan["options"].get("quarantine", False) else []
    dispatched = False
    for group in task["groups"]:
        group["status"] = "RUNNING"
        _persist(task, dispatched=dispatched)
        try:
            dispatched = True
            proc = runner([sys.executable, "-B", script, "--json", *deletion_options, "--",
                           *(glob.escape("./" + p) for p in group["paths"])],
                          cwd=task["workspace"], env=env, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        except subprocess.TimeoutExpired:
            group.update(status="UNKNOWN", error="safe_delete timed out; inspect recovery evidence before any new plan")
        except OSError as exc:
            group.update(status="STOPPED", error="safe_delete could not start: " + str(exc))
        else:
            _receipt(group, proc, task["workspace"])
        if group["status"] == "UNKNOWN":
            task["status"] = "UNKNOWN"
        elif group["status"] != "COMPLETED":
            task["status"] = "PARTIAL" if any(g["confirmed"] for g in task["groups"]) else "STOPPED"
        elif all(g["status"] == "COMPLETED" for g in task["groups"]):
            task["status"] = "COMPLETED"
        _persist(task, dispatched=dispatched)
        if group["status"] != "COMPLETED":
            break
    return _public(task)
