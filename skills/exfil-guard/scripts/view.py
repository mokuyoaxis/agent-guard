#!/usr/bin/env python3
"""Show a value-free view of an explicitly selected JSON or dotenv config."""
from __future__ import annotations

import argparse
import json
import os
import sys

import _bootstrap  # noqa: F401

from core import classifier, safe_view


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, _message):
        raise safe_view.SafeViewError("INVALID_ARGUMENT", "invalid arguments")


def main() -> int:
    parser = SafeArgumentParser(description=__doc__)
    parser.add_argument("path", help="workspace-relative config path")
    parser.add_argument("--workspace", default=None,
                        help="workspace root (default: discovered from cwd)")
    parser.add_argument("--format", dest="format_name", default="auto",
                        choices=("auto", "json", "dotenv"))
    try:
        args = parser.parse_args()
        workspace = args.workspace or classifier.discover_workspace(os.getcwd())
        result = safe_view.read_view(workspace, args.path, args.format_name)
    except safe_view.SafeViewError as exc:
        print(json.dumps({"status": "error", "code": exc.code,
                          "message": exc.message}), file=sys.stderr)
        return 2
    except Exception:
        print(json.dumps({"status": "error", "code": "INTERNAL_ERROR",
                          "message": "safe view failed"}), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
