# Compatibility contract

agent-guard's promise is recoverability. A promise is only as good as its
stability over time - this page states exactly what may change and what
may not, per release class.

The stable release line is [0.2.5](../releases/release-notes-0.2.5.md), carrying
the cumulative RC maintenance below. The opt-in prerelease remains
[0.2.5-rc3](../releases/release-notes-0.2.5-rc3.md), 0.2.4 is the previous stable, and
[0.2.4-hotfix.1](https://github.com/mokuyoaxis/agent-guard/releases/tag/0.2.4-hotfix.1)
is an opt-in quarantine protection backport. Versioned sections retain the
origin and preparation labels of changes introduced by earlier releases.

RC3 publishes defensive research and an example around the existing four
zero-model controls. Experimental disclosure labels and private delegation
methods are not additions to the Decision Protocol or native-hook contract.
No production dependency, policy verdict or storage/configuration migration
is introduced by the research documentation.

## The adapter contract (frozen surface)

<a id="unreleased-feedback-and-diagnostic-maintenance"></a>

### 0.2.6 feedback and diagnostic maintenance (unpublished preparation)

The [0.2.6 preparation](../releases/release-notes-0.2.6.md) adds an opt-in POSIX Kimi
Stop hook after the existing PreToolUse path. It may request an extra response
after new compensation records. It does not change the earlier tool result,
Core verdicts, recovery format or deletion rules; it stores separate local
emission cursors. Existing PreToolUse setups keep their behavior until Stop
is explicitly configured. The current example recommends an outer 120-second
PreToolUse timeout, leaving margin around the existing 90-second guard check.

DSH Lab version/config preflight errors gain a JSON diagnostic suffix after
the stage-specific text, with fixed reason categories, child exit status,
timing and captured byte counts. The Lab CLI still reports errors on stderr
and exits 1; normal receipts, supported host versions and the 10/15-second
budgets retain their contracts. Consumers should not match the entire older
generic error sentence. Raw host/configuration output is not included.
The Kimi CLI doctor adds the separate `feedback` result described below.
An absent optional Stop passes; a configured Stop that fails configuration
or a requested probe exits 1 and makes drift `BROKEN`. This is an
upgrade-visible diagnostic change. Existing top-level fields and the
importable `check_kimi()` wrapper retain PreToolUse scope; Claude output is
unchanged. Configured Stop fields/helper bytes join the existing fingerprints.
The runtime fingerprint also includes package metadata, so version changes
can produce `DRIFTED` against an older baseline. Model delivery remains
`UNVERIFIED` even after a Bash live sentinel.

The Kimi Bash profile also adds exact `2.3.0` alongside `0.42.0` and `2.1.1`
after bounded prompt-CLI/ACP root-Bash trials. This is a declaration change;
the Core, adapter and bridge stay unchanged. Unlisted versions remain
`STALE`; a changed profile fingerprint can make an older baseline `DRIFTED`.
The separately pinned 2.1.1 Lab helper and subagent coverage do not expand.
See the [2.3.0 evidence](../reports/test-report-kimi-code-block.md#kimi-230-acceptance).

The later DSH compatibility work adds `0.2.1-alpha.4` to the default deletion
adapter alongside the two existing exact versions. Top-level engines and an
optional DSH peer align with the nested declaration, so the reviewed loaders
can enforce the list. This installs no production dependency and changes no
Core policy. Optional native-read fingerprints and Lab host formats remain
separately pinned. See [DSH compatibility](dsh-compatibility.md) and the
[native report](../reports/test-report-dsh-alpha4.md).

The maintainer selected 0.2.6 for this bounded feedback/diagnostic maintenance;
the additive surfaces below are carried as an explicit maintenance-version
exception. This does not relax the general minor-release rules or authorize
Core policy changes. These additions are absent from published 0.2.5.
0.2.6 is not published; the tag decision is pending.

### 0.2.5 feedback maintenance

Deletion rules, wildcard refusal, Decision/reason codes, CLI flags and exit
roles retain their contracts. `ALLOW_NOOP` receives a clearer explanation;
human check output displays explanations. Optional `storage_notice`/`gc`
metadata describes actual retained files and maintenance commands without
entering the shared audit payload. Status gains a local-versus-host notice.

The DSH adapter now checks local Python/checker imports before registration
and reports startup failure explicitly rather than returning silently when
shell/script dependencies are missing. This changes plugin lifecycle
diagnostics, not the Core policy or the host's ability to continue other
tools. Python must also be available on the plugin process PATH for this
startup probe. Its scope is the plugin process; live interception remains
`UNVERIFIED`. Status adds adapter facts and static runtime diagnostics.
Failed status queries return `ok: false`, `report: null` and `STATUS_*`
diagnostics for execution failure, timeout, unsuccessful exit or unusable
reports. Partial relocation failures preserve the existing failure/exit
semantics and expose their transaction IDs and retained-storage guidance.
No storage/configuration migration, new dependency or rm execution route is
introduced. Details are in the [feedback guide](../guides/recovery-feedback.md).

The maintainer selected 0.2.5 for the cumulative RC1–RC3 maintenance and this
feedback work. It carries forward the bounded RC1 maintenance exception
documented below. Quarantine control-path refusals inherited from RC1 and
explicit DSH initialization failure are upgrade-visible changes. This choice
does not permit unrelated verdict changes under the patch versioning rules.

### Existing decision surface

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
| Kimi doctor `feedback` object (unreleased) | additive diagnostic | Existing top-level fields retain PreToolUse scope; optional Stop has separate configuration/local results and always-unverified model delivery. Absent Stop passes; a configured Stop failure exits 1 and prevents baseline creation/live sentinel. Configured Stop fields/helper join drift fingerprints. `check_kimi()` retains its PreToolUse-only contract; Claude output is unchanged. |
| Cross-vocabulary mismatch floor (`0.2.3-rc1`) | minor safety fix | At a selected-dialect command boundary, a supported destructive shape that produced no operation becomes target-free `UNKNOWN` → `BLOCK_UNDETERMINABLE_EFFECT`; it never borrows a compensation plan from another grammar |

### 0.2.5-rc1 assessment contract clarification (source candidate)

Advisory and dry-run retain their existing best-effort audit behavior and
metadata side effects. Tests and documentation distinguish unchanged command
targets from zero filesystem writes, and advisory exit 0 from execution
authorization. No flag, result field, policy decision, audit storage location
or exit code changes. See the [assessment contract](../../skills/delete-guard/references/policy.md#assessment-and-dry-run-side-effects).

### 0.2.5-rc1 safe_delete audit minimization (source candidate)

New safe_delete audit events replace raw target/reason payloads with
`target_count`; established relocation reports retain `txid` and add
`moved_count`/`skipped_count`, including storage-failure decisions. Decision
codes, phases and static outcome labels remain available. Free-form
`--reason` stays accepted but is omitted from new audit events and
`tx-start.meta`. Essential recovery paths/intents, CLI result fields,
verdicts, exit codes and durability gates retain their behavior.
Audit event shapes are not frozen. At the released RC1 baseline, generic
audit/session handling is unchanged; the RC2 projection below
extends it. See the [writer contract](../../skills/delete-guard/references/policy.md#safe_delete-audit-metadata).

### 0.2.5-rc2 shared audit metadata maintenance (source candidate)

`append` stores a closed metadata projection and returns the stored record;
`tail` projects legacy records without rewriting them, skips non-object JSON,
and returns no records for non-positive limits. Unknown/custom fields and
free text under allowed names are omitted. Audit shapes are not frozen, but
callers relying on arbitrary payload storage must adapt explicitly.

Session/display identities become deterministic opaque correlation. Generated
transaction/check IDs remain exact; noncanonical audit IDs become correlation
tags, and `find_by_txid` accepts the original spelling. Unknown GC selections
use digest metadata; CLI results and recovery journal IDs/paths remain exact.
Ordinary status actor display is projected without changing authorization
state or session selection. No decision/exit/flag semantics, manifest schema,
audit locations or durability gates change. This work is not included in the
published RC1; see the [full audit contract](../guides/audit-metadata.md).

### 0.2.5-rc2 observer startup maintenance (source candidate)

The readiness window stays five seconds after spawn. Startup logs add static
phases and monotonic timestamps without tokens, paths or exception text.
They remain diagnostic and do not establish evidence health. Startup timeout
requests cooperative cancellation, then permits up to two seconds of local
child reaping; a live child remains registered without a forced signal.
Reap timeouts preserve the original LabError. Cancelled startup is
`FAILED` / `OBSERVER_STARTUP_CANCELLED` and `INCONCLUSIVE`, including late READY
publication; ordinary stops after accepted readiness retain their meaning.
Public CLI arguments, exit codes, evidence levels and loopback scope stay
unchanged. The first intermittent timeout's root cause is still unknown.
See the [Lab contract](../lab/guard-lab.md) and [RC2 notes](../releases/release-notes-0.2.5-rc2.md).

### 0.2.5-rc1 exemption configuration diagnostics (source candidate)

The existing limited parser, first-readable-file precedence and exemption
matching are retained. No full TOML/gitignore parser, configuration migration,
production dependency or new policy reason is added. Unknown top-level keys
and bare lines remain ineffective; recognized legacy entries may still apply
alongside warnings. Diagnostics do not change verdicts or exit codes.

Successful text scans may add `exemption` provenance/count/diagnostic metadata
to checker JSON and the sanitizer library result. Human checker and sanitizer
CLI warnings use stderr; JSON checker stdout and sanitizer payload/plan stdout
keep their respective formats. Normal missing/valid configurations remain
quiet. Diagnostic codes are configuration metadata, not Decision Protocol
reason codes. Consumers should tolerate the additive fields and warnings;
these diagnostics originated in RC1 and are retained by RC2 and RC3. The diagnostic
grammar and privacy contract are in [rules.md](../../skills/exfil-guard/references/rules.md#configuration-diagnostics).

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

### 0.2.5-rc1 lifecycle and restore-audit maintenance (source candidate)

Existing `BLOCK_PROTECTED_PATH` now wins before `ALLOW_TRASH_GC` for
workspace/Git and quarantine control paths. Quarantine root metadata,
session state and in-workspace storage parents are protected. Ordinary
unprotected explicit payload housekeeping and the explicit GC CLI remain
available. Opaque quarantine globs now return `BLOCK_WILDCARD`; safe_delete
can enumerate a concrete batch before policy checks.
See [F26](../history/friction.md#f26--housekeeping-overrode-quarantine-control-protection).
This changes default verdicts and would ordinarily follow the minor-release
contract. The maintainer explicitly selected 0.2.5-rc1 for this maintenance
candidate instead of 0.3.0. This is a new, bounded exception for the existing
control-path/glob, PURGED inventory, audit-writer and exemption-diagnostic
maintenance, not an extension of the prior 0.2.4 exception to arbitrary changes.
Existing decision/reason strings, CLI arguments and exit codes retain their
roles. There is no manifest migration or production dependency. Publication,
and any later stable 0.2.5 decision, remain separate from candidate preparation.
The exact -rcN tag maps to GitHub prerelease and npm rc, preserving stable latest.
See the [candidate notes](../releases/release-notes-0.2.5-rc1.md).

Active `tx_inventory` and GC plans now omit `PURGED` history, while
`transactions` and manifests retain it. Failed/unpurged transactions retain
their prior eligibility rules; no stash deletion or storage migration is added.

Restore audit records replace `restored`/`conflicts`/`errors` payload copies
with `restored_count`/`conflict_count`/`error_count`. Established IDs and backup
attempt IDs stay correlated; invalid/unknown caller IDs use `txid_sha256`.
Audit shapes beyond manifests are not frozen. CLI parameters, exit codes,
recovery manifests and returned recovery paths/errors retain their roles.
That released change is one writer's minimization. The RC2 shared
projection is described above; it does not rewrite manifests or old log bytes.

A changed default verdict for an existing shape is a **minor** release and
requires an entry in [friction.md](../history/friction.md) - never a silent behaviour
shift. The bounded 0.2.4 and 0.2.5-rc1 exceptions are recorded above. Additive reason codes
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
| Quarantine root/control paths, protected metadata inside trash, or an in-workspace storage parent | Could be allowed as housekeeping or regenerable content | `BLOCK_PROTECTED_PATH` | Recovery/state evidence protection precedes housekeeping (introduced in 0.2.5-rc1, F26) |

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
