# agent-guard architecture

## Positioning

agent-guard is **Agent Reliability Infrastructure**: it lowers the
probability that an autonomous agent causes irreversible loss through a
misjudged deletion, a mis-expanded command, or mistaken context - and it
leaves a recovery path and an evidence trail when anything destructive does
happen. It is explicitly *not* a sandbox or security boundary (see
`threat-model.md`).

## The four pillars and where they live

| Pillar | Question it answers | Component |
|---|---|---|
| Scope | Where may the agent act? | `core/classifier.py` (boundary facts) + `core/policy.py` (rules 3-6) |
| Recoverability | If wrong, how do we come back? | `core/recovery.py` (relocate / snapshot / restore) |
| Authorization | Who decides what? | `core/policy.py` (mode state machine) |
| Auditability | What actually happened? | `core/audit.py` + `.agent-trash/*.jsonl` |

A fifth, cross-cutting rule governs all four:

> **Uncertainty increases restriction** (fail closed).

Anything the classifier cannot resolve statically - shell variables, command
substitution, unbalanced quotes, indirect shells, stdin-fed target lists -
becomes a BLOCK verdict, never a guess.

## Data flow

```
shell command (from any harness)
        │
        ▼
[harness adapter]  ── fast prefilter: destructive keywords present? ──no──▶ run unchanged
        │ yes
        ▼
scripts/check.py --enforce -- <command>
        │
        ▼
classifier.classify_command(dialect=posix|cmd|powershell)
        │                        │
        │        dialects.py ────┘  lex + map vocabulary onto shared effect facts
        ▼
[OpSpec...]                                      facts, not decisions
        │
        ▼
policy.decide_ops ──▶ [Verdict...]               ALLOW / RELOCATE / COMPENSATE / BLOCK
        │                     │
        │              any BLOCK? ──▶ refuse, audit, exit 2
        ▼ no
recovery.RecoveryEngine                Compensation Strategy
        │                              ├─ relocate   (fs targets → .agent-trash/<txid>/)
        │                              ├─ snapshot   (git stash create+store, tree untouched)
        │                              └─ clean-enum (git clean -n → relocate matches)
        ▼
audit.append (JSONL, one line per decision)
        │
        ▼
PROCEED with txids ──▶ original command runs
```

Explicit-path tools skip the shell parsing front half:
`safe_delete.py PATH... → classify_paths → decide_path_batch → relocate`.

## The Decision Protocol

The stable cross-harness interface is not allow/block:

```
Effect -> Classifier -> Policy -> Decision  ∈ {ALLOW, RELOCATE, SNAPSHOT,
                                                ASK, BLOCK}
                                 + ReasonCode   (stable, machine-readable)
                                 + Explanation  (human-facing)
                                 + RecoveryPlan (payload: txids, strategy)
```

### Command dialects

The same effect vocabulary does not exist on native Windows shells, and
neither do the lexical rules. `core/dialects.py` isolates both concerns:

```
classify_command(cmd, dialect="posix")
    posix        -> shlex + heredoc stripping        (default; unchanged)
    cmd          -> caret escapes, & separators, no
                    backslash escapes in quotes, del/erase/rd/rmdir, /s /q
    powershell   -> '' literal quoting, ` escapes, $() kept verbatim,
                    Remove-Item + del/erase/rd/rmdir/rm/ri alias subset
