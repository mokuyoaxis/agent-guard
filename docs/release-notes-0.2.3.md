# Agent Guard 0.2.3

Stable release, 2026-10-03. See the
[readiness review](test-report-release-readiness-0.2.3.md#stable-preparation--2026-10-03)
for validation checkpoints and evidence limits.

0.2.3 completes the Windows/Core fixes and offline guard-lab MVP planned in
rc1/rc2, and adds focused information-protection improvements. Core, the
Decision Protocol and Skills remain shared across hosts.

## Changes

- Windows/Core fail-closed fixes from rc1: known destructive vocabulary is
  checked across shell dialects, and Windows path normalization/recovery has
  focused real-Windows CI evidence. The fixes originated in
  [community Windows feedback through WorkBuddy](test-report-workbuddy-windows-core.md).
  Arbitrary Windows harness E2E remains outside the claim.
- The sanitizer withholds the body on ASK, BLOCK or error. External plans
  must match the current scan; output is rescanned before emission. Checker,
  sanitizer and plan input share bounded UTF-8 handling and policy defaults.
- URI userinfo passwords are redacted without discarding useful connection
  structure. Percent-encoded passwords and JSON-escaped slashes are supported;
  general DSN parsing and network-topology privacy are outside this rule.
- Credential rules cover PyPI publishing tokens and newer GitHub installation
  tokens, including complete long signatures and Chinese-adjacent matches.
  Placeholder exemptions require a complete placeholder shape.
- Secret-reference names use complete components, keeping ordinary names and
  metadata readable. AWS resource IDs are distinguished from access-key IDs;
  a JWT header requires a nonempty ASCII string `alg`. Ambiguous public-key
  names and generic `sk-` shapes retain their documented conservative behavior.
- `agent-guard-lab`: disposable synthetic fixtures, a bounded loopback
  observer, clean/positive/injection/snapshot controls, explicit output scans
  and privacy-minimal reports. Missing or unhealthy evidence is inconclusive.
- Reviewed real-harness capture helpers and matched comparisons: an effective
  guard-off baseline is required before declaring mitigation. Historical
  evidence retains its original format and channel coverage.
- DSH adapter translation for `0.2.0-rc.2` Shell handles, with the tested old
  default deletion path retained. Core Decision Protocol and deletion rules
  remain shared and independent of DSH.
- Default-off DSH complete text-read protection on reviewed, artifact-pinned
  providers. It replaces structured values so native content and metadata
  regenerate; incomplete, unsupported or unscannable reads are withheld.
- Bounded native session v4 support uses receipt v6 / parser v3 alongside
  preserved historical interpretation, including `role=tool` source-call
  correlation and retry-attempt evidence. Modern isolated profiles bind their
  initialized composition before capture. Surface replay, image offload and
  arbitrary session shapes remain unsupported.
- DSH/Kimi Lab startup failures retain the original observer error and never
  launch a host task or acquire a successful capture receipt.

## Evidence and limits

The offline Lab controls establish instrument calibration. Historical bounded
model trials and fresh no-model native checks are listed in the
[rc2 notes](release-notes-0.2.3-rc2.md) and
[current review](test-report-release-readiness-0.2.3.md). Pending real-model
Lab captures retain their review state; no new injection L2 result is claimed.
Native read probes use reviewed providers and deterministic streams, and
cover returned values, content, presentation metadata, the next request and
durable JSONL. They do not certify every output path. A refused Guard plugin
does not stop all other host tools, and Web
inventory alone does not establish activation in an agent's scope.

No production dependency was added. Python Core remains Python 3.9+;
the optional pinned DSH read feature uses Node 22 and the Kimi TOML capture
helper requires Python 3.11+. Lab evidence is limited to declared channels;
same-UID isolation, general host indexing/uploads and external exfiltration
containment are outside its guarantee.

## DSH distribution

The scoped package includes a DSH bundle and prebuilt JavaScript. It needs
no install-time build script. After publication, install the exact version
into a chosen profile:

```sh
dsh plugin --profile <your-profile> add @mokuyoaxis/agent-guard@0.2.3
```

Python 3.9+ and Git must already be available. The admitted DSH versions are
`0.1.5-rc.1` and `0.2.0-rc.2`; optional read protection additionally requires
Node 22 and reviewed artifact hashes. Its default remains off. Marketplace
listing is a separate community-catalog submission, not a broader host or
model support declaration.
