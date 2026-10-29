# 0.2.7 changes and 0.2.6 maintenance history

<a id="unreleased-changes-and-026-maintenance-history"></a>
<a id="027-candidate-changes-and-026-maintenance-history"></a>

The maintainer selected **0.2.7** for Cleanup Copilot, deletion admission and
optional local MCP tools. Check its exact GitHub release and npm version
through the [version notes](release-notes-0.2.7.md) before installing; a source
version alone does not establish publication. This path is retained for
existing links. At the **2026-10-29 local preparation checkpoint**, versioned
validation passed:
1129 Python cases and both Node groups in 1194.265 seconds, with no failures,
errors, skips, timeouts or automatic retries, plus a 155-file offline package
and installed MCP identity/operation checks. The diagnostic collector kept
`npm test` scope/deadlines without invoking npm. Release SDK-gate fault checks
also passed; that checkpoint did not include final-commit remote CI or
publication. These results are separate from the historical checkpoints below; this preparation
made no new real-model requests. See
[version scope, upgrade effects and validation](release-notes-0.2.7.md).
The separate 0.3.0 plan remains paused.

<a id="local-mcp-tools-unreleased"></a>
<a id="local-mcp-tools-027-candidate"></a>

## Local MCP tools (0.2.7)

The checkout adds an optional local stdio server for explicit safe_delete,
ordinary restore, transaction/status queries and GC-plan preview. Workspace
and stable session identity are set at startup; tools cannot override them,
the environment or quarantine. Operations use the existing CLI without a
shell and retain deletion protection, budget, audit and recovery semantics.
Session selection chooses saved CLI authorization mode as well as budget;
without it, legacy CLI state and the shared default bucket apply. Reusing
state requires matching workspace/quarantine/session. Native host-session
restrictions or in-memory veto require trusted bridging and are not inherited
merely by registering this server.
No force restore, GC execution, budget reset or Cleanup approval is exposed;
safe-delete quarantine targets are also refused. The existing GC CLI remains
separate; preview-only MCP does not add a terminal requirement to that CLI.

The official MIT-licensed `mcp==2.3.0` SDK is an optional separate-environment
dependency, not a new Core/CLI requirement or automatic npm installation.
MCP tools do not replace native hooks or cover shell/script/editor actions
that bypass them. For the 2026-10-28 frozen candidate, the first full local
run finished 1123 Python cases with one observer READY-startup error in
1156.312 seconds; Node did not run. The original error remains unresolved.
The same case passed separate SDK/system-Python checks, and a second complete
run passed all 1123 Python cases and both Node groups in 1143.435 seconds,
without failures, errors, skips, timeouts, runtime changes or automatic retries.
The diagnostic collector preserved `npm test` scope/deadlines; npm itself was
not invoked. Default Core testing without the optional SDK skips seven
protocol cases; the two recorded full runs included it. An independent
SDK CI job is configured; the local preparation checkpoint included no
remote execution of that job.

The 154-file offline install/12 CLI checks and three real Kimi 2.3.0 native-MCP
trials passed: ordinary delete/restore, same-session budget and configured
protection. The model made 16 MCP calls across 19 real configured-route HTTP
200 requests, without Bash, fixed replies or route changes. Its gateway's
server-side model identity was not independently verified. Local evidence
covers ARM64/PRoot Python 3.13.5; there are no new Windows/macOS/shared-storage
or live Claude/Cursor/Codex tests. Their setup examples remain documentation
guidance. See [installation, failure history and limits](../guides/mcp.md).
Published 0.2.6 and its old evidence remain unchanged; 0.3.0 is still paused.

<a id="deletion-admission-hardening-unreleased"></a>
<a id="deletion-admission-hardening-027-candidate"></a>

## Deletion admission hardening (0.2.7)

The current checkout adds quarantine capacity admission, persistent session
byte/entry budgets, short-window rate and repeated-target-set limits, and
shared protected paths in optional `.agent-guard/delete-policy.json`.
Cross-process locking covers measurement, durable admission debit and the
guarded mutation. Failures and restores do not refund session budget; unnamed
CLI processes share a default bucket. Configuration/state failures block new
admission instead of silently disabling limits.

