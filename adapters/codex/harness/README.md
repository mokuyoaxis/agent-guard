# Codex acceptance harness

Reproduces the 20-check acceptance run recorded in
[`docs/test-report-codex-gpt-6-astra-high.md`](../../../docs/test-report-codex-gpt-6-astra-high.md)
(2026-09-19, baseline `de5a00e`, `gpt-6-astra` / `high`).

Unlike [`adapters/claude/harness/`](../../claude/harness/), there is no Codex
adapter behind this directory. Codex has no native PreToolUse hook in this
repository, so nothing here implies automatic interception: the driver
exercises the guard through the production CLI (`check.py`, `safe_delete.py`,
`restore.py`, `status.py`, `gc.py`) exactly the way a skill-guided agent would.

## Provenance

Recovered 2026-09-21 from the Codex session cache. The original driver lived in
`/tmp/agent-guard-astra-high-20260919-HxRpvY/acceptance.py` together with its
`events.jsonl` and `summary.json`; that directory was temporary storage and did
not survive. The recovered driver was subsequently made portable by replacing
its hard-coded checkout path with `AGENT_GUARD_PROJECT`; before that edit, its
SHA-256 was `b667c9ad8683516cf79fc60d6bf7c85bd7ef7dd7e6d872ae6ca5b7a6927a91f6`.

## Run

The driver requires the checkout path and refuses an existing run directory.
Run it from a fresh directory; `AGENT_GUARD_PROJECT` must be absolute:

```bash
run=$(mktemp -d /tmp/agent-guard-astra-XXXXXX)
cp adapters/codex/harness/acceptance.py "$run"/
cd "$run" && AGENT_GUARD_PROJECT=/absolute/path/to/agent-guard python3 acceptance.py
```

The driver uses `AGENT_GUARD_SESSION=astra-high-acceptance` and clears any
inherited `AGENT_GUARD_*` variables, so the audit trail is attributable.

## Outputs

| File | Contents |
|---|---|
| `events.jsonl` | one record per subprocess call: `cwd`, `argv`, `exit`, `stdout`, `stderr`, `elapsed_s` |
| `summary.json` | `{"passed": N, "checks": [...], "commands": N}` |

## Scope and limits

BLOCK and ASK commands are submitted as data to `check.py`; they are never
executed. The fixture `git reset` and `git clean` run only after compensation
succeeded and the transaction was independently listed `RESTORABLE`. Permission
probes touch only newly created fixture files.

Passing this driver means the deliberate skill/CLI path works on the host it
ran on. It does not establish a Codex interception hook, an OS sandbox
boundary, or portability beyond the recorded environment (Android 16 / Termux /
PRoot Debian 13.6, aarch64).
