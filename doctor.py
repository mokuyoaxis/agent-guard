#!/usr/bin/env python3
"""Local agent-guard installation checks; never edits harness configuration."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
from typing import Optional

from core.dialects import resolve_dialect


ROOT = Path(__file__).resolve().parent
KIMI_BRIDGE = ROOT / "adapters" / "kimi-code" / "hook_bridge.sh"
CLAUDE_BRIDGE = ROOT / "adapters" / "claude" / "hook_bridge.sh"


@dataclass
class Diagnosis:
    harness: str
    config_path: Path
    configuration: str
    local_probe: str
    problems: list[str]

    def as_dict(self) -> dict:
        return {
            "harness": self.harness,
            "configuration": self.configuration,
            "local_probe": self.local_probe,
            "host_interception": "UNVERIFIED",
            "problems": self.problems,
        }


def _kimi_config_path(explicit: Optional[str]) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    kimi_home = os.environ.get("KIMI_CODE_HOME")
    if kimi_home:
        return Path(kimi_home).expanduser() / "config.toml"
    return Path.home() / ".kimi-code" / "config.toml"


def _claude_config_path(explicit: Optional[str]) -> Path:
    # A selected settings file is not Claude's effective merged configuration.
    # Global, local, managed, plugin and session sources may alter the result.
    if explicit:
        return Path(explicit).expanduser()
    return Path.cwd() / ".claude" / "settings.json"


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
    except (OSError, ValueError):
        return False


def _executable(path: Path) -> bool:
    try:
        return path.is_file() and os.access(path, os.X_OK)
    except (OSError, ValueError):
        return False


def _bridge_command(
        command: object, expected_bridge: Path,
) -> tuple[list[str], Optional[tuple[Path, Path, Path]]]:
    if not isinstance(command, str):
        return ["hook command must be a string"], None
    try:
        tokens = _command_tokens(command)
    except ValueError:
        return ["hook command has invalid shell quoting"], None
    if len(tokens) != 3 or not all(Path(part).is_absolute() for part in tokens):
        return ["hook command must be: ABSOLUTE_SH ABSOLUTE_BRIDGE ABSOLUTE_PYTHON"], None

    shell, bridge, python = map(Path, tokens)
    problems = []
    if not _executable(shell):
        problems.append(f"POSIX shell is missing or not executable: {shell}")
    if not _same_file(bridge, expected_bridge):
        problems.append(f"bridge does not point to this checkout: {expected_bridge}")
    if not _executable(python):
        problems.append(f"Python is missing or not executable: {python}")
    return problems, (shell, bridge, python)


def _probe(harness: str, shell: Path, bridge: Path, python: Path) -> list[str]:
    problems: list[str] = []
    with tempfile.TemporaryDirectory(prefix=f"agent-guard-doctor-{harness}-") as cwd:
        sentinel = Path(cwd) / ".guard-doctor-denied"
        payload = json.dumps({
            "hook_event_name": "PreToolUse", "tool_name": "Bash",
            "tool_input": {"command": "printf guard-doctor-local-probe"}, "cwd": cwd,
        })
        core_payload = json.dumps({
            "hook_event_name": "PreToolUse", "tool_name": "Bash",
            "tool_input": {"command": "touch .guard-doctor-denied"}, "cwd": cwd,
        })
        ask_payload = json.dumps({
            "hook_event_name": "PreToolUse", "tool_name": "Bash",
            "tool_input": {"command": "touch .guard-doctor-ask && rm .guard-doctor-ask"},
            "cwd": cwd,
        })
        probe_env = dict(os.environ)
        probe_env.pop("AGENT_GUARD_DIALECT", None)
        probe_env.pop("AGENT_GUARD_SESSION", None)
        probe_env["AGENT_GUARD_WORKSPACE"] = cwd
        cases = (
            (python, payload, 0, "valid Bash payload", None, None),
            (python, "{", 2, "malformed payload", None, "malformed hook payload"),
            (python, core_payload, 2, "Core policy refusal",
             "fish", "BLOCK_DIALECT_UNKNOWN"),
            (Path(cwd) / "missing-python", payload, 2,
             "missing interpreter", None, "hook process failed"),
        )
        for interpreter, data, expected, label, dialect, marker in cases:
            env = dict(probe_env)
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
            if label == "valid Bash payload" and (result.stdout or result.stderr):
                problems.append("valid Bash payload: expected a silent allow")
            if expected == 2 and result.stdout:
                problems.append(f"{label}: refusal wrote unexpected stdout")
        try:
            ask = subprocess.run(
                [str(shell), str(bridge), str(python)],
                input=ask_payload, text=True, capture_output=True,
                cwd=cwd, timeout=15, env=probe_env,
            )
        except (OSError, subprocess.TimeoutExpired):
            problems.append("ASK mapping: bridge could not finish")
        else:
            if harness == "kimi":
                if ask.returncode != 2 or "host cannot enforce ASK" not in ask.stderr:
                    problems.append("ASK mapping: Kimi did not hard-refuse")
            else:
                try:
                    decision = json.loads(ask.stdout)["hookSpecificOutput"]["permissionDecision"]
                except (ValueError, KeyError, TypeError):
                    decision = None
                if ask.returncode != 0 or decision != "ask":
                    problems.append("ASK mapping: Claude did not request approval")
        # The hook only returns a verdict; no Bash command is executed here.
        # In particular, an absent file is not host-enforcement evidence.
        if sentinel.exists():
            problems.append("Core policy refusal: local hook executed probe command")
        if (Path(cwd) / ".guard-doctor-ask").exists():
            problems.append("ASK mapping: local hook executed probe command")
    return problems


def _dialect_problems() -> list[str]:
    selected_dialect = os.environ.get("AGENT_GUARD_DIALECT")
    if selected_dialect is not None and not resolve_dialect(
            selected_dialect, source="env AGENT_GUARD_DIALECT").ok:
        return ["AGENT_GUARD_DIALECT is invalid for this process"]
    return []


def _check_kimi_config(config_path: Path) -> tuple[list[str], Optional[tuple[Path, Path, Path]]]:
    problems: list[str] = []
    problems.extend(_dialect_problems())
    try:
        import tomllib
    except ImportError:
        return ["doctor kimi requires Python 3.11+ (the hook adapter does not)"], None

    try:
        with config_path.open("rb") as config_file:
            config = tomllib.load(config_file)
    except OSError:
        return problems + [f"cannot read Kimi config: {config_path}"], None
    except tomllib.TOMLDecodeError:
        return problems + [f"Kimi config is not valid TOML: {config_path}"], None

    hooks = config.get("hooks", [])
    if not isinstance(hooks, list):
        return problems + ["Kimi config `hooks` must be an array"], None
    candidates = [hook for hook in hooks
                  if isinstance(hook, dict)
                  and hook.get("event") == "PreToolUse"
                  and any(marker in str(hook.get("command", ""))
                          for marker in ("hook_bridge.sh", "pre_tool_use.py"))]
    if not candidates:
        return problems + ["no agent-guard PreToolUse hook found in Kimi config"], None
    if len(candidates) != 1:
        return problems + ["expected exactly one agent-guard PreToolUse hook"], None

    hook = candidates[0]
    if hook.get("matcher") != "^Bash$":
        problems.append("matcher must be exactly ^Bash$ for this checked installation")
    command_problems, bridge_command = _bridge_command(hook.get("command"), KIMI_BRIDGE)
    problems.extend(command_problems)
    timeout = hook.get("timeout", 30)
    if (not isinstance(timeout, int) or isinstance(timeout, bool)
            or not 1 <= timeout <= 600):
        problems.append("hook timeout must be an integer from 1 to 600 seconds")

    return problems, bridge_command


def _check_claude_config(config_path: Path) -> tuple[list[str], Optional[tuple[Path, Path, Path]]]:
    problems = _dialect_problems()
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        return problems + [f"cannot read Claude settings: {config_path}"], None
    except json.JSONDecodeError:
        return problems + [f"Claude settings is not valid JSON: {config_path}"], None
    if not isinstance(config, dict):
        return problems + ["Claude settings must be a JSON object"], None
    if ("disableAllHooks" in config
            and not isinstance(config["disableAllHooks"], bool)):
        problems.append("selected Claude settings has invalid disableAllHooks value")
    if config.get("disableAllHooks") is True:
        problems.append("selected Claude settings has disableAllHooks=true")
    hooks = config.get("hooks")
    if not isinstance(hooks, dict):
        return problems + ["Claude settings `hooks` must be an object"], None
    pre_tool = hooks.get("PreToolUse")
    if not isinstance(pre_tool, list):
        return problems + ["Claude settings `hooks.PreToolUse` must be an array"], None

    candidates = []
    for group in pre_tool:
        if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
            continue
        for handler in group["hooks"]:
            if (isinstance(handler, dict)
                    and isinstance(handler.get("command"), str)
                    and any(marker in handler["command"]
                            for marker in ("hook_bridge.sh", "pre_tool_use.py"))):
                candidates.append((group, handler))
    if not candidates:
        return problems + [
            "no agent-guard PreToolUse hook found in selected Claude settings"], None
    if len(candidates) != 1:
        return problems + [
            "expected exactly one agent-guard PreToolUse hook in selected Claude settings"], None

    group, handler = candidates[0]
    if group.get("matcher") != "Bash":
        problems.append("Claude matcher must be exactly Bash for this checked installation")
    if handler.get("type") != "command":
        problems.append("Claude hook handler must have type=command")
    if handler.get("async", False) is not False:
        problems.append("Claude hook must be synchronous; async hooks cannot block")
    if "if" in handler:
        problems.append("Claude hook must not have an `if` filter that can skip Bash calls")
    if handler.get("args"):
        problems.append("Claude hook must not add separate `args` to the checked shell command")
    timeout = handler.get("timeout", 600)
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout < 1:
        problems.append("Claude hook timeout must be a positive integer")
    command_problems, bridge_command = _bridge_command(handler.get("command"), CLAUDE_BRIDGE)
    problems.extend(command_problems)
    return problems, bridge_command


def _diagnose(harness: str, config_path: Path, run_probe: bool) -> Diagnosis:
    problems, bridge_command = (
        _check_kimi_config(config_path) if harness == "kimi"
        else _check_claude_config(config_path)
    )
    if problems:
        return Diagnosis(harness, config_path, "FAIL", "NOT_RUN", problems)
    if not run_probe:
        return Diagnosis(harness, config_path, "PASS", "NOT_RUN", [])
    assert bridge_command is not None
    probe_problems = _probe(harness, *bridge_command)
    return Diagnosis(
        harness, config_path, "PASS",
        "FAIL" if probe_problems else "PASS", probe_problems)


def check_kimi(config_path: Path, run_probe: bool = False) -> list[str]:
    """Compatibility wrapper for callers using the original Kimi checker."""
    return _diagnose("kimi", config_path, run_probe).problems


def check_claude(config_path: Path, run_probe: bool = False) -> list[str]:
    return _diagnose("claude", config_path, run_probe).problems


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="harness", required=True)
    kimi = subcommands.add_parser("kimi", help="check the local Kimi hook")
    kimi.add_argument(
        "--config", help="Kimi config.toml (defaults to KIMI_CODE_HOME or ~/.kimi-code)")
    claude = subcommands.add_parser("claude", help="check a selected Claude settings file")
    claude.add_argument(
        "--config", help="Claude settings.json (defaults to .claude/settings.json in cwd)")
    for command in (kimi, claude):
        command.add_argument(
            "--probe", action="store_true",
            help="exercise the local bridge on harmless payloads; no model call",
        )
        command.add_argument("--json", action="store_true", help="print machine-readable status")
    args = parser.parse_args(argv)
    config_path = (
        _kimi_config_path(args.config) if args.harness == "kimi"
        else _claude_config_path(args.config)
    )
    diagnosis = _diagnose(args.harness, config_path, args.probe)
    if args.json:
        print(json.dumps(diagnosis.as_dict(), ensure_ascii=False))
        return 1 if diagnosis.problems else 0
    if diagnosis.problems:
        for problem in diagnosis.problems:
            print(f"FAIL: {problem}", file=sys.stderr)
    else:
        print(f"PASS: {args.harness} selected hook configuration shape checked ({config_path})")
    if diagnosis.local_probe == "PASS":
        print("PASS: local Bash allow, malformed refusal, Core policy refusal, "
              "ASK mapping, and failed-interpreter probes")
    else:
        print(f"LOCAL_PROBE: {diagnosis.local_probe}")
    print("HOST_INTERCEPTION: UNVERIFIED (the selected file and local bridge "
          "do not prove a live host loaded or enforced this hook)")
    return 1 if diagnosis.problems else 0


if __name__ == "__main__":
    sys.exit(main())
