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
   [../skills/delete-guard/references/policy.md](../../skills/delete-guard/references/policy.md).
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
| Default policy verdict changes for an existing shape | minor + entry in docs/history/friction.md |

### 0.2.4 maintenance patch exception

For this release, the maintainer explicitly selected `0.2.4` for the recovery
maintenance fixes and read-only trash query. This is a documented exception
to the minor-release rules above, not a general relaxation of those rules.
It adds `BLOCK_GIT_RESET_COLLISION` and refuses hard resets whose collision
preflight or command context cannot safely establish recoverability.
Relocation restore also refuses invalid source/destination boundaries and
requires preservation before a forced overwrite. See
[F24](../history/friction.md#f24--hard-reset-could-destroy-content-outside-the-tracked-snapshot)
and [F25](../history/friction.md#f25--restore-trusted-paths-and-removed-an-occupied-destination-before-recovery).

The reason string is additive; existing strings and decision classes are not
renamed. Existing CLI parameters and exit codes retain their roles. Optional
`backup_txids` and the separate trash-query schema are described below.
No manifest migration or new transaction strategy is introduced. Consumers
must accept the additional refusal reason and ignore unknown optional fields.

## Additive surfaces since v0.1.1

| Surface | Class | Notes |
|---|---|---|
| Command dialects (`cmd`, `powershell`) | minor | `classify_command(cmd, dialect=...)`; the default lexical grammar stays `posix` |
| `core/dialects.py` module + `TokenStream` | minor | Internal-but-documented; used by the dialect unit tests |
| `OpSpec.dialect` field | minor | New field; unknown fields stay opaque to consumers |
| `Ask` on PowerShell `-WhatIf` | minor | A dry run is an `ALLOW_NOOP`; a real delete keeps existing rules |
| `check.py --dialect` flag | minor | Defaults to the POSIX lexical grammar; later safety releases may newly block a previously invisible known destructive vocabulary mismatch |
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
| `doctor.py --check-drift` report and adapter `compatibility.json` profiles | minor | Additive local preflight. Without drift flags the doctor keeps its previous process-execution and exit behavior; `CURRENT` never promotes live interception above `UNVERIFIED` |
| `doctor.py --live-sentinel` and hashed hook receipt | minor | Explicit, opt-in real-host/model probe only. Default doctor behavior is unchanged; raw command/model output is not retained, and incomplete evidence is `INCONCLUSIVE` rather than PASS |
| Cross-vocabulary mismatch floor (`0.2.3-rc1`) | minor safety fix | At a selected-dialect command boundary, a supported destructive shape that produced no operation becomes target-free `UNKNOWN` → `BLOCK_UNDETERMINABLE_EFFECT`; it never borrows a compensation plan from another grammar |

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

`0.2.3-rc1` adds a focused real-Windows Core gate for dialect mismatch,
separator normalization, disposable-fixture recovery and exact-byte path
redaction. It also makes known destructive vocabulary fail closed when it
does not match the configured dialect. This is only Phase 3a: native
cmd/PowerShell execution through each harness, hook-selected dialect wiring,
broader UNC/device paths, module auto-loading and full-suite Windows
portability remain unverified.

### 0.2.4 trash location query

status.py --trash-index is an additive read-only mode, with --root, --trash,
--max-depth and --max-entries. Ordinary status arguments and result fields
keep their prior behavior. Index-specific options require index mode.
Its JSON is a separate schema_version=1 contract documented in
[trash-index.md](../guides/trash-index.md): count is candidate locations,
identified_count is layout-identified locations, and complete applies only
to the declared scope. Exit 0 means complete, 1 means partial results with
errors, and 2 means invalid arguments. The shared core query and this CLI
are additions in 0.2.4, with no storage or manifest migration.

### 0.2.4 forced-restore preservation

Forced relocation restores now preserve an existing occupant with the normal
relocation transaction and journal before replacing it. Existing CLI parameters,
exit codes and result fields retain their roles. The JSON result may add
`backup_txids`, a list of preservation attempts; inspect transaction state and
files after errors rather than treating an ID as proof of a complete copy.
Results without a preservation attempt omit this field. Text output identifies
the same attempts. CLI audit events also include these IDs when present.

The preservation transaction uses existing `tx-start`/`relocate-intent`/
`relocate` records and correlates through the existing `meta` object; no
manifest migration or new transaction strategy is introduced. Restore journal
or CLI audit write failure reports a non-success result while retaining the
filesystem outcome and preservation IDs for inspection. These changes belong
to 0.2.4; the published `0.2.3` artifact does not include them.

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
requires an entry in [friction.md](../history/friction.md) - never a silent behaviour
shift. The explicit 0.2.4 exception is recorded above. Additive reason codes
are listed above; this table is for shapes
whose *existing* verdict moved.

| Shape | Was | Now | Why |
|---|---|---|---|
| `git clean -f...` whose target set cannot be enumerated (not a repository, `git` unavailable) | `BLOCK` / `COMPENSATION_FAILED` | `BLOCK` / `BLOCK_UNDETERMINABLE_EFFECT` | Nothing was attempted, so the verdict must not claim a compensation broke; this is the same effect-uncertainty class as every other unknowable target set (F14, policy.md row 11b) |
| `cd ... && git push --force` | `ASK` / `COMPOUND_CWD_DELETE` | `BLOCK` / `BLOCK_FORCE_PUSH` | A shape rule cannot downgrade a position-independent hard refusal (F1) |
| `cd ... && rm` with an out-of-workspace, protected, or unknowable target | `ASK` / `COMPOUND_CWD_DELETE` | The applicable hard `BLOCK` | F1 describes compensation difficulty, not permission to cross a boundary |
| Attached or newline-separated destructive commands (`echo ok;rm ...`, `echo ok` followed by newline and `rm ...`) | Could miss the destructive segment | Classifies and applies its normal policy | POSIX command boundaries must be preserved by the lexer |
| `git reset --hard [ref]` with untracked/ignored or quarantine collisions | Tracked-only `SNAPSHOT` could proceed | `BLOCK` / `BLOCK_GIT_RESET_COLLISION` | Stash does not preserve the colliding content (0.2.4 maintenance exception, F24) |
| Hard reset with uncertain preflight or unsupported compound/wrapper context | Could proceed or ask despite incomplete coverage | `BLOCK_UNDETERMINABLE_EFFECT` before compensation | Snapshot and preflight must describe the actual Git context (F24) |

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
