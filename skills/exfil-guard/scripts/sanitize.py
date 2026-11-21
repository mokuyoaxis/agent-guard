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

import json
import os
import re
import sys
import time

sys.dont_write_bytecode = True
import _bootstrap  # noqa: F401

from core import policy, redaction
from core.redaction import get_channel, scan_text
from _payload import (InvalidArguments, PayloadTooLarge, SafeArgumentParser, StdinIdle,
                      audit_result, emit_exemption_diagnostics, read_bounded_text,
                      read_payload, resolve_context)

REDACTED = "<REDACTED>"
PATH_PLACEHOLDER = "<PATH>"
_REDACTION_PREFIX_RE = re.compile(
    r"^(sk-|rk_|ghp_|gho_|ghu_|ghs_|ghr_|github_pat_|"
    r"glpat-|xox[baprs]-|pypi-|AKIA|ASIA|sk_live_|rk_live_|"
    + redaction.MODERN_VENDOR_PREFIX_PATTERN + r")")
# Windows drive/UNC prefixes are layout, not identity, and they are what
# makes a rewritten path still readable ("C:\\Users\\..." -> "C:\\<PATH>").
_DRIVE_PREFIX_RE = re.compile(r"^[A-Za-z]:[\\/]")


def placeholder_for(span) -> str:
    """The replacement text for one span; derived from shape, never content."""
    if span.family != redaction.FAMILY_SECRET:
        return PATH_PLACEHOLDER
    if span.rule_id in (redaction.RULE_PRIVATE_KEY_BLOCK,
                        redaction.RULE_CONNECTION_PASSWORD, redaction.RULE_HTTP_BASIC_AUTH):
        return REDACTED
    # Keep the vendor prefix: it is public information (it says *which*
    # credential kind leaked) and it is what makes the output reviewable.
    match = _REDACTION_PREFIX_RE.match(span.raw)
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


class Unscannable(RuntimeError):
    """A scan or the rewritten payload could not be certified."""


def _scan(text, channel, workspace, exemption):
    try:
        result = scan_text(text, channel=channel.name, workspace=workspace, exemption=exemption)
    except (OSError, ValueError, RuntimeError):
        raise Unscannable("payload could not be scanned") from None
    if not result.scanned:
        raise Unscannable("payload could not be scanned")
    return result


def sanitize_text(text: str, channel_name: str, workspace=None, plan=None) -> dict:
    """Scan + decide + apply, in one call (the library form)."""
    channel = get_channel(channel_name)
    if channel is None:
        raise ValueError("unknown egress channel")
    workspace, mode = resolve_context(workspace)
    return _sanitize(text, channel, workspace, mode, plan)


