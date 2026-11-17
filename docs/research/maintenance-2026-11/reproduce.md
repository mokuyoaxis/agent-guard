# 用现有 CLI 走一份新的合成任务

针对完整的 Agent Guard 0.3.0 安装或相应源码入口。需要 Bash、Git 和 Python 3。文件内容、大小和 ID 会与[历史案例](README.md)不同；这里复验公开接口行为，不重演私有模型会话，也不需要模型 API。

在单独的 Bash 终端中，把 `AG_INSTALL` 设为完整安装目录的绝对路径，例如 `AG_INSTALL=/absolute/path/to/agent-guard`。以下步骤保持同一终端、工作区、隔离位置和会话设置。

## 1. 创建新目录与五份合成文件

```bash
set -eu
: "${AG_INSTALL:?请先设为完整安装的绝对路径}"
AG_DEMO=$(mktemp -d "${TMPDIR:-/tmp}/agent-guard-demo.XXXXXX")
export AG_DEMO
export AGENT_GUARD_WORKSPACE="$AG_DEMO"
export AGENT_GUARD_TRASH="$AG_DEMO/.agent-trash"
git -C "$AG_DEMO" init -q
python3 - <<'PY'
from pathlib import Path
import hashlib
import json
import os
import stat

root = Path(os.environ['AG_DEMO'])
files = {
    '.pytest_cache/nodeids': b'generated demo cache\n',
    'discarded/session.txt': b'demo session explicitly declared obsolete\n',
    'unknown.txt': b'purpose not established; retain\n',
    'protected/notes.txt': b'explicitly protected demo content\n',
    'keeper.txt': b'outside the candidate list; retain\n',
}
baseline = {}
for name, body in files.items():
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(body)
    path.chmod(0o640)
    baseline[name] = {
        'sha256': hashlib.sha256(body).hexdigest(),
        'mode': stat.S_IMODE(path.stat().st_mode),
    }
(root / '.gitignore').write_text('.pytest_cache/\n.agent-guard/\n.agent-trash/\n')
(root / 'baseline.json').write_text(json.dumps(baseline, indent=2))
print('Demo workspace:', root)
PY
```

这些文件均为本次新建数据。废弃声明只针对 `discarded`，保护声明只针对 `protected`；keeper 不进入候选。

## 2. 保存并读取计划，停在批准之前

```bash
python3 "$AG_INSTALL/skills/delete-guard/scripts/cleanup.py" \
  --root "$AG_DEMO" \
  --target .pytest_cache --target discarded \
  --target unknown.txt --target protected \
  --discard discarded --protect protected \
  --select .pytest_cache --select discarded \
  --quarantine --save-plan --json --view full \
  > "$AG_DEMO/plan-receipt.json"
AG_PLAN_ID=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["saved_plan_id"])' "$AG_DEMO/plan-receipt.json")
python3 "$AG_INSTALL/skills/delete-guard/scripts/cleanup.py" \
  --root "$AG_DEMO" --from-plan "$AG_PLAN_ID" --json --view full
```

预期是四类各一项，只选择 `.pytest_cache` 与 `discarded`，两项 effect 均为 quarantine。保护目录大小应保持未知，不能把未遍历写成 0 B。此时五份文件都应在原位；保存计划只写控制记录，不批准或执行，也不预留预算。

## 3. 操作者审阅并批准

以下命令由操作者在自己的交互终端运行。审阅 Core 展示的完整选择后，决定是否输入 `APPROVE`；批准的有效期为 600 秒。

```bash
python3 "$AG_INSTALL/skills/delete-guard/scripts/cleanup.py" \
  --root "$AG_DEMO" --from-plan "$AG_PLAN_ID" \
  --approve --json --view full > "$AG_DEMO/approval-receipt.json"
AG_TASK_ID=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["task_id"])' "$AG_DEMO/approval-receipt.json")
python3 "$AG_INSTALL/skills/delete-guard/scripts/cleanup.py" \
  --root "$AG_DEMO" --task "$AG_TASK_ID" --json --view full
```

