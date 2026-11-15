# 0.3.0 — Reviewed cleanup tasks

Save a concrete cleanup selection, review and approve it once, then let
the agent execute and query that approved task through CLI or MCP. Existing
protection, budgets, capacity and rate checks still apply.

本版把清理任务接成闭环：先保存明确目标，由操作者审阅批准一次，再交给 Agent
执行、查状态和交接结果。无需手写 Core 调用，也不必反复录入同一批目标。
Cleanup Copilot 仍属于 Delete-guard；Exfil-guard 和 Guard-lab 的功能归属不变。

This is an unreleased source candidate. Its manifest is 0.3.0; tag, GitHub
Release and npm publication have not been performed.

## Changes

- Save literal targets or a JSON target list as a reviewed proposal. The
  operator approves that selection; the agent continues with its task ID.
- MCP adds `cleanup_plan`, `cleanup_execute` and `cleanup_task_status`,
  bringing the default service to ten tools. Operator-selected
  `--approval-channel codex` adds `cleanup_request_approval`: a complete
  read-only list and a default-negative host form, with durable request/task
  lookup after disconnection. The existing terminal workflow remains available.
- Dry-run reports current admission and required approval separately from
  the policy decision, with the supported next operation.
- Plans, tasks and execution receipts share readable summaries and bounded
  detail views. Default text status shows a read-only overview.
- `--quarantine` can preserve regenerable artifacts too. The choice is bound
  to the saved plan; it never falls back to direct deletion after a refusal.
- The Skill installer links a complete installation, locates Core through
  the real script path and retains a replaced Skill outside host discovery.
  MCP can use an operator-selected fixed quarantine location.
- CLI summaries handle legacy output encodings without turning a completed
  recovery into an encoding failure.

For targets already confirmed obsolete:

```sh
agent-guard-cleanup --targets-file /absolute/path/to/targets.json --discard-targets --save-plan --json
# Operator terminal: review the selection and type APPROVE yourself.
agent-guard-cleanup --from-plan '<saved-plan-id>' --approve --json
# Agent: execute once, then query the actual task state.
agent-guard-cleanup --task '<task-id>' --execute --json
agent-guard-cleanup --task '<task-id>' --json
```

Saving is not approval. Approval expires after ten minutes and does not lift
protection or admission limits. After a partial result or missing response,
query the task and recovery transaction before deciding what remains.

## Upgrade

Keep doctor, MCP server, CLI and linked Skill on the same reviewed installation.
Version 0.2.9 exposes seven MCP tools; 0.3.0 exposes ten by default, or eleven
with the operator-enabled Codex channel. Reconnect the client
after upgrading, inspect the actual tool list and keep unfinished approved
tasks on their original installation/workspace/quarantine/session binding.

Use the [Skill installer](../guides/skill-installation.md) to preview and
apply an explicitly selected Skill link. It does not configure MCP or hooks.
Older doctor baselines may report drift after the upgrade and need review.
Existing recovery records, budgets and history are not reset or migrated;
selecting another bucket does not merge them. Core/CLI requires Python 3.9+
and Git; the optional MCP service retains Python 3.10+ and `mcp==2.3.0`.
No production dependency was added.

Before registry installation, verify the exact
[GitHub Release](https://github.com/mokuyoaxis/agent-guard/releases/tag/0.3.0)
and [npm version](https://www.npmjs.com/package/@mokuyoaxis/agent-guard/v/0.3.0).
These identify the intended version; local metadata or an archive does not
establish publication. Until those channels are available, use reviewed
source or a checked local package.

## Validation and scope

The 2026-11-15 host-confirmation extension has separate
[protocol and operator acceptance](../reports/test-report-maintenance-0.3.0.md#host-confirmation-2026-11-15).
Earlier full-suite, archive, CI and model-host records below predate that
extension; they do not certify its new runtime bytes. A host form is an
operator-selected trusted channel, not independent human attestation; see
[setup and limits](../guides/mcp.md#host-confirmation-030-development).

The operator-driven Codex 0.165.0 form trial completed one confirmation and
four MCP calls for a 71-root quarantine/restore task. Independent checks
matched all 1006 payload files and both keepers after recovery. The updated
optional-SDK CI selection passed 100 tests locally, including nine approval
protocol cases; eight release-gate regressions also passed. Model-led use of
the new approval channel remains unverified, and commit-level CI is separate.

Codex 0.165.0, Kimi Code 2.3.0 and DSH 0.2.0-rc.2 each first executed a new
operator-approved 71-root / 1061-entry task through MCP. Each used five model
MCP calls and one execution dispatch. The controller independently checked
all 1006 quarantine payload files and both keepers, then restored the data
through MCP; budget charges remained. Codex also passed a real-cache task.
These runtime trials used development archives with 0.2.9 metadata; current
candidate version/package checks are separate.

At the 2026-11-14 alignment checkpoint, the candidate passed 54 package/release/MCP tests and the
actual pinned-SDK coverage preflight. Its 172-file local archive installed
offline; all 13 CLI help entrypoints, linked Skill/Core/Cleanup and a
ten-tool doctor reporting version 0.3.0 passed. Those runtime payload files matched
the earlier tested development package apart from version metadata. These
checks are separate from a full release-workflow run.

The runtime at `f6930fa` passed all 15 development-CI jobs after Windows
encoding and macOS fixture corrections. That result predates the candidate
version alignment. The release commit is checked by its own CI and release
gate. First failures and their corrections remain in the
[maintenance acceptance](../reports/test-report-maintenance-0.3.0.md).

Claude Code connected and discovered the tools; model-backed maintenance is
deferred. MCP operations do not certify native shell/editor interception.
The PRoot native sandbox limitation and each hook's recorded coverage remain
separate. Restore is transaction-level, quarantine still occupies storage,
and this release adds no automatic GC or arbitrary-file restore within a
multi-file transaction. GUI/TUI panels and commercial retention/legal-hold
features are outside this version.
