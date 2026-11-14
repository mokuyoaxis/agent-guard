"""Read-only projections over existing plans and public task receipts."""
from __future__ import annotations

import json
import os
import time

from .cleanup import CATEGORIES
from .operation_summary import execution_summary, format_execution_summary


SCHEMA = "cleanup_view/v1"
DEFAULT_LIMIT = 50
MAX_LIMIT = 200
TASK_ROW_STATES = ("confirmed", "unconfirmed", "not_dispatched")


def read_options(kind, *, view="summary", category=None, path=None, state=None, offset=None, limit=None):
    """Validate presentation input at CLI/MCP boundaries, never target edits."""
    if view not in ("summary", "details", "full"):
        raise ValueError("view must be summary, details or full")
    if kind == "execution" and view == "details":
        raise ValueError("execution returns summary or full; query a saved plan/task for paginated details")
    if view != "details" and any(value is not None for value in (category, path, state, offset, limit)):
        raise ValueError("category/path/state/offset/limit require the details view; they never change execution")
    if category is not None and (kind != "plan" or category not in CATEGORIES):
        raise ValueError("category requires a plan detail view and one of its four categories")
    if state is not None and (kind != "task" or state not in TASK_ROW_STATES):
        raise ValueError("state requires task details: confirmed, unconfirmed or not_dispatched")
    if path is not None:
        if not isinstance(path, str) or not path or "\0" in path:
            raise ValueError("path must be a nonempty literal relative path")
        path = os.path.normpath(path).replace(os.sep, "/")
        if os.path.isabs(path) or path == ".." or path.startswith("../"):
            raise ValueError("detail path must be relative to the recorded workspace")
    offset = 0 if offset is None else offset
    limit = DEFAULT_LIMIT if limit is None else limit
    if type(offset) is not int or offset < 0:
        raise ValueError("offset must be a nonnegative integer")
    if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
        raise ValueError("limit must be an integer between 1 and 200")
    return {"view": view, "category": category, "path": path, "state": state,
            "offset": offset, "limit": limit}


def _matches(row, options):
    path = options["path"]
    return ((options["category"] is None or row.get("category") == options["category"])
            and (options["state"] is None or row.get("state") == options["state"])
            and (path is None or path == "." or row["path"] == path or row["path"].startswith(path + "/")))


def _page(rows, options):
    matches = [row for row in rows if _matches(row, options)]
    offset, limit = options["offset"], options["limit"]
    entries = matches[offset:offset + limit]
    more = offset + len(entries) < len(matches)
    return {"entries": entries,
            "page": {"total_rows": len(rows), "matched_rows": len(matches), "offset": offset,
                     "limit": limit, "returned": len(entries), "has_more": more,
                     "next_offset": offset + len(entries) if more else None}}


def plan_view(result, options):
    """Do not mutate saved payloads or incorporate view fields into plan IDs."""
    if options["view"] == "full":
        return result
    stored = "plan" in result
    plan = result["plan"] if stored else result
    selection = set(result["selection"]) if stored else None
    categories = {}
    for category in CATEGORIES:
        rows = [row for row in plan["entries"] if row["category"] == category]
        unknown = sum(row["size_bytes"] is None for row in rows)
        categories[category] = {"candidate_rows": len(rows),
                                "known_logical_bytes": plan["summary"][category]["size_bytes"],
                                "unknown_size_rows": unknown,
                                "size_complete": plan["complete"] and unknown == 0}
    selected = ([row for row in plan["entries"] if row["path"] in selection]
                if selection is not None else None)
    effects = ({effect: sum(row["effect"] == effect for row in selected)
                for effect in ("quarantine", "delete")} if selected is not None else None)
    projected = {"schema": SCHEMA, "kind": "plan", "view": options["view"],
                 "plan_id": plan["plan_id"], "saved_plan_id": result.get("saved_plan_id"),
                 "workspace": plan["workspace"], "trash_root": plan["trash_root"], "mode": plan["mode"],
                 "scope": plan.get("scope", "workspace"), "complete": plan["complete"],
                 "refreshed": result.get("refreshed", not stored), "read_at": time.time(),
                 "overview": {"requested_roots": len(plan["targets"]) if "targets" in plan else None,
                              "candidate_rows": len(plan["entries"]), "selected_roots": len(selection) if selection is not None else None,
                              "selectable_rows": sum(row["selectable"] for row in plan["entries"]),
                              "categories": categories, "selected_effects": effects,
                              "examined_entries": plan["examined"]},
                 "error_count": len(plan["errors"]), "warning_count": len(plan["warnings"]),
                 "errors": plan["errors"][:3], "warnings": plan["warnings"][:3],
                 "next_step": result.get("next_step"),
                 "detail_reference": {"saved_plan_id": result.get("saved_plan_id"),
                                      "notice": "Read details from this saved snapshot; it is not a live source check." if stored else
                                                "Unsaved plan: use explicit target saving for later ID-based details, or request full output in this call."},
                 "size_notice": plan["size_notice"],
                 "notice": "View filters affect output only. Summary/page coverage is separate from scan completeness; no approval or admission reservation."}
    if plan["options"].get("quarantine", False):
        projected["overview"]["quarantine_requested"] = True
    if options["view"] == "details":
        page = _page(plan["entries"], options)
        page["entries"] = [{**row, "selected": row["path"] in selection if selection is not None else None}
                           for row in page["entries"]]
        projected.update(page)
    return projected


