#!/usr/bin/env python3
"""status - inspect guard state, quarantine usage, recent decisions.

    status.py [--json] [--tail N]
    status.py --trash-index [--root PATH ...] [--trash PATH ...] [--json]
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
    ap.add_argument("--trash-index", action="store_true",
                    help="read-only quarantine locations for agents and future UI")
    ap.add_argument("--root", action="append", default=[],
                    help="search root for --trash-index (repeatable)")
    ap.add_argument("--trash", action="append", default=[],
                    help="declared external/custom quarantine path (repeatable)")
    ap.add_argument("--max-depth", type=int, default=None,
                    help="directory depth below each search root (default 3)")
    ap.add_argument("--max-entries", type=int, default=None,
                    help="index directory-entry budget (default 10000)")
    args = ap.parse_args()

    base = os.getcwd()
    if args.trash_index:
        from core.trash_index import (
            DEFAULT_MAX_DEPTH, DEFAULT_MAX_ENTRIES, list_trash_locations)
        roots, paths = list(args.root), list(args.trash)
        if not roots and not paths:
            roots = [base]
            configured = os.environ.get("AGENT_GUARD_TRASH")
            if configured:
                paths.append(configured)
        try:
            result = list_trash_locations(
                roots, trash_paths=paths,
                max_depth=(DEFAULT_MAX_DEPTH if args.max_depth is None
                           else args.max_depth),
                max_entries=(DEFAULT_MAX_ENTRIES if args.max_entries is None
                             else args.max_entries))
        except ValueError as exc:
            ap.error(str(exc))
        if args.as_json:
            # ASCII escapes preserve unusual POSIX names in valid UTF-8 JSON.
            print(json.dumps(result, ensure_ascii=True, indent=2))
        else:
            print(f"quarantine locations: {result['count']} candidates, "
                  f"{result['identified_count']} with metadata")
            print(f"scope: depth={result['max_depth']} budget={result['max_entries']}")
            for path in result["roots"]:
                print(f"  root: {json.dumps(path)}")
            for path in result["trash_paths"]:
                print(f"  declared: {json.dumps(path)}")
            for row in result["entries"]:
                print(f"  [{row['status']}] {json.dumps(row['trash_root'])}")
                if row["trash_root"] != row["resolved_trash_root"]:
                    print(f"    resolved: {json.dumps(row['resolved_trash_root'])}")
            for error in result["errors"]:
                print(f"  {error['code']}: {json.dumps(error['path'])}")
            if not result["complete"]:
                print("partial index: inspect scope and errors")
        return 0 if result["complete"] else 1
    if (args.root or args.trash or args.max_depth is not None or
            args.max_entries is not None):
        ap.error("index options require --trash-index")
    workspace = classifier.discover_workspace(base)
    trash_root = os.environ.get(
        "AGENT_GUARD_TRASH", os.path.join(workspace, TRASH_DIRNAME))
    engine = recovery.RecoveryEngine(workspace, trash_root)
    state = policy.load_mode(trash_root)
    # Project display-only identity/time metadata, never authorization state.
    mode_metadata = audit.project({"ts": state["since"], "session": state["set_by"]})
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
        "mode_since": mode_metadata.get("ts"),
        "mode_set_by": mode_metadata.get("session"),
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
          + (f" (since {info['mode_since']} by {info['mode_set_by']})"
             if info["mode_since"] else ""))
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
    # core.audit projects both historical field names and values. Status
    # exposes a smaller summary, with no session or compensation details.
    fields = ("ts", "event", "action", "decision", "code", "txid",
              "check_id", "guard_latency_ms")
    return [{key: record[key] for key in fields if key in record}
            for record in audit.tail(os.path.join(trash_root, AUDIT_NAME), n)]


DECISION_EVENTS = {"check", "enforce-block", "enforce-proceed", "ask",
                   "decision"}


def decision_stats(trash_root):
    """Aggregate projected reason codes for the status result; ask
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
