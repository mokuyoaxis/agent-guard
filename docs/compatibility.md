# Compatibility contract

agent-guard's promise is recoverability. A promise is only as good as its
stability over time - this page states exactly what may change and what
may not, per release class.

## The adapter contract (frozen surface)

Harness adapters integrate against exactly four things:

1. **Decision classes** - `ALLOW`, `RELOCATE`, `SNAPSHOT`, `ASK`, `BLOCK`.
2. **Reason codes** - stable machine-readable strings (`RELOCATE_TREE`,
   `COMPOUND_CWD_DELETE`, `BLOCK_PROTECTED_PATH`, ...). See
   [../skills/delete-guard/references/policy.md](../skills/delete-guard/references/policy.md).
3. **Explanation field** - human-facing text attached to every verdict.
4. **check.py exit codes** - `0` proceed/advisory-ok, `2` blocked,
   `3` ask, `1` internal error.

## Versioning promises

While the major version is `0`:

| Change | Release class |
|---|---|
| New reason code (additive) | minor |
| New decision class | minor, announced in README |
| Reason code renamed or re-semanticized | **not allowed** in 0.x - if ever needed: major |
| Manifest record gains a new field | minor (old fields never repurposed) |
| Manifest record loses/repurposes a field | major + MIGRATION.md |
| check.py CLI flags removed or re-semanticized | major + MIGRATION.md |
| Default policy verdict changes for an existing shape | minor + entry in docs/friction.md |

## Additive surfaces since v0.1.1

| Surface | Class | Notes |
|---|---|---|
| Command dialects (`cmd`, `powershell`) | minor | `classify_command(cmd, dialect=...)`; the default stays `posix`, so pre-existing callers are unaffected |
| `core/dialects.py` module + `TokenStream` | minor | Internal-but-documented; used by the dialect unit tests |
| `OpSpec.dialect` field | minor | New field; unknown fields stay opaque to consumers |
| `Ask` on PowerShell `-WhatIf` | minor | A dry run is an `ALLOW_NOOP`; a real delete keeps existing rules |
| `check.py --dialect` flag | minor | Defaults to `posix`; omitting it is byte-for-byte the old behaviour |
| `AGENT_GUARD_DIALECT` env var | minor | Read by `check.py` and both adapters; unset means `posix` |
| `BLOCK_DIALECT_UNKNOWN` / `BLOCK_DIALECT_INVALID` | minor | New reason codes (additive) |
| PowerShell parameter prefix expansion | minor | `-r`/`-rec`/`-fo` now resolve; see below |

Unknown dialect names raise `ValueError` from `normalize_dialect`, and the
production paths (`check.py`, both adapters) turn an unusable selector into
an explicit `BLOCK` rather than a silent POSIX fallback. That is a
deliberate *closed* failure: a silent fallback would lex a Windows command
line with POSIX rules and could under-restrict it.

`check.py --dialect` deliberately does **not** use argparse `choices`. An
unknown selector must produce a Decision Protocol verdict with a reason
code; a usage error carries none, and a harness could read "no decision" as
"nothing to worry about".

Phase 3 (real Windows end-to-end validation, UNC/device paths, module
auto-loading) has not happened. The dialect layer stays additive and
opt-in so Windows support lands without changing any POSIX verdict.

### PowerShell parameter prefixes

PowerShell resolves a parameter by unambiguous prefix, so `ri build -r -fo`
is `-Recurse -Force`. The guard expands those, but is deliberately
*stricter* than the host in one place:

| Written | Host expands to | Guard does |
|---|---|---|
| `-r` `-rec` `-recur` `-Recurse` | `-Recurse` | expands |
| `-fo` `-for` `-force` `-Force` | `-Force` | expands |
| `-wi` | `-WhatIf` | **refuses** - also matches `-WarningAction` |
| `-c`, `-p` | `-Confirm`/`-Credential`, `-Path`/`-PSPath` | **refuses** |

A prefix is expanded only when every candidate leads to the *same* effect
fact; otherwise it stays an unknown parameter, which policy BLOCKs. `-wi`
is the interesting case: `-WhatIf` stops the delete while `-WarningAction`
does not, so a wrong guess is asymmetric and the guard will not make it.
Every expansion is recorded in the op notes for audit.

## What is explicitly NOT frozen

- Explanation wording (humans read it; improve freely).
- Prompt-section text (guidance, not contract).
- Audit record shapes beyond the manifest - append-only by policy, but
  consumers should treat unknown fields as opaque.
- Retention thresholds (documented defaults may tune).

## Rationale

ReasonCode is the part third parties build against: harness adapters map
codes onto native UX, users write allowlists around them, incident reports
cite them. Renaming a code silently breaks all three. Everything else can
evolve faster.
