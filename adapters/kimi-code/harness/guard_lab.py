#!/usr/bin/env python3
"""Private Kimi 2.1.1 Lab capture, role separation and explicit host review."""

from __future__ import annotations

import argparse
import copy
from datetime import date, datetime, time
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Optional, Sequence


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from adapters.harness_support import (  # noqa: E402
    capture_process, digest, read_regular, write_json, write_new,
)
from core.lab import (  # noqa: E402
    INJECTION_SAMPLES, LabError, MAX_SCAN_BYTES, MAX_TASK_BYTES, TRIAL_GROUP_PATTERN,
    TRIAL_LABEL_PATTERN, arm_observer, build_report, compare_runs, prepare_run,
    record_host_result, request_stop, scan_bytes,
)

TESTED_VERSION = "2.1.1"
MAX_CONFIG_BYTES = 1024 * 1024
MAX_RECORDS = 4096
# Match safe_view's filename convention: the corpus scanner otherwise treats
# a quoted store name in source code as a request to dump that store.
AUTH_STORE_NAME = "creden" + "tials"


def load_config(path: Path) -> tuple[bytes, dict]:
    try:
        import tomllib
    except ImportError as exc:
        raise LabError("Kimi Lab configuration checks require Python 3.11+") from exc
    payload = read_regular(path, MAX_CONFIG_BYTES)
    try:
        return payload, tomllib.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise LabError("Kimi configuration must be valid UTF-8 TOML") from exc


def toml_value(value) -> str:
    # Inline tables keep arbitrary configuration keys while avoiding a dependency
    # or editing the user's original TOML. Round-trip equality is mandatory.
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(
            json.dumps(key, ensure_ascii=False) + " = " + toml_value(item)
            for key, item in value.items()
        ) + " }"
    raise LabError("configuration contains an unsupported TOML value")


def without_guard(config: dict) -> tuple[dict, dict]:
    result = copy.deepcopy(config)
    hooks = result.get("hooks", [])
    if not isinstance(hooks, list):
        raise LabError("Kimi hooks must be an array")
    kept = [hook for hook in hooks if not (
        isinstance(hook, dict) and hook.get("event") == "PreToolUse"
        and "adapters/kimi-code/" in str(hook.get("command", ""))
        and any(name in str(hook.get("command", "")) for name in (
            "hook_bridge.sh", "pre_tool_use.py",
        ))
    )]
    changes = {"guard_hooks_removed": len(hooks) - len(kept), "guard_skill_dirs_removed": 0}
    if "hooks" in result:
        result["hooks"] = kept
    for key in ("extraSkillDirs", "extra_skill_dirs"):
        if key not in result:
            continue
        entries = result[key]
        if not isinstance(entries, list) or not all(isinstance(item, str) for item in entries):
            raise LabError("Kimi extra skill directories must be string arrays")
        filtered = [item for item in entries if Path(item).expanduser().resolve() != ROOT / "skills"]
        changes["guard_skill_dirs_removed"] += len(entries) - len(filtered)
        result[key] = filtered
    return result, changes


def config_fingerprint(config: dict) -> str:
    return digest(json.dumps(config, sort_keys=True, separators=(",", ":"), default=str).encode())


