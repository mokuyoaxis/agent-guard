# Claude Code end-to-end harness

Reproduces the live interception test in
[`docs/test-report-claude-code-harness.md`](../../../docs/test-report-claude-code-harness.md):
the **real** Claude Code CLI, the **real** agent-guard `PreToolUse` hook, and
a **scripted mock Anthropic endpoint** standing in for the model.

The model is not the subject of the test - the harness interception path is.
Swapping the model for a scripted endpoint makes the run deterministic and
network-free while every hook, permission, and guard code path stays real.

## Pieces

| File | Role |
|---|---|
| `mock_anthropic_api.mjs` | Minimal Anthropic Messages API. Serves one scripted turn per request and advances the script only when the client sends back a `tool_result`, so one scripted turn == one harness turn. |
| `run_scenario.sh` | Fresh git project + real hook in `.claude/settings.json` + CLI run, capturing transcript, HTTP request log, and `.agent-trash/`. |
| `run_scenario_no_python3.sh` | Same, but the hook is invoked as `python` and the CLI runs on a `PATH` with **no `python3`** - the Windows / minimal-image shape that made the adapter fail closed. |

## Run

```bash
npm install -g @anthropic-ai/claude-code     # or point CLAUDE_BIN at any build
CLAUDE_BIN=claude bash adapters/claude/harness/run_scenario.sh \
  relocate "rm -rf build" 8901
```

`run_scenario.sh <name> <command> <port> [permission-mode]`

For the no-`python3` variant, create a directory holding `python`, `git`,
and the POSIX utilities the guard's own commands need, then:

```bash
bash adapters/claude/harness/run_scenario_no_python3.sh \
  nopy3_fixed "rm -rf build" 8908
```

`HARNESS_OUT` selects the output root (default `/tmp/agent-guard-harness`).

## Outputs (per scenario)

```
<out>/<name>/
├── script.json          # the scripted model turns
├── transcript.jsonl     # claude --output-format stream-json --include-hook-events
├── requests.jsonl       # what the CLI actually sent the endpoint
├── stderr.txt, exit.txt
└── project/             # the workspace afterwards (incl. .agent-trash/)
```

The `hook_response` records in `transcript.jsonl` carry the hook's stdout and
exit code, which is where the `permissionDecision` mapping is observable.
