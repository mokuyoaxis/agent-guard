#!/usr/bin/env python3
"""Cleanup Copilot: propose cleanup; explicitly confirm before safe_delete.

Default planning performs no workspace writes. Execution requires a reviewed
plan ID, named selections and interactive terminal acknowledgement. This is
a local workflow, not a security boundary against a same-UID process.
"""
from __future__ import annotations

import sys

# Set before importing project modules: planning must not create bytecode.
sys.dont_write_bytecode = True

import argparse
import json
import os
from pathlib import Path
import subprocess

import _bootstrap  # noqa: F401

from core import classifier, recovery
from core.cleanup import DEFAULT_MAX_DEPTH, DEFAULT_MAX_ENTRIES, plan_cleanup, select_entries
from core.messages import print_gc_guidance


LABELS = {"regenerable": "可再生 / regenerable", "discarded": "明确废弃 / discarded",
          "uncertain": "不确定 / uncertain", "protected": "受保护 / protected"}


def print_plan(plan, *, stream=None, selected=None):
    stream = stream or sys.stdout
    print("Plan: " + plan["plan_id"], file=stream)
    print("Workspace: " + json.dumps(plan["workspace"], ensure_ascii=True), file=stream)
    print("Complete: " + str(plan["complete"]), file=stream)
    print(plan["size_notice"], file=stream)
    rows = plan["entries"] if selected is None else selected
    for category, label in LABELS.items():
        print("\n" + label + ":", file=stream)
        for row in rows:
            if row["category"] != category:
                continue
            size = "unknown" if row["size_bytes"] is None else str(row["size_bytes"]) + " B"
            print("  " + json.dumps(row["path"], ensure_ascii=True)
                  + " | " + size + " | " + row["effect"] + " | " + row["reason"], file=stream)
    for error in plan["errors"]:
        print("Scan error: " + json.dumps(error), file=stream)
    for warning in plan["warnings"]:
        print("Warning: " + warning, file=stream)


def confirm(plan, selected):
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise ValueError("execution requires an interactive terminal; no --yes bypass is provided")
    print_plan(plan, stream=sys.stderr, selected=selected)
    token = "EXECUTE"
    print("\nDelete means permanent removal; quarantine retains recovery copies and disk usage.", file=sys.stderr)
    print("Type " + token + " to execute this selection: ", end="", file=sys.stderr, flush=True)
    return sys.stdin.readline().rstrip("\r\n") == token


def execute_selection(plan, selected, *, runner=None):
    """Only called after confirmation and revalidation; stop on first failure."""
    runner = runner or subprocess.run
    reports = []
    env = dict(os.environ, AGENT_GUARD_WORKSPACE=plan["workspace"],
               AGENT_GUARD_TRASH=plan["trash_root"], PYTHONDONTWRITEBYTECODE="1")
    from core.admission import approve_cleanup
    recovery.RecoveryEngine(plan["workspace"], plan["trash_root"]).ensure_layout()
    env["AGENT_GUARD_CLEANUP_APPROVAL"] = approve_cleanup(
        plan["workspace"], plan["trash_root"],
        [os.path.join(plan["workspace"], entry["path"]) for entry in selected], plan["plan_id"])
    script = str(Path(__file__).resolve().with_name("safe_delete.py"))
    for entry in selected:
        # One explicit target per invocation preserves the per-row policy
        # projection instead of mixing regenerable and valuable targets.
        try:
            proc = runner([sys.executable, "-B", script, "--json", "--", "./" + entry["path"]],
                          cwd=plan["workspace"], env=env, capture_output=True, text=True)
        except OSError as exc:
            reports.append({"path": entry["path"], "exit": 1,
                            "error": "safe_delete could not start: " + str(exc)})
            break
        try:
            report = json.loads(proc.stdout)
            if not isinstance(report, dict):
                raise ValueError("expected a JSON object")
        except ValueError:
            reports.append({"path": entry["path"], "exit": proc.returncode or 1,
                            "error": "safe_delete returned no valid JSON object; inspect recovery state before retrying"})
            break
        reports.append({"path": entry["path"], "exit": proc.returncode, "report": report})
        if proc.returncode:
            break
    failed = any(report["exit"] for report in reports)
    return {"tool": "cleanup", "plan_id": plan["plan_id"],
            "outcome": "stopped_after_failure" if failed else "executed",
            "reports": reports, "exit": 2 if failed else 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", help="workspace root; defaults to the discovered workspace")
    parser.add_argument("--discard", action="append", default=[], metavar="PATH",
                        help="explicitly declare a path/subtree discarded; repeatable")
    parser.add_argument("--protect", action="append", default=[], metavar="PATH",
                        help="protect an additional path/subtree; repeatable")
    parser.add_argument("--max-entries", type=int, default=DEFAULT_MAX_ENTRIES)
    parser.add_argument("--max-depth", type=int, default=DEFAULT_MAX_DEPTH)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--plan-id", help="exact ID from the plan being reviewed")
    parser.add_argument("--select", action="append", default=[], metavar="PATH",
                        help="select an exact selectable plan entry; repeatable")
    args = parser.parse_args(argv)
    try:
        if not args.execute and (args.select or args.plan_id):
            raise ValueError("--select and --plan-id require --execute")
        if args.execute and not args.plan_id:
            raise ValueError("--execute requires the reviewed --plan-id")
        root = args.root or classifier.discover_workspace(os.getcwd())
        options = dict(discard=args.discard, protect=args.protect,
                       max_entries=args.max_entries, max_depth=args.max_depth)
        plan = plan_cleanup(root, **options)
        if not args.execute:
            if args.json:
                print(json.dumps(plan, ensure_ascii=True, indent=2))
            else:
                print_plan(plan)
                print("\nPlanning only. Review selectable entries, then use --execute --plan-id ID --select PATH.")
            return 0 if plan["complete"] else 1
        selected = select_entries(plan, args.select, args.plan_id)
        if not confirm(plan, selected):
            result = {"tool": "cleanup", "plan_id": plan["plan_id"], "outcome": "cancelled", "exit": 0}
        else:
            # Prompting may take time. Recheck membership, metadata, policy,
            # configuration and the workspace before the first mutation.
            fresh = plan_cleanup(root, **options)
            selected = select_entries(fresh, args.select, plan["plan_id"])
            result = execute_selection(fresh, selected)
        if args.json:
            print(json.dumps(result, ensure_ascii=True, indent=2))
        else:
            print(result["outcome"])
            for item in result.get("reports", []):
                report = item.get("report", {})
                print(json.dumps(item["path"]) + ": " + report.get("outcome", item.get("error", "")))
                if report.get("txid"):
                    print("  txid=" + report["txid"])
                if report.get("storage_notice"):
                    print(report["storage_notice"])
                    print_gc_guidance(report["gc"])
        return result["exit"]
    except (ValueError, OSError) as exc:
        if args.json:
            print(json.dumps({"tool": "cleanup", "outcome": "refused", "error": str(exc), "exit": 2}))
        else:
            print("Cleanup refused: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
