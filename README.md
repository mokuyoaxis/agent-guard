[![CI](https://github.com/mokuyoaxis/agent-guard/actions/workflows/ci.yml/badge.svg)](https://github.com/mokuyoaxis/agent-guard/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/mokuyoaxis/agent-guard)](https://github.com/mokuyoaxis/agent-guard/releases)
[![License](https://img.shields.io/github/license/mokuyoaxis/agent-guard)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Node.js 20 smoke](https://img.shields.io/badge/Node.js-20%20smoke-339933?logo=nodedotjs&logoColor=white)](.github/workflows/ci.yml)

[![Codex tested](https://img.shields.io/badge/Codex-gpt--5.6--sol%20medium%20%2B%20high-000000?logo=openai&logoColor=white)](docs/test-report-codex-gpt-5.6-sol.md)
[![DSH live-tested](https://img.shields.io/badge/DSH-v0.1.1%20DeepSeek%20V4%20Pro%20high%20minimal-4D6BFE)](docs/test-report-dsh-v0.1.1.md)
[![ZCode live-tested](https://img.shields.io/badge/ZCode-GLM--Flash%20win32%20live--tested-7C5CE0)](docs/test-report-zcode-glm-flash.md)
[![Claude Code live-tested](https://img.shields.io/badge/Claude%20Code-2.1.270%20hook%20live--tested%20mock%20model-D97757?logo=anthropic&logoColor=white)](docs/test-report-claude-code-harness.md)
[![DSH v0.1.0 history](https://img.shields.io/badge/DSH-v0.1.0%20friction%20log-8B8B8B)](docs/friction.md)

# agent-guard

**Make destructive agent actions reversible by default.**
**[简体中文](README.zh-CN.md)**

Agents increasingly run shell commands autonomously. When the command is
`rm -rf`, a wrong variable or one misjudged context switch is all it takes
to lose a repository — or worse. agent-guard makes destruction *reversible
by default* and records durable intent before supported mutations, across any
harness that can run Python.

> **Agent Guard is not an approval system. It is an automatic recovery
> system with human escalation.** The agent works uninterrupted while
> operations stay reversible; only when the guard cannot safely automate —
> but user intent may be legitimate — does a decision escalate to a human.
>
> It is reliability infrastructure, **not a security sandbox**: it defends
> against mistakes, not against a malicious agent with equal OS privileges.

## The four pillars

| Pillar | Guarantee |
|---|---|
| **Scope** | Workspace boundary, `.git`, and outside paths are never deletable |
| **Recoverability** | Deletions relocate to `.agent-trash/` with a manifest; git overwrites snapshot first |
| **Authorization** | Session-scoped capability; a veto downgrades one-way, only humans restore |
| **Auditability** | Enforced verdicts, compensation intents, outcomes, and restores use append-only JSONL; mutation fails closed if its intent cannot be stored |

A rule runs through all four: **uncertainty increases restriction.**

## How it decides

The stable interface is not allow/block — it is a Decision Protocol:

```
Effect → Classifier → Policy → Decision   ∈ { ALLOW, SANITIZE, RELOCATE,
                                            SNAPSHOT, ASK, BLOCK }
                                + ReasonCode   (stable, machine-readable)
                                + Explanation  (human-facing)
                                + RecoveryPlan (txids, strategy)
```

| Tier | Decisions | What the agent experiences |
|---|---|---|
| **SAFE** | `ALLOW` · `SANITIZE` · `RELOCATE` · `SNAPSHOT` | Runs silently; compensation applied first; restorable via txid. `SANITIZE` rewrites a *payload* (not a command) and returns a redaction plan |
| **AMBIGUOUS** | `ASK` | Single-execution authorization (`ASK_ONCE`) — e.g. compound shapes the guard cannot safely automate |
| **FORBIDDEN** | `BLOCK` | Refused with reason and remediation; never askable |

Precedence when several decisions meet in one operation, weakest to
strongest:

```
ALLOW < SANITIZE < RELOCATE < SNAPSHOT < ASK < BLOCK
```

`SANITIZE` ranks *below* `ASK` deliberately: it is automatic (SAFE tier),
while `ASK` forfeits automation. A payload carrying both a sanitizable
secret and a shape that cannot be rewritten must `ASK` — you cannot
silently proceed when part of the emission is uninspectable.

True effect-uncertainty (`$VAR` targets, `bash -c`, `find -delete`,
stdin-fed lists) stays on the BLOCK path: allowing it would forfeit the
core guarantee. Adapters map decisions onto their harness natively — DSH
`PreToolDecision`, Claude Code PreToolUse `ask`, or a deny carrying the
explanation where no ask exists.

## Two guard branches

`delete-guard` answers *"if this destroys something, can we come back?"*.
`exfil-guard` answers *"if this leaves the machine, was it supposed to?"* -
the same Decision Protocol, mirrored: where deletion compensates and
proceeds, disclosure redacts and emits, and there is nothing to recover
afterwards. `delete-guard` guards *before a delete*; `exfil-guard` guards
*before an emission*.

## exfil-guard

**What it is.** A pre-emission filter for the text an agent is about to
write, send, commit or push. It keeps two things from leaving the machine by
accident: **known credentials**, and **host-identifying absolute paths**.
It is a redaction guard, not a compensation engine - there is nothing to
recover after an emission, which is why it is built around *prevention plus a
decision trail* rather than undo.

It is **not a security sandbox** and does not stop adversarial exfiltration.
It defends against mistakes, not against a malicious agent with equal OS
privileges.

### Decisions exfil-guard can return

The full Decision Protocol applies, but only four classes are reachable for
a text payload (`RELOCATE`/`SNAPSHOT` belong to delete-guard - the guard
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
`secret/stripe-key` (live keys only - `sk_test_` is exempt), `secret/jwt`
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
/proc/self/environ`). It **never reads the value** - that invariant is what
keeps the guard's own output, logs and audit lines leak-free.

**Host-identifying paths** (`path/*`). `path/workspace-relative` is `ALLOW`
(the workspace is exempt); `path/system` (`/usr`, `/etc`, `C:\Windows`) is
`ALLOW`; `path/host-absolute` (under `HOME`/`TEMP`, a CI root, or a
workspace ancestor) is `SANITIZE`; `path/generic-absolute` (no host
correlation) is `ASK`; `path/device` (UNC, `\\?\`, pipes) is `SANITIZE`.

### Channel decide the disposition

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

`check_span.py` reads the payload on **stdin** and is a pure function - it
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
and placeholders only - **never the matched bytes**), and `--path` to enable
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
  program, and the human clipboard get **no verdict at all** - no coverage is
  claimed there. See `references/channels.md` and
  [docs/secret-guard-analysis.md](docs/secret-guard-analysis.md) §2.4 for
  the reachability table this claim is traceable to.
- **Not a file scanner.** It is not a gitleaks replacement; it scans what the
  guard can see *on the way out*.
- **No history rewriting.** Detecting a secret already in git history is a
  report at most. Rewriting history is a human action with its own risks.
- **No T3 entropy detector in this release.** It is the single largest
  false-positive source, and the named scenarios do not require it.

## Quickstart

Zero third-party dependencies. Requirements: Python 3.9+, POSIX shell,
git.

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

Harness adapter - intercept before executing any shell command:

```bash
python3 skills/delete-guard/scripts/check.py --enforce -- "$COMMAND"
case $? in 0) run "$COMMAND" ;; 2) refuse ;; 3) ask-the-human ;; esac
```

## What gets protected

```text
rm -rf build/            → RELOCATE  (tree quarantined, command proceeds)
rm -rf .                 → BLOCK     (workspace root)
rm -rf $DIR/             → BLOCK     (unresolvable target: fail closed)
rm *.log                 → BLOCK     (opaque glob; safe_delete expands it)
cd X && rm -rf build     → ASK_ONCE  (COMPOUND_CWD_DELETE)
touch f && rm f          → ASK_ONCE  (COMPOUND_CREATE_DELETE)
git clean -fd            → RELOCATE  (enumerate via -n, relocate, proceed)
git reset --hard         → SNAPSHOT  (stash first, apply to recover)
git push --force         → BLOCK     (remote history is never automated)
node_modules/ (ignored)  → ALLOW     (provably regenerable)
quarantine full          → BLOCK     (never fall back to permanent delete)
```

## Adapters

| Harness | Status | Mechanism |
|---|---|---|
| **DSH** (DeepSeek Harness) | **published plugin**; live-tested at `v0.1.1` (DeepSeek V4 Pro high, minimal mode) | `dsh plugin --profile <p> add github:mokuyoaxis/agent-guard` — waterfall interception + tools + prompt section |
| **Codex** | reviewed (`gpt-5.6-sol`, high); forward-tested at medium, then medium + high | `delete-guard` skill + CLI under `workspace-write`; preflight supports a pre-ignored `.agent-trash/` when `.git` is read-only |
| **Claude Code** | ready (`adapters/claude/`) | PreToolUse hook → `permissionDecision` allow/ask/deny |
| OpenCode / MCP | planned | once conformance has proven out twice |

Cross-harness guarantee, enforced by `tests/test_conformance.py`:
identical command + cwd + workspace state must produce identical core
decision + reason code through any adapter.

## Repository layout

```
agent-guard/
├── skills/delete-guard/   # agent-facing skill: SKILL.md + CLI scripts
├── skills/exfil-guard/    # egress skill: check_span.py · sanitize.py
├── core/                  # classifier · policy · recovery · audit · redaction
├── adapters/claude/       # Claude Code PreToolUse hook adapter
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
| [docs/test-report-codex-gpt-5.6-sol.md](docs/test-report-codex-gpt-5.6-sol.md) | v0.1.1 Codex evaluation (medium + high) |
| [docs/test-report-dsh-v0.1.1.md](docs/test-report-dsh-v0.1.1.md) | v0.1.1 DSH live test (DeepSeek V4 Pro high, minimal mode) |
| [skills/delete-guard/references/policy.md](skills/delete-guard/references/policy.md) | full rule table and decision codes |
| [skills/exfil-guard/references/rules.md](skills/exfil-guard/references/rules.md) | exfil rule table, reason codes, exemption format, audit shape |
| [skills/exfil-guard/references/channels.md](skills/exfil-guard/references/channels.md) | egress-channel taxonomy and the unreachable channels |

## Status & roadmap

`v0.1.1` is the reliability-hardening release after live DSH `v0.1.0`
usage, an initial fresh Codex forward test at `gpt-5.6-sol` medium, a high
reasoning review, a second paired forward test at medium and high, and a live
DSH minimal-mode run with DeepSeek V4 Pro `high` reasoning. It adds
write-ahead relocation intents, Git-quoted path safety, fail-closed Git
compensation, clean audit preflight under read-only `.git`, explicit
`RESTORABLE` / `RESTORED` lifecycle state, and a DSH runtime smoke test.
The patch version is deliberate: the model/harness validation matrix still
covers only two model families across two harnesses. Next:
additional Codex/Claude/DSH models and reasoning levels,
Windows shell dialects (Phases 1-2 landed: cmd/PowerShell lexing and
effect mapping in `core/dialects.py`, unambiguous PowerShell parameter
prefixes, and dialect selection wired through `check.py --dialect`,
`AGENT_GUARD_DIALECT` and both adapters - POSIX behaviour and the default
path unchanged; real-Windows end-to-end validation still needs a Windows
host), then `database-guard` / `cloud-guard` on
the same compensation engine. `v0.2.0` adds the second guard branch:
**`exfil-guard`** - the `SANITIZE` decision class, the text-span classifier
(`core/redaction.py`), and a standalone `check_span.py` / `sanitize.py` CLI
that needs no adapter change. It is a **minor** release by the compatibility
contract (new decision class, new reason codes, README announcement).

## License

MIT — see [LICENSE](LICENSE).
