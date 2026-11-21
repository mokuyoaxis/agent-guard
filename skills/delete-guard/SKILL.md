---
name: delete-guard
description: >-
  Recoverable deletion discipline for AI agents. Use whenever you need to
  delete, clean, or discard files/directories or run destructive git
  operations (clean, reset --hard, restore, force push) inside a workspace.
  Routes supported operations through quarantine or Git snapshots, and
  explains how to stop safely when compensation or policy blocks you.
  Also use for viewing or changing a workspace's Delete-guard limits.
---

# delete-guard

You operate inside a workspace where irreversible destruction is not a default capability. This skill defines the deletion discipline the guard enforces and how to work within it productively.

## The four pillars

- **Scope** - act inside the workspace; the workspace root, `.git`, and everything outside are never yours to delete.
- **Recoverability** - supported valuable deletions relocate to the selected quarantine with a manifest; supported Git overwrites snapshot tracked state. Provably regenerable, ignored artifacts may be deleted directly (unless quarantine is explicitly requested).
- **Authorization** - a human decides permission boundaries. You can be *downgraded* to `RESTRICTED` mode; you can never promote yourself back.
- **Auditability** - supported writers record decision/recovery metadata in JSONL according to their CLI contract. Mutation intents must be durable; assessment/blocked audit may be best effort, and early no-op paths may not write an audit. Calls outside the covered path are not automatically visible.

## The one rule

**Prefer `safe_delete` over `rm`.** It applies the guard's recovery policy:

```bash
# delete explicit targets; valuable content is quarantined:
python3 /absolute/path/to/agent-guard/skills/delete-guard/scripts/safe_delete.py src/old_module.py
python3 /absolute/path/to/agent-guard/skills/delete-guard/scripts/safe_delete.py 'build/**/*.tmp' --reason "stale build output"

# undo a transaction (list first if unsure):
python3 /absolute/path/to/agent-guard/skills/delete-guard/scripts/restore.py list
python3 /absolute/path/to/agent-guard/skills/delete-guard/scripts/restore.py '<txid>'

# current guard state:
python3 /absolute/path/to/agent-guard/skills/delete-guard/scripts/status.py
```

In `0.3.0+`, text `status.py` defaults to a bounded read-only overview showing current-budget task IDs, transaction metadata, and budget bars (use `--view overview --json` or MCP `status(view="overview")`; default JSON/MCP `status` preserves legacy full output). Overview queries do not measure capacity GC or validate grants, and partial coverage is not absence—query individual task IDs for full details (see [query scope](../../docs/guides/read-only-query.md)).

Globs passed to `safe_delete` are expanded explicitly before anything moves, which is why `safe_delete '*.log'` works while `rm *.log` is blocked: the guard refuses opaque target sets, not cleanup work.

<a id="installation-and-updates-030-development"></a>

## Installation and updates (0.3.0)

Keep this Skill inside a complete Agent Guard installation (`0.2.7+` includes Cleanup proposals and deletion admission; see [0.2.7 release notes](../../docs/releases/release-notes-0.2.7.md)) and register a directory symlink rather than copying `SKILL.md` alone. In `0.3.0+`, preview and link using:

```sh
python3 /absolute/path/to/agent-guard/install_skill.py --target /absolute/path/to/host-skills/delete-guard --json
```

