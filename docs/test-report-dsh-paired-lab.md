# DSH matched Lab workflow and result-rewriting investigation

Date: 2026-09-30 (UTC)

This work strengthens comparison prerequisites, adds a reviewed matched DSH
workflow, and investigates the installed native result-rewriting contract.
It made **no new model request** and establishes no new real L1/L2 outcome.
Historical evidence and the retained-log review were not rewritten.

This report describes receipt version 3 and its four-channel scan scope. The
later [retry-evidence follow-up](test-report-dsh-retry-evidence.md) adds version
4 coverage for new pairs and preserves the historical interpretation below.

## Comparison and orchestration

Generic comparison now requires a valid matching lowercase SHA-256 for the
declared non-guard configuration and the same actual scanned `stage` /
`source_kind` set. Reports rebuild that set from scan events; an empty native
tool channel still has a completed scan. Missing fingerprints, CLI-only versus
native coverage, or different source kinds return `INCOMPARABLE`, before any
mitigation claim. Individual historical reports remain readable. New Kimi
captures attach their existing semantic non-Guard configuration fingerprint
to the Lab trial so they meet the shared comparison gate.

DSH receipt version 3 binds the new `dsh-lab-non-guard-v1` fingerprint. Its scope
is the pinned launcher's rendered non-Guard rows, saved settings, home `.env`,
the headless package manifest and inherited launch environment. Only source
comments and the exact active local Guard row are removed. Literal blank
lines remain configuration data. The disposable `DSH_HOME` is normalized;
`PWD`, `OLDPWD`, `_` are excluded. Raw settings/environment values are never
printed or retained in public evidence. Authentication, backend identity,
external plugin-owned files and installed dependency code are outside this
fingerprint's scope.

`prepare-pair` copies a bounded standard headless bootstrap into four fresh
private homes, omitting sessions, storage and unrelated files. It creates one
local-source Guard overlay and checks equivalent non-Guard configurations.
`pair-next` launches at most one host task through:

1. clean-off;
2. clean-on;
3. guard-off baseline;
4. guarded trial, only after the baseline exposed bait.

Each capture stops for explicit completion review. Repeating `pair-next` while
review is pending makes no new task request. Failed/unhealthy/exposed controls
stop the sequence; a quiet baseline leaves guarded unlaunched. Current assets,
task hashes, scoped settings and absence of prior sessions are checked before
the next launch. Manual Core completion alone cannot override a failed
capture gate. The DSH `compare` wrapper revalidates receipt/native bytes and
requires the exact four stdout/stderr/tool/assistant scan sources.

Output summaries distinguish tool and assistant markers, fake Lab execution
and loopback URL contact. One host task can make multiple model requests;
`host_tasks_launched` counts tasks only. Guard-on selects the existing
deletion adapter, which does not enforce tool-result redaction.

## Real boot-free preflight

The installed DSH launcher was `0.1.5-rc.1`; its actual `dsh-tools`,
`dsh-agent-loop` and `dsh-tool-fs` dependencies were `0.1.5-rc.2`. Node was
`22.23.2`, and local Python was `3.13.5` on Linux ARM64 / PRoot.
The launcher version does not lock every installed dependency version; the
probe records and requires the actually tested tools-runtime version.
A disposable source from the previous official-model baseline was copied
privately. All four `--version` / `--dump-config` / decoder preflights passed,
with no source modification, prior session copy, model request or package
installation. The scoped fingerprint matched across all four homes:

`6b80d2bcc935cfeb5dd45cdfd363da6602cc1e75b6387ae469efa093e67bf43f`

Rechecking all four final homes produced the same recorded fingerprint and
zero prior sessions. Source bootstrap hashes still matched the copied-input
manifest. Earlier development plans were retained privately; a plan created
before normalization changed is not reused for the final check.

The real launcher resolves the local Guard module name to a `file://` URL.
An initial preflight correctly refused that unfamiliar representation; the
normalizer now recognizes the exact same local module in path or file-URL
form, with a regression for both. It continues to refuse unknown, duplicate,
disabled or differently configured Guard rows. The incomplete first private
copy was retained separately; no existing files were removed or overwritten.

## Native result hook investigation

Primary references are the installed pinned `dsh-tools`, `dsh-agent-loop` and
`dsh-tool-fs` distributions, with the upstream
[tool pipeline](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/tool-execution-pipeline.md)
and [tool contract](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/subsystems/tools.md)
as supplementary documentation. Upstream master is not substituted for the
locally tested version.

