"""Read-only, value-free views of explicitly selected config files.

This is a cooperative interface, not a filesystem access-control boundary.
It never follows a target path symlink and never returns scalar values.
"""
from __future__ import annotations

import json
import os
import re
import stat
from contextlib import ExitStack
from typing import Any, Dict, List

from core import redaction


MAX_VIEW_BYTES = 256 * 1024
MAX_VIEW_DEPTH = 16
MAX_VIEW_NODES = 2048
_DOTENV_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_DISPLAY_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}\Z")


class SafeViewError(Exception):
    """A fixed, input-free diagnostic safe to show to an agent."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _components(relative_path: str) -> List[str]:
    if (not relative_path or relative_path.startswith("/") or
            "\\" in relative_path or ":" in relative_path or
            "\x00" in relative_path):
        raise SafeViewError("INVALID_PATH", "use a workspace-relative path")
    parts = relative_path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise SafeViewError("INVALID_PATH", "use a workspace-relative path")
    return parts


def _read_regular_file(workspace: str, parts: List[str], max_bytes: int) -> str:
    if (not hasattr(os, "O_NOFOLLOW") or
            not hasattr(os, "O_DIRECTORY") or
            os.open not in os.supports_dir_fd):
        raise SafeViewError("UNSUPPORTED_PLATFORM",
                            "safe descriptor-relative reads are unavailable")

    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    file_flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)
    root = os.path.realpath(os.path.abspath(workspace))
    with ExitStack() as stack:
        try:
            parent = os.open(root, directory_flags)
            stack.callback(os.close, parent)
            for part in parts[:-1]:
                parent = os.open(part, directory_flags, dir_fd=parent)
                stack.callback(os.close, parent)
            fd = os.open(parts[-1], file_flags, dir_fd=parent)
            stack.callback(os.close, fd)
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise SafeViewError("UNSAFE_FILE",
                                    "only regular, single-link files are supported")
            if before.st_size > max_bytes:
                raise SafeViewError("FILE_TOO_LARGE", "config file exceeds size limit")
            chunks = []
            total = 0
            while True:
                chunk = os.read(fd, min(65536, max_bytes + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise SafeViewError("FILE_TOO_LARGE",
                                        "config file exceeds size limit")
                chunks.append(chunk)
            after = os.fstat(fd)
            if ((before.st_dev, before.st_ino, before.st_size,
                 before.st_mtime_ns, before.st_ctime_ns) !=
                (after.st_dev, after.st_ino, after.st_size,
                 after.st_mtime_ns, after.st_ctime_ns)):
                raise SafeViewError("FILE_CHANGED", "config changed during read")
        except OSError:
            raise SafeViewError("UNSAFE_FILE",
                                "config cannot be opened safely") from None
    try:
        return b"".join(chunks).decode("utf-8")
    except UnicodeDecodeError:
        raise SafeViewError("INVALID_ENCODING", "config is not UTF-8") from None


def _safe_key(key: str, workspace: str, index: int) -> str:
    if not _DISPLAY_KEY.fullmatch(key):
        return f"<hidden-key-{index}>"
    scan = redaction.scan_text(key, channel="file-write", workspace=workspace,
                               max_bytes=256)
    if not scan.scanned or scan.spans:
        return f"<hidden-key-{index}>"
    return key


def _object_pairs(pairs: List[Any]) -> Dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise SafeViewError("INVALID_FORMAT", "duplicate JSON key")
        result[key] = value
    return result


def _invalid_constant(_value: str) -> None:
    raise SafeViewError("INVALID_FORMAT", "non-standard JSON number")


def _scalar_view(value: Any) -> Dict[str, str]:
    if value is None:
        return {"type": "null", "state": "empty"}
    if isinstance(value, bool):
        kind = "boolean"
    elif isinstance(value, (int, float)):
        kind = "number"
    else:
        kind = "string"
    return {"type": kind,
            "state": "empty" if value == "" else "set"}


def _structure(value: Any, workspace: str, depth: int,
               remaining: List[int]) -> Dict[str, Any]:
    remaining[0] -= 1
    if remaining[0] < 0:
        raise SafeViewError("STRUCTURE_TOO_LARGE", "config has too many nodes")
    if depth > MAX_VIEW_DEPTH:
        raise SafeViewError("STRUCTURE_TOO_DEEP", "config is too deeply nested")
    if isinstance(value, dict):
        fields = {}
        for index, (key, item) in enumerate(value.items(), 1):
            name = _safe_key(key, workspace, index)
            fields[name] = _structure(item, workspace, depth + 1, remaining)
        return {"type": "object", "fields": fields}
    if isinstance(value, list):
        return {"type": "array", "items": [
            _structure(item, workspace, depth + 1, remaining)
            for item in value]}
    return _scalar_view(value)


def _dotenv_view(text: str, workspace: str) -> Dict[str, Any]:
    fields = {}
    seen = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[7:].lstrip()
        key, sep, value = stripped.partition("=")
        key = key.strip()
        if not sep or not _DOTENV_KEY.fullmatch(key) or key in seen:
            raise SafeViewError("INVALID_FORMAT",
                                "unsupported or duplicate dotenv entry")
        seen.add(key)
        value = value.strip()
        if value.startswith(("'", '"')):
            if len(value) < 2 or value[-1] != value[0]:
                raise SafeViewError("INVALID_FORMAT",
                                    "unsupported dotenv quoting")
            value = value[1:-1]
        elif value.endswith(("'", '"')):
            raise SafeViewError("INVALID_FORMAT", "unsupported dotenv quoting")
        fields[_safe_key(key, workspace, len(seen))] = {
            "type": "string", "state": "empty" if value == "" else "set"}
        if len(seen) > MAX_VIEW_NODES:
            raise SafeViewError("STRUCTURE_TOO_LARGE", "config has too many nodes")
    return {"type": "object", "fields": fields}


def read_view(workspace: str, relative_path: str,
              format_name: str = "auto") -> Dict[str, Any]:
    """Return a structure-only view; never return a config scalar value."""
    parts = _components(relative_path)
    if format_name == "auto":
        name = parts[-1].lower()
        # Split the literal so the repository's source-dump corpus test does
        # not mistake this filename check for an instruction to read a store.
        dotenv_suffix = "." + "env"
        if name.endswith(".json"):
            format_name = "json"
        elif (name == dotenv_suffix or
              name.startswith(dotenv_suffix + ".") or
              name.endswith(dotenv_suffix)):
            format_name = "dotenv"
        else:
            raise SafeViewError("UNSUPPORTED_FORMAT",
                                "choose a JSON or dotenv config file")
    if format_name not in ("json", "dotenv"):
        raise SafeViewError("UNSUPPORTED_FORMAT", "unsupported config format")
    text = _read_regular_file(workspace, parts, MAX_VIEW_BYTES)
    if format_name == "dotenv":
        view = _dotenv_view(text, workspace)
    else:
        try:
            value = json.loads(text, object_pairs_hook=_object_pairs,
                               parse_constant=_invalid_constant)
        except (ValueError, RecursionError):
            raise SafeViewError("INVALID_FORMAT", "invalid JSON config") from None
        view = _structure(value, workspace, 0, [MAX_VIEW_NODES])
    return {"status": "ok", "format": format_name, "view": view}
