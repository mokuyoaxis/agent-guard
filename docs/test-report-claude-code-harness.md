# agent-guard Claude Code Harness Live-Test Report

**Date:** 2026-09-14
**Repository:** `apigogo/agent-guard`
**Baseline commit:** `90878d6` (`main`)
**Interception mode:** Claude Code `PreToolUse` hook → `adapters/claude/pre_tool_use.py` → `check.py --enforce`
**Harness:** Claude Code CLI **2.1.270** (npm `@anthropic-ai/claude-code`, native Linux x64 build)
**Model:** mocked — a scripted Anthropic Messages endpoint (`adapters/claude/harness/mock_anthropic_api.mjs`)
**Sandbox:** inherit-session (no additional backend)
**Platform:** Linux 5.4.241 (x86_64, container), Python 3.11.2, Node.js v24.21.0, git 2.39.5

## Executive summary

Two previously unverified claims are now closed.

1. **The `python3` hardcode is fixed and the failure is reproducible.**
   `adapters/claude/pre_tool_use.py` spawned the guard as `["python3", ...]`.
   On a host that provides only `python`, every interception failed closed —
   the guard was installed but *unusable*, and the failure was silent apart
   from a stderr line the model may ignore. The child is now spawned with
   `sys.executable`. The A/B below was run **inside the real harness** on an
   identical environment: pre-fix the hook exits 2 with
   `guard infrastructure error (fail-closed): [Errno 2] No such file or
   directory: 'python3'`; post-fix the same `rm -rf build` returns a clean
   `RELOCATE_TREE` allow and quarantines the tree. Two regression tests pin
   both the allow and the block path on a `PATH` without `python3`.

2. **The Claude Code adapter works end-to-end against a real harness
   session.** A real Claude Code CLI, driving a real Bash tool through a real
   project `.claude/settings.json` hook, hit RELOCATE, BLOCK, and ASK exactly
   as `adapters/claude/README.md` documents. RELOCATE emitted
   `permissionDecision: "allow"` and moved `build/` into
   `.agent-trash/`; BLOCK exited 2 and the guard's stderr reached the model
   as a hook error; ASK emitted `permissionDecision: "ask"` and, with no
   interactive approver, Claude Code declined the call and the command never
   ran. Each case left a matching `.agent-trash/audit.jsonl` record.

A control run (benign `git status`) confirms the fast path costs nothing:
no `.agent-trash/` is created and the hook stays silent.

**This is still not a security boundary.** The mock endpoint means the
*tool-selection* behaviour of a real model is out of scope here — what was
measured is the interception mechanism, which is model-independent. The two
upstream reports' "Claude Code live session at `v0.1.1`" item is advanced to
a real harness; a real-model forward test on Claude Code remains open.

## Scope and methodology

1. Full deterministic suite — `GIT_CONFIG_GLOBAL=/dev/null python3 -m
   unittest discover tests` — run as a non-root user, as CI does.
2. `node tests/test_dsh_adapter.mjs` — DSH adapter smoke, for regression.
3. `python3 -m unittest tests.test_conformance` — the adapter conformance
   suite, including the two new `python3`-free regression tests.
4. Real-harness scenarios, each in a fresh disposable git project containing
   tracked source (`src/main.py`), a regenerable `build/` tree, an ignored
   `app.log`, and an untracked, potentially valuable `NOTES.md`:

   - **RELOCATE** — `rm -rf build`
   - **BLOCK** — `rm -rf .`
   - **ASK** — `touch f && rm f`
   - **control** — `git status --short` (benign fast path)

5. The no-`python3` A/B: the same scenario re-run with the hook invoked as
   `python` and the CLI's `PATH` containing `python` but no `python3`,
   once with the pre-fix adapter and once with the fix.

Every scenario ran with the **unmodified real hook** and the real
`check.py --enforce`; nothing was stubbed on the agent-guard side. Only the
model was replaced. No repository source was modified during behavioural
testing, and no destructive probe touched anything outside a temp fixture.

### Why a mock model is the right instrument

Claude Code accepts any Anthropic-protocol endpoint via `ANTHROPIC_BASE_URL`
+ `ANTHROPIC_AUTH_TOKEN`. The subject under test is the interception path —
hook discovery, payload shape, guard invocation, exit-code semantics, and the
permission decision the harness acts on — none of which depend on which model
produced the `tool_use`. Scripting the turns makes each decision reachable
deterministically and removes network variance, at the cost of not measuring
model behaviour (see Limitations).

