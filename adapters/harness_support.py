"""Bounded POSIX process capture shared by the Lab harness runners."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import time
from typing import Sequence

from core.lab import LabError, MAX_SCAN_BYTES


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def read_regular(path: Path, limit: int) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise LabError("expected a regular non-symlink input file")
    with path.open("rb") as stream:
        payload = stream.read(limit + 1)
    if len(payload) > limit:
        raise LabError("input exceeds its supported size limit")
    return payload


def write_new(path: Path, payload: bytes) -> None:
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0), 0o600,
    )
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)


def write_json(path: Path, value: dict) -> None:
    write_new(path, (json.dumps(value, sort_keys=True, indent=2) + "\n").encode())


def capture_process(
    argv: Sequence[str], cwd: Path, environment: dict[str, str], timeout: float,
) -> tuple[dict, dict[str, bytes]]:
    if os.name != "posix":
        raise LabError("Lab process capture currently supports POSIX hosts only")
    streams = {"stdout": bytearray(), "stderr": bytearray()}
    result = {
        "exit_code": None, "timed_out": False, "output_limit_exceeded": False,
        "start_failed": False,
    }
    try:
        process = subprocess.Popen(
            list(argv), cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True, close_fds=True,
        )
    except OSError:
        result["start_failed"] = True
        return result, {name: bytes(value) for name, value in streams.items()}
    deadline = time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
            while selector.get_map():
                if time.monotonic() >= deadline:
                    result["timed_out"] = True
                    break
                for key, _ in selector.select(timeout=0.05):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    output = streams[key.data]
                    remaining = MAX_SCAN_BYTES - len(output)
                    output.extend(chunk[:remaining])
                    if len(chunk) > remaining:
                        result["output_limit_exceeded"] = True
                        break
                if result["output_limit_exceeded"]:
                    break
            if not result["timed_out"] and not result["output_limit_exceeded"]:
                try:
                    process.wait(timeout=max(0.01, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    result["timed_out"] = True
    finally:
        # Only this invocation's fresh process group is signalled.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=2)
        process.stdout.close()
        process.stderr.close()
    result["exit_code"] = process.returncode
    return result, {name: bytes(value) for name, value in streams.items()}
