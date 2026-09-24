# Agent Guard 0.2.1 — source and release notes

These notes describe the `0.2.1` source. Check
[GitHub Releases](https://github.com/mokuyoaxis/agent-guard/releases) for
publication status; a version in the repository does not by itself mean a
tag or Release has been published. The root npm manifest remains private.

## What changed since 0.2.0

- Claude Code and Kimi Code now have POSIX shell bridges in their documented
  hook commands. When the bridge starts, it converts a sampled pre-adapter
  Python startup failure into a refusal instead of silently allowing that
  call. The host must still load and run the bridge for this to help.
- Kimi Code 0.42.0 keeps its host-specific `ASK` → hard-refusal mapping;
  malformed Kimi and Claude `PreToolUse` payloads are refused once their
  adapters run. The shared Core policy remains harness-neutral.
- `doctor.py kimi|claude [--config PATH] [--probe] [--json]` checks one selected
  configuration file and can run harmless local bridge/adapter probes without
  a model call. It reports `configuration`, `local_probe`, and
  `host_interception` separately. The last is always `UNVERIFIED`:
  a local PASS does not establish that a running host loaded the hook.
- The [README integration matrix](../README.md#integration-and-validation-matrix)
  links each documented host path and its tested-version boundary. The
  [unlisted-host guide](../adapters/INTEGRATION.md) gives an agent a safe
  self-adaptation and evidence checklist, not automatic support.

## Evidence and limits

The [Claude report](test-report-claude-code-harness.md) covers real CLI/hook
sessions on 2.1.270 and 2.1.273 with a scripted model endpoint, including
sampled allow/block/ask and interpreter-failure paths. It is not a real
Claude-model or Windows startup-failure test.

The [Kimi report](test-report-kimi-code-block.md) covers Kimi Code 0.42.0
and bounded root/single-child Bash refusals, recoverable deletion/restore,
ASK denial, and a pre-adapter Python-failure trial. Two request model IDs
were sampled; backend identity and future versions are not guaranteed.
Two same-turn child calls were observed but did not exercise simultaneous
hook execution.

`doctor` checks selected file shape, not effective merged settings or a live
session. Hook absence, matcher mismatch, bridge-spawn failure, host timeout,
other tools, and arbitrary subagents remain outside the demonstrated
fail-closed boundary. A malicious model or harness with the same OS
privileges can bypass this reliability layer. DSH 0.1.5-rc.1 still lacks a
confirmed current-version plugin-load and execution-level refusal trial;
real Windows host end-to-end coverage is also open. See the
[capability matrix](harness-capabilities.md) and [threat model](threat-model.md).
The optional Kimi doctor needs Python 3.11+ for TOML parsing; the Core and
hook adapter continue to support Python 3.9+.

The offline `guard-lab` honeytoken experiment, universal native-hook
adaptation, comprehensive output/audit redaction, and a public npm package
are **not** part of 0.2.1.

## Verification and start point

The local source passes `npm test`: 405 Python tests plus the DSH adapter
smoke test. This is regression evidence, not a substitute for the bounded
real-host observations above or CI on the published commit.

Use the [quick start](../README.md#quick-start-with-your-coding-agent),
then follow the adapter guide for your actual host and version. Do not use a
destructive command to test installation. Report both the protected path
and the unverified paths to the user.
