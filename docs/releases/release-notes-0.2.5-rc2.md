# Agent Guard 0.2.5-rc2

Maintenance prerelease, prepared 2026-10-09; double-channel publication
authorized 2026-10-10. The stable baseline is [0.2.4](release-notes-0.2.4.md),
and the previous prerelease is [0.2.5-rc1](release-notes-0.2.5-rc1.md).
The exact RC tag routes to GitHub prerelease and npm `rc`, preserving stable
`latest` and existing version artifacts. Publication receipts are the
[GitHub release](https://github.com/mokuyoaxis/agent-guard/releases/tag/0.2.5-rc2)
and [npm version](https://www.npmjs.com/package/@mokuyoaxis/agent-guard/v/0.2.5-rc2).

维护者已选择 RC2 候选准备范围：共享审计元信息最小化、Lab observer 启动与取消收尾、
配套文档及回归，并已授权 main 提交／推送及本次 GitHub prerelease／npm rc 双推。
发布仍需精确提交远端门禁和两个渠道的同工件核查；状态以版本化发布回执为准。

## Changes since RC1

- **Shared audit projection.** `append` stores supported fields and validated
  values only and returns the stored record. `tail` applies the same projection
  to old objects in memory, skips malformed/non-object JSON, and returns no
  records for non-positive limits. Unsupported/custom fields and free text are
  omitted. Earlier log bytes and recovery manifests are preserved.
- **Opaque audit identities and bounded status metadata.** Supplied sessions
  and displayed actors use stable SHA-256 correlation; the automatic identity
  no longer includes a raw username or hostname. Generated transaction IDs
  remain exact. Noncanonical IDs are summarized in audit views, while
  `find_by_txid` still accepts their original spelling. Authorization session
  selection and mode state are unchanged.
- **GC audit metadata.** Unknown selected IDs use digests and missing counts;
  reason maps cover selected transactions only. Exact CLI diagnostics and
  recovery associations remain available. Preflight and durable intent still
  precede purge.
- **Observer startup diagnosis.** Private `observer.log` gains static phases
  and monotonic timestamps for spawn, Python bootstrap, validation, loopback
  binding, endpoint writing and readiness. These notes contain no tokens,
  paths or exception text, are best-effort, and do not establish health.
- **Cancelled startup remains inconclusive.** The five-second readiness
  window stays unchanged. Startup timeout requests cooperative cancellation
  and permits up to two additional seconds of local child reaping. A live
  child remains registered without a forced signal. Cancelled startup records
  `FAILED` / `OBSERVER_STARTUP_CANCELLED`, including late READY publication.
  Reap timeouts preserve the original LabError; ordinary stops after accepted
  readiness retain their existing meaning.
- **Documentation and regression coverage.** Current entry points identify
  the RC2 candidate separately from published baselines, describe audit
  compatibility and observer behavior, and preserve historical test/host
  evidence. The two maintenance groups add 40 regression tests over RC1.

RC2 retains the lifecycle, quarantine control-path, PURGED inventory,
restore/safe_delete writer, exemption-diagnostic and assessment maintenance
described in the [RC1 notes](release-notes-0.2.5-rc1.md).

## Compatibility

Audit logs are supported metadata records, not arbitrary payload storage.
Consumers relying on custom fields, raw sessions/actors or noncanonical audit
IDs must review the [audit contract](../guides/audit-metadata.md).
A digest is not a restore argument: recovery uses the original ID and exact
manifest/CLI paths. Digests provide correlation, not encryption or identity
authentication.

Public CLI parameters, exit-code roles, supported recovery strategies,
authorization selection, audit locking/flush/fsync and existing
required-versus-best-effort writer gates retain their behavior.
No production dependency or storage/configuration migration is introduced.
RC2 introduces no new destructive-command policy verdict or refusal shape;
the bounded version exceptions inherited from RC1 remain documented in the
[compatibility contract](../design/compatibility.md).

## Evidence and limits

Before this version/documentation preparation, the combined maintenance
source passed 914 Python tests (1279.463 seconds, no skips) and both Node
adapter smoke groups, with 176 public files frozen during that run.
The local 123-file package matched source and offline installation, and all
four installed zero-model controls passed. These are prior-source checkpoints,
not publication receipts for a package named 0.2.5-rc2.

The versioned RC2 candidate independently passed 914 Python tests
(1072.129 seconds, no skips) and both Node smoke groups; 177 public files
remained unchanged throughout that run. This evidence paragraph was completed
afterwards; runtime code and test cases retain their tested bytes.

The final RC2 candidate gate separately verifies
version/channel routing, final package contents, offline installation,
CLI/adapter entry points, synthetic recovery round trips and zero-model
calibration. Publishing additionally requires exact-commit remote CI,
npm preflight and matching GitHub/npm artifacts; a manifest or local tarball
does not establish registry availability.

**The first intermittent observer startup timeout's unique cause remains
unknown.** Controlled delay injections establish the cancellation/health
defect and its correction; normal rechecks do not identify that historical
cause or prove every future startup will finish within five seconds.
The [Lab contract](../lab/guard-lab.md) explains the diagnostic and health gates.

Old raw logs, exact recovery data, ordinary tool output and streams are
outside the shared audit projection. No new real-model or native-host trial
is credited to this candidate. Historical unfinished pairs remain pending.
Same-UID control-plane isolation, recovery concurrency isolation, universal
egress coverage and complete Windows-native harness E2E remain outside the
guarantee. Later stable 0.2.5 scope and publication remain separate decisions.
