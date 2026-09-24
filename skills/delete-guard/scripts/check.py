#!/usr/bin/env python3
"""check - classify a shell command before it runs (harness adapter entry).

    check.py [--cwd DIR] [--enforce] [--json]
             [--dialect {posix,cmd,powershell}] -- COMMAND...

Advisory mode (default): prints the verdict and does not mutate command
targets. It records the assessment when audit storage is available.
--enforce: performs the compensations first (relocate targets / git snapshot
/ git-clean enumeration+relocation) and tells the caller to PROCEED, or
refuses with BLOCKED. Any BLOCK in the line means nothing is executed.
--dialect selects the lexical front end for COMMAND: `posix` (default,
unchanged behaviour), `cmd`, or `powershell`. Aliases such as `pwsh` and
`cmd.exe` are accepted. The dialect is NOT validated by argparse's
`choices` on purpose: an unknown selector must produce an explicit BLOCK
decision (with a reason code), not an argparse usage error that a harness
could mistake for "nothing to worry about".
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import _bootstrap  # noqa: F401

from core import AUDIT_NAME, TRASH_DIRNAME
from core import audit, classifier, dialects, policy, recovery


_REDACTED_COMMAND = "<redacted>"
_SAFE_OPS = frozenset({
    "rm", "rmdir", "unlink", "shred", "find", "git", "del", "erase",
    "rd", "ri", "remove-item",
})


def _safe_op(op: str) -> str:
    """Only emit known verb names, never a verb copied unchecked from input."""
    name = op.lower()
    return name if name in _SAFE_OPS else "<opaque>"


class EnumerationUnavailable(Exception):
    """The target set could not be enumerated, so the effect is unknown.

    Deliberately NOT a compensation failure: nothing was attempted and
    nothing was mutated. `git clean -fdx` outside a repository (or with a
    `git` that cannot run) has an unknown blast radius, which is the same
    fact as every other effect-uncertainty shape - so it takes the same
    verdict (policy.md row 11b), not COMPENSATION_FAILED.
    """


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cwd")
    ap.add_argument("--enforce", action="store_true")
    ap.add_argument("--json", action="store_true", dest="as_json")
    # No argparse `choices`: an unknown dialect must reach the policy layer
    # as an explicit BLOCK, not as a usage error (see module docstring).
    ap.add_argument("--dialect", default=None, metavar="{posix,cmd,powershell}",
                    help="shell dialect of COMMAND (default: posix)")
    ap.add_argument("command", nargs=argparse.REMAINDER)
    args = ap.parse_args()
    cmd_tokens = [t for t in args.command if t != "--"]
    cmd = " ".join(cmd_tokens)

    base = os.path.abspath(args.cwd) if args.cwd else os.getcwd()
    workspace = classifier.discover_workspace(base)
    trash_root = os.environ.get(
        "AGENT_GUARD_TRASH", os.path.join(workspace, TRASH_DIRNAME))
    mode = policy.load_mode(trash_root)["mode"]
    ctx = policy.PolicyContext(
        workspace=workspace, trash_root=trash_root, base_dir=base, mode=mode)
    engine = recovery.RecoveryEngine(workspace, trash_root)
    audit_path = os.path.join(trash_root, AUDIT_NAME)

    started = time.monotonic()

    # Resolve the dialect BEFORE classifying: an unusable selector means the
    # command line was never classified, which is a BLOCK (with its own
    # reason code) - never a silent fallback to POSIX.
    resolution = dialects.resolve_dialect(
        args.dialect if args.dialect is not None
        else os.environ.get("AGENT_GUARD_DIALECT"),
        source="flag --dialect" if args.dialect is not None
        else "env AGENT_GUARD_DIALECT")

    if resolution.ok:
        specs, parse_error = classifier.classify_command(
            cmd, resolution.dialect)
        verdicts = policy.decide_ops(specs, ctx) if not parse_error else [
            policy.Verdict(policy.DECISION_BLOCK,
                           policy.CODE_BLOCK_UNDETERMINABLE_EFFECT,
                           [f"parse error: {parse_error}"])]
    else:
        # No classification happened; the only safe verdict is BLOCK.
        specs, parse_error = [], None
        verdicts = [policy.decide_dialect_failure(resolution)]

    top = policy.worst(verdicts)
    latency_ms = round((time.monotonic() - started) * 1000, 1)
    check_id = audit.new_txid()
    requested = resolution.requested
    if requested is None:
        safe_requested = None
    elif resolution.ok and isinstance(requested, str):
        safe_requested = requested.strip().lower() or None
    else:
        safe_requested = "<redacted>"

    out = {
        "command": _REDACTED_COMMAND,
        "check_id": check_id,
        "mode": mode,
        "dialect": resolution.dialect,
        "dialect_requested": safe_requested,
        "dialect_outcome": "ok" if resolution.ok else "unusable",
        "decision": top.decision,
        "code": top.code,
        "explanation": policy.EXPLANATIONS.get(top.code, "Guard decision."),
        # Policy notes can contain raw target spellings or parser fragments.
        # The stable reason code and static explanation are the public detail.
        "reasons": [],
        "guard_latency_ms": latency_ms,
        "ops": [{"op": _safe_op(s.op), "kind": s.kind,
                 "dialect": getattr(s, "dialect", resolution.dialect),
                 "undeterminable": s.undeterminable}
                for s in specs],
        "enforced": bool(args.enforce),
    }
    if not resolution.ok:
        out["dialect_error"] = ("invalid dialect selector" if
                                resolution.kind == "invalid" else
                                "unknown dialect selector")

    def finish(code):
        out["exit"] = code
        if args.as_json:
            print(json.dumps(out, ensure_ascii=False, indent=2))
        else:
            summary = f"{out['decision']} [{out['code']}]"
            print(summary)
            for reason in out["reasons"][:4]:
                print(f"  - {reason}")
        return code

    def record(entry):
        """Append enforcement audit without ever changing the verdict.

        Establishing the ignore rule before the audit directory prevents a
        blocked request from dirtying a fresh Git worktree. If audit storage
        is unavailable, keep the safer decision and report the degradation.
        """
        try:
            engine.ensure_layout()
            audit.append({"check_id": check_id, **entry}, audit_path)
            return True
        except Exception:
            out.setdefault("warnings", []).append(
                "audit unavailable: record could not be stored")
            return False

    if not args.enforce:
        record({"event": "check", "decision": top.decision,
                "code": top.code, "command": _REDACTED_COMMAND,
                "dialect": resolution.dialect,
                "dialect_requested": safe_requested,
                "dialect_outcome": "ok" if resolution.ok else "unusable",
                "guard_latency_ms": latency_ms})
        return finish(0)

    if top.asks:
        # Single-execution authorization point. Adapters map this to their
        # native ask UI; a harness without ask support degrades to deny
        # while keeping the explanation (never silently allow).
        record({"event": "ask", "code": top.code,
                "command": _REDACTED_COMMAND, "reasons": out["reasons"]})
        return finish(3)

    if top.blocked:
        record({"event": "enforce-block", "code": top.code,
                "command": _REDACTED_COMMAND, "reasons": out["reasons"],
                "guard_latency_ms": latency_ms})
        return finish(2)

    # Execute compensations in order; collect evidence of recoverability.
    compensations = []
    try:
        # Preflight quarantine metadata before enumeration or any mutation.
        # This also excludes `.agent-trash` before `git clean -n` can see it.
        mutating_codes = {
            policy.CODE_ALLOW_REGENERABLE,
            policy.CODE_ALLOW_TRASH_GC,
            policy.CODE_RELOCATE_PATHS,
            policy.CODE_RELOCATE_TREE,
            policy.CODE_RELOCATE_NARROW,
            policy.CODE_RELOCATE_VIA_CLEAN_ENUMERATE,
            policy.CODE_SNAPSHOT_GIT_STASH,
        }
        if any(verdict.code in mutating_codes for verdict in verdicts):
            engine.ensure_layout()
        for spec, verdict in zip(specs, verdicts):
            if spec.kind == classifier.KIND_FS_DELETE and \
                    verdict.decision == policy.DECISION_RELOCATE:
                target_specs = classifier.classify_paths(
                    spec.targets, base, workspace, trash_root)
                report = engine.relocate(target_specs, meta={
                    "tool": "check --enforce", "check_id": check_id,
                    "command": _REDACTED_COMMAND})
                if report.get("storage_failure"):
                    raise recovery.StorageUnavailable(
                        "quarantine storage unavailable during relocation")
                if report.get("skipped"):
                    raise RuntimeError(
                        "relocation did not cover every requested target: " +
                        repr(report["skipped"][:3]))
                compensations.append({"strategy": "relocate",
                                      "txid": report["txid"],
                                      "moved": len(report["moved"])})
            elif verdict.code == policy.CODE_RELOCATE_VIA_CLEAN_ENUMERATE:
                flags = getattr(spec, "extra_flags", [])
                paths, err = engine.enumerate_git_clean(
                    base, flags, getattr(spec, "targets", []))
                if err:
                    raise EnumerationUnavailable(
                        f"git clean enumeration failed: {err}")
                target_specs = classifier.classify_paths(
                    paths, base, workspace, trash_root)
                report = engine.relocate(target_specs, meta={
                    "tool": "check --enforce", "strategy": "clean-enumerate",
                    "check_id": check_id, "command": _REDACTED_COMMAND})
                if report.get("storage_failure"):
                    raise recovery.StorageUnavailable(
                        "quarantine storage unavailable during git clean")
                if report.get("skipped") or len(report["moved"]) != len(paths):
                    raise RuntimeError(
                        "git clean compensation did not cover every enumerated "
                        f"target (enumerated={len(paths)}, "
                        f"moved={len(report['moved'])}, "
                        f"skipped={report.get('skipped', [])[:3]})")
                compensations.append({"strategy": "clean-enumerate",
                                      "txid": report["txid"],
                                      "moved": len(report["moved"])})
            elif verdict.code == policy.CODE_SNAPSHOT_GIT_STASH:
                snap = engine.snapshot_git(cwd=base, meta={
                    "tool": "check --enforce", "check_id": check_id,
                    "command": _REDACTED_COMMAND})
                if not snap.get("ok"):
                    raise RuntimeError(
                        "git snapshot failed: " +
                        (snap.get("error") or "unknown snapshot failure"))
                compensations.append({"strategy": "snapshot",
                                      "txid": snap["txid"],
                                      "sha": snap["sha"]})
    except EnumerationUnavailable:
        # The targets exist but their set is unknowable; refusing is the
        # only honest verdict. Nothing was mutated, so this is not a
        # compensation failure and must not be reported as one.
        out["decision"], out["code"] = "BLOCK", \
            policy.CODE_BLOCK_UNDETERMINABLE_EFFECT
        out["explanation"] = policy.EXPLANATIONS[out["code"]]
        out["reasons"] = ["git clean enumeration failed"]
        record({"event": "enforce-block", "code": out["code"],
                "command": _REDACTED_COMMAND, "reasons": out["reasons"]})
        return finish(2)
    except recovery.StorageUnavailable:
        # Hard principle: never fall back to permanent deletion.
        out["decision"], out["code"] = "BLOCK", \
            policy.CODE_BLOCK_RELOCATE_FAILED_STORAGE
        out["explanation"] = policy.EXPLANATIONS[out["code"]]
        out["reasons"] = ["quarantine storage unavailable"]
        record({"event": "enforce-block", "code": out["code"],
                "command": _REDACTED_COMMAND, "reasons": out["reasons"]})
        return finish(2)
    except Exception:  # compensation failed: refuse to proceed
        out["decision"], out["code"] = "BLOCK", \
            policy.CODE_BLOCK_COMPENSATION_FAILED
        out["explanation"] = policy.EXPLANATIONS[out["code"]]
        out["reasons"] = ["compensation failed"]
        record({"event": "enforce-error", "command": _REDACTED_COMMAND,
                "code": out["code"], "error": "compensation failed"})
        return finish(2)

    out["compensations"] = compensations
    out["decision"] = "ALLOW"
    record({"event": "enforce-proceed", "command": _REDACTED_COMMAND,
            "compensations": compensations,
            "guard_latency_ms": latency_ms})
    return finish(0)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        print("agent-guard internal error", file=sys.stderr)
        sys.exit(1)
