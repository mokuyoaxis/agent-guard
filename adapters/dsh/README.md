# agent-guard adapter for DeepSeek Harness

DSH integration shipped as a `dsh-plugin` bundle in the Agent Guard package.
This adapter maps the Decision Protocol onto DSH's native mechanisms and
delegates deletion verdicts to the shared Python core
(`skills/delete-guard/scripts/check.py`).
The opt-in text-read prototype reuses the existing Core exfil policy through
a bounded local worker. Both ship inside this package.

The [0.2.3 contract review](../../docs/reports/test-report-release-readiness-0.2.3.md)
verifies the default deletion path on `0.2.0-rc.2` and `0.1.5-rc.1`, and
the opt-in read path on the reviewed new local and sandbox filesystem providers.
Those are the published 0.2.5 host declarations. Lab preflight accepts both and
binds their native formats separately; unsupported session shapes remain
inconclusive. A running Web service's plugin inventory does not validate this
checkout or its agent scope. The [initial upgrade check](../../docs/reports/test-report-dsh-0.2-upgrade.md)
retains the earlier failures and correction history.

## Install

The [0.2.5 release](../../docs/releases/release-notes-0.2.5.md) includes the
startup/status diagnostics below without broadening admitted DSH versions or
native-read coverage. Install the exact stable version:

```bash
dsh plugin --profile 'your-profile' add @mokuyoaxis/agent-guard@0.2.5
```

Use an exact package version whose host contract matches your DSH installation.
The package ships prebuilt JavaScript and its Python Core; Python 3.9+ and
Git must already be available. Installation does not enable the optional
text-read guard.

