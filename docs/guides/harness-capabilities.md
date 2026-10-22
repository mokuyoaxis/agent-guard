# Harness capability and evidence matrix

Stable release line: [0.2.5](../releases/release-notes-0.2.5.md).
It retains RC1–RC3 and adds clearer startup, command and storage
feedback. Published prerelease, checked on 2026-10-19:
[0.2.5-rc3](../releases/release-notes-0.2.5-rc3.md).
Rows below retain their tested source/host versions and do not automatically
certify a later release. Local regression or package checks are separate from
new native-host/model evidence.

[0.2.6 preparation](../releases/release-notes-0.2.6.md) adds optional Kimi Stop
feedback, separate doctor checks, DSH Lab diagnostics, alpha.4 default
deletion compatibility and Kimi 2.3.0 root-Bash compatibility. It is unpublished;
the tag decision is pending. The source-only row below does not describe
published 0.2.5. See [unreleased maintenance](../releases/unreleased.md).

The new [research notes](../research/README.md) describe bounded private
experiments; their disclosure/delegation methods do not change the supported
production coverage in this matrix.

The Python Core and CLI are harness-neutral. A Skill or CLI being usable in a
host does **not** mean that host intercepts tool calls, and an adapter returning
`BLOCK` does **not** by itself prove the host prevented execution.
For an unlisted host, use the [self-adaptation guide](../../adapters/INTEGRATION.md)
and keep its native-interception status `UNVERIFIED` until independently tested.

We currently focus on DSH and Kimi Code. Claude Code's adapter and past
results remain available; testing new Claude versions is paused. For other
hosts, ask the official agent to configure and test the integration locally.

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
In the unpublished 0.2.6 Kimi source, a separate `feedback` object checks optional
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
| DSH 0.2.1-alpha.4 / Node 22 (unpublished 0.2.6 checkout) | Default deletion adapter; three recovery tools and prompt guidance | Native approval service; sampled rejection and fixed answerer grant | [Native CLI report](../reports/test-report-dsh-alpha4.md): actual Profile/Boot, Bash allow/block, quarantine/restore, root/child BLOCK and ASK, per-session policy forwarding; no external model calls | No optional read or Lab admission. Interactive approval UI, OS sandbox confinement, arbitrary PTC, concurrent agents, GUI activation and real-model behavior are unverified. |
| Kimi Code 0.42.0 / 2.1.1 | `PreToolUse` for `Bash`; reuses Claude mapping with a host-specific ASK override | Adapter hard-denies ASK; live ASK sampled on 0.42.0 | [Bounded report](../reports/test-report-kimi-code-block.md): authenticated 2.1.1 root-Bash sentinels; older 0.42.0 root/subagent enforcement; later actual-CLI fixed-loopback fault cases on both versions | 2.1.1 subagents, concurrency and live ASK UI remain unverified. Missing/timed-out hooks allowed tools in the fault cases; other tools and arbitrary spawn failures remain uncovered. Client route labels do not identify remote model weights. |
| Kimi Code 2.3.0 (unpublished 0.2.6 checkout) | Root `Bash` PreToolUse in prompt CLI and ACP; ACP uses Kimi's local executor | Adapter hard-denies ASK | [Sixteen actual-host loopback trials](../reports/test-report-kimi-code-block.md#kimi-230-acceptance): allow/BLOCK/ASK, Python startup refusal, absence control, recovery; ACP process/payload cwd matched the session workspace despite a different service startup cwd | No external model calls. Subagents, other tools, client-provided terminals, concurrency, background execution and host-timeout behavior remain unverified. Hook absence allowed the inert marker. Web checks covered startup/status/shutdown only. |
| Kimi optional Stop, 0.42.0 / 2.1.1 / 2.3.0; unreleased source | Recovery guidance after the initial answer; separately configured POSIX hook | No additional approval path | [Feedback follow-up](../reports/test-report-kimi-code-block.md#recovery-feedback-follow-up-2026-10-20-unreleased-source): actual-CLI fixed-loopback delivery and one bounded 2.1.1 real-model session. [2.3.0 prompt/ACP follow-up](../reports/test-report-kimi-code-block.md#kimi-230-acceptance): actual txids, storage/GC guidance and byte-exact recovery with fixed responses | Adds a response after new compensation records. The initial answer may be inaccurate; emission is not acknowledgement. Shared-session actors, exhausted budgets and broader model/scheduling behavior retain their limits. |
| Codex CLI 0.154.0 (tested session) | Skill and production CLI on older source; no native interception adapter in this repository | CLI reports ASK; agent/user must decide | CLI acceptance only; see [report](../reports/test-report-codex-gpt-6-astra-high.md) | No automatic tool-call enforcement claim. |
| ZCode (Windows session; host version unrecorded) | Historical Skill/CLI and process-type `PreToolUse` trial | One ASK prompted the user; persistent approval then skipped the hook | [Windows evaluation](../reports/test-report-zcode-glm-flash.md) | No current-version native enforcement claim; the observed permission bypass and real Windows end-to-end gaps remain. |
| Other / unlisted hosts | No supported adapter by default | Host-specific | [Self-adaptation checklist](../../adapters/INTEGRATION.md), not a verification result | Requires a blocking pre-tool contract and independent host evidence; otherwise Skill/CLI only. |

The default adapters address destructive **shell** calls. Exfil-guard also has
a CLI/cooperative path and the narrowly scoped, opt-in DSH text-read prototype
above. This table does not imply universal interception of model context,
uploads, or every output channel.

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
