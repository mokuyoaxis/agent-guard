#!/usr/bin/env python3
"""restore - inspect or undo a guarded deletion transaction.

    restore.py list [--json]
    restore.py preview TXID [--path PATH ...] [--force] [--json]
    restore.py [restore] TXID [--path PATH ...] [--preview] [--force] [--json] [--view summary|full]

Preview is strictly read-only: it never writes files, manifests or audit records.
Restore is non-destructive by default: it refuses to overwrite anything now
occupying an origin path. --force preserves that occupant in a separate
transaction before restoring. Inspect backup_txids after success or failure.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys

sys.dont_write_bytecode = True
import _bootstrap  # noqa: F401

from core import AUDIT_NAME, TRASH_DIRNAME, audit
from core import classifier, policy, recovery
from core.identifiers import unique_prefix
from core.operation_summary import execution_summary, format_execution_summary, unavailable_summary


_PREFIX_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")


def context():
    base = os.getcwd()
    workspace = classifier.discover_workspace(base)
    trash_root = os.environ.get(
        "AGENT_GUARD_TRASH", os.path.join(workspace, TRASH_DIRNAME))
    return recovery.RecoveryEngine(workspace, trash_root)


def resolve_txid(engine, raw_txid):
    """Resolve an exact transaction ID or unique CLI prefix without mutating state."""
    if not isinstance(raw_txid, str) or not _PREFIX_RE.fullmatch(raw_txid):
        return raw_txid
    txs = engine.transactions()
    if raw_txid in txs:
        return raw_txid
    match = unique_prefix(raw_txid, txs.keys(), "transaction")
    return match if match is not None else raw_txid


def restore_audit_event(txid, force, report, *, selective=False):
    """Non-recovery metadata only; exact paths/errors stay in the result/journal."""
    event = {
        "event": "restore", "force": force, "ok": report.get("ok"),
        "selective": selective, "selected_count": len(report.get("selected_paths", [])),
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


def _format_size(size):
    return f"{size}B" if isinstance(size, int) else "-"


def print_preview(report):
    if report.get("error"):
        print(f"error: {report['error']}", file=sys.stderr)
        return
    summary = report["summary"]
    print(f"restore preview: {report['txid']} ({report['state']})")
    print(f"quarantine     : {report['trash_root']}")
    if "selected_paths" in report and not report.get("error"):
        print(
            f"selection      : {len(report['selected_paths'])} selected "
            f"(omitted: {report.get('omitted_relocate_count', 0)} relocations, "
            f"{report.get('omitted_snapshot_count', 0)} snapshots)"
        )
    print(
        f"summary        : ready={summary['ready_count']} "
        f"conflicts={summary['conflict_count']} "
        f"(identical={summary['identical_conflict_count']}) "
        f"blocked={summary.get('blocked_count', 0)} "
        f"missing={summary['missing_copy_count']} "
        f"snapshots={summary['snapshot_count']} "
        f"already_restored={summary['already_restored_count']} "
        f"retained_snapshots={summary['retained_snapshot_count']} "
        f"manual_review={summary['manual_review_count']}"
    )
    print(
        f"can_restore    : {'yes' if report['can_restore'] else 'no'} "
        f"(with --force: {'yes' if report['can_restore_with_force'] else 'no'})"
    )
    for item in report["items"]:
        if item["type"] == "relocate":
            line = f"  [{item['status']}] {item['origin_path']}"
            if item["status"] == "conflict" and item.get("comparison"):
                comp = item["comparison"]
                q = item["quarantine_entry"]
                o = item["occupant_entry"]
                equal = (
                    "identical" if comp["content_equal"] is True else
                    "different" if comp["content_equal"] is False else
                    comp["basis"]
                )
                line += (f" (quarantine={q['kind']}:{_format_size(q['size_bytes'])}, "
                         f"current={o['kind']}:{_format_size(o['size_bytes'])}, {equal})")
                if item.get("trash_path"):
                    line += f"\n      quarantine copy: {item['trash_path']}"
            elif item.get("note"):
                line += f" ({item['note']})"
            print(line)
        else:
            print(f"  [snapshot:{item['status']}] sha={item.get('sha') or 'none'} ({item['note']})")
    for row in report["manual_review"]:
        exists = "copy present" if row["payload_exists"] else "copy absent"
        print(f"  [manual_review] {row['origin_path']} -> {row['trash_path']} ({exists}; {row['error']})")
    for err in report["errors"]:
        print(f"error: {err}", file=sys.stderr)
    following = report.get("next_step")
    if following:
        print(f"next           : {following['message']}")
        if report["can_restore"]:
            print(f"restore cmd    : {following['restore_command']}")
        elif following.get("force_restore_command"):
            print(f"force cmd      : {following['force_restore_command']}")
        print(following["note"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    lp = sub.add_parser("list")
    lp.add_argument("--json", action="store_true", dest="as_json")
    pp = sub.add_parser("preview")
    pp.add_argument("txid")
    pp.add_argument("--path", dest="paths", action="append",
                    help="select a recorded origin path (repeat for multiple entries)")
    pp.add_argument("--force", action="store_true")
    pp.add_argument("--exact-txid", action="store_true",
                    help="require a complete transaction ID (disable CLI prefix matching)")
    pp.add_argument("--json", action="store_true", dest="as_json")
    rp = sub.add_parser("restore")
    rp.add_argument("txid")
    rp.add_argument("--path", dest="paths", action="append",
                    help="select a recorded origin path (repeat for multiple entries)")
    rp.add_argument("--preview", action="store_true",
                    help="read-only restore preflight without modifying files or audit logs")
    rp.add_argument("--force", action="store_true")
    rp.add_argument("--exact-txid", action="store_true",
                    help="require a complete transaction ID (disable CLI prefix matching)")
    rp.add_argument("--json", action="store_true", dest="as_json")
    rp.add_argument("--view", choices=("summary", "full"),
                    help="output only; full by default, summary explicitly omits detailed rows")
    # Allow the ergonomic form: restore.py <txid> == restore.py restore <txid>
    argv = sys.argv[1:]
    if argv and argv[0] not in ("list", "preview", "restore", "-h", "--help"):
        argv = ["restore"] + argv
    args = ap.parse_args(argv)
    paths = getattr(args, "paths", None)
    if paths is not None and any(not path for path in paths):
        ap.error("--path requires a nonempty literal path")

    engine = context()

    if args.cmd == "list":
        txs = engine.transactions()
        usage = engine.usage()
        listing = [{"txid": t["txid"], "ts": t.get("ts"),
                    "items": len(t["items"]),
                    "restorable_items": t.get("restorable_items", 0),
                    "manual_review_items": t["manual_review_items"],
                    **{field: t[field] for field in recovery.RESTORE_ITEM_COUNTS},
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
                review = item["manual_review_items"]
                print(f"  {item['txid']}  {item['state']:<10} "
                      f"{item['restorable_items']:>3}/{item['items']} items  "
                      f"{','.join(item['strategies'])}"
                      + (f"  {review} item(s) require manual review" if review else ""))
        return 0

    is_preview = args.cmd == "preview" or getattr(args, "preview", False)
    if getattr(args, "exact_txid", False):
        txid = args.txid
    else:
        try:
            txid = resolve_txid(engine, args.txid)
        except ValueError as exc:
            if args.as_json:
                payload = {"ok": False, "error": str(exc)}
                if is_preview:
                    payload["schema"] = "restore_preview/v1"
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                print(f"error: {exc}", file=sys.stderr)
            return 2

    selector_cwd = os.getcwd() if paths is not None else None
    if is_preview:
        report = engine.preview_restore(txid, force=args.force, paths=paths, cwd=selector_cwd)
        if args.as_json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print_preview(report)
        return 0 if report.get("ok") else 1

    report = engine.restore(txid, force=args.force, cwd=selector_cwd, paths=paths)
    event = restore_audit_event(txid, args.force, report, selective=paths is not None)
    try:
        audit.append(event, os.path.join(engine.trash_root, AUDIT_NAME))
    except OSError:
        report["ok"] = False
        report.setdefault("errors", []).append(
            "restore audit could not be stored; inspect filesystem state")
    report["execution_summary"] = execution_summary("restore", report, reference=txid)
    view = args.view or "full"
    if args.as_json and view == "full":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif args.as_json:
        print(json.dumps({"execution_summary": report["execution_summary"]}, ensure_ascii=True, indent=2))
    elif view == "summary":
        print(format_execution_summary(report["execution_summary"]))
    else:
        print(format_execution_summary(report["execution_summary"]))
        if "selected_paths" in report and not report.get("error"):
            print(
                f"selection: {len(report['selected_paths'])} selected "
                f"(omitted: {report.get('omitted_relocate_count', 0)} relocations, "
                f"{report.get('omitted_snapshot_count', 0)} snapshots)"
            )
        for backup_id in report.get("backup_txids", []):
            print(f"current-version preservation transaction: {backup_id} (inspect state)")
        for path in report.get("restored", []):
            print(f"restored: {path}")
        for path in report.get("already_restored", []):
            print(f"already restored: {path}")
        for sha in report.get("already_applied", []):
            print(f"stash already applied: {sha}")
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
        print(format_execution_summary(unavailable_summary("restore")), file=sys.stderr)
        sys.exit(1)
