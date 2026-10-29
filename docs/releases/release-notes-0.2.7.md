# 0.2.7 — Cleanup, deletion limits and local MCP

Version 0.2.7 adds Cleanup plans, shared deletion admission and optional local
MCP tools to [0.2.6](https://github.com/mokuyoaxis/agent-guard/blob/0.2.7/docs/releases/release-notes-0.2.6.md).
Use the exact [GitHub release](https://github.com/mokuyoaxis/agent-guard/releases/tag/0.2.7)
and [npm version](https://www.npmjs.com/package/@mokuyoaxis/agent-guard/v/0.2.7)
to verify availability before installing; a manifest, checkout or local
tarball alone does not establish publication. This version leaves the
existing `rc` and `hotfix` channels unchanged. The separate 0.3.0 plan remains
paused. Local preparation evidence below is dated 2026-10-29.

本版收录 Cleanup Copilot、删除准入加固和可选本地 MCP 工具。新增预算与保护
可能拒绝此前可执行的删除。安装前请确认精确版本的发行与包页面可用；下方
版本化测试、打包与发布门记录单独保留，不能沿用旧 0.2.6 元数据工件的结论。

<a id="candidate-scope"></a>

## Version scope

### Cleanup Copilot

`agent-guard-cleanup` proposes workspace candidates with logical sizes,
reasons and projected effects in four categories: regenerable, explicitly
caller-declared discarded, uncertain and protected. Age, ignore status or
being untracked alone does not make a file disposable.

Default planning writes no workspace files, guard metadata or audit records.
Execution requires an unchanged reviewed plan, explicit selection and terminal
`EXECUTE` confirmation, then delegates to the existing safe_delete path.
Valuable files retain recovery copies and disk use; only provably regenerable
artifacts follow the existing physical-delete rules. See
[the workflow and limits](https://github.com/mokuyoaxis/agent-guard/blob/0.2.7/docs/guides/cleanup-plan.md).

### Shared deletion admission

Optional `.agent-guard/delete-policy.json` supplies protected paths and limits.
Missing configuration uses defaults; malformed configuration or unusable
admission state refuses covered execution instead of silently disabling it.
The configuration directory and admission control files are protected.

| Default | Value |
|---|---|
| Quarantine admission ceiling | 5 GiB / 100000 entries |
| Cumulative session budget | 1 GiB / 10000 entries |
| Rolling rate | 60 admissions per 60 seconds |
| Repeated normalized target set | 5 admissions per window |
| Batch review threshold | Above 256 MiB or 1000 entries |

Measurement, durable debit and guarded mutation share a cross-process lock.
Unnamed CLI processes use a stable shared budget; failures and later restores
do not refund admitted bytes/entries. Rate windows expire, while cumulative
budgets require explicit operator action. A repeated-target-set stop is a
simple loop signal, not a general anomaly detector.

Large covered automated deletions return `BLOCK_DELETE_BATCH_CONFIRM` with
Cleanup guidance. The shell checker combines supported rm/artifact/Git-clean
targets across a request before its first compensation. Cleanup confirmation
creates a ten-minute session/configuration/path/metadata-bound approval,
consumed once per selected path; it lifts only the batch gate, not protection,
capacity, budget or rate limits.

The additive refusal codes are:

- `BLOCK_DELETE_CONFIG_INVALID`
- `BLOCK_DELETE_PROTECTED`
- `BLOCK_DELETE_LIMIT`
- `BLOCK_DELETE_RATE`
- `BLOCK_DELETE_REPEAT`
- `BLOCK_DELETE_BATCH_CONFIRM`
- `BLOCK_DELETE_STATE_INVALID`

Existing Decision classes, check.py exit roles and recovery-manifest formats
retain their contracts. No data/configuration migration or automatic GC is
introduced. See [defaults, accounting and operator recovery](https://github.com/mokuyoaxis/agent-guard/blob/0.2.7/docs/guides/deletion-limits.md)
and the [compatibility contract](https://github.com/mokuyoaxis/agent-guard/blob/0.2.7/docs/design/compatibility.md) for the bounded
maintainer decision to select 0.2.7 for these additions.

### Optional local MCP tools

`agent-guard-mcp` exposes safe_delete, ordinary restore, transactions, status
and GC-plan preview over local stdio. Workspace, local quarantine and stable
session identity are fixed at trusted startup. Tools cannot supply a new
workspace/session/environment, force restore, GC execution, budget reset or
Cleanup approval. Quarantine targets are refused by MCP safe_delete to avoid
a permanent-GC shortcut.

The official MIT-licensed `mcp==2.3.0` SDK is installed separately in an
optional Python 3.10+ environment. Core/CLI still requires Python 3.9+ and Git
without third-party Python packages; npm does not install the SDK. No server
network listener is provided. Follow the [MCP setup guide](https://github.com/mokuyoaxis/agent-guard/blob/0.2.7/docs/guides/mcp.md).

MCP is an explicit cooperative tool interface. It does not intercept shell,
script or editor operations that bypass it. Startup session selection chooses
the existing CLI authorization mode as well as budget. It does not inherit
native host restrictions or an in-memory veto without trusted bridging.

## Upgrade and operator notes

- Covered automated filesystem compensation, regenerable direct deletion,
  Git-clean compensation and force-restore occupant preservation can newly
  refuse work when limits are reached. An earlier advisory assessment is not
  a capacity grant. A later refusal can follow completed earlier operations;
  inspect returned transaction IDs and the filesystem.
- Normal restore remains available without a fresh deletion debit. Explicit
  transaction GC and existing unprotected quarantine housekeeping retain
  their exemptions; configuration/control-path protection still precedes
  housekeeping. The existing GC CLI's `--execute` does not require a TTY;
  MCP's preview-only scope does not change that separate interface.
- Existing compound-command ASK and Git reset/restore/checkout SNAPSHOT paths
  are outside these new admission counters. Git stash storage can still grow.
  Do not describe this release as a limit on every destructive operation.
- `status.py --reset-delete-budget` requires an interactive terminal and
  `RESET`, after investigation. It resets only the selected bucket and its
  approvals, without deleting recovery copies or altering capacity/limits.
  Agents must not change identity, remove state or manufacture confirmation
  to bypass a refusal.
- Accounting uses bounded logical lstat inventory, including directory and
  symlink metadata and conservative per-entry hardlink counts. It is not an
  OS disk quota. Audit/manifest metadata and unrelated writers can still grow
  storage; scan/state bounds may refuse before a larger configured limit.
- Package/Core changes affect doctor fingerprints. An old baseline may become
  `DRIFTED`; it is not silently replaced, and a new local baseline does not
  prove native-hook interception or model receipt of feedback.

Existing installations require an explicit upgrade to acquire these additions.
Installing a separate copy does not upgrade another existing installation;
host configuration and hook activation remain separate steps.
Same-UID, concurrency and uncovered-tool
limitations remain in the [threat model](https://github.com/mokuyoaxis/agent-guard/blob/0.2.7/docs/design/threat-model.md).

<a id="evaluate-the-unpublished-candidate"></a>

## Install or evaluate version 0.2.7

After confirming that the exact version is available on both publication
pages above, install it into a chosen, stable prefix:

```sh
npm install --prefix /absolute/path/to/agent-guard-install @mokuyoaxis/agent-guard@0.2.7
```

For source review or before publication is available, use a reviewed checkout
or a tarball produced and checked from that exact source. An already-built
local tarball can be installed into a disposable prefix:

```sh
npm install --offline --ignore-scripts --legacy-peer-deps --no-audit --no-fund \
  --prefix /absolute/path/to/agent-guard-review \
  /absolute/path/to/mokuyoaxis-agent-guard-0.2.7.tgz
```

The optional MCP SDK remains a separate installation. Compare the selected
package with the release artifact checksum; local packaging and the version
string alone do not establish registry availability.

## Validation status

**Local 0.2.7-versioned validation passed on 2026-10-29.** The explicit
219-file public candidate excluded private experiments. All 1129 Python cases
and both Node groups passed in 1194.265 seconds, with no failures, errors,
skips, timeouts or automatic retries. The existing diagnostic collector kept
`npm test`'s scope and original deadlines; npm itself was not invoked.

The 155-file 0.2.7 package passed offline installation, all 12 CLI entry-point
checks and actual installed stdio deletion, restoration, persistent-budget,
protection and batch-gate checks. MCP `serverInfo.version` matched package
version `0.2.7`; read-only tools left the workspace unchanged and forbidden
override/force/reset/GC-execute arguments were refused. A separate final
package consistency check verifies documentation-only updates against the
tested runtime and the final source.

Six release-gate tests executed the workflow's actual SDK preflight body:
missing/wrong/broken SDK and disabled/skipped/empty protocol suites were
refused. The same body also passed against the existing isolated SDK. The tag
workflow now installs that pinned SDK and requires enabled protocol coverage
before its unchanged `npm test` step. These are the **2026-10-29 local
preparation results**: they did not include final-commit remote CI or
publication checks and made no new real-model requests. Published artifact
and workflow results must be checked through the exact version links above.

Earlier local source evidence is retained with its original checkpoint:

| Checkpoint | Observed result |
|---|---|
| Cleanup-only | 1012 Python cases and both Node groups passed continuously in 979.997 seconds; 37 Cleanup/9 manifest checks and a 146-file offline package also passed |
| Deletion hardening | Three independent runs each passed 1090 Python cases and both Node groups in 1061.017 / 1025.315 / 1042.145 seconds; a 149-file offline package and three real Kimi root-Bash scenarios passed |
| MCP source, first full run | 1123 Python cases, one observer READY-startup error, no skips; 1156.312 seconds; Node did not start |
| Same MCP source, second full run | 1123 Python cases and both Node groups passed in 1143.435 seconds, without failures, errors, skips, timeouts or automatic retries |
| MCP source packaging | 154 files / 12 CLI checks, local offline installation and actual installed stdio deletion/recovery/budget/protection checks passed |

These source/package checks used 0.2.6 package metadata before selecting
version 0.2.7. They are not acceptance of a renamed 0.2.7 tarball. The first MCP
run's observer error remains unresolved; the unchanged case passed separate
SDK/system-Python checks in 4.409 / 2.979 seconds. The successful second run
does not establish a general timeout fix. Both full runs used the existing
diagnostic result collector with npm test's scope and original deadlines,
without invoking npm itself.

At the earlier 0.2.6-metadata MCP checkpoint, three real Kimi Code 2.3.0
root-agent native MCP trials passed: normal
deletion/model-driven restore (27.288 seconds), same-session budget refusal
and model-driven restore (26.121 seconds), and configured protection
(16.814 seconds). All 19 configured-route requests returned HTTP 200;
16 model-generated MCP calls matched native stdio requests, without Bash,
fixed replies, automatic trial retries or route changes. Restored bytes and
user configuration/auth fingerprints matched; restored budget remained
charged. The existing loopback gateway's server-side model identity was not
independently authenticated.

Local evidence is ARM64/PRoot POSIX, Python 3.13.5 and Node 22.23.4.
Without the optional SDK, seven real-stdio protocol cases skip; the local
0.2.7 full run included that SDK, and the release gate refuses skipped
protocol coverage. New SDK-backed CI declarations are not
remote-CI results. Protocol evidence exercised the classic 2025-06-18
handshake, not every extension/client version. Claude/Cursor/Codex examples
are documentation guidance; no new live-client, subagent, native Windows,
macOS or shared-Android-storage acceptance is claimed.