def _sanitize(text, channel, workspace, mode, plan, *, structured=False):
    exemption = redaction.load_exemption(workspace)
    scan = _scan(text, channel, workspace, exemption)
    ctx = policy.PolicyContext(workspace=workspace or "", trash_root="",
                               base_dir=workspace or "",
                               mode=mode)
    verdicts = policy.decide_spans(scan.spans, ctx=ctx, channel=channel)
    top = policy.worst(verdicts) if verdicts else policy.Verdict(policy.DECISION_ALLOW, policy.CODE_ALLOW_NOOP)
    derived = [plan_for_span(span, channel)
               for span, verdict in zip(scan.spans, verdicts)
               if verdict.decision == policy.DECISION_SANITIZE]
    result = {"text": "", "plan": derived, "decision": top.decision, "code": top.code,
              "spans": redaction.verdict_rows(scan.spans, verdicts), "exemption": exemption.safe(),
              "exempted": scan.exempted}
    if top.decision not in (policy.DECISION_ALLOW, policy.DECISION_SANITIZE):
        return result
    applied = derived if plan is None else _checked_plan(text, plan, derived)
    rewritten = apply_plan(text, applied)
    checked = _scan(rewritten, channel, workspace, exemption)
    remaining = policy.decide_spans(checked.spans, ctx=ctx, channel=channel)
    if remaining and policy.worst(remaining).decision != policy.DECISION_ALLOW:
        raise Unscannable("sanitized payload did not pass verification")
    if structured and checked.exempted:
        # A policy exemption is not permission to return a known secret to
        # a JSON consumer, including a model using the MCP tool.
        unfiltered = _scan(rewritten, channel, workspace, redaction.Exemption())
        if any(span.family == redaction.FAMILY_SECRET for span in unfiltered.spans):
            return {**result, "decision": policy.DECISION_BLOCK,
                    "code": policy.CODE_BLOCK_OUTPUT_UNSCANNABLE, "plan": []}
    return {**result, "text": rewritten, "plan": applied}


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
    started = time.monotonic()
    ap = SafeArgumentParser(description=__doc__)
    ap.add_argument("--channel", default=os.environ.get(
        "AGENT_GUARD_EXFIL_CHANNEL", "file-write"))
    ap.add_argument("--workspace", default=None)
    ap.add_argument("--plan", default=None,
                    help="read an explicit plan (JSON) instead of deriving "
                         "one; useful for replaying a check_span verdict")
    output = ap.add_mutually_exclusive_group()
    output.add_argument("--dry-run", action="store_true", help="print the plan, not the sanitized text")
    output.add_argument("--json", action="store_true", help="return a structured result and safe diagnostics")
    ap.add_argument("--audit", action="store_true", help="record decision metadata in the shared audit log")
    try:
        args = ap.parse_args()
    except InvalidArguments:
        return _fail("invalid arguments", True)

    workspace, mode = resolve_context(args.workspace)
    channel = get_channel(args.channel)
    out = {"channel": channel.name if channel else None, "workspace": workspace, "mode": mode}
    try:
        text = read_payload()
        out["size_bytes"] = len(text.encode("utf-8"))
        if channel is None:
            raise Unscannable("unknown egress channel")
        plan = _read_plan(args.plan) if args.plan else None
        result = _sanitize(text, channel, workspace, mode, plan, structured=args.json)
    except (PayloadTooLarge, Unscannable) as exc:
        exit_code = 2 if args.json or isinstance(exc, PayloadTooLarge) else 1
        out.update(decision=policy.DECISION_BLOCK, code=policy.CODE_BLOCK_OUTPUT_UNSCANNABLE,
                   text="", spans=[], redaction_plan=[], exemption=None, exit=exit_code)
        audit_result(out, "exfil_sanitize", "complete", started, args.audit)
        if args.json:
            print(json.dumps(out, ensure_ascii=True))
        elif exit_code == 1:
            return _fail("payload or plan validation failed", False)
        else:
            print("sanitize: BLOCK [BLOCK_OUTPUT_UNSCANNABLE] input exceeds cap", file=sys.stderr)
        return exit_code
    except StdinIdle:
        return _fail("no payload arrived on stdin within the wait", args.json)
    except (OSError, UnicodeError, ValueError, RecursionError):
        return _fail("payload or plan validation failed", args.json)
    exit_code = {policy.DECISION_BLOCK: 2, policy.DECISION_ASK: 3}.get(result["decision"], 0)
    out.update(result)
    out["redaction_plan"] = out.pop("plan")
    out["exit"] = exit_code
    if exit_code:
        out.update(text="", redaction_plan=[])
    audit_result(out, "exfil_sanitize", "complete", started, args.audit)
    if args.json:
        print(json.dumps(out, ensure_ascii=True))
        return exit_code
    emit_exemption_diagnostics(result["exemption"])
    if exit_code:
        print("sanitize: " + result["decision"] + " payload withheld", file=sys.stderr)
        return exit_code
    if args.dry_run:
        print(json.dumps(result["plan"], ensure_ascii=False, indent=2))
        return 0
    sys.stdout.write(result["text"])
    return 0


def _fail(message, as_json):
    if as_json:
        print(json.dumps({"error": message, "exit": 1}))
    else:
        print("sanitize: " + message, file=sys.stderr)
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"agent-guard internal error: {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)