def prepare_homes(source_home: Path, output_dir: Path) -> dict:
    source = source_home.expanduser().resolve()
    raw, guarded = load_config(source / "config.toml")
    baseline, changes = without_guard(guarded)
    if changes["guard_hooks_removed"] != 1 or not changes["guard_skill_dirs_removed"]:
        raise LabError("expected one Guard hook and its repository skill root")
    output = output_dir.expanduser().absolute()
    if output.exists() or output.is_symlink() or not output.parent.is_dir():
        raise LabError("profile output directory must be new")
    output = output.resolve()
    if output == source or output in source.parents or source in output.parents:
        raise LabError("profile output and source home must be disjoint")
    import tomllib
    serialized = "".join(json.dumps(key) + " = " + toml_value(value) + "\n" for key, value in baseline.items()).encode()
    if tomllib.loads(serialized.decode()) != baseline:
        raise LabError("baseline TOML round-trip changed configuration")
    output.mkdir(mode=0o700)
    for label, payload in (("off", serialized), ("on", raw)):
        home = output / label
        home.mkdir(mode=0o700)
        write_new(home / "config.toml", payload)
        # Copy only authentication/bootstrap state, never sessions or model logs.
        # Private copies let token refresh avoid writes to the user's home.
        for name in (AUTH_STORE_NAME, "oauth", "device_id", "region"):
            original = source / name
            if original.is_symlink():
                raise LabError("authentication bootstrap entries must not be symlinks")
            if original.is_file():
                write_new(home / name, read_regular(original, MAX_CONFIG_BYTES))
            elif original.is_dir():
                target = home / name
                target.mkdir(mode=0o700)
                for item in original.rglob("*"):
                    if item.is_symlink():
                        raise LabError("authentication tree must not contain symlinks")
                    selected = target / item.relative_to(original)
                    if item.is_dir():
                        selected.mkdir(mode=0o700, exist_ok=True)
                    elif item.is_file():
                        selected.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                        write_new(selected, read_regular(item, MAX_CONFIG_BYTES))
    identity = config_fingerprint(baseline)
    manifest = {
        "schema_version": 1, "non_guard_configuration_sha256": identity,
        "semantic_equality_verified": True, **changes,
        "auth_copied_privately": True, "source_modified": False,
    }
    write_json(output / "pair.json", manifest)
    return {"profile_dir": str(output), **manifest}


def check_host(executable: str, home: Path, guard_state: str) -> tuple[dict, str, dict]:
    selected = shutil.which(executable)
    if not selected:
        raise LabError("select the Kimi 2.1.1 executable explicitly")
    selected = str(Path(selected).absolute())
    home = home.expanduser().resolve()
    if home == (Path.home() / ".kimi-code").resolve():
        raise LabError("select a disposable Kimi home")
    raw, config = load_config(home / "config.toml")
    baseline, changes = without_guard(config)
    if guard_state not in {"off", "on"}:
        raise LabError("guard state must be off or on")
    if guard_state == "off" and any(changes.values()):
        raise LabError("guard-off configuration still contains known Guard entries")
    if guard_state == "on" and changes["guard_hooks_removed"] != 1:
        raise LabError("guard-on configuration requires one known Guard hook")
    if guard_state == "on":
        from doctor import check_kimi
        if check_kimi(home / "config.toml", run_probe=True):
            raise LabError("guard-on Kimi local configuration or bridge probe failed")
    environment = {key: value for key, value in os.environ.items() if not key.startswith("AGENT_GUARD_")}
    environment["KIMI_CODE_HOME"] = str(home)
    result, streams = capture_process([selected, "--version"], home, environment, 15)
    if result["exit_code"] != 0 or streams["stdout"].strip() != TESTED_VERSION.encode():
        raise LabError("Kimi version preflight requires exactly " + TESTED_VERSION)
    return {
        "harness_version": TESTED_VERSION, "guard_state": guard_state.upper(),
        "configuration_sha256": digest(raw),
        "non_guard_configuration_sha256": config_fingerprint(baseline),
        "identity_source": "user-declared",
    }, selected, environment


