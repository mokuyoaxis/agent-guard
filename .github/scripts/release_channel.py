#!/usr/bin/env python3
"""Classify an Agent Guard tag into its GitHub/npm release channels."""
from __future__ import annotations

import re
import sys
from typing import Dict


_NUMBER = r"(?:0|[1-9][0-9]*)"
_STABLE_RE = re.compile(rf"^{_NUMBER}\.{_NUMBER}\.{_NUMBER}$")
_RC_RE = re.compile(rf"^{_NUMBER}\.{_NUMBER}\.{_NUMBER}-rc{_NUMBER}$")


def release_channel(version: str) -> Dict[str, str]:
    """Return workflow variables for one exact project version."""
    if _STABLE_RE.fullmatch(version):
        return {
            "RELEASE_KIND": "stable",
            "RELEASE_PRERELEASE": "false",
            "NPM_DIST_TAG": "latest",
        }
    if _RC_RE.fullmatch(version):
        return {
            "RELEASE_KIND": "prerelease",
            "RELEASE_PRERELEASE": "true",
            "NPM_DIST_TAG": "rc",
        }
    raise ValueError(
        "release version must be X.Y.Z or X.Y.Z-rcN without leading zeros")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: release_channel.py VERSION", file=sys.stderr)
        return 2
    try:
        channel = release_channel(argv[1])
    except ValueError as exc:
        print(f"release channel error: {exc}", file=sys.stderr)
        return 2
    for key, value in channel.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
