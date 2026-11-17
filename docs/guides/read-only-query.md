# Unified read-only queries / 统一只读查询

These queries are included in **0.3.0** and absent from 0.2.9. Runtime
checks cover bounded metadata reads, no writes/Git/payload traversal, partial
data, terminal formatting and CLI/MCP use in the recorded POSIX environment.
Other platforms and native hooks are not certified by these checks; see
[maintenance acceptance](../reports/test-report-maintenance-0.3.0.md).
The version scope uses CLI/JSON/MCP
queries; GUI, web/TUI applications and interactive operation panels are
deferred independently.

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

The existing MCP `status` tool gains optional read arguments; the default
service still has ten tools. The separately enabled
[Codex confirmation channel](mcp.md#host-confirmation-030-development) adds
an eleventh tool and keeps this status contract:

```json
{"name":"status","arguments":{"view":"overview","limit":10}}
```

Default MCP status remains the old full result for compatibility. Prefer the
explicit overview for routine maintenance. A valid partial overview returns
`ok: true, partial: true` and CLI exit 1; complete overview exits 0, invalid
input exits 2. Read success is not deletion permission or a completed task.
Doctor continues its original three state queries; it does not certify the
new overview path merely by discovering the tool.

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

Age hints use the existing soft retention period. Missing timestamps remain
unknown rather than being inferred from an ID. Capacity-based eligibility
requires detailed measurement; explicitly request the existing GC preview
when needed. Overview does not claim physically released space or offer GC
execution. See [result measurement units](maintenance-output.md#fixed-execution-summaries).

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
