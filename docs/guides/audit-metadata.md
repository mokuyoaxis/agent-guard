# Audit metadata / 审计元信息

**Status: included in 0.2.5-rc2.** The published RC1
does not include this shared projection. Its restore/safe_delete writer
minimization remains documented in the [RC notes](../releases/release-notes-0.2.5-rc1.md).
See the [RC2 scope and evidence](../releases/release-notes-0.2.5-rc2.md).

`core.audit.append()` now persists supported metadata only and returns the
exact stored object. `tail()` applies the same projection to legacy objects
in memory. Neither function rewrites earlier log bytes or recovery manifests.
Callers must not use this log as storage for arbitrary payloads or custom
fields: unsupported names, free text and malformed values are omitted.

## Supported evidence

| Metadata | Stored/read representation |
|---|---|
| Events, tools, decisions, reason codes, phases, outcomes | Reviewed labels from the current implementation; arbitrary strings and nested values are omitted |
| Timestamp | UTC `YYYY-MM-DDTHH:MM:SSZ` shape; a missing/invalid append timestamp gets the current time |
| Counts and flags | Nonnegative integers and actual booleans; latency also permits finite nonnegative floats |
| `txid`, `check_id`, `plan_id`, ID lists | Generated `YYYYMMDD-HHMMSS-xxxxxxxx` IDs remain exact; other nonempty spellings use `sha256:<digest>` correlation |
| `session` | Deterministic `sha256:<digest>` of a supplied identity; no raw username/hostname fallback |
| `compensations` | Strategy/status labels, transaction correlation, counts and a validated Git SHA (or `null`); nested payloads are omitted |
| `plan_reasons` | Transaction correlation mapped to `age`/`capacity` |
| Optional Exfil records | Built-in rule/channel/family/confidence labels, numeric offsets and length, `span` and `exempted`. `key=<source name>` context becomes `key` |
| Command/error/reasons | Only the existing `<redacted>` command marker, static `compensation failed` diagnostic and empty reasons list can remain |

The full field/label set is in [core/audit.py](../../core/audit.py). Supporting
a new writer requires an explicit schema review; its fields are not retained
merely because they look safe. The projection does not scan or hash arbitrary
payload bodies. It is not a general recursive text sanitizer.

`tail(path, n)` skips corrupt/torn JSON and non-object JSON lines and keeps
the last `n` object records in bounded memory. Non-positive `n` returns an
empty list without reading the file. It still scans the file for positive
limits; unreadable files and invalid UTF-8 are not silently certified clean.

## Opt-in Exfil audit

The development checkout writes egress audit records when the operator adds
`--audit` to `check_span.py`, `sanitize.py`, the export preflight or a Git hook, or sets
`AGENT_GUARD_EXFIL_AUDIT=1` (`true` and `yes` also work). The MCP server uses
its own trusted `--audit-exfil` startup flag. It ignores the ambient flag and
never accepts it in tool arguments. [MCP Exfil tools](mcp.md#exfil-guard-tools)

The writer appends to `audit.jsonl` in the selected quarantine. It creates
only the parent directory and log and refuses a linked or nonregular final
file. Each decision has an `exfil-allow`, `exfil-sanitize`, `exfil-ask` or
`exfil-block` summary. Each `exfil-span` detail uses the same generated
`check_id`. At most 64 spans are logged, with the remainder counted as
`omitted_span_count`. `size_bytes` is present for a fully read input and is
omitted on early cutoff. The log contains neither payload bytes, target paths,
nor hashes of matched values. Git `pre-commit` records blocked entries and one
overall summary. The workspace `status --json` counts decision summaries.

The export preflight writes `tool: "exfil_export"` and uses `target_count`
for the files whose text was scanned. Its byte total includes binary
probes. Bash adapters use `tool: "exfil_bash_hook"` for evaluated commands
when the operator enables `AGENT_GUARD_EXFIL_AUDIT`. The DSH fast path
skips both the worker and its audit. File names and command text stay
outside these records.

An `audit` receipt reports whether the write was enabled and recorded. A
write failure produces `AUDIT_WRITE_FAILED` and leaves the current decision
and exit code in effect. A recorded decision describes a scan or rewrite.
Transmission remains outside its scope.

## Correlation and visible status

A raw `AGENT_GUARD_SESSION` still selects the existing authorization state.
Only audit/display metadata changes. The same supplied session correlates
across CLI processes; without one, correlation is stable within a process
and differs between processes. Explicit record sessions take precedence.
Already-projected tags remain stable when read or re-appended.

`find_by_txid(path, original_id)` projects the lookup ID too, so legacy
noncanonical IDs remain findable in old and new records. A `sha256:` tag is
not a restore argument: obtain the original ID from the recovery manifest
or exact CLI result. Generated transaction IDs remain directly usable.

Normal `status` still provides a small recent-event summary. Its decision
statistics use projected reason codes; legacy free text under an allowed
field name is no longer copied into counts or text output. `mode_set_by`
uses opaque correlation and invalid display timestamps become `null`.
The state file, mode decisions and session selection are unchanged.

GC intent records keep known transaction correlation and `target_count`.
Unknown selected IDs are recorded in `missing_txid_sha256`; receipts add
`missing_count`, keep known missing IDs in `missing`, and retain `purged`.
GC reason maps cover selected transactions only. CLI missing-ID diagnostics
and results remain exact. Preflight and durable intent still precede purge.

## Limits / 边界

这批维护只收口共享审计存取、GC writer 和普通 status 的非恢复元信息。
旧日志文件中的原始内容仍在；直接打开或转发文件不会经过投影。
manifest、准确恢复路径、CLI 目标／错误、隔离区位置查询和授权状态不是
本投影的脱敏对象，仍可能含敏感文字。普通工具输出／流没有因此获得统一保护。

摘要用于关联，不是加密、鉴权或秘密存储：低熵标识可被猜测，也可能跨记录关联。
可信调用方仍须保证 ID、计数等事实正确；字段白名单不抵抗篡改或刻意编码。
本次没有控制面签名、配置迁移、新依赖或新的拒绝策略。

Audit locking, flush/fsync and existing required-versus-best-effort writer
boundaries remain in place. An I/O failure can leave a partial or complete
line even when append raises; it never authorizes a required operation by
itself. Supported recovery is verified separately from audit projection.
