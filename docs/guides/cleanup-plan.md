# Cleanup Copilot / 工作区清理计划

Cleanup Copilot is introduced in **0.2.7** and is absent from 0.2.6. Run the
Python script from the selected package or reviewed checkout; the package
also exposes `agent-guard-cleanup`. The shared
[deletion admission rules](deletion-limits.md) also apply when executing it.
See the [0.2.7 notes](../releases/release-notes-0.2.7.md) for upgrade effects,
versioned validation and exact-version availability checks. Source/package
metadata alone does not establish publication.

Cleanup Copilot scans workspace metadata and proposes concrete cleanup
targets. The default command writes no files, audit records, quarantine
layout or Git exclude rules. It also suppresses project bytecode creation.
Planning does not call `safe_delete --dry-run`, whose metadata side effects
remain documented in the [policy reference](../../skills/delete-guard/references/policy.md#assessment-and-dry-run-side-effects).

Version 0.3.0 adds [overviews, detail pages and execution summaries](maintenance-output.md).
Text planning defaults to an overview; `--json` retains full output unless
`--view summary|details` is explicitly selected. Saved reads accept display
filters without changing the selected targets. Confirmation still prints
the entire selection. These additions are absent from published 0.2.9;
runtime checks and bounded host observations are recorded in the
[maintenance acceptance](../reports/test-report-maintenance-0.3.0.md).

<a id="require-quarantine-030-development"></a>

## Require quarantine (0.3.0)

For authorized cleanup that must remain recoverable, add `--quarantine`
while creating the plan. Proven regenerable artifacts keep their category,
but their proposed action becomes `quarantine` instead of permanent `delete`:

```sh
python3 /absolute/path/to/agent-guard/skills/delete-guard/scripts/cleanup.py \
  --root /absolute/path/to/project --target .pytest_cache --quarantine --save-plan --json
```

The direct CLI also supports `safe_delete.py --quarantine PATH`, including
`--dry-run`. MCP accepts `quarantine: true` on `safe_delete` or a new
`cleanup_plan`; published 0.2.9 lacks these options. Omission/false keeps the
existing policy. The flag requests stricter recovery handling, not discard
evidence or execution permission. Unknown content still needs review;
protection, RESTRICTED mode and all admission limits still apply.

An explicit true choice is stored in the plan options and bound into its ID,
selection and approved effects. Saved reads and approval inherit it; the agent
executes only the returned task ID, without another mode argument. Do not
append `--quarantine` to `--from-plan`/`--task`, or supply it with MCP saved-ID
reads or execution. Create a new proposal to change a choice. This addition
preserves fingerprints and policy for proposals without the option; no stored
record is migrated or silently converted. Earlier reason-text changes retain
their separately documented compatibility limits below.

Dry-run next-step commands retain the flag. If quarantine cannot accept the
request, execution stops without falling back to direct deletion or automatic
GC. Recovery copies still occupy disk space. Application-managed caches need
their own consistency review; this option adds no native cache locking.
Restore remains transaction-level, including when the transaction contains a
whole cache directory. Keep an independently authorized single-file recovery
test in its own transaction when that is the required scope.

<a id="saved-explicit-target-plans-030-development"></a>

## Saved explicit-target plans (0.3.0)

This checkout adds literal-target planning and saved proposals. Published
0.2.9 does not include these flags. Save/read/refresh, source-change refusal
and execution have local runtime evidence; host-specific gaps remain in the
[acceptance table](../reports/test-report-maintenance-0.3.0.md).

For a short list, repeat `--target PATH`. For a large batch, provide a JSON
array such as `["sessions/old-1.jsonl", "sessions/old-directory"]`:

```sh
# Scan only the listed roots; do not save or delete anything.
python3 /absolute/path/to/agent-guard/skills/delete-guard/scripts/cleanup.py \
  --root /absolute/path/to/project --targets-file /absolute/path/to/targets.json --json

# If this entire batch is confirmed discarded, declare that once and save.
python3 /absolute/path/to/agent-guard/skills/delete-guard/scripts/cleanup.py \
  --root /absolute/path/to/project --targets-file /absolute/path/to/targets.json \
  --discard-targets --save-plan --json

# View the stored proposal without rescanning.
python3 /absolute/path/to/agent-guard/skills/delete-guard/scripts/cleanup.py \
  --root /absolute/path/to/project --from-plan '<saved-plan-id>' --json

# After review, the operator types APPROVE in a terminal; sources stay put.
python3 /absolute/path/to/agent-guard/skills/delete-guard/scripts/cleanup.py \
  --root /absolute/path/to/project --from-plan '<saved-plan-id>' --approve --json

# The agent uses the returned task ID, without another terminal prompt.
python3 /absolute/path/to/agent-guard/skills/delete-guard/scripts/cleanup.py \
  --root /absolute/path/to/project --task '<task-id>' --execute --json

# Read the last durable status; this does not rescan or retry.
python3 /absolute/path/to/agent-guard/skills/delete-guard/scripts/cleanup.py \
  --root /absolute/path/to/project --task '<task-id>' --json
```

`--target` and `--targets-file` are alternatives. Paths are literal names,
relative to the fixed workspace or absolute inside it; no glob expansion
occurs. Exact duplicates normalize to one root, overlapping roots are refused,
and directory-link parents are refused. The original workspace boundary is
retained. Only the listed roots and their subtrees are scanned; tracked Git
facts are also requested for those literal roots. No unrelated workspace
directory metadata is included in this plan's fingerprint.

Literal observation does not make every filename executable: the existing
safe_delete path API still refuses some glob-metacharacter names, including
`[`. Preserve the refusal and report it rather than switching deletion routes.

A target list does not declare content discarded. Without discard or proven
regenerability, ordinary content remains uncertain. `--discard-targets` is an
explicit caller declaration for all supplied roots, not an inference or an
approval; use individual `--discard PATH` declarations for a mixed batch.
Protection always wins. The four categories and scan limits remain in force.

If the task already confirms every listed target discarded, include
`--discard-targets` in the initial saved proposal (MCP: `discard_targets=true`).
For a mixed batch, declare only the confirmed obsolete paths. An uncertain
row now distinguishes a Git-tracked path/descendant, no tracked content found,
and unavailable Git evidence, then explains the missing cleanup evidence.
Being untracked does not establish obsolescence; a Git warning does not prove
the absence of tracked content.

`--save-plan` writes a proposal and selection under
`.agent-guard/cleanup-plans/<saved-plan-id>.json`. Plan files are created with
owner-only permissions, an existing record is verified and reused without
replacement, and a new store creates its own `.gitignore` when absent.
Existing ignore rules and explicitly tracked files retain Git's normal
semantics. The store belongs to the already-protected configuration subtree;
it adds no quarantine, debit or approval. Inputs and saved JSON have a 16 MiB
read/write bound. The store may not use directory links.

The 0.3.0 reason-text refinement can change a plan fingerprint:
reasons are part of the reviewed plan. Older snapshots remain readable, but
an affected proposal stops at the existing refresh check. Review and save a
new proposal, then obtain a new operator approval when required. Do not edit
stored JSON or reuse an earlier approval to suppress that check. This adds no
storage-format migration and does not rewrite completed recovery records.

By default the proposed selection contains only selectable **requested roots**
from a complete plan. The tool does not silently substitute descendants when
a requested parent cannot be selected. To propose a specific subset, supply
`--select PATH` while saving. Incomplete scans or no eligible roots can be
saved with an empty selection for review, but no execution command is offered.
The selection and snapshot are bound by the saved ID; it is a consistency
digest, not a signature or human approval.

`--from-plan` accepts that saved ID in the currently selected workspace. Its
view returns stored data with `refreshed: false`; exit 0 means the record was
read, not that it is current or complete. Generation still exits 1 for an
incomplete scan, 2 for refusal. To change targets, options or selection,
create a new proposal; do not combine `--from-plan` with override flags.

Approval reloads the stored request and rescans **only its targets** before
confirmation and once again afterward; task execution checks it again.
Source metadata, classification, Git
facts, configuration, mode, quarantine or session changes invalidate the
reviewed plan. Keep the same workspace/quarantine/session settings. The
input targets JSON is not reread to change a saved request.

Saving or editing a file does not authorize deletion. All actual operations
still go through safe_delete and existing limits. The convenience command
`--from-plan ID --execute` retains the operator's `EXECUTE` prompt, then
creates and executes the same kind of task. The original full-workspace
`--execute --plan-id ID --select PATH` workflow retains its per-row behavior.

<a id="operator-approved-tasks-030-development"></a>

## Operator-approved tasks (0.3.0)

`--from-plan ID --approve` requires both stdin and stderr to be terminals.
The operator reviews the selection and types `APPROVE`. The command creates
approval/control metadata and returns a `task_id`; it does not move or delete
source files or debit session counters. An agent must never allocate a PTY,
answer confirmation, or call internal approval functions for the operator.
Terminal confirmation is a cooperative workflow, not human authentication.

The approval lasts **600 seconds** and binds the saved selection, workspace,
quarantine, session, configuration, source metadata and planned effects.
Keep `AGENT_GUARD_SESSION` and `AGENT_GUARD_TRASH` settings consistent between
operator and agent. The private grant token is not returned in status or
commands. Task files live under `.agent-guard/cleanup-tasks/<task-id>.json`
with owner-only creation permissions and a local ignore file. They include
paths and receipts; treat them as private operational data. There is no new
configuration/recovery migration or automatic history cleanup.

For an MCP service using the development `--trash-root` option, the returned
approval/query/execution argv retains that location through POSIX `env`
arguments. Run the complete instruction and keep the trusted session setting;
using another bucket rejects the saved plan/task instead of rebinding it.
Dry-run and GC guidance also retain custom storage. GC execution remains an
explicit operator action. See [MCP storage selection](mcp.md#select-a-fixed-quarantine-directory-030-development).

`--task ID --execute` atomically claims an approved task once. It checks the
whole request's current budget, then groups targets by planned effect:
quarantine first, regenerable direct deletion second, at most two safe_delete
calls. A task consumes one rate/repeat event on its first actual admission;
bytes and entries are debited for each admitted group. Failure and restore
do not refund them. Approval cannot lift protection, budgets or capacity,
change quarantine into direct deletion, or authorize quarantine housekeeping.
Preflight is an observation, not a reservation; later checks can still stop
a partially completed task.

| Task status | Meaning |
|---|---|
| `APPROVED` | No attempt claimed yet; `can_execute` also checks expiry, configuration and grant availability |
| `RUNNING` | Durable attempt intent exists; it does not prove a process is still alive |
| `COMPLETED` | Execution receipts confirm every selected root |
| `PARTIAL` | Some roots are confirmed, then execution stopped |
| `STOPPED` | No root is confirmed by receipts; consult group errors before assuming sources are unchanged |
| `UNKNOWN` | A timeout or unusable response prevents establishing the full outcome |

Status is read-only: no inventory, lock creation, debit or retry. A successful
query exits 0 even for an incomplete task; read `status`, `counts`, group
receipts and `may_have_changed`. Counts describe selected roots, not recursive
entries or freed bytes. A no-op is not counted as a confirmed deletion.
Each group lists its unconfirmed roots. `counts.not_dispatched` covers only
groups never sent to safe_delete; unconfirmed roots within a dispatched
group need receipt/transaction inspection and are not declared untouched.
`can_execute` does not verify source freshness, budgets, rate or capacity.
Querying an expired task does
not change its stored status.

Execution intent is persisted before each child call and receipts afterward.
Relocation manifests carry `cleanup_task` for correlation if a response is
lost. A repeated execute on a claimed task only returns its recorded status;
it never resumes, even after a crash. Inspect transactions and source state
first, then propose remaining targets for new review and approval. A task-state
write failure reports the task ID and uncertainty; do not blindly rerun it.

If a task stops because the saved plan changed and cleanup is still needed,
preview the same targets again and save a fresh proposal. Have the operator
review and approve the new proposal, then execute using its new `task_id`.

These CLI additions are absent from published 0.2.9. Local acceptance includes
one real operator-approved 71-root task; mixed effects and failure cases also
have unit regression evidence. DSH and Kimi also first executed their own
newly approved batches; Codex's new attempt hit a host usage limit and Claude
model acceptance is deferred. The [MCP task tools](mcp.md#reviewed-maintenance-tasks-030-development)
reuse this workflow. The optional [Codex host confirmation](mcp.md#host-confirmation-030-development)
exports the whole saved list for review and requests a default-negative form;
only an explicit host reply can issue the same Core task. Repeated requests
reuse durable state, while terminal approval remains available. No agent-issued approval,
atomic filesystem snapshot or independent same-UID security boundary is added.

<a id="directory-overview-and-measurement-unreleased"></a>

## Directory overview and measurement (0.2.9)

These modes are introduced in **0.2.9**, not included in the published 0.2.8
package. They share the stdlib-only Core with two new
[read-only MCP tools](mcp.md#cleanup-observations-029). The existing
default plan and explicit execution workflow below keep their interfaces.
This addition remains part of Delete-guard's Cleanup Copilot.

Start with a bounded overview, then inspect a specific unexpanded directory
or measure explicit scopes, always keeping the original workspace root:

```sh
python3 /absolute/path/to/agent-guard/skills/delete-guard/scripts/cleanup.py \
  --root /absolute/path/to/project --discover --json
python3 /absolute/path/to/agent-guard/skills/delete-guard/scripts/cleanup.py \
  --root /absolute/path/to/project --discover --scope .work --json
python3 /absolute/path/to/agent-guard/skills/delete-guard/scripts/cleanup.py \
  --root /absolute/path/to/project --measure --scope build --scope .pytest_cache --json
```

Scopes are literal, non-overlapping directories below that fixed root;
directory-link components, protected scopes, outside paths and the workspace
root itself are refused. Without scopes, discovery starts at the root.
Measurement requires scopes. Quote spaces; use `--scope=-name` for a leading
dash. A scoped discovery starts a new bounded observation rather than a
cursor or a claim that previous results now cover the entire workspace.

| Mode | Default limits | Output |
|---|---|---|
| `--discover` | Depth 4 per selected root, 10000 entries total, 256 expanded directories, 512 entries per directory | Compact overview; JSON includes all observed groups, review branches and unexpanded paths; all sizes unknown |
| `--measure` | Depth 32 per scope, 20000 entries shared across scopes | Logical file/link sizes with inode deduplication, protected roots, issues and completeness per scope |

`--max-entries` and `--max-depth` override their mode's positive limits in the
CLI. The MCP tools only allow lowering the stated defaults. These new modes
have been exercised on Linux ARM64/PRoot; their no-follow cache-marker opening
uses POSIX facilities and has no Windows acceptance evidence.

Discovery recognizes protected boundaries before runtime markers, cache
markers and known artifact patterns. A regular `pyvenv.cfg` takes precedence
over `CACHEDIR.TAG`; it identifies an environment hint, not an unused runtime.
A valid cache signature and the name `uv` provide an ownership hint only.
Managed caches stay grouped without suggesting their internal build fragments.
Cache/artifact path hints affect queue order, not permission or proof of
obsolescence. No age or filename heuristic declares content discarded.

JSON uses independent `cleanup_group_discovery/v1` and
`cleanup_group_measurement/v1` schemas with an `observation_id`, **no
executable `plan_id`**. Every observation has `execution_supported: false`
and every group has `selectable: false`. Discovery's `frontier_complete`
only describes its bounded frontier; `workspace_scan_complete` stays false.
Inspect `coverage.unexpanded`, limits and issues before choosing a scope.

Measurement never follows links. Protected subtrees and external/protected
link boundaries are omitted; `size_bytes: null` means unknown, not zero.
`observed_bytes` counts the observed subset and does not predict freed disk
space. `observed_subset_complete` may be true while `size_complete` and
`interior_checked` are false because protection prevents a whole-size claim.
The metadata digest is not a content hash or atomic snapshot. Scans read
metadata and at most 43 bytes per examined cache tag, plus existing Guard
configuration and Git facts; they do not parse database or cache payloads.

For complete ordinary artifact scopes, `core_policy` may show a shared Core
projection. Approval remains `NOT_GRANTED`, budget `NOT_CHECKED`, and the
projection does not grant execution. Application-managed caches remain
subject to their own consistency and locking protocol.

Exit 0 means the requested frontier or observed measurement subset completed;
1 retains partial observations after limits, I/O errors or observed source
changes; 2 means invalid options or refusal. A protected unknown whole size
can therefore coexist with measurement exit 0. None authorizes deletion.
`--scope` requires these new modes; they reject `--discard`, `--protect`,
`--execute`, `--plan-id` and `--select`. To delete, produce a separate reviewed
plan using the existing workflow; an observation cannot be promoted into one.

## Review a plan

From the intended workspace, run:

```sh
python3 /absolute/path/to/agent-guard/skills/delete-guard/scripts/cleanup.py
python3 /absolute/path/to/agent-guard/skills/delete-guard/scripts/cleanup.py --json
```

`--root /absolute/path/to/workspace` explicitly chooses the workspace
boundary. Otherwise the command uses the existing workspace discovery and
`AGENT_GUARD_WORKSPACE` setting. Existing trash, session mode and artifact
settings and shared deletion configuration are respected. Git supplies ignore/tracking facts; without usable
Git facts no target is proposed as regenerable.

| Category | Evidence | Default handling |
|---|---|---|
| 可再生 / `regenerable` | Git-ignored, matches an existing artifact pattern, and no tracked descendants | Selectable if existing policy permits; inspect `effect` |
| 明确废弃 / `discarded` | Caller explicitly declared the path/subtree with `--discard PATH` | Selectable if existing policy permits; ordinary content goes to quarantine |
| 不确定 / `uncertain` | No sufficient regeneration or discard evidence, or incomplete scan | Review only |
| 受保护 / `protected` | `.git`, `.internal`, quarantine names/boundaries, explicit protected paths or special entries | Keep; do not traverse |

Age, being untracked, or being ignored alone never means discarded.
Declare known disposable paths and additional evidence/backup roots explicitly:

```sh
python3 /absolute/path/to/agent-guard/skills/delete-guard/scripts/cleanup.py \
  --discard old-output --protect backups --protect evidence
```

These flags are invocation options, not persisted cleanup rules. Shared
`.agent-guard/delete-policy.json` protection is also applied, and its directory
is always protected. Discard
declarations must exist and stay within the workspace; the workspace root
cannot be declared discarded. Protection overrides discard declarations and
artifact names. Protected descendants prevent selection of their ancestor;
eligible siblings may remain visible as individual entries. Existing Core
protection can prune an entire parent containing a recovery bucket.

Declared protection covers both the supplied path and its physical location
inside the workspace. A newly discovered nested protected symlink whose
physical location was not declared makes the plan incomplete with
`PROTECTED_ALIAS_REQUIRES_EXPLICIT_PROTECT`. Repeat the plan with
`--protect` naming that link before selecting other targets.

Unknown subtrees collapse into review rows unless generated, discarded or
protected descendants need to be shown. Candidate directories and their
children are not offered as overlapping selections.

The planner reuses ordinary-file metadata and its batched Git facts within
one scan. Directory and link boundaries still receive full checks. This does
not make a partial scan complete or change the scan limits. Execution still
rescans the plan and reads current policy and Git facts.

A regenerable classification does not verify an application's cache protocol.
For example, [uv documents its cache consistency and locking requirements](https://docs.astral.sh/uv/concepts/cache/#cache-safety)
and warns against directly changing cache contents. Review such managed
caches separately; the planner does not coordinate their application locks
or automatically invoke native cache-cleaning commands.

## Confirm and execute

Copy the plan's full `plan_id`, select exact rows marked `selectable`, and
repeat the same root, discard, protect and scan-limit options:

```sh
python3 /absolute/path/to/agent-guard/skills/delete-guard/scripts/cleanup.py \
  --discard old-output --protect backups --protect evidence \
  --execute --plan-id '<plan-id>' --select build --select old-output
```

The example assumes `build` and `old-output` actually appear as selectable
entries in your reviewed plan. Paths passed to `--select` are literal,
workspace-relative paths, not patterns. Quote spaces and use `--select=-name`
for a leading dash.

The command checks the current plan against the supplied ID, prints the
selected paths, sizes and effects, then asks the operator to type
`EXECUTE` in an interactive terminal. Any other answer or EOF
cancels without creating guard metadata. Piped/noninteractive execution is
refused; there is no `--yes` flag. Agents may propose a plan and explain it;
they must leave the confirmation response to the human operator. No new
native host tool or host approval integration is supplied.

After confirmation, the command rescans and checks the plan again before
the first mutation. The ID covers workspace, options, metadata/membership,
policy projections, mode, artifact settings and deletion configuration.
Changed plans require fresh review. It is not a content hash or an atomic filesystem snapshot; concurrent
changes during execution retain the existing Core's TOCTOU limits. Terminal
acknowledgement is a workflow check, not proof of human identity against a
same-UID process. See the [threat model](../design/threat-model.md).

The subsequent deletion hardening creates a ten-minute approval only after
this confirmation and rescan. It binds exact selected paths and their measured
metadata to the current session and deletion configuration; each path is
consumed once before execution. Changed, expired or replayed approval fails.
Approval satisfies the batch-review gate but does not bypass capacity,
session budget, rate/repeat rules or protected paths. Ordinary large deletes
through `check --enforce` or `safe_delete` BLOCK with Cleanup guidance rather
than entering a new host ASK flow. This gate covers automated deletion paths;
existing compound ASK, Git snapshots and quarantine housekeeping retain their
separate rules. See [deletion limits](deletion-limits.md).

Execution invokes existing `safe_delete` once per concrete target:

- `effect: delete`: current policy will physically delete the target. This
  commonly applies to ignored build outputs. There is no recovery copy.
- `effect: quarantine`: current policy will relocate content and return a
  transaction ID. Data remains on disk. Use the existing restore or explicit
  GC workflow in the [feedback guide](recovery-feedback.md).
- `effect: blocked`, `keep` or `review`: not selectable. Existing mode and
  boundary refusals cannot be overridden by confirmation.

An explicit discard declaration does not force a particular deletion
method; the actual `effect` and `policy_code` come from existing policy.
Separate invocations preserve each row's effect when a selection mixes
generated and ordinary targets. On a child failure, execution stops and
keeps earlier and partial transaction reports. Inspect those IDs before
retrying. No automatic purge or rollback runs.

## Sizes, limits and output

This section describes plan JSON and the confirmation workflow. The
[read-only observation modes](#directory-overview-and-measurement-unreleased)
have their own schemas and limits.

Sizes are logical file bytes, with hardlinks counted once per entry and per
category summary. Directory metadata is omitted; links use their own size
and directory links are not traversed. Protected subtree sizes are unknown.
Hardlinks shared across categories may appear in both category totals.
These values do not predict freed blocks, sparse-file allocation or storage
released by quarantine. Errors retain partial observations.

Execution admission measures directory metadata too and conservatively counts
hardlinks per filesystem entry. Its budget/capacity figures can therefore be
larger than these displayed plan sizes. A selectable plan is not a reservation
of capacity or session budget.

The default budget is 10,000 entries and depth 32 below the workspace.
`--max-entries` must be positive; `--max-depth` accepts 1–128. A reached limit,
unreadable subtree or observed directory mutation makes `complete: false`
and prevents execution. Increase the scope limits only after reviewing the
reported partial scan. Protected exclusions are part of the declared scan,
not incomplete traversal errors.

JSON uses `schema_version: 1`, a stable `plan_id`, `summary`, `entries`, scan
`errors`/`warnings`, options and workspace/trash context. Entry fields include
`path`, `category`, `size_bytes`, `size_complete`, `reason`, `selectable`,
`effect`, `policy_code` and an opaque metadata fingerprint. Paths remain
exact local paths and can be private; review output before sharing it.
Planning itself persists none of this output. Executing retains existing
safe_delete audit and recovery contracts.

When saving a plan, write it outside the scanned workspace or into a
protected subtree. Creating or updating an ordinary output file inside the
scan changes the workspace metadata and can invalidate the saved plan ID.

Exit 0 means a complete plan, cancellation, or completed execution. Exit 1
means an incomplete default scan. Exit 2 means invalid options, refusal or
stopped execution; inspect `reports` for partial effects.

Names unsupported by the existing concrete path API, including glob
characters, substitution syntax or trailing whitespace, remain blocked as
individual selections. Ordinary spaces and leading dashes are supported.
No rm wildcard rule, recovery format, GC policy or production dependency
changes. Adding the Core module and package command changes doctor runtime
fingerprints, so older baselines can become `DRIFTED`; review and validate
before writing a new baseline at a new path.

## 中文摘要

0.2.9 新增 `--discover` 目录概况、`--measure --scope PATH` 指定范围计量，
并提供对应的 `cleanup_discover`／`cleanup_measure` MCP 工具；已发布 0.2.8
不含这些能力。按范围继续发现仍保持原工作区，未展开部分和大小未知必须保留。
这些观察不生成可执行计划或删除许可，MCP 的 `ok: true, partial: true` 也不代表
完整扫描。执行仍需另外生成并审阅下面的既有计划。

本功能自 0.2.7 加入。默认扫描只输出计划，不修改工作区；“明确废弃”仅来自
调用者显式声明，旧文件／未跟踪文件不会自动变为垃圾。执行必须提供计划 ID、选定
具体条目并由操作者在终端确认，确认后复查，再逐项走现有 safe_delete。普通文件
隔离后仍占空间，可再生产物符合现有规则时直接物理删除。保护项、不完整或过期的
计划不能执行；不提供自动清理或新的宿主原生工具。

## Validation scope

The following historical checkpoints used 0.2.6 package metadata. They do not
certify the versioned 0.2.7 artifact; its separate validation is tracked
in the [0.2.7 notes](../releases/release-notes-0.2.7.md).

On 2026-10-28, the subsequent shared configuration, admission limits and
operation-bound approvals passed three independent full runs on one frozen
public runtime: 1090 Python cases and both Node groups per run, in 1061.017,
1025.315 and 1042.145 seconds. There were no failures, errors, skips, timeouts
or automatic retries, and frozen source bytes remained unchanged. The
existing diagnostic collector preserved `npm test` scope and deadlines;
the npm command itself was not invoked.

The 149-file offline package passed installed CLI, real terminal confirmation,
mixed physical deletion/quarantine/restore, budget/reset, loop, capacity,
protection and compound-batch checks. Three real Kimi Code 2.3.0 root-Bash
scenarios passed: recoverable deletion, same-session budget refusal and
configured protection. Nine configured-route requests returned HTTP 200;
the loopback gateway's server-side model identity was not independently
authenticated. See [deletion limits](deletion-limits.md) for scope and exceptions.
Acceptance receipts were then updated in documentation without changing runtime
or tests. The following results retain the earlier Cleanup-only checkpoint.

On 2026-10-28, the Cleanup-only public runtime passed one continuous full-suite
run: 1012 Python cases and both Node groups in 979.997 seconds, within the
original 1800-second budget. There were no failures, errors, skips or
retries. The local environment was ARM64/PRoot POSIX with Python 3.13.5
and Node 22.23.4.

This run used the existing diagnostic unittest result collector and then
both Node commands, preserving the test scope, assertions and deadlines
from `npm test`. It did not invoke the npm command itself. An earlier
literal `npm test` run timed out after 653 logged completions; follow-up
checks covered the remaining cases. That timeout is retained, its cause
remains unresolved, and it did not recur during the continuous rerun.

Focused checks passed all 37 cleanup cases and 9 package-manifest cases.
An offline package installation verified 146 file contents, all 11 CLI
entry points, actual pseudo-terminal confirmation, physical artifact
removal, ordinary-file quarantine/restore and noninteractive refusal.
Planning also passed a write audit; cancelled/stale plans and protected
symlink paths have regression coverage. That earlier Cleanup-only checkpoint
made no external model calls.

Workspace-alias checks ran locally. Native macOS, native Windows, shared
Android storage and new host approval integrations were not exercised.
The existing release matrix and historical host reports retain their own
version/scope boundaries.
