"""Native DSH evidence must include later frames and preserve channel boundaries."""

import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from adapters.dsh.harness.native_session import (
    DECODER, MAX_RECORDS, capture_session, session_inventory, split_session,
)
from core.lab import LabError, MAX_SCAN_BYTES
from adapters.dsh.harness.retry_stream import MAX_ATTEMPTS, MAX_STREAM_CHUNKS


TASK = "Review the synthetic project.\n"
TASK_SHA = hashlib.sha256(TASK.encode()).hexdigest()


def rows_for(fixture):
    rows = [{"type": "session", "version": 3, "id": "session-synthetic",
             "createdAt": 1, "cwd": str(fixture), "isSeeded": False}]
    def event(kind, data, **extra):
        rows.append({"type": kind, "seq": len(rows) - 1, "time": 1, "data": data, **extra})
    event("turn/start", {"turn": 1})
    event("step/start", {"turn": 1, "step": 0})
    event("user/message", {"role": "user", "id": "m-user", "source": {"kind": "user"},
          "content": [{"type": "text", "text": TASK}]}, surfaceOp="append")
    event("tool/call", {"turn": 1, "step": 0, "callId": "c1", "name": "read", "arguments": "{}"})
    event("tool/result", {"turn": 1, "step": 0, "message": {"role": "user", "id": "m-tool",
          "source": {"kind": "tool", "callId": "c1"}, "content": [{"type": "tool-result",
          "toolCallId": "c1", "content": [{"type": "text", "text": "TOOL_CONTENT_MARKER"}]}]}}, surfaceOp="append")
    event("assistant/message", {"turn": 1, "step": 0, "message": {"role": "assistant", "id": "m-assistant",
          "source": {"kind": "model", "provider": "synthetic", "model": "synthetic"},
          "content": [{"type": "text", "text": "ASSISTANT_CONTENT_MARKER"}]}, "stream": []}, surfaceOp="append")
    event("step/end", {"turn": 1, "step": 0})
    event("turn/end", {"turn": 1, "reason": {"kind": "completed"}})
    return rows


def serialize(rows):
    return ("\n".join(json.dumps(row) for row in rows) + "\n").encode()


def retry_rows_for(fixture, attempts=1, stream=None):
    original = rows_for(fixture)
    rows = copy.deepcopy(original[:4])
    rows.append({"type": "request/header", "time": 1, "data": {
        "header": {"config": {"provider": "synthetic", "model": "synthetic"}},
        "reason": "initial",
    }})
    failure = {"message": "Synthetic transport failure.", "code": "TRANSPORT"}
    if stream is None:
        stream = [{"type": "chunk", "time": 1, "chunk": {
            "type": "finish", "reason": {"kind": "error", "failure": failure},
        }}]
    else:
        failure = stream[-1]["chunk"]["reason"]["failure"]
    for retry in range(1, attempts + 1):
        rows.extend([
            {"type": "assistant/attempt", "time": 1, "data": {"turn": 1, "step": 0, "stream": copy.deepcopy(stream)}},
            {"type": "llm/retry", "time": 1, "data": {
                "retryId": "synthetic-retry-chain", "turn": 1, "step": 0,
                "provider": "synthetic", "mode": "normal",
                "policyKey": json.dumps(["normal", MAX_ATTEMPTS + 1, ["TRANSPORT"], 1, 2, 0]),
                "retry": retry, "maxRetries": MAX_ATTEMPTS + 1, "delayMs": 1,
                "failure": copy.deepcopy(failure),
            }},
            {"type": "llm/retry-started", "time": 1, "data": {
                "retryId": "synthetic-retry-chain", "turn": 1, "step": 0, "retry": retry,
            }},
        ])
    # A successful settlement precedes the executed calls, unlike the simple
    # legacy fixture above. Failed-attempt tool fragments never execute.
    rows.extend(copy.deepcopy([original[6], original[4], original[5], *original[7:]]))
    for seq, row in enumerate(rows[1:]):
        row["seq"] = seq
    return rows


def runtime_available():
    if not shutil.which("node"):
        return False
    try:
        return subprocess.run(["node", str(DECODER), "--probe"], capture_output=True,
                              timeout=10, check=False).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


class NativeSessionParserTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="dsh-native-parser-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def parse(self, rows, parser_version=2):
        return split_session(serialize(rows), self.root, TASK_SHA, parser_version=parser_version)

    def test_v4_requires_explicit_format_and_preserves_v3_interpretation(self):
        rows = retry_rows_for(self.root)
        rows[0]["version"] = 4
        old, channels = self.parse(rows)
        self.assertEqual(old["issues"], ["UNSUPPORTED_SESSION_HEADER"])
        self.assertTrue(all(not value for value in channels.values()))
        new, channels = split_session(serialize(rows), self.root, TASK_SHA, native_format_version=4)
        self.assertTrue(new["recognized"] and new["completed"])
        self.assertEqual(new["format_version"], 4)
        self.assertEqual(new["retry_evidence"]["attempts"], 1)
        with self.assertRaises(LabError):
            split_session(serialize(rows), self.root, TASK_SHA, parser_version=1, native_format_version=4)

    def test_v4_registry_updates_require_prior_header_and_known_tool_name(self):
        rows = retry_rows_for(self.root)
        rows[0]["version"] = 4
        rows[4]["data"]["header"]["tools"] = [{"name": "read"}]
        rows.insert(5, {"type": "developer/message", "time": 1, "surfaceOp": "append", "data": {
            "turn": 1, "step": 0, "headerSeq": 3, "message": {
                "role": "developer", "id": "registry", "source": {"kind": "tool-registry"},
                "content": [{"type": "tool-addition", "toolName": "read"}],
            },
        }})
        for seq, row in enumerate(rows[1:]): row["seq"] = seq
        parse = lambda value: split_session(serialize(value), self.root, TASK_SHA, native_format_version=4)
        self.assertTrue(parse(rows)[0]["recognized"])
        for mutate in (
            lambda value: value[5]["data"].update(headerSeq=99),
            lambda value: value[5]["data"]["message"]["content"][0].update(toolName="unbound"),
            lambda value: value[5].update(surfaceOp={"op": "replace"}),
            lambda value: value[5]["data"]["message"].update(content=[{"type": "text", "text": "unsupported"}]),
        ):
            changed = copy.deepcopy(rows); mutate(changed)
            summary, channels = parse(changed)
            self.assertFalse(summary["recognized"])
            self.assertTrue(all(not value for value in channels.values()))

    def test_v4_image_offload_and_replayed_surface_are_not_cleared(self):
        for extra in ({"type": "image/offload", "data": {}}, {"type": "workspace/changes", "data": {"turn": 1}, "sourceEventSeqs": [1]}):
            rows = rows_for(self.root); rows[0]["version"] = 4
            rows.append({"seq": len(rows) - 1, "time": 1, **extra})
            summary, channels = split_session(serialize(rows), self.root, TASK_SHA, native_format_version=4)
            self.assertFalse(summary["recognized"])
            self.assertTrue(all(not value for value in channels.values()))

    def test_tool_user_role_is_classified_by_event_and_only_content_is_scanned(self):
        rows = rows_for(self.root)
        rows[5]["data"]["meta"] = {"private": "PRIVATE_META_MARKER"}
        rows[6]["data"]["stream"] = [{"text": "STREAM_ONLY_MARKER"}]
        summary, channels = self.parse(rows)
        self.assertTrue(summary["recognized"])
        self.assertTrue(summary["completed"])
        self.assertTrue(summary["cwd_matches"] and summary["task_matches"])
        self.assertEqual(summary["counts"], {"tool": 1, "assistant": 1, "tool_calls": 1})
        self.assertIn(b"TOOL_CONTENT_MARKER", channels["tool"])
        self.assertNotIn(b"TOOL_CONTENT_MARKER", channels["assistant"])
        self.assertIn(b"ASSISTANT_CONTENT_MARKER", channels["assistant"])
        for marker in (b"PRIVATE_META_MARKER", b"STREAM_ONLY_MARKER", TASK.encode()):
            self.assertNotIn(marker, b"".join(channels.values()))
        self.assertNotIn("CONTENT_MARKER", json.dumps(summary))
        self.assertNotIn(str(self.root), json.dumps(summary))

    def test_task_cwd_seed_and_format_mismatches_are_not_recognized(self):
        for field, value in (("cwd", "/synthetic-other"), ("cwd", "relative"), ("cwd", "\x00"),
                             ("version", 2), ("isSeeded", True), ("parentSession", "prior"),
                             ("origin", "subagent"), ("delegationDepth", 1)):
            with self.subTest(field=field, value=value):
                rows = rows_for(self.root)
                rows[0][field] = value
                summary, channels = self.parse(rows)
                self.assertFalse(summary["recognized"])
                self.assertTrue(all(not payload for payload in channels.values()))
        rows = rows_for(self.root)
        rows[3]["data"]["content"][0]["text"] = "Different task."
        self.assertFalse(self.parse(rows)[0]["recognized"])

    def test_gaps_duplicate_sequences_and_incomplete_lifecycles_are_refused(self):
        variants = []
        original = rows_for(self.root)
        gap = copy.deepcopy(original); gap[5]["seq"] += 1; variants.append(gap)
        duplicate = copy.deepcopy(original); duplicate[5]["seq"] -= 1; variants.append(duplicate)
        variants.append(original[:-1])
        no_result = copy.deepcopy(original); no_result.pop(5)
        for seq, row in enumerate(no_result[1:]): row["seq"] = seq
        variants.append(no_result)
        no_step_end = copy.deepcopy(original); no_step_end.pop(-2)
        for seq, row in enumerate(no_step_end[1:]): row["seq"] = seq
        variants.append(no_step_end)
        for rows in variants:
            self.assertFalse(self.parse(rows)[0]["recognized"])

    def test_tool_source_role_call_id_and_error_must_agree(self):
        mutations = [
            lambda data: data["message"].update(role="assistant"),
            lambda data: data["message"]["source"].update(kind="model"),
            lambda data: data["message"]["source"].update(callId="absent"),
            lambda data: data["message"]["content"][0].update(toolCallId="other"),
            lambda data: data.update(step=1),
            lambda data: data.update(error={"code": "synthetic-error"}),
        ]
        for mutate in mutations:
            rows = rows_for(self.root); mutate(rows[5]["data"])
            self.assertFalse(self.parse(rows)[0]["recognized"])

    def test_unknown_and_attachment_blocks_or_surface_replacements_are_refused(self):
        for block in ({"type": "unknown"}, {"type": "image", "attachment": {}},
                      {"type": "file", "attachment": {}}, {"type": []}, {"type": "text", "text": []}):
            rows = rows_for(self.root)
            rows[5]["data"]["message"]["content"][0]["content"] = [block]
            self.assertFalse(self.parse(rows)[0]["recognized"])
        rows = rows_for(self.root)
        rows[5]["surfaceOp"] = {"op": "replace", "startSeq": 3, "endSeq": 3}
        self.assertFalse(self.parse(rows)[0]["recognized"])

    def test_unknown_required_event_refuses_but_ignorable_metadata_is_preserved(self):
        rows = rows_for(self.root)
        rows.append({"type": "synthetic-extension", "seq": len(rows) - 1, "time": 1, "data": {}})
        self.assertFalse(self.parse(rows)[0]["recognized"])
        rows[-1]["ignorable"] = True
        self.assertTrue(self.parse(rows)[0]["recognized"])

    def test_header_only_bad_json_utf8_surrogates_and_constants_are_refused(self):
        raw = serialize(rows_for(self.root))
        invalid = [raw[:-1], b"\xff\n", b"{}\n", raw + b"\n",
                   raw.replace(b'"version": 3', b'"version": 3, "version": 3', 1),
                   raw.replace(b'"time": 1', b'"time": NaN', 1),
                   raw.replace(b'TOOL_CONTENT_MARKER', b'\\ud800', 1)]
        for payload in invalid:
            summary, _ = split_session(payload, self.root, TASK_SHA)
            self.assertFalse(summary["recognized"])

    def test_record_and_byte_limits_fail_without_partial_channels(self):
        for payload in (b"x" * (MAX_SCAN_BYTES + 1), serialize([{}] * (MAX_RECORDS + 1))):
            summary, channels = split_session(payload, self.root, TASK_SHA)
            self.assertFalse(summary["recognized"])
            self.assertTrue(all(not data for data in channels.values()))

    def test_native_error_turn_is_recognized_but_cannot_be_completed(self):
        rows = rows_for(self.root); rows[-1]["data"]["reason"] = {"kind": "error"}
        summary, _ = self.parse(rows)
        self.assertTrue(summary["recognized"])
        self.assertFalse(summary["completed"])

    def test_legacy_parser_refuses_attempt_and_discards_partial_channels(self):
        # A real Flash trial retained an assistant attempt before its completed
        # turn. Even after useful results, unsupported retry streams must not
        # become a quiet success or leak a partial recognized channel.
        for index in (4, 7):
            with self.subTest(attempt_index=index):
                rows = rows_for(self.root)
                rows.insert(index, {
                    "type": "assistant/attempt", "time": 1,
                    "data": {"turn": 1, "step": 0, "stream": [
                        {"type": "chunk", "time": 1, "chunk": {
                            "type": "finish", "reason": "stop",
                        }},
                    ]},
                })
                for seq, row in enumerate(rows[1:]):
                    row["seq"] = seq
                summary, channels = self.parse(rows, parser_version=1)
                self.assertFalse(summary["recognized"])
                self.assertFalse(summary["completed"])
                self.assertEqual(summary["issues"], ["UNSUPPORTED_ASSISTANT_ATTEMPT"])
                self.assertEqual(channels, {"tool": b"", "assistant": b""})

    def test_retry_success_scans_attempt_and_keeps_surface_channels_separate(self):
        summary, channels = self.parse(retry_rows_for(self.root, attempts=2))
        self.assertTrue(summary["recognized"] and summary["completed"])
        self.assertEqual(summary["parser_version"], 2)
        self.assertEqual(summary["retry_evidence"], {
            "attempts": 2, "scheduled": 2, "started": 2, "stream_chunks": 2,
        })
        self.assertIn(b"Synthetic transport failure.", channels["attempt"])
        self.assertNotIn(b"Synthetic transport failure.", channels["assistant"])
        self.assertIn(b"TOOL_CONTENT_MARKER", channels["tool"])
        self.assertNotIn("Synthetic transport", json.dumps(summary))

    def test_retry_scan_joins_text_reasoning_and_tool_arguments_across_records(self):
        original = retry_rows_for(self.root)[5]["data"]["stream"][-1]
        stream = []
        for index, kind in enumerate(("text-chunks", "reasoning-chunks", "tool-call-chunks")):
            for fragment in ("SPLIT_", "ATTEMPT_", "MARKER"):
                row = {"type": kind, "time0": 1, "index": index, "dt": [],
                       "args" if kind == "tool-call-chunks" else "texts": [fragment]}
                if kind == "tool-call-chunks":
                    row.update(id="unexecuted-call", name="read")
                stream.append(row)
        stream.append(original)
        summary, channels = self.parse(retry_rows_for(self.root, stream=stream))
        self.assertTrue(summary["recognized"] and summary["completed"])
        self.assertEqual(channels["attempt"].count(b"SPLIT_ATTEMPT_MARKER"), 3)
        self.assertNotIn(b"SPLIT_ATTEMPT_MARKER", channels["assistant"])
        self.assertEqual(summary["counts"]["tool_calls"], 1)

    def test_raw_attempt_chunks_and_failure_diagnostics_are_scanned(self):
        failure = {"message": "FAILED_ATTEMPT_DIAGNOSTIC", "code": "TRANSPORT", "requestId": "SYNTHETIC_REQUEST"}
        raw = lambda chunk: {"type": "chunk", "time": 1, "chunk": chunk}
        stream = [raw({"type": "block-start", "index": 0, "blockType": "text"}),
                  raw({"type": "text-delta", "index": 0, "text": "PARTIAL_"}),
                  raw({"type": "text-delta", "index": 0, "text": "OUTPUT"}),
                  raw({"type": "block-end", "index": 0, "block": {"type": "text", "text": "PARTIAL_OUTPUT"}}),
                  raw({"type": "usage", "usage": {"inputTokens": 1, "outputTokens": 2}}),
                  raw({"type": "finish", "reason": {"kind": "error", "failure": failure}})]
        summary, channels = self.parse(retry_rows_for(self.root, stream=stream))
        self.assertTrue(summary["recognized"])
        for marker in (b"PARTIAL_OUTPUT", b"FAILED_ATTEMPT_DIAGNOSTIC", b"SYNTHETIC_REQUEST"):
            self.assertIn(marker, channels["attempt"])
            self.assertNotIn(marker, channels["assistant"])

    def test_retry_ids_sequences_routes_and_missing_boundaries_refuse_all_channels(self):
        mutations = [
            lambda rows: rows[6]["data"].update(retry=2),
            lambda rows: rows[6]["data"].update(provider="other"),
            lambda rows: rows[6]["data"].update(failure={"message": "other", "code": "TRANSPORT"}),
            lambda rows: rows[6]["data"].update(delayMs=-1),
            lambda rows: rows[6]["data"].update(mode=[]),
            lambda rows: rows[7]["data"].update(retryId="other-chain"),
            lambda rows: rows[7]["data"].update(retry=2),
            lambda rows: rows[7]["data"].update(step=1),
            lambda rows: rows[8]["data"]["message"]["source"].update(model="other-model"),
            lambda rows: rows.pop(7),
            lambda rows: rows.pop(6),
            lambda rows: rows.pop(4),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                rows = retry_rows_for(self.root)
                mutate(rows)
                for seq, row in enumerate(rows[1:]): row["seq"] = seq
                summary, channels = self.parse(rows)
                self.assertFalse(summary["recognized"])
                self.assertFalse(summary["completed"])
                self.assertTrue(all(not value for value in channels.values()))

    def test_unknown_malformed_attachment_and_post_finish_attempt_streams_refuse(self):
        mutations = [
            lambda stream: stream.append({"type": "chunk", "time": 1, "chunk": {"type": "text-delta", "index": 0, "text": "late"}}),
            lambda stream: stream[0].update(type="unknown"),
            lambda stream: stream[0].update(time=True),
            lambda stream: stream[0]["chunk"].update(replayState={"private": "PRIVATE_REPLAY"}),
            lambda stream: stream.insert(0, {"type": "text-chunks", "time0": 1, "index": 0, "dt": [], "texts": ["a", "b"]}),
            lambda stream: stream.insert(0, {"type": "text-chunks", "time0": 1, "index": True, "dt": [], "texts": ["a"]}),
            lambda stream: stream.insert(0, {"type": "chunk", "time": 1, "chunk": {"type": "block-start", "index": 0, "blockType": "image"}}),
            lambda stream: stream.insert(0, {"type": "chunk", "time": 1, "chunk": {"type": "block-end", "index": 0, "block": {"type": "file", "attachment": {}}}}),
        ]
        for mutate in mutations:
            rows = retry_rows_for(self.root)
            mutate(rows[5]["data"]["stream"])
            summary, channels = self.parse(rows)
            self.assertFalse(summary["recognized"])
            self.assertEqual(summary["issues"], ["INVALID_OR_UNSUPPORTED_ATTEMPT_STREAM"])
            self.assertTrue(all(not value for value in channels.values()))

    def test_attempt_and_expanded_chunk_limits_refuse_without_partial_channels(self):
        rows = retry_rows_for(self.root, attempts=MAX_ATTEMPTS + 1)
        summary, channels = self.parse(rows)
        self.assertFalse(summary["recognized"])
        self.assertTrue(all(not value for value in channels.values()))
        rows = retry_rows_for(self.root)
        rows[5]["data"]["stream"].insert(0, {"type": "text-chunks", "time0": 1, "index": 0,
            "dt": [0] * (MAX_STREAM_CHUNKS - 1), "texts": [""] * MAX_STREAM_CHUNKS})
        summary, channels = self.parse(rows)
        self.assertFalse(summary["recognized"])
        self.assertTrue(all(not value for value in channels.values()))

    def test_failed_or_aborted_retry_turn_does_not_become_completed(self):
        for reason in ("error", "aborted"):
            rows = retry_rows_for(self.root)
            rows[-1]["data"]["reason"] = {"kind": reason}
            summary, channels = self.parse(rows)
            self.assertTrue(summary["recognized"])
            self.assertFalse(summary["completed"])
            self.assertTrue(channels["attempt"])

    def test_always_retry_mode_uses_same_bounded_evidence_rules(self):
        rows = retry_rows_for(self.root, attempts=2)
        for row in rows:
            if row["type"] == "llm/retry":
                row["data"].update(mode="always", policyKey=json.dumps(["always", 1, 2, 0]))
                del row["data"]["maxRetries"]
        summary, channels = self.parse(rows)
        self.assertTrue(summary["recognized"] and summary["completed"])
        self.assertEqual(summary["retry_evidence"]["attempts"], 2)
        self.assertTrue(channels["attempt"])

    def test_changed_chain_policy_and_post_tool_attempt_are_refused(self):
        mutations = [
            lambda rows: rows[9]["data"].update(maxRetries=MAX_ATTEMPTS + 2),
            lambda rows: rows[9]["data"].update(policyKey="different policy"),
            lambda rows: rows[9]["data"].update(retryId="different chain"),
            lambda rows: rows.insert(5, copy.deepcopy(rows[-4])),
        ]
        for mutate in mutations:
            rows = retry_rows_for(self.root, attempts=2)
            mutate(rows)
            for seq, row in enumerate(rows[1:]): row["seq"] = seq
            summary, channels = self.parse(rows)
            self.assertFalse(summary["recognized"] or summary["completed"])
            self.assertTrue(all(not value for value in channels.values()))

    def test_unrecovered_attempt_cannot_be_hidden_by_a_later_completed_step(self):
        rows = retry_rows_for(self.root)
        # Close the failed step without retry; a later completed step cannot
        # retroactively turn its unconsumed attempt into a recovered retry.
        rows[6:8] = [{"type": "step/end", "time": 1, "data": {"turn": 1, "step": 0}},
                     {"type": "step/start", "time": 1, "data": {"turn": 1, "step": 1}}]
        for row in rows[8:-1]: row["data"]["step"] = 1
        for seq, row in enumerate(rows[1:]): row["seq"] = seq
        summary, channels = self.parse(rows)
        self.assertEqual(summary["issues"], ["UNRECOVERED_ASSISTANT_ATTEMPT"])
        self.assertFalse(summary["recognized"] or summary["completed"])
        self.assertTrue(all(not value for value in channels.values()))

    def test_tool_call_start_and_packed_arguments_are_joined_without_execution(self):
        rows = retry_rows_for(self.root)
        rows[5]["data"]["stream"][:0] = [
            {"type": "chunk", "time": 1, "chunk": {"type": "block-start", "index": 0, "blockType": "tool-call"}},
            {"type": "tool-call-chunks", "time0": 1, "index": 0, "id": "unexecuted", "name": "read", "args": ["SPLIT_"], "dt": []},
            {"type": "chunk", "time": 1, "chunk": {"type": "tool-call-delta", "index": 0, "id": "unexecuted", "argumentsDelta": "ARGS"}},
        ]
        summary, channels = self.parse(rows)
        self.assertTrue(summary["recognized"] and summary["completed"])
        self.assertIn(b"SPLIT_ARGS", channels["attempt"])
        self.assertEqual(summary["counts"]["tool_calls"], 1)

    def test_no_retry_still_declares_empty_attempt_channel_and_legacy_shape_is_frozen(self):
        summary, channels = self.parse(rows_for(self.root))
        self.assertEqual(channels["attempt"], b"")
        self.assertEqual(summary["retry_evidence"]["attempts"], 0)
        old_summary, old_channels = self.parse(rows_for(self.root), parser_version=1)
        self.assertNotIn("parser_version", old_summary)
        self.assertNotIn("retry_evidence", old_summary)
        self.assertEqual(set(old_channels), {"tool", "assistant"})

    @unittest.skipUnless(os.name == "posix", "POSIX session tree")
    def test_inventory_refuses_directory_symlinks_and_oversized_trees(self):
        home = self.root / "home"; home.mkdir()
        (home / "sessions").symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(LabError): session_inventory(home)
        separate = self.root / "separate"; (separate / "sessions").mkdir(parents=True)
        for i in range(513): (separate / "sessions" / str(i)).touch()
        with self.assertRaises(LabError): session_inventory(separate)


@unittest.skipUnless(os.name == "posix" and runtime_available(), "POSIX / Node with Zstandard")
class NativeSessionDecoderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="dsh-native-decoder-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def compress(self, chunks):
        encoded = json.dumps([base64.b64encode(c).decode() for c in chunks]).encode()
        result = subprocess.run(["node", "--input-type=module", "-e",
            'import fs from "node:fs";import {zstdCompressSync,constants} from "node:zlib";const chunks=JSON.parse(fs.readFileSync(0,"utf8"));process.stdout.write(Buffer.concat(chunks.map(c=>zstdCompressSync(Buffer.from(c,"base64"),{params:{[constants.ZSTD_c_checksumFlag]:1,[constants.ZSTD_c_contentSizeFlag]:0}}))));'],
            input=encoded, capture_output=True, timeout=15, check=False)
        self.assertEqual(result.returncode, 0)
        return result.stdout

    def decode(self, raw):
        path = self.root / "source.bin"
        path.write_bytes(raw)
        return subprocess.run(["node", str(DECODER), str(path)], capture_output=True, timeout=15, check=False)

    def test_concatenated_frames_keep_late_tool_results_and_footer(self):
        rows = rows_for(self.root)
        chunks = [serialize(rows[:1]), serialize(rows[1:5]), serialize(rows[5:])]
        result = self.decode(self.compress(chunks))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"".join(chunks))
        summary, roles = split_session(result.stdout, self.root, TASK_SHA)
        self.assertTrue(summary["recognized"] and summary["completed"])
        self.assertIn(b"TOOL_CONTENT_MARKER", roles["tool"])

    def test_corrupt_truncated_or_junk_tail_emits_no_partial_evidence(self):
        first = self.compress([b"PRIVATE_TEST_PREFIX\n"])
        second = self.compress([b"PRIVATE_TEST_TAIL\n"])
        bad_checksum = bytearray(second); bad_checksum[-1] ^= 1
        for raw in (first + second[:-2], first + b"not-zstd", first + second + b"junk", first + bad_checksum):
            result = self.decode(raw)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, b"")
            self.assertNotIn(b"PRIVATE_TEST", result.stderr)

    def test_compressed_input_and_expanded_output_caps(self):
        for raw in (b"x" * (MAX_SCAN_BYTES + 1), self.compress([b"x" * (MAX_SCAN_BYTES + 1)])):
            result = self.decode(raw)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, b"")

    def test_frame_cap_and_symlink_input_are_refused(self):
        frame = self.compress([b"x"])
        self.assertNotEqual(self.decode(frame * (MAX_RECORDS + 1)).returncode, 0)
        link = self.root / "linked.bin"; link.symlink_to(self.root / "source.bin")
        result = subprocess.run(["node", str(DECODER), str(link)], capture_output=True, timeout=15, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")

    def test_compressed_capture_preserves_original_private_bytes(self):
        home = self.root / "home"; home.mkdir()
        before = session_inventory(home)
        path = home / "sessions/project/session-synthetic"; path.mkdir(parents=True)
        raw = self.compress([serialize(rows_for(self.root)[:1]), serialize(rows_for(self.root)[1:])])
        (path / "session.v3.jsonl.zstd").write_bytes(raw)
        capture = self.root / "capture"; capture.mkdir(mode=0o700)
        summary, captures = capture_session(home, before, capture, self.root, TASK_SHA, shutil.which("node"), dict(os.environ))
        self.assertTrue(summary["recognized"])
        self.assertEqual((capture / "session.bin").read_bytes(), raw)
        self.assertEqual(captures["session"]["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual((capture / "session.jsonl").stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
