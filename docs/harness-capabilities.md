# Harness capability and evidence matrix

The Python Core and CLI are harness-neutral. A Skill or CLI being usable in a
host does **not** mean that host intercepts tool calls, and an adapter returning
`BLOCK` does **not** by itself prove the host prevented execution.
For an unlisted host, use the [self-adaptation guide](../adapters/INTEGRATION.md)
and keep its native-interception status `UNVERIFIED` until independently tested.

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
| Claude Code 2.1.270 / 2.1.273 (post-0.2.0 bridge) | `PreToolUse` for `Bash`; shared Core via Python and POSIX startup bridge | Native `permissionDecision: ask` (headless runs need an approver) | Real CLI/hook with scripted model endpoint: ALLOW, BLOCK, headless ASK, injected Python failure; see [report](test-report-claude-code-harness.md) | Hook absence/timeout, Windows bridge, other tools, arbitrary subagents and real model behavior are not covered. |
| DSH 0.1.5-rc.1 | `tools/pre-execute` for `bash`, plus model tools and prompt section | `kind: ask` | [Real host, no-model packaged-plugin probe](test-report-dsh-0.1.5-rc.1.md): benign execution, Core `BLOCK`, absent bypass marker, durable audit, and loaded-adapter Core-startup failure | Direct tool-runtime call only; model/Agent, subagents, concurrency, ASK UI, non-`bash` tools, hook absence and host upgrades remain unverified. |
| Kimi Code 0.42.0 / 2.1.1 | `PreToolUse` for `Bash`; reuses Claude mapping with a host-specific ASK override | Adapter hard-denies ASK; live ASK behavior was sampled only on 0.42.0 | [Bounded host-level tests](test-report-kimi-code-block.md): authenticated 2.1.1 root-Bash sentinel PASS on an OAuth official model and maintainer-confirmed official K3 relay; older 0.42.0 root/subagent BLOCK, recoverable deletion/restore and pre-adapter Python-failure refusal | The client cannot independently attest opaque relay weights. 2.1.1 subagents, concurrency, ASK UI and fault injection remain unverified; hook absence/timeout/bridge-spawn failure and non-Bash tools remain outside the claim. |
| Codex CLI 0.154.0 (tested session) | Skill and production CLI on older source; no native interception adapter in this repository | CLI reports ASK; agent/user must decide | CLI acceptance only; see [report](test-report-codex-gpt-6-astra-high.md) | No automatic tool-call enforcement claim. |
| ZCode (Windows session; host version unrecorded) | Historical Skill/CLI and process-type `PreToolUse` trial | One ASK prompted the user; persistent approval then skipped the hook | [Windows evaluation](test-report-zcode-glm-flash.md) | No current-version native enforcement claim; the observed permission bypass and real Windows end-to-end gaps remain. |
| Other / unlisted hosts | No supported adapter by default | Host-specific | [Self-adaptation checklist](../adapters/INTEGRATION.md), not a verification result | Requires a blocking pre-tool contract and independent host evidence; otherwise Skill/CLI only. |

These adapters currently address destructive **shell** calls. Exfil-guard is a
separate CLI/cooperative path; this table does not imply native interception of
file reads, model context assembly, uploads, or every output channel.

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
