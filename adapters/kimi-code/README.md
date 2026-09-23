# Kimi Code adapter

PreToolUse interception for [Kimi Code CLI](https://github.com/MoonshotAI/kimi-code),
built on the shared Core and Claude Code adapter with one required
host-specific difference: Core `ASK` is a hard refusal on Kimi 0.42.0.
Kimi Code ships an external-hook system
(`packages/agent-core-v2/src/features/externalHooks`) whose event names,
payload field names and decision contract are field-compatible with Claude
Code's hooks, so `pre_tool_use.py` here delegates to
`../claude/pre_tool_use.py` instead of reimplementing the mapping.

Verified against **@moonshot-ai/kimi-code 0.42.0**.

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
   tool. Keep it anchored to the shell tool (`Bash`) unless you have a reason
   to widen it.

2. Keep `extraSkillDirs` pointing at this repository's `skills/` directory so
   the model also gets the discipline, not just the enforcement:

   ```toml
   extraSkillDirs = ["/absolute/path/to/agent-guard/skills"]
   ```

3. Restart the session. Hooks are read at session start.

## Decision mapping

The shared Claude adapter supplies the Core call and normal mappings; Kimi
sets `ask_is_block=True` because this CLI cannot enforce a prompt:

| Core decision | Adapter output |
|---|---|
| `ALLOW`, no compensations | exit 0, silent |
| `ALLOW`, compensations applied | exit 0 + `permissionDecision: "allow"` (reason names the quarantine) |
| `ASK` | exit 2, stderr explains that the command must be split/restated |
| `BLOCK` | exit 2, stderr fed back to the model |
| guard failure / adapter bug | exit 2 (fail-closed) |

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
- **Subagent inheritance is observational, not a blanket guarantee.** In two
  isolated `local/kimi-k3` sandboxes, single and concurrent subagent Bash
  calls reached the native hook and produced recoverable audit records.
  Other commands, models and scheduling patterns remain untested. Host-side
  enforcement of a `BLOCK` verdict remains unproven.
- **Config is read at session start**, so a running session keeps its old hook
  set until restarted.
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
