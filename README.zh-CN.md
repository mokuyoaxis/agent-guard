<h1 align="center">
  <img src="assets/agent-guard-logo.svg" alt="AGENT-GUARD" width="760">
</h1>

<p align="center">
  <a href="https://github.com/mokuyoaxis/agent-guard/actions/workflows/ci.yml"><img src="https://github.com/mokuyoaxis/agent-guard/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://www.npmjs.com/package/@mokuyoaxis/agent-guard"><img src="https://img.shields.io/npm/v/%40mokuyoaxis%2Fagent-guard.svg" alt="npm 稳定版"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/mokuyoaxis/agent-guard" alt="License"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&amp;logoColor=white" alt="Python 3.9+"></a>
  <a href="docs/releases/release-notes-0.3.1.md"><img src="https://img.shields.io/badge/notes-0.3.1-5B6B7A" alt="0.3.1 说明"></a>
</p>

<p align="center">
  <strong>外发检查 · 可恢复删除 · Agent 验测</strong><br>
  <a href="README.md">English</a>
</p>

编码 Agent 经常在可预见的地方出错：一条范围过宽的 `rm` 清空未提交的改动，一段调试日志把凭据带进提交或提示词，或者宿主工具升级后旧 hook 悄悄失效。**Agent Guard** 基于共享 Python Core（`3.9+`）与可选宿主适配层，为编码 Agent 提供确定性的安全检查点与可控的行为验测。

