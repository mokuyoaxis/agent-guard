# 0.2.0-rc1 source-preview notes

The `0.2.0-rc1` Git tag is a **source preview**. It is not a GitHub Release,
an npm package, a security sandbox, or a claim of universal tool interception.
Those distribution steps remain separate maintainer decisions.

## What changed since 0.1.1

- A harness-neutral Python Core and Decision Protocol now support shell
  dialect selection (`posix`, `cmd`, `powershell`) and fail closed on unknown
  selectors. Windows logic is unit-tested; real Windows host E2E is open.
- Delete-guard's compensation, transaction, GC, command segmentation and
  hard-refusal handling have additional incident regressions.
- Exfil-guard provides a cooperative CLI for text-span inspection and
  channel-specific allow/sanitize/ask/block decisions. It does not hook every
  file read, upload or model-context path.
- Recovery-audit provides a Skill and evidence schemas for rebuilding a
  project from surviving Git, AI-session and cache material. It cannot
  recover bytes for which no copy survives.
- Claude Code and DSH adapters, Codex/ZCode Skill/CLI evaluations, and the
  bounded Kimi Code 0.42.0 hook observations are documented separately in
  the [harness capability matrix](harness-capabilities.md).

## Known limits and release gates

- Kimi Code's `ASK` hook response is not an approval prompt in the tested
  version; the adapter denies such calls. Its real-host execution-level
  `BLOCK` remains unproved. Two `local/kimi-k3` sandboxes established only
  the sampled root/subagent hook and compensation paths.
- DSH 0.1.5-rc.1 has adapter-level smoke tests but not a completed real-host
  plugin-load and denied-command sentinel trial. The older DSH v0.1.1 report
  does not substitute for current-version evidence.
- Codex has no native interception adapter in this repository. Its Skill/CLI
  acceptance driver needs an explicit checkout path and does not prove
  automatic interception.
- Native adapters cover their listed shell tools, not all filesystem,
  network or subagent paths. No claim is made against a malicious same-UID
  harness, deliberate bypass, or arbitrary prompt injection.
- Original Secret Guard safe config view/edit, general audit redaction,
  trusted read interception and the proposed offline guard-lab are not in
  this candidate.
- The npm root manifest remains private; its current package selection is
  DSH-oriented and is **not** a complete harness-neutral distribution.

See the [compatibility contract](compatibility.md) and
[threat model](threat-model.md) before treating any host as protected.
