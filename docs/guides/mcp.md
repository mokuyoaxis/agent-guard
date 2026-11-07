# Local MCP tools / 本地 MCP 工具

This adapter is introduced in **0.2.7** and is absent from 0.2.6. The separate
0.3.0 plan remains paused. The historical
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

The local launch command is:

```sh
/absolute/path/to/agent-guard-mcp-venv/bin/python -B /absolute/path/to/agent-guard/adapters/mcp/server.py --workspace /absolute/path/to/your-project
```

The client starts this stdio process. No network listener, cloud account or
system service is required. Use absolute paths and bind one workspace at
startup; a tool call cannot change it or select another quarantine. Protocol
messages use stdout; diagnostics use stderr.
This adapter binds the workspace-local `.agent-trash/`; it does not adopt an
ambient custom quarantine or deletion-policy environment override.

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

From a 0.2.8 installation or checkout, run:

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

The checks cover:

1. The selected interpreter, pinned SDK metadata and actual SDK imports.
2. A classic `2025-06-18` stdio handshake, the `agent-guard` service identity
   and a service version matching the doctor's package version.
3. The five expected tools and read-only annotations for the query tools.
4. Successful `status`, `transactions` and `gc_plan` calls, including the
   requested workspace and its local quarantine binding reported by `status`.

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
| `safe_delete` | `paths` (1–1000 literal strings), optional `dry_run` (default false) | Delegate to safe_delete with the existing policy, budget, audit and recovery rules |
| `restore` | `txid` | Restore a transaction without force-overwriting an occupant |
| `transactions` | none | List the selected workspace's recovery transactions |
| `status` | optional integer `tail` (0–100, default 10) | Inspect recovery state, limits and recent audit information |
| `gc_plan` | none | Preview the existing transaction GC plan; never execute it |

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

Large batches remain refused with Cleanup guidance; use the existing
[reviewed terminal workflow](cleanup-plan.md#confirm-and-execute) when
authorized. A model-provided `confirm=true`, client tool annotation or
permissive auto-approval setting does not replace that workflow. Restore
conflicts require operator review. Changing policy, force restoration,
executing GC or resetting a budget remains outside this server's tool set.

A tool call can fail after a partial mutation. Inspect its structured result,
reported transaction IDs and `transactions`/`status`; do not assume failure or
cancellation means nothing moved. Do not automatically retry a mutating call
after a client timeout or disconnection. The existing recovery, concurrency
and same-UID boundaries remain; this server is not an OS sandbox.

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
```

Use `codex mcp list` and `/mcp` in a fresh session to inspect configuration and
connection state. Configuration being present does not prove a model actually
called the tool or that all filesystem actions are covered. See the
[official OpenAI MCP documentation](https://learn.chatgpt.com/docs/extend/mcp?site_variant=chatgpt).

These source-based examples were checked against official documentation on
2026-10-28. Claude Code and Cursor remain configuration examples; the later
Codex acceptance below covers one exact version and selected MCP scenarios.
Client schemas, approvals and connection behavior can change; verify the
installed version rather than copying another client's configuration shape.

## Verify and report

1. Confirm the interpreter starts the server and the client discovers the five
   tools. Check stderr when a dependency or executable cannot be found.
2. Call `status`, `transactions` and `gc_plan` in a disposable project; confirm
   preview leaves recovery payloads intact.
3. Create a small synthetic valuable file, call `safe_delete`, verify a real
   restorable transaction, then `restore` and compare original bytes.
4. Independently check configured protection and budget refusals preserve
   targets. Confirm forbidden override/force/GC-execute arguments are refused.
5. Report the client/version, platform, workspace binding, tool calls, actual
   results and unverified paths. A raw protocol test, a real client call and a
   real-model trial are different evidence levels.

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
version and cooperative MCP path, not the 0.2.8 artifact, native
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
