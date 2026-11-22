#!/usr/bin/env python3
"""View and configure Delete-guard limits, protected paths, and regenerable patterns."""
from __future__ import annotations

import argparse
from dataclasses import replace
from fractions import Fraction
import json
import os
from pathlib import Path
import re
import stat
import sys

sys.dont_write_bytecode = True

import _bootstrap  # noqa: F401

from core import TRASH_DIRNAME, classifier
from core.delete_config import (
    CONFIG_DIRECTORY, LIMIT_FIELDS, MAX_CONFIG_BYTES, MAX_REGENERABLE_PATTERNS,
    DeleteConfig, DeleteConfigError, config_path, normalize_protected_path,
    parse_config, read_config_bytes, validate_limit, validate_regenerable_pattern,
)


BYTE_UNITS = {
    "TiB": 1024 ** 4, "TB": 1000 ** 4,
    "GiB": 1024 ** 3, "GB": 1000 ** 3,
    "MiB": 1024 ** 2, "MB": 1000 ** 2,
    "KiB": 1024, "KB": 1000, "B": 1,
}
BYTE_MULTIPLIERS = {unit.lower(): factor for unit, factor in BYTE_UNITS.items()}
_CORE_PROTECTED_REL = frozenset({".", ".git", CONFIG_DIRECTORY, TRASH_DIRNAME})


def parse_limit_value(name, value):
    try:
        return int(value, 10)
    except ValueError as exc:
        if not name.endswith("_bytes"):
            raise DeleteConfigError(f"{name} requires an integer") from exc
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z]+)", value.strip())
    if not match or match[2].lower() not in BYTE_MULTIPLIERS:
        raise DeleteConfigError(f"{name} requires bytes or a size such as 512MiB or 2GB")
    try:
        size = Fraction(match[1]) * BYTE_MULTIPLIERS[match[2].lower()]
    except ValueError as exc:
        raise DeleteConfigError(f"{name} size is too large to parse") from exc
    if size.denominator != 1:
        raise DeleteConfigError(f"{name} must represent a whole number of bytes")
    return size.numerator


def parse_assignments(arguments):
    limits = {}
    for argument in arguments:
        name, separator, value = argument.partition("=")
        if not separator or name not in LIMIT_FIELDS:
            raise DeleteConfigError(f"expected a numeric policy field as FIELD=VALUE, got {argument!r}")
        if name in limits:
            raise DeleteConfigError(f"limit specified more than once: {name}")
        limit = parse_limit_value(name, value)
        validate_limit(name, limit)
        limits[name] = limit
    return limits


def parse_list_action(command, arguments):
    if len(arguments) < 2 or arguments[0] not in ("add", "remove"):
        raise DeleteConfigError(f"{command} requires 'add' or 'remove' followed by at least one value")
    return arguments[0], arguments[1:]


def _protected_add_target(item, workspace):
    normalized = normalize_protected_path(item, str(workspace))
    posix_rel = normalized.replace("\\", "/")
    if posix_rel in _CORE_PROTECTED_REL or posix_rel.split("/", 1)[0] in (".git", CONFIG_DIRECTORY, TRASH_DIRNAME):
        raise DeleteConfigError(
            f"{posix_rel!r} is already unconditionally protected by Core; "
            "adding the workspace root or a control directory is not permitted")
    return normalized


def limit_rows(config, overrides, defaults):
    return {name: {"value": getattr(config, name), "default": getattr(defaults, name),
                   "source": "workspace" if name in overrides else "default"}
            for name in LIMIT_FIELDS}


def show_scope(workspace, path, stream):
    print("Workspace " + json.dumps(str(workspace)), file=stream)
    print("Policy " + json.dumps(str(path)), file=stream)


def format_limit(name, value):
    if not name.endswith("_bytes"):
        return f"{value:,}"
    for unit, factor in BYTE_UNITS.items():
        if value >= factor and value % factor == 0:
            return f"{value // factor:,} {unit}"
    return "0 B"