The mock advances its script **only when the client returns a `tool_result`**,
so one scripted turn equals one harness turn and the destructive command is
guaranteed to actually execute and be observed.

## Finding: the `python3` hardcode disabled the guard (fixed)

**Symptom.** `pre_tool_use.py` line 88 spawned the guard as
`["python3", CHECK, "--enforce", ...]`. `python3` is a POSIX convention;
Windows (and stripped container images) ship `python` only. Where `python3`
is absent, the `subprocess.run` raised, and the adapter's fail-closed handler
returned exit 2 with:

```
[agent-guard] guard infrastructure error (fail-closed): [Errno 2] No such file or directory: 'python3'
```

Every Bash command that matched the destructive prefilter was therefore
blocked — including commands the guard would have allowed. The guard's
recoverability promise was replaced by a blanket denial, and on Windows this
made the adapter non-functional (the same class of defect as the
`os.uname()` finding in the ZCode report).

**Reproduced inside the real harness.** With the pre-fix adapter, a
`PATH` exposing `python` but not `python3`, and `rm -rf build`:

```json
{
 "stdout": "",
 "stderr": "[agent-guard] guard infrastructure error (fail-closed): [Errno 2] No such file or directory: 'python3'\n",
 "exit_code": 2,
 "outcome": "error"
}
```

**Fix.** One line — spawn the guard with the interpreter already running the
adapter:

```diff
-            ["python3", CHECK, "--enforce", "--json", "--", command],
+            [sys.executable, CHECK, "--enforce", "--json", "--", command],
```

This is strictly more correct than resolving `python3` from `PATH`: the
guard runs under the *same* interpreter as the adapter, so an interpreter
mismatch cannot silently change core behaviour.

**Verified — same environment, post-fix:**

```json
{
 "stdout": "{\"hookSpecificOutput\": {\"hookEventName\": \"PreToolUse\", \"permissionDecision\": \"allow\", \"permissionDecisionReason\": \"[agent-guard] RELOCATE_TREE: compensated automatically (1 relocation(s)); restorable via txid\"}}\n",
 "stderr": "",
 "exit_code": 0,
 "outcome": "success"
}
```

and the matching audit record:

```json
{"command": "rm -rf build", "compensations": [{"moved": 1, "strategy": "relocate", "txid": "20260914-051016-2d3c9915"}], "event": "enforce-proceed", "guard_latency_ms": 1.8, "session": "49b2d3e4-6852-4b9f-99f7-19348071252c", "ts": "2026-09-14T05:10:16Z"}
```

**Regression coverage** (`tests/test_conformance.py`) drives the adapter with
a `PATH` that has `python` but no `python3` —
`test_adapter_spawns_guard_with_sys_executable` (allow + quarantine must
still happen) and `test_adapter_blocking_still_works_without_python3_on_path`
(hard boundary must still block, and must not report an infrastructure
error). Both fail on the pre-fix adapter with exactly the reported symptom and
pass after it — the red/green pair is what makes the fix load-bearing rather
than merely different.

## Results

### Deterministic release checks

| Check | Result |
|---|---|
| Python unit, CLI, policy, recovery, GC, conformance suite (non-root, as CI) | PASS, 86/86 |
| `tests/test_conformance` incl. new `python3`-free regression tests | PASS, 9/9 |
| Same suite on the pre-fix adapter | FAIL (2) — reproduces the issue |
| DSH adapter Node smoke test | PASS |
| Repository worktree after evaluation | Clean |

The `python3`-free tests inject a `PATH` containing both `python` **and**
`git`: the core shells out to `git` for workspace discovery and snapshot
work independently of the spawn fix, so removing it would test the wrong
thing.

### Real-harness interception matrix

| Scenario | Command | Hook exit | Hook output | Guard verdict observed | Executed? |
|---|---|---|---|---|---|
| RELOCATE | `rm -rf build` | 0 | `permissionDecision: "allow"` + `RELOCATE_TREE` | `RELOCATE_TREE` | yes, after quarantine |
| BLOCK | `rm -rf .` | 2 | stderr teaching message | `BLOCK_PROTECTED_PATH` | **no** |
| ASK | `touch f && rm f` | 0 | `permissionDecision: "ask"` + `COMPOUND_CREATE_DELETE` | `COMPOUND_CREATE_DELETE` | **no** (not approved) |
| control | `git status --short` | 0 | silent | none (prefilter fast path) | yes |

