# Compatibility contract

agent-guard's promise is recoverability. A promise is only as good as its
stability over time - this page states exactly what may change and what
may not, per release class.

The active **0.3.0 maintenance scope** adds reviewed-task execution and
queries. Its [acceptance table](../reports/test-report-maintenance-0.3.0.md)
records passed checks and remaining gates; it is not yet a published artifact.
Historical sections below retain their original release decisions. Their
paused 0.3.0 references concern the earlier disclosure-authorization research,
not this maintenance scope.

Version [0.2.9](../releases/release-notes-0.2.9.md) adds read-only Cleanup
inventory and measurement, faster planning and a repository-corpus test fix.
Version [0.2.8](../releases/release-notes-0.2.8.md) adds diagnosis and
reliability maintenance to the published
[0.2.7](../releases/release-notes-0.2.7.md) baseline. Use exact GitHub release
and npm version links to confirm availability; a version in source or a local
tarball does not establish publication.
Earlier release [0.2.6](../releases/release-notes-0.2.6.md) carries cumulative
RC maintenance and the feedback/diagnostic additions below. The opt-in
prerelease [0.2.5-rc3](../releases/release-notes-0.2.5-rc3.md), earlier stable
0.2.5 and the
[0.2.4-hotfix.1](https://github.com/mokuyoaxis/agent-guard/releases/tag/0.2.4-hotfix.1)
quarantine protection backport retain their separate artifacts. Versioned
sections retain the origin and preparation labels of changes introduced by
earlier releases.

The maintainer selected **0.2.7** for Cleanup Copilot, deletion admission and
optional MCP tools. The bounded versioning decision is recorded below; the
separate disclosure-authorization research remained paused at that checkpoint.
The later **0.2.8** selection is independently scoped to MCP diagnosis and
reliability maintenance, as recorded below; earlier exceptions do not grant
permission to change unrelated policy under a patch number.
The **0.2.9** selection has its own bounded decision for the Cleanup
observation interfaces and accompanying performance/test maintenance below.

RC3 publishes defensive research and an example around the existing four
zero-model controls. Experimental disclosure labels and private delegation
methods are not additions to the Decision Protocol or native-hook contract.
No production dependency, policy verdict or storage/configuration migration
is introduced by the research documentation.

<a id="unreleased-cleanup-observation-additions"></a>

## Cleanup observation additions (0.2.9)

Version 0.2.9 adds independent read-only discovery/measurement schemas,
CLI modes and MCP tools. They are absent from published 0.2.8 and cannot
produce an executable plan or approval. Its MCP server advertises seven
tools; its doctor verifies that set and invokes only the original three
state queries. Published 0.2.8 retains its five-tool contract. Use doctor and
server from the same installation; identical version text in a working
checkout does not establish identical bytes or tools.

Existing default Cleanup plans, execution confirmation, original five MCP
tools, deletion policy and recovery formats retain their interfaces. No
dependency, configuration or storage migration is introduced.
See [new observation semantics](../guides/mcp.md#cleanup-observations-029).
The versioned checkpoints below describe their original release scope.

The new CLI modes, independent observation JSON and two MCP tools are
additive public interfaces, even though they grant no deletion permission.
The maintainer selected 0.2.9 through the new bounded decision below;
earlier patch-version exceptions do not authorize it. The paused 0.3.0
research roadmap is a separate scope decision and is not resumed.

## Skill installation (0.3.0 development, unreleased)

This checkout adds `install_skill.py` / `agent-guard-install-skill` and a
real-path bootstrap for delete-guard scripts. The helper defaults to a plan;
`--execute --target PATH` registers the selected complete package's Skill
directory link, retaining a previous Skill outside the search directory.
It does not edit host settings, reset budgets or change recovery formats.
The installer and its new bin entry are absent from published 0.2.9.

Script startup resolves symlinks before locating the package Core. A detached
Skill without Core exits 1 with installation guidance rather than exposing
the import traceback; it does not search the workspace or use another Core.
New [setup instructions](../guides/agent-setup-prompt.md) distinguish the
four-host maintenance target from measured support and MCP from native hooks.
Runtime/link/update and package smoke checks passed in owned POSIX locations;
global installations were not automatically updated. Native Windows link
installation, final candidate checks, version alignment and publication remain separate.

## The adapter contract (frozen surface)

### Saved target proposals (0.3.0 development, unreleased)

Cleanup adds `--target`, `--targets-file`, `--discard-targets`, `--save-plan`
and `--from-plan`. Target mode scopes traversal and tracked Git facts to the
listed literal roots. Its plan adds `scope`, `targets` and an opaque session
fingerprint; the legacy default workspace plan retains its output and
fingerprint construction. Discovery/measurement `--scope` stays separate.

Explicit saving creates `cleanup_saved_plan/v1` records under the protected
`.agent-guard/cleanup-plans` directory, retaining the plan and proposed
selection. It does not migrate existing config, manifest or recovery data.
The saved ID checks consistency, not authenticity. A view does not refresh;
approval reconstructs the same scoped plan before and after terminal
confirmation. Existing CLI full-workspace execution remains available;
MCP access is described below. Step-4 guidance generates a literal-target proposal command.
See [flags, selection, effects and limits](../guides/cleanup-plan.md#saved-explicit-target-plans-030-development).
Runtime, save/read/refresh, legacy-plan and source-change checks have recorded
results. Literal observation does not expand the existing execution API's
filename support; some glob-metacharacter names, including `[`, still refuse.

### Approved cleanup tasks (0.3.0 development, unreleased)

`--from-plan ID --approve` obtains operator-terminal confirmation and returns
a task ID without deleting source data. `--task ID` reads status, and
`--task ID --execute` permits one agent execution attempt without another
terminal prompt. Saved-plan `--execute` remains an operator convenience that
creates and immediately attempts the same kind of task. Legacy full-workspace
execution retains its original interface and per-row accounting.

New `cleanup_task/v1` control records are stored under the protected
`.agent-guard/cleanup-tasks` subtree. Existing admission-state version 1 gains
optional task grant fields for task ID, effect binding and a shared rate event;
legacy grants retain their behavior. There is no configuration or recovery
format migration. Approved tasks must be executed by this same implementation;
older versions do not implement effect binding or task accounting. Do not
switch to an older package while task approvals remain outstanding.

Tasks bind the existing short-lived grant to the exact selection and planned
effects. They group at most two safe_delete calls, charge one rate event and
debit logical bytes/entries per admitted group. Preflight does not reserve
capacity. Durable intent and receipts distinguish completion, partial work
and unknown outcomes; a repeated execute returns status rather than retrying.
Read-only query exits 0 for a readable record regardless of completion.
Tokens remain private. Same-UID files and terminal prompts are cooperative,
not independent authentication. See [task states and limits](../guides/cleanup-plan.md#operator-approved-tasks-030-development).
Runtime/concurrency/failure regressions passed, with constructed grants kept
separate from the real operator-approved 71-root protocol-client task. The
MCP tools below share these semantics. DSH/Kimi fresh-approved 71-root tasks
passed; Codex later passed a fresh-approved single-root real cache task with
independent recovery. Its separate 71-root task remains unverified after an
earlier host usage-limit block. Claude model work is deferred.

### Fixed MCP storage (0.3.0 development, unreleased)

Trusted startup `--trash-root` selects a stable absolute quarantine; omitted,
it retains `WORKSPACE/.agent-trash`. The explicit path is resolved once at
startup. Tool arguments and ambient environment cannot select another bucket.
Doctor and operator handoff commands retain the same storage/session binding.
This adds no dependency or configuration/recovery-format migration.

Selecting another bucket selects its existing authorization, budget and
recovery records; it does not merge or migrate them. Existing plans/tasks
retain their original path binding. Separately authorized directory relocation
is an operator operation, not a side effect of starting the server.

Capacity measurement follows the quarantine root to its physical directory,
including a retained default-path link; deletion-target links remain opaque.
Old installed copies do not acquire this correction automatically. Storage
regression, stdio recovery and a local DSH directory-layout check have passed;
the latter does not certify every application layout. A later original-root
DSH headless startup and approved MCP batch also passed, separately from native-plugin evidence.
See [storage and limits](../guides/mcp.md#select-a-fixed-quarantine-directory-030-development).

### MCP maintenance tasks (0.3.0 development, unreleased)

Three additive tools bring this checkout's MCP contract to ten:
`cleanup_plan`, `cleanup_execute`, `cleanup_task_status`. Published 0.2.9
retains seven; update doctor and server from the same installation. No SDK,
protocol version, configuration or recovery migration is introduced, and the
original seven operation arguments retain their contracts.

Planning accepts bounded literal targets and optional declarations/limits.
It defaults to zero writes; explicit saving retains the CLI proposal and
selection. Saved-ID viewing with read controls does not refresh. Valid incomplete plans
return `ok: true, partial: true` with the original CLI exit code. The plan
tool's read-only annotation is false because it can save metadata. Status
is read-only, and approved execution accepts only a task ID. MCP does not
provide approval, tokens, budget identities, force restore or GC execution.

The development feedback refinement distinguishes known Git tracking from
missing discard evidence and unavailable Git facts. Categories, selection
rules and declarations keep their existing meanings. Reason text is included
in plan fingerprints: affected older snapshots remain readable, but refresh
requires a newly reviewed proposal and any required new approval. This is
the existing changed-plan boundary, with no record migration. The MCP tool
description also explains when to include the already-supported discard
declaration in the first proposal; argument names and defaults are unchanged.

Task query success reports readable state, not completed cleanup. Mutating
failures preserve CLI evidence; missing responses retain the requested task
ID and query guidance. Existing calls retain their 120-second deadline; task
execution has a separate 300-second outer deadline for two inner groups.
Timeouts do not imply rollback or safe automatic retry. doctor validates ten
registrations/annotations and invokes only its original three read-only state
queries. SDK/runtime and local approved execution have passed; three hosts
completed ordinary model-backed MCP maintenance and completed-task replay.
DSH/Kimi first executed new approved batches; Codex later completed a
fresh-approved real cache task with controller recovery. Its separate
71-root task remains unverified, and Claude model work is deferred. The
operator's Core approval and the client's tool permission are separate:
follow the bounded setup in the MCP guide without changing tool annotations
or broadening Core limits.
Later [native handoff checks](../reports/test-report-maintenance-0.3.0.md#native-interception-to-mcp-handoff-2026-11-12)
observed a Kimi root-Bash block, MCP plan, real terminal approval,
original-session execution and independently verified MCP recovery;
DSH's sandboxed profile was environment-blocked while its isolated local
control passed. Broader native paths retain their separate evidence limits.
See [arguments and handoff](../guides/mcp.md#reviewed-maintenance-tasks-030-development).

### Maintenance output views (0.3.0 development, unreleased)

Shared Core projections add `cleanup_view/v1` for overviews/detail pages and
`maintenance_summary/v1` for execution summaries. Views are generated after
planning/receipt creation, outside plan fingerprints and persisted payloads.
Existing saved plans, approvals and task formats are unchanged.

Cleanup plan/task text defaults to summary, and existing CLI `--json` defaults to full.
Restore/GC text prints a summary plus the original detailed rows by default;
explicit `--view summary` omits rows. There is no general retained-detail
endpoint for those operations, so retrieving details must not require replay.
The legacy workspace execution flow also keeps per-row text receipts by
default because it has no retained task-detail endpoint.
Full execution JSON keeps existing keys/exit semantics and adds
`execution_summary`. The original seven MCP operation arguments remain the
same, with the later additive status overview controls below. New cleanup MCP tools default to summary; plan/status support paginated
details/full, execution supports summary/full. The tool set stays ten.

Saved-plan input now permits read controls alongside its ID, while rejecting
target/declaration/selection overrides. Detail filters never narrow execution,
and confirmation still presents the complete selection. Pages distinguish
display coverage from scan completeness; mutable task pages are not atomic
multi-request snapshots. Full task output remains the public projection,
without private grant tokens or raw task state.

Counts retain selected-root, candidate-row, recursive admission-entry,
restore-item and GC-transaction units. Confirmed receipt counts are distinct
from unknown actual effects and undispatched roots. Size estimates, previous
admission previews, unestablished debit and unmeasured physical freed space
remain separate. GC's existing best-effort logical inventory is not promoted
to a complete space measurement. No deletion/approval/GC authority changes.
See [views, fields and compatibility](../guides/maintenance-output.md).
Runtime/view/legacy-output and recorded transport checks have passed. These
bounded observations do not establish a general token or speed reduction.

### Unified read-only queries (0.3.0 development, unreleased)

The version scope now ships CLI/JSON/MCP queries; GUI/web/TUI applications
and operation panels are deferred. `workspace_query/v1` is a separate bounded
metadata projection. Plain status text defaults to the formatted overview;
existing `--json`, explicit `--tail` and MCP status default to the legacy
full result. Explicit `--view full` also retains that behavior.

The MCP status tool adds `view: overview/full` and overview-only `limit`
(1–50, default 10). Existing tail/default semantics and other tool schemas
are retained; the service stays at ten tools. Valid partial overview results
use `ok: true, partial: true` with CLI exit 1. Doctor still performs only its
original three state calls, without certifying the new overview path.

Bounded task listing uses the same validation as direct task reads and
filters the current budget/quarantine before projecting public fields. The
JSON reader accepts an internal byte bound; default saved-plan/task limits
are unchanged. Existing transaction reconciliation can accept a validated
bounded record list for queries, while restore/GC still use their full input.
No writer, approval/budget rule, configuration or persistence format changes.

Overview does not walk payload trees, measure occupancy or establish
capacity-based GC eligibility. Unknown measurements, incomplete coverage and
existing Core best-effort path/mode semantics stay explicit. Terminal layout
and optional TTY section colors use stdlib only; JSON/MCP contains no added
ANSI sequences. See [limits and query contract](../guides/read-only-query.md).
Runtime, bounded-read and rendering checks passed in the recorded POSIX
environment, alongside bounded model-host overview reads. Other platforms,
native interception and final artifact validation remain separate.

### Dry-run admission (0.3.0 development, unreleased)

`safe_delete --dry-run` gains an additive `admission` projection and next-step
guidance. Its original policy verdict, outcome and exit codes remain: current
execution admission may be blocked while an assessment exits 0; policy BLOCK
still exits 2. The MCP tool forwards the projection in `result.admission`,
with its existing `ok`/`isError` mapping and arguments. The task tools are
separate additions to the previous seven-tool set.
`check.py` advisory responses are unchanged.

Shared read-only checks preserve execution refusal order. Only `admit()`
locks, consumes approval, saves a debit and yields to mutation. The projection
does not prove lock availability or payload-storage writability and reserves
nothing; the old advisory audit metadata side effects remain. Generated
commands use existing CLIs and the observed targets, with structured argv
and POSIX shell quoting. The preview adds no approval endpoint, reset or automatic GC.
See [field semantics](../guides/deletion-limits.md#dry-run-admission-preview-030-development).
Runtime and compatibility regressions passed at the recorded maintenance
checkpoint; the final release candidate still needs its own full verification.

<a id="mcp-doctor-028-development"></a>

### MCP doctor (0.2.8)

`doctor.py mcp` is an explicit local diagnosis path. It accepts the selected
Python interpreter, existing workspace, optional reviewed server script and
a diagnostic timeout; it does not parse or rewrite host configuration. It
checks the optional pinned SDK and its imports, service handshake/name/version,
the five-tool set and query annotations, workspace/quarantine binding and
successful status/transactions/gc_plan calls. The selected service version
must match the doctor's package version. It does not call a model or execute
deletion, restore, GC or budget-reset tools.

Its independent `schema_version: 1` report uses fixed `status`, `stage`, `code`,
`message`, `guidance` and `checks` fields rather than raw child stderr,
configuration contents or recovery/audit records. It keeps `host_connection`,
`model_delivery` and `host_interception` at `UNVERIFIED`: direct service
diagnosis cannot certify another client's loading or behavior. Exit `0` means
PASS, `1` means diagnostic failure and `2` remains argument-usage failure.
Diagnostic codes belong to this report, not the Decision Protocol reason-code
namespace. Existing Kimi/Claude doctor interfaces retain their semantics.
`diagnostic_stderr_bytes` is a count, not raw stderr. A successful status check
also records `deletion_admission` as `BLOCKED` or `READABLE`; the latter means
the information was inspected, not that deletion is authorized. A connection
PASS can coexist with blocked admission, which the text renderer highlights.
The 5–120 second diagnostic deadline defaults to 30 seconds; service-process
shutdown can add up to three seconds of process waits and one second of reader-thread waiting.

The SDK remains separately optional, including when invoking Core/CLI or
doctor help. Changing package/helper bytes can change existing doctor runtime
fingerprints and produce `DRIFTED`; an old baseline is not automatically
replaced. See [options, result scope and recovery after a lost response](../guides/mcp.md#diagnose-a-local-service-028).

<a id="local-mcp-tools-027-candidate"></a>

### Local MCP tools (0.2.7)

The 0.2.7 release introduced an optional local stdio MCP server with five explicit tools:
safe_delete, ordinary restore, transactions, status and GC-plan preview.
It invokes existing CLI operations without a shell, preserves policy/audit/
recovery semantics and binds workspace/session identity at trusted startup.
Tool calls cannot select a new workspace, session, environment or quarantine.
`--session` selects both saved CLI authorization mode and deletion budget;
omission uses legacy CLI state and the shared default bucket. Reusing saved
state requires matching workspace, quarantine and session. Native host-session
restrictions/in-memory veto are not inherited without a trusted bridge.
Quarantine deletion, force restore, GC execution, budget reset and Cleanup
approval are not exposed. Existing CLI interfaces remain available separately.

The optional `mcp==2.3.0` Python SDK and its transitive dependencies install
into a separate environment; Core/CLI still has no third-party Python
requirement. This interface is included in 0.2.7 and is absent from published
0.2.6. It does not resume 0.3.0. It is a cooperative tool interface, not expanded
native interception. Existing host-version evidence and doctor scope do not
automatically cover MCP. On the 2026-10-28 frozen candidate, the first local
full run completed 1123 Python cases with one existing observer-startup error
and did not run Node; its cause remains undetermined. The second full run
passed 1123 Python cases and both Node groups in 1143.435 seconds, with no
failures, errors, skips or timeouts. Neither invoked npm directly or changed
the runtime/tests. A 154-file offline installation and three real Kimi 2.3.0
native-MCP trials passed; at that checkpoint Claude/Cursor/Codex setup examples
were docs-only. Later Codex 0.165.0 acceptance of the published 0.2.7 package
is recorded separately in the [MCP guide](../guides/mcp.md#codex-01650-with-published-027).
Without the optional SDK, seven protocol tests skip. The added SDK-backed
Python 3.10/3.13 CI job had no remote execution at the 2026-10-29 local
preparation checkpoint. See
[setup, full failure history and platform/tool limits](../guides/mcp.md).
Those measurements predate the 0.2.7 version/document alignment and are not
acceptance of the final versioned artifact. The subsequent 219-file 0.2.7
candidate passed 1129 Python cases and both Node groups in 1194.265 seconds,
with no failures, errors, skips, timeouts or automatic retries. Its 155-file
offline package passed 12 CLI entry-point and installed MCP checks, including
`serverInfo.version == 0.2.7`. Local release-gate checks passed; that local
preparation checkpoint did not include final-commit remote CI or publication.
No additional real-model trial was run. See
the [versioned validation record](../releases/release-notes-0.2.7.md#validation-status).

<a id="deletion-admission-limits-027-candidate"></a>

### Deletion admission limits (0.2.7)

The current checkout adds shared deletion configuration, persistent
per-session admission budgets, rate/repeat limits and quarantine capacity
checks. `.agent-guard/delete-policy.json` is optional; invalid configuration
or state fails closed. Configuration paths and the new quarantine
`admission.json`, temporary state and `admission.lock` are protected. Existing
recovery manifests and transaction strategies are retained without migration.

New reason codes are `BLOCK_DELETE_CONFIG_INVALID`, `BLOCK_DELETE_PROTECTED`,
`BLOCK_DELETE_LIMIT`, `BLOCK_DELETE_RATE`, `BLOCK_DELETE_REPEAT`,
`BLOCK_DELETE_BATCH_CONFIRM` and `BLOCK_DELETE_STATE_INVALID`. Decision classes
and `check.py` exit codes retain their contracts. Limits are applied at
execution admission; an earlier advisory assessment is not a capacity grant.
Covered automatic compensation/artifact paths can newly BLOCK when a
configured limit is reached. The general versioning promises below classify
these as minor safety changes. The maintainer selected 0.2.7 for these bounded
additions as an explicit exception recorded below, not as a change shipped
in 0.2.6 or a general permission to change policy in patch releases. The
separate 0.3.0 plan remains paused.

Cleanup retains plan schema version 1 and terminal-confirmed execution, then
issues metadata/session/configuration-bound, ten-minute approval for each
selected path once. Covered automatic large-delete checks BLOCK with Cleanup guidance;
there is no new host ASK/resume protocol. `status.py --reset-delete-budget`
adds explicit interactive reset of the current bucket, not capacity or GC.
Existing compound-command ASK and Git SNAPSHOT paths remain outside the new
budget. Normal restore, explicit GC and unprotected quarantine payload
housekeeping remain exempt; configuration/control-path protection still applies.
Same-UID state remains advisory; accounting is logical admitted work, not an
OS quota. New Core bytes change doctor fingerprints. The 2026-10-28 public
runtime passed three independent 1090-case Python plus two-Node-group runs,
an offline 149-file package check and three real Kimi 2.3.0 root-Bash scenarios.
These local POSIX results do not establish new platform or tool coverage;
previous Cleanup and release evidence keeps its original scope. See
[defaults, accounting and recovery](../guides/deletion-limits.md).

<a id="cleanup-copilot-027-candidate"></a>

### Cleanup Copilot (0.2.7)

The checkout adds `core.cleanup` and `agent-guard-cleanup` with read-only
planning, categorized candidates and explicit terminal-confirmed selection.
Execution delegates to the existing safe_delete CLI one concrete target at a
time. Plan JSON has its own `schema_version: 1`; it is not a new Decision
Protocol surface. The initial Cleanup-only checkpoint retained existing
deletion policy; the subsequent admission changes are described above.
Recovery formats, explicit GC and native adapter tools retain their contracts.
Cleanup itself adds no third-party Core dependency or configuration migration.
This feature is included in 0.2.7 and is absent from the published 0.2.6 package.

The new Core module and package metadata change doctor runtime fingerprints.
An old baseline can become `DRIFTED`; it is not automatically replaced.
See [the CLI contract](../guides/cleanup-plan.md) for confirmation, partial
execution, output privacy, scan limits and the existing concurrency boundary.

<a id="unreleased-feedback-and-diagnostic-maintenance"></a>

### 0.2.6 feedback and diagnostic maintenance

Release [0.2.6](../releases/release-notes-0.2.6.md) adds an opt-in POSIX Kimi
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

<a id="029-cleanup-maintenance-decision"></a>

### 0.2.9: bounded Cleanup maintenance decision

The maintainer selected **0.2.9** for read-only directory discovery and
measurement, planning performance and the normal-corpus test correction.
The new CLI modes, version-1 observation schemas and two MCP tools are
additive public interfaces carried as this release's explicit maintenance
exception. The general release-class promises remain in force; this decision
does not authorize other patch releases or resume the 0.3.0 research plan.

Upgrade both doctor and server from the same package: 0.2.9 expects seven
tools, while 0.2.8 expects five. Existing installations need an explicit
upgrade. Changed package/helper bytes may make an older doctor fingerprint
baseline `DRIFTED`; review it rather than silently replacing it.

The original five MCP operations, default executable plans and confirmation,
Decision classes/reasons, default deletion rules, budgets, recovery manifests,
ordinary restore and preview-only MCP GC retain their contracts. Observation
IDs and policy projections cannot authorize deletion. No dependency or
configuration/storage migration is introduced. See the
[release notes](../releases/release-notes-0.2.9.md).

<a id="028-diagnosis-maintenance-decision"></a>

### 0.2.8: bounded diagnosis maintenance decision

The maintainer selected **0.2.8** for the explicit MCP doctor path, real
protocol-interruption checks and their documentation. Earlier doctor drift
and live-sentinel additions were classified as minor below. The new optional
diagnostic surface is carried as this version's bounded maintenance decision;
the 0.2.7 exception is not inherited and the general release-class promises
remain in force.

The independent MCP doctor parameters, version-1 diagnosis report and exit
codes are the upgrade-visible additions. Package/helper changes may affect
existing doctor fingerprints. The five MCP tools and their arguments,
Decision classes/reasons, default deletion policy, persistent accounting,
recovery manifests, ordinary restore and preview-only MCP GC retain their
contracts. No configuration/storage migration or default SDK installation is
introduced. The diagnosis checks a selected service; it adds no native host
interception or client configuration management. The 0.3.0 and commercial
plans are not resumed.

Source validation, installed-package and workflow results retain their
separate checkpoints in the
[0.2.8 notes](../releases/release-notes-0.2.8.md#validation-status).

<a id="027-candidate-bounded-maintainer-selected-exception"></a>

### 0.2.7: bounded maintainer-selected exception

The maintainer explicitly selected 0.2.7 for the completed Cleanup Copilot,
deletion-admission hardening and optional local MCP adapter. This version
is a bounded exception to the general minor-release rules above; the older
0.2.4, 0.2.5 and 0.2.6 exceptions do not automatically authorize it. It does
not relax those rules for other patch releases or resume the 0.3.0 plan.

The upgrade-visible changes are explicit:

- Covered automated deletion/compensation paths now enforce persistent
  session byte/entry budgets, rate/repeated-target-set limits and quarantine
  admission capacity. Regenerable direct deletion also consumes applicable
  budgets. Failed admitted operations and restores do not refund them.
- Ordinary large batches now BLOCK with Cleanup guidance before covered
  compensation; reviewed terminal-confirmed Cleanup provides a ten-minute,
  per-path approval for that gate only. It cannot bypass other limits.
- Optional deletion configuration and configured protected paths precede
  artifact/housekeeping exemptions. Invalid configuration or unavailable
  admission state refuses new covered deletion instead of silently disabling
  the checks. The configuration directory and admission controls are protected.
- Seven additive reason codes are introduced: `BLOCK_DELETE_CONFIG_INVALID`,
  `BLOCK_DELETE_PROTECTED`, `BLOCK_DELETE_LIMIT`, `BLOCK_DELETE_RATE`,
  `BLOCK_DELETE_REPEAT`, `BLOCK_DELETE_BATCH_CONFIRM` and
  `BLOCK_DELETE_STATE_INVALID`. Existing code names, decision classes,
  check exit codes, recovery manifests and transaction strategies retain
  their meanings.
- Cleanup adds explicit plan/selection/terminal confirmation; optional MCP
  adds a separate stdio tool interface and SDK environment, not automatic
  interception or a new host ASK/resume protocol.

Default limits are a 5-GiB/100000-entry quarantine admission ceiling,
1-GiB/10000-entry cumulative session budget, 60 operations per 60 seconds,
five admissions of the same target set per window, and batch review above
256 MiB or 1000 filesystem entries. These are logical admission figures,
including directory metadata and conservative hardlink counting, not an OS
disk quota. See the [limit guide](../guides/deletion-limits.md) for fixed
inventory bounds and configuration semantics.

Normal restore, explicit GC and unprotected quarantine payload housekeeping
retain their exemptions; existing compound ASK and Git SNAPSHOT paths are
outside the new budget. Capacity pressure never authorizes automatic GC or
a permanent-delete fallback. Operators should inspect status and the stated
reason, review a fresh Cleanup plan for a batch refusal, or explicitly reset
the current budget with an interactive `RESET` only after investigating the
caller. Protection/configuration failures require preserving evidence and
correcting the actual issue, not removing state or changing identity.

The changed existing shapes are recorded in
[F32](../history/friction.md#f32-deletion-admission),
[F33](../history/friction.md#f33-batch-confirmation) and
[F34](../history/friction.md#f34-deletion-protection).
The [0.2.7 version notes](../releases/release-notes-0.2.7.md) describe scope
and upgrade impact, including the passing local 1129-case/two-Node-group and
155-file versioned-package checks. The 2026-10-29 local preparation checkpoint
did not include remote CI or publication; the earlier 1090/1123-case and
real-Kimi evidence keeps its original scope.
No legal hold, directory retention or deletion-certificate capability is
introduced by this version.

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
| Kimi doctor `feedback` object (0.2.6) | additive diagnostic | Existing top-level fields retain PreToolUse scope; optional Stop has separate configuration/local results and always-unverified model delivery. Absent Stop passes; a configured Stop failure exits 1 and prevents baseline creation/live sentinel. Configured Stop fields/helper join drift fingerprints. `check_kimi()` retains its PreToolUse-only contract; Claude output is unchanged. |
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
shift. The bounded 0.2.4, 0.2.5-rc1 and 0.2.7 exceptions
are recorded above. Additive reason codes
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
| Covered deletion/compensation after session, rate, repeated-target-set or quarantine admission limits are reached | Could continue through existing relocation or artifact policy | `BLOCK_DELETE_LIMIT`, `BLOCK_DELETE_RATE` or `BLOCK_DELETE_REPEAT` | Persistent admission suppresses repeated deletion and storage growth (0.2.7 exception, F32) |
| Covered automated request above the batch threshold, or changed/expired Cleanup approval | Could compensate or directly delete under existing policy | `BLOCK_DELETE_BATCH_CONFIRM` | Review a fresh plan and confirm selected paths through Cleanup; approval lifts only this gate (F33) |
| Configured protected paths/configuration directory, malformed deletion policy or unusable admission state | Could follow ordinary artifact/relocation policy without these checks | `BLOCK_DELETE_PROTECTED`, `BLOCK_DELETE_CONFIG_INVALID` or `BLOCK_DELETE_STATE_INVALID` | Protection and usable admission evidence precede covered execution; no silent bypass (F34) |

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
