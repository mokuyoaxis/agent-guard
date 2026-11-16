# 0.3.0 — Reviewed cleanup tasks

Save a cleanup selection, review and approve it once, then let the agent
execute and query the approved task through CLI or MCP.

本版重点是把清理接成一项完整任务：保存明确目标，由操作者审阅批准一次，
Agent 随后执行、查状态并交接结果。无需手写 Core 调用，也不必反复录入同一批目标。
Cleanup Copilot 仍属于 Delete-guard；三大功能的归属保持不变。

0.2.x already provided recoverable deletion, Cleanup plans and MCP tools.
This release adds the saved selection and task handoff between review,
approval and the agent's later execution.

## Changes

- Save literal targets or a JSON target list. Continue by saved plan ID,
  then by task ID after approval.
- MCP adds `cleanup_plan`, `cleanup_execute` and `cleanup_task_status`,
  for ten tools by default. The operator-enabled Codex channel adds
  `cleanup_request_approval`, a complete read-only list and a native form.
  Terminal approval remains the default.
- Dry-run separates the current admission result from the policy decision
  and shows the required approval and next operation.
- Plans, tasks and receipts share readable summaries, bounded detail views
  and explicit partial/unknown states. Status text has a read-only overview.
- `--quarantine` can preserve regenerable artifacts too. The saved plan and
  approval retain that choice; a refusal never falls back to direct deletion.
- The Skill installer links a complete installation and retains a replaced
  Skill. Scripts locate Core through their real path. MCP can use an
  operator-selected fixed quarantine location.
- CLI summaries handle legacy encodings without turning a completed
  recovery into an output-encoding failure.

## Saved task example

For targets confirmed obsolete, with recovery required:

```sh
agent-guard-cleanup --targets-file /absolute/path/to/targets.json --discard-targets --quarantine --save-plan --json
# Operator terminal: review the complete selection and type APPROVE yourself.
agent-guard-cleanup --from-plan '<saved-plan-id>' --approve --json
# Agent: execute once, then query the task.
agent-guard-cleanup --task '<task-id>' --execute --json
agent-guard-cleanup --task '<task-id>' --json
```

Operators using the supported Codex native form can enable
[`--approval-channel codex`](../guides/mcp.md#host-confirmation-030-development)
at server startup. The agent requests confirmation for the saved ID; only
the operator's explicit decision and review declaration can issue the task.
Agents must not answer on the operator's behalf.

Saving is not approval. Approval expires after ten minutes and retains
protection, budgets, capacity and rate checks. After a partial result or
missing response, query the existing task and recovery transaction before
deciding what remains.

## Upgrade

Check the exact [GitHub Release](https://github.com/mokuyoaxis/agent-guard/releases/tag/0.3.0)
and [npm version](https://www.npmjs.com/package/@mokuyoaxis/agent-guard/v/0.3.0)
before registry installation. If unavailable, use a reviewed checkout or
checked local archive.

Keep doctor, CLI, MCP server and linked Skill on the same installation.
Version 0.2.9 exposes seven MCP tools; 0.3.0 exposes ten by default or eleven
with the Codex channel. Reconnect the client after upgrading and check the
actual tool list. Old doctor fingerprints may need review.

Use the [Skill installer](../guides/skill-installation.md) to preview and
apply a selected link. It does not configure MCP or hooks. Keep unfinished
tasks on their original installation, workspace, quarantine and session
binding. Recovery records and budgets are not reset or migrated; another
bucket does not merge their history.

Core/CLI requires Python 3.9+ and Git. Optional MCP retains Python 3.10+
and `mcp==2.3.0`. No production dependency was added.

## Validation and scope

Codex, DSH and Kimi completed bounded, operator-approved 71-root MCP tasks
with independent quarantine checks and recovery. Codex also passed a real
cache task. The optional Codex form then passed a real-model trial:
seven model MCP calls, one native confirmation and one execution.
All 1006 payload files were independently verified and restored; both
keepers stayed unchanged. Task, transaction and budget queries agreed.

See the [maintenance acceptance](../reports/test-report-maintenance-0.3.0.md)
for exact versions, automated checks, retained failures and package
checkpoints. Follow [development CI](https://github.com/mokuyoaxis/agent-guard/actions/workflows/ci.yml)
for the submitted commit. The tag-triggered release gate separately runs
the pinned-SDK checks and full suite before publishing the same archive
to GitHub and npm.

Claude Code connected and discovered tools; model-backed maintenance
remains deferred. Explicit MCP operations retain their own scope, separate
from native shell/editor interception and the PRoot sandbox limitation.
The [capability matrix](../guides/harness-capabilities.md) records each
tested path.

Quarantine retains data on disk. Restore is transaction-level and GC is
manual. This version adds no automatic GC, arbitrary-file restore within
a multi-file transaction, GUI panel or commercial retention/legal-hold.
