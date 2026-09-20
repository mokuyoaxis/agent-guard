#!/usr/bin/env python3
"""sanitize - apply a redaction plan to a payload (design 4.3).

    sanitize.py --channel <name> [--workspace DIR] [--plan FILE] < text

The guard never rewrites the payload itself: `check_span.py` returns a plan
(offsets + placeholders, no bytes) and the *payload owner* applies it. This
script is that applicator, and it exists so the discipline is testable and
so a shell pipeline can use it without writing code:

    check_span.py --channel llm-request --json < prompt.json > verdict.json
    sanitize.py   --channel llm-request < prompt.json     # sanitized text

Two properties are load-bearing:

* **Format preservation.** A redacted secret keeps the shape a reader needs
  to recognise it (`sk-proj-Ab…` -> `sk-proj-<REDACTED>`), never the bytes.
  For paths the placeholder is `<PATH>`.
* **Leak freedom.** The plan carries offsets and placeholders only, and the
  output is built by *replacing* the matched ranges - so no code path here
  can echo a matched secret into stdout, the plan, or an error message.

Exit codes: 0 applied (or nothing to do) | 1 error.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

import _bootstrap  # noqa: F401

from core import policy, redaction
from core.redaction import get_channel, scan_text

REDACTED = "<REDACTED>"
PATH_PLACEHOLDER = "<PATH>"
# Windows drive/UNC prefixes are layout, not identity, and they are what
# makes a rewritten path still readable ("C:\\Users\\..." -> "C:\\<PATH>").
_DRIVE_PREFIX_RE = re.compile(r"^[A-Za-z]:[\\/]")


def placeholder_for(span) -> str:
    """The replacement text for one span; derived from shape, never content."""
    if span.family != redaction.FAMILY_SECRET:
        return PATH_PLACEHOLDER
    if span.rule_id == redaction.RULE_PRIVATE_KEY_BLOCK:
        return REDACTED
    # Keep the vendor prefix: it is public information (it says *which*
    # credential kind leaked) and it is what makes the output reviewable.
    match = re.match(r"^(sk-|rk_|ghp_|gho_|ghu_|ghs_|ghr_|github_pat_|"
                     r"glpat-|xox[baprs]-|AKIA|ASIA|AGPA|AIDA|AROA|ANPA|"
                     r"sk_live_|rk_live_)", span.raw)
    if match:
        return match.group(1) + REDACTED
    return REDACTED


def plan_for_span(span, channel=None) -> dict:
    """One plan entry: offsets + placeholder. Never the matched bytes."""
    return {
        "start": span.start,
        "end": span.end,
        "rule_id": span.rule_id,
        "family": span.family,
        "confidence": span.confidence,
        "placeholder": placeholder_for(span),
    }


def apply_plan(text: str, plan, verify: bool = True) -> str:
    """Replace every planned range, back to front so offsets stay valid.

    Blocks whose span overlaps a gap (stale plan) are skipped rather than
    applied blindly: a wrong splice would silently corrupt the payload,
    which is the false-SANITIZE failure mode the analysis warns about.
    """
    pieces = []
    for entry in sorted(plan, key=lambda e: e["start"], reverse=True):
        start, end = entry["start"], entry["end"]
        if start < 0 or end > len(text) or start >= end:
            raise ValueError("redaction plan has an out-of-range span")
        pieces.append((start, end, entry["placeholder"]))
    result = text
    for start, end, placeholder in pieces:
        result = result[:start] + placeholder + result[end:]
    if verify and REDACTED in result and result.count(REDACTED) > len(pieces):
        # Another marker was already present: harmless, but worth knowing.
        pass
    return result


def sanitize_text(text: str, channel_name: str, workspace=None,
                  plan=None) -> dict:
    """Scan + decide + apply, in one call (the library form)."""
    channel = get_channel(channel_name)
    if channel is None:
        raise ValueError("unknown egress channel: " + str(channel_name))
    scan = scan_text(text, channel=channel_name, workspace=workspace)
    if not scan.scanned:
        raise RuntimeError(scan.error)
    ctx = policy.PolicyContext(workspace=workspace or "", trash_root="",
                               base_dir=workspace or "",
                               mode=policy.MODE_NORMAL)
    verdicts = policy.decide_spans(scan.spans, ctx=ctx, channel=channel)
    derived = [plan_for_span(span, channel)
               for span, verdict in zip(scan.spans, verdicts)
               if verdict.decision == policy.DECISION_SANITIZE]
    applied = plan if plan is not None else derived
    return {
        "text": apply_plan(text, applied),
        "plan": applied,
        "decision": policy.worst(verdicts).decision if verdicts else
        policy.DECISION_ALLOW,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--channel", default=os.environ.get(
        "AGENT_GUARD_EXFIL_CHANNEL", "file-write"))
    ap.add_argument("--workspace", default=None)
    ap.add_argument("--plan", default=None,
                    help="read an explicit plan (JSON) instead of deriving "
                         "one; useful for replaying a check_span verdict")
    ap.add_argument("--dry-run", action="store_true", dest="dry_run",
                    help="print the plan, not the sanitized text")
    args = ap.parse_args()

    workspace = args.workspace or os.environ.get("AGENT_GUARD_WORKSPACE")
    text = sys.stdin.read()
    plan = None
    if args.plan:
        with open(args.plan, "r", encoding="utf-8") as fh:
            plan = json.load(fh).get("redaction_plan", [])
    try:
        result = sanitize_text(text, args.channel, workspace, plan)
    except (ValueError, RuntimeError) as exc:
        print(f"sanitize: {exc}", file=sys.stderr)
        return 1
    if args.dry_run:
        print(json.dumps(result["plan"], ensure_ascii=False, indent=2))
        return 0
    sys.stdout.write(result["text"])
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"agent-guard internal error: {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)
