# Local MCP tools / 本地 MCP 工具

This adapter is introduced in **0.2.7** and is absent from 0.2.6. The separate
disclosure-authorization research remains paused. The historical
protocol/package records and three real Kimi Code trials below belong to the earlier
2026-10-28 source checkpoint with 0.2.6 package metadata, including a failed
first full run. They do not certify the new versioned artifact. See the
[0.2.7 notes](../releases/release-notes-0.2.7.md) for versioned validation and
exact-version availability checks.
Configuration examples are not host acceptance.

Version **0.2.8** adds [local MCP diagnosis](#diagnose-a-local-service-028)
and protocol-interruption validation; see the
[0.2.8 scope and validation status](../releases/release-notes-0.2.8.md).
The later [Codex 0.165.0 acceptance](#codex-01650-with-published-027) below used
the published 0.2.7 package and is separate from both development checkpoints.

Version **0.2.9** adds `cleanup_discover` and `cleanup_measure`.
Version 0.2.8 retains the original five tools. The new read-only workflow
and its separate partial-result contract are described
[below](#cleanup-observations-029).

The **0.3.0 development checkout** adds reviewed maintenance tasks through
`cleanup_plan`, `cleanup_execute` and `cleanup_task_status`, for ten tools in
total. These additions are absent from published 0.2.9. Runtime and host
acceptance are recorded separately; tool discovery alone does not prove a
model completed an approved task or a native hook intercepted an operation.
See [the task workflow](#reviewed-maintenance-tasks-030-development).
The [maintenance acceptance](../reports/test-report-maintenance-0.3.0.md)
records local approved execution, three hosts' ordinary model-backed tasks,
DSH/Kimi fresh-approved execution, Claude's connected/deferred status and
Codex's earlier usage-limit block before business calls, and the separate
2026-11-14 single-root real-cache execution with controller recovery.
The same development scope also adds `status(view=overview)` for
[bounded metadata queries](read-only-query.md), keeping the ten-tool set and
legacy default status result. CLI/JSON/MCP queries are the current interface;
frontend and operation panels are deferred.

MCP gives a supporting coding-agent client an explicit way to use the existing
safe deletion and recovery operations. It does **not** intercept shell `rm`,
scripts, editor deletion or tools that bypass this server. Keep a compatible
blocking hook for the paths where automatic interception is required.

## Install the optional adapter

The Core/CLI still needs Python 3.9+ and Git without third-party Python
packages. This adapter separately uses the official MIT-licensed Python
`mcp==2.3.0` SDK. Its release metadata requires Python 3.10+; use a compatible
interpreter in a dedicated virtual environment. The SDK and its dependencies
are optional and are not installed by the existing Core/CLI or by npm.

From a checkout or package containing `adapters/mcp/`:

```sh
python3 -m venv /absolute/path/to/agent-guard-mcp-venv
/absolute/path/to/agent-guard-mcp-venv/bin/python -m pip install -r /absolute/path/to/agent-guard/adapters/mcp/requirements.txt
```

If the existing Python lacks `ensurepip`, and an existing pip supports
`--python` (pip 22.3+), it can provision the separate environment instead:

```sh
python3 -m venv --without-pip /absolute/path/to/agent-guard-mcp-venv
python3 -m pip --python /absolute/path/to/agent-guard-mcp-venv/bin/python install -r /absolute/path/to/agent-guard/adapters/mcp/requirements.txt
```

The adapter accepts the pinned SDK 2.3.0; other SDK versions are rejected
until separately validated. Transitive dependency versions are resolved by
pip; this SDK pin is not a complete cross-platform dependency lock.

Use a user-owned path on the Linux filesystem. These POSIX examples do not
establish Windows, macOS or Android shared-storage compatibility. The SDK
wheel is platform-independent, but some transitive dependencies contain
native code; the selected environment must successfully install them.
Do not replace a working global Python environment to enable this adapter.

For a persistent client entry, keep this environment at a stable location
outside experiment artifacts or temporary package prefixes. Check the
selected package's SDK pin before installation and obtain any required
dependency-installation authorization. Run `doctor mcp` with the intended
interpreter and storage binding before registering it in a client.

The local launch command is:

```sh
/absolute/path/to/agent-guard-mcp-venv/bin/python -B /absolute/path/to/agent-guard/adapters/mcp/server.py --workspace /absolute/path/to/your-project
```

The client starts this stdio process. No network listener, cloud account or
system service is required. Use absolute paths and bind one workspace at
startup; a tool call cannot change it or select another quarantine. Protocol
messages use stdout; diagnostics use stderr.
Storage defaults to the workspace-local `.agent-trash/`. The **0.3.0
development checkout** also accepts a trusted startup `--trash-root` for a
fixed quarantine directory; published 0.2.9 does not have this option. Ambient
quarantine and deletion-policy environment overrides are still ignored.

### Select a fixed quarantine directory (0.3.0 development)

For a workspace whose owning application expects a strict directory layout,
select a dedicated stable bucket outside that application-managed tree:

```sh
/absolute/path/to/agent-guard-mcp-venv/bin/python -B \
  /absolute/path/to/agent-guard/adapters/mcp/server.py \
  --workspace /absolute/path/to/workspace \
  --trash-root /absolute/path/to/guard-storage/workspace-a
```

Only the operator's startup configuration chooses this path. Tool arguments
cannot change it. Explicit paths must be absolute after home expansion;
directory links are resolved once at startup. The directory may be absent,
but it cannot be a file, the workspace itself or an ancestor containing the
workspace. Startup and read-only queries do not create it. Writes occur when
an operation audits, approves or executes through the existing Core.

Use one stable bucket per workspace and retain it across restarts. Selecting
another bucket selects that bucket's recovery records, authorization state
and budget ledger; it does not carry over or merge the old ledger. Existing
saved plans/tasks remain bound to their original location. This option
performs no migration. The retained default `.agent-trash/` and its aliases
remain protected from MCP safe_delete even when another bucket is selected.

This matters when cleaning a DSH session-storage root: the installed backend
examined during development rejected quarantine JSONL audit/manifest files
as old flat session logs. Even an audited dry-run can create those files.
Keeping new audit data outside the session tree avoids that collision; an
already existing conflicting bucket still needs a separate reviewed
migration. Changing this flag does not fix its presence. Ordinary project
workspaces outside that session tree do not have this particular conflict.

Copy the returned plan/task commands to retain the selected location. Custom
storage instructions use POSIX `env` argv with `AGENT_GUARD_WORKSPACE` and
`AGENT_GUARD_TRASH`; keep the trusted session setting as described below.
Native Windows command handoff for this option is not verified.

Development verification also exercised an authorized existing-bucket move
to external storage, retaining an old-path link. The installed DSH directory
checker then passed. A later full headless DSH read-only task and new approved
MCP batch also passed using that original persistence root.
This is a version-specific compatibility observation, not an automatic
migration or a universal recommendation for application storage trees.
Current Core measures a linked bucket's physical contents for capacity;
older copies need the correction or an explicit physical storage path.
See [storage acceptance](../reports/test-report-maintenance-0.3.0.md#local-workflow-and-storage).

An optional trusted startup `--session stable-name` selects both the existing
CLI authorization mode and persistent deletion-budget identity. Without it,
the adapter uses the legacy CLI state and stable shared default budget bucket.
To share saved CLI state, the workspace, quarantine location and session
selection must match. Do not generate a new identity for each call or
reconnect: it can select different authorization state as well as a different
budget. Session identity is trusted configuration, not a model-supplied tool
argument or an independent authentication boundary.

This server does not automatically inherit the calling host's native-session
restrictions or in-memory veto. A host that keeps authoritative restrictions
in memory needs a trusted bridge to enforce them for MCP calls; registering
this server alone does not provide that bridge. Existing
[limits](deletion-limits.md) and the CLI's mode semantics still apply.

<a id="diagnose-a-local-service-028-development"></a>

## Diagnose a local service (0.2.8)

From a 0.2.8 or later installation or checkout, run:

```sh
python3 -B /absolute/path/to/agent-guard/doctor.py mcp \
  --python /absolute/path/to/agent-guard-mcp-venv/bin/python \
  --workspace /absolute/path/to/your-project --json
```

The installed CLI entry is `agent-guard-doctor mcp` with the same options.
`--python` defaults to the interpreter running the doctor; select the separate
SDK environment explicitly when the Core interpreter has no MCP SDK.
`--workspace` is required and must already exist. `--server` optionally selects
another reviewed `server.py`; otherwise the doctor uses its own package's
adapter. The doctor starts this script with the selected Python, so both are
trusted executable inputs. It uses no shell and does not discover or rewrite
client configuration. `--timeout` bounds the diagnostic checks in seconds
(default 30; accepted range 5–120). Closing the service process can add up to
three seconds of process waits and one second of reader-thread waiting beyond that deadline.

For a development service using custom storage, pass the same `--trash-root`
to the doctor. It validates that binding and still calls only the original
three read-only tools; it neither creates nor migrates the selected bucket:

```sh
python3 -B /absolute/path/to/agent-guard/doctor.py mcp \
  --python /absolute/path/to/agent-guard-mcp-venv/bin/python \
  --workspace /absolute/path/to/workspace \
  --trash-root /absolute/path/to/guard-storage/workspace-a --json
```

The checks cover:

1. The selected interpreter, pinned SDK metadata and actual SDK imports.
2. A classic `2025-06-18` stdio handshake, the `agent-guard` service identity
   and a service version matching the doctor's package version.
3. The expected tools and query annotations for this installation: five in
   0.2.8, seven in published 0.2.9, ten in the 0.3.0 development checkout.
   The plan tool is annotated as writable because it can explicitly save;
   task status is read-only. Use doctor and server
   from the same installation; matching package version text alone does not
   establish an identical tool set.
4. Successful `status`, `transactions` and `gc_plan` calls, including the
   requested workspace and the selected quarantine binding reported by `status`.

No model, deletion, restore, GC execution or dependency installation is part
of this diagnosis. It does not create host configuration or approve a budget
reset. A normal service leaves workspace file names and content unchanged
under these read-only calls; this is not a system-call-level zero-write proof
for an arbitrary selected script.

The independent JSON report has `schema_version: 1`, `harness: "mcp"`,
`status`, `stage`, `code`, `message`, `guidance` and `checks`. It contains
fixed diagnostic findings rather than raw service stderr, transaction data,
audit text, authentication or configuration contents. Exit code `0` means
the checks passed, `1` means a diagnosis failed, and `2` is a CLI usage error.
These diagnostic codes are separate from the Decision Protocol reason codes.
`diagnostic_stderr_bytes` reports only the captured stderr byte count; raw
stderr is not returned. After a successful status query, `deletion_admission`
is `BLOCKED` or `READABLE`. `READABLE` records that admission information was
inspected; it is not permission for a future deletion.

Use the returned stage and guidance to fix a missing interpreter/SDK, a
startup or protocol failure, a version mismatch, an incorrect workspace
binding or a query failure. A successful `status` can still report blocked
deletion admission; the text renderer highlights `BLOCKED`, and diagnosis does
not authorize deleting through that block.

`host_connection`, `model_delivery` and `host_interception` stay `UNVERIFIED`.
A PASS proves the selected service command answered these direct checks.
Inspect the actual client's connection and use a bounded host acceptance
trial to establish that a particular host and model used it. The doctor does
not extend Kimi/Claude hook diagnosis or automatically prove shell protection.

## After a lost response or interrupted connection

A client deadline, cancellation or process exit can occur after a mutation
has committed but before its response arrives. It does not roll the files or
budget back. Stop automatic mutation retries, reconnect to the same reviewed
workspace/quarantine/session identity, then inspect `status` and `transactions`.
Check the source and recovery payload before restoring the actual returned or
listed transaction. Ordinary restoration still refuses an existing occupant
and does not refund the deletion budget.

If evidence is incomplete or an operation may still be running, preserve it
and resolve the process/transaction state before another mutation. An absent
response or one empty query is not proof that nothing changed. Changing
session identity, removing admission state or manufacturing confirmation is
not recovery.

The 0.2.8 interruption checks target four response-loss windows: a client
deadline, a cancellation notification, stdin EOF and service termination.
Their controlled barrier is after real safe_delete has committed and before
the response is sent. They validate recoverability and persistent accounting
at that boundary, not arbitrary interruption within every multi-file operation
or atomic rollback. Final candidate results are recorded in the
[0.2.8 validation status](../releases/release-notes-0.2.8.md#validation-status).

## Tools and limits

| Tool | Inputs | Effect |
|---|---|---|
| `safe_delete` | `paths` (1–1000 literal strings), optional `dry_run` (default false); 0.3.0 adds `quarantine` (default false) | Existing policy and admission; `quarantine: true` retains recovery copies even for proven regenerable artifacts |
| `restore` | `txid` | Restore a transaction without force-overwriting an occupant |
| `transactions` | none | List the selected workspace's recovery transactions |
| `status` | optional `tail` (0–100); 0.3.0 adds `view: overview/full` and overview-only `limit` (1–50) | Legacy full by default; explicit overview reads bounded task/transaction metadata and budget without payload size walks |
| `gc_plan` | none | Preview the existing transaction GC plan; never execute it |
| `cleanup_discover` (0.2.9) | optional literal directory `scopes`, `max_entries` (1–10000), `max_depth` (1–4) | Bounded read-only overview; no executable plan |
| `cleanup_measure` (0.2.9) | required directory `scopes`, optional `max_entries` (1–20000), `max_depth` (1–32) | Read-only metadata measurement; protected whole sizes stay unknown |
| `cleanup_plan` (0.3.0 dev) | literal `targets` with declarations/limits, `save` and optional `quarantine`, or `saved_plan_id`; optional output view/detail controls | Default overview; explicit saving stores metadata and requested effects; saved reads do not refresh sources |
| `cleanup_execute` (0.3.0 dev) | operator-approved `task_id`, optional `view: full` | Default receipt summary; never sign an approval or resume a claimed task |
| `cleanup_task_status` (0.3.0 dev) | `task_id`, optional view/detail controls | Default durable summary; no inventory, budget debit or retry |

For recoverable cleanup in the **0.3.0 development checkout**, use
`safe_delete` with `quarantine: true`, or create a proposal such as:

```json
{"name":"cleanup_plan","arguments":{"targets":[".pytest_cache"],"quarantine":true,"save":true}}
```

This retains the regenerable classification while changing its action to
quarantine. Unknown content still needs discard evidence; protection and all
admission checks remain. The saved choice is bound into the plan/approval;
follow its operator command and execute by task ID. Saved-ID queries and
`cleanup_execute` reject a quarantine override, even false. A failed quarantine
never falls back to direct deletion or automatic GC. The option does not
coordinate application cache locks or free disk space by itself. It is absent
from published 0.2.9; use matching updated CLI/server processes and reconnect
existing clients after updating. See [quarantine-only plans](cleanup-plan.md#require-quarantine-030-development).

In the **0.3.0 development checkout**, `safe_delete` with `dry_run: true`
also returns `result.admission`, including current admission/approval status
and supported next steps. This preserves the existing safe_delete arguments;
the three task tools above are separate additions. `ok: true` still means
the preview completed, even when admission is
`REQUIRES_APPROVAL` or `BLOCKED`; policy BLOCK keeps exit 2 and `ok: false`.
This snapshot consumes no budget/grant and reserves nothing. It is absent
from published 0.2.9 and has not yet received the planned runtime regression
checks. See [fields, effects and exit codes](deletion-limits.md#dry-run-admission-preview-030-development).

Paths are literal names, relative to the fixed workspace or absolute; the
Core still refuses outside targets. Glob characters name actual files rather
than selecting a pattern; the existing Core may still refuse such names as
wildcards or indeterminable effects. Literal transport does not relax those
rules. No shell parses these arguments. Unknown tool
arguments and incorrect types are refused, including boolean values for
integer fields.
Paths that normalize to a name ending in whitespace are refused, because
the underlying classifier trims that spelling. Existing Core restrictions
on glob/variable-like filenames still apply; literal passing prevents
selecting neighboring names but does not relax those refusals.

Results carry `tool`, `ok`, `exit_code` and the existing CLI JSON in `result`;
MCP errors are marked as errors as well. Adapter failures use an `error`
object with `code`, `message`, `may_have_changed` and inspection-tool guidance.
Raw non-JSON subprocess stderr is not returned as tool content.

Ordinary valuable deletion retains a recovery copy and disk usage; only
provably regenerable artifacts follow the existing physical-delete rules.
Dry run is advisory: it preserves source targets and does not debit deletion
budget, but may initialize normal guard/audit metadata. It is not a zero-write
scan or an execution grant.
Tool responses can include workspace paths and recovery metadata; review them
before sharing logs. MCP does not add file-content disclosure sanitization.

The tool schema supplies no workspace/session/environment override, arbitrary
shell command, force restore, Cleanup approval, budget reset or GC execute
argument. The MCP safe-delete entry also refuses quarantine targets, so it
cannot be used as an alternate permanent-GC interface. `gc_plan` is a preview
restriction of this adapter, not a new claim that the existing GC CLI requires
a terminal or human confirmation.

The direct safe_delete tool still refuses unapproved large batches. In this
development checkout use the [saved-plan task workflow](#reviewed-maintenance-tasks-030-development);
older versions use the [reviewed terminal workflow](cleanup-plan.md#confirm-and-execute).
A model-provided `confirm=true`, client tool annotation or
permissive auto-approval setting does not replace that workflow. Restore
conflicts require operator review. Changing policy, force restoration,
executing GC or resetting a budget remains outside this server's tool set.

A tool call can fail after a partial mutation. Inspect its structured result,
reported transaction IDs and `transactions`/`status`; do not assume failure or
cancellation means nothing moved. Do not automatically retry a mutating call
after a client timeout or disconnection. The existing recovery, concurrency
and same-UID boundaries remain; this server is not an OS sandbox.

<a id="cleanup-observations-unreleased"></a>

## Cleanup observations (0.2.9)

First call `cleanup_discover` with `{}`. Its compact `overview` accompanies
the complete observed `groups`, `review_branches` and `coverage.unexpanded`
lists. Choose exact directory scopes for a follow-up discovery or measurement:

```json
{"name":"cleanup_discover","arguments":{"scopes":[".work"]}}
```

```json
{"name":"cleanup_measure","arguments":{"scopes":["build",".pytest_cache"]}}
```

Scopes accept at most 64 strings, stay below the same workspace, and cannot
overlap, cross protection or contain directory links. Omitting discovery
scopes starts at the root; measurement requires a nonempty selection. No
workspace, environment, approval, plan ID or execution override is accepted.
The limits are shared across all scopes in one call. A follow-up scope starts
a fresh observation rather than completing or authorizing an old one.

Both tools are read-only and preserve source files, audit and recovery state.
They do not call advisory safe_delete, which can write normal guard metadata.
Their results use the independent observation schemas described in the
[Cleanup guide](cleanup-plan.md#directory-overview-and-measurement-unreleased).
Runtime and managed-cache labels are hints; all groups remain nonselectable.
Do not treat a Core policy projection or successful inspection as approval.

A valid partial observation returns `ok: true`, `partial: true` and is not an
MCP `isError`. The original CLI exit code remains present, including exit 1
for a limited frontier or incomplete observed subset. `partial` also remains
true when measurement exit 0 has a protected unknown whole size. Inspect the
underlying coverage, issues, protected roots and completeness fields; `ok`
means the tool returned usable observations, not that the workspace is fully
scanned or a target may be deleted. Invalid options, unavailable subprocesses
and protocol failures remain errors. These semantics do not change the
original five tools' error or mutation contracts.

The doctor verifies the two new tool annotations but continues to invoke
only `status`, `transactions` and `gc_plan`; diagnosis does not silently start
directory discovery or measurement.

## Configure a client

The following examples register a local server; they do not run deletion or
change existing hooks. Merge the entry into the appropriate configuration
instead of overwriting other servers. Confirm trust and tool permissions in
the actual client, then start a fresh connection and inspect discovered tools.

### Claude Code

From the intended project, register a project-scoped stdio server:

```sh
claude mcp add --transport stdio --scope project agent-guard -- /absolute/path/to/agent-guard-mcp-venv/bin/python -B /absolute/path/to/agent-guard/adapters/mcp/server.py --workspace /absolute/path/to/your-project
```

The command changes project MCP configuration; review it before use. Complete
Claude's normal trust review, then inspect `/mcp`. This setup is an explicit
MCP tool path and does not extend the separate Claude hook adapter's tested
versions. See the [official Claude Code MCP guide](https://code.claude.com/docs/en/mcp).

### Cursor

Merge into `.cursor/mcp.json` in the intended project:

```json
{
  "mcpServers": {
    "agent-guard": {
      "type": "stdio",
      "command": "/absolute/path/to/agent-guard-mcp-venv/bin/python",
      "args": [
        "-B",
        "/absolute/path/to/agent-guard/adapters/mcp/server.py",
        "--workspace",
        "/absolute/path/to/your-project"
      ]
    }
  }
}
```

Enable and inspect the server in the installed client's MCP settings. This
local configuration does not make your filesystem available to a remote
cloud agent. See the [official Cursor MCP guide](https://cursor.com/docs/mcp).

### Codex

Register the same command through the CLI:

```sh
codex mcp add agent-guard -- /absolute/path/to/agent-guard-mcp-venv/bin/python -B /absolute/path/to/agent-guard/adapters/mcp/server.py --workspace /absolute/path/to/your-project
```

`codex mcp add` writes Codex configuration. For explicit project scoping in a
trusted project, merge this entry into `.codex/config.toml` instead:

```toml
[mcp_servers.agent-guard]
command = "/absolute/path/to/agent-guard-mcp-venv/bin/python"
args = ["-B", "/absolute/path/to/agent-guard/adapters/mcp/server.py", "--workspace", "/absolute/path/to/your-project"]
cwd = "/absolute/path/to/your-project"
startup_timeout_sec = 30
tool_timeout_sec = 360
required = true
```

These are explicit client deadlines, not new Core limits. The development
adapter allows 120 seconds for an ordinary CLI call and 300 seconds for an
approved task; Codex's default 60-second tool deadline can expire earlier.
The 360-second example leaves a margin for the existing task deadline.
`required = true` makes this project's Codex startup fail if the service
cannot connect. Choose it when Guard availability is required; it grants
neither deletion permission nor operator approval. A timeout or lost reply
still requires querying the task/transaction before another mutation.

Bind each entry to its intended workspace, existing quarantine and budget
identity. Omitting `--session` retains the shared CLI default; do not add a
new identity during setup or reconnection. A local configuration containing
machine-specific paths should stay out of public commits. Project-scoped
configuration is loaded only for trusted projects; do not broaden the global
project trust policy to make an unreviewed entry load.

Use `codex mcp list` and `/mcp` in a fresh session to inspect configuration and
connection state. Configuration being present does not prove a model actually
called the tool or that all filesystem actions are covered. See the
[official OpenAI MCP documentation](https://learn.chatgpt.com/docs/extend/mcp?site_variant=chatgpt).

These source-based examples were checked against official documentation on
2026-10-28. Claude Code and Cursor remain configuration examples; the later
Codex acceptance below covers one exact version and selected MCP scenarios.
Client schemas, approvals and connection behavior can change; verify the
installed version rather than copying another client's configuration shape.

The project-scoped entry was also exercised on 2026-11-13 with Codex 0.165.0,
a stable SDK 2.3.0 environment and this ten-tool development checkout. A fresh
session used the actual project configuration for three read-only MCP calls,
without shell calls or MCP launch overrides. See the
[registration acceptance](../reports/test-report-maintenance-0.3.0.md#project-scoped-codex-mcp-registration-2026-11-13)
for the separate protocol-client recovery check and remaining host limits.

## Reviewed maintenance tasks (0.3.0 development)

Guard's operator approval and the MCP client's permission to call a writing
tool are separate gates. A valid task does not override the client's policy.
In a recorded Codex trial, `approval_policy="never"` rejected
`cleanup_execute` before it reached Core, despite a current terminal-approved
task. The model conversation still exited successfully; that was not a
successful maintenance receipt. Verify the client's approval path before
requesting a time-limited business grant, and inspect the actual task after a
refusal. Read-only connection/plan tests do not establish mutation permission.
See [OpenAI's approval behavior](https://learn.chatgpt.com/docs/sandboxing/auto-review)
and [client tool settings](https://learn.chatgpt.com/docs/extend/mcp).
Preserve truthful tool annotations and existing safety boundaries when
arranging client approval; never label a writer as read-only to avoid a gate.

For an operator-authorized unattended run, Codex 0.165.0 can grant permission
to one MCP tool in that process. After verifying the same behavior with an
owned no-op tool, start the execution process with these CLI overrides:

```sh
codex exec --sandbox read-only \
  -c 'approval_policy="never"' \
  -c 'features.shell_tool=false' \
  -c 'mcp_servers.agent-guard.enabled_tools=["status","cleanup_task_status","cleanup_execute"]' \
  -c 'mcp_servers.agent-guard.tools.cleanup_execute.approval_mode="approve"' \
  'Inspect the approved task, execute its task_id once, then query its receipts.'
```

Use the registered server's actual name and the same project directory. These
arguments do not write configuration files. They permit this client's tool
call; they do not create Core approval, extend its expiry, or lift protection
and limits. The setting applies to the tool, not to a single task ID; Core
still validates each task. Other tools retain their client policies. Arrange
any broader access separately with the operator. The read-only sandbox
selection alone does not certify effective OS isolation on PRoot.

The bounded host probe succeeded for the permitted synthetic tool while an
unpermitted sibling remained blocked, with destructive annotations retained.
That probe establishes the client setting's behavior in the tested version.
The real cleanup was verified separately below. See the [permission record](../reports/test-report-maintenance-0.3.0.md#codex-process-tool-permission-2026-11-14).

A later fresh-approved real cache task also passed with this process setup:
four Codex MCP calls and one execution, followed by independent controller
verification and recovery of the root and a separate one-file transaction.
All five original files were restored; no permanent GC ran. See the
[real-project acceptance](../reports/test-report-maintenance-0.3.0.md#codex-real-project-quarantine-and-restore-2026-11-14)
for the scope, accounting and remaining large-batch/native-sandbox limits.

The three task tools reuse the same package's Cleanup CLI and Core. They
cannot change workspace, quarantine, session, environment, target selection
on execution, or supply `approved`, `confirm` or a grant token. Approval is
still an operator-terminal action; MCP does not create a new approval bridge.
No additional SDK, network service, configuration or recovery migration is
needed. Update/reconnect the selected server and use its own doctor and CLI;
package version text alone does not identify this unreleased implementation.

These tools now default to `view: summary`. Plan/status queries can request
`view: details` with category/state, literal relative path and pagination;
`view: full` returns full public JSON. Execute supports summary/full only.
Read filters change display, never selection or execution. The shared
[output contract](maintenance-output.md) defines units, unknowns and pages;
MCP text and structured content both carry the selected view. Local protocol
and view regressions have passed, with bounded model-host observations in
the [acceptance report](../reports/test-report-maintenance-0.3.0.md).
The tool count and read-only annotations stay unchanged.

`cleanup_plan` takes 1–1000 literal `targets`, optional `discard` and `protect`
arrays (up to 1000 each), `discard_targets` (default false), `max_entries`
(1–10000, default 10000) and `max_depth` (1–32, default 32). Paths retain the
fixed workspace boundary; no shell/glob expansion occurs. Unknown or protected
roots remain unavailable. Listing roots does not declare them discarded.

Default `save: false` planning writes nothing. `save: true` explicitly saves
the request and proposed selection under `.agent-guard/cleanup-plans` without
deleting sources or approving execution. Optional `select` lists 1–1000 exact
selectable plan entries and requires saving; otherwise selectable requested
roots are proposed. Use `discard_targets: true` only after confirming every
requested root is discarded; use individual declarations for a mixed batch.
For example, after confirming both synthetic example roots are disposable:

```json
{"name":"cleanup_plan","arguments":{"targets":["sessions/old.jsonl","cache/old-run"],"discard_targets":true,"save":true}}
```

The saved ID is in `result.saved_plan_id`; `result.next_step.argv` provides
the operator approval command when the stored selection is complete and
nonempty. Empty or incomplete selections grant no execution permission.
A valid incomplete proposal has `ok: true, partial: true`, preserving CLI
exit 1; a complete proposal has `partial: false`. Check the actual selection
and reasons, not just `ok`.

To read an existing proposal without rescanning, pass its saved ID, optionally
with read controls:

```json
{"name":"cleanup_plan","arguments":{"saved_plan_id":"<saved-plan-id>"}}
```

The response retains `refreshed: false`; `partial` describes the stored
snapshot, not present source freshness. Only `view`, `category`, `path`,
`offset` and `limit` may accompany that ID; targets, declarations and selection
cannot be overridden. To change the proposal, create a new one.

The returned `binding` records workspace, quarantine and `session_mode`.
Storage follows the trusted startup `--trash-root`, or the workspace's
`.agent-trash` when that option is absent. For `SHARED_CLI_DEFAULT`,
the operator must unset `AGENT_GUARD_SESSION`; for `EXPLICIT_STARTUP_SESSION`,
use the identical identity from trusted `--session` configuration. The raw
session identity is not returned. Keep the same package and settings when
approving and executing. With custom storage the returned `next_step.argv`
already contains its workspace/quarantine environment. For the shared default
session, a POSIX operator terminal can use this equivalent command, with the
actual selected quarantine path:

```sh
env -u AGENT_GUARD_SESSION \
  AGENT_GUARD_TRASH=/absolute/path/to/selected-quarantine \
  /absolute/path/to/agent-guard-mcp-venv/bin/python -B \
  /absolute/path/to/agent-guard/skills/delete-guard/scripts/cleanup.py \
  --root /absolute/path/to/project --from-plan '<saved-plan-id>' --approve --json
```

The operator reviews the selection and types `APPROVE`. The CLI refreshes
the plan and returns `task_id` without moving sources or charging the session.
The agent must not allocate a PTY, fill confirmation, call internal approval
functions or change budget identity to make this step pass.

```json
{"name":"cleanup_execute","arguments":{"task_id":"<task-id>"}}
{"name":"cleanup_task_status","arguments":{"task_id":"<task-id>"}}
```

Execution delegates to `cleanup --task ID --execute`. The 600-second grant,
source/effect binding, one rate event and per-group logical budget accounting
are identical to CLI execution. All source mutations still go through
safe_delete. Query delegates to `cleanup --task ID --json`: `ok: true` means
the record was read even if `result.status` is PARTIAL, STOPPED or UNKNOWN.
Task status does not wait behind the server's execution-call lock. Read the
counts, unconfirmed roots and per-group receipts; RUNNING is a last
durable intent, not proof of a live process. `can_execute` does not check
source freshness or current budgets/capacity. See [task states](cleanup-plan.md#operator-approved-tasks-030-development).

Adapter timeout, invalid UTF-8 or missing JSON retains the requested task ID
and a `query` invocation for `cleanup_task_status`. Structured CLI failures
retain their receipts. A repeated execute never resumes a claimed task.
Inspect task/transaction/source evidence before reviewing remaining targets
in a new proposal; do not infer rollback from a missing response.

Existing calls retain a 120-second adapter deadline; cleanup_execute has a
300-second outer deadline to allow two bounded safe_delete groups plus
preflight. A timeout/disconnect can still leave work or a child process in
progress. After response loss, query instead of automatically retrying.
For a timed-out saving plan call, `may_have_changed` concerns proposal metadata,
not source deletion. Task/control files and tool results contain private paths;
neither their raw contents nor grant tokens belong in public reports.

doctor checks all ten registrations and their read-only annotations, then
still calls only `status`, `transactions` and `gc_plan`. It does not invent
a task, save a plan, approve or execute one. Runtime/SDK/host checks for this
new flow include a real operator-approved 71-root protocol-client execution,
and three model hosts' ordinary maintenance plus completed-task reads/replay.
DSH and Kimi have since first executed separate new operator-approved tasks;
Codex's new attempt was stopped by a host usage limit before business calls.
Claude model maintenance is deferred. Historical five/seven-tool results
below retain their original scope.

## Verify and report

1. Confirm the interpreter starts the server and the client discovers the
   tools for that installation: five in 0.2.8, seven in published 0.2.9,
   ten in the 0.3.0 development checkout.
   Check stderr when a dependency or executable cannot
   be found, and reconnect after changing the selected installation.
2. Call `status`, `transactions` and `gc_plan` in a disposable project; confirm
   preview leaves recovery payloads intact.
3. Create a small synthetic valuable file, call `safe_delete`, verify a real
   restorable transaction, then `restore` and compare original bytes.
4. Independently check configured protection and budget refusals preserve
   targets. Confirm forbidden override/force/GC-execute arguments are refused.
5. Report the client/version, platform, workspace binding, tool calls, actual
   results and unverified paths. A raw protocol test, a real client call and a
   real-model trial are different evidence levels.

For the 0.2.9 observation tools, separately call `cleanup_discover`
and `cleanup_measure` on explicit synthetic directory scopes. Check that
source files and recovery state stay unchanged, unknown sizes remain null,
and a limited result can have `ok: true, partial: true` without execution
permission. A successful query or the old five-tool acceptance records do
not establish that the new tools reached a model; see the
[current source validation](../releases/unreleased.md#current-validation-checkpoint).

## Recorded validation and remaining limits

### Codex 0.165.0 with published 0.2.7

On 2026-10-29, **Codex CLI 0.165.0** on local ARM64/PRoot exercised the
published **agent-guard 0.2.7** package through Codex's actual MCP client.
Three controlled native-client groups, without model turns, covered five-tool
discovery, queries, deletion/restoration, quarantine/outside-target refusal,
protection and batch gates, and budget persistence across a fresh Codex
process. Read-only queries preserved the workspace file inventory and content
hashes.

Three separate real-model tasks using the configured `gpt-6.1-sol / max`
completed 16 MCP calls: ordinary delete/restore, same-session budget refusal
and protected-path refusal. Model call arguments and structured results
matched the recorded stdio requests and responses. Restored files matched
their original bytes; rejected files remained intact; restoration did not
refund the admitted budget. The model used only the MCP tools, and GC was
preview-only. There were no trial timeouts or harness-level automatic retries.
The client model label was observed; backend model identity was not
independently authenticated, and no upstream HTTP-request count is claimed.

Four native-client recorder exit-metadata files were incomplete. The captured
tool exchanges supported the reported behavior, and the recorded process IDs
were absent at the final check; the missing lifecycle metadata was not
reconstructed. This limitation is separate from the three completed model
tasks. These results apply to that published 0.2.7 artifact, exact Codex
version and cooperative MCP path, not later artifacts, native
shell/editor interception, arbitrary subagents or all Codex releases.

### Earlier preparation and Kimi checkpoints

The 219-file, versioned **0.2.7 preparation checkpoint** passed local validation on
2026-10-29: 1129 Python cases and both Node groups in 1194.265 seconds, with
no failures, errors, skips, timeouts or automatic retries. It used the existing
diagnostic collector with `npm test` scope and original deadlines rather than
invoking npm. Its 155-file offline installation passed 12 CLI entry-point and
actual installed MCP checks, including `serverInfo.version == 0.2.7`, normal
delete/restore, budget persistence across reconnect, protected/batch refusal
and read-only tools without workspace writes. Local SDK release-gate checks
also passed. This 2026-10-29 local checkpoint did not include final-commit
remote CI or publication; no new real-model trial was run during version
alignment. See the
[versioned validation record](../releases/release-notes-0.2.7.md#validation-status).

The earlier 217-file, 0.2.6-metadata frozen public candidate was tested locally on ARM64/PRoot
POSIX with Python 3.13.5 and the isolated MCP SDK 2.3.0 environment. Both full
runs retained the existing 1800-second total budget and per-test deadlines;
neither changed the runtime/tests or automatically retried a failing case.

| Full run | Python | Node groups | Total time | Result |
|---|---|---|---|---|
| First | 1123 cases, one error, no assertion failures or skips | Not started after Python failed | 1156.312 seconds | FAIL |
| Second | 1123 cases, no failures, errors or skips | Both passed | 1143.435 seconds | PASS |

The first run's existing DSH Lab case
`test_native_capture_corruption_is_refused_before_host_result` could not bring
its observer to READY within the existing five-second startup deadline.
One separate single-case check with the SDK interpreter and one with system
Python passed in 4.409 and 2.979 seconds. The cause remains undetermined; these
checks and the successful second full run do not rewrite the first failure.
The second run completed without a timeout. Both runs used the existing
diagnostic unittest result collector with the same scope as `npm test`, then
the Node commands when Python passed; the npm command itself was not invoked.

Without the optional SDK or an explicitly configured MCP test interpreter,
the original Core checkpoint skipped seven real-stdio protocol cases. The two
full runs above included that SDK and skipped none. At that local checkpoint,
the separate SDK-backed Python 3.10/3.13 CI job had not been run remotely.
Subsequent 0.2.7 release validation for `7431da1` passed its
[15-job CI](https://github.com/mokuyoaxis/agent-guard/actions/runs/40835352396)
and [release workflow](https://github.com/mokuyoaxis/agent-guard/actions/runs/40842400686),
including 1130 Python cases and both Node groups with no release-test skips.
Those workflows predate the 0.2.8 doctor/interruption additions and do not
certify the new checks.

The 154-file offline package matched the frozen source and installed locally.
All 12 CLI help checks, adapter import, installed stdio handshake and five-tool
discovery passed. Installed tests covered valuable-file deletion and restore,
read-only queries, GC preview without payload removal, reconnect-persistent
budget, protection/batch refusals and refusal of override/force/GC-execute
parameters. Protocol acceptance exercised the classic `2025-06-18` handshake;
it does not establish every MCP protocol extension or client version.

Three real **Kimi Code 2.3.0 root-agent native MCP** trials passed:

| Trial | Time | Real model requests / MCP calls | Observed result |
|---|---:|---:|---|
| Ordinary delete/restore | 27.288 seconds | 7 / 6 | Model deleted, obtained a real transaction and restored original bytes |
| Same-session budget | 26.121 seconds | 7 / 6 | Second delete refused; model restored first file; budget remained charged |
| Configured protection | 16.814 seconds | 5 / 4 | Delete refused, original bytes retained, no recovery transaction |

All 19 configured-route requests returned HTTP 200, with 16 native MCP calls,
no fixed model replies, automatic trial retries or route switches. Each trial
used isolated configuration and synthetic files; the model had no Bash tool.
GC was previewed in all three trials and never executed. Before/after user
configuration/auth fingerprints and frozen candidate bytes remained equal.
The existing loopback gateway's actual server-side model identity was not
independently authenticated.

These earlier Kimi results do not add native-hook, subagent, Claude/Cursor/Codex
or other tool coverage; the Codex result is recorded separately above. Claude
Code and Cursor remain documented examples. Native Windows, macOS and Android
shared storage have no new local host validation. The first full-run error and
the earlier unexplained project test timeouts remain recorded as unresolved;
the current successful full run is not evidence of a general timeout fix.

The SDK and upstream setup references are primary sources, not this project's
acceptance results: [official Python SDK](https://github.com/modelcontextprotocol/python-sdk),
[selected SDK release metadata](https://pypi.org/project/mcp/2.3.0/).
