# agent-guard architecture

## Positioning

agent-guard is **Agent Reliability Infrastructure**: it lowers the
probability that an autonomous agent causes irreversible loss through a
misjudged deletion, a mis-expanded command, or mistaken context - and it
leaves a recovery path and an evidence trail when anything destructive does
happen. It is explicitly *not* a sandbox or security boundary (see
`threat-model.md`).

The shared Core, Decision Protocol, and Skills are the product boundary.
Harness adapters are replaceable translations onto host-native hooks; DSH,
Claude Code, Codex, or any future host is a compatibility target rather than
the identity of the project.

## The four pillars and where they live

| Pillar | Question it answers | Component |
|---|---|---|
| Scope | Where may the agent act? | `core/classifier.py` (boundary facts) + `core/policy.py` (rules 3-6) |
| Recoverability | If wrong, how do we come back? | `core/recovery.py` (relocate / snapshot / restore) |
| Authorization | Who decides what? | `core/policy.py` (mode state machine) |
| Auditability | What actually happened? | `core/audit.py` + `.agent-trash/*.jsonl` |

The 0.2.5-rc2 source candidate adds a shared
[audit metadata projection](../guides/audit-metadata.md) on append and legacy
read, with opaque session correlation. Recovery manifests retain exact paths;
authorization state and required audit durability remain separate contracts.

A fifth, cross-cutting rule governs all four:

> **Uncertainty increases restriction** (fail closed).

Anything the classifier cannot resolve statically - shell variables, command
substitution, unbalanced quotes, indirect shells, stdin-fed target lists -
becomes a BLOCK verdict, never a guess.

## Data flow: supported delete-guard shell path

This diagram shows a supported native adapter path. An explicit CLI caller
joins at `check.py`, skipping the adapter prefilter. Neither path implies that
every harness or tool call is intercepted. Exfil-guard's cooperative text CLI
is separate; see [harness capabilities](../guides/harness-capabilities.md).

```
shell command (from a supported native adapter)
        │
        ▼
[harness adapter]  ── fast prefilter: destructive keywords present? ──no──▶ run unchanged
        │ yes
        ▼
skills/delete-guard/scripts/check.py --enforce -- <command>
        │
        ▼
classifier.classify_command(dialect=posix|cmd|powershell)
        │                        │
        │        dialects.py ────┘  lex + map vocabulary onto shared effect facts
        ▼
[OpSpec...]                                      facts, not decisions
        │
        ▼
policy.decide_ops ──▶ [Verdict...]               ALLOW / RELOCATE / SNAPSHOT / ASK / BLOCK
        │                     │
        │              BLOCK or ASK? ──▶ return 2 or 3; no mutation
        ▼ no
recovery.RecoveryEngine                Compensation Strategy
        │                              ├─ relocate   (fs targets → .agent-trash/<txid>/)
        │                              ├─ snapshot   (git stash create+store, tree untouched)
        │                              └─ clean-enum (git clean -n → relocate matches)
        ▼
audit.append (JSONL, one line per decision)
        │
        ▼
PROCEED with txids ──▶ host may run the original command
```

Explicit-path tools skip the shell parsing front half:
`safe_delete.py PATH... → classify_paths → decide_path_batch → relocate`.

## guard-lab evidence flow

`guard-lab` is a diagnostic plane beside the enforcement Core. It does not
change policy verdicts and is deliberately not installed as a Skill for the
tested agent.

```text
user controller ── prepare ──▶ disposable fixture ──▶ tested agent/harness
       │                         │       │
       │                         │       ├─ synthetic canary data
       │                         │       ├─ loopback bait URL
       │                         │       ├─ harmless fake Lab stub
       │                         │       └─ bounded snapshot test double
       │                         │
       └─ private evidence ◀─────┴──── observer / host result / output scan
                 │
                 └─ control hash + chained events + health check ──▶ L0/L1 report
                                                                    │
                          matched guard-off / guard-on reports ─────┴─▶ L2 compare
```

The fixture and evidence directory must be disjoint. Only the fixture is shown
to the tested agent. The observer binds to loopback for a bounded interval;
selected output scanning happens after it stops so two writers cannot race the
event chain. Missing controls, changed control data, malformed event chains, or
an unfinished observer yield `INCONCLUSIVE` rather than a guessed pass/fail.
Control reports always use `security_outcome=CALIBRATION_ONLY`. The manual
`injection-probe` reverses the positive-control interpretation: a bait hit is
`EXPOSURE_OBSERVED` and a failing criterion. Its comparison layer requires an
effective guard-off baseline plus matching protocol, harness/version/model,
trial group, and task hash before it can label a guard-on difference L2.
Every user-declared real-model run also needs one `HOST_RESULT=COMPLETED` event.
Process exit zero alone is insufficient because a harness may encode model or
configuration failure in its stream while exiting normally.
Other host settings are not bound by the evidence format; the experimenter
must hold them constant and record how that equality was checked.

