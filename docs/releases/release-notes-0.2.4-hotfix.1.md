# Agent Guard 0.2.4-hotfix.1

Maintenance hotfix prepared 2026-10-18, based on
[0.2.4](release-notes-0.2.4.md). This opt-in backport delivers the quarantine
control-path repair while keeping the existing 0.2.5 RC line and its
cumulative changes intact. It uses GitHub prerelease and the independent
npm hotfix channel; artifact availability is shown by those release receipts.

## Scope

- **Protect recovery storage.** Quarantine root, root manifest/audit/state,
  state temporary files and session state now take precedence over ordinary
  trash housekeeping. In-workspace storage parents and reviewed aliases
  receive the same protection, including parents matching artifact rules.
- **Refuse opaque or mixed targets.** Opaque quarantine globs return
  `BLOCK_WILDCARD`; a supported deletion batch containing a protected target
  returns `BLOCK_PROTECTED_PATH` before moving or removing business files.
- **Retain ordinary cleanup.** Explicit unprotected transaction payload
  cleanup and selected transaction GC retain their existing behavior.
- **Separate release routing.** X.Y.Z-hotfix.N tags use GitHub prerelease
  and npm `hotfix`. Stable `latest` and prerelease `rc` routing stay intact.

The runtime change is limited to the existing RC classifier/policy repair.
Other RC maintenance, audit, observer and research changes are outside this
backport. No production dependency, storage/configuration migration, CLI
flag, exit code, decision class or reason code is added.

## Compatibility and installation

This deliberately changes the formerly allowed root/control-path deletion
into a refusal. The maintainer selected this bounded maintenance exception
to the normal 0.x minor-release rule. See the
[compatibility contract](../design/compatibility.md#quarantine-control-maintenance-hotfix)
and [F26](../history/friction.md#f26--housekeeping-overrode-quarantine-control-protection).

Under [SemVer](https://semver.org/#spec-item-9), -hotfix.1 is a prerelease
suffix: `0.2.4-hotfix.1 < 0.2.4`. Ordinary ranges such as ^0.2.4 do not select
this as an upgrade. Users must explicitly select the version or the
independent npm hotfix channel. This release does not move `latest` or `rc`.

After publication, install the exact version:

```sh
npm install --prefix /absolute/path/to/agent-guard-install @mokuyoaxis/agent-guard@0.2.4-hotfix.1
```

To explicitly select the hotfix channel after publication:

```sh
npm install --prefix /absolute/path/to/agent-guard-install @mokuyoaxis/agent-guard@hotfix
```

## Validation and limits

Before naming this candidate, the isolated repair passed 806 Python tests
and both Node adapter smoke groups. The old 0.2.4 baseline failed the control
regressions; the repaired source passed. Those are earlier source receipts,
not validation of the final hotfix version or proof of registry availability.

The exact versioned candidate independently passed all 807 Python tests and
both Node adapter smoke groups. All 168 candidate source files retained their
bytes throughout that run. The local 119-file package matched source and
offline installation; all ten CLI help entries, adapter import, protected
deletion refusals, normal relocation/restore and explicit transaction GC passed.
This evidence paragraph and the subsequent publication wording were completed
after the full run. Runtime and test bytes retain the tested versions;
final package/source/installation equality is checked again after these
documentation updates. An additional 194 focused maintenance tests and
34 main-branch checks also passed before publication preparation.

Only supported deletion entry points are protected. This is not same-UID
isolation or coverage for arbitrary interpreter, script or overwrite paths.
No new real-model, native-host, Windows or macOS trial is credited to this
local hotfix preparation. Exact-commit remote CI and artifact availability
are established by the versioned workflow and GitHub/npm release receipts.