Large batches require Cleanup's existing terminal review, followed by a
ten-minute approval bound to paths, metadata, configuration and session and
consumed once per selected path. Covered automated large-delete checks BLOCK with
Cleanup guidance; no new host approval/resume protocol is added. Approval
does not bypass capacity, budgets or protection. Status adds explicit
terminal-confirmed reset of the current budget only.
Existing compound-command ASK and Git snapshots remain outside the new
budget; normal restore, explicit GC and unprotected quarantine housekeeping
remain exempt. Configuration/control-path protection still applies.

Accounting uses logical `lstat` sizes including directory metadata and
conservative per-entry hardlink counts, not physical disk quotas. Audits,
metadata and unrelated writers can still grow storage. GC remains explicit;
capacity refusal never downgrades to permanent deletion. See
[defaults, reason codes and recovery](../guides/deletion-limits.md).

The 2026-10-28 frozen public runtime passed three independent full runs,
each with 1090 Python cases and both Node groups: 1061.017, 1025.315 and
1042.145 seconds, with no failures, errors, skips, timeouts or automatic retries.
The existing diagnostic collector retained `npm test` scope/deadlines without
invoking npm itself. An offline 149-file package and three real Kimi 2.3.0
root-Bash cases passed; nine configured-route model requests returned HTTP 200.
See the guide for POSIX-only validation, gateway identity and host/tool limits.
The earlier unexplained npm timeout did not recur; its history is retained.
The Cleanup-only results below predate these changes. Those earlier checks
used 0.2.6 package metadata without claiming the additions shipped in that
release. Subsequent versioned 0.2.7 tests and package checks passed as recorded
above; the separate 0.3.0 plan remains paused.

<a id="cleanup-copilot-unreleased"></a>
<a id="cleanup-copilot-027-candidate"></a>

## Cleanup Copilot (0.2.7)

The current checkout adds a metadata-only cleanup plan and an explicitly
confirmed CLI execution workflow. It reports regenerable, caller-declared
discarded, uncertain and protected targets with logical sizes, reasons and
projected deletion/quarantine effects. Execution still uses safe_delete;
ordinary quarantined files retain disk usage. The default plan writes no
workspace or guard metadata. See [usage and limits](../guides/cleanup-plan.md).

This initial addition is separate from published 0.2.6 and is included in
version 0.2.7. At its initial checkpoint it added no native host tool or
deletion-policy change. Subsequent admission hardening is described above. At the Cleanup-only
checkpoint, the runtime passed a continuous local ARM64/PRoot POSIX rerun:
1012 Python cases and both Node groups in 979.997 seconds, with no failures,
errors, skips or retries. This used the existing diagnostic result collector
with the same test scope and deadlines as `npm test`. The earlier literal
npm command's timeout remains recorded and unexplained; it did not recur
in this rerun. Focused cleanup/manifest checks and an offline package also passed,
including terminal confirmation, physical deletion and quarantine/restore.
See the guide's [validation scope](../guides/cleanup-plan.md#validation-scope)
for the local platform and host limits.

## Maintenance delivered in 0.2.6 / 0.2.6 维护说明

Updated 2026-10-22. The additions previously tracked here now ship in
[0.2.6](release-notes-0.2.6.md). This path is retained for existing links and
the preparation history below. The [0.2.5 notes](release-notes-0.2.5.md) retain
that release's scope. Existing installations need an upgrade; optional Stop
also needs explicit configuration and a new Kimi session.

## Availability

