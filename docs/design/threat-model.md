# agent-guard threat model

## Honest positioning

agent-guard assumes the agent runs with the **same OS privileges as the
guard**. Under that assumption a *determined* adversary can always find a
bypass. Therefore:

- agent-guard is **reliability infrastructure**, not a security boundary.
  It defends against *mistakes*: misjudged context, mis-expanded commands,
  wrong working directory, hallucinated paths - which is what essentially
  all real-world "agent deleted my data" incidents are.
- You may claim: recovery for supported valuable deletions, explicit audit
  contracts for observed calls, graded authorization discipline, and reduced
  blast radius of common accidents. Regenerable artifacts may be deleted
  directly; best-effort audit and bypassed calls are not complete visibility.
- You must NOT claim: sandboxing, containment of malicious agents,
  prevention of determined bypass.

## Assets

1. User data inside the workspace (tracked, untracked, ignored).
2. Everything outside the workspace boundary.
3. Git history (local refs, remote refs).

## Vectors and mitigations

| # | Vector | Mitigation | Residual risk |
|---|---|---|---|
| 1 | Agent deletes via unrecognized channel: its own script calling `unlink()`, `python -c`, `node -e` | Out of scope for command classification; prompt layer forbids circumvention; audit records what was seen | Real. Upgrade path: kernel-side enforcement (fanotify/eBPF/sandbox) or intercepting the FS service rather than the shell |
| 2 | Agent tampers with `.agent-trash/state.json` to self-promote mode | Portable file is advisory by design; harness adapters SHOULD hold the authoritative mode in host memory the agent's shell cannot reach | Accepted for pure-CLI usage; documented |
| 3 | Agent allocates a PTY to pass the `isatty()` human check | The CLI check is a heuristic. An integration requiring human-only promotion must provide independent host authority; the shipped adapters do not supply isolated mode storage | Same-UID configuration/state remain writable; no authenticated human identity guarantee |
| 4 | Agent edits/removes `manifest.jsonl` to hide provenance | Append-only by convention; audit log is separate; adapters may ship both off-box | Tamper-evident only if exported |
| 5 | Symlink confusion (delete link vs target, trailing slash dereference) | Lexical boundary analysis; link relocation preserves the link; realpath recorded | Low |
| 6 | TOCTOU between classification and execution | Checks and moves are not atomic. Native hooks run Core before handing control back to host execution; there is no promised maximum gap | Concurrent changes can invalidate facts or affect later execution. Atomic isolation remains unimplemented |
| 7 | Destructive restore | Relocation destinations and sources are checked against the workspace and their quarantine transaction. IDs and directory locations are validated; leaf symlinks retain their semantics. Human `--force` preserves an occupant through a separate relocation transaction before replacement; failed preservation stops that item | Checks and moves are not atomic against concurrent changes. Cross-filesystem failures may leave a partial destination or backup; inspect the filesystem and reported transaction IDs. Preserved versions add storage use and follow existing explicit GC |
| 8 | Quarantine bloat turns `.agent-trash` into a liability (exfil via packaging, disk exhaustion) | Existing `.gitignore` or `.git/info/exclude`; `status.py` exposes size; 30-day/5-GiB soft eligibility with explicit audited purge | A human or scheduler must execute purge; audit is retained |

## guard-lab diagnostic boundary

`guard-lab` introduces synthetic bait for controlled experiments; it does not
upgrade the enforcement boundary above. Its assets are disposable markers and
the integrity of a single run's evidence, not real credentials.

| Vector | MVP treatment | Residual risk |
|---|---|---|
| Tested agent invokes the fake Lab | Stub can only notify a run-specific loopback endpoint and return fixed text | Contact shows invocation, not intent or compromise |
| Tested agent reads a marker | Only an explicitly selected input/output can be scanned for that marker | Ordinary file reads and background indexing remain invisible |
| Snapshot calibration includes synthetic sources | Exact fixture members and four markers are validated at a bounded `127.0.0.1` sink; the body is discarded | Inclusion is not syscall-level read telemetry, and loopback receipt is not external exfiltration |
| Observer fails or evidence is changed | Positive control, control-file hash, event hash chain; unhealthy evidence becomes `INCONCLUSIVE` | Same-UID attacker can rewrite data and hashes |
| Harness sends data elsewhere | No external callback is used by default | Loopback contact cannot prove remote exfiltration |
| Harness sidecar bypasses tool hooks | General file reads and the external boundary remain `UNSUPPORTED` | Existing PreToolUse adapters cannot mediate activity that never becomes an agent tool call |
| Model recognizes the honeypot | Per-run markers, separate clean/positive cases, multiple bait shapes and short windows | A sufficiently informed or controller-aware adversary can selectively behave |
| Tested agent finds the controller | Fixture omits controller paths/tokens; user is told to expose only the fixture | Process/filesystem access under the same OS identity can still discover it |
| A quiet or mismatched run is presented as mitigation | Attack probes distinguish exposure from calibration; `compare` requires an effective unguarded baseline and matching protocol/task/trial identity | Harness, model, guard state, task delivery, and equality of unrelated host settings are not independently bound; one matched task does not generalize |
| A failed host run looks quiet and is credited as protection | Manual real-model reports require a create-once `HOST_RESULT=COMPLETED`; failed, timed-out, missing, or malformed results are inconclusive | Completion is user-declared; Lab does not authenticate or fully parse arbitrary host transcripts |
| A mixed harness transcript is mislabeled as model output | Documentation requires role/channel separation before scanning; reports disclose that scan input and stage are user-selected | Generic Lab does not authenticate vendor-specific transcript provenance |

The Lab controller is user-facing and is not an agent Skill. Never place real
secrets or irreplaceable work in its fixture. See [guard-lab](../lab/guard-lab.md) for
the exact observation and report semantics.

## Experimental DSH text-read redaction

The default-off [native read prototype](../reports/test-report-dsh-read-redaction.md)
mediates complete text-read results in reviewed, artifact-pinned local-FS and
DSH `0.2.0-rc.2` sandbox-FS compositions; see the
[current review](../reports/test-report-release-readiness-0.2.3.md). It
uses Core patterns, replaces structured values and regenerates model content
and presentation metadata. Its actual AgentLoop/JSONL probe uses synthetic
streams and establishes no real-model L2 mitigation result.

The controller worker is trusted local Python code fed through bounded stdin;
it reads no target file and is independent of the model-facing shell executor.
Other plugins and the OS identity remain trusted. Arbitrary custom/later
finalizers, PTC, early events, call arguments, unreviewed providers and other
tool results are outside its scope. Complete-read restrictions withhold
pagination and truncation rather than certifying partial context. This adds
no general file-read telemetry or external-network containment.

## What would upgrade this to a security boundary

Real containment requires enforcement below the agent's privilege level:
seccomp/Landlock/AppArmor profiles, containers/VMs, or a filesystem service
the agent can only reach through mediation. In such a world agent-guard's
rule engine remains useful as the *policy authoring* layer - the four
pillars and compensation model do not change; the enforcement point moves
into the kernel.