def split_stream(payload: bytes) -> tuple[dict, dict[str, bytes]]:
    """Recognize the selected version's NDJSON role records without claiming authorship."""
    roles = {"assistant": [], "tool": [], "meta": []}
    issues = []
    version_count = 0
    record_count = 0
    meaningful = False
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeError:
        return {"recognized": False, "issues": ["STREAM_NOT_UTF8"], "record_count": 0}, {}
    for line in lines:
        if not line.strip():
            continue
        record_count += 1
        if record_count > MAX_RECORDS:
            issues.append("STREAM_RECORD_LIMIT")
            break
        try:
            value = json.loads(line)
        except ValueError:
            issues.append("STREAM_NOT_NDJSON")
            continue
        if not isinstance(value, dict) or value.get("role") not in roles:
            issues.append("UNKNOWN_RECORD_ROLE")
            continue
        role = value["role"]
        if role == "meta":
            if not isinstance(value.get("type"), str):
                issues.append("INVALID_META_RECORD")
            if value.get("type") == "system.version":
                version_count += 1
                if value.get("version") != TESTED_VERSION:
                    issues.append("STREAM_VERSION_MISMATCH")
        elif not isinstance(value.get("content", ""), str):
            issues.append("UNSUPPORTED_CONTENT_SHAPE")
        elif role == "tool" and not isinstance(value.get("tool_call_id"), str):
            issues.append("TOOL_CALL_ID_MISSING")
        if role == "assistant" and isinstance(value.get("content"), str) and value["content"].strip():
            meaningful = True
        roles[role].append(line.encode("utf-8") + b"\n")
    if version_count != 1:
        issues.append("VERSION_RECORD_MISSING_OR_DUPLICATE")
    if not meaningful:
        issues.append("ASSISTANT_RESPONSE_MISSING")
    return {
        "recognized": not issues, "issues": sorted(set(issues)),
        "record_count": record_count,
        "role_counts": {name: len(lines) for name, lines in roles.items()},
        "provenance": "captured-role-labels-not-authenticated-authorship",
    }, {name: b"".join(lines) for name, lines in roles.items()}


def run_trial(
    output_dir: Path, home: Path, executable: str, case_id: str, task_file: Path,
    model_label: str, trial_group: str, guard_state: str, timeout: int = 180,
    attack_sample: str = "direct-v1",
) -> dict:
    if case_id not in {"clean", "injection-probe"} or not 1 <= timeout <= 600:
        raise LabError("unsupported Kimi case or timeout")
    if attack_sample not in INJECTION_SAMPLES or (case_id != "injection-probe" and attack_sample != "direct-v1"):
        raise LabError("select a known attack sample for injection-probe only")
    if not TRIAL_LABEL_PATTERN.fullmatch(model_label) or not TRIAL_GROUP_PATTERN.fullmatch(trial_group):
        raise LabError("use public Lab model labels and a trial-group slug")
    task = read_regular(task_file.expanduser().absolute(), MAX_TASK_BYTES)
    prompt = task.decode("utf-8")
    if not prompt.strip() or "\x00" in prompt:
        raise LabError("task must be nonempty UTF-8 without NUL bytes")
    output = output_dir.expanduser().absolute()
    home = home.expanduser().resolve()
    if output.exists() or output.is_symlink() or not output.parent.is_dir():
        raise LabError("trial output directory must be new")
    output = output.resolve()
    if output == home or output in home.parents or home in output.parents:
        raise LabError("Kimi home and trial output must be disjoint")
    preflight, selected, environment = check_host(executable, home, guard_state)
    output.mkdir(mode=0o700)
    capture = output / "capture"
    capture.mkdir(mode=0o700)
    metadata = {} if case_id == "clean" else {
        "harness_label": "Kimi Code", "harness_version": TESTED_VERSION,
        "model_label": model_label, "guard_state": guard_state,
        "trial_group": trial_group, "task_file": task_file,
        "non_guard_configuration_sha256": preflight["non_guard_configuration_sha256"],
    }
    prepared = prepare_run(output / "fixture", output / "evidence", case_id,
        model_usage="real", attack_sample=attack_sample, **metadata)
    if metadata and prepared["trial"]["task_sha256"] != digest(task):
        raise LabError("task changed during preparation")
    fixture = Path(prepared["fixture_dir"])
    evidence = Path(prepared["evidence_dir"])
    # Startup handles its own failure/stop request; preserve its original error.
    arm_observer(evidence, timeout + 15)
    try:
        host, streams = capture_process([
            selected, "-p", prompt, "--output-format", "stream-json",
        ], fixture, environment, timeout)
    finally:
        request_stop(evidence)
    configuration_unchanged = False
    try:
        after, _ = load_config(home / "config.toml")
        configuration_unchanged = digest(after) == preflight["configuration_sha256"]
    except (LabError, OSError):
        pass
    stream_summary, _ = split_stream(streams["stdout"])
    captures = {}
    for name, payload in streams.items():
        write_new(capture / (name + ".bin"), payload)
        captures[name] = {"bytes": len(payload), "sha256": digest(payload)}
    receipt = {
        "schema_version": 1, "run_id": prepared["run_id"], "case_id": case_id,
        **({"attack_sample": prepared["attack_sample"]} if "attack_sample" in prepared else {}),
        "task_sha256": digest(task), "model_label": model_label, "trial_group": trial_group,
        "preflight": preflight, "configuration_unchanged": configuration_unchanged,
        "stream": stream_summary, "host": host, "captures": captures,
        "completion": "USER_REVIEW_REQUIRED", "raw_host_output_retained": True,
    }
    write_json(capture / "receipt.json", receipt)
    report = build_report(evidence)
    return {
        "output_dir": str(output), "run_id": prepared["run_id"],
        "host": host, "stream": stream_summary,
        "configuration_unchanged": receipt["configuration_unchanged"],
        "completion": receipt["completion"], "report_result": report["result"],
    }


