# Harness capability and evidence matrix (0.2.0-rc1)

The Python Core and CLI are harness-neutral. A Skill or CLI being usable in a
host does **not** mean that host intercepts tool calls, and an adapter returning
`BLOCK` does **not** by itself prove the host prevented execution.

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

## Current coverage

| Host / tested version | Entry and scope | ASK mapping | Highest evidence for current path | Important gap |
|---|---|---|---|---|
| Claude Code 2.1.270 | `PreToolUse` for `Bash`; shared Core via Python | Native `permissionDecision: ask` (headless runs need an approver) | Real CLI/hook with scripted model endpoint; see [report](test-report-claude-code-harness.md) | Other tools, arbitrary subagents and all model behaviors are not covered. |
| DSH 0.1.5-rc.1 | `tools/pre-execute` for `bash`, plus model tools and prompt section | `kind: ask` | Adapter smoke with mocked services; [older v0.1.1 report](test-report-dsh-v0.1.1.md) is not current-version proof | Current-version plugin loading and execution-level `BLOCK` remain unverified. |
| Kimi Code 0.42.0 | `PreToolUse` for `Bash`; reuses Claude mapping with a host-specific ASK override | Hard deny (`exit 2`), because this version treats `ask` as allow | Real hook observations in two isolated `local/kimi-k3` sandboxes, including sampled subagents | Real-host execution-level `BLOCK` not proven; non-Bash tools and other models untested. |
| Codex (tested sessions) | Skill and production CLI; no native interception adapter in this repository | CLI reports ASK; agent/user must decide | CLI acceptance only; see [report](test-report-codex-gpt-6-astra-high.md) | No automatic tool-call enforcement claim. |
| ZCode (tested Windows session) | Skill/CLI evaluation | CLI reports ASK | [Windows evaluation](test-report-zcode-glm-flash.md) | No native hook or general Windows end-to-end guarantee. |
| OpenCode / MCP | No adapter | Not applicable | Not supported | Requires separate host contract and evidence. |

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
