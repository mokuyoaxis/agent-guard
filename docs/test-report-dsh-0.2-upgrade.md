# DSH 0.2 upgrade and Core separation check

Date: 2026-09-30 (UTC)

This is the initial upgrade snapshot. The later
[contract and release review](test-report-release-readiness-0.2.3.md) records
the subsequent runtime admission, native v4 support and fresh validation.
The limitations below describe the source at this initial checkpoint.

The unchanged Core works with the upgraded native DSH deletion seam after
a small adapter correction. Full project compatibility with `0.2.0-rc.2`
has **not** passed: the production read guard and Lab still pin the previous
runtime, and the running Web service reports a different installed Guard
bundle. No new external model call or Kimi OAuth quota was used.

## Runtime and isolation

The default local CLI reports `0.2.0-rc.2`. Authenticated, read-only Web RPCs
also report official base/Web bundles at `0.2.0-rc.2`, 197 plugin rows and
165 active rows. `agent-guard` is active; `agent-guard-dsh` is disabled as a
plugin row, while its installed bundle reports version `0.1.1` and enabled.
Bundle selection and plugin activation are different projections. These
responses do not identify the active implementation as this `0.2.3-rc2`
worktree, or prove interception within an agent's Web context.

The supplied access token and resulting cookie stayed in memory. Only
allowlisted counts, names, versions and states were retained. No live Web
configuration, session, account, model selection or workspace was changed.
Browser UI behavior was not tested; the Web checks used the installed
Connection RPC envelope and cookie authentication.

Native execution tests used new private homes and Git workspaces outside
the repository, on Android/PRoot Debian ARM64 with Node `22.23.2` and Python
`3.13.5`. The initial fresh profile defaulted to `workspace-write`; the host
refused its benign command because no confinement backend was usable.
Subsequent execution probes explicitly set `danger-full-access` in their
private profiles. This is the scope of those results, not a test of
workspace-write confinement or a change to the running Web deployment.

## Default adapter: failure, correction and native comparison

The initial adapter called `shell.run(shell.resolve(request))`.
DSH `0.2.0-rc.2` exposes `execute(spec)` returning an execution handle whose
`result()` resolves to the foreground outcome. The obsolete call was caught
as an infrastructure failure. It withheld the test command, but produced
no Core audit; it was not a successful policy decision.

The local correction resolves the same request, uses `execute(...).result()`
when available, and retains `run(...)` for the tested earlier host. It changes
no Core rule, request policy, tool arguments, public interface or read pin.
Failures during preparation or result collection remain fail-closed, and
never retry through the legacy path after execution starts.

The native probe's dangerous-looking text is an inert shell comment. If
unguarded, the command creates only a harmless marker. No destructive
command is executed.

| Private native composition | Benign marker | Guarded marker | Core block audit | Result |
|---|---|---|---|---|
| Initial new host, default sandbox | absent; native sandbox error | absent; adapter infrastructure error | absent | failed |
| Initial new host, explicit host access | created | absent; adapter infrastructure error | absent | failed |
| Isolated corrected adapter, new host | created | absent | `BLOCK_UNDETERMINABLE_EFFECT` | passed |
| Corrected worktree adapter, new host | created | absent | `BLOCK_UNDETERMINABLE_EFFECT` | passed |
| Corrected worktree adapter, old `0.1.5-rc.1` host | created | absent | `BLOCK_UNDETERMINABLE_EFFECT` | passed |

The 19 Core and shared skill-script sources match the upgrade baseline
byte-for-byte, with aggregate SHA-256:

`be770e825289a67ce2d7084b0be8e84a29b95048960ecf5ae43ccec0ae98c02a`

This supports the separation at these tested seams: host API translation
belongs in the adapter; decision policy remains in Core. It does not establish
compatibility for every host provider, sandbox or agent scope. The npm entry
still selects the DSH adapter and the package engine still pins the old host.

## Read guard startup and research seam

The unchanged production read guard rejects the new runtime with
`READ_GUARD_STARTUP_REFUSED`, as required by its exact version/artifact pins.
An important distinction was observed: **the Cordis host continued running
other plugins after this row failed**. With host access enabled, both harmless
markers were created and no Core block audit existed. Since adapter startup
awaits read-guard initialization before contributing deletion tools/hooks,
that failed row supplied neither protection. A refused plugin is not proof
that the entire host refuses tools.

A separate research composition used the new native `read`, LocalFileSystem,
ToolRuntime and real AgentLoop, with the unchanged Core worker called from a
private post-execute hook. It did not relax or replace the production runtime
gate. The native read binding conformance check passed, as did:

- Exposure of both supported synthetic secret classes in baseline content/meta.
- Redaction from guarded value/content/meta, while preserving benign content,
  Chinese text, line numbers and an ordinary random marker.
- Withholding an incomplete paginated read.
- Clean next-request messages and full durable JSONL, using two deterministic
  synthetic streams and zero external model/network/shell calls.
- Restoring the unguarded baseline after research-hook disposal.

The new ToolRuntime result-hook probe also retained the previously observed
contract: content-only replacement leaves original presentation metadata;
value replacement regenerates it; a later finalizer can reintroduce content.
These are research results, not production read-guard admission, Web sandbox
provider coverage, a real-model injection trial or an L1/L2 claim.

## Lab gate and remaining acceptance work

The current Lab preflight refuses the actual new CLI with:
`DSH version preflight requires exactly 0.1.5-rc.1`. No capture, pair or
historical receipt was upgraded, and no unsupported real-model run was launched.
The separately generated 17-record synthetic native session has native
header **version 4**. The current parser requires native header version 3
and returns `UNSUPPORTED_SESSION_HEADER`, discarding all recognized channels.
This native format version is distinct from Lab capture receipt version 4.
The sample is retained for offline research; it does not validate every new
event or retry form emitted by a full new-host profile. A new format needs
explicit versioned interpretation while preserving prior receipts/parsers.

Remaining acceptance work is to review/admit the new read providers and
artifact bindings, validate the new Lab composition/session/retry contracts,
and establish which code and hooks run in the actual Web agent scope. Only
then should a fresh, matched official `deepseek-official / deepseek-flash`
(`DeepSeek-V41-Flash`) pair establish any real-model conclusion. Prior
[retry evidence](test-report-dsh-retry-evidence.md) and
[real-model follow-up](test-report-dsh-real-followup.md) retain their original
runtime and interpretation.

## Validation

The pre-change full suite passed 590 Python tests in 536.943 seconds and
both Node smoke suites. After the correction, the full suite again passed
590 Python tests in 774.065 seconds and both Node smoke suites. The added
Node cases exercise the new execution
handle, status tool completion, preparation/result failures, cancellation
signal and cwd forwarding, and absence of legacy fallback after a new API
failure. Native old/new host probes passed after the correction.
Both repository corpus checks, syntax/LF/whitespace checks, `git diff --check`
and 224 local links across 54 Markdown files passed. The npm dry-run includes
105 files, including this report and no private controllers, captures or
compiled Python files. The 19 shared sources have no reverse DSH runtime
imports. Private receipts retain hashes and the before/after snapshots.
No hosted CI, Windows/PowerShell, publication, commit or push was performed.
