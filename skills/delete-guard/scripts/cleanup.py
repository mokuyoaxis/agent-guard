#!/usr/bin/env python3
"""Cleanup Copilot: propose cleanup; explicitly confirm before safe_delete.

Default planning performs no workspace writes. --save-plan explicitly stores
a target proposal; --from-plan reuses its request and selection. An operator
can approve it in a terminal, then an agent executes or queries --task ID.
This local workflow is
not a security boundary against a same-UID process.
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
from core.cleanup_groups import discover_groups, measure_groups
from core.cleanup_saved import load_saved_plan, read_targets_file, refresh_saved_plan, save_plan
from core.cleanup_tasks import TaskStateError, approve_task, execute_task, task_status
from core.cleanup_views import format_cleanup_view, plan_view, read_options, task_view
from core.messages import print_gc_guidance, quarantine_command
from core.operation_summary import execution_summary, format_execution_summary, unavailable_summary


LABELS = {"regenerable": "可再生 / regenerable", "discarded": "明确废弃 / discarded",
          "uncertain": "不确定 / uncertain", "protected": "受保护 / protected"}


def print_groups(result):
    print("Read-only observation; no execution permission. Workspace: "
          + json.dumps(result["workspace"], ensure_ascii=True))
    if "overview" in result:
        for row in result["overview"]:
            print(row["kind"] + ": " + str(row.get("groups", row.get("branches")))
                  + " groups; size unknown")
        coverage = result["coverage"]
        print("Frontier complete: " + str(coverage["frontier_complete"])
              + "; workspace scan complete: False; unexpanded: " + str(len(coverage["unexpanded"])))
        print("Use --json for groups, scopes, issues and unexpanded paths; "
              "continue discovery with explicit --scope PATH.")
    else:
        for row in result["scopes"]:
            size = str(row["size_bytes"]) + " B" if row["size_complete"] else (
                "whole size unknown; observed " + str(row["observed_bytes"]) + " B")
            print(json.dumps(row["path"], ensure_ascii=True) + ": " + size)
    print(result["notice"])


def print_plan(plan, *, stream=None, selected=None):
    stream = stream or sys.stdout
    print("Plan: " + plan["plan_id"], file=stream)
    print("Workspace: " + json.dumps(plan["workspace"], ensure_ascii=True), file=stream)
    print("Complete: " + str(plan["complete"]), file=stream)
    if plan["options"].get("quarantine", False):
        print("Quarantine requested: includes regenerable artifacts; no direct deletion.", file=stream)
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


def confirm(plan, selected, *, approve_only=False):
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise ValueError("operator confirmation requires an interactive terminal; no --yes bypass is provided")
    print_plan(plan, stream=sys.stderr, selected=selected)
    token = "APPROVE" if approve_only else "EXECUTE"
    print("\nDelete means permanent removal; quarantine retains recovery copies and disk usage.", file=sys.stderr)
    action = "approve later execution of" if approve_only else "execute"
    print("Type " + token + " to " + action + " this selection: ", end="", file=sys.stderr, flush=True)
    return sys.stdin.readline().rstrip("\r\n") == token


def execute_selection(plan, selected, *, runner=None):
    """Only called after confirmation and revalidation; stop on first failure."""
    runner = runner or subprocess.run
    reports = []
    env = dict(os.environ, AGENT_GUARD_WORKSPACE=plan["workspace"],
               AGENT_GUARD_TRASH=plan["trash_root"], PYTHONDONTWRITEBYTECODE="1")
    env.pop("AGENT_GUARD_CLEANUP_TASK", None)
    from core.admission import approve_cleanup
    recovery.RecoveryEngine(plan["workspace"], plan["trash_root"]).ensure_layout()
    env["AGENT_GUARD_CLEANUP_APPROVAL"] = approve_cleanup(
        plan["workspace"], plan["trash_root"],
        [os.path.join(plan["workspace"], entry["path"]) for entry in selected], plan["plan_id"])
    script = str(Path(__file__).resolve().with_name("safe_delete.py"))
    deletion_options = ["--quarantine"] if plan["options"].get("quarantine", False) else []
    for entry in selected:
        # One explicit target per invocation preserves the per-row policy
        # projection instead of mixing regenerable and valuable targets.
        try:
            proc = runner([sys.executable, "-B", script, "--json", *deletion_options, "--", "./" + entry["path"]],
                          cwd=plan["workspace"], env=env, capture_output=True, text=True)
        except OSError as exc:
            reports.append({"path": entry["path"], "exit": 1,
                            "started": False,
                            "error": "safe_delete could not start: " + str(exc)})
            break
        try:
            report = json.loads(proc.stdout)
            if not isinstance(report, dict):
                raise ValueError("expected a JSON object")
        except ValueError:
            reports.append({"path": entry["path"], "exit": proc.returncode or 1,
                            "started": True,
                            "error": "safe_delete returned no valid JSON object; inspect recovery state before retrying"})
            break
        reports.append({"path": entry["path"], "exit": proc.returncode, "report": report, "started": True})
        if proc.returncode:
            break
    failed = any(report["exit"] for report in reports)
    return {"tool": "cleanup", "plan_id": plan["plan_id"],
            "outcome": "stopped_after_failure" if failed else "executed",
            "reports": reports, "requested_roots": len(selected), "exit": 2 if failed else 0}


def saved_plan_output(saved, as_json, *, options=None):
    options = options or read_options("plan", view="full")
    command = [sys.executable, str(Path(__file__).resolve()),
               "--root", saved["plan"]["workspace"], "--from-plan",
               saved["saved_plan_id"], "--approve"]
    command = quarantine_command(command, saved["plan"]["workspace"], saved["plan"]["trash_root"])
    selection_ready = bool(saved["selection"]) and saved["plan"]["complete"]
    result = {**saved, "refreshed": False, "next_step": {
        "argv": command if selection_ready else None, "cwd": saved["plan"]["workspace"],
        "notice": ("Saved proposal only. Keep the same quarantine/session settings; an operator must approve it in a terminal before an agent can execute the returned task ID."
                   if selection_ready else "No complete selectable selection. Review the recorded reasons and create a new proposal; no execution is suggested.")}}
    if as_json:
        print(json.dumps(plan_view(result, options), ensure_ascii=True, indent=2))
    elif options["view"] != "full":
        print(format_cleanup_view(plan_view(result, options)))
        import shlex
        if selection_ready:
            print("After review, in an operator terminal: " + shlex.join(command))
    else:
        print("Saved proposal (not refreshed): " + saved["saved_plan_id"])
        print("Workspace: " + json.dumps(saved["plan"]["workspace"]))
        print("Requested targets: " + str(len(saved["plan"]["targets"]))
              + "; proposed selection: " + str(len(saved["selection"])))
        print(json.dumps({"plan": saved["plan"], "selection": saved["selection"]},
                         ensure_ascii=True, indent=2))
        import shlex
        if selection_ready:
            print("After review, in an operator terminal: " + shlex.join(command))
        print(result["next_step"]["notice"])


def task_output(result, as_json, *, options=None):
    options = options or read_options("task", view="full" if as_json else "summary")
    command = [sys.executable, str(Path(__file__).resolve()), "--root", result["workspace"],
               "--task", result["task_id"], "--json", "--view", "summary"]
    command = quarantine_command(command, result["workspace"], result["trash_root"])
    result["next_step"] = {"status_argv": command,
                           "execute_argv": [*command, "--execute"] if result["can_execute"] else None,
                           "cwd": result["workspace"],
                           "notice": "Keep the approved quarantine/session settings. Never repeat an unresolved task or generate approval on the operator's behalf."}
    projected = task_view(result, options)
    if as_json or options["view"] == "full":
        print(json.dumps(projected, ensure_ascii=True, indent=2))
    else:
        print(format_cleanup_view(projected))
        print("Query: --task " + result["task_id"] + " --view summary")
        if result["can_execute"]:
            print("Approved execution: --task " + result["task_id"] + " --execute")


def plan_output(plan, as_json, options):
    projected = plan_view(plan, options)
    if as_json:
        print(json.dumps(projected, ensure_ascii=True, indent=2))
    elif options["view"] == "full":
        print_plan(plan)
    else:
        print(format_cleanup_view(projected))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", help="workspace root; defaults to the discovered workspace")
    parser.add_argument("--discard", action="append", default=[], metavar="PATH",
                        help="explicitly declare a path/subtree discarded; repeatable")
    parser.add_argument("--protect", action="append", default=[], metavar="PATH",
                        help="protect an additional path/subtree; repeatable")
    parser.add_argument("--quarantine", action="store_true",
                        help="plan quarantine even for regenerable artifacts; saved plans/tasks inherit this choice")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--discover", action="store_true", help="read-only directory overview; no executable plan")
    modes.add_argument("--measure", action="store_true", help="read-only metadata sizes for explicit directory scopes")
    parser.add_argument("--scope", action="append", default=[], metavar="PATH",
                        help="directory below the fixed root; repeatable, for --discover or --measure")
    parser.add_argument("--max-entries", type=int, help="entry budget; defaults: plan/discover 10000, measure 20000")
    parser.add_argument("--max-depth", type=int, help="depth budget; defaults: plan 32, discover 4, measure 32")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--view", choices=("summary", "details", "full"),
                        help="output only; plan/task text defaults summary, JSON/legacy execution full")
    parser.add_argument("--category", help="plan detail category; does not change selection")
    parser.add_argument("--path", dest="detail_path", help="literal relative detail path/subtree; no rescan")
    parser.add_argument("--state", help="task details: confirmed, unconfirmed or not_dispatched")
    parser.add_argument("--offset", type=int, help="detail page offset; default 0")
    parser.add_argument("--limit", type=int, help="detail page size; default 50, maximum 200")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--execute", action="store_true")
    action.add_argument("--approve", action="store_true", help="operator-terminal approval of a saved plan; no source deletion")
    parser.add_argument("--plan-id", help="exact ID from the plan being reviewed")
    parser.add_argument("--select", action="append", default=[], metavar="PATH",
                        help="select an exact selectable plan entry; repeatable")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--target", action="append", default=[], metavar="PATH",
                        help="literal target root; scan only these roots, repeatable")
    source.add_argument("--targets-file", metavar="JSON",
                        help="JSON array of literal target paths below the same workspace")
    source.add_argument("--from-plan", metavar="ID",
                        help="view, approve or execute a saved target proposal in this workspace")
    source.add_argument("--task", metavar="ID", help="query an approved task, or attempt it once with --execute")
    parser.add_argument("--discard-targets", action="store_true",
                        help="explicitly declare every provided target discarded; not approval")
    parser.add_argument("--save-plan", action="store_true",
                        help="save target proposal and selection; no deletion or approval")
    args = parser.parse_args(argv)
    try:
        observing = args.discover or args.measure
        target_mode = bool(args.target) or args.targets_file is not None
        if observing and any(value is not None for value in (
                args.view, args.category, args.detail_path, args.state, args.offset, args.limit)):
            raise ValueError("view controls apply to plans/tasks, not discovery or measurement")
        view_options = read_options(
            "execution" if args.execute or args.approve else "task" if args.task is not None else "plan",
            view=args.view or ("full" if args.json or (args.execute and args.task is None and args.from_plan is None)
                               else "summary"),
            category=args.category, path=args.detail_path, state=args.state, offset=args.offset, limit=args.limit)
        if observing and (args.execute or args.approve or args.plan_id or args.select or args.discard or args.protect
                          or target_mode or args.from_plan is not None or args.task is not None or args.discard_targets or args.save_plan
                          or args.quarantine):
            raise ValueError("read-only discovery/measurement cannot accept plan declarations or execution arguments")
        if args.scope and not observing:
            raise ValueError("--scope requires --discover or --measure; it does not narrow an executable plan")
        if observing:
            root = args.root or classifier.discover_workspace(os.getcwd())
            limits = {name: value for name, value in (("max_entries", args.max_entries), ("max_depth", args.max_depth))
                      if value is not None}
            result = (discover_groups(root, args.scope, **limits) if args.discover
                      else measure_groups(root, args.scope, **limits))
            if args.json:
                print(json.dumps(result, ensure_ascii=True, indent=2))
            else:
                print_groups(result)
            complete = (result["coverage"]["frontier_complete"] if args.discover
                        else all(row["observed_subset_complete"] for row in result["scopes"]))
            return 0 if complete else 1
        if args.approve and args.from_plan is None:
            raise ValueError("--approve requires --from-plan ID and operator-terminal confirmation")
        if args.task is not None:
            if (args.plan_id or args.select or args.discard or args.protect or args.discard_targets
                    or args.save_plan or args.max_entries is not None or args.max_depth is not None or args.quarantine):
                raise ValueError("tasks keep the approved request; query first, then review a new proposal for unresolved targets")
            root = args.root or classifier.discover_workspace(os.getcwd())
            result = execute_task(root, args.task) if args.execute else task_status(root, args.task)
            task_output(result, args.json, options=view_options)
            return result["exit"]
        if args.from_plan is not None:
            if (args.plan_id or args.select or args.discard or args.protect or args.discard_targets
                    or args.save_plan or args.max_entries is not None or args.max_depth is not None or args.quarantine):
                raise ValueError("saved proposals keep their targets, options and selection; create a new proposal to change them")
            root = args.root or classifier.discover_workspace(os.getcwd())
            saved = load_saved_plan(root, args.from_plan)
            if not args.execute and not args.approve:
                saved_plan_output(saved, args.json, options=view_options)
                return 0
            plan, selected = refresh_saved_plan(saved)
            if not confirm(plan, selected, approve_only=args.approve):
                result = {"tool": "cleanup", "plan_id": plan["plan_id"],
                          "outcome": "cancelled", "exit": 0}
            else:
                # Approval performs its own post-confirmation refresh. It
                # writes control metadata but never deletes source content.
                result = approve_task(saved)
                if args.execute:
                    result = execute_task(root, result["task_id"])
                task_output(result, args.json, options=view_options)
                return result["exit"]
            result["saved_plan_id"] = saved["saved_plan_id"]
            result["execution_summary"] = execution_summary("cleanup", result)
            if args.json or view_options["view"] == "full":
                print(json.dumps(result, ensure_ascii=True, indent=2))
            else:
                print(format_execution_summary(result["execution_summary"]))
            return result["exit"]
        if (args.discard_targets or args.save_plan) and not target_mode:
            raise ValueError("--discard-targets and --save-plan require --target or --targets-file")
        if target_mode:
            if args.execute or args.plan_id or (args.select and not args.save_plan):
                raise ValueError("save a target proposal first, then use --from-plan ID --approve")
            targets = args.target
            if args.targets_file is not None:
                targets = read_targets_file(args.targets_file)
            root = args.root or classifier.discover_workspace(os.getcwd())
            options = dict(discard=[*args.discard, *(targets if args.discard_targets else [])],
                           protect=args.protect, quarantine=args.quarantine,
                           max_entries=DEFAULT_MAX_ENTRIES if args.max_entries is None else args.max_entries,
                           max_depth=DEFAULT_MAX_DEPTH if args.max_depth is None else args.max_depth)
            plan = plan_cleanup(root, targets=targets, **options)
            if args.save_plan:
                saved = save_plan(plan, args.select or None)
                saved_plan_output(saved, args.json, options=view_options)
            else:
                plan_output(plan, args.json, view_options)
                if not args.json:
                    print("Proposal only. Use --save-plan to retain the request and selection.")
            return 0 if plan["complete"] else 1
        if not args.execute and (args.select or args.plan_id):
            raise ValueError("--select and --plan-id require --execute")
        if args.execute and not args.plan_id:
            raise ValueError("--execute requires the reviewed --plan-id")
        root = args.root or classifier.discover_workspace(os.getcwd())
        options = dict(discard=args.discard, protect=args.protect, quarantine=args.quarantine,
                       max_entries=DEFAULT_MAX_ENTRIES if args.max_entries is None else args.max_entries,
                       max_depth=DEFAULT_MAX_DEPTH if args.max_depth is None else args.max_depth)
        plan = plan_cleanup(root, **options)
        if not args.execute:
            plan_output(plan, args.json, view_options)
            if not args.json:
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
        result["execution_summary"] = execution_summary("cleanup", result)
        if args.json and view_options["view"] == "full":
            print(json.dumps(result, ensure_ascii=True, indent=2))
        elif args.json:
            print(json.dumps({key: value for key, value in result.items() if key != "reports"}))
        elif view_options["view"] == "summary":
            print(format_execution_summary(result["execution_summary"]))
        else:
            print(format_execution_summary(result["execution_summary"]))
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
        context = ({"task_id": exc.task_id, "may_have_changed": exc.may_have_changed,
                    "outcome": "state_unavailable"} if isinstance(exc, TaskStateError) else {})
        failure = {"tool": "cleanup", "outcome": "refused", "error": str(exc), "exit": 2, **context}
        if args.execute or args.approve or args.task is not None:
            failure["execution_summary"] = (unavailable_summary("cleanup") if args.task is not None and not args.execute
                                             else execution_summary("cleanup", failure))
        if args.json:
            print(json.dumps(failure))
        else:
            print("Cleanup refused: " + str(exc), file=sys.stderr)
            if "execution_summary" in failure:
                print(format_execution_summary(failure["execution_summary"]), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
