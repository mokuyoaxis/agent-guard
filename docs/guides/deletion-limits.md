# Deletion limits / 删除准入与预算

Delete-guard enforces shared execution-time admission controls across `safe_delete`, `check --enforce`, Cleanup execution, and local MCP tools so runaway loops or mis-scoped cleanups stop cleanly rather than exhausting storage or wiping critical workspace state:

- **Quarantine capacity:** checks logical bytes and entry counts across processes at admission time and refuses quarantine requests that exceed the limit (logical admission rather than a hard OS disk quota—audit logs, manifests, directory metadata, and unrelated writes can still grow disk usage).
- **Session budgets:** caps total deleted/quarantined bytes and entries per workspace session bucket.
- **Rate and repeat limits:** throttles rapid-fire calls and repeated retries on identical target sets.
- **Protected paths:** blocks mutations to `.git`, guard configuration/control metadata, and operator-protected workspace paths before artifact exemptions apply.
- **Large-batch confirmation:** halts oversized automated deletions (`BLOCK_DELETE_BATCH_CONFIRM`) and directs them through reviewed Cleanup approval instead of silent fallback.

Limits apply per workspace root via `.agent-guard/delete-policy.json` (falling back to built-in defaults for omitted keys). 删除准入控制按工作区根目录生效，未显式设置的字段自动沿用内置默认值。

> [!NOTE]
> Shared deletion admission arrived in [**0.2.7**](../releases/release-notes-0.2.7.md) (deployments on `0.2.6` and earlier retain only soft retention and manual GC). Version [**0.3.0**](../releases/release-notes-0.3.0.md) adds dry-run `admission` previews, `--quarantine` preservation requests, and approved-task accounting, and [**0.3.1**](../releases/release-notes-0.3.1.md) adds `agent-guard-delete-policy`, conditional dry-run phrasing, and multi-target `--targets-file` handoff; historical validation checkpoints and host test scope remain recorded in the [0.2.7 notes](../releases/release-notes-0.2.7.md) and [0.3.0 acceptance report](../reports/test-report-maintenance-0.3.0.md).

<a id="dry-run-admission-preview-030-development"></a>

## Dry-run admission preview (0.3.0)

Version **0.3.0** adds `admission` to `safe_delete --dry-run` JSON and terminal output (absent from `0.2.9`). While `verdict` remains the static policy projection, `admission` evaluates live execution checks and reports the first applicable refusal in order.

In `0.3.1+`, relocation and regenerable-deletion previews use conditional phrasing—stating what *would* move or be deleted without implying that disk state changed; published `0.3.0` tarballs may still print past-tense phrasing during dry runs. Always read `dry_run`, the execution summary, and `admission` together: the policy code alone does not prove execution or approval.

| `admission.status` | Meaning |
|---|---|
| `READY` | Current shared admission checks pass; execution must recheck |
| `REQUIRES_APPROVAL` | A reviewed Cleanup approval is missing, expired, changed or consumed |
| `BLOCKED` | Policy, a current limit, protection, configuration, source inventory or state prevents proceeding |
| `NOT_APPLICABLE` | No deletion is needed, or shared admission does not apply to quarantine housekeeping |

The object includes `code`, `reason`, the measured request bytes/entries and
batch limits when available, plus `approval.required` and `approval.status`.
Approval status is `VALID`, `REQUIRED`, `INVALID`, `NOT_REQUIRED` or
`NOT_CHECKED`. A null requirement means it was not determined; an earlier
limit can stop checks before approval validation. `requested` remains null
when inventory was not performed. No token or approval-state inventory is
returned.

`reserved` and `lock_checked` are false. The check does not create an
admission lock/state, debit a budget, consume a grant, reserve capacity or
test payload-storage writes. The CLI retains its existing advisory audit
side effects, which can create metadata. Measurements and state can change
after the snapshot; execution still checks under its lock.

Exit codes retain their meaning: a successful preview exits 0 even if its
current admission is `BLOCKED` or `REQUIRES_APPROVAL`. A policy BLOCK still
exits 2. A dry-run of an unmatched glob remains a no-op. Do not interpret
exit 0, `READY`, or an MCP `ok: true` as a new grant of permission.

`admission.next_step` supplies an action, explanation, working directory,
workspace/quarantine binding and supported commands. Each command has both
structured `argv` and a POSIX-quoted `command` string. Keep the same session
settings; the output never includes its approval token. A ready request's
execution command uses the observed literal targets, with glob characters
escaped for the existing CLI. It does not automatically execute them.

