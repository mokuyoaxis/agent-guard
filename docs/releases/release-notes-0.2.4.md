# Agent Guard 0.2.4

Stable release, 2026-10-07. Artifact availability is shown in GitHub Releases
and npm. This maintenance release builds on
[0.2.3](release-notes-0.2.3.md); its information-protection and offline Lab
features are retained rather than counted again as new changes.

## Changes

- **Hard reset collision checks.** Before a supported standalone
  `git reset --hard [ref]`, the Core compares the target tree with untracked
  and ignored paths, including ancestor collisions and quarantine storage.
  Unsafe collisions return `BLOCK_GIT_RESET_COLLISION` before snapshotting.
  Incomplete preflight, submodules, stash-relative targets and unsupported
  options, wrappers or compound contexts are refused. A tracked-only stash
  does not claim to preserve untracked content.
- **Restore path checks.** Relocation restore validates every destination
  inside the selected workspace and every source inside its own transaction,
  including transaction IDs and parent symlink changes. Batch preflight and
  per-item checks preserve final symlink semantics. Force cannot bypass these
  checks. These checks do not provide atomic isolation from concurrent edits.
- **Preservation before forced restore.** An occupied destination is saved
  through the existing relocation transaction before the old version is
  restored. Optional `backup_txids` records preservation attempts, including
  failed attempts. An ID alone is not proof of a complete copy. Journal or
  audit failures retain the reported filesystem outcome and these IDs; inspect
  state and files after an error before attempting another restore.
- **Read-only trash locations.** The shared `core.trash_index` query and
  `agent-guard-status --trash-index` expose a separate schema v1 for CLI,
  agents and future frontend callers. Depth/entry budgets, alias deduplication,
  explicit custom buckets and partial errors keep scope visible. Metadata
  layout is a hint, not ownership or cleanup authorization. The query does
  not read recovery contents or write an index file. See the
  [query contract](../guides/trash-index.md).
- **Documentation navigation.** Guides, design, Lab tasks/candidate cases,
  reports, releases and historical material now have separate directories.
  README, adapter, test and release-workflow links use the new paths.
  Historical evidence retains its original scope and result.

## Compatibility

The maintainer selected `0.2.4` as a maintenance patch exception to the normal
0.x minor-release rule for additive reason codes and stricter default verdicts.
The new collision reason, additional refusals and optional query/result fields
are explicitly documented in the
[compatibility contract](../design/compatibility.md#024-maintenance-patch-exception)
and [friction log](../history/friction.md#f24--hard-reset-could-destroy-content-outside-the-tracked-snapshot).
Consumers must tolerate the additive reason and optional fields.

Existing decision classes, reason strings, exit codes and ordinary status
arguments remain available. Existing manifest strategies and record fields
retain their roles; no storage migration or production dependency is added.
Force still requests overwrite, but now requires successful preservation first.
Read-only location discovery is separate from transaction usage inspection.

## Evidence and limits

The completed local maintenance work added 109 Python regressions to the
0.2.3 baseline: 32 hard-reset (30 new module cases plus two policy cases),
14 restore-target, 14 restore-source, 22 forced
restore and 27 trash-query tests. The versioned candidate passed all 785
Python tests (722.174 seconds) and both Node adapter smoke groups. The tested
npm package contains 117 files; the later PTC report brings final documentation
packing to 118. Offline installation, all 10 CLI help commands,
adapter import and the installed read-only query passed; the query fixture
was unchanged. Runnable assets retain the tested bytes through final packing.

The initial local preparation made no new real-model call. A later
[official Flash / PTC evaluation](../reports/test-report-dsh-0.2.4-ptc.md)
observed root and one child's inner Bash collision refusal, with matching
audits and unchanged original files. The original root PTC task failed JSON
output validation and reached its token cap; a separate same-Session summary
completed. This is bounded call-level evidence, not a whole-task PTC PASS.
No fake-ai-api experiment was run. Pending historical DSH captures retain
their review status. Existing host evidence is not upgraded
to new native Windows/macOS or broader model coverage by these Core tests.
Remaining GC, control-file protection and same-UID/concurrency limitations
retain their documented scope. There is no frontend or operation service
in this release; the query is the reusable backend hook.

Python Core remains 3.9+ with Git; the optional default-off DSH read feature
uses Node 22 and reviewed providers. The Kimi TOML capture helper requires
Python 3.11+. DSH contracts remain `0.1.5-rc.1` and `0.2.0-rc.2`.

Install the exact version:

```sh
npm install --prefix /absolute/path/to/agent-guard-install @mokuyoaxis/agent-guard@0.2.4
```
