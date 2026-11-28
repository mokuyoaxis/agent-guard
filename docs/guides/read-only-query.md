# Unified read-only queries / 统一只读查询

Before executing cleanup or after an interrupted batch, operators and agents often need a quick read-only picture of recent tasks and remaining session deletion budget (scoped to the current session budget), alongside active quarantine transactions (read from the selected quarantine bucket, which may include other participating sessions)—without pulling unbounded legacy audit logs or scanning source directories. Both `status.py` and the MCP `status` tool provide this bounded overview (`0.3.0+`; absent from `0.2.9`) without modifying files, invoking Git, or traversing payloads.

CLI, JSON, and MCP are the supported query interfaces (no GUI or interactive control panel is included). 在 CLI 文本或显式 `--view overview`（以及 MCP `status(view="overview")`）下，任务与额度按当前工作区及会话预算筛选，近期事务按所选隔离区读取（可能包含同一隔离区下的其他会话，并不授予跨预算执行权限），同时保留原有完整 `--json` 输出以保持兼容。See [maintenance acceptance](../reports/test-report-maintenance-0.3.0.md) for verified POSIX test scope.

## CLI and MCP

Run the selected complete checkout/package from the workspace to inspect,
or retain its trusted AGENT_GUARD_WORKSPACE/quarantine/session environment.
The package path selects the code, not a different target workspace:

```sh
# Text defaults to the metadata overview.
python3 -B /absolute/path/to/agent-guard/skills/delete-guard/scripts/status.py

# The same data for an agent; 1–50 rows per task/transaction list, default 10.
python3 -B /absolute/path/to/agent-guard/skills/delete-guard/scripts/status.py \
  --view overview --limit 10 --json

# Legacy full status/size/GC inspection remains available explicitly.
python3 -B /absolute/path/to/agent-guard/skills/delete-guard/scripts/status.py --view full
```

Existing `--json` without a view retains the full legacy JSON. Explicit
`--tail N` also selects the legacy view unless `--view overview` is requested;
tail applies only to the full view. `--limit` applies only to overview.
`--trash-index` keeps its existing separate location-query contract.
Overview cannot be combined with budget reset; the existing interactive
reset workflow remains separate.

