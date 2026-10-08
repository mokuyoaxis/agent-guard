# AGENT-GUARD

[![CI](https://github.com/mokuyoaxis/agent-guard/actions/workflows/ci.yml/badge.svg)](https://github.com/mokuyoaxis/agent-guard/actions/workflows/ci.yml)
[![npm stable](https://img.shields.io/npm/v/%40mokuyoaxis%2Fagent-guard.svg)](https://www.npmjs.com/package/@mokuyoaxis/agent-guard)
[![License](https://img.shields.io/github/license/mokuyoaxis/agent-guard)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![0.2.5-rc1](https://img.shields.io/badge/RC-0.2.5--rc1-5B6B7A)](docs/releases/release-notes-0.2.5-rc1.md)

**Make destructive agent actions reversible by default.** · [简体中文](README.zh-CN.md)

Agent Guard is reliability infrastructure for coding agents. It provides
recovery before supported destructive actions, checks text before disclosure,
and gives users a Lab for testing observed agent and harness behaviour.
The shared Python Core works through explicit CLIs and optional host adapters.

| Capability | What it helps you do | Entry |
|---|---|---|
| **Recovery protection** | Relocate valuable files before deletion; snapshot tracked state before supported Git overwrites; inspect and restore transactions | [delete-guard](skills/delete-guard/SKILL.md) |
| **Disclosure checks** | Detect supported credentials and host-identifying paths; apply a redaction plan or withhold a payload | [exfil-guard](skills/exfil-guard/SKILL.md) |
| **Behaviour experiments** | Calibrate an observer, test a declared exposure channel, then compare matched guard-off/on trials | [guard-lab](docs/lab/guard-lab.md) |

A Skill supplies instructions; it does not intercept tool calls. Automatic
protection depends on an active, compatible host hook and its covered tools.
Agent Guard is not an OS sandbox against a malicious process with equal
privileges. The [capability matrix](docs/guides/harness-capabilities.md)
states the observed coverage and gaps.

<a id="guard-lab"></a>

## guard-lab: test with evidence

guard-lab is a **user-controlled experiment tool**. It puts non-secret,
synthetic markers and harmless bait into a disposable project and observes
only declared channels. The real controller and evidence stay outside the
fixture given to the tested agent.

Its workflow separates three questions:

1. **Calibrate:** do the observer, markers, scans and evidence chain work?
   Built-in controls call no model and make no external-network request.
2. **Observe exposure:** does an unguarded real-harness trial trigger the
   selected channel?
3. **Compare protection:** after an effective baseline, does a matched
   guarded trial reduce or block that exposure?

Start with one zero-model control from the package or checkout root:

```sh
lab_run="$(mktemp -d)"
python3 guard_lab.py run --case clean --output-dir "$lab_run/clean" --json
```

The other controls are `mock-positive`, `mock-injection` and
`snapshot-positive`. Run all four before a real-harness experiment, following
the [Lab guide](docs/lab/guard-lab.md). Installed packages also expose
`agent-guard-lab`.

A control PASS means **instrument calibration**, not model safety.
An unhealthy observer or missing evidence is `INCONCLUSIVE`; a quiet
guard-off baseline cannot establish mitigation. Matched comparisons require
the same relevant harness/version/model, task, protocol and non-guard
configuration. Lab results cover the declared channels, not every file read,
external transfer or model request. Existing
[Kimi](docs/reports/test-report-kimi-guard-lab.md) and
[DSH](docs/reports/test-report-dsh-guard-lab.md) reports retain their bounded
results, failures and review states.

<a id="quick-start-with-your-coding-agent"></a>

## Install and choose a version

Python 3.9+ and Git are required for the Core. The examples use a POSIX shell;
native bridges and optional capture helpers have their own requirements.

| Version | Status |
|---|---|
| [0.2.4](docs/releases/release-notes-0.2.4.md) | Published stable baseline |
| [0.2.5-rc1](docs/releases/release-notes-0.2.5-rc1.md) | Maintenance release candidate; select the exact RC version |

For the published stable package, use a chosen, stable user-owned prefix:

```sh
npm install --prefix /absolute/path/to/agent-guard-install @mokuyoaxis/agent-guard@0.2.4
```

The package root is
`/absolute/path/to/agent-guard-install/node_modules/@mokuyoaxis/agent-guard`.
The exact scoped package name matters. A source checkout is also usable:

```sh
git clone https://github.com/mokuyoaxis/agent-guard.git
cd agent-guard
```

For the RC, select `@mokuyoaxis/agent-guard@0.2.5-rc1` explicitly; the RC
channel does not replace stable `latest`. Check the
[GitHub release](https://github.com/mokuyoaxis/agent-guard/releases/tag/0.2.5-rc1)
or [npm version](https://www.npmjs.com/package/@mokuyoaxis/agent-guard/v/0.2.5-rc1)
for publication. A reviewed checkout or locally supplied tarball is also usable. See
[setup and the agent setup prompt](docs/guides/getting-started.md).

## Recover a supported deletion

From the workspace being protected, point at your package/checkout root:

```sh
python3 /absolute/path/to/agent-guard/skills/delete-guard/scripts/safe_delete.py src/old_module.py --dry-run
python3 /absolute/path/to/agent-guard/skills/delete-guard/scripts/safe_delete.py src/old_module.py
python3 /absolute/path/to/agent-guard/skills/delete-guard/scripts/restore.py list
python3 /absolute/path/to/agent-guard/skills/delete-guard/scripts/restore.py <txid>
```

Valuable targets are relocated with exact recovery paths in a manifest.
Provably regenerable, Git-ignored artifacts may be deleted directly.
Supported Git overwrites snapshot tracked state; hard-reset collisions with
untracked/ignored content are refused. Force restore preserves the current
occupant before replacement. Errors can leave partial work: inspect the
reported transaction IDs, filesystem and state before retrying.

Advisory `check.py` and `safe_delete.py --dry-run` preserve command targets but
may write quarantine/audit metadata and a Git local exclude entry. Advisory
exit 0 is not permission to execute a BLOCK or ASK.
See the [policy and assessment contract](skills/delete-guard/references/policy.md).

The RC adds quarantine control-path protection, excludes PURGED transactions
from active GC planning, and minimizes restore/safe_delete audit fields.
Exact CLI/manifest recovery paths remain available. These changes are not in
the published 0.2.4 package; see the [RC notes](docs/releases/release-notes-0.2.5-rc1.md).

## Check text before emission

The payload owner invokes the scanner and applies its decision.
For a text file:

```sh
python3 skills/exfil-guard/scripts/check_span.py --channel file-write --json < draft.md
python3 skills/exfil-guard/scripts/sanitize.py --channel file-write < draft.md
```

A complete synthetic credential example is generated at runtime:

```sh
python3 -c 'print("config: sk-proj-" + "AbCdEf0123456789GhIjKl")' |
  python3 skills/exfil-guard/scripts/sanitize.py --channel file-write
```

The expected output is `config: sk-<REDACTED>`. The checker returns a
decision and plan; it never rewrites the payload. Its exit 0 means
ALLOW/SANITIZE assessment, while the sanitizer's successful stdout is the
allowed or rewritten text. ASK/BLOCK/error withholds sanitizer payload output.

Detection covers documented credential shapes, value-free secret references
and selected host paths. It is not a general repository scanner or protection
against deliberate obfuscation. The text CLIs do not automatically append
egress audit events; a trusted integration owns emission and any audit storage.
For configuration inspection, an explicit safe view reports supported
JSON/dotenv structure without scalar values; it is not an editable copy or
automatic read interception.

Read the [Skill](skills/exfil-guard/SKILL.md),
[rules and exemption diagnostics](skills/exfil-guard/references/rules.md), and
[channel contract](skills/exfil-guard/references/channels.md).

<a id="integration-and-validation-matrix"></a>

## Connect your coding agent

These are version-specific historical observations. They do not certify a
new source candidate, every tool or every subagent path.

| Host | Integration and evidence |
|---|---|
| Claude Code | Native Bash `PreToolUse`; sampled 2.1.270/2.1.273 scripted-host enforcement. [Setup](adapters/claude/README.md) |
| Kimi Code | Native Bash `PreToolUse`; bounded 0.42.0 root/child and 2.1.1 root-Bash observations. ASK is denied; missing/timed-out hooks remain uncovered. [Setup](adapters/kimi-code/README.md) |
| DSH | Reviewed 0.1.5-rc.1/0.2.0-rc.2 deletion paths; optional complete text-read protection is default off. Sampled PTC Bash refusal does not imply universal PTC mediation. [Setup](adapters/dsh/README.md) |
| Codex / other hosts | Explicit Skill/CLI is usable. Native interception needs a verified blocking hook. [Adaptation guide](adapters/INTEGRATION.md) |

Use the [full capability and evidence matrix](docs/guides/harness-capabilities.md)
for precise versions, tests and gaps. `doctor.py kimi|claude --probe` checks a
selected configuration and local adapter path; PASS does not prove a live
host loaded the hook. Drift checks are local preflight. A live sentinel is
explicit, may consume provider quota, and covers only its sampled root Bash
call. See the [host drift guide](docs/guides/host-drift.md).

## Decisions, recovery and documentation

The shared protocol is `ALLOW · SANITIZE · RELOCATE · SNAPSHOT · ASK · BLOCK`,
with stable reason codes and explanations.
Aggregation is `BLOCK > ASK > RELOCATE/SNAPSHOT > SANITIZE > ALLOW`.
Opaque targets and protected boundaries are refused; supported recoverable
mutations compensate before proceeding. Authorization state remains a local
discipline, not an isolated identity boundary.

After an incident, [recovery-audit](skills/recovery-audit/SKILL.md) helps recover
from surviving Git/session/cache evidence, distinguishing original bytes,
reconstruction and known gaps. It cannot recreate bytes that no source kept.

| Document | Purpose |
|---|---|
| [Getting started](docs/guides/getting-started.md) | Package paths, agent setup prompt and safe verification |
| [Documentation index](docs/README.md) | Guides, design, Lab, reports, releases and history |
| [Architecture](docs/design/architecture.md) | Shared Core, compensation and adapter flow |
| [Threat model](docs/design/threat-model.md) | Trust assumptions and unresolved boundaries |
| [Compatibility](docs/design/compatibility.md) | Interface and release contracts |
| [0.2.5-rc1 notes](docs/releases/release-notes-0.2.5-rc1.md) | This candidate's changes and validation status |

## Contributing

Bug reports, compatibility evidence, documentation fixes and focused code
changes are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) before sharing
logs. Use [Issues](https://github.com/mokuyoaxis/agent-guard/issues) and
[Pull Requests](https://github.com/mokuyoaxis/agent-guard/pulls); keep credentials,
private configuration and unredacted incident records out of public reports.

## Community

[![LINUX DO community link](assets/linux-do-community.svg)](https://linux.do/t/topic/2942799)

This project-made banner links to our
[LINUX DO project post](https://linux.do/t/topic/2942799). It does not imply
official endorsement by the community.

## License

MIT — see [LICENSE](LICENSE).
