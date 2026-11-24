#!/usr/bin/env python3
"""Read a workspace-relative text file with whole-file redaction and line pagination."""
from __future__ import annotations

import json
import os
import sys
import time

sys.dont_write_bytecode = True
import _bootstrap  # noqa: F401

from core import classifier, exfil_audit, policy, safe_read as safe_read_core
from _payload import InvalidArguments, SafeArgumentParser


def _invalid_json_result() -> dict:
    return {
        "schema": safe_read_core.SCHEMA,
        "status": "error",
        "decision": policy.DECISION_BLOCK,
        "code": policy.CODE_BLOCK_OUTPUT_UNSCANNABLE,
        "error_code": "INVALID_ARGUMENT",
        "message": safe_read_core.ERROR_MESSAGES["INVALID_ARGUMENT"],
        "path": None,
        "text": "",
        "spans": [],
        "exit": 1,
    }


def main(argv=None) -> int:
    started = time.monotonic()
    parser = SafeArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=None,
                        help="workspace root (default: discover from cwd)")
    parser.add_argument("--start-line", type=int, default=1,
                        help="1-based starting line (default: 1)")
    parser.add_argument("--end-line", type=int, default=None,
                        help="1-based inclusive ending line (default: up to 200 lines from start-line)")
    parser.add_argument("--strict-paths", action="store_true",
                        help="also rewrite generic absolute paths to <PATH>")
    parser.add_argument("--fail-on-redaction", action="store_true",
                        help="refuse the whole file if any content requires redaction")
    parser.add_argument("--audit", action="store_true",
                        help="record page decision metadata in the shared audit log")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="return structured safe_read/v1 JSON")
    parser.add_argument("path", help="workspace-relative POSIX file path")
    try:
        args = parser.parse_args(argv)
    except InvalidArguments:
        print(json.dumps(_invalid_json_result(), ensure_ascii=True))
        return 1

    workspace = args.workspace or classifier.discover_workspace(os.getcwd())
    result = safe_read_core.safe_read(
        workspace,
        args.path,
        start_line=args.start_line,
        end_line=args.end_line,
        strict_paths=args.strict_paths,
        fail_on_redaction=args.fail_on_redaction,
    )
    audit_enabled = exfil_audit.enabled(args.audit)
    if audit_enabled:
        if result["exit"] == 0:
            audit_payload = {
                "channel": safe_read_core.CHANNEL_NAME,
                "decision": result["decision"],
                "code": result["code"],
                "exit": 0,
                "spans": result["spans"],
                "span_count": len(result["spans"]),
                "redaction_plan": result["spans"],
                "size_bytes": result["returned_bytes"],
                "exempted": 0,
            }
        else:
            audit_payload = {
                "channel": safe_read_core.CHANNEL_NAME,
                "decision": result["decision"],
                "code": result["code"],
                "exit": result["exit"],
                "spans": [],
                "span_count": 0,
                "redaction_plan": [],
                "size_bytes": 0,
                "exempted": 0,
            }
        if not os.path.isdir(workspace):
            result["audit"] = {"enabled": True, "recorded": False, "error": "AUDIT_WRITE_FAILED"}
        else:
            result["audit"] = exfil_audit.record(
                os.path.realpath(os.path.abspath(workspace)),
                "exfil_safe_read",
                "complete",
                audit_payload,
                started=started,
                active=True,
            )
    else:
        result["audit"] = {"enabled": False, "recorded": False}
    if "error" in result["audit"]:
        print(exfil_audit.WRITE_WARNING, file=sys.stderr)

    if args.as_json:
        print(json.dumps(result, ensure_ascii=True))
        return result["exit"]
    if result["exit"] == 0:
        sys.stdout.write(result["text"])
        return 0
    print(f"safe_read: {result['decision']} [{result['error_code']}] {result['message']}",
          file=sys.stderr)
    return result["exit"]


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"agent-guard internal error: {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)