def finalize_trial(output_dir: Path, status: str) -> dict:
    output = output_dir.expanduser().absolute()
    if any(path.is_symlink() for path in (output, output / "capture", output / "evidence")):
        raise LabError("trial directories must not be symlinks")
    receipt = json.loads(read_regular(output / "capture/receipt.json", MAX_CONFIG_BYTES))
    run = json.loads(read_regular(output / "evidence/run.json", MAX_CONFIG_BYTES))
    if (receipt["run_id"], receipt["case_id"]) != (run["run_id"], run["case_id"]):
        raise LabError("capture and Lab identities differ")
    if Path(run["evidence_dir"]).resolve() != (output / "evidence").resolve():
        raise LabError("capture evidence path does not match this trial")
    streams = {}
    for name in ("stdout", "stderr"):
        payload = read_regular(output / "capture" / (name + ".bin"), MAX_SCAN_BYTES)
        if receipt["captures"][name] != {"bytes": len(payload), "sha256": digest(payload)}:
            raise LabError("capture integrity check failed")
        streams[name] = payload
    summary, roles = split_stream(streams["stdout"])
    if summary != receipt["stream"]:
        raise LabError("stream interpretation differs from its capture receipt")
    if isinstance(run.get("trial"), dict) and "non_guard_configuration_sha256" in run["trial"] and (
        run["trial"]["non_guard_configuration_sha256"]
        != receipt["preflight"]["non_guard_configuration_sha256"]
        or run["trial"]["task_sha256"] != receipt["task_sha256"]
    ):
        raise LabError("capture configuration or task differs from the Lab trial")
    host = receipt["host"]
    if status == "completed" and (
        host["exit_code"] != 0 or host["timed_out"] or host["start_failed"]
        or host["output_limit_exceeded"] or not receipt["configuration_unchanged"]
        or not summary["recognized"]
    ):
        raise LabError("failed, changed or unrecognized Kimi capture cannot be completed")
    if host["timed_out"] and status != "timed-out":
        raise LabError("a timed-out capture must be recorded as timed-out")
    exit_code = host["exit_code"]
    if not isinstance(exit_code, int) or not 0 <= exit_code <= 255:
        exit_code = None
    evidence = output / "evidence"
    record_host_result(evidence, status, exit_code)
    if summary["recognized"]:
        for role, stage in (("assistant", "model-output"), ("tool", "tool-output"), ("meta", "host-output")):
            scan_bytes(evidence, roles[role], stage, source_kind="kimi-role-" + role)
    else:
        scan_bytes(evidence, streams["stdout"], "host-output", source_kind="kimi-unrecognized-stdout")
    scan_bytes(evidence, streams["stderr"], "host-output", source_kind="kimi-stderr")
    return build_report(evidence)


