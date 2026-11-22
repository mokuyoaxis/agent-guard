# 0.3.2 — Exfil-guard hardening, Delete-guard policy & restore previews, L0 lifecycle calibration and workspace doctor

Zero-dependency deterministic Core hardening across all three pillars: expanded
Exfil-guard channels, credential rules and export/Bash preflights; controlled
Delete-guard workspace policies, read-only restore previews and inline-script
deletion blocks; L0 paired lifecycle calibration and read-only workspace
diagnostics with Unicode carrier-smuggling checks.

本版围绕零依赖确定性 Core 同步补强三大模块：**Exfil-guard** 新增 TOML 无标量配置视图、无原文外发审计、3 项 MCP 工具、本地 Git Hook、八类现代凭据检测、导出预检 CLI（`agent-guard-exfil-export`）与 opt-in Bash 外发预检；**Delete-guard** 补齐保护路径与受控可再生模式 CLI、只读恢复预览（含 MCP `restore_preview`）、内联脚本删除双重锚定拦截及 Windows PowerShell 交接命令格式化；**Guard-lab 与 Doctor** 新增零模型成对生命周期校准套件与只读工作区体检（含白名单持久载体不可见 Unicode 走私检测），并同步 `exfil-guard` Skill 安装清单。既有恢复格式、核心边界与手动 GC 契约保持不变。

## Changes

### Exfil-guard