Review the selected package root and target, then add `--execute` within your authorized scope (an older Skill is backed up outside the host's Skill search directory; `0.2.9` and earlier lack this installer, so call scripts directly from the complete package root instead of copying `core/` or patching `PYTHONPATH`). Registering a Skill does not configure hooks or MCP—use the [setup prompt](../../docs/guides/agent-setup-prompt.md) and [installation guide](../../docs/guides/skill-installation.md) for host wiring.

<a id="read-only-cleanup-inventory-unreleased-checkout"></a>

## Read-only cleanup inventory (0.2.9)

From a 0.2.9 installation or checkout, start a cleanup
investigation with a bounded directory overview:

```bash
python3 /absolute/path/to/agent-guard/skills/delete-guard/scripts/cleanup.py --discover --json
python3 /absolute/path/to/agent-guard/skills/delete-guard/scripts/cleanup.py --measure --scope build --json
```

Keep the same workspace root when selecting scopes. Use
`--discover --scope PATH` to inspect an unexpanded directory. Environment and managed-cache
labels are hints; all groups remain nonselectable. Unknown whole sizes stay
null, and a complete observed subset is not a complete workspace scan.
The corresponding MCP tools are `cleanup_discover` and `cleanup_measure`;
`ok: true, partial: true` is usable partial data, never execution permission.

These modes are absent from published 0.2.8. For that package, use its
existing plan workflow below. With the new modes, produce a separate reviewed
plan before any execution; `observation_id` cannot replace `plan_id`. Review
application-managed cache consistency separately. See
[observation limits](../../docs/guides/cleanup-plan.md#directory-overview-and-measurement-unreleased)
and [MCP semantics](../../docs/guides/mcp.md#cleanup-observations-unreleased).

<a id="require-quarantine-030-development"></a>

## Require quarantine (0.3.0)

When authorized cleanup requires recovery, use `safe_delete.py --quarantine`
(also with `--dry-run`) or add `--quarantine` while creating a Cleanup plan.
MCP `safe_delete` and new `cleanup_plan` calls accept `quarantine: true`.
Proven regenerable artifacts keep their category, but the action becomes
quarantine. Defaults are unchanged; published 0.2.9 lacks these options.

The choice is saved and bound to the plan and approval. Follow the returned
operator command, then execute by task ID; do not add overrides to saved
queries, approval or execution. Dry-run follow-up commands retain it.
Protection, RESTRICTED mode, budgets, capacity, rate and batch confirmation
still apply. A refusal never falls back to direct deletion or automatic GC.
Quarantine still occupies storage; this option cannot purge recovery copies,
declare unknown content obsolete, coordinate application cache locks, or
restore an arbitrary file inside a multi-file transaction. See
[quarantine-only plans](../../docs/guides/cleanup-plan.md#require-quarantine-030-development).

<a id="cleanup-proposals-027-candidate"></a>

## Cleanup proposals (0.2.7)

Version 0.3.0 can retain an explicit batch without repeating
its declaration and selection on execution:

```sh
python3 /absolute/path/to/agent-guard/skills/delete-guard/scripts/cleanup.py --targets-file /absolute/path/to/targets.json --discard-targets --save-plan --json
# Operator terminal: review, then type APPROVE (agent must not fill this in).
python3 /absolute/path/to/agent-guard/skills/delete-guard/scripts/cleanup.py --from-plan '<saved-plan-id>' --approve --json
# Agent: attempt the returned task once; query before further work.
python3 /absolute/path/to/agent-guard/skills/delete-guard/scripts/cleanup.py --task '<task-id>' --execute --json
python3 /absolute/path/to/agent-guard/skills/delete-guard/scripts/cleanup.py --task '<task-id>' --json
```

Keep the same workspace, quarantine and session settings. The JSON input is a list of literal
paths; use `--discard-targets` only after confirming the whole batch is
discarded. It is a proposal, not approval. Protection and unknowns remain;
empty selections cannot execute. Saved execution refreshes only the stored
targets. Only the operator approves; the agent can then execute the returned
task ID without a terminal prompt. Approval expires after 600 seconds and
does not lift protection, budgets or capacity limits. Tasks count one rate
event, with bytes/entries charged per admitted effect group. Partial, unknown
or already attempted tasks never automatically resume: inspect the task and
recovery transactions, then seek review of remaining targets. Do not allocate
a PTY, auto-answer confirmation or call internal approval functions. These
flags are absent from published 0.2.9. See
[saved target plans](../../docs/guides/cleanup-plan.md#saved-explicit-target-plans-030-development).

On the development branch, CLI `--from-plan` and `--task` also accept a unique
lowercase ID prefix (ambiguous prefixes fail closed; files, JSON output and MCP
calls keep complete IDs). Terminal review prints permanent deletions before
quarantine moves with readable paths and exact logical bytes; selections with
more than 20 roots open with a top-level directory summary and a complete
owner-read-only review file (`.agent-guard/cleanup-reviews/`, or a private
temporary directory outside the workspace for full-workspace execution), then
require pressing Enter to expand every row before typing `APPROVE` or `EXECUTE`.

If the selected installation actually exposes the 0.3.0 MCP
tools, use `cleanup_plan` with literal `targets`; only `save: true` stores
a proposal. `saved_plan_id` alone reads it without refresh. Follow the
returned operator command and `binding` for the default terminal workflow.
An operator-enabled `--approval-channel codex` additionally exposes
`cleanup_request_approval(saved_plan_id)`: have the operator review the
complete exported list, then request the host form. Default is no approval;
never answer the form, change startup trust or manufacture consent yourself.
After response loss read `cleanup_plan(saved_plan_id)` for its durable
`host_approval` receipt and possible task ID; repeated requests do not prompt
or issue again. Negative/expired requests retain terminal operator review as
the fallback. This trusted host channel is not independent human attestation.
See [host confirmation and limits](../../docs/guides/mcp.md#host-confirmation-030-development).
After the operator approves in the same package/workspace/quarantine/session,
use `cleanup_execute` with the returned `task_id`, and `cleanup_task_status`
after partial or uncertain results. Never substitute `approved`, `confirm`,
a grant token or a new identity. Local runtime and bounded MCP-host results
are recorded in the [maintenance acceptance](../../docs/reports/test-report-maintenance-0.3.0.md);
the flow is absent from published 0.2.9. See [MCP task handoff](../../docs/guides/mcp.md#reviewed-maintenance-tasks-030-development).

The 0.3.0 cleanup MCP tools default to an overview. Use `view: details`
on saved plan/task queries for bounded rows (50 by default, at most 200),
or `view: full` for the full public result. Filters affect display only;
they never alter the approved selection. Cleanup plan/task text defaults to summaries,
while existing `--json` keeps full fields unless a compact view is requested.
Confirmation still shows the entire selection. Read `execution_summary`
as receipt evidence: distinguish dispatched-unconfirmed from not-dispatched,
keep missing outcomes unknown and do not equate logical bytes with freed
disk space. See [views and result units](../../docs/guides/maintenance-output.md).

To propose concrete executable candidates, produce a separate read-only plan:

```bash
python3 /absolute/path/to/agent-guard/skills/delete-guard/scripts/cleanup.py --json
```

The planner separates regenerable, explicitly discarded, uncertain and
protected entries, with sizes, reasons and projected effects. Declare known
discarded paths with `--discard PATH` and evidence/backup roots with
`--protect PATH`. Age, ignore status and untracked status alone do not prove
discardability. `.internal`, Git metadata and recovery boundaries are pruned.

Only propose the plan by default. The original full-workspace execution requires `--execute`, the
reviewed `--plan-id`, exact `--select PATH` entries and a human's terminal
acknowledgement. Do not answer that prompt on the user's behalf. The tool
rechecks the plan after confirmation and delegates to existing safe_delete;
ordinary files retain recovery copies and disk usage. This CLI adds no
native host tool or human-identity guarantee. See the
[workflow, effects and limits](../../docs/guides/cleanup-plan.md).

<a id="deletion-admission-027-candidate"></a>

## Deletion admission (0.2.7)

In 0.3.0, `safe_delete --dry-run` also reports
`admission` separately from the policy `verdict`. Read its current status,
approval requirement and `next_step`; exit 0 only means the preview completed.
`READY` is a snapshot, not reserved budget or execution permission. The check
does not debit budget or consume approval, while existing dry-run auditing
may still write metadata. Published 0.2.9 lacks this projection; see
[preview fields and limits](../../docs/guides/deletion-limits.md#dry-run-admission-preview-030-development).
On the development branch, multi-target `BLOCK_DELETE_BATCH_CONFIRM` guidance
points to MCP `cleanup_plan(save=true)` or `cleanup.py --targets-file` and
supplies the literal path array in `admission.next_step.targets_file.contents`
when `--json` is requested.

Covered deletion paths now share persistent session byte/entry budgets,
short-window rate/repeated-target-set limits and quarantine capacity admission.
The optional `.agent-guard/delete-policy.json` at the workspace root stores
limit overrides and additional protected paths. Applications and libraries
bound to that root share the policy. Core supplies defaults for omitted
fields. The configuration directory and admission control files are protected.
Protection wins over artifact and housekeeping exemptions. Malformed
configuration or state blocks new admission.

Defaults are 5 GiB/100000 entries for quarantine admission, 1 GiB/10000
entries per persistent session, 60 operations per 60 seconds, five repeated
admissions of one target set per window, and batch review above 256 MiB or
1000 entries. Counts include filesystem entries and logical metadata; these
limits are not an OS storage quota. Updating the package does not silently
reset existing counters or replace a drifted doctor baseline.

Unnamed CLI processes share a stable budget. Failed admitted operations and
restores do not refund it. A limit refusal is not permission to change session
identity, remove state, disguise the delete or purge recovery copies. Inspect
status and explain the reason. Large batches require the reviewed Cleanup
workflow; covered automated checks BLOCK rather than creating a new host ASK flow.
Cleanup's ten-minute, one-use-per-path approval still obeys budgets, capacity
and protection. No limit automatically triggers permanent deletion or GC.
Existing compound ASK and Git snapshot paths are outside this new budget;
normal restore, explicit GC and unprotected quarantine housekeeping retain
their exemptions. Configuration/control-path protection still applies.

After investigation, an operator can explicitly reset the current bucket with
`status.py --reset-delete-budget`, an interactive terminal and `RESET`.
Do not supply this acknowledgement on the user's behalf. Reset does not free
storage or change limits; normal restore and explicit GC remain separate.
Portable state and TTY remain advisory against same-UID processes. See
[defaults, accounting and limits](../../docs/guides/deletion-limits.md).

### Workspace limit settings (unreleased)

A user can ask to set a limit in plain language, such as “set this project's
session deletion budget to 2 GiB.” Use `agent-guard-delete-policy` or the
selected installation's `scripts/delete_policy.py`. The
[settings guide](../../docs/guides/deletion-limits.md#configure-workspace-limits)
maps user terms to fields and units.

1. Identify the workspace root and pass it explicitly with `--root PATH`.
   Use `show --json` for effective limits and their sources. Treat a requested
   repository limit as an override for that root. Report usage from the same
   workspace and session when available. Clarify an ambiguous scope, limit
   or unit before proposing a change.
2. Preview the requested change with `set session_max_bytes=2GiB --preview --json`,
   or use `reset-limits --preview --json` for defaults. Show the workspace,
   before/after values in readable units and any need to review pending Cleanup
   approvals. If the preview has no changes, report the current settings.
3. Give the operator the same command without `--preview` for their terminal.
   The CLI shows the current diff and the operator types `APPLY` to save.
   Leave the confirmation and write to the operator. Use read-only
   `show` to check the saved result after they apply it.

Restoring defaults removes numeric overrides for the selected workspace.
It preserves protected paths, session accounting and recovery records.
After a limit refusal, inspect the current status and explain which limit
applied. Propose a settings change when the user asks to change it.

## If you run `rm` / destructive git anyway

A harness adapter may intercept the command before execution. Verdicts you
can receive:

| Verdict | Meaning | Your move |
|---|---|---|
| `ALLOW` | no recovery action for recognized operations; unrecognized effects are unchecked | continue only within the authorized scope; do not infer that scripts or overwrites are safe |
| `RELOCATE` / `SNAPSHOT` (harness may show `PROCEED` after compensation) | quarantine or tracked Git snapshot required | record and verify the resulting `txid` before continuing |
| `ASK` | this understood operation needs single-execution approval | use the host's human approval path; if unavailable, stop or split the command |
| `BLOCK_UNDETERMINABLE_EFFECT` | targets unresolvable (`$VAR`, `bash -c`, `find -delete`, `xargs`) | restate with explicit paths, or use `safe_delete` |
| `BLOCK_WILDCARD` | glob target set is opaque | use `safe_delete` with the glob |
| `BLOCK_OUT_OF_WORKSPACE` / `BLOCK_PROTECTED_PATH` / `BLOCK_PROTECTED_ANCESTOR` | outside boundary, workspace root, `.git`, or a filesystem root (`/`, `/home`, `/usr`, `$HOME`) | do not retry; this is a hard boundary. Ask the human if it is truly needed |
| `BLOCK_RESTRICTED_MODE` | session is downgraded | only explicit single-file deletes are permitted; ask the human for anything more |
| `BLOCK_FORCE_PUSH` | remote history destruction | do not retry; escalate to the human |
| `BLOCK_DELETE_CONFIG_INVALID` / `BLOCK_DELETE_STATE_INVALID` | invalid configuration, state or admission machinery | stop and report; do not remove state to bypass the refusal |
| `BLOCK_DELETE_PROTECTED` | configured protection or deletion configuration overlap | preserve the target and its protected descendants |
| `BLOCK_DELETE_LIMIT` / `BLOCK_DELETE_RATE` / `BLOCK_DELETE_REPEAT` | capacity, cumulative budget or short-window limit | inspect status/loop; wait only when the reported window can expire |
| `BLOCK_DELETE_BATCH_CONFIRM` | large operation or stale/consumed Cleanup approval | propose and review a fresh Cleanup plan |

Shape rules on compound commands: a single line that `cd`s anywhere before a
destructive op (F1), or that creates files (`touch/mkdir/cp/mv/tee`,
redirections) before destroying them (F2), can return `ASK` when its targets
are resolvable and within policy boundaries. Unresolvable effects and hard
boundaries still return `BLOCK`; hosts without a usable approval path deny
the `ASK` call. Run deletions as standalone commands with an explicit workdir.

A block is not an error to route around. Retrying the same operation in a
disguised form (`/bin/rm`, `python -c`, a script) violates this discipline and
can bypass both protection and audit. Do not treat a missing audit as proof
that no operation occurred.

`git reset --hard [commit]` first checks whether its target tree would
overwrite untracked or ignored content. A tracked-only stash cannot recover
those paths, so a collision returns `BLOCK_GIT_RESET_COLLISION` without
moving them or creating a snapshot. Preserve the conflicting content
separately before retrying. Use a standalone reset without shell wrappers;
compound commands, submodule effects, unknown options, and stash-relative
targets are refused as undeterminable. Unrelated untracked files are allowed.

## Compensation preflight and verification

Before sandboxed use, ensure `.agent-trash/` is already ignored by a tracked
`.gitignore` or by host-managed `.git/info/exclude`. If Git metadata is
read-only and no ignore rule exists, the guard blocks before moving a source.

After a relocation or snapshot, verify that its `txid` appears as
`RESTORABLE` in `restore.py list` before running a related destructive command.
After restoration, verify that a consumed relocation shows `RESTORED`.
If the guard reports an internal, preflight, manifest, or compensation error,
stop immediately. Inspect both the origin and `.agent-trash/`; never infer
that an error means no filesystem change occurred. A durable
`relocate-intent` can keep an interrupted relocation discoverable. On the
development branch, `restore.py list`, `status` and MCP queries also report
`manual_review_items` for failed relocations without a completion record; keep
those files and journal entries for inspection, as ordinary restore refuses
surviving unconfirmed copies.

## Assessment and preflight

Advisory `check.py` and `safe_delete.py --dry-run` preserve command targets,
but may create quarantine/audit metadata and a Git local exclude rule.
They are not zero-write filesystem checks. Advisory exit 0 means the
assessment completed; inspect the decision and never execute a BLOCK/ASK
command merely because the exit was zero. Blocked safe_delete still exits 2
under dry-run. Audit warnings do not imply that all metadata writes were
undone. Use only a disposable fixture when verifying this behavior; the
[policy reference](references/policy.md#assessment-and-dry-run-side-effects)
defines the no-match, existing-ignore and external-bucket branches.

Since 0.2.5-rc1, new `safe_delete` audit events record decisions, target counts and established
recovery IDs/counts rather than target paths or free-form reasons. `--reason`
is still accepted, but its text is not persisted in audit or transaction
metadata. Exact recovery paths remain in the CLI result and manifest.
At released RC1, historical records and automatic session handling are unchanged.
Since RC2, the shared audit layer projects supported metadata on append/read,
uses opaque session correlation and limits status/GC metadata; see the
[shared audit contract](../../docs/guides/audit-metadata.md) and the
[policy reference](references/policy.md#safe_delete-audit-metadata).

## Quarantine location queries

In 0.2.5, deletion/status results explain
retained storage and include GC commands. Moving files to quarantine keeps
their data on disk. Review the plan before choosing `--execute --all-eligible`
or a specific `--execute --txid`; use the same working directory and workspace/
trash settings. Age/size thresholds do not perform automatic cleanup. On the
development branch, single-transaction relocation receipts include the exact
`--txid` in their GC commands, `gc.py --txid` accepts a unique transaction
prefix, and `gc.py --task ID` previews completed quarantine receipts from a
Cleanup task (using the same workspace, quarantine and session; add `--execute`
only after review, and inspect unresolved tasks or incomplete receipts first).

`status.py` reports local recovery data and does not verify host activation.
DSH's status tool separately reports the plugin process startup check and
listener registration, with live interception still `UNVERIFIED`. A missing
plugin cannot supply its own status tool. `ALLOW_NOOP` does not certify
unrecognized script/interpreter effects. See the
[feedback guide](../../docs/guides/recovery-feedback.md).

When the user asks where quarantines are or how many exist, use the shared
read-only query with a current-directory or user-selected scope:

```bash
python3 /absolute/path/to/agent-guard/skills/delete-guard/scripts/status.py --trash-index --json
python3 /absolute/path/to/agent-guard/skills/delete-guard/scripts/status.py --trash-index --root /absolute/path/to/projects --max-depth 3 --json
python3 /absolute/path/to/agent-guard/skills/delete-guard/scripts/status.py --trash-index --trash /absolute/path/to/custom-store --json
```

Report the scope, candidate count, metadata-identified count and read/budget
errors. Do not claim that unconfirmed/unreadable locations are absent or that
the result covers the whole host. Explain that these are bucket locations,
not file/transaction totals. If you supplement the result yourself, identify
that source separately. Existing native status tools may show the current
project only; use the CLI for this indexed query. Location metadata and cached
JSON are not authorization to purge, and recovery file contents must not be
read just to answer a location/count question.

## RESTRICTED mode

Version 0.2.5 retains RC1 protection for the quarantine root, root manifest,
audit/state files, state temp file, sessions tree and storage access parents.
Housekeeping does not override those checks. Use the existing explicit
`gc.py --execute --txid '<txid>'` entry for a validated, audited transaction purge.
Ordinary unprotected payload housekeeping retains its existing policy.
GC plans omit `PURGED` history; they do not delete stored Git stashes.

After a human veto, the session runs with narrowed powers: explicit
single-file deletes inside the workspace still work (quarantined as usual);
recursive deletes, globs, and destructive git operations are refused. Only a
human can restore NORMAL. If a task requires more, say so plainly
and ask.

## Regenerable artifacts

Provably regenerable targets (git-ignored AND matching known artifact names
like `node_modules/`, `dist/`, `__pycache__/`) are allowed to be deleted
directly - no quarantine overhead. If git cannot prove a target is ignored,
it is treated as valuable and quarantined. Uncertainty increases restriction.

## Recovery

Every relocation writes `origin -> trash` pairs into
`.agent-trash/manifest.jsonl`. Restore is non-destructive: it refuses to
overwrite anything that now exists at the origin unless a human passes
`--force`. Git snapshots are stored as stashes named `agent-guard:<txid>`;
restore applies them and never drops them.

With human `--force`, an existing occupant is first preserved by the normal
relocation journal in a separate transaction. The result's optional
`backup_txids` lists preservation attempts; an ID alone does not prove the
copy completed. Inspect those IDs with `restore.py list --json` and the
filesystem after any error. A failed old-version move must not be followed
by deleting the preserved occupant. Restoring a completed backup transaction
uses the same `restore.py <txid>` command; if the origin is occupied, the
usual conflict/force rules apply again. Preserved versions consume quarantine
space and remain subject to its existing explicit GC policy.

See `references/policy.md` for the complete rule table, verdict codes, and
manifest format.

Restore audit records counts and transaction associations rather
than duplicate paths/error text. CLI results and recovery manifests still
contain the exact data needed to inspect and restore; a failed CLI result
must still be checked against the filesystem and preservation attempts.
