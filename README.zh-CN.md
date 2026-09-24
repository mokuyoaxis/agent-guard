# AGENT-GUARD

[![CI](https://github.com/mokuyoaxis/agent-guard/actions/workflows/ci.yml/badge.svg)](https://github.com/mokuyoaxis/agent-guard/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/mokuyoaxis/agent-guard)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![v0.2.0 源码](https://img.shields.io/badge/%E6%BA%90%E7%A0%81-v0.2.0-5B6B7A)](https://github.com/mokuyoaxis/agent-guard/releases)

**让 AI Agent 的破坏性操作默认可逆。** · [English](README.md)

Agent Guard 是给编码 Agent 用的可靠性工具：让受支持的高风险操作尽可能
可恢复，而不是一次失手就永久损失；日常工作则尽量不被打断。

- **删除文件**：可先迁入 `.agent-trash/`，留下恢复清单，而非直接销毁。
- **破坏性 Git 操作**：可先保存可恢复的状态，再覆盖工作树。
- **意外外发**：合作式文本 CLI 可检查已知凭据和带本机标识的绝对路径；
  持有载荷的调用方按判决应用脱敏计划、请求人工处理或阻断。

共享 Core 支持 Python 3.9+ 和 Git，不依附某个 harness。能否自动拦截，
仍取决于宿主有没有兼容 hook；仅安装 Skill 不会自动拦截工具调用。
Core、决策协议与 Skills 是产品本体，适配器只是可替换的接入桥。

> **能安全恢复的操作尽量自动完成；不能安全代办时再交给人。**
> Agent Guard 是可靠性基础设施，不是安全沙箱：它防范失误，
> 不承诺抵抗拥有相同系统权限的恶意 Agent。

## 它会怎样处理

```text
rm -rf build/       → RELOCATE   # 工作区内目录先迁入隔离区
rm -rf .            → BLOCK      # 保护工作区根目录
git reset --hard    → SNAPSHOT   # Git 状态允许时先做快照
git push --force    → BLOCK      # 不自动改写远端历史
```

这是受支持输入的**示意判决**，不是让你执行这些命令，也不表示所有宿主都会
自动拦截。被忽略且可再生的目标可能判为 `ALLOW`；Git 快照无法建立时会
保守拒绝。能恢复或安全改写时，Agent 可以继续工作；否则交给人或阻断。

## 让编码 Agent 帮你接入

先把本仓库放在稳定的本地路径；如果已经有 checkout，跳过克隆：

```sh
git clone https://github.com/mokuyoaxis/agent-guard.git
cd agent-guard
```

Core 需要 Python 3.9+ 和 Git；是否能自动拦截取决于宿主是否提供相应 hook。
把下面这段交给编码 Agent，先替换为你的仓库路径：

```text
请从 /absolute/path/to/agent-guard 为当前工作区接入 agent-guard。
先识别当前 harness 实际支持的 hook 与 Skill，阅读本 README 和对应 adapter
说明，并检查 Python、Git。安装适用的 Skills；只有宿主确实支持时才配置
原生 shell hook。保留现有设置；修改用户级配置或安装依赖前先展示差异并征求确认。
Claude Code 参考 adapters/claude/README.md，Kimi Code 参考
adapters/kimi-code/README.md，DSH 参考 adapters/dsh/README.md。
Codex 或没有已验证 hook 的宿主只接入 Skill/CLI，并明确说明没有自动拦截。
用无害命令和仅作为数据传给 check.py 的 BLOCK 样例验证；不要真正执行
破坏性测试命令。最后报告实际安装内容、宿主确实拦截的范围和未验证路径。
```

手动接入与证据边界见 [harness 能力矩阵](docs/harness-capabilities.md)
及对应的 adapter README。

## 设计原则

| 原则 | 做法 |
|---|---|
| **守住边界** | 操作进入 Guard 时，阻断工作区根、`.git` 和外部路径的删除 |
| **先保留退路** | 受支持的删除先迁入 `.agent-trash/` 并记 manifest；破坏性 Git 覆写先做快照 |
| **约束授权** | 授权只在会话内有效；否决会单向降权，只有人能恢复 |
| **留下记录** | 强制判决、补偿 intent、结果和恢复写入追加式 JSONL；intent 无法持久化时拒绝修改 |

贯穿四项原则的一条规则是：**越不确定，限制越严格。**

## 决策协议

每个进入 Guard 的操作都会按效果分类，再选择足以维持安全或恢复承诺的
最宽松判决。稳定的跨 harness 接口不是简单的 allow/block，而是一套
Decision Protocol：

```
效果 → 分类器 → 策略 → Decision   ∈ { ALLOW, SANITIZE, RELOCATE,
                                     SNAPSHOT, ASK, BLOCK }
                           + ReasonCode   (稳定机器码)
                           + Explanation  (面向人类的解释)
                           + RecoveryPlan (txid 与补偿策略)
```

| 层级 | 判决 | Agent 的体验 |
|---|---|---|
| **SAFE** | `ALLOW` · `SANITIZE` · `RELOCATE` · `SNAPSHOT` | 尽量不中断工作；需要时先补偿，可恢复的修改凭 txid 找回。`SANITIZE` 返回由**载荷持有方**应用的脱敏计划，不改写命令 |
| **AMBIGUOUS** | `ASK` | 单次执行授权(`ASK_ONCE`)——例如 Guard 无法安全代办的复合形态 |
| **FORBIDDEN** | `BLOCK` | 附理由与修正建议拒绝;永不升级为询问 |

当一个操作同时命中多个判决时，由弱到强的优先级为：

```
ALLOW < SANITIZE < RELOCATE < SNAPSHOT < ASK < BLOCK
```

`SANITIZE` 排在 `ASK` **之下**是有意的：它属于自动化的 SAFE 层，
而 `ASK` 需要人处理。载荷里同时有可脱敏的密钥和无法改写的部分时，
不能只处理前者便静默放行。

真正无法确定效果的命令（如 `$VAR` 目标、`bash -c`、`find -delete`）
会被拒绝；放行它们就无法守住边界。适配器再把判决映射到宿主机制：
DSH 的 `PreToolDecision`、Claude Code PreToolUse 的 `ask`，或在不支持
询问的宿主中附带解释的拒绝。

## Agent Guard 包含什么

### `delete-guard`

回答“删了还能找回来吗？”通过受支持的适配器或 CLI 调用时，
它在删除或破坏性 Git 操作前检查，并在可恢复时先做补偿。

### `exfil-guard`

回答“这份内容本该离开本机吗？”载荷持有方主动调用其合作式 CLI 时，
它在发出前检查文本，并针对受支持的模式返回脱敏或升级判决。

### `recovery-audit`

如果预防没有运行或没有覆盖那条路径，它负责事故后的证据整理：
区分原文恢复、依据重建与确认缺失，审计回放工具，并把落地、提交、
推送和发布保留为独立授权门。

`delete-guard` 和 `exfil-guard` 是两条预防分支；
`recovery-audit` 负责事后的证据驱动恢复。

## recovery-audit

有时预防根本没有机会运行：harness 没有 adapter、子代理绕开预期路径，或范围过大的
命令在人工介入前删掉了 workspace。工作树可能已经消失，但编码 Agent 的会话缓存里
仍可能保存成功 patch、文件快照、工具结果、diff 与命令上下文。

`recovery-audit` 把这些残留，与 Git remote/reflog/stash、编辑器或工具缓存、构建产物
和项目计划一起组织成证据驱动的恢复流程：

- 每个单元明确标记为**原文恢复（recovered）**、**依据重建（reconstructed）**或
  **确认缺失（missing）**；
- 按真实时间顺序回放工具效果，并检查记录与回放是否分歧；
- 同一份冻结证据重复回放，必须得到逐字节一致的树；
- 落地、commit、push 与 release 始终是互相独立的授权门。

它不是文件系统 undelete，也不能创造任何幸存来源从未保存过的字节。它承诺的是：
尽快恢复到证据真正支持的最强项目状态，并把缺口写清楚，而不是藏起来。

## exfil-guard

`exfil-guard` 检查 Agent 即将写入、发送、提交或推送的文本，前提是
**载荷持有方主动调用它的 CLI**。它还提供对指定 JSON/dotenv 配置文件的
显式只读安全视图。文本扫描针对两类意外外发：**已知凭据**和
**带本机标识的绝对路径**。依据信道，Guard 可放行、返回脱敏计划、
请求人处理或阻断。

它做的是预防和脱敏，不是补偿：内容发出去后就不能撤销。它也**不是
安全沙箱**，不负责抵抗拥有相同系统权限的 Agent 蓄意外泄。

### exfil-guard 的四种判决

完整决策协议仍然适用,但文本载荷只会落到其中四类
(`RELOCATE`/`SNAPSHOT` 属于 delete-guard——Guard 无法改写自己没写过的东西):

| 判决 | 含义 | 示例 |
|---|---|---|
| `ALLOW` | 无匹配,或命中受控占位符、工作区内相对路径 | `echo "hello" \| check_span.py` |
| `SANITIZE` | 返回脱敏计划;由**载荷持有方**改写后再发出 | `file-write` / `llm-request` 上的真实密钥 |
| `ASK` | 信道既不能改写也无法收回 | `shell-stdout` 上的本机路径 |
| `BLOCK` | 拒绝:不可变/远端历史、载荷无法扫描、配置非法 | `git-push-payload` 中的凭据 |

### 检测什么

**T1 厂商凭据特征**(`secret/*`,确定性,误报接近零)。rule id 包括:
`secret/openai-key`、`secret/github-token`、`secret/aws-access-key-id`、
`secret/gitlab-token`、`secret/slack-token`、`secret/stripe-key`
(仅 live key,`sk_test_` 豁免)、`secret/jwt`(结构化:头部必须 base64
解码为含 `alg` 的 JSON),以及 `secret/private-key-block`
(整段 `-----BEGIN ... PRIVATE KEY-----` 一次性脱敏)。冻结规则表见
[skills/exfil-guard/references/rules.md](skills/exfil-guard/references/rules.md)。

**不读值的密钥引用**(`secret/source-reference`)。Guard 只对变量**名**
(`*KEY*`、`*TOKEN*`、`*SECRET*`、`*PASSWORD*`、`*CRED*`、`*AUTH*`)与
密钥库**文件名**(`.env`、`*.pem`、`id_rsa*`、`.netrc`、`kubeconfig` 等)
分类,并识别整环境展开(`printenv`、`env | ...`、
`cat /proc/self/environ`)。这个扫描器**不解析变量的值**；这不代表其他
Guard 输出或已有审计记录都已证明不含秘密。

**带本机标识的路径**(`path/*`)。`path/workspace-relative` 为 `ALLOW`
(工作区豁免);`path/system`(`/usr`、`/etc`、`C:\Windows`)为 `ALLOW`;
`path/host-absolute`(位于 `HOME`/`TEMP`、CI 根或工作区祖先之下)为
`SANITIZE`;`path/generic-absolute`(与本机无关联)为 `ASK`;
`path/device`(UNC、`\\?\`、管道)为 `SANITIZE`。

### 信道决定处置

信道由两个事实定义:能否**改写**、发出后是否**留存**。`rewritable`
决定了 `SANITIZE` 是否有意义;`persistence` 决定了 `BLOCK` 是否成立。

| 信道 | 可改写 | 留存 | 默认 |
|---|---|---|---|
| `llm-request` | 是 | 远端 | SANITIZE |
| `file-write` | 是 | 工作区 | SANITIZE |
| `forge-comment` / `issue-body` / `pr-description` | 是 | 公开 | SANITIZE |
| `git-commit-message` | 是(改 argv) | 远端历史 | **BLOCK** |
| `git-push-payload` | 否 | **远端** | **BLOCK** |
| `shell-stdout` | **否** | 本地记录 | ASK |
| `shell-file-redirect` | 是 | 本地 | ASK |
| `archive-upload` | 是 | 远端 | ASK |
| `process-argv` | 是 | 本地 | ASK |

信道名未知属于配置缺陷,而非"无风险":`check_span.py` 返回
`BLOCK_OUTPUT_UNSCANNABLE`,绝不隐式放行。

### 使用示例

`check_span.py` 从 **stdin** 读取载荷,是纯函数——不写文件、不改写、
也不打印匹配内容。`sanitize.py` 应用 Guard 返回的计划。

```bash
# 可改写信道上的凭据 -> SANITIZE,退出码 0
echo 'config: sk-proj-AbCdEf…' | python3 skills/exfil-guard/scripts/check_span.py --channel file-write

# 即将进入远端历史的凭据 -> BLOCK,退出码 2
echo 'token=ghp_abcdefghijklmnopqrstuvwxyz…' | python3 skills/exfil-guard/scripts/check_span.py --channel git-push-payload

# 应用脱敏计划(保留格式:sk-<REDACTED>)
echo 'config: sk-proj-AbCdEf…' | python3 skills/exfil-guard/scripts/sanitize.py --channel file-write
```

退出码契约:`0` = ALLOW/SANITIZED · `2` = BLOCK · `3` = ASK · `1` = ERROR。
`--json` 输出机器可读判决(仅偏移、rule id 与占位符——**绝不含匹配到的
字节**);`--path` 为即将写入的文件启用仓库本地豁免文件。

### 不打印值地查看配置

此 CLI 从 `0.2.0` 源码开始提供，**不属于**此前的 `0.2.0-rc2`
源码预览标签。

```bash
python3 skills/exfil-guard/scripts/view.py --workspace /path/to/workspace .env
python3 skills/exfil-guard/scripts/view.py --workspace /path/to/workspace config.json
```

文件路径必须相对该工作区。JSON 结果保留字段名与结构，以及标量类型和
`set`/`empty` 状态，**不返回标量值**；已知密钥形态的字段名也会隐藏，
但未知秘密藏在字段名中仍是局限。仅支持 UTF-8 JSON 与严格的单行 dotenv
子集（最多 256 KiB、16 层、2048 个节点）。符号链接、硬链接、特殊文件、
越界路径、无效格式或平台缺少安全的相对目录描述符读取能力时一律拒绝。
退出码 `0` 表示产生视图，`2` 表示拒绝，`1` 表示内部错误。视图仅供诊断，
不能写回覆盖原配置；它也不会拦截 harness 的普通文件读取工具。

### 与 delete-guard 的关系

它们是同一承诺在动作两侧的两半:

| | `delete-guard` | `exfil-guard` |
|---|---|---|
| 问题 | "还能回头吗?" | "这份内容本该离开吗?" |
| 把守 | **删除之前** | **发出之前** |
| 响应 | 先补偿,再执行 | 先脱敏,再发出 |
| 失误代价 | 可凭 txid 恢复 | **不可逆** |
| 入口 | `check.py -- <command>` | `check_span.py`(stdin) |

二者共享词汇表(`core/policy.py`)、聚合逻辑(`worst()`)、豁免纪律与审计
日志。`worst()` 由两个 Guard 共用,这正是 `SANITIZE` 的排序只需定义一次的原因。

### 覆盖范围与局限

这里如实说明,因为一个夸大自身能力的可靠性工具就是一份虚假的安全声明:

- **不是沙箱。** 它不阻止对抗性外泄。把密钥混淆以绕过扫描的 Agent 不在
  范围内;它抓的是**意外**。
- **没有 hook 的信道在结构上不可达。** 无代理的托管模型调用、模型自身的
  工具调用、程序内部产生的内容、人类剪贴板,一律**不给判决**——不作任何
  覆盖声明。详见 `references/channels.md` 与
  [docs/secret-guard-analysis.md](docs/secret-guard-analysis.md) §2.4 的
  可达性表。
- **不是文件扫描器。** 它不是 gitleaks 的替代品;它扫描 Guard 在**发出
  路径上**能看到的内容。
- **不改写历史。** 检测到已经进入 git 历史的密钥最多只是一份报告。
  改写历史是需要人类执行、且自带风险的动作。
- **本版本不含 T3 熵检测器。** 它是最大的单一误报来源,而目标场景并不需要它。

## 手动使用（不依赖特定 harness）

Core 没有第三方依赖。需要 Python 3.9+、POSIX shell 和 Git。

```bash
# 删除文件/目录/glob —— 进入隔离区而非销毁:
python3 skills/delete-guard/scripts/safe_delete.py build/ --reason "stale"

# 查看状态与恢复:
python3 skills/delete-guard/scripts/status.py
python3 skills/delete-guard/scripts/restore.py list
python3 skills/delete-guard/scripts/restore.py <txid>

# 隔离区维护(默认只出计划,不动数据):
python3 skills/delete-guard/scripts/gc.py
```

受支持的 harness 适配器可在 shell 命令执行前调用 Guard，再将退出码映射
为宿主自己的工具判决：

```text
python3 skills/delete-guard/scripts/check.py --enforce -- "$COMMAND"
退出码 0 → 宿主可以执行原命令
退出码 2 → 拒绝
退出码 3 → 宿主支持时询问用户；否则拒绝
退出码 1 → Guard 出错，保守拒绝
```

## 受保护行为一览

下列是命令确实进入 Guard、且目标符合所述条件时的示意结果；各宿主实际
验证到的范围见 [能力矩阵](docs/harness-capabilities.md)。

```text
rm -rf build/            → RELOCATE  (整树隔离后放行)
rm -rf .                 → BLOCK     (workspace 根)
rm -rf $DIR/             → BLOCK     (目标无法解析:fail-closed)
rm *.log                 → BLOCK     (不透明通配;safe_delete 会显式展开)
cd X && rm -rf build     → ASK_ONCE  (COMPOUND_CWD_DELETE)
touch f && rm f          → ASK_ONCE  (COMPOUND_CREATE_DELETE)
git clean -fd            → RELOCATE  (先 -n 枚举迁移再放行)
git reset --hard         → SNAPSHOT  (Git 状态允许建立快照时)
git push --force         → BLOCK     (远端历史不交给 Agent 自动处理)
node_modules/(已 ignore) → ALLOW     (可证明可再生)
隔离区写满               → BLOCK     (绝不回退到永久删除)
```

## 接入与验证矩阵

[![Node.js 20 smoke](https://img.shields.io/badge/Node.js-20%20smoke-339933?logo=nodedotjs&logoColor=white)](.github/workflows/ci.yml)
[![Codex Skill/CLI tested](https://img.shields.io/badge/Codex-Skill%2FCLI%20tested-000000?logo=openai&logoColor=white)](docs/test-report-codex-gpt-6-astra-high.md)
[![DSH v0.1.1 live-tested](https://img.shields.io/badge/DSH-v0.1.1%20live--tested-4D6BFE)](docs/test-report-dsh-v0.1.1.md)
[![ZCode win32 CLI evaluated](https://img.shields.io/badge/ZCode-win32%20CLI%20evaluated-7C5CE0)](docs/test-report-zcode-glm-flash.md)
[![Claude Code hook tested with scripted model](https://img.shields.io/badge/Claude%20Code-hook%20tested%20%28scripted%20model%29-D97757?logo=anthropic&logoColor=white)](docs/test-report-claude-code-harness.md)
[![Kimi Code K3 hook observed](https://img.shields.io/badge/Kimi%20Code-K3%20hook%20observed-5B9BD5)](docs/harness-capabilities.md)

“Core 可用”、“受 Skill 引导的 Agent 使用过”和“harness 会强制拦截每次匹配的
工具调用”是三种不同强度的结论：

| Harness | 接入层级 | 证据与边界 |
|---|---|---|
| **Claude Code** | 原生 `PreToolUse` adapter | 使用真实 CLI 与 hook、模拟模型端点完成真机测试；映射 allow/ask/deny |
| **DSH**（DeepSeek Harness） | 原生 adapter | `v0.1.1` 真机测试；提供瀑布拦截、模型工具与提示层。安装：`dsh plugin --profile <p> add github:mokuyoaxis/agent-guard` |
| **Codex** | Skill + 生产 CLI 验收 | 已主审及前向测试；本仓库不声称存在 Codex 原生透明拦截 hook |
| **ZCode** | Windows 上的 Skill/CLI 评估 | 已用 GLM-Flash 在 win32 真机测试；证明可移植路径，不等于通用 hook 保证 |
| **Kimi Code 0.42.0** | 原生 `PreToolUse` adapter（Bash） | 两个独立沙盒使用同一 `local/kimi-k3` 模型，观察到 root、单子代理、并发双子代理的可恢复操作进入 hook；Core `ASK` 在适配器处被拒绝，不会提示确认。宿主对 `BLOCK` 判决的执行级拦截仍未证实。 |
| OpenCode / MCP | 规划中 | 尚无支持声明 |

`tests/test_conformance.py` 覆盖共享 Core 和 Claude adapter；DSH 有 smoke 测试，
Kimi 有针对性 adapter 测试。上述 Kimi 观察只覆盖实测调用，不构成所有 Shell
语法或一般并发子代理安全保证。
各宿主的覆盖范围、证据等级和执行级验收条件见
[harness 能力矩阵](docs/harness-capabilities.md)。

## 目录结构

```
agent-guard/
├── skills/delete-guard/   # Agent 行为层:SKILL.md + CLI 脚本
├── skills/exfil-guard/    # 出口侧技能:check_span.py · sanitize.py
├── skills/recovery-audit/ # 证据驱动的仓库审计与恢复
├── core/                  # classifier · policy · recovery · audit · redaction
├── adapters/claude/       # Claude Code PreToolUse hook 适配器
├── adapters/kimi-code/   # Kimi Code PreToolUse hook 适配器
├── adapters/dsh/          # DeepSeek Harness 接入桥
├── adapters/codex/harness/# CLI 验收 driver；不是原生 hook
├── tests/                 # unittest 测试套件,含跨 harness 一致性
└── docs/                  # architecture · threat-model · friction log
```

Skill 负责 Agent 行为引导,约束全部下沉 Core。未来的 `git-guard`、
`database-guard`、`cloud-guard` 直接挂同一补偿引擎,无需重构仓库。

## 文档

| 阅读 | 内容 |
|---|---|
| [docs/architecture.md](docs/architecture.md) | 四柱↔组件映射、数据流、关键设计决定 |
| [docs/release-notes-0.2.0.md](docs/release-notes-0.2.0.md) | 0.2.0 变更、证据等级与已知限制 |
| [docs/threat-model.md](docs/threat-model.md) | 诚实边界:它是什么、不是什么 |
| [docs/friction.md](docs/friction.md) | 真实 Agent 撞出来的教训(F1–F11) |
| [docs/development-note-unguarded-deletion.md](docs/development-note-unguarded-deletion.md) | 去标识化事故探索与面向恢复的后续方向 |
| [docs/test-report-codex-gpt-5.6-sol.md](docs/test-report-codex-gpt-5.6-sol.md) | v0.1.1 Codex 评估(medium + high) |
| [docs/test-report-dsh-v0.1.1.md](docs/test-report-dsh-v0.1.1.md) | v0.1.1 DSH 真机测试(DeepSeek V4 Pro high,极简模式) |
| [skills/recovery-audit/SKILL.md](skills/recovery-audit/SKILL.md) | 证据优先级、确定性回放、恢复与落地门禁 |
| [skills/delete-guard/references/policy.md](skills/delete-guard/references/policy.md) | 完整规则表与判决码 |
| [skills/exfil-guard/references/rules.md](skills/exfil-guard/references/rules.md) | exfil 规则表、reason code、豁免格式与审计结构 |
| [skills/exfil-guard/references/channels.md](skills/exfil-guard/references/channels.md) | 出口信道分类与不可达信道 |

## 状态与路线图

当前源码版本为 **v0.2.0**。它保留 v0.1.1 已加固的恢复路径（预写式迁移
intent、Git 快照安全、干净的审计预检和明确的 `RESTORABLE` / `RESTORED`
生命周期），并新增 cmd/PowerShell 方言解析、`SANITIZE` 判决、`exfil-guard`
以及证据驱动的 `recovery-audit` Skill。

相较 `v0.2.0-rc2` 源码预览，本工作树还加入显式只读配置安全视图，并减少
新 `check.py` 结果与本地记录中的原始命令副本；历史追加式记录不会自动
改写。已发布产物及状态请以
[GitHub Releases](https://github.com/mokuyoaxis/agent-guard/releases) 为准。

该版本的项目身份与 harness 无关。现有 DSH、Claude adapter，Codex/ZCode
验收证据，以及有边界的 Kimi 实测，只是不断扩展的兼容矩阵，不分别定义产品。
Kimi 结果证明所测调用走通了 hook 补偿路径，不证明宿主强制执行所有 `BLOCK`，
也不意味着任意 Agent 操作都受保护。真实 Windows 端到端覆盖与一般并发子代理
安全仍是明确缺口。
后续 `git-guard`、`database-guard`、`cloud-guard` 继续复用同一协议与补偿引擎。

## 0.2.x 预告：guard-lab 合成蜜罐

这是规划中的可选实验，**不属于 0.2.0**。它会在离线、一次性的测试项目
里放入无认证能力的合成标记，对照正常任务与提示词注入诱导；另设试次观察
harness 是否在 Agent 未请求读取时自行索引或外发文件。正负对照、独立观察器
和证据分级将区分“提出读取”“本地接触”与“证实越过外部边界”。不使用真实
凭据，不默认常驻后台；它也不是抵抗恶意模型或宿主的安全保证。

## 社区友链

[![LINUX DO 社区友链](assets/linux-do-community.svg)](https://linux.do/t/topic/2942799)

这张自制横幅直达我们的
[LINUX DO 项目帖](https://linux.do/t/topic/2942799)，不代表社区官方推荐。

## 许可证

MIT —— 见 [LICENSE](LICENSE)。
