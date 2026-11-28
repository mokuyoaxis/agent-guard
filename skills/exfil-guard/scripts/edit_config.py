#!/usr/bin/env python3
"""Minimal local operator CLI for allowlisted JSON and single-line dotenv scalar edits."""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional, TextIO

sys.dont_write_bytecode = True
import _bootstrap  # noqa: F401

from core import classifier, config_edit


class _InvalidArguments(ValueError):
    """Fixed argument refusal without echoing caller input."""


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise _InvalidArguments()


def _build_parser() -> SafeArgumentParser:
    parser = SafeArgumentParser(
        prog="agent-guard-config-edit",
        description=__doc__,
    )
    sub = parser.add_subparsers(dest="command", parser_class=SafeArgumentParser)
    set_cmd = sub.add_parser(
        "set",
        help="preview or apply one allowlisted scalar update",
    )
    set_cmd.add_argument("path", help="workspace-relative POSIX config path")
    set_cmd.add_argument("--key", required=True, help="JSON Pointer (/a/b) or dotenv key")
    set_cmd.add_argument("--value", default=None, help="new scalar value (omittable only for --type null)")
    set_cmd.add_argument(
        "--type",
        dest="value_type",
        default="string",
        choices=config_edit.VALUE_TYPES,
        help="scalar value type (default: string; dotenv allows string only)",
    )
    set_cmd.add_argument(
        "--format",
        dest="format_name",
        default="auto",
        choices=config_edit.FORMATS,
        help="config format (default: auto from policy)",
    )
    set_cmd.add_argument(
        "--workspace",
        default=None,
        help="workspace root (default: discover from cwd)",
    )
    mode_group = set_cmd.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--preview",
        action="store_true",
        help="read-only preview (default)",
    )
    mode_group.add_argument(
        "--apply",
        action="store_true",
        help="apply after interactive TTY APPLY confirmation",
    )
    set_cmd.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="emit structured config_edit/v1 JSON on stdout",
    )
    return parser


def _emit(
    receipt: Dict[str, Any],
    exit_code: int,
    *,
    as_json: bool,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    if as_json:
        stdout.write(json.dumps(receipt, ensure_ascii=True) + "\n")
        stdout.flush()
        return exit_code

    status = receipt["status"]
    code = receipt["code"]
    message = receipt["message"]
    if status == "preview":
        stdout.write(
            f"config-edit preview: {receipt['file']} {receipt['key']} "
            f"({receipt['before_type']}:{receipt['before_state']} -> {receipt['after_type']})\n"
        )
        stdout.flush()
        return exit_code
    if status == "applied":
        stdout.write(
            f"config-edit applied: {receipt['file']} {receipt['key']} "
            f"(replaced=true, durability_confirmed=true)\n"
        )
        stdout.flush()
        return exit_code

    extras: List[str] = []
    if receipt.get("replaced"):
        extras.append("replaced=true")
        extras.append(
            "durability_confirmed=" + ("true" if receipt.get("durability_confirmed") else "false")
        )
    if receipt.get("warning"):
        extras.append("warning=" + receipt["warning"])
    if receipt.get("audit_error"):
        extras.append("audit_error=" + receipt["audit_error"])
    suffix = f" ({', '.join(extras)})" if extras else ""
    stderr.write(f"config-edit {status} [{code}]: {message}{suffix}\n")
    stderr.flush()
    return exit_code


def _is_tty(stream: TextIO) -> bool:
    check = getattr(stream, "isatty", None)
    if check is None:
        return False
    try:
        return bool(check())
    except OSError:
        return False


def _prompt_apply(snapshot: config_edit.PreviewSnapshot, stdin: TextIO, stderr: TextIO) -> bool:
    stderr.write(
        "config-edit apply preview:\n"
        f"  file: {snapshot.safe_file_label}\n"
        f"  key: {snapshot.safe_key_label}\n"
        f"  format: {snapshot.format_name}\n"
        f"  before: {snapshot.before_type} ({snapshot.before_state})\n"
        f"  after: {snapshot.after_type}\n"
        "Type APPLY to replace: "
    )
    stderr.flush()
    line = stdin.readline()
    if line.endswith("\r\n"):
        response = line[:-2]
    elif line.endswith("\n"):
        response = line[:-1]
    else:
        response = line
    return response == "APPLY"


def main(
    argv: Optional[List[str]] = None,
    *,
    stdin: Optional[TextIO] = None,
    stdout: Optional[TextIO] = None,
    stderr: Optional[TextIO] = None,
) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    in_stream = sys.stdin if stdin is None else stdin
    out_stream = sys.stdout if stdout is None else stdout
    err_stream = sys.stderr if stderr is None else stderr
    wants_json = "--json" in raw_argv

    parser = _build_parser()
    try:
        args = parser.parse_args(raw_argv)
    except _InvalidArguments:
        receipt, code = config_edit.failure_receipt(config_edit.ConfigEditError("INVALID_ARGUMENT"))
        return _emit(receipt, code, as_json=wants_json, stdout=out_stream, stderr=err_stream)

    if args.command != "set":
        receipt, code = config_edit.failure_receipt(config_edit.ConfigEditError("INVALID_ARGUMENT"))
        return _emit(receipt, code, as_json=wants_json, stdout=out_stream, stderr=err_stream)

    as_json = bool(args.as_json)

    # Platform capability gate runs BEFORE opening policy, checking TTY, or prompting.
    if not config_edit.supports_safe_config_edit():
        receipt, code = config_edit.failure_receipt(
            config_edit.ConfigEditError("UNSUPPORTED_PLATFORM"),
        )
        return _emit(receipt, code, as_json=as_json, stdout=out_stream, stderr=err_stream)

    # --apply requires both stdin and stderr to be TTYs BEFORE generating preview.
    if args.apply and not (_is_tty(in_stream) and _is_tty(err_stream)):
        receipt, code = config_edit.failure_receipt(config_edit.ConfigEditError("TTY_REQUIRED"))
        return _emit(receipt, code, as_json=as_json, stdout=out_stream, stderr=err_stream)

    workspace = args.workspace or classifier.discover_workspace(os.getcwd())
    try:
        snapshot, preview_receipt = config_edit.preview_edit(
            workspace,
            args.path,
            key=args.key,
            value=args.value,
            value_type=args.value_type,
            format_name=args.format_name,
        )
    except config_edit.ConfigEditError as exc:
        receipt, code = config_edit.failure_receipt(exc)
        return _emit(receipt, code, as_json=as_json, stdout=out_stream, stderr=err_stream)

    if not args.apply:
        return _emit(preview_receipt, 0, as_json=as_json, stdout=out_stream, stderr=err_stream)

    if not _prompt_apply(snapshot, in_stream, err_stream):
        receipt, code = config_edit.failure_receipt(
            config_edit.ConfigEditError("APPROVAL_CANCELLED"),
        )
        return _emit(receipt, code, as_json=as_json, stdout=out_stream, stderr=err_stream)

    apply_receipt, exit_code = config_edit.apply_preview(snapshot)
    return _emit(apply_receipt, exit_code, as_json=as_json, stdout=out_stream, stderr=err_stream)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        receipt, code = config_edit.failure_receipt(config_edit.ConfigEditError("INTERNAL_ERROR"))
        if "--json" in sys.argv[1:]:
            sys.stdout.write(json.dumps(receipt, ensure_ascii=True) + "\n")
        else:
            sys.stderr.write(f"config-edit error [{receipt['code']}]: {receipt['message']}\n")
        sys.exit(code)