The native pipeline runs post-execute policy, definition-owned final content,
then authoritative result notification. The agent loop subsequently appends
the model-facing content and private result metadata to `tool/result`.
The pinned `read` tool's presentation metadata contains the returned lines.
Replacing only `content` therefore leaves a second copy of read text in the
private session/UI data.

The [native probe](../tests/probes/dsh_result_hook.mjs) instantiates the real
Cordis, SystemPrompt and ToolRuntime services, registers synthetic tools and
tests their results without a model, shell, session or network request:

```sh
node tests/probes/dsh_result_hook.mjs /absolute/path/to/installed/@deepseek-ai/dsh
```

| Native probe | Observed |
|---|---|
| `tools/post-execute` replacement changes model-facing content | yes |
| Content-only replacement leaves original presentation metadata | yes |
| Replacing the typed `value` regenerates content and metadata | yes, for the synthetic supported schema |
| A later tool-owned `finalizeContent` can restore original text | yes |
| Invalid non-JSON arguments can bypass post-execute | yes |
| Final `tools/result` receives the frozen authoritative outcome | yes |

These are runtime-mechanics observations, not an implemented exfil-guard hook
or proof of remote delivery. `tools/result` is an observer, so it cannot be
used to repair bytes already materialized. PTC sub-dispatch logs have their
own `tools/ptc-dispatch-log` seam. Additional contexts, tool-owned earlier
events, attachments, error text and finalizers require separate coverage.

### Proposed next implementation scope

A small opt-in prototype can target the pinned native text `read` tool:
validate its typed value, pass bounded text to the existing Core redaction
policy, and replace the supported value so the registry regenerates both
model-facing content and presentation metadata. Any ASK/BLOCK or scanner
failure must be represented explicitly; CLI `sanitize.py` exit zero alone
does not certify an ALLOW decision. Preserve valid read schema/line numbering
and verify the durable native result as well as rendered content.

This proposal needs a defined failure policy and scope before becoming a
production protection capability. It must not claim coverage for arbitrary
tools, custom finalizers, errors, PTC, attachments or earlier logging. Existing
Core rules should be reused; recognizing Lab canaries specially would overfit
the instrument. A synthetic note marker is not automatically a secret, so
the next fixture should separately identify protected secret-shaped data and
ordinary context. Clean controls must show useful text remains readable.

A separate zero-model Core check used newly generated, non-credential values:

| Synthetic input | Existing `llm-request` policy | Original value retained |
|---|---|---|
| Plain Lab note marker | `ALLOW` | yes |
| Lab marker in the service-token assignment | `ALLOW` | yes |
| Value matching an existing fixed-prefix secret rule | `SANITIZE` | no |

The existing two marker classes are exposure instruments, not an expectation
that the current Core will redact them. A future redaction fixture must use
supported secret-shaped synthetic data and keep ordinary context separate.

## Verification

Focused regressions cover native/CLI channel mismatch, missing/unequal
configuration, source-kind mismatch, private fresh bootstrap, malformed Guard
rows, settings drift, pending review, quiet-baseline stop, not-mitigated
outcomes, capture tampering and cross-process continuation. All use synthetic
subprocess hosts and loopback observation; their L2-shaped assertions are not
real-harness mitigation evidence.

The final `npm test` passed **558 Python tests and the DSH adapter smoke test**.
The earlier 556-test run found one corpus false positive in the new bootstrap
filename references; using the existing Kimi copier's naming convention
resolved it, and the complete final suite passed. The final seven targeted
profile/gate/CLI checks and separate nine corpus/package checks also passed.

The real boot-free preflight, repeat checks and native result-hook probe passed.
Package dry run included **97 files**, including the new profile helper and
this report, with no private session/capture/evidence artifacts. Syntax,
LF/trailing-whitespace and Git difference checks passed. The Node 22 CI job
now includes profile/pair regressions; its hosted run was not executed locally.
No production dependency, protection policy, version, commit or remote state
was changed by this follow-up.

## Later real-model follow-up

The [official Flash follow-up](test-report-dsh-real-followup.md) subsequently
ran both clean members and the `maintenance-v1` baseline. The baseline retained
an unsupported `assistant/attempt` record, so the capture gate stopped the pair
inconclusive before guarded. A separate direct-read experiment enabled the
opt-in read prototype and observed supported synthetic-secret redaction; it
does not alter this deletion-only workflow or establish injection L2 evidence.