As checked on 2026-10-19, the published opt-in versions are
`@mokuyoaxis/agent-guard@0.2.4-hotfix.1` for the stable-line quarantine fix and
`@mokuyoaxis/agent-guard@0.2.5-rc3` for the cumulative prerelease.
Versioned [GitHub](https://github.com/mokuyoaxis/agent-guard/releases/tag/0.2.5)
and [npm](https://www.npmjs.com/package/@mokuyoaxis/agent-guard/v/0.2.5)
receipts establish 0.2.5 availability. A reviewed checkout or tarball is also
usable as described in [getting started](../../docs/guides/getting-started.md).

The local [0.2.6 preparation](../../docs/releases/release-notes-0.2.6.md) adds
Lab version/config preflight diagnostics; it is unpublished and its tag is
pending. This checkout also admits `0.2.1-alpha.4` for the default deletion
adapter, alongside `0.1.5-rc.1` and `0.2.0-rc.2`. It retains the existing
interception and recovery logic. Alpha.4 is not admitted for optional native
read protection or Lab. See [DSH compatibility](../../docs/design/dsh-compatibility.md)
for the exact declarations and [native validation](../../docs/reports/test-report-dsh-alpha4.md)
for the evidence. Use a reviewed local package/checkout for these additions;
published 0.2.5 does not acquire them. See the [Lab runner](harness/README.md)
for the separate preflight diagnostic fields.

## What gets registered

| Contribution | Mechanism | Behavior |
|---|---|---|
| Interception | `tools/pre-execute` waterfall | destructive-looking bash runs through `check.py --enforce` first; permitted calls proceed after any required compensation, ASK uses the host approval path or denies when unavailable, BLOCK denies with explanation and remediation |
| `agent_guard_safe_delete` | model tool | recoverable delete with explicit glob expansion and manifest |
| `agent_guard_restore` | model tool | list / non-overwriting restore of quarantine transactions; forced overwrite remains human-only CLI functionality |
| `agent_guard_status` | model tool | adapter diagnostics, mode, usage, retention view, GC commands and recent decisions; no live-host certification |
| Prompt section | system prompt (order 105) | deletion discipline for the model |
| Experimental text `read` redaction | opt-in `tools/post-execute` value replacement | shared Core scans a complete read and DSH regenerates content and presentation metadata; unsupported or unscannable results are withheld |

## Startup and status diagnostics

Version 0.2.5 runs `python3 check.py --help`
before contributing the Hook/tools. This tests Python and imports in the
plugin process, without assessing a command or writing guard metadata.
Missing dependencies or a failed/timed-out startup check produce an explicit
initialization error instead of a log-only disabled adapter. The local check
has a 10-second timeout; the existing command-check timeout remains unchanged.

Startup success says that the listener was registered, while live host
interception remains `UNVERIFIED`. `agent_guard_status.adapter` reports that
distinction and the most recent checker result. It does not turn a local
Python test into a check of every shell/sandbox environment. If the plugin
never loads, its tools may be absent: inspect the host's plugin activation
status. An initialization error does not stop the host's other tools.

Checker process errors, timeout and unusable output remain denials with
static diagnostics separate from policy refusals. Relocation results state
that data still occupies storage; status includes accurate GC commands.
Status-query timeout, unsuccessful exit and unusable reports return `ok: false`
with a `STATUS_*` diagnostic and adapter facts, without changing the last
command-checker result. Partial relocation failures retain their recovery IDs
and storage guidance when files have moved.
See [status and cleanup feedback](../../docs/guides/recovery-feedback.md).

## Configuration (cordis.patch.yml row)

| Key | Default | Meaning |
|---|---|---|
| `repoRoot` | `""` (this package) | absolute path to a live agent-guard checkout for development |
| `defaultCwd` | executor default | working directory for guard invocations |
| `promptSection` | `true` | register the deletion-discipline section |
| `sectionOrder` | `105` | system-prompt section order |
| `dialect` | `""` (posix) | shell dialect for guard invocations: `posix`, `cmd`, or `powershell` |
| `readResultGuard` | `false` | enable the bounded native text-read prototype described below |
| `readGuardDshRoot` | `""` | absolute installed DSH npm package root; required when the prototype is enabled |

## Experimental text-read redaction

The default bundle keeps this feature off. Enable it explicitly in a private
composition **after** the native filesystem tool suite has registered `read`:

```yaml
config:
  repoRoot: '/absolute/agent-guard-checkout'
  defaultCwd: '/absolute/fixture-workspace'
  readResultGuard: true
  readGuardDshRoot: '/absolute/installed/@deepseek-ai/dsh'
```

This prototype pins Node 22 and exact runtime entry-file SHA-256 values.
CLI `0.1.5-rc.1` uses the reviewed `0.1.5-rc.2` tool/local-FS artifacts;
CLI `0.2.0-rc.2` uses reviewed `0.2.0-rc.2` tools and local or sandbox FS.
The current local legacy installation has a different filesystem-tool hash
and is correctly refused for this optional feature; its default deletion path
still passes. Wrong versions/artifacts, missing services, an
unsupported read schema/finalizer, or an unavailable Core worker refuse
initialization with `READ_GUARD_STARTUP_REFUSED`. The worker and Core must
belong to the same package/checkout; `repoRoot` cannot redirect this feature
to another Core. The workspace must match the agent session workspace.

A refused plugin does not guarantee that the Cordis host stops other tools.
On `0.2.0-rc.2`, an isolated composition continued executing tools after this
plugin failed initialization, with no deletion or read guard contributed by
that row. Verify successful plugin activation before using a guarded profile;
the enable flag and a running host alone do not establish protection.

Coverage is complete native text reads only: first line 1, all file lines
returned, at most 20,000 lines, no native line truncation, and a one MiB IPC
input cap including JSON overhead. Pagination and truncation return
`READ_RESULT_INCOMPLETE`; oversized IPC returns `READ_RESULT_SIZE`. Scanner
failure, timeout, cancellation, failed reads and nonempty additional contexts
are withheld with fixed reason codes. Core `ASK`/`BLOCK` are withheld too.
Existing Core detection limits still apply; an arbitrary Lab marker has no
secret semantics merely because it appears in a config field.

The worker receives structured text over stdin using a fixed Python command.
It runs as the trusted local plugin controller, outside the shell executor;
it reads no target file and performs no model/network call. Native `read`
continues to own filesystem access. The shell-policy inheritance note below
applies to the existing deletion commands, not this controller worker.

The [zero-model native report](../../docs/reports/test-report-dsh-read-redaction.md)
verifies the actual returned value/content/meta, next request, and full durable
JSONL log through the real AgentLoop with a synthetic stream. It does not
establish real-model injection mitigation or universal emission protection.
The separate [official Flash real-model follow-up](../../docs/reports/test-report-dsh-real-followup.md)
observed supported synthetic-secret redaction in tool content, presentation
metadata and the decoded session during a direct-read off/on experiment. It
preserved useful configuration and remains outside the Lab injection L2 protocol.
Custom/later finalizers, unreviewed tool providers, PTC, other tool results,
early events, tool-call arguments and preexisting session data are outside
this prototype. Plugin composition is trusted; the binding check is not
authentication of arbitrary plugins. Lab pairs enable this flag only when
the explicit [`read-redaction-v1` profile](harness/README.md#explicit-native-read-redaction-profile)
is selected; the default profiles keep it off.

## Shell dialect

Interception forwards a shell dialect to `check.py` so Windows-native
command lines are lexed with the right rules. Explicitly invalid plugin
configuration (including unknown field names) is rejected by Standard Schema
validation; only omitted fields receive defaults. Precedence for valid
configuration: the tool argument (`dialect`), then `AGENT_GUARD_DIALECT`,
then the `dialect` config
key, then `posix`. The fast prefilter screens both POSIX and Windows
vocabularies regardless of dialect. An unrecognised tool/environment selector
is forwarded verbatim rather than swapped for POSIX, so `check.py` returns
`BLOCK_DIALECT_UNKNOWN` and the call is denied with that code.

## Notes

The [0.2.4 official Flash/PTC report](../../docs/reports/test-report-dsh-0.2.4-ptc.md)
also observed sampled root/child SDK Bash collision refusals. Its original root
PTC task failed; a separate summary completed. This does not establish general
PTC Node API mediation or injection L2. The 0.2.5-rc1 documentation update adds
no new native-host/model result; use the
[capability matrix](../../docs/guides/harness-capabilities.md) for the full scope.

- Deletion commands inherit the caller's sandbox policy per invocation
  (`exec.agent.session`) through the native shell executor.
- Once the adapter has registered its pre-execute listener, Core process and
  output failures fail closed (deny). An absent/disabled bundle, a changed host
  event contract, or failure before listener registration cannot deny a call
  the adapter never receives.
- The Python core requires `python3` and `git` on PATH.
- The scoped npm package is published. Use an exact package version when
  reproducing a test; a source checkout may contain later changes.
- DSH `0.1.5-rc.1` package loading and one harmless execution-level `bash`
  block are independently verified in the
  [bounded host report](../../docs/reports/test-report-dsh-0.1.5-rc.1.md). Model,
  subagent, concurrency, ASK UI and non-`bash` paths remain separate tests.
- For repeatable baselines and reviewed guard-off/on Lab pairs, use the
  [headless capture/review runner](harness/README.md). Its separate `check`
  command is zero-model; a captured trial remains inconclusive until the user
  verifies host completion and finalizes it.
