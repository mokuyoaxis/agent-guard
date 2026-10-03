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
  to recognise it (`sk-proj-Ab…` -> `sk-<REDACTED>`), never the bytes.
  For paths the placeholder is `<PATH>`.
* **Leak freedom.** The plan carries offsets and placeholders only, and the
  output is built by *replacing* the matched ranges - so no code path here
  can echo a matched secret into stdout, the plan, or an error message.

Exit codes: 0 allow/applied | 2 block | 3 ask | 1 error.
Refusals and errors emit no payload. External plans are revalidated against
the current scan; they are never an authorization to bypass its verdict.
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
from _payload import (PayloadTooLarge, StdinIdle, read_bounded_text,
                      read_payload, resolve_context)

REDACTED = "<REDACTED>"
PATH_PLACEHOLDER = "<PATH>"
# Windows drive/UNC prefixes are layout, not identity, and they are what
# makes a rewritten path still readable ("C:\\Users\\..." -> "C:\\<PATH>").
_DRIVE_PREFIX_RE = re.compile(r"^[A-Za-z]:[\\/]")


def placeholder_for(span) -> str:
    """The replacement text for one span; derived from shape, never content."""
    if span.family != redaction.FAMILY_SECRET:
        return PATH_PLACEHOLDER
    if span.rule_id in (redaction.RULE_PRIVATE_KEY_BLOCK,
                        redaction.RULE_CONNECTION_PASSWORD):
        return REDACTED
    # Keep the vendor prefix: it is public information (it says *which*
    # credential kind leaked) and it is what makes the output reviewable.
    match = re.match(r"^(sk-|rk_|ghp_|gho_|ghu_|ghs_|ghr_|github_pat_|"
                     r"glpat-|xox[baprs]-|pypi-|AKIA|ASIA|"
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


def _plan_pieces(text, plan):
    """Validate every range before constructing any output."""
    if not isinstance(plan, list):
        raise ValueError("redaction plan must be a list")
    pieces = []
    for entry in plan:
        if not isinstance(entry, dict) or not {"start", "end", "placeholder"}.issubset(entry):
            raise ValueError("invalid redaction plan entry")
        start, end = entry["start"], entry["end"]
        if type(start) is not int or type(end) is not int:
            raise ValueError("redaction offsets must be integers")
        if start < 0 or end > len(text) or start >= end:
            raise ValueError("redaction plan has an out-of-range span")
        if not isinstance(entry["placeholder"], str) or not entry["placeholder"]:
            raise ValueError("invalid redaction placeholder")
        pieces.append((start, end, entry["placeholder"]))
    pieces.sort()
    for previous, current in zip(pieces, pieces[1:]):
        if current[0] < previous[1]:
            raise ValueError("redaction plan has overlapping spans")
    return pieces


def apply_plan(text: str, plan, verify: bool = True) -> str:
    """Use validated original coordinates to assemble output in one pass.

    `verify` is retained for callers. Structural validation is mandatory;
    only `sanitize_text` can verify a plan against a fresh policy decision.
    """
    pieces = _plan_pieces(text, plan)
    chunks = []
    cursor = 0
    for start, end, placeholder in pieces:
        chunks.extend((text[cursor:start], placeholder))
        cursor = end
    chunks.append(text[cursor:])
    return "".join(chunks)


def _checked_plan(text, plan, derived):
    """Only a plan equivalent to the current scanner's plan may be replayed."""
    # Structural validation is independent of policy/metadata checks.
    _plan_pieces(text, plan)
    expected = sorted(derived, key=lambda entry: entry["start"])
    supplied = sorted(plan, key=lambda entry: entry["start"])
    if len(supplied) != len(expected):
        raise ValueError("redaction plan does not match the current scan")
    for entry, actual in zip(supplied, expected):
        if set(entry) - set(actual):
            raise ValueError("invalid redaction plan fields")
        if any(entry.get(key) != actual[key] for key in ("start", "end", "placeholder")):
            raise ValueError("redaction plan does not match the current scan")
        if any(value != actual[key] for key, value in entry.items()):
            raise ValueError("redaction plan metadata does not match the current scan")
    return expected


def sanitize_text(text: str, channel_name: str, workspace=None,
                  plan=None) -> dict:
    """Scan + decide + apply, in one call (the library form)."""
    channel = get_channel(channel_name)
    if channel is None:
        raise ValueError("unknown egress channel")
    workspace, mode = resolve_context(workspace)
    exemption = redaction.load_exemption(workspace)
    scan = scan_text(text, channel=channel_name, workspace=workspace,
                     exemption=exemption)
    if not scan.scanned:
        raise RuntimeError("payload could not be scanned")
    ctx = policy.PolicyContext(workspace=workspace or "", trash_root="",
                               base_dir=workspace or "",
                               mode=mode)
    verdicts = policy.decide_spans(scan.spans, ctx=ctx, channel=channel)
    decision = (policy.worst(verdicts).decision if verdicts else
                policy.DECISION_ALLOW)
    derived = [plan_for_span(span, channel)
               for span, verdict in zip(scan.spans, verdicts)
               if verdict.decision == policy.DECISION_SANITIZE]
    if decision not in (policy.DECISION_ALLOW, policy.DECISION_SANITIZE):
        return {"text": "", "plan": derived, "decision": decision}
    applied = derived if plan is None else _checked_plan(text, plan, derived)
    rewritten = apply_plan(text, applied)
    checked = scan_text(rewritten, channel=channel_name, workspace=workspace,
                        exemption=exemption)
    if not checked.scanned:
        raise RuntimeError("sanitized payload could not be scanned")
    remaining = policy.decide_spans(checked.spans, ctx=ctx, channel=channel)
    if remaining and policy.worst(remaining).decision != policy.DECISION_ALLOW:
        raise RuntimeError("sanitized payload did not pass verification")
    return {
        "text": rewritten,
        "plan": applied,
        "decision": decision,
    }


def _unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("duplicate plan field")
        obj[key] = value
    return obj


def _read_plan(filename):
    with open(filename, "rb") as stream:
        text = read_bounded_text(stream, redaction.max_scan_bytes())
    obj = json.loads(text, object_pairs_hook=_unique_object)
    if not isinstance(obj, dict) or not isinstance(obj.get("redaction_plan"), list):
        raise ValueError("invalid plan document")
    return obj["redaction_plan"]


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

    try:
        workspace, _ = resolve_context(args.workspace)
        text = read_payload()
        plan = _read_plan(args.plan) if args.plan else None
        result = sanitize_text(text, args.channel, workspace, plan)
    except PayloadTooLarge:
        print("sanitize: BLOCK [BLOCK_OUTPUT_UNSCANNABLE] input exceeds cap",
              file=sys.stderr)
        return 2
    except StdinIdle:
        print("sanitize: no payload arrived on stdin within the wait", file=sys.stderr)
        return 1
    except (OSError, UnicodeError, ValueError, RuntimeError):
        print("sanitize: payload or plan validation failed", file=sys.stderr)
        return 1
    if result["decision"] in (policy.DECISION_BLOCK, policy.DECISION_ASK):
        print("sanitize: " + result["decision"] + " payload withheld", file=sys.stderr)
        return 2 if result["decision"] == policy.DECISION_BLOCK else 3
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
