# 防守研究 / Blue-team research

这里公开 Agent Guard 自身受控实验和实现评审的整理稿，重点是防护设计、证据采集、
授权边界、正常任务效用与失败诊断。资料截至 2026-10-13，属于持续推进的实验研究。

These notes publish defensive methods and bounded observations from our own
controlled experiments. They describe provenance, disclosure permissions,
task utility and evidence failures. Research proceeds gradually; the public
examples and the historical real-model observations have different scopes.

| 文档 | 主要内容 |
|---|---|
| [数据流观察](dataflow-observability.md) | 区分读取、上下文导入、发送与接收，解释观察盲区 |
| [正文发送授权](content-authorization.md) | 本地读取与模型发送的独立许可、来源绑定和固定通知 |
| [父子代理来源](delegation-provenance.md) | 派生返回、接收者许可、实际呈现与历史再次消费 |
| [验证与评分](evaluation-method.md) | 分开评价证据健康、授权遵守、任务完成与输入一致性 |
| [阶段实验报告](../reports/blue-team-lab-2026-10.md) | 去敏后的方法、结果、失败与有限结论 |
| [汇总数据](lab-observations-2026-10.json) | 与阶段报告对应的匿名化观察摘要 |
| [零模型校准示例](../lab/blue-team-calibration.md) | 使用公开包复验已有四种仪器控制 |

## 如何解读这些资料

共享 Core 的已交付行为由[架构](../design/architecture.md)、
[兼容契约](../design/compatibility.md)和[能力矩阵](../guides/harness-capabilities.md)定义。
研究稿中的正文授权、历史投影与父子来源处理是在特定私有 Lab 装配中验证的实验方法，
尚未成为通用生产 guard 或受支持的新宿主接口。

阶段报告保留不完整响应、来源故障与未触发的对照，公开整理不会把它们升级成成功。
去敏稿不包含个人路径、私有网关配置、凭据存储、原始会话或可直接复用的诱导载荷。
完整私有实模原件没有随稿公开；读者可以复验公开校准入口，并提交独立实验结果，
不能仅凭这些摘要重放或逐字节核验历史实模。

## 参与研究

欢迎 fork、PR、方法评审和独立复现。我们不承诺固定实验频率或按期完成全部研究方向。
贡献时记录代码版本、宿主版本、声明的模型配置、合成样本、预算、对照条件、
观察范围、正常任务结果和失败记录。公开反馈以防守方法和必要的去敏证据为主；
敏感细节按[贡献指南](../../CONTRIBUTING.md)协调。

本文档采用项目 [MIT 许可证](../../LICENSE)。结论覆盖记录的试次和通路，不构成对任意
模型、厂商、工具或同权限进程的安全认证。
