# Getting started / 入门指南

Agent Guard provides three independent paths: **Delete-guard** (recoverable deletion, Git snapshots, and reviewed workspace cleanup), **Exfil-guard** (pre-emission credential/path checks and safe config views), and **Guard-lab** (controlled behavioral experiments). CLI, Skill, and local MCP workflows work right out of the box without modifying host internals; add a compatible native blocking hook (`PreToolUse` or the DSH plugin) when you also want automatic interception of native shell calls.

Agent Guard 提供三条彼此独立的能力路径：**Delete-guard**（可恢复删除、Git 快照与审核后清理）、**Exfil-guard**（外发前凭据与本机路径检查、无标量配置视图）以及 **Guard-lab**（可控行为验测）。CLI、Skill 与本地 MCP 接入开箱即用；如果需要宿主自动拦截原生命令行调用，则另需启用并验证对应宿主的执行前阻塞 hook 或插件。

## Install Agent Guard

### Requirements

- **Core CLI & Skills:** Python `3.9+` and Git (examples assume a POSIX shell). No third-party Python packages required.
- **Optional local MCP server:** Python `3.10+` and a separately installed [pinned MCP SDK](mcp.md) (`npm` does not install Python dependencies). MCP exposes explicit tools and does not intercept shell or editor calls that bypass them.
- **Host-specific extras:** Optional DSH native read prototype requires Node 22; Kimi TOML configuration helpers require Python `3.11+`. Windows Core unit coverage does not establish native Windows hook-bridge support.

### Install `0.3.0` (current stable)

Install into a fixed, user-owned directory so host hook and Skill paths remain stable across sessions:

```sh
npm install --prefix /absolute/path/to/agent-guard-install @mokuyoaxis/agent-guard@0.3.0
```

Your package root is `/absolute/path/to/agent-guard-install/node_modules/@mokuyoaxis/agent-guard`. Keep the `@mokuyoaxis/` scope. You can also run directly from a source clone (keep in mind that `main` may include post-release changes described in [unreleased notes](../releases/unreleased.md)):

```sh
git clone https://github.com/mokuyoaxis/agent-guard.git
cd agent-guard
```

