# Codex GPT-6 Astra High Local Evaluation

**Date:** 2026-09-19
**Baseline:** `de5a00e976f84947150ef68627c9d4baa104d49c` (`main`)
**Package version:** `0.1.1`; Git description: `v0.1.1-25-gde5a00e`
**Evaluator:** current Codex session, `gpt-6-astra`, `high` reasoning

## Assessment

PASS for the supported skill and explicit CLI workflow on this local
Linux ARM64 environment. All 249 existing Python tests, the DSH adapter
smoke test, the recovery demo, and 20 additional acceptance checks passed.
No implementation changes were needed.

This is a current-session acceptance run, not a fresh-agent blind evaluation
or a statistical comparison with GPT-5.6 Sol. The evaluator read the previous
report and the project skill before testing. It invoked the production CLI
from the current harness, using a deterministic disposable-fixture driver.
No nested Codex process, extra model invocation, or parallel evaluator was used.

## Environment and Harness Evidence

| Item | Observed value |
|---|---|
| Host context | Android 16 / Termux / PRoot Debian, as supplied by the user |
| Guest OS | Debian GNU/Linux 13.6 (trixie) |
| Kernel / architecture | `Linux 6.17.0-PRoot-Distro aarch64` |
| Effective user | Non-root, UID 1000 |
| Python | 3.13.5 |
| Node | v20.19.2 |
| Git | 2.47.3 |
| Active session harness | Codex CLI 0.154.0, `codex-tui`, source `cli` |
| Active session model / effort | `gpt-6-astra` / `high` |
| Active session sandbox / approval | `danger-full-access` / `never` |
| Separate `codex` command on PATH | `/usr/local/bin/codex`, version 0.147.0 |

The active harness, model, effort, sandbox, and approval values were read
from this session's metadata and latest turn context. The PATH executable's
version is different and was not used to identify or launch this evaluator.
Selected local configuration fields also specified `gpt-6-astra` and `high`.
No credentials or complete configuration files were included in the evidence.

Normal tool execution worked during this run. Earlier initial repository
inspection under `workspace-write` had returned exit 182 even for `pwd`;
the successful full-access run does not establish that sandbox issue is fixed.

## Verification Results

| Check | Result |
|---|---|
| `npm test`: Python unittest discovery | PASS, 249 tests, 95.686 seconds |
| `npm test`: DSH adapter runtime smoke | PASS, mocked host services |
| `bash demo.sh` | PASS, quarantine, discovery, and content recovery |
| Additional acceptance driver | PASS, 20 checks across 82 subprocess calls |
| `node --check adapters/dsh/lib/index.js` | PASS |
| `bash -n demo.sh` | PASS |
| Final whitespace / patch check | `git diff --check` passed |

The 20 acceptance checks covered:

1. Workspace-root deletion blocked with exit 2.
2. Git metadata deletion blocked with exit 2.
3. An outside-workspace sentinel protected from deletion.
4. An unresolved shell-variable target blocked.
5. An opaque shell glob blocked.
6. Force push blocked; no remote operation executed.
7. An indirect destructive shell command blocked.
8. A compound cwd-change/delete command returned ASK and exit 3, without execution.
9. Advisory BLOCK returned exit 0, distinguished from enforced blocking.
10. Tracked-file recovery matched SHA-256 and transitioned RESTORABLE to RESTORED.
11. Enforced directory deletion quarantined nonignored build output before ALLOW.
12. An ignored log remained recoverable instead of being treated as regenerable.
13. Ignored `node_modules` content was directly deleted through `safe_delete`.
14. Restore conflict preserved both new origin content and the quarantined original.
15. Git clean preserved Chinese, space, newline, tab, quote, and backslash filenames;
    all six files were hash-verified after recovery and an actual fixture `git clean`.
16. Git snapshot preceded an actual fixture reset; restore recovered the dirty
    tracked file by hash, and the stored stash was retained.
17. A read-only audit file blocked regenerable deletion before the target changed.
18. Preignored quarantine worked with read-only `.git/info` and exclude metadata.
19. Without a preexisting ignore rule, the same permission restriction blocked
    before moving the source or creating quarantine.
20. All generated audit/manifest JSONL parsed, quarantine stayed out of Git status,
    and the outside-workspace sentinel retained its original content.

BLOCK and ASK commands were only submitted as data to `check.py`; they were
not executed. The fixture reset and clean ran only after compensation succeeded
and the transaction was independently listed as RESTORABLE. Permission probes
changed only newly created fixture files and restored their permissions afterward.

## Scope and Limitations

- The previous Sol report used WSL2 and `workspace-write`; this run uses PRoot
  and full access. Their results are not a controlled model-only comparison.
- This run validates deliberate skill/CLI use. It does not prove automatic
  interception of every Codex shell tool invocation. The repository's DSH and
  Claude adapter tests do not establish a native Codex interception hook.
- POSIX permission fixtures exercise specific failure paths; they do not
  reproduce the Codex OS sandbox or make agent-guard a security boundary.
- DSH host integration was mocked, and Claude hooks were exercised by the
  existing conformance suite. Neither received a new live model session here.
- Native Windows cmd/PowerShell execution was not tested; dialect logic ran
  in the Python suite on Linux. Android shared storage was not exercised.
- npm emitted an ambient `globalignorefile` configuration warning. It did not
  affect test results, and global configuration was not changed.

For the distinction between sandbox enforcement and approval policy, see the
[official Codex approvals and security documentation](https://developers.openai.com/codex/agent-approvals-security),
retrieved during this run. The actual session metadata is the evidence for
this run's settings.

## Local Evidence and Repository State

The acceptance driver and its raw structured outputs remain at:

```text
/tmp/agent-guard-astra-high-20260919-HxRpvY/acceptance.py
/tmp/agent-guard-astra-high-20260919-HxRpvY/events.jsonl
/tmp/agent-guard-astra-high-20260919-HxRpvY/summary.json
```

That directory also retains the disposable repositories and recovery evidence.
It is local temporary storage, not a durable or portable evidence archive.
To repeat the driver, place it in a fresh empty temporary directory; its
fixture directory creation intentionally refuses an existing run directory.

The main worktree was clean before and after execution. This report is the
only project addition. No production dependencies, source code, host settings,
remote state, or early local checkout were changed. No commit or push was made.
