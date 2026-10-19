# 蓝队零模型校准 / Blue-team calibration

RC3 引入的便携示例保留在 0.2.5 中，将现有四种 Lab 控制依次运行并保存简短摘要。
它复用已交付的 Core，不需要 DSH、Kimi、模型账户、私有 home 或新的 Python 依赖。
需要 Python 3.9+，并允许本机 IPv4 回环监听；完整 Core 安装仍按[入门指南](../guides/getting-started.md)检查。

This example runs the four existing deterministic controls from a checkout or
installed package. It calls no model and makes no external-network request.
It creates disposable fixtures and private local evidence, including a
bounded IPv4-loopback observer. It is an example, not a new stable CLI.

## 运行

在包根目录或 checkout 根目录，选一个不存在的输出目录；父目录须已存在：

```sh
python3 docs/lab/examples/blue-team-calibration.py --output-dir /tmp/agent-guard-blue-control-001
```

示例也可从其它工作目录通过其绝对路径调用。它根据脚本位置找到同一包内的 Core，
不使用个人安装路径或全局宿主配置。再次运行请选择新目录，已有输出不会覆盖或清理。

| 控制 | 用途 |
|---|---|
| `clean` | 干净的负对照 |
| `mock-positive` | 合成接触／输出标记阳性 |
| `mock-injection` | 固定脚本中的标记流转阳性；名称沿用原 Lab 契约 |
| `snapshot-positive` | 合成归档与本机接收端校准 |

四种控制均为 `model_usage=NONE`、`security_outcome=CALIBRATION_ONLY`。
摘要中的 `COMPLETE` 需要控制报告 COMPLETE、判据 PASS、观察器 HEALTHY。
非完整报告或已知 Lab 错误会立即停止后续控制，保留已有夹具和证据，不自动重试。

输出目录包含各控制的 `fixture/`、`evidence/` 和整体 `summary.json`。
观察器的路由和控制材料保存在私有证据中；公开反馈只提交必要的去敏摘要。
脚本在终端输出同一摘要，完整返回退出码 0，控制未完成返回 4；参数或目录错误返回非零。

## 如何使用结果

控制通过说明这个运行环境中的仪器能完成指定校准；不说明真实模型安全、不认证宿主
已经加载 hook，也不产生真实防护 L2 结论。若回环监听、权限或启动失败，先检查保留的
`observer.log` 与证据状态，按[Lab 指南](guard-lab.md)解释失败。

本示例不重放私有实模，也不实现[研究稿](../research/README.md)中的请求授权或父子装配器。
它为后续独立防守实验提供公开起点。贡献入口见[CONTRIBUTING.md](../../CONTRIBUTING.md)。
