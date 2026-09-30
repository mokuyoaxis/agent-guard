"""Bounded text-only coverage of pinned DSH compact failed-attempt streams.

This is captured host output, not a reconstructed assistant surface. Scan both
the original records/diagnostics and joined deltas: JSON fragment boundaries
must not hide a marker split across consecutive chunks of the same block.
"""

from __future__ import annotations

import json
import math


MAX_ATTEMPTS = 64
MAX_STREAM_CHUNKS = 4096
MAX_INTEGER = 2**53 - 1


def integer(value, minimum=0):
    return type(value) is int and minimum <= value <= MAX_INTEGER


def finite_number(value):
    return type(value) in (int, float) and 0 <= value <= MAX_INTEGER and math.isfinite(value)


def failure_supported(value):
    return (
        isinstance(value, dict)
        and {"message", "code"} <= set(value)
        and set(value) <= {"message", "code", "status", "providerRetryAfterMs", "requestId"}
        and all(isinstance(value[k], str) and value[k] for k in ("message", "code"))
        and ("status" not in value or integer(value["status"]))
        and ("providerRetryAfterMs" not in value or finite_number(value["providerRetryAfterMs"]))
        and ("requestId" not in value or isinstance(value["requestId"], str) and bool(value["requestId"]))
    )


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode()


