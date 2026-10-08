# Agent Guard 0.2.5-rc1

Maintenance prerelease candidate, prepared 2026-10-08. The stable baseline
remains [0.2.4](release-notes-0.2.4.md). The release workflow maps this exact
RC to a GitHub prerelease and npm `rc`, preserving stable `latest` and the
original 0.2.4 tag/artifact. Publication receipts are the
[GitHub release](https://github.com/mokuyoaxis/agent-guard/releases/tag/0.2.5-rc1)
and [npm version](https://www.npmjs.com/package/@mokuyoaxis/agent-guard/v/0.2.5-rc1).

本候选合并 10-08 已完成的维护与本轮文档整理，不是仅修改 README。
版本由维护者明确选择为 0.2.5-rc1，并授权 GitHub／npm 双推；稳定版范围仍另行决定。

## Changes

- **Quarantine controls before housekeeping.** Existing
  `BLOCK_PROTECTED_PATH` wins for root/control files, session state and
  in-workspace storage parents, including reviewed aliases. Opaque quarantine
  globs are refused; safe_delete can enumerate an explicit batch. Ordinary
  unprotected payload housekeeping and validated explicit transaction GC
  remain available.
- **PURGED history stays out of active GC inventory.** Tombstoned transactions
  are excluded before payload accounting/planning. Historical manifests
  remain intact, failed purges remain active, and this does not prune retained
  Git stashes or claim ownership of recreated directories.
- **Restore and safe_delete audit minimization.** New writer events retain
  decisions, counts and recovery associations instead of duplicating paths
  and arbitrary error/reason bodies. safe_delete still accepts `--reason`
  but does not persist its text in new audit/transaction metadata.
  CLI results and essential recovery paths/intents stay exact. Old records,
  generic audit writers and automatic session metadata are unchanged.
- **Exemption configuration diagnostics.** The existing limited parser,
  first-readable-file priority and matching are retained. Checker JSON and
  sanitizer library results gain provenance/count/diagnostic metadata;
  human CLI warnings use stderr. Normal absent/valid configurations stay
  quiet. No full TOML/gitignore parser or configuration migration is added.
- **Assessment contract.** Advisory checks and deletion dry-runs preserve
  targets while potentially writing metadata/exclude entries. Advisory exit 0
  does not authorize BLOCK/ASK. This is clarification and regression coverage,
  not a new zero-write assessment flag.
- **Documentation.** Both READMEs introduce recovery protection, disclosure
  checks and guard-lab near the top, with setup moved to a dedicated guide.
  Current contracts distinguish planned emitter/audit integrations from
  implemented CLIs, local metadata from optional provider calls, and observed
  host evidence from candidate validation. Historical results retain their
  original dates, failures and scope.
- **Documentation resources in the npm package.** Include SECURITY.md and
  the existing community banner assets, which the previous allowlist omitted.
  Runtime entry points and dependency requirements are unchanged.

## Compatibility and version choice

Control-path/glob changes make some previously allowed shapes refuse by
default. The usual 0.x contract classifies that as minor. The maintainer
explicitly chose 0.2.5-rc1 instead of 0.3.0 as a bounded maintenance exception.
The prior 0.2.4 exception is not reused as blanket permission.
See the [compatibility contract](../design/compatibility.md#025-rc1-lifecycle-and-restore-audit-maintenance-source-candidate).

Existing decision/reason strings, CLI arguments and exit-code roles remain.
Consumers should tolerate additive optional fields and stderr diagnostics.
Audit shapes outside essential recovery manifests are not frozen.
No production dependency, storage migration, new transaction strategy or
general output enforcement is introduced. Later stable 0.2.5 scope and
publication remain separate decisions.

## Evidence and limits

Before this version/documentation pass, the combined maintenance worktree
passed 874 Python tests and both Node adapter smoke groups, plus package,
offline install and a synthetic deletion/restore round trip. The first full
run retained one DSH Lab observer-startup timeout; the single-case recheck and
full rerun passed without changing its timeout. Its root cause remains unknown.
These are prior-source checkpoints, not this versioned candidate's release gate.

The versioned regression checkpoint passed 874 Python tests (821.138 seconds)
and both Node adapter smoke groups. It preserved 173 public source files
throughout the run. Afterwards only documentation and the packaging allowlist
for the two existing resources above changed; runtime code and test cases
retain those tested bytes. The final metadata/corpus checks, package contents,
offline installation, documented example and installed Lab calibration are
verified separately: 13 metadata/corpus checks, a 122-file tarball and offline
install, all 10 CLI help commands, adapter import, the documented example,
read-only trash query, a synthetic deletion/restore round trip and all four
zero-model Lab controls passed. These local checks do not establish publication.
No new model or native-host trial is started. Existing bounded DSH/PTC and
Kimi reports do not certify this new candidate. Historical unfinished pairs
remain pending; quiet or unhealthy baselines do not establish Lab mitigation.
Concurrency isolation, same-UID control-plane protection, universal egress
coverage and full Windows-native harness E2E remain outside the guarantee.

发布流程要求精确提交的远端 CI、npm 凭据门禁与两个渠道的工件核查；
本地打包通过不能替代这些步骤，具体执行结果以对应 Actions 和发布工件为准。
