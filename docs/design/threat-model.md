# agent-guard threat model

## Trust assumptions and scope

**agent-guard assumes the agent runs with the same OS privileges as the guard.** Under that shared-privilege model, a determined adversary executing arbitrary code under the same user account can always find a bypass. Accordingly, agent-guard is built as **reliability infrastructure** against operational mistakes—misjudged context, mis-expanded globs, wrong working directories, or hallucinated targets—not as an OS containment boundary.

| In scope (what you can rely on) | Out of scope (what requires OS containment) |
|---|---|
| Deterministic quarantine and Git snapshots for supported destructive commands | Sandboxing or containing a malicious same-UID process |
| Bounded admission rules (quarantine capacity, session budgets, rate/repeat limits, protected paths) | Preventing determined bypasses via custom scripts or direct syscalls |
| Explicit, structured verdicts and append-only local audit trails for observed calls | Guaranteeing unbypassable telemetry or complete visibility across bypassed paths |
| Safe redaction of documented credential shapes and host-identifying paths on inspected channels | Legal hold, cryptographic deletion certificates, or authenticated human identity |

Regenerable, Git-ignored artifacts may still be deleted directly unless an operator explicitly requires quarantine (e.g., via Cleanup `--quarantine`). Versioned release scope and acceptance records live in the [0.3.2 release notes](../releases/release-notes-0.3.2.md), [0.3.1 release notes](../releases/release-notes-0.3.1.md), and [0.3.0 acceptance report](../reports/test-report-maintenance-0.3.0.md).

## Assets

1. User data inside the workspace (tracked, untracked, ignored).
2. Everything outside the workspace boundary.
3. Git history (local refs, remote refs).

## Vectors and mitigations

| # | Vector | Mitigation | Residual risk |
|---|---|---|---|
| 1 | Agent deletes via unrecognized channel: external scripts calling `unlink()`, obfuscated code, or direct syscalls | In `0.3.2+`, inline script deletion calls (`python -c`, `node -e`, `ruby -e`, `perl -e`, `deno eval`, interpreter heredocs) fail closed as `BLOCK_UNDETERMINABLE_EFFECT`; arbitrary external scripts and obfuscated invocations remain out of scope for static command classification; prompt layer forbids circumvention; audit records what was seen | Real. Upgrade path: kernel-side enforcement (fanotify/eBPF/sandbox) or intercepting the FS service rather than the shell |
| 2 | Agent tampers with `.agent-trash/state.json` to self-promote mode | Portable file is advisory by design; harness adapters SHOULD hold the authoritative mode in host memory the agent's shell cannot reach | Accepted for pure-CLI usage; documented |
| 3 | Agent allocates a PTY to pass the `isatty()` human check | The CLI check is a heuristic. An integration requiring human-only promotion must provide independent host authority; the shipped adapters do not supply isolated mode storage | Same-UID configuration/state remain writable; no authenticated human identity guarantee |
| 4 | Agent edits/removes `manifest.jsonl` to hide provenance | Append-only by convention; audit log is separate; adapters may ship both off-box | Tamper-evident only if exported |
| 5 | Symlink confusion (delete link vs target, trailing slash dereference) | Lexical boundary analysis; link relocation preserves the link; realpath recorded | Low |
| 6 | TOCTOU between classification and execution | Checks and moves are not atomic. Native hooks run Core before handing control back to host execution; there is no promised maximum gap | Concurrent changes can invalidate facts or affect later execution. Atomic isolation remains unimplemented |
| 7 | Destructive restore | Relocation destinations and sources are checked against the workspace and their quarantine transaction. IDs and directory locations are validated. Leaf symlinks retain their semantics. Human `--force` preserves an occupant through a separate relocation transaction before replacement. In `0.3.1+`, cross-filesystem copies are staged before publication, and failed preservation stops the restore | Concurrent changes can still affect checks and moves. A source-cleanup failure can leave data at both paths. Inspect the filesystem and reported transaction IDs. Preserved versions add storage use and follow explicit GC |
| 8 | Quarantine bloat turns `.agent-trash` into a liability (exfil via packaging, disk exhaustion) | `.gitignore` / `.git/info/exclude`, status/GC commands, plus `0.2.7+` cross-process capacity admission, persistent session budgets, rate/repeat limits, and batch confirmation | Logical admission is not an OS quota. Audit, directory metadata, and unrelated writers can still grow storage; failures/restores do not refund session charges; GC stays explicit; same-UID tampering remains possible |
| 9 | Agent deletes configured evidence or resets its own deletion budget | Shared path protection (`0.2.7+`) guards configured paths, ancestors, config, and admission control files before artifact/housekeeping exemptions; CLI budget reset requires terminal acknowledgement | Portable protection and TTY prompts are not independent authority against same-UID processes; no authenticated human guarantee |

Full configuration defaults and edge-case semantics for deletion budgets and protected paths are documented in the [deletion limits guide](../guides/deletion-limits.md) (deployments pinned to `0.2.6` or earlier retain only soft retention and manual GC).

## Optional local MCP boundary

The [local MCP adapter](../guides/mcp.md) exposes explicit maintenance and query tools bound at startup to a single workspace root and stable session/budget identity:

- **Reuse of Core protections:** MCP tool handlers route through the same CLI policy, admission, recovery, and audit paths. Tool arguments cannot alter the startup binding or invoke `--force` restore, GC execution, budget reset, or autonomous Cleanup approval.
- **No quarantine-purge shortcut:** Targets inside quarantine are refused by `safe_delete` so MCP calls cannot turn into an unapproved permanent purge.
- **Explicit surface and confirmation boundary:** Generic MCP client tool annotations and generic client permission prompts remain advisory UI hints, not Core authorization. When an operator explicitly enables `--approval-channel codex`, `cleanup_request_approval(saved_plan_id)` presents summary counts, logical size estimates, workspace, and the path to the complete exported list (`host_approval.review.file`), and a conforming host acceptance reply (`action: accept` with the required review confirmation payload) triggers Core to revalidate the saved selection and issue a task approval. Even on that operator-selected trusted host channel, `human_verified` stays `false`—same-UID clients or consent-altering hooks remain out of the server's trust boundary, and human identity is not independently attested. The MCP server never intercepts shell, script, or editor operations that bypass its tools, and same-UID processes can still modify local state or spawn an unguarded process.
- **State and session isolation:** Startup session flags govern both mode and deletion budget bucket (omission uses legacy CLI state and the default bucket). Restrictions remain scoped to a matching `(workspace, quarantine, session)` tuple and do not inherit a host harness's in-memory vetoes unless bridged by the host.
- **Failure semantics and output scope:** Client cancellations, disconnects, or timeouts do not guarantee rollback or zero disk mutation—always inspect recorded transactions before retrying. Tool responses return local metadata and paths rather than sanitized prose; the optional MCP SDK adds its own dependency footprint, though a local stdio server opens no network listener.

For protocol validation history and host-by-host MCP test coverage, see the [MCP guide](../guides/mcp.md), the [0.2.7 validation record](../releases/release-notes-0.2.7.md#validation-status), and the [0.3.0 acceptance report](../reports/test-report-maintenance-0.3.0.md).

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
