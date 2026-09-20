# exfil-guard (secret + path leakage) — requirement analysis

Status: **analysis, pre-implementation.** No code, no schema, no verdict
table is frozen by this document. It exists to decide *whether* the
feature belongs in agent-guard, *where* it plugs into the existing
interception model, and *what the MVP is*.

Working name: **`exfil-guard`**. The two user requests (secret leakage,
local-path leakage) are two rule families, not two skills — §3 argues
this explicitly.

---

## 0. The constraint that shapes everything

agent-guard is **reliability infrastructure, not a security sandbox**
(`docs/threat-model.md`). Its own threat model says a determined agent
with equal OS privileges can always bypass it. Every claim in this
document must survive that sentence.

That immediately reclassifies both requests:

| | Security framing (rejected) | Reliability framing (accepted) |
|---|---|---|
| Secret guard | "prevent adversarial exfiltration" | "prevent the *accidental* publication/transmission of a credential the agent had legitimate read access to" |
| Path guard | "prevent information disclosure attacks" | "prevent *accidental* disclosure of host-identifying paths that produce false bug reports and privacy churn" |

The honest one-line positioning:

> exfil-guard makes accidental disclosure of credentials and host paths
> **catchable before the bytes leave**, with a decision trail. It does not
> and cannot stop an agent that is trying to leak.

Why this is still worth building: the §1 scenarios are overwhelmingly
*mistakes* (inadequate context, a debugging `echo`, a copy-paste into a PR
body), and they are the same class of mistake delete-guard already
addresses. There is no sandbox that stops an agent from `curl`ing a secret
it can read; there *is* a cheap place to catch the mistake.

### 0.1 The irrevocability axis (why this is a fourth compensation strategy)

delete-guard's design is: **destroy → compensate → let it happen.** Loss
is undone afterwards. Disclosure does not work that way — once a token is
in a public gist, on a remote ref, or in someone else's API logs, no
local compensation exists.

So exfil-guard is a **pre-commitment filter**, not a compensation engine:

```
delete-guard:  classify → relocate/snapshot → MUTATE → (recover later)
exfil-guard:   classify → redact/withhold  → EMIT    → (nothing to recover)
```

This is not a contradiction of the four pillars; it is which pillar
carries the weight:

| Pillar | delete-guard | exfil-guard |
|---|---|---|
| Scope | workspace boundary | **the egress boundary** (§2) |
| Recoverability | quarantine / snapshot | *weak* — a **RedactionRecord** (payload-evidence §4.4), not a rollback of the emission |
| Authorization | NORMAL / RESTRICTED | unchanged, plus a new capability: **secret-scan disable is host-side only** |
| Auditability | every verdict, txid | every verdict + **`rule_id`, spans/lengths, never matched bytes** |

Two consequences that are non-negotiable in the design doc:

1. **A false ALLOW is a permanent leak; a false SANITIZE is cheap.** The
   tolerance is therefore inverted relative to delete-guard. See §4.4 —
   this is the single largest open design tension and it belongs to the
   human, not to the guard's defaults.
2. **The audit log is itself an egress channel** (threat #7). Recording a
   secret in `audit.jsonl` recreates the leak in a durable file *and*
   ships it in bug reports. Precedent already exists in-repo:
   `docs/publishing-from-ephemeral-environments.md` is the reason
   `SECURITY.md` documents "credential-adjacent wording" scanner hits.

---

## 1. Threat scenario enumeration

Each scenario is scored on three axes chosen for *this* architecture:

- **Egress** — does the bytes leave the machine / become persistent?
- **Reachable** — can any of agent-guard's existing interception points see it?
- **Compensable** — if it happens, is there anything to undo?

`H`/`M`/`L` are relative within this repo's context (a dev workstation with
a git remote and an LLM API), not absolute risk scores.

### 1.1 Secret leakage

