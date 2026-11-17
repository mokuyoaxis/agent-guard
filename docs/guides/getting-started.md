# Getting started / 入门指南

Agent Guard has three paths: recover supported destructive actions,
check disclosure before emission, and use guard-lab for controlled behaviour
experiments. Begin with the path you need; native interception is optional
and depends on the actual host.

Agent Guard 提供恢复保护、外发检测和用户控制的行为验测。先选择需要的能力；
自动拦截另需实际宿主提供并加载兼容 hook。

## Select the package root

This guide covers [0.3.0](../releases/release-notes-0.3.0.md): saved selections,
operator-approved task execution and query, optional quarantine for
regenerable artifacts, and linked Skill installation. It retains 0.2.9's
read-only Cleanup inventory, measurement and faster scans. Confirm the
exact [GitHub release](https://github.com/mokuyoaxis/agent-guard/releases/tag/0.3.0)
and [npm version](https://www.npmjs.com/package/@mokuyoaxis/agent-guard/v/0.3.0)
are available before installing from the registry. A checkout or manifest
does not establish publication or the current npm `latest` channel.

The earlier published [0.2.6](../releases/release-notes-0.2.6.md) includes Kimi
recovery feedback, diagnostics and reviewed DSH alpha.4/Kimi 2.3.0 Bash compatibility.
The prerelease line remains [0.2.5-rc3](../releases/release-notes-0.2.5-rc3.md).
The optional [0.2.4-hotfix.1](https://github.com/mokuyoaxis/agent-guard/releases/tag/0.2.4-hotfix.1)
backports quarantine protection onto the stable line.

Once the exact version is available, use a user-selected stable prefix:

```sh
npm install --prefix /absolute/path/to/agent-guard-install @mokuyoaxis/agent-guard@0.3.0
```

The package root is
`/absolute/path/to/agent-guard-install/node_modules/@mokuyoaxis/agent-guard`.
To select the prerelease, use `@mokuyoaxis/agent-guard@0.2.5-rc3` explicitly;
its [GitHub release](https://github.com/mokuyoaxis/agent-guard/releases/tag/0.2.5-rc3)
and [npm version](https://www.npmjs.com/package/@mokuyoaxis/agent-guard/v/0.2.5-rc3)
identify the published package. For the stable-line fix alone, select
`@mokuyoaxis/agent-guard@0.2.4-hotfix.1`. These opt-in channels do not move
stable `latest`.

If that version is unavailable, use a reviewed source checkout or a checked
local tarball. Install the tarball into a separate prefix:

```sh
npm install --offline --ignore-scripts --legacy-peer-deps --no-audit --no-fund --prefix /absolute/path/to/agent-guard-review /absolute/path/to/mokuyoaxis-agent-guard-0.3.0.tgz
```

The review package root is
`/absolute/path/to/agent-guard-review/node_modules/@mokuyoaxis/agent-guard`.
Keep the selected package/checkout path consistent when configuring the host.
Do not infer publication from a manifest/main commit or use an unscoped
same-name package. A checkout's default branch can differ from a tagged stable release.

The [0.2.7 changes](../releases/release-notes-0.2.7.md#upgrade-and-operator-notes)
add execution-time limits that can refuse previously successful deletions.
Review the [default budgets and operator recovery](deletion-limits.md) before
upgrading; normal restore remains available without a new deletion debit.

The [0.2.6 additions](../releases/release-notes-0.2.6.md) require an upgraded package
or a checkout containing the corresponding files. In particular, 0.2.5 lacks
`adapters/kimi-code/stop_feedback.py`. Check the selected root before configuring
the optional Stop hook. Installing a new copy does not update another existing
installation or its host configuration.

Version 0.2.6 adds DSH `0.2.1-alpha.4` for the default deletion adapter.
Version 0.2.5 retains its earlier host declaration. Optional native read and Lab keep
their separate two-version scope. Check [DSH compatibility](../design/dsh-compatibility.md)
before enabling a profile.

The 0.2.6 Kimi Bash profile also adds exact version `2.3.0`, based on
[prompt CLI and ACP trials](../reports/test-report-kimi-code-block.md#kimi-230-acceptance).
Published 0.2.5 retains its earlier list. The Kimi Lab capture helper remains
pinned to 2.1.1; updating the Bash profile does not update that separate path.

Core needs Python 3.9+ and Git, with no third-party Python packages. POSIX
examples/bridges require a POSIX shell. The optional pinned DSH read path uses
Node 22; Kimi TOML helpers need Python 3.11+. Windows Core evidence does not
establish Windows-native hook bridge or full-suite portability.
The optional [MCP adapter](mcp.md) requires Python 3.10+ and its separately
installed pinned SDK; npm does not install it. MCP exposes explicit tools
and does not intercept operations that bypass them.

## Set up a coding agent

Use the [copyable setup prompt](agent-setup-prompt.md) with the full package
root, workspace and authorized configuration scope. It is the shared entry
for Codex, DSH, Claude Code, Kimi Code and local adaptation of other hosts.
The four-host list is the 0.3.0 maintenance target; actual tested coverage
still comes from the [capability matrix](harness-capabilities.md).

Keep the complete installation. Version 0.3.0 adds a preview-first
[Skill installer](skill-installation.md) that links `skills/delete-guard`
into the selected host directory and preserves an old Skill during updates.
Version 0.2.9 has no `install_skill.py`; its existing complete-package CLI
and MCP entrypoints remain available. Check the selected version before
following the installer instructions.

The [maintenance acceptance table](../reports/test-report-maintenance-0.3.0.md)
records passed local installation/workflow checks and bounded MCP host tasks.
Use the [upgrade checklist](skill-installation.md#upgrade-a-development-installation)
to keep the selected complete package, outstanding task binding and MCP
configuration consistent. Existing global installations are not updated by
changing a checkout.

Use MCP for explicit maintenance tools. Add a blocking hook/plugin where the
actual host supports it; MCP registration alone does not protect native
shell/editor operations. The [integration guide](../../adapters/INTEGRATION.md)
defines the shared Core, approval and verification requirements. Configuration
and live verification can be handed off separately when checks are deferred.

不要把“Skill 已安装”“配置检查通过”和“真实调用被拦截”合并为一个成功状态。
用户范围外的配置或依赖改动需另行确认；已经授权的常规接入不重复询问。

## Verify without executing a destructive command

From an isolated fixture, pass command text as data:

```sh
python3 /absolute/path/to/agent-guard/skills/delete-guard/scripts/check.py --json -- rm /
```

Read the BLOCK decision. Advisory exit 0 means assessment completed, not that
the command may run. Assessment can create quarantine/audit metadata and a
Git local exclude entry. Use a disposable fixture, and see the
[assessment contract](../../skills/delete-guard/references/policy.md#assessment-and-dry-run-side-effects).

For Claude/Kimi, local doctor probes do not start a model:

```sh
python3 /absolute/path/to/agent-guard/doctor.py claude --probe --json
python3 /absolute/path/to/agent-guard/doctor.py kimi --probe --json
```

Select the actual host, not both by default. These probes check one selected
configuration and a local bridge/adapter. Real host evidence needs a separate
controlled sentinel. An explicit live sentinel may consume provider quota;
its result covers one sampled root-agent Bash call. See
[host drift](host-drift.md) and the
[capability matrix](harness-capabilities.md).

For Kimi's optional Stop feedback, choose whether the extra response is
wanted, follow the [separate setup](../../adapters/kimi-code/README.md#optional-recovery-feedback-unreleased-checkout-addition),
and start a new session. In 0.2.6, the doctor reports Stop
configuration and local execution separately under `feedback`. An absent Stop
is optional; a configured but invalid or failing Stop exits 1. `--probe` uses
synthetic records in temporary storage. `feedback.model_delivery` remains
`UNVERIFIED`; local PASS does not establish model receipt.

## Try the three paths

- **Recovery:** use the [delete Skill](../../skills/delete-guard/SKILL.md).
  Valuable content relocates; provably regenerable artifacts may delete
  directly. Inspect txids/state after errors, including force-restore backups.
  For a reviewed batch, [save the explicit targets](cleanup-plan.md#saved-explicit-target-plans-030-development),
  have the operator approve once, then let the agent execute/query its task ID.
  Use [MCP tasks](mcp.md#reviewed-maintenance-tasks-030-development) for the same
  handoff, with terminal approval by default or the operator-enabled
  [Codex native form](mcp.md#host-confirmation-030-development).
- **Disclosure:** use the [exfil Skill](../../skills/exfil-guard/SKILL.md).
  A checker plan is not completed redaction. The payload owner applies the
  plan, controls emission and owns any egress audit.
- **Experiments:** use the [Lab guide](../lab/guard-lab.md).
  Calibrate all four built-in controls before real trials. Only an effective
  unguarded baseline enables a matched mitigation comparison.

For incident recovery from surviving evidence, use
[recovery-audit](../../skills/recovery-audit/SKILL.md).
Full rules, historical reports and releases are in the
[documentation index](../README.md).
