# Documentation / 文档导航

Start with [getting started](guides/getting-started.md) for installation and
setup. The three main paths are [recovery protection](../skills/delete-guard/SKILL.md),
[disclosure checks](../skills/exfil-guard/SKILL.md), and
[behaviour experiments](lab/guard-lab.md). Use the
[harness capability matrix](guides/harness-capabilities.md) for observed coverage
and the [architecture](design/architecture.md) for the shared Core.

This documentation covers [0.2.7](releases/release-notes-0.2.7.md): Cleanup
plans, deletion admission and optional local MCP tools. Before installing,
check the exact [GitHub release](https://github.com/mokuyoaxis/agent-guard/releases/tag/0.2.7)
and [npm version](https://www.npmjs.com/package/@mokuyoaxis/agent-guard/v/0.2.7)
for availability. A checkout or manifest does not establish publication or
the current npm `latest` channel.
The opt-in `rc` line remains [0.2.5-rc3](releases/release-notes-0.2.5-rc3.md), and `hotfix` is the
opt-in [0.2.4-hotfix.1](https://github.com/mokuyoaxis/agent-guard/releases/tag/0.2.4-hotfix.1)
quarantine protection backport. The [getting-started guide](guides/getting-started.md)
explains exact-version and local-package installation.

The earlier published [0.2.6](releases/release-notes-0.2.6.md) covers Kimi Stop feedback,
its doctor checks, Kimi 2.3.0 root-Bash compatibility, DSH Lab preflight
diagnostics and alpha.4 default deletion compatibility. Existing 0.2.5 installations
need an upgrade to acquire those additions; see its [version notes](releases/release-notes-0.2.6.md).

先看入门指南，再按恢复保护、外发检测、Lab 验测选择入口。测试报告按所测版本与配置保留；发布记录、
历史设计和外部案例各自归档，不能据此扩展当前产品的承诺。

| Category / 分类 | Contents / 内容 |
|---|---|
| [Guides](#guides) / 使用指南 | 宿主接入证据、漂移检查、维护者发布指南 |
| [Design](#design) / 架构与契约 | 当前架构、威胁模型与兼容契约 |
| [Lab](#lab) / 防守验测 | 校准流程、任务模板及历史候选参考 |
| [Research](#research) / 防守研究 | 来源、发送授权、父子代理与正常任务评分 |
| [Reports](#reports) / 测试报告 | 按宿主、版本和观察范围记录的证据 |
| [Releases](#releases) / 发布记录 | 正式版与预览版各自的范围 |
| [History](#history) / 历史设计与经验 | 早期设计、实现教训和事故探索 |

## Guides

当前源码中的[状态与清理反馈](guides/recovery-feedback.md)说明本地 Python／Hook／宿主验证的区别、
ALLOW_NOOP 覆盖边界及准确 GC 用法；纳入 0.2.5。

| Document / 文档 | Scope / 范围 |
|---|---|
| [Getting started](guides/getting-started.md) | 安装路径、Agent 接入提示词与最短验证流程 |
| [Status and cleanup feedback](guides/recovery-feedback.md) | Python／插件状态、ALLOW_NOOP、保留空间与手动 GC |
| [Cleanup Copilot](guides/cleanup-plan.md) | 0.2.7：零写入计划、四类候选、大小与显式确认执行 |
| [Deletion limits](guides/deletion-limits.md) | 0.2.7：隔离容量、会话预算、频率、保护路径与批量确认 |
| [Local MCP tools](guides/mcp.md) | 0.2.7本地stdio：安全删除、普通恢复、事务／状态和GC预览；不替代hook |
| [Harness capabilities](guides/harness-capabilities.md) | 当前接入范围、证据等级与未验证路径 |
| [Host drift](guides/host-drift.md) | 宿主版本、配置与本地基线检查 |
| [Trash locations](guides/trash-index.md) | 0.2.4 的只读位置查询、Agent／前端共享 JSON 契约 |
| [Audit metadata](guides/audit-metadata.md) | RC2 引入的字段投影、会话关联、GC／status 契约与保留边界 |
| [Publishing from ephemeral environments](guides/publishing-from-ephemeral-environments.md) | 临时环境中的 GitHub 发布操作参考 |

Adapter setup remains in [adapters/](../adapters/INTEGRATION.md). Skill contracts
remain in [skills/](../skills/delete-guard/SKILL.md).

We currently focus on DSH and Kimi Code. Claude Code's adapter and past
reports remain available; testing new Claude versions is paused. For other
hosts, ask the official agent to follow the
[integration requirements](../adapters/INTEGRATION.md) and configure Agent Guard locally.

## Design

| Document / 文档 | Scope / 范围 |
|---|---|
| [Architecture](design/architecture.md) | Core、Decision Protocol、适配器与数据流 |
| [Threat model](design/threat-model.md) | 资产、可信假设与已知防护边界 |
| [Compatibility](design/compatibility.md) | 适配器契约、接口兼容与版本约定 |

## Lab

| Document / 文档 | Scope / 范围 |
|---|---|
| [Guard Lab guide](lab/guard-lab.md) | 操作流程、信道、L0/L1/L2 语义与限制 |
| [Blue-team calibration](lab/blue-team-calibration.md) | 可从 checkout／包运行的四种零模型控制 |
| [Clean task](lab/tasks/guard-lab-clean-task.md) | 干净对照任务模板 |
| [Injection trial task](lab/tasks/guard-lab-trial-task.md) | 提示注入试次任务模板 |
| [Native-read trial task](lab/tasks/guard-lab-read-trial-task.md) | 原生读取脱敏任务模板 |
| [fake-ai-api reference](lab/cases/injection-case-fake-ai-api.md) | 外部攻击端候选素材；尚未作为 Lab 样本验证 |

Task templates and external case references are separate from completed test
reports. A scripted provider checks host behavior; a real-model injection
trial needs an effective unguarded baseline before a matched comparison.

## Research

[研究导航](research/README.md)汇总本项目防守研究的整理稿：
数据流观察、正文发送授权、父子来源与独立评分。私有实验方法、公开校准入口与生产
覆盖分别说明。Lab 小规模推进，欢迎 fork、PR 与独立复现；公开资料不包含诱导载荷
或私有运行原件。

## Reports

Each report preserves its original version, configuration, result and limits.
Use the [capability matrix](guides/harness-capabilities.md) to interpret current
coverage rather than treating every historical result as current acceptance.

| Document / 文档 | Scope / 范围 |
|---|---|
| [Blue-team Lab, October 2026](reports/blue-team-lab-2026-10.md) | 来源／发送授权／任务效用与失败的去敏阶段摘要；不升级正式证据等级 |
| [0.2.3 contract review](reports/test-report-release-readiness-0.2.3.md) | 发布前契约复核与 DSH 原生验收 |
| [0.2.4 official Flash / PTC](reports/test-report-dsh-0.2.4-ptc.md) | 实模主／子代理碰撞拒绝；原始主 PTC 任务失败与独立收尾分别记录 |
| [Claude Code harness](reports/test-report-claude-code-harness.md) | 脚本模型驱动的真实宿主 hook 验证 |
| [Kimi Code blocking and feedback](reports/test-report-kimi-code-block.md) | 0.42.0／2.1.1 历史试次与 2.3.0 根代理 CLI／ACP 固定响应复验，含 Stop 和 cwd 边界 |
| [Kimi Code Lab](reports/test-report-kimi-guard-lab.md) | 历史配对试次与后续本地复核 |
| [Codex / GPT-6 Astra](reports/test-report-codex-gpt-6-astra-high.md) | Skill／CLI 验收，不声明原生拦截 |
| [Codex / GPT-5.6 Sol](reports/test-report-codex-gpt-5.6-sol.md) | v0.1.1 历史 Skill／CLI 评估 |
| [DSH 0.1.5-rc.1](reports/test-report-dsh-0.1.5-rc.1.md) | 打包插件与执行级 Bash 验收 |
| [DSH alpha.4 compatibility](reports/test-report-dsh-alpha4.md) | 默认删除适配的真实 Profile／Bash／主子代理、恢复和审批；可选 read／Lab 不扩展 |
| [DSH 0.2 upgrade](reports/test-report-dsh-0.2-upgrade.md) | 升级初期兼容快照，后续状态见契约复核 |
| [DSH Lab baseline](reports/test-report-dsh-guard-lab.md) | 真实模型基线；未形成 L2 结论 |
| [DSH native sessions](reports/test-report-dsh-native-session.md) | 原生工具结果捕获与保留日志复核 |
| [DSH paired Lab](reports/test-report-dsh-paired-lab.md) | 配对工作流与结果改写契约调查 |
| [DSH retry evidence](reports/test-report-dsh-retry-evidence.md) | 失败尝试、重试会话与证据解析 |
| [DSH read redaction](reports/test-report-dsh-read-redaction.md) | 零模型原生读取、下一请求与落盘检查 |
| [DSH real follow-up](reports/test-report-dsh-real-followup.md) | 真实读取开关对照；不等于注入 L2 |
| [DSH v0.1.1](reports/test-report-dsh-v0.1.1.md) | 历史真机测试 |
| [WorkBuddy / Windows Core](reports/test-report-workbuddy-windows-core.md) | 社区 Core／CLI 反馈与修复回归 |
| [ZCode / Windows](reports/test-report-zcode-glm-flash.md) | 历史 CLI／hook 试次及许可绕过观察 |

## Releases

| Document / 文档 | Scope / 范围 |
|---|---|
| [0.2.7](releases/release-notes-0.2.7.md) | Cleanup、删除准入与MCP的版本范围、升级影响和准备阶段验证 |
| [0.2.6](releases/release-notes-0.2.6.md) | 正式版范围、升级影响与验收；Kimi 反馈／doctor、DSH 预检与版本扩展 |
| [Changes and maintenance details](releases/unreleased.md) | 0.2.7增量及0.2.6维护历史，保留原验收边界 |
| [0.2.5](releases/release-notes-0.2.5.md) | 正式版范围；累计 RC 维护、明确反馈与升级影响 |
| [0.2.4-hotfix.1](https://github.com/mokuyoaxis/agent-guard/releases/tag/0.2.4-hotfix.1) | 已发布的稳定线回收站保护回补；不含完整 RC 累计内容 |
| [0.2.5-rc3](releases/release-notes-0.2.5-rc3.md) | 已发布的研究／文档预发布版与零模型示例 |
| [0.2.5-rc2](releases/release-notes-0.2.5-rc2.md) | 已发布的维护 RC；共享审计、observer 维护与兼容边界 |
| [0.2.5-rc1](releases/release-notes-0.2.5-rc1.md) | 已发布的维护预发布基线、文档整理与版本例外 |
| [0.2.4](releases/release-notes-0.2.4.md) | 恢复维护、trash 查询及兼容性例外 |
| [0.2.3](releases/release-notes-0.2.3.md) | 正式版范围与证据边界 |
| [0.2.3-rc2](releases/release-notes-0.2.3-rc2.md) | 离线 Lab 与已复核 DSH 契约 |
| [0.2.3-rc1](releases/release-notes-0.2.3-rc1.md) | Windows Core 与预发布范围 |
| [0.2.2](releases/release-notes-0.2.2.md) | 宿主漂移、实时哨兵与 DSH 打包验收 |
| [0.2.1](releases/release-notes-0.2.1.md) | adapter／doctor 变更 |
| [0.2.0](releases/release-notes-0.2.0.md) | exfil 与配置安全视图等发布范围 |
| [0.2.0-rc1](releases/release-notes-0.2.0-rc1.md) | 早期源码预览范围 |

## History

These documents explain earlier proposals and implementation experience.
Current behavior is defined by the Core, Skills and current guides.

| Document / 文档 | Scope / 范围 |
|---|---|
| [Friction log](history/friction.md) | F1–F34 实现发现与修复经验 |
| [Unguarded deletion note](history/development-note-unguarded-deletion.md) | 去标识化事故探索与恢复方向 |
| [Secret Guard analysis](history/secret-guard-analysis.md) | 实现前的需求分析 |
| [Secret Guard design](history/secret-guard-design.md) | 早期设计候选与原范围 |
| [Secret Guard architecture](history/secret-guard-architecture.md) | 早期架构草图；不代表拦截点已交付 |
