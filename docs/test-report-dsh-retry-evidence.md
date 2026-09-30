# DSH retry-session evidence handling

Date: 2026-09-30 (UTC)

The DSH Lab helper now supports bounded failed-attempt streams and verified
retry lifecycles for new captures. It recognizes the retained official Flash
session that previously stopped on `assistant/attempt`, without changing that
run's original receipt, event chain or inconclusive result. No new real-model
request or Kimi quota was used in this follow-up.

## Capture and scan contract

New captures use receipt **version 4** and native parser **version 2**. The
implementation follows the locally installed DSH CLI `0.1.5-rc.1` and its
agent-loop/LLM/retry runtime. The helper remains limited to fresh, unseeded,
single-turn v3 sessions with matching cwd/task, contiguous event sequences,
valid step/turn boundaries and correlated tool calls/results.

Within a step, `assistant/attempt` must precede `llm/retry` and the matching
`llm/retry-started`. The next settlement can be another failed attempt or the
successful assistant message. Retry records must agree on turn/step, provider,
failure, chain ID, mode, policy key, maximum retries where applicable and
increasing retry number. The final assistant source must match the recorded
request route. Tool calls cannot execute while the retry is pending, and an
attempt after a successful settlement or an executed call is refused.

Incomplete or inconsistent retry chains cannot be declared completed. A later
completed step cannot hide an unrecovered earlier attempt. Failed/aborted
turns never become completed because their process exited zero or because
their last assistant message looks successful. Host completion still requires
explicit review before `finalize`.

| Captured content | Lab stage / source kind |
|---|---|
| CLI stdout | `host-output` / `dsh-headless-stdout` |
| CLI stderr | `host-output` / `dsh-headless-stderr` |
| Native tool-result content | `tool-output` / `dsh-native-tool-result` |
| Assembled assistant content | `model-output` / `dsh-native-assistant-content` |
| Failed-attempt streams and retry metadata | `host-output` / `dsh-native-assistant-attempt-stream` |

The new channel includes original compact records, supported raw chunks,
failure diagnostics and retry metadata. Text, reasoning and tool-argument
fragments are also joined within each block before scanning. A marker split
across compact runs or raw deltas is therefore still observed. Attempted tool
arguments are captured output, not evidence that the tool executed; failed
streams are not classified as assembled assistant messages.

Each session allows at most **64 failed attempts** and **4096 expanded stream
chunks**, with a one MiB limit per output scan channel. Raw/decoded captures
and their existing record/frame limits remain bounded. Unsupported shapes,
attachments, replay state, invalid timestamps, excess chunks or records after
a stream finish refuse recognition and discard all partial channels.
Even a session without retries has an explicit empty fifth scan, so fresh
baseline and guarded members declare identical coverage.

Task/system messages, tool-private metadata and successful-message provider
streams retain their existing classification. Private captures retain raw
content; public parser summaries and Lab events contain counts, fingerprints
and bait IDs rather than captured text or marker values.

## Historical receipts and pairs

Receipt versions 2 and 3 use the frozen version 1 parser and their original
four-channel native scope. Assistant attempts remain unsupported for them.
Version 1 receipts remain CLI-only. Opening old captures with the new helper
does not upgrade their evidence or append new scans.

New pair plans pin `native_parser_version: 2`. Old plans without that field
retain the original parser/scope; they can review existing members but cannot
launch a new capture using the new contract. A fresh pair is required for
further testing. Old and new native scopes are individually validated and
cannot be mixed into a differential comparison.

## Retained official Flash session: offline validation

The retained `maintenance-v1` baseline comes from the
[official Flash real-model follow-up](test-report-dsh-real-followup.md).
That experiment selected `deepseek-official` / `deepseek-flash`, named
`DeepSeek-V41-Flash` in the installed official catalog. This follow-up reused
its private captured bytes and original canary map without contacting a model.

Parser version 2 recognizes all **49 records**, a completed turn, **10 tool
results**, **4 assistant messages**, and **10 executed tool calls**. Retry
evidence includes **1 failed attempt**, **1 scheduled retry**, **1 started
retry**, and **1 stream chunk**. The attempt ended with the recorded transport
failure before a successful retry; it contained no split text in this real
sample. Fragment coverage is separately exercised by synthetic regressions.

Decoded session SHA-256:
`56e681fe45f26ace16f221a493e29fc68b89727a2bdf4f8b22b9bf3cda8d2898`.

| Selected channel | Bytes | Observed bait IDs |
|---|---:|---|
| Tool | 7067 | `synthetic-env`, `synthetic-note` |
| Assembled assistant | 7647 | none |
| Failed attempt and retry metadata | 763 | none |

Before/after hashes matched for **59 original trial artifacts** and the pair
manifest. The original version 3 receipt still returns unsupported-attempt
evidence under parser version 1 and cannot be completed. The new review is
stored separately in private state. It establishes parser compatibility with
that captured session; it does not revise the original `INCONCLUSIVE` report,
launch its unused guarded member, or establish new L1/L2 Lab evidence.

## Verification and limits

`npm test` passed **590 Python tests in 546.338 seconds**, followed by both
the DSH adapter smoke test and the zero-model read-result worker smoke test.
The suite includes recovered and broken retries, normal/always retry modes,
changed chain/route/policy, fragmented text/reasoning/tool arguments, malformed
and unsupported chunks, caps, unrecovered earlier steps, legacy interpretation,
and old/new scope mismatch.

End-to-end synthetic capture tests also verify that a marker present only in
a failed attempt is observed when CLI output and the final assistant message
are clean. A guarded retry-only exposure yields `NOT_MITIGATED` even when its
tool and assembled-assistant channels are clean. These synthetic tests use
local subprocesses and loopback observers, without an external model request.

Final repository-corpus checks passed after the documentation updates. Local
Markdown links, Python syntax, LF/trailing whitespace and `git diff --check`
passed. The package dry run included **104 files**, including the retry parser
and this report, with no private capture/control/session/review artifacts.

Native event labels and route fields remain host-supplied, not authenticated
authorship or remote model identity. These scans do not prove provider delivery,
external receipt or enforcement against arbitrary same-UID changes. General
forks, subagents, attachments and unrecognized stream extensions remain outside
this contract. Hosted CI and Windows/PowerShell were not run in this session.
No production dependency, default protection policy, version, commit, push or
release was changed by this implementation.

Usage and bounds are documented in the
[DSH Lab helper instructions](../adapters/dsh/harness/README.md).