> [!IMPORTANT]
> **负责接入本项目的 AI Agent：** 修改宿主配置或编写桥接前，请先阅读[适配要求](adapters/INTEGRATION.md)、[接入流程](docs/guides/getting-started.md#set-up-a-coding-agent)以及对应宿主适配指南。核实实际宿主与版本号并查阅其官方 hook 文档，只如实报告已验证的覆盖范围——不能仅凭“Skill 已安装”或“配置已复制”就判断防护已生效。

## 核心能力一览

| 能力模块 | 核心功能 | 指南与契约 |
|---|---|---|
| **Delete-guard** | 受支持破坏性删除前的隔离保全与 Git 快照；Cleanup Copilot 盘点与获准维护任务；按工作区生效的容量、速率/重复限制与保护路径；事务恢复、手动 GC 与可选本地 MCP 工具 | [Skill 与 CLI](skills/delete-guard/SKILL.md) · [Cleanup Copilot](docs/guides/cleanup-plan.md) · [删除限制](docs/guides/deletion-limits.md) · [恢复与 GC](docs/guides/recovery-feedback.md) · [MCP 工具](docs/guides/mcp.md) · [MCP doctor](docs/guides/mcp.md#diagnose-a-local-service-028) |
| **Exfil-guard** | 扫描待外发文本中已覆盖的凭据格式、凭据源引用与宿主标识路径；基于计划验证的安全脱敏；不暴露标量值的 JSON / `.env` 配置结构视图（`safe_view`） | [Skill 与 CLI](skills/exfil-guard/SKILL.md) · [检测规则](skills/exfil-guard/references/rules.md) · [安全配置视图](skills/exfil-guard/SKILL.md#safe-config-view) |
| **Guard-lab** | 在一次性夹具中运行四项零模型校准对照，投放非秘密合成标记，经由独立声明信道观察行为并执行关闭／开启防护的配对比较 | [Lab 指南](docs/lab/guard-lab.md) · [四控制示例](docs/lab/blue-team-calibration.md) · [证据等级](docs/lab/guard-lab.md#evidence-levels) |

Core 与适配器构成上述模块的共享底座。若事故已经发生，可辅助使用 [recovery-audit](skills/recovery-audit/SKILL.md) 从尚存的 Git、会话与缓存记录中审计并重建工作区。

## 工作机制与能力边界

- **行为引导与真实拦截分离：** Skill 为 Agent 提供操作纪律，自动拦截则依赖宿主实际加载的兼容 hook（如 `PreToolUse` 或 DSH 插件）及其覆盖的工具路径。可选本地 MCP 适配器暴露显式维护工具，不会拦截绕过它的原生命令或编辑器操作。
- **工程可靠性基建，而非 OS 沙箱：** Agent Guard 与 Agent 运行在**同一操作系统权限级别**。它的目标是兜住常见失误——工作目录错位、命令误展开、意外外发——而非隔离同权限的恶意进程（详见[威胁模型](docs/design/threat-model.md)）。
- **受支持破坏性操作不确定即收紧（Fail-closed）：** 各链路共用统一决策协议（`ALLOW · SANITIZE · RELOCATE · SNAPSHOT · ASK · BLOCK`，聚合优先级 `BLOCK > ASK > RELOCATE/SNAPSHOT > SANITIZE > ALLOW`）。当识别到受支持的破坏性操作（或无法解析的引号／语法），且无法静态确定其目标或效果时——例如包含未解析变量、命令替换、间接 Shell 解释器或经标准输入传入目标列表的删除命令——系统拒绝猜测并返回 `BLOCK`；未命中受支持破坏性模式的普通命令（例如输出 `$PWD` 或 `$(pwd)`）仍返回 `ALLOW_NOOP`（详见[策略契约](skills/delete-guard/references/policy.md)）。
- **额度按工作区根目录生效：** Delete-guard 的预算、速率限制与保护路径通过工作区根目录的 `.agent-guard/delete-policy.json` 统一管理；同一仓库内的应用和库共用该策略，未设置字段沿用内置默认值（详见[工作区配置](docs/guides/deletion-limits.md#configuration-and-defaults)）。

<a id="安装与版本选择"></a>

## 安装与版本

Core 需要 **Python 3.9+** 与 **Git**（示例默认使用 POSIX Shell）。可选本地 MCP 适配器需要 **Python 3.10+** 及单独安装的[固定版本 MCP SDK](docs/guides/mcp.md)。

将当前稳定版 **`0.3.1`** 安装到长期保留的用户目录：

```sh
npm install --prefix /absolute/path/to/agent-guard-install @mokuyoaxis/agent-guard@0.3.1
```

安装后的包根目录为 `/absolute/path/to/agent-guard-install/node_modules/@mokuyoaxis/agent-guard`（请保留 `@mokuyoaxis/` 作用域）。也可以直接使用源码仓库：

```sh
git clone https://github.com/mokuyoaxis/agent-guard.git
cd agent-guard
```

具体发行资产可在 [GitHub Release (`0.3.1`)](https://github.com/mokuyoaxis/agent-guard/releases/tag/0.3.1) 与 [npm (`0.3.1`)](https://www.npmjs.com/package/@mokuyoaxis/agent-guard/v/0.3.1) 查看。离线 tarball 安装、版本升级及扩展运行要求（如可选 DSH 读取原型需 Node 22、Kimi TOML 辅助需 Python 3.11+）见[入门指南](docs/guides/getting-started.md)。

<details>
<summary>版本演进与可选通道（0.2.4 – 0.3.1）</summary>

| 版本 | 核心变更 |
|---|---|
| **[0.3.1](docs/releases/release-notes-0.3.1.md)** | Cleanup 终端确认屏重构（永久删除置顶、可读/精确容量、超 20 根目录汇总与只读清单）、CLI 唯一短前缀 ID、多目标 `--targets-file` 引导、精确 `txid` 与 `gc.py --task` 清理、跨文件系统暂存恢复与 `manual_review_items`、`agent-guard-delete-policy` 额度 CLI、三套 Skill 安装器 |
| **[0.3.0](docs/releases/release-notes-0.3.0.md)** | 审核后批量维护工作流（保存明确目标计划、操作者单次批准、Agent 按任务 ID 执行与查询）、可选强制隔离、linked Skill 安装器、可选 Codex 原生确认表单 |
| **[0.2.9](docs/releases/release-notes-0.2.9.md)** | Cleanup 只读目录概况（`--discover`）与选定范围计量（`--measure`）、两项只读 MCP 工具、计划扫描性能优化 |
| **[0.2.8](docs/releases/release-notes-0.2.8.md)** | 本地 MCP 连接诊断（`doctor.py mcp`）与协议中断恢复核验 |
| **[0.2.7](docs/releases/release-notes-0.2.7.md)** | Cleanup Copilot 计划、共享删除准入控制（容量、预算、速率／重复限制、保护路径）、可选本地 MCP 服务端 |
| **[0.2.6](docs/releases/release-notes-0.2.6.md)** | Kimi 可选 Stop 恢复反馈与 doctor 检查、DSH Lab 预检诊断、DSH `0.2.1-alpha.4` 默认删除适配、Kimi `2.3.0` Bash profile |
| **[0.2.5](docs/releases/release-notes-0.2.5.md)** / **[0.2.4](docs/releases/release-notes-0.2.4.md)** | 早期稳定版（显式存储／GC／NOOP 反馈、持久迁移记录与 `trash_index` 查询） |
| **[0.2.5-rc3](docs/releases/release-notes-0.2.5-rc3.md)** / **[0.2.4-hotfix.1](https://github.com/mokuyoaxis/agent-guard/releases/tag/0.2.4-hotfix.1)** | 显式选择的独立通道（对应 npm `rc` 与 `hotfix` 标签，不覆盖稳定版 `latest`）；`0.2.4-hotfix.1` 仅在 `0.2.4` 上回补隔离区控制路径保护 |

说明：自 `0.2.7` 起引入的删除准入限制可能拒绝此前版本能够直接放行的批量或无保护删除。从旧版本升级时需显式执行安装或更新步骤，系统不会自动迁移既有全局 Skill 或宿主配置。

</details>

## 核心工作流

<a id="恢复一次受支持的删除"></a>

### 1. 可恢复删除与审核维护（`Delete-guard`）

在受保护的工作区根目录调用安装包或源码目录下的脚本：

```sh
python3 /absolute/path/to/agent-guard/skills/delete-guard/scripts/safe_delete.py src/old_module.py --dry-run
python3 /absolute/path/to/agent-guard/skills/delete-guard/scripts/safe_delete.py src/old_module.py
python3 /absolute/path/to/agent-guard/skills/delete-guard/scripts/restore.py list
python3 /absolute/path/to/agent-guard/skills/delete-guard/scripts/restore.py '<txid>'
```

**执行与恢复契约：**
- **保全与直接清理：** 有价值的目标迁入隔离区，manifest 保留精确恢复路径；可证明能够再生成且被 Git 忽略的产物可直接删除（操作者显式要求隔离时除外）。
- **Git 快照与强制恢复：** 受支持的 Git 覆盖操作会先为 tracked 状态创建快照，hard reset 与 untracked 或 ignored 文件发生碰撞时拒绝执行；`--force` restore 会先通过独立事务保全当前占位文件再执行替换。
- **无害预览边界：** `check.py` 与 `safe_delete.py --dry-run` 不会改动命令目标，但可能写入隔离区／审计元数据以及本地 Git exclude。advisory 检查退出码 0 仅说明评估完成，绝非允许执行 `BLOCK` 或 `ASK` 操作的许可。
- **空间、GC 与部分失败：** 隔离数据仍占用磁盘，需要手动执行 GC 清理；`ALLOW_NOOP` 只说明未命中受支持破坏性模式，不证明未知命令无害。执行报错可能留下部分中间状态，重试前请核查返回的事务 ID 与磁盘状态。详见[状态与清理反馈](docs/guides/recovery-feedback.md)与[审计元数据](docs/guides/audit-metadata.md)。

**审核后批量维护（`0.3.0`）：** Cleanup Copilot 将“方案生成”、“操作者审核批准”与“Agent 执行”拆开。先保存明确的目标清单（可指定强制隔离可再生产物），由操作者在终端审阅完整清单并一次性批准——或在启用可选 [Codex 原生表单](docs/guides/mcp.md#host-confirmation-030-development)后，打开导出的完整清单（`host_approval.review.file`），审阅选中目标再通过表单确认——随后 Agent 即可通过 CLI 或 MCP 按任务 ID 执行与查询，同时继续受路径保护、预算和速率限制约束。默认只读盘点（`--discover`、`--measure`）不写入也不授予删除许可；`--save-plan` 与 `--approve` 会持久化控制记录，但不会提前移动文件。

<a id="外发前检查文本"></a>

### 2. 外发前检查与脱敏（`Exfil-guard`）

在写草稿、外发消息或粘贴日志前，使用 `check_span.py` 评估或 `sanitize.py` 脱敏。在受保护的项目目录调用安装包／源码路径（如已在源码仓根目录可省略前缀）：

```sh
python3 /absolute/path/to/agent-guard/skills/exfil-guard/scripts/check_span.py --channel file-write --json < draft.md
python3 /absolute/path/to/agent-guard/skills/exfil-guard/scripts/sanitize.py --channel file-write < draft.md
```

可用运行时拼接的合成凭据验证输出：

```sh
python3 -c 'print("config: sk-proj-" + "AbCdEf0123456789GhIjKl")' |
  python3 /absolute/path/to/agent-guard/skills/exfil-guard/scripts/sanitize.py --channel file-write
# 预期输出 -> config: sk-<REDACTED>
```

**职责划分：**
- **`check_span.py` 只评估不改写：** 返回判决（`ALLOW`、`SANITIZE`、`ASK`、`BLOCK`）与可验证的脱敏计划，退出码 0 表示 `ALLOW` 或 `SANITIZE`。
- **`sanitize.py` 仅在安全时产出文本：** 在 `ALLOW`/`SANITIZE` 时输出允许或脱敏后的文本；遇到 `ASK`、`BLOCK` 或校验错误时静默阻断 stdout。
- **检测边界与审计：** 覆盖文档定义的凭据格式、无需读值的凭据源引用与特定宿主标识路径，不是全仓秘钥审计器，也不防御刻意编码混淆；外发决策与外发审计日志仍由调用方负责。
- **无标量配置视图：** 需要查看配置结构时，可通过 `safe_view` 只读展示受支持 JSON / `.env` 的键结构与状态，不返回敏感标量值，也不自动拦截普通文件读取。

规则与信道契约见 [Exfil-guard Skill](skills/exfil-guard/SKILL.md)、[检测规则](skills/exfil-guard/references/rules.md)与[信道契约](skills/exfil-guard/references/channels.md)。

<a id="guard-lab-合成蜜罐"></a>

### 3. 受控行为验测（`Guard-lab`）

**Guard-lab** 用可控实验检验宿主与防护链路是否真正按预期工作，而不暴露任何真实秘密。它在一次性夹具中放入非秘密合成标记与无害诱饵，并将控制器与可检出篡改的哈希链证据保存在受测 Agent 夹具之外。

实验流程严格按三步推进：
1. **仪器校准：** 确认观察器、标记检测与哈希链证据均能通过四项零模型控制测试。
2. **确认基线暴露：** 在关闭防护时运行试次，确认受测信道确实会产生触发（安静的无防护基线不能证明防护有效）。
3. **开启防护对比：** 保持宿主版本、模型、任务、协议与其他配置一致，再进行配对比较。

切换到安装包根目录或源码仓库根目录，运行单个零模型校准（安装包也可直接使用 `agent-guard-lab`）：

```sh
lab_run="$(mktemp -d)"
python3 guard_lab.py run --case clean --output-dir "$lab_run/clean" --json
```

在包含该校准示例的安装包或源码根目录，可一次性运行全部四项零模型校准（`clean`、`mock-positive`、`mock-injection`、`snapshot-positive`）：

```sh
blue_run="$(mktemp -d)"
python3 docs/lab/examples/blue-team-calibration.py --output-dir "$blue_run/controls"
```

> [!NOTE]
> 对照 PASS 代表**实验仪器校准通过**，不代表模型本身安全。哈希链校验可检出完整性异常并将损坏或丢失的证据记为 `INCONCLUSIVE`，但同权限进程仍有能力改写本地记录与哈希；实验结论也仅覆盖明确声明的观察信道。详情参考 [Lab 指南](docs/lab/guard-lab.md)、[四控制示例](docs/lab/blue-team-calibration.md)、[蓝队研究](docs/research/README.md)，以及既有 [Kimi](docs/reports/test-report-kimi-guard-lab.md) 与 [DSH](docs/reports/test-report-dsh-guard-lab.md) 报告。

<a id="接入你的编码-agent"></a>

## 宿主接入与实测覆盖范围

让编码 Agent 结合[通用提示词](docs/guides/agent-setup-prompt.md)与[适配指南](adapters/INTEGRATION.md)，依据实际安装的包根目录和工作区完成配置。建议将 MCP 显式维护工具与经验证的阻塞 hook／插件配合使用；可使用 `install_skill.py` 将 `skills/delete-guard` 链接到兼容的 Skill 目录（详见[Skill 安装指南](docs/guides/skill-installation.md)）。

下表中，**MCP 与维护闭环（`0.3.0`）**一列的结果均来自各自记录的 `0.3.0` 发布前检查点（并非对最终已发布包逐项复跑），而**原生拦截证据与已知缺口**则沿用各适配器各自对应的实测记录（例如 DSH `0.2.1-alpha.4` 默认删除适配是在 `0.2.6` 检查点完成验证，Claude 原生 `Bash` hook 证据对应 `2.1.270`／`2.1.273`）。所有结论均仅适用各自实测宿主版本与工具路径，其他版本或组合仍需单独验证（完整记录见[能力矩阵](docs/guides/harness-capabilities.md)与 [0.3.0 验收报告](docs/reports/test-report-maintenance-0.3.0.md#host-acceptance)）：

| 宿主 | 实测版本 | MCP 与维护闭环（`0.3.0`） | 原生拦截证据与已知缺口 | 相关文档 |
|---|---|---|---|---|
| **Codex** | `0.165.0` | 已通过新批准 71 根批量、真实缓存任务及可选原生表单模型试次，隔离与恢复均由控制器独立核验 | 原生 Shell／编辑器拦截需另行建立证据；默认保持终端批准 | [验收报告](docs/reports/test-report-maintenance-0.3.0.md#host-acceptance) · [早期试次](docs/guides/mcp.md#codex-01650-with-published-027) |
| **Kimi Code** | `2.3.0` | 已完成普通及新批准 MCP 维护闭环 | 已实测当前源码下的根代理 Bash 原生拦截 → 计划保存 → 终端批准 → 原会话执行与独立恢复；Stop 与超时边界另记 | [验收报告](docs/reports/test-report-maintenance-0.3.0.md#host-acceptance) · [接入指南](adapters/kimi-code/README.md) |
| **DSH** | `0.2.0-rc.2` | 已通过修复原会话根后的启动及新批准 MCP 执行 | 保留 `0.2.0-rc.2`／`0.2.1-alpha.4` 原生插件证据；后续原生试次在 PRoot 环境受制于缺失 OS 沙箱（独立零模型本地对照通过） | [验收报告](docs/reports/test-report-maintenance-0.3.0.md#host-acceptance) · [兼容契约](docs/design/dsh-compatibility.md) · [接入指南](adapters/dsh/README.md) |
| **Claude Code** | `2.1.311` / `2.1.270–273` | `2.1.311` 已连通并发现 10 个 MCP 工具；真实模型维护试次延期 | 原生 Bash hook 证据保留在 `2.1.270`／`2.1.273` 的脚本宿主运行中；`2.1.311` 新版 hook 尚未验证 | [验收报告](docs/reports/test-report-maintenance-0.3.0.md#host-acceptance) · [接入指南](adapters/claude/README.md) |
| **其他宿主** | — | 支持 Skill / CLI / MCP 协作式运行 | 参照[适配指南](adapters/INTEGRATION.md)完成本地接入，并实测确实阻塞危险调用后再声明原生拦截能力 | [适配指南](adapters/INTEGRATION.md) |

日常巡检可使用 `doctor.py kimi|claude --probe` 与 `--check-drift` 对照兼容性档案检查本地配置（`PASS` 仅代表本地接线完整，不证明实时宿主已加载 hook）。显式 `live sentinel` 会发起单次根代理 Bash 采样并可能消耗模型额度，详见[宿主漂移指南](docs/guides/host-drift.md)。

## 文档索引

| 文档 | 内容重点 |
|---|---|
| [入门指南](docs/guides/getting-started.md) | 安装方式、Agent 接入提示词与安全非破坏校验 |
| [文档导航](docs/README.md) | 全量指南、设计文档、Lab、测试报告、发行记录与历史归档 |
| [系统架构](docs/design/architecture.md) | 四大支柱、Decision Protocol、Shell 方言、补偿与数据流 |
| [威胁模型](docs/design/threat-model.md) | 同权限假设、攻击面与向内核隔离演进的路径 |
| [兼容契约](docs/design/compatibility.md) | 适配器接口约定与版本兼容边界 |
| [蓝队研究](docs/research/README.md) | 防守实验方法、去敏报告与零模型校准基线 |
| [0.3.1 发布说明](docs/releases/release-notes-0.3.1.md) · [0.3.0 说明](docs/releases/release-notes-0.3.0.md) | 版本功能汇总、升级影响与验收范围 |

## 参与贡献

欢迎通过 [Issues](https://github.com/mokuyoaxis/agent-guard/issues) 与 [Pull Requests](https://github.com/mokuyoaxis/agent-guard/pulls) 反馈 Bug、补充兼容性证据、改进文档或提交专注的代码修订。分享研究资料或运行日志前，请先阅读 [CONTRIBUTING.md](CONTRIBUTING.md)，切勿包含凭据、私有配置或未脱敏的事故现场数据。

## 社区讨论

[![LINUX DO 社区友链](assets/linux-do-community.svg)](https://linux.do/t/topic/2942799)

作者团队在 [LINUX DO](https://linux.do/t/topic/2942799) 维护的项目讨论帖。

## 许可证

MIT —— 见 [LICENSE](LICENSE)。
