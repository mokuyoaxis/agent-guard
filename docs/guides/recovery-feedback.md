# Reading guard status and cleanup results

The CLI/DSH feedback below is included in
[0.2.5](../releases/release-notes-0.2.5.md).
They do not change deletion policy, wildcard handling, retention thresholds,
or the recovery format.
The final Kimi section describes the unpublished
[0.2.6 preparation](../releases/release-notes-0.2.6.md).

## What the status establishes

The DSH adapter runs `python3 check.py --help` before registering its tools
and Hook. This checks that Python and the checker imports run in the plugin
process environment; `python3` must be on that process's PATH as well as
available to the actual executor. It evaluates no command and writes no guard metadata.
It does not test the caller's shell, sandbox, or live host interception.

`agent_guard_status` adds an `adapter` object alongside the Core report:

| Field | Meaning |
|---|---|
| `startup_check` | The local checker startup check passed |
| `startup_scope` | `plugin_process`; the environment actually checked |
| `hook_registered` | This plugin registered its pre-execute listener |
| `last_checker_result` | `NOT_RUN`, `OK`, or the most recent checker infrastructure diagnostic; a policy refusal can still be an `OK` check |
| `host_interception` | `UNVERIFIED`; registration and local queries do not certify a live host path |

Missing shell service, missing checker, Python launch/import failure, a
10-second startup timeout, or excessive startup output now fails plugin initialization explicitly.
The default path reports `GUARD_STARTUP_FAILED`; the optional read guard
retains `READ_GUARD_STARTUP_REFUSED`. Check the host's plugin status for
successful activation. If the plugin is absent or inactive, its own status
tool may not exist. A running host or a CLI status result is insufficient:
the host can continue running other tools after this plugin fails.

If the status script fails after registration, the model tool returns
`ok: false` and `report: null` with its adapter facts. It omits raw process
exceptions and unusable process output. These diagnostics describe the status
query; they do not overwrite the last command-checker result.

| Diagnostic | Meaning |
|---|---|
| `STATUS_EXECUTION_FAILED` | The caller's shell could not start or finish the status process |
| `STATUS_TIMEOUT` | The status process timed out, even if it produced output |
| `STATUS_PROCESS_FAILED` | The status process exited unsuccessfully |
| `STATUS_INVALID_OUTPUT` | A zero exit status did not provide a usable recovery report |

Once a command reaches the checker, process failure, timeout and unusable
output remain denials. Static `CHECKER_*` diagnostics distinguish these
failures from a Core policy refusal without copying private command text or
raw process errors into adapter logs.

## What ALLOW_NOOP means

`ALLOW_NOOP` keeps its original decision and reason code. Its explanation is:

> No recovery action is needed for the recognized operations. Unrecognized command effects have not been checked.

For an unrecognized script or interpreter command, this is not a claim that
the operation is harmless. Scripts, overwrites and other uncovered paths
retain their existing limits. See the [threat model](../design/threat-model.md).

## Moving a file does not remove its recovery copy

A successful relocation now reports `storage_notice` and `gc` guidance in
CLI JSON and displays the same information in terminal output. Enforced
checks and `safe_delete` also report retained data after a partial relocation,
including a storage failure after some files moved. The failed decision stays
unchanged; both JSON and terminal output include the recovery transaction ID.
A storage failure before any move does not claim retained data. Direct artifact
deletion, a no-match result and a deletion dry-run do not claim a relocation.
The DSH model-facing preview puts adapter/storage feedback before long
target or audit arrays within the existing preview limit.

Quarantined data still uses storage. Cross-filesystem moves and actual free
blocks depend on the filesystem; no exact freed-space measurement is claimed.
Status uses the existing size accounting and eligibility plan. Regenerable
artifacts still require the existing ignored-path and artifact-pattern rules.

Run maintenance from the same working directory with the same
`AGENT_GUARD_WORKSPACE` and `AGENT_GUARD_TRASH` settings as the operation.
Replace the package path below with your installation; CLI results supply
POSIX-shell-quoted commands for that package and Python interpreter. Use a
POSIX shell for these command strings; their quoting is not PowerShell or CMD syntax.

```sh
# Inspect the plan; this removes nothing.
python3 /absolute/agent-guard/skills/delete-guard/scripts/gc.py

# Permanently remove the recovery copies selected by the plan.
python3 /absolute/agent-guard/skills/delete-guard/scripts/gc.py --execute --all-eligible

# Or explicitly select one transaction, including one not yet eligible.
python3 /absolute/agent-guard/skills/delete-guard/scripts/gc.py --execute --txid '<txid>'
```

Review the plan and selected transactions before choosing a purge. A manual
`--txid` selection is not limited to age/capacity eligibility. The 30-day and
5-GiB thresholds do not run GC automatically. `--execute` alone selects
nothing; GC does not prune Git stash snapshots.

## Kimi: tool success and recovery feedback

In the sampled Kimi 0.42.0, 2.1.1 and 2.3.0 paths, successful PreToolUse recovery
explanations did not enter the next model request. A successful Bash result
alone therefore does not tell the model that files were retained.
The [2.3.0 observations](../reports/test-report-kimi-code-block.md#kimi-230-acceptance)
used fixed responses through the actual prompt CLI and ACP host, with no
external model calls.

The unpublished 0.2.6 source provides a separate, opt-in POSIX Stop hook. It supplies
actual transaction IDs, state/restore commands, retained-storage guidance and
full GC commands, then asks for one extra response after the initial answer.
The initial answer may already be inaccurate, and the model can still omit
details. Emission cursors track notices, not model acknowledgement.

Restoring files returns them to their original location; it does not free
total disk space. GC permanently removes recovery copies and remains an
explicit operation. The Stop hook itself executes no restore or GC.
Published 0.2.5 lacks the Stop helper. See [setup and limits](../../adapters/kimi-code/README.md#optional-recovery-feedback-unreleased-checkout-addition)
and the [bounded real-host report](../reports/test-report-kimi-code-block.md).
In the unpublished 0.2.6 source, the doctor separates PreToolUse from Stop
configuration and local feedback probes. `--probe` uses isolated synthetic
records and leaves existing recovery data alone. Stop's `model_delivery`
remains `UNVERIFIED`; local PASS does not establish model receipt. See the
[Stop doctor fields](../../adapters/kimi-code/README.md#stop-doctor-checks-unreleased).
