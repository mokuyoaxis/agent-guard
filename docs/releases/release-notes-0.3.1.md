# 0.3.1 — Cleanup review, cross-filesystem recovery and workspace limit settings

Cleaner operator review and recovery handoff for Delete-guard, staged
cross-filesystem quarantine copies, workspace limit settings via CLI, and
extended Skill installation.

本版围绕 Delete-guard 日常维护的可靠性与交互可读性收口：重构终端清理确认屏与
CLI 交接、修正跨文件系统隔离副本的暂存与恢复判定、提供工作区额度设置 CLI，并
扩展 Skill 软链接安装器。既有策略边界、恢复格式与手动 GC 契约保持不变。

## Changes

- **Cleanup terminal review:** Terminal review lists permanent deletions first,
  followed by moves to quarantine, with readable Unicode paths (control and
  formatting characters escaped) and both human-readable sizes and exact logical
  byte counts. Selections with more than 20 roots open with a top-level
  directory summary and a complete owner-read-only review file
  (`.agent-guard/cleanup-reviews/`, or a private temporary directory outside
  the workspace for full-workspace execution), then require pressing Enter to
  display every selected row before typing `APPROVE` or `EXECUTE`.
  See [Cleanup review](../guides/cleanup-plan.md#operator-approved-tasks-030-development).
- **Unique ID prefixes and multi-target dry-run handoff:** CLI `--from-plan`,
  `--task` and `gc.py --txid` accept unique ID prefixes (exact IDs take
  precedence; ambiguous prefixes fail closed; files, JSON output and MCP calls
  retain full IDs). Multi-target `safe_delete --dry-run` batch refusals point to
  MCP `cleanup_plan(save=true)` or `cleanup.py --targets-file` and supply the
  literal path array in `admission.next_step.targets_file.contents`.
  See [Admission guidance](../guides/deletion-limits.md#dry-run-admission-preview-030-development).
- **Direct transaction and task GC guidance:** Single-transaction relocation
  receipts carry the exact `--txid` in their GC preview and purge commands.
  `gc.py --task ID` previews the completed quarantine receipts belonging to a
  Cleanup task; add `--execute` after review to purge them with existing
  validation and audit records. Unresolved tasks or incomplete quarantine
  receipts still require inspection before explicit selection.
  See [Recovery handoff](../guides/recovery-feedback.md#moving-a-file-does-not-remove-its-recovery-copy).
- **Cross-filesystem recovery hardening:** Cross-filesystem (`EXDEV`)
  relocations stage copies on the destination filesystem and atomically rename
  them into place, recording the confirmed quarantine copy before clearing the
  source. Failed relocations without a completion record are excluded from
  `restorable_items`, surfaced as `manual_review_items` across `restore.py list`,
  `status` and MCP queries, and refused by ordinary restore when an unconfirmed
  copy remains.
  See [Cross-filesystem recovery](unreleased.md#delete-guard-cross-filesystem-recovery).
- **Workspace limit settings CLI:** Added `agent-guard-delete-policy`
  (`skills/delete-guard/scripts/delete_policy.py`) to inspect effective limits
  and defaults (`show`), preview or apply workspace overrides (`set` with units
  such as `2GiB`), and restore numeric defaults (`reset-limits`) while keeping
  `protected_paths`, session usage and recovery records intact.
  See [Workspace limits](../guides/deletion-limits.md#configure-workspace-limits).
- **Extended Skill installer and guidance alignment:** `install_skill.py` now
  also supports linking `--skill exfil-guard` and `--skill recovery-audit` with
  the same preview/backup contract, and `skills/exfil-guard/scripts/_bootstrap.py`
  resolves Core through its real path. Dry-run explanations use conditional
  phrasing, saved-plan refresh and restore-budget guidance are aligned, public
  documentation is streamlined, and release automation pins tag-specific note
  links and runs bounded post-publish verification.
  See [Skill installation](../guides/skill-installation.md) and
  [Release workflow](../guides/release-process.md).

## Upgrade

Check the exact [GitHub Release](https://github.com/mokuyoaxis/agent-guard/releases/tag/0.3.1)
and [npm version](https://www.npmjs.com/package/@mokuyoaxis/agent-guard/v/0.3.1)
before registry installation. If unavailable, use a reviewed checkout or a
checked local archive.

Keep doctor, CLI, MCP server and linked Skills on the same installation.
Version `0.3.1` retains the ten default MCP tools (eleven with the opt-in Codex
confirmation channel) and expands the npm CLI entrypoints to 14 with
`agent-guard-delete-policy`. Existing recovery manifests, deletion policy files
and session accounting require no migration. Complete outstanding approved tasks
under their original package, workspace, quarantine and session binding; older
doctor fingerprints may show `DRIFTED` after upgrading and should be reviewed
before updating a baseline.

Core/CLI requires Python 3.9+ and Git. Optional MCP retains Python 3.10+ and
`mcp==2.3.0`. No new production dependency is introduced.

## Validation and scope

This release builds on the `0.3.0` host acceptance baseline recorded in
[maintenance acceptance](../reports/test-report-maintenance-0.3.0.md). Local
and CI validation for `0.3.1` adds cross-filesystem staging/recovery fault
injection and real cross-mount roundtrip checks, Cleanup review formatting,
unique ID prefix and task-GC regressions, real terminal confirmation and task-GC
end-to-end verification in owned fixtures, workspace limit CLI tests, and
three-Skill installer regressions. Follow
[development CI](https://github.com/mokuyoaxis/agent-guard/actions/workflows/ci.yml)
for the exact source commit. The tag-triggered release workflow runs the pinned
MCP SDK gate and full suite before packing one archive for GitHub Releases and
npm (with provenance) and running independent read-only publication checks.

Claude Code model-backed maintenance remains deferred, and explicit MCP tools
remain separate from native shell/editor interception and OS sandboxing; see
the [capability matrix](../guides/harness-capabilities.md). Quarantine copies
still occupy disk space, restore remains transaction-level, and GC remains an
explicit manual action with no automatic purge, GUI panel or commercial
retention/legal-hold features.
