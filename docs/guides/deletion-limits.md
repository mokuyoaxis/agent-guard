# Deletion limits / 删除准入与预算

This is an **unreleased checkout addition**, absent from published 0.2.6.
No feature version has been selected; the 0.3.0 plan remains paused.
On 2026-10-28, the same frozen public runtime passed three independent full
runs: each contained 1090 Python cases and both Node groups, taking 1061.017,
1025.315 and 1042.145 seconds. No failures, errors, skips, timeouts or automatic
retries occurred. The existing diagnostic unittest result collector preserved
the scope and deadlines of `npm test`; the npm command itself was not invoked.
An offline 149-file package and three real Kimi Code 2.3.0 scenarios also
passed: recoverable deletion, same-session budget refusal and configured-path
protection. Nine configured-route model requests returned HTTP 200. The
loopback gateway's server-side model identity was not independently verified.
Tests ran on ARM64/PRoot POSIX, Python 3.13.5 and Node 22.23.4; native Windows,
macOS, PowerShell and Android shared storage were not exercised. Kimi coverage
is limited to the observed root Bash hook and optional Stop, not other tools
or subagents. Historical Cleanup/release evidence keeps its original scope;
the earlier npm timeout remains unexplained and did not recur in these runs.

## Configuration and defaults

The optional workspace file `.agent-guard/delete-policy.json` overrides the
defaults below. An absent file uses defaults without creating configuration.
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

Numbers must be integers through `2**63 - 1`; booleans and numeric strings
are invalid. Byte limits may be zero, meaning no byte allowance, not unlimited.
Other numeric limits must be positive. Configuration files larger than 1 MiB are rejected.
Protected paths may be workspace-relative or absolute inside the workspace;
they may name future paths, but cannot escape through an external directory
link. A final outward-pointing link itself can be protected.

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
