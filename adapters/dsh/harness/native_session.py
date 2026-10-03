"""Bounded capture of fresh DSH v3/v4 single-turn sessions, without replay."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil

from adapters.harness_support import capture_process, digest, read_regular, write_new
from core.lab import LabError, MAX_SCAN_BYTES
from adapters.dsh.harness.retry_stream import (
    MAX_ATTEMPTS, MAX_STREAM_CHUNKS, failure_supported, finite_number,
    inspect_stream, json_bytes,
)


DECODER = Path(__file__).with_name("session_decode.mjs")
MAX_RECORDS = 4096
MAX_TREE_ENTRIES = 512
PARSER_VERSION = 3
# Frozen vocabulary from DSH 0.1.5-rc.1's dsh-session known-event-types.
KNOWN_EVENTS = frozenset("""
agent-preset/selected agent/inbox/spliced approval/asked approval/decided
approval/policy assistant/attempt assistant/message command/done command/run
compaction/end compaction/prune compaction/start compaction/summary
deliverables/presented feedback/message-delete feedback/message-put feedback/record
goal/change hook/invoked hook/result llm/retry llm/retry-started model/selection
permission/preset plan/mode request/context request/header sandbox/mode
schedule/change session-log-deepseek/delivery-accepted session/end-seed session/title
session/title-llm-request step/end step/start subagent/catalog subagent/descriptor
subagent/model-selection-policy system/message team/member team/message/delivered
team/message/queued team/task todo/write tool-workflow/agent-end
tool-workflow/agent-start tool-workflow/run-end tool-workflow/run-start tool/call
tool/ptc-dispatch tool/ptc-dispatch-start tool/result turn/end turn/start
user/message web/deepseek-search-llm-request
""".split())
V4_EVENTS = KNOWN_EVENTS | {"developer/message", "image/offload", "workspace/changes"}


def check_decoder(executable: str, cwd: Path, environment: dict) -> tuple[dict, str]:
    selected = shutil.which(executable)
    if not selected:
        raise LabError("native DSH capture requires a Node runtime with Zstandard support")
    selected = str(Path(selected).absolute())
    host, streams = capture_process([selected, str(DECODER), "--probe"], cwd, environment, 10)
    try:
        value = json.loads(streams["stdout"])
    except (ValueError, UnicodeError):
        value = {}
    if (
        host["exit_code"] != 0 or host["timed_out"] or host["output_limit_exceeded"]
        or not isinstance(value, dict) or value.get("zstd_supported") is not True
        or not isinstance(value.get("version"), str)
    ):
        raise LabError("native DSH capture requires a Node runtime with Zstandard support")
    return {"node_version": value["version"], "decoder": "concatenated-zstd-v1"}, selected


def session_inventory(home: Path) -> set[Path]:
    """Inventory names, never contents; refuse symlinks and oversized trees."""
    root = home / "sessions"
    if root.is_symlink():
        raise LabError("native session tree must not contain symlinks")
    if not root.exists():
        return set()
    if not root.is_dir():
        raise LabError("native session root must be a directory")
    files = set()
    count = 0
    def fail(_error):
        raise LabError("native session inventory was unreadable")
    for base, dirs, names in os.walk(root, followlinks=False, onerror=fail):
        if len(Path(base).relative_to(root).parts) > 4:
            raise LabError("native session tree exceeds its depth limit")
        count += len(dirs) + len(names)
        if count > MAX_TREE_ENTRIES:
            raise LabError("native session tree exceeds its entry limit")
        for name in (*dirs, *names):
            item = Path(base) / name
            if item.is_symlink():
                raise LabError("native session tree must not contain symlinks")
        for name in names:
            if name.startswith("session.") and name.endswith((".jsonl", ".jsonl.zstd")):
                files.add(Path(base) / name)
    return files


def _integer(value) -> bool:
    return type(value) is int and 0 <= value <= 2**53 - 1


def _object_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _invalid_constant(_value):
    raise ValueError("non-JSON constant")


def _blocks_supported(blocks, depth=0) -> bool:
    if not isinstance(blocks, list) or depth > 8:
        return False
    for block in blocks:
        if not isinstance(block, dict):
            return False
        kind = block.get("type")
        if not isinstance(kind, str):
            return False
        if kind in {"text", "reasoning"}:
            if not isinstance(block.get("text"), str):
                return False
        elif kind == "tool-call":
            if not all(isinstance(block.get(k), str) and block[k] for k in ("id", "name", "arguments")):
                return False
        elif kind == "tool-result":
            if not isinstance(block.get("toolCallId"), str) or not block["toolCallId"]:
                return False
            if "isError" in block and type(block["isError"]) is not bool:
                return False
            if not _blocks_supported(block.get("content"), depth + 1):
                return False
        else:
            # Attachment bytes and unknown blocks cannot be cleared by text scans.
            return False
    return True


def split_session(payload: bytes, fixture: Path, task_sha256: str,
                  *, parser_version: int = PARSER_VERSION,
                  native_format_version: int = 3) -> tuple[dict, dict[str, bytes]]:
    """Classify model-facing payloads by native event type, not message.role.

    ToolResultMessage.role is 'user' in v3. Tool-private meta and user/system
    input are preserved in the private raw file, excluded from output scans.
    This reads append-only, unseeded, one-turn fixtures; it does not reconstruct
    arbitrary forks, replacements, attachments or model-visible context.
    Version 2 also scans failed-attempt streams and verifies pinned retry chains.
    Version 1/2 keep the frozen interpretations used by earlier receipts.
    Version 3 additionally accepts a v4 flat tool message whose source event
    reference binds it to the corresponding earlier call, without replay.
    """
    if type(parser_version) is not int or parser_version not in {1, 2, 3}:
        raise LabError("unsupported native parser version")
    if (type(native_format_version) is not int or native_format_version not in {3, 4}
            or native_format_version == 4 and parser_version < 2):
        raise LabError("unsupported native format/parser contract")
    channels = {"tool": bytearray(), "assistant": bytearray()}
    if parser_version >= 2:
        channels["attempt"] = bytearray()
    counts = {"tool": 0, "assistant": 0, "tool_calls": 0}
    summary = {
        "format_version": native_format_version, "recognized": False, "completed": False,
        "record_count": 0, "counts": counts, "issues": [],
        "cwd_matches": False, "task_matches": False,
        "provenance": "captured-native-event-labels-not-authenticated-authorship",
    }
    if parser_version >= 2:
        summary.update({"parser_version": parser_version, "retry_evidence": {
            "attempts": 0, "scheduled": 0, "started": 0, "stream_chunks": 0,
        }})
    def refuse(issue):
        summary["issues"] = [issue]
        if parser_version >= 2:
            summary["completed"] = False
        return summary, {k: b"" for k in channels}
    if not payload or len(payload) > MAX_SCAN_BYTES or not payload.endswith(b"\n"):
        return refuse("EMPTY_OVERSIZED_OR_UNTERMINATED_SESSION")
    try:
        lines = payload.decode("utf-8").splitlines()
        if not 2 <= len(lines) <= MAX_RECORDS or any(not line.strip() for line in lines):
            return refuse("SESSION_RECORD_LIMIT_OR_EMPTY_LINE")
        records = [json.loads(line, object_pairs_hook=_object_pairs, parse_constant=_invalid_constant) for line in lines]
    except (ValueError, UnicodeError, RecursionError):
        return refuse("INVALID_SESSION_JSON")
    summary["record_count"] = len(records)
    header = records[0]
    if (
        not isinstance(header, dict) or header.get("type") != "session"
        or type(header.get("version")) is not int or header["version"] != native_format_version
        or not isinstance(header.get("id"), str) or not header["id"]
        or not _integer(header.get("createdAt")) or header.get("isSeeded") is not False
        or header.get("parentSession") is not None or header.get("origin") is not None
        or type(header.get("delegationDepth", 0)) is not int or header.get("delegationDepth", 0) != 0
    ):
        return refuse("UNSUPPORTED_SESSION_HEADER")
    if native_format_version == 4 and "seedLength" in header:
        return refuse("UNSUPPORTED_SESSION_HEADER")
    try:
        summary["session_id_sha256"] = digest(header["id"].encode())
    except UnicodeError:
        return refuse("INVALID_SESSION_JSON")
    cwd = header.get("cwd")
    try:
        summary["cwd_matches"] = isinstance(cwd, str) and Path(cwd).is_absolute() and Path(cwd).resolve() == fixture.resolve()
    except (OSError, ValueError):
        return refuse("SESSION_CWD_MISMATCH")
    if not summary["cwd_matches"]:
        return refuse("SESSION_CWD_MISMATCH")
    turn = None
    ended = False
    user_count = 0
    calls = {}
    call_events = {}
    results = set()
    step = None
    closed_steps = set()
    route = None
    pending_retry = None
    retry_chain = None
    step_assistant_seen = False
    unresolved_attempt = False
    request_headers = {}
    for expected_seq, record in enumerate(records[1:]):
        if (
            not isinstance(record, dict) or type(record.get("seq")) is not int
            or record["seq"] != expected_seq or not _integer(record.get("time"))
            or not isinstance(record.get("type"), str) or not isinstance(record.get("data"), dict)
        ):
            return refuse("INVALID_EVENT_ENVELOPE_OR_SEQUENCE")
        kind = record["type"]
        data = record["data"]
        if native_format_version == 4 and "sourceEventSeqs" in record:
            if parser_version < 3 or kind != "tool/result" or record.get("surfaceOp") != "append":
                return refuse("UNSUPPORTED_SURFACE_REPLAY")
        known_events = V4_EVENTS if native_format_version == 4 else KNOWN_EVENTS
        if kind not in known_events and record.get("ignorable") is not True:
            return refuse("UNKNOWN_REQUIRED_EVENT")
        if kind in {"system/message", "user/message", "assistant/message", "tool/result"} or (
                native_format_version == 4 and kind == "developer/message"):
            if record.get("surfaceOp") != "append":
                return refuse("UNSUPPORTED_SURFACE_OPERATION")
        if kind == "turn/start":
            if turn is not None or not _integer(data.get("turn")):
                return refuse("UNSUPPORTED_MULTIPLE_OR_INVALID_TURNS")
            turn = data["turn"]
        elif kind == "step/start":
            if turn is None or ended or not _integer(data.get("turn")) or data.get("turn") != turn or step is not None or not _integer(data.get("step")) or data["step"] in closed_steps:
                return refuse("INVALID_STEP_START")
            step = data["step"]
            pending_retry, retry_chain, step_assistant_seen = None, None, False
        elif kind == "step/end":
            if step is None or not _integer(data.get("turn")) or not _integer(data.get("step")) or data.get("turn") != turn or data.get("step") != step:
                return refuse("INVALID_STEP_END")
            if parser_version >= 2 and pending_retry is not None:
                if pending_retry["state"] != "attempt":
                    return refuse("UNFINISHED_RETRY_CHAIN")
                unresolved_attempt = True
            closed_steps.add(step)
            step = None
        elif kind == "turn/end":
            if turn is None or ended or step is not None or not _integer(data.get("turn")) or data.get("turn") != turn or not isinstance(data.get("reason"), dict) or not isinstance(data["reason"].get("kind"), str):
                return refuse("INVALID_TURN_END")
            ended = True
            summary["completed"] = data["reason"].get("kind") == "completed"
            if parser_version >= 2 and summary["completed"] and unresolved_attempt:
                return refuse("UNRECOVERED_ASSISTANT_ATTEMPT")
        elif parser_version >= 2 and kind == "request/header":
            config = data.get("header", {}).get("config") if isinstance(data.get("header"), dict) else None
            if (turn is None or ended or step is None or not isinstance(config, dict)
                    or not all(isinstance(config.get(k), str) and config[k] for k in ("provider", "model"))):
                return refuse("INVALID_RETRY_REQUEST_HEADER")
            if native_format_version == 4 and "system" in data["header"]:
                return refuse("UNSUPPORTED_REQUEST_HEADER")
            request_headers[expected_seq] = data["header"].get("tools", [])
            selected_route = (config["provider"], config["model"])
            if pending_retry is not None and selected_route != route:
                return refuse("RETRY_ROUTE_CHANGED")
            route = selected_route
        elif native_format_version == 4 and kind == "developer/message":
            message = data.get("message")
            if (turn is None or ended or step is None or not _integer(data.get("turn"))
                    or not _integer(data.get("step")) or data["turn"] != turn
                    or data["step"] != step or not isinstance(message, dict)
                    or message.get("role") != "developer"
                    or not isinstance(message.get("id"), str) or not message["id"]
                    or message.get("source") != {"kind": "tool-registry"}
                    or not isinstance(message.get("content"), list) or not message["content"]):
                return refuse("UNSUPPORTED_DEVELOPER_MESSAGE")
            additions = False
            for block in message["content"]:
                if (not isinstance(block, dict) or set(block) != {"type", "toolName"}
                        or block["type"] not in {"tool-addition", "tool-removal"}
                        or not isinstance(block["toolName"], str) or not block["toolName"]):
                    return refuse("UNSUPPORTED_DEVELOPER_MESSAGE")
                additions |= block["type"] == "tool-addition"
            if additions and (not _integer(data.get("headerSeq")) or data["headerSeq"] not in request_headers):
                return refuse("UNBOUND_TOOL_REGISTRY_UPDATE")
            if additions:
                definitions = request_headers[data["headerSeq"]]
                if (not isinstance(definitions, list) or any(not isinstance(item, dict)
                        or not isinstance(item.get("name"), str) for item in definitions)
                        or any(block["toolName"] not in {item.get("name") for item in definitions}
                               for block in message["content"] if block["type"] == "tool-addition")):
                    return refuse("UNBOUND_TOOL_REGISTRY_UPDATE")
        elif native_format_version == 4 and kind == "image/offload":
            return refuse("UNSUPPORTED_IMAGE_OFFLOAD")
        elif native_format_version == 4 and kind == "workspace/changes":
            if set(data) != {"turn"} or not _integer(data["turn"]) or data["turn"] != turn:
                return refuse("INVALID_WORKSPACE_CHANGE_NOTICE")
        elif parser_version >= 2 and kind in {"llm/retry", "llm/retry-started"}:
            if (turn is None or ended or step is None or not _integer(data.get("turn"))
                    or not _integer(data.get("step")) or data["turn"] != turn or data["step"] != step
                    or pending_retry is None or not isinstance(data.get("retryId"), str)
                    or not data["retryId"] or not _integer(data.get("retry")) or data["retry"] < 1):
                return refuse("RETRY_LIFECYCLE_MISMATCH")
            if kind == "llm/retry":
                expected = {"retryId", "turn", "step", "provider", "mode", "policyKey", "retry", "delayMs", "failure"}
                if data.get("mode") == "normal":
                    expected.add("maxRetries")
                if (set(data) != expected or not isinstance(data.get("mode"), str) or data["mode"] not in {"normal", "always"}
                        or route is None or data["provider"] != route[0]
                        or not isinstance(data.get("policyKey"), str) or not data["policyKey"]
                        or not finite_number(data.get("delayMs")) or not failure_supported(data.get("failure"))
                        or pending_retry["state"] != "attempt"
                        or pending_retry["finish"] not in {"error", "aborted"}
                        or data["failure"] != pending_retry["failure"]
                        or data["mode"] == "normal" and (not _integer(data["maxRetries"]) or data["retry"] > data["maxRetries"])):
                    return refuse("INVALID_RETRY_SCHEDULE")
                chain = (data["retryId"], data["provider"], data["mode"], data["policyKey"], data.get("maxRetries"))
                expected_retry = 1 if retry_chain is None else retry_chain[1] + 1
                if data["retry"] != expected_retry or retry_chain is not None and retry_chain[0] != chain:
                    return refuse("RETRY_CHAIN_ID_OR_SEQUENCE_MISMATCH")
                retry_chain = (chain, data["retry"])
                pending_retry.update({"state": "scheduled", "retryId": data["retryId"], "retry": data["retry"]})
                summary["retry_evidence"]["scheduled"] += 1
            else:
                if (set(data) != {"retryId", "turn", "step", "retry"}
                        or pending_retry["state"] != "scheduled"
                        or (data["retryId"], data["retry"]) != (pending_retry["retryId"], pending_retry["retry"])):
                    return refuse("RETRY_LIFECYCLE_MISMATCH")
                pending_retry["state"] = "started"
                summary["retry_evidence"]["started"] += 1
            try:
                channels["attempt"].extend(json_bytes({"event_type": kind, "data": data}))
            except (ValueError, UnicodeError):
                return refuse("INVALID_SESSION_JSON")
        elif kind in {"tool/call", "tool/result", "assistant/message", "assistant/attempt"}:
            if turn is None or ended or step is None or not _integer(data.get("turn")) or not _integer(data.get("step")) or data.get("turn") != turn or data.get("step") != step:
                return refuse("OUTPUT_OUTSIDE_TURN")
            if kind == "tool/call":
                if parser_version >= 2 and pending_retry is not None:
                    return refuse("RETRY_LIFECYCLE_MISMATCH")
                call = data.get("callId")
                if not isinstance(call, str) or not call or call in calls or not all(isinstance(data.get(k), str) and data[k] for k in ("name", "arguments")):
                    return refuse("INVALID_OR_DUPLICATE_TOOL_CALL")
                calls[call] = data["step"]
                call_events[call] = expected_seq
                counts["tool_calls"] += 1
                continue
            if kind == "assistant/attempt":
                if parser_version == 1:
                    return refuse("UNSUPPORTED_ASSISTANT_ATTEMPT")
                if (set(data) != {"turn", "step", "stream"} or "surfaceOp" in record
                        or "sourceEventSeqs" in record or route is None
                        or step_assistant_seen or any(call_step == step for call_step in calls.values())
                        or pending_retry is not None and pending_retry["state"] != "started"):
                    return refuse("INVALID_ASSISTANT_ATTEMPT")
                retry = summary["retry_evidence"]
                if retry["attempts"] >= MAX_ATTEMPTS:
                    return refuse("ATTEMPT_STREAM_LIMIT_OR_SHAPE")
                try:
                    inspected, scan = inspect_stream(data.get("stream"), MAX_STREAM_CHUNKS - retry["stream_chunks"])
                except (ValueError, TypeError, KeyError, OverflowError, UnicodeError):
                    return refuse("INVALID_OR_UNSUPPORTED_ATTEMPT_STREAM")
                channels["attempt"].extend(scan)
                retry["attempts"] += 1
                retry["stream_chunks"] += inspected["chunks"]
                failure = None
                if inspected["finish_kind"] in {"error", "aborted"}:
                    failure = data["stream"][-1]["chunk"]["reason"]["failure"]
                pending_retry = {"state": "attempt", "finish": inspected["finish_kind"], "failure": failure}
                continue
            message = data.get("message")
            role = "tool" if kind == "tool/result" else "assistant"
            flat_tool = (role == "tool" and parser_version >= 3 and native_format_version == 4
                         and isinstance(message, dict) and message.get("role") == "tool")
            if (
                not isinstance(message, dict) or not isinstance(message.get("id"), str) or not message["id"]
                or message.get("role") != ("tool" if flat_tool else "user" if role == "tool" else "assistant")
                or not isinstance(message.get("source"), dict)
                or message["source"].get("kind") != ("tool" if role == "tool" else "model")
                or not _blocks_supported(message.get("content"))
            ):
                return refuse("INVALID_OR_UNSUPPORTED_OUTPUT_MESSAGE")
            if role == "tool":
                blocks = message["content"]
                call = message["source"].get("callId")
                if (
                    not isinstance(call, str) or call not in calls or call in results
                    or calls[call] != data["step"]
                ):
                    return refuse("TOOL_RESULT_CORRELATION_FAILED")
                if flat_tool:
                    refs = record.get("sourceEventSeqs")
                    if (message.get("toolCallId") != call or type(message.get("isError")) is not bool
                            or message["source"] != {"kind": "tool", "callId": call}
                            or not isinstance(refs, list) or len(refs) != 1
                            or type(refs[0]) is not int or refs[0] != call_events[call]
                            or any(block["type"] not in {"text", "reasoning"} for block in blocks)):
                        return refuse("INVALID_FLAT_TOOL_RESULT_OR_SOURCE")
                    is_error = message["isError"]
                else:
                    if (len(blocks) != 1 or blocks[0]["type"] != "tool-result"
                            or blocks[0]["toolCallId"] != call
                            or parser_version >= 3 and "sourceEventSeqs" in record):
                        return refuse("TOOL_RESULT_CORRELATION_FAILED")
                    is_error = blocks[0].get("isError")
                if "error" in data and (not isinstance(data["error"], dict) or is_error is not True):
                    return refuse("INVALID_TOOL_ERROR_METADATA")
                results.add(call)
            elif not all(isinstance(message["source"].get(k), str) and message["source"][k] for k in ("provider", "model")):
                return refuse("INVALID_ASSISTANT_SOURCE")
            if parser_version >= 2 and role == "assistant":
                if step_assistant_seen:
                    return refuse("DUPLICATE_ASSISTANT_SETTLEMENT")
                if pending_retry is not None:
                    if pending_retry["state"] != "started":
                        return refuse("RETRY_LIFECYCLE_MISMATCH")
                    if (message["source"]["provider"], message["source"]["model"]) != route:
                        return refuse("RETRY_ROUTE_CHANGED")
                    pending_retry = None
                step_assistant_seen = True
            # Only the model-facing content, never data.meta, source, or streams.
            try:
                channels[role].extend((json.dumps(message["content"], ensure_ascii=False, separators=(",", ":")) + "\n").encode())
            except UnicodeError:
                return refuse("INVALID_SESSION_JSON")
            counts[role] += 1
        elif kind == "user/message":
            if not isinstance(data.get("source"), dict):
                return refuse("INVALID_TASK_MESSAGE")
            if data["source"].get("kind") == "user":
                user_count += 1
                content = data.get("content")
                if (
                    turn is None or ended or step is None or data.get("role") != "user"
                    or not isinstance(content, list) or len(content) != 1
                    or not isinstance(content[0], dict) or content[0].get("type") != "text"
                    or not isinstance(content[0].get("text"), str)
                ):
                    return refuse("INVALID_TASK_MESSAGE")
                try:
                    summary["task_matches"] = digest(content[0]["text"].encode()) == task_sha256
                except UnicodeError:
                    return refuse("INVALID_SESSION_JSON")
    if user_count != 1 or not summary["task_matches"]:
        return refuse("SESSION_TASK_MISMATCH")
    if not ended or not counts["assistant"] or set(calls) != results:
        return refuse("INCOMPLETE_SESSION_OR_TOOL_RESULTS")
    if any(len(value) > MAX_SCAN_BYTES for value in channels.values()):
        return refuse("OUTPUT_CHANNEL_LIMIT")
    summary["recognized"] = True
    return summary, {k: bytes(v) for k, v in channels.items()}


def capture_session(home: Path, before: set[Path], capture: Path, fixture: Path,
                    task_sha256: str, node: str, environment: dict,
                    *, parser_version: int = PARSER_VERSION,
                    native_format_version: int = 3) -> tuple[dict, dict]:
    """Copy one new artifact. Failure retains CLI captures and stays inconclusive."""
    value = {"status": "REFUSED", "recognized": False, "issues": []}
    captures = {}
    try:
        fresh = session_inventory(home) - before
        value["new_artifact_count"] = len(fresh)
        if len(fresh) != 1:
            value["issues"] = ["NATIVE_SESSION_MISSING" if not fresh else "NATIVE_SESSION_AMBIGUOUS"]
            return value, captures
        source = next(iter(fresh))
        if type(native_format_version) is not int or native_format_version not in {3, 4}:
            raise LabError("unsupported native capture format")
        stem = "session.v" + str(native_format_version) + ".jsonl"
        if source.name not in {stem, stem + ".zstd"}:
            value["issues"] = ["UNSUPPORTED_NATIVE_LAYOUT"]
            return value, captures
        raw = read_regular(source, MAX_SCAN_BYTES)
        target = capture / "session.bin"
        write_new(target, raw)
        captures["session"] = {"bytes": len(raw), "sha256": digest(raw)}
        compression = "zstd" if source.name.endswith(".zstd") else "none"
        value["compression"] = compression
        if compression == "zstd":
            host, streams = capture_process([node, str(DECODER), str(target)], capture, environment, 15)
            if host["exit_code"] != 0 or host["timed_out"] or host["output_limit_exceeded"]:
                value["issues"] = ["NATIVE_DECODE_FAILED_OR_OVERSIZED"]
                return value, captures
            decoded = streams["stdout"]
        else:
            decoded = raw
        write_new(capture / "session.jsonl", decoded)
        captures["session_jsonl"] = {"bytes": len(decoded), "sha256": digest(decoded)}
        summary, _ = split_session(decoded, fixture, task_sha256, parser_version=parser_version,
                                   native_format_version=native_format_version)
        value.update({"status": "CAPTURED", "recognized": summary["recognized"], "parser": summary})
    except (LabError, OSError, ValueError, RecursionError):
        value["issues"] = ["NATIVE_SESSION_UNREADABLE_OR_OVERSIZED"]
    return value, captures