| # | Scenario | Concrete shape | Egress | Reachable | Compensable | Notes |
|---|---|---|---|---|---|---|
| S1 | Secret inlined into an outbound LLM request | agent writes `Here is my config: sk-proj-…` into the prompt | **H** | ✗ (§2.3) | ✗ | The prompt is the one channel every agent always uses |
| S2 | Secret written into a tracked config file, then pushed | `sed -i 's/KEY=.*/KEY='$KEY'/' .env.example`; `git commit -am`; `git push` | **H** | partial | partial | `push` is the last local checkpoint; `.env` is usually git-ignored, `.env.example` is not |
| S3 | Secret printed to terminal | `echo "$OPENAI_API_KEY"`, `env`, `printenv`, `cat .env` | M/H | **✓ pre-command** | ✗ | Transcripts are egress: harness logs, CI output, screen shares |
| S4 | Secret in a debug log / traceback | `logger.debug("token=%s", token)`; requests library dumping headers | M | partial | ✗ | Content is produced by a *program*, not by the command line — see B1, §2.4 |
| S5 | `.env` contents pasted into an issue / PR comment | `cat .env` then a human-shaped copy-paste, or an agent using a forge tool | **H** | ✗ | ✗ | Public-by-default once posted; forge edits keep history |
| S6 | Hardcoded secret in generated code | `client = Client("sk-live-…")` written into a new source file | **H** | **✓ pre-write** (new point) | partial (if caught before commit) |
| S7 | Secret in a git commit message | `git commit -m "fix: rotate key ghp_…"` | **H** | **✓ pre-command** | ✗ | Messages are immutable-ish (rebase rewrites history, breaks signatures) |
| S8 | Secret in an upload / bundle / artifact | `.agent-trash/` packaged by mistake, a `tar czf` of the workspace | **H** | partial | ✗ | Already named as residual risk in `docs/threat-model.md` #8 |
| S9 | Secret in a dependency lockfile / generated fixture | agent regenerates a fixture from a live capture | M | partial | partial | The realistic "long random string" false-positive factory |

**Delete-guard adjacency worth noting:** S2/S8 are the *same failure
family* as threat-model #8 (quarantine bloat → exfil via packaging). The
quarantine directory is a secret-liability volume by construction: it holds
copies of `.env` files the agent deleted. Any secret-guard scan of the
workspace must therefore treat `.agent-trash/` as a first-class source, and
`gc.py` decisions interact with it.

### 1.2 Local-path leakage

| # | Scenario | Concrete shape | Egress | Reachable | Compensable | Notes |
|---|---|---|---|---|---|---|
| P1 | Absolute path in an outbound LLM prompt | `"why does /home/alice/work/thing fail?"` | **H** | ✗ | ✗ | Same channel as S1; lowest severity, highest volume |
| P2 | Traceback with host paths pasted into an issue | `File "/Users/alice/.venv/lib/…"` | **H** | partial | ✗ | Public bug reports; also a **false-report generator** (paths are useless to maintainers) |
| P3 | Log file written with absolute paths | app logs `cwd=/root/agent-ws/ab12/` | M | pre-write (new point) | ✓ (file is local) | Highest *compensability* of all scenarios |
| P4 | Absolute path in a commit message | `git commit -m "fix /home/alice/… path"` | **H** | **✓ pre-command** | ✗ | Same interception as S7 |
| P5 | Packaged traceback / artifact containing host paths | CI bundle, issue attachment, error report | **H** | partial | ✗ | |

**Severity honesty:** path leakage is *low* severity for a typical
developer workstation (the username is often already public) and *high*
severity in three specific contexts: (a) CI/CD runner paths embedded in
published artifacts — this repository's own test reports and failure
outputs are exactly this shape; (b) shared/agent-provisioned machines
where a path leaks a codename or a tenant id; (c) sandboxes whose paths
encode a session id or a customer id. The design should therefore default
to **helpful** for path rules and **strict** for the narrow set of secret
rules, rather than treating them symmetrically (§4.4).

