#!/usr/bin/env python3
"""check_span - scan an outbound payload before it leaves the machine.

    check_span.py --channel <name> [--workspace DIR] [--mode M] [--json] < text

This is deliberately a *separate* entry point from `check.py`: that one
classifies a shell command line (its `--` REMAINDER convention, its dialect
resolution, its compensation phase). A text payload has a different input, a
smaller decision set (no RELOCATE/SNAPSHOT - the guard cannot rewrite what
it did not write) and no compensation phase at all.

It is a pure function of its input: it never mutates the payload and never
writes a file, so "apply the plan" stays the caller's decision. The output
carries offsets, rule ids and placeholders - **never the matched bytes** -
which is why `--json` output is safe to paste into a CI log.

Decisions (design 1.3): ALLOW | SANITIZE | ASK | BLOCK.

Exit codes: 0 ALLOW/SANITIZE (advisory ok) | 2 BLOCK | 3 ASK | 1 ERROR.

`--json` payload:
    {"decision": "SANITIZE", "code": "SANITIZE_SECRET_REDACT",
     "spans": [{"start": 104, "end": 151, "rule_id": "secret/openai-key",
                "confidence": "deterministic"}],
     "redaction_plan": [{"start": 104, "end": 151,
                         "placeholder": "sk-<REDACTED>"}]}
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import _bootstrap  # noqa: F401

from core import redaction
from core.redaction import (
    CHANNELS, UNREACHABLE_CHANNELS, get_channel, scan_text,
)
from core import policy


def build_redaction_plan(spans, decisions, channel) -> list:
    """Offsets + placeholder only; the caller applies it (design 4.3)."""
    from sanitize import plan_for_span          # same directory
    plan = []
    for span, verdict in zip(spans, decisions):
        if verdict.decision != policy.DECISION_SANITIZE:
            continue
        plan.append(plan_for_span(span, channel))
    return plan


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--channel", default=os.environ.get(
        "AGENT_GUARD_EXFIL_CHANNEL", "file-write"),
        help="egress channel: " + ", ".join(sorted(CHANNELS)))
    ap.add_argument("--workspace", default=None,
                    help="workspace root (default: AGENT_GUARD_WORKSPACE "
                         "or discovery from cwd)")
    ap.add_argument("--mode", default=None, choices=["NORMAL", "RESTRICTED"])
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--max-bytes", type=int, default=None,
                    help="override the scan size cap")
    ap.add_argument("--path", default=None,
                    help="path the payload will be written to; enables the "
                         "repo-local exemption file for that path")
    args = ap.parse_args()

    workspace = args.workspace
    if workspace is None:
        from core.classifier import discover_workspace
        workspace = discover_workspace(os.getcwd())

    mode = args.mode
    if mode is None:
        trash_root = os.environ.get(
            "AGENT_GUARD_TRASH", os.path.join(workspace, ".agent-trash"))
        try:
            mode = policy.load_mode(trash_root)["mode"]
        except Exception:                    # noqa: BLE001 - state is advisory
            mode = policy.MODE_NORMAL

    channel_name = (args.channel or "").strip().lower()
    channel = get_channel(channel_name)

    try:
        text = sys.stdin.read()
    except (OSError, UnicodeDecodeError) as exc:
        return _fail(f"could not read stdin: {exc}", args.as_json)

    out = {
        "channel": channel_name,
        "channel_known": channel is not None,
        "channel_rewritable": bool(channel and channel.rewritable),
        "channel_persistence": channel.persistence if channel else None,
        "workspace": workspace,
        "mode": mode,
        "size_bytes": len(text.encode("utf-8", errors="replace")),
    }

    if channel is None:
        # An unknown channel name is a configuration defect, and a channel
        # the guard cannot name is one it cannot certify. Fail closed.
        if channel_name in UNREACHABLE_CHANNELS:
            out["reason"] = ("channel is unreachable by design; exfil-guard "
                             "makes no claim to cover it")
        else:
            out["reason"] = ("unknown egress channel; expected one of: "
                             + ", ".join(sorted(CHANNELS)))
        return _emit(out, policy.DECISION_BLOCK,
                     policy.CODE_BLOCK_OUTPUT_UNSCANNABLE, [], [], args, 2)

    scan = scan_text(text, channel=channel_name, workspace=workspace,
                     max_bytes=args.max_bytes, path=args.path)
    out["scanned"] = scan.scanned
    out["exempted"] = scan.exempted
    if args.path:
        out["path"] = args.path
    if not scan.scanned:
        out["reason"] = scan.error
        return _emit(out, policy.DECISION_BLOCK,
                     policy.CODE_BLOCK_OUTPUT_UNSCANNABLE, [], [], args, 2)

    ctx = policy.PolicyContext(workspace=workspace, trash_root="",
                              base_dir=workspace, mode=mode)
    verdicts = policy.decide_spans(scan.spans, ctx=ctx, channel=channel)

    # De-duplicate the per-span narratives: one payload gets one verdict,
    # and the aggregate must not depend on how many spans matched.
    top = (policy.worst(verdicts) if verdicts else
           policy.Verdict(policy.DECISION_ALLOW,
                          policy.CODE_ALLOW_NOOP,
                          explanation="No secret or host path matched."))

    spans_out = []
    for span, verdict in zip(scan.spans, verdicts):
        record = span.safe()
        record["decision"] = verdict.decision
        record["code"] = verdict.code
        spans_out.append(record)

    plan = build_redaction_plan(scan.spans, verdicts, channel)
    exit_code = {policy.DECISION_BLOCK: 2, policy.DECISION_ASK: 3}.get(
        top.decision, 0)
    return _emit(out, top.decision, top.code, spans_out, plan, args, exit_code,
                 top)


def _emit(out, decision, code, spans, plan, args, exit_code,
          top=None) -> int:
    out["decision"] = decision
    out["code"] = code
    out["explanation"] = (top.explanation if top is not None
                          else policy.EXPLANATIONS.get(code, code))
    out["reasons"] = list(top.reasons) if top is not None else []
    out["spans"] = spans
    out["redaction_plan"] = plan
    out["exit"] = exit_code
    if args.as_json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(f"{decision} [{code}] {len(spans)} span(s) on "
              f"channel {out.get('channel')}")
        if out.get("explanation"):
            print(f"  {out['explanation']}")
        for span in spans:
            print(f"  - {span['rule_id']} [{span['start']},{span['end']}] "
                  f"{span['decision']}")
    return exit_code


def _fail(message, as_json) -> int:
    if as_json:
        print(json.dumps({"error": message, "exit": 1}, ensure_ascii=False))
    else:
        print(f"exfil-guard error: {message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # fail closed, never a traceback with payload
        print(f"agent-guard internal error: {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)
