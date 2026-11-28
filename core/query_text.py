"""Small terminal renderer for workspace_query/v1; stdlib only, no actions."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import unicodedata

from .messages import QUARANTINE_NOTICE


def _text(value):
    # Preserve readable Unicode while escaping terminal controls/newlines.
    escaped = json.dumps(str(value), ensure_ascii=False)[1:-1]
    return "".join(json.dumps(char)[1:-1]
                   if unicodedata.category(char) in {"Cc", "Cf", "Cs", "Zl", "Zp"} else char
                   for char in escaped)


def _width(value):
    return sum(0 if unicodedata.combining(char) else
               2 if unicodedata.east_asian_width(char) in ("W", "F") else 1 for char in value)


def _cell(value, width):
    text = _text(value)
    if _width(text) > width:
        clipped, used = [], 0
        for char in text:
            size = _width(char)
            if used + size > width - 1:
                break
            clipped.append(char)
            used += size
        text = "".join(clipped) + "…"
    return text + " " * (width - _width(text))


def _table(headers, rows, widths):
    line = "  " + " │ ".join(_cell(value, width) for value, width in zip(headers, widths))
    separator = "  " + "─┼─".join("─" * width for width in widths)
    return [line, separator, *["  " + " │ ".join(_cell(value, width) for value, width in zip(row, widths)) for row in rows]]


def _size(value):
    if value is None:
        return "not measured"
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return (str(int(value)) if unit == "B" else f"{value:.1f}") + " " + unit
        value /= 1024


def _bar(used, maximum):
    filled = min(12, int(12 * used / maximum)) if maximum else 0
    return "[" + "#" * filled + "·" * (12 - filled) + "]"


def _when(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%m-%d %H:%M")


def format_workspace_query(data, *, width=96, color=False):
    """Color is caller-controlled and never used for JSON/MCP output."""
    lines = []

    def section(label):
        heading = label if not color else "\033[1;36m" + label + "\033[0m"
        lines.extend(["", heading, "─" * min(max(width, 32), 96)])

    title = "AGENT-GUARD  ·  READ-ONLY OVERVIEW"
    lines.append(title if not color else "\033[1m" + title + "\033[0m")
    lines.append("Workspace   " + _text(data["workspace"]))
    lines.append("Quarantine  " + _text(data["trash_root"]))
    lines.append("Mode (Core) " + data["mode"] + "   |   Metadata "
                 + ("COMPLETE" if data["complete"] else "PARTIAL") + "   |   UTC " + _when(data["read_at"]))

    section("BUDGET / 预算")
    budget = data["delete_admission"]
    if budget.get("status") == "BLOCKED":
        lines.append("  UNAVAILABLE [" + budget["code"] + "] " + _text(budget["reason"]))
    else:
        limits = budget["limits"]
        lines.append("  Session  " + _bar(budget["used_bytes"], limits["session_max_bytes"]) + "  "
                     + _size(budget["used_bytes"]) + " / " + _size(limits["session_max_bytes"]))
        lines.append("  Entries  " + str(budget["used_entries"]) + " / " + str(limits["session_max_entries"]))
        lines.append("  Rate     " + _bar(budget["window_operations"], limits["rate_max_operations"]) + "  "
                     + str(budget["window_operations"]) + " / " + str(limits["rate_max_operations"])
                     + " in " + str(limits["rate_window_seconds"]) + "s")
        storage = data["storage"]
        if not storage.get("measured"):
            lines.append("  Trash cap " + _size(limits["quarantine_max_bytes"]) + " / "
                         + str(limits["quarantine_max_entries"]) + " entries; occupancy not measured")
        elif storage.get("complete"):
            lines.append("  Trash cap " + _bar(storage["logical_bytes"], limits["quarantine_max_bytes"]) + "  "
                         + _size(storage["logical_bytes"]) + " / " + _size(limits["quarantine_max_bytes"])
                         + f" ({storage['entries']}/{limits['quarantine_max_entries']} entries)")
        else:
            lines.append("  Trash cap " + _size(limits["quarantine_max_bytes"]) + " / "
                         + str(limits["quarantine_max_entries"]) + " entries; partial observed "
                         + _size(storage["observed_logical_bytes"])
                         + f" ({storage['observed_entries']} entries)")
        lines.append("  Admitted work; failures and restores do not refund the session budget.")

    tasks = data["tasks"]
    section("RECENT TASKS / 最近任务  (" + str(tasks["returned"]) + " shown)")
    task_rows = []
    for row in tasks["items"]:
        status = row["status"] + (" !" if row["status"] == "APPROVED" and row["approval_expired"] else "")
        task_rows.append((row["task_id"], status, str(row["confirmed"]) + "/" + str(row["targets"]), _when(row["updated_at"])))
    if not task_rows:
        lines.append("  No matching task records observed.")
    elif width >= 80:
        lines.extend(_table(("TASK ID", "STATE", "CONFIRMED", "UPDATED UTC"), task_rows, (32, 12, 9, 11)))
    else:
        for task_id, status, done, updated in task_rows:
            lines.extend(["  " + task_id, "    " + status + "  confirmed=" + done + "  UTC " + updated])
    lines.append("  ! approval expired; task status is retained. Rows do not check grants or process liveness.")
    if tasks["has_more"]:
        lines.append("  More matching rows available within the observed metadata scope; increase --limit (max 50).")
    elif tasks["has_more"] is None:
        lines.append("  Remaining task rows unknown: inspect metadata coverage issues.")

    transactions = data["transactions"]
    section("TRANSACTIONS / 隔离事务  (" + str(transactions["returned"]) + " shown)")
    tx_rows = [
        (row["txid"], row["state"] + (" !" if row["manual_review_items"] else ""), row["items"],
         "yes" if row["age_candidate_hint"] else "?" if row["age_candidate_hint"] is None else "no")
        for row in transactions["items"]
    ]
    if not tx_rows:
        lines.append("  No transaction records observed.")
    elif width >= 72:
        lines.extend(_table(("TRANSACTION ID", "CORE STATE", "ITEMS", "AGE HINT"), tx_rows, (26, 12, 7, 8)))
    else:
        for txid, state, items, age in tx_rows:
            lines.extend(["  " + _text(txid), "    " + state + "  items=" + str(items) + "  age hint=" + age])
    if transactions["manual_review_items"]:
        lines.append(f"  ! Manual review required for {transactions['manual_review_items']} failed relocation item(s).")
    lines.append("  Core states are metadata/path observations, not payload-integrity or restore-permission proof.")

    section("STORAGE & GC / 占用与候选")
    gc = data["gc_candidates"]
    storage = data["storage"]
    location = storage["location"]
    location_status = location.get("status") or (location["entries"][0]["status"] if location["entries"] else "UNCONFIRMED")
    lines.append("  Location        " + location_status)
    if not storage.get("measured"):
        lines.append("  Occupancy       not measured (overview does not walk payload trees)")
    elif storage.get("complete"):
        lines.append("  Occupancy       " + _size(storage["logical_bytes"])
                     + f" ({storage['entries']} entries; logical, admission accounting)")
        ratio = storage.get("admission_ratio")
        if ratio is not None:
            b_pct = f"{ratio['bytes_ratio'] * 100:.1f}%" if ratio["bytes_ratio"] is not None else "n/a"
            e_pct = f"{ratio['entries_ratio'] * 100:.1f}%" if ratio["entries_ratio"] is not None else "n/a"
            lines.append(f"  Admission ratio bytes={b_pct}, entries={e_pct}")
        else:
            lines.append("  Admission ratio unavailable (" + str(storage.get("admission_ratio_unavailable_reason")) + ")")
        top = storage.get("top_transactions")
        if top is None:
            lines.append("  Top transactions unavailable (" + str(storage.get("top_transactions_unavailable_reason")) + ")")
        elif not top:
            lines.append("  Top transactions none observed")
        else:
            top_rows = [(row["txid"], row["state"], _size(row["logical_bytes"]), row["entries"]) for row in top]
            if width >= 72:
                lines.extend(_table(("TOP TXID", "STATE", "LOGICAL SIZE", "ENTRIES"), top_rows, (26, 12, 12, 8)))
            else:
                for txid, state, size_txt, entries in top_rows:
                    lines.append(f"    {_text(txid)}  {state}  {size_txt}  entries={entries}")
    else:
        lines.append("  Occupancy       PARTIAL (observed " + _size(storage["observed_logical_bytes"])
                     + f", {storage['observed_entries']} entries; attempted {storage['attempted_entries']}/{storage['max_entries']})")
        lines.append("  Admission ratio unavailable (" + str(storage.get("admission_ratio_unavailable_reason")) + ")")
        lines.append("  Top transactions unavailable (" + str(storage.get("top_transactions_unavailable_reason")) + ")")
    lines.append("  Age-only hints  " + (str(gc["count"]) if gc["count"] is not None else "unknown")
                 + "; unknown age (observed)=" + str(gc["unknown_age_transactions"]))
    lines.append("  Capacity hints  not measured; use the existing explicit GC preview for details")
    lines.append("  Soft hints      " + str(gc["soft_retention_days"]) + "d / " + _size(gc["soft_limit_bytes"]))
    lines.append("  " + QUARANTINE_NOTICE)
    lines.append("  GC is manual. Review the GC plan before selecting recovery copies to purge.")
    issues = [*tasks["coverage"]["issues"], *transactions["coverage"]["issues"],
              *[row["code"] for row in location.get("errors", [])],
              *storage.get("issues", [])]
    if issues:
        lines.append("  Coverage issues " + ", ".join(sorted(set(issues))))
    lines.extend(["", "Task details: cleanup.py --task TASK_ID --view details (use an ID above)",
                  "Full legacy status/size inventory: status.py --view full",
                  "Metadata inspection does not verify native host interception.",
                  "Snapshot only · partial coverage is not absence · GC remains an explicit separate action."])
    return "\n".join(lines)
