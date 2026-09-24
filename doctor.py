#!/usr/bin/env python3
"""Local agent-guard installation checks; never edits harness configuration."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

from core.dialects import resolve_dialect


ROOT = Path(__file__).resolve().parent
KIMI_BRIDGE = ROOT / "adapters" / "kimi-code" / "hook_bridge.sh"


def _config_path(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    kimi_home = os.environ.get("KIMI_CODE_HOME")
    if kimi_home:
        return Path(kimi_home).expanduser() / "config.toml"
    return Path.home() / ".kimi-code" / "config.toml"


def _command_tokens(command: str) -> list[str]:
    # Preserve shell operators as tokens so `cmd; other_cmd` cannot pass as a
    # simple three-argument bridge command. Quoted spaces in paths still work.
    lexer = shlex.shlex(command, posix=True, punctuation_chars="();<>|&")
    lexer.whitespace_split = True
    lexer.commenters = ""
    return list(lexer)


def _same_file(left: Path, right: Path) -> bool:
    try:
        return left.samefile(right)
    except OSError:
        return False


def _probe(shell: Path, bridge: Path, python: Path) -> list[str]:
    problems: list[str] = []
    with tempfile.TemporaryDirectory(prefix="agent-guard-doctor-kimi-") as cwd:
        payload = json.dumps({
            "hook_event_name": "PreToolUse", "tool_name": "Glob",
            "tool_input": {}, "cwd": cwd,
        })
        sentinel = Path(cwd) / ".guard-doctor-sentinel"
        core_payload = json.dumps({
            "hook_event_name": "PreToolUse", "tool_name": "Bash",
            "tool_input": {"command": f"touch {sentinel.name}"}, "cwd": cwd,
        })
        cases = (
            (python, payload, 0, "valid non-Bash payload", None, None),
            (python, "{", 2, "malformed payload", None, None),
            (python, core_payload, 2, "Core policy refusal",
             "fish", "BLOCK_DIALECT_UNKNOWN"),
            (Path(cwd) / "missing-python", payload, 2,
             "missing interpreter", None, None),
        )
        for interpreter, data, expected, label, dialect, marker in cases:
            env = dict(os.environ)
            env.pop("AGENT_GUARD_DIALECT", None)
            env.pop("AGENT_GUARD_SESSION", None)
            env["AGENT_GUARD_WORKSPACE"] = cwd
            if dialect:
                env["AGENT_GUARD_DIALECT"] = dialect
            try:
                result = subprocess.run(
                    [str(shell), str(bridge), str(interpreter)],
                    input=data, text=True, capture_output=True,
                    cwd=cwd, timeout=15, env=env,
                )
            except (OSError, subprocess.TimeoutExpired):
                problems.append(f"{label}: bridge could not finish")
                continue
            if result.returncode != expected:
                problems.append(
                    f"{label}: expected exit {expected}, got {result.returncode}")
            if marker and marker not in result.stderr:
                problems.append(f"{label}: expected {marker} in hook refusal")
        if sentinel.exists():
            problems.append("Core policy refusal: probe command unexpectedly ran")
    return problems


def check_kimi(config_path: Path, run_probe: bool = False) -> list[str]:
    problems: list[str] = []
    selected_dialect = os.environ.get("AGENT_GUARD_DIALECT")
    if selected_dialect is not None and not resolve_dialect(
            selected_dialect, source="env AGENT_GUARD_DIALECT").ok:
        problems.append("AGENT_GUARD_DIALECT is invalid for this process")
    try:
        import tomllib
    except ImportError:
        return ["doctor kimi requires Python 3.11+ (the hook adapter does not)"]

    try:
        with config_path.open("rb") as config_file:
            config = tomllib.load(config_file)
    except OSError:
        return [f"cannot read Kimi config: {config_path}"]
    except tomllib.TOMLDecodeError:
        return [f"Kimi config is not valid TOML: {config_path}"]

    hooks = config.get("hooks", [])
    if not isinstance(hooks, list):
        return ["Kimi config `hooks` must be an array"]
    candidates = [hook for hook in hooks
                  if isinstance(hook, dict)
                  and hook.get("event") == "PreToolUse"
                  and any(marker in str(hook.get("command", ""))
                          for marker in ("hook_bridge.sh", "pre_tool_use.py"))]
    if not candidates:
        return ["no agent-guard PreToolUse hook found in Kimi config"]
    if len(candidates) != 1:
        return ["expected exactly one agent-guard PreToolUse hook"]

    hook = candidates[0]
    if hook.get("matcher") != "^Bash$":
        problems.append("matcher must be exactly ^Bash$ for this checked installation")
    command = hook.get("command")
    if not isinstance(command, str):
        return problems + ["hook command must be a string"]
    try:
        tokens = _command_tokens(command)
    except ValueError:
        return problems + ["hook command has invalid shell quoting"]
    if len(tokens) != 3 or not all(Path(part).is_absolute() for part in tokens):
        return problems + [
            "hook command must be: ABSOLUTE_SH ABSOLUTE_BRIDGE ABSOLUTE_PYTHON"]

    shell, bridge, python = map(Path, tokens)
    if not shell.is_file() or not os.access(shell, os.X_OK):
        problems.append(f"POSIX shell is missing or not executable: {shell}")
    if not _same_file(bridge, KIMI_BRIDGE):
        problems.append(f"bridge does not point to this checkout: {KIMI_BRIDGE}")
    if not python.is_file() or not os.access(python, os.X_OK):
        problems.append(f"Python is missing or not executable: {python}")
    timeout = hook.get("timeout", 30)
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout < 1:
        problems.append("hook timeout must be a positive integer")

    if run_probe and not problems:
        problems.extend(_probe(shell, bridge, python))
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="harness", required=True)
    kimi = subcommands.add_parser("kimi", help="check the local Kimi hook")
    kimi.add_argument("--config", help="Kimi config.toml (defaults to KIMI_CODE_HOME or ~/.kimi-code)")
    kimi.add_argument(
        "--probe", action="store_true",
        help="execute the configured local Python/bridge on harmless payloads; no model call",
    )
    args = parser.parse_args(argv)
    config_path = _config_path(args.config)
    problems = check_kimi(config_path, run_probe=args.probe)
    if problems:
        for problem in problems:
            print(f"FAIL: {problem}", file=sys.stderr)
        return 1
    print(f"PASS: Kimi hook configuration shape checked ({config_path})")
    if args.probe:
        print("PASS: local allow, malformed refusal, Core policy refusal, and failed-interpreter probes")
    print("NOTE: this does not prove that a running Kimi session loaded the hook; a missing, unmatched, or timed-out hook can still fail open")
    return 0


if __name__ == "__main__":
    sys.exit(main())
