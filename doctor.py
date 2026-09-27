#!/usr/bin/env python3
"""Local agent-guard installation checks; never edits harness configuration."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Optional

from core.dialects import resolve_dialect


ROOT = Path(__file__).resolve().parent
KIMI_BRIDGE = ROOT / "adapters" / "kimi-code" / "hook_bridge.sh"
CLAUDE_BRIDGE = ROOT / "adapters" / "claude" / "hook_bridge.sh"
PROFILE_PATHS = {
    "kimi": ROOT / "adapters" / "kimi-code" / "compatibility.json",
    "claude": ROOT / "adapters" / "claude" / "compatibility.json",
}


@dataclass(frozen=True)
class HostProfile:
    profile_id: str
    harness: str
    display_name: str
    executable_candidates: tuple[str, ...]
    version_args: tuple[str, ...]
    version_pattern: str
    tested_versions: tuple[str, ...]
    path: Path


@dataclass
class Diagnosis:
    harness: str
    config_path: Path
    configuration: str
    local_probe: str
    problems: list[str]
    profile: Optional[str] = None
    host_version: Optional[str] = None
    drift_status: str = "NOT_CHECKED"
    configuration_fingerprint: Optional[str] = None
    runtime_fingerprint: Optional[str] = None
    drift_problems: Optional[list[str]] = None
    baseline_written: bool = False

    def as_dict(self) -> dict:
        return {
            "harness": self.harness,
            "configuration": self.configuration,
            "local_probe": self.local_probe,
            "host_interception": "UNVERIFIED",
            "problems": self.problems,
            "profile": self.profile,
            "host_version": self.host_version,
            "drift_status": self.drift_status,
            "configuration_fingerprint": self.configuration_fingerprint,
            "runtime_fingerprint": self.runtime_fingerprint,
            "drift_problems": self.drift_problems or [],
            "baseline_written": self.baseline_written,
        }


def _load_profile(harness: str) -> HostProfile:
    path = PROFILE_PATHS[harness]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("compatibility profile cannot be read") from exc
    required = {
        "schema_version", "profile_id", "harness", "display_name",
        "executable_candidates", "version_args", "version_pattern",
        "tested_versions", "contract",
    }
    if (not isinstance(data, dict) or not required.issubset(data)
            or data.get("schema_version") != 1
            or data.get("harness") != harness
            or not all(isinstance(data.get(key), str) and data[key]
                       for key in ("profile_id", "display_name", "version_pattern"))
            or not isinstance(data.get("contract"), dict)):
        raise ValueError("compatibility profile has an invalid shape")
    list_keys = ("executable_candidates", "version_args", "tested_versions")
    if any(not isinstance(data.get(key), list)
           or not all(isinstance(item, str) and item for item in data[key])
           for key in list_keys):
        raise ValueError("compatibility profile has invalid command/version fields")
    if not data["executable_candidates"] or not data["tested_versions"]:
        raise ValueError("compatibility profile has no executable or tested version")
    try:
        re.compile(data["version_pattern"])
    except re.error as exc:
        raise ValueError("compatibility profile has an invalid version pattern") from exc
    return HostProfile(
        profile_id=data["profile_id"], harness=harness,
        display_name=data["display_name"],
        executable_candidates=tuple(data["executable_candidates"]),
        version_args=tuple(data["version_args"]),
        version_pattern=data["version_pattern"],
        tested_versions=tuple(data["tested_versions"]), path=path,
    )


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _runtime_fingerprint(harness: str, profile: HostProfile) -> str:
    paths = {
        ROOT / "package.json",
        profile.path,
        ROOT / "skills" / "delete-guard" / "scripts" / "check.py",
        ROOT / "skills" / "delete-guard" / "scripts" / "_bootstrap.py",
    }
    paths.update((ROOT / "core").glob("*.py"))
    if harness == "claude":
        paths.update({
            CLAUDE_BRIDGE,
            ROOT / "adapters" / "claude" / "pre_tool_use.py",
        })
    else:
        paths.update({
            KIMI_BRIDGE,
            ROOT / "adapters" / "kimi-code" / "pre_tool_use.py",
            ROOT / "adapters" / "claude" / "pre_tool_use.py",
        })
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: str(item.relative_to(ROOT))):
        relative = str(path.relative_to(ROOT)).encode("utf-8")
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return "sha256:" + digest.hexdigest()


def _detect_host_version(
        profile: HostProfile, explicit: Optional[str],
) -> tuple[Optional[str], Optional[str]]:
    executable: Optional[str] = None
    if explicit:
        executable = shutil.which(explicit)
    else:
        for candidate in profile.executable_candidates:
            executable = shutil.which(candidate)
            if executable:
                break
    if not executable:
        return None, f"{profile.display_name} executable was not found"
    try:
        result = subprocess.run(
            [executable, *profile.version_args], capture_output=True, text=True,
            cwd=ROOT, timeout=5, errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, f"{profile.display_name} version command could not finish"
    if result.returncode != 0:
        return None, f"{profile.display_name} version command exited non-zero"
    match = re.search(profile.version_pattern, result.stdout + "\n" + result.stderr)
    if not match:
        return None, f"{profile.display_name} version output was not recognized"
    return match.group(1), None


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


def _check_kimi_config(
        config_path: Path,
) -> tuple[list[str], Optional[tuple[Path, Path, Path]], Optional[str]]:
    problems: list[str] = []
    problems.extend(_dialect_problems())
    try:
        import tomllib
    except ImportError:
        return ["doctor kimi requires Python 3.11+ (the hook adapter does not)"], None, None

    try:
        with config_path.open("rb") as config_file:
            config = tomllib.load(config_file)
    except OSError:
        return problems + [f"cannot read Kimi config: {config_path}"], None, None
    except tomllib.TOMLDecodeError:
        return problems + [f"Kimi config is not valid TOML: {config_path}"], None, None

    hooks = config.get("hooks", [])
    if not isinstance(hooks, list):
        return problems + ["Kimi config `hooks` must be an array"], None, None
    candidates = [hook for hook in hooks
                  if isinstance(hook, dict)
                  and hook.get("event") == "PreToolUse"
                  and any(marker in str(hook.get("command", ""))
                          for marker in ("hook_bridge.sh", "pre_tool_use.py"))]
    if not candidates:
        return problems + ["no agent-guard PreToolUse hook found in Kimi config"], None, None
    if len(candidates) != 1:
        return problems + ["expected exactly one agent-guard PreToolUse hook"], None, None

    hook = candidates[0]
    if hook.get("matcher") != "^Bash$":
        problems.append("matcher must be exactly ^Bash$ for this checked installation")
    command_problems, bridge_command = _bridge_command(hook.get("command"), KIMI_BRIDGE)
    problems.extend(command_problems)
    timeout = hook.get("timeout", 30)
    if (not isinstance(timeout, int) or isinstance(timeout, bool)
            or not 1 <= timeout <= 600):
        problems.append("hook timeout must be an integer from 1 to 600 seconds")

    fingerprint = None
    if not problems and bridge_command is not None:
        dialect = resolve_dialect(
            os.environ.get("AGENT_GUARD_DIALECT"),
            source="env AGENT_GUARD_DIALECT").dialect
        fingerprint = _fingerprint({
            "schema_version": 1,
            "harness": "kimi",
            "event": hook.get("event"),
            "matcher": hook.get("matcher"),
            "command_tokens": [str(part) for part in bridge_command],
            "timeout": timeout,
            "dialect": dialect,
        })
    return problems, bridge_command, fingerprint


def _check_claude_config(
        config_path: Path,
) -> tuple[list[str], Optional[tuple[Path, Path, Path]], Optional[str]]:
    problems = _dialect_problems()
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        return problems + [f"cannot read Claude settings: {config_path}"], None, None
    except json.JSONDecodeError:
        return problems + [f"Claude settings is not valid JSON: {config_path}"], None, None
    if not isinstance(config, dict):
        return problems + ["Claude settings must be a JSON object"], None, None
    if ("disableAllHooks" in config
            and not isinstance(config["disableAllHooks"], bool)):
        problems.append("selected Claude settings has invalid disableAllHooks value")
    if config.get("disableAllHooks") is True:
        problems.append("selected Claude settings has disableAllHooks=true")
    hooks = config.get("hooks")
    if not isinstance(hooks, dict):
        return problems + ["Claude settings `hooks` must be an object"], None, None
    pre_tool = hooks.get("PreToolUse")
    if not isinstance(pre_tool, list):
        return problems + ["Claude settings `hooks.PreToolUse` must be an array"], None, None

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
            "no agent-guard PreToolUse hook found in selected Claude settings"], None, None
    if len(candidates) != 1:
        return problems + [
            "expected exactly one agent-guard PreToolUse hook in selected Claude settings"], None, None

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
    fingerprint = None
    if not problems and bridge_command is not None:
        dialect = resolve_dialect(
            os.environ.get("AGENT_GUARD_DIALECT"),
            source="env AGENT_GUARD_DIALECT").dialect
        fingerprint = _fingerprint({
            "schema_version": 1,
            "harness": "claude",
            "disable_all_hooks": bool(config.get("disableAllHooks", False)),
            "event": "PreToolUse",
            "matcher": group.get("matcher"),
            "handler_type": handler.get("type"),
            "async": bool(handler.get("async", False)),
            "command_tokens": [str(part) for part in bridge_command],
            "timeout": timeout,
            "dialect": dialect,
        })
    return problems, bridge_command, fingerprint


def _load_baseline(path: Path, harness: str) -> tuple[Optional[dict], list[str]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None, ["drift baseline cannot be read as JSON"]
    required = {
        "schema_version", "harness", "profile", "host_version",
        "configuration_fingerprint", "runtime_fingerprint",
    }
    if (not isinstance(data, dict) or not required.issubset(data)
            or data.get("schema_version") != 1
            or data.get("harness") != harness
            or not all(isinstance(data.get(key), str) and data[key]
                       for key in required - {"schema_version"})):
        return None, ["drift baseline has an invalid shape or harness"]
    for key in ("configuration_fingerprint", "runtime_fingerprint"):
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", data[key]):
            return None, ["drift baseline has an invalid fingerprint"]
    return data, []


def _write_baseline(path: Path, diagnosis: Diagnosis) -> Optional[str]:
    if (diagnosis.drift_status != "CURRENT"
            or diagnosis.local_probe != "PASS"
            or not diagnosis.profile
            or not diagnosis.host_version
            or not diagnosis.configuration_fingerprint
            or not diagnosis.runtime_fingerprint):
        return "baseline requires CURRENT drift status and a passing local probe"
    payload = {
        "schema_version": 1,
        "harness": diagnosis.harness,
        "profile": diagnosis.profile,
        "host_version": diagnosis.host_version,
        "configuration_fingerprint": diagnosis.configuration_fingerprint,
        "runtime_fingerprint": diagnosis.runtime_fingerprint,
    }
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as baseline_file:
            json.dump(payload, baseline_file, ensure_ascii=True, indent=2)
            baseline_file.write("\n")
    except OSError:
        return "baseline path must have an existing parent and must not exist"
    diagnosis.baseline_written = True
    return None


def _diagnose(
        harness: str, config_path: Path, run_probe: bool,
        check_drift: bool = False, host_executable: Optional[str] = None,
        baseline_path: Optional[Path] = None,
) -> Diagnosis:
    problems, bridge_command, configuration_fingerprint = (
        _check_kimi_config(config_path) if harness == "kimi"
        else _check_claude_config(config_path)
    )
    if problems:
        diagnosis = Diagnosis(
            harness, config_path, "FAIL", "NOT_RUN", problems,
            configuration_fingerprint=configuration_fingerprint)
    elif not run_probe:
        diagnosis = Diagnosis(
            harness, config_path, "PASS", "NOT_RUN", [],
            configuration_fingerprint=configuration_fingerprint)
    else:
        assert bridge_command is not None
        probe_problems = _probe(harness, *bridge_command)
        diagnosis = Diagnosis(
            harness, config_path, "PASS",
            "FAIL" if probe_problems else "PASS", probe_problems,
            configuration_fingerprint=configuration_fingerprint)

    if not check_drift:
        return diagnosis

    drift_problems: list[str] = []
    diagnosis.drift_problems = drift_problems
    try:
        profile = _load_profile(harness)
    except ValueError as exc:
        drift_problems.append(str(exc))
        diagnosis.drift_status = "UNVERIFIED"
        return diagnosis

    diagnosis.profile = profile.profile_id
    try:
        diagnosis.runtime_fingerprint = _runtime_fingerprint(harness, profile)
    except OSError:
        drift_problems.append("runtime fingerprint could not be computed")

    version, version_problem = _detect_host_version(profile, host_executable)
    diagnosis.host_version = version
    if version_problem:
        drift_problems.append(version_problem)

    baseline = None
    baseline_invalid = False
    if baseline_path is not None:
        baseline, baseline_problems = _load_baseline(baseline_path, harness)
        drift_problems.extend(baseline_problems)
        baseline_invalid = baseline is None

    if diagnosis.problems:
        diagnosis.drift_status = "BROKEN"
    elif baseline_invalid:
        diagnosis.drift_status = "UNVERIFIED"
    elif (not diagnosis.configuration_fingerprint
          or not diagnosis.runtime_fingerprint):
        diagnosis.drift_status = "UNVERIFIED"
    elif baseline is not None and (
            baseline["profile"] != diagnosis.profile
            or baseline["configuration_fingerprint"]
            != diagnosis.configuration_fingerprint
            or baseline["runtime_fingerprint"] != diagnosis.runtime_fingerprint):
        diagnosis.drift_status = "DRIFTED"
        if baseline["profile"] != diagnosis.profile:
            drift_problems.append("compatibility profile changed since baseline")
        if (baseline["configuration_fingerprint"]
                != diagnosis.configuration_fingerprint):
            drift_problems.append("hook configuration changed since baseline")
        if baseline["runtime_fingerprint"] != diagnosis.runtime_fingerprint:
            drift_problems.append("guard runtime changed since baseline")
    elif version is None:
        diagnosis.drift_status = "UNVERIFIED"
    elif (baseline is not None and baseline["host_version"] != version):
        diagnosis.drift_status = "STALE"
        drift_problems.append("host version changed since baseline")
    elif version not in profile.tested_versions:
        diagnosis.drift_status = "STALE"
        drift_problems.append("host version is outside the tested profile")
    else:
        diagnosis.drift_status = "CURRENT"
    return diagnosis


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
        command.add_argument(
            "--check-drift", action="store_true",
            help="query the local host version and compute privacy-safe fingerprints",
        )
        command.add_argument(
            "--host-executable",
            help="host executable name/path for --check-drift (defaults to profile candidates)",
        )
        command.add_argument(
            "--baseline", type=Path,
            help="compare --check-drift fingerprints with an existing baseline JSON",
        )
        command.add_argument(
            "--write-baseline", type=Path,
            help="create a new 0600 baseline (requires --probe and CURRENT status)",
        )
        command.add_argument(
            "--live-sentinel", action="store_true",
            help="run one real-host model/tool probe; may consume configured provider quota",
        )
        command.add_argument(
            "--sentinel-output", type=Path,
            help="new evidence directory for --live-sentinel (default: private temp dir)",
        )
        command.add_argument(
            "--sentinel-timeout", type=int, default=180, metavar="SECONDS",
            help="live sentinel timeout, 15..600 seconds (default: 180)",
        )
    args = parser.parse_args(argv)
    if args.write_baseline is not None and not args.probe:
        parser.error("--write-baseline requires --probe")
    if args.live_sentinel and args.write_baseline is not None:
        parser.error("--live-sentinel and --write-baseline must be separate runs")
    if args.sentinel_output is not None and not args.live_sentinel:
        parser.error("--sentinel-output requires --live-sentinel")
    if not 15 <= args.sentinel_timeout <= 600:
        parser.error("--sentinel-timeout must be from 15 to 600 seconds")
    check_drift = bool(
        args.check_drift or args.host_executable
        or args.baseline is not None or args.write_baseline is not None
        or args.live_sentinel)
    config_path = (
        _kimi_config_path(args.config) if args.harness == "kimi"
        else _claude_config_path(args.config)
    )
    diagnosis = _diagnose(
        args.harness, config_path, args.probe or args.live_sentinel, check_drift,
        args.host_executable,
        args.baseline.expanduser() if args.baseline is not None else None,
    )
    write_error = None
    if args.write_baseline is not None:
        write_error = _write_baseline(args.write_baseline.expanduser(), diagnosis)
        if write_error:
            if diagnosis.drift_problems is None:
                diagnosis.drift_problems = []
            diagnosis.drift_problems.append(write_error)
    sentinel_result = None
    sentinel_report = None
    if args.live_sentinel:
        if (diagnosis.configuration != "PASS"
                or diagnosis.local_probe != "PASS"):
            sentinel_report = {
                "status": "INCONCLUSIVE", "alarm": "WARNING",
                "reason": "LOCAL_PREFLIGHT_FAILED",
            }
        elif diagnosis.host_version is None:
            sentinel_report = {
                "status": "INCONCLUSIVE", "alarm": "WARNING",
                "reason": "HOST_VERSION_UNVERIFIED",
            }
        else:
            try:
                from live_sentinel import run_live_sentinel
                profile = _load_profile(args.harness)
                sentinel_result = run_live_sentinel(
                    args.harness, config_path, profile.executable_candidates,
                    host_executable=args.host_executable,
                    output_dir=(args.sentinel_output.expanduser()
                                if args.sentinel_output is not None else None),
                    timeout=args.sentinel_timeout,
                    drift_status=diagnosis.drift_status,
                )
                sentinel_report = sentinel_result.as_dict()
            except (OSError, ValueError):
                sentinel_report = {
                    "status": "INCONCLUSIVE", "alarm": "WARNING",
                    "reason": "SENTINEL_SETUP_FAILED",
                }
    if args.json:
        report = diagnosis.as_dict()
        if sentinel_report is not None:
            report["live_sentinel"] = sentinel_report
        print(json.dumps(report, ensure_ascii=False))
        if sentinel_report is not None:
            return {"PASS": 0, "FAIL": 2}.get(sentinel_report["status"], 1)
        return 1 if (diagnosis.problems or write_error or (
            check_drift and diagnosis.drift_status != "CURRENT")) else 0
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
    if check_drift:
        print(f"HOST_VERSION: {diagnosis.host_version or 'UNVERIFIED'}")
        print(f"DRIFT_STATUS: {diagnosis.drift_status}")
        for problem in diagnosis.drift_problems or []:
            print(f"DRIFT: {problem}", file=sys.stderr)
        if diagnosis.baseline_written:
            print("BASELINE: WRITTEN (fingerprints only; not host interception proof)")
    if sentinel_report is not None:
        print(f"LIVE_SENTINEL: {sentinel_report['status']}")
        print(f"ALARM: {sentinel_report['alarm']}")
        print(f"SENTINEL_REASON: {sentinel_report['reason']}")
        if sentinel_result is not None:
            print(f"EVIDENCE_DIR: {sentinel_result.evidence_dir}")
        if sentinel_report["status"] != "PASS":
            print("WARNING: live host protection was not verified", file=sys.stderr)
        return {"PASS": 0, "FAIL": 2}.get(sentinel_report["status"], 1)
    return 1 if (diagnosis.problems or write_error or (
        check_drift and diagnosis.drift_status != "CURRENT")) else 0


if __name__ == "__main__":
    sys.exit(main())