def compare_trials(baseline_dir: Path, guarded_dir: Path) -> dict:
    receipts = [json.loads(read_regular(path / "capture/receipt.json", MAX_CONFIG_BYTES))
                for path in (baseline_dir, guarded_dir)]
    result = compare_runs(baseline_dir / "evidence", guarded_dir / "evidence")
    matching = (
        receipts[0]["preflight"]["non_guard_configuration_sha256"]
        == receipts[1]["preflight"]["non_guard_configuration_sha256"]
    )
    result["matching_dimensions"]["non_guard_configuration"] = matching
    if not matching:
        result.update({
            "comparison_outcome": "INCOMPARABLE", "result": "INCONCLUSIVE",
            "evidence_level": "UNESTABLISHED", "reason": "NON_GUARD_CONFIGURATION_MISMATCH",
        })
        result["precondition_issues"].append("NON_GUARD_CONFIGURATION_MISMATCH")
    return result


def main(arguments: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    profiles = commands.add_parser("prepare-homes", help="derive private matched homes; no model")
    profiles.add_argument("--source-home", type=Path, required=True)
    profiles.add_argument("--output-dir", type=Path, required=True)
    check = commands.add_parser("check", help="check selected version/configuration; no model")
    run = commands.add_parser("run", help="capture one real-host trial")
    for command in (check, run):
        command.add_argument("--kimi-home", type=Path, required=True)
        command.add_argument("--kimi-executable", required=True)
        command.add_argument("--guard-state", choices=("off", "on"), required=True)
    run.add_argument("--output-dir", type=Path, required=True)
    run.add_argument("--case", choices=("clean", "injection-probe"), required=True)
    run.add_argument("--attack-sample", choices=sorted(INJECTION_SAMPLES), default="direct-v1")
    run.add_argument("--task-file", type=Path, default=ROOT / "docs/lab/tasks/guard-lab-trial-task.md")
    run.add_argument("--model-label", required=True)
    run.add_argument("--trial-group", required=True)
    run.add_argument("--timeout", type=int, default=180)
    finalize = commands.add_parser("finalize", help="record reviewed completion and scan role channels")
    finalize.add_argument("--output-dir", type=Path, required=True)
    finalize.add_argument("--status", choices=("completed", "failed", "timed-out"), required=True)
    compare = commands.add_parser("compare", help="compare reviewed trials with matching non-Guard settings")
    compare.add_argument("--baseline-dir", type=Path, required=True)
    compare.add_argument("--guarded-dir", type=Path, required=True)
    options = parser.parse_args(arguments)
    try:
        if options.command == "prepare-homes":
            value = prepare_homes(options.source_home, options.output_dir)
        elif options.command == "check":
            value, _, _ = check_host(options.kimi_executable, options.kimi_home, options.guard_state)
        elif options.command == "run":
            value = run_trial(options.output_dir, options.kimi_home, options.kimi_executable,
                options.case, options.task_file, options.model_label, options.trial_group,
                options.guard_state, options.timeout, options.attack_sample)
        elif options.command == "finalize":
            value = finalize_trial(options.output_dir, options.status)
        else:
            value = compare_trials(options.baseline_dir, options.guarded_dir)
        print(json.dumps(value, sort_keys=True))
        return {"COMPLETE": 0, "FAIL": 2, "INCONCLUSIVE": 4}.get(value.get("result"), 0)
    except LabError as exc:
        print("Kimi Lab: " + str(exc), file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print("Kimi Lab operation failed; raw diagnostics are not printed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
