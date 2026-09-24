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
| malformed hook payload / guard failure after adapter starts | exit 2 (fail-closed for that call) |
| Python launch/runtime failure while bridge runs | bridge converts non-0/2 exits to exit 2 |

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
            "command": "/bin/sh /absolute/path/to/agent-guard/adapters/claude/hook_bridge.sh /absolute/path/to/python3",
            "timeout": 120
          }
        ]
      }
    ]
  }
}
```

   This bridge is for POSIX hosts. Replace both repository and interpreter
   paths with absolute paths on your machine; quote paths containing spaces
   for the shell command. A direct `python3 pre_tool_use.py` hook may be
   skipped by Claude Code if Python fails before the adapter starts.

   On Windows, use an absolute path to `python` with a direct adapter command;
   a stock install may not have a `python3` alias. The POSIX shell bridge does
   not run natively there, and this direct form has **not** been validated
   against Python startup failure on a real Windows Claude Code host.

3. Optionally copy `skills/delete-guard/SKILL.md` into the project's skill
   directory so the model prefers the safe-delete flow proactively.

4. From the project directory, run:

   ```sh
   python3 /absolute/path/to/agent-guard/doctor.py claude --probe
   ```

   By default it checks only this project's
   `.claude/settings.json`; use `--config` to select another settings file,
   such as `.claude/settings.local.json` or a user-level file. It checks the
   exact `Bash` matcher, synchronous POSIX bridge and interpreter, then sends
   harmless payloads to that bridge. `--json` prints machine-readable status.
   A passing check does **not** inspect Claude's merged settings, prove a
   running session loaded the hook, or verify host-level interception. For
   those claims, use the isolated host acceptance in the
   [capability matrix](../../docs/harness-capabilities.md).

Requirements: Python 3.9+, POSIX shell, git. No third-party packages.
The optional Claude doctor uses only standard-library modules; the adapter
does not require it at runtime.

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

Validated end-to-end against real Claude Code sessions (2.1.270 and 2.1.273) with a
scripted mock Anthropic endpoint standing in for the model, plus the
`python3`-free regression tests. See
[`docs/test-report-claude-code-harness.md`](../../docs/test-report-claude-code-harness.md)
and the shipped harness in [`harness/`](harness/README.md).

The guard child is spawned with `sys.executable`, never `python3` from
`PATH`: on a host with only `python`, a hardcoded `python3` made every
interception fail closed and the guard silently unusable. See the report's
A/B evidence.

The 2.1.273 run used the POSIX bridge and checked healthy compensation,
hard BLOCK, headless ASK denial, and an injected Python process failure.
This does **not** prove that a missing hook, unmatched tool, or timed-out hook
is blocked: those are host-level fail-open boundaries. The adapter only
matches `Bash`; other tools and real-model behavior are outside this test.
