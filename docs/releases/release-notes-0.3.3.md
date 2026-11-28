# 0.3.3 — Exfil-guard safe read & guarded config edit, Delete-guard on-demand storage measurement & selective restore

Zero-dependency deterministic Core release expanding the trusted-side Exfil-guard
and Delete-guard workflows: bounded whole-file redacted reads (`safe_read` CLI
and 15th default MCP tool), policy-allowlisted local scalar config editing
(`agent-guard-config-edit`), read-only on-demand quarantine storage measurement
(`status --measure-storage`), and transaction-reconciled selective restore
(`restore --path` / MCP `paths`), with cross-platform workspace doctor policy
checks and zero storage migration. This release also includes the **Nov 28
Anniversary Special Edition** M + iris emblem in the READMEs (strictly limited
to `2026-11-28` and `Agent Guard 0.3.3`).

本版在零新增生产依赖、不迁移既有存储格式的前提下，同步扩展 **Exfil-guard** 与 **Delete-guard** 的可信侧日常工作流：**Exfil-guard** 新增脱敏分页只读入口 `safe_read`（CLI `agent-guard-safe-read` 与第 15 项默认 MCP 工具）以及基于独立白名单策略的受控本地标量配置编辑 CLI（`agent-guard-config-edit`，覆盖 JSON 与 Node 22 对齐的单行 dotenv 子集，并接入跨平台 `agent-guard-doctor workspace` 只读核验）；**Delete-guard** 新增按需只读隔离区空间计量（`status --view overview --measure-storage` 与 MCP `status` 同源参数，支持 Top-10 事务占用排行与部分结果保护）以及跨历史回执归并的事务状态与选择性按路径恢复（CLI `--path` 与 MCP `paths`）。本次同时在双语 README 收录 **11.28 纪念日特别版** M 与鸢尾花徽标（仅限 `2026-11-28` 与 `Agent Guard 0.3.3`）。既有核心边界、恢复格式与手动 GC 契约保持不变。

## Changes

### Exfil-guard