```

### Dialect selection in production

The dialect is selected by, in precedence order:

1. `--dialect {posix,cmd,powershell}` on `check.py`;
2. the hook payload / tool arguments (`dialect`, `shell_dialect`);
3. `AGENT_GUARD_DIALECT`;
4. `posix` (the default).

Both adapters forward the *requested* selector verbatim rather than a
resolved value, so `check.py` owns the single verdict for an unusable
selector. Adapters also pick their cheap prefilter from the dialect: the
POSIX regex cannot see `ri build -r -fo`, so a Windows session would
otherwise skip the guard entirely. The POSIX prefilter is untouched, so
the default path keeps its exact behaviour and cost.

Four rules keep the layer honest:

* **The default dialect is POSIX.** Every existing caller keeps its
  exact behaviour; opting in is explicit.
* **An unknown dialect name is never a silent POSIX fallback.** The
  library raises/returns unusable; the CLI and adapters turn that into
  `BLOCK_DIALECT_UNKNOWN` (name not recognised) or
  `BLOCK_DIALECT_INVALID` (malformed selector). BLOCK, not ASK: a bad
  selector recurs on every command, so it is a configuration defect rather
  than a per-execution authorization.
* **Unresolvable Windows lexemes fail closed.** `%VAR%`, `$var`, `$(...)`,
  piped target sets, `-LiteralPath`, `-Include`/`-Exclude`/`-Filter`,
  interactive `-Confirm`, unknown switches and nested hosts
  (`powershell -Command "..."`) all become undeterminable facts -> BLOCK.
  `-WhatIf:$true` is a dry run (ALLOW); `-WhatIf:$false` is a real delete;
  a *variable* switch value blocks.
* **Abbreviations expand only when unambiguous.** PowerShell resolves
  `-r`/`-rec` to `-Recurse` and `-fo` to `-Force`, and the guard follows so
  a recursive forced delete is not read as a mild one. A prefix matching
  two different effects (`-wi` -> `-WhatIf` / `-WarningAction`; `-c`, `-p`)
  is left unexpanded and BLOCKs as an unknown parameter.

Adapters map decisions to native mechanisms - DSH `PreToolDecision`,
Claude Code PreToolUse `ask`, or, on harnesses without ask support, a deny
that carries the explanation (never a silent allow). Harness capability
thus never pollutes policy.

Architecture invariant (B1): **the guard analyzes the shell command's
direct effect; it does not infer the internal behavior of arbitrary
programs.** `npm run build && rm -rf dist` is invisible to creation
analysis by design - chasing program-internal effects would degrade the
classifier into a poor shell program analyzer.

Interaction tiers: SAFE (auto-execute, silent) / AMBIGUOUS (ASK_ONCE) /
FORBIDDEN (BLOCK, never askable).

## Key design decisions

1. **Effect-oriented, dialect as a front end.** The classifier recognizes a
   concrete vocabulary per *dialect* (POSIX: rm family, find -delete, git
   clean/reset/restore/checkout/push; cmd: del/erase/rd/rmdir; PowerShell:
   Remove-Item and its alias subset) but classifies by resulting effect on
   data. Dialects only lex and map vocabulary onto the same effect facts -
   policy, compensation and audit are shared verbatim. Adapters never
   re-implement rules; they call `check.py`, so there is exactly one rule
   engine across every harness and every dialect.
2. **Enumerate-then-act.** Opaque target sets (globs) are refused at the
   shell layer; the explicit tool expands globs itself first. Opacity is
   converted into explicitness instead of being banned outright.
3. **Two compensation strategies, one interface.** Filesystem deletions
   *relocate* (the file still exists elsewhere); content-overwriting git ops
   *snapshot* (`stash create` without touching the tree). Both produce a
   txid recorded in manifest + audit. Future compensations (database
   backup/PITR, cloud snapshot) plug into the same slot.
4. **Restore is non-destructive.** It refuses to overwrite existing origin
   paths; `--force` is an explicit human decision. A recovery tool that can
   clobber would be a second destruction vector.
5. **Lexical boundaries.** Workspace containment is decided on normalized
   paths; deleting a symlink removes the link, never the target, so symlink
   targets outside the workspace neither leak nor block.
6. **Self-exclusion.** Targets inside `.agent-trash/` are exempted from
   quarantine - housekeeping cannot recurse into itself forever.

## Harness adapter model

Any agent harness integrates through three optional points, in increasing
order of value:

1. **Interception hook** (recommended): before executing a shell command,
   run `check.py --enforce -- <command>`; proceed on exit 0, refuse on
   exit 2. This is the DSH plugin's `tools/pre-execute` listener today.
2. **Agent-facing tools**: expose `safe_delete` / `restore` / `status` as
   model tools so the supported path is also the easiest path.
3. **Prompt section**: inject a short instruction pointing the model at the
   skill and the "prefer safe_delete" rule. Interception without prompting
   causes friction; prompting without interception is advisory only.

## Roadmap

- V1 (this repo): delete-guard skill, fs + git compensations, Linux/macOS.
- v0.1.1: durable relocation intents, explicit retention/GC policy, quoted
  Git-path safety, fail-closed compensation, clean audit preflight,
  transaction lifecycle state, and adapter smoke coverage.
- V1.x: broader model/harness conformance matrix.
  - **Windows shell dialects - Phase 1 (done, `core/dialects.py`).** Pure
    logic: cmd and PowerShell lexers, destructive-vocabulary mapping,
    alias subset, `-WhatIf`/`-Recurse`/`-Force`/`-Confirm` semantics, and
    fail-closed handling of variables, subexpressions, piped target sets
    and nested hosts. Unit-tested on Linux CI; POSIX stays the default
    dialect so no existing adapter changes behaviour.
  - **Phase 2 (not started, needs a real Windows host).** End-to-end
    validation: real cmd/PowerShell execution, relocation across Windows
    path semantics, adapter wiring that selects the dialect from the host
    shell, and the Windows portability gaps reported in
    `test-report-zcode-glm-flash.md`.
- V2: `git-guard` skill (remote ref protection with lease semantics);
  adapter hardening (host-side mode storage, tamper-evident audit).
- V3+: `database-guard` (compensations = transaction / backup /
  point-in-time recovery), `cloud-guard` (snapshot / state capture). The
  Guard answers "may this happen"; the Compensation Engine answers "how do
  we come back" - both extend without restructuring the repository.