def task_view(result, options):
    summary = execution_summary("cleanup", result)
    if options["view"] == "full":
        return {**result, "execution_summary": summary}
    projected = {"schema": SCHEMA, "kind": "task", "view": options["view"],
                 **{key: result[key] for key in ("task_id", "saved_plan_id", "plan_id", "workspace", "trash_root",
                                                "status", "approval_status", "expires", "can_execute", "counts", "exit")},
                 "execution_summary": summary, "read_at": time.time(),
                 "next_step": result.get("next_step"), "may_have_changed": result["may_have_changed"],
                 "detail_reference": {"task_id": result["task_id"]},
                 "notice": result["notice"] + " Detail pages can change while running; filters never select execution targets."}
    if options["view"] == "details":
        rows = []
        for group in result["groups"]:
            confirmed = set(group["confirmed"])
            receipt = group.get("receipt", {})
            for path in group["paths"]:
                state = ("confirmed" if path in confirmed else
                         "not_dispatched" if group["status"] == "PENDING" else "unconfirmed")
                rows.append({"path": path, "effect": group["effect"], "state": state,
                             "group_status": group["status"], "txid": receipt.get("txid"),
                             "decision_code": receipt.get("verdict", {}).get("code")})
        projected.update(_page(sorted(rows, key=lambda row: row["path"]), options))
    return projected


def format_cleanup_view(result):
    if result["kind"] == "task":
        lines = ["Task: " + result["task_id"], format_execution_summary(result["execution_summary"]),
                 "Approval: " + result["approval_status"] + "; can_execute=" + str(result["can_execute"])]
    else:
        overview = result["overview"]
        lines = ["Plan: " + result["plan_id"], "Saved plan: " + str(result["saved_plan_id"]),
                 "Scan complete: " + str(result["complete"]) + "; refreshed=" + str(result["refreshed"]),
                 "Requested roots: " + (str(overview["requested_roots"]) if overview["requested_roots"] is not None else "workspace scope")
                 + "; selected roots: " + (str(overview["selected_roots"]) if overview["selected_roots"] is not None else "not proposed")
                 + "; candidate rows: " + str(overview["candidate_rows"])]
        for category, row in overview["categories"].items():
            lines.append(category + ": " + str(row["candidate_rows"]) + " rows; known logical bytes="
                         + str(row["known_logical_bytes"]) + "; unknown-size rows=" + str(row["unknown_size_rows"]))
        lines.append("Selected effects: " + json.dumps(overview["selected_effects"]))
        if overview.get("quarantine_requested", False):
            lines.append("Quarantine requested: includes regenerable artifacts; no direct deletion.")
        lines.append("Errors=" + str(result["error_count"]) + "; warnings=" + str(result["warning_count"]))
        for issue in [*result["errors"], *result["warnings"]]:
            lines.append("Issue: " + json.dumps(issue, ensure_ascii=True))
        lines.append(result["size_notice"])
    lines.append("Workspace: " + json.dumps(result["workspace"], ensure_ascii=True))
    if result["view"] == "details":
        lines.append("Page: " + json.dumps(result["page"]))
        for row in result["entries"]:
            lines.append(json.dumps(row, ensure_ascii=True))
    lines.append(result["notice"])
    return "\n".join(lines)