### 1.3 Scenarios deliberately excluded from this analysis

- Malicious exfiltration (the agent obfuscates a secret to evade the
  scanner). Excluded by the §0 positioning.
- Secrets the agent never had access to (that is sandboxing).
- Scanning for secrets *at rest* across an entire machine — this is a
  file scanner product (gitleaks/trufflehog territory), not an
  interception layer.
- Post-hoc git-history scrubbing (BFG/`filter-repo` workflow). Useful, but
  it is a *remediation* tool, not a guard, and it belongs to a hypothetical
  `git-guard` (V2) rather than here.

---

## 2. Attack-surface analysis: where does agent-guard actually get to intervene?

### 2.1 The existing interception model, restated

From `docs/architecture.md`, agent-guard has exactly **one** enforcement
point today:

```
harness hook ──prefilter──▶ check.py --enforce -- <command> ──▶ PROCEED | refuse
                                  ▲
                                  └── the *whole* enforcement surface of V1
```

Everything else (model tools `safe_delete`/`restore`/`status`, the prompt
section) is a *nudge*, not enforcement. The architecture doc is explicit:
"Interception without prompting causes friction; prompting without
interception is advisory only."

So the question "where do we intercept exfiltration?" has an uncomfortable
first answer: **the egress surface is much wider than the shell-command
surface, and most of it is invisible to us.**

### 2.2 Class A — reusable on the existing pre-command hook

These are *shell-command* scenarios. They need no new interception point;
they need new rules in the same classifier→policy pipeline.

| Scenario | Mechanism |
|---|---|
| S3 (`echo "$API_KEY"`, `printenv`, `env`) | Detect the *shape*: a command whose effect is "write an environment variable / a file's content to stdout or a file". Redaction is impossible pre-execution — one cannot un-print — so this is ALLOW-with-warning or ASK, see §4.4 |
| S7 / P4 (secret or path inside `git commit -m …`) | The message is a literal on the command line; the classifier can see it. But the *value* may come from a variable (`-m "$MSG"`) → undeterminable, fail closed or warn |
| S2 partially (`git push` carrying a tracked file) | `check.py` already sees the push. It can enumerate what the push would send (`git diff --cached`, `git log @{u}..HEAD --name-only`) and scan those diffs before allowing the push |
| S8 (`tar czf …`, `git add -A`) | A command that packages the workspace for egress. Scannable in principle; the target set is enumerable exactly like `git clean -n` |
| Any command whose *stdout* is piped to a network tool (`curl -d @-`, `nc`, `ssh … < file`) | Detectable as a shape ("stdout feeds a network sink"), not as content |

**Key structural insight:** for Class A, the *decision* vocabulary is
already sufficient. `RELOCATE`/`SNAPSHOT` do not apply, but `ASK`,
`BLOCK`, and a **new `SANITIZE`** do — and SANITIZE is only meaningful
where the guard can rewrite the bytes (a file write, a git object, an
outbound request body), which is precisely the set of scenarios *not* in
Class A.

### 2.3 Class B — needs a new interception point

| Egress point | Can it be intercepted? | How, honestly |
|---|---|---|
| **Outbound LLM request body** (S1, P1) | *Yes, for some harnesses* | Harnesses that expose a pre-request hook, or route through a local proxy (this repo already ships `adapters/claude/harness/mock_anthropic_api.mjs` — a proxy is a **demonstrated** integration shape here). A truly hosted model call with no proxy is unreachable |
| **File write content** (S6, P3) | *Yes, at the tool layer* | A `PreToolUse`-style hook on Write/Edit tools, or a `PreWrite` hook. This is a *new adapter point*, not a new core rule engine |
| **Outbound forge API call** (S5: comment, issue body, PR description) | Forge-specific | Same adapter shape as the LLM case |
| **Outbound HTTP/DB query** (the V3+ `database-guard`/`cloud-guard` neighbours) | Out of scope here | Would share the RedactionRecord, not the rules |