All four matched the documented mapping in `adapters/claude/README.md`.

### Evidence per decision

**RELOCATE — quarantined before the original command ran.**

```json
{"hook_name": "PreToolUse:Bash", "hook_event": "PreToolUse",
 "stdout": "{\"hookSpecificOutput\": {\"hookEventName\": \"PreToolUse\", \"permissionDecision\": \"allow\", \"permissionDecisionReason\": \"[agent-guard] RELOCATE_TREE: compensated automatically (1 relocation(s)); restorable via txid\"}}\n",
 "stderr": "", "exit_code": 0, "outcome": "success"}
```

`.agent-trash/audit.jsonl`:

```json
{"command": "rm -rf build", "compensations": [{"moved": 1, "strategy": "relocate", "txid": "20260914-050923-48f8e9a0"}], "event": "enforce-proceed", "guard_latency_ms": 2.3, "session": "a8c61691-4ec0-4151-b128-fd4c6b221a3d", "ts": "2026-09-14T05:09:23Z"}
```

`.agent-trash/manifest.jsonl` — durable intent precedes the move:

```json
{"meta": {"command": "rm -rf build", "tool": "check --enforce"}, "strategy": "relocate", "ts": "2026-09-14T05:09:23Z", "txid": "20260914-050923-48f8e9a0", "type": "tx-start"}
{"origin_path": "<run>/project/build", "raw": "build", "trash_path": "<run>/project/.agent-trash/20260914-050923-48f8e9a0/build", "ts": "2026-09-14T05:09:23Z", "txid": "20260914-050923-48f8e9a0", "type": "relocate-intent"}
{"origin_path": "<run>/project/build", "trash_path": "<run>/project/.agent-trash/20260914-050923-48f8e9a0/build", "ts": "2026-09-14T05:09:23Z", "txid": "20260914-050923-48f8e9a0", "type": "relocate"}
```

Post-state: `build/` absent from the workspace, `build/o.js` present under
`.agent-trash/20260914-050923-48f8e9a0/build/`. The full roundtrip closes:

```
$ python3 skills/delete-guard/scripts/restore.py 20260914-050923-48f8e9a0
restored: /tmp/<run>/project/build
$ cat build/o.js
artifact
```

**BLOCK — the model, not just the user, is told why.**

```json
{"hook_name": "PreToolUse:Bash", "hook_event": "PreToolUse", "stdout": "",
 "stderr": "[agent-guard] BLOCKED [BLOCK_PROTECTED_PATH] Target is the workspace root or git metadata. Hard boundary - not askable. (protected: workspace-root (.)) | Restate with explicit workspace-relative paths, or use the safe-delete flow. Do NOT circumvent the guard.\n",
 "exit_code": 2, "outcome": "error"}
```

Claude Code converted the exit-2 stderr into a tool error handed back to the
model, which is the teaching channel the adapter intends:

```
PreToolUse:Bash hook error: [python3 .../pre_tool_use.py]: [agent-guard] BLOCKED
[BLOCK_PROTECTED_PATH] ... Do NOT circumvent the guard.
```

`.agent-trash/audit.jsonl`:

```json
{"code": "BLOCK_PROTECTED_PATH", "command": "rm -rf .", "event": "enforce-block", "guard_latency_ms": 0.1, "reasons": ["protected: workspace-root (.)"], "session": "4ca9b011-3faa-4e41-b960-f1d4d17d6d16", "ts": "2026-09-14T05:09:33Z"}
```

The workspace afterwards is byte-for-byte intact — `src/`, `build/`,
`NOTES.md`, `app.log`, and `.git/` all still present. Nothing was deleted.

**ASK — escalated once, and nothing ran without approval.**

```json
{"hook_name": "PreToolUse:Bash", "hook_event": "PreToolUse",
 "stdout": "{\"hookSpecificOutput\": {\"hookEventName\": \"PreToolUse\", \"permissionDecision\": \"ask\", \"permissionDecisionReason\": \"[agent-guard] COMPOUND_CREATE_DELETE: This line creates files and then destroys them, so pre-execution compensation cannot see the targets. Allow once to run it as-is, or split the deletion into its own command.\"}}\n",
 "stderr": "", "exit_code": 0, "outcome": "success"}
```

