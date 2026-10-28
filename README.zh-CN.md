# AGENT-GUARD

[![CI](https://github.com/mokuyoaxis/agent-guard/actions/workflows/ci.yml/badge.svg)](https://github.com/mokuyoaxis/agent-guard/actions/workflows/ci.yml)
[![npm 稳定版](https://img.shields.io/npm/v/%40mokuyoaxis%2Fagent-guard.svg)](https://www.npmjs.com/package/@mokuyoaxis/agent-guard)
[![License](https://img.shields.io/github/license/mokuyoaxis/agent-guard)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![0.2.6](https://img.shields.io/badge/release-0.2.6-5B6B7A)](docs/releases/release-notes-0.2.6.md)

**让 AI Agent 的破坏性操作默认可逆。** · [English](README.md)

Agent Guard 在受支持的删除、覆盖操作前保留恢复数据，检查待外发文本中的
敏感信息，并提供 Lab 来测试 Agent 和宿主的实际行为。共享 Python Core
可通过命令行工具或宿主适配器调用。

**负责接入本项目的 AI agent：** 修改配置或编写桥接前，必须先读取
[适配要求](adapters/INTEGRATION.md)，再阅读
[接入流程](docs/guides/getting-started.md#set-up-a-coding-agent)和对应适配器指南。
确认实际宿主／版本，查阅其官方 hook 文档，完成验证后报告实际覆盖范围。
不能仅凭 Skill 已安装或配置已复制，就告诉用户防护已生效。

目前主要维护 **DSH 和 Kimi Code** 的接入。其他宿主建议交给对应的官方
编码 Agent 在本地配置。

| 能力 | 能帮助你做什么 | 入口 |
|---|---|---|
| **恢复保护** | 删除前迁移有价值的文件；受支持的 Git 覆盖前快照 tracked 状态；查询和恢复事务 | [delete-guard](skills/delete-guard/SKILL.md) |
| **清理提议（未发布源码）** | 扫描候选、分类、大小与实际执行方式；显式选择并确认后交给 safe_delete | [Cleanup Copilot](docs/guides/cleanup-plan.md) |
| **外发检测** | 检测受支持的凭据和本机标识路径；应用脱敏计划或拒绝输出 | [exfil-guard](skills/exfil-guard/SKILL.md) |
| **行为验测** | 校准观察器，验证声明信道的暴露，再比较匹配的关闭／开启防护试次 | [guard-lab](docs/lab/guard-lab.md) |

Skill 提供操作纪律，不会自行拦截工具调用。自动保护依赖宿主实际加载了
兼容 hook，且调用位于其覆盖范围内。Agent Guard 不是抵抗同权限恶意进程的
OS 沙箱；实际接入证据和缺口见[能力矩阵](docs/guides/harness-capabilities.md)。

<a id="guard-lab-合成蜜罐"></a>

## guard-lab：用证据验测行为

guard-lab 是**用户控制的实验工具**。它在一次性项目中放入非秘密合成标记和
无害诱饵，只观察明确声明的信道。真正的控制器与证据保存在交给受测 Agent 的
夹具之外。

流程分别回答三个问题：

1. **校准**：观察器、标记、扫描和证据链是否正常？内建对照不调用模型，
   不访问外网。
2. **观察暴露**：关闭防护的真实宿主试次是否触发了选定信道？
3. **比较防护**：有效基线成立后，匹配的开启防护试次是否减少或阻止了该暴露？

在安装包或源码根目录，从一项零模型对照开始：

```sh
lab_run="$(mktemp -d)"
python3 guard_lab.py run --case clean --output-dir "$lab_run/clean" --json
```

另三项对照是 `mock-positive`、`mock-injection` 和 `snapshot-positive`。
真实宿主实验前应按 [Lab 指南](docs/lab/guard-lab.md)跑齐四项对照；
安装包也提供 `agent-guard-lab` 命令。

对照 PASS 只表示**仪器校准**，不表示模型安全。观察器不健康或证据缺失时为
`INCONCLUSIVE`；未防护基线安静，不能证明防护有效。配对比较需要匹配相关的
harness／版本／模型、任务、协议及非防护配置。结果只覆盖声明信道，不能扩展
到所有文件读取、外网传输或模型请求。已有
[Kimi](docs/reports/test-report-kimi-guard-lab.md) 与
[DSH](docs/reports/test-report-dsh-guard-lab.md) 报告保留各自的有限结论、
失败和待审状态。

自 RC3 起，包中包含[蓝队研究](docs/research/README.md)、
[去敏实验报告](docs/reports/blue-team-lab-2026-10.md)和
[便携四控制示例](docs/lab/blue-team-calibration.md)，公开来源、发送授权和正常任务效用的
有界研究。私有装配中的验证不扩展生产 hook 覆盖。Lab 将缓慢、小规模推进，欢迎 fork、PR
与独立防守复现；失败和未定结果也应保留。

在包含该示例的源码或包根目录，可一次运行现有四种控制：

```sh
blue_run="$(mktemp -d)"
python3 docs/lab/examples/blue-team-calibration.py --output-dir "$blue_run/controls"
```

示例不调用模型，摘要只用于仪器校准。

## 安装与版本选择

Core 需要 Python 3.9+ 和 Git。示例采用 POSIX Shell；原生桥接和可选采集
工具另有运行要求。

| 版本 | 状态 |
|---|---|
| [0.2.6](docs/releases/release-notes-0.2.6.md) | 正式版；npm `latest`；发布地址见下方 |
| [0.2.5](docs/releases/release-notes-0.2.5.md) | 上一稳定版 |
| [0.2.4](docs/releases/release-notes-0.2.4.md) | 更早稳定版 |
| [0.2.4-hotfix.1](https://github.com/mokuyoaxis/agent-guard/releases/tag/0.2.4-hotfix.1) | 显式选择的回收站保护回补；npm `hotfix` |
| [0.2.5-rc3](docs/releases/release-notes-0.2.5-rc3.md) | 已发布预发布版，包含 RC1／RC2 维护；npm `rc` |

实际发布状态以精确版本的
[GitHub Release](https://github.com/mokuyoaxis/agent-guard/releases/tag/0.2.6)和
[npm 版本页](https://www.npmjs.com/package/@mokuyoaxis/agent-guard/v/0.2.6)为准，
checkout 或 manifest 本身不代表包已可安装。

稳定包可安装到用户选定的长期目录：

```sh
npm install --prefix /absolute/path/to/agent-guard-install @mokuyoaxis/agent-guard@0.2.6
```

包根目录是
`/absolute/path/to/agent-guard-install/node_modules/@mokuyoaxis/agent-guard`。
必须使用这个带作用域的包名，也可以使用源码 checkout：

```sh
git clone https://github.com/mokuyoaxis/agent-guard.git
cd agent-guard
```

选用已发布的预发布版时，指定 `@mokuyoaxis/agent-guard@0.2.5-rc3`，对应
[GitHub Release](https://github.com/mokuyoaxis/agent-guard/releases/tag/0.2.5-rc3)与
[npm 版本页](https://www.npmjs.com/package/@mokuyoaxis/agent-guard/v/0.2.5-rc3)。
若只需要稳定线的回收站保护修复，可显式选择 `@mokuyoaxis/agent-guard@0.2.4-hotfix.1`。
这两个通道均不替换稳定版 `latest`。

[0.2.6](docs/releases/release-notes-0.2.6.md) 在 0.2.5 的基础上补充 Kimi 可选 Stop
恢复反馈、独立 Stop doctor 检查、DSH Lab 预检诊断、DSH `0.2.1-alpha.4` 默认删除适配，
以及 Kimi `2.3.0` Bash profile。已有安装需要升级才能取得这些功能，Stop 还需显式配置。
接入步骤与 Agent 提示词见[入门指南](docs/guides/getting-started.md)，
功能边界见[维护说明](docs/releases/unreleased.md)。

## 恢复一次受支持的删除

从要保护的工作区调用你的安装包／源码路径：

```sh
python3 /absolute/path/to/agent-guard/skills/delete-guard/scripts/safe_delete.py src/old_module.py --dry-run
python3 /absolute/path/to/agent-guard/skills/delete-guard/scripts/safe_delete.py src/old_module.py
python3 /absolute/path/to/agent-guard/skills/delete-guard/scripts/restore.py list
python3 /absolute/path/to/agent-guard/skills/delete-guard/scripts/restore.py '<txid>'
```

有价值的目标迁入隔离区，manifest 保留准确恢复路径。可证明能够再生成且被
Git 忽略的产物可直接删除。受支持的 Git 覆盖操作先快照 tracked 状态；
hard reset 与 untracked／ignored 内容发生碰撞时拒绝执行。force restore
先保全当前占位版本，再恢复旧版本。错误可能留下部分操作，应先核查返回的
事务 ID、现场文件和状态，再决定后续动作。

0.2.5 明确提示：迁入回收站的数据仍占空间，
GC 需要显式执行，`ALLOW_NOOP` 不代表未识别命令没有破坏性。
本地状态查询也不能证明宿主已加载防护。见[状态与清理反馈](docs/guides/recovery-feedback.md)。

advisory `check.py` 和 `safe_delete.py --dry-run` 保持命令目标不变，但可能写入
隔离区／审计元数据及 Git 本地 exclude。advisory 退出码 0 不代表 BLOCK／ASK
操作获准执行。详见[策略与评估契约](skills/delete-guard/references/policy.md)。

RC1 已增加隔离区控制路径保护、从活跃 GC 计划排除 PURGED 事务，并最小化
restore／safe_delete 审计字段；CLI／manifest 仍保留准确恢复路径。
原始 0.2.4 包不含这些维护；0.2.4-hotfix.1 仅回补回收站控制路径保护。
见 [RC 说明](docs/releases/release-notes-0.2.5-rc1.md)。

RC2 进一步限定通用审计字段、使用不透明 session 关联，
并收口旧日志 status 展示与 GC 元信息，见[审计契约及保留边界](docs/guides/audit-metadata.md)。
同时补充 observer 启动诊断与取消健康记账；最初偶发超时的具体原因仍未知，
见 [RC2 范围与证据](docs/releases/release-notes-0.2.5-rc2.md)。

## 外发前检查文本

持有载荷的调用方主动运行扫描器，并按判决处理文本：

```sh
python3 skills/exfil-guard/scripts/check_span.py --channel file-write --json < draft.md
python3 skills/exfil-guard/scripts/sanitize.py --channel file-write < draft.md
```

在运行时生成完整的合成凭据示例：

```sh
python3 -c 'print("config: sk-proj-" + "AbCdEf0123456789GhIjKl")' |
  python3 skills/exfil-guard/scripts/sanitize.py --channel file-write
```

预期输出是 `config: sk-<REDACTED>`。检查器返回判决和计划，不改写载荷；
其退出码 0 表示 ALLOW／SANITIZE 评估成功，sanitizer 成功输出的 stdout 才是
允许或已改写的文本。ASK／BLOCK／错误时 sanitizer 不输出载荷。

检测覆盖文档列明的凭据形状、无需读值的秘密引用和特定本机路径。它不是通用
仓库扫描器，也不能抵抗刻意编码绕过。文本 CLI 不自动追加外发审计；
可信接入方负责实际外发和需要的审计存储。检查配置时，可用显式安全视图查看
支持的 JSON／dotenv 结构及状态；它不是可编辑副本，也不自动拦截普通读取。

详见 [Skill](skills/exfil-guard/SKILL.md)、
[规则与豁免诊断](skills/exfil-guard/references/rules.md)、
[信道契约](skills/exfil-guard/references/channels.md)。

## 接入你的编码 Agent

让对应宿主的官方编码 Agent 按[适配指南](adapters/INTEGRATION.md)，为当前
安装的版本配置 Agent Guard。优先使用能在工具执行前阻止调用的 hook。
我们主要维护 DSH 和 Kimi Code 的接入；其他宿主由 Agent 在本地配置桥接。

下表记录所测源码、宿主版本和工具路径；其他组合需要另行验证。

| 宿主 | 接入与证据 |
|---|---|
| Claude Code | 保留参考实现，暂缓新版本验收。Bash 原生 `PreToolUse`；2.1.270／2.1.273 的受限脚本宿主拦截记录。[接入](adapters/claude/README.md) |
| Kimi Code | Bash 原生 `PreToolUse`；0.42.0 的有限主／子路径及 2.1.1 的根代理 Bash 记录。0.2.6 新增 2.3.0 prompt／ACP 根代理 Bash 固定响应证据。ASK 拒绝；缺失／超时 hook 不覆盖。[接入](adapters/kimi-code/README.md) |
| DSH | 默认删除适配覆盖 0.1.5-rc.1／0.2.0-rc.2／0.2.1-alpha.4。可选原生 read 与 Lab 保留各自的两版边界。[兼容范围](docs/design/dsh-compatibility.md) · [接入](adapters/dsh/README.md) |
| Codex／其他宿主 | 推荐官方 Agent 按当前版本完成本地 hook 接入，并验证确实阻止执行。没有阻塞 hook 时，Skill／CLI 仍属于协作式使用。[适配指南](adapters/INTEGRATION.md) |

精确版本、试验与缺口统一见[完整能力矩阵](docs/guides/harness-capabilities.md)。
`doctor.py kimi|claude --probe` 只检查选定配置和本地适配路径；PASS 不证明
真实宿主已加载 hook。漂移检查也是本地预检。live sentinel 为显式操作，
可能消耗模型额度，只覆盖该次根代理 Bash 调用，见
[宿主漂移指南](docs/guides/host-drift.md)。

## 决策、事故恢复与文档

共享协议是 `ALLOW · SANITIZE · RELOCATE · SNAPSHOT · ASK · BLOCK`，
附带稳定 reason code 和说明。
聚合优先级为 `BLOCK > ASK > RELOCATE/SNAPSHOT > SANITIZE > ALLOW`。
不透明目标和保护边界拒绝执行；受支持的可恢复修改先补偿再继续。
授权状态属于本地操作纪律，不是隔离的身份边界。

事故后，[recovery-audit](skills/recovery-audit/SKILL.md)帮助依据尚存的
Git／会话／缓存证据恢复项目，区分原文、重建与确认缺失；没有来源保留的字节
无法凭空恢复。

| 文档 | 用途 |
|---|---|
| [入门指南](docs/guides/getting-started.md) | 包路径、Agent 接入提示词、安全验证 |
| [文档导航](docs/README.md) | 指南、设计、Lab、报告、发布与历史 |
| [架构](docs/design/architecture.md) | 共享 Core、补偿与适配器数据流 |
| [威胁模型](docs/design/threat-model.md) | 信任假设与尚未解决的边界 |
| [兼容契约](docs/design/compatibility.md) | 接口与发布约定 |
| [蓝队研究](docs/research/README.md) | 防守方法、去敏结果与零模型起点 |
| [0.2.6 说明](docs/releases/release-notes-0.2.6.md) | 发布范围、升级影响与验证边界 |

## 参与贡献

欢迎 fork、PR、独立防守复现、缺陷报告、兼容性证据和文档修订。分享研究资料或日志前阅读
[CONTRIBUTING.md](CONTRIBUTING.md)，通过
[Issues](https://github.com/mokuyoaxis/agent-guard/issues) 和
[Pull Requests](https://github.com/mokuyoaxis/agent-guard/pulls)参与；
不得公开凭据、私有配置和未脱敏事故记录。

## 社区友链

[![LINUX DO 社区友链](assets/linux-do-community.svg)](https://linux.do/t/topic/2942799)

这张自制横幅直达我们的
[LINUX DO 项目帖](https://linux.do/t/topic/2942799)，不代表社区官方推荐。

## 许可证

MIT —— 见 [LICENSE](LICENSE)。
