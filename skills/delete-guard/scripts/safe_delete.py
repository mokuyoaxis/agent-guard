#!/usr/bin/env python3
"""safe_delete - the recoverable deletion tool for AI agents.

    safe_delete.py [--json] [--dry-run] [--reason TEXT] PATH...

Globs are expanded here, explicitly, before classification: an opaque
wildcard never reaches the filesystem unexamined. Assessments are best-effort
audited; an unmatched glob returns before audit setup. --dry-run and policy
refusals before compensation leave command targets unchanged, but may create
quarantine/audit metadata and a Git local exclude rule. Policy blocks exit 2
even in dry-run. A failed real compensation can leave partial target changes.
New audit events contain decisions, counts and recovery IDs, not target paths
or free-form --reason text. Exact recovery paths stay in the manifest/result.
"""
from __future__ import annotations

import argparse
import glob as globlib
import json
import os
import shutil
import sys

import _bootstrap  # noqa: F401

from core import AUDIT_NAME, TRASH_DIRNAME, audit
from core import classifier, policy, recovery


def expand_globs(patterns):
    """Expand globs explicitly. Returns (concrete_paths, unmatched_patterns).

    A pattern with no matches is NOT passed on as an opaque wildcard - it is
    reported as 'no matches' instead of triggering a misleading BLOCK.
    """
    out = []
    unmatched = []
    for pattern in patterns:
        matches = globlib.glob(os.path.expanduser(pattern), recursive=True)
        if matches:
            out.extend(sorted(matches))
        elif any(c in pattern for c in ("*", "?", "[")):
            unmatched.append(pattern)
        else:
            out.append(pattern)  # literal path: let policy report the miss
    return out, unmatched


def delete_directly(spec):
    """Physically remove one concrete target (regenerable / trash GC only)."""
    path = spec.resolved
    if spec.is_symlink or spec.is_file:
        os.unlink(path)
        return "deleted"
    if spec.is_dir:
        shutil.rmtree(path)
        return "deleted-tree"
    return "absent"


