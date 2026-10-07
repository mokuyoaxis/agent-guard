# DSH native text-read redaction prototype — 2026-09-30

## Scope and runtime

The source candidate adds an explicit `readResultGuard: true` flag; it remains
off in the default bundle and existing Lab paired profiles. No model trial,
network request, package installation, publication or remote change was made.

Tested locally on Android/PRoot Debian ARM64 with Node `22.23.2` and Python
`3.13.5`. The selected DSH CLI is `0.1.5-rc.1`; its actual `dsh-tools`,
`dsh-tool-fs`, `dsh-fs-local`, `dsh-agent-loop`, Session and JSONL backend
dependencies are `0.1.5-rc.2`. The feature refuses an unknown CLI/runtime/FS
version or changed tools/FS entry artifact before registering its listener.

## Implementation

- A fresh private `dsh-read-redaction-v1` fixture mixes ordinary Chinese config,
  an ordinary random Lab marker, a synthetic GitHub-shaped token and a
  synthetic multiline private-key envelope. Values are generated at runtime;
  private fixture files/control are create-only with modes 0700/0600. The
  control file is outside the fixture tree.
- A bounded Python stdin worker delegates to the existing `sanitize_text`
  scan/policy and applicator. It scans across line boundaries, replaces entire
  sensitive spans, preserves line numbering with blank continuation lines,
  and rescans its result. It never accepts Core ASK/BLOCK as clean output.
- The post hook replaces `value`, allowing the native read contract to
  regenerate both rendered `content` and presentation `meta`. Blocking drops
  the original value and metadata. Ordinary config text remains unchanged.
- The fixed local worker command carries no payload in argv or temporary
  files. Worker diagnostics are suppressed. Input/output have byte caps;
  timeout, cancellation, malformed input, scanner failure, incomplete reads,
  context attachments and unsupported bindings produce fixed reason codes.
- Node 22, pinned runtime/provider identity, read schema/projectors, an absent
  custom finalizer, matching workspace and Core readiness are startup gates.
  The listener wraps downstream post policies; supported scoped tool shadows
  are denied through the runtime's monotonic tool guard.

## Native validation

`tests/probes/dsh_read_redaction.mjs` loads the actual installed Cordis, tools,
local FS and filesystem tool plugins. It reads fresh fixture files using
native `tools.execute`, then drives the actual AgentLoop with two deterministic
synthetic streams and the actual JSONL persistence backend. The streams use no
external provider/model and reproduce the tool-call/result/request lifecycle;
the log is produced by DSH itself. Raw fixture values/logs remain outside the
repository in a fresh private evidence directory.

| Check | Result |
|---|---|
| Baseline native read content and private presentation meta | Both contain the synthetic protected values |
| Enabled read value, content and meta | Protected values absent; redaction placeholders present |
| Ordinary marker, Chinese config, trailing config and line numbers | Preserved |
| Benign read before/after enabling | Identical result |
| Next AgentLoop request and entire physical JSONL session | Protected values absent |
| Durable `tool/result` message and meta | Both contain redaction placeholders |
| Paged/offset read, missing file, oversized result, native long-line truncation | Withheld |
| Core scan-cap failure, peer content/context replacement and peer exception | Withheld without original metadata/value |
| Custom finalizer at binding check, CLI/dependency/artifact drift | Refused |
| Scoped shadow without a finalizer | Denied before its body executes |
| Cordis plugin load, failed startup and disposal | Initialization awaited; refusal propagated; disposal restores baseline read |
| External model / network / shell calls | 0 / 0 / 0 |

Reproduce with a selected pinned DSH package root:

```bash
python3 -m unittest -v tests.test_dsh_read_redaction
npm run test:dsh
node tests/probes/dsh_read_redaction.mjs "$DSH_PACKAGE_ROOT"
```

The native probe retains private evidence and prints only counts, booleans,
versions and the evidence directory. The report fixture is a zero-model
policy/enforcement control, not an `injection-probe` L1 or paired-model L2.

## Final checks

- `npm test`: all 569 Python tests passed (464.817 seconds), followed by the
  existing DSH adapter smoke and new read-worker Node smoke, both passing.
- Focused worker and repository-corpus gates: 13 checks passed. Fixture source
  constructs its envelope without embedding a literal key block; Core rules
  and exemptions were not changed.
- Final native probe: 10 withheld-result cases passed, including an earlier
  peer hook. Cordis startup refusal and disposal passed. The second request
  was positively observed with placeholders and ordinary config, in addition
  to checking protected-value absence.
- `npm pack --dry-run --json`: 101 package files, including the JS/Python
  worker, fixture generator and report; no private control/session/log data.
- `git diff --check` passed. No commit, push, release or real-model experiment
  was performed.

## Limits and next gate

The prototype covers complete, untruncated text reads in the pinned native
local-FS composition. Pagination, arbitrary custom/later finalizers, alternate
providers, PTC, images, other tools, early events, original call arguments and
preexisting session rows remain outside coverage. Invalid arguments can fail
before post-execute runs; this listener does not claim to filter those events.
Plugin composition is trusted; schema/projector checks do not authenticate
arbitrary same-process plugin code. Existing Core pattern/allowlist limits
remain, including ordinary random Lab markers being ALLOW.

The later [real-model follow-up](test-report-dsh-real-followup.md) used the
official `DeepSeek-V41-Flash` route and an explicit private feature overlay.
Its direct-read off/on experiment observed supported synthetic-secret redaction
in native content, presentation metadata and the decoded session, while useful
configuration remained readable. That is a separate enforcement check, not a
versioned injection sample or Lab L2 claim; this zero-model report retains its
original scope.

The current pair profile validator still recognizes deletion-only rows. A Lab
L2 trial requires an explicit read-redaction profile, versioned attack sample,
configuration evidence and conclusive native capture. The follow-up's standard
injection baseline stopped inconclusive on an unsupported retry record. No
existing baseline is promoted to L2 by either result.
