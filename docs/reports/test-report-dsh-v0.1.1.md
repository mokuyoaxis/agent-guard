# agent-guard DSH Live-Test Report — v0.1.1

**Date:** 2026-08-29
**Repository:** `mokuyoaxis/agent-guard`
**Commit under test:** `173edd8903023d13ba517ce0953570228a819bfe` (`release: harden agent-guard for v0.1.1`)
**Version under test:** `v0.1.1` (`package.json`)
**Harness:** DeepSeek Harness (DSH), minimal-mode composition
**Model:** DeepSeek V4 Pro, `high` reasoning effort
**Interception mode:** `tools/pre-execute` waterfall → `check.py --enforce`
**Sandbox:** inherit-session (minimal mode carries no additional sandbox backend)
**Platform:** Linux (Ubuntu 22.04), Python 3.10.12, Node.js v22.23.2, git 2.34.1

## Executive summary

agent-guard `v0.1.1` passed a live DSH minimal-mode run with DeepSeek V4 Pro
`high` reasoning. The deterministic release checks, the 60-second recovery
demo, a real-shell simulation of the DSH `tools/pre-execute` interception, and
the three registered model tools all behaved as specified. Hard boundaries
blocked, compound shapes escalated to ASK, rooted deletions were quarantined
before the original command ran, and `git reset --hard` snapshotted tracked
modifications when a commit existed and failed closed when no commit existed.

Combined with the earlier DSH `v0.1.0` live session (see `docs/history/friction.md`,
F1–F11) and the Codex `gpt-5.6-sol` paired medium/high evaluation (see
`docs/reports/test-report-codex-gpt-5.6-sol.md`), the DSH runtime-protection item left
open in that report is now closed. The validation matrix remains deliberately
narrow — one model family per harness — so this is evidence of runtime
protection quality in minimal mode, not a hostile-agent security boundary.

## Scope and methodology

The run exercised the adapter exactly as a minimal DSH composition would
expose it:

1. `python3 -m unittest discover tests -v` — full core, CLI, policy, recovery,
   GC, and cross-harness conformance suite.
2. `node tests/test_dsh_adapter.mjs` — DSH adapter import, `apply()`,
   registration of the three model tools, prompt section, pre-execute handler
   mapping, and mocked Cordis service execution.
3. `./demo.sh` — 60-second quarantine → inspect → restore roundtrip.
4. A real-shell DSH minimal-mode smoke: the published `adapters/dsh/lib/index.js`
   loaded with a `defineTool` shim, a real `bash -lc`-backed `shell` service
   (inherit-session, no sandbox backend), and an isolated git workspace. The
   pre-execute handler then processed real commands end-to-end:
   benign `git status`, hard block `rm -rf .`, compensated `rm -rf build/`,
   and ASK shape `touch f && rm f`; the `agent_guard_safe_delete` and
   `agent_guard_status` tools executed against the real Python core.
5. A `check.py --enforce` decision matrix over the documented shapes:
   `git status`, `rm -rf .`, `rm -rf $DIR/`, `rm *.log`,
   `cd build && rm -rf .`, `touch f && rm f`, `git push --force`,
   `rm -rf build/`, `git clean -fd`, and `git reset --hard` with and without
   an initial commit.

No source files were modified during behavioral testing. All destructive
probes operated on disposable fixtures under `/tmp`.

## Results

### Deterministic release checks

| Check | Result |
|---|---|
| Python unit, CLI, policy, recovery, GC, and Claude conformance tests | PASS, 84/84 |
| DSH adapter Node smoke test | PASS |
| `node --check adapters/dsh/lib/index.js` | PASS |
| 60-second quarantine-and-restore demo | PASS, content recovered verbatim |
| Real-shell DSH minimal-mode smoke | PASS |
| Repository worktree after evaluation | Clean |

### DSH minimal-mode interception matrix (real shell)

| Command | Observed decision | Expected | Result |
|---|---|---|---|
| `git status` | `continued` (benign fast path, no Python spawn) | ALLOW | ✓ |
| `rm -rf .` | `deny` · `BLOCK_PROTECTED_PATH` | BLOCK | ✓ |
| `rm -rf build/` | `continued` after relocation; `build/` absent, txid logged | RELOCATE | ✓ |
| `touch f && rm f` | `ask` · `COMPOUND_CREATE_DELETE` | ASK | ✓ |
| `agent_guard_safe_delete(["keep.txt"])` | `ok` · verdict `RELOCATE` | RELOCATE | ✓ |
| `agent_guard_status()` | `ok` · mode `NORMAL` | status | ✓ |

### `check.py --enforce` decision matrix

| Command | Decision | Exit | Result |
|---|---|---|---|
| `git status` | `ALLOW_NOOP` | 0 | ✓ |
| `rm -rf .` | `BLOCK_PROTECTED_PATH` | 2 | ✓ |
| `rm -rf $DIR/` | `BLOCK_UNDETERMINABLE_EFFECT` | 2 | ✓ |
| `rm *.log` | `BLOCK_WILDCARD` | 2 | ✓ |
| `cd build && rm -rf .` | `ASK` · `COMPOUND_CWD_DELETE` | 3 | ✓ |
| `touch f && rm f` | `ASK` · `COMPOUND_CREATE_DELETE` | 3 | ✓ |
| `git push --force` | `BLOCK_FORCE_PUSH` | 2 | ✓ |
| `rm -rf build/` | `RELOCATE_TREE` | 0 | ✓ |
| `git clean -fd` | `RELOCATE_VIA_CLEAN_ENUMERATE` | 0 | ✓ |
| `git reset --hard` (with commit, dirty tree) | `SNAPSHOT_GIT_STASH` | 0 | ✓ |
| `git reset --hard` (no initial commit) | `COMPENSATION_FAILED` (fail-closed) | 2 | ✓ |

The empty-repository `git reset --hard` case is deliberately fail-closed:
snapshot compensation cannot be produced, so the command is blocked rather
than allowed with a false recovery promise. This matches the P0-3 remediation
verified in the Codex report.

## Relationship to prior history

- **DSH v0.1.0 live session** (`docs/history/friction.md`): F1–F11 were found live,
  fixed, and regression-pinned. The v0.1.1 run above re-verified the fixed
  behaviors: compound shapes now ASK instead of mis-resolving targets (F1/F2),
  heredoc bodies are payload (F5/F6/F7), workspace root resolves through
  symlinks (F8), and workdir context is honored (F9).
- **Codex v0.1.1 evaluation** (`docs/reports/test-report-codex-gpt-5.6-sol.md`):
  P0-1–P0-4, P1, and P2 fixes were the entry criteria for this run. The
  real-shell DSH minimal-mode smoke is the missing live-DSH verification item
  named in that report's "Remaining matrix expansion".

## Remaining matrix expansion

- Live DSH runs with additional model families and reasoning levels.
- Live DSH runs in non-minimal compositions (sandbox backends present).
- Claude Code and other harness live sessions at `v0.1.1`.
- Windows (cmd/PowerShell) remains out of V1 scope by design.

## Conclusion

`v0.1.1` is validated for DSH runtime protection in minimal mode with
DeepSeek V4 Pro `high` reasoning. The adapter delegates decisions to the
single shared Python core, blocks hard boundaries, escalates compound shapes,
and keeps supported destructive operations recoverable before execution.
