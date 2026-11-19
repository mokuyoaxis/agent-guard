"""Saved target proposals, never approval; execution refreshes the same scope."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat

from .cleanup import _digest, plan_cleanup, select_entries
from .delete_config import CONFIG_DIRECTORY


SCHEMA = "cleanup_saved_plan/v1"
MAX_SAVED_BYTES = 16 * 1024 * 1024


def _store(workspace):
    root = Path(os.path.realpath(workspace))
    store = root / CONFIG_DIRECTORY / "cleanup-plans"
    if store.resolve() != store:
        raise ValueError("the saved-plan directory must not use directory links")
    return store


def _string_list(value):
    return isinstance(value, list) and all(isinstance(item, str) and item for item in value)


def _read_json(path, *, max_bytes=MAX_SAVED_BYTES):
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
                         | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise ValueError("cleanup JSON inputs must be regular files")
        raw = source.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError("cleanup JSON exceeds its size limit")
    try:
        return json.loads(raw)
    except (ValueError, RecursionError) as exc:
        raise ValueError("cleanup input is not valid JSON") from exc


def read_targets_file(path):
    targets = _read_json(Path(path).expanduser().resolve())
    if not _string_list(targets) or not targets:
        raise ValueError("--targets-file must contain a nonempty JSON array of path strings")
    return targets


def load_saved_plan(workspace, identifier):
    """Read one immutable proposal in the selected workspace's own store."""
    if re.fullmatch(r"[0-9a-f]{64}", identifier) is None:
        raise ValueError("--from-plan requires a saved plan ID")
    path = _store(workspace) / (identifier + ".json")
    record = _read_json(path)
    if (not isinstance(record, dict) or set(record) != {"schema", "plan", "selection"}
            or record["schema"] != SCHEMA or _digest(record) != identifier):
        raise ValueError("saved plan is invalid or changed; create a fresh proposal")
    plan = record["plan"]
    if (not isinstance(plan, dict) or plan.get("scope") != "targets"
            or plan.get("workspace") != os.path.realpath(workspace)
            or not isinstance(plan.get("plan_id"), str)
            or not _string_list(plan.get("targets")) or not plan["targets"]
            or not _string_list(record["selection"])):
        raise ValueError("saved plan does not match the selected workspace or target format")
    options = plan.get("options")
    if (not isinstance(options, dict)
            or set(options) - {"quarantine"} != {"discard", "protect", "max_entries", "max_depth"}
            or type(options.get("quarantine", False)) is not bool
            or not _string_list(options.get("discard"))
            or not _string_list(options.get("protect"))
            or type(options.get("max_entries")) is not int
            or type(options.get("max_depth")) is not int):
        raise ValueError("saved plan has invalid planning options")
    return {**record, "saved_plan_id": identifier, "file": str(path)}


def save_plan(plan, selection=None):
    """Save a proposed selection without granting permission or overwriting."""
    if plan.get("scope") != "targets":
        raise ValueError("--save-plan requires explicit target planning")
    if selection is None:
        requested = set(plan["targets"])
        selection = ([row["path"] for row in plan["entries"]
                      if row["path"] in requested and row["selectable"]]
                     if plan["complete"] else [])
    if selection:
        selection = [row["path"] for row in select_entries(plan, selection, plan["plan_id"])]
    record = {"schema": SCHEMA, "plan": plan, "selection": selection}
    identifier = _digest(record)
    raw = (json.dumps(record, ensure_ascii=True, indent=2) + "\n").encode("utf-8")
    if len(raw) > MAX_SAVED_BYTES:
        raise ValueError("saved plan exceeds its size limit")
    store = _store(plan["workspace"])
    path = store / (identifier + ".json")
    if not os.path.lexists(path):
        store.mkdir(parents=True, exist_ok=True)
        ignore = store / ".gitignore"
        if not os.path.lexists(ignore):
            with ignore.open("x", encoding="utf-8") as output:
                output.write("*\n")
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(raw)
    # Existing proposals are checked and reused, never silently replaced.
    return load_saved_plan(plan["workspace"], identifier)


def refresh_saved_plan(saved):
    """Rescan only stored targets before confirmation and again afterward."""
    previous = saved["plan"]
    fresh = plan_cleanup(previous["workspace"], targets=previous["targets"],
                         **previous["options"])
    if fresh["plan_id"] != previous["plan_id"]:
        raise ValueError(
            "saved plan changed. If cleanup is still needed, preview the same targets again and save a fresh proposal. "
            "Have the operator review and approve it, then execute using the newly issued task_id."
        )
    if not saved["selection"]:
        raise ValueError("saved proposal has no selection; review the targets and save a new selectable proposal")
    selected = select_entries(fresh, saved["selection"], previous["plan_id"])
    return fresh, selected
