# AGENT-GUARD

[![CI](https://github.com/mokuyoaxis/agent-guard/actions/workflows/ci.yml/badge.svg)](https://github.com/mokuyoaxis/agent-guard/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/mokuyoaxis/agent-guard)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Source preview tags](https://img.shields.io/badge/Source-preview%20tags-5B6B7A)](https://github.com/mokuyoaxis/agent-guard/tags)

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
git reset --hard    → SNAPSHOT   # snapshot first when Git state supports it
git push --force    → BLOCK      # remote history is not automated
```

These are illustrative verdicts for supported inputs, not commands to run or
proof that every harness intercepts them. Ignored, regenerable targets may be
`ALLOW`; a Git snapshot that cannot be made fails closed. When recovery or safe
rewriting is possible, the agent can keep working. Otherwise, the guard asks
the human or blocks the operation.

## Quick start with your coding agent

Keep a stable local checkout of this repository (skip the clone if you already
have one):

```sh
git clone https://github.com/mokuyoaxis/agent-guard.git
cd agent-guard
```

Python 3.9+ and Git are required for the Core; native interception depends on
the host's hook support. Then give your coding agent the following setup prompt
(replace the path with your checkout):

```text
Set up agent-guard from /absolute/path/to/agent-guard for this workspace.
First identify the current harness and its actual hook/skill capabilities;
read this README and the matching adapter README. Check Python and Git.
Install the relevant Skills, then configure a native shell hook only if this
harness supports one. Preserve existing settings and show me the proposed
diff before editing user-wide configuration or installing dependencies.
For Claude Code use adapters/claude/README.md; for Kimi Code use
adapters/kimi-code/README.md; for DSH use adapters/dsh/README.md.
For Codex or a host without a verified hook, set up Skill/CLI use and say
plainly that automatic interception is not enabled.
Verify a harmless command and pass a BLOCK-shaped command only as data to
check.py; never execute a destructive test command. Report what was actually
installed, what the host intercepted, and any unverified paths.
```

For manual setup and evidence limits, see the
[adapter matrix](docs/harness-capabilities.md) and the adapter README for your
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
**when the payload owner calls its CLI**. It is designed to catch two accidental
disclosure classes: **known credentials** and **host-identifying absolute
paths**. Depending on the channel, it can allow the payload, return a redaction
plan, ask for a human decision, or block the emission.

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

**T1 vendor credential patterns** (`secret/*`, deterministic, near-zero
false positives). Rule ids: `secret/openai-key`, `secret/github-token`,
`secret/aws-access-key-id`, `secret/gitlab-token`, `secret/slack-token`,
`secret/stripe-key` (live keys only — `sk_test_` is exempt), `secret/jwt`
(structural: the header must base64-decode to JSON containing `alg`), and
`secret/private-key-block` (whole `-----BEGIN ... PRIVATE KEY-----` block,
redacted in one piece). See
[skills/exfil-guard/references/rules.md](skills/exfil-guard/references/rules.md)
for the frozen table.

**Value-free secret references** (`secret/source-reference`). The guard
classifies an environment variable's *name* (`*KEY*`, `*TOKEN*`,
`*SECRET*`, `*PASSWORD*`, `*CRED*`, `*AUTH*`) and a secret-store *file name*
(`.env`, `*.pem`, `id_rsa*`, `.netrc`, `kubeconfig`, ...), and detects
whole-environment expansions (`printenv`, `env | ...`, `cat
/proc/self/environ`). It **never reads the value** — that invariant is what
keeps the guard's own output, logs and audit lines leak-free.

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
  [docs/secret-guard-analysis.md](docs/secret-guard-analysis.md) §2.4 for
  the reachability table this claim is traceable to.
- **Not a file scanner.** It is not a gitleaks replacement; it scans what the
  guard can see *on the way out*.
- **No history rewriting.** Detecting a secret already in Git history is a
  report at most. Rewriting history is a human action with its own risks.
- **No T3 entropy detector in this release.** It is the single largest
  false-positive source, and the named scenarios do not require it.

## Manual, harness-neutral usage

The Core has zero third-party dependencies. Requirements: Python 3.9+, POSIX
shell, and Git.

```bash
# delete something - it is quarantined, not destroyed:
python3 skills/delete-guard/scripts/safe_delete.py build/ --reason "stale"

# inspect and undo:
python3 skills/delete-guard/scripts/status.py
python3 skills/delete-guard/scripts/restore.py list
python3 skills/delete-guard/scripts/restore.py <txid>

# quarantine maintenance (dry plan by default):
python3 skills/delete-guard/scripts/gc.py
```

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
stated conditions; see the [capability matrix](docs/harness-capabilities.md)
for what each host has actually demonstrated.

```text
rm -rf build/            → RELOCATE  (tree quarantined, command proceeds)
rm -rf .                 → BLOCK     (workspace root)
rm -rf $DIR/             → BLOCK     (unresolvable target: fail closed)
rm *.log                 → BLOCK     (opaque glob; safe_delete expands it)
cd X && rm -rf build     → ASK_ONCE  (COMPOUND_CWD_DELETE)
touch f && rm f          → ASK_ONCE  (COMPOUND_CREATE_DELETE)
git clean -fd            → RELOCATE  (enumerate via -n, relocate, proceed)
git reset --hard         → SNAPSHOT  (when a Git snapshot can be made)
git push --force         → BLOCK     (remote history is never automated)
node_modules/ (ignored)  → ALLOW     (provably regenerable)
quarantine full          → BLOCK     (never fall back to permanent delete)
```

## Integration and validation matrix

[![Node.js 20 smoke](https://img.shields.io/badge/Node.js-20%20smoke-339933?logo=nodedotjs&logoColor=white)](.github/workflows/ci.yml)
[![Codex Skill/CLI tested](https://img.shields.io/badge/Codex-Skill%2FCLI%20tested-000000?logo=openai&logoColor=white)](docs/test-report-codex-gpt-6-astra-high.md)
[![DSH v0.1.1 live-tested](https://img.shields.io/badge/DSH-v0.1.1%20live--tested-4D6BFE)](docs/test-report-dsh-v0.1.1.md)
[![ZCode win32 CLI evaluated](https://img.shields.io/badge/ZCode-win32%20CLI%20evaluated-7C5CE0)](docs/test-report-zcode-glm-flash.md)
[![Claude Code hook tested with scripted model](https://img.shields.io/badge/Claude%20Code-hook%20tested%20%28scripted%20model%29-D97757?logo=anthropic&logoColor=white)](docs/test-report-claude-code-harness.md)
[![Kimi Code K3 hook observed](https://img.shields.io/badge/Kimi%20Code-K3%20hook%20observed-5B9BD5)](docs/harness-capabilities.md)

"The Core works", "a Skill-guided agent used it", and "the harness
intercepts every matching tool call" are separate claims. This table keeps
those evidence levels explicit:

| Harness | Integration level | Evidence and limit |
|---|---|---|
| **Claude Code** | Native `PreToolUse` adapter | Real CLI and hook path live-tested with a mock model endpoint; maps allow/ask/deny |
| **DSH** (DeepSeek Harness) | Native adapter | Live-tested at `v0.1.1`; waterfall interception, model tools, and prompt guidance. Install: `dsh plugin --profile <p> add github:mokuyoaxis/agent-guard` |
| **Codex** | Skill + production CLI acceptance | Reviewed and forward-tested; no native interception hook is claimed by this repository |
| **ZCode** | Skill/CLI evaluation on Windows | GLM-Flash live-tested on win32; this is evidence for the portable path, not a universal hook claim |
| **Kimi Code 0.42.0** | Native `PreToolUse` adapter (Bash) | Two isolated `local/kimi-k3` runs observed recoverable root, single-subagent, and two-subagent calls reaching the hook; Core ASK is refused, not prompted. Harness enforcement of a BLOCK verdict remains unproven. |
| OpenCode / MCP | Planned | No support claim yet |

`tests/test_conformance.py` checks the shared Core and Claude adapter; DSH
has a smoke test and Kimi has targeted adapter tests. The Kimi observations
above cover only the tested calls, not every shell construct or a general
concurrent-agent safety guarantee.
See [harness capabilities and evidence levels](docs/harness-capabilities.md)
for the per-host scope and execution-level acceptance criteria.

## Repository layout

```
agent-guard/
├── skills/delete-guard/   # agent-facing skill: SKILL.md + CLI scripts
├── skills/exfil-guard/    # egress skill: check_span.py · sanitize.py
├── skills/recovery-audit/ # evidence-led repository audit and recovery
├── core/                  # classifier · policy · recovery · audit · redaction
├── adapters/claude/       # Claude Code PreToolUse hook adapter
├── adapters/kimi-code/   # Kimi Code PreToolUse hook adapter
├── adapters/dsh/          # DeepSeek Harness integration bridge
├── adapters/codex/harness/# CLI acceptance driver; not a native hook
├── tests/                 # unittest suites incl. cross-harness conformance
└── docs/                  # architecture · threat-model · friction log
```

Skills guide agent behavior; constraints live in Core. Future
`git-guard`, `database-guard`, `cloud-guard` skills plug into the same
compensation engine without restructuring.

## Documentation

| Read | For |
|---|---|
| [docs/architecture.md](docs/architecture.md) | pillars ↔ components, data flow, design decisions |
| [docs/threat-model.md](docs/threat-model.md) | honest limits: what this is and is not |
| [docs/friction.md](docs/friction.md) | what real agents taught us (F1–F11) |
| [docs/development-note-unguarded-deletion.md](docs/development-note-unguarded-deletion.md) | de-identified incident exploration and the recovery-aware direction |
| [docs/test-report-codex-gpt-5.6-sol.md](docs/test-report-codex-gpt-5.6-sol.md) | v0.1.1 Codex evaluation (medium + high) |
| [docs/test-report-dsh-v0.1.1.md](docs/test-report-dsh-v0.1.1.md) | v0.1.1 DSH live test (DeepSeek V4 Pro high, minimal mode) |
| [skills/recovery-audit/SKILL.md](skills/recovery-audit/SKILL.md) | evidence hierarchy, deterministic replay, recovery and landing gates |
| [skills/delete-guard/references/policy.md](skills/delete-guard/references/policy.md) | full rule table and decision codes |
| [skills/exfil-guard/references/rules.md](skills/exfil-guard/references/rules.md) | exfil rule table, reason codes, exemption format, audit shape |
| [skills/exfil-guard/references/channels.md](skills/exfil-guard/references/channels.md) | egress-channel taxonomy and the unreachable channels |

## Status & roadmap

The current development line is **v0.2.0-rc2**. It keeps the hardened
v0.1.1 recovery path (write-ahead relocation intent, Git snapshot safety,
clean audit preflight, and explicit `RESTORABLE` / `RESTORED` lifecycle),
then adds cmd/PowerShell dialect parsing, the `SANITIZE` decision class,
`exfil-guard`, and the evidence-led `recovery-audit` Skill.

**v0.2.0-rc2** is a documentation and presentation revision of `v0.2.0-rc1`:
the decision protocol, the Core, and the adapter contracts are unchanged.

The release identity is harness-neutral. Existing DSH and Claude adapters,
Codex/ZCode acceptance evidence, and the bounded Kimi exercise are entries
in a growing compatibility matrix, not separate definitions of the product.
The Kimi result is a witnessed hook-compensation path, not proof that its
host enforces every BLOCK or that arbitrary agent actions are protected.
Real Windows end-to-end coverage and general concurrent-subagent safety
also remain explicit gaps. Later Guard branches (`git-guard`, `database-guard`,
`cloud-guard`) reuse the same protocol and compensation engine.

## 0.2.x preview: guard-lab

Planned, **not included in 0.2.0-rc2**: an opt-in, offline honeytoken lab
using disposable projects and synthetic, non-secret markers. It will compare
normal tasks with prompt-injection attempts, and separately observe whether a
harness indexes or exports files without an agent-requested read. Positive and
negative controls, an independent observer, and evidence labels will keep a
mere read request distinct from a confirmed external transfer. No real
credentials or automatic background monitoring are part of the plan. This is
a bounded diagnostic experiment, not a guarantee against a malicious model
or harness.

## Community

[![LINUX DO community link](assets/linux-do-community.svg)](https://linux.do/t/topic/2942799)

This project-made banner links to our
[LINUX DO project post](https://linux.do/t/topic/2942799). It does not imply
official endorsement by the community.

## License

MIT — see [LICENSE](LICENSE).
