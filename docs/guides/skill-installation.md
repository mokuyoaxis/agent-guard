# Skill installation and updates / Skill 安装与更新

The installer and real-path bootstrap below are **0.3.0 development changes**,
not additions to the published 0.2.9 package. Runtime, link/update and package
smoke checks passed in owned POSIX locations; existing global Skills were not
automatically migrated. See the [acceptance scope](../reports/test-report-maintenance-0.3.0.md).
The package version field still identifies the stable baseline; inspect the
selected installation's actual files when using a development checkout.

Delete-guard depends on the shared `core/` directory. Keep the complete
checkout or npm package, then link its `skills/delete-guard` directory into
the host's selected Skill directory. This keeps `SKILL.md`, Core and Cleanup
on the same installation. Copying only the Skill does not create a standalone
package.

## Install or update

Select the full package root and a Skill target supported by the actual host
version. The target must end in `delete-guard`; the helper does not guess a
host-wide configuration location.

```sh
# Preview only; creates no directory or link.
python3 /absolute/path/to/agent-guard/install_skill.py --target /absolute/path/to/host-skills/delete-guard --json

# Apply the reviewed target within the user's authorized scope.
python3 /absolute/path/to/agent-guard/install_skill.py --target /absolute/path/to/host-skills/delete-guard --execute
```

The checkout also registers `agent-guard-install-skill` as an npm bin entry.
It requires no additional Python package. Existing directories must contain
`SKILL.md`; unrelated targets are refused. An existing Skill directory or
link moves to `.agent-guard-skill-backups/delete-guard-<UTC timestamp>` beside
the host's Skill directory before the new link is created. Keeping backups
outside that search directory avoids registering a second stale Skill.
The plan gives the exact backup path; backups have no automatic rotation.
If link creation fails, the helper attempts to put the previous Skill back;
an error identifies a retained backup when it still exists.

To update to a different package root, run that installation's helper with
the same target. A target already pointing to the selected Skill is unchanged.
Keep the complete installation at a stable path for as long as the link is
used. Reload the host if it caches Skill content.

The helper uses directory symlinks. Select a filesystem that supports them;
Android shared storage and Windows permissions may impose different limits.
No automatic copy fallback is provided. The full-package CLI remains usable
without registering a Skill. Native Windows link installation is unverified.

## Core and Cleanup entrypoints

The bootstrap resolves the real script location before selecting the adjacent
package Core. A missing Core produces installation guidance rather than a
bare `ModuleNotFoundError`. It does not search the workspace for arbitrary
modules or create another copy of Core.

After registration, the selected Skill contains `scripts/safe_delete.py`,
`scripts/cleanup.py`, `scripts/restore.py` and `scripts/status.py`. The helper's
JSON lists those paths. You can also call the complete installation directly:

```sh
python3 /absolute/path/to/agent-guard/skills/delete-guard/scripts/cleanup.py --discover --json
python3 /absolute/path/to/agent-guard/skills/delete-guard/scripts/cleanup.py --json
```

The first command observes directories; the second proposes a plan. Neither
is execution approval. Existing reviewed-plan and terminal-confirmation
requirements remain in force. Registering a Skill does not configure an MCP
service or approve a task. The development package's separate
[MCP task tools](mcp.md#reviewed-maintenance-tasks-030-development) reuse the same
CLI/Core. See [Cleanup](cleanup-plan.md).

## Upgrade a development installation

1. Select a reviewed complete package root at a stable path. Check its actual
   installer and tool flags; identical version text does not establish
   identical development code.
2. Complete outstanding approved tasks using their original package,
   workspace, quarantine and session binding. Do not copy or rewrite their
   control records to move them to a new bucket or older implementation.
3. Preview the selected installation's helper with the same explicit Skill
   target, then apply within the authorized scope. Retain the old package
   while a saved Skill link still points to it; reload a host that caches Skills.
4. Reconnect MCP using the selected server and its matching doctor/CLI. Keep
   the same trusted session and quarantine settings; custom storage also needs
   the same `--trash-root` in doctor. A different bucket selects different
   history and counters, not an automatic upgrade or migration.

Use owned synthetic files to verify the selected entrypoints. A successful
installation, connection and native-hook enforcement are separate results;
see [MCP storage](mcp.md#select-a-fixed-quarantine-directory-030-development)
and the [remaining acceptance gates](../reports/test-report-maintenance-0.3.0.md#remaining-acceptance-and-release-work).

## Scope and rollback

The helper changes only the explicitly selected Skill target, a retained
backup and any missing parent directories. It does not install dependencies,
edit host settings, change budgets or touch quarantine data. Hook/MCP
configuration and activation are separate tasks covered by the
[setup prompt](agent-setup-prompt.md).

For rollback, inspect the current link and retained backup first. Preserve
the current link through the project's recoverable removal path before
putting the backup back at the target; do not overwrite an occupied target.
The backup may itself be a link to an older installation, which must still
exist. No automatic rollback to another package or policy is performed.

中文要点：保留完整包，用明确目标路径注册目录链接；默认只预览，更新保留旧
Skill 备份。先确认宿主实际支持的目录和作用域，再执行。不能只复制 Skill 或
临时拼接 Core。安装成功、MCP 可用和原生工具受保护分别验证；未运行的检查
保留待验，已有实测范围见验收表，不自动迁移全局安装或旧任务。
