"""Read-only quarantine locations for CLI, agent and future UI callers.

This queries directory layout, not recovery contents or purge authority. No
RecoveryEngine is constructed, no Git command runs, and no layout is created.
"""
from __future__ import annotations

import errno
import os
import stat
from typing import Any, Dict, Iterable, List

from . import AUDIT_NAME, MANIFEST_NAME, STATE_NAME, TRASH_DIRNAME


SCHEMA_VERSION = 1
DEFAULT_MAX_DEPTH = 3
DEFAULT_MAX_ENTRIES = 10_000
EXCLUDED_DIRS = (".git", "node_modules", ".internal", "__pycache__")


def _paths(values: Iterable[str]) -> List[str]:
    if isinstance(values, (str, bytes)):
        raise ValueError("paths must be a sequence of text paths")
    try:
        values = iter(values)
    except TypeError:
        raise ValueError("paths must be a sequence of text paths") from None
    result = []
    for value in values:
        try:
            value = os.fspath(value)
        except TypeError:
            raise ValueError("paths must contain text paths") from None
        if not isinstance(value, str) or not value or "\0" in value:
            raise ValueError("paths must contain nonempty text paths")
        normalized = os.path.normpath(os.path.abspath(os.path.expanduser(value)))
        if normalized not in result:
            result.append(normalized)
    return result


def _error_code(exc: OSError) -> str:
    if exc.errno in (errno.EACCES, errno.EPERM):
        return "PERMISSION_DENIED"
    if exc.errno == errno.ENOENT:
        return "NOT_FOUND"
    if exc.errno == errno.ENOTDIR:
        return "NOT_DIRECTORY"
    return "IO_ERROR"


def list_trash_locations(
        roots: Iterable[str] = (), *, trash_paths: Iterable[str] = (),
        max_depth: int = DEFAULT_MAX_DEPTH,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        ) -> Dict[str, Any]:
    """Return schema v1 location candidates within explicit bounded roots.

    Depth counts directories below a root: depth 0 still checks that root's
    .agent-trash. A recognized bucket is never traversed. Ordinary child
    directory links are skipped; explicitly supplied roots/buckets may use
    aliases. Expected marker names are lstat'ed, never opened.

    count includes unconfirmed/unreadable candidates; identified_count only
    counts existing metadata layouts. Neither identifies ownership, content
    integrity or permission to restore/purge. complete covers the declared
    depth/exclusions and successful reads, not the whole host or an atomic
    filesystem snapshot. Errors retain partial results.
    """
    if type(max_depth) is not int or max_depth < 0:
        raise ValueError("max_depth must be a nonnegative integer")
    if type(max_entries) is not int or max_entries < 1:
        raise ValueError("max_entries must be a positive integer")
    roots, trash_paths = _paths(roots), _paths(trash_paths)
    if not roots and not trash_paths:
        raise ValueError("provide roots or trash_paths")

    buckets: Dict[str, Dict[str, Any]] = {}
    errors: List[Dict[str, str]] = []
    visited: Dict[str, int] = {}
    examined = 0
    exhausted = False

    def error(path: str, code: str) -> None:
        record = {"path": path, "code": code}
        if record not in errors:
            errors.append(record)

    def charge(path: str) -> bool:
        nonlocal examined, exhausted
        if examined >= max_entries:
            if not exhausted:
                error(path, "ENTRY_LIMIT_REACHED")
            exhausted = True
            return False
        examined += 1
        return True

    def bucket(path: str, source: str) -> None:
        try:
            if not stat.S_ISDIR(os.stat(path).st_mode):
                error(path, "NOT_DIRECTORY")
                return
            resolved = os.path.normpath(os.path.realpath(path))
        except OSError as exc:
            error(path, _error_code(exc))
            return
        key = os.path.normcase(resolved)
        if key in buckets:
            row = buckets[key]
            if path not in row["locations"]:
                row["locations"].append(path)
            if source not in row["sources"]:
                row["sources"].append(source)
            return

        present = unreadable = False
        for name in (MANIFEST_NAME, AUDIT_NAME, STATE_NAME, "sessions"):
            try:
                mode = os.lstat(os.path.join(path, name)).st_mode
                present |= (stat.S_ISDIR(mode) if name == "sessions"
                            else stat.S_ISREG(mode))
            except FileNotFoundError:
                continue
            except OSError as exc:
                unreadable = True
                error(path, _error_code(exc))
        status = ("unreadable" if unreadable else
                  "metadata_present" if present else "unconfirmed")
        buckets[key] = {
            "trash_root": path, "resolved_trash_root": resolved,
            "directory": os.path.dirname(path), "locations": [path],
            "sources": [source], "status": status,
        }

    for path in trash_paths:
        if not charge(path):
            break
        bucket(path, "declared")

    for root in roots:
        if exhausted:
            break
        stack = [(root, 0)]
        while stack and not exhausted:
            directory, depth = stack.pop()
            if not charge(directory):
                break
            if os.path.basename(directory) == TRASH_DIRNAME:
                bucket(directory, "discovered")
                continue
            try:
                physical = os.path.normcase(os.path.realpath(directory))
            except OSError as exc:
                error(directory, _error_code(exc))
                continue
            remaining = max_depth - depth
            if visited.get(physical, -1) >= remaining:
                continue
            visited[physical] = remaining
            children = []
            try:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        if not charge(entry.path):
                            break
                        if entry.name == TRASH_DIRNAME:
                            bucket(entry.path, "discovered")
                        elif (depth < max_depth and entry.name not in EXCLUDED_DIRS
                              and entry.is_dir(follow_symlinks=False)):
                            children.append(entry.path)
            except OSError as exc:
                error(directory, _error_code(exc))
            # Reverse push gives stable lexical order among directory children.
            stack.extend((path, depth + 1) for path in sorted(children, reverse=True))

    rows = sorted(buckets.values(), key=lambda row: row["resolved_trash_root"])
    for row in rows:
        row["locations"].sort()
        row["sources"].sort()
        row["trash_root"] = row["locations"][0]
        row["directory"] = os.path.dirname(row["trash_root"])
    return {
        "schema_version": SCHEMA_VERSION,
        "roots": roots, "trash_paths": trash_paths,
        "max_depth": max_depth, "max_entries": max_entries,
        "excluded_dirs": list(EXCLUDED_DIRS), "examined": examined,
        "complete": not errors,
        "count": len(rows),
        "identified_count": sum(row["status"] == "metadata_present" for row in rows),
        "entries": rows, "errors": errors,
    }
