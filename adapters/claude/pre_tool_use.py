#!/usr/bin/env python3
"""Claude Code PreToolUse hook - agent-guard adapter (Decision Protocol).

Reads the hook payload from stdin, runs the shared core (check.py), and
maps decisions onto Claude Code's native hook semantics:

    ALLOW, no compensations      -> exit 0, silent
    ALLOW, compensations applied -> exit 0 + permissionDecision "allow"
                                    (reason surfaces what was quarantined)
    ASK                          -> exit 0 + permissionDecision "ask"
                                    (ASK_ONCE: reason shown to the user)
    BLOCK                        -> exit 2; stderr is fed back to Claude so
                                    the model can self-correct
    guard failure                -> exit 2 (fail-closed)

Install - project .claude/settings.json:

{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python3 /path/to/agent-guard/adapters/claude/pre_tool_use.py"
          }
        ]
      }
    ]
  }
}

Also copy skills/delete-guard into the project (or reference it) so the
model learns the discipline. Set AGENT_GUARD_DEBUG=1 to print the raw core
verdict JSON to stderr (used by conformance tests).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from core.classifier import (  # noqa: E402
    DESTRUCTIVE_PREFILTER_RE, DESTRUCTIVE_PREFILTER_RE_WINDOWS)
from core.dialects import DIALECT_POSIX, resolve_dialect  # noqa: E402

# Payload keys checked (in order) for a dialect selector. Claude Code does
# not define a dialect field today, so this is an extension point: a host
# that grows one takes effect without an adapter change.
_DIALECT_PAYLOAD_KEYS = ("dialect", "shell_dialect", "shellDialect")

CHECK = os.path.join(_REPO_ROOT, "skills", "delete-guard",
                     "scripts", "check.py")


def emit(payload: dict) -> None:
    print(json.dumps(payload))


def allow_reason(code: str, compensations: list) -> str:
    return (f"[agent-guard] {code}: compensated automatically "
            f"({len(compensations)} relocation(s)); restorable via txid")


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return 0  # not our payload shape: stay out of the way
    if str(payload.get("tool_name", "")).lower() != "bash":
        return 0
    tool_input = payload.get("tool_input") or {}
    command = tool_input.get("command")
    if not isinstance(command, str) or not command:
        return 0
    # Dialect resolution must happen BEFORE the prefilter: the POSIX regex
    # cannot see `ri build -r -fo`, so a Windows-native payload would be
    # skipped as harmless. Precedence: payload -> AGENT_GUARD_DIALECT ->
    # posix (the documented default). An unusable selector is NOT swallowed
    # here; it is handed to check.py, which BLOCKs with an explicit code.
    dialect_raw = None
    for key in _DIALECT_PAYLOAD_KEYS:
        candidate = payload.get(key)
        if isinstance(candidate, str) and candidate.strip():
            dialect_raw = candidate
            break
    resolution = resolve_dialect(
        dialect_raw if dialect_raw is not None
        else os.environ.get("AGENT_GUARD_DIALECT"),
        source="payload" if dialect_raw is not None
        else "env AGENT_GUARD_DIALECT")

    prefilter = (DESTRUCTIVE_PREFILTER_RE if resolution.dialect == DIALECT_POSIX
                 else DESTRUCTIVE_PREFILTER_RE_WINDOWS)
    if resolution.ok and not prefilter.search(command):
        return 0  # fast path: regex cost only

    cwd = payload.get("cwd") or os.getcwd()
    env = dict(os.environ)
    session_id = payload.get("session_id")
    if session_id:
        env["AGENT_GUARD_SESSION"] = str(session_id)
    # Forward the *requested* selector verbatim rather than the resolved
    # value: check.py owns the "is this usable?" verdict, so an unknown
    # dialect becomes BLOCK_DIALECT_UNKNOWN instead of a silent posix
    # fallback. The payload selector travels on argv; the environment
    # variable is inherited by the child as-is.
    argv = [sys.executable, CHECK, "--enforce", "--json"]
    if dialect_raw is not None:
        argv += ["--dialect", dialect_raw]
    elif resolution.ok:
        argv += ["--dialect", resolution.dialect]
    argv += ["--", command]

    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=90, cwd=cwd, env=env,
        )
    except Exception as exc:
        sys.stderr.write(
            f"[agent-guard] guard infrastructure error (fail-closed): {exc}\n")
        return 2

    try:
        verdict = json.loads(proc.stdout)
    except json.JSONDecodeError:
        verdict = None
    if not isinstance(verdict, dict) or not verdict.get("decision"):
        sys.stderr.write(
            "[agent-guard] unparseable guard output (fail-closed)\n")
        return 2

    if os.environ.get("AGENT_GUARD_DEBUG"):
        sys.stderr.write(json.dumps(verdict, ensure_ascii=False) + "\n")

    code = verdict.get("code", "")
    explanation = verdict.get("explanation", "")
    reasons = "; ".join(verdict.get("reasons") or [])
    decision = verdict["decision"]

    if decision == "ALLOW":
        compensations = verdict.get("compensations") or []
        if compensations:
            emit({"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "permissionDecisionReason": allow_reason(code, compensations),
            }})
        return 0

    if decision == "ASK":
        # ASK_ONCE: Claude Code prompts the user; the reason is shown to
        # the human. Splitting the command avoids future prompts.
        emit({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": f"[agent-guard] {code}: {explanation}",
        }})
        return 0

    # BLOCK: exit 2 feeds stderr back to Claude - the model sees WHY and
    # the remediation, which is the teaching channel.
    message = (f"[agent-guard] BLOCKED [{code}] {explanation}"
               + (f" ({reasons})" if reasons else "")
               + " | Restate with explicit workspace-relative paths, or use "
                 "the safe-delete flow. Do NOT circumvent the guard.")
    sys.stderr.write(message + "\n")
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # never fail open on adapter bugs
        sys.stderr.write(f"[agent-guard] adapter error (fail-closed): {exc}\n")
        sys.exit(2)
