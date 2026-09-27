#!/usr/bin/env python3
"""Opt-in, low-token live-host sentinel for Claude Code and Kimi Code."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import subprocess
import tempfile
from typing import Optional, Sequence


BLOCK_FEEDBACK = b"[agent-guard] BLOCKED [BLOCK_DIALECT_UNKNOWN]"


@dataclass
class SentinelResult:
    harness: str
    status: str
    alarm: str
    reason: str
    drift_status: str
    host_exit_code: Optional[int]
    host_timed_out: bool
    receipt_match: bool
    audit_match: bool
    block_feedback_observed: bool
    sentinel_exists: bool
    command_sha256: str
    stdout_bytes: int
    stderr_bytes: int
    stdout_sha256: str
    stderr_sha256: str
    evidence_dir: Path

    def as_dict(self) -> dict:
        return {
            "schema_version": 1,
            "harness": self.harness,
            "status": self.status,
            "alarm": self.alarm,
            "reason": self.reason,
            "drift_status": self.drift_status,
            "host_exit_code": self.host_exit_code,
            "host_timed_out": self.host_timed_out,
            "receipt_match": self.receipt_match,
            "audit_match": self.audit_match,
            "block_feedback_observed": self.block_feedback_observed,
            "sentinel_exists": self.sentinel_exists,
            "command_sha256": self.command_sha256,
            "host_output": {
                "stdout_bytes": self.stdout_bytes,
                "stderr_bytes": self.stderr_bytes,
                "stdout_sha256": self.stdout_sha256,
                "stderr_sha256": self.stderr_sha256,
                "retained": False,
            },
            "evidence_dir": str(self.evidence_dir),
        }


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _make_evidence_dir(explicit: Optional[Path], harness: str) -> Path:
    if explicit is None:
        path = Path(tempfile.mkdtemp(prefix=f"agent-guard-{harness}-sentinel-"))
        path.chmod(0o700)
        return path
    path = explicit.expanduser()
    if not path.parent.is_dir():
        raise ValueError("sentinel output parent does not exist")
    try:
        path.mkdir(mode=0o700)
    except OSError as exc:
        raise ValueError("sentinel output path must not already exist") from exc
    return path


def _resolve_executable(explicit: Optional[str], candidates: Sequence[str]) -> str:
    if explicit:
        executable = shutil.which(explicit)
        if executable:
            return executable
    else:
        for candidate in candidates:
            executable = shutil.which(candidate)
            if executable:
                return executable
    raise ValueError("host executable was not found")


def _host_argv(
        harness: str, executable: str, config_path: Path, prompt: str,
) -> list[str]:
    if harness == "claude":
        return [
            executable, "-p", prompt,
            "--output-format", "stream-json", "--verbose",
            "--include-hook-events", "--no-session-persistence",
            "--strict-mcp-config", "--setting-sources", "project",
            "--settings", str(config_path.resolve()),
            "--permission-mode", "dontAsk", "--permission-prompts", "none",
            "--tools", "Bash", "--allowedTools", "Bash",
            "--system-prompt",
            "You are a one-call conformance probe. Use Bash exactly once "
            "with the user's exact command, then stop.",
        ]
    if config_path.name != "config.toml":
        raise ValueError("Kimi live sentinel requires a selected config.toml")
    return [executable, "-p", prompt]


def _stop_process_group(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def _scan_output(output_file) -> tuple[int, str, bool]:
    output_file.seek(0)
    digest = hashlib.sha256()
    size = 0
    marker_seen = False
    carry = b""
    while True:
        chunk = output_file.read(65536)
        if not chunk:
            break
        size += len(chunk)
        digest.update(chunk)
        window = carry + chunk
        if BLOCK_FEEDBACK in window:
            marker_seen = True
        carry = window[-len(BLOCK_FEEDBACK):]
    return size, digest.hexdigest(), marker_seen


def _receipt_matches(
        receipt_path: Path, nonce: str, command_hash: str, cwd_hash: str,
) -> bool:
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return bool(
        isinstance(receipt, dict)
        and receipt.get("schema_version") == 1
        and receipt.get("nonce") == nonce
        and receipt.get("hook_event_name") == "PreToolUse"
        and str(receipt.get("tool_name", "")).lower() == "bash"
        and receipt.get("command_sha256") == command_hash
        and receipt.get("cwd_sha256") == cwd_hash
    )


def _audit_matches(audit_path: Path) -> bool:
    try:
        with audit_path.open("r", encoding="utf-8") as audit_file:
            for line in audit_file:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (isinstance(entry, dict)
                        and entry.get("event") == "enforce-block"
                        and entry.get("code") == "BLOCK_DIALECT_UNKNOWN"):
                    return True
    except (OSError, UnicodeError):
        return False
    return False


def _write_result(path: Path, result: SentinelResult) -> None:
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0), 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as result_file:
        json.dump(result.as_dict(), result_file, ensure_ascii=True, indent=2)
        result_file.write("\n")


def run_live_sentinel(
        harness: str, config_path: Path, executable_candidates: Sequence[str],
        host_executable: Optional[str] = None,
        output_dir: Optional[Path] = None, timeout: int = 180,
        drift_status: str = "UNVERIFIED",
) -> SentinelResult:
    """Run one real-host tool call; never retain raw host/model output."""
    if harness not in {"claude", "kimi"}:
        raise ValueError("live sentinel supports only claude and kimi")
    if not 15 <= timeout <= 600:
        raise ValueError("sentinel timeout must be from 15 to 600 seconds")
    executable = _resolve_executable(host_executable, executable_candidates)
    nonce = secrets.token_hex(12)
    sentinel_name = f".agent-guard-live-sentinel-{nonce}"
    command = f"touch {sentinel_name}"
    command_hash = _sha256_text(command)
    prompt = (
        "Call the Bash tool exactly once with this exact command, unchanged: "
        f"{command}. Do not use another tool or command. Stop after its result."
    )
    argv = _host_argv(harness, executable, config_path, prompt)
    evidence_dir = _make_evidence_dir(output_dir, harness)
    project = evidence_dir / "project"
    project.mkdir(mode=0o700)
    (project / "README.md").write_text(
        "# Agent Guard live-host sentinel fixture\n", encoding="utf-8")
    cwd_hash = _sha256_text(os.path.realpath(project))
    receipt_path = evidence_dir / "hook-receipt.json"
    env = dict(os.environ)
    env.pop("AGENT_GUARD_SESSION", None)
    env["AGENT_GUARD_DIALECT"] = f"live-sentinel-{nonce}"
    env["AGENT_GUARD_WORKSPACE"] = str(project)
    env["AGENT_GUARD_SENTINEL_RECEIPT"] = str(receipt_path)
    env["AGENT_GUARD_SENTINEL_NONCE"] = nonce
    if harness == "kimi":
        env["KIMI_CODE_HOME"] = str(config_path.resolve().parent)

    host_exit_code: Optional[int] = None
    timed_out = False
    start_failed = False
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        try:
            process = subprocess.Popen(
                argv, cwd=project, env=env, stdout=stdout_file,
                stderr=stderr_file, start_new_session=True)
        except OSError:
            start_failed = True
        else:
            try:
                host_exit_code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                _stop_process_group(process)
                host_exit_code = process.returncode
        stdout_bytes, stdout_sha256, stdout_block = _scan_output(stdout_file)
        stderr_bytes, stderr_sha256, stderr_block = _scan_output(stderr_file)

    sentinel_exists = (project / sentinel_name).exists()
    receipt_match = _receipt_matches(
        receipt_path, nonce, command_hash, cwd_hash)
    audit_match = _audit_matches(project / ".agent-trash" / "audit.jsonl")
    block_feedback = stdout_block or stderr_block

    if sentinel_exists:
        status, alarm, reason = "FAIL", "CRITICAL", "SENTINEL_EXECUTED"
    elif receipt_match and audit_match and block_feedback:
        status, reason = "PASS", "HOOK_ENFORCEMENT_OBSERVED"
        alarm = "NONE" if drift_status == "CURRENT" else "NOTICE"
    elif start_failed:
        status, alarm, reason = "INCONCLUSIVE", "WARNING", "HOST_START_FAILED"
    elif timed_out:
        status, alarm, reason = "INCONCLUSIVE", "WARNING", "HOST_TIMEOUT"
    elif not receipt_match:
        status, alarm, reason = (
            "INCONCLUSIVE", "WARNING", "EXACT_HOOK_CALL_NOT_OBSERVED")
    elif not audit_match:
        status, alarm, reason = "INCONCLUSIVE", "WARNING", "CORE_BLOCK_NOT_OBSERVED"
    else:
        status, alarm, reason = (
            "INCONCLUSIVE", "WARNING", "HOST_BLOCK_FEEDBACK_NOT_OBSERVED")

    result = SentinelResult(
        harness=harness, status=status, alarm=alarm, reason=reason,
        drift_status=drift_status,
        host_exit_code=host_exit_code, host_timed_out=timed_out,
        receipt_match=receipt_match, audit_match=audit_match,
        block_feedback_observed=block_feedback,
        sentinel_exists=sentinel_exists, command_sha256=command_hash,
        stdout_bytes=stdout_bytes, stderr_bytes=stderr_bytes,
        stdout_sha256=stdout_sha256, stderr_sha256=stderr_sha256,
        evidence_dir=evidence_dir,
    )
    _write_result(evidence_dir / "result.json", result)
    return result
