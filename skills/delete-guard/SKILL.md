---
name: delete-guard
description: >-
  Recoverable deletion discipline for AI agents. Use whenever you need to
  delete, clean, or discard files/directories or run destructive git
  operations (clean, reset --hard, restore, force push) inside a workspace.
  Routes supported operations through quarantine or Git snapshots, and
  explains how to stop safely when compensation or policy blocks you.
---

# delete-guard

You operate inside a workspace where irreversible destruction is not a
default capability. This skill defines the deletion discipline the guard
enforces and how to work with it productively. The four pillars:

- **Scope** - you act inside the workspace; the workspace root, `.git`, and
  everything outside are never yours to delete.
- **Recoverability** - supported valuable deletions relocate to the selected
  quarantine with a manifest; supported Git overwrites snapshot tracked state.
  Provably regenerable, ignored artifacts may be deleted directly.
- **Authorization** - a human decides permission boundaries. You can be
  *downgraded* to RESTRICTED mode; you can never promote yourself back.
- **Auditability** - supported writers record decision/recovery metadata in
  JSONL according to their CLI contract. Mutation intents must be durable;
  assessment/blocked audit may be best effort, and early no-op paths may not
  write an audit. Calls outside the covered path are not automatically visible.

## The one rule

**Prefer `safe_delete` over `rm`.** It applies the guard's recovery policy.

```bash
# delete explicit targets; valuable content is quarantined:
python3 <repo>/skills/delete-guard/scripts/safe_delete.py src/old_module.py
python3 <repo>/skills/delete-guard/scripts/safe_delete.py 'build/**/*.tmp' --reason "stale build output"

# undo a transaction (list first if unsure):
python3 <repo>/skills/delete-guard/scripts/restore.py list
python3 <repo>/skills/delete-guard/scripts/restore.py <txid>

# current guard state:
python3 <repo>/skills/delete-guard/scripts/status.py
```

Globs passed to `safe_delete` are expanded explicitly before anything moves.
That is why `safe_delete '*.log'` works while `rm *.log` is blocked: the
guard refuses opaque target sets, not cleanup work.

## If you run `rm` / destructive git anyway

A harness adapter may intercept the command before execution. Verdicts you
can receive:

| Verdict | Meaning | Your move |
|---|---|---|
| `ALLOW` (harness may show `PROCEED`) | safe as-is or compensation already applied | continue; record and verify any `txid` |
| `BLOCK_UNDETERMINABLE_EFFECT` | targets unresolvable (`$VAR`, `bash -c`, `find -delete`, `xargs`) | restate with explicit paths, or use `safe_delete` |
| `BLOCK_WILDCARD` | glob target set is opaque | use `safe_delete` with the glob |
| `BLOCK_OUT_OF_WORKSPACE` / `BLOCK_PROTECTED_PATH` / `BLOCK_PROTECTED_ANCESTOR` | outside boundary, workspace root, `.git`, or a filesystem root (`/`, `/home`, `/usr`, `$HOME`) | do not retry; this is a hard boundary. Ask the human if it is truly needed |
| `BLOCK_RESTRICTED_MODE` | session is downgraded | only explicit single-file deletes are permitted; ask the human for anything more |
| `BLOCK_FORCE_PUSH` | remote history destruction | do not retry; escalate to the human |

Shape rules on compound commands: a single line that `cd`s anywhere before a
destructive op (F1), or that creates files (`touch/mkdir/cp/mv/tee`,
redirections) before destroying them (F2), is refused as undeterminable.
Run deletions as standalone commands with an explicit workdir.

A block is not an error to route around. Retrying the same operation in a
disguised form (`/bin/rm`, `python -c`, a script) violates this discipline and
can bypass both protection and audit. Do not treat a missing audit as proof
that no operation occurred.

`git reset --hard [commit]` first checks whether its target tree would
overwrite untracked or ignored content. A tracked-only stash cannot recover
those paths, so a collision returns `BLOCK_GIT_RESET_COLLISION` without
moving them or creating a snapshot. Preserve the conflicting content
separately before retrying. Use a standalone reset without shell wrappers;
compound commands, submodule effects, unknown options, and stash-relative
targets are refused as undeterminable. Unrelated untracked files are allowed.

## Compensation preflight and verification

Before sandboxed use, ensure `.agent-trash/` is already ignored by a tracked
`.gitignore` or by host-managed `.git/info/exclude`. If Git metadata is
read-only and no ignore rule exists, the guard blocks before moving a source.