The deterministic snapshot control submits an in-memory tar containing four
synthetic source classes to an authenticated IPv4-loopback route. The sink
validates and discards it, retaining only hashes, sizes, stages, and bait IDs.
This calibrates archive/sink evidence; it does not provide general file-read
telemetry, prove an external upload, or show that a PreToolUse hook can mediate
a harness-owned background sidecar.

This layout separates accidental access in the intended workflow, not OS
privileges. A same-UID adversary can inspect or alter both sides. See
[guard-lab](../lab/guard-lab.md) for the evidence semantics and unsupported channels.

## Opt-in DSH text-read boundary

The [experimental read prototype](../reports/test-report-dsh-read-redaction.md) adds a
separate post-execute path for reviewed, artifact-pinned native text-read
contracts (local FS and DSH `0.2.0-rc.2` sandbox FS):
native complete read value → bounded Python stdin worker → existing Core
scan/policy/plan → native value replacement → regenerated content and meta.
The original filesystem access remains owned by DSH. The worker is a trusted
local controller subprocess and reads no target file. The default bundle and
reviewed deletion-only Lab profiles keep this path disabled.

Native validation uses the actual read, AgentLoop and JSONL backend with a
deterministic synthetic stream; it checks next-request content and durable
metadata too; the [current review](../reports/test-report-release-readiness-0.2.3.md)
records both new-host providers. Pagination, custom finalizers/unreviewed providers, PTC, other tools and
events before this hook remain outside this boundary. No real-model L2
evidence follows from the zero-model probe.

## The Decision Protocol

The stable cross-harness interface is not allow/block:

```
Effect -> Classifier -> Policy -> Decision  ∈ {ALLOW, SANITIZE, RELOCATE,
                                                SNAPSHOT, ASK, BLOCK}
                                 + ReasonCode   (stable, machine-readable)
                                 + Explanation  (human-facing)
                                 + RecoveryPlan (payload: txids, strategy)
```

### Command dialects

The same effect vocabulary does not exist on native Windows shells, and
neither do the lexical rules. `core/dialects.py` isolates both concerns:

```
classify_command(cmd, dialect="posix")
    posix        -> shlex + heredoc stripping        (default lexer)
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
selector. Their cheap prefilter screens both POSIX and Windows vocabularies:
the selector chooses the lexer, not whether a destructive-looking command
reaches Core at all.

Four rules keep the layer honest:

* **The default lexer is POSIX.** Existing POSIX syntax keeps POSIX quoting
  and separator rules. The cross-vocabulary safety floor is deliberately
  additive: a known destructive command that previously vanished on a
  mismatch now blocks instead of becoming `ALLOW_NOOP`.
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
* **Known destructive vocabulary cannot disappear on a dialect mismatch.**
  The selected tokenizer defines command boundaries. If one of those
  segments contains a supported destructive command shape but its dialect
  classifier produced no operation, Core emits a target-free `UNKNOWN` fact
  and blocks. It never reuses targets or flags guessed under another shell
  grammar, and it does not scan ordinary argument text for dangerous words.
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
   Hard reset first compares its target tree with untracked and ignored
   paths. Collisions are blocked because the tracked-only stash cannot
   protect them; no automatic untracked archive is created in this version.
4. **Restore is non-destructive.** It refuses to overwrite existing origin
   paths; `--force` is an explicit human decision. Relocation paths are checked
   as a batch before mutation and again before each item: destination parent
   chains must stay inside the selected workspace, and source parent chains
   inside their own quarantine transaction. IDs cannot contain path separators
   or traversal; transaction directories cannot redirect to another location.
   The final component is kept unresolved so restoring a symlink restores the
   link. `--force` cannot bypass either boundary check. Before replacing an
   occupant, it uses the existing relocation journal to preserve that version
   in a separate transaction. Preservation failure stops that item; the result
   lists preservation attempts in optional `backup_txids`. Quarantine roots,
   aliases and ancestor paths cannot themselves be moved for preservation.
   This is not an atomic restore: concurrent changes and cross-filesystem
   failures may leave a partial destination, so inspect the reported paths
   and transaction state before retrying.
5. **Lexical boundaries.** Workspace containment is decided on normalized
   paths; deleting a symlink removes the link, never the target, so symlink
   targets outside the workspace neither leak nor block.
6. **Self-exclusion with protected controls.** Ordinary unprotected payloads
   inside the selected quarantine use housekeeping rather than relocation.
   Protection introduced in 0.2.5-rc1 checks root/control paths and storage parents first;
   housekeeping cannot override protection. Opaque globs must be enumerated
   explicitly, and validated transaction purge uses the GC CLI.
7. **Diagnostics do not silently become enforcement.** `guard-lab` records
   declared synthetic channels and evaluates one case criterion. It does not
   feed a Lab observation into Core policy or claim to ban model calls. A real
   block would require a host hook or request gateway that owns the call.

## Read-only quarantine queries

core/trash_index.py exposes list_trash_locations for CLI, agent and future
trusted local frontend callers. status.py --trash-index supplies explicit
roots or declared buckets and returns schema version 1. This branch executes
before ordinary status accounting: no RecoveryEngine, Git, audit writes,
payload traversal or GC planning. Discovery is bounded, prunes quarantine
subtrees and deduplicates physical addresses. Metadata presence is a layout
hint; location results are not authorization for later restore or purge.

## Harness adapter model

Any agent harness integrates through three optional points, in increasing
order of value:

1. **Interception hook** (recommended): before executing a shell command,
   run `check.py --enforce -- <command>`; proceed on exit 0, refuse on
   exit 2, and map exit 3 only when the host has a real human-approval path.
   Existing adapters translate this contract onto their host-specific hook.
2. **Agent-facing tools**: expose `safe_delete` / `restore` / `status` as
   model tools so the supported path is also the easiest path.
3. **Prompt section**: inject a short instruction pointing the model at the
   skill and the "prefer safe_delete" rule. Interception without prompting
   causes friction; prompting without interception is advisory only.

## Delivery status and design directions

The current RC source is 0.2.5-rc2, with publication checked by its receipts;
stable 0.2.4 and the previous prerelease 0.2.5-rc1 remain baselines. It retains RC1 maintenance and adds
shared audit metadata projection plus observer startup diagnosis/cancellation.
See [candidate notes](../releases/release-notes-0.2.5-rc2.md) and
[compatibility](compatibility.md) for behaviour and validation boundaries.

The milestones below describe historical delivery and future design scope,
not a committed schedule or a guarantee that new compensation types need no
architecture changes.

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
    lexical grammar. Since `0.2.3-rc1`, the shared safety floor can turn a
    previously invisible cross-vocabulary mismatch into a target-free
    refusal without guessing another shell's targets.
  - **Phase 2 (done at the pure-logic/adapter-test level).** Differential
    and regression suites (`tests/test_dialects.py`,
    `tests/test_dialect_phase2.py`) cover further cmd/PowerShell spellings,
    option aliases and fail-closed edge cases. Hook payload dialect forwarding
    has adapter tests; this is not a Windows host execution claim.
  - **Phase 3a (shipped since `0.2.3-rc1`).** A blocking `windows-latest` Core
    job covers the Issue #7 dialect-mismatch and separator regressions,
    deterministic cmd/PowerShell facts, and recovery inside disposable
    fixtures. The job uses Python argv/bytes so a surrounding shell cannot
    rewrite the evidence.
  - **Phase 3 remains incomplete.** Real cmd/PowerShell execution through
    each harness, host-selected dialect wiring, privileged symlink cases,
    broader UNC/device paths and full-suite portability still require
    separate evidence. A focused Core runner is not a universal Windows E2E
    claim.
- Offline `guard-lab` MVP, introduced in `0.2.3-rc2` and shipped in `0.2.3`: disposable
  synthetic fixtures, zero-token controls, a bounded loopback observer,
  explicit-output marker scanning, chained evidence, and fail-inconclusive
  reports. General file-read visibility, remote callbacks, real-model rating,
  and universal model-call blocking are deliberately outside this MVP.
- V2: `git-guard` skill (remote ref protection with lease semantics);
  adapter hardening (host-side mode storage, tamper-evident audit).
- V3+: `database-guard` (compensations = transaction / backup /
  point-in-time recovery), `cloud-guard` (snapshot / state capture). The
  Guard answers "may this happen"; the Compensation Engine answers "how do
  we come back". These remain design directions requiring separate scope and
  implementation decisions.
