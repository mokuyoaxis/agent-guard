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
- **Recoverability** - deletions are compensated (relocated to `.agent-trash/`
  with a manifest, or snapshotted as a git stash) before they take effect.
- **Authorization** - a human decides permission boundaries. You can be
  *downgraded* to RESTRICTED mode; you can never promote yourself back.
- **Auditability** - every verdict, relocation, and restore is recorded in
  JSONL. Nothing is invisible.

## The one rule

**Prefer `safe_delete` over `rm`. It is not slower for you - it is the
supported path.**

```bash
# delete files/dirs/globs - they are quarantined, not destroyed:
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
disguised form (`/bin/rm`, `python -c`, a script) is a violation of the
authorization pillar and is recorded in the audit log.

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

## RESTRICTED mode

After a human veto, the session runs with narrowed powers: explicit
single-file deletes inside the workspace still work (quarantined as usual);
recursive deletes, globs, and destructive git operations are refused. Only a
human can restore NORMAL. If a task genuinely requires more, say so plainly
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

See `references/policy.md` for the complete rule table, verdict codes, and
manifest format.
