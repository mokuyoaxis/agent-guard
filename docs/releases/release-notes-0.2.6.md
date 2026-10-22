# 0.2.6 — Kimi feedback, diagnostics and host compatibility

Released 2026-10-22 as the stable maintenance release on npm `latest`.
Use the exact [GitHub release](https://github.com/mokuyoaxis/agent-guard/releases/tag/0.2.6)
and [npm version](https://www.npmjs.com/package/@mokuyoaxis/agent-guard/v/0.2.6)
to identify the package. RC and hotfix channels keep their existing versions.
See [getting started](https://github.com/mokuyoaxis/agent-guard/blob/0.2.6/docs/guides/getting-started.md) for installation and setup.

本版补齐 Kimi 恢复信息的反馈出口与本地诊断，细分 DSH Lab 预检失败原因，
并为默认删除适配增加 DSH `0.2.1-alpha.4`、为 Kimi Bash profile 增加精确版本 `2.3.0`。
已有安装需要升级；Kimi Stop 仍需显式配置，并在新会话中加载。

## Kimi recovery feedback

In the sampled Kimi 0.42.0, 2.1.1 and 2.3.0 success-hook paths, the recovery
explanation did not enter the next model request. A successful Bash result
therefore did not tell the model that files had moved to quarantine.

The optional POSIX Stop hook supplies actual transaction IDs, inspection and
restore commands, retained-storage guidance and complete GC commands. It asks
for an additional response after new compensation records. Existing
PreToolUse-only installations keep their behavior; Stop must be explicitly
configured and read by a new Kimi session.

The initial answer appears first and may be inaccurate. Stop adds a model
generation and cannot repair the earlier tool result. Emission cursors suppress
repeated notices in the tested scope; they are not model acknowledgements.
Cancellation, exhausted budgets and shared-session actors retain their limits.
The hook executes no restore or GC. Restoring files moves them back without
freeing total disk space; permanent GC removes recovery copies.

The example recommends PreToolUse 120 seconds around the unchanged internal
90-second check, and Stop 30 seconds. Missing or timed-out hooks can still
allow tools in the sampled hosts. The outer timeout margin does not change
that behavior. See [setup and limits](https://github.com/mokuyoaxis/agent-guard/blob/0.2.6/adapters/kimi-code/README.md#optional-recovery-feedback-unreleased-checkout-addition)
and the [bounded host report](https://github.com/mokuyoaxis/agent-guard/blob/0.2.6/docs/reports/test-report-kimi-code-block.md).

## Kimi 2.3.0 Bash compatibility

The Kimi compatibility profile now lists exact versions `0.42.0`, `2.1.1`
and `2.3.0`. The new version has eight actual prompt-CLI and eight ACP trials
with fixed loopback responses: allow, BLOCK, ASK hard refusal, Python startup
failure, hook absence, recovery, partial compensation and optional Stop
delivery. ACP hook process/payload cwd matched the session workspace even
when the service started in another directory. No external model calls were
made. See the [bounded report](https://github.com/mokuyoaxis/agent-guard/blob/0.2.6/docs/reports/test-report-kimi-code-block.md#kimi-230-acceptance).

Core and bridge behavior are unchanged. A valid 2.3.0 local preflight can
be `CURRENT`, while unlisted versions remain `STALE`. Existing baselines may
be `DRIFTED` because the profile fingerprint changed; they are not overwritten.
This does not add subagent, background, other-tool or real-model guarantees.
Web checks covered startup/status/shutdown, not Web tool calls. The separate
Kimi Lab capture helper remains pinned to 2.1.1. Published 0.2.5 retains its
recorded list; upgrade that installation to obtain the new profile.

## Separate Stop doctor results

`doctor.py kimi --probe --json` retains the existing top-level PreToolUse
results and adds `feedback` for optional Stop:

- Absent Stop is `NOT_CONFIGURED` and does not fail the CLI. An unreadable or
  invalid configuration is `UNVERIFIED`, rather than a claim that Stop is off.
- Checks cover the event, absolute shell/bridge/Python command, option,
  matcher, timeout, duplicate definitions and helper presence.
- A requested local probe runs synthetic records in its own temporary
  home/workspace/trash. It checks feedback content and repeat suppression
  without touching existing recovery records or calling a model. PreToolUse
  probes also isolate inherited recovery locations.
- A configured Stop that fails configuration or a requested local probe makes
  the CLI exit 1 and drift `BROKEN`, prevents writing a passing baseline and
  stops the live sentinel before it can call a model.
- Configured Stop fields and helper bytes join drift fingerprints. Runtime
  fingerprints include `package.json`, so the version change itself can
  produce `DRIFTED` against an older baseline. Review the change before
  creating a new baseline at a new path.

`feedback.model_delivery` always remains `UNVERIFIED`, including after a
Bash live sentinel. Local success does not prove a live host loaded Stop or
a model received its feedback. The importable `check_kimi()` wrapper remains
PreToolUse-only; Claude output retains its existing contract.
See the [doctor fields](https://github.com/mokuyoaxis/agent-guard/blob/0.2.6/adapters/kimi-code/README.md#stop-doctor-checks-unreleased).

## DSH Lab preflight diagnostics

Version/config checks distinguish `START_FAILED`, `TIMEOUT`, `OUTPUT_LIMIT`,
`NONZERO_EXIT`, `EMPTY_STDOUT` and `UNSUPPORTED_VERSION`. Errors retain their
stage text and add JSON with the child exit code, capture flags,
elapsed/deadline seconds and captured byte counts. Raw configuration, stderr,
command arguments and environment values are omitted.

The Lab CLI still exits 1 on failure. Its 10/15-second budgets and admitted
DSH versions remain unchanged, without automatic retry. Consumers should
inspect diagnostic fields rather than match the whole old error sentence.
This concerns the Lab controller, not production deletion policy.
See the [Lab runner guide](https://github.com/mokuyoaxis/agent-guard/blob/0.2.6/adapters/dsh/harness/README.md).

## Upgrade scope

The default DSH deletion adapter now admits `0.2.1-alpha.4` alongside
`0.1.5-rc.1` and `0.2.0-rc.2`. The top-level engines, optional DSH peer and
nested bundle declaration match the exact list. This corrects the loader
metadata: the nested field alone was not an admission gate. Reviewed loaders
now reject tested unlisted hosts without exemptions. The optional peer does
not install DSH. See [compatibility](https://github.com/mokuyoaxis/agent-guard/blob/0.2.6/docs/design/dsh-compatibility.md) and
[native validation](https://github.com/mokuyoaxis/agent-guard/blob/0.2.6/docs/reports/test-report-dsh-alpha4.md).

Optional read protection and Lab retain their two-version boundaries.

Core decisions, rm wildcard refusal, command recognition, workspace boundaries,
recovery formats and manual GC retain their contracts. No production
dependency or storage/configuration migration is introduced. Stop is opt-in;
the local doctor exit behavior for an invalid configured Stop is
upgrade-visible. Earlier RC and hotfix artifacts retain their separate scope.

The maintainer selected 0.2.6 for this feedback/diagnostic maintenance. The
[compatibility contract](https://github.com/mokuyoaxis/agent-guard/blob/0.2.6/docs/design/compatibility.md) records the bounded
maintenance-version exception for its additive diagnostics. This does not
authorize unrelated policy changes under the patch versioning rules.

## Validation and limits

The implementation checkpoint
[9295b9a](https://github.com/mokuyoaxis/agent-guard/commit/9295b9a8ccadba1a5a34b71fe049105fd8aefe6b)
passed [all 13 CI jobs](https://github.com/mokuyoaxis/agent-guard/actions/runs/39804200594)
on 2026-10-22. Linux and macOS each ran the complete 974-test discovery on
Python 3.9–3.13. Python 3.9/3.10 each retained 29 conditional skips;
3.11–3.13 had none. Both Node groups, 113 DSH native-Lab checks, 92 focused
Windows Core checks and the end-to-end workspace-root refusal smoke passed.
Linux's full matrix is pinned to Ubuntu 24.04 because Ubuntu 26.04 lacks the
Python 3.9 build. No version or test was removed to resolve that setup failure.
Final publication wording changes only Markdown; runtime, tests and policies
retain that checkpoint's bytes.

The later DSH expansion has separate actual CLI Profile/Boot evidence on all
three admitted hosts and alpha.4 scripted root/child-agent and native approval
checks. No external model request was made. Its new manifest/forwarding tests,
package verification and limits are recorded in the
[DSH report](https://github.com/mokuyoaxis/agent-guard/blob/0.2.6/docs/reports/test-report-dsh-alpha4.md); the earlier full run below
does not include this later manifest change. The implementation CI above does.

### Historical local checkpoint

Before this version/document alignment, the unchanged implementation passed
one complete frozen run on 2026-10-21: **974 Python tests and both Node groups**,
with no failure, error or skip. The same 195-file candidate completed all three
stages in 1270.573 seconds within the unchanged 1800-second deadline, without
retry. An internal unittest result collector wrote immediate failures and
flushed per-test events; the discovery scope matched `npm test`. This was a
single continuous workflow, not a combination of earlier targeted runs.

The local environment was ARM64/PRoot Debian, Python 3.13.5 and Node v22.23.2.
Earlier full-run errors/timeouts remain historical failures; their original
causes are not established by this passing run. The version assertion and
package metadata were aligned separately during preparation. The prior
139-file offline package described the pre-version checkpoint, not a
published or already-validated 0.2.6 artifact.

The later Kimi 2.3.0 profile alignment and its bounded host trials also
postdate the historical full run. They do not turn that run into a complete
validation of the final candidate. Earlier actual-CLI loopback trials
covered sampled Kimi 0.42.0/2.1.1 paths;
one bounded 2.1.1 real-model session received the actual transaction and full
commands, followed by restoration and repeat-Stop checks. Publishing this
release adds no new host/model trial or general subagent/background guarantee.
The historical local run did not execute other Python versions, macOS or
Windows; the remote CI above supplies separate platform evidence. Passing CI
does not establish the cause of the earlier local timeouts or failures.