MCP `status` accepts optional read arguments. Versions `0.3.0` and `0.3.1`
have ten default tools; `0.3.2` adds three Exfil tools and `restore_preview`
(fourteen default tools), and `0.3.3+` adds `safe_read` (fifteen default tools,
sixteen with Codex confirmation) plus optional overview-only storage measurement
(`measure_storage`, `max_measure_entries`) while retaining this status contract.
[Tool listing](mcp.md#exfil-guard-tools)

```json
{"name":"status","arguments":{"view":"overview","limit":10}}
{"name":"status","arguments":{"view":"overview","measure_storage":true,"max_measure_entries":50000}}
```

Default MCP status remains the old full result for compatibility, and MCP
`view: "full"` rejects explicit `measure_storage` or `max_measure_entries`
arguments. Prefer the
explicit overview for routine maintenance. A valid partial overview returns
`ok: true, partial: true` and CLI exit 1; complete overview exits 0, invalid
input exits 2. Read success is not deletion permission or a completed task.
Doctor probes the three original state queries plus a benign `exfil_check`
probe in `0.3.2+` with auditing disabled.

## Read model

CLI and MCP share `core.workspace_query.workspace_query` and the independent
`workspace_query/v1` response. The context is one selected workspace,
quarantine and current budget identity. MCP keeps its trusted fixed workspace,
operator-selected startup quarantine (default: workspace-local) and session
binding; callers cannot
override them through status arguments.

| Section | Source and limits |
|---|---|
| Workspace/mode | Existing Core mode reader, including its default/fallback semantics; no capability changes |
| Budget | Existing persistent admitted-byte/entry/rate counters and limits; no debit, reset or lock creation |
| Recent tasks | Private task records filtered to the same workspace/quarantine/budget; only IDs, stored status and counts are projected |
| Transactions | Bounded manifest records passed to the existing Core transaction reconciliation; recorded paths may be checked, never recursively walked |
| Location | Existing trash-location reader for this declared quarantine only; no project-collection search |
| Space/GC | Occupancy and capacity candidates stay unmeasured; metadata provides age-only hints, not an executable GC selection |

Budget/task rows use the current budget identity. Transaction history belongs
to the selected quarantine and can include other participating sessions;
that does not grant cross-budget task execution.

Task listing examines at most 1000 directory entries and charges at most
32 MiB of observed candidate file sizes. Each read is bounded by that file's
observed size and the existing 16 MiB record limit; concurrent growth is
refused. Malformed/unreadable/oversized records retain partial coverage.
Other budget/quarantine records are excluded. Listing does not validate
stored grant tokens: APPROVED is the retained task status, and expiry is
reported separately. Use `cleanup_task_status` for the task's public details
and current approval inspection before execution.

Manifest reading is bounded to 8 MiB, 20000 examined lines and 64 KiB per
line, with a small over-limit detection read. Bad/unsupported records,
incomplete lines, links, nonregular files and exhausted bounds produce
partial coverage. With incomplete manifest coverage, transaction states are
UNKNOWN and age totals remain unknown; an older prefix cannot establish the
latest restore/purge state. Existing restore/GC still read their full input;
they never execute from the overview's bounded subset.

`observed_count` refers to examined metadata, not every possible task or
transaction. `has_more` is true when extra observed rows exist, false when
coverage proves there are no more, or null when unread coverage leaves that
unknown. Counts and ordering are scoped to examined entries. A missing task
store/quarantine is normal and is not created by inspection.

The overview reads no payload contents and performs no payload tree or byte
measurement. Existing Core transaction reconciliation can lstat recorded
copy paths and recover evidence from move intents. Its states remain best
effort: EMPTY is not verified absence, and RESTORABLE does not verify payload
integrity, Git snapshot availability or authorization to restore. Partial
coverage, budget errors and unmeasured space are not converted into zeroes.

In 0.3.1+, `manual_review_items` counts failed relocation
paths without a matching completion record, per transaction and across the
overview's transactions. These entries are excluded from `restorable_items`.
The counts are null when manifest coverage is partial. The terminal overview
marks observed transactions that require review.

Age hints use the existing soft retention period. Missing timestamps remain
unknown rather than being inferred from an ID. Capacity-based eligibility
requires detailed measurement; explicitly request the existing GC preview
when needed. Overview does not claim physically released space or offer GC
execution. See [result measurement units](maintenance-output.md#fixed-execution-summaries).

### On-demand storage measurement

In `0.3.3+`, overview remains strictly zero-traversal by default (`measured:
false`, `logical_bytes: null`), and operators or agents can explicitly opt into
a bounded read-only quarantine scan:

```sh
python3 -B /absolute/path/to/agent-guard/skills/delete-guard/scripts/status.py \
  --view overview --measure-storage --max-measure-entries 50000 --json
```

- **Bounds and parity:** `--max-measure-entries` (MCP `max_measure_entries`)
  defaults to `50,000` (cap `200,000`, depth limit `64`), matching Core
  `admission.py` logical byte and entry accounting across both POSIX
  `O_NOFOLLOW`/`dir_fd`/`fstat` open-time verification and the Windows
  junction-aware fallback. Every attempted entry (including depth-limit probes)
  charges the single global budget without prefetch, and subtrees that change
  concurrently or truncate are rolled back from observed totals.
- **Missing vs. unreadable root:** Only a quarantine root whose `lstat` returns
  `FileNotFoundError` reports complete zero occupancy; permission errors,
  non-directories, and dangling links report partial results
  (`logical_bytes: null`) alongside `observed_logical_bytes`,
  `observed_entries`, and `issues`.
- **Admission ratio and Top-10 transactions:** `admission_ratio` is computed
  only from complete measurements with valid limits. `top_transactions` (up to
  10 unpurged direct entity directories sorted by logical bytes descending,
  entries descending, `txid` ascending) requires both complete measurement and
  actual manifest ownership evidence; an existing quarantine directory without
  verified manifest ownership returns `null` rather than `[]`. The scan never
  writes files, manifests, locks, or audit records.

## Terminal presentation

The plain CLI groups workspace, budget, recent tasks, transactions and
storage/GC hints. Budget bars show logical admitted work; tables retain full
normal task IDs for copying. Narrow terminals use a compact per-row layout.
Long display cells may be clipped; use JSON for exact complete values, never
execute a clipped transaction identifier.

Section colors are automatic only on a terminal when TERM is not dumb and
NO_COLOR is absent. JSON/MCP and redirected output contain no added ANSI
sequences. Unicode paths stay readable and terminal control characters are
escaped. The implementation uses the Python standard library, with no new
production dependency, browser, listener or refresh loop.

The snapshot is read-only and non-atomic. A running task or another process
may change data between section reads. The query does not establish native
host interception. Results include selected workspace/quarantine paths;
raw grant tokens, session identity, manifest bodies, task payload paths and
free-form metadata are not exposed. Public sharing still requires review.
