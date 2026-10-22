# Unreleased maintenance / 未发布维护

Updated 2026-10-22. These additions are aligned for the local
[0.2.6 preparation](release-notes-0.2.6.md); they have not been released.
The local manifest is 0.2.6, with the tag decision pending.
The [published 0.2.5 notes](release-notes-0.2.5.md) retain that release's scope.
A local manifest or tarball does not establish publication or prove that an
existing installation contains these additions.

## Availability

| Addition | Availability and setup |
|---|---|
| Kimi recovery feedback | Requires a checkout or reviewed local package containing `adapters/kimi-code/stop_feedback.py`, plus an explicitly configured Stop hook. Published 0.2.5 lacks this helper. |
| Kimi configuration guidance | The current example recommends PreToolUse 120 seconds and optional Stop 30 seconds; merge top-level skill directories separately from TOML hook tables. Existing installations can adjust their PreToolUse timeout independently of Stop. |
| Kimi Stop doctor checks | Source under review; `doctor.py kimi --probe --json` reports optional Stop configuration/local execution in a separate `feedback` object. Published 0.2.5 lacks these checks. |
| Kimi 2.3.0 Bash profile | This checkout/local package adds the exact version after prompt-CLI and ACP root-Bash trials. Published 0.2.5 keeps its earlier list; the separate Kimi Lab helper stays pinned to 2.1.1. |
| DSH Lab preflight diagnostics | Source under review; published 0.2.5 retains the older generic error messages. This concerns the Lab controller's version/config checks, not the production deletion policy. |
| DSH alpha.4 default deletion adapter | Requires this reviewed checkout/local package. Exact loader declarations include `0.2.1-alpha.4`; optional native read and Lab keep their two-version scope. Published 0.2.5 retains its earlier declaration. |

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
This preparation aligns only version metadata, the exact-version assertion
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

## 中文摘要

本页记录 0.2.6 本地准备中的维护：Kimi 可选 Stop 恢复反馈、120 秒外层超时接入说明，以及
DSH Lab 版本／配置预检的原因、退出码和计时诊断，及 Kimi Stop 的独立 doctor 检查。
后续默认删除适配加入 DSH `0.2.1-alpha.4`，读取脱敏／Lab 不随之扩大。
Kimi Bash profile 新增精确版本 2.3.0；CLI／ACP 各八项固定响应试次提供有限根代理证据，
未调用外部模型，也未扩大 Kimi Lab 的 2.1.1 准入或子代理声明。
Stop 未配置不报错；配置或本地探针失败退出 1，模型送达始终未验证。已发布 0.2.5 的范围保持原记录；
使用新功能前应确认所选 checkout／本地包确实包含对应文件；尚未发布，tag 待定。

版本／文档对齐前，最终实现检查点的 974 项 Python 与两组 Node 已在同一冻结流程通过；
此前 962 项检查点和历史失败分别保留，原失败原因仍未确定。
删除策略、rm 通配符规则、恢复格式和手动 GC 保持原样。
