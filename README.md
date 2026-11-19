<h1 align="center">
  <img src="assets/agent-guard-logo.svg" alt="AGENT-GUARD" width="760">
</h1>

<p align="center">
  <a href="https://github.com/mokuyoaxis/agent-guard/actions/workflows/ci.yml"><img src="https://github.com/mokuyoaxis/agent-guard/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://www.npmjs.com/package/@mokuyoaxis/agent-guard"><img src="https://img.shields.io/npm/v/%40mokuyoaxis%2Fagent-guard.svg" alt="npm stable"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/mokuyoaxis/agent-guard" alt="License"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&amp;logoColor=white" alt="Python 3.9+"></a>
  <a href="docs/releases/release-notes-0.3.0.md"><img src="https://img.shields.io/badge/notes-0.3.0-5B6B7A" alt="0.3.0 notes"></a>
</p>

<p align="center">
  <strong>Disclosure checks · Recoverable deletion · Agent experiments</strong><br>
  <a href="README.zh-CN.md">简体中文</a>
</p>

Coding agents fail in predictable ways: an over-broad `rm` wipes uncommitted work, a diagnostic dump leaks credentials, or a CLI update silently breaks a hook. **Agent Guard** puts deterministic safety checkpoints and empirical verification around your coding agent, backed by a shared Python Core (`3.9+`) and optional host integrations.

