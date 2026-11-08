# 0.2.9 — Cleanup inventory and faster scans

Cleanup Copilot can now show a bounded directory overview before a detailed
scan. Pick the directories that matter, measure those scopes, then decide
whether to prepare a cleanup plan. Both steps are available through CLI and MCP.

本版主要改善清理前的盘点：先看目录概况，再计量选定范围。既有计划扫描也做了提速，
删除仍按原来的计划、确认和 safe_delete 流程执行。

## Changes

- `agent-guard-cleanup --discover` groups directory hints and reports areas
  left unexpanded. `--measure --scope PATH` measures selected directories.
- MCP adds the read-only `cleanup_discover` and `cleanup_measure` tools.
  Partial observations remain usable; incomplete coverage and unknown whole
  sizes are explicit in the result.
- Plan scans reuse ordinary-file metadata and batch Git queries instead of
  repeating them for every candidate.
- The normal-corpus regression test uses Git-index paths and current file
  contents. Private untracked experiments no longer enter that metric;
  tracked and newly staged unsafe content still fails it.

```sh
agent-guard-cleanup --discover --json
agent-guard-cleanup --measure --scope build --json
```

Inventory results cannot authorize deletion. Cache labels are hints, and
protected or unmeasured whole sizes stay unknown. Scanning does not coordinate
application cache locks. See the
[Cleanup guide](https://github.com/mokuyoaxis/agent-guard/blob/0.2.9/docs/guides/cleanup-plan.md#directory-overview-and-measurement-029)
and [MCP guide](https://github.com/mokuyoaxis/agent-guard/blob/0.2.9/docs/guides/mcp.md#cleanup-observations-029).

## Upgrade

Install the exact version after checking its
[GitHub release](https://github.com/mokuyoaxis/agent-guard/releases/tag/0.2.9)
and [npm page](https://www.npmjs.com/package/@mokuyoaxis/agent-guard/v/0.2.9):

```sh
npm install --prefix /absolute/path/to/agent-guard-install @mokuyoaxis/agent-guard@0.2.9
```

Upgrade doctor and server together: 0.2.9 expects seven MCP tools; 0.2.8
expects five. Older doctor fingerprint baselines may report `DRIFTED` after
the upgrade and need review. The optional `mcp==2.3.0` SDK remains a separate
installation; Core/CLI still needs Python 3.9+ and Git.

The maintainer selected 0.2.9 as a
[bounded maintenance exception](https://github.com/mokuyoaxis/agent-guard/blob/0.2.9/docs/design/compatibility.md#029-cleanup-maintenance-decision)
for these additions. The original five MCP operations, deletion rules,
budgets and recovery formats retain their contracts. There is no data or
configuration migration. The separate 0.3.0 research plan stays paused.

## Validation

The 0.2.9 public candidate passed `npm test`: 1197 Python cases and both Node
groups, with the optional MCP SDK enabled and no failures, errors or skips.
All 16 real MCP protocol, doctor and interruption cases executed. Remote CI
and publication are checked separately through the existing release workflow.

Earlier local checks also covered real-project stdio inventory and one Kimi
Code task in a synthetic workspace. That single task used three MCP calls;
it does not establish general host reliability, native shell interception or
application-cache deletion safety.