def inspect_stream(stream, remaining_chunks):
    """Return fixed metadata and complete raw/joined scan bytes, or refuse."""
    if not isinstance(stream, list) or len(stream) > remaining_chunks:
        raise ValueError("ATTEMPT_STREAM_LIMIT_OR_SHAPE")
    joined = {}
    identities = {}
    closed = set()
    chunks = 0
    finish = None

    def identity(index, kind, call_id=None):
        if not integer(index) or index in closed:
            raise ValueError("INVALID_ATTEMPT_BLOCK")
        selected = (kind, call_id)
        if index in identities and identities[index] != selected:
            raise ValueError("INVALID_ATTEMPT_BLOCK")
        identities[index] = selected
        return joined.setdefault((index, kind, call_id), [])

    for record in stream:
        if finish is not None or not isinstance(record, dict):
            raise ValueError("INVALID_ATTEMPT_STREAM")
        kind = record.get("type")
        if kind in {"text-chunks", "reasoning-chunks", "tool-call-chunks"}:
            expected = {"type", "time0", "index", "dt", "args" if kind == "tool-call-chunks" else "texts"}
            if kind == "tool-call-chunks":
                expected.add("id")
                if "name" in record:
                    expected.add("name")
                if not isinstance(record.get("id"), str) or not record["id"]:
                    raise ValueError("INVALID_ATTEMPT_STREAM")
                if "name" in record and (not isinstance(record["name"], str) or not record["name"]):
                    raise ValueError("INVALID_ATTEMPT_STREAM")
            if set(record) != expected or not integer(record["time0"]):
                raise ValueError("INVALID_ATTEMPT_STREAM")
            members = record["args" if kind == "tool-call-chunks" else "texts"]
            gaps = record["dt"]
            if (not isinstance(members, list) or not members or not all(isinstance(v, str) for v in members)
                    or not isinstance(gaps, list) or len(gaps) != len(members) - 1):
                raise ValueError("INVALID_ATTEMPT_STREAM")
            chunks += len(members)
            if chunks > remaining_chunks:
                raise ValueError("ATTEMPT_STREAM_LIMIT_OR_SHAPE")
            timestamp = record["time0"]
            for gap in gaps:
                if type(gap) is not int or not -MAX_INTEGER <= gap <= MAX_INTEGER:
                    raise ValueError("INVALID_ATTEMPT_STREAM")
                timestamp += gap
                if not integer(timestamp):
                    raise ValueError("INVALID_ATTEMPT_STREAM")
            block_kind = {"text-chunks": "text", "reasoning-chunks": "reasoning", "tool-call-chunks": "tool-call"}[kind]
            if block_kind == "tool-call" and identities.get(record["index"]) == ("tool-call", None):
                identities[record["index"]] = (block_kind, record["id"])
            identity(record["index"], block_kind, record.get("id")).extend(members)
            continue
        if kind != "chunk" or set(record) != {"type", "time", "chunk"} or not integer(record["time"]):
            raise ValueError("INVALID_ATTEMPT_STREAM")
        chunk = record["chunk"]
        chunks += 1
        if chunks > remaining_chunks or not isinstance(chunk, dict):
            raise ValueError("ATTEMPT_STREAM_LIMIT_OR_SHAPE")
        chunk_kind = chunk.get("type")
        if chunk_kind == "block-start":
            if set(chunk) != {"type", "index", "blockType"} or chunk["blockType"] not in {"text", "reasoning", "tool-call"}:
                raise ValueError("UNSUPPORTED_ATTEMPT_CHUNK")
            index = chunk["index"]
            if not integer(index) or index in identities or index in closed:
                raise ValueError("INVALID_ATTEMPT_BLOCK")
            # A Tool-call id becomes known at its first delta, not at start.
            identities[index] = (chunk["blockType"], None)
        elif chunk_kind in {"text-delta", "reasoning-delta", "tool-call-delta"}:
            if chunk_kind == "tool-call-delta":
                expected = {"type", "index", "id", "argumentsDelta"} | ({"name"} if "name" in chunk else set())
                if (not isinstance(chunk.get("id"), str) or not chunk["id"]
                        or "name" in chunk and (not isinstance(chunk["name"], str) or not chunk["name"])):
                    raise ValueError("INVALID_ATTEMPT_STREAM")
                field, block_kind = "argumentsDelta", "tool-call"
            else:
                expected, field, block_kind = {"type", "index", "text"}, "text", chunk_kind.removesuffix("-delta")
            if set(chunk) != expected or not isinstance(chunk[field], str):
                raise ValueError("INVALID_ATTEMPT_STREAM")
            index, call_id = chunk["index"], chunk.get("id")
            if block_kind == "tool-call" and identities.get(index) == ("tool-call", None):
                identities[index] = (block_kind, call_id)
            identity(index, block_kind, call_id).append(chunk[field])
        elif chunk_kind == "block-end":
            block, index = chunk.get("block"), chunk.get("index")
            if set(chunk) != {"type", "index", "block"} or not isinstance(block, dict):
                raise ValueError("INVALID_ATTEMPT_STREAM")
            block_kind = block.get("type")
            if block_kind in {"text", "reasoning"}:
                valid = set(block) == {"type", "text"} and isinstance(block["text"], str)
            elif block_kind == "tool-call":
                valid = set(block) == {"type", "id", "name", "arguments"} and all(isinstance(block[k], str) and block[k] for k in ("id", "name", "arguments"))
            else:
                valid = False
            if not valid or not integer(index) or index in closed:
                raise ValueError("UNSUPPORTED_ATTEMPT_CHUNK")
            selected = (block_kind, block.get("id"))
            if index in identities and identities[index] not in {selected, ("tool-call", None)}:
                raise ValueError("INVALID_ATTEMPT_BLOCK")
            closed.add(index)
        elif chunk_kind == "usage":
            usage = chunk.get("usage")
            if (set(chunk) != {"type", "usage"} or not isinstance(usage, dict)
                    or not {"inputTokens", "outputTokens"} <= set(usage)
                    or not set(usage) <= {"inputTokens", "outputTokens", "totalTokens", "cacheReadTokens", "cacheWriteTokens", "reasoningTokens"}
                    or not all(integer(v) for v in usage.values())):
                raise ValueError("INVALID_ATTEMPT_STREAM")
        elif chunk_kind == "finish":
            reason = chunk.get("reason")
            if set(chunk) != {"type", "reason"} or not isinstance(reason, dict):
                raise ValueError("INVALID_ATTEMPT_STREAM")
            finish = reason.get("kind")
            if finish in {"error", "aborted"}:
                valid = set(reason) == {"kind", "failure"} and failure_supported(reason["failure"])
            else:
                valid = finish in {"stop", "tool-calls", "max-tokens"} and set(reason) == {"kind"}
            if not valid:
                raise ValueError("UNSUPPORTED_ATTEMPT_CHUNK")
        else:
            raise ValueError("UNSUPPORTED_ATTEMPT_CHUNK")
    # A raw Tool-call block start can precede a packed run carrying its id.
    # The raw records retain names, ids, finish failures and complete blocks.
    value = {"stream": stream, "joined_fragments": [
        {"index": index, "kind": kind, "text": "".join(parts)}
        for (index, kind, _call_id), parts in joined.items()
    ]}
    return {"records": len(stream), "chunks": chunks, "finish_kind": finish}, json_bytes(value)
