"""Local deletion limits and protected paths; advisory against same-UID edits.

Admission reads the optional JSON file. Missing configuration uses documented
defaults; malformed configuration never silently disables protection.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import List, Optional

from . import redaction


CONFIG_DIRECTORY = ".agent-guard"
CONFIG_NAME = "delete-policy.json"
MAX_CONFIG_BYTES = 1024 * 1024
MAX_INTEGER = (1 << 63) - 1
MAX_REGENERABLE_PATTERNS = 64
MAX_PATTERN_LENGTH = 128

_FORBIDDEN_REGENERABLE_SAMPLES = (
    ".git", ".agent-trash", ".agent-guard", ".internal",
    "." + "env", "." + "env.local", "." + "env.production",
    *sorted(redaction.SECRET_STORE_NAMES),
    *(f"sample{suffix}" for suffix in sorted(redaction.SECRET_STORE_SUFFIXES)),
)
_BRACKET_CLASS_RE = re.compile(r"\[[^\]]*\]")


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
    regenerable_patterns: List[str] = field(default_factory=list)

    def fingerprint(self) -> str:
        return fingerprint(self)


LIMIT_FIELDS = tuple(
    name for name in DeleteConfig.__dataclass_fields__
    if name not in ("protected_paths", "regenerable_patterns")
)


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


def normalize_protected_path(value: str, workspace: str) -> str:
    """Validate and normalize one workspace-relative protected path."""
    return _declaration(value, workspace)


def validate_regenerable_pattern(value: str) -> str:
    """Validate one workspace-relative regenerable artifact pattern.

    Rejects broad catch-all globs, recursive '**' wildcards, path escapes,
    and patterns that could match protected or secret-store names.
    """
    if not isinstance(value, str) or not value or len(value) > MAX_PATTERN_LENGTH:
        raise DeleteConfigError(
            f"regenerable_patterns entries must be nonempty strings up to {MAX_PATTERN_LENGTH} characters")
    if value != value.strip() or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise DeleteConfigError("regenerable_patterns cannot contain control characters or outer whitespace")
    if "\\" in value or ":" in value or value.startswith("/") or value.endswith("/"):
        raise DeleteConfigError("regenerable_patterns must use relative POSIX-style patterns inside the workspace")
    if "**" in value:
        raise DeleteConfigError("regenerable_patterns cannot use recursive '**' wildcards")
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise DeleteConfigError("regenerable_patterns cannot contain empty, '.' or '..' segments")
    for part in parts:
        stripped_part = _BRACKET_CLASS_RE.sub("", part)
        if "[" in stripped_part or "]" in stripped_part:
            raise DeleteConfigError("regenerable_patterns contains an unbalanced character class")
    leaf = parts[-1]
    literals = _BRACKET_CLASS_RE.sub("", leaf).replace("*", "").replace("?", "")
    if sum(ch.isalnum() for ch in literals) < 2:
        raise DeleteConfigError(
            "regenerable_patterns final segment must contain at least two literal alphanumeric characters")
    for sample in _FORBIDDEN_REGENERABLE_SAMPLES:
        if fnmatch.fnmatch(sample, leaf) or (len(parts) == 1 and fnmatch.fnmatch(sample, value)):
            raise DeleteConfigError("regenerable_patterns cannot match protected or secret-store names")
    for core_dir in (".git", ".agent-trash", ".agent-guard", ".internal"):
        if any(fnmatch.fnmatch(core_dir, part) for part in parts):
            raise DeleteConfigError("regenerable_patterns cannot match protected or secret-store names")
    return value


def _unique_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise DeleteConfigError("duplicate deletion configuration field")
        result[name] = value
    return result


def config_path(workspace: str) -> Path:
    return Path(os.path.abspath(workspace)) / CONFIG_DIRECTORY / CONFIG_NAME


def read_config_bytes(path: Path) -> Optional[bytes]:
    """Read a bounded snapshot, distinguishing a missing file from an empty one."""
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
        return None
    except OSError as exc:
        raise DeleteConfigError("cannot read deletion configuration") from exc
    if len(raw) > MAX_CONFIG_BYTES:
        raise DeleteConfigError("deletion configuration exceeds the size limit")
    return raw


def validate_limit(name: str, value: int) -> None:
    minimum = 0 if name.endswith("_bytes") else 1
    if type(value) is not int or not minimum <= value <= MAX_INTEGER:
        raise DeleteConfigError(f"{name} must be an integer from {minimum} to {MAX_INTEGER}")


def parse_config(raw: Optional[bytes], workspace: str) -> tuple[dict, DeleteConfig]:
    """Keep explicit values for the operator CLI and normalize the Core policy."""
    if raw is None:
        return {}, DeleteConfig()
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
        elif name == "regenerable_patterns":
            if not isinstance(value, list):
                raise DeleteConfigError("regenerable_patterns must be a list of patterns")
            if len(value) > MAX_REGENERABLE_PATTERNS:
                raise DeleteConfigError(
                    f"regenerable_patterns supports at most {MAX_REGENERABLE_PATTERNS} entries")
            values[name] = sorted(set(validate_regenerable_pattern(item) for item in value))
        else:
            validate_limit(name, value)
    return data, DeleteConfig(**values)


def load_config(workspace: str) -> DeleteConfig:
    """Read the optional workspace policy, rejecting malformed known fields."""
    _, config = parse_config(read_config_bytes(config_path(workspace)), workspace)
    return config


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
