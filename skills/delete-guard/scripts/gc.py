#!/usr/bin/env python3
"""gc - quarantine retention maintenance (B4 policy).

Default is a DRY PLAN: shows what is GC_ELIGIBLE (soft 30-day retention or
5 GiB cap, oldest first) and touches nothing. Purging is explicit:

    gc.py                      # plan only
    gc.py --execute --txid ID [--txid ID ...]
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
import json
import os
import sys

import _bootstrap  # noqa: F401

from core import AUDIT_NAME, TRASH_DIRNAME, audit
from core import classifier, recovery


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--execute", action="store_true",
                    help="actually purge (default: plan only)")
    ap.add_argument("--txid", action="append", default=[],
                    help="transaction to purge (repeatable)")
    ap.add_argument("--all-eligible", action="store_true",
                    help="purge every GC_ELIGIBLE transaction")
    args = ap.parse_args()

    base = os.getcwd()
    workspace = classifier.discover_workspace(base)
    trash_root = os.environ.get(
        "AGENT_GUARD_TRASH", os.path.join(workspace, TRASH_DIRNAME))
    engine = recovery.RecoveryEngine(workspace, trash_root)
    plan = engine.gc_plan()

    if not args.execute:
        payload = {"mode": "plan", **plan}
        if args.as_json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"quarantine bytes: {plan['total_bytes']} "
                  f"(soft cap {plan['size_limit_bytes']})")
            for entry in plan["eligible"]:
                print(f"  GC_ELIGIBLE [{entry['reason']}] {entry['txid']} "
                      f"{entry['bytes']}B {entry['items']} items")
            if not plan["eligible"]:
                print("  nothing eligible")
            print("(dry plan; pass --execute to purge)")
        return 0

    txids = list(args.txid)
    if args.all_eligible:
        txids.extend(e["txid"] for e in plan["eligible"])
    if not txids:
        print("nothing selected: pass --txid or --all-eligible",
              file=sys.stderr)
        return 1

    # Preflight both identity and audit durability before irreversible purge.
    # A failed final receipt still leaves this intent plus manifest tombstones.
    engine.validate_gc_targets(txids)
    audit.append({"event": "gc-intent", "txids": txids},
                 os.path.join(trash_root, AUDIT_NAME))
    report = engine.gc_execute(txids)
    audit.append({"event": "gc", "action": "PURGED",
                  "purged": report["purged"], "missing": report["missing"],
                  "plan_reasons": {e["txid"]: e["reason"]
                                   for e in plan["eligible"]}},
                 os.path.join(trash_root, AUDIT_NAME))
    if args.as_json:
        print(json.dumps({"mode": "execute", **report}, ensure_ascii=False,
                         indent=2))
    else:
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
        sys.exit(1)
