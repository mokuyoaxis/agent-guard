# Getting started / 入门指南

Agent Guard has three paths: recover supported destructive actions,
check disclosure before emission, and use guard-lab for controlled behaviour
experiments. Begin with the path you need; native interception is optional
and depends on the actual host.

Agent Guard 提供恢复保护、外发检测和用户控制的行为验测。先选择需要的能力；
自动拦截另需实际宿主提供并加载兼容 hook。

## Select the package root

The published stable baseline is 0.2.4 and the published RC baseline is
0.2.5-rc1; see its [scope](../releases/release-notes-0.2.5-rc1.md).
Current source is the unpublished [0.2.5-rc2 candidate](../releases/release-notes-0.2.5-rc2.md),
including [shared audit metadata](audit-metadata.md) and observer startup maintenance.
Use a user-selected stable prefix for the published package:

```sh
npm install --prefix /absolute/path/to/agent-guard-install @mokuyoaxis/agent-guard@0.2.4
```

The package root is
`/absolute/path/to/agent-guard-install/node_modules/@mokuyoaxis/agent-guard`.
For the published RC baseline, select `@mokuyoaxis/agent-guard@0.2.5-rc1` explicitly and check its
[GitHub release](https://github.com/mokuyoaxis/agent-guard/releases/tag/0.2.5-rc1)
or [npm version](https://www.npmjs.com/package/@mokuyoaxis/agent-guard/v/0.2.5-rc1)
for publication. Use a reviewed checkout or supplied local tarball to evaluate
the RC2 candidate while its publication is pending.
Do not infer RC availability from the manifest or use an unscoped same-name
package. A checkout's default branch can differ from a tagged stable release.

Core needs Python 3.9+ and Git, with no third-party Python packages. POSIX
examples/bridges require a POSIX shell. The optional pinned DSH read path uses
Node 22; Kimi TOML helpers need Python 3.11+. Windows Core evidence does not
establish Windows-native hook bridge or full-suite portability.

## Set up a coding agent

Replace the root path in this prompt, and give it to your agent:

```text
Set up Agent Guard for this workspace using /absolute/path/to/agent-guard.
Identify the actual host version, tool names and hook/skill capabilities.
Read the README, Skill contracts and matching adapter guide; check Python/Git.
Use the selected checkout/package version and preserve existing settings.
Respect already-approved paths and configuration scope. Ask only for missing
information or changes beyond that scope, including new dependencies.
Install/reference the relevant Skills. Configure a native hook only where a
blocking event is supported; otherwise report Skill/CLI-only operation.
Claude: adapters/claude/README.md. Kimi: adapters/kimi-code/README.md.
DSH: adapters/dsh/README.md. Other hosts: adapters/INTEGRATION.md.
Verify a harmless call. Submit BLOCK-shaped commands only as data to check.py;
never execute deletion of real data as a test. For Claude/Kimi, run the local
doctor, but do not treat PASS as proof of actual host interception.
Report the host/version, installed paths, configuration scope, covered tools,
observed interception and unverified paths separately.
guard-lab is the user's controller, not a Skill to install into the tested
agent. Run real-host/model experiments only in an explicitly selected scope.
```

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

## Try the three paths

- **Recovery:** use the [delete Skill](../../skills/delete-guard/SKILL.md).
  Valuable content relocates; provably regenerable artifacts may delete
  directly. Inspect txids/state after errors, including force-restore backups.
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
