---
name: recovery-audit
description: >-
  Recover and audit repository or workspace work after accidental deletion,
  corruption, or destructive agent actions, including when the original tree
  or .git is gone but Git remotes, AI session records, tool caches, snapshots,
  or plans survive. Use when provenance and completeness matter; not for
  routine code review, filesystem undelete, or an ordinary clean rollback.
---

# Recovery Audit

Recover the strongest state the evidence supports. A passing build is not proof
that the original bytes or full scope were recovered.

This skill is the after-incident branch of agent-guard. It does not undelete a
disk or promise to restore an entire home directory. It reconstructs project
state only from bytes and effects preserved by surviving evidence, especially
AI session/tool records that may contain successful patches, file snapshots,
command output, and working-directory context after the workspace itself is
gone.

When producing an evidence ledger or final recovery report, read
`references/evidence-records.md` and use its schemas. Keep the populated copy
outside publishable source paths unless the user explicitly approves a
redacted public report.

## Establish authority and permission

First distinguish the requested endpoint: audit only, reconstruction, landing
in the working tree, commit, push, and release are separate permissions. Do not
infer a later permission from an earlier one.

Before any mutation:

- inspect branch, HEAD, remotes, tags, status, ignored/untracked files, reflog,
  stashes, local refs, and available backups;
- preserve the damaged tree and every session/cache/evidence source; do not
  pull over it, re-clone into it, clean it, or run destructive Git commands;
- prefer a fresh recovery stage rooted at a named trusted commit. If staging is
  impossible, create a recoverable snapshot before touching the tree;
- record the intended version, last trusted anchor, time window, affected
  paths, and user-selected source precedence.

Read the loss itself before reading any reconstruction. Commit history, reflog,
and the incident-window `git status`/`git log` are primary evidence:

- a reflog whose oldest entry is `clone: from …` means the original `.git` is
  gone; there are no dangling objects to mine, and the trusted anchor is
  whatever the remote still holds;
- an incident-window status line like `ahead N` means N local commits sat
  beyond the pushed anchor. Treat them as an explicit loss class and require
  session evidence for each, rather than assuming the working tree equals HEAD;
- the incident-window `git status` (modified + untracked) is the best available
  completeness checklist for that moment. Reconcile your final tree against it
  file by file.

When deletion or destructive Git work becomes necessary, use `$delete-guard`
or an equivalent quarantine guard if installed. Before a report, commit,
archive, or push leaves the recovery boundary, use `$exfil-guard` or an
equivalent staged/payload scan if available. A recovery audit never grants
deletion, history rewrite, force-push, or release authority by itself.

Re-verify remote state yourself before reporting it. A handoff note that claims
"nothing was committed or pushed" is a claim, not evidence: check
`git ls-remote`, ahead/behind, upstream tracking, tags, and repository
visibility. Treat a recovery branch as potentially published until the remote
says otherwise, and if it did escape, scan the published contents for
credentials, private paths, and internal reports before deciding whether to
retract it.

## Build an evidence ledger

Define the authority order for this incident instead of assuming that newest
or most complete-looking always wins. A useful default is:

1. the user's explicit decisions about intended state and scope;
2. a trusted Git anchor as the reconstruction base;
3. byte-level snapshots and successful tool effects from authoritative
   sessions, applied in their real chronology;
4. secondary session transcripts, editor caches, exported worktrees, CI
   artifacts, and package archives;
5. plans, tests, and documentation as behavioral specifications;
6. inference, only with explicit approval and a `reconstructed` label.

Track each recovered unit with source, timestamp/session ordinal, target path,
hash when available, observed tool result, confidence, and one status:

- `recovered`: the bytes or exact patch are evidenced;
- `reconstructed`: behavior was rebuilt from a specification, not recovered;
- `missing`: existence or intent is evidenced but the contents are unavailable.

Never collapse these statuses. Preserve gap markers and overturned conclusions.

## Audit the recorder before replaying it

The recovery engine is part of the evidence chain and must itself be tested.
Match the original tool semantics:

- replay patch blocks atomically and with their real ordered-search/cursor
  behavior; a global-uniqueness text replacement is not equivalent;
- distinguish atomic tools from shell commands that may have written files
  before returning a nonzero exit code;
- preserve complete compound-command semantics, heredocs, encoded payloads,
  working directories, exit status, and generated helper files;
- decode base64 or similar wrappers before concluding that a symbol or edit is
  absent;
- treat truncated diffs and alternate recorder/transcript shapes as partial
  evidence, not as empty evidence;
- do not interpret duplicate retries, approval transcripts, or a command line
  without its result as the final state.

### Hard gates

These are pass/fail conditions, not advice. Do not judge recovery quality while
any of them is unmet.

**Engine verdict matrix.** Extract each mutating event together with the status
the original session recorded for it, then replay and classify:
`agree_ok`, `agree_fail`, `diverged_ok`, `diverged_fail`. Every event the
original applied successfully must apply here; tune the engine until
`diverged_fail` is zero for the authoritative timeline. A large failure count is
a statement about the engine, not about the cache.

**Determinism.** Replay the same frozen inputs twice and compare trees
byte-for-byte. On any difference, bisect the most recent engine change; do not
adjust filtering knobs until the numbers look better. Note that a narrow
predicate change (e.g. `contains` becoming `starts_with` on a snapshot body) can
silently drop events and invalidate every comparison made after it.

**No intra-file hunk skipping.** Fail at file granularity, never at hunk
granularity. In a multi-file batch, a file whose context is missing is abandoned
whole and recorded, while its siblings still apply. Skipping only the unmatched
hunks inside one file leaves it structurally broken: the text still looks
plausible but no longer parses, and the damage surfaces later as syntax errors in
files you never touched.

**Hunk bounds.** Bound `@@ -l,s +l,s @@` bodies by their declared line counts.
Transcripts routinely glue unrelated text (tool-call JSON, the next command)
straight after a diff; without the count bound that text is swallowed as context
and every anchor becomes unmatchable.

### Replay mechanics

File reads, `git diff` output, tarballs, and equivalent byte snapshots can be
stronger than a chain of fragile patches. Insert snapshots at the point in the
timeline where they were observed. Prefer the session's own `git diff -- <paths>`
as a state snapshot: applying it re-synchronizes the tree to the state the next
recorded edit was written against.

Protect journal-replayed paths from an older bulk overlay by hashing the tree
before and after the replay and skipping exactly the changed paths. Protection is
not automatically correct: when the bulk overlay is the better reconstruction for
a given file, protect by evidence per file rather than by a blanket rule.

When a diff will not apply, its added lines are still verbatim evidence. Extract
them and re-anchor onto the current file's real context; a drifted context line
is not a reason to discard the change.

Enumerate every transcript shape before trusting coverage. One recorder may emit
`user_message` payloads while another emits `message` with a role and a content
list; a parser that knows only one shape silently loses whole time windows, and
for some windows the approval/guardian transcript is the only surviving record of
real executed edits. What is untrustworthy about such transcripts is treating a
duplicate retry as the final state, not their existence.

Merging sources from a shared cache directory requires validated target paths.
A journal directory often serves several projects; a filter that accepts
"anything that looks relative" will write unrelated repositories into the tree.

Triage failed commands by what they provably wrote. A multi-target edit helper
applies its targets in order and aborts on the first anchor miss, so a nonzero
exit can still leave earlier files rewritten. Blanket replaying of failed
commands corrupts state, blanket skipping loses real work: replay a failure only
when you can prove which earlier targets it reached, and otherwise restore the
survivors as anchored edits.

### Residual anchored edits

Edits that cannot be placed in the timeline — survivors of an aborted helper, or
a diff whose base has drifted — may be applied after the ordered replay as an
explicit, auditable list: source file, path, exact `before`/`after`, and
provenance for each. Requirements: exact-match anchors only, apply the first
match and report the count, tolerate one stale entry without dropping the rest of
the file, keep an eye on repeated sibling blocks (a list of identical entries
should fill each site in turn), and never use an anchored edit to introduce
content the evidence does not contain.