只有成功批准后才有可执行 task_id。操作者取消、批准过期或状态不可确认时，保留回执并停止；agent 不创建 PTY 或代填确认。

## 4. 执行一次，再查状态

确认任务为 APPROVED、批准 CURRENT 且 can_execute=true 后，使用同一 task_id。保存计划与任务已经绑定隔离选择，不再添加覆盖参数。

```bash
python3 "$AG_INSTALL/skills/delete-guard/scripts/cleanup.py" \
  --root "$AG_DEMO" --task "$AG_TASK_ID" --execute --json --view full \
  > "$AG_DEMO/execution-receipt.json"
python3 "$AG_INSTALL/skills/delete-guard/scripts/cleanup.py" \
  --root "$AG_DEMO" --task "$AG_TASK_ID" --json --view full
```

预期两个根均确认，任务 COMPLETED；两个源目录离开原位，unknown、protected、keeper 保留。回执丢失或结果部分完成时，先查现有任务与事务，不再次执行同一任务。

## 5. 核对并恢复本次事务

先检查执行回执中的 moved 路径及隔离副本，按第一步 baseline.json 比较两项文件字节和权限；事务引用本身不能证明副本完好。下面只提取当前这份执行回执的事务 ID。

```bash
AG_TXID=$(python3 - "$AG_DEMO/execution-receipt.json" <<'PY'
import json
import sys

with open(sys.argv[1]) as stream:
    receipt = json.load(stream)
assert receipt['status'] == 'COMPLETED'
groups = receipt['groups']
assert len(groups) == 1 and groups[0]['effect'] == 'quarantine'
print(groups[0]['receipt']['txid'])
PY
)
python3 "$AG_INSTALL/skills/delete-guard/scripts/restore.py" "$AG_TXID" --json
python3 "$AG_INSTALL/skills/delete-guard/scripts/restore.py" list --json
python3 - <<'PY'
from pathlib import Path
import hashlib
import json
import os
import stat

root = Path(os.environ['AG_DEMO'])
baseline = json.loads((root / 'baseline.json').read_text())
for name, expected in baseline.items():
    path = root / name
    assert hashlib.sha256(path.read_bytes()).hexdigest() == expected['sha256'], name
    assert stat.S_IMODE(path.stat().st_mode) == expected['mode'], name
print('All five demo files match their original bytes and modes.')
PY
```

本次事务应显示 RESTORED。若原位置出现新占用者，普通恢复会拒绝覆盖；先保留双方并检查冲突。隔离期间文件仍占空间，恢复也不返还已经计入的删除预算。演示数据与记录保留供核对，这份说明不包含永久 GC。

## MCP 和复核范围

若选择 MCP，应把服务绑定到这份新示例工作区，按同样的分工使用 cleanup_plan、cleanup_task_status、cleanup_execute 和 restore。批准沿用实际配置的操作者通路。只读观察或保存计划不会增加执行许可，调用未配置的私有中继也不是使用公开接口的必要条件。

2026-11-17 已沿同一示例完成五步核对：先前创建文件并保存／读取计划，随后由真实操作者终端批准，执行一次并普通恢复一次。执行与恢复之间另查了两个根的隔离副本字节／权限；恢复后五份文件均与 baseline 一致，任务 COMPLETED、事务 RESTORED。新示例计账 6967 B／4 条目，恢复不退款，未调用模型或永久 GC。

原先的仅预览记录保留，后续执行没有重建计划、换桶、改变会话设置或重复执行。完整历史模型装配与原始捕获未随稿提供；这里的 CLI 示例不承诺重现相同模型输出。

如果遇到问题，反馈包／宿主版本、使用入口、原原因码、额外人工动作及去敏的合成最小例即可。本文不要求真实待删除文件、密钥或完整会话。
