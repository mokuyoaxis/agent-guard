# Agent setup prompt / 通用适配提示词

Replace the paths and configuration scope below, then give the prompt to the
agent performing the setup. It references the installed package's actual
capabilities rather than assuming unreleased tools are available.

The 0.3.0 maintenance targets are **Codex, DSH, Claude Code and Kimi Code**.
They share Core and MCP; host-specific configuration and blocking adapters
retain separate version/tool evidence. Other hosts can create a local thin
bridge using the same contract, without automatically becoming officially
maintained. See the [integration requirements](../../adapters/INTEGRATION.md)
and [existing coverage](harness-capabilities.md).

Current checkout results are summarized in the
[maintenance acceptance table](../reports/test-report-maintenance-0.3.0.md).
Local and bounded MCP checks have passed; they do not activate or certify a
new installation. Claude model work remains deferred and native hooks retain
their separately recorded evidence.

## Copyable prompt

```text
请为以下工作区接入 Agent Guard：
完整安装根：/absolute/path/to/agent-guard
工作区：/absolute/path/to/workspace
已授权配置范围：填写项目级或用户明确授权的宿主配置／Skill 目录

先读取该安装的 adapters/INTEGRATION.md、相关 Skill 和宿主指南。
识别实际宿主版本、系统、工具及配置作用域，核对相应官方接入文档。
只检查本次需要的配置字段，不把凭据、provider 路由或完整配置复制到上下文。

保持完整安装，确认 Core、Cleanup 等入口实际存在。优先用该安装的
install_skill.py 预览并注册 delete-guard 目录链接；已有授权范围内审阅后可执行，
更新保留旧 Skill 备份。旧包没有安装器时使用其完整包入口，不复制 Core 或改
PYTHONPATH 拼装另一份安装。不要调用该版本尚未提供的工具。

主动维护优先使用共享 MCP 服务，固定工作区和预算身份。若宿主有经核对的
执行前阻塞 hook／插件，再复用对应薄适配；确有需要才生成最小本地桥接。
核对所选包的 SDK 版本，使用独立、稳定的运行环境；安装依赖须在授权范围内，
不把常驻入口长期指向临时试验环境。先用同一 Python、工作区及隔离位置运行
doctor mcp，再注册宿主配置。客户端工具期限应覆盖服务的任务期限；本开发版
普通调用为120秒、获准任务为300秒，Codex配置可用360秒留出余量。连接必需
开关只决定启动失败行为，不授予操作许可；超时后先查询任务，不直接重试写入。
MCP 连接不证明原生 Shell／脚本／编辑器受保护；没有验证阻塞入口时如实标明。
共享 Core 决定策略，不复制规则、不放宽阈值、不换身份或拆批绕过拒绝。
若所选安装支持启动参数 --trash-root，操作者可明确指定固定隔离目录；默认仍是
工作区下 .agent-trash。清理DSH会话等严格存储树时，优先由操作者选择树外稳定
专用桶，不把位置作为模型工具参数，不随机换桶。MCP不采用环境中的隐式隔离
位置覆盖；诊断须传相同 --trash-root。旧桶、旧计划和预算不会自动迁移或合并。
按返回的完整 argv 保留自定义工作区／隔离环境，并保持批准所用的同一会话。
如果所选版本提供 status 的 view=overview，日常查看优先使用这一只读元数据
入口，按任务编号继续查详情。占用未测、GC仅年龄提示时保留未知；详细空间
计量按需调用现有完整查询。0.3.0只做CLI／JSON／MCP统一读取，前端、TUI和
可操作面板另行延期；不要为适配增加展示框架或控制服务。

操作者批准与 agent 执行分开。遵循该安装现有的批准入口；没有实际批准通路
时拒绝 ASK，不由 agent 自填 approved／confirm、分配 PTY 或代填终端确认。
观察结果不是执行许可。检查所选安装实际提供的工具，不假定旧版具有新入口。
0.3.0 提供 cleanup_plan、cleanup_execute、cleanup_task_status；先计划，
显式 save 才写提议。已有 saved_plan_id 可只读查看；执行只接受操作者获准的
task_id，不接受 approved／confirm 或 token。默认十工具没有批准入口。
若操作者明确选择可信 Codex 交互通道，可在服务启动参数加入
--approval-channel codex，额外暴露 cleanup_request_approval(saved_plan_id)。
先由操作者审阅保存计划返回的完整只读 review.file，再请求宿主确认；表单默认
暂不批准，须明确选择仅批准本次并声明已审阅。不得代答、自动启用或用客户端
名字冒充人工凭据。重连后按 saved_plan_id 查询 host_approval 与已有 task_id，
重复请求不重新弹窗或签发。拒绝／取消／过期后的新决定保留原终端入口。
doctor mcp 的 --approval-channel codex 只核对十一工具与三只读调用，不认证人工。
Claude／DSH／Kimi 本轮仍使用终端通路；所选宿主／安装须分别验收，不能继承
其他环境的通过结果。完整清单与内部回执不放进公开报告。
Guard任务批准与宿主调用MCP写工具的许可是两道门；Core的can_execute不代表
宿主已允许调用。先验证宿主审批通路，再索取有时限的Core批准；never会直接
拒绝仍需审批的调用。会话退出0不等于业务成功，宿主拒绝后只读查询并记录
Core状态，不换入口冒充该宿主通过，也不隐藏工具的写入／破坏性标记。
经操作者明确授权，可为独立执行进程配置单工具许可。Codex 0.165.0已用无文件
操作的合成MCP验证tools.<tool>.approval_mode="approve"：只放行指定工具，同服务
其他工具仍被拦截。具体命令见MCP指南的reviewed maintenance tasks。先验证当前
宿主版本，再通过进程参数只许可cleanup_execute、暴露status／cleanup_task_status／
cleanup_execute，并关闭Shell工具；不自动写全局配置或放行safe_delete／restore。
该许可针对工具而非单个task_id，Core仍逐任务核对操作者批准、有效期及原限制。
宿主预检完成后再请操作者批准业务任务，防止批准时间耗在宿主装配上。
目标清单本身不是废弃声明。任务已明确确认整批废弃时，在首次 cleanup_plan
提议中带 discard_targets=true；混合批仅用 discard 声明确认过的废弃项。
不要仅因 untracked、年龄或名称推断废弃。uncertain 理由区分已跟踪、未发现
跟踪内容及 Git 证据不可用；按实际结果解释，旧版组合理由不能证明已跟踪。
空选择没有可批准的执行范围；保存快照不能追加声明，需按已确认事实另建提议。
需要可恢复清理时，先核对安装支持，再在 safe_delete 或新 cleanup_plan 中带
quarantine=true（CLI：--quarantine）；可再生产物保持分类、动作改为隔离。
该选择随计划／批准保存，按ID查询／批准／执行继承，不临时覆盖。容量或其他
准入失败就停止，不改为直删或自动GC；仍需核验受管理缓存自身的一致性要求。
新清理工具默认返回 summary；需要清单时用 view=details，按分类／路径／状态
分页查看，必要时取 full。这些条件只筛选显示，不缩小执行范围。保存计划
按ID读取不重扫；未保存计划需显式save才能稍后按保存ID取详情。人类批准仍
审阅完整选择。执行摘要区分已确认、已派发未确认和未派发；字节按既有计量
来源解释，实际释放量未测时保留未知，不能从成功退出码补写成全部完成。
若所选安装确实提供已保存计划与任务入口：操作者使用 --from-plan ID --approve
审阅批准；agent 使用返回的 --task ID --execute，--task ID --json 查询。
按 MCP 返回的 binding 保持原工作区、隔离区及会话身份，默认共享会话时操作者
需取消 AGENT_GUARD_SESSION，显式会话则采用可信启动配置。部分完成／未知先
用 cleanup_task_status 查询证据，不自动
重试或续跑；剩余目标需新计划和批准。旧版本没有这些参数时不得自行拼装。

保留原配置、用户账号／模型提供商及回退路径。范围外设置、新依赖或安全策略
变更，先展示具体结果取得授权；已授权的常规配置无需反复确认。
Skill、MCP 和 hook 分别接入，不把 Guard-lab 安装成被测 agent 的自认证工具。
Delete-guard MCP 不代表 Exfil-guard 已接管模型请求外发。

按用户安排验证：先必要的本地检查，夹具、冒烟及真实宿主可集中补验。
使用自有合成文件，不删除真实数据作测试；模型试验须有明确范围和额度。
分别记录安装／配置、本地连接、真实任务、原生拦截与反馈；未运行标 NOT_RUN，
缺少证据标 UNVERIFIED，配置完成不能写成保护已生效。测试缺失时继续交接，
不要为了清除某个状态反复诊断或自动重跑。
DSH 的插件进程启动通过不代表调用者沙箱可用。若检查器或状态工具明确报告
SANDBOX_UNAVAILABLE，应报告宿主后端的环境问题并交由操作者检查，不能解释成
Core 已作出删除策略判决，也不能自动放宽沙箱或换工具执行被拒绝的操作。

最后交付简短说明：所选版本与安装根、改了哪些路径及作用域、MCP／hook／Skill
各自的状态、回退及重启要求、实际证据和待验证项。公开报告不附凭据、会话正文
或原始 manifest。
隔离也属于文件系统变更；若只是没用编辑器，应写“未直接编辑”，不要把已隔离
的结果说成“未修改任何文件”。以真实动作、回执和独立核对为准。
```

## Host references

| Host | Maintained setup reference and evidence |
|---|---|
| Codex | [MCP setup and recorded client trials](mcp.md#codex); native interception requires its own evidence |
| DSH | [Plugin setup](../../adapters/dsh/README.md) and [version boundaries](../design/dsh-compatibility.md) |
| Claude Code | [Bash hook reference](../../adapters/claude/README.md); new-version acceptance is pending |
| Kimi Code | [Bash hook and optional feedback](../../adapters/kimi-code/README.md), plus the shared [MCP tools](mcp.md) |

The directory-link helper is included in 0.3.0 and absent from
0.2.9. Check for the actual file before using it. The four-host maintenance
target does not replace the precise tested-version matrix or certify agent-
generated bridges. Setup and verification can be handed off separately.