| Addition | Availability and setup |
|---|---|
| Kimi recovery feedback | Included in 0.2.6 as `adapters/kimi-code/stop_feedback.py`; requires an explicitly configured Stop hook. Version 0.2.5 lacks this helper. |
| Kimi configuration guidance | The current example recommends PreToolUse 120 seconds and optional Stop 30 seconds; merge top-level skill directories separately from TOML hook tables. Existing installations can adjust their PreToolUse timeout independently of Stop. |
| Kimi Stop doctor checks | Included in 0.2.6; `doctor.py kimi --probe --json` reports optional Stop configuration/local execution in a separate `feedback` object. Version 0.2.5 lacks these checks. |
| Kimi 2.3.0 Bash profile | Added in 0.2.6 after prompt-CLI and ACP root-Bash trials. Version 0.2.5 keeps its earlier list; the separate Kimi Lab helper stays pinned to 2.1.1. |
| DSH Lab preflight diagnostics | Included in 0.2.6; 0.2.5 retains the older generic error messages. This concerns the Lab controller's version/config checks, not the production deletion policy. |
| DSH alpha.4 default deletion adapter | Added in 0.2.6. Exact loader declarations include `0.2.1-alpha.4`; optional native read and Lab keep their two-version scope. Version 0.2.5 retains its earlier declaration. |

Use the [installation guide](../guides/getting-started.md) to keep the chosen
checkout or local-package path consistent. Start a new Kimi session after
changing hooks. The setup remains opt-in; this page does not change user settings.

## Kimi recovery feedback

The sampled Kimi 0.42.0, 2.1.1 and 2.3.0 success-hook paths omit the recovery
explanation from the next model request. The optional POSIX Stop hook asks
for one additional response after new compensations. It supplies actual
transaction IDs, state/restore commands, retained-storage guidance and full
GC commands. Restoring files moves them back without freeing total disk space.
The hook itself performs no restore or purge.

The initial answer appears first and may be inaccurate. Stop cannot repair
the earlier tool result; the model may still omit details. Emission cursors
prevent repeated notices in the tested scope but do not prove model receipt.
Shared-session actors, cancellation and exhausted budgets retain their limits.
The local doctor checks described below do not prove optional Stop delivery.

