# Agent Guard 0.2.5

Stable maintenance release, prepared 2026-10-19. Version 0.2.5 combines
the maintenance and research delivered in RC1–RC3 with clearer feedback about
runtime readiness, command coverage and recovery storage.

The exact tag routes to a GitHub release and npm `latest`. Availability is
established by the [GitHub release](https://github.com/mokuyoaxis/agent-guard/releases/tag/0.2.5)
and [npm version](https://www.npmjs.com/package/@mokuyoaxis/agent-guard/v/0.2.5).
RC3 and 0.2.4-hotfix.1 retain their independent opt-in channels and artifacts.

0.2.5 正式版汇合 RC1—RC3 与明确反馈维护；GitHub／npm 的实际发布结果以精确版本回执为准。
本版不自动升级用户已有安装，不改变 rm 通配符规则或执行路径。

## What users will see

- **Startup checks run Python.** Before registering its tools and listener,
  the DSH adapter runs `python3 check.py --help` in the plugin process.
  Missing dependencies, an import/process failure or a 10-second startup
  timeout now produces an explicit initialization failure. The host can
  continue running other tools, so inspect the plugin's activation status.
- **Local readiness and host interception are separate.** DSH status shows
  the startup check, listener registration and most recent checker result.
  Live interception remains `UNVERIFIED`; local CLI status reports recovery
  data only. A plugin that never loaded may provide no status tool.
- **Unrecognized effects are identified.** `ALLOW_NOOP` now explains:
  “No recovery action is needed for the recognized operations. Unrecognized
  command effects have not been checked.” Its verdict and reason code stay
  the same. Script/interpreter effects are not certified as harmless.
- **Relocation explains retained storage.** Successful moves and partial
  failures with moved files report that the data still uses disk space,
  retain the recovery transaction ID, and provide GC commands. A dry-run,
  no-match or direct artifact deletion does not claim a relocation.
- **GC guidance selects an actual action.** Results provide a plan command,
  `--execute --all-eligible`, and explicit `--execute --txid '<txid>'` guidance.
  Keep the same working directory and workspace/trash settings. The 30-day
  and 5-GiB thresholds do not run cleanup automatically; `--execute` alone
  selects nothing. Purge removes recovery copies permanently and does not
  prune Git stash snapshots.
- **Failures explain the affected step.** Static `CHECKER_*` and `STATUS_*`
  diagnostics distinguish execution, timeout, process and output failures
  from policy refusals. Checker infrastructure failures still deny the call.
  A failed status query returns `ok: false` and `report: null`, retaining
  adapter facts without replacing the last command-checker result or copying
  raw process errors into diagnostics.

这些反馈不扩大自动拦截范围。Python 在插件进程中可运行，不代表调用者的 shell／sandbox
已经验证；Hook 已注册也不代表实际调用经过它。迁入回收站是保留恢复副本，数据仍占空间。
具体字段、故障标签及 GC 示例见[状态与清理反馈](../guides/recovery-feedback.md)。

## Included from the prereleases

- [RC1](release-notes-0.2.5-rc1.md): quarantine control-path protection,
  PURGED-history exclusion from active GC planning, minimized restore and
  safe_delete audit writers, exemption diagnostics and assessment clarification.
- [RC2](release-notes-0.2.5-rc2.md): shared audit metadata projection, opaque
  session correlation, bounded legacy status/GC metadata, observer startup
  diagnostics and cancellation accounting.
- [RC3](release-notes-0.2.5-rc3.md): defensive research, sanitized bounded
  observations and the portable four-control calibration example. Private
  experimental methods do not become production authorization features.

## Upgrade impact

| Starting point | What to review |
|---|---|
| 0.2.4 | Quarantine control paths can now be refused before housekeeping. Review RC audit field changes and explicit DSH initialization failures. |
| 0.2.4-hotfix.1 | Control-path protection is already backported. The remaining RC maintenance, research and feedback are additional. |
| 0.2.5-rc1 / rc2 | Later RC content and the feedback changes above are cumulative. |
| 0.2.5-rc3 | Review the new startup/status diagnostics, `ALLOW_NOOP` explanation and retained-storage/GC guidance. |

Consumers must tolerate optional result fields. Audit records are supported
metadata, not arbitrary payload storage: raw/custom audit fields may be
omitted or projected, including in-memory views of old records. Earlier log
bytes are not rewritten. Recovery IDs and manifest/CLI paths remain exact;
an audit correlation digest is not a restore argument. See the
[audit contract](../guides/audit-metadata.md).

The default DSH lifecycle changes from log-only inactivity to explicit
initialization failure. Python must be on the plugin process PATH and
available to the actual executor. Existing admitted host versions remain
`0.1.5-rc.1` and `0.2.0-rc.2`; optional read redaction stays off by default
and keeps its existing artifact pins and coverage limits.

The maintainer selected 0.2.5 for this cumulative maintenance, carrying
forward RC1's documented versioning exception. Decision/reason strings,
public CLI parameters and exit-code roles, recovery strategies and formats
retain their contracts. No production dependency or configuration/storage
migration is introduced. See the [compatibility contract](../design/compatibility.md).

本轮没有改动 rm 通配符、变量、管道、find 或工作区边界规则，也没有新增替代执行路径。
`safe_delete` 仍按原规则显式展开自己的参数；这不放宽 `rm` 的通配符判定。

## Validation and limits

Before the version-only promotion, the feedback-maintenance checkout passed
one complete `npm test` on 2026-10-19: 933 Python tests and both Node adapter
groups, with original assertions and timeouts. Eleven actual CLI scenarios,
feedback/audit checks, bounded zero-model DSH runtime checks for both admitted
versions, and source/tarball/offline-install equality also passed. This is
local maintenance evidence, separate from versioned remote release gates.

The versioned 0.2.5 checkpoint separately passed one complete `npm test`:
933 Python tests (835.794 seconds, no skips) and both Node groups, with
192 public files unchanged throughout the run. The 137-file local package
matched source and offline installation; ten CLI entries, adapter import,
synthetic recovery, explicit GC guidance and unchanged wildcard refusal passed.
After that run, only release-status documentation and this evidence paragraph
changed. Runtime and test bytes retain the verified checkpoint; final
source/package/installation equality is checked again for publication.

Earlier intermittent subprocess timeouts remain recorded as failures. Their
historical cause is unresolved; later successful checks do not establish a
root-cause fix. The [capability matrix](../guides/harness-capabilities.md)
retains each host report's tested versions and scope. This preparation adds
no real-model trial, UI/sandbox certification or general subagent guarantee.
Full Windows-native harness E2E, universal egress protection and isolation
against same-UID processes remain outside the guarantee.

Install the exact version or use a reviewed checkout/tarball following
[getting started](../guides/getting-started.md). Publication requires the
final commit's CI, npm credential preflight and matching GitHub/npm artifacts.
Local checks do not establish those remote receipts or registry availability.
