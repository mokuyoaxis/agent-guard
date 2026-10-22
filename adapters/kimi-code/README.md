# Kimi Code adapter

PreToolUse interception for [Kimi Code CLI](https://github.com/MoonshotAI/kimi-code),
built on the shared Core and Claude Code adapter with one conservative
host-specific difference: Core `ASK` is mapped to a hard refusal rather than
being allowed when the host cannot enforce an approval.
Kimi Code ships an external-hook system
(`packages/agent-core-v2/src/features/externalHooks`) whose event names,
payload field names and decision contract are field-compatible with Claude
Code's hooks, so `pre_tool_use.py` here delegates to
`../claude/pre_tool_use.py` instead of reimplementing the mapping.

Verified against **@moonshot-ai/kimi-code 0.42.0, 2.1.1 and 2.3.0**. The detailed
contract comparison below was established against 0.42.0; the 2.1.1 trials
independently observed authenticated root `Bash` blocks through the same
exit-2 hook path on an OAuth official model and a second configured model
route. Connection details and private route identifiers are omitted. See the
[bounded report](../../docs/reports/test-report-kimi-code-block.md).

Version 0.2.6 adds exact **2.3.0** after eight prompt-CLI and eight
ACP root-Bash trials with fixed loopback responses, including BLOCK, ASK
refusal, Python startup failure, recovery and Stop delivery. ACP hooks ran
in the session workspace even when the server started elsewhere. There were
no external model calls. See the [2.3.0 evidence and limits](../../docs/reports/test-report-kimi-code-block.md#kimi-230-acceptance).
This does not extend subagent, other-tool or real-model claims; the separate
[Lab helper](harness/README.md) remains pinned to 2.1.1. Published 0.2.5 keeps
its earlier tested-version list.

This README describes [0.2.6](../../docs/releases/release-notes-0.2.6.md).
The optional Stop helper, doctor checks and setup guidance ship in this version;
see the [maintenance details](../../docs/releases/unreleased.md).
Version 0.2.5 retains its recorded scope. Upgrade the selected installation
before configuring Stop.

For the separate honeytoken experiment, see the
[guard-lab pair and local follow-up](../../docs/reports/test-report-kimi-guard-lab.md).
Its recorded tool-channel exposure and matched guarded observation are distinct
from native Bash interception. A local doctor pass does not rerun that pair.
Use the [private capture/review helper](harness/README.md) to repeat the Lab
workflow with a pinned executable and separate role-channel scans.

## Contract (what was checked, not assumed)

| Surface | Kimi Code 0.42.0 | Claude Code | Match |
|---|---|---|---|
| Payload encoding | JSON on stdin, `camelToSnake` at the boundary | JSON on stdin | ✓ |
| Tool name field | `tool_name` | `tool_name` | ✓ |
| Arguments field | `tool_input` (object) | `tool_input` | ✓ |
| Working directory | `cwd` | `cwd` | ✓ |
| Session id | `session_id` | `session_id` | ✓ |
| Event name field | `hook_event_name` | `hook_event_name` | ✓ |
| Hard block | process exit code `2`, stderr fed back to the model | same | ✓ |
| Soft decision | Only `deny` blocks; `ask` is treated as allow | `ask` can request approval | **different** |
| Hook config | `~/.kimi-code/config.toml`, `[[hooks]]` array | `.claude/settings.json` | different shape |

`resultFromExitCode()` in the Kimi Code bundle maps `2` to
`{action: "block"}` and otherwise parses stdout through
`HookJsonOutputSchema`. Its `structuredOutput()` produces a block only for
`permissionDecision: "deny"`; every other value, including `"ask"`, is
allowed. `blockDecision()` collects any hook result with `action === "block"`.

## Install

1. Merge the hook and skill root into `~/.kimi-code/config.toml` — see
   [`config.example.toml`](config.example.toml). Place the top-level
   `extraSkillDirs` before the first TOML table, or extend its existing array;
   append the `[[hooks]]` blocks separately. Appending the entire example
   after an existing table puts `extraSkillDirs` inside that table.
   `matcher` is a **regular
   expression** matched against the tool name; an empty string matches every
   tool. Use `^Bash$` to select only the shell tool. An unanchored `Bash`
   pattern can also match future tool names containing that word. The example
   invokes the POSIX [shell bridge](hook_bridge.sh) with absolute shell and
   Python paths. Replace all three example paths with paths on your machine;
   quote paths containing spaces for the shell command string. Directly
   invoking `python3 pre_tool_use.py` leaves Python startup failures exposed
   to Kimi's fail-open behavior.

2. Keep `extraSkillDirs` pointing at this repository's `skills/` directory so
   the model also gets the discipline, not just the enforcement:

   ```toml
   extraSkillDirs = ["/absolute/path/to/agent-guard/skills"]
   ```

3. Set the PreToolUse hook's `timeout = 120`, then restart the session.
   The adapter waits up to 90 seconds for the guard; the host needs additional
   time for Python startup and the refusal to return. An outer 90-second
   timeout races that inner deadline. Kimi allows the tool if the hook itself
   times out, so this margin reduces the race without making host timeouts
   fail-closed. Start a new Kimi process after editing hooks; a running
   setup process keeps its earlier hook configuration.

4. From the repository root, run `python3 doctor.py kimi --probe`. The doctor
   checks the configured matcher and command, then exercises the bridge with
   harmless Bash payloads, a Bash→Core policy refusal, the host-specific
   ASK→hard-refusal mapping, and a deliberately missing interpreter. `--json`
   reports PreToolUse `configuration`, `local_probe`, and
   `host_interception` separately; the latter remains `UNVERIFIED` even when
   the local probe passes. The doctor also rejects an invalid
   `AGENT_GUARD_DIALECT` in the current process. It does **not** invoke a
   model, edit the configuration, inspect a running Kimi session, or prove
   host-level interception. It accepts the host's 1–600 second timeout range
   and does not test deadline exhaustion; use the recommended 120 seconds
   even when a shorter configuration passes the doctor.
   The optional Kimi doctor uses Python 3.11+ for
   TOML parsing; the guard adapter itself continues to support Python 3.9+.
   In 0.2.6, a separate `feedback` result checks the optional
   Stop hook. See the [Stop doctor checks](#stop-doctor-checks-unreleased).
   Add `--check-drift` to compare the installed Kimi Code version with the
   shipped profile and compute privacy-safe configuration/runtime
   fingerprints. `CURRENT` still does not prove live interception; see the
   [drift status contract](../../docs/guides/host-drift.md).
   An explicit `--live-sentinel` may then use one configured model call in a
   private fixture and requires adapter, audit, host-feedback, and marker
   evidence. The selected file must be named `config.toml`; the runner does
   not rewrite it. Kimi prompt mode uses its machine-readable stream; adding
   `--auto` or `--yolo` is neither needed nor valid alongside `--prompt` in
   Kimi 2.x.

For an npm installation, `tests/` is not shipped. The source unittest
references below apply to a full checkout; use the installed doctor for local
probes and report the unit suite as `NOT_INCLUDED`. Finish this configuration
stage with a handoff recording paths, probe results and pending fresh-process
verification. The doctor's `UNVERIFIED` host-interception/model-delivery fields
are expected. Repeat probes only to check a new change or investigate a
failure. If the setup hits a step limit, preserve that handoff for continuation.

## Decision mapping

The shared Claude adapter supplies the Core call and normal mappings; Kimi
sets `ask_is_block=True` because this CLI cannot enforce a prompt:

| Core decision | Adapter output |
|---|---|
| `ALLOW`, no compensations | exit 0, silent |
| `ALLOW`, compensations applied | exit 0 + `permissionDecision: "allow"` (reason names the quarantine) |
| `ASK` | exit 2, stderr explains that the command must be split/restated |
| `BLOCK` | exit 2, stderr fed back to the model |
| Malformed `PreToolUse` JSON or missing required Bash fields | exit 2 (fail-closed) |
| guard failure / adapter bug caught after Python starts | exit 2 (fail-closed for that call) |
| Python fails to start, but the shell bridge runs | bridge converts the failure to exit 2 |

This table describes adapter output **when the adapter actually starts**.
It does not describe a missing hook or a failed hook command.

<a id="optional-recovery-feedback-unreleased-checkout-addition"></a>

## Optional recovery feedback

The tested 0.42.0, 2.1.1 and 2.3.0 success-hook paths do not include the hook's
recovery explanation in the next model request. A successful Bash result
alone can therefore hide that the guard already moved files to quarantine.
Version 0.2.6 adds a separate, opt-in **POSIX Stop hook**. It is not included
in the published 0.2.5 package. The existing PreToolUse installation keeps
its current behavior until this additional hook is configured.

```toml
# Keep the existing PreToolUse hook. Add this only when the extra response is wanted.
[[hooks]]
event = "Stop"
command = "/bin/sh /absolute/path/to/agent-guard/adapters/kimi-code/hook_bridge.sh /absolute/path/to/python3 --stop-feedback"
timeout = 30
```

Confirm the selected root contains [`stop_feedback.py`](stop_feedback.py)
before adding this hook. Replace and quote paths as in the PreToolUse
installation. Start a new
session after changing the configuration. This hook reads the existing audit
and, for incomplete compensations, correlates transaction headers in the
recovery journal. It reports actual txids, recorded moves, recovery-state
inspection and restore commands. File relocations include the retained-space
notice and complete GC commands with the recorded transaction IDs. Restoring
files moves them back without freeing total disk space; permanent GC removes
their recovery copies. Git snapshots are named
separately; GC does not remove their stash entries. No command, restore or
purge is executed by this hook, and deletion policy is unchanged.

**The initial answer appears first.** When new compensation records exist,
Stop asks the model for one additional response to supplement or correct it.
It cannot repair the earlier Bash tool result or prevent an inaccurate initial
answer. The feedback asks for a brief supplement with commands copied in full;
the model can still omit or misstate details. No compensation, direct regenerable deletion, an empty clean
enumeration and a clean Git snapshot with nothing to save produce no extra response. Partial failures identify incomplete
recovery records without claiming that Bash ran or every file is recoverable.

Emission cursors live in `$KIMI_CODE_HOME/agent-guard-feedback/` (default
`~/.kimi-code/agent-guard-feedback/`), scoped to the physical workspace,
trash location and session. A POSIX lock and atomic checkpoint prevent
concurrent Stop calls from emitting the same records again. Cursors record
emission, **not model acknowledgement**. Cancellation, exhausted model/step
budgets, failed hook launch or host changes can still prevent delivery.
Cursor corruption or replacement/truncation of the append-only audit produces
an explicit feedback-unavailable message; it does not rewind silently or
modify recovery data. Inspect `restore.py list` from the original workspace.

The scope is the session, not an authenticated agent identity. A parent and
child sharing a session/workspace may have their records emitted by whichever
Stop runs first. This does not guarantee a separate notice to every actor.
The local doctor checks below do not establish delivery to a running model.

<a id="stop-doctor-checks-unreleased"></a>

### Stop doctor checks

Run `python3 doctor.py kimi --probe --json` from the checkout containing the
Stop helper. Existing top-level `configuration` and `local_probe` fields
describe PreToolUse. The separate `feedback` object reports:

| Field | Meaning |
|---|---|
| `configured` | `false` when no agent-guard Stop definition is found; `null` when the configuration cannot be checked. |
| `configuration` | `NOT_CONFIGURED`, `PASS`, `FAIL`, or `UNVERIFIED`. Checks the event, command, matcher, timeout, duplicate definitions and helper presence. |
| `local_probe` | `NOT_RUN` without `--probe`, or when absent/invalid; otherwise `PASS` or `FAIL`. |
| `model_delivery` | Always `UNVERIFIED`; local execution does not prove a live host loaded Stop or a model received it. |
| `problems` | Stop-specific configuration or local execution failures. |

The command must use this installation's absolute shell/bridge/Python paths
followed by `--stop-feedback`. Use `event = "Stop"`, omit `matcher` or leave
it empty, and choose an integer timeout from 1 to 600 seconds (recommended
30). A Bash matcher would filter out Stop. Unrelated Stop commands are ignored.

`--probe` uses a temporary home, workspace, recovery audit and emission cursor.
It checks silent no-record/active-Stop cases, a synthetic transaction's
retained-space and complete GC guidance, then repeat-Stop silence. It performs
no real deletion, restore, GC or model call and leaves existing recovery records
alone. An executable Python path or an existing helper file alone is not a
successful runtime probe. The probe uses a fixed local deadline; it does not
exhaust the configured host timeout.

An absent optional Stop does not fail the doctor. A configured Stop that fails
configuration or a requested local probe makes the CLI exit 1, even when
PreToolUse passes. An unreadable config reports Stop as `UNVERIFIED`, rather
than claiming it is disabled. Neither local success nor a passing Bash live
sentinel changes `feedback.model_delivery`. These additions ship in
[0.2.6](../../docs/releases/release-notes-0.2.6.md) and are absent from 0.2.5.

## Known limitations — read before claiming interception

- **Hook launch and timeout are separate from adapter refusal.** A local
  0.42.0 / 2.1.1 audit confirmed that a nonexistent hook command or a started
  hook exceeding its one-second test deadline still allowed an inert Bash
  sentinel to run. The bridge blocked a missing Python interpreter when the
  bridge itself started. A longer timeout does not protect a missing hook.
  Stop Python startup failure produced one feedback-unavailable continuation;
  it did not undo the preceding Bash call. These were fixed loopback-response
  trials of the actual CLIs, not new real-model trials.
- **ASK is not supported by this host's hook contract.** Even outside
  `kimi --auto`, Kimi 0.42.0 treats `permissionDecision: "ask"` as allow.
  This adapter therefore maps Core ASK to exit 2 (BLOCK). The failure class
  is described in the public
  [`development note`](../../docs/history/development-note-unguarded-deletion.md),
  and it is fixed:
  `cd /tmp && rm -rf "$PWD/../home"` now classifies as
  `BLOCK / BLOCK_UNDETERMINABLE_EFFECT`. A shape rule describes
  *compensation* difficulty rather than effect scope, so it no longer
  returns before the hard boundaries are evaluated. Regression suite:
  `tests/test_incident_regression.py`. A shape-only ASK now asks
  the agent to split and retry instead of proceeding without compensation.
  The 2.1.1 live sentinel exercised a hard `BLOCK`, not a live ASK path, so
  this conservative mapping is not advertised as a 2.1.1 approval-UI test.
  The 2.3.0 prompt/ACP trials observed ASK being hard-refused with fixed
  responses; they do not establish native approval-UI behavior either.
- **Subagent inheritance is observational, not a blanket guarantee.** In two
  isolated sandboxes using one configured model route, single and concurrent
  subagent Bash calls reached the native hook and produced recoverable audit
  records.
  A third isolated project then showed [execution-level `BLOCK` enforcement](../../docs/reports/test-report-kimi-code-block.md)
  for one root and one single-subagent inert Bash probe. Its separate root
  `ASK` probe was denied. A fourth isolated project reproduced these bounded
  paths using a second configured request model ID and
  verified a recoverable deletion/restore cycle. Two same-turn subagents
  each received `BLOCK`, but their Bash calls reached the hook seconds apart;
  simultaneous hook execution, other tools/routes, backend model identity,
  and broader scheduling patterns remain untested.
- **Config is read at session start**, so a running session keeps its old hook
  set until restarted.
- Strict payload refusal only applies **after this hook is invoked**. It cannot
  detect or block a missing hook, a mismatched `Bash` matcher, or a host that
  stops emitting `PreToolUse`.
- **Host fail-open was observed when the old direct-Python hook command failed
  before Python started.** In an isolated Kimi 0.42.0 trial, a process-local failing
  `python3` shim made a Bash call execute even though the correctly started
  adapter would have returned `BLOCK_DIALECT_UNKNOWN`. The sentinel appeared
  and no guard audit event was added. See the [bounded report](../../docs/reports/test-report-kimi-code-block.md).
  The bridge changes this particular failure into exit 2 **if the bridge
  itself starts**. It cannot block a hook that is absent, skipped by the
  matcher, fails to spawn, or times out. The host remains fail-open for those
  cases; a verified interpreter path is risk reduction, not a hard boundary.
- **The bridge was exercised through a new Kimi 0.42.0 host session** using
  the second configured request model ID: one harmless Bash call was
  blocked under a process-local invalid dialect, and a separate harmless
  Bash call ran under the normal dialect. The denied sentinel was absent,
  the allowed sentinel was present, and the block was audited. A later
  process-local Python startup fault returned exit 1 before the adapter ran;
  the bridge converted it to Kimi's hard refusal, and the sentinel stayed
  absent. This does not test a bridge-spawn failure or timeout; see the
  [bounded report](../../docs/reports/test-report-kimi-code-block.md).
- Kimi's `doctor config` accepted a hook-free test config and an invalid
  regex matcher; it checks configuration validity, not live interception.
  In the installed 0.42.0 bundle, a matcher regex error yields no match.
- This adapter covers `Bash` only, matching the Claude adapter's scope.

## Verification

From the repository root:

```sh
python3 doctor.py kimi --probe --json
python3 -m unittest tests.test_doctor tests.test_kimi_adapter tests.test_kimi_feedback
```

These are local checks. They do not certify a running session or the optional
Stop hook's delivery. See the [versioned host report](../../docs/reports/test-report-kimi-code-block.md)
for actual CLI trials and their limits. Set `AGENT_GUARD_DEBUG=1` in the Kimi
process environment for adapter troubleshooting; the TOML example does not
define a debug switch.