- **Bounded redacted read (`safe_read` / `agent-guard-safe-read`):** Adds a
  workspace-relative POSIX descriptor-based text reader (`core/safe_read.py`,
  CLI `skills/exfil-guard/scripts/safe_read.py`, MCP tool `safe_read`, schema
  `safe_read/v1`) that reads up to 256 KiB without following symlinks, checks
  the first 8 KiB for NUL bytes, requires strict UTF-8, and verifies before and
  after `fstat` identity. Known secret-store filenames, case-insensitive
  control directories (`.git`, `.agent-guard`, `.agent-trash`), and both
  default and custom quarantine roots are refused before opening. Text goes
  through whole-file `llm-request` scanning/redaction plus a dual re-scan
  including an empty-exemption final check (`RESIDUAL_SECRET_BLOCKED` /
  `RESIDUAL_PATH_BLOCKED`), returns whole-line pages (max 200 lines / 32 KiB
  per page; `LINE_TOO_LONG` for oversized lines), supports `--strict-paths`,
  `--fail-on-redaction`, and opt-in page-scoped audit (`tool: "exfil_safe_read"`;
  MCP `--audit-exfil` sets `readOnlyHint: false`), and reports
  `UNSUPPORTED_PLATFORM` on Windows.
  See [Exfil-guard Skill](../../skills/exfil-guard/SKILL.md#safe-redacted-read),
  [MCP Exfil tools](../guides/mcp.md#exfil-guard-tools), and
  [Opt-in Exfil audit](../guides/audit-metadata.md#opt-in-exfil-audit).
- **Minimal allowlisted config editor (`agent-guard-config-edit`):** Adds an
  operator-only local CLI (`core/config_edit.py`,
  `skills/exfil-guard/scripts/edit_config.py`, schema `config_edit/v1`; not
  exposed as an MCP write tool) for updating existing non-sensitive scalar
  values in workspace JSON (RFC 6901 JSON Pointer object navigation) and a
  Node 22-aligned single-line dotenv subset. Edits are default-deny and require
  an operator-maintained `.agent-guard/config-edit-policy.json` (single-link
  regular file ≤1 MiB, strict literal `version: 1`, explicit format and
  `editable_keys`), enforce a 20-term sensitive-key ban and two-layer new-value
  rejection (empty-exemption `file-write` scan plus placeholder/NUL ban), and
  report only `before_type` and `before_state` (`set`/`empty`) with no raw
  values, `changed` boolean, byte offsets, or fingerprints. `--preview` is
  read-only by default; `--apply` requires dual interactive TTY (`stdin` and
  `stderr`) with exact `APPLY`, acquires `.agent-guard/config-edit.lock`,
  re-verifies policy, live quarantine boundary, parent chain, and target
  identity before atomic `os.replace(..., src_dir_fd, dst_dir_fd)` with
  surgical byte-span replacement and full pre-write re-parse verification,
  preserves owner/group, normal mode bits, and matching inherited
  `security.selinux` labels (refusing special mode bits, ACLs, custom xattrs,
  or xattr-uninspectable filesystems as `UNSUPPORTED_FILE_METADATA`), reports
  explicit `replaced`/`durability_confirmed` (`DIR_FSYNC_UNCONFIRMED` exits `1`),
  and writes post-`APPLY` metadata-only audit (`tool: "exfil_config_edit"`).
  Preview and apply require Linux with descriptor operations and
  `os.listxattr`/`os.getxattr` (`UNSUPPORTED_PLATFORM` on macOS and Windows).
  See [Exfil-guard Skill](../../skills/exfil-guard/SKILL.md#guarded-config-editing)
  and [Opt-in Exfil audit](../guides/audit-metadata.md#opt-in-exfil-audit).

### Delete-guard

- **On-demand quarantine storage measurement (`status --measure-storage`):**
  Extends `status.py --view overview` and MCP `status(view="overview")` with
  opt-in `--measure-storage` / `measure_storage: true` and
  `--max-measure-entries` / `max_measure_entries` (default `50,000`, cap
  `200,000`, depth limit `64`). Default overviews remain strictly zero-traversal
  (`measured: false`, `logical_bytes: null`), and MCP `view="full"` rejects
  explicit `measure_storage` parameters. Only a missing quarantine root
  (`lstat` `FileNotFoundError`) returns complete zero occupancy; unreadable
  roots, non-directories, or dangling links return partial results. Measurement
  matches Core `admission.py` logical byte and entry accounting using POSIX
  `O_NOFOLLOW`/`dir_fd`/`fstat` open-time verification and a Windows
  junction-aware fallback, streaming every attempted entry (including depth
  probes) against a single global budget with staged subtree rollback on
  concurrent change or truncation. Partial scans report `observed_logical_bytes`,
  `observed_entries`, and `issues` while keeping `logical_bytes: null`.
  `admission_ratio` requires complete measurement plus valid limits, and Top-10
  `top_transactions` requires complete measurement plus verified manifest
  ownership evidence (unpurged direct entity directories sorted by bytes
  descending, entries descending, `txid` ascending; directories lacking manifest
  evidence return `null` rather than `[]`). Entirely read-only.
  See [Unified read-only queries](../guides/read-only-query.md#on-demand-storage-measurement)
  and [MCP tools](../guides/mcp.md#tools-and-limits).
- **Selective restore and transaction state reconciliation:** Reconciles
  per-item transaction state across all confirmed historical restore receipts
  (including `relocate-intent` copies and applied Git stashes), preventing a
  partial restore from marking a transaction `RESTORED` while
  `restorable_items > 0`. Adds selective path restoration via
  `restore.py preview|restore <txid> --path PATH ...` and MCP
  `restore_preview` / `restore` `paths: [...]` (MCP continues to require exact
  complete `txid`s). Both CLI and MCP share a read-only selection preflight
  that refuses the whole batch before mutating on missing, ambiguous,
  already-restored, or manual-review selections; unfiltered restore preserves
  `PARTIAL` and `manual_review_items` handling and resolves Git snapshots
  against the bound workspace. Cleans empty parent directories under quarantine
  left by successful moves and records `selective` and `selected_count` in
  restore audit metadata.
  See [Selective restore](../guides/recovery-feedback.md#selective-restore-and-state-reconciliation)
  and [Delete-guard Skill](../../skills/delete-guard/SKILL.md#recovery).

### Workspace doctor, Skill installation and package alignment

- **Cross-platform config-edit policy diagnostics:** `agent-guard-doctor
  workspace` adds a read-only `checks.config_edit_policy` check
  (`NOT_CONFIGURED` / `PASS` / `FAIL`) validating
  `.agent-guard/config-edit-policy.json` across Linux, macOS, and Windows while
  keeping `exfil_allow` checks independent.
  See [Workspace doctor](../guides/host-drift.md#workspace-doctor).
- **Skill installer, MCP doctor and npm CLI entrypoints:** `install_skill.py`
  validates all **7** `exfil-guard` entrypoints (`check_span`, `check_export`,
  `check_bash_exfil`, `safe_read`, `sanitize`, `view`, `edit_config`).
  `doctor mcp` checks **15 default tools** (**16** with
  `--approval-channel codex`), including `safe_read`. `package.json` exposes
  **17** npm CLI entrypoints (`+agent-guard-safe-read`,
  `+agent-guard-config-edit`).
  See [Skill installation](../guides/skill-installation.md) and
  [MCP doctor](../guides/mcp.md#diagnose-a-local-service-028).

## Upgrade

Check the exact [GitHub Release](https://github.com/mokuyoaxis/agent-guard/releases/tag/0.3.3)
and [npm version](https://www.npmjs.com/package/@mokuyoaxis/agent-guard/v/0.3.3)
before registry installation. If unavailable, use a reviewed checkout or a
checked local archive.

Keep doctor, CLI, MCP server and linked Skills on the same installation.
Version `0.3.3` expands the local MCP server from 14 to **15 default tools**
(from 15 to **16** with `--approval-channel codex`) by adding `safe_read`,
extends `status(view="overview")` with opt-in storage measurement and
`restore`/`restore_preview` with selective `paths`, and adds
`agent-guard-safe-read` and `agent-guard-config-edit` for **17 npm CLI
entrypoints** (`exfil-guard` Skill expands from 5 to **7** validated
entrypoints). Existing recovery manifests, deletion policy files, Exfil
exemptions, and session accounting require no migration. Complete outstanding
approved Cleanup tasks under their original package, workspace, quarantine and
session binding; older doctor baselines may report `DRIFTED` after upgrading
and should be reviewed before writing a new baseline.

Core/CLI requires Python 3.9+ and Git. Optional MCP retains Python 3.10+ and
`mcp==2.3.0`. TOML `safe_view` and Kimi TOML doctor helpers require Python
3.11+. Platform boundaries for file-descriptor / xattr features are explicit:
`agent-guard-exfil-export` and `agent-guard-safe-read` / MCP `safe_read`
require POSIX file-descriptor operations (`UNSUPPORTED_PLATFORM` on Windows);
`agent-guard-config-edit` preview and apply require Linux with descriptor
operations and `os.listxattr`/`os.getxattr` (`UNSUPPORTED_PLATFORM` on macOS
and Windows); `status --measure-storage`, selective `restore`, and
`agent-guard-doctor workspace` (`checks.config_edit_policy`) run cross-platform.
No new production dependency is introduced.

## Validation and scope

This release builds on the `0.3.0` host acceptance baseline recorded in
[maintenance acceptance](../reports/test-report-maintenance-0.3.0.md). Local
and CI validation across the `0.3.3` milestones covers bounded `safe_read`
redaction, dual re-scan, pagination, and real MCP stdio contracts (including
Windows `UNSUPPORTED_PLATFORM` assertions); on-demand quarantine storage
measurement across POSIX `dir_fd` and Windows junction-aware fallback with
CLI/MCP parity and read-only checks; multi-receipt transaction state
reconciliation and selective restore across CLI, MCP stdio, and native Windows
Core tests; `agent-guard-config-edit` policy validation, Node 22 dotenv
parity, surgical replacement, SELinux/xattr metadata checks, atomic
re-verification, post-`APPLY` audit, macOS/Windows `UNSUPPORTED_PLATFORM`
gates, and cross-platform `doctor workspace` checks; plus package/Skill
entrypoint and release-note link checks. Follow
[development CI](https://github.com/mokuyoaxis/agent-guard/actions/workflows/ci.yml)
for exact commit-level Linux, macOS and Windows Core RC runs. The tag-triggered
release workflow runs the pinned MCP SDK gate and full suite before packing one
archive for GitHub Releases and npm (with provenance) and running independent
read-only publication checks.

`agent-guard-lab run --case lifecycle-calibration` remains at its 11-pair (22
check; 10 supported pairs on Windows) `0.3.2` L0 calibration baseline unless
expanded in a separate update. Claude Code model-backed maintenance remains
deferred, Kimi's standalone Lab capture helper remains pinned to `2.1.1`, and
explicit MCP tools remain separate from native shell/editor interception and OS
sandboxing; see the [capability matrix](../guides/harness-capabilities.md).
`agent-guard-config-edit` is an operator-confirmed local CLI rather than an
autonomous agent write path or MCP tool. Quarantine copies still occupy disk
space until manual GC runs, and GC remains an explicit manual action with no
automatic purge, GUI panel, or commercial retention/legal-hold features.
