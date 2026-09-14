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
效果 → 分类器 → 策略 → Decision   ∈ { ALLOW, RELOCATE, SNAPSHOT,
                                     ASK, BLOCK }
                           + ReasonCode   (稳定机器码)
                           + Explanation  (面向人类的解释)
                           + RecoveryPlan (txid 与补偿策略)
```

| 层级 | 判决 | Agent 的体验 |
|---|---|---|
| **SAFE** | `ALLOW` · `RELOCATE` · `SNAPSHOT` | 静默执行;补偿先行;凭 txid 可恢复 |
| **AMBIGUOUS** | `ASK` | 单次执行授权(`ASK_ONCE`)——例如 Guard 无法安全代办的复合形态 |
| **FORBIDDEN** | `BLOCK` | 附理由与修正建议拒绝;永不升级为询问 |

真正的效果不确定(`$VAR` 目标、`bash -c`、`find -delete`、管道喂入列表)
一律走 BLOCK:放行它们等于放弃核心保证。各适配器把判决映射到原生机制——
DSH 的 `PreToolDecision`、Claude Code PreToolUse 的 `ask`,不支持询问的
harness 则降级为"携带解释的拒绝"。

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
├── core/                  # classifier · policy · recovery · audit
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
`database-guard` / `cloud-guard`。

## 许可证

MIT —— 见 [LICENSE](LICENSE)。