Publication artifacts live on [GitHub Releases (`0.3.0`)](https://github.com/mokuyoaxis/agent-guard/releases/tag/0.3.0) and [npm (`0.3.0`)](https://www.npmjs.com/package/@mokuyoaxis/agent-guard/v/0.3.0).

<details>
<summary>Offline tarballs, alternate channels & upgrade impacts</summary>

- **Local tarball installation:**
  ```sh
  npm install --offline --ignore-scripts --legacy-peer-deps --no-audit --no-fund \
    --prefix /absolute/path/to/agent-guard-review \
    /absolute/path/to/mokuyoaxis-agent-guard-0.3.0.tgz
  ```
  The package root is `/absolute/path/to/agent-guard-review/node_modules/@mokuyoaxis/agent-guard`.
- **Opt-in channels:** Prerelease `@mokuyoaxis/agent-guard@0.2.5-rc3` ([GitHub](https://github.com/mokuyoaxis/agent-guard/releases/tag/0.2.5-rc3) · [npm](https://www.npmjs.com/package/@mokuyoaxis/agent-guard/v/0.2.5-rc3)) and stable quarantine backport `@mokuyoaxis/agent-guard@0.2.4-hotfix.1` remain explicitly selectable without altering `latest`.
- **Upgrading from `0.2.6` or earlier:**
  - `0.2.7+` introduces [deletion admission limits](deletion-limits.md) (capacity, budgets, rate/repeat limits, protected paths) that can refuse previously allowed deletions; normal restore remains available without new debits.
  - `0.2.6+` adds `adapters/kimi-code/stop_feedback.py`, DSH `0.2.1-alpha.4` default deletion support ([DSH compatibility](../design/dsh-compatibility.md)), and Kimi `2.3.0` Bash profile ([Kimi report](../reports/test-report-kimi-code-block.md#kimi-230-acceptance)). The Kimi Lab capture helper remains pinned to `2.1.1`.
  - `0.3.0` adds the preview-first [Skill installer](skill-installation.md) (`install_skill.py`), which is absent from `0.2.9` and earlier. Installing into a new directory never migrates existing global installations or host settings automatically.

</details>

## Set up a coding agent

Use the [copyable setup prompt](agent-setup-prompt.md) together with the full package root, workspace path, and authorized configuration scope. It targets **Codex, DSH, Claude Code, and Kimi Code**, and guides local adaptation on other hosts ([integration requirements](../../adapters/INTEGRATION.md)).

Recommended layering:
1. **Skill link (`0.3.0`):** Use `install_skill.py` (preview first, then execute) to symlink `skills/delete-guard` into your host's Skill directory while backing up any older Skill cleanly ([Skill installation](skill-installation.md), [upgrade checklist](skill-installation.md#upgrade-a-development-installation)).
2. **Explicit MCP tools:** Configure the local MCP server for structured cleanup, reviewed tasks, and restore ([MCP guide](mcp.md)).
3. **Native blocking hooks / plugin:** Enable host-specific hooks where supported and verified; MCP and Skills alone do not intercept native shell or editor tool calls.

> [!IMPORTANT]
> Check the [harness capability matrix](harness-capabilities.md) and [0.3.0 acceptance report](../reports/test-report-maintenance-0.3.0.md) for what is actually verified on your host version.
> 切勿将“Skill 已安装”“本地配置检查通过”与“真实工具调用已被宿主拦截”混为一谈；超出用户授权范围的配置或依赖更改需先确认再执行。

## Verify safely (non-destructive)

Test rule evaluation in a disposable fixture by passing command text as data:

```sh
python3 /absolute/path/to/agent-guard/skills/delete-guard/scripts/check.py --json -- rm /
```

You should see a `BLOCK` verdict. Advisory checks (`check.py`, `safe_delete.py --dry-run`) never alter command targets, though they may initialize quarantine/audit metadata and a local Git exclude entry ([assessment side effects](../../skills/delete-guard/references/policy.md#assessment-and-dry-run-side-effects)). Exit code `0` means evaluation completed—never permission to run a `BLOCK` or `ASK` command.

For **Claude Code** or **Kimi Code**, verify local hook wiring without invoking a model (run the check for your actual host):

```sh
python3 /absolute/path/to/agent-guard/doctor.py claude --probe --json
python3 /absolute/path/to/agent-guard/doctor.py kimi --probe --json
```

`--probe` verifies configuration structure and local bridge execution using temporary storage (`PASS` does not establish that a live host process loaded the hook; see [host drift](host-drift.md)). Running an optional live sentinel checks one sampled root-agent Bash call and may consume model quota.

If enabling **Kimi Code's optional Stop feedback**, follow the [Stop feedback setup](../../adapters/kimi-code/README.md#optional-recovery-feedback-unreleased-checkout-addition) and start a new session. `doctor.py kimi --probe` reports `feedback` separately (failing only if configured but broken); `feedback.model_delivery` remains `UNVERIFIED` in offline probes.

## Next steps by capability

- **Recoverable deletion & cleanup:** Read the [Delete-guard Skill](../../skills/delete-guard/SKILL.md). Valuable files relocate to quarantine; regenerable Git-ignored artifacts may delete directly unless quarantine is requested. For reviewed batches, [save explicit targets](cleanup-plan.md#saved-explicit-target-plans-030-development), approve once in your terminal (or the optional [Codex native form](mcp.md#host-confirmation-030-development)), and let the agent execute and query by task ID via CLI or [MCP tasks](mcp.md#reviewed-maintenance-tasks-030-development). Always inspect returned `txid`s after partial errors or `--force` restore backups.
- **Outbound secret & path checks:** Read the [Exfil-guard Skill](../../skills/exfil-guard/SKILL.md). `check_span.py` produces a decision and redaction plan; `sanitize.py` applies it. The caller controls emission and any egress audit logging.
- **Controlled behavior experiments:** Read the [Guard-lab guide](../lab/guard-lab.md). Run all four zero-model calibration controls first, confirm exposure in an unguarded baseline, and only then run a matched guarded comparison.
- **Post-incident reconstruction:** Use [recovery-audit](../../skills/recovery-audit/SKILL.md) to recover from surviving Git, session, and cache evidence.

Browse all guides, architecture specs, and historical test reports in the [documentation index](../README.md).
