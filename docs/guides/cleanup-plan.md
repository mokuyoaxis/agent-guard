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