def print_changes(changes, effective_changed, stream, *, warning=None):
    for name, change in changes.items():
        if name in ("protected_paths", "regenerable_patterns"):
            for item in change["added"]:
                print(f"  + {name}: {json.dumps(item)}", file=stream)
            for item in change["removed"]:
                print(f"  - {name}: {json.dumps(item)}", file=stream)
        else:
            before, after = change["before"], change["after"]
            print(f"  {name}: {format_limit(name, before['value'])} ({before['source']}) -> "
                  f"{format_limit(name, after['value'])} ({after['source']})", file=stream)
    if warning:
        print("WARNING: " + warning, file=stream)
    print("Existing accounting and recovery records are retained.", file=stream)
    if effective_changed:
        print("Pending Cleanup approvals bound to different limits or policy lists require a new review.", file=stream)


def confirm(summary, effective_changed, *, warning=None):
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise DeleteConfigError("updating policy requires an interactive operator terminal")
    show_scope(summary["workspace"], summary["policy_file"], sys.stderr)
    print_changes(summary["changes"], effective_changed, sys.stderr, warning=warning)
    print("Type APPLY to confirm: ", end="", file=sys.stderr, flush=True)
    return sys.stdin.readline().rstrip("\r\n") == "APPLY"


def write_policy(path, expected, values):
    if Path(os.path.realpath(path)) != path:
        raise DeleteConfigError("policy writes require an unlinked workspace configuration file")
    payload = (json.dumps(values, indent=2) + "\n").encode("ascii")
    if len(payload) > MAX_CONFIG_BYTES:
        raise DeleteConfigError("updated policy exceeds the configuration size limit")
    path.parent.mkdir(mode=0o700, exist_ok=True)
    staged = path.with_name("." + path.name + ".tmp")
    # Exclusive staging serializes CLI writers through the final comparison.
    try:
        descriptor = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise DeleteConfigError(f"a policy update is using {staged.name}; inspect it before retrying") from exc
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        if read_config_bytes(path) != expected:
            raise DeleteConfigError("policy changed during review; rerun to review the current settings")
        if expected is not None:
            staged.chmod(stat.S_IMODE(path.stat().st_mode))
        os.replace(staged, path)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise


