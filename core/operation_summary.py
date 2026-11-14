"""Receipt-derived maintenance summaries; no inventory, policy or mutations."""
from __future__ import annotations

import json


SCHEMA = "maintenance_summary/v1"
ACTION_LABELS = {
    "quarantined": "隔离已确认 / quarantined",
    "regenerable_deleted": "可再生直接清理已确认 / regenerable deleted",
    "quarantine_payload_removed": "隔离载荷清理 / quarantine payload removed",
    "restored": "恢复已确认 / restored",
    "recovery_copies_purged": "恢复副本移除 / recovery copies purged",
}


def unavailable_summary(operation):
    return {"schema": SCHEMA, "operation": operation, "status": "UNKNOWN",
            "counts": {"requested": None, "confirmed": None, "unconfirmed": None},
            "actions": {}, "transactions": {"count": None, "sample": [], "complete": False},
            "space": {"admitted_bytes": None, "physical_freed_bytes": None},
            "evidence": "no_usable_receipt", "issues": [],
            "next_action": "inspect_evidence_before_new_work",
            "notice": "No usable receipt: unknown does not mean unchanged. Inspect task/transaction/source evidence before new work."}


def execution_summary(operation, result, *, reference=None):
    """Count proven receipt items; never infer physical space or missing work.

    Callers pass the existing CLI/public task result, not private task state.
    This projection never becomes part of a plan's approval fingerprint.
    """
    summary = unavailable_summary(operation)
    summary.update(evidence="returned_cli_receipt", issues=[],
                   notice="Counts describe this receipt, not live filesystem state. Transaction references do not establish current restorability; physical freed space is unmeasured.")
    refs = []
    if operation == "cleanup" and "groups" in result:
        counts = result["counts"]
        summary.update(status=result["status"], evidence="last_durable_task_receipts",
                       counts={"unit": "selected_roots", "requested": counts["targets"],
                               "confirmed": counts["confirmed"], "unconfirmed": counts["unconfirmed"],
                               "not_dispatched": counts["not_dispatched"],
                               "dispatched_unconfirmed": counts["unconfirmed"] - counts["not_dispatched"]},
                       actions={"quarantined": 0, "regenerable_deleted": 0})
        for group in result["groups"]:
            action = "quarantined" if group["effect"] == "quarantine" else "regenerable_deleted"
            summary["actions"][action] += len(group["confirmed"])
            receipt = group.get("receipt", {})
            summary["issues"].extend(receipt.get("warnings", []))
            if receipt.get("txid"):
                refs.append(receipt["txid"])
            if group.get("error"):
                summary["issues"].append(group["error"])
            elif group["status"] == "STOPPED":
                summary["issues"].extend(receipt.get("verdict", {}).get("reasons", []))
        preflight = result.get("preflight") or []
        if preflight and preflight[0].get("requested"):
            summary["space"]["preflight_requested_bytes"] = preflight[0]["requested"]["bytes"]
        summary["issues"].extend(str(check["code"]) + ": " + check["reason"] for check in preflight
                                 if check.get("code") is not None)
        if result.get("error"):
            summary["issues"].append(result["error"])
        summary["next_action"] = ("execute_approved_task" if result.get("can_execute") else
                                  "handoff_complete" if result["status"] == "COMPLETED" else
                                  "inspect_task_and_transactions")
    elif operation == "cleanup" and "reports" in result:
        reports = result["reports"]
        requested = result.get("requested_roots")
        actions = {"quarantined": 0, "regenerable_deleted": 0, "quarantine_payload_removed": 0}
        unknown = False
        for row in reports:
            if "report" not in row:
                unknown |= row.get("started") is not False
                if row.get("error"):
                    summary["issues"].append(row["error"])
                continue
            child = execution_summary("safe_delete", row["report"])
            unknown |= child["status"] == "UNKNOWN"
            for action, count in child["actions"].items():
                actions[action] += count
            refs.extend(child["transactions"]["sample"])
        confirmed = sum(actions.values())
        not_dispatched = (requested - sum(row.get("started") is not False for row in reports)
                          if requested is not None else None)
        summary.update(actions=actions,
                       status="UNKNOWN" if unknown else "COMPLETED" if requested == confirmed else
                              "PARTIAL" if confirmed else "STOPPED",
                       counts={"unit": "selected_roots", "requested": requested, "confirmed": confirmed,
                               "unconfirmed": requested - confirmed if requested is not None else None,
                               "not_dispatched": not_dispatched},
                       next_action="handoff_complete" if requested == confirmed and not unknown else "inspect_task_and_transactions")
    elif operation == "safe_delete":
        if "verdict" not in result:
            return unavailable_summary(operation)
        code = result["verdict"]["code"]
        requested = len(result["targets"])
        actions = {"quarantined": len(result.get("moved", [])),
                   "regenerable_deleted": (sum(value in ("deleted", "deleted-tree") for value in result.get("deleted", []))
                                           if code == "ALLOW_REGENERABLE" else 0),
                   "quarantine_payload_removed": (sum(value in ("deleted", "deleted-tree") for value in result.get("deleted", []))
                                                   if code == "ALLOW_TRASH_GC" else 0)}
        confirmed = sum(actions.values())
        preview = result.get("dry_run", False)
        noop = code == "ALLOW_NOOP"
        status = ("PREVIEW" if preview else "NOOP" if noop else
                  "COMPLETED" if result["exit"] == 0 and requested == confirmed else
                  "COMPLETED_WITH_SKIPS" if result["exit"] == 0 else "PARTIAL" if confirmed else "STOPPED")
        summary.update(status=status, actions=actions, decision_code=code,
                       counts={"unit": "input_target_records", "requested": requested, "confirmed": confirmed,
                               "unconfirmed": 0 if preview or noop else requested - confirmed,
                               "skipped": len(result.get("skipped", [])),
                               "not_needed": requested if noop else 0},
                       next_action="review_admission" if preview else "handoff_complete" if status in ("COMPLETED", "NOOP") else "inspect_task_and_transactions")
        if result.get("txid"):
            refs.append(result["txid"])
        if result.get("admission", {}).get("requested"):
            summary["space"]["preflight_requested_bytes"] = result["admission"]["requested"]["bytes"]
        if result["verdict"].get("decision") == "BLOCK":
            summary["issues"] = list(result["verdict"].get("reasons", []))
        summary["issues"].extend(result.get("warnings", []))
    elif operation == "restore":
        restored, conflicts = len(result.get("restored", [])), len(result.get("conflicts", []))
        errors = len(result.get("errors", [])) + bool(result.get("error"))
        summary.update(status="COMPLETED" if result.get("ok") else "PARTIAL" if restored else "STOPPED",
                       counts={"unit": "restore_items", "requested": None, "confirmed": restored,
                               "conflicts": conflicts, "error_events": errors}, actions={"restored": restored},
                       issues=[*result.get("errors", []), *([result["error"]] if result.get("error") else [])],
                       next_action="inspect_restore_conflicts_and_errors" if errors or conflicts else "handoff_complete")
        summary["conflicts"] = {"count": conflicts, "sample": result.get("conflicts", [])[:3], "complete": conflicts <= 3}
        if reference and not result.get("error"):
            refs.append(reference)
        refs.extend(result.get("backup_txids", []))
    elif operation == "gc":
        if result["mode"] == "plan":
            summary.update(status="PREVIEW", counts={"unit": "transactions", "eligible": len(result["eligible"]),
                                                    "confirmed": 0}, actions={"recovery_copies_purged": 0},
                           next_action="review_gc_selection")
            summary["space"].update(reported_logical_bytes=result["total_bytes"],
                                    logical_measurement="existing_best_effort_inventory; completeness unverified")
        else:
            purged, missing = len(result["purged"]), len(result["missing"])
            summary.update(status="COMPLETED_WITH_SKIPS" if missing else "COMPLETED",
                           counts={"unit": "transaction_records", "requested": purged + missing,
                                   "confirmed": purged, "skipped": missing},
                           actions={"recovery_copies_purged": purged},
                           next_action="inspect_missing_transactions" if missing else "handoff_complete")
            refs.extend(result["purged"])
        summary["notice"] = "GC describes only the selected recovery copies, not every backup or storage medium. Logical inventory does not prove physical freed space."
    elif operation == "cleanup":
        if result.get("may_have_changed") or result.get("outcome") == "state_unavailable":
            return unavailable_summary(operation)
        summary.update(status="CANCELLED" if result.get("outcome") == "cancelled" else "STOPPED",
                       counts={"requested": None, "confirmed": 0, "unconfirmed": None},
                       next_action="review_plan")
    else:
        raise ValueError("unsupported maintenance summary operation")
    refs = list(dict.fromkeys(refs))
    summary["transactions"] = {"count": len(refs), "sample": refs[:5], "complete": len(refs) <= 5}
    summary["issue_count"] = len(summary["issues"])
    summary["issues"] = summary["issues"][:3]
    return summary


