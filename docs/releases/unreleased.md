# Unreleased changes and maintenance history

<a id="current-unreleased-scope"></a>

## Cleanup review and quarantine handoff

Terminal review lists permanent deletions before quarantine moves, with exact
logical bytes and readable paths. Large selections have a grouped preview and
a complete read-only review file. The terminal displays every row before the
operator enters the existing confirmation token. See the
[Cleanup guide](../guides/cleanup-plan.md#operator-approved-tasks-030-development).

CLI plan, task and GC transaction selectors accept unique ID prefixes.
Multi-target approval guidance provides the literal array for `--targets-file`.
Relocation receipts include their exact GC command, and `gc.py --task ID`
previews retained transactions from a Cleanup task for explicit GC. See
[admission guidance](../guides/deletion-limits.md#dry-run-admission-preview-030-development)
and [quarantine feedback](../guides/recovery-feedback.md#moving-a-file-does-not-remove-its-recovery-copy).

## Delete-guard cross-filesystem recovery

Cross-filesystem moves stage copies on the destination filesystem before
renaming them into place. Relocation records the completed quarantine copy
before clearing the source and returns the transaction ID on a source-cleanup
failure. Transaction queries show `manual_review_items` for failed relocation
items without a matching completion record. Ordinary restore recovers
confirmed copies and reports surviving unconfirmed copies for manual review.
See the
[recovery feedback guide](../guides/recovery-feedback.md).

## Delete-guard workspace limit settings

The operator CLI `agent-guard-delete-policy` shows effective values and
built-in defaults, previews changes, and sets or restores limits for one
workspace. Byte fields accept sizes such as `2GiB`. The Delete-guard Skill
can prepare the preview from a user's request. The operator reviews and
confirms the write in their terminal. Restoring defaults preserves protected
paths, usage and recovery records.
The existing budget reset remains a separate action. See
[operator limit settings](../guides/deletion-limits.md#configure-workspace-limits).

`install_skill.py` also checks and reports `scripts/delete_policy.py` for
`delete-guard`, supports optional `--skill exfil-guard` and
`--skill recovery-audit` links with the same preview/backup rules, and
`skills/exfil-guard/scripts/_bootstrap.py` resolves Core through its real path.
See [Skill installation](../guides/skill-installation.md).

## Release automation maintenance (unversioned)

The next maintenance change renders release-note links against the selected
tag without editing their source, then independently checks the published
GitHub/npm versions, intended channel and archive bytes. Bounded read-only
polling distinguishes unavailable data, read errors and content mismatches;
fixed summaries and attempt evidence survive failed verification.

These repository helpers do not change package version 0.3.0 or publish a new
release. Installation, model/MCP runtime and cryptographic signature audits
retain their separate scope. See the [release workflow](../guides/release-process.md).

Usage guides, Skill instructions and adapter summaries now describe the
released 0.3.0 features consistently. Historical checks keep their original
dates and scope, and existing section links remain available.

## 0.3.0 maintenance workflow

The maintenance workflow connects review to execution: save an explicit
selection, obtain operator approval once, then let the agent
execute and query the returned task ID. This avoids re-entering the same batch
or writing Core calls by hand. Execution still goes through safe_delete;
approval does not lift protection, budget, capacity or rate limits.

These additions belong to 0.3.0 and are absent from 0.2.9. Before installing,
check the exact [GitHub release](https://github.com/mokuyoaxis/agent-guard/releases/tag/0.3.0)
and [npm version](https://www.npmjs.com/package/@mokuyoaxis/agent-guard/v/0.3.0).
See the [0.3.0 notes](release-notes-0.3.0.md). The
[maintenance acceptance table](../reports/test-report-maintenance-0.3.0.md)
records passed checks, retained failures and remaining work.

The operator-enabled Codex form channel has now passed a
[real-model synthetic task](../reports/test-report-maintenance-0.3.0.md#model-host-confirmation-2026-11-15):
one saved 71-root plan, one native confirmation and one execution, with
seven model MCP calls. Independent checks verified the quarantine copies,
restored every payload through MCP, verified both keepers unchanged, and confirmed retained
budget charges. Default approval remains the terminal workflow.

| Area | Change | Guide |
|---|---|---|
| Skill setup | Real-path Core/Cleanup startup and preview-first link installer; updates retain the old Skill | [Installation and upgrade](../guides/skill-installation.md) |
| Preview | Current admission, required approval and supported next steps alongside the existing verdict | [Dry-run semantics](../guides/deletion-limits.md#dry-run-admission-preview-030-development) |
| Explicit targets | Save a literal batch and proposed selection; read by ID without rescanning | [Saved proposals](../guides/cleanup-plan.md#saved-explicit-target-plans-030-development) |
| Approved tasks | Separate operator approval from agent execution; one rate event, per-group debit, durable partial/unknown state | [Task workflow](../guides/cleanup-plan.md#operator-approved-tasks-030-development) |
| MCP | Ten tools by default with terminal approval; an operator-enabled Codex channel adds a default-negative confirmation request and durable lookup | [Tasks](../guides/mcp.md#reviewed-maintenance-tasks-030-development) · [Host confirmation](../guides/mcp.md#host-confirmation-030-development) |
| Results and queries | Shared overview, paginated details, execution summaries and bounded read-only CLI/JSON/MCP status | [Output](../guides/maintenance-output.md) · [Query](../guides/read-only-query.md) |
| Storage | Trusted fixed `--trash-root`, matching doctor and handoff instructions; linked-bucket capacity counts physical contents | [External quarantine](../guides/mcp.md#select-a-fixed-quarantine-directory-030-development) |
| Recovery choice | Require quarantine for regenerable artifacts; retain the choice in saved plans and approved effects | [Explicit quarantine](../guides/cleanup-plan.md#require-quarantine-030-development) |
| Feedback | Distinguish Git tracking from missing discard evidence; explain initial discard declarations and known DSH sandbox-backend failures | [Feedback checks](../reports/test-report-maintenance-0.3.0.md#feedback-refinement-2026-11-13) |

Local acceptance includes a real operator-approved 71-root task: all targets
quarantined, two keeper objects preserved, one safe_delete dispatch and one
rate event. Codex, Kimi and DSH completed ordinary model-backed MCP maintenance
and queried/replayed an already completed task. DSH and Kimi have since first
executed separate new operator-approved 71-root batches; their recovery copies
were independently verified and restored through MCP. DSH also passed full
headless startup on its repaired original session root. Codex later executed
a fresh-approved real cache task with independent root and single-file
recovery. A later new terminal approval also led to a successful Codex
71-root batch: five model MCP calls, one execution, independent verification
of 1006 payload files and MCP recovery. Its earlier host usage-limit block
remains recorded. Claude connected and discovered tools, but model
acceptance is deferred. A later native-handoff trial observed a current-source
Kimi root-Bash block, saved plan, real terminal
approval, original-session execution and independent MCP recovery. An extra
plan save remains recorded. DSH's sandboxed native profile was environment-blocked
on PRoot; a separate zero-model local control passed. Broader native coverage
and historical hook evidence retain their exact limits.

Runtime, installation/update, output/query and package checks have recorded
results. Storage received separate focused regression and real MCP recovery
checks. The capacity correction passed 59 related tests. The earlier
2026-11-11 candidate passed actual npm test: 1237 Python cases and both Node
groups. Its 171-file archive passed isolated installation, 13 bin help checks,
linked Skill/Cleanup, doctor and installed-service MCP recovery. These checkpoints
retain their separate source and documentation scopes; see the
[regression history](../reports/test-report-maintenance-0.3.0.md#regression-and-package-checkpoints).

Keep the complete selected package, workspace, quarantine and session binding
across approval and execution. Existing global Skills and host configuration
are not automatically updated. Selecting a different quarantine performs no
migration or budget/history merge, and existing tasks keep their original
binding. An application-managed session tree may need a dedicated external
bucket and separately authorized handling of any old conflicting bucket.
Older installed copies do not acquire the linked-capacity correction by
moving a directory.

The 2026-11-13 feedback patch passed 132 related Python cases and both Node
groups. One Kimi model trial saved all three confirmed-obsolete targets on
its first MCP call. DSH delivered the explicit backend cause through both
checker and status results, while its model's closing recommendation still
misstated the status hint; that contradiction is retained. This does not
establish general model reliability. Changed uncertain-reason text can change
a reviewed plan fingerprint: older snapshots stay readable, but affected
proposals require a new review/save and any required new operator approval.

No frontend/TUI panel, automatic GC, dependency or recovery-format migration
is introduced. The earlier full runtime and archive/install checkpoints
precede this feedback patch; its targeted checks are recorded separately.
The quarantine-option runtime checkpoint below adds a full-suite result.
The 2026-11-14 runtime at `f6930fa` passed a 171-file development archive
before version alignment: offline installation, all 13 CLI help entrypoints, linked Skill/Core/Cleanup,
doctor and installed-service quarantine/reconnect/restore using owned data.
Its exact-commit development CI passed all 15 jobs; the original failed run
remains recorded. See the [current archive acceptance](../reports/test-report-maintenance-0.3.0.md#development-archive-acceptance-2026-11-14).
Claude model work remains deferred; broader native-hook acceptance and
publication remain unfinished. Commit-level CI is tracked separately from
these local checkpoints. The setup contract and
four-host target are in the [shared prompt](../guides/agent-setup-prompt.md)
and [compatibility notes](../design/compatibility.md).

After alignment, the local 0.3.0 candidate passed 54 package/release/MCP
tests and the actual SDK-coverage preflight. Its 172-file archive, including
the new release note, passed offline installation, CLI/Skill startup and
ten-tool doctor version checks. See the
[candidate checkpoint](../reports/test-report-maintenance-0.3.0.md#local-030-candidate-alignment-2026-11-14).
Follow [development CI](https://github.com/mokuyoaxis/agent-guard/actions/workflows/ci.yml)
for commit-level checks. Tag and publication are separate release steps.

### Explicit quarantine for regenerable artifacts

The 0.3.0 development CLI adds `safe_delete --quarantine` and the same option
when creating Cleanup plans. MCP `safe_delete` and new `cleanup_plan` calls
accept `quarantine: true`; the default set remains ten tools. The optional
[Codex confirmation channel](../guides/mcp.md#host-confirmation-030-development)
adds an eleventh tool. Regenerable artifacts
keep their category while their action becomes quarantine. The choice is
retained in saved proposals and approved effects; execution inherits it by
task ID. Dry-run next-step commands preserve the choice.

Default behavior and old proposals without the option remain unchanged.
Protection, RESTRICTED mode, budgets, capacity, rate and batch approval still
apply. Failed quarantine never falls back to direct deletion or automatic GC.
Application cache locking and transaction-level restore retain their existing
limits. The option is absent from published 0.2.9; see
[workflow and binding](../guides/cleanup-plan.md#require-quarantine-030-development).
On 2026-11-14 the second exact npm test run passed all 1562 Python cases
(1255 public candidate / 307 retained private research) and both Node groups.
The first run's one existing observer-readiness timeout remains recorded;
an isolated diagnostic and the second full run used unchanged source,
assertions and deadlines, without establishing the original timeout's cause.
A real Codex session saved a one-root quarantine proposal through three MCP
calls. An initial approved attempt was refused by the host before Core
dispatch. After testing a process-only permission for cleanup_execute, a new
operator-approved task completed through four Codex MCP calls in 53.697
seconds. The controller verified and restored the five-file cache, then
verified recovery of a separate one-file transaction. Source files and old
transactions were preserved; budget charges remained after restore. This
does not itself certify the separate 71-root model task or native-shell sandbox.
The later [Codex batch acceptance](../reports/test-report-maintenance-0.3.0.md#codex-fresh-approved-batch-2026-11-14)
records the new approval, first batch execution and independent recovery.
See the [scoped verification history](../reports/test-report-maintenance-0.3.0.md#explicit-quarantine-option-2026-11-13-development).

### CLI encoding and portable test fixtures

The Skill CLIs preserve the selected output encoding and escape characters
it cannot represent. This fixes a completed restore returning an error only
because its bilingual summary could not be printed through a legacy Windows
pipe. The recovery action and admission rules are unchanged.

Installation tests compare resolved backup locations, including aliased
parent directories. Shared test repositories disable automatic Git
maintenance before committing their baseline so background writes do not
race full read-only snapshots. The assertions still compare all observed
entries. See the [CI failure and regression record](../reports/test-report-maintenance-0.3.0.md#ci-portability-corrections-2026-11-14).

The [follow-up CI for `f6930fa`](https://github.com/mokuyoaxis/agent-guard/actions/runs/43561931583)
passed all 15 jobs, including the Windows restore/strict-stream regressions
and all five macOS jobs. This validates that development commit, not a
versioned 0.3.0 publication.

## 0.2.9 maintenance history

The 0.2.x sections below retain their original checkpoints. References there
to a paused 0.3.0 plan concern the earlier disclosure-authorization research;
the active maintenance scope and its current evidence are recorded above.

The maintainer selected **0.2.9** for the changes below. Confirm exact-version
availability through the [release notes](release-notes-0.2.9.md) before installation.
Cleanup Copilot and its observation tools remain part of **Delete-guard**.
The separate disclosure-authorization roadmap and commercial work stay paused.

| Area | Change | Entry |
|---|---|---|
| Directory inventory | Bounded overview, explicit-scope discovery and metadata measurement | [CLI workflow](../guides/cleanup-plan.md#directory-overview-and-measurement-unreleased) |
| MCP observations | `cleanup_discover` and `cleanup_measure`; partial data is distinct from a failed call | [Tool contract](../guides/mcp.md#cleanup-observations-unreleased) |
| Planning performance | Reuse ordinary-file metadata and batch Git facts within one plan | [Planner boundaries](../guides/cleanup-plan.md#review-a-plan) |
| Corpus regression | Define the normal text corpus by Git-index paths and current working-tree contents | [Contributor workflow](../../CONTRIBUTING.md) |

The default executable-plan workflow, original five MCP operations, deletion
policy, budgets and recovery formats retain their contracts. Overview and
measurement never grant execution permission. Use doctor and server from the
same installation; published 0.2.8 has five tools and 0.2.9 has seven.
The development task additions above bring this checkout to ten.
See the [latest local validation](#current-validation-checkpoint) before
interpreting historical counts or host evidence.

<a id="cleanup-directory-observations-and-mcp-unreleased"></a>

## Cleanup directory observations and MCP (0.2.9)

Version 0.2.9 adds shared-Core directory discovery and selected-scope
measurement, exposed by `agent-guard-cleanup --discover|--measure` and the
MCP tools `cleanup_discover` / `cleanup_measure`. These additions are absent
from published 0.2.8. They remain under
Delete-guard's Cleanup Copilot, with no new production dependency or storage
migration.

Discovery groups runtime/cache hints, prioritizes cache/artifact path hints
within fixed limits, and records unexpanded areas. Explicit scopes continue
discovery under the same workspace. Measurement omits protected subtrees and
keeps unknown whole sizes distinct from observed bytes. All observations
remain nonselectable and produce no executable plan ID or approval; the
existing default plan and confirmed safe_delete workflow remain separate.

The MCP adapter returns valid partial observations with `ok: true` and
`partial: true`, preserving the CLI exit code and completeness details. The
original five tools retain their contracts. The 0.2.9 doctor expects seven
tools but still invokes only the original three read-only state queries.
Use doctor and server from the same installation.

See [CLI workflow and result fields](../guides/cleanup-plan.md#directory-overview-and-measurement-unreleased)
and [MCP partial-result semantics](../guides/mcp.md#cleanup-observations-unreleased).

<a id="cleanup-planning-performance-unreleased"></a>

## Cleanup planning performance (0.2.9)

Version 0.2.9 reduces repeated ordinary-file path and metadata checks
and reuses batched Git facts when projecting candidate verdicts. Shared
decision rules retain their live execution entrypoint; deleting files still
checks current Git and protection settings. Scan limits, categories, plan
fingerprints, confirmation and deletion budgets keep their contracts.

This performance change is not part of the published 0.2.8 package. On its
own it changes no recognition rules or MCP tools; the separate observation
addition above supplies directory groups and read-only MCP entrypoints.
Neither coordinates application locks. See the [cleanup guide](../guides/cleanup-plan.md)
for planning and managed-cache boundaries. The earlier validation records
below retain their original versions and results.

<a id="repository-corpus-noise-regression-unreleased"></a>

## Repository corpus noise regression (0.2.9)

The repository's normal-corpus test now reads current regular UTF-8 files
named by its own Git index. Tracked modifications and newly staged files are
included; untracked private adversarial experiments are outside this metric.
Git failures, a borrowed parent index and unreadable tracked files are not
silently accepted. Binary files and link targets are outside the text corpus.

This corrects the test's collection boundary without changing Exfil rules,
exemptions or private examples. Regressions check that unsafe tracked and new
staged content still fails, untracked examples still BLOCK when scanned
directly, and staging an example brings it into the corpus gate. New files
must be staged intentionally before this versioned-corpus pre-commit check;
it is not a whole-workspace safety scan. See the
[current validation](#current-validation-checkpoint); earlier failed
checkpoints remain recorded below.

<a id="current-validation-checkpoint"></a>

## 0.2.9 validation checkpoint (historical)

Before the CI corrections below, the versioned 0.2.9 public candidate passed the literal `npm test`: 1197
Python cases and both Node groups, with no failures, errors or skips.
All 16 real MCP protocol/doctor/interruption cases executed with the existing
pinned SDK. The candidate's 227 public source files stayed unchanged during
the run. This is local candidate validation; remote CI and publication are
separate checks, available through the [0.2.9 notes](release-notes-0.2.9.md).

The first cross-version CI then exposed a test-fixture issue on Python 3.9
and 3.10: mocking `os.stat` did not inject the intended metadata error through
those versions' `Path.lstat` implementation. The fixture now injects at
`Path.lstat` and verifies that the fault was reached, retaining the original
error/incompleteness assertions. Further macOS logs exposed a preview fast
path that missed a configured recovery parent through a directory alias.
It now checks both recovery-path spellings before skipping classification;
a new alias regression reproduces the pre-fix failure. The HOME fixture uses
the scanner's physical path, and the read-only fixture disables detached Git
maintenance before its baseline commit. These corrections retain deletion
policy and live execution checks. The full run above keeps its original
checkpoint; subsequent validation uses 1198 public cases and is recorded
separately.

Before the version alignment, local `npm test` passed all 1504 Python cases (1197 in public
test modules and 307 existing private development cases) and both Node
groups, with the existing optional MCP SDK enabled. There were no failures,
errors, skips or automatic retries; the 214 frozen code/configuration inputs
remained unchanged. Eight new collection-boundary controls and direct scans
of the original eleven private samples confirm that the detector still BLOCKs
those samples. Exfil rules and the allowfile were not changed.

These are local working-tree results, not acceptance of the versioned
release or fresh remote CI. The source suite includes private development
cases absent from a public checkout; public-module and private counts are
listed separately. The subsequent Markdown consolidation changed no
production code or tests and did not repeat model trials. Versioned release
checks are recorded separately in the [0.2.9 notes](release-notes-0.2.9.md#validation).

Local checks include actual stdio discovery/measurement against a real
project and one bounded real Kimi Code inventory task in a synthetic
workspace (four model requests, three native MCP calls). The latter completed
scoped discovery and measurement, preserved unknown UV whole size and mixed
data uncertainty, and left fixture files unchanged. This is one host/task
sample, not general reliability, application-cache deletion safety or shell
interception evidence.

The overview remains bounded and may leave an incomplete frontier. Finding
registered roots does not establish a complete workspace scan. The model
trial's source/task boundaries remain separate from native shell interception
and application-cache deletion safety.

### Earlier source checkpoints

At the earlier pre-corpus-fix checkpoint, the full local `npm test` run
completed 1496 Python cases (1189 in public test modules and 307 existing
private development cases), with one corpus
gate failure caused by existing private experiment files. The saved
pre-change view reproduced the same eleven hits; the then-current public-only
view passed that single gate. The two Node commands were then run separately
and passed. That original npm run remains recorded as failed; the public-only
gate check is not a second full-suite PASS. No Exfil exemption, production rule or
private fixture was changed to suppress it. The final 25 directory-group
controls and actual MCP/host checks passed; the earlier targeted run's
runpy-bootstrap fixture failure was corrected with its log retained.

These earlier results retain their failures and scope. They are not the
latest candidate's outcome; the successful follow-up run is recorded above.

<a id="028-development-and-earlier-maintenance-history"></a>

<a id="unreleased-changes-and-026-maintenance-history"></a>
<a id="027-candidate-changes-and-026-maintenance-history"></a>

<a id="mcp-diagnosis-and-reliability-maintenance-028-development"></a>

## MCP diagnosis and reliability maintenance (0.2.8)

The maintainer selected **0.2.8** for an explicit `doctor.py mcp` path,
real-protocol response-loss checks and documentation. Stable installation
examples select this exact version; confirm its GitHub release and npm
availability through the [version notes](release-notes-0.2.8.md). The local
validation and remote CI checkpoints are recorded separately.

The doctor checks a selected Python/optional SDK, service handshake and
version, the five-tool set, workspace binding and read-only queries. It does
not call a model, change host configuration or delete/restore files. Its
version-1 diagnosis and exit codes are separate from the existing
Kimi/Claude doctor and Decision Protocol surfaces. A direct-service PASS keeps
host connection, model delivery and interception `UNVERIFIED`.

The interruption work covers client deadline, cancellation, stdin EOF and
service termination after a real deletion has committed but before a reply.
Checks examine transaction recoverability and persistent budget, without
assuming that losing a response rolled back the operation. This does not
prove arbitrary multi-file interruption atomicity. A separate full rerun passed
1152 Python cases and both Node groups with no failures/errors/skips; local
157-file package checks passed. The earlier full-run initialization timeout
and its unresolved cause remain recorded in the
[0.2.8 notes](release-notes-0.2.8.md#validation-status).

The five tools, default deletion policy, recovery format and budget semantics
retain their contracts. The SDK stays separately optional; the separate
0.3.0 and commercial plans remain paused. See the
[MCP diagnosis and interruption guide](../guides/mcp.md#diagnose-a-local-service-028)
and this release's [bounded compatibility decision](../design/compatibility.md#028-diagnosis-maintenance-decision).

The guide also records later Codex 0.165.0 acceptance of the published 0.2.7
package: three native-client groups and three real-model tasks with 16 MCP
calls. That exact-version cooperative-MCP evidence is not 0.2.8 acceptance or
native-shell interception evidence. Earlier checkpoints below keep their
original versions, results and failure history.

## 0.2.7 changes and 0.2.6 maintenance history

The maintainer selected **0.2.7** for Cleanup Copilot, deletion admission and
optional local MCP tools. Check its exact GitHub release and npm version
through the [version notes](release-notes-0.2.7.md) before installing; a source
version alone does not establish publication. This path is retained for
existing links. At the **2026-10-29 local preparation checkpoint**, versioned
validation passed:
1129 Python cases and both Node groups in 1194.265 seconds, with no failures,
errors, skips, timeouts or automatic retries, plus a 155-file offline package
and installed MCP identity/operation checks. The diagnostic collector kept
`npm test` scope/deadlines without invoking npm. Release SDK-gate fault checks
also passed; that checkpoint did not include final-commit remote CI or
publication. These results are separate from the historical checkpoints below; this preparation
made no new real-model requests. See
[version scope, upgrade effects and validation](release-notes-0.2.7.md).
The separate 0.3.0 plan remains paused.

<a id="local-mcp-tools-unreleased"></a>
<a id="local-mcp-tools-027-candidate"></a>

## Local MCP tools (0.2.7)

The checkout adds an optional local stdio server for explicit safe_delete,
ordinary restore, transaction/status queries and GC-plan preview. Workspace
and stable session identity are set at startup; tools cannot override them,
the environment or quarantine. Operations use the existing CLI without a
shell and retain deletion protection, budget, audit and recovery semantics.
Session selection chooses saved CLI authorization mode as well as budget;
without it, legacy CLI state and the shared default bucket apply. Reusing
state requires matching workspace/quarantine/session. Native host-session
restrictions or in-memory veto require trusted bridging and are not inherited
merely by registering this server.
No force restore, GC execution, budget reset or Cleanup approval is exposed;
safe-delete quarantine targets are also refused. The existing GC CLI remains
separate; preview-only MCP does not add a terminal requirement to that CLI.

The official MIT-licensed `mcp==2.3.0` SDK is an optional separate-environment
dependency, not a new Core/CLI requirement or automatic npm installation.
MCP tools do not replace native hooks or cover shell/script/editor actions
that bypass them. For the 2026-10-28 frozen candidate, the first full local
run finished 1123 Python cases with one observer READY-startup error in
1156.312 seconds; Node did not run. The original error remains unresolved.
The same case passed separate SDK/system-Python checks, and a second complete
run passed all 1123 Python cases and both Node groups in 1143.435 seconds,
without failures, errors, skips, timeouts, runtime changes or automatic retries.
The diagnostic collector preserved `npm test` scope/deadlines; npm itself was
not invoked. Default Core testing without the optional SDK skips seven
protocol cases; the two recorded full runs included it. An independent
SDK CI job is configured; the local preparation checkpoint included no
remote execution of that job.

The 154-file offline install/12 CLI checks and three real Kimi 2.3.0 native-MCP
trials passed: ordinary delete/restore, same-session budget and configured
protection. The model made 16 MCP calls across 19 real configured-route HTTP
200 requests, without Bash, fixed replies or route changes. Its gateway's
server-side model identity was not independently verified. Local evidence
covers ARM64/PRoot Python 3.13.5; there are no new Windows/macOS/shared-storage
or live Claude/Cursor/Codex tests. Their setup examples remain documentation
guidance. See [installation, failure history and limits](../guides/mcp.md).
Published 0.2.6 and its old evidence remain unchanged; 0.3.0 is still paused.

<a id="deletion-admission-hardening-unreleased"></a>
<a id="deletion-admission-hardening-027-candidate"></a>

## Deletion admission hardening (0.2.7)

The current checkout adds quarantine capacity admission, persistent session
byte/entry budgets, short-window rate and repeated-target-set limits, and
shared protected paths in optional `.agent-guard/delete-policy.json`.
Cross-process locking covers measurement, durable admission debit and the
guarded mutation. Failures and restores do not refund session budget; unnamed
CLI processes share a default bucket. Configuration/state failures block new
admission instead of silently disabling limits.

Large batches require Cleanup's existing terminal review, followed by a
ten-minute approval bound to paths, metadata, configuration and session and
consumed once per selected path. Covered automated large-delete checks BLOCK with
Cleanup guidance; no new host approval/resume protocol is added. Approval
does not bypass capacity, budgets or protection. Status adds explicit
terminal-confirmed reset of the current budget only.
Existing compound-command ASK and Git snapshots remain outside the new
budget; normal restore, explicit GC and unprotected quarantine housekeeping
remain exempt. Configuration/control-path protection still applies.

Accounting uses logical `lstat` sizes including directory metadata and
conservative per-entry hardlink counts, not physical disk quotas. Audits,
metadata and unrelated writers can still grow storage. GC remains explicit;
capacity refusal never downgrades to permanent deletion. See
[defaults, reason codes and recovery](../guides/deletion-limits.md).

The 2026-10-28 frozen public runtime passed three independent full runs,
each with 1090 Python cases and both Node groups: 1061.017, 1025.315 and
1042.145 seconds, with no failures, errors, skips, timeouts or automatic retries.
The existing diagnostic collector retained `npm test` scope/deadlines without
invoking npm itself. An offline 149-file package and three real Kimi 2.3.0
root-Bash cases passed; nine configured-route model requests returned HTTP 200.
See the guide for POSIX-only validation, gateway identity and host/tool limits.
The earlier unexplained npm timeout did not recur; its history is retained.
The Cleanup-only results below predate these changes. Those earlier checks
used 0.2.6 package metadata without claiming the additions shipped in that
release. Subsequent versioned 0.2.7 tests and package checks passed as recorded
above; the separate 0.3.0 plan remains paused.

<a id="cleanup-copilot-unreleased"></a>
<a id="cleanup-copilot-027-candidate"></a>

## Cleanup Copilot (0.2.7)

The current checkout adds a metadata-only cleanup plan and an explicitly
confirmed CLI execution workflow. It reports regenerable, caller-declared
discarded, uncertain and protected targets with logical sizes, reasons and
projected deletion/quarantine effects. Execution still uses safe_delete;
ordinary quarantined files retain disk usage. The default plan writes no
workspace or guard metadata. See [usage and limits](../guides/cleanup-plan.md).

This initial addition is separate from published 0.2.6 and is included in
version 0.2.7. At its initial checkpoint it added no native host tool or
deletion-policy change. Subsequent admission hardening is described above. At the Cleanup-only
checkpoint, the runtime passed a continuous local ARM64/PRoot POSIX rerun:
1012 Python cases and both Node groups in 979.997 seconds, with no failures,
errors, skips or retries. This used the existing diagnostic result collector
with the same test scope and deadlines as `npm test`. The earlier literal
npm command's timeout remains recorded and unexplained; it did not recur
in this rerun. Focused cleanup/manifest checks and an offline package also passed,
including terminal confirmation, physical deletion and quarantine/restore.
See the guide's [validation scope](../guides/cleanup-plan.md#validation-scope)
for the local platform and host limits.

## Maintenance delivered in 0.2.6 / 0.2.6 维护说明

Updated 2026-10-22. The additions previously tracked here now ship in
[0.2.6](release-notes-0.2.6.md). This path is retained for existing links and
the preparation history below. The [0.2.5 notes](release-notes-0.2.5.md) retain
that release's scope. Existing installations need an upgrade; optional Stop
also needs explicit configuration and a new Kimi session.

## Availability

| Addition | Availability and setup |
|---|---|
| Kimi recovery feedback | Included in 0.2.6 as `adapters/kimi-code/stop_feedback.py`; requires an explicitly configured Stop hook. Version 0.2.5 lacks this helper. |
| Kimi configuration guidance | The current example recommends PreToolUse 120 seconds and optional Stop 30 seconds; merge top-level skill directories separately from TOML hook tables. Existing installations can adjust their PreToolUse timeout independently of Stop. |
| Kimi Stop doctor checks | Included in 0.2.6; `doctor.py kimi --probe --json` reports optional Stop configuration/local execution in a separate `feedback` object. Version 0.2.5 lacks these checks. |
| Kimi 2.3.0 Bash profile | Added in 0.2.6 after prompt-CLI and ACP root-Bash trials. Version 0.2.5 keeps its earlier list; the separate Kimi Lab helper stays pinned to 2.1.1. |
| DSH Lab preflight diagnostics | Included in 0.2.6; 0.2.5 retains the older generic error messages. This concerns the Lab controller's version/config checks, not the production deletion policy. |
| DSH alpha.4 default deletion adapter | Added in 0.2.6. Exact loader declarations include `0.2.1-alpha.4`; optional native read and Lab keep their two-version scope. Version 0.2.5 retains its earlier declaration. |

Use the [installation guide](../guides/getting-started.md) to keep the chosen
checkout or local-package path consistent. Start a new Kimi session after
changing hooks. The setup remains opt-in; this page does not change user settings.

## Kimi recovery feedback

The sampled Kimi 0.42.0, 2.1.1 and 2.3.0 success-hook paths omit the recovery
explanation from the next model request. The optional POSIX Stop hook asks
for one additional response after new compensations. It supplies actual
transaction IDs, state/restore commands, retained-storage guidance and full
GC commands. Restoring files moves them back without freeing total disk space.
The hook itself performs no restore or purge.

The initial answer appears first and may be inaccurate. Stop cannot repair
the earlier tool result; the model may still omit details. Emission cursors
prevent repeated notices in the tested scope but do not prove model receipt.
Shared-session actors, cancellation and exhausted budgets retain their limits.
The local doctor checks described below do not prove optional Stop delivery.

See [setup and delivery limits](../../adapters/kimi-code/README.md#optional-recovery-feedback-unreleased-checkout-addition)
and the [bounded host report](../reports/test-report-kimi-code-block.md).
Missing/timed-out hooks still allow tools in the sampled hosts. The 120-second
outer timeout gives the internal 90-second check margin; it does not change
that host behavior.

## Kimi Stop doctor checks

The existing top-level results retain PreToolUse scope. A separate `feedback`
object distinguishes absent Stop (`NOT_CONFIGURED`), invalid configuration
(`FAIL`), unreadable configuration (`UNVERIFIED`), and requested local probe
success/failure. An absent optional Stop does not fail the CLI; a configured
Stop that fails configuration or a requested probe exits 1.

The probe checks synthetic recovery guidance, empty/active-Stop silence and
repeat suppression in its own temporary home/workspace/trash. Existing audit
records and cursors are left alone; no model, deletion, restore or GC runs.
PreToolUse probes also isolate inherited `AGENT_GUARD_*` recovery locations.
Executable/file presence is checked separately from successful execution.
`feedback.model_delivery` remains `UNVERIFIED`, even after a Bash live sentinel.

Configured Stop fields and helper bytes join drift fingerprints. A Stop
failure prevents writing a passing baseline or proceeding to the live sentinel.
The importable `check_kimi()` wrapper remains PreToolUse-only; Claude behavior,
Core policy and rm wildcard rules are unchanged. See the
[doctor fields and limits](../../adapters/kimi-code/README.md#stop-doctor-checks-unreleased).

## DSH Lab preflight diagnostics

Version/config failures distinguish `START_FAILED`, `TIMEOUT`, `OUTPUT_LIMIT`,
`NONZERO_EXIT`, `EMPTY_STDOUT` and `UNSUPPORTED_VERSION`. Errors include child
exit code, capture flags, elapsed/deadline seconds and captured byte counts.
Configuration text, stderr text, command arguments and environment values are
omitted. Version/config budgets remain 10/15 seconds, with no automatic retry.
CLI failure still exits 1. See the [Lab runner guide](../../adapters/dsh/harness/README.md).

## Validation and remaining uncertainty

The [0.2.6 release notes](release-notes-0.2.6.md#validation-and-limits) record
the implementation checkpoint's 13/13 CI result, including ten complete
Python matrix jobs and both Node groups. The dated local checkpoints below
remain historical records and do not replace that final implementation check.

The later [DSH compatibility work](../design/dsh-compatibility.md) adds alpha.4
to default deletion support. Real CLI Profile/Boot checks covered all three
admitted hosts, with scripted root/child-agent, policy-forwarding and approval
checks on alpha.4. The [report](../reports/test-report-dsh-alpha4.md) records
that separate checkpoint; the historical full run below predates the new
manifest/forwarding assertions and does not validate those additions.

Before the 0.2.6 version/document alignment, the implementation passed a
complete frozen run on 2026-10-21: **974 Python tests and both Node groups**,
no failures, errors or skips, in 1270.573 seconds with the original
1800-second deadline and no retry. It used an internal unittest result
collector for immediate failure/event logs, with the same discovery scope
and sequential Node stages as `npm test`. The 195 public files stayed frozen.
That preparation aligned only version metadata, the exact-version assertion
and documentation; it does not reclassify the older failures below as passes.

The earlier DSH diagnostics checkpoint passed one complete `npm test` on 2026-10-21:
**962 Python tests and both Node groups**, on ARM64/PRoot POSIX. A 138-file
local package was installed offline at a path containing spaces; its CLI
reported the controlled child exit code correctly without copying payloads.
Those counts describe that earlier checkpoint, before the Stop doctor
additions. A later 139-file offline package checked the doctor candidate;
neither older artifact is a versioned 0.2.6 release.

The earlier complete-test attempts included a timeout and one DSH config
preflight error. Its original result details were not retained. Five bounded
repetitions of the failing case and the new complete run passed, but the
historical cause remains unknown. Controlled faults verified the new
diagnostics; they are separate from reproducing that historical failure.

Later Kimi compatibility work adds exact `2.3.0` to the Bash profile after
sixteen actual-host fixed-loopback trials: eight prompt CLI and eight ACP,
including allow, BLOCK, ASK refusal, runtime failure, absence controls,
quarantine/restore and Stop feedback. ACP hooks ran in the session directory
despite a different server startup cwd. This postdates the historical full
run above; it changes the version declaration, not Core or the bridge.
The [2.3.0 report](../reports/test-report-kimi-code-block.md#kimi-230-acceptance)
keeps root Bash, client-terminal, timeout, model and subagent limits explicit.
Updating the profile can make an old baseline `DRIFTED`; no baseline is
automatically rewritten. The Lab helper remains pinned to 2.1.1.

Earlier Kimi fixed loopback trials exercised the actual 0.42.0/2.1.1 CLIs.
A bounded 2.1.1 real-model session received the recorded transaction and full
commands; restoration and repeat-Stop checks passed. This does not establish
all-model, subagent, background or long-running behavior. The new DSH
diagnostic checks made no external model requests.

<a id="中文摘要"></a>

## 0.2.6 中文摘要

以下为 0.2.6 历史维护摘要：Kimi 可选 Stop 恢复反馈、120 秒外层超时接入说明，以及
DSH Lab 版本／配置预检的原因、退出码和计时诊断，及 Kimi Stop 的独立 doctor 检查。
后续默认删除适配加入 DSH `0.2.1-alpha.4`，读取脱敏／Lab 不随之扩大。
Kimi Bash profile 新增精确版本 2.3.0；CLI／ACP 各八项固定响应试次提供有限根代理证据，
未调用外部模型，也未扩大 Kimi Lab 的 2.1.1 准入或子代理声明。
Stop 未配置不报错；配置或本地探针失败退出 1，模型送达始终未验证。已发布 0.2.5 的范围保持原记录；
这些功能自 0.2.6 起提供，Stop 另需显式配置；原页面路径为已有链接保留。

版本／文档对齐前，最终实现检查点的 974 项 Python 与两组 Node 已在同一冻结流程通过；
此前 962 项检查点和历史失败分别保留，原失败原因仍未确定。
上述 0.2.6 维护未改变删除策略、rm 通配符规则、恢复格式或手动 GC。
