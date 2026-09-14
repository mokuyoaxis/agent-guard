# delete-guard policy reference (V1)

Platforms: Linux/macOS shells + git. Windows (cmd/PowerShell) is out of V1.

## Decision Protocol

The stable cross-harness interface is Decision + ReasonCode + Explanation
(+ RecoveryPlan in payload). Five decision classes:

| Decision | Meaning | Interaction tier |
|---|---|---|
| `ALLOW` | run unchanged (noop / provably regenerable / trash GC) | SAFE - silent |
| `RELOCATE` | quarantine targets, then run | SAFE - silent, txid audited |
| `SNAPSHOT` | git snapshot first, then run | SAFE - silent, txid audited |
| `ASK` | single-execution authorization (ASK_ONCE, never a rule exemption) | AMBIGUOUS |
| `BLOCK` | refuse: policy violation or true effect-uncertainty | FORBIDDEN |

ASK is reserved for well-understood operations whose safe *automation* is
unavailable (`COMPOUND_CWD_DELETE`, `COMPOUND_CREATE_DELETE`). True
effect-uncertainty (`BLOCK_UNDETERMINABLE_EFFECT`: `$VAR`, `bash -c`,
`find -delete`, `xargs`-fed lists) stays on the BLOCK path - allowing it
would forfeit the core guarantee. Hard boundaries
(`BLOCK_PROTECTED_PATH`, `BLOCK_OUT_OF_WORKSPACE`, `BLOCK_FORCE_PUSH`,
RESTRICTED prohibitions) are policy violations and never askable.

check.py exit codes: 0 proceed/advisory-ok · 2 blocked · 3 ask · 1 error.

### Dialects

`check.py` lexes the command line for a shell dialect: `--dialect posix`
(default) `| cmd | powershell`, or `AGENT_GUARD_DIALECT`. The Claude and
DSH adapters forward it from the payload / tool arguments / environment.
An unusable selector is a BLOCK, never a silent POSIX fallback:

| Condition | Verdict code | Effect |
|---|---|---|
| dialect name not recognised (e.g. `fish`) | `BLOCK_DIALECT_UNKNOWN` | refuse; fix the selector |
| dialect selector malformed (e.g. `posix:cmd`) | `BLOCK_DIALECT_INVALID` | refuse; fix the selector |

Both are configuration defects rather than one-off authorization, so they
BLOCK instead of ASKing: the selector would fail again on the next command.
The command is never classified in that case, which is why allowing it is
not an option. Under PowerShell, unambiguous parameter prefixes (`-r`,
`-rec`, `-fo`) are expanded to their full names; a prefix matching two
different effects (`-wi`, `-c`, `-p`) stays unknown and BLOCKs.

## Rule table (first match wins)

| # | Condition | Verdict code | Effect |
|---|---|---|---|
| 1 | dry run / no targets | `ALLOW_NOOP` | proceed unchanged |
| 2 | unresolvable targets: shell vars, command substitution, unbalanced quotes, `bash -c` with destructive smell, `find -delete`, `find -exec rm`, stdin-fed (`xargs`) | `BLOCK_UNDETERMINABLE_EFFECT` | refuse - allowing it would forfeit the core guarantee |
| 2a | SHAPE F1: destructive op preceded by `cd` in the same command line | `ASK` (`COMPOUND_CWD_DELETE`) | single-execution authorization; splitting the command avoids the prompt |
| 2b | SHAPE F2: file-creation op (`touch/mkdir/cp/mv/install/ln/tee`, `>`/`>>`) precedes a target-dependent destructive op in the same line | `ASK` (`COMPOUND_CREATE_DELETE`) | single-execution authorization; `reset --hard`/force-push exempt (position-independent) |
| 3 | any target inside quarantine (`.agent-trash/`) | `ALLOW_TRASH_GC` | direct delete permitted (housekeeping) |
| 4 | target outside workspace | `BLOCK_OUT_OF_WORKSPACE` | refuse |
| 5 | target is workspace root or `.git` (any depth) | `BLOCK_PROTECTED_PATH` | refuse |
| 6 | any glob target | `BLOCK_WILDCARD` | refuse; use safe_delete which enumerates |
| 7 | RESTRICTED mode, git/remote destructive op | `BLOCK_RESTRICTED_MODE` | refuse |
| 8 | RESTRICTED mode, fs delete | narrow files only -> `RELOCATE_NARROW`, else `BLOCK_RESTRICTED_MODE` | refuse or quarantine |
| 9 | force/mirror/ref-deletion push | `BLOCK_FORCE_PUSH` | refuse |
| 10 | `git clean` dry run | `ALLOW_NOOP` | proceed |
| 11 | supported `git clean -f...` | `RELOCATE_VIA_CLEAN_ENUMERATE` | enumerate via `clean -n`, decode every Git-quoted path, relocate every match, proceed only on full coverage |
| 11a | `git clean -ff`, interactive `-i`, or exclude `-e` | `BLOCK_UNDETERMINABLE_EFFECT` | refuse; nested-repository and interactive/exclusion semantics are not safely mirrored |
| 12 | `git reset --hard`, `git restore <path>`, `git checkout -- <path>` | `SNAPSHOT_GIT_STASH` | `git stash create`+`store` first; any create/store failure blocks |
| 13 | all targets git-ignored AND match artifact patterns | `ALLOW_REGENERABLE` | direct delete |
| 14 | rooted recursive delete | `RELOCATE_TREE` | quarantine whole tree, proceed |
| 15 | named files/dirs | `RELOCATE_PATHS` | quarantine, proceed |