Under `--permission-mode default` with no interactive approver available in
`-p` mode, Claude Code declined the call. The harness records the denial:

```json
{"permission_denials": [{"tool_name": "Bash", "tool_use_id": "toolu_d", "tool_input": {"command": "touch f && rm f"}}]}
```

and the tool result fed back to the model is the guard's explanation,
`is_error: true`. The strongest evidence is the filesystem: `f` was never
created — the compound command did **not** run. `.agent-trash/audit.jsonl`
records the escalation:

```json
{"code": "COMPOUND_CREATE_DELETE", "command": "touch f && rm f", "event": "ask", "reasons": ["this command line creates files before destroying them; pre-execution compensation cannot see targets that do not exist yet"], "session": "51b54dab-06f7-4758-9bed-027dbb495f74", "ts": "2026-09-14T05:09:39Z"}
```

Note the asymmetry the adapter documents held in practice: RELOCATE's reason
reached the user through `permissionDecisionReason`, while BLOCK's reached the
*model* through stderr.

**Control — the benign fast path stays free.**

`git status --short` produced a silent exit-0 hook and **no
`.agent-trash/` at all**: the regex prefilter short-circuits before any
Python process is spawned, so benign Bash traffic pays neither guard latency
nor audit writes. `ls -A` in the same session behaved identically.

### The no-`python3` A/B, inside the harness

| | pre-fix (`["python3", ...]`) | post-fix (`[sys.executable, ...]`) |
|---|---|---|
| Hook exit | 2 | 0 |
| Hook stderr | `guard infrastructure error (fail-closed): [Errno 2] No such file or directory: 'python3'` | *(empty)* |
| `permissionDecision` | — (never reached the guard) | `allow` / `RELOCATE_TREE` |
| `build/` after run | untouched, command refused | quarantined, `.agent-trash/` audited |
| Guard usable? | **no** | yes |

Both runs used the same harness, the same settings file (hook invoked as
`python`), the same `PATH` directory, and the same scripted command. Only
the adapter line differs.

## Reproduction

The harness ships with the repository so the run is repeatable rather than
merely described.

```bash
npm install -g @anthropic-ai/claude-code        # or export CLAUDE_BIN=...

# RELOCATE / BLOCK / ASK / control
bash adapters/claude/harness/run_scenario.sh relocate "rm -rf build" 8901
bash adapters/claude/harness/run_scenario.sh block    "rm -rf ."     8903
bash adapters/claude/harness/run_scenario.sh ask      "touch f && rm f" 8904 default
bash adapters/claude/harness/run_scenario.sh control  "git status --short" 8905

# the Windows / no-python3 shape
bash adapters/claude/harness/run_scenario_no_python3.sh nopy3_fixed "rm -rf build" 8908
```

Each run writes `script.json`, `transcript.jsonl` (with
`--include-hook-events`, so hook stdout/stderr/exit codes are recorded),
`requests.jsonl`, and the resulting `project/` including `.agent-trash/`.
See `adapters/claude/harness/README.md`.

## Findings about the harness (not agent-guard)

- **`PreToolUse` hooks fire in `-p` (non-interactive) mode without a
  workspace-trust step.** Unlike the ZCode finding in
  `docs/test-report-zcode-glm-flash.md`, no trust prompt gated the hook here:
  with `.claude/settings.json` in place, project hooks loaded at session
  start and intercepted on the first matching Bash call. Non-interactive runs
  skip the trust dialog by design.
- **`--dangerously-skip-permissions` cannot be combined with running as
  root**, so the non-interactive runs used
  `--permission-mode dontAsk` (RELOCATE/BLOCK) or `default` (ASK). The
  guard's decision is emitted before the harness's own permission layer
  resolves, and was unaffected by the mode.
- **An ASK that nobody approves is a deny — and that is fail-safe.** With no
  interactive approver the command did not run. That is the correct outcome
  for a headless harness, but it means ASK in `-p` mode is not an
  "interactive prompt"; operators get a denial unless they wire up an
  approver. Worth stating in the adapter README.
- Claude Code validates the adapter's `hookSpecificOutput` schema strictly,
  exactly as ZCode did; no schema-strictness issue was found.

## Limitations

- **The model is mocked.** Tool-selection behaviour (does a real model reach
  for `rm -rf .` at all, does it retry after a BLOCK, does it argue with an
  ASK) is out of scope. This report is evidence about the *interception
  mechanism* on a real Claude Code build; it is not a Claude Code forward
  test. The upstream "Claude Code live session" item is advanced, not closed.
