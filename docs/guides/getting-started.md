# Getting started / 入门指南

Agent Guard has three paths: recover supported destructive actions,
check disclosure before emission, and use guard-lab for controlled behaviour
experiments. Begin with the path you need; native interception is optional
and depends on the actual host.

Agent Guard 提供恢复保护、外发检测和用户控制的行为验测。先选择需要的能力；
自动拦截另需实际宿主提供并加载兼容 hook。

## Select the package root

The stable release line is [0.2.5](../releases/release-notes-0.2.5.md), and the
prerelease line remains [0.2.5-rc3](../releases/release-notes-0.2.5-rc3.md).
The optional [0.2.4-hotfix.1](https://github.com/mokuyoaxis/agent-guard/releases/tag/0.2.4-hotfix.1)
backports quarantine protection onto the stable line.
Version 0.2.5 combines RC1–RC3 and clearer status, command and storage feedback.
Its [GitHub release](https://github.com/mokuyoaxis/agent-guard/releases/tag/0.2.5)
and [npm version](https://www.npmjs.com/package/@mokuyoaxis/agent-guard/v/0.2.5)
establish package availability. Use a user-selected stable prefix:

```sh
npm install --prefix /absolute/path/to/agent-guard-install @mokuyoaxis/agent-guard@0.2.5
```

The package root is
`/absolute/path/to/agent-guard-install/node_modules/@mokuyoaxis/agent-guard`.
To select the prerelease, use `@mokuyoaxis/agent-guard@0.2.5-rc3` explicitly;
its [GitHub release](https://github.com/mokuyoaxis/agent-guard/releases/tag/0.2.5-rc3)
and [npm version](https://www.npmjs.com/package/@mokuyoaxis/agent-guard/v/0.2.5-rc3)
identify the published package. For the stable-line fix alone, select
`@mokuyoaxis/agent-guard@0.2.4-hotfix.1`. These opt-in channels do not move
stable `latest`.

For a reviewed local checkout or explicitly selected local tarball, use a
separate prefix. The current [0.2.6 preparation](../releases/release-notes-0.2.6.md)
is unpublished, with the tag decision pending; use the local artifact:

```sh
npm install --offline --ignore-scripts --no-audit --no-fund --prefix /absolute/path/to/agent-guard-review /absolute/path/to/mokuyoaxis-agent-guard-0.2.6.tgz
```

The review package root is
`/absolute/path/to/agent-guard-review/node_modules/@mokuyoaxis/agent-guard`.
Keep the selected package/checkout path consistent when configuring the host.
Do not infer publication from a manifest/main commit or use an unscoped
same-name package. A checkout's default branch can differ from a tagged stable release.

The [unreleased 0.2.6 additions](../releases/unreleased.md) require files
from the reviewed checkout or local package. In particular, published 0.2.5
does not contain `adapters/kimi-code/stop_feedback.py`. Check the selected
root before configuring the optional Stop hook. A local version or filename
does not establish publication or the availability of these features in an
existing installation. The stable registry install above remains 0.2.5.

The unpublished checkout also adds DSH `0.2.1-alpha.4` for the default deletion
adapter. Published 0.2.5 retains its earlier host declaration; alpha.4 users
need the reviewed checkout/local package. Optional native read and Lab keep
their separate two-version scope. Check [DSH compatibility](../design/dsh-compatibility.md)
before enabling a profile.

The unpublished Kimi Bash profile also adds exact version `2.3.0`, based on
[prompt CLI and ACP trials](../reports/test-report-kimi-code-block.md#kimi-230-acceptance).
Published 0.2.5 retains its earlier list. The Kimi Lab capture helper remains
pinned to 2.1.1; updating the Bash profile does not update that separate path.

Core needs Python 3.9+ and Git, with no third-party Python packages. POSIX
examples/bridges require a POSIX shell. The optional pinned DSH read path uses
Node 22; Kimi TOML helpers need Python 3.11+. Windows Core evidence does not
establish Windows-native hook bridge or full-suite portability.

## Set up a coding agent

Replace the root path below with your installation path, then give the prompt
to your host's official coding agent. The
[integration guide](../../adapters/INTEGRATION.md) covers the hook requirements
and checks it should follow for the installed version.

We currently focus on DSH and Kimi Code. The Claude Code adapter remains a
reference; testing new Claude versions is paused. Other hosts are configured
locally by their official agents.

```text
Set up Agent Guard for this workspace using /absolute/path/to/agent-guard.
Before changing anything, read adapters/INTEGRATION.md in full, including
its verification and evidence requirements. Do this even if an adapter exists.
Read this host's current official hook documentation and inspect its installed version.
Identify the actual host version, tool names and hook/skill capabilities.
Read the README, Skill contracts and matching adapter guide; check Python/Git.
Use the selected checkout/package version and preserve existing settings.
For a host outside the DSH/Kimi maintenance focus, keep any new bridge and
configuration local; reuse the shared Core and versioned references.
Check which verification files that root includes. The npm package omits
tests/; use its local doctor/probes and report source unit tests as NOT_INCLUDED.
Respect already-approved paths and configuration scope. Ask only for missing
information or changes beyond that scope, including new dependencies.
Install/reference the relevant Skills. Configure a native hook only where a
blocking event is supported; otherwise report Skill/CLI-only operation.
Reuse the shared Core and its decisions; do not copy or relax its policy.
Check required project/hook trust through the host's normal review flow
within the authorized scope. Report activation as pending until it is complete.
Keep the user's selected account/provider; report model checks that cannot run.
Claude: adapters/claude/README.md. Kimi: adapters/kimi-code/README.md.
DSH: adapters/dsh/README.md. Other hosts: adapters/INTEGRATION.md.
Verify a harmless call. Submit BLOCK-shaped commands only as data to check.py;
never execute deletion of real data as a test. For Claude/Kimi, run the local
doctor, but do not treat PASS as proof of actual host interception.
Finish configuration and local probes with a clear handoff before fresh-host
verification. Report startup/reload requirements and preserve a checkpoint if
the step budget ends. UNVERIFIED host-interception/model-delivery fields are
expected local-doctor results; repeating the same probes will not clear them.
Report the host/version, installed paths, configuration scope, covered tools,
observed interception and unverified paths separately.
Use the handoff format in section 4 of adapters/INTEGRATION.md.
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

For Kimi's optional Stop feedback, choose whether the extra response is
wanted, follow the [separate setup](../../adapters/kimi-code/README.md#optional-recovery-feedback-unreleased-checkout-addition),
and start a new session. In the unpublished 0.2.6 preparation, the doctor reports Stop
configuration and local execution separately under `feedback`. An absent Stop
is optional; a configured but invalid or failing Stop exits 1. `--probe` uses
synthetic records in temporary storage. `feedback.model_delivery` remains
`UNVERIFIED`; local PASS does not establish model receipt.

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
