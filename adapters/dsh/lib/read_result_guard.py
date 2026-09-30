#!/usr/bin/env python3
"""Bounded stdin/stdout worker for the opt-in DSH text-read prototype.

Only the shared Core decides what is secret. No raw data in argv, files,
diagnostics or policy explanations. Successful stdout belongs to the adapter.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "skills" / "exfil-guard" / "scripts"))

from core import policy  # noqa: E402
from sanitize import apply_plan, sanitize_text  # noqa: E402

MAX_INPUT_BYTES = 1024 * 1024
MAX_OUTPUT_BYTES = 2 * MAX_INPUT_BYTES
MAX_INTEGER = 2 ** 53 - 1


def blocked(code):
    return {"kind": "block", "code": code}


def integer(value, minimum=0):
    return type(value) is int and minimum <= value <= MAX_INTEGER


def validate_value(value):
    if not isinstance(value, dict) or set(value) != {"path", "offset", "lines", "totalLines"}:
        raise ValueError("schema")
    if not isinstance(value["path"], str) or any(c in value["path"] for c in "\n\r\x00"):
        raise ValueError("schema")
    if not integer(value["offset"], 1) or not integer(value["totalLines"]):
        raise ValueError("schema")
    lines = value["lines"]
    if not isinstance(lines, list) or len(lines) > 20000:
        raise ValueError("schema")
    for index, line in enumerate(lines):
        if not isinstance(line, dict) or set(line) != {"number", "text"}:
            raise ValueError("schema")
        if not integer(line["number"], 1) or line["number"] != value["offset"] + index:
            raise ValueError("schema")
        if not isinstance(line["text"], str) or "\n" in line["text"]:
            raise ValueError("schema")
    # Strict UTF-8; escaped lone surrogates must never become a clean result.
    value["path"].encode("utf-8", errors="strict")
    for line in lines:
        line["text"].encode("utf-8", errors="strict")


def redact(text, workspace, keep_lines=False):
    result = sanitize_text(text, "llm-request", workspace=workspace)
    if result["decision"] not in (policy.DECISION_ALLOW, policy.DECISION_SANITIZE):
        raise PermissionError("policy")
    plan = result["plan"]
    if keep_lines:
        # Keep DSH's original line numbers, including multiline key blocks.
        # The entire original span is removed; blank continuation lines only
        # preserve layout. The Core applicator owns all offset replacement.
        plan = [dict(entry, placeholder=entry["placeholder"] +
                     "\n" * text[entry["start"]:entry["end"]].count("\n"))
                for entry in plan]
    replaced = apply_plan(text, plan)
    checked = sanitize_text(replaced, "llm-request", workspace=workspace)
    if checked["decision"] != policy.DECISION_ALLOW or checked["plan"]:
        raise PermissionError("verification")
    return replaced, len(plan)


def protect(request):
    try:
        if not isinstance(request, dict) or set(request) != {"workspace", "value"}:
            return blocked("READ_RESULT_SCHEMA")
        workspace = request["workspace"]
        if not isinstance(workspace, str) or not Path(workspace).is_absolute():
            return blocked("READ_RESULT_SCHEMA")
        workspace.encode("utf-8", errors="strict")
        value = request["value"]
        validate_value(value)
        if value["offset"] != 1 or len(value["lines"]) != value["totalLines"]:
            return blocked("READ_RESULT_INCOMPLETE")
        # Native read truncates long lines before policy receives them.
        if any("... (line truncated to " in line["text"] for line in value["lines"]):
            return blocked("READ_RESULT_INCOMPLETE")
        filename, path_count = redact(value["path"], workspace)
        text = "\n".join(line["text"] for line in value["lines"])
        text, line_count = redact(text, workspace, keep_lines=True)
        texts = text.split("\n") if value["lines"] else []
        if len(texts) != len(value["lines"]):
            return blocked("READ_RESULT_SCHEMA")
        output = dict(value, path=filename, lines=[
            {"number": line["number"], "text": sanitized}
            for line, sanitized in zip(value["lines"], texts)])
        count = path_count + line_count
        return {"kind": "accept", "value": output,
                "decision": "SANITIZE" if count else "ALLOW", "redactions": count}
    except PermissionError:
        return blocked("READ_RESULT_POLICY")
    except (ValueError, TypeError, UnicodeError):
        return blocked("READ_RESULT_SCHEMA")
    except Exception:
        return blocked("READ_RESULT_SCAN_FAILED")


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate field")
        value[key] = item
    return value


def main():
    try:
        payload = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if len(payload) > MAX_INPUT_BYTES:
            result = blocked("READ_RESULT_SIZE")
        else:
            request = json.loads(payload.decode("utf-8", errors="strict"),
                                 object_pairs_hook=unique_object,
                                 parse_constant=lambda _: (_ for _ in ()).throw(ValueError("constant")))
            result = protect(request)
        encoded = json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(encoded) > MAX_OUTPUT_BYTES:
            encoded = json.dumps(blocked("READ_RESULT_SIZE")).encode("ascii")
    except Exception:
        encoded = json.dumps(blocked("READ_RESULT_SCAN_FAILED")).encode("ascii")
    sys.stdout.buffer.write(encoded + b"\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
