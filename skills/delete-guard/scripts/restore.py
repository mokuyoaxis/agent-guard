#!/usr/bin/env python3
"""restore - undo a guarded deletion transaction.

    restore.py list [--json]
    restore.py TXID [--force] [--json]

Restore is non-destructive by default: it refuses to overwrite anything now
occupying an origin path. --force preserves that occupant in a separate
transaction before restoring. Inspect backup_txids after success or failure.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

import _bootstrap  # noqa: F401

from core import AUDIT_NAME, TRASH_DIRNAME, audit
from core import classifier, policy, recovery


def context():
    base = os.getcwd()
    workspace = classifier.discover_workspace(base)
    trash_root = os.environ.get(
        "AGENT_GUARD_TRASH", os.path.join(workspace, TRASH_DIRNAME))
    return recovery.RecoveryEngine(workspace, trash_root)


def restore_audit_event(txid, force, report):
    """Non-recovery metadata only; exact paths/errors stay in the result/journal."""
    event = {
        "event": "restore", "force": force, "ok": report.get("ok"),
        "restored_count": len(report.get("restored", [])),
        "conflict_count": len(report.get("conflicts", [])),
        "error_count": len(report.get("errors", [])) + bool(report.get("error")),
    }
    if report.get("error"):
        # Invalid/unknown caller input is not an established transaction ID.
        # Keep correlation without copying arbitrary input into the audit log.
        event["txid_sha256"] = hashlib.sha256(
            txid.encode("utf-8", errors="surrogatepass")).hexdigest()
    else:
        event["txid"] = txid
    if report.get("backup_txids"):
        event["backup_txids"] = report["backup_txids"]
    return event


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    lp = sub.add_parser("list")
    lp.add_argument("--json", action="store_true", dest="as_json")
    rp = sub.add_parser("restore")
    rp.add_argument("txid")
    rp.add_argument("--force", action="store_true")
    rp.add_argument("--json", action="store_true", dest="as_json")
    # Allow the ergonomic form: restore.py <txid> == restore.py restore <txid>
    argv = sys.argv[1:]
    if argv and argv[0] not in ("list", "restore", "-h", "--help"):
        argv = ["restore"] + argv
    args = ap.parse_args(argv)

    engine = context()

    if args.cmd == "list":
        txs = engine.transactions()
        usage = engine.usage()
        listing = [{"txid": t["txid"], "ts": t.get("ts"),
                    "items": len(t["items"]),
                    "restorable_items": t.get("restorable_items", 0),
                    "state": t.get("state", "EMPTY"),
                    "strategies": sorted({i.get("type") for i in t["items"]})}
                   for t in txs.values()]
        if args.as_json:
            print(json.dumps({"transactions": listing, "usage": usage},
                             ensure_ascii=False, indent=2))
        else:
            print(f"quarantine: {engine.trash_root}")
            print(f"files={usage['files']} bytes={usage['bytes']} "
                  f"transactions={usage['transactions']}")
            for item in listing[-20:]:
                print(f"  {item['txid']}  {item['state']:<10} "
                      f"{item['restorable_items']:>3}/{item['items']} items  "
                      f"{','.join(item['strategies'])}")
        return 0

    report = engine.restore(args.txid, force=args.force)
    event = restore_audit_event(args.txid, args.force, report)
    try:
        audit.append(event, os.path.join(engine.trash_root, AUDIT_NAME))
    except OSError:
        report["ok"] = False
        report.setdefault("errors", []).append(
            "restore audit could not be stored; inspect filesystem state")
    if args.as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for txid in report.get("backup_txids", []):
            print(f"current-version preservation transaction: {txid} (inspect state)")
        for path in report.get("restored", []):
            print(f"restored: {path}")
        for path in report.get("conflicts", []):
            print(f"conflict (exists; use --force): {path}")
        for err in report.get("errors", []):
            print(f"error: {err}", file=sys.stderr)
        if report.get("error"):
            print(f"error: {report['error']}", file=sys.stderr)
    if report.get("errors"):
        return 1
    if report.get("conflicts"):
        return 3
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"agent-guard internal error: {exc}", file=sys.stderr)
        sys.exit(1)