See [setup and delivery limits](../../adapters/kimi-code/README.md#optional-recovery-feedback-unreleased-checkout-addition)
and the [bounded host report](../reports/test-report-kimi-code-block.md).
Missing/timed-out hooks still allow tools in the sampled hosts. The 120-second
outer timeout gives the internal 90-second check margin; it does not change
that host behavior.

## Kimi Stop doctor checks

The existing top-level results retain PreToolUse scope. A separate `feedback`
object distinguishes absent Stop (`NOT_CONFIGURED`), invalid configuration
(`FAIL`), unreadable configuration (`UNVERIFIED`), and requested local probe
success/failure. An absent optional Stop does not fail the CLI; a configured
Stop that fails configuration or a requested probe exits 1.

The probe checks synthetic recovery guidance, empty/active-Stop silence and
repeat suppression in its own temporary home/workspace/trash. Existing audit
records and cursors are left alone; no model, deletion, restore or GC runs.
PreToolUse probes also isolate inherited `AGENT_GUARD_*` recovery locations.
Executable/file presence is checked separately from successful execution.
`feedback.model_delivery` remains `UNVERIFIED`, even after a Bash live sentinel.

Configured Stop fields and helper bytes join drift fingerprints. A Stop
failure prevents writing a passing baseline or proceeding to the live sentinel.
The importable `check_kimi()` wrapper remains PreToolUse-only; Claude behavior,
Core policy and rm wildcard rules are unchanged. See the
[doctor fields and limits](../../adapters/kimi-code/README.md#stop-doctor-checks-unreleased).

## DSH Lab preflight diagnostics

Version/config failures distinguish `START_FAILED`, `TIMEOUT`, `OUTPUT_LIMIT`,
`NONZERO_EXIT`, `EMPTY_STDOUT` and `UNSUPPORTED_VERSION`. Errors include child
exit code, capture flags, elapsed/deadline seconds and captured byte counts.
Configuration text, stderr text, command arguments and environment values are
omitted. Version/config budgets remain 10/15 seconds, with no automatic retry.
CLI failure still exits 1. See the [Lab runner guide](../../adapters/dsh/harness/README.md).

## Validation and remaining uncertainty

The [0.2.6 release notes](release-notes-0.2.6.md#validation-and-limits) record
the implementation checkpoint's 13/13 CI result, including ten complete
Python matrix jobs and both Node groups. The dated local checkpoints below
remain historical records and do not replace that final implementation check.

The later [DSH compatibility work](../design/dsh-compatibility.md) adds alpha.4
to default deletion support. Real CLI Profile/Boot checks covered all three
admitted hosts, with scripted root/child-agent, policy-forwarding and approval
checks on alpha.4. The [report](../reports/test-report-dsh-alpha4.md) records
that separate checkpoint; the historical full run below predates the new
manifest/forwarding assertions and does not validate those additions.

Before the 0.2.6 version/document alignment, the implementation passed a
complete frozen run on 2026-10-21: **974 Python tests and both Node groups**,
no failures, errors or skips, in 1270.573 seconds with the original
1800-second deadline and no retry. It used an internal unittest result
collector for immediate failure/event logs, with the same discovery scope
and sequential Node stages as `npm test`. The 195 public files stayed frozen.
That preparation aligned only version metadata, the exact-version assertion
and documentation; it does not reclassify the older failures below as passes.

The earlier DSH diagnostics checkpoint passed one complete `npm test` on 2026-10-21:
**962 Python tests and both Node groups**, on ARM64/PRoot POSIX. A 138-file
local package was installed offline at a path containing spaces; its CLI
reported the controlled child exit code correctly without copying payloads.
Those counts describe that earlier checkpoint, before the Stop doctor
additions. A later 139-file offline package checked the doctor candidate;
neither older artifact is a versioned 0.2.6 release.

The earlier complete-test attempts included a timeout and one DSH config
preflight error. Its original result details were not retained. Five bounded
repetitions of the failing case and the new complete run passed, but the
historical cause remains unknown. Controlled faults verified the new
diagnostics; they are separate from reproducing that historical failure.

Later Kimi compatibility work adds exact `2.3.0` to the Bash profile after
sixteen actual-host fixed-loopback trials: eight prompt CLI and eight ACP,
including allow, BLOCK, ASK refusal, runtime failure, absence controls,
quarantine/restore and Stop feedback. ACP hooks ran in the session directory
despite a different server startup cwd. This postdates the historical full
run above; it changes the version declaration, not Core or the bridge.
The [2.3.0 report](../reports/test-report-kimi-code-block.md#kimi-230-acceptance)
keeps root Bash, client-terminal, timeout, model and subagent limits explicit.
Updating the profile can make an old baseline `DRIFTED`; no baseline is
automatically rewritten. The Lab helper remains pinned to 2.1.1.

Earlier Kimi fixed loopback trials exercised the actual 0.42.0/2.1.1 CLIs.
A bounded 2.1.1 real-model session received the recorded transaction and full
commands; restoration and repeat-Stop checks passed. This does not establish
all-model, subagent, background or long-running behavior. The new DSH
diagnostic checks made no external model requests.

<a id="中文摘要"></a>

## 0.2.6 中文摘要

以下为 0.2.6 历史维护摘要：Kimi 可选 Stop 恢复反馈、120 秒外层超时接入说明，以及
DSH Lab 版本／配置预检的原因、退出码和计时诊断，及 Kimi Stop 的独立 doctor 检查。
后续默认删除适配加入 DSH `0.2.1-alpha.4`，读取脱敏／Lab 不随之扩大。
Kimi Bash profile 新增精确版本 2.3.0；CLI／ACP 各八项固定响应试次提供有限根代理证据，
未调用外部模型，也未扩大 Kimi Lab 的 2.1.1 准入或子代理声明。
Stop 未配置不报错；配置或本地探针失败退出 1，模型送达始终未验证。已发布 0.2.5 的范围保持原记录；
这些功能自 0.2.6 起提供，Stop 另需显式配置；原页面路径为已有链接保留。

版本／文档对齐前，最终实现检查点的 974 项 Python 与两组 Node 已在同一冻结流程通过；
此前 962 项检查点和历史失败分别保留，原失败原因仍未确定。
上述 0.2.6 维护未改变删除策略、rm 通配符规则、恢复格式或手动 GC。
