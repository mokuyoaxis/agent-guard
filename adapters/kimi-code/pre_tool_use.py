#!/usr/bin/env python3
"""Kimi Code PreToolUse hook - agent-guard adapter (Decision Protocol).

Kimi Code's external-hook contract is **field-compatible** with Claude Code's
PreToolUse hook. Verified against @moonshot-ai/kimi-code 0.42.0
(`packages/agent-core-v2/src/features/externalHooks`):

    payload (stdin, snake_case JSON)
        hook_event_name, session_id, cwd, client_type, session_title,
        tool_name, tool_input, tool_call_id
      - the harness camelCases internally and converts to snake_case at the
        boundary (`toHookInputData` -> `camelToSnake`), so field names land
        exactly where Claude Code puts them.

    decision contract
        exit 2                       -> BLOCK, stderr fed back to the model
        exit 0 + hookSpecificOutput  -> permissionDecision allow

    Kimi Code 0.42.0 treats permissionDecision="ask" as allow. The shared
    adapter therefore runs with ask_is_block=True: a Core ASK is refused
    until the agent restates it as separately checkable commands.

    config (TOML, ~/.kimi-code/config.toml)
        [[hooks]]
        event = "PreToolUse"
        matcher = "Bash"          # regular expression over the tool name
        command = "python3 /path/to/agent-guard/adapters/kimi-code/pre_tool_use.py"
        timeout = 90

Because the two contracts match, this adapter **delegates to the shared
Claude adapter** rather than reimplementing the payload -> core -> decision
mapping. The repository rule is one implementation per decision surface; a
second copy here would drift the first time either harness changes.

What is Kimi-specific and therefore lives here or in README.md:
  - the config file location and the `[[hooks]]` TOML shape;
  - `matcher` is a **regular expression** (empty matches every tool);
  - subagent inheritance: Kimi Code runs hooks from the same runner for
    `SubagentStart`/`SubagentStop`, and PreToolUse is triggered from the
    shared tool executor. Whether a *subagent's* Bash call reaches this hook
    must be proven by observing the event, not assumed - see README.md.

Set AGENT_GUARD_DEBUG=1 to surface the raw core verdict on stderr.
"""
from __future__ import annotations

import os
import runpy
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
_CLAUDE_ADAPTER = os.path.join(_REPO_ROOT, "adapters", "claude", "pre_tool_use.py")

if __name__ == "__main__":
    try:
        if not os.path.isfile(_CLAUDE_ADAPTER):
            raise FileNotFoundError(_CLAUDE_ADAPTER)
        shared = runpy.run_path(_CLAUDE_ADAPTER)
        sys.exit(shared["main"](ask_is_block=True))
    except Exception as exc:  # never fail open on adapter bugs
        sys.stderr.write(
            f"[agent-guard] Kimi adapter error (fail-closed): {exc}\n")
        sys.exit(2)