After a relocation or snapshot, verify that its `txid` appears as
`RESTORABLE` in `restore.py list` before running a related destructive command.
After restoration, verify that a consumed relocation shows `RESTORED`.
If the guard reports an internal, preflight, manifest, or compensation error,
stop immediately. Inspect both the origin and `.agent-trash/`; never infer
that an error means no filesystem change occurred. A durable
`relocate-intent` can keep an interrupted relocation discoverable.

## Assessment and preflight

Advisory `check.py` and `safe_delete.py --dry-run` preserve command targets,
but may create quarantine/audit metadata and a Git local exclude rule.
They are not zero-write filesystem checks. Advisory exit 0 means the
assessment completed; inspect the decision and never execute a BLOCK/ASK
command merely because the exit was zero. Blocked safe_delete still exits 2
under dry-run. Audit warnings do not imply that all metadata writes were
undone. Use only a disposable fixture when verifying this behavior; the
[policy reference](references/policy.md#assessment-and-dry-run-side-effects)
defines the no-match, existing-ignore and external-bucket branches.

In the 0.2.5-rc1 source candidate, new `safe_delete` audit events record decisions, target counts and established
recovery IDs/counts rather than target paths or free-form reasons. `--reason`
is still accepted, but its text is not persisted in audit or transaction
metadata. Exact recovery paths remain in the CLI result and manifest.
Historical records and automatic session metadata are unchanged; see the
[policy reference](references/policy.md#safe_delete-audit-metadata).

## Quarantine location queries

When the user asks where quarantines are or how many exist, use the shared
read-only query with a current-directory or user-selected scope:

```bash
python3 <repo>/skills/delete-guard/scripts/status.py --trash-index --json
python3 <repo>/skills/delete-guard/scripts/status.py --trash-index --root <projects> --max-depth 3 --json
python3 <repo>/skills/delete-guard/scripts/status.py --trash-index --trash <custom-store> --json
```

Report the scope, candidate count, metadata-identified count and read/budget
errors. Do not claim that unconfirmed/unreadable locations are absent or that
the result covers the whole host. Explain that these are bucket locations,
not file/transaction totals. If you supplement the result yourself, identify
that source separately. Existing native status tools may show the current
project only; use the CLI for this indexed query. Location metadata and cached
JSON are not authorization to purge, and recovery file contents must not be
read just to answer a location/count question.

## RESTRICTED mode

The 0.2.5-rc1 source candidate protects the quarantine root, root manifest,
audit/state files, state temp file, sessions tree and storage access parents.
Housekeeping does not override those checks. Use the existing explicit
`gc.py --execute --txid <id>` entry for a validated, audited transaction purge.
Ordinary unprotected payload housekeeping retains its existing policy.
GC plans omit `PURGED` history; they do not delete stored Git stashes.

After a human veto, the session runs with narrowed powers: explicit
single-file deletes inside the workspace still work (quarantined as usual);
recursive deletes, globs, and destructive git operations are refused. Only a
human can restore NORMAL. If a task requires more, say so plainly
and ask.

## Regenerable artifacts

Provably regenerable targets (git-ignored AND matching known artifact names
like `node_modules/`, `dist/`, `__pycache__/`) are allowed to be deleted
directly - no quarantine overhead. If git cannot prove a target is ignored,
it is treated as valuable and quarantined. Uncertainty increases restriction.

## Recovery

Every relocation writes `origin -> trash` pairs into
`.agent-trash/manifest.jsonl`. Restore is non-destructive: it refuses to
overwrite anything that now exists at the origin unless a human passes
`--force`. Git snapshots are stored as stashes named `agent-guard:<txid>`;
restore applies them and never drops them.

With human `--force`, an existing occupant is first preserved by the normal
relocation journal in a separate transaction. The result's optional
`backup_txids` lists preservation attempts; an ID alone does not prove the
copy completed. Inspect those IDs with `restore.py list --json` and the
filesystem after any error. A failed old-version move must not be followed
by deleting the preserved occupant. Restoring a completed backup transaction
uses the same `restore.py <txid>` command; if the origin is occupied, the
usual conflict/force rules apply again. Preserved versions consume quarantine
space and remain subject to its existing explicit GC policy.

See `references/policy.md` for the complete rule table, verdict codes, and
manifest format.

The candidate's restore audit records counts and transaction associations rather
than duplicate paths/error text. CLI results and recovery manifests still
contain the exact data needed to inspect and restore; a failed CLI result
must still be checked against the filesystem and preservation attempts.
