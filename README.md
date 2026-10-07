# AGENT-GUARD

[![CI](https://github.com/mokuyoaxis/agent-guard/actions/workflows/ci.yml/badge.svg)](https://github.com/mokuyoaxis/agent-guard/actions/workflows/ci.yml)
[![npm version](https://img.shields.io/npm/v/%40mokuyoaxis%2Fagent-guard.svg)](https://www.npmjs.com/package/@mokuyoaxis/agent-guard)
[![License](https://img.shields.io/github/license/mokuyoaxis/agent-guard)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![v0.2.4 source](https://img.shields.io/badge/Source-v0.2.4-5B6B7A)](docs/releases/release-notes-0.2.4.md)

**Make destructive agent actions reversible by default.** · [简体中文](README.zh-CN.md)

Agent Guard is a reliability layer for coding agents. It makes supported
high-impact actions recoverable instead of permanently destructive, while
keeping routine work automatic.

- **Destructive file operations** can be relocated to `.agent-trash/` with a
  recovery manifest instead of being permanently deleted.
- **Destructive Git operations** can snapshot recoverable state before they
  overwrite the working tree.
- **Accidental outbound disclosure** of known credentials or host-identifying
  absolute paths can be checked through a cooperative text CLI. A caller that
  owns the emission can apply its redaction plan, escalate, or block.
- **Synthetic honeytoken experiments** can exercise declared local channels
  with zero-token controls and fail-inconclusive evidence health checks.

The Core is harness-neutral and supports Python 3.9+ and Git. Automatic
interception still depends on whether the host exposes a compatible hook; a
Skill by itself does not intercept tool calls. The shared Core, Decision
Protocol, and Skills define the product; harness adapters are replaceable
integration bridges rather than the product boundary.

> **Agent Guard keeps reversible actions automatic and escalates only when it
> cannot safely automate them.** It is reliability infrastructure, not a
> security sandbox: it protects against mistakes, not a malicious agent with
> equal OS privileges.

## What it looks like

```text
rm -rf build/       → RELOCATE   # an in-scope tree moves to quarantine
rm -rf .            → BLOCK      # the workspace root is protected
git reset --hard    → SNAPSHOT   # after collision preflight, snapshot tracked changes
git push --force    → BLOCK      # remote history is not automated
```

These are illustrative verdicts for supported inputs, not commands to run or
proof that every harness intercepts them. Ignored, regenerable targets may be
`ALLOW`; hard reset is blocked if its target tree would overwrite untracked
or ignored content, or if its preflight/snapshot cannot be completed. When
recovery or safe rewriting is possible, the agent can keep working. Otherwise,
the guard asks the human or blocks the operation.

## Quick start with your coding agent

Use the scoped npm package or a stable Git checkout. The unscoped
`agent-guard` package is unrelated to this project.

Pinned npm installation into a stable, user-owned prefix:

```sh
npm install --prefix /absolute/path/to/agent-guard-install @mokuyoaxis/agent-guard@0.2.4
```

**0.2.4** adds hard-reset collision checks,
restore path validation, current-version preservation on forced restore, and
a read-only trash location query. See the [release notes](docs/releases/release-notes-0.2.4.md)
for behavior changes and evidence limits. The npm badge and
[GitHub Releases](https://github.com/mokuyoaxis/agent-guard/releases) show
which artifacts are available.

The package root is then
`/absolute/path/to/agent-guard-install/node_modules/@mokuyoaxis/agent-guard`.
Alternatively, clone the source (skip this if you already have a checkout):

```sh
git clone https://github.com/mokuyoaxis/agent-guard.git
cd agent-guard
```

Python 3.9+ and Git are required for the Core; native interception depends on
the host's hook support. Then give your coding agent the following setup prompt
(replace the path with your checkout):

```text
Set up agent-guard for this workspace. Use either an existing Git checkout or
the exact scoped npm package @mokuyoaxis/agent-guard@0.2.4;
never install the unscoped package named agent-guard. If 0.2.4 is unavailable,
report that and use the existing checkout or ask me to choose a published
version. Before installing, ask me to choose and
approve a stable user-owned prefix. Treat the checkout or installed package
root as /absolute/path/to/agent-guard below.
First identify the current harness and its actual hook/skill capabilities;
read this README and the matching adapter README. Check Python and Git.
Install the relevant Skills, then configure a native shell hook only if this
harness supports one. Preserve existing settings and show me the proposed
diff before editing user-wide configuration or installing dependencies.
For Claude Code use adapters/claude/README.md; for Kimi Code use
adapters/kimi-code/README.md; for DSH use adapters/dsh/README.md.
For another host, read adapters/INTEGRATION.md and do not invent a native
hook. If no blocking pre-tool hook is verified, use only Skill/CLI and say
plainly that automatic interception is not enabled.
Verify a harmless command and pass a BLOCK-shaped command only as data to
check.py; never execute a destructive test command. For Claude/Kimi, run the
local doctor but do not treat its PASS as proof of host interception. Report
the host version, tool coverage, what was installed, what the host actually
intercepted, and any unverified paths.
```

For manual setup and evidence limits, see the
[adapter matrix](docs/guides/harness-capabilities.md) and the adapter README for your
host.

## Design principles

| Principle | Guarantee |
|---|---|
| **Stay in scope** | The guard blocks deletion of the workspace root, `.git`, and outside paths when the operation reaches it |
| **Make it recoverable** | Supported deletions relocate to `.agent-trash/` with a manifest; destructive Git overwrites snapshot first |
| **Constrain authorization** | Authorization is session-scoped; a veto downgrades one-way, and only a human restores it |
| **Leave a durable trail** | Enforced verdicts, compensation intents, outcomes, and restores use append-only JSONL; mutation fails closed if its intent cannot be stored |

One rule runs through all four: **uncertainty increases restriction.**

## How it decides

Each inspected operation is classified by its effect and then mapped to the
least restrictive decision that preserves the relevant safety or recovery
guarantee. The stable interface is a Decision Protocol, not a binary
allow/block check:

```
Effect → Classifier → Policy → Decision   ∈ { ALLOW, SANITIZE, RELOCATE,
                                            SNAPSHOT, ASK, BLOCK }
                                + ReasonCode   (stable, machine-readable)
                                + Explanation  (human-facing)
                                + RecoveryPlan (txids, strategy)
```

| Tier | Decisions | What the agent experiences |
|---|---|---|
| **SAFE** | `ALLOW` · `SANITIZE` · `RELOCATE` · `SNAPSHOT` | Runs silently; compensation is applied first where needed; recoverable mutations are restorable via txid. `SANITIZE` returns a plan for the *payload owner* to rewrite (not a command rewrite) |
| **AMBIGUOUS** | `ASK` | Single-execution authorization (`ASK_ONCE`) — e.g. compound shapes the guard cannot safely automate |
| **FORBIDDEN** | `BLOCK` | Refused with reason and remediation; never askable |

Precedence when several decisions meet in one operation, weakest to strongest:

```
ALLOW < SANITIZE < RELOCATE < SNAPSHOT < ASK < BLOCK
```

`SANITIZE` ranks *below* `ASK` deliberately: it is automatic (SAFE tier),
while `ASK` forfeits automation. A payload carrying both a sanitizable secret
and a shape that cannot be rewritten must `ASK` — you cannot silently proceed
when part of the emission is uninspectable.

True effect uncertainty (`$VAR` targets, `bash -c`, `find -delete`, stdin-fed
lists) stays on the BLOCK path: allowing it would forfeit the core guarantee.
Adapters map decisions onto their harness natively — DSH `PreToolDecision`,
Claude Code PreToolUse `ask`, or a deny carrying the explanation where no ask
exists.

## What Agent Guard includes

### `delete-guard`

Answers *"if this destroys something, can we come back?"* It runs before a
delete or destructive Git action when invoked through a supported adapter or
CLI, and compensates first when recovery is possible.

### `exfil-guard`

Answers *"if this leaves the machine, was it supposed to?"* Its cooperative
CLI checks text before an emission when the payload owner invokes it, returning
a redaction or escalation decision for supported patterns.

### `recovery-audit`

The incident-response companion for cases where prevention never ran or did not
cover the path. It establishes source precedence, distinguishes recovered bytes
from reconstructed behavior and known gaps, audits replay tooling, and keeps
landing, commit, push, and release as separate authorization gates.

`delete-guard` and `exfil-guard` are the two preventive guard branches;
`recovery-audit` handles evidence-led recovery after the fact.

## recovery-audit

Sometimes prevention never ran: a harness had no adapter, a subagent bypassed
the expected path, or an over-broad command removed the workspace before anyone
could intervene. The working tree may be gone while the coding agent's session
cache still preserves successful patches, file snapshots, tool results, diffs,
and command context.

`recovery-audit` turns those remnants, Git remotes/reflogs/stashes, editor or
tool caches, build artifacts, and project plans into an evidence-led recovery:

- every unit is labelled **recovered**, **reconstructed**, or **missing**;
- recorded tool effects are replayed in chronology and checked for divergence;
- repeated replay must produce a byte-identical tree;
- landing, commit, push, and release remain separate authorization gates.

It is not filesystem undelete and cannot recreate bytes no surviving source
captured. Its promise is a fast, auditable path to the strongest project state
the evidence actually supports, with gaps reported instead of hidden.

## exfil-guard

`exfil-guard` checks text before an agent writes, sends, commits, or pushes it
**when the payload owner calls its CLI**. It also offers an explicit, read-only
safe view of selected JSON/dotenv configuration files. The text scanner is
designed to catch two accidental disclosure classes: **known credentials**
and **host-identifying absolute paths**. Depending on the channel, it can
allow the payload, return a redaction plan, ask for a human decision, or block
the emission.

DSH also offers a [default-off text-read redaction prototype](adapters/dsh/README.md#experimental-text-read-redaction)
for complete native reads in a pinned composition. It reuses Core and
regenerates both rendered text and presentation metadata; a zero-model native
probe covers the next request and durable JSONL log. A separate
[official Flash direct-read trial](docs/reports/test-report-dsh-real-followup.md)
observed supported synthetic-secret redaction while useful config stayed readable.

It is a prevention and redaction guard, not a compensation engine: after an
emission there is nothing to recover. It is also **not a security sandbox** and
does not attempt to stop adversarial exfiltration by an agent with equal OS
privileges.

### Decisions exfil-guard can return

The full Decision Protocol applies, but only four classes are reachable for
a text payload (`RELOCATE`/`SNAPSHOT` belong to delete-guard — the guard
cannot rewrite what it did not write):

| Decision | Meaning | Example |
|---|---|---|
| `ALLOW` | nothing matched, a documented placeholder, or a workspace-relative path | `echo "hello" \| check_span.py` |
| `SANITIZE` | a redaction plan is returned; the *payload owner* rewrites and emits | a real key on `file-write` / `llm-request` |
| `ASK` | the channel cannot be rewritten and cannot be taken back | a host path on `shell-stdout` |
| `BLOCK` | refuse: immutable/remote history, an un-scannable payload, invalid config | a credential in `git-push-payload` |

### What is detected

**T1 vendor credential patterns** (`secret/*`). These rules use known formats;
documented placeholders and negative examples constrain false positives.
Rule ids: `secret/openai-key`, `secret/github-token`,
`secret/aws-access-key-id`, `secret/gitlab-token`, `secret/slack-token`,
`secret/stripe-key` (live keys only — `sk_test_` is exempt),
`secret/pypi-token`, `secret/jwt` (structural: the header must decode to JSON
with a nonempty ASCII string `alg`), and
`secret/private-key-block` (whole `-----BEGIN ... PRIVATE KEY-----` block,
redacted in one piece). See
[skills/exfil-guard/references/rules.md](skills/exfil-guard/references/rules.md)
for the rule table and remaining ambiguities.

`secret/connection-password` also detects nonempty passwords in URI userinfo.
It redacts only the password, preserving the scheme, account and host for
diagnostics. Percent-encoded passwords and JSON-escaped scheme slashes are
supported; this does not hide network topology or scan every DSN format.

**Value-free secret references** (`secret/source-reference`). The guard
classifies complete components in an environment variable's *name* (such as
`KEY`, `TOKEN`, `SECRET`, `PASSWORD`, `PASSWD`, `CRED`, or `AUTH`) and a
secret-store *file name*
(`.env`, `*.pem`, `id_rsa*`, `.netrc`, `kubeconfig`, ...), and detects
whole-environment expansions (`printenv`, `env | ...`, `cat
/proc/self/environ`). This scanner **does not resolve the variable value**;
that does not certify unrelated Guard output or existing audit records as
secret-free.

**Host-identifying paths** (`path/*`). `path/workspace-relative` is `ALLOW`
(the workspace is exempt); `path/system` (`/usr`, `/etc`, `C:\Windows`) is
`ALLOW`; `path/host-absolute` (under `HOME`/`TEMP`, a CI root, or a
workspace ancestor) is `SANITIZE`; `path/generic-absolute` (no host
correlation) is `ASK`; `path/device` (UNC, `\\?\`, pipes) is `SANITIZE`.

### Channels determine the disposition

A channel is defined by two facts: can it be **rewritten**, and does the
emission **persist**? `rewritable` is what makes `SANITIZE` meaningful;
`persistence` is what justifies `BLOCK`.

| Channel | Rewritable | Persistence | Default |
|---|---|---|---|
| `llm-request` | yes | remote | SANITIZE |
| `file-write` | yes | workspace | SANITIZE |
| `forge-comment` / `issue-body` / `pr-description` | yes | public | SANITIZE |
| `git-commit-message` | yes (rewrite argv) | remote history | **BLOCK** |
| `git-push-payload` | no | **remote** | **BLOCK** |
| `shell-stdout` | **no** | local transcript | ASK |
| `shell-file-redirect` | yes | local | ASK |
| `archive-upload` | yes | remote | ASK |
| `process-argv` | yes | local | ASK |

An unknown channel name is a configuration defect, not "no risk":
`check_span.py` returns `BLOCK_OUTPUT_UNSCANNABLE`, never an implicit ALLOW.

### Usage

`check_span.py` reads the payload on **stdin** and is a pure function — it
never writes, never rewrites, and never prints the match. `sanitize.py`
applies the plan the guard returned.

```bash
# a credential on a rewritable channel -> SANITIZE, exit 0
echo 'config: sk-proj-AbCdEf…' | python3 skills/exfil-guard/scripts/check_span.py --channel file-write

# a credential bound for remote history -> BLOCK, exit 2
echo 'token=ghp_abcdefghijklmnopqrstuvwxyz…' | python3 skills/exfil-guard/scripts/check_span.py --channel git-push-payload

# apply the redaction plan (format preserved: sk-<REDACTED>)
echo 'config: sk-proj-AbCdEf…' | python3 skills/exfil-guard/scripts/sanitize.py --channel file-write
```

Exit code contract: `0` = ALLOW/SANITIZED · `2` = BLOCK · `3` = ASK ·
`1` = ERROR. Use `--json` for the machine-readable verdict (offsets, rule ids
and placeholders only — **never the matched bytes**), and `--path` to enable
the repo-local exemption file for the file being written.

`sanitize.py` enforces the same exit codes: ASK/BLOCK and errors emit no
payload on stdout. It validates an external `--plan` against the current scan
and rescans the rewritten text before emitting. Both CLIs use the same default
workspace and host-side mode; stdin is bounded to the scan cap (4 MiB by default)
during reading, as is an external plan file. A checker plan produced with an
explicit mode or path exemption must still satisfy the sanitizer's current
policy; the plan itself does not authorize emission.

Credential detection covers classic and stateless `ghs_APPID_JWT` GitHub
installation tokens, including long signatures and credentials next to CJK
prose. Placeholder exemptions require whole shapes; incidental example words
inside a token do not exempt it. Secret variable references are classified by
complete name components, preserving ordinary identifiers such as `MONKEY`
and metadata such as `TOKEN_COUNT`. See the [rule reference](skills/exfil-guard/references/rules.md)
for supported forms and limitations.

PyPI publishing tokens are also detected with their documented prefix and
minimum body shape, with complete redaction of long tokens. AWS group/user/
role/policy IDs are kept distinct from access-key IDs; JWT headers require
the declared nonempty ASCII string `alg`. Public-key variable names and the
broad legacy `sk-` shape retain their documented conservative boundaries.

### Read a config without printing its values

Introduced in the `0.2.0` source; this CLI is **not** in the earlier
`0.2.0-rc2` preview tag.

```bash
python3 skills/exfil-guard/scripts/view.py --workspace /path/to/workspace .env
python3 skills/exfil-guard/scripts/view.py --workspace /path/to/workspace config.json
```

The path must be relative to that workspace. The JSON output preserves field
names and structure, plus scalar types and `set`/`empty` states, but **never
scalar values**. Known secret-shaped field names are hidden; unknown secrets
in field names remain a limitation. Only UTF-8 JSON and a strict, single-line
dotenv subset are supported (256 KiB maximum, 16 levels, 2048 nodes). The
CLI refuses symlinks, hardlinks, special files, unsafe paths, malformed input,
and platforms without safe descriptor-relative reads. Exit `0` means a view
was produced, `2` means refused, and `1` means an internal error. This view
is for diagnosis only: do not write it back over the original config. It
does not intercept ordinary file reads made by a harness.

### Relationship to delete-guard

They are two halves of the same promise, on opposite sides of the action:

| | `delete-guard` | `exfil-guard` |
|---|---|---|
| Question | "can we come back?" | "was this supposed to leave?" |
| Guards | *before a delete* | *before an emission* |
| Response | compensate, then proceed | redact, then emit |
| Failure cost | recoverable via txid | **irreversible** |
| Entry point | `check.py -- <command>` | `check_span.py` (stdin) |

They share the vocabulary (`core/policy.py`), the aggregation (`worst()`),
the exemption discipline, and the audit log. `worst()` is shared by both
guards, which is why `SANITIZE` had to be ranked once, not twice.

### Coverage and limitations

This is stated plainly, because a reliability tool that overstates its reach
is a false security claim:

- **Not a sandbox.** It does not prevent adversarial exfiltration. An agent
  that obfuscates a secret to evade the scanner is out of scope; this
  catches *accidents*.
- **Channels with no hook are unreachable by construction.** A hosted model
  call with no proxy, the model's own tool calls, content produced *inside* a
  program, and the human clipboard get **no verdict at all** — no coverage is
  claimed there. See `references/channels.md` and
  [docs/history/secret-guard-analysis.md](docs/history/secret-guard-analysis.md) §2.4 for
  the reachability table this claim is traceable to.
- **Not a file scanner.** It is not a gitleaks replacement; it scans what the
  guard can see *on the way out*.
- **No history rewriting.** Detecting a secret already in Git history is a
  report at most. Rewriting history is a human action with its own risks.
- **No T3 entropy detector in this release.** Random-looking text alone is
  insufficient evidence of a credential; ordinary identifiers stay readable.

## Manual, harness-neutral usage

The Core has zero third-party dependencies. Requirements: Python 3.9+, POSIX
shell, and Git.

```bash
# delete something - it is quarantined, not destroyed:
python3 skills/delete-guard/scripts/safe_delete.py build/ --reason "stale"

# inspect and undo:
python3 skills/delete-guard/scripts/status.py
python3 skills/delete-guard/scripts/status.py --trash-index --root <projects> --json
python3 skills/delete-guard/scripts/restore.py list
python3 skills/delete-guard/scripts/restore.py <txid>

# quarantine maintenance (dry plan by default):
python3 skills/delete-guard/scripts/gc.py
```

The local [trash location query](docs/guides/trash-index.md) provides a shared
read-only JSON interface for agents and future UI callers. It reports scoped
location candidates and layout hints; it neither scans recovery contents nor
grants cleanup permission. It is included in 0.2.4.

A harness adapter can invoke the guard before a supported shell command and
map its exit status to the host's own tool decision:

```text
python3 skills/delete-guard/scripts/check.py --enforce -- "$COMMAND"
exit 0 → host may run the original command
exit 2 → deny
exit 3 → ask the human if supported; otherwise deny
exit 1 → guard error; fail closed
```

## What gets protected

These examples assume the command reaches the guard and its targets meet the
stated conditions; see the [capability matrix](docs/guides/harness-capabilities.md)
for what each host has actually demonstrated.

```text
rm -rf build/            → RELOCATE  (tree quarantined, command proceeds)
rm -rf .                 → BLOCK     (workspace root)
rm -rf $DIR/             → BLOCK     (unresolvable target: fail closed)
rm *.log                 → BLOCK     (opaque glob; safe_delete expands it)
cd X && rm -rf build     → ASK_ONCE  (COMPOUND_CWD_DELETE)
touch f && rm f          → ASK_ONCE  (COMPOUND_CREATE_DELETE)
git clean -fd            → RELOCATE  (enumerate via -n, relocate, proceed)
git reset --hard         → SNAPSHOT  (collision preflight + tracked snapshot)
reset with collisions   → BLOCK     (preserve untracked/ignored paths first)
git push --force         → BLOCK     (remote history is never automated)
node_modules/ (ignored)  → ALLOW     (provably regenerable)
quarantine full          → BLOCK     (never fall back to permanent delete)
```

## Integration and validation matrix

[![Node.js 22 smoke](https://img.shields.io/badge/Node.js-22%20smoke-339933?logo=nodedotjs&logoColor=white)](.github/workflows/ci.yml)
[![Codex Skill/CLI tested](https://img.shields.io/badge/Codex-Skill%2FCLI%20tested-000000?logo=openai&logoColor=white)](docs/reports/test-report-codex-gpt-6-astra-high.md)
[![DSH 0.1.5-rc.1 host BLOCK tested](https://img.shields.io/badge/DSH%200.1.5--rc.1-host%20BLOCK%20tested-4D6BFE)](docs/reports/test-report-dsh-0.1.5-rc.1.md)
[![ZCode win32 CLI evaluated](https://img.shields.io/badge/ZCode-win32%20CLI%20evaluated-7C5CE0)](docs/reports/test-report-zcode-glm-flash.md)
[![Claude Code hook tested with scripted model](https://img.shields.io/badge/Claude%20Code-hook%20tested%20%28scripted%20model%29-D97757?logo=anthropic&logoColor=white)](docs/reports/test-report-claude-code-harness.md)
[![Kimi Code 2.1.1 root Bash BLOCK](https://img.shields.io/badge/Kimi%20Code%202.1.1-root%20Bash%20BLOCK-5B9BD5)](docs/reports/test-report-kimi-code-block.md)
[![WorkBuddy Windows Core/CLI evaluated](https://img.shields.io/badge/WorkBuddy-Windows%20Core%2FCLI%20evaluated-5B6B7A)](docs/reports/test-report-workbuddy-windows-core.md)
[![Windows Core CI](https://img.shields.io/badge/Windows-Core%20CI-0078D4)](docs/releases/release-notes-0.2.3-rc1.md#focused-windows-ci)
[![DSH native text-read checked](https://img.shields.io/badge/DSH-native%20text--read%20checked-4D6BFE)](docs/reports/test-report-release-readiness-0.2.3.md)

These badges link to scoped evidence; they do not certify every tool or model.
The DSH text-read feature is optional and off by default.

"The Core works", "a Skill-guided agent used it", and "the harness
intercepts every matching tool call" are separate claims. This table keeps
those evidence levels explicit:

| Harness / tested version | Integration path | Evidence and limit |
|---|---|---|
| **Claude Code 2.1.270 / 2.1.273** | [Native `PreToolUse` for Bash](adapters/claude/README.md) | [Real CLI + scripted model](docs/reports/test-report-claude-code-harness.md): sampled allow/ask/deny and Python-startup failure; other tools unverified. |
| **DSH 0.1.5-rc.1** | [Native pre-execute adapter](adapters/dsh/README.md) | [Real-host packaged-plugin probe](docs/reports/test-report-dsh-0.1.5-rc.1.md): execution-level `BLOCK` and loaded-adapter Core failure. A [real-model Lab baseline](docs/reports/test-report-dsh-guard-lab.md) stayed quiet with guard off, so no L2 mitigation claim exists; model-proposed destructive `bash` enforcement remains unverified. The separate opt-in read path is listed below. |
| **DSH CLI rc.1 / tools and FS rc.2 / Node 22** | [Experimental text-read redaction](adapters/dsh/README.md#experimental-text-read-redaction), default off | [Zero-model native probe](docs/reports/test-report-dsh-read-redaction.md): next request and durable JSONL. [Official Flash direct-read off/on](docs/reports/test-report-dsh-real-followup.md): supported synthetic secrets redacted in tool content/meta/session, useful config preserved; no injection L2 claim. |
| **DSH 0.2.0-rc.2 / Node 22** | [Default deletion and optional text-read adapter](adapters/dsh/README.md) | [Fresh contract review](docs/reports/test-report-release-readiness-0.2.3.md): native deletion blocking, read redaction on reviewed local/sandbox FS, next synthetic request and durable JSONL. Native v4 Lab support remains bounded; no new real-model L2 result. |
| **Codex CLI 0.154.0 (tested session)** | [Skill + production CLI](docs/reports/test-report-codex-gpt-6-astra-high.md) | Older-source cooperative acceptance; no native hook claim. |
| **WorkBuddy (version not recorded; Windows 11)** | [Core / CLI](docs/reports/test-report-workbuddy-windows-core.md) | Community feedback on 0.2.2 found two Core defects now covered by regressions and focused Windows CI; no WorkBuddy adapter or native-hook validation. |
| **ZCode (version unrecorded; win32)** | [Historical Skill/CLI + hook trial](docs/reports/test-report-zcode-glm-flash.md) | Older hook observation includes a persistent-permission bypass; current version unverified. |
| **Kimi Code 0.42.0 / 2.1.1** | [Native `PreToolUse` for Bash](adapters/kimi-code/README.md) | [Bounded tests](docs/reports/test-report-kimi-code-block.md): authenticated 2.1.1 root-Bash PASS on an OAuth official model and a second configured model route; older 0.42.0 root/child BLOCK, ASK denial and Python-failure refusal. Hook absence/timeout remains fail-open. |
| **Other / unlisted hosts** | [Self-adaptation guide](adapters/INTEGRATION.md) | No native claim without a blocking pre-tool event and independent non-execution check. |

For agent-led setup: identify the actual host version and tool names, follow
the matching guide above, preserve existing settings, then report separate
configuration, local-probe, and real-host evidence. For an unlisted host,
follow the [self-adaptation checklist](adapters/INTEGRATION.md); a prompt or
adapter exit code alone is not proof of interception. Ask before changing
user-wide settings, security policy, or dependencies.

`tests/test_conformance.py` checks the shared Core and Claude adapter; DSH
has a smoke test and Kimi has targeted adapter tests. The Kimi observations
above cover only the tested calls, not every shell construct or a general
concurrent-agent safety guarantee.
Run `python3 doctor.py kimi --probe` or `python3 doctor.py claude --probe`
to check a selected configuration file and the local shell bridge without a
model call. Add `--json` for machine-readable `configuration`, `local_probe`,
and `host_interception` statuses. The last status is always `UNVERIFIED`:
this doctor cannot prove that a live session loaded or enforced the hook.
Add `--check-drift` to query the local host version and compute privacy-safe
configuration/runtime fingerprints. It reports `CURRENT`, `STALE`,
`DRIFTED`, `BROKEN`, or `UNVERIFIED`; even `CURRENT` is static preflight
evidence, not interception proof. Optional create-new baselines contain only
the parsed version and SHA-256 fingerprints. See the
[host drift guide](docs/guides/host-drift.md) for the status and baseline contract.
An explicit `--live-sentinel` can then spend one configured model call in a
private blank fixture. It reports `PASS`, `FAIL`, or `INCONCLUSIVE` with
`NONE`/`NOTICE`, `CRITICAL`, or `WARNING`; marker absence alone never passes.
It retains a hashed/redacted result but not raw model output. This is a
reliability probe for a trusted provider, not a sandbox for a malicious model.
The optional Kimi doctor needs Python 3.11+ for TOML parsing; the Core and
hook adapter continue to support Python 3.9+.
See the [Kimi](adapters/kimi-code/README.md) and
[Claude](adapters/claude/README.md) adapter guides.
See [harness capabilities and evidence levels](docs/guides/harness-capabilities.md)
for the per-host scope and execution-level acceptance criteria.

## Repository layout

```
agent-guard/
├── skills/delete-guard/   # agent-facing skill: SKILL.md + CLI scripts
├── skills/exfil-guard/    # egress skill: check_span.py · sanitize.py
├── skills/recovery-audit/ # evidence-led repository audit and recovery
├── core/                  # classifier · policy · recovery · audit · redaction
├── doctor.py              # local configuration, probes, and drift preflight
├── live_sentinel.py       # opt-in real-host sentinel and alarm evidence
├── guard_lab.py           # user-controlled offline synthetic honeytoken lab
├── adapters/INTEGRATION.md # checklist for an unlisted host
├── adapters/claude/       # Claude Code PreToolUse hook adapter
├── adapters/kimi-code/   # Kimi Code PreToolUse hook adapter
├── adapters/dsh/          # DeepSeek Harness integration bridge
├── adapters/codex/harness/# CLI acceptance driver; not a native hook
├── tests/                 # unittest suites incl. cross-harness conformance
└── docs/                  # guides · design · Lab · reports · releases · history
```

Skills guide agent behavior; constraints live in Core. Future
`git-guard`, `database-guard`, `cloud-guard` skills plug into the same
compensation engine without restructuring.

## Documentation

The [documentation index](docs/README.md) lists every document by purpose.
Historical reports retain their tested versions and evidence limits.

| Read | For |
|---|---|
| [CONTRIBUTING.md](CONTRIBUTING.md) | how to propose an Issue and submit a focused Pull Request |
| [Guides](docs/README.md#guides) | current harness capabilities, host drift and maintainer guidance |
| [Design and contracts](docs/README.md#design) | architecture, threat model and compatibility |
| [Lab](docs/README.md#lab) | experiment guide, task templates and candidate attack references |
| [Test reports](docs/README.md#reports) | versioned Core, CLI, hook and Lab evidence |
| [Release notes](docs/README.md#releases) | stable and prerelease scopes |
| [Historical design and findings](docs/README.md#history) | early proposals, friction log and incident exploration |
| [adapters/INTEGRATION.md](adapters/INTEGRATION.md) | self-adaptation checklist for an unlisted host |
| [skills/recovery-audit/SKILL.md](skills/recovery-audit/SKILL.md) | evidence hierarchy, deterministic replay, recovery and landing gates |
| [skills/delete-guard/references/policy.md](skills/delete-guard/references/policy.md) | full rule table and decision codes |
| [skills/exfil-guard/references/rules.md](skills/exfil-guard/references/rules.md) | exfil rule table, reason codes, exemption format, audit shape |
| [skills/exfil-guard/references/channels.md](skills/exfil-guard/references/channels.md) | egress-channel taxonomy and the unreachable channels |

## Status & roadmap

**v0.2.4** adds focused recovery maintenance and the trash query
to 0.2.3. It refuses hard-reset collisions with untracked/ignored content,
checks restore sources and destinations, and preserves an occupied destination
before a forced restore. The query supplies a shared CLI/agent/future UI
contract. Existing text redaction, safe config inspection, offline Lab,
Claude/Kimi bridges, doctor, host-drift checks and live sentinel remain available.
The DSH bridge supports the reviewed `0.1.5-rc.1` and `0.2.0-rc.2` contracts;
its optional text-read protection remains off by default.

The [0.2.4 notes](docs/releases/release-notes-0.2.4.md) describe this release's scope.
The [0.2.3](docs/releases/release-notes-0.2.3.md),
[rc2](docs/releases/release-notes-0.2.3-rc2.md),
[rc1](docs/releases/release-notes-0.2.3-rc1.md), and
[0.2.2](docs/releases/release-notes-0.2.2.md) notes retain their historical scope.
Check [GitHub Releases](https://github.com/mokuyoaxis/agent-guard/releases)
and npm for published artifacts.

The Core and Decision Protocol are shared across harnesses. DSH, Claude and
Kimi adapters, together with Codex/WorkBuddy/ZCode acceptance records, are documented in
one compatibility matrix.
The Kimi result includes sampled hook-compensation and execution-level BLOCK
paths, not proof that its host enforces every BLOCK or that arbitrary agent
actions are protected. A tested direct-Python hook startup failure was
fail-open. The optional shell bridge blocked a sampled Python startup failure
in a real Kimi host session, but only when the bridge itself ran; config
validation alone cannot prove that the hook is live.
The release adds a focused real-Windows Core gate for Issue #7, but native
cmd/PowerShell execution through every harness and general concurrent-subagent
safety remain explicit gaps. Later Guard branches (`git-guard`, `database-guard`,
`cloud-guard`) reuse the same protocol and compensation engine.

## guard-lab

The opt-in, offline honeytoken Lab uses disposable
projects and synthetic, non-secret markers. Its `clean`, `mock-positive`,
`mock-injection`, and `snapshot-positive` controls call no model and make no
external request. A bounded loopback observer records the fake Lab stub, bait
URL, or a validated synthetic snapshot; an explicit post-run scan can detect a
canary in selected output without retaining the raw value.

The user keeps the true controller and evidence outside the fixture shown to
the tested agent. Broken controls or changed evidence produce `INCONCLUSIVE`,
never an optimistic pass. The Lab cannot observe ordinary file
reads, prove remote transfer, universally block LLM calls, or resist a same-UID
adversary. Start with the zero-token controls and read the
[guard-lab guide](docs/lab/guard-lab.md) before involving a real harness.
The built-in cases report `CALIBRATION_ONLY`. A separate manual
`injection-probe` treats bait contact as `EXPOSURE_OBSERVED`, and `compare`
accepts a mitigation result only after an effective unguarded baseline and a
matched guarded trial with the same protocol, harness/version/model, group,
and task hash. Manual real-model runs also require a user-verified completed
host result, so an exit-0 startup/model failure cannot count as a quiet win.
A quiet baseline stays `INCONCLUSIVE`; no result is a general model or vendor
safety rating.

## Contributing

Bug reports, design proposals, compatibility evidence, documentation fixes,
and focused code changes are welcome. Please start with an
[Issue](https://github.com/mokuyoaxis/agent-guard/issues) for non-trivial or
security-boundary changes, and submit implementations as a focused
[Pull Request](https://github.com/mokuyoaxis/agent-guard/pulls). Read the
[contribution guide](CONTRIBUTING.md) before sharing logs or test evidence;
credentials, private configuration, and unredacted incident data must not be
posted publicly.

## Community

[![LINUX DO community link](assets/linux-do-community.svg)](https://linux.do/t/topic/2942799)

This project-made banner links to our
[LINUX DO project post](https://linux.do/t/topic/2942799). It does not imply
official endorsement by the community.

## License

MIT — see [LICENSE](LICENSE).
