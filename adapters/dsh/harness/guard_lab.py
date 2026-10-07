#!/usr/bin/env python3
"""Capture, review and compare bounded DSH headless Lab trials.

This runner deliberately keeps real-host completion user-declared. A zero
exit status and a nonempty capture are prerequisites, not proof of completion.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Optional, Sequence


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from core.lab import (  # noqa: E402
    INJECTION_SAMPLES, LabError, MAX_SCAN_BYTES, MAX_TASK_BYTES, TRIAL_GROUP_PATTERN,
    TRIAL_LABEL_PATTERN, arm_observer, build_report, compare_runs,
    prepare_run, record_host_result, request_stop, scan_bytes,
)
from adapters.harness_support import (  # noqa: E402
    capture_process as _capture_process, digest as _digest,
    read_regular as _read_regular, write_new as _write_new,
    write_json as _write_json,
)
from adapters.dsh.harness.native_session import (  # noqa: E402
    PARSER_VERSION, capture_session, check_decoder, session_inventory, split_session,
)
from adapters.dsh.harness.profiles import (  # noqa: E402
    ADAPTER, MEMBERS, GUARD_PROFILES, configuration_fingerprint, guard_patch,
    normalize_composition, prepare_homes,
)


TESTED_VERSION = "0.2.0-rc.2"
HOST_CONTRACTS = {"0.1.5-rc.1": (3, 4), "0.2.0-rc.2": (4, 5)}
BASELINE_PATCH = Path(__file__).with_name("baseline.patch.yml")
BOOTSTRAP_PATCH = Path(__file__).with_name("bootstrap.patch.yml")
BOOTSTRAP_INIT = Path(__file__).with_name("bootstrap_init.mjs")
RECEIPT_VERSION = 6
MAX_RECEIPT_BYTES = 64 * 1024


def _profile_options(profile, workspace, dsh_root):
    return {"profile": profile, "workspace": workspace, "dsh_root": dsh_root}


def _launcher_root(executable: str, version: str) -> Path:
    for parent in Path(executable).resolve().parents:
        manifest = parent / "package.json"
        if manifest.is_file():
            value = json.loads(_read_regular(manifest, MAX_RECEIPT_BYTES))
            if value.get("name") == "@deepseek-ai/dsh" and value.get("version") == version:
                return parent
    raise LabError("bootstrap requires the reviewed installed DSH package launcher")


def _guard_assets() -> dict:
    names = ["adapters/dsh/lib/index.js", "adapters/dsh/lib/read_result_guard.js",
        "adapters/dsh/lib/read_result_guard.py", "skills/exfil-guard/scripts/sanitize.py",
        "skills/exfil-guard/scripts/_payload.py", "skills/exfil-guard/scripts/_bootstrap.py"]
    names.extend(str(path.relative_to(ROOT)) for path in sorted((ROOT / "core").glob("*.py")))
    return {name: _digest(_read_regular(ROOT / name, MAX_SCAN_BYTES)) for name in names}


def _environment(home: Path) -> dict[str, str]:
    environment = dict(os.environ)
    # A previous sentinel/workspace must not redirect this independent trial.
    for name in tuple(environment):
        if name.startswith("AGENT_GUARD_"):
            environment.pop(name)
    environment["DSH_HOME"] = str(home)
    environment["DSH_TELEMETRY_DISABLED"] = "1"
    return environment


def _host_options(
    executable: str, home: Path, patches: Sequence[Path],
    guard_state: str = "off", guard_patch_file: Optional[Path] = None,
    *, guard_profile="deletion-v1", workspace=None, dsh_root=None,
) -> tuple[list[str], dict[str, str]]:
    if os.name != "posix":
        raise LabError("this DSH runner currently supports POSIX hosts only")
    resolved = shutil.which(executable)
    if not resolved:
        raise LabError("DSH executable was not found")
    resolved = str(Path(resolved).absolute())
    home = home.expanduser().resolve()
    if not (home / "profiles" / "headless" / "package.json").is_file():
        raise LabError("select an already initialized disposable headless DSH home")
    if home == (Path.home() / ".dsh").resolve():
        raise LabError("use a disposable DSH home, not the default user home")
    argv = [resolved, "--profile", "headless"]
    if guard_state not in {"off", "on"} or (guard_state == "on") != (guard_patch_file is not None):
        raise LabError("guard-on requires the generated local guard patch; guard-off omits it")
    expected_patch = guard_patch(guard_profile, workspace, dsh_root)
    if guard_patch_file is not None and _read_regular(guard_patch_file, MAX_RECEIPT_BYTES) != expected_patch:
        raise LabError("select the exact generated local Guard patch")
    layers = (*patches, BASELINE_PATCH, *((guard_patch_file,) if guard_patch_file else ()))
    for patch in layers:
        selected = patch.expanduser().absolute()
        _read_regular(selected, MAX_RECEIPT_BYTES)
        argv.extend(["--patch", str(selected)])
    return argv, _environment(home)


def _config_dump(
    argv: Sequence[str], cwd: Path, environment: dict[str, str],
    guard_state: str = "off",
    *, guard_profile="deletion-v1", workspace=None, dsh_root=None,
) -> bytes:
    result, output = _capture_process(
        [*argv, "--dump-config"], cwd, environment, 15,
    )
    if (
        result["exit_code"] != 0 or result["timed_out"]
        or result["output_limit_exceeded"] or not output["stdout"].strip()
    ):
        raise LabError("DSH config preflight failed; raw diagnostics are not printed")
    normalize_composition(output["stdout"], guard_state,
        **_profile_options(guard_profile, workspace, dsh_root))
    return output["stdout"]


def check_host(
    executable: str, home: Path, patches: Sequence[Path] = (),
    node_executable: str = "node",
    guard_state: str = "off", guard_patch_file: Optional[Path] = None,
    *, guard_profile="deletion-v1", workspace=None, dsh_root=None,
) -> tuple[dict, list[str], dict[str, str]]:
    home = home.expanduser().resolve()
    context = dict(guard_profile=guard_profile, workspace=workspace, dsh_root=dsh_root)
    argv, environment = _host_options(executable, home, patches, guard_state, guard_patch_file, **context)
    result, output = _capture_process([argv[0], "--version"], home.resolve(), environment, 10)
    version = output["stdout"].strip().decode("ascii", errors="replace")
    if (
        result["exit_code"] != 0 or result["timed_out"]
        or result["output_limit_exceeded"]
        or version not in HOST_CONTRACTS
    ):
        raise LabError("DSH version preflight requires a reviewed exact host version")
    if guard_profile == "read-redaction-v1" and _launcher_root(argv[0], version) != Path(dsh_root).resolve():
        raise LabError("read profile root must match the selected DSH launcher")
    configuration = _config_dump(argv, home.resolve(), environment, guard_state, **context)
    decoder, _ = check_decoder(node_executable, home, environment)
    return {
        "harness_version": version,
        "native_format_version": HOST_CONTRACTS[version][0],
        "profile": "headless", "guard_state": guard_state.upper(),
        "guard_state_source": "user-declared",
        "guard_profile": guard_profile, "native_parser_version": PARSER_VERSION,
        "composition_bytes": len(configuration),
        "composition_sha256": _digest(configuration),
        "telemetry_disabled": True, "configuration_retained": False,
        "model_identity_source": "user-declared",
        "native_decoder": decoder,
        "non_guard_configuration_sha256": configuration_fingerprint(home, configuration, environment, guard_state,
            **_profile_options(guard_profile, workspace, dsh_root)),
        "configuration_scope": "dsh-lab-non-guard-v2",
        "guard_adapter_sha256": _digest(_read_regular(ADAPTER, MAX_SCAN_BYTES)),
        "guard_assets": _guard_assets(),
    }, argv, environment


def _initialize_home(executable, home, patches, node_executable) -> dict:
    preflight, argv, environment = check_host(executable, home, patches, node_executable)
    if session_inventory(home):
        raise LabError("bootstrap requires a fresh disposable home")
    if preflight["harness_version"] == "0.1.5-rc.1":
        return {"method": "legacy-host-no-import", "model_tasks_launched": 0}
    root = _launcher_root(argv[0], preflight["harness_version"])
    legacy = home / "settings.yaml"
    imported = home / "settings.yaml.imported"
    legacy_payload = _read_regular(legacy, MAX_SCAN_BYTES) if legacy.exists() else None
    if legacy_payload is not None and imported.exists():
        raise LabError("ambiguous legacy bootstrap documents")
    init_argv = [node_executable, str(BOOTSTRAP_INIT), str(root),
        *(str(path.expanduser().absolute()) for path in patches), str(BASELINE_PATCH), str(BOOTSTRAP_PATCH)]
    capture = home / ".lab-bootstrap"
    if capture.exists() or capture.is_symlink():
        raise LabError("bootstrap capture already exists; use fresh copied homes")
    capture.mkdir(mode=0o700)
    fingerprints = []
    for index in range(2):
        result, streams = _capture_process(init_argv, home, environment, 60)
        for name, payload in streams.items():
            _write_new(capture / (str(index + 1) + "-" + name + ".bin"), payload)
        _write_json(capture / (str(index + 1) + "-receipt.json"), {
            "host": result, "model_tasks_launched": 0,
            "streams": {name: {"bytes": len(payload), "sha256": _digest(payload)}
                        for name, payload in streams.items()}})
        if (result["exit_code"] != 0 or any(result[key] for key in
                ("timed_out", "start_failed", "output_limit_exceeded"))
                or any(b"was not imported" in data for data in streams.values())
                or session_inventory(home)):
            raise LabError("native bootstrap failed or created a session; no model task was launched by the Lab")
        if legacy.exists() or (legacy_payload is not None
                and _read_regular(imported, MAX_SCAN_BYTES) != legacy_payload):
            raise LabError("legacy bootstrap import did not complete")
        value, _, _ = check_host(executable, home, patches, node_executable)
        fingerprints.append(value["non_guard_configuration_sha256"])
    if fingerprints[0] != fingerprints[1]:
        raise LabError("native bootstrap settings did not stabilize")
    return {"method": "native-import-with-driver-disabled-v1", "passes": 2,
        "model_tasks_launched": 0, "sessions_created": 0,
        "settings_stable": True, "legacy_imported": legacy_payload is not None}


def run_trial(
    output_dir: Path, home: Path, executable: str, case_id: str,
    task_file: Path, model_label: str, trial_group: str,
    patches: Sequence[Path] = (), timeout: int = 120,
    attack_sample: str = "direct-v1",
    node_executable: str = "node",
    guard_state: str = "off", guard_patch_file: Optional[Path] = None,
    expected_configuration_sha256: Optional[str] = None,
    *, guard_profile="deletion-v1", dsh_root=None,
) -> dict:
    if case_id not in {"clean", "injection-probe"}:
        raise LabError("this runner supports clean and injection-probe only")
    if not 1 <= timeout <= 600:
        raise LabError("host timeout must be between 1 and 600 seconds")
    if attack_sample not in INJECTION_SAMPLES or (case_id != "injection-probe" and attack_sample != "direct-v1"):
        raise LabError("select a known attack sample for injection-probe only")
    if case_id == "injection-probe" and ((attack_sample == "read-redaction-v1") != (guard_profile == "read-redaction-v1")):
        raise LabError("read redaction profile and attack sample must be selected together")
    if not TRIAL_LABEL_PATTERN.fullmatch(model_label):
        raise LabError("model label must follow the Lab public-label format")
    if not TRIAL_GROUP_PATTERN.fullmatch(trial_group):
        raise LabError("trial group must follow the Lab slug format")
    task = _read_regular(task_file.expanduser().absolute(), MAX_TASK_BYTES)
    try:
        prompt = task.decode("utf-8")
    except UnicodeError as exc:
        raise LabError("task must be UTF-8") from exc
    if not prompt.strip() or "\x00" in prompt:
        raise LabError("task must be nonempty text without NUL bytes")
    output = output_dir.expanduser().absolute()
    home = home.expanduser().resolve()
    if output.is_symlink() or output.exists() or not output.parent.is_dir():
        raise LabError("output directory must be new with an existing parent")
    output = output.resolve()
    if output == home or output in home.parents or home in output.parents:
        raise LabError("trial output and the selected DSH home must be disjoint")
    context = dict(guard_profile=guard_profile, workspace=output / "fixture", dsh_root=dsh_root)
    preflight, argv, environment = check_host(executable, home, patches, node_executable, guard_state, guard_patch_file, **context)
    if preflight["harness_version"] == "0.2.0-rc.2" and (home / "settings.yaml").exists():
        raise LabError("initialize the copied modern DSH home before freezing a trial")
    if expected_configuration_sha256 is not None and preflight["non_guard_configuration_sha256"] != expected_configuration_sha256:
        raise LabError("paired non-guard settings changed; no host task was launched")
    before = session_inventory(home)
    selected_node = str(Path(shutil.which(node_executable)).absolute())
    output.mkdir(mode=0o700)
    capture = output / "capture"
    capture.mkdir(mode=0o700)
    metadata = {}
    if case_id == "injection-probe":
        metadata = {
            "harness_label": "DSH", "harness_version": preflight["harness_version"],
            "model_label": model_label, "guard_state": guard_state,
            "trial_group": trial_group, "task_file": task_file,
            "non_guard_configuration_sha256": preflight["non_guard_configuration_sha256"],
        }
    prepared = prepare_run(
        output / "fixture", output / "evidence", case_id,
        model_usage="real", attack_sample=attack_sample, **metadata,
    )
    if metadata and prepared["trial"]["task_sha256"] != _digest(task):
        raise LabError("task changed during preparation; no host task was launched")
    evidence = Path(prepared["evidence_dir"])
    fixture = Path(prepared["fixture_dir"])
    # Startup handles its own failure/stop request. Only a ready observer can
    # be stopped here; otherwise cleanup would hide the original startup error.
    arm_observer(evidence, timeout + 15)
    try:
        # Task bytes used for hashing are exactly the bytes passed to the host.
        result, streams = _capture_process([*argv, prompt], fixture, environment, timeout)
    finally:
        request_stop(evidence)
    composition_unchanged = False
    try:
        after = _config_dump(argv, home, environment, guard_state, **context)
        composition_unchanged = (
            _digest(after) == preflight["composition_sha256"]
            and configuration_fingerprint(home, after, environment, guard_state,
                **_profile_options(guard_profile, output / "fixture", dsh_root))
            == preflight["non_guard_configuration_sha256"]
            and _digest(_read_regular(ADAPTER, MAX_SCAN_BYTES)) == preflight["guard_adapter_sha256"]
            and _guard_assets() == preflight["guard_assets"]
        )
    except LabError:
        pass
    captures = {}
    for name, payload in streams.items():
        _write_new(capture / (name + ".bin"), payload)
        captures[name] = {"bytes": len(payload), "sha256": _digest(payload)}
    native, native_captures = capture_session(
        home, before, capture, fixture, _digest(task), selected_node, environment,
        native_format_version=preflight["native_format_version"],
    )
    captures.update(native_captures)
    receipt = {
        "schema_version": RECEIPT_VERSION,
        "run_id": prepared["run_id"], "case_id": case_id,
        **({"attack_sample": prepared["attack_sample"]} if "attack_sample" in prepared else {}),
        "task_sha256": _digest(task), "task_bytes": len(task),
        "model_label": model_label, "trial_group": trial_group,
        "task_text_retained": "session" in captures, "preflight": preflight,
        "composition_unchanged": composition_unchanged,
        "host": result, "captures": captures,
        "native_session": native,
        "completion": "USER_REVIEW_REQUIRED",
        "raw_host_output_retained": True,
    }
    _write_json(capture / "receipt.json", receipt)
    report = build_report(evidence)
    return {
        "output_dir": str(output), "run_id": prepared["run_id"],
        "case_id": case_id, "completion": receipt["completion"],
        "host": result, "composition_unchanged": composition_unchanged,
        "native_session": native,
        "report_result": report["result"],
        "raw_host_output_retained": True,
    }


def _read_capture(output_dir: Path) -> tuple[dict, dict, dict, Optional[dict], bool]:
    output = output_dir.expanduser().absolute()
    if (
        output.is_symlink() or (output / "capture").is_symlink()
        or (output / "evidence").is_symlink()
    ):
        raise LabError("trial directories must not be symlinks")
    capture = output / "capture"
    receipt = json.loads(_read_regular(capture / "receipt.json", MAX_RECEIPT_BYTES))
    run = json.loads(_read_regular(output / "evidence" / "run.json", MAX_RECEIPT_BYTES))
    if not isinstance(receipt, dict) or not isinstance(run, dict):
        raise LabError("capture metadata must be JSON objects")
    if type(receipt.get("schema_version")) is not int or receipt["schema_version"] not in {1, 2, 3, 4, 5, 6}:
        raise LabError("unsupported capture receipt version")
    host_version = receipt.get("preflight", {}).get("harness_version")
    native_format = 4 if receipt["schema_version"] == 5 else 3
    if receipt["schema_version"] == 6:
        preflight = receipt.get("preflight", {})
        native_format = preflight.get("native_format_version")
        if (type(native_format) is not int or native_format not in {3, 4}
                or type(preflight.get("native_parser_version")) is not int
                or preflight["native_parser_version"] != 3
                or preflight.get("configuration_scope") != "dsh-lab-non-guard-v2"
                or preflight.get("guard_profile") not in GUARD_PROFILES):
            raise LabError("capture parser/profile/format contract does not match")
    if receipt["schema_version"] >= 3:
        expected_host = "0.2.0-rc.2" if native_format == 4 else "0.1.5-rc.1"
        if host_version != expected_host or (native_format == 4 and
                receipt["preflight"].get("native_format_version") != 4):
            raise LabError("capture host and native format contract do not match")
    if receipt.get("run_id") != run.get("run_id") or receipt.get("case_id") != run.get("case_id"):
        raise LabError("capture and Lab run identities do not match")
    if (
        Path(run["evidence_dir"]).resolve() != (output / "evidence").resolve()
        or Path(run["fixture_dir"]).resolve() != (output / "fixture").resolve()
        or run.get("case_id") not in {"clean", "injection-probe"}
    ):
        raise LabError("capture paths or case do not match this trial")
    payloads = {}
    names = {"stdout": "stdout.bin", "stderr": "stderr.bin"}
    if receipt["schema_version"] >= 2:
        for name, filename in (("session", "session.bin"), ("session_jsonl", "session.jsonl")):
            if name in receipt["captures"]:
                names[name] = filename
    for name, filename in names.items():
        payload = _read_regular(capture / filename, MAX_SCAN_BYTES)
        expected = receipt["captures"][name]
        if expected != {"bytes": len(payload), "sha256": _digest(payload)}:
            raise LabError("host capture integrity check failed")
        payloads[name] = payload
    roles = None
    native_ready = False
    if isinstance(run.get("trial"), dict) and receipt["task_sha256"] != run["trial"]["task_sha256"]:
        raise LabError("capture task fingerprint differs from the Lab trial")
    if receipt["schema_version"] >= 3 and isinstance(run.get("trial"), dict):
        trial = run["trial"]
        if any(trial.get(key) != value for key, value in {
            "harness_label": "DSH", "harness_version": host_version,
            "guard_state": receipt["preflight"]["guard_state"],
            "non_guard_configuration_sha256": receipt["preflight"]["non_guard_configuration_sha256"],
            "model_label": receipt["model_label"], "trial_group": receipt["trial_group"],
        }.items()):
            raise LabError("capture configuration or identity differs from the Lab trial")
    if receipt["schema_version"] >= 2:
        native = receipt["native_session"]
        if not isinstance(native, dict):
            raise LabError("native capture metadata must be an object")
        if "session_jsonl" in payloads:
            if "session" not in payloads or native.get("status") != "CAPTURED" or native.get("compression") not in {"none", "zstd"}:
                raise LabError("native capture is missing its original artifact or format")
            summary, roles = split_session(payloads["session_jsonl"], Path(run["fixture_dir"]), receipt["task_sha256"],
                parser_version=3 if receipt["schema_version"] == 6 else (2 if receipt["schema_version"] >= 4 else 1),
                native_format_version=native_format)
            if native.get("parser") != summary or native.get("recognized") is not summary["recognized"]:
                raise LabError("native session interpretation differs from the receipt")
            native_ready = native.get("status") == "CAPTURED" and summary["recognized"] and summary["completed"]
    return receipt, run, payloads, roles, native_ready


def finalize_trial(output_dir: Path, status: str) -> dict:
    output = output_dir.expanduser().absolute()
    receipt, run, payloads, roles, native_ready = _read_capture(output)
    if status == "completed" and receipt["schema_version"] >= 2 and not native_ready:
        raise LabError("missing, ambiguous, unsupported or unfinished native session cannot be completed")
    host = receipt["host"]
    if status == "completed" and (
        host["exit_code"] != 0 or host["timed_out"] or host["start_failed"]
        or host["output_limit_exceeded"] or not receipt["composition_unchanged"]
        or not payloads["stdout"].strip()
    ):
        raise LabError("failed, empty, changed or incomplete capture cannot be declared completed")
    if host["timed_out"] and status != "timed-out":
        raise LabError("a timed-out capture must be recorded as timed-out")
    exit_code = host["exit_code"]
    if not isinstance(exit_code, int) or not 0 <= exit_code <= 255:
        exit_code = None
    evidence = output / "evidence"
    record_host_result(evidence, status, exit_code)
    for name in ("stdout", "stderr"):
        scan_bytes(evidence, payloads[name], "host-output", source_kind="dsh-headless-" + name)
    if roles is not None and receipt["native_session"].get("recognized"):
        scan_bytes(evidence, roles["tool"], "tool-output", source_kind="dsh-native-tool-result")
        scan_bytes(evidence, roles["assistant"], "model-output", source_kind="dsh-native-assistant-content")
        if receipt["schema_version"] >= 4:
            scan_bytes(evidence, roles["attempt"], "host-output", source_kind="dsh-native-assistant-attempt-stream")
    elif "session_jsonl" in payloads:
        scan_bytes(evidence, payloads["session_jsonl"], "host-output", source_kind="dsh-native-unrecognized-session")
    return build_report(evidence)


LEGACY_NATIVE_CHANNELS = sorted([
    {"stage": "host-output", "source_kind": "dsh-headless-stdout"},
    {"stage": "host-output", "source_kind": "dsh-headless-stderr"},
    {"stage": "tool-output", "source_kind": "dsh-native-tool-result"},
    {"stage": "model-output", "source_kind": "dsh-native-assistant-content"},
], key=lambda item: (item["stage"], item["source_kind"]))
NATIVE_CHANNELS = sorted([
    *LEGACY_NATIVE_CHANNELS,
    {"stage": "host-output", "source_kind": "dsh-native-assistant-attempt-stream"},
], key=lambda item: (item["stage"], item["source_kind"]))


def compare_trials(baseline_dir: Path, guarded_dir: Path) -> dict:
    result = compare_runs(baseline_dir / "evidence", guarded_dir / "evidence")
    valid = True
    contracts = []
    for label, directory, state in (("baseline", baseline_dir, "OFF"), ("guarded", guarded_dir, "ON")):
        try:
            receipt, run, _, _, ready = _read_capture(directory)
            report = result[label]
            if (
                receipt["schema_version"] not in {3, 4, 5, 6} or not ready
                or receipt["preflight"]["guard_state"] != state
                or not receipt["composition_unchanged"]
                or run["case_id"] != "injection-probe"
                or report["scanned_channels"] != (NATIVE_CHANNELS if receipt["schema_version"] >= 4 else LEGACY_NATIVE_CHANNELS)
                or receipt["host"]["exit_code"] != 0
                or any(receipt["host"][key] for key in ("timed_out", "start_failed", "output_limit_exceeded"))
            ):
                raise LabError("trial is outside the paired native-capture scope")
            contracts.append((receipt["schema_version"],
                receipt["preflight"].get("guard_profile", "deletion-v1"),
                receipt["preflight"].get("configuration_scope", "dsh-lab-non-guard-v1")))
        except (LabError, OSError, ValueError, KeyError, TypeError):
            valid = False
            result["precondition_issues"].append(label.upper() + "_DSH_CAPTURE_INVALID")
    result["matching_dimensions"]["dsh_native_capture_verified"] = valid
    matching_contract = len(contracts) == 2 and contracts[0] == contracts[1]
    result["matching_dimensions"]["dsh_capture_contract"] = matching_contract
    if not matching_contract:
        result["precondition_issues"].append("DSH_CAPTURE_CONTRACT_MISMATCH")
    if not valid or not matching_contract:
        result.update({"comparison_outcome": "INCOMPARABLE", "result": "INCONCLUSIVE",
            "evidence_level": "UNESTABLISHED", "reason": "COMPARISON_PRECONDITIONS_FAILED"})
    result["precondition_issues"] = sorted(set(result["precondition_issues"]))
    return result


def prepare_pair(
    output_dir: Path, source_home: Path, executable: str, model_label: str,
    trial_group: str, task_file: Path, clean_task_file: Path,
    patches: Sequence[Path] = (), timeout: int = 120,
    attack_sample: str = "direct-v1", node_executable: str = "node",
    *, guard_profile="deletion-v1", dsh_root=None,
) -> dict:
    if not TRIAL_LABEL_PATTERN.fullmatch(model_label) or not TRIAL_GROUP_PATTERN.fullmatch(trial_group):
        raise LabError("use a public model label and trial-group slug")
    if attack_sample not in INJECTION_SAMPLES or not 1 <= timeout <= 600:
        raise LabError("select a known attack sample and bounded timeout")
    if ((attack_sample == "read-redaction-v1") != (guard_profile == "read-redaction-v1")):
        raise LabError("read redaction profile and attack sample must be selected together")
    # Validate profile paths before copying any private bootstrap.
    guard_patch(guard_profile, output_dir.expanduser().absolute() / "trials/guarded/fixture", dsh_root)
    tasks = {}
    for label, path in (("attack", task_file), ("clean", clean_task_file)):
        payload = _read_regular(path.expanduser().absolute(), MAX_TASK_BYTES)
        text = payload.decode("utf-8")
        if not text.strip() or "\x00" in text:
            raise LabError("tasks must be nonempty UTF-8 without NUL bytes")
        tasks[label] = {"path": str(path.expanduser().absolute()), "sha256": _digest(payload)}
    layers = [{"path": str(path.expanduser().absolute()),
        "sha256": _digest(_read_regular(path.expanduser().absolute(), MAX_RECEIPT_BYTES))} for path in patches]
    output = output_dir.expanduser().absolute()
    if output.exists() or output.is_symlink() or not output.parent.is_dir():
        raise LabError("pair directory must be new with an existing parent")
    if source_home.expanduser().is_symlink():
        raise LabError("source DSH home must not be a symlink")
    source = source_home.expanduser().resolve()
    output = output.resolve()
    if output == source or output in source.parents or source in output.parents:
        raise LabError("pair directory and source home must be disjoint")
    # Check external inputs before creating any private profiles.
    executable = shutil.which(executable)
    node_executable = shutil.which(node_executable)
    if not executable or not node_executable:
        raise LabError("DSH and decoder executables must be available")
    output.mkdir(mode=0o700)
    profiles = prepare_homes(source, output / "homes")
    (output / "trials").mkdir(mode=0o700)
    patch = output / "guard.patch.yml"
    guard_patches = {}
    if guard_profile == "deletion-v1":
        _write_new(patch, guard_patch())
    else:
        for member in ("clean-on", "guarded"):
            selected = output / (member + "-guard.patch.yml")
            _write_new(selected, guard_patch(guard_profile, output / "trials" / member / "fixture", dsh_root))
            guard_patches[member] = {"path": str(selected), "sha256": _digest(_read_regular(selected, MAX_RECEIPT_BYTES))}
    preflights = {}
    initializations = {}
    for member in MEMBERS:
        state = "on" if member in {"clean-on", "guarded"} else "off"
        initializations[member] = _initialize_home(executable, output / "homes" / member, patches, node_executable)
        preflight, _, _ = check_host(executable, output / "homes" / member,
            patches, node_executable, state,
            Path(guard_patches[member]["path"]) if member in guard_patches else (patch if state == "on" else None),
            guard_profile=guard_profile, workspace=output / "trials" / member / "fixture", dsh_root=dsh_root)
        if session_inventory(output / "homes" / member):
            raise LabError("paired homes must have no prior sessions")
        preflights[member] = preflight
    fingerprints = {value["non_guard_configuration_sha256"] for value in preflights.values()}
    if len(fingerprints) != 1:
        raise LabError("paired homes have different non-guard settings")
    for name, expected in profiles["bootstrap_sha256"].items():
        if _digest(_read_regular(source / name, MAX_SCAN_BYTES)) != expected:
            raise LabError("source bootstrap changed while deriving the pair")
    manifest = {
        "schema_version": 2, "native_parser_version": PARSER_VERSION,
        "harness_version": preflights["baseline"]["harness_version"],
        "native_format_version": preflights["baseline"]["native_format_version"],
        "pair_dir": str(output), "members": list(MEMBERS),
        "executable": str(Path(executable).absolute()), "node_executable": str(Path(node_executable).absolute()),
        "model_label": model_label, "trial_group": trial_group, "attack_sample": attack_sample,
        "timeout": timeout, "tasks": tasks, "patches": layers,
        "guard_profile": guard_profile, "read_guard_dsh_root": str(Path(dsh_root).resolve()) if dsh_root else None,
        "configuration_scope": "dsh-lab-non-guard-v2", "initializations": initializations,
        "guard_patches": guard_patches, "guard_assets": preflights["guarded"]["guard_assets"],
        "non_guard_configuration_sha256": fingerprints.pop(),
        "guard_patch_sha256": _digest(guard_patch()), "guard_adapter_sha256": preflights["guarded"]["guard_adapter_sha256"],
        "baseline_patch_sha256": _digest(_read_regular(BASELINE_PATCH, MAX_RECEIPT_BYTES)),
        "bootstrap_assets": {path.name: _digest(_read_regular(path, MAX_SCAN_BYTES))
            for path in (BOOTSTRAP_INIT, BOOTSTRAP_PATCH)},
        "bootstrap": profiles, "completion_source": "user-declared",
    }
    _write_json(output / "pair.json", manifest)
    return {"pair_dir": str(output), "state": "READY", "host_tasks_launched": 0,
        "non_guard_configuration_sha256": manifest["non_guard_configuration_sha256"],
        "members": list(MEMBERS), "source_modified": False}


def pair_next(pair_dir: Path) -> dict:
    output = pair_dir.expanduser().absolute()
    if any(path.is_symlink() for path in (output, output / "homes", output / "trials")):
        raise LabError("pair directories must not be symlinks")
    manifest = json.loads(_read_regular(output / "pair.json", MAX_RECEIPT_BYTES))
    if type(manifest.get("schema_version")) is not int or manifest["schema_version"] not in {1, 2} or manifest.get("pair_dir") != str(output.resolve()) or manifest.get("members") != list(MEMBERS):
        raise LabError("unsupported pair metadata or directory identity")
    parser_version = manifest.get("native_parser_version", 1)
    if type(parser_version) is not int or parser_version not in {1, 2, 3}:
        raise LabError("unsupported paired native parser version")
    native_format = manifest.get("native_format_version", 3)
    host_version = manifest.get("harness_version", "0.1.5-rc.1")
    if (host_version not in HOST_CONTRACTS or type(native_format) is not int
            or HOST_CONTRACTS[host_version][0] != native_format
            or native_format == 4 and parser_version < 2
            or (manifest["schema_version"] == 2) != (parser_version == 3)):
        raise LabError("unsupported paired host/native format contract")
    receipt_version = 6 if parser_version == 3 else (5 if native_format == 4 else (4 if parser_version == 2 else 3))
    expected_channels = NATIVE_CHANNELS if parser_version >= 2 else LEGACY_NATIVE_CHANNELS
    guard_profile = manifest.get("guard_profile", "deletion-v1")
    dsh_root = manifest.get("read_guard_dsh_root")
    assets = [("baseline_patch_sha256", BASELINE_PATCH), ("guard_adapter_sha256", ADAPTER)]
    if guard_profile == "deletion-v1":
        assets.append(("guard_patch_sha256", output / "guard.patch.yml"))
    elif guard_profile == "read-redaction-v1" and manifest["schema_version"] == 2:
        if set(manifest.get("guard_patches", {})) != {"clean-on", "guarded"}:
            raise LabError("read pair requires both bound guard patches")
        for member, item in manifest["guard_patches"].items():
            expected_patch = guard_patch(guard_profile, output / "trials" / member / "fixture", dsh_root)
            if (_read_regular(Path(item["path"]), MAX_RECEIPT_BYTES) != expected_patch
                    or _digest(expected_patch) != item["sha256"]):
                raise LabError("paired read guard patch changed")
    else:
        raise LabError("unsupported paired guard profile")
    if manifest["schema_version"] == 2 and (_guard_assets() != manifest.get("guard_assets")
            or manifest.get("configuration_scope") != "dsh-lab-non-guard-v2"
            or any(_digest(_read_regular(path, MAX_SCAN_BYTES)) != manifest["bootstrap_assets"].get(path.name)
                for path in (BOOTSTRAP_INIT, BOOTSTRAP_PATCH))):
        raise LabError("paired implementation assets changed; prepare a fresh pair")
    for key, path in assets:
        if _digest(_read_regular(path, MAX_SCAN_BYTES)) != manifest[key]:
            raise LabError("paired launch assets changed; no host task was launched")
    for item in [*manifest["tasks"].values(), *manifest["patches"]]:
        if _digest(_read_regular(Path(item["path"]), MAX_TASK_BYTES)) != item["sha256"]:
            raise LabError("paired tasks or overlays changed; no host task was launched")
    for member in MEMBERS:
        directory = output / "trials" / member
        state = "on" if member in {"clean-on", "guarded"} else "off"
        task = manifest["tasks"]["clean" if member.startswith("clean-") else "attack"]
        if directory.exists() or directory.is_symlink():
            receipt, _, _, _, ready = _read_capture(directory)
            if (
                receipt["schema_version"] != receipt_version or receipt["task_sha256"] != task["sha256"]
                or receipt["case_id"] != ("clean" if member.startswith("clean-") else "injection-probe")
                or (not member.startswith("clean-") and receipt.get("attack_sample", {}).get("id") != manifest["attack_sample"])
                or receipt["model_label"] != manifest["model_label"] or receipt["trial_group"] != manifest["trial_group"]
                or receipt["preflight"]["guard_state"] != state.upper()
                or receipt["preflight"]["guard_adapter_sha256"] != manifest["guard_adapter_sha256"]
                or receipt["preflight"]["non_guard_configuration_sha256"] != manifest["non_guard_configuration_sha256"]
                or (manifest["schema_version"] == 2 and (receipt["preflight"].get("guard_profile") != guard_profile
                    or receipt["preflight"].get("guard_assets") != manifest["guard_assets"]))
            ):
                raise LabError("captured member does not belong to this pair")
            report = build_report(directory / "evidence")
            if report["host_execution"]["status"] == "NOT_RECORDED":
                return {"pair_dir": str(output), "state": "USER_REVIEW_REQUIRED", "member": member,
                    "output_dir": str(directory), "host_tasks_launched": 0, "result": "INCONCLUSIVE"}
            if (
                not ready or report["host_execution"]["status"] != "COMPLETED"
                or not receipt["composition_unchanged"] or receipt["host"]["exit_code"] != 0
                or any(receipt["host"][key] for key in ("timed_out", "start_failed", "output_limit_exceeded"))
                or report["observer"]["health"] != "HEALTHY"
                or report["scanned_channels"] != expected_channels
                or (member.startswith("clean-") and report["criterion"]["status"] != "PASS")
                or report["security_outcome"] == "INCONCLUSIVE"
            ):
                return {"pair_dir": str(output), "state": "STOPPED", "member": member,
                    "reason": "CONTROL_OR_TRIAL_NOT_HEALTHY", "host_tasks_launched": 0, "result": "INCONCLUSIVE"}
            if member == "baseline" and report["security_outcome"] != "EXPOSURE_OBSERVED":
                return {"pair_dir": str(output), "state": "STOPPED", "member": member,
                    "reason": "BASELINE_ATTACK_NOT_EFFECTIVE", "host_tasks_launched": 0, "result": "INCONCLUSIVE"}
            continue
        if manifest["schema_version"] != 2:
            raise LabError("prepare a fresh pair for retry-aware coverage; legacy plans do not launch new captures")
        if session_inventory(output / "homes" / member):
            raise LabError("selected paired home has prior sessions; no host task was launched")
        captured = run_trial(directory, output / "homes" / member, manifest["executable"],
            "clean" if member.startswith("clean-") else "injection-probe", Path(task["path"]),
            manifest["model_label"], manifest["trial_group"], [Path(item["path"]) for item in manifest["patches"]],
            manifest["timeout"], "direct-v1" if member.startswith("clean-") else manifest["attack_sample"],
            manifest["node_executable"], state,
            (Path(manifest["guard_patches"][member]["path"]) if guard_profile == "read-redaction-v1"
                else output / "guard.patch.yml") if state == "on" else None,
            manifest["non_guard_configuration_sha256"], guard_profile=guard_profile, dsh_root=dsh_root)
        return {**captured, "pair_dir": str(output), "member": member,
            "state": "USER_REVIEW_REQUIRED", "host_tasks_launched": 1}
    comparison = compare_trials(output / "trials/baseline", output / "trials/guarded")
    return {"pair_dir": str(output), "state": "FINISHED", "host_tasks_launched": 0, **comparison}


def main(arguments: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="check the selected version and composition; no model")
    run = commands.add_parser("run", help="capture one real-model trial; completion remains pending")
    for command in (check, run):
        command.add_argument("--dsh-executable", default="dsh")
        command.add_argument("--dsh-home", type=Path, required=True)
        command.add_argument("--node-executable", default="node")
        command.add_argument("--patch", type=Path, action="append", default=[])
        command.add_argument("--guard-state", choices=("off", "on"), default="off")
        command.add_argument("--guard-patch", type=Path)
        command.add_argument("--guard-profile", choices=GUARD_PROFILES, default="deletion-v1")
        command.add_argument("--read-guard-dsh-root", type=Path)
    check.add_argument("--fixture-dir", type=Path, help="bound fixture path for the read profile")
    run.add_argument("--output-dir", type=Path, required=True)
    run.add_argument("--case", choices=("clean", "injection-probe"), required=True)
    run.add_argument("--attack-sample", choices=sorted(INJECTION_SAMPLES), default="direct-v1")
    run.add_argument("--task-file", type=Path, default=ROOT / "docs" / "lab" / "tasks" / "guard-lab-trial-task.md")
    run.add_argument("--model-label", required=True)
    run.add_argument("--trial-group", required=True)
    run.add_argument("--timeout", type=int, default=120)
    finalize = commands.add_parser("finalize", help="record a reviewed host result and scan CLI/native output channels")
    finalize.add_argument("--output-dir", type=Path, required=True)
    finalize.add_argument("--status", choices=("completed", "failed", "timed-out"), required=True)
    compare = commands.add_parser("compare", help="verify native captures and compare reviewed trials")
    compare.add_argument("--baseline-dir", type=Path, required=True)
    compare.add_argument("--guarded-dir", type=Path, required=True)
    pair = commands.add_parser("prepare-pair", help="derive four fresh matched homes and a private plan; no model")
    pair.add_argument("--source-home", type=Path, required=True)
    pair.add_argument("--output-dir", type=Path, required=True)
    pair.add_argument("--dsh-executable", default="dsh")
    pair.add_argument("--node-executable", default="node")
    pair.add_argument("--patch", type=Path, action="append", default=[])
    pair.add_argument("--model-label", required=True)
    pair.add_argument("--trial-group", required=True)
    pair.add_argument("--task-file", type=Path, default=ROOT / "docs/lab/tasks/guard-lab-trial-task.md")
    pair.add_argument("--clean-task-file", type=Path, default=ROOT / "docs/lab/tasks/guard-lab-clean-task.md")
    pair.add_argument("--attack-sample", choices=sorted(INJECTION_SAMPLES), default="direct-v1")
    pair.add_argument("--timeout", type=int, default=120)
    pair.add_argument("--guard-profile", choices=GUARD_PROFILES, default="deletion-v1")
    pair.add_argument("--read-guard-dsh-root", type=Path)
    next_step = commands.add_parser("pair-next", help="capture at most one next trial after review gates")
    next_step.add_argument("--pair-dir", type=Path, required=True)
    options = parser.parse_args(arguments)
    try:
        if options.command == "check":
            value, _, _ = check_host(options.dsh_executable, options.dsh_home, options.patch, options.node_executable, options.guard_state, options.guard_patch,
                guard_profile=options.guard_profile, workspace=options.fixture_dir, dsh_root=options.read_guard_dsh_root)
            exit_code = 0
        elif options.command == "run":
            value = run_trial(
                options.output_dir, options.dsh_home, options.dsh_executable,
                options.case, options.task_file, options.model_label,
                options.trial_group, options.patch, options.timeout,
                options.attack_sample,
                options.node_executable,
                options.guard_state, options.guard_patch,
                guard_profile=options.guard_profile, dsh_root=options.read_guard_dsh_root,
            )
            exit_code = 0
        elif options.command == "finalize":
            value = finalize_trial(options.output_dir, options.status)
            exit_code = {"COMPLETE": 0, "FAIL": 2}.get(value["result"], 4)
        elif options.command == "prepare-pair":
            value = prepare_pair(options.output_dir, options.source_home, options.dsh_executable,
                options.model_label, options.trial_group, options.task_file, options.clean_task_file,
                options.patch, options.timeout, options.attack_sample, options.node_executable,
                guard_profile=options.guard_profile, dsh_root=options.read_guard_dsh_root)
            exit_code = 0
        elif options.command == "pair-next":
            value = pair_next(options.pair_dir)
            exit_code = {"COMPLETE": 0, "FAIL": 2, "INCONCLUSIVE": 4}.get(value.get("result"), 0)
        else:
            value = compare_trials(options.baseline_dir, options.guarded_dir)
            exit_code = {"COMPLETE": 0, "FAIL": 2}.get(value["result"], 4)
        print(json.dumps(value, sort_keys=True))
        return exit_code
    except LabError as exc:
        print("DSH Lab: " + str(exc), file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print("DSH Lab operation refused or failed; raw host diagnostics are not printed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
