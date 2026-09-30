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

Verified against **@moonshot-ai/kimi-code 0.42.0 and 2.1.1**. The detailed
contract comparison below was established against 0.42.0; the 2.1.1 trials
independently observed authenticated root `Bash` blocks through the same
exit-2 hook path on an OAuth official route and on official K3 through a
maintainer-confirmed trusted relay. See the
[bounded report](../../docs/test-report-kimi-code-block.md).

For the separate honeytoken experiment, see the
[guard-lab pair and local follow-up](../../docs/test-report-kimi-guard-lab.md).
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

1. Add the hook and the skill root to `~/.kimi-code/config.toml` — see
   [`config.example.toml`](config.example.toml). `matcher` is a **regular
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

3. Restart the session. Hooks are read at session start.

4. From the repository root, run `python3 doctor.py kimi --probe`. The doctor
   checks the configured matcher and command, then exercises the bridge with
   harmless Bash payloads, a Bash→Core policy refusal, the host-specific
   ASK→hard-refusal mapping, and a deliberately missing interpreter. `--json`
   reports `configuration`, `local_probe`, and
   `host_interception` separately; the latter remains `UNVERIFIED` even when
   the local probe passes. The doctor also rejects an invalid
   `AGENT_GUARD_DIALECT` in the current process. It does **not** invoke a
   model, edit the configuration, inspect a running Kimi session, or prove
   host-level interception. The optional Kimi doctor uses Python 3.11+ for
   TOML parsing; the guard adapter itself continues to support Python 3.9+.
   Add `--check-drift` to compare the installed Kimi Code version with the
   shipped profile and compute privacy-safe configuration/runtime
   fingerprints. `CURRENT` still does not prove live interception; see the
   [drift status contract](../../docs/host-drift.md).
   An explicit `--live-sentinel` may then use one configured model call in a
   private fixture and requires adapter, audit, host-feedback, and marker
   evidence. The selected file must be named `config.toml`; the runner does
   not rewrite it. Kimi prompt mode uses its machine-readable stream; adding
   `--auto` or `--yolo` is neither needed nor valid alongside `--prompt` in
   Kimi 2.x.

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

## Known limitations — read before claiming interception

- **ASK is not supported by this host's hook contract.** Even outside
  `kimi --auto`, Kimi 0.42.0 treats `permissionDecision: "ask"` as allow.
  This adapter therefore maps Core ASK to exit 2 (BLOCK). The failure class
  is described in the public
  [`development note`](../../docs/development-note-unguarded-deletion.md),
  and it is fixed:
  `cd /tmp && rm -rf "$PWD/../home"` now classifies as
  `BLOCK / BLOCK_UNDETERMINABLE_EFFECT`. A shape rule describes
  *compensation* difficulty rather than effect scope, so it no longer
  returns before the hard boundaries are evaluated. Regression suite:
  `tests/test_incident_regression.py`. A genuinely shape-only ASK now asks
  the agent to split and retry instead of proceeding without compensation.
  The 2.1.1 live sentinel exercised a hard `BLOCK`, not a live ASK path, so
  this conservative mapping is not advertised as a 2.1.1 approval-UI test.
- **Subagent inheritance is observational, not a blanket guarantee.** In two
  isolated `local/kimi-k3` sandboxes, single and concurrent subagent Bash
  calls reached the native hook and produced recoverable audit records.
  A third isolated project then showed [execution-level `BLOCK` enforcement](../../docs/test-report-kimi-code-block.md)
  for one root and one single-subagent inert Bash probe. Its separate root
  `ASK` probe was denied. A fourth isolated project reproduced these bounded
  paths using the separate `kimi-code/kimi-for-coding` request model ID and
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
  and no guard audit event was added. See the [bounded report](../../docs/test-report-kimi-code-block.md).
  The bridge changes this particular failure into exit 2 **if the bridge
  itself starts**. It cannot block a hook that is absent, skipped by the
  matcher, fails to spawn, or times out. The host remains fail-open for those
  cases; a verified interpreter path is risk reduction, not a hard boundary.
- **The bridge was exercised through a new Kimi 0.42.0 host session** using
  the `kimi-code/kimi-for-coding` request model ID: one harmless Bash call was
  blocked under a process-local invalid dialect, and a separate harmless
  Bash call ran under the normal dialect. The denied sentinel was absent,
  the allowed sentinel was present, and the block was audited. A later
  process-local Python startup fault returned exit 1 before the adapter ran;
  the bridge converted it to Kimi's hard refusal, and the sentinel stayed
  absent. This does not test a bridge-spawn failure or timeout; see the
  [bounded report](../../docs/test-report-kimi-code-block.md).
- Kimi's `doctor config` accepted a hook-free test config and an invalid
  regex matcher; it checks configuration validity, not live interception.
  In the installed 0.42.0 bundle, a matcher regex error yields no match.
- This adapter covers `Bash` only, matching the Claude adapter's scope.

## Verification

```sh
# Should exit 2 with a BLOCK code (the string is data, never executed):
printf '%s' '{"hook_event_name":"PreToolUse","session_id":"t","cwd":"/tmp/agent-guard-kimi-sandbox-20260921","tool_name":"Bash","tool_input":{"command":"rm -rf /"},"tool_call_id":"c1"}' \
  | python3 adapters/kimi-code/pre_tool_use.py; echo "exit=$?"

# Should exit 0 silently:
printf '%s' '{"hook_event_name":"PreToolUse","session_id":"t","cwd":"/tmp/agent-guard-kimi-sandbox-20260921","tool_name":"Bash","tool_input":{"command":"ls -la"},"tool_call_id":"c2"}' \
  | python3 adapters/kimi-code/pre_tool_use.py; echo "exit=$?"
```

Set `AGENT_GUARD_DEBUG=1` to print the raw Core verdict JSON to stderr.
