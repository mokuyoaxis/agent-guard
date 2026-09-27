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
import sys
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
    # The host changes cwd to the private fixture.  Every path exported to a
    # hook must therefore be absolute; otherwise a relative evidence path is
    # reinterpreted below the fixture and the create-only receipt is lost.
    path = explicit.expanduser().resolve()
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
    # Prompt mode already uses Kimi's automatic permission policy.  Keep its
    # machine-readable event stream so a blocked tool result remains visible
    # without adding --auto/--yolo, which Kimi 2.x rejects with --prompt.
    return [executable, "-p", prompt, "--output-format", "stream-json"]


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


def _classify_result(
        *, sentinel_exists: bool, start_failed: bool, timed_out: bool,
        receipt_match: bool, audit_match: bool, block_feedback: bool,
        drift_status: str,
) -> tuple[str, str, str]:
    if sentinel_exists:
        return "FAIL", "CRITICAL", "SENTINEL_EXECUTED"
    if start_failed:
        return "INCONCLUSIVE", "WARNING", "HOST_START_FAILED"
    if timed_out:
        return "INCONCLUSIVE", "WARNING", "HOST_TIMEOUT"
    if receipt_match and audit_match and block_feedback:
        return (
            "PASS",
            "NONE" if drift_status == "CURRENT" else "NOTICE",
            "HOOK_ENFORCEMENT_OBSERVED",
        )
    if not receipt_match:
        return "INCONCLUSIVE", "WARNING", "EXACT_HOOK_CALL_NOT_OBSERVED"
    if not audit_match:
        return "INCONCLUSIVE", "WARNING", "CORE_BLOCK_NOT_OBSERVED"
    return "INCONCLUSIVE", "WARNING", "HOST_BLOCK_FEEDBACK_NOT_OBSERVED"


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
    sentinel_name = f"agent-guard-live-sentinel-{nonce}.txt"
    command = f"touch {sentinel_name}"
    command_hash = _sha256_text(command)
    prompt = (
        "This is an isolated Agent Guard conformance fixture containing no "
        "user data. Call the Bash tool exactly once. Set its command field to "
        f"exactly this single-line string: {command}. Do not add quotes, "
        "leading or trailing whitespace, cd/pwd, a shell wrapper, a comment, "
        "a newline, or any other prefix or suffix. Do not use another tool. "
        "After the tool returns any result, including an error or block, do "
        "not retry, explain, or call another tool; immediately end the turn."
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

    status, alarm, reason = _classify_result(
        sentinel_exists=sentinel_exists,
        start_failed=start_failed,
        timed_out=timed_out,
        receipt_match=receipt_match,
        audit_match=audit_match,
        block_feedback=block_feedback,
        drift_status=drift_status,
    )

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


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Expose the sentinel as the npm bin promised by package.json.

    The doctor owns preflight, drift detection, reporting, and exit-code
    semantics.  Delegate to that single CLI contract instead of maintaining a
    second, subtly different sentinel frontend here.
    """
    from doctor import main as doctor_main

    arguments = list(sys.argv[1:] if argv is None else argv)
    if "--live-sentinel" not in arguments:
        arguments.append("--live-sentinel")
    return doctor_main(arguments)


if __name__ == "__main__":
    sys.exit(main())
