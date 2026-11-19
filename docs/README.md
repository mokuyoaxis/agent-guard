# Documentation / 文档导航

Start with [Getting started](guides/getting-started.md) for installation and agent setup, or browse directly by capability and architecture area below.

先看[入门指南](guides/getting-started.md)完成安装与接入，再按能力模块、系统设计或实测证据查阅对应文档。

## By capability / 按能力模块

| Capability / 能力模块 | Included guides & contracts / 包含指南与契约 |
|---|---|
| **Delete-guard** | [Recoverable deletion](../skills/delete-guard/SKILL.md) · [Cleanup Copilot](guides/cleanup-plan.md) · [Deletion limits](guides/deletion-limits.md) · [Recovery & GC](guides/recovery-feedback.md) · [MCP tools](guides/mcp.md) · [MCP doctor](guides/mcp.md#diagnose-a-local-service-028) |
| **Exfil-guard** | [Text inspection & redaction](../skills/exfil-guard/SKILL.md) · [Detection rules](../skills/exfil-guard/references/rules.md) · [Safe config view](../skills/exfil-guard/SKILL.md#safe-config-view) |
| **Guard-lab** | [Controlled experiments & evidence](lab/guard-lab.md) · [Four zero-model controls](lab/blue-team-calibration.md) · [Defensive research](research/README.md) |

Core and host adapters provide shared infrastructure, while [recovery-audit](../skills/recovery-audit/SKILL.md) covers post-incident workspace reconstruction.

| Category / 分类 | Contents / 内容 |
|---|---|
| [Guides](#guides) / 使用指南 | 安装接入、Cleanup、删除预算、MCP 服务、宿主能力矩阵与漂移检查 |
| [Design](#design) / 架构与契约 | 系统架构、威胁模型与兼容契约 |
| [Lab](#lab) / 防守验测 | 校准流程、任务模板与示例 |
| [Research](#research) / 防守研究 | 来源、发送授权、父子代理与任务效用研究 |
| [Reports](#reports) / 测试报告 | 按宿主与版本记录的实测证据与已知缺口 |
| [Releases](#releases) / 发布记录 | `0.3.0` 与各历史发行说明（当前主线维护见[变更摘要](releases/unreleased.md)） |
| [History](#history) / 历史设计与经验 | 早期设计方案、实现教训与事故记录 |

## Guides

| Document / 文档 | Scope / 范围 |
|---|---|
| [Getting started](guides/getting-started.md) | 安装包选择、Agent 接入提示词与无害校验 |
| [Agent setup prompt](guides/agent-setup-prompt.md) | Codex / DSH / Claude Code / Kimi Code 通用接入提示词与职责分工 |
| [Skill installation & updates](guides/skill-installation.md) | `0.3.0`：预览优先的 Skill 目录链接、Core 定位与版本升级核对 |
| [Harness capabilities](guides/harness-capabilities.md) | 各宿主当前接入状态、证据等级（`CLI` → `Enforcement observed`）与未验证路径 |
| [Host drift](guides/host-drift.md) | 宿主版本、配置指纹与本地基线预检 |
| [Cleanup Copilot](guides/cleanup-plan.md) | 零写入盘点计划、四类候选分类与显式确认清理 |
| [Directory overview & measurement](guides/cleanup-plan.md#directory-overview-and-measurement-029) | 先看目录概况再按范围计量；只读观察不授予执行许可 |
| [Deletion limits](guides/deletion-limits.md) | 隔离容量、会话预算、频率/重复限制、保护路径与批量确认 |
| [Status & cleanup feedback](guides/recovery-feedback.md) | 本地 Python / 插件状态区分、`ALLOW_NOOP` 边界、保留空间与手动 GC |
| [Local MCP tools](guides/mcp.md) | 本地 stdio 安全删除、恢复、事务查询与 GC 预览（显式工具，不替代 hook） |
| [Read-only cleanup MCP](guides/mcp.md#cleanup-observations-029) | `cleanup_discover` / `cleanup_measure`、固定工作区与部分观察状态 |
| [Reviewed maintenance MCP](guides/mcp.md#reviewed-maintenance-tasks-030-development) | `0.3.0`：保存清单、操作者批准交接、任务执行/查询及可选 Codex 原生表单 |
| [Maintenance output](guides/maintenance-output.md) | 人与 Agent 共享概况、只读分页详情与执行摘要格式 |
| [Unified read-only query](guides/read-only-query.md) | 任务、隔离事务与预算的统一终端概览与 JSON 查询 |
| [0.3.0 maintenance acceptance](reports/test-report-maintenance-0.3.0.md) | `0.3.0` 本地闭环、安装、MCP/宿主验收详情与延期项（含 Claude 模型检查） |
| [MCP diagnosis](guides/mcp.md#diagnose-a-local-service-028) | 本地 MCP 服务依赖、握手、工作区绑定与只读调用检查（`doctor.py mcp`） |
| [Trash locations](guides/trash-index.md) | 只读隔离区位置与共享 JSON 查询契约 |
| [Audit metadata](guides/audit-metadata.md) | 共享审计字段投影、不透明会话关联与保留边界 |
| [Publishing from ephemeral environments](guides/publishing-from-ephemeral-environments.md) | 临时环境中的 GitHub 发布操作参考 |
| [Release workflow](guides/release-process.md) | 维护者发布流程：发行说明渲染与发布后只读核验 |

Host-specific setup lives under [adapters/](../adapters/INTEGRATION.md), targeting Codex, DSH, Claude Code, and Kimi Code (see the [0.3.0 acceptance report](reports/test-report-maintenance-0.3.0.md) and [capability matrix](guides/harness-capabilities.md) for verified coverage and open gaps such as Claude's deferred model run). Other hosts can adapt Agent Guard locally against the integration contract.

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
| [Maintenance walkthrough](research/maintenance-2026-11/reproduce.md) | 使用现有 CLI 的新合成维护任务；批准、执行与恢复分工及核对范围 |
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

十一月的[维护任务与回执判读](research/maintenance-2026-11/README.md)单列一项获准维护任务和六个静态判读成员，
保留装配问题、无改善差异和问法限制，并提供[结构化摘要](research/maintenance-2026-11/results.json)与独立示例。

## Reports

Each report preserves its original version, configuration, result and limits.
Use the [capability matrix](guides/harness-capabilities.md) to interpret current
coverage rather than treating every historical result as current acceptance.

| Document / 文档 | Scope / 范围 |
|---|---|
| [Blue-team Lab, October 2026](reports/blue-team-lab-2026-10.md) | 来源／发送授权／任务效用与失败的去敏阶段摘要；不升级正式证据等级 |
| [Maintenance and receipt reading, November 2026](research/maintenance-2026-11/README.md) | 维护闭环与小样本判读分别记录；自用受控试验，不作外部反馈或总体成功率 |
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
| [0.3.0](releases/release-notes-0.3.0.md) | 保存清理任务、批准与执行交接、Codex原生表单及升级边界；安装前核实精确版本渠道 |
| [0.2.9](releases/release-notes-0.2.9.md) | 目录概况／计量、两只读 MCP 工具、扫描提速与语料门修复 |
| [0.2.8](releases/release-notes-0.2.8.md) | MCP 接入诊断、真实协议中断验证、安装与验收边界 |
| [0.2.7](releases/release-notes-0.2.7.md) | Cleanup、删除准入与MCP的版本范围、升级影响和准备阶段验证 |
| [0.2.6](releases/release-notes-0.2.6.md) | 正式版范围、升级影响与验收；Kimi 反馈／doctor、DSH 预检与版本扩展 |
| [Changes and maintenance details](releases/unreleased.md) | 未发布维护改动、0.3.0 范围及按检查点记录的验证；其后保留0.2.9、0.2.8、0.2.7及更早维护历史 |
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
