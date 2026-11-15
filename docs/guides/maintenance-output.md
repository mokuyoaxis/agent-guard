# Maintenance overviews, details and result summaries

These output views are **0.3.0 development changes**, absent from published
0.2.9. Runtime, output/legacy regressions and package checks have passed at
recorded development checkpoints. Three hosts also read task details and
replayed an already completed task; fresh approved first execution in each
host and final release-artifact checks remain separate. See
[acceptance and limits](../reports/test-report-maintenance-0.3.0.md).

The shared Core output layer serves CLI and MCP. It reads existing plans
and public receipts, without rescanning sources or changing plan IDs,
selection, approvals, admission or execution. Operator confirmation still
prints the full selected list and revalidates it before approval/execution.

## Choose the amount of detail

| Entry | Default | Optional views |
|---|---|---|
| Cleanup plan/task text CLI | `summary` | `details`, `full` |
| Legacy workspace execution text | Summary followed by per-row receipts | Explicit `--view summary` omits detailed rows |
| Cleanup `--json` | `full`, retaining existing fields | `summary`, `details` |
| New cleanup MCP tools | `summary` | plan/status: `details` or `full`; execute: `full` |
| Restore/GC text CLI | Summary followed by full original rows | Explicit `--view summary` omits detailed rows |
| Restore/GC `--json` | Full original fields plus `execution_summary` | `--view summary` |
| safe_delete | Existing fields/text plus `execution_summary` or its formatted text | Optional 0.3.0 `--quarantine` / MCP `quarantine: true` for recovery, including regenerable artifacts |

