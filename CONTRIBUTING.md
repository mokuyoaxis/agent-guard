# Contributing to Agent Guard / 为 Agent Guard 做贡献

Agent Guard welcomes bug reports, design discussions, compatibility evidence,
documentation improvements, and focused code contributions.

Agent Guard 欢迎缺陷报告、设计讨论、兼容性证据、文档改进与范围明确的代码贡献。

## English

### Start with an Issue

Please search the existing [Issues](https://github.com/mokuyoaxis/agent-guard/issues)
before opening a new one. An Issue is especially useful before work that changes
a public interface, security policy, package layout, dependency, harness
adapter, or release process.

A useful report includes:

- the Agent Guard version or commit;
- operating system, Python/Node version, harness version, and relevant tool;
- a minimal, non-destructive reproduction;
- expected and observed behavior;
- whether the evidence comes from Core/CLI, a mock host, a real hook, or an
  execution-level sentinel.

Do not post credentials, authentication files, private provider configuration,
raw incident archives, identifying absolute paths, or unredacted model/session
logs. If a problem cannot be described safely in public, open only a minimal
redacted Issue requesting private coordination.

### Send a Pull Request

Focused [Pull Requests](https://github.com/mokuyoaxis/agent-guard/pulls) are
welcome. For a non-trivial change, link the Issue that established its scope.

Before opening a PR:

1. Keep Core and the Decision Protocol harness-neutral; put host-specific
   normalization and result mapping in the adapter.
2. Add regression tests for behavior changes and update the relevant public
   documentation and evidence boundary.
3. Run `npm test`. Also run any narrower adapter or packaging check affected by
   the change.
4. Use only isolated fixtures and synthetic data. Never validate a guard by
   deleting real user data, rewriting a real remote, or exposing a real secret.
5. Do not add production dependencies or change public APIs, versions, release
   automation, or security policy without prior maintainer agreement.
6. Preserve existing authorship and unrelated work. Keep the PR focused enough
   to review and revert independently.

In the PR description, state what changed, what was tested, what remains
unverified, and whether the result is a local Core/CLI check, a mock-host check,
or real-host evidence. A passing mock must not be described as universal host
enforcement.

Review, merge, tagging, GitHub Release, and npm publication are separate
maintainer decisions. Acceptance of a contribution does not promise a specific
release date.

## 中文

### 先发 Issue

提交前请先搜索现有 [Issues](https://github.com/mokuyoaxis/agent-guard/issues)。
如果改动涉及公开接口、安全策略、包结构、生产依赖、harness 适配器或发布流程，
建议先通过 Issue 确认范围。

一份便于复现的报告应包括：

- Agent Guard 版本或提交；
- 操作系统、Python/Node 版本、harness 版本与相关工具；
- 最小、无破坏性的复现步骤；
- 预期行为与实际行为；
- 证据来自 Core/CLI、模拟宿主、真实 hook，还是执行级哨兵。

不要公开凭据、认证文件、私有 provider 配置、事故原始归档、带身份信息的绝对
路径，或未经去敏的模型／会话日志。若问题无法安全地公开描述，只提交最小化、
已去敏的 Issue，请求维护者另行协调私下沟通。

### 提交 Pull Request

欢迎范围清晰的 [Pull Requests](https://github.com/mokuyoaxis/agent-guard/pulls)。
非小型改动应关联已经确认范围的 Issue。

发起 PR 前：

1. 保持 Core 与 Decision Protocol 宿主中立；宿主输入归一化和结果映射留在
   adapter。
2. 行为变更应增加回归测试，并同步相关公开文档与证据边界。
3. 运行 `npm test`，以及本次改动涉及的适配器或打包专项检查。
4. 只使用隔离夹具和合成数据；不得通过删除真实用户数据、改写真实远端或暴露
   真实秘密来证明防护有效。
5. 未经维护者事先认可，不新增生产依赖，也不改变公开 API、版本号、发布自动化
   或安全策略。
6. 保留既有署名与无关改动，让 PR 可以独立审阅和回退。

PR 描述请写明改了什么、验证了什么、哪些路径仍未验证，以及证据属于本地
Core/CLI、模拟宿主还是真实宿主。模拟测试通过不能写成所有宿主都已强制拦截。

审阅、合并、标签、GitHub Release 与 npm 发布是维护者分别作出的决定；贡献被
接受不代表承诺在某个版本或日期发布。
