#!/usr/bin/env python3
"""status - inspect guard state, quarantine usage, recent decisions.

    status.py [--json] [--tail N]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

import _bootstrap  # noqa: F401

from core import AUDIT_NAME, TRASH_DIRNAME
from core import audit, classifier, policy, recovery


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--tail", type=int, default=10)
    args = ap.parse_args()

    base = os.getcwd()
    workspace = classifier.discover_workspace(base)
    trash_root = os.environ.get(
        "AGENT_GUARD_TRASH", os.path.join(workspace, TRASH_DIRNAME))
    engine = recovery.RecoveryEngine(workspace, trash_root)
    state = policy.load_mode(trash_root)
    usage = engine.usage()

    stash_count = 0
    try:
        proc = subprocess.run(["git", "-C", workspace, "stash", "list"],
                              capture_output=True, text=True, timeout=10)
        stash_count = sum(
            1 for line in proc.stdout.splitlines() if "agent-guard:" in line)
    except (OSError, subprocess.TimeoutExpired):
        pass

    plan = engine.gc_plan()
    info = {
        "workspace": workspace,
        "mode": state["mode"],
        "mode_since": state["since"],
        "mode_set_by": state["set_by"],
        "trash_root": trash_root,
        "usage": usage,
        "retention": {
            "soft_days": plan["retention_days"],
            "soft_limit_bytes": plan["size_limit_bytes"],
            "total_bytes": plan["total_bytes"],
            "gc_eligible": [{"txid": e["txid"], "bytes": e["bytes"],
                             "reason": e["reason"]}
                            for e in plan["eligible"]],
        },
        "guard_snapshots": stash_count,
        "decisions": decision_stats(engine.trash_root),
        "recent_audit": audit_tail(engine.trash_root, args.tail),
    }
    if args.as_json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 0

    print(f"workspace : {info['workspace']}")
    print(f"mode      : {info['mode']}"
          + (f" (since {state['since']} by {state['set_by']})" if state["since"] else ""))
    print(f"quarantine: {trash_root}")
    print(f"usage     : {usage['files']} files, {usage['bytes']} bytes, "
          f"{usage['transactions']} transactions "
          f"({usage['restorable_transactions']} restorable)")
    print(f"snapshots : {stash_count} agent-guard stashes")
    ret = info["retention"]
    print(f"retention : soft {ret['soft_days']}d / "
          f"{ret['soft_limit_bytes'] // (1024**3)}GiB; "
          f"{len(ret['gc_eligible'])} GC-eligible")
    stats = info["decisions"]
    if stats:
        print(f"decisions : {top_codes(stats)}")
    print("recent decisions:")
    for record in info["recent_audit"]:
        print(f"  [{record.get('ts','')}] {record.get('action', record.get('event'))}"
              f" {record.get('code','')} {record.get('targets', record.get('txid',''))}"
              .rstrip())
    return 0


def audit_tail(trash_root, n):
    # Historical append-only records can contain raw commands. Never copy
    # their free-form fields into an agent-visible status result.
    fields = ("ts", "event", "action", "decision", "code", "txid",
              "check_id", "guard_latency_ms")
    return [{key: record[key] for key in fields if key in record}
            for record in audit.tail(os.path.join(trash_root, AUDIT_NAME), n)]


DECISION_EVENTS = {"check", "enforce-block", "enforce-proceed", "ask",
                   "decision"}


def decision_stats(trash_root):
    """Local-only aggregation of recorded decisions (nothing leaves the
    machine). Counts reason codes across all decision-bearing events; ask
    OUTCOMES are not visible to the guard and are therefore not claimed
    here."""
    import collections
    counter = collections.Counter()
    for record in audit.tail(os.path.join(trash_root, AUDIT_NAME), 5000):
        if record.get("event") not in DECISION_EVENTS:
            continue
        code = record.get("code")
        if code:
            counter[code] += 1
        elif record.get("event") == "enforce-proceed":
            counter["PROCEED(compensated)"] += 1
    return dict(counter)


def top_codes(stats, k=3):
    return ", ".join(f"{c} x{n}" for c, n in
                     sorted(stats.items(), key=lambda kv: -kv[1])[:k])

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"agent-guard internal error: {exc}", file=sys.stderr)
        sys.exit(1)
