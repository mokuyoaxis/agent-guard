# Development Note: Learning from an Unguarded Deletion Incident

- **Incident window:** September 2026
- **Scope:** destructive-command interception, recovery evidence, and adapter coverage
- **Document status:** public, de-identified engineering note

> This is not the primary incident record. The byte-exact report and its local
> evidence index are retained outside the published source tree. This note
> removes host identity, session identifiers, unrelated project names, private
> filesystem details, and executable destructive commands. Its purpose is to
> preserve the engineering lessons without publishing operator-specific data.

## 1. Why this note exists

An agent session issued a recursively destructive command whose path looked
local but normalized to a much broader ancestor directory. The active harness
did not have an agent-guard adapter, so the command never reached the guard.
The host was also running in an automatic approval mode. The operation began
removing user files and stopped only after external interruption.

The event exposed a useful distinction:

- a policy can be correct and still provide no protection when the harness has
  no interception path;
- an adapter can be present and still be unsafe if path normalization happens
  after an early shape decision;
- a passing test suite does not prove that every supported harness actually
  invokes the guard;
- recovery claims need evidence provenance, not only a rebuilt tree that looks
  plausible.

This note turns those observations into product requirements.

## 2. The failure class

The command combined two properties:

1. it changed the working directory before a destructive operation; and
2. its target contained parent traversal that resolved outside the intended
   workspace.

In abstract form:

```text
change-directory <temporary-location>
then recursively-delete <temporary-location>/../<protected-ancestor>
```

The important fact is the normalized target, not the spelling supplied to the
shell. No destructive reproduction is included here. A safe diagnostic should
resolve and print the target only; it must never execute the deletion.

## 3. What failed

### 3.1 No adapter meant no enforcement

agent-guard has two different integration levels:

- the Skill and CLI path, where an agent deliberately calls the guard; and
- a harness adapter, which intercepts destructive-looking commands before the
  shell executes them.

The incident happened on a harness with neither a native adapter nor an
equivalent pre-execution hook. Prompt guidance alone could not enforce the
policy. This is an integration gap, not a classifier false negative.

### 3.2 Automatic approval removed the human boundary

The session could execute shell operations without a contemporaneous human
decision. In such a host, `ASK` cannot honestly mean “ask once.” It must degrade
to `BLOCK`, or the host must provide a real approval callback.

### 3.3 Shape classification could precede hard boundaries

The policy recognized “change directory, then delete” as an ambiguous compound
shape. That shape historically returned `ASK` before the resolved target was
checked against the workspace boundary.

Hard boundaries need priority over interaction shape:

```text
resolve target
  -> protected root or outside workspace: BLOCK
  -> undeterminable effect: BLOCK
  -> otherwise evaluate compound shape: ASK or compensate
```

An operation does not become merely ambiguous because it is written as a
compound command.

## 4. What recovery taught us

The most valuable surviving source was not a backup of the deleted projects.
It was the collection of agent-session records that preserved successful patch
payloads and command results. That evidence was sufficient to reconstruct some
work, but not enough to call every result an exact recovery.

Three labels are necessary:

- **recovered** — exact bytes or an exact patch are evidenced;
- **reconstructed** — behavior was rebuilt from plans, tests, or prose;
- **missing** — prior existence is evidenced, but the content is unavailable.

Collapsing these labels makes a green build look stronger than the evidence
supports. A trustworthy recovery therefore needs an evidence ledger, replay
reports, deterministic reruns, and explicit gap markers.

These requirements became the [`recovery-audit`](../skills/recovery-audit/SKILL.md)
Skill. It complements `delete-guard` and `exfil-guard`:

```text
before destruction  -> delete-guard
before disclosure   -> exfil-guard
after an incident   -> recovery-audit
```

## 5. Product changes suggested by the incident

### Immediate

1. Treat harness coverage as a first-class compatibility claim. “The CLI
   works” and “the harness intercepts every destructive shell call” are
   different statements.
2. Evaluate protected roots, workspace escape, and undeterminable targets
   before compound-command interaction rules.
3. Make `ASK` fail closed when the host cannot prove that a human approval
   channel exists.
4. Keep original recovery evidence outside publishable package paths.
5. Scan package contents—not only Git-tracked files—for private paths,
   temporary artifacts, and internal reports.

### Next experiments

- a harness capability handshake that reports interception, approval, sandbox,
  and shell-dialect support;
- an adapter conformance probe that deliberately submits safe, non-executed
  destructive samples and verifies they reach the shared policy core;
- protected ancestor roots independent of workspace configuration;
- first-class detection of parent traversal in destructive targets;
- a local incident bundle containing hashes, verdicts, and redacted metadata,
  but never credential material;
- deterministic recovery replay with machine-readable coverage reports.

## 6. What this incident does not prove

- agent-guard is not an operating-system sandbox and cannot intercept a host
  that never calls it;
- a local reconstruction is not automatically an exact recovery;
- a mocked adapter test is not equivalent to a live harness run;
- one interrupted deletion does not establish how or why the process stopped;
- the event does not justify publishing host-specific logs or attributing
  intent to a model or harness.

The project should continue to describe itself as reliability infrastructure
with human escalation, not as a boundary against a malicious or fully
privileged process. See [`docs/threat-model.md`](threat-model.md).

## 7. Public evidence policy

Public reports may include:

- the failure class and normalized policy facts;
- de-identified timelines at a useful engineering granularity;
- minimal non-destructive probes;
- code-level findings, tests, and remediation status;
- clearly stated uncertainty.

They should exclude:

- usernames, home directories, session identifiers, and local project names;
- inventories of authentication files or local configuration;
- absolute paths into caches, logs, or recovery archives;
- copy-pasteable destructive reproductions;
- claims that cannot be separated from private evidence.

The private incident record remains the source of truth for the event. This
document is a reconstructed public narrative derived from that record and is
not byte-equivalent to it.

## 8. Direction

The incident changes the roadmap in a small but important way: adapter coverage
and recovery provenance are now product features, not only operational notes.
The near-term goal is not to add more command patterns. It is to prove that the
same policy reaches every advertised harness, fails closed when human approval
is unavailable, and leaves enough evidence to recover honestly when prevention
fails.