Replay into a new stage. Change one replay rule at a time and retain
machine-readable reports of applied, skipped, failed, already-applied, and
skipped-anchor events.

## Audit scope against facts

Separate project intent from implementation facts. Build a compact coverage
matrix for each promised capability:

| Axis | Required evidence |
|---|---|
| Implementation | code exists and isolated tests pass |
| Integration | all intended entry points consume the same fact/contract |
| Real-world evidence | target platform/provider exercised, or explicitly not run |
| Release state | documentation and published artifacts may truthfully claim it |

Compare plans with code, tests, CLI/API surfaces, persistence schemas, UI, and
packaging. Classify findings as promised-but-missing, implemented-with-drift,
or unplanned risk/hard-coded obstruction. Deduplicate dependent symptoms.

Use executable probes for pure functions, security boundaries, failure paths,
and read/write behavior; static reading alone can misclassify intentional UI or
compatibility behavior. Record corrections without erasing the earlier finding.

## Restore conservatively

- Apply exact evidence in the declared authority order onto the trusted base.
- Keep recovery tools, evidence, rejects, caches, authentication material, signed URLs,
  private paths, and machine-specific configuration outside product sources.
- Never delete a failing test or weaken a contract merely to obtain green
  output.
- Do not invent unavailable source. Rebuild only after explicit approval, and
  mark rebuilt files separately from recovered files.
- Land with a non-deleting copy or explicit file allowlist. Preserve unrelated
  user changes, Git metadata, dependencies, and data directories.

## Verify in layers

Run checks proportional to the project, normally in this order:

1. structural checks, syntax, `diff --check`, and targeted tests;
2. full tests and lint, comparing file/test counts with the last evidenced
   result rather than accepting a smaller green suite;
3. offline/headless diagnostics proving the core does not silently depend on a
   host, network, or user configuration;
4. package manifest/dry-run and, when relevant, installation from the produced
   archive outside the repository;
5. scans of staged/package contents for secrets, internal reports, absolute
   paths, temporary artifacts, and unintended binary metadata;
6. file modes, symlinks, line endings, version fields, tag/release workflows,
   and platform-specific checks.

Enumerate failures exhaustively. A runner that stops at the first failing file
hides the real failure set; execute every test file independently and collect a
machine-readable matrix before planning work. Lint/total file counts are also
completeness signals: a suite that is green but smaller than the last evidenced
count has lost files, not fixed them.

Verify a landed tree by re-deriving it, not by trusting the producing artifact.
Diff the landed tree against the stage that produced it and classify every
difference. If the improvement came from edited tests, review each test diff and
state whether the change is a legitimate portability/robustness fix or a
weakened assertion; a green suite obtained by relaxing contracts is not recovery.
Implementation files that are byte-identical to the recovered stage are the
strongest available evidence that the fixes were real.

Keep fixture/mock evidence distinct from paid provider, device, browser, or CI
evidence. State every unrun environment plainly. A lower test count, a lint-only
pass, or one successful canary must not be reported as full recovery.

## Land and report

Before a commit, stage from an allowlist and inspect the staged names, summary,
content checks, executable bits, package version, and excluded evidence. Prefer
a dedicated recovery branch and keep it local until publication is separately
authorized. Mention known gaps in the commit body. Commit, push, tag, package
publication, and marketplace/release actions remain separate authorization
gates.

The final handoff should state:

- trusted baseline and recovered endpoint;
- evidence sources and precedence;
- counts of recovered, reconstructed, and missing items;
- loss classes: files absent from every source, commits beyond the pushed
  anchor, and periods covered only by secondary transcripts;
- exact validation commands and results, with file and test counts;
- excluded or quarantined artifacts;
- unresolved gaps and their practical impact;
- local commit, remote branch, tag, package, and release status separately,
  re-verified against the remote rather than restated from the plan.

Call the outcome an exact recovery only when provenance and completeness support
that claim. Otherwise use `evidence-backed partial recovery` or `reconstructed
checkpoint`, even when all current tests pass.