Batch refusals now point to [saved explicit-target proposals](cleanup-plan.md#saved-explicit-target-plans-030-development)
in 0.3.0. The generated command lists the observed
literal roots and uses `--save-plan`; it does not declare them discarded.
After confirming discardability, add `--discard-targets` or individual
`--discard` declarations, inspect the stored selection and use `--from-plan ID --approve`
in an operator terminal. The agent then executes or queries the returned
`--task ID`. These CLI additions do not add an MCP approval
tool. Limits direct the caller to status; rate/repeat refusals call
for inspecting the window/loop. Quarantine housekeeping directs the caller
to a GC preview. No guidance automatically resets state, splits batches,
changes identity, answers confirmation or purges recovery copies.

In 0.3.1+, a multi-target request requiring approval points
to MCP `cleanup_plan(save=true)` or to `cleanup.py --targets-file`. The
dry-run JSON includes the exact literal array at
`admission.next_step.targets_file.contents`. Write that array to a JSON file
and replace the command's `<targets.json>` placeholder with its path.

Runtime, no-debit/grant-preservation and CLI/MCP regressions passed in the
[maintenance verification](../reports/test-report-maintenance-0.3.0.md).
The older counts above retain their separate source checkpoint; 0.3.0
regression results and untested host paths are recorded separately.

<a id="quarantine-only-requests-030-development"></a>

## Quarantine-only requests (0.3.0)

Use `safe_delete --quarantine` or create a Cleanup plan with `--quarantine`
when authorized cleanup must preserve recovery copies even for proven
regenerable artifacts. MCP `safe_delete` and new `cleanup_plan` calls accept
`quarantine: true`. Published 0.2.9 lacks this option. Defaults are unchanged;
false/omission follows existing policy, rather than forcing direct deletion.

Preview admission measures quarantine capacity for the requested effect, and
its ready/batch-plan commands retain `--quarantine`. The choice does not
reserve space or relax protection, restricted mode, batch approval, session
budget or rate checks. Quarantine admission/storage failure stops the request;
there is no direct-delete or automatic-GC fallback. Restore does not refund
the budget. This option cannot be used to purge existing recovery copies.
See [saved choice and execution](cleanup-plan.md#require-quarantine-030-development).

## Configuration and defaults

In 0.3.0, capacity inspection resolves a linked quarantine
root to its physical directory before measuring occupancy. Links among the
deletion targets remain opaque entries. This correction is absent from older
installed copies; selecting an external bucket does not update their code.

Delete-guard reads one policy per workspace root, normally the root of a
Git repository or worktree. Applications and libraries in the same workspace
share that policy. A process bound to a workspace uses that root.

Core supplies the built-in defaults below. To set a repository's limits, use
`.agent-guard/delete-policy.json` at its workspace root. The fields in that file
override the built-in values for that workspace. Omitted fields use the
built-in values, so a workspace can use defaults without a policy file.
The configuration directory is always protected. Unknown or duplicate keys,
invalid JSON, unreadable configuration and invalid values block new deletion.

| Field | Default | Meaning |
|---|---|---|
| `quarantine_max_bytes` | 5368709120 (5 GiB) | Admission ceiling for measured existing quarantine plus incoming content |
| `quarantine_max_entries` | 100000 | Same ceiling in filesystem entries |
| `session_max_bytes` | 1073741824 (1 GiB) | Cumulative logical bytes admitted for this session |
| `session_max_entries` | 10000 | Cumulative filesystem entries admitted for this session |
| `rate_window_seconds` | 60 | Rolling admission window |
| `rate_max_operations` | 60 | Admitted operations in that window |
| `repeat_max_operations` | 5 | Admissions of the same normalized target set in that window |
| `batch_max_bytes` | 268435456 (256 MiB) | Larger operations require confirmed Cleanup execution |
| `batch_max_entries` | 1000 | Same confirmation threshold in filesystem entries |
| `protected_paths` | `[]` | Additional literal workspace paths to preserve |
| `regenerable_patterns` | `[]` | Validated workspace-relative artifact patterns (development checkout; max 64) |

Numbers must be integers through `2**63 - 1`; booleans and numeric strings
are invalid. Byte limits may be zero, meaning no byte allowance, not unlimited.
Other numeric limits must be positive. Configuration files larger than 1 MiB are rejected.
Protected paths may be workspace-relative or absolute inside the workspace;
they may name future paths, but cannot escape through an external directory
link. A final outward-pointing link itself can be protected.

Workspace `regenerable_patterns` extend built-in artifact names under four
guardrails: patterns must be workspace-relative POSIX patterns up to 128
characters without `**`, `.` / `..`, drive/path prefixes, or catch-all
wildcards (the final segment needs at least two literal alphanumeric characters
and cannot match protected or secret-store names, and multi-segment patterns
match segment-by-segment so `*` never crosses `/`); targets must still be
Git-ignored and outside protected paths; whole-tree inspection ensures that
secret-store files or directories containing secret-store descendants (or
whose scan is incomplete/unreadable) fall back to recoverable quarantine
instead of direct deletion; and `--quarantine` always takes precedence over
direct deletion. Adding or removing patterns changes the policy fingerprint and
invalidates pending Cleanup approvals (`CONFIG_CHANGED`).

For example, an operator can choose stricter limits and preserve evidence:

```json
{
  "session_max_bytes": 536870912,
  "rate_max_operations": 30,
  "protected_paths": ["evidence", "backups/current"]
}
```

Protection covers named and physical locations, descendants and deletion of
an ancestor that would remove a protected path. It precedes regenerable and
quarantine-housekeeping exemptions. Cleanup's `--protect` remains an extra
invocation-only planning option; use the configuration for shared protection.

<a id="configure-workspace-limits"></a>

### Operator limit settings (0.3.1)

Use `agent-guard-delete-policy` (`0.3.1+`) to inspect
or change limits for one workspace. From source, run
`python3 skills/delete-guard/scripts/delete_policy.py` with the same arguments.

```sh
agent-guard-delete-policy show
agent-guard-delete-policy set session_max_bytes=2GiB --preview
agent-guard-delete-policy protect add evidence backups/current --preview
agent-guard-delete-policy regenerable add '.venv-wheel-*' --preview
```

`show` lists the selected workspace, effective values and built-in defaults.
In `--json` output, each field's `source` is `workspace` for an explicit
override or `default` for a built-in value. Use `--root PATH` to select an
existing workspace explicitly. Otherwise the command uses the current binding
or discovers the root from the current directory. For current usage, run
`agent-guard-status --view overview` in the same workspace and session.

`set` changes the fields you name. Byte fields accept integer bytes or sizes
such as `512MiB`, `1.5GiB` and `2GB`. With a space, quote the whole
assignment, as in `'session_max_bytes=2 GiB'`. Use `KiB` through `TiB` for
powers of 1024 and `KB` through `TB` for powers of 1000. Sizes must resolve to
whole bytes. Entry counts, operation counts and seconds take integers.

Use `--preview` with `set` or `reset-limits` to see the proposed diff without
writing. Preview JSON marks `mode` as `preview` and includes
`requires_confirmation`, `effective_policy_changed` and the before/after
values in `changes`. Byte values in JSON and on disk stay exact integers.

With the Delete-guard Skill loaded, you can ask the agent to prepare a change
in plain language. For example, ask to set the current project's session
deletion budget to 2 GiB. The agent can inspect the settings and show the
preview. To apply the change, the operator runs the command in their terminal
without `--preview`. Review the current workspace and diff, then type
`APPLY` to save or press Enter to cancel. The writer requires a regular,
unlinked policy path. Repair invalid policy JSON in its source file before
applying an update.

`protect add|remove` and `regenerable add|remove` (development checkout) manage
`protected_paths` and `regenerable_patterns` through the same `--preview` and
interactive terminal `APPLY` flow. `protect add` refuses the workspace root (`.`)
and Core control directories (`.git`, `.agent-guard`, `.agent-trash`);
`protect remove` warns that guarded deletion will be allowed for the removed
path. `regenerable add` validates each pattern against the guardrails above and
warns that Git-ignored matches may be permanently deleted without quarantine
copies unless `--quarantine` is requested. `show` displays both lists alongside
effective numeric limits.

`reset-limits` removes the numeric overrides for the selected workspace.
Those fields return to the built-in defaults. Run
`agent-guard-delete-policy reset-limits --preview` to review the change.
The workspace keeps its `protected_paths`, `regenerable_patterns`, cumulative
usage, recorded rate events and recovery transactions. The effective window
length determines which recorded events count. An effective policy change can
make a pending Cleanup task report `CONFIG_CHANGED`. Review and approve the
selection under the current policy before executing it. Use the separate
[operator budget reset](#status-and-operator-recovery)
when you intend to clear current-session accounting.

## Accounting and execution

`core.admission` stores counters and short-lived approvals in the selected
quarantine's `admission.json`, with `admission.lock` for cross-process
serialization. Measurement, durable debit and guarded mutation share the
lock. Missing state starts empty; malformed or unavailable state, lock or
inventory blocks admission. State, its temporary file and lock are protected
control files, alongside the existing recovery evidence.

`AGENT_GUARD_SESSION` selects a persistent budget within that quarantine;
its value is hashed for storage. Unnamed CLI processes share one stable
default bucket, including Cleanup's child processes. Counters charge
**admitted work**, before mutation. Failed operations and later restores do
not refund it. The rate and repeat windows expire, but cumulative session
bytes and entries do not silently renew. The same-target-set rule is a
simple delete/recreate loop signal, not general anomaly classification.

Inventories use bounded `lstat` traversal, count directory metadata and
links themselves, and conservatively count hardlinks per entry. Capacity
compares existing quarantine and incoming logical bytes/entries. These
figures differ from Cleanup's displayed, hardlink-deduplicated candidate
sizes and from physical disk blocks. Scan errors or changed directories
are refusals, never zero-size estimates.

Raising policy numbers does not raise the implementation's inventory bounds:
200000 entries per scan, depth 128, 1024 stored sessions, 32 outstanding
approvals, 10000 window events per session and 8 MiB of state. A higher
configured rate still caps at 10000 events. Reaching a bound refuses work;
it does not silently discard counters. Scanning existing quarantine is
proportional to its entries, with no guaranteed wall-clock completion time.

Relocations, including Git clean compensation and force-restore occupant
preservation, check capacity. Regenerable direct deletion also checks session,
rate, repeat and batch limits. Covered automatic native-hook paths charge
admission before returning control to the host; they cannot prove the host
subsequently performed the delete.
Normal restore, explicit transaction GC and existing unprotected quarantine
payload housekeeping remain available without a new session/rate/batch debit.
Configuration and control-file protection still applies to housekeeping.
No limit triggers automatic GC or a fallback to permanent
deletion. Already completed earlier operations remain completed after a later
refusal; inspect reported transaction IDs.

Large deletions on the automated rm, Git-clean and artifact paths through
`check --enforce` or `safe_delete` return
`BLOCK_DELETE_BATCH_CONFIRM`, directing the operator to
[Cleanup Copilot](cleanup-plan.md#confirm-and-execute), rather than host `ASK`.
The enforced shell checker combines deletion targets across the request,
including enumerated Git clean targets, before its first compensation.
After terminal confirmation and a fresh plan check, Cleanup creates an approval
valid for ten minutes. It binds the session, configuration, exact paths and
measured metadata. Each selected path is consumed once before its operation;
failure does not restore approval. Changed, expired or replayed approvals are
refused. Approval lifts only the batch-confirmation gate: protection, budgets,
rate and capacity still apply.

Existing compound-command `ASK` paths return to host authorization before
automatic compensation and do not consume this admission budget. Existing
Git reset/restore/checkout snapshots also use their separate Git storage,
outside this quarantine budget. This change adds no new coverage or bound
for those paths; Git snapshot storage can still grow.

<a id="approved-task-accounting-030-development"></a>

## Approved-task accounting (0.3.0)

The [saved-plan task workflow](cleanup-plan.md#operator-approved-tasks-030-development)
separates operator approval from an agent's execution. Approval alone does
not debit counters. One task groups quarantine and regenerable deletion into
at most two safe_delete calls. Its first admitted group charges one rate and
repeat event using the entire approved target set; the second group shares
that event. Legacy CLI and native-hook requests keep their existing accounting.

Each group still checks protection, configuration, session/capacity limits,
source metadata and its approved effect under the admission lock. Logical
bytes/entries are debited once for that admitted group, including directory
metadata; failures and restores do not refund them. A whole-task read-only
preflight checks the combined session budget, with capacity checked for the
quarantine group. It reserves nothing. Concurrent activity or expiry can
still stop later groups, leaving a partial task.

Task grants keep the existing 600-second expiry. They bind a task ID and
per-root effect, so a quarantine plan cannot later use the regenerable-delete
or housekeeping path. No threshold is relaxed and no identity changes.
Query `cleanup --task ID --json` after a partial or unknown result; a repeated
execute never resumes a claimed task. Additional work requires reviewing a
fresh proposal for unresolved roots. This workflow is included in 0.3.0 and
absent from 0.2.9. Runtime and bounded host checks are recorded in the
[maintenance acceptance](../reports/test-report-maintenance-0.3.0.md).

## Status and operator recovery

Inspect effective limits and current-session counters with `status.py --json`.
The [policy reference](../../skills/delete-guard/references/policy.md#unreleased-execution-admission)
defines the new machine-readable refusal codes.
After investigating a stopped loop, an operator may run:

```sh
python3 /absolute/path/to/agent-guard/skills/delete-guard/scripts/status.py --reset-delete-budget
```

This requires an interactive terminal and typing `RESET`. It clears only the
current bucket's counters, short-window events and outstanding approvals.
It does not purge recovery copies, reset quarantine capacity or alter limits.
An agent must leave this confirmation to the operator. Corrupt state is a
diagnostic failure, not an instruction to delete or silently reinitialize it.

These checks suppress accidents on covered paths; they are not an OS disk
quota or authenticated human authority. Same-UID processes can change local
configuration/state, change session identity or allocate a terminal. Audit,
manifest and new directory metadata, concurrent unrelated writers and writes
outside guarded paths can still grow storage. Existing 30-day/5-GiB GC
eligibility remains a separate soft planning rule. Review its explicit plan
using the [storage and GC guide](recovery-feedback.md).
