[![CI](https://github.com/mokuyoaxis/agent-guard/actions/workflows/ci.yml/badge.svg)](https://github.com/mokuyoaxis/agent-guard/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/mokuyoaxis/agent-guard)](https://github.com/mokuyoaxis/agent-guard/releases)
[![License](https://img.shields.io/github/license/mokuyoaxis/agent-guard)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Node.js 20 smoke](https://img.shields.io/badge/Node.js-20%20smoke-339933?logo=nodedotjs&logoColor=white)](.github/workflows/ci.yml)

[![Codex tested](https://img.shields.io/badge/Codex-gpt--5.6--sol%20medium%20%2B%20high-000000?logo=openai&logoColor=white)](docs/test-report-codex-gpt-5.6-sol.md)
[![DSH live-tested](https://img.shields.io/badge/DSH-v0.1.1%20DeepSeek%20V4%20Pro%20high%20minimal-4D6BFE)](docs/test-report-dsh-v0.1.1.md)
[![ZCode live-tested](https://img.shields.io/badge/ZCode-GLM--Flash%20win32%20live--tested-7C5CE0)](docs/test-report-zcode-glm-flash.md)
[![Claude Code live-tested](https://img.shields.io/badge/Claude%20Code-2.1.270%20hook%20live--tested%20mock%20model-D97757?logo=anthropic&logoColor=white)](docs/test-report-claude-code-harness.md)
[![DSH v0.1.0 history](https://img.shields.io/badge/DSH-v0.1.0%20friction%20log-8B8B8B)](docs/friction.md)

# agent-guard

**让 AI Agent 的破坏性操作默认可逆。**
**[English](README.md)**

Agent 正在越来越多地自主执行 shell 命令。当命令是 `rm -rf` 时,一个错误的变量、
一次误判的上下文,就足以让整个仓库灰飞烟灭。agent-guard 让破坏*默认可逆*，
并在支持的修改前持久记录 intent，可接入任何能跑 Python 的 harness。

> **Agent Guard 不是审批系统,而是带人工升级的自动恢复系统。**
> 只要操作保持可逆,Agent 就不被打断;只有当 Guard 无法安全代办、
> 而用户意图又可能合理时,决策才升级给人类。
>
> 它是可靠性基础设施,**不是安全沙箱**:它防的是判断失误与上下文错误,
> 不是拥有相同 OS 权限的恶意 Agent。

## 四根支柱

| 支柱 | 保证 |
|---|---|
| **Scope(边界)** | Workspace 边界、`.git` 与外部路径永不可删 |
| **Recoverability(可恢复)** | 删除先迁移到 `.agent-trash/` 并记录 manifest;git 覆写先做快照 |
| **Authorization(授权)** | 会话级能力;否决即单向降权,只有人类能恢复 |
| **Auditability(审计)** | 强制判决、补偿 intent、结果与恢复写入追加式 JSONL；intent 无法持久化时拒绝修改 |

贯穿四者的一条原则:**不确定性提升限制**(fail-closed)。

## 决策协议

稳定的跨 harness 接口不是 allow/block,而是一套 Decision Protocol:

```
效果 → 分类器 → 策略 → Decision   ∈ { ALLOW, SANITIZE, RELOCATE,
                                     SNAPSHOT, ASK, BLOCK }
                           + ReasonCode   (稳定机器码)
                           + Explanation  (面向人类的解释)
                           + RecoveryPlan (txid 与补偿策略)
```

| 层级 | 判决 | Agent 的体验 |
|---|---|---|
| **SAFE** | `ALLOW` · `SANITIZE` · `RELOCATE` · `SNAPSHOT` | 静默执行;补偿先行;凭 txid 可恢复。`SANITIZE` 改写的是**载荷**而非命令,返回脱敏计划 |
| **AMBIGUOUS** | `ASK` | 单次执行授权(`ASK_ONCE`)——例如 Guard 无法安全代办的复合形态 |
| **FORBIDDEN** | `BLOCK` | 附理由与修正建议拒绝;永不升级为询问 |

当一个操作同时命中多个判决时,由弱到强的优先级为:

```
ALLOW < SANITIZE < RELOCATE < SNAPSHOT < ASK < BLOCK
```

`SANITIZE` 排在 `ASK` **之下**是有意的:它属于自动化的 SAFE 层级,
而 `ASK` 放弃了自动化。载荷中若同时存在可脱敏的密钥与无法改写的形态,
必须 `ASK`——当一部分外发内容无法检查时,不能静默放行。

真正的效果不确定(`$VAR` 目标、`bash -c`、`find -delete`、管道喂入列表)
一律走 BLOCK:放行它们等于放弃核心保证。各适配器把判决映射到原生机制——
DSH 的 `PreToolDecision`、Claude Code PreToolUse 的 `ask`,不支持询问的
harness 则降级为"携带解释的拒绝"。

## 两个 Guard 分支

`delete-guard` 回答"这次破坏还能回头吗";`exfil-guard` 回答"这份内容本该
离开本机吗"——同一套决策协议互为镜像:删除先补偿再执行,泄露先脱敏再发出,
而发出之后没有任何东西可以恢复。`delete-guard` 把守**删除之前**,
`exfil-guard` 把守**发出之前**。

## exfil-guard

**它是什么。** 一个面向"发出前"的过滤器,针对 Agent 即将写入、发送、提交或
推送的文本。它拦住两类会被误发的内容:**已知凭据**,以及**带本机标识的
绝对路径**。它是脱敏型 Guard,不是补偿引擎——内容一旦发出便无法找回,
因此它的设计核心是*预防 + 判决记录*,而非撤销。

它是**安全沙箱的反面**,也不阻止对抗性外泄。它防的是判断失误,
不是拥有相同 OS 权限的恶意 Agent。

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
`cat /proc/self/environ`)。它**从不读取值**——正是这条不变量保证了
Guard 自身的输出、日志与审计行不含密钥。

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

## 快速开始

零第三方依赖。要求:Python 3.9+、POSIX shell、git。

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

harness 适配——在任何 shell 命令执行前拦截:

```bash
python3 skills/delete-guard/scripts/check.py --enforce -- "$COMMAND"
case $? in 0) 执行 "$COMMAND" ;; 2) 拒绝 ;; 3) 交由用户决定 ;; esac
```

## 受保护行为一览

```text
rm -rf build/            → RELOCATE  (整树隔离后放行)
rm -rf .                 → BLOCK     (workspace 根)
rm -rf $DIR/             → BLOCK     (目标无法解析:fail-closed)
rm *.log                 → BLOCK     (不透明通配;safe_delete 会显式展开)
cd X && rm -rf build     → ASK_ONCE  (COMPOUND_CWD_DELETE)
touch f && rm f          → ASK_ONCE  (COMPOUND_CREATE_DELETE)
git clean -fd            → RELOCATE  (先 -n 枚举迁移再放行)
git reset --hard         → SNAPSHOT  (先 stash,可 apply 找回)
git push --force         → BLOCK     (远端历史不交给 Agent 自动处理)
node_modules/(已 ignore) → ALLOW     (可证明可再生)
隔离区写满               → BLOCK     (绝不回退到永久删除)
```

## 适配器

| Harness | 状态 | 机制 |
|---|---|---|
| **DSH**(DeepSeek Harness) | **已发布插件**；`v0.1.1` 真机测试(DeepSeek V4 Pro high,极简模式) | `dsh plugin --profile <p> add github:mokuyoaxis/agent-guard`——瀑布拦截 + 工具 + 提示层 |
| **Codex** | 主审(`gpt-5.6-sol`,high)；先 medium，后 medium + high 前向测试 | `delete-guard` skill + `workspace-write` 下的 CLI；`.git` 只读时支持预先 ignore 的 `.agent-trash/` |
| **Claude Code** | 就绪(`adapters/claude/`) | PreToolUse hook → `permissionDecision` allow/ask/deny |
| OpenCode / MCP | 规划中 | 待一致性保证在两个适配器上验证后再扩 |

跨 harness 保证(由 `tests/test_conformance.py` 强制):同一命令、同一 cwd、
同一 workspace 状态,经任何适配器必须产出完全一致的 decision + reason code。

## 目录结构

```
agent-guard/
├── skills/delete-guard/   # Agent 行为层:SKILL.md + CLI 脚本
├── skills/exfil-guard/    # 出口侧技能:check_span.py · sanitize.py
├── core/                  # classifier · policy · recovery · audit · redaction
├── adapters/claude/       # Claude Code PreToolUse hook 适配器
├── tests/                 # unittest 测试套件,含跨 harness 一致性
└── docs/                  # architecture · threat-model · friction log
```

Skill 负责 Agent 行为引导,约束全部下沉 Core。未来的 `git-guard`、
`database-guard`、`cloud-guard` 直接挂同一补偿引擎,无需重构仓库。

## 文档

| 阅读 | 内容 |
|---|---|
| [docs/architecture.md](docs/architecture.md) | 四柱↔组件映射、数据流、关键设计决定 |
| [docs/threat-model.md](docs/threat-model.md) | 诚实边界:它是什么、不是什么 |
| [docs/friction.md](docs/friction.md) | 真实 Agent 撞出来的教训(F1–F11) |
| [docs/test-report-codex-gpt-5.6-sol.md](docs/test-report-codex-gpt-5.6-sol.md) | v0.1.1 Codex 评估(medium + high) |
| [docs/test-report-dsh-v0.1.1.md](docs/test-report-dsh-v0.1.1.md) | v0.1.1 DSH 真机测试(DeepSeek V4 Pro high,极简模式) |
| [skills/delete-guard/references/policy.md](skills/delete-guard/references/policy.md) | 完整规则表与判决码 |
| [skills/exfil-guard/references/rules.md](skills/exfil-guard/references/rules.md) | exfil 规则表、reason code、豁免格式与审计结构 |
| [skills/exfil-guard/references/channels.md](skills/exfil-guard/references/channels.md) | 出口信道分类与不可达信道 |

## 状态与路线图

`v0.1.1` 是在 DSH `v0.1.0` 真机使用、首次 `gpt-5.6-sol` medium
前向测试、high 主审、第二轮 medium + high 并行前向测试，以及 DSH
极简模式下 DeepSeek V4 Pro `high` 真机运行之后的可靠性加固版。
它加入预写式迁移 intent、Git 转义路径安全、Git 补偿
fail-closed、只读 `.git` 下不污染工作树的审计预检、明确的
`RESTORABLE` / `RESTORED` 生命周期状态和 DSH 运行时 smoke test。
仍使用 patch 版本是有意的:当前验证矩阵只覆盖两个模型家族、两个 harness。
下一步将扩展 Codex/Claude/DSH 的模型与 reasoning level，
并推进 Windows 原生 shell 方言(Phase 1-2 已落地:cmd/PowerShell
的纯逻辑分词与效果映射在 `core/dialects.py`,PowerShell 无歧义参数前缀展开,
方言选择已接入 `check.py --dialect`、`AGENT_GUARD_DIALECT` 与两个适配器,
POSIX 行为与默认路径不变;真实 Windows 端到端验证仍需 Windows 机器),以及同一补偿引擎上的
`database-guard` / `cloud-guard`。`v0.2.0` 增加第二个 Guard 分支:
**`exfil-guard`**——新增 `SANITIZE` 判决、文本 span 分类器
(`core/redaction.py`),以及无需改动适配器即可使用的
`check_span.py` / `sanitize.py` 命令行。按兼容性契约这是 **minor** 版本
(新增判决类、新增 reason code,并在 README 公告)。

## 许可证

MIT —— 见 [LICENSE](LICENSE)。
