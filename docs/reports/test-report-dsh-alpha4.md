# DSH alpha.4 default deletion adapter

Tested 2026-10-22 on ARM64/PRoot Debian with Node v22.23.2 and Python 3.13.5.
At validation, the selected Agent Guard checkout identified as 0.2.6 and was
unpublished. The tested additions now ship in [0.2.6](../releases/release-notes-0.2.6.md).
This report adds default deletion evidence for DSH `0.2.1-alpha.4`; the
[compatibility page](../design/dsh-compatibility.md) states the separate
optional-read and Lab boundaries.

## Actual CLI and bundle loading

Three isolated runs launched the actual DSH CLI using an owned profile with
`dsh-base` and a local link to this checkout's package. Python Core came from
that selected package, with no `repoRoot` override. Homes, settings, profiles,
Git workspaces and recovery directories belonged to the trials. No user
profile was updated, and no DSH/dependency installation was performed.

The CLI roots were `0.1.5-rc.1`, `0.2.0-rc.2` and `0.2.1-alpha.4`.
The legacy root's key SDK packages are `0.1.5-rc.2`; root and SDK versions
were not assumed interchangeable. All three completed the following seven
checks through the real Profile/Boot and native Bash/shell services:

- Load the selected bundle and register its three recovery tools.
- Execute a harmless command.
- Reject a force-push-shaped command before its harmless bypass marker runs.
- Reject a wildcard deletion shape, preserving the owned log file.
- Reject an ASK when the direct call has no agent/approval route.
- Query status without promoting `host_interception` above `UNVERIFIED`.
- Quarantine an owned valuable file, report retained disk use and GC guidance,
  then restore its original bytes through the registered tool.

## Root and native child agents on alpha.4

The actual AgentLoop and native continuable subagent service ran five actors.
A local scripted LLM adapter emitted 15 fixed streams and ten Bash calls;
there were no external model requests. Bash, the deletion guard and the native
approval service were not replaced.

| Actor | Observed result |
|---|---|
| Root BLOCK | Benign command executed; force-push-shaped call refused; bypass marker absent |
| Native child BLOCK | Depth 1 and parent lineage recorded; same refusal and absent marker |
| Root ASK with policy `never` | Native approval rejected the call; marker absent |
| Native child ASK | Native delegated approval policy rejected the call; marker absent |
| Root ASK with fixed local answerer | Native approval recorded `allowed-once`; only the owned harmless approval marker ran |

The approval grant used a deterministic answerer for one owned sentinel.
It does not establish interactive approval UI behavior. The first attempt used
the older `subagents.start` API: its root case passed, then the child driver
failed because alpha.4 exposes `startActivation` instead. That failed attempt
was retained. Updating the test driver to the actual SDK interface required
no product adapter change.

Observe-only instrumentation checked each of the five checker requests against
the invoking agent session's native resolved policy. Workspace, signal and
the existing 90-second timeout were retained. Native execution used
`danger-full-access`. Unit checks additionally alternate distinct root/child
`read-only` and `workspace-write` policies to detect caching across callers.
These are forwarding checks, not an OS sandbox-confinement certification.

## Loading failures and feature separation

Three additional alpha.4 CLI trials confirmed:

- Injected startup Python failure reports `GUARD_STARTUP_FAILED`, contributes
  no guard tools and leaves the host able to run other tools.
- An absent bundle contributes no interception; the harmless marker runs.
- Explicitly enabling optional read redaction on alpha.4 reports
  `READ_GUARD_STARTUP_REFUSED` and contributes no deletion/read guard from
  that failed plugin row. Leave it disabled on alpha.4.

The default version expansion does not update native-read artifact hashes or
Lab session/config parsers. Their previous two-host contracts remain intact.

## Manifest and regression scope

The default adapter's three-version profile matches top-level `engines.dsh`,
the optional DSH peer and nested `dsh.engines.dsh`. Actual rc.2 and alpha.4
compatibility helpers accepted all three declared versions and rejected twelve
unlisted versions each: six accept and 24 rejection checks, without exemptions.
The optional peer supplies compatibility metadata without installing DSH.
The legacy loader was checked through actual Profile/Boot rather than a
modern helper it does not provide.

The broader 12-version SDK experiment used controlled shell/synthetic Bash
tool bodies. Its remaining nine versions were not promoted into the published
declaration. This report's CLI checks use real native execution.

Package-manifest and DSH smoke/regression checks accompany this change.
The previous 974-test full run predates this expansion and is not its full-suite
result. Core decisions, command recognition, rm wildcard refusal, compensation,
recovery formats and the production JavaScript adapter retain their bytes.
Existing Kimi maintenance changes are separate from this DSH addition.

## Limits

No external model, graphical UI, arbitrary PTC/API calls or concurrent-agent
trial was run. Policy forwarding does not prove OS sandbox enforcement.
Agent-less checks do not imply subagent coverage; the separate native child
cases above supply that bounded evidence. Package version, local test success
and a running host do not establish publication or activation in a user's
existing profile. Tag and release decisions remain pending.