### 2.4 Class C — not interceptable under the current architecture

State these plainly; they are the residual risk section, and pretending
otherwise is what turns a reliability tool into a false security claim.

1. **The agent's own tool calls.** If the harness does not route a channel
   through a hook, the guard has no bytes to inspect. This is the same
   residual risk as threat-model #1 ("agent deletes via its own script
   calling `unlink()`") — and the same upgrade path (mediating service,
   not shell interception).
2. **Content produced *inside* a program.** B1 in `docs/architecture.md`
   ("the guard analyzes the command's direct effect; it does not infer the
   internal behavior of arbitrary programs") applies verbatim. `python
   app.py` whose logger writes a token is invisible. We see the *intent to
   run a program*, never its output.
3. **The model's own latent knowledge of a path it read earlier.** Once a
   path is in context, only the outbound-request interceptor can catch it,
   and only on harnesses that have one. This is *the* dominant real-world
   path-leak channel (P1/P2) and it is mostly unguarded.
4. **The human clipboard.** If the agent prints it and a human pastes it,
   the guard's last chance was S3's terminal output — where, again, it
   cannot un-print.

### 2.5 The uncomfortable summary

```
scenario count by reachability
  Class A  (reuse pre-command hook)      ~5 of 14      ← what we can actually ship
  Class B  (new adapter point needed)    ~5 of 14      ← needs each harness to cooperate
  Class C  (unreachable by construction) ~4 of 14      ← must be documented, not promised
```

The cheap, high-yield wins are **Class B file-write/intercepted-request**
(from 0% coverage today) and **Class A commit-message/push scans**. S1 —
the scenario the user listed first — is the *hardest* one, and shipping a
`secret-guard` that claims to cover it would be the exact over-claim
`docs/threat-model.md` forbids.

---

## 3. Relationship to delete-guard

### 3.1 One skill or two? — **one skill, two rule families**

Arguments for a single `exfil-guard` (or an `exfil-guard` skill whose
`references/` splits the two families):

1. **Shared classifier output type.** Both families answer the same
   question — "does this text contain a span that must not leave?"
   Whether the span is `sk-…` or `/home/alice` changes the *rule*, not the
   *machinery* (offsets, spans, redaction, audit). Delete-guard already
   proved the shape: one `OpSpec`/`PathSpec` fact layer feeding one policy
   table.
2. **Shared decision vocabulary.** Both need the same new decision
   (`SANITIZE`) and the same audit shape. Splitting duplicates the one
   thing this repository is organized to avoid duplicating — the rule
   engine (§"Key design decisions" #1: "there is exactly one rule engine
   across every harness").
3. **They co-occur.** A commit message can contain both; a pushed file can
   contain both. Two independent interceptors would produce two verdicts
   for one emission and force the adapter to invent an ordering. The
   existing `policy.worst()` aggregator already solves this — if they are
   the same pipeline.
4. **Naming honesty.** `path-guard` next to `delete-guard` reads as "a
   guard for paths", which collides conceptually with path *deletion*.
   `exfil-guard` names the risk (disclosure), not the payload.
5. **Cost.** Each new skill pays the adapter/integration cost again (hook
   wiring, prefilter, prompt section, conformance tests). Two skills for
   one pipeline is 2× integration for ~1.1× capability.

Arguments for two skills, and why they lose:

- *Different default strictness* (secrets strict, paths lenient). This is a
  **policy-configuration** concern, not a package-boundary concern — the
  rule table supports per-family severity.
- *Independent disable* (turn off path noise, keep secret rules). Also
  configuration (`AGENT_GUARD_EXFIL_RULES=secret`).

**Decision: one skill, `exfil-guard`, with two rule families
(`secret/*`, `path/*`) addressed by the same rule ids.** If implementation
reveals that the families genuinely diverge in mechanism, the split is
mechanical later; the reverse (merging two shipped skills) is not.

### 3.2 What is reused verbatim

| Component | Reuse | Why |
|---|---|---|
| Decision Protocol (decision + ReasonCode + Explanation + payload) | **yes** | Frozen adapter surface (`docs/compatibility.md`); adapters already map it |
| `policy.worst()` aggregation + rank table | **yes**, extended | One verdict per emission must be the existing aggregate |
| `core/audit.py` append/tail/fsync/JSONL | **yes**, new record events | Same "append-only, dumb, never mutated" discipline |
| NORMAL/RESTRICTED capability model | **yes** | Unchanged semantics; RESTRICTED should *tighten* exfil rules, never loosen |
| `check.py` CLI contract (exit 0/2/3, `--json`) | **yes**, new subcommand/flag | Conformance tests (`tests/test_conformance.py`) already pin this |
| Harness prefilter pattern (regex fast path) | **yes**, new prefilter | Latency budget matters (friction F4: ~0.3 s/command) |
| `recovery.py` relocate/snapshot | **no** | Wrong axis (§0.1) — do not force it |

### 3.3 What is genuinely new

1. A **content classifier** (`SpanSpec`) — the first classifier in this repo
   whose input is text payload rather than a command or a path.
2. A new decision, **`SANITIZE`**, and its exit-code mapping.
3. **Redaction records** in audit — and the discipline that matched bytes
   are never stored (§4.4).
4. A **value-free comparator** for environment variables (T2, §4.2): the
   guard must decide "would this print a secret?" without reading the
   secret.
5. Two new adapter interception points (pre-write, pre-request) — Class B.

### 3.4 New ReasonCodes (proposed; names are a design decision, not final)

Secrets — `secret/*`:

| Code | Decision | Meaning |
|---|---|---|
| `SANITIZE_KNOWN_SECRET_PATTERN` | SANITIZE | High-confidence vendor pattern (T1 list) |
| `SANITIZE_HIGH_ENTROPY_VALUE` | SANITIZE | Entropy heuristic with a secret-ish key context (T3) |
| `SANITIZE_PRIVATE_KEY_BLOCK` | SANITIZE | `-----BEGIN … PRIVATE KEY-----` block, truncate wholly |
| `BLOCK_SECRET_IN_COMMIT_MESSAGE` | BLOCK | Credential about to enter immutable history |
| `BLOCK_SECRET_IN_PUSH_PAYLOAD` | BLOCK | Credential in the outgoing diff |
| `ASK_SECRET_PRINT` | ASK | Command *shape* would print a secret (S3); cannot redact post-hoc |
| `ASK_SECRET_UNDETERMINABLE` | ASK | Value comes from a variable/subsystem we cannot evaluate |

Paths — `path/*`:

| Code | Decision | Meaning |
|---|---|---|
| `SANITIZE_HOST_PATH` | SANITIZE | Absolute host path outside the workspace, replaced with a stable token |
| `BLOCK_HOST_PATH_IN_COMMIT_MESSAGE` | BLOCK | P4 |
| `ASK_HOST_PATH_PRINT` | ASK | Low severity; a human says yes in ~1 second |

Cross-cutting:

| Code | Decision | Meaning |
|---|---|---|
| `BLOCK_EXFIL_SCANNER_UNAVAILABLE` | BLOCK | Scan errored. **Fail closed**: an unscanned egress is not a clean egress |
| `BLOCK_EXFIL_RULE_CONFIG_INVALID` | BLOCK | Malformed user rule (same class as `BLOCK_DIALECT_UNKNOWN`) |

**Compatibility note:** adding decision classes and reason codes is a
*minor* release under `docs/compatibility.md` ("New reason code
(additive) → minor"; "New decision class → minor, announced in README").
`SANITIZE` must be announced in the README, and every adapter must state
its native mapping (ask §4.4 for harnesses without re-write capability).

### 3.5 False positives — the cross-cutting problem, stated once

Delete-guard's failure mode is *friction*: an over-eager BLOCK wastes a
human's minute. Exfil-guard's failure mode is *worse and asymmetric*:

- A **false SANITIZE that replaces a legitimate value** (a test fixture, a
  public key, a hash) silently corrupts the agent's output, and the agent
  often cannot tell. A guard that quietly mangles output erodes trust
  faster than one that refuses.
- A **false POSITIVE flood** (path rules on every absolute path in every
  traceback) makes the guard something to be disabled — the worst outcome
  for a tool whose value is being on by default.
- A **false NEGATIVE is a permanent leak** (§0.1).

Mitigations are enumerated in the design doc §5; the analysis-level
requirement is only this: **the guard must be able to say ALLOW with high
precision, and must never silently rewrite a value it is not confident
about.** Three concrete instruments follow from that, all borrowed from
existing repo precedent:

1. **A confidence gate** (single decisive pattern vs. contextual
   heuristic), mirroring the dialect layer's "abbreviations expand only
   when unambiguous" rule.
2. **A workspace-relative exemption**, mirroring the lexical-boundary
   discipline: paths inside the workspace are not host identifiers.
3. **An in-repo exemption list**, not a global one — the same problem
   `SECURITY.md` solves for scanners ("Expected scanner hits on this
   repository"). Any own-repo deployment of exfil-guard will scan
   `tests/` fixtures full of `sk-…` strings and *must* stay usable.

---

## 4. Open questions for the design doc (and for the human)

These are carried into `secret-guard-design.md` and answered there; listed
here because they are genuinely unresolved, not rhetorical.

- **Q1 Egress definition.** Which channels count as "out"? A terminal
  transcript is not the same as a public gist. The design must pick a
  channel taxonomy, because the *strictness* differs per channel and so
  does the recovery story.
- **Q2 SANITIZE on a locked channel.** If the harness cannot re-write the
  payload (no pre-write hook), does `SANITIZE` degrade to `ASK`, to
  `BLOCK`, or to a warning-only `ALLOW`? Fail-closed says BLOCK; usability
  says ASK-or-warn. This is the single most consequential decision in the
  feature.
- **Q3 Value-free detection.** How do we detect "this command prints
  `$OPENAI_API_KEY`" without ever reading that value — and what do we do
  when the variable is *undefined*, or holds a non-secret?
- **Q4 Irreversibility and human consent.** Given §0.1, is a BLOCK on a
  commit message legitimate? (delete-guard never blocks an operation that
  the *human* could trivially redo; but committing a secret is not trivial
  to redo.)
- **Q5 MVP intersection.** Which ≤3 scenarios justify the first release,
  and which must be explicitly declared NOT DONE so the README cannot be
  read as covering them?
- **Q6 Roadmap slot.** New `X-guard` branch beside `git-guard`, or does the
  shared content classifier belong to the core *before* `git-guard`?

---

## 5. Non-goals (analysis-level)

Stated here so the design doc cannot quietly expand scope:

1. **Not a sandbox.** No kernel-level enforcement, no network mediation we
   do not already have a hook for.
2. **Not a history scrubbing tool.** exfil-guard prevents; if it fails, the
   human runs `filter-repo`. A guard that tries to also be a rewriting tool
   becomes a second destruction vector — exactly the reasoning behind
   "restore is non-destructive" (`docs/architecture.md` #4).
3. **Not a full file scanner / not a gitleaks replacement.** Scope is
   *egress-relevant* content the guard can reach, not "all secrets on the
   disk".
4. **Not a DLP product.** No ML classification, no entitlement system, no
   per-destination policy server.
5. **Not a claim of coverage over Unreachable channels (§2.4).** Every doc
   must carry this limitation, in the same spirit as
   `docs/threat-model.md`'s "What would upgrade this to a security
   boundary".
