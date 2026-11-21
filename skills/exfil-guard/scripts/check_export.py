#!/usr/bin/env python3
"""Check an explicit workspace file set before creating or sharing an export."""
from __future__ import annotations

import json
import os
import sys
import time

sys.dont_write_bytecode = True
import _bootstrap  # noqa: F401

from core import classifier, exfil_audit, export_scan, redaction
from _payload import InvalidArguments, SafeArgumentParser


def main() -> int:
    started = time.monotonic()
    parser = SafeArgumentParser(description=__doc__)
    parser.add_argument("--workspace", help="workspace root (default: discover from cwd)")
    parser.add_argument("--channel", default="archive-upload", help="egress channel (default: archive-upload)")
    parser.add_argument("--max-files", type=int, default=export_scan.DEFAULT_MAX_FILES)
    parser.add_argument("--max-file-bytes", type=int, default=min(redaction.max_scan_bytes(), export_scan.MAX_FILE_BYTES))
    parser.add_argument("--max-total-bytes", type=int, default=export_scan.MAX_TOTAL_BYTES)
    parser.add_argument("--strict-paths", action="store_true", help="refuse generic absolute paths")
    parser.add_argument("--paths-file", help="workspace-local UTF-8 file with one literal path per line")
    parser.add_argument("--audit", action="store_true", help="record metadata in the shared audit log")
    parser.add_argument("--json", action="store_true", help="return bounded structured findings")
    parser.add_argument("paths", nargs="*", metavar="PATH", help="literal paths relative to the workspace")
    try:
        args = parser.parse_args()
        if not (1 <= args.max_files <= export_scan.MAX_FILES
                and 1 <= args.max_file_bytes <= export_scan.MAX_FILE_BYTES
                and 1 <= args.max_total_bytes <= export_scan.MAX_TOTAL_BYTES):
            parser.error("invalid scan limits")
    except InvalidArguments:
        print(json.dumps({"error": "invalid arguments", "exit": 1}))
        return 1

    workspace = args.workspace or classifier.discover_workspace(os.getcwd())
    scan = export_scan.ExportScan(
        workspace, redaction.get_channel(args.channel), max_files=args.max_files,
        max_file_bytes=args.max_file_bytes, max_total_bytes=args.max_total_bytes,
        strict_paths=args.strict_paths)
    scan.scan(args.paths, args.paths_file)
    result = scan.result()
    audit_enabled = exfil_audit.enabled(args.audit)
    if audit_enabled and not scan.workspace_ready:
        result["audit"] = {"enabled": True, "recorded": False, "error": "AUDIT_WRITE_FAILED"}
    else:
        result["audit"] = exfil_audit.record(
            scan.workspace, "exfil_export", "advisory",
            {**result, "spans": scan.spans, "target_count": scan.counts["files_scanned"]},
            started=started, active=audit_enabled)
    if "error" in result["audit"]:
        print(exfil_audit.WRITE_WARNING, file=sys.stderr)
    if args.json:
        print(json.dumps(result, ensure_ascii=True))
    else:
        print("Export scan " + result["decision"] + " [" + result["code"] + "]")
        print(json.dumps(result["counts"], sort_keys=True))
        for finding in result["findings"]:
            location = json.dumps(finding["path"], ensure_ascii=True) if finding["path"] is not None else "<withheld>"
            print("  " + location + " [" + finding["code"] + "] " + finding["reason"])
    return result["exit"]


if __name__ == "__main__":
    sys.exit(main())
