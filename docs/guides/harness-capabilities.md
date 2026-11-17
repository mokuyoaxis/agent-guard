# Harness capability and evidence matrix

This matrix records [0.3.0](../releases/release-notes-0.3.0.md)
and earlier versioned evidence separately. Confirm exact-version availability
through the release and package links in its notes. A source version does not
establish publication.
Published prerelease, checked on 2026-10-19:
[0.2.5-rc3](../releases/release-notes-0.2.5-rc3.md).
Rows below retain their tested source/host versions and do not automatically
certify a later release. Local regression or package checks are separate from
new native-host/model evidence.

Earlier release [0.2.6](../releases/release-notes-0.2.6.md) adds optional Kimi Stop
feedback, separate doctor checks, DSH Lab diagnostics, alpha.4 default
deletion compatibility and Kimi 2.3.0 root-Bash compatibility. These additions
are absent from 0.2.5. See its [version notes](../releases/release-notes-0.2.6.md).

The new [research notes](../research/README.md) describe bounded private
experiments; their disclosure/delegation methods do not change the supported
production coverage in this matrix.

The Python Core and CLI are harness-neutral. A Skill or CLI being usable in a
host does **not** mean that host intercepts tool calls, and an adapter returning
`BLOCK` does **not** by itself prove the host prevented execution.
For an unlisted host, use the [self-adaptation guide](../../adapters/INTEGRATION.md)
and keep its native-interception status `UNVERIFIED` until independently tested.

The 0.3.0 maintenance workflow targets Codex, DSH, Claude Code and Kimi Code,
using the shared [setup prompt](agent-setup-prompt.md). Its
[maintenance acceptance](../reports/test-report-maintenance-0.3.0.md) now records
local runtime checks and three model hosts' explicit MCP tasks; Claude model
acceptance is deferred. Fresh approved 71-root batches passed on Codex,
DSH and Kimi; Codex also passed a single-root real cache task. Independent
controllers verified recovery copies and restored the data. Codex's earlier
usage-limit block remains a separate historical attempt. These are explicit MCP operations. A later
current-source trial observed Kimi root-Bash blocking, MCP planning, operator
approval, original-session execution and independent recovery. DSH's sandboxed native path was
environment-blocked, with a separate zero-model local control passing.
The rows below retain their actual native source/version evidence.
Other hosts use local adaptation and report each tested path separately.

## Evidence levels

| Level | Meaning |
|---|---|
| CLI | The guard can be invoked deliberately by an agent or user. No automatic interception is claimed. |
| Adapter | A unit/smoke test proves payload-to-Core-to-host-decision translation. The host itself may be mocked. |
| Hook observed | A real host session invoked the configured hook for the specified tool call. This proves coverage only for observed calls. |
| Enforcement observed | A real host submitted a safe, synthetic operation that Core denied, and an independent sentinel confirmed the operation did not run. |

Evidence is version-, tool-, and configuration-specific. A test with a mock
model endpoint may prove host behavior without proving model behavior. A
subagent observation is not a guarantee for every subagent, tool, or schedule.

