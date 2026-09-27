# DSH 0.1.5-rc.1 adapter acceptance (Agent Guard 0.2.2 candidate)

Date: 2026-09-27 (UTC)

This report records a bounded, execution-level acceptance test of the DSH
adapter. It supersedes the DSH row's earlier mock-only status; it does not
replace the historical [Agent Guard v0.1.1 trial](test-report-dsh-v0.1.1.md).

## Environment and privacy boundary

- Host: DeepSeek Harness `0.1.5-rc.1`
- Runtime: Node.js `22.23.2`, Linux ARM64
- Agent Guard: local `0.2.2` candidate tree
- Profile: disposable DSH home and nested disposable Git workspaces
- Model calls: **none**
- Network-dependent behavior: **none in the probes**

The retained evidence contains booleans, counts, reason codes, byte counts and
SHA-256 digests. It does not retain model output, credentials, account names,
home-directory paths or the raw test command. No destructive operation was
performed: a bypass could create only a uniquely named empty marker file.

## Paths tested

Two equivalent loading paths were exercised:

1. DSH loaded the adapter source directly through an isolated patch.
2. `npm pack` produced a local tarball; `dsh plugin --profile ... add` installed
   it into an isolated profile; DSH discovered the package's own
   `cordis.patch.yml` bundle and loaded the packaged adapter.

The second path matters because it covers package resolution and the shipped
bundle rather than importing a rewritten adapter in a test harness. During
this work the adapter's unnecessary runtime import of a host-internal helper
was removed, so the package no longer depends on DSH's private dependency
layout merely to load.

## Results

| Probe | Host observation | Independent observation | Result |
|---|---|---|---|
| Benign `bash` call | DSH returned success | allow marker existed | `OBSERVED` |
| Core `BLOCK` | DSH returned an error carrying the Agent Guard block reason | bypass marker absent; one durable `enforce-block` event with `BLOCK_UNDETERMINABLE_EFFECT` | `ENFORCEMENT OBSERVED` |
| Packaged bundle | DSH composition contained the package bundle and adapter row | packaged run produced the same block code and absent marker | `OBSERVED` |
| Core process startup failure after hook registration | DSH returned the adapter's fail-closed error | bypass marker absent | `OBSERVED` |

The synthetic blocked input used an stdin-fed shell shape. Its only active
effect in a bypass was marker creation; deletion-shaped text was inert. The
test therefore proves that this DSH version invoked the registered
`tools/pre-execute` listener and prevented that specific `bash` body from
running, without risking user data.

## What this proves

For DSH `0.1.5-rc.1`, the tested package and configuration can:

- load as a DSH bundle from a normal local package installation;
- let a benign global `bash` execution continue;
- translate a Core `BLOCK` into a DSH tool error before execution;
- fail closed when the already-loaded adapter cannot obtain parseable Core
  output; and
- leave independent audit and non-execution evidence for the healthy block.

This reaches the repository's **Enforcement observed** evidence level for one
global, root-like `bash` path. No LLM was needed to establish host mechanics.

## Limits and unverified paths

- The probe called DSH's real tool runtime directly; it did not test model
  behavior, an Agent object, subagents, concurrent calls or approval UI.
- `ASK` mapping, compensation-bearing `ALLOW`, and adapter output handling have
  automated smoke coverage, but were not exercised through this real-host
  probe.
- Only DSH's `bash` tool was tested. Other tools, PowerShell and arbitrary
  plugin-provided execution paths are not covered.
- A failure *after the hook is registered* was fail-closed. If the package is
  absent, its bundle is disabled, the event contract changes, or the adapter
  exits before registering its listener, it cannot deny a call it never sees.
- The result is version-specific. A DSH upgrade requires drift checking and a
  repeat of the packaged sentinel before the compatibility claim moves.

Accordingly this is reliability evidence for a known host path, not a claim
that Agent Guard is a security sandbox or can contain a malicious peer with
the same operating-system privileges.
