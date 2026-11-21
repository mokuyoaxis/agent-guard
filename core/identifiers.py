"""Resolve operator-entered prefixes without changing stored identifiers."""
from __future__ import annotations

import re


def unique_prefix(prefix, identifiers, label):
    """Return the single match or None. Callers can give exact IDs priority."""
    matches = (identifier for identifier in identifiers if identifier.startswith(prefix))
    match = next(matches, None)
    if next(matches, None) is not None:
        raise ValueError(f"ambiguous {label} prefix; use a longer prefix or the full ID")
    return match


def resolve_record_id(directory, identifier, length, label):
    if re.fullmatch(rf"[0-9a-f]{{1,{length}}}", identifier) is None:
        raise ValueError(f"{label} requires a lowercase hexadecimal ID or unique prefix")
    if len(identifier) == length:
        return identifier
    identifier_pattern = re.compile(rf"[0-9a-f]{{{length}}}\.json\Z")
    candidates = (path.stem for path in directory.glob(identifier + "*.json")
                  if identifier_pattern.fullmatch(path.name))
    match = unique_prefix(identifier, candidates, label)
    if match is None:
        raise ValueError(f"no matching {label} ID in this workspace")
    return match