- **TOML safe config view and opt-in metadata-only audit:** `view.py`
  (`agent-guard-config-view`) inspects TOML structure without scalar values on
  Python 3.11+ (older runtimes report `UNSUPPORTED_RUNTIME` before opening TOML
  inputs). `--audit` / `AGENT_GUARD_EXFIL_AUDIT=1` (and MCP `--audit-exfil`)
  record metadata-only egress summaries and spans without matched bytes, paths
  or secret hashes. `sanitize.py --json` returns structured decisions, plans
  and audit receipts while withholding text and plans on `ASK`/`BLOCK`.
  See [Exfil-guard Skill](../../skills/exfil-guard/SKILL.md#safe-config-view) and
  [Opt-in Exfil audit](../guides/audit-metadata.md#opt-in-exfil-audit).
- **MCP Exfil tools and opt-in local Git hooks:** The local stdio MCP server
  adds `config_safe_view`, `exfil_check` and `exfil_sanitize`, and `doctor mcp`
  adds a benign Exfil read-only probe. Git repositories can separately enable
  local `pre-commit` (staged UTF-8 blobs) and `commit-msg` hooks via
  `adapters/git/hook.py`.
  See [MCP Exfil tools](../guides/mcp.md#exfil-guard-tools) and
  [Git hooks](../../adapters/git/README.md).
- **Eight modern credential rules, export preflight and opt-in Bash checks:**
  Adds T1 detectors for npm, Google API, Hugging Face, SendGrid, HashiCorp
  Vault, DigitalOcean, Perplexity and structurally valid HTTP Basic tokens.
  Adds `agent-guard-exfil-export` (`skills/exfil-guard/scripts/check_export.py`)
  for bounded POSIX descriptor-walk export preflights before packaging or
  sharing files. Adds default-off credential and secret-source checks on native
  Bash events in Claude Code, Kimi Code (`AGENT_GUARD_EXFIL_BASH=1`) and DSH
  (`exfilBashGuard: true`), evaluated before Delete-guard compensation.
  See [Rule catalogue](../../skills/exfil-guard/references/rules.md#export-preflight)
  and [Opt-in Bash Exfil](../guides/harness-capabilities.md#opt-in-bash-exfil).

### Delete-guard

- **Protected paths CLI and guarded workspace `regenerable_patterns`:**
  `agent-guard-delete-policy` adds `protect add|remove` (refusing `.`, `.git`,
  `.agent-guard` and `.agent-trash` to prevent self-lockout) and
  `regenerable add|remove` with `--preview` and interactive terminal `APPLY`
  confirmation. Custom patterns enforce four guardrails (strict POSIX
  segment-by-segment matching without `**` or broad/secret-store globs,
  Git-ignored and unprotected checks, whole-tree no-follow inspection shared
  between Core and Cleanup that falls back to quarantine if a directory holds a
  secret-store descendant or cannot be completely scanned, and `--quarantine`
  precedence). Policy edits change the config fingerprint (`CONFIG_CHANGED`),
  while `reset-limits` preserves both lists.
  See [Workspace policy settings](../guides/deletion-limits.md#configure-workspace-limits).
- **Read-only restore preview and MCP `restore_preview`:** Adds zero-write
  `restore.py preview <txid>` (and `--preview`) with unique CLI transaction
  prefixes, bounded 4 MiB no-follow SHA-256 boolean content equality on
  occupied regular files (never emitting file bytes or digests), explicit
  `blocked` status without `--force` when an origin ancestor directory is no
  longer a readable directory, and the read-only MCP tool `restore_preview`.
  Both MCP `restore` and `restore_preview` require exact complete transaction
  IDs.
  See [Restore preview](../guides/recovery-feedback.md#read-only-restore-preview)
  and [MCP tools](../guides/mcp.md#tools-and-limits).
- **Inline script deletion blocks and Windows PowerShell handoff:** Core and
  DSH prefilters block inline script deletion calls (`python -c`, `node -e`,
  `bun -e`, `deno eval`, `ruby -e`, `perl -e`, interpreter heredocs and piped
  scripts invoking file-deletion APIs) as `BLOCK_UNDETERMINABLE_EFFECT`, while
  masking ordinary string literals and ignoring Deno data arguments to avoid
  blocking benign scripts or `cat`/`tee` heredocs. On Windows, CLI handoff
  commands render PowerShell single-quoted invocations (escaping ASCII and
  typographic single quotes) with single-invocation `try`/`finally` environment
  scoping, verified with an echo probe in the Windows Core CI gate.
  See [Delete-guard Skill](../../skills/delete-guard/SKILL.md#if-you-run-rm--destructive-git-anyway)
  and [Recovery handoff](../guides/recovery-feedback.md#moving-a-file-does-not-remove-its-recovery-copy).

### Guard-lab, workspace doctor and Skill installation

- **L0 paired lifecycle calibration (`agent-guard-lab run --case lifecycle-calibration`):**
  Runs 11 paired checks (22 checks across configuration, hard boundaries and
  inline scripts, Cleanup approval drift, recovery preview/conflicts and
  Exfil/export/Bash preflights) in an isolated disposable worker with zero
  model calls and zero retained fixture values. Reports count protective block
  cases and benign allow cases separately, expose read-only `report` retrieval,
  and report the export pair as `UNSUPPORTED_PLATFORM` when the required
  file-descriptor operations are unavailable. Windows runs 10 supported pairs.
  See [Lifecycle calibration](../lab/guard-lab.md#lifecycle-calibration).
- **Read-only workspace doctor (`agent-guard-doctor workspace`):** Checks
  `.agent-guard/delete-policy.json`, effective Exfil exemptions, Skill links
  (local and explicit `--skill-dir`), active Git Hook wrappers (supporting
  `--strict-paths` and `--audit`), and persistent instruction carriers against
  invisible Unicode Tags, BiDi overrides/unpaired isolates, non-CJK
  supplemental variation selectors and invisible-character runs using bundled
  Unicode 17.0 and Adobe-Japan1 sequence tables (51,667 bytes compressed,
  `Unicode-3.0` license). `WARN` exits `0` by default and `1` with `--strict`.
  `FAIL` exits `1`. The report sets `host_interception` and `hook_enforcement`
  to `UNVERIFIED`.
  See [Workspace doctor](../guides/host-drift.md#workspace-doctor) and
  [Unicode data provenance](../../assets/workspace-doctor-unicode-LICENSE.txt).
- **Skill installer alignment:** `install_skill.py` now checks and reports
  `scripts/check_export.py` and `scripts/check_bash_exfil.py` alongside the
  existing `exfil-guard` entrypoints, verified through symlinked real-path
  invocation. The separate Kimi `2.1.1` Lab capture helper retains its pinned
  version and historical scope unchanged.
  See [Skill installation](../guides/skill-installation.md).

## Upgrade

Check the exact [GitHub Release](https://github.com/mokuyoaxis/agent-guard/releases/tag/0.3.2)
and [npm version](https://www.npmjs.com/package/@mokuyoaxis/agent-guard/v/0.3.2)
before registry installation. If unavailable, use a reviewed checkout or a
checked local archive.

Keep doctor, CLI, MCP server and linked Skills on the same installation.
Version `0.3.2` expands the local MCP server from 10 to **14 default tools**
(from 11 to **15** with `--approval-channel codex`) and adds
`agent-guard-exfil-export` for **15 npm CLI entrypoints**. Existing recovery
manifests, deletion policy files and session accounting require no migration.
Complete outstanding approved tasks under their original package, workspace,
quarantine and session binding; older doctor baselines may report `DRIFTED`
after upgrading and should be reviewed before writing a new baseline.

Core/CLI requires Python 3.9+ and Git. Optional MCP retains Python 3.10+ and
`mcp==2.3.0`. TOML `safe_view` and Kimi TOML doctor helpers require Python
3.11+, and `agent-guard-exfil-export` requires POSIX file-descriptor
operations. No new production dependency is introduced; bundled Unicode table
metadata ships under `assets/` with `SPDX-License-Identifier: Unicode-3.0`.

## Validation and scope

This release builds on the `0.3.0` host acceptance baseline recorded in
[maintenance acceptance](../reports/test-report-maintenance-0.3.0.md). Local
and CI validation across the `0.3.2` milestones covers TOML safe views,
metadata-only Exfil audit, MCP Exfil and `restore_preview` real-stdio
contracts, local Git hooks, eight new credential rules, POSIX export
preflights, synthetic-payload Bash Exfil hooks across Claude/Kimi/DSH,
whole-tree `regenerable_patterns` and `protected_paths` policy CLI checks,
inline script deletion block/allow pairs, Windows PowerShell echo-probe
formatting, L0 lifecycle calibration, read-only workspace doctor and Unicode
carrier checks, and three-Skill symlinked entrypoint checks. Follow
[development CI](https://github.com/mokuyoaxis/agent-guard/actions/workflows/ci.yml)
for exact commit-level Linux, macOS and Windows Core RC runs. The tag-triggered
release workflow runs the pinned MCP SDK gate and full suite before packing one
archive for GitHub Releases and npm (with provenance) and running independent
read-only publication checks.

Claude Code model-backed maintenance remains deferred, Kimi's standalone Lab
capture helper remains pinned to `2.1.1`, and explicit MCP tools remain
separate from native shell/editor interception and OS sandboxing; see the
[capability matrix](../guides/harness-capabilities.md). Opt-in Bash Exfil
coverage is verified on direct synthetic hook payloads and mocked DSH services
rather than live model sessions. Quarantine copies still occupy disk space,
restore remains transaction-level, and GC remains an explicit manual action
with no automatic purge, GUI panel or commercial retention/legal-hold features.
