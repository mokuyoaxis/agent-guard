# Documentation / 文档导航

Start with [getting started](guides/getting-started.md) for installation and
setup. The three main paths are [recovery protection](../skills/delete-guard/SKILL.md),
[disclosure checks](../skills/exfil-guard/SKILL.md), and
[behaviour experiments](lab/guard-lab.md). Use the
[harness capability matrix](guides/harness-capabilities.md) for observed coverage
and the [architecture](design/architecture.md) for the shared Core.

Previous published RC baseline: [0.2.5-rc1](releases/release-notes-0.2.5-rc1.md).
Published stable baseline: [0.2.4](releases/release-notes-0.2.4.md).
Current RC: [0.2.5-rc2](releases/release-notes-0.2.5-rc2.md),
including [shared audit metadata](guides/audit-metadata.md) and observer startup maintenance;
publication is established by its GitHub/npm receipts.

先看入门指南，再按恢复保护、外发检测、Lab 验测选择入口。测试报告按所测版本与配置保留；发布记录、
历史设计和外部案例各自归档，不能据此扩展当前产品的承诺。

| Category / 分类 | Contents / 内容 |
|---|---|
| [Guides](#guides) / 使用指南 | 宿主接入证据、漂移检查、维护者发布指南 |
| [Design](#design) / 架构与契约 | 当前架构、威胁模型与兼容契约 |
| [Lab](#lab) / 实验与候选素材 | Lab 流程、任务模板、外部攻击案例 |
| [Reports](#reports) / 测试报告 | 按宿主、版本和观察范围记录的证据 |
| [Releases](#releases) / 发布记录 | 正式版与预览版各自的范围 |
| [History](#history) / 历史设计与经验 | 早期设计、实现教训和事故探索 |

## Guides

| Document / 文档 | Scope / 范围 |
|---|---|
| [Getting started](guides/getting-started.md) | 安装路径、Agent 接入提示词与最短验证流程 |
| [Harness capabilities](guides/harness-capabilities.md) | 当前接入范围、证据等级与未验证路径 |
| [Host drift](guides/host-drift.md) | 宿主版本、配置与本地基线检查 |
| [Trash locations](guides/trash-index.md) | 0.2.4 的只读位置查询、Agent／前端共享 JSON 契约 |
| [Audit metadata](guides/audit-metadata.md) | RC2 候选的字段投影、会话关联、GC／status 契约与保留边界 |
| [Publishing from ephemeral environments](guides/publishing-from-ephemeral-environments.md) | 临时环境中的 GitHub 发布操作参考 |

Adapter setup remains in [adapters/](../adapters/INTEGRATION.md). Skill contracts
remain in [skills/](../skills/delete-guard/SKILL.md).

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
| [Clean task](lab/tasks/guard-lab-clean-task.md) | 干净对照任务模板 |
| [Injection trial task](lab/tasks/guard-lab-trial-task.md) | 提示注入试次任务模板 |
| [Native-read trial task](lab/tasks/guard-lab-read-trial-task.md) | 原生读取脱敏任务模板 |
| [fake-ai-api reference](lab/cases/injection-case-fake-ai-api.md) | 外部攻击端候选素材；尚未作为 Lab 样本验证 |

Task templates and external case references are separate from completed test
reports. A scripted provider checks host behavior; a real-model injection
trial needs an effective unguarded baseline before a matched comparison.

## Reports

Each report preserves its original version, configuration, result and limits.
Use the [capability matrix](guides/harness-capabilities.md) to interpret current
coverage rather than treating every historical result as current acceptance.

| Document / 文档 | Scope / 范围 |
|---|---|
| [0.2.3 contract review](reports/test-report-release-readiness-0.2.3.md) | 发布前契约复核与 DSH 原生验收 |
| [0.2.4 official Flash / PTC](reports/test-report-dsh-0.2.4-ptc.md) | 实模主／子代理碰撞拒绝；原始主 PTC 任务失败与独立收尾分别记录 |
| [Claude Code harness](reports/test-report-claude-code-harness.md) | 脚本模型驱动的真实宿主 hook 验证 |
| [Kimi Code blocking](reports/test-report-kimi-code-block.md) | 所测版本的 Bash BLOCK 与故障路径 |
| [Kimi Code Lab](reports/test-report-kimi-guard-lab.md) | 历史配对试次与后续本地复核 |
| [Codex / GPT-6 Astra](reports/test-report-codex-gpt-6-astra-high.md) | Skill／CLI 验收，不声明原生拦截 |
| [Codex / GPT-5.6 Sol](reports/test-report-codex-gpt-5.6-sol.md) | v0.1.1 历史 Skill／CLI 评估 |
| [DSH 0.1.5-rc.1](reports/test-report-dsh-0.1.5-rc.1.md) | 打包插件与执行级 Bash 验收 |
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
| [0.2.5-rc2](releases/release-notes-0.2.5-rc2.md) | 当前维护 RC；共享审计、observer 维护与兼容边界 |
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
| [Friction log](history/friction.md) | F1–F20 实现发现与修复经验 |
| [Unguarded deletion note](history/development-note-unguarded-deletion.md) | 去标识化事故探索与恢复方向 |
| [Secret Guard analysis](history/secret-guard-analysis.md) | 实现前的需求分析 |
| [Secret Guard design](history/secret-guard-design.md) | 早期设计候选与原范围 |
| [Secret Guard architecture](history/secret-guard-architecture.md) | 早期架构草图；不代表拦截点已交付 |