def safe_delete_audit_event(event, decision, code, target_count, *,
                            phase=None, report=None, outcome=None):
    """Build this writer's metadata; never copy input paths/reasons/errors.

    Codes and outcome labels come from this CLI. Transaction IDs come from
    the recovery engine; exact moved/skipped payloads stay in its journal
    and the CLI result. core.audit projects automatic session metadata.
    """
    entry = {"event": event, "tool": "safe_delete", "decision": decision,
             "code": code, "target_count": target_count}
    if phase is not None:
        entry["phase"] = phase
    if outcome is not None:
        entry["outcome"] = outcome
    if report is not None:
        if report.get("txid"):
            entry["txid"] = report["txid"]
        entry["moved_count"] = len(report.get("moved", []))
        entry["skipped_count"] = len(report.get("skipped", []))
    return entry


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("paths", nargs="+", help="paths or globs to delete")
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--reason", default="",
                    help="why this deletion is needed (text is not persisted)")
    args = ap.parse_args()

    base = os.getcwd()
    workspace = classifier.discover_workspace(base)
    trash_root = os.environ.get(
        "AGENT_GUARD_TRASH", os.path.join(workspace, TRASH_DIRNAME))
    mode = policy.load_mode(trash_root)["mode"]
    ctx = policy.PolicyContext(
        workspace=workspace, trash_root=trash_root, base_dir=base, mode=mode)

    concrete, unmatched = expand_globs(args.paths)
    specs = classifier.classify_paths(concrete, base, workspace, trash_root)
    if not specs and unmatched:
        result = {
            "tool": "safe_delete",
            "mode": mode,
            "workspace": workspace,
            "verdict": {"action": "ALLOW", "code": "ALLOW_NOOP",
                        "reasons": ["no matches for: " + ", ".join(unmatched)]},
            "targets": list(args.paths),
            "dry_run": args.dry_run,
            "outcome": "nothing to do (no matches)",
            "exit": 0,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2) if args.as_json
              else result["outcome"])
        return 0
    verdict = policy.decide_path_batch(specs, ctx, recursive=True)
    engine = recovery.RecoveryEngine(workspace, trash_root)
    relocation_report = None

    result = {
        "tool": "safe_delete",
        "mode": mode,
        "workspace": workspace,
        "verdict": {"decision": verdict.decision, "code": verdict.code,
                    "explanation": verdict.explanation,
                    "reasons": verdict.reasons},
        "targets": [s.raw for s in specs],
        "no_match": unmatched,
        "dry_run": args.dry_run,
    }

    def record(entry, required=False):
        """Append audit after safe layout setup.

        Blocked requests retain their policy decision if audit storage is
        unavailable. Mutation intents use `required=True` and fail closed.
        """
        try:
            engine.ensure_layout()
            audit.append(entry, os.path.join(trash_root, AUDIT_NAME))
            return True
        except Exception as exc:
            if required:
                raise
            result.setdefault("warnings", []).append(
                f"audit unavailable: {exc}")
            return False

    if verdict.blocked:
        record(safe_delete_audit_event(
            "decision", "BLOCK", verdict.code, len(specs)))
        result["exit"] = 2
        print(json.dumps(result, ensure_ascii=False, indent=2) if args.as_json
              else f"BLOCKED [{verdict.code}]: {verdict.reasons}")
        return 2

    # Preflight every real mutation before recording authorization or touching
    # a target. In sandboxes where `.git` is read-only this either confirms an
    # existing ignore rule or fails while all source paths are still intact.
    if not args.dry_run and verdict.code != policy.CODE_ALLOW_NOOP:
        try:
            engine.ensure_layout()
        except Exception as exc:
            result["verdict"] = {
                "decision": "BLOCK",
                "code": policy.CODE_BLOCK_COMPENSATION_FAILED,
                "explanation": policy.EXPLANATIONS[
                    policy.CODE_BLOCK_COMPENSATION_FAILED],
                "reasons": [f"quarantine preflight failed: {exc}"],
            }
            result["outcome"] = "BLOCKED: quarantine preflight failed"
            result["exit"] = 2
            print(json.dumps(result, ensure_ascii=False, indent=2)
                  if args.as_json else result["outcome"])
            return 2

    # Authorization/intent must be durable before direct deletion. Dry-run
    # assessments are best-effort audited but never fail for audit storage.
    if args.dry_run:
        record(safe_delete_audit_event(
            "decision", verdict.decision, verdict.code, len(specs),
            phase="advisory"))
    elif verdict.code != policy.CODE_ALLOW_NOOP:
        try:
            record(safe_delete_audit_event(
                "decision", verdict.decision, verdict.code, len(specs),
                phase="intent"), required=True)
        except Exception as exc:
            result["verdict"] = {
                "decision": "BLOCK",
                "code": policy.CODE_BLOCK_COMPENSATION_FAILED,
                "explanation": policy.EXPLANATIONS[
                    policy.CODE_BLOCK_COMPENSATION_FAILED],
                "reasons": [f"audit intent failed: {exc}"],
            }
            result["outcome"] = "BLOCKED: durable audit intent failed"
            result["exit"] = 2
            print(json.dumps(result, ensure_ascii=False, indent=2)
                  if args.as_json else result["outcome"])
            return 2

    if verdict.code == policy.CODE_ALLOW_NOOP:
        result["outcome"] = "nothing to do"
    elif args.dry_run:
        result["outcome"] = f"would {verdict.decision.lower()} ({verdict.code})"
    elif verdict.code == policy.CODE_ALLOW_REGENERABLE:
        deleted = [delete_directly(s) for s in specs]
        result["outcome"] = "deleted directly (provably regenerable)"
        result["deleted"] = deleted
    elif verdict.code == policy.CODE_ALLOW_TRASH_GC:
        deleted = [delete_directly(s) for s in specs]
        result["outcome"] = "deleted directly (quarantine housekeeping)"
        result["deleted"] = deleted
    else:  # RELOCATE_*
        report = engine.relocate(specs, meta={
            "tool": "safe_delete", "code": verdict.code})
        relocation_report = report
        if report.get("storage_failure"):
            # Hard principle: capacity limits never downgrade to deletion.
            result["verdict"] = {
                "decision": "BLOCK",
                "code": policy.CODE_BLOCK_RELOCATE_FAILED_STORAGE,
                "reasons": ["quarantine storage unavailable; moved targets "
                            "(if any) stay restorable in quarantine"],
            }
            result["outcome"] = "BLOCKED: quarantine cannot accept relocation"
            result["exit"] = 2
            record(safe_delete_audit_event(
                "decision", "BLOCK", policy.CODE_BLOCK_RELOCATE_FAILED_STORAGE,
                len(specs), report=report))
            print(json.dumps(result, ensure_ascii=False, indent=2)
                  if args.as_json else result["outcome"])
            return 2
        if report.get("skipped"):
            result["verdict"] = {
                "decision": "BLOCK",
                "code": policy.CODE_BLOCK_COMPENSATION_FAILED,
                "explanation": policy.EXPLANATIONS[
                    policy.CODE_BLOCK_COMPENSATION_FAILED],
                "reasons": ["relocation did not cover every target: " +
                            repr(report["skipped"][:3])],
            }
            result["outcome"] = (
                "BLOCKED: partial relocation remains recoverable")
            result["txid"] = report["txid"]
            result["moved"] = report["moved"]
            result["skipped"] = report["skipped"]
            result["exit"] = 2
            record(safe_delete_audit_event(
                "decision", "BLOCK", policy.CODE_BLOCK_COMPENSATION_FAILED,
                len(specs), report=report))
            print(json.dumps(result, ensure_ascii=False, indent=2)
                  if args.as_json else result["outcome"])
            return 2
        result["outcome"] = "relocated to quarantine"
        result["txid"] = report["txid"]
        result["moved"] = report["moved"]
        result["skipped"] = report["skipped"]

    if not args.dry_run and verdict.code != policy.CODE_ALLOW_NOOP:
        record(safe_delete_audit_event(
            "outcome", verdict.decision, verdict.code, len(specs),
            phase="complete", report=relocation_report,
            outcome=result.get("outcome")))
    result["exit"] = 0
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(result["outcome"] + (f" txid={result['txid']}" if result.get("txid") else ""))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # internal error: fail loudly, change nothing
        print(f"agent-guard internal error: {exc}", file=sys.stderr)
        sys.exit(1)
