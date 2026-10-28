"""Local deletion limits and protected paths; advisory against same-UID edits.

The optional JSON file is read only. Missing configuration uses documented
defaults; malformed configuration never silently disables protection.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import os
import stat
from typing import List, Optional


CONFIG_DIRECTORY = ".agent-guard"
CONFIG_NAME = "delete-policy.json"
MAX_CONFIG_BYTES = 1024 * 1024
MAX_INTEGER = (1 << 63) - 1


class DeleteConfigError(ValueError):
    """The local deletion configuration cannot be used safely."""


@dataclass
class DeleteConfig:
    quarantine_max_bytes: int = 5 * 1024 ** 3
    quarantine_max_entries: int = 100_000
    session_max_bytes: int = 1024 ** 3
    session_max_entries: int = 10_000
    rate_window_seconds: int = 60
    rate_max_operations: int = 60
    repeat_max_operations: int = 5
    batch_max_bytes: int = 256 * 1024 ** 2
    batch_max_entries: int = 1_000
    protected_paths: List[str] = field(default_factory=list)

    def fingerprint(self) -> str:
        return fingerprint(self)


def _inside(path: str, root: str) -> bool:
    try:
        path, root = os.path.normcase(path), os.path.normcase(root)
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


def _declaration(value: str, workspace: str) -> str:
    if not isinstance(value, str) or not value or "\0" in value:
        raise DeleteConfigError("protected_paths must contain nonempty literal paths")
    root = os.path.abspath(workspace)
    path = os.path.normpath(os.path.join(root, value))
    if not _inside(path, root):
        raise DeleteConfigError("protected_paths must stay inside the workspace")
    # Resolve directory aliases, retaining a final link's own location: a
    # declaration can protect an outward-pointing link without authorizing
    # configuration of an arbitrary external directory.
    parent, leaf = os.path.split(path)
    location = os.path.join(os.path.realpath(parent), leaf)
    if path != root and not _inside(location, os.path.realpath(root)):
        raise DeleteConfigError("protected_paths cannot cross an external directory link")
    return os.path.relpath(path, root)


def _unique_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise DeleteConfigError("duplicate deletion configuration field")
        result[name] = value
    return result


def load_config(workspace: str) -> DeleteConfig:
    """Read the optional workspace policy, rejecting malformed known fields."""
    path = os.path.join(os.path.abspath(workspace), CONFIG_DIRECTORY, CONFIG_NAME)
    try:
        # A FIFO must not block even briefly before we can classify it. Check
        # the opened descriptor, rather than stat-then-open with a race.
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise DeleteConfigError("deletion configuration must be a regular file")
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                raw = handle.read(MAX_CONFIG_BYTES + 1)
        finally:
            os.close(descriptor)
    except FileNotFoundError as exc:
        if os.path.lexists(path):
            raise DeleteConfigError("deletion configuration is an unreadable link") from exc
        return DeleteConfig()
    except OSError as exc:
        raise DeleteConfigError("cannot read deletion configuration") from exc
    if len(raw) > MAX_CONFIG_BYTES:
        raise DeleteConfigError("deletion configuration exceeds the size limit")
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise DeleteConfigError("deletion configuration must be a valid UTF-8 JSON object") from exc
    if not isinstance(data, dict):
        raise DeleteConfigError("deletion configuration must be an object")
    known = DeleteConfig.__dataclass_fields__
    if set(data) - set(known):
        raise DeleteConfigError("unknown deletion configuration field")
    values = dict(data)
    for name, value in values.items():
        if name == "protected_paths":
            if not isinstance(value, list):
                raise DeleteConfigError("protected_paths must be a list of literal paths")
            values[name] = sorted(set(_declaration(item, workspace) for item in value))
        else:
            minimum = 0 if name.endswith("_bytes") else 1
            if type(value) is not int or not minimum <= value <= MAX_INTEGER:
                raise DeleteConfigError("deletion limits require bounded integers; only byte limits may be zero")
    return DeleteConfig(**values)


def fingerprint(config: DeleteConfig) -> str:
    """Digest effective limits and paths without exposing their contents."""
    raw = json.dumps(asdict(config), ensure_ascii=True, sort_keys=True,
                     separators=(",", ":")).encode("ascii")
    return hashlib.sha256(raw).hexdigest()


def protected_reason(paths: List[str], workspace: str,
                     config: Optional[DeleteConfig] = None) -> Optional[str]:
    """Return a static reason when deleting any target overlaps protection.

    Both the named location and physical destination are protected, including
    deletion of an ancestor that would remove a protected descendant.
    """
    config = config if config is not None else load_config(workspace)
    root = os.path.abspath(workspace)
    protected = [os.path.join(root, CONFIG_DIRECTORY)]
    protected.extend(os.path.join(root, value) for value in config.protected_paths)
    boundaries = set()
    for path in protected:
        absolute = os.path.normpath(path)
        parent, leaf = os.path.split(absolute)
        boundaries.update((absolute, os.path.realpath(absolute),
                           os.path.join(os.path.realpath(parent), leaf)))
    for value in paths:
        absolute = os.path.normpath(os.path.join(root, value))
        parent, leaf = os.path.split(absolute)
        variants = (absolute, os.path.realpath(absolute),
                    os.path.join(os.path.realpath(parent), leaf))
        if any(_inside(target, boundary) or _inside(boundary, target)
               for target in variants for boundary in boundaries):
            return "target overlaps deletion configuration or a configured protected path"
    return None
