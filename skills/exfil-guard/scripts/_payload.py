"""Shared, bounded CLI input and host-side context resolution."""
from __future__ import annotations

import os
import sys
import time

from core import policy, redaction
from core.classifier import discover_workspace


DEFAULT_STDIN_TIMEOUT_SEC = 10.0


class StdinIdle(Exception):
    """No first byte arrived within the configured wait."""


class PayloadTooLarge(Exception):
    """Fixed, value-free diagnostic for an input budget refusal."""


def _stdin_timeout():
    try:
        value = float(os.environ.get("AGENT_GUARD_STDIN_TIMEOUT", ""))
    except (TypeError, ValueError):
        return DEFAULT_STDIN_TIMEOUT_SEC
    return value if 0 < value < float("inf") else DEFAULT_STDIN_TIMEOUT_SEC


def read_bounded_text(stream, max_bytes):
    """Read at most the cap plus one byte, then strictly decode UTF-8.

    Text-only streams (e.g. StringIO) are bounded in characters first and
    checked in bytes next. They retain at most four times the byte cap.
    No partial input is ever returned as a successful payload.
    """
    if type(max_bytes) is not int or max_bytes < 0:
        raise ValueError("invalid payload size limit")
    source = getattr(stream, "buffer", stream)
    chunks = []
    total = 0
    kind = None
    while True:
        data = source.read(min(65536, max_bytes + 1 - total))
        if not isinstance(data, (str, bytes)) or (kind is not None and type(data) is not kind):
            raise ValueError("invalid input stream")
        kind = type(data)
        if not data:
            break
        total += len(data.encode("utf-8", errors="strict")) if isinstance(data, str) else len(data)
        if total > max_bytes:
            raise PayloadTooLarge("payload exceeds scan cap")
        chunks.append(data)
    if kind is str:
        return "".join(chunks)
    return b"".join(chunks).decode("utf-8", errors="strict")


def read_payload(stream=None, timeout=None, max_bytes=None):
    """Bound payload memory; retain the existing first-byte timeout.

    This is not an end-to-end transport deadline: once a producer has
    spoken, reading still waits for EOF or the size refusal boundary.
    """
    stdin = stream if stream is not None else sys.stdin
    limit = redaction.max_scan_bytes() if max_bytes is None else max_bytes
    if stdin.isatty():
        sys.stderr.write("exfil-guard: reading payload from stdin "
                         "(Ctrl-D to finish)\n")
        return read_bounded_text(stdin, limit)
    try:
        import select
    except ImportError:                 # pragma: no cover - non-POSIX
        return read_bounded_text(stdin, limit)
    budget = _stdin_timeout() if timeout is None else timeout
    deadline = time.monotonic() + budget
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise StdinIdle("no payload arrived on stdin within the wait; "
                            "pipe the text in or use a terminal")
        try:
            ready, _, _ = select.select([stdin], [], [], min(remaining, 0.5))
        except (OSError, ValueError):
            return read_bounded_text(stdin, limit)
        if ready:
            return read_bounded_text(stdin, limit)


def resolve_context(workspace=None, mode=None):
    """Read the same workspace and host-side mode for both text CLIs."""
    if workspace is None:
        workspace = discover_workspace(os.getcwd())
    if mode is None:
        trash_root = os.environ.get(
            "AGENT_GUARD_TRASH", os.path.join(workspace, ".agent-trash"))
        mode = policy.load_mode(trash_root)["mode"]
    return workspace, mode
