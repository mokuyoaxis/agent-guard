# Read-only trash locations / 垃圾桶只读位置查询

This is the 0.2.4 query interface for CLI, agent and future frontend
callers. It lists locations within a declared scope, without reading recovery
contents or changing storage. The published 0.2.3 artifact does not include it.

这是 0.2.4 的查询接口，可从安装后的 CLI 或本地源码使用。
用户可直接查看，Agent 可解释同一份结果，未来前端
可复用相同函数／JSON。当前不提供页面或操作服务，索引地址不代表清理授权。

## CLI

The existing status command keeps its current-project mode. Add --trash-index
for the location query; an installed command uses the same flags.

```bash
# Default: current directory, depth 3, 10000 examined entries.
python3 skills/delete-guard/scripts/status.py --trash-index

# A specific project collection; repeated roots are allowed.
agent-guard-status --trash-index --root ~/projects --max-depth 3 --json

# Declare a custom/external bucket; with only --trash, no root is walked.
agent-guard-status --trash-index --trash /path/to/custom-store --json
```

默认搜索当前目录；若没有 --root 或 --trash，可纳入当前 AGENT_GUARD_TRASH
配置。显式 --root 不附加环境中的其他位置；需要外部桶时使用 --trash。

max_depth counts directories below each root. At depth 0, the root's own
.agent-trash is still checked. Found buckets are pruned, including an explicit
root named .agent-trash. Ordinary child directory symlinks are not walked;
explicit roots/buckets may be aliases, and results deduplicate physical paths.

默认跳过 .git、node_modules、.internal、__pycache__ 子目录。显式将这些目录
作为 root 时会按所给范围检查。max_entries 限制检查的路径／目录项数；触及
限制会保留部分结果并报告错误。它不是文件系统调用的墙钟超时。

No payload, transaction, byte-size or GC eligibility scan is performed.
Expected manifest/audit/state files and the sessions directory are only
lstat'ed. These are layout hints, not authenticated ownership or complete
backup evidence. A linked marker file alone is not accepted as a regular marker.

退出码：0 完整查询；1 部分结果（读取／路径／预算错误）；2 参数错误。
complete 只指声明深度、排除项和范围内的读取完成，不代表全机覆盖或原子快照。

## Shared backend hook

```python
from core.trash_index import list_trash_locations

result = list_trash_locations(
    ["/path/to/projects"],
    trash_paths=["/path/to/custom-store"],
    max_depth=3,
    max_entries=10000,
)
```

Provide a sequence of text paths or Path objects. The function validates
limits and paths before traversal and raises ValueError for invalid input.
It has no environment-selected default roots; the CLI supplies its context.
It creates no layout, modifies no Git metadata, writes no audit and constructs
no RecoveryEngine. Backend callers must choose their own trusted scope.

## JSON schema version 1

| Field | Meaning |
|---|---|
| schema_version | 1; callers should tolerate additive fields |
| roots / trash_paths | Normalized absolute search roots / declared buckets |
| max_depth / max_entries | Requested bounds |
| excluded_dirs | Names pruned below roots |
| examined | Charged paths/directory entries, bounded by max_entries |
| complete | No read/path/budget errors within the declared scope |
| count | Unique location candidates, including unconfirmed/unreadable rows |
| identified_count | Rows with status metadata_present |
| entries | Deterministically ordered rows |
| errors | Partial-query errors as path + static code |

Each row contains trash_root (first sorted location spelling),
resolved_trash_root, directory (that spelling's parent), locations (all
encountered aliases), sources (declared/discovered) and status.
directory is a location hint, not verified workspace ownership.

| Status | Meaning |
|---|---|
| metadata_present | At least one expected regular marker file or sessions directory exists |
| unconfirmed | A named/declared directory exists, but no usable marker was found |
| unreadable | Marker inspection failed; identification is unknown |

Error codes: NOT_FOUND, NOT_DIRECTORY, PERMISSION_DENIED, IO_ERROR,
ENTRY_LIMIT_REACHED. Errors contain paths but no raw OS exception text.
Missing or non-directory requested locations appear in errors and are not
counted. Concurrent filesystem changes can affect a query snapshot.

## Agent and future UI usage

用户问“有哪些垃圾桶／有多少／在哪”时，Agent 应按当前目录或用户指定范围
查询，分别报告候选数量、布局识别数量和错误。无法检查不能说成不存在；自己
补查的信息应区分来源。Native status tools that lack index flags remain
current-project tools; invoke this CLI for a cross-project location query.

未来只读界面直接读取上述 JSON。恢复或清理应走独立操作入口，重新验证可信
workspace、trash、事务、当前路径和权限；不能将扫描地址或缓存 JSON 当作授权。
本接口不改变分散存储布局，也不读取配置或恢复文件中的秘密值。