> [!IMPORTANT]
> **AI agents configuring this repository:** Before editing host configuration or bridges, read [`adapters/INTEGRATION.md`](adapters/INTEGRATION.md), the [setup workflow](docs/guides/getting-started.md#set-up-a-coding-agent), and the adapter guide for your target host. Verify against the live host version and its official hook docs, then report only observed coverage—installing a Skill or copying config does not prove interception is active.

## Capabilities at a glance

| Capability | What it does | Guides & contracts |
|---|---|---|
| **Delete-guard** | Recoverable deletion and Git snapshots before destructive overwrites; Cleanup Copilot inventory and reviewed maintenance tasks; workspace budgets, rate/repeat limits, and protected paths; transaction restore, manual GC, and optional local MCP tools | [Skill & CLI](skills/delete-guard/SKILL.md) · [Cleanup Copilot](docs/guides/cleanup-plan.md) · [Deletion limits](docs/guides/deletion-limits.md) · [Recovery & GC](docs/guides/recovery-feedback.md) · [MCP tools](docs/guides/mcp.md) · [MCP doctor](docs/guides/mcp.md#diagnose-a-local-service-028) |
| **Exfil-guard** | Outbound scanning for documented credential formats, secret source references, and host-identifying paths; plan-verified redaction; scalar-free JSON/dotenv inspection (`safe_view`) | [Skill & CLI](skills/exfil-guard/SKILL.md) · [Rules](skills/exfil-guard/references/rules.md) · [Safe config view](skills/exfil-guard/SKILL.md#safe-config-view) |
| **Guard-lab** | Controlled experiments in disposable fixtures using four zero-model calibration controls, synthetic markers, declared-channel observers, and matched guard-off / guard-on runs | [Lab guide](docs/lab/guard-lab.md) · [Four-control example](docs/lab/blue-team-calibration.md) · [Evidence levels](docs/lab/guard-lab.md#evidence-levels) |

Core and adapters form shared infrastructure across all three modules. After an incident, [recovery-audit](skills/recovery-audit/SKILL.md) provides a separate workflow for reconstructing a workspace from surviving Git, session, and cache records.

## How Agent Guard works (and what it does not claim)

- **Guidance vs. enforcement:** Skills teach the agent safe habits, while automatic interception depends on an active host hook (`PreToolUse` or the DSH plugin) and its covered tools. The optional local MCP server exposes explicit maintenance tools—it does not intercept native shell or editor calls that bypass it.
- **Reliability infrastructure, not an OS sandbox:** Agent Guard runs with the **same OS privileges** as the agent. It is built to catch mistakes—wrong working directories, mis-expanded commands, accidental leaks—rather than contain a determined adversary sharing the same user account ([Threat model](docs/design/threat-model.md)).
- **Fail-closed decisions on destructive operations:** All paths share a single verdict protocol (`ALLOW · SANITIZE · RELOCATE · SNAPSHOT · ASK · BLOCK`, aggregated with `BLOCK > ASK > RELOCATE/SNAPSHOT > SANITIZE > ALLOW`). When Delete-guard recognizes a supported destructive command (or encounters unparseable shell syntax) and cannot statically determine its targets or effects—such as unresolved shell variables, command substitution, unbalanced quotes, indirect interpreters, or stdin-fed targets—it refuses with `BLOCK` rather than guessing. Unrecognized benign commands (such as `printf "$PWD"` or `$(pwd)`) still return `ALLOW_NOOP` ([Policy contract](skills/delete-guard/references/policy.md)).
- **Per-workspace policies:** Delete-guard budgets, rate limits, and protected paths apply per workspace root via `.agent-guard/delete-policy.json`, falling back to built-in defaults for omitted keys ([Workspace configuration](docs/guides/deletion-limits.md#configuration-and-defaults)).

<a id="quick-start-with-your-coding-agent"></a>
<a id="install-and-choose-a-version"></a>

## Installation & versions

Core requires **Python 3.9+** and **Git** (examples assume a POSIX shell). The optional local MCP server requires **Python 3.10+** and its separately installed [pinned MCP SDK](docs/guides/mcp.md).

Install the current **`0.3.0`** release into a stable, user-owned prefix:

```sh
npm install --prefix /absolute/path/to/agent-guard-install @mokuyoaxis/agent-guard@0.3.0
```

Your package root is `/absolute/path/to/agent-guard-install/node_modules/@mokuyoaxis/agent-guard` (keep the `@mokuyoaxis/` scope). You can also run directly from a Git checkout:

```sh
git clone https://github.com/mokuyoaxis/agent-guard.git
cd agent-guard
```

Verify exact publication details on [GitHub Releases (`0.3.0`)](https://github.com/mokuyoaxis/agent-guard/releases/tag/0.3.0) or [npm (`0.3.0`)](https://www.npmjs.com/package/@mokuyoaxis/agent-guard/v/0.3.0). See [Getting started](docs/guides/getting-started.md) for offline tarball installation, upgrades, and full dependency details (including Node 22 for the optional DSH read prototype and Python 3.11+ for Kimi TOML helpers).

<details>
<summary>Release history & alternate channels (0.2.4 – 0.3.0)</summary>

| Release | Key additions |
|---|---|
| **[0.3.0](docs/releases/release-notes-0.3.0.md)** | Reviewed maintenance workflow (saved target plans, single-step operator approval, agent task execution/query), optional quarantine requirement, linked Skill installer, optional Codex native confirmation form |
| **[0.2.9](docs/releases/release-notes-0.2.9.md)** | Read-only Cleanup directory overview (`--discover`) and targeted measurement (`--measure`), two read-only MCP tools, faster plan scans |
| **[0.2.8](docs/releases/release-notes-0.2.8.md)** | Local MCP service diagnostics (`doctor.py mcp`) and protocol-interruption recovery checks |
| **[0.2.7](docs/releases/release-notes-0.2.7.md)** | Cleanup Copilot plans, shared deletion admission (capacity, budgets, rate/repeat limits, protected paths), optional local MCP server |
| **[0.2.6](docs/releases/release-notes-0.2.6.md)** | Optional Kimi Stop recovery feedback and doctor checks, DSH Lab preflight diagnostics, DSH `0.2.1-alpha.4` deletion support, Kimi `2.3.0` Bash profile |
| **[0.2.5](docs/releases/release-notes-0.2.5.md)** / **[0.2.4](docs/releases/release-notes-0.2.4.md)** | Earlier stable releases (explicit storage/GC/NOOP feedback, durable relocation, `trash_index` queries) |
| **[0.2.5-rc3](docs/releases/release-notes-0.2.5-rc3.md)** / **[0.2.4-hotfix.1](https://github.com/mokuyoaxis/agent-guard/releases/tag/0.2.4-hotfix.1)** | Opt-in channels (`npm` tags `rc` and `hotfix`; neither replaces `latest`). `0.2.4-hotfix.1` backports quarantine control-path protection onto `0.2.4` |

Note: Deletion admission limits introduced in `0.2.7` can refuse high-volume or unprotected deletions that succeeded on `0.2.6` and earlier. Upgrading from an older version requires an explicit install/upgrade step; global Skills and host configurations are not updated automatically.

</details>

## Core workflows

<a id="recover-a-supported-deletion"></a>

### 1. Recoverable deletion & reviewed cleanup (`Delete-guard`)

Run `safe_delete.py` from the workspace root you are protecting, pointing to your installed package or checkout root:

```sh
python3 /absolute/path/to/agent-guard/skills/delete-guard/scripts/safe_delete.py src/old_module.py --dry-run
python3 /absolute/path/to/agent-guard/skills/delete-guard/scripts/safe_delete.py src/old_module.py
python3 /absolute/path/to/agent-guard/skills/delete-guard/scripts/restore.py list
python3 /absolute/path/to/agent-guard/skills/delete-guard/scripts/restore.py '<txid>'
```

**Execution & recovery guarantees:**
- **Relocate vs. discard:** Valuable files move into workspace quarantine with exact recovery paths recorded in a transaction manifest. Provably regenerable, Git-ignored build artifacts may be deleted directly unless the operator requires quarantine.
- **Git & restore safety:** Supported Git overwrites snapshot tracked state first, and hard-reset collisions with untracked or ignored content are refused. `--force` restore moves the current occupant to a separate quarantine transaction before restoring.
- **Non-destructive dry runs & advisory checks:** `check.py` and `safe_delete.py --dry-run` leave command targets untouched (they may create quarantine/audit metadata and a local Git exclude entry). Exit code `0` from an advisory check reports assessment completion—never permission to run a `BLOCK` or `ASK` command.
- **Operational accounting:** Quarantined files still occupy disk until you run manual garbage collection (GC). `ALLOW_NOOP` means no supported destructive pattern was matched, not that arbitrary shell scripts are safe. Failed operations can leave partial state—check reported `txid`s before retrying. See [Recovery & GC](docs/guides/recovery-feedback.md) and [Audit metadata](docs/guides/audit-metadata.md).

**Reviewed batch maintenance (`0.3.0`):** For multi-root cleanup, Cleanup Copilot separates *proposal*, *human approval*, and *agent execution*. Save an explicit target list (optionally requiring quarantine for regenerable artifacts), review the full selection in your terminal and approve once—or, when using the optional [Codex native form](docs/guides/mcp.md#host-confirmation-030-development), open the exported complete list (`host_approval.review.file`), review the selected targets, and confirm in the form—then let the agent execute and query by task ID via CLI or MCP while keeping all path protections, budgets, and rate limits active. Default read-only discovery/measurement (`--discover`, `--measure`) never authorizes deletion; `--save-plan` and `--approve` persist control metadata locally without moving files.

<a id="check-text-before-emission"></a>

### 2. Outbound inspection & redaction (`Exfil-guard`)

Before writing drafts or sending prompts externally, scan with `check_span.py` or rewrite with `sanitize.py`. Call them from your workspace pointing to your installed package or checkout root (or omit the prefix when already at the repository root):

```sh
python3 /absolute/path/to/agent-guard/skills/exfil-guard/scripts/check_span.py --channel file-write --json < draft.md
python3 /absolute/path/to/agent-guard/skills/exfil-guard/scripts/sanitize.py --channel file-write < draft.md
```

Try a synthetic credential constructed at runtime:

```sh
python3 -c 'print("config: sk-proj-" + "AbCdEf0123456789GhIjKl")' |
  python3 /absolute/path/to/agent-guard/skills/exfil-guard/scripts/sanitize.py --channel file-write
# -> config: sk-<REDACTED>
```

**Design split:**
- **`check_span.py` is read-only:** Returns a verdict (`ALLOW`, `SANITIZE`, `ASK`, `BLOCK`) and a verified redaction plan without modifying input. Exit `0` indicates `ALLOW`/`SANITIZE`.
- **`sanitize.py` rewrites only on safe paths:** Outputs allowed or redacted text to stdout on `ALLOW`/`SANITIZE`; suppresses stdout completely on `ASK`, `BLOCK`, or validation errors.
- **What is covered:** Documented credential formats, value-free secret source references (e.g., environment dumps), and host-identifying paths—not general repository-wide secret auditing or deliberate encoding obfuscation. The caller remains responsible for emission and egress audit logging.
- **Configuration structure without secrets:** Use `safe_view` to inspect supported JSON and `.env` schemas without displaying scalar values; it is a read-only view, not an automatic read hook.

See the [Exfil-guard Skill](skills/exfil-guard/SKILL.md), [Detection rules](skills/exfil-guard/references/rules.md), and [Channel contract](skills/exfil-guard/references/channels.md).

<a id="guard-lab"></a>

### 3. Controlled behavioral verification (`Guard-lab`)

**Guard-lab** tests whether a real harness and guard integration behave as expected without using real secrets. It seeds a disposable fixture with non-secret synthetic markers and harmless bait while keeping the controller and tamper-detecting hash-chained evidence outside the workspace exposed to the agent.

Its methodology enforces three steps:
1. **Calibrate:** Confirm observers, marker scans, and the hash-chained evidence pipeline pass all four zero-model controls.
2. **Establish baseline exposure:** Confirm an *unguarded* trial triggers the target channel (a silent unguarded run cannot prove mitigation).
3. **Compare guarded runs:** Re-run with guards active under identical host version, model, task, protocol, and non-guard settings.

From the installed package root or checkout root, run a single zero-model calibration control (installed packages also expose `agent-guard-lab`):

```sh
lab_run="$(mktemp -d)"
python3 guard_lab.py run --case clean --output-dir "$lab_run/clean" --json
```

Or from a package or checkout containing the calibration example, run all four zero-model controls (`clean`, `mock-positive`, `mock-injection`, `snapshot-positive`):

```sh
blue_run="$(mktemp -d)"
python3 docs/lab/examples/blue-team-calibration.py --output-dir "$blue_run/controls"
```

> [!NOTE]
> A passing control verifies **instrument calibration**, not model safety. The hash chain flags corrupted or altered evidence as `INCONCLUSIVE`, though a process sharing the same OS privileges can still modify local logs and hashes. Observations apply strictly to declared channels rather than arbitrary file reads or unmonitored network egress. See the [Lab guide](docs/lab/guard-lab.md), [Four-control example](docs/lab/blue-team-calibration.md), [Blue-team research](docs/research/README.md), and existing [Kimi](docs/reports/test-report-kimi-guard-lab.md) / [DSH](docs/reports/test-report-dsh-guard-lab.md) reports.

<a id="integration-and-validation-matrix"></a>
<a id="connect-your-coding-agent"></a>

## Host integrations & observed coverage

Give your coding agent the [shared setup prompt](docs/guides/agent-setup-prompt.md) along with [`adapters/INTEGRATION.md`](adapters/INTEGRATION.md) to configure the installed package root and workspace. Where available, pair MCP maintenance tools with a verified blocking hook (`install_skill.py` links `skills/delete-guard` into compatible Skill directories; see [Skill installation](docs/guides/skill-installation.md)).

In the table below, entries in the **MCP & maintenance (`0.3.0`)** column come from their respective recorded `0.3.0` pre-release checkpoints (not a per-host re-run of the final published artifact), whereas **Native hook evidence** retains each adapter's own recorded test checkpoint (for example, DSH `0.2.1-alpha.4` default deletion was validated at the `0.2.6` checkpoint, and scripted Claude `Bash` hooks at `2.1.270` / `2.1.273`). Every row applies only to those tested host versions and tool paths; other combinations require separate validation (see the full [Capability matrix](docs/guides/harness-capabilities.md) and [0.3.0 acceptance report](docs/reports/test-report-maintenance-0.3.0.md#host-acceptance)):

| Host | Tested version | MCP & maintenance (`0.3.0`) | Native hook evidence & known gaps | Guides |
|---|---|---|---|---|
| **Codex** | `0.165.0` | Passed fresh-approved 71-root batch, real cache task, and optional native-form model task with independent verification & restore | Native shell/editor interception requires separate validation; terminal approval remains the default | [Report](docs/reports/test-report-maintenance-0.3.0.md#host-acceptance) · [Earlier trials](docs/guides/mcp.md#codex-01650-with-published-027) |
| **Kimi Code** | `2.3.0` | Passed standard and fresh-approved MCP maintenance tasks | Observed root-Bash native block -> plan -> terminal approval -> original-session execution & restore; Stop and missing/timed-out hook caveats remain separate | [Report](docs/reports/test-report-maintenance-0.3.0.md#host-acceptance) · [Setup](adapters/kimi-code/README.md) |
| **DSH** | `0.2.0-rc.2` | Passed repaired original-root startup and fresh-approved MCP execution | Native `0.2.0-rc.2` / `0.2.1-alpha.4` plugin evidence; a later native trial hit an OS sandbox block under PRoot (separate zero-model local control passed) | [Report](docs/reports/test-report-maintenance-0.3.0.md#host-acceptance) · [Compatibility](docs/design/dsh-compatibility.md) · [Setup](adapters/dsh/README.md) |
| **Claude Code** | `2.1.311` / `2.1.270–273` | `2.1.311` connected and discovered 10 MCP tools; live model maintenance deferred | Scripted native Bash hook evidence is from `2.1.270` / `2.1.273`; `2.1.311` native hooks remain unverified | [Report](docs/reports/test-report-maintenance-0.3.0.md#host-acceptance) · [Setup](adapters/claude/README.md) |
| **Other hosts** | — | Skill / CLI / MCP cooperative usage | Use the [adaptation guide](adapters/INTEGRATION.md) for local setup; verify actual blocking before claiming native interception | [Adaptation guide](adapters/INTEGRATION.md) |

For ongoing configuration health, `doctor.py kimi|claude --probe` and `--check-drift` inspect local wiring against compatibility profiles (`PASS` validates local files, not live host hook loading). Optional live sentinels test one sampled root-agent Bash call and may use provider quota. See the [Host drift guide](docs/guides/host-drift.md).

## Documentation

| Document | Focus |
|---|---|
| [Getting started](docs/guides/getting-started.md) | Installation channels, setup prompt, and non-destructive verification |
| [Documentation index](docs/README.md) | Full map of guides, design docs, Lab workflows, reports, releases, and history |
| [Architecture](docs/design/architecture.md) | Core pillars, Decision Protocol, dialects, compensation, and adapter data flow |
| [Threat model](docs/design/threat-model.md) | Trust assumptions, attack vectors, and security-boundary upgrade paths |
| [Compatibility](docs/design/compatibility.md) | Adapter contracts, interface guarantees, and release rules |
| [Blue-team research](docs/research/README.md) | Defensive methods, sanitized studies, and calibration baseline |
| [0.3.0 release notes](docs/releases/release-notes-0.3.0.md) · [0.2.9 notes](docs/releases/release-notes-0.2.9.md) | Release summaries, upgrade impacts, and verification scope |

## Contributing

Bug reports, compatibility reports, documentation improvements, and focused PRs are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) before submitting defensive research or logs, and use [Issues](https://github.com/mokuyoaxis/agent-guard/issues) and [Pull Requests](https://github.com/mokuyoaxis/agent-guard/pulls). Never commit credentials, private configuration, or unredacted incident records.

## Community

[![LINUX DO community link](assets/linux-do-community.svg)](https://linux.do/t/topic/2942799)

Project discussion thread on [LINUX DO](https://linux.do/t/topic/2942799), maintained by the authors.

## License

MIT — see [LICENSE](LICENSE).