def _print_policy_lists(config, stream):
    print("Protected paths:", file=stream)
    if config.protected_paths:
        for item in config.protected_paths:
            print("  " + json.dumps(item), file=stream)
    else:
        print("  (none)", file=stream)
    print("Regenerable patterns (workspace):", file=stream)
    if config.regenerable_patterns:
        for item in config.regenerable_patterns:
            print("  " + json.dumps(item), file=stream)
    else:
        print("  (none)", file=stream)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("show", "set", "reset-limits", "protect", "regenerable"),
        nargs="?", default="show",
        help="show policy (default), set limits, reset-limits, or manage protect/regenerable lists",
    )
    parser.add_argument(
        "assignments", metavar="ARG", nargs="*",
        help="FIELD=VALUE for set, or 'add|remove VALUE...' for protect/regenerable",
    )
    parser.add_argument("--root", type=Path, help="workspace directory (defaults to workspace discovery)")
    parser.add_argument("--preview", action="store_true", help="show policy changes without writing")
    parser.add_argument("--json", action="store_true", help="emit the resulting policy as JSON")
    args = parser.parse_intermixed_args(argv)
    if args.command == "set" and not args.assignments:
        parser.error("set requires at least one FIELD=VALUE")
    if args.command in ("show", "reset-limits") and args.assignments:
        parser.error(f"extra arguments are not supported by {args.command}")
    if args.command == "show" and args.preview:
        parser.error("--preview does not apply to show")

    workspace = Path(classifier.workspace_boundary_root(args.root) if args.root is not None
                     else classifier.discover_workspace(os.getcwd()))
    if not workspace.is_dir() or workspace == workspace.parent:
        parser.error("select an existing workspace directory other than the filesystem root")
    path = config_path(workspace)
    warning = None
    try:
        raw = read_config_bytes(path)
        existing, config = parse_config(raw, str(workspace))
        defaults = DeleteConfig()
        before = limit_rows(config, existing, defaults)
        values, updated = dict(existing), config
        changes = {}
        if args.command == "set":
            limits = parse_assignments(args.assignments)
            values = {**existing, **limits}
            updated = replace(config, **limits)
        elif args.command == "reset-limits":
            values = {key: value for key, value in existing.items() if key not in LIMIT_FIELDS}
            updated = replace(defaults,
                              protected_paths=list(config.protected_paths),
                              regenerable_patterns=list(config.regenerable_patterns))
        elif args.command == "protect":
            action, items = parse_list_action("protect", args.assignments)
            current = list(config.protected_paths)
            if action == "add":
                requested = sorted(set(_protected_add_target(item, workspace) for item in items))
                added = [item for item in requested if item not in current]
                removed = []
                new_list = sorted(set(current) | set(added))
            else:
                requested = sorted(set(normalize_protected_path(item, str(workspace)) for item in items))
                missing = [item for item in requested if item not in current]
                if missing:
                    raise DeleteConfigError("protected path is not configured: " + ", ".join(missing))
                added = []
                removed = requested
                new_list = [item for item in current if item not in removed]
                warning = "Removing a protected path allows guarded deletion of that path."
            if added or removed:
                if new_list:
                    values["protected_paths"] = new_list
                else:
                    values.pop("protected_paths", None)
                updated = replace(config, protected_paths=new_list)
                changes["protected_paths"] = {
                    "added": added, "removed": removed,
                    "before": current, "after": new_list,
                }
        elif args.command == "regenerable":
            action, items = parse_list_action("regenerable", args.assignments)
            current = list(config.regenerable_patterns)
            if action == "add":
                requested = sorted(set(validate_regenerable_pattern(item) for item in items))
                added = [item for item in requested if item not in current]
                removed = []
                new_list = sorted(set(current) | set(added))
                if len(new_list) > MAX_REGENERABLE_PATTERNS:
                    raise DeleteConfigError(
                        f"regenerable_patterns supports at most {MAX_REGENERABLE_PATTERNS} entries")
                if added:
                    warning = (
                        "Git-ignored targets matching added regenerable patterns may be "
                        "PERMANENTLY DELETED without quarantine copies (unless --quarantine is set)."
                    )
            else:
                requested = sorted(set(items))
                missing = [item for item in requested if item not in current]
                if missing:
                    raise DeleteConfigError("regenerable pattern is not configured: " + ", ".join(missing))
                added = []
                removed = requested
                new_list = [item for item in current if item not in removed]
            if added or removed:
                if new_list:
                    values["regenerable_patterns"] = new_list
                else:
                    values.pop("regenerable_patterns", None)
                updated = replace(config, regenerable_patterns=new_list)
                changes["regenerable_patterns"] = {
                    "added": added, "removed": removed,
                    "before": current, "after": new_list,
                }

        after = limit_rows(updated, values, defaults)
        if args.command in ("set", "reset-limits"):
            changes = {name: {"before": before[name], "after": after[name]}
                       for name in LIMIT_FIELDS if before[name] != after[name]}
        summary = {
            "workspace": str(workspace), "policy_file": str(path), "limits": after,
            "protected_path_count": len(updated.protected_paths),
            "protected_paths": list(updated.protected_paths),
            "regenerable_pattern_count": len(updated.regenerable_patterns),
            "regenerable_patterns": list(updated.regenerable_patterns),
            "changes": changes,
        }
        effective_changed = updated != config
        if args.preview:
            summary.update(mode="preview", requires_confirmation=bool(changes),
                           effective_policy_changed=effective_changed)
            if warning:
                summary["warning"] = warning
        elif changes:
            if not confirm(summary, effective_changed, warning=warning):
                print("Cancelled. Workspace policy was left as found.", file=sys.stderr)
                return 2
            write_policy(path, raw, values)
    except (DeleteConfigError, OSError) as exc:
        print(f"Delete-guard policy {str(path)!r}: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nPolicy update interrupted. Inspect the current settings with show.", file=sys.stderr)
        return 130

    if args.json:
        print(json.dumps(summary, indent=2))
    elif args.preview:
        show_scope(workspace, path, sys.stdout)
        print("Read-only preview.")
        if changes:
            print_changes(changes, effective_changed, sys.stdout, warning=warning)
        else:
            print("Policy already matches the requested settings.")
    else:
        show_scope(workspace, path, sys.stdout)
        if args.command != "show":
            message = "Policy updated." if changes else "Policy already matches the requested settings."
            print(message)
        print(f"{'Limit':<27} {'Effective':>16} {'Default':>16}  Source")
        for name, row in after.items():
            value = format_limit(name, row["value"])
            default = format_limit(name, row["default"])
            print(f"{name:<27} {value:>16} {default:>16}  {row['source']}")
        _print_policy_lists(updated, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