`doctor.py kimi|claude [--config PATH] [--probe] [--json]` checks one selected
configuration file and optionally runs local bridge/adapter verdict probes
in a temporary workspace with controlled dialect input.
Its `configuration: PASS` is a *file-shape check*, not an effective merged
host-settings check; `local_probe: PASS` is not host interception. The doctor
always reports `host_interception: UNVERIFIED`. Only a separate real-host
trial with an independent non-execution check can establish the bounded
"Enforcement observed" level above. Neither doctor mode starts a model.
In 0.2.6, a separate Kimi `feedback` object checks optional
Stop configuration and, with `--probe`, synthetic feedback in temporary
storage. Missing Stop is `NOT_CONFIGURED` and does not fail the doctor;
configured Stop failures exit 1. Unreadable configuration is `UNVERIFIED`.
`feedback.model_delivery` stays `UNVERIFIED`, including after a Bash live
sentinel. See the [Stop doctor fields](../../adapters/kimi-code/README.md#stop-doctor-checks-unreleased).
The optional `--check-drift` mode additionally compares the installed host
version with a shipped compatibility profile and computes privacy-safe
configuration/runtime fingerprints. Its `CURRENT` status is static preflight
evidence only; `STALE` requests a re-test and does not itself mean the adapter
is incompatible. Optional local baselines can detect later `DRIFTED` inputs.
See the [host drift guide](host-drift.md).
Its opt-in `--live-sentinel` adds one real-host/model call. A PASS requires an
exact hashed adapter receipt, matching Core block audit, host block feedback,
and an absent marker; otherwise it reports FAIL or INCONCLUSIVE. This is
bounded "Enforcement observed" evidence only for that root-agent Bash call.

## Current coverage

| Host / tested version | Entry and scope | ASK mapping | Highest evidence for current path | Important gap |
|---|---|---|---|---|
| Claude Code 2.1.270 / 2.1.273 (post-0.2.0 bridge) | `PreToolUse` for `Bash`; shared Core via Python and POSIX startup bridge | Native `permissionDecision: ask` (headless runs need an approver) | Real CLI/hook with scripted model endpoint: ALLOW, BLOCK, headless ASK, injected Python failure; see [report](../reports/test-report-claude-code-harness.md) | Hook absence/timeout, Windows bridge, other tools, arbitrary subagents and real model behavior are not covered. |
| DSH 0.1.5-rc.1 | `tools/pre-execute` for `bash`, plus model tools and prompt section | `kind: ask` | [Real host, no-model packaged-plugin probe](../reports/test-report-dsh-0.1.5-rc.1.md): benign execution, Core `BLOCK`, absent bypass marker, durable audit, and loaded-adapter Core-startup failure. A separate [real-model Lab baseline](../reports/test-report-dsh-guard-lab.md) stayed quiet with guard off, so it produced no L2 claim. | Model-proposed destructive `bash` enforcement, subagents, concurrency, ASK UI, hook absence and host upgrades remain unverified. The opt-in read path has separate evidence below. |
| DSH CLI 0.1.5-rc.1 / tools and FS 0.1.5-rc.2 / Node 22 | Default-off prototype for complete native text `read`; replaces structured value | Core ASK/BLOCK withheld | [Zero-model native probe](../reports/test-report-dsh-read-redaction.md): next request and durable JSONL. [Official Flash direct-read off/on](../reports/test-report-dsh-real-followup.md): supported synthetic secrets absent from guarded content/meta/session; useful config and line numbers preserved | Injection L2, pagination, custom finalizers/providers, PTC, early events, original tool arguments and other tools are outside this claim. Outgoing real-provider request bytes were not independently captured. |
| DSH 0.2.0-rc.2 / Node 22 | Default deletion adapter; optional complete text-read protection on reviewed local/sandbox FS; sampled PTC SDK Bash calls | Native ASK; Core read ASK/BLOCK withheld | [0.2.3 native review](../reports/test-report-release-readiness-0.2.3.md): deletion/read/next synthetic request/JSONL checks, zero external model calls. [0.2.4 official Flash PTC](../reports/test-report-dsh-0.2.4-ptc.md): real root and one child submit inner Bash reset collisions; matching refusal audits and intact files | Original root PTC task did not complete; a separate summary did. Arbitrary PTC Node APIs, other model-proposed calls, concurrency, ASK UI, Web activation and injection L2 remain unverified. Historical pending captures remain inconclusive. |
| DSH 0.2.1-alpha.4 / Node 22 (Agent Guard 0.2.6) | Default deletion adapter; three recovery tools and prompt guidance | Native approval service; sampled rejection and fixed answerer grant | [Native CLI report](../reports/test-report-dsh-alpha4.md): actual Profile/Boot, Bash allow/block, quarantine/restore, root/child BLOCK and ASK, per-session policy forwarding; no external model calls | No optional read or Lab admission. Interactive approval UI, OS sandbox confinement, arbitrary PTC, concurrent agents, GUI activation and real-model behavior are unverified. |
| Kimi Code 0.42.0 / 2.1.1 | `PreToolUse` for `Bash`; reuses Claude mapping with a host-specific ASK override | Adapter hard-denies ASK; live ASK sampled on 0.42.0 | [Bounded report](../reports/test-report-kimi-code-block.md): authenticated 2.1.1 root-Bash sentinels; older 0.42.0 root/subagent enforcement; later actual-CLI fixed-loopback fault cases on both versions | 2.1.1 subagents, concurrency and live ASK UI remain unverified. Missing/timed-out hooks allowed tools in the fault cases; other tools and arbitrary spawn failures remain uncovered. Client route labels do not identify remote model weights. |
| Kimi Code 2.3.0 (Agent Guard 0.2.6) | Root `Bash` PreToolUse in prompt CLI and ACP; ACP uses Kimi's local executor | Adapter hard-denies ASK | [Sixteen actual-host loopback trials](../reports/test-report-kimi-code-block.md#kimi-230-acceptance): allow/BLOCK/ASK, Python startup refusal, absence control, recovery; ACP process/payload cwd matched the session workspace despite a different service startup cwd | No external model calls. Subagents, other tools, client-provided terminals, concurrency, background execution and host-timeout behavior remain unverified. Hook absence allowed the inert marker. Web checks covered startup/status/shutdown only. |
| Kimi Code 2.3.0 (0.3.0 development checkout, 2026-11-12) | One real-model root `Bash` probe, then MCP planning and original-session approved execution | No ASK sampled | [Native handoff](../reports/test-report-maintenance-0.3.0.md#native-interception-to-mcp-handoff-2026-11-12): current hook receipt, Core `BLOCK_WILDCARD`, absent marker; real terminal approval, 71/71 roots, one transaction/rate event and independent MCP recovery | Extra plan save retained as an instruction-limit deviation. Native and MCP budget identities differ in this configuration; no universal budget sharing, Stop or other-tool claim. |
| DSH 0.2.0-rc.2 (0.3.0 development checkout, 2026-11-12) | Current headless profile plus separate actual-SDK local-executor control | Local control refused ASK without approver | [Native handoff](../reports/test-report-maintenance-0.3.0.md#native-interception-to-mcp-handoff-2026-11-12): headless checker denied when the OS sandbox was unavailable; six zero-model local checks passed | The `workspace-write` headless path is environment-blocked on this PRoot host. Local control does not establish that profile's Core-policy execution or OS confinement. |
| Kimi optional Stop, 0.42.0 / 2.1.1 / 2.3.0; Agent Guard 0.2.6 | Recovery guidance after the initial answer; separately configured POSIX hook | No additional approval path | [Feedback follow-up](../reports/test-report-kimi-code-block.md#recovery-feedback-follow-up-2026-10-20-unreleased-source): actual-CLI fixed-loopback delivery and one bounded 2.1.1 real-model session. [2.3.0 prompt/ACP follow-up](../reports/test-report-kimi-code-block.md#kimi-230-acceptance): actual txids, storage/GC guidance and byte-exact recovery with fixed responses | Adds a response after new compensation records. The initial answer may be inaccurate; emission is not acknowledgement. Shared-session actors, exhausted budgets and broader model/scheduling behavior retain their limits. |
| Codex CLI 0.154.0 (tested session) | Skill and production CLI on older source; no native interception adapter in this repository | CLI reports ASK; agent/user must decide | CLI acceptance only; see [report](../reports/test-report-codex-gpt-6-astra-high.md) | No automatic tool-call enforcement claim. |
| ZCode (Windows session; host version unrecorded) | Historical Skill/CLI and process-type `PreToolUse` trial | One ASK prompted the user; persistent approval then skipped the hook | [Windows evaluation](../reports/test-report-zcode-glm-flash.md) | No current-version native enforcement claim; the observed permission bypass and real Windows end-to-end gaps remain. |
| Other / unlisted hosts | No supported adapter by default | Host-specific | [Self-adaptation checklist](../../adapters/INTEGRATION.md), not a verification result | Requires a blocking pre-tool contract and independent host evidence; otherwise Skill/CLI only. |

The default adapters address destructive **shell** calls. Exfil-guard also has
a CLI/cooperative path and the narrowly scoped, opt-in DSH text-read prototype
above. This table does not imply universal interception of model context,
uploads, or every output channel.

## Local MCP tools introduced in 0.2.7

The optional [local stdio MCP adapter](mcp.md) provides explicit safe_delete,
ordinary restore, transaction/status queries and GC preview under one
startup-bound workspace/session. It does not intercept shell, script or
editor operations that bypass those tools. Its Python 3.10+ SDK environment
is separate from the standard-library Core/CLI requirements.

The guide records the 0.2.7 installed-package/protocol checks and the earlier
bounded Kimi Code 2.3.0 root-agent native-MCP trials with their original
checkpoint and gateway limits. A separate
[Codex 0.165.0 acceptance](mcp.md#codex-01650-with-published-027) used published
0.2.7: three actual-client groups and three real-model tasks, with 16 model MCP
calls. Its recorded results and incomplete native-client exit metadata retain
their stated limits. These cooperative MCP observations do not expand the
native-hook rows above or establish live Claude/Cursor, arbitrary subagent,
other-version or additional-platform coverage. Untested client setup examples
remain documentation guidance.

The [MCP doctor introduced in 0.2.8](mcp.md#diagnose-a-local-service-028)
checks a selected service through handshake and read-only calls. Its PASS
does not establish host connection, model use or native interception; those
remain separate evidence levels.

Version 0.3.0 adds `cleanup_plan`, `cleanup_execute` and
`cleanup_task_status`, reusing the same approved task records as CLI.
Terminal approval is the default; the optional
[Codex confirmation channel](mcp.md#host-confirmation-030-development) adds
native-form approval of a saved selection. doctor expects ten tools by
default or eleven with `--approval-channel codex`, and still queries only
the original three state tools. Runtime/SDK, operator-approved protocol-client execution,
ordinary model maintenance in Codex/Kimi/DSH and completed-task replay have
recorded results. Codex, DSH and Kimi each passed first execution of a fresh
operator-approved 71-root task; Claude model work is deferred.
The opt-in Codex channel also passed a
[real-model native-form trial](../reports/test-report-maintenance-0.3.0.md#model-host-confirmation-2026-11-15):
seven model MCP calls for one saved 71-root synthetic plan, one operator
confirmation and one execution, followed by independently verified MCP recovery.
These results do not extend the native-hook evidence above. See the
[current MCP host table](../reports/test-report-maintenance-0.3.0.md#host-acceptance).
See [the task contract](mcp.md#reviewed-maintenance-tasks-030-development).

Version 0.2.9 adds the read-only `cleanup_discover` and `cleanup_measure`
tools. Local source checks include real-project stdio observations and one
bounded Kimi Code inventory task in a synthetic workspace (three MCP calls).
That task covers the new observation path, not a fresh cross-host acceptance
of the published package or native interception. See the
[0.2.9 validation](../releases/release-notes-0.2.9.md#validation).

## Acceptance for each native adapter

1. Pin host version, profile/configuration, tool name, shell dialect, and
   whether the call originates in the root agent or a subagent.
2. Confirm a harmless call reaches the hook and continues. Confirm an ASK
   either prompts the human or is denied; never silently treat it as allow.
3. Submit a synthetic command that would create a uniquely named sentinel
   **only if** the host ignores the guard's BLOCK. Verify the hook verdict and
   independently verify the sentinel does not exist. No real user data or
   dangerous deletion command is needed for this enforcement check.
4. Exercise a guard failure/missing dependency and verify fail-closed behavior.
   Distinguish an adapter failure from a host that never loaded the adapter.
5. Report `OBSERVED`, `NOT OBSERVED`, `NOT VERIFIED`, or `INCONCLUSIVE` per
   probe. Do not promote adapter-level output to execution-level evidence.

Host versions may change these contracts. Re-run acceptance after a host
upgrade or when a new tool/event path is added.