- **Single harness build.** Claude Code 2.1.270 on Linux x64 only. Hook
  semantics are the harness's contract and could change between builds.
- **No hostile-agent testing.** Consistent with `docs/threat-model.md`, this
  is mistake protection, not a privilege boundary.
- The no-`python3` A/B uses a synthetic `PATH` (symlinks) rather than a
  genuine Windows host. It reproduces the failure mode exactly — the missing
  executable — because that is what the defect keys on, but it is not a
  Windows run. Windows remains out of V1 scope.
- RELOCATE's three-decision sample is one command per decision class;
  `tests/test_conformance.py` remains the broad matrix.

## Relationship to prior history

- **ZCode/GLM Flash report** (`docs/test-report-zcode-glm-flash.md`): its
  `os.uname()` finding is the same *shape* of defect as this report's
  `python3` hardcode — a POSIX-only assumption that fail-closed into total
  guard unavailability on Windows. Both are one-line platform fixes; both
  were invisible to the decision matrix because the fixtures always had the
  expected environment. Its "Claude Code integration" open item is advanced
  here to a real Claude Code build.
- **DSH v0.1.1 report** (`docs/test-report-dsh-v0.1.1.md`): same decision
  matrix methodology and the same "remaining matrix expansion → Claude Code
  live sessions" item, now partially closed.
- **Codex report** (`docs/test-report-codex-gpt-5.6-sol.md`): its fail-closed
  compensation contract is unrelated to this defect but shares the principle —
  a guard that cannot do its job must refuse rather than pretend. Here the
  refusal was correct in isolation and catastrophic in aggregate, which is why
  the regression tests assert *success*, not merely *an exit code*.

## Remaining matrix expansion

- Claude Code with a **real** model family (forward test through the same
  harness).
- A real Windows host running the adapter under Git Bash / `python`.
- Interactive Claude Code session, to observe ASK actually prompting a human
  and to check whether Claude Code's own "always allow" rule can short-circuit
  the hook the way ZCode's does.
- Additional Claude Code builds as the hook schema evolves.

## Conclusion

The `python3` hardcode was a real, reproducible, release-blocking defect on
any host without a `python3` alias: it turned the guard from a recovery
system into a blanket denial while reporting only a stderr line. Spawning the
guard with `sys.executable` fixes it at the source, and two regression tests
keep it fixed on both the allow and block paths.

With that fix in place the Claude Code adapter is validated end-to-end against
a real Claude Code 2.1.270 session: RELOCATE quarantined and allowed, BLOCK
refused and taught the model why, ASK escalated and executed nothing without
approval — each with matching audit records and a control run showing the
benign path stays free. The harness ships in
`adapters/claude/harness/` so the result can be re-derived.

## Post-test note (2026-09-14)

*Added after the report above was finalised; the dated evidence in the body
is unchanged.*

**Windows-native shell support has since landed.** The Limitations section
recorded "Windows remains out of V1 scope" because that was the repository
state when these scenarios ran. The dialect layer was merged afterwards, in
two phases:

- **MR !3 / !6 (`core/dialects.py`)** - cmd and PowerShell lexical front ends
  that map Windows-native vocabulary (`ri`/`rd`/`del`/`Remove-Item`,
  `-Recurse`/`-Force`, PowerShell short-parameter prefixes) onto the same
  effect facts the POSIX lexer produces. `check.py` gained `--dialect`, the
  hook forwards a dialect, and the prefilter follows it.
- The differential and phase-2 suites (`tests/test_dialects.py`,
  `tests/test_dialect_phase2.py`) now cover what the earlier limitation
  described, so that sentence is superseded as a statement of repository
  capability.

**Still open.** A real Windows HOST running the adapter end to end - Git Bash
or `cmd`, a stock `python` (no `python3` alias), the hook wired into
`.claude/settings.json` - remains an open item, and this report does not
claim otherwise. What changed is the reason: the gap is no longer "Windows is
out of scope" but "the dialect layer is covered by unit and conformance
tests, not yet by a live run on Windows". The `python3`-free A/B earlier in
this report still stands as the closest available approximation of that host.

Note also that the symlink-ancestor path fixes delivered later (F9/F9b in
macOS CI) are outside this report's scope; see
`tests/test_workspace_symlink.py`.
