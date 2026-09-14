# agent-guard adapter for Claude Code

Wraps Claude Code's `PreToolUse` hook so every Bash command passes through
the shared core (`skills/delete-guard/scripts/check.py`) before execution.
The rule engine is not duplicated here - this adapter only translates the
Decision Protocol onto Claude Code's native hook semantics.

## Decision mapping

| Core decision | Claude Code behavior |
|---|---|
| `ALLOW` (no compensation) | exit 0, silent |
| `ALLOW` (compensation applied) | `permissionDecision: "allow"` + reason naming what was quarantined and the txid |
| `ASK` (`COMPOUND_CWD_DELETE`, `COMPOUND_CREATE_DELETE`) | `permissionDecision: "ask"` - ASK_ONCE, reason shown to the human; splitting the command avoids future prompts |
| `BLOCK` | exit 2 - stderr is fed back to **Claude**, so the model sees the code, the explanation, and the remediation |
| guard failure | exit 2 (fail-closed) |

Note the deliberate asymmetry: allow/ask reasons are user-facing, while
BLOCK uses the stderr channel so the *model* learns the remediation.

In a headless run (`claude -p`) with no interactive approver, an ASK that
nobody approves is a **denial** and the command does not run. That is the
fail-safe outcome, but wire up an approver if you need ASK to be an actual
prompt.

## Install

1. Copy or reference this repository from a stable path.

2. Add the hook to project `.claude/settings.json`
   (see `settings.example.json`):

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python3 /absolute/path/to/agent-guard/adapters/claude/pre_tool_use.py",
            "timeout": 120
          }
        ]
      }
    ]
  }
}
```

   On Windows, write that interpreter as `python`: a stock install has no
   `python3` alias, so the hook would fail before the adapter ever runs.
   Only that one token changes; the path and the rest of the command stay
   as they are.

3. Optionally copy `skills/delete-guard/SKILL.md` into the project's skill
   directory so the model prefers the safe-delete flow proactively.

Requirements: Python 3.9+, POSIX shell, git. No third-party packages.

## Conformance

`tests/test_conformance.py` pins the cross-harness guarantee: identical
command + cwd + workspace state must produce identical core decision +
reason code through any adapter. Run it after any change to either side:

```
python3 -m unittest tests.test_conformance
```

Debug: set `AGENT_GUARD_DEBUG=1` to print the raw core verdict JSON to
stderr.

## Shell dialect

The hook forwards a shell dialect to `check.py` so Windows-native command
lines are lexed with the right rules. Precedence:

| Source | Example |
|---|---|
| hook payload | `"dialect": "powershell"` (also `shell_dialect`) |
| environment | `AGENT_GUARD_DIALECT=powershell` |
| default | `posix` |

The prefilter follows the dialect: the POSIX regex cannot see
`ri build -r -fo`, so a Windows-native payload would otherwise skip the
guard entirely. The POSIX prefilter is unchanged, so the default path keeps
its exact behaviour. An unrecognised dialect selector is **not** swapped
for POSIX - `check.py` returns `BLOCK_DIALECT_UNKNOWN` and the hook exits 2.

## Live-tested

Validated end-to-end against a real Claude Code session (2.1.270) with a
scripted mock Anthropic endpoint standing in for the model, plus the
`python3`-free regression tests. See
[`docs/test-report-claude-code-harness.md`](../../docs/test-report-claude-code-harness.md)
and the shipped harness in [`harness/`](harness/README.md).

The guard child is spawned with `sys.executable`, never `python3` from
`PATH`: on a host with only `python`, a hardcoded `python3` made every
interception fail closed and the guard silently unusable. See the report's
A/B evidence.
