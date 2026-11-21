#!/usr/bin/env python3
"""Read a Bash command from stdin and return a value-free Exfil decision."""
from __future__ import annotations

import json
import os
import sys

sys.dont_write_bytecode = True
import _bootstrap  # noqa: F401

from core import exfil_audit, exfil_bash, redaction
from _payload import InvalidArguments, PayloadTooLarge, SafeArgumentParser, StdinIdle, read_payload


def main() -> int:
    parser = SafeArgumentParser(description=__doc__)
    parser.add_argument("--workspace", help="command working directory (default: cwd)")
    parser.add_argument("--json", action="store_true", help="return structured findings")
    parser.add_argument("--audit", action="store_true", help="record decision metadata")
    try:
        args = parser.parse_args()
    except InvalidArguments:
        print(json.dumps({"error": "invalid arguments", "exit": 2}))
        return 2
    try:
        command = read_payload(max_bytes=min(redaction.max_scan_bytes(), redaction.DEFAULT_MAX_SCAN_BYTES))
    except (OSError, UnicodeError, PayloadTooLarge, StdinIdle):
        command = None
    result = exfil_bash.assess(command, args.workspace or os.getcwd(), audit=args.audit)
    if "error" in result["audit"]:
        print(exfil_audit.WRITE_WARNING, file=sys.stderr)
    if args.json:
        print(json.dumps(result, ensure_ascii=True))
    elif result["exit"]:
        print(exfil_bash.refusal_message(result), file=sys.stderr)
    return result["exit"]


if __name__ == "__main__":
    sys.exit(main())
