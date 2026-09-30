# 0.2.3 contract and release readiness review

Date: 2026-09-30 (UTC). Source: the uncommitted `0.2.3-rc2` worktree on
`31a8874`; this report does not describe a published `0.2.3` artifact.

## DSH impact and admitted contracts

Most [DSH rc.2 release changes](https://github.com/deepseek-ai/deepseek-harness/releases/tag/dsh-v0.2.0-rc.2)
concern desktop/UI behavior. However, the installed upgrade from
`0.1.5-rc.1` to `0.2.0-rc.2` changes Shell execution and native session
contracts used by this adapter. Core policy does not need to follow the UI;
adapter translation and evidence interpretation do need verification.

| Surface | Current implementation | Fresh evidence / limit |
|---|---|---|
| Package admission | Exactly `0.1.5-rc.1` or `0.2.0-rc.2` | Manifest and Lab preflight agree |
| Default deletion | New `execute(spec).result()`; legacy `run(spec)` when the new method is absent | Both installed hosts pass harmless execution, marker absence and Core block audit; execution failures do not retry the legacy method |
| Optional text-read guard | Node 22, exact versions/artifact hashes and native read binding | New local and sandbox FS pass actual ToolRuntime, AgentLoop and JSONL probes |
| Native Lab capture | Old host: native v3 / receipt v4; new host: native v4 / receipt v5; parser v2 | Fresh boot-free checks and four-home preparation pass; unsupported sessions remain inconclusive |
| Historical evidence | Original receipt/parser/format interpretation retained | No historical receipt, report or real-model outcome was upgraded |
| Running Web agent | Separate installed deployment | This checkout's activation and agent scope remain unverified |

The [initial upgrade report](test-report-dsh-0.2-upgrade.md) remains a historical
snapshot. Its statements that package/read/Lab pins still reject the new host
were true then and are superseded by the current source and checks above.

## Fresh native verification

Environment: Android/PRoot Debian ARM64, Node `22.23.2`, Python `3.13.5`.
No new production dependency, external model call or live Web configuration
change was made. Synthetic fixtures and evidence are private and outside
the repository.

- Default deletion probes on old and new hosts pass. A further new-host probe
  with `readResultGuard` explicitly enabled also passes and does not report
  startup refusal. In each probe a benign marker exists, the blocked marker
  does not, and a durable `BLOCK_UNDETERMINABLE_EFFECT` audit exists.
- The deletion probes use `danger-full-access` only in fresh private profiles
  because Android/PRoot lacks the host's shell confinement backend. They do
  not verify workspace-write shell confinement or alter a user's deployment.
- New local and sandbox FS read probes each pass all 10 withheld-result
  cases, plugin load/startup/disposal checks, scoped-shadow denial, version
  and artifact drift refusal. Protected synthetic values are absent from
  guarded value/content/meta, the next synthetic request and full durable
  JSONL. Ordinary config, Chinese text, line numbers and a random marker stay
  readable. Each uses two deterministic streams and zero external model,
  network or shell calls. The sandbox FS result is a filesystem-provider
  check, not a shell sandbox acceptance result.
- The new result-hook probe confirms that replacing only content retains
  original presentation metadata; replacing the value regenerates metadata;
  a later finalizer can reintroduce content. This justifies the pinned binding
  checks and does not establish arbitrary plugin containment.
- Old and new boot-free Lab preflights pass. The new four-home pair preparation
  reports `READY`, equal scoped non-Guard fingerprints and
  `host_tasks_launched=0`; no model trial was launched.
- The local old installation's `dsh-tool-fs` entry hash differs from the
  reviewed old artifact. Its optional read guard correctly refuses startup.
  This is an installation-drift limit, not a passing legacy read re-test; its
  default deletion path passes independently. No installed code or pin was
  changed to force admission.

## Real-model follow-up is incomplete

The retained new-host direct-read experiment has only the `clean-off`
capture and no completed review/off-on result. Its receipt reports
`UNSUPPORTED_SURFACE_REPLAY`, `recognized=false` and
`composition_unchanged=false`. Exit zero does not clear these gates. The
current parser intentionally refuses v4 surface replay and image offload;
passing synthetic v4/retry tests does not make this real trial complete.

Prior official Flash direct-read results and Kimi/DSH Lab results retain their
original host versions and observation channels. No new DSH injection L2
claim follows from this review. Repairing replay interpretation or adding a
read-redaction injection protocol is separate work, rather than a requirement
for the originally planned offline Lab MVP.

## Local regression and package gate

The first fresh full suite ran 595 tests in 670.686 seconds and had one error:
an observer did not become ready within the existing five-second startup
bound. The runner then attempted to stop the unarmed observer, masking the
original error. The isolated original test passed on repeat; that does not
turn the failed full run into a passing gate.

Both DSH and Kimi runners now enter host-execution cleanup only after observer
startup succeeds. Startup keeps its own bounded stop-request handling. Two
new regressions confirm that the original error survives, no host task starts,
no capture receipt is credited and the report remains inconclusive. Those
regressions plus the original test passed together (3 tests, 3.377 seconds).
The startup deadline, Core policy and observer success criteria were not
relaxed. This fixes error preservation, not a guarantee of startup under any
system load.

- After the correction, full `npm test` passed all 597 Python tests in
  1026.421 seconds, followed by both Node adapter/read-worker smoke suites.
- Python syntax (63 public source files), JavaScript syntax, LF/trailing
  whitespace, `git diff --check` and 240 local links in 56 Markdown files pass.
  Shared Core/skill sources have no reverse DSH/adapter imports.
- The candidate package contains 107 files. Package scanning found no private
  captures/controllers, tests, bytecode, local account paths or supported
  credential-shaped values. Packaged files were checked against the worktree.
- Offline isolated installation of the local tarball passes all 10 public
  CLI help checks and adapter import without DSH. All four deterministic Lab
  cases return `COMPLETE/PASS`, L0 `CALIBRATION_ONLY`, observer `STOPPED` and
  `external-boundary=UNSUPPORTED`.
- Final documentation is repacked after recording these results; its runnable
  assets are compared byte-for-byte with the package exercised above. Final
  checksums and source inventory are retained in private review evidence.

This is completed local regression, not hosted CI or a passing new-host
real-model trial. Native Windows/PowerShell and macOS were not run locally.

## Release decision

The planned `0.2.3` scope is the rc1 Windows/Core fix plus the offline
guard-lab MVP. It does not require UI parity with DSH, universal host
adaptation, a new real-model mitigation claim or live Web deployment changes.
The current source has those planned features and scoped new-host support.

Direct stable publication is not yet cleared: this worktree is still
`0.2.3-rc2`, and hosted CI covers only `31a8874` before the Lab/DSH additions.
The read-only Actions check confirms the old run's 12 jobs passed, including
Windows Core. It supplies no CI evidence for the current additions. Registry
dist-tags still read `latest=0.2.2`, `rc=0.2.3-rc1`.

Follow the existing candidate sequence: review the exact diff, obtain
authorization to commit/push rc2, run CI for that exact source, then prepare
the approved `0.2.3` manifest/tests/README versions and validate its final
artifact before authorizing the stable tag workflow. A
[stable release-note draft](release-notes-0.2.3.md) is ready for that step.
Remote publication is not authorized by this review.