def format_execution_summary(summary):
    """Same fixed text for people and agents; explanatory prose is separate."""
    counts = summary["counts"]
    lines = ["结果 / result: " + summary["status"]]
    if counts.get("requested") is not None:
        lines.append("请求 / requested: " + str(counts["requested"]) + " " + counts.get("unit", "records"))
    for action, count in summary["actions"].items():
        lines.append(ACTION_LABELS[action] + ": " + str(count))
    labels = {"unconfirmed": "无完成回执（含未派发） / without completion receipt",
              "dispatched_unconfirmed": "已有执行意图但未确认 / attempted unconfirmed",
              "not_dispatched": "尚未派发 / not dispatched", "conflicts": "恢复冲突 / conflicts",
              "error_events": "错误事件 / error events", "skipped": "跳过 / skipped", "eligible": "GC候选事务 / eligible"}
    for key in ("unconfirmed", "dispatched_unconfirmed", "not_dispatched", "conflicts", "error_events", "skipped", "eligible"):
        if key in counts and counts[key] is not None:
            lines.append(labels[key] + ": " + str(counts[key]))
    if summary["transactions"]["sample"]:
        lines.append("事务引用 / transaction references: " + json.dumps(summary["transactions"], ensure_ascii=True))
    if summary.get("conflicts", {}).get("sample"):
        lines.append("冲突样例 / conflict samples: " + json.dumps(summary["conflicts"], ensure_ascii=True))
    lines.append("实际释放空间 / physical freed space: 未测量 / unmeasured")
    if "reported_logical_bytes" in summary["space"]:
        lines.append("GC reported logical bytes (best effort, completeness unverified): "
                     + str(summary["space"]["reported_logical_bytes"]))
    for issue in summary["issues"]:
        lines.append("Issue: " + json.dumps(issue, ensure_ascii=True))
    lines.extend(["下一步 / next: " + summary["next_action"], summary["notice"]])
    return "\n".join(lines)
