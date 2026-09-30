# Agent Guard 0.2.3 — release-note draft

This is a draft for the planned stable release. The current source remains
`0.2.3-rc2`; these notes do not mean `0.2.3` has been published. See the
[readiness review](test-report-release-readiness-0.2.3.md) for outstanding gates.

## Changes

- Windows/Core fail-closed fixes from rc1: known destructive vocabulary is
  checked across shell dialects, and Windows path normalization/recovery has
  focused real-Windows CI evidence. Arbitrary Windows harness E2E remains
  outside the claim.
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
- Bounded native session v4 / receipt v5 support alongside preserved v3
  interpretation, including retry-attempt evidence. Surface replay, image
  offload and arbitrary session shapes remain unsupported.
- DSH/Kimi Lab startup failures retain the original observer error and never
  launch a host task or acquire a successful capture receipt.

## Evidence and limits

The offline Lab controls establish instrument calibration. Historical bounded
model trials and fresh no-model native checks are listed in the
[rc2 notes](release-notes-0.2.3-rc2.md) and
[current review](test-report-release-readiness-0.2.3.md). The retained new-host
real-model clean capture remains inconclusive; no new injection L2 result is
claimed. A refused Guard plugin does not stop all other host tools, and Web
inventory alone does not establish activation in an agent's scope.

No production dependency was added. Python Core remains Python 3.9+;
the optional pinned DSH read feature uses Node 22 and the Kimi TOML capture
helper requires Python 3.11+. Lab evidence is limited to declared channels;
same-UID isolation, general host indexing/uploads and external exfiltration
containment are outside its guarantee.
