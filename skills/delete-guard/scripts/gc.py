#!/usr/bin/env python3
"""gc - quarantine retention maintenance (B4 policy).

Default is a DRY PLAN: shows what is GC_ELIGIBLE (soft 30-day retention or
5 GiB cap, oldest first) and touches nothing. Purging is explicit:

    gc.py                      # plan only
    gc.py --execute --txid ID [--txid ID ...]
    gc.py --task ID            # preview the task's retained transactions
    gc.py --execute --task ID
    gc.py --execute --all-eligible

Every purge writes manifest tombstones and audit records; the audit log
itself is never garbage-collected.

Authority model (deliberate): --execute is allowed non-interactively.
Scheduled maintenance is legitimate, and purging already-quarantined
evidence is categorically lower-risk than capability escalation (which DOES
require a human terminal). Compensating control: every purge is audited
with session identity, and the audit trail is immutable by policy. Capacity limits NEVER cause the guard to
fall back to permanent deletion of live targets - see
CODE_RELOCATE_FAILED_STORAGE.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

import _bootstrap  # noqa: F401

from core import AUDIT_NAME, TRASH_DIRNAME, audit
from core import classifier, recovery
from core.cleanup_tasks import resolve_task_id, task_status
from core.identifiers import unique_prefix
from core.messages import gc_guidance, print_gc_guidance
from core.operation_summary import execution_summary, format_execution_summary, unavailable_summary


def resolve_txids(selectors, known, trash_root):
    candidates = [txid for txid in known if recovery._GC_TXID_RE.fullmatch(txid)]
    txids = []
    for selector in selectors:
        # Keep exact IDs and existing direct children on the normal validation
        # path, including unknown names that must be refused as unmanaged.
        if (not recovery._GC_TXID_RE.fullmatch(selector) or selector in known
                or os.path.lexists(os.path.join(trash_root, selector))):
            txids.append(selector)
        else:
            txids.append(unique_prefix(selector, candidates, "transaction ID") or selector)
    return txids


def task_txids(workspace, task_id, known):
    task = task_status(workspace, task_id)
    if task["status"] in {"APPROVED", "RUNNING", "UNKNOWN"}:
        raise ValueError("cleanup task is unresolved; query the task before selecting recovery copies")
    txids = []
    for group in task["groups"]:
        if group["effect"] != "quarantine" or group["status"] == "PENDING":
            continue
        if group["status"] != "COMPLETED" or group["unconfirmed"]:
            raise ValueError("incomplete quarantine receipt; inspect the task and select the exact transaction separately")
        txid = group["receipt"]["txid"]
        tx = known.get(txid)
        if tx is None or tx.get("meta", {}).get("cleanup_task") != task_id:
            raise ValueError("cleanup receipt does not match a transaction in this quarantine")
        if tx["manual_review_items"]:
            raise ValueError("transaction has unconfirmed copies; inspect it before selecting it for GC")
        if not tx.get("purged"):
            txids.append(txid)
    return txids


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--view", choices=("summary", "full"),
                    help="output only; full by default, summary explicitly omits detailed rows")
    ap.add_argument("--execute", action="store_true",
                    help="actually purge (default: plan only)")
    ap.add_argument("--txid", action="append", default=[],
                    help="transaction ID or unique prefix (repeatable)")
    ap.add_argument("--task", metavar="ID",
                    help="select retained transactions by cleanup task ID or unique prefix")
    ap.add_argument("--all-eligible", action="store_true",
                    help="purge every GC_ELIGIBLE transaction")
    args = ap.parse_args()
    if args.task is not None and (args.txid or args.all_eligible):
        ap.error("--task cannot be combined with --txid or --all-eligible")
    view = args.view or "full"

    base = os.getcwd()
    workspace = classifier.discover_workspace(base)
    trash_root = os.environ.get(
        "AGENT_GUARD_TRASH", os.path.join(workspace, TRASH_DIRNAME))
    engine = recovery.RecoveryEngine(workspace, trash_root)
    known = engine.transactions() if args.execute or args.txid or args.task is not None else {}
    selection = {}
    if args.task is not None:
        task_id = resolve_task_id(workspace, args.task)
        txids = task_txids(workspace, task_id, known)
        selection["task_id"] = task_id
    else:
        txids = resolve_txids(args.txid, known, trash_root)
    plan = engine.gc_plan()
    if args.all_eligible:
        txids.extend(e["txid"] for e in plan["eligible"])
    if args.txid or args.task is not None:
        selection["selected_txids"] = txids

    if not args.execute:
        if selection:
            engine.validate_gc_targets(txids)
        payload = {"mode": "plan", **plan, **selection,
                   "gc": gc_guidance(workspace, trash_root, txid=txids[0] if len(txids) == 1 else None)}
        payload["execution_summary"] = execution_summary("gc", payload)
        if args.as_json and view == "full":
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        elif args.as_json:
            print(json.dumps({"mode": "plan", **selection,
                              "execution_summary": payload["execution_summary"]}, indent=2))
        else:
            print(format_execution_summary(payload["execution_summary"]))
            if selection:
                print("Selected transaction IDs: " + (", ".join(txids) or "none"))
            if view == "full":
                print(f"quarantine bytes: {plan['total_bytes']} "
                      f"(soft cap {plan['size_limit_bytes']})")
                for entry in plan["eligible"]:
                    print(f"  GC_ELIGIBLE [{entry['reason']}] {entry['txid']} "
                          f"{entry['bytes']}B {entry['items']} items")
                if not plan["eligible"]:
                    print("  nothing eligible by age or size")
                print("Dry plan. No recovery copies were removed.")
            print_gc_guidance(payload["gc"])
        return 0

    if not txids:
        message = ("no remaining quarantine transactions for cleanup task " + task_id
                   if args.task is not None else "nothing selected: pass --txid, --task or --all-eligible")
        print(message, file=sys.stderr)
        return 1

    # Preflight both identity and audit durability before irreversible purge.
    # A failed final receipt still leaves this intent plus manifest tombstones.
    engine.validate_gc_targets(txids)

    def selection_metadata(selected):
        return {
            "txids": [txid for txid in selected if txid in known],
            "missing_txid_sha256": [hashlib.sha256(
                txid.encode("utf-8", errors="surrogatepass")).hexdigest()
                for txid in selected if txid not in known],
        }

    audit.append({"event": "gc-intent", "target_count": len(txids),
                  **selection_metadata(txids)},
                 os.path.join(trash_root, AUDIT_NAME))
    report = engine.gc_execute(txids)
    missing = selection_metadata(report["missing"])
    audit.append({"event": "gc", "action": "PURGED",
                  "purged": report["purged"], "missing": missing["txids"],
                  "missing_count": len(report["missing"]),
                  "missing_txid_sha256": missing["missing_txid_sha256"],
                  "plan_reasons": {e["txid"]: e["reason"]
                                   for e in plan["eligible"] if e["txid"] in txids}},
                 os.path.join(trash_root, AUDIT_NAME))
    context = {"task_id": task_id} if args.task is not None else {}
    payload = {"mode": "execute", **context, **report}
    payload["execution_summary"] = execution_summary("gc", payload)
    if args.as_json and view == "full":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif args.as_json:
        print(json.dumps({"mode": "execute", **context,
                          "execution_summary": payload["execution_summary"]}, indent=2))
    elif view == "summary":
        print(format_execution_summary(payload["execution_summary"]))
    else:
        print(format_execution_summary(payload["execution_summary"]))
        for txid in report["purged"]:
            print(f"purged: {txid}")
        for txid in report["missing"]:
            print(f"missing (skipped): {txid}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"agent-guard internal error: {exc}", file=sys.stderr)
        print(format_execution_summary(unavailable_summary("gc")), file=sys.stderr)
        sys.exit(1)