Precedence inside rule groups: `outside-workspace` > `workspace-root` >
`git-metadata`. RESTRICTED narrows before NORMAL relaxes.

## Verdict actions

- `ALLOW` - run unchanged.
- `RELOCATE` - move targets to quarantine, then run (the run itself then
  finds nothing - effect preserved, loss reversed).
- `SNAPSHOT` - durably store a Git snapshot, then run.
- `BLOCK` - refuse; nothing is executed.

## Modes

```
NORMAL ──(human veto / high-risk event)──> RESTRICTED
RESTRICTED ──(interactive human terminal ONLY)──> NORMAL
```

State: `.agent-trash/state.json`. Non-interactive promotion raises
`AuthorizationRequired`. Harness adapters SHOULD keep the authoritative mode
in host memory instead (agent shells cannot touch it) and treat the file as
a portable fallback.

## Configuration (environment)

| Variable | Meaning |
|---|---|
| `AGENT_GUARD_WORKSPACE` | override workspace boundary discovery |
| `AGENT_GUARD_TRASH` | override quarantine location |
| `AGENT_GUARD_SESSION` | session identity recorded in audit/manifest |
| `AGENT_GUARD_ARTIFACTS` | `os.pathsep`-joined extra artifact patterns |
| `AGENT_GUARD_ALLOW_REGENERABLE` | `0` disables direct deletion of artifacts |

Default artifact patterns: `node_modules dist build out target __pycache__
.cache coverage .next .nuxt .venv venv .pytest_cache .mypy_cache .tox
.turbo .parcel-cache *.pyc *.pyo *.egg-info`

## Files

```
.agent-trash/
├── state.json        # authorization mode (portable fallback)
├── manifest.jsonl    # tx-start / relocate-intent / relocate / snapshot / restore
└── audit.jsonl       # decisions, compensation intents, and outcomes
```

The guard accepts an existing `.gitignore` rule or writes a workspace-local
`.git/info/exclude` rule (the user's `.gitignore` is never modified).
Relocations use a durable write-ahead intent; an intent whose move completed
but whose completion record failed remains discoverable and restorable.
Retention/GC is intentionally manual in V1; `restore.py list` distinguishes
`RESTORABLE`, `RESTORED`, `FAILED`, and `PURGED` transaction state, while
`status.py` reports both historical and currently restorable counts.

## Retention / GC (B4)

Soft policy: 30-day retention, 5 GiB cap. Thresholds only MARK transactions
`GC_ELIGIBLE` (oldest first under the cap); purging is an explicit
`gc.py --execute` maintenance action. Lifecycle is fully audited:
`QUARANTINED -> RESTORABLE -> GC_ELIGIBLE -> PURGED`; the audit log itself
is never garbage-collected.

Authority model: `gc.py --execute` is intentionally permitted
non-interactively (scheduled maintenance is legitimate; purging
already-quarantined evidence is lower-risk than capability escalation,
which alone requires a human terminal). Every purge is audited with
session identity.

Hard principle: **capacity limits never downgrade to permanent deletion.**
If the quarantine cannot accept a relocation, the decision is
`RELOCATE_FAILED_STORAGE` -> BLOCK; the untouched target stays at its origin.

## Authorization scope

NORMAL/RESTRICTED is a **session/agent capability**, not workspace state:
one degraded agent must not DoS concurrent agents. State lives in
`.agent-trash/sessions/<id>.json` (portable fallback); harness adapters
SHOULD keep the authoritative mode in host memory. Promotion to NORMAL is
host-side authority (`policy.force_mode`); agents never receive it as a
model tool.

## Known limitations (V1)

- Command-level interception covers the recognized vocabulary; arbitrary
  scripts that delete internally are invisible to `check.py` (see
  `docs/threat-model.md`).
- `git stash create` captures tracked modifications; untracked files are not
  affected by `reset --hard`, so they need no snapshot.
- Windows cmd/PowerShell, databases, cloud resources: future skills
  (`git-guard`, `database-guard`, `cloud-guard`) on the same core.
