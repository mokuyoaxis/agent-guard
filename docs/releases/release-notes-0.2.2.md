# Agent Guard 0.2.2 — source and release notes

These notes describe the `0.2.2` source candidate. Check
[GitHub Releases](https://github.com/mokuyoaxis/agent-guard/releases) and the
npm registry independently: a version in this repository is not proof that a
GitHub Release or npm package has been published.

## Host drift and live evidence

- `doctor.py kimi|claude --check-drift` compares the installed host version
  with a small adapter-owned compatibility profile and fingerprints only the
  normalized Agent Guard hook fields and enforcement runtime. It makes no
  model call and never upgrades `host_interception` from `UNVERIFIED`.
- Optional create-new baselines contain only the parsed version and SHA-256
  fingerprints. They are written as `0600`, never overwrite an existing file,
  and detect later configuration/runtime/profile drift without copying the
  host configuration.
- Explicit `--live-sentinel` may spend one configured model call in a private
  blank fixture. PASS requires an exact hashed hook receipt, matching Core
  audit, host block feedback, and an absent marker. Missing evidence is
  `INCONCLUSIVE`; an executed marker is a `CRITICAL` failure. Raw host/model
  output is counted and hashed, then discarded.
- The sentinel is a narrow reliability canary for one root-agent `Bash` call.
  It does not prove subagent, concurrency, other-tool or malicious-host
  containment.

See the [host drift guide](../guides/host-drift.md) for the status and alarm contracts.

## DSH package acceptance

- DSH `0.1.5-rc.1` now has current, execution-level host evidence without a
  model call. A benign `bash` invocation continued; a synthetic stdin-shell
  shape was denied before execution; its bypass-only marker remained absent;
  and Core wrote `BLOCK_UNDETERMINABLE_EFFECT` to the isolated audit.
- The same behavior passed after `npm pack` and installation through DSH's own
  plugin manager, exercising the package bundle rather than a mocked service.
  A separate fault injection confirmed fail-closed behavior when an already
  registered adapter could not obtain parseable Core output.
- The DSH adapter no longer imports the host-internal `defineTool` helper at
  runtime. It registers plain public Tool Definition objects and performs its
  small argument validation locally, avoiding accidental reliance on DSH's
  internal dependency layout.

The exact observations and limits are in the
[DSH 0.1.5-rc.1 report](../reports/test-report-dsh-0.1.5-rc.1.md). Model/Agent execution,
subagents, concurrency, ASK UI, non-`bash` tools, pre-registration failure and
future host versions remain unverified.

## Universal npm artifact candidate

- The package identity is now `@mokuyoaxis/agent-guard@0.2.2`, matching the
  maintainer scope and avoiding the unrelated unscoped package.
- One package carries the harness-neutral Core, all Skills, Claude/Kimi/DSH
  adapters, the unlisted-host integration guide, documentation, `doctor.py`
  and `live_sentinel.py`. DSH remains the JavaScript default export; it does
  not redefine the project as a DSH-only plugin.
- Nine Python-backed npm binaries expose the doctor, live sentinel, delete
  guard and cooperative exfil/config-view entry points. Their package targets
  are checked for presence, executable mode and Python shebang.
- `prepublishOnly` runs the full suite and `publishConfig.access` is public.
  No production dependency was added.
- The release workflow follows the project's unprefixed `X.Y.Z` tag convention,
  verifies the exact scoped name and version, runs the release tests, packs
  once, attaches that tarball and its SHA-256 to the GitHub Release, and
  publishes the same artifact to npm with public access and provenance. A
  manual preflight can validate `NPM_TOKEN` without publishing.
- The README documents a pinned scoped-package setup route and links a bilingual
  contribution guide that recommends scoped Issues and focused Pull Requests.
- The post-documentation candidate contains 77 files. Its extracted inventory
  contains no `.internal`/bytecode files, repository-local absolute paths,
  recognized credential shapes, or identifiers from the maintainer's private
  Kimi providers. All checked relative links across the repository's Markdown
  files resolve.

The artifact remains universal rather than host-specific. npm keeps its own
credential and version gates, while the exact `0.2.2` tag publishes one packed
artifact to both the GitHub Release and npm. A retry may fill a missing side,
but never replaces an npm version that already exists.

## Kimi and Claude status

Kimi Code's current official `2.1.1` CLI was installed from its checksum-
verified Linux ARM64 release during this candidate cycle. Its current
`[[hooks]]` / `PreToolUse` / `Bash` / exit-2 contract still matches the thin
adapter, and both Kimi's config doctor and Agent Guard's local bridge probe
passed. After OAuth login, an available official model completed the revised
root-Bash live sentinel. A second configured model route completed the same
probe. For both routes, exact receipt, Core audit and host block feedback all
matched, while the bypass-only marker remained absent. Both results are
`PASS / HOOK_ENFORCEMENT_OBSERVED`; `2.1.1` is now in the tested-version
profile. The client evidence proves the requested routes and hook behavior,
not the remote model weights. Private route identifiers and connection details
are omitted from the public report.

That repeat also found and fixed a sentinel-side relative-path bug: an explicit
relative evidence directory is now resolved before the host changes cwd, so
the hook receives absolute create-only receipt and workspace paths. Kimi 2.x
prompt mode now requests `stream-json` directly; it does not append the
incompatible `--auto`/`--yolo` flags. A second result-integrity fix prevents
complete hook evidence from overriding a host timeout: timeout is always
`INCONCLUSIVE`. The second route's prompt now forbids retrying after a block.
The evidence remains limited to one root `Bash` call per route; 2.1.1 subagents, ASK,
concurrency and fault injection were not tested.

A real Claude-model canary was not run. The existing scripted-model CLI
evidence remains limited to its tested versions and configurations.

## Verification gate

The current candidate passes `npm test`: 434 Python tests plus the DSH adapter
smoke test. Package-manifest, doctor/live-sentinel and DSH adapter targeted
suites pass, as do the scoped-tarball DSH Core-block and failure probes. The
Markdown-link, package-inventory, credential/host-path and Git whitespace
checks also pass. The authenticated Kimi 2.1.1 root-Bash repeat now passes and
has been added to the bounded compatibility claim. These checks do not
authorize a commit, push, tag, GitHub Release or npm publication by themselves.

The offline `guard-lab` honeytoken experiment is planned for `0.2.3` and is
not part of `0.2.2`.