The default MCP set remains ten tools; operator-enabled
[Codex host confirmation](mcp.md#host-confirmation-030-development) adds an
eleventh tool without changing these output views. The original seven MCP operations retain
their default behavior and full CLI results, with additive execution summaries
for safe_delete, restore and GC preview. The development safe_delete entry
adds the optional quarantine choice above. Restore/GC have no general retained
receipt-detail endpoint yet, so default text preserves their full rows.
The legacy full-workspace execute flow likewise preserves its per-row receipts
because it does not create a retained task; saved-plan task execution can
default to summary and provide later ID-based reads.
Capture full output in the original call if you will need it later; do not
rerun a mutation to retrieve details. New cleanup tools pass the requested
view to the same CLI. Both MCP text content and structured content contain
that view, without an additional hidden full plan/receipt payload.

Moving files into quarantine changes the filesystem. If no editor tool was
used, say no direct edits were made; do not summarize a completed quarantine
as no files being changed. Explain the recorded action and its current evidence.

An explicit quarantine-only plan also reports `overview.quarantine_requested`
in summary/detail views and prints the request in text and confirmation.
Its category may remain `regenerable` while its action/selected effect is
`quarantine`. Default plans omit the new flag and keep their IDs. Successful
execution counts quarantined roots from receipts; neither regenerability nor
logical bytes establishes physical disk space released. See
[choice binding](cleanup-plan.md#require-quarantine-030-development).

For example, explicitly save a confirmed-discarded batch and return its
overview, then inspect only uncertain rows of that saved snapshot:

```sh
python3 /absolute/path/to/agent-guard/skills/delete-guard/scripts/cleanup.py \
  --root /absolute/path/to/project --targets-file /absolute/path/to/targets.json \
  --discard-targets --save-plan --json --view summary

python3 /absolute/path/to/agent-guard/skills/delete-guard/scripts/cleanup.py \
  --root /absolute/path/to/project --from-plan '<saved-plan-id>' \
  --json --view details --category uncertain --offset 0 --limit 50
```

Discard declarations require evidence; they do not authorize deletion.
An entirely discarded batch may have no uncertain rows. Protection still
wins, and a detail filter never changes the stored selection.

## Plan overview and detail pages

Compact plan/task responses use `cleanup_view/v1`. A plan overview includes
the plan/saved ID, fixed workspace and quarantine, scope, scan completeness,
requested/selected root counts, candidate-row counts, the four categories,
known logical sizes, unknown-size row counts and selected effects. It also
reports error/warning counts with up to three samples and the next action.

These counts have distinct units. A candidate row can represent a whole
directory; it is not a count of recursive files. Known category sizes follow
the existing planner's file-byte/hardlink accounting and exclude protected
subtrees and directory metadata. Zero known bytes with unknown rows is not
proof of zero size. No category sizes are advertised as physical freed space.

Saved-ID reads do not refresh the source. `read_at` is the view-read time;
`refreshed: false` means the stored snapshot was not checked against current
sources. Unsaved planning remains zero-write: its plan ID alone cannot be
used to fetch details later. Explicitly save the first request for subsequent
ID-based reads, or request the full result in that planning call. The legacy
workspace plan remains unsaved; explicit target plans support saving.

`view: details` accepts these presentation-only controls:

| Control | Scope | Meaning |
|---|---|---|
| `category` | Plan | `regenerable`, `discarded`, `uncertain`, `protected` |
| `state` | Task query | `confirmed`, `unconfirmed`, `not_dispatched` |
| `path` | Plan/task query | Literal relative path or subtree; no glob or filesystem lookup |
| `offset` | Details | Nonnegative row offset; default 0 |
| `limit` | Details | 1–200 rows; default 50 |

CLI controls use `--category`, `--state`, `--path`, `--offset`, `--limit`.
They require details mode and cannot accompany approval/execution. Executing
a task supports summary/full output only; query its ID afterward for details.
For MCP, saved_plan_id permits these read controls but still rejects target,
declaration or selection overrides. For example:

```json
{"name":"cleanup_plan","arguments":{"saved_plan_id":"<saved-plan-id>","view":"details","category":"protected","limit":20}}
```

Pages return `entries` and `page`: total/matched rows, offset, limit, returned
count, `has_more` and `next_offset`. Whole-plan scan completeness and a page's
intentional omission of other rows are separate facts. Summary output has
no row array; it does not mark an otherwise complete scan partial merely
because details were omitted. All views reference the same full plan ID.

Task detail rows are ordered by recorded path and include effect, per-root
receipt state, group status and transaction/decision references. Unknown
outcomes are not inferred from present source existence. Mutable task pages
are separate read-time observations: a running task can change between
requests. Inspect a stopped/completed task for stable cross-page review;
an empty filtered page does not establish task completion.

```json
{"name":"cleanup_task_status","arguments":{"task_id":"<task-id>","view":"details","state":"unconfirmed","limit":50}}
```

`state: unconfirmed` means roots with an execution intent but no completion proof;
`not_dispatched` is separate. The existing task `counts.unconfirmed` includes
both. The execution summary also provides `dispatched_unconfirmed` so the
two subsets are explicit, without redefining the old count. Dispatch here
means a recorded attempt/intent, not proof that a child process started;
launch failure or a crash after intent can also leave these roots unconfirmed.

## Fixed execution summaries

`execution_summary` uses `maintenance_summary/v1`, generated from the actual
CLI/public task receipt. CLI text uses the same projection; agent-written
explanations cannot overwrite it. Full JSON retains the existing result keys
and exit semantics. A successful preview/query is not completed deletion.

The Skill CLIs keep the output stream's encoding and escape characters it
cannot represent. This lets a legacy Windows pipe report a completed restore
without failing while printing its bilingual summary. UTF-8 output retains
its readable text; JSON fields and operation results are unchanged. If a
caller loses a response for any reason, inspect the transaction or task
instead of repeating the filesystem operation.

| Action | Count and evidence |
|---|---|
| `quarantined` | Confirmed moved target records/selected roots; transaction references |
| `regenerable_deleted` | Receipt-confirmed direct deletion; no guard recovery copy is claimed |
| `quarantine_payload_removed` | Existing safe_delete housekeeping, distinct from transaction GC |
| `restored` | Returned restore items; conflicts and error events reported separately |
| `recovery_copies_purged` | Returned GC transaction records; missing targets are skipped |

`counts.unit` distinguishes selected roots, input target records, restore
items and transaction records. A restored Git snapshot can be one restore
item; it is not one file. `error_events` counts diagnostics, not failed file
paths. Counts describe historical receipts rather than live filesystem state.

Task summaries retain their stored status and distinguish confirmed roots,
all roots without completion proof, dispatched-unconfirmed roots and roots
not dispatched. No-op is not counted as a confirmed deletion. Missing usable
receipts preserve UNKNOWN and any counts still proven by other receipts.
Zero confirmed means no confirmation evidence, not proof of zero actual
deletions. When no usable result exists at all, counts remain unknown rather
than fabricated zeroes. Legacy
full-workspace execution also reports its actual receipt counts and roots
not dispatched, without changing its existing execution/exit behavior.

Transaction references contain a count and at most five samples; issue output
contains at most three samples with its total count. Use full output for all
original receipt data. A transaction reference is a recovery/evidence lead,
not proof that the copy still exists: restore/GC or another writer may have
changed it. Task tokens and internal session/config bindings remain private;
`full` is the full **public** task result, not the raw task file or manifest.

Space fields deliberately keep their measurement basis:

- Plan categories report known logical file bytes and unknown-size rows.
- `space.preflight_requested_bytes`, when present, is a prior admission
  preview including its directory/hardlink accounting, not a debit receipt.
- `space.admitted_bytes` remains null because the current operation receipts
  do not establish exact per-operation debit. Session status is separate.
- GC preview's `reported_logical_bytes` is the existing best-effort inventory;
  its completeness is unverified because some read failures can be skipped.
- `space.physical_freed_bytes` remains null; no free-block measurement is
  performed for a summary. Quarantine retains recovery copies, and GC only
  describes removal of its selected copies, not all backups or media.

An exception after partial work can leave no usable final JSON receipt.
Existing CLI errors/exit codes remain; their text and MCP transport failures
preserve UNKNOWN and inspection guidance. Query task/transaction/source
evidence before any new proposal; never automatically retry a mutation to
obtain a prettier summary. No summary authorizes cleanup, force restore,
GC execution, budget reset or identity changes.

The version now uses [unified read-only queries](read-only-query.md) and a
formatted CLI overview, without a panel or persistence format migration.
GUI/web operation panels are deferred independently. Existing CLI/MCP results can include
private paths; full results are not suitable for unreviewed public sharing.
