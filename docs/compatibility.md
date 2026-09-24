# Compatibility contract

agent-guard's promise is recoverability. A promise is only as good as its
stability over time - this page states exactly what may change and what
may not, per release class.

## The adapter contract (frozen surface)

Harness adapters integrate against exactly four things:

1. **Decision classes** - `ALLOW`, `SANITIZE`, `RELOCATE`, `SNAPSHOT`,
   `ASK`, `BLOCK`. (`SANITIZE` first appears in the `0.2.0-rc1` source
   preview for exfil-guard; it ranks below `ASK`, between `ALLOW` and
   `RELOCATE`.)
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
| `AGENT_GUARD_DIALECT` env var | minor | Read by `check.py` and the native shell adapters; unset means `posix` |
| `BLOCK_DIALECT_UNKNOWN` / `BLOCK_DIALECT_INVALID` | minor | New reason codes (additive) |
| `BLOCK_PROTECTED_ANCESTOR` | minor | New reason code (additive). Filesystem roots (`/`, `/home`, `/usr`, `$HOME`, ...) are refused by identity rather than by falling outside the workspace |
| PowerShell parameter prefix expansion | minor | `-r`/`-rec`/`-fo` now resolve; see below |
| `SANITIZE` decision class | minor | exfil-guard; announced in both READMEs. Adapters must state their native mapping (a harness without re-write capability degrades to ASK/deny) |
| `core/redaction.py` module (`SpanSpec`, `detect_secrets`, `detect_paths`, `scan_text`) | minor | Internal-but-documented; the fact layer for text spans |
| `policy.decide_spans()` + the exfil reason codes | minor | New function; the ten new codes are additive |
| `skills/exfil-guard/` (scripts, SKILL.md, references) | minor | Standalone CLI; no adapter change required to use it |
| `check_span.py` exit codes | minor | `0` allow/sanitize, `2` block, `3` ask, `1` error - `check.py`'s contract is unchanged |
| `.agent-guard/exfil-allow.toml` exemption file | minor | Read from the workspace root only; values exempted by `sha256:...`, never by value |

### 0.2.0 check output minimization

`check.py` still returns the same decision class, reason code, static
explanation, and exit code. Its `command` field is now `<redacted>` rather
than a copy of the input; `check_id` correlates the result with new audit
events and compensation metadata. `reasons` no longer carries target
spellings or parser fragments, and `ops` retains only bounded operation
identity and kind fields, not raw notes or shape details. Invalid dialect
selectors are reported by class without echoing the supplied string.
Adapters must not log the command while reporting a guard verdict.

New check audit events and compensation metadata do not copy raw commands.
`status --json` projects legacy audit records onto a small field set rather
than re-emitting their historical free-form fields. Historical append-only
files are **not rewritten**, and recovery manifests still need real target
paths for restoration. These changes do not sanitize a harness's own tool
logs or OS process arguments.

Unknown dialect names raise `ValueError` from `normalize_dialect`, and the
production paths (`check.py` and the native shell adapters) turn an unusable
selector into an explicit `BLOCK` rather than a silent POSIX fallback. That is a
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

## Verdict changes since v0.1.1

A changed default verdict for an existing shape is a **minor** release and
requires an entry in [friction.md](friction.md) - never a silent behaviour
shift. Additive reason codes are listed above; this table is for shapes
whose *existing* verdict moved.

| Shape | Was | Now | Why |
|---|---|---|---|
| `git clean -f...` whose target set cannot be enumerated (not a repository, `git` unavailable) | `BLOCK` / `COMPENSATION_FAILED` | `BLOCK` / `BLOCK_UNDETERMINABLE_EFFECT` | Nothing was attempted, so the verdict must not claim a compensation broke; this is the same effect-uncertainty class as every other unknowable target set (F14, policy.md row 11b) |
| `cd ... && git push --force` | `ASK` / `COMPOUND_CWD_DELETE` | `BLOCK` / `BLOCK_FORCE_PUSH` | A shape rule cannot downgrade a position-independent hard refusal (F1) |
| `cd ... && rm` with an out-of-workspace, protected, or unknowable target | `ASK` / `COMPOUND_CWD_DELETE` | The applicable hard `BLOCK` | F1 describes compensation difficulty, not permission to cross a boundary |
| Attached or newline-separated destructive commands (`echo ok;rm ...`, `echo ok` followed by newline and `rm ...`) | Could miss the destructive segment | Classifies and applies its normal policy | POSIX command boundaries must be preserved by the lexer |

Unchanged on purpose: a genuine compensation fault (`git stash create`
failing, a relocation that does not cover every target, an audit intent
that cannot persist) still reports `COMPENSATION_FAILED`. The distinction
is *was anything attempted* - not how serious the outcome would have been.

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
