# Local MCP tools / 本地 MCP 工具

This adapter is introduced in **0.2.7** and is absent from 0.2.6. The separate
0.3.0 plan remains paused. The historical
protocol/package records and three real Kimi Code trials below belong to the earlier
2026-10-28 source checkpoint with 0.2.6 package metadata, including a failed
first full run. They do not certify the new versioned artifact. See the
[0.2.7 notes](../releases/release-notes-0.2.7.md) for versioned validation and
exact-version availability checks.
Configuration examples are not host acceptance.

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
2026-10-28. They are not claims that all three clients were exercised locally.
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
the default Core suite skips seven real-stdio protocol cases. The two full
runs above included that SDK and skipped none. A separate CI job now declares
SDK-backed MCP checks for Python 3.10/3.13; it has not been run remotely in
this work, and no remote-CI success is claimed.

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

These results do not add native-hook, subagent, Claude/Cursor/Codex or other
tool coverage. Those three clients' examples were checked against official
documentation, not exercised as live clients here. Native Windows, macOS and
Android shared storage have no new validation. The first full-run error and
the earlier unexplained project test timeouts remain recorded as unresolved;
the current successful full run is not evidence of a general timeout fix.

The SDK and upstream setup references are primary sources, not this project's
acceptance results: [official Python SDK](https://github.com/modelcontextprotocol/python-sdk),
[selected SDK release metadata](https://pypi.org/project/mcp/2.3.0/).
