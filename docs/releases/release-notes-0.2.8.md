# 0.2.8 — MCP diagnosis and recovery validation

Version 0.2.8 adds MCP connection diagnosis, real protocol-interruption
checks and documentation to the
[0.2.7](https://github.com/mokuyoaxis/agent-guard/blob/0.2.8/docs/releases/release-notes-0.2.7.md)
baseline. Use the exact
[GitHub release](https://github.com/mokuyoaxis/agent-guard/releases/tag/0.2.8)
and [npm version](https://www.npmjs.com/package/@mokuyoaxis/agent-guard/v/0.2.8)
to verify availability before installing; a source version or local tarball
alone does not establish publication. The separate `rc` and `hotfix` channels
retain their versions.

本版提供 MCP 接入诊断与恢复验证。安装前核实精确版本的发行与包页面。已有 0.2.7 的测试、宿主实测
和发布记录保留各自版本边界，不能直接作为 0.2.8 候选的通过证据。

## Version scope

### Explicit MCP doctor

`agent-guard-doctor mcp` checks a selected local service through the existing
stdio interface. It checks the interpreter and optional pinned MCP SDK,
SDK imports, service handshake and package-matching version, the expected
five tools and query annotations, workspace/quarantine binding, and successful
`status`, `transactions` and `gc_plan` calls.

From this checkout or a package built from it:

```sh
python3 -B /absolute/path/to/agent-guard/doctor.py mcp \
  --python /absolute/path/to/agent-guard-mcp-venv/bin/python \
  --workspace /absolute/path/to/your-project --json
```

`--python` defaults to the doctor interpreter. `--server` optionally selects
another reviewed script, otherwise the package's own adapter is used.
`--timeout` defaults to 30 seconds and accepts 5–120 for diagnostic checks;
service-process shutdown can add up to three seconds of process waits and one second
of reader-thread waiting. The doctor starts these
trusted executable inputs without a shell; it neither installs dependencies
nor discovers or rewrites client configuration.

The independent `schema_version: 1` report contains fixed diagnostic status,
stage/code, message, guidance and checks. It does not expose raw child stderr,
configuration/authentication contents or recovery/audit records. Exit `0`
means PASS, `1` means diagnostic failure and `2` means invalid CLI usage.
These codes do not extend the Decision Protocol reason-code namespace.
The existing Kimi/Claude doctor paths retain their interfaces.
`diagnostic_stderr_bytes` exposes a byte count only. The report can show
`deletion_admission: BLOCKED` while connection checks pass; `READABLE` means
admission information was inspected, not that future deletion is approved.

A PASS covers direct checks of the selected service. `host_connection`,
`model_delivery` and `host_interception` stay `UNVERIFIED`; actual host loading
and model use require separate acceptance. No model, deletion, restore, GC
execution, policy modification or budget reset is performed by the doctor.
See the [MCP guide](https://github.com/mokuyoaxis/agent-guard/blob/0.2.8/docs/guides/mcp.md#diagnose-a-local-service-028).

### Recovery after response loss

The protocol checks target a client deadline, cancellation notification,
stdin EOF and service termination at a controlled barrier after safe_delete
has committed but before a response is sent. They inspect files, recovery
transactions, persistent accounting and ordinary restoration on reconnect.
This checks a specific response-loss boundary, not arbitrary partial
multi-file interruption or atomic rollback.

After an uncertain mutation, inspect status, transactions and the source/
recovery copy before another mutation. Reconnect with the same trusted
workspace/quarantine/session identity. Restoration does not refund the
deletion budget. See [interrupted-connection recovery](https://github.com/mokuyoaxis/agent-guard/blob/0.2.8/docs/guides/mcp.md#after-a-lost-response-or-interrupted-connection).

### Documentation and existing Codex evidence

The MCP guide now distinguishes direct-service diagnosis, live-client
acceptance and actual model calls. It includes the separate 2026-10-29
Codex CLI 0.165.0 acceptance of the published **0.2.7** package: three
native-client groups and three real-model tasks, totaling 16 model MCP calls.
That exact-version result covers the cooperative MCP path, not native Shell/
editor interception or the 0.2.8 candidate. Original failures and evidence
limitations remain in the [recorded validation](https://github.com/mokuyoaxis/agent-guard/blob/0.2.8/docs/guides/mcp.md#recorded-validation-and-remaining-limits).

## Compatibility and upgrade notes

The maintainer selected 0.2.8 for these bounded diagnosis/reliability additions.
Earlier doctor drift/live-sentinel additions were classified as minor. This
release's independent diagnostic surface is explicitly recorded as a
maintenance-version decision; the 0.2.7 exception is not inherited and does
not permit unrelated policy changes. See the
[compatibility decision](https://github.com/mokuyoaxis/agent-guard/blob/0.2.8/docs/design/compatibility.md#028-diagnosis-maintenance-decision).

The five MCP tools and their arguments, Decision classes/reasons, default
deletion rules, recovery manifests, persistent budget and preview-only MCP
GC retain their contracts. There is no storage/configuration migration or
automatic client setup. The pinned `mcp==2.3.0` SDK remains an optional
separate-environment dependency; Core/CLI still needs Python 3.9+ and Git
without third-party Python packages. MCP requires a compatible Python 3.10+
SDK environment.

Package/helper changes can make an older doctor fingerprint baseline
`DRIFTED`; it is not silently replaced. Existing installations require an
explicit upgrade to gain the new doctor path. Same-UID, concurrency and
uncovered-tool boundaries retain their existing scope. Configuration
diagnosis prototypes, new deletion permissions, automatic GC, 0.3.0 work
and commercial capabilities are outside this release.

## Install or evaluate version 0.2.8

After confirming availability on both publication pages above, install into
a chosen stable prefix:

```sh
npm install --prefix /absolute/path/to/agent-guard-install @mokuyoaxis/agent-guard@0.2.8
```

For source review or before registry availability, use reviewed source or a
verified local tarball in a separate prefix. The optional MCP SDK is still
installed separately; npm does not install it or configure host clients.
Existing installations require an explicit upgrade.

## Validation status

Local 0.2.8 candidate validation **passed** on 2026-11-06. Local checks used ARM64/PRoot,
Python 3.13.5, Node 22.23.4 and the separate pinned MCP SDK 2.3.0 environment.

| Check | Observed result |
|---|---|
| Focused MCP doctor | 16 cases passed in 40.956 seconds, including four real-SDK cases |
| Focused protocol interruption | Four cases passed in 44.090 seconds |
| First completed full regression | 1152 Python cases in 1620.567 seconds: no assertion failures, one error, no skips; Node groups were not started after Python failed |
| Independent check of the failed case | Passed in 10.802 seconds (11.301 seconds for the outer process); this does not replace the failed full run |
| Complete rerun of the same candidate | PASS: 1152 Python cases and both Node groups, no failures/errors/skips, 1719.330 seconds overall; all 224 frozen public files were unchanged |
| Local package | 157 files; offline installation, 12 CLI help checks, installed doctor and actual MCP deletion/restoration passed; restored bytes matched and budget remained charged |

The full-run error was a fresh server's initialization exceeding its existing
20-second wait in an interruption test. The same run's monitoring also had a
415.159-second heartbeat gap. The cause remains unknown; that observation is
not a proven explanation for the timeout. Runtime, assertions and deadlines
were not changed to make the independent check or complete rerun pass.
The passing rerun also recorded a separate 470.896-second monitoring gap,
without test errors. Neither the passing checks nor these timing observations
establish the cause of the earlier timeout.

The full rerun used the existing immediate-diagnostic unittest collector,
followed by the two Node groups, with the public `npm test` pipeline's scope
and unchanged per-test limits. It did not invoke npm directly. All 1152 test
start/finish records paired uniquely; the 1800-second overall deadline was
retained. The SDK-backed doctor and interruption tests actually executed.

An earlier full attempt was intentionally stopped to add explicit handling
and a regression for an empty `--python` argument; it is not a completed full
run. That additional regression later passed among the 17 doctor cases in
the completed Python suite above. It is not combined with the earlier
16-case focused run and reported as a single 17-case focused result.

Package checks compare runtime and test bytes with the frozen full-test
candidate and account for documentation-only updates separately. They include
offline packing/installation, member hashes, the installed doctor and actual
MCP recovery. Optional-SDK skips are not protocol acceptance; SDK-backed release
checks must exercise the real protocol cases.

The implementation commit `1323ca5` subsequently passed
[CI 42353094903](https://github.com/mokuyoaxis/agent-guard/actions/runs/42353094903)
on 2026-11-07: all 15 jobs succeeded, including the Linux/macOS Python
3.9–3.13 matrices, Windows Core, DSH checks and both SDK-backed MCP jobs.
This checkpoint precedes the documentation-only publication alignment;
the release commit requires its own CI and the tag's Release workflow.
Actual CI/release runs and package availability remain separately verifiable;
these local and implementation-CI records do not certify a later artifact.
No new real-model trial was performed for 0.2.8.

The preceding 0.2.7 artifact and Codex/Kimi records retain their original
versions and scope. They are not substituted for this candidate's results.
