# exfil-guard — design

Status: **design, pre-implementation.** Extends the Decision Protocol with
one new decision class (`SANITIZE`) and one new classifier input type
(text spans). Read `secret-guard-analysis.md` first — §2.5 there
(reachability) and §0.1 (irrevocability) are the two facts this design is
built around.

Naming: the skill is `exfil-guard`; the rule families are `secret/*` and
`path/*`. `docs/secret-guard-architecture.md` states where it sits.

---

## 1. Core component design

### 1.1 Module layout (mirroring the existing tree)

```
core/
  classifier.py     UNCHANGED (command/path facts)          [reuse]
  dialects.py       UNCHANGED                                [reuse]
  policy.py         + decide_spans(), SANITIZE, new codes    [extend]
  audit.py          UNCHANGED API (new event kinds only)     [reuse]
  recovery.py       UNCHANGED (no compensation here)         [reuse]
  redaction.py      NEW - the Compensation-Strategy slot:
                    detect(spans) -> Allowed | Sanitized | Withheld
                             + RedactionRecord (never the bytes)

skills/
  exfil-guard/
    SKILL.md                    model-facing discipline
    agents/openai.yaml          parity with delete-guard
    references/
      rules.md                  rule table = the policy doc
      secret-patterns.md        T1/T3 pattern catalogue (with provenance)
      channels.md               egress-channel taxonomy (§4.1)
    scripts/
      _bootstrap.py             copy of the delete-guard shim (3 lines)
      check_span.py             NEW entry: scan text from stdin, NOT a
                                command line — Decision Protocol out
      sanitize.py               NEW: apply a redaction plan to text
      scan_path.py              NEW: scan a file/dir/diff (reuse of the
                                same span engine, different source)
      status.py                 reuse pattern from delete-guard
```

Two structural choices worth defending:

**(a) `redaction.py` is a new *strategy* on the existing Compensation slot,
not a new engine.** `docs/architecture.md` #3 already frames compensation
as pluggable ("Future compensations … plug into the same slot"). Redaction
is the *degraded* member of that family — it does not restore recoverable
loss, it only records what was removed. Naming it inside `recovery.py`'s
family keeps the mental model intact; giving it its own module keeps the
irreversibility explicit.

**(b) `check_span.py` is a *separate entry point*, not a flag on
`check.py`.** `check.py`'s contract is "classify a shell command line"
(its docstring, the `--` REMAINDER convention, the dialect resolution, the
compensation execution). Loading it with "or maybe a blob of text" would
break the single-responsibility that makes `tests/test_conformance.py` a
meaningful test. Span mode has a different input, a different (smaller)
decision set, and no compensation phase:

```
check.py       --enforce -- <command>      → ALLOW|RELOCATE|SNAPSHOT|ASK|BLOCK   exit 0|2|3
check_span.py  --channel <name> < text     → ALLOW|SANITIZE|ASK|BLOCK            exit 0|2|3|4
```

Both construct the *same* `Verdict` objects and both go through
`policy.worst()`, so the aggregation and adapter mapping stay shared.

### 1.2 The classifier contract (facts, not decisions)

Following `classifier.py`'s rule ("it deliberately does NOT decide"):

```python
@dataclass
class SpanSpec:
    raw: str                    # the matched substring, HELD IN MEMORY ONLY
    start: int                  # byte offset into the scanned text
    end: int
    rule_id: str                # e.g. "secret/openai-key"
    family: str                 # "secret" | "path"
    confidence: str             # "deterministic" | "contextual" | "entropy"
    context: str                # "key=…" | "prose" | "diff-added-line" | "env-var"
    channel: str                # egress channel that will carry it (§4.1)
    notes: List[str] = field(default_factory=list)
```

`raw` is the analogue of `PathSpec.raw` — the fact layer keeps the caller's
spelling, and only `audit`/`redaction` decide what may be *persisted*
(nothing). `context` and `channel` exist because §4.4 shows the right
decision is a function of both, not of the pattern alone.

### 1.3 Fact → decision table (the shape of `decide_spans`)

```
span.family, span.confidence, channel.rewritable, channel.persistence
        │
        ├─ rewritable? ──no──▶ ASK (print-like channels) | BLOCK (immutable channel)
        └─ yes
             ├─ deterministic secret           → SANITIZE
             ├─ contextual/entropy secret      → SANITIZE (if rule enabled) | ASK
             ├─ path inside workspace          → ALLOW  (not a host identifier)
             ├─ path outside workspace         → SANITIZE (path/* rules) | ASK
             └─ anything ambiguous             → ASK
```

First-match-wins, like the delete-guard rule table. `worst()` aggregates
across spans with the existing rank, extended by one:

```
ALLOW(0) < SANITIZE(1) < ASK(2) < BLOCK(3)
```

`SANITIZE` ranks below `ASK` deliberately: it is *automatic* (SAFE tier,
like RELOCATE/SNAPSHOT), while ASK forfeits automation. A line containing
both a sanitizable secret and an un-rewritable askable shape must ASK —
you cannot silently proceed when part of the emission is uninspectable.

---

## 2. Secret detection strategy

### 2.1 Three detectors, one pipeline

| Tier | Name | Character | Decision default |
|---|---|---|---|
| T1 | Known vendor patterns | deterministic, near-zero FP | SANITIZE |
| T2 | Environment-variable / file-content references | structural (value-free) | ASK |
| T3 | Generic high-entropy with secret-ish context | probabilistic | SANITIZE behind a threshold + context gate |

The tiers are **not** alternatives to be chosen between — the reviewer of
the seed idea asked "regex vs entropy vs known-pattern library", and the
correct answer for this repository is all three, layered, because they have
disjoint failure modes:

- T1 alone: misses self-hosted tokens, custom `X-Api-Key` values, rotated
  formats. Zero FP, poor recall.
- T3 alone: flags lockfile hashes, test fixtures, base64 blobs, UUIDs, and
  every `git rev-parse HEAD`. Unusable by default.
- T1+T3 without T2: cannot answer "will this command print my key?" — the
  single highest-signal scenario in Class A (S3), and the only one where a
  value-free answer is possible.

### 2.2 T1 — known patterns

Catalogue lives in `references/secret-patterns.md`, each entry with the
vendor, the format documented, and a "why this is deterministic" note.
Seed list (prefix-anchored to keep FP ≈ 0):

| Rule id | Shape | Anchor |
|---|---|---|
| `secret/openai-key` | `sk-` + 20+ url-safe chars, incl. `sk-proj-`/`sk-ant-` | prefix + length + charset |
| `secret/github-token` | `ghp_`/`gho_`/`ghu_`/`ghs_`/`ghr_` + 36, `github_pat_` + 82 | fixed-format prefix |
| `secret/aws-access-key-id` | `AKIA`/`ASIA`/`AGPA`/`AIDA` + 16 upper-alnum | `AKIA…` is globally reserved |
| `secret/gitlab-token` | `glpat-` + 20 | prefix |
| `secret/slack-token` | `xox[baprs]-` | prefix |
| `secret/stripe-key` | `sk_live_`/`rk_live_` (NOT `sk_test_` — see §5) | prefix + mode |
| `secret/jwt` | three base64url segments, header decodes to JSON with `alg` | **structural**, not just `a.b.c` |
| `secret/private-key-block` | `-----BEGIN (RSA |EC|OPENSSH|DSA|PGP)? ?PRIVATE KEY-----` … `-----END` | delimiters; redact the whole block |
| `secret/ssh-private-key-file` | the above, but detected as a *file* by name + first line | file source |

Design rules for T1:

1. **Structural validation when the format allows it.** A JWT is only a JWT
   if segment 1 base64-decodes to JSON containing `alg` — otherwise it is a
   version string (`1.2.3`) or a hostname (`api.example.com`), both of
   which occur constantly in this repo's own text.
2. **No "generic keyword" patterns in T1.** `password=` / `secret=` are
   *context*, which is T3's job. Putting them in T1 makes the whole tier
   noisy and destroys the "deterministic ⇒ auto-sanitize" property that
   the design depends on.
3. **Patterns are additive and versioned** in `secret-patterns.md`; adding
   one is a minor release (new reason code not required — rule ids are
   payload, not verdict codes).

### 2.3 T3 — entropy with a context gate

Entropy alone is a losing detector. The gate is the design:

```
candidate = token matching [A-Za-z0-9+/_\-=]{20,}
entropy   = Shannon bits/char over the token
signal    = (entropy >= H_high)                 # e.g. 4.0
            OR (entropy >= H_mid AND key-context)  # e.g. 3.0 + context
where key-context = one of, within the same line/statement:
        key|token|secret|passwd|password|pwd|auth|apikey|api_key|credential
        (case-insensitive), separated by one of = : " ' , FOLLOWED BY nothing
        that looks like a placeholder (§5)
```

Why the context gate carries the weight: the two populations overlap in
entropy. A lockfile integrity hash (`sha512-…`) and a random 32-byte token
are statistically identical. What separates them is *how they are
introduced* — and the introduction is exactly what the key-context regex
sees. This mirrors the dialect layer's rule ("abbreviations expand only
when unambiguous"): expand the heuristic only inside a context that makes
the interpretation unambiguous.

Tuning levers, all explicit and all in config (§5):

- `H_high`, `H_mid`, minimum length, and the key-context word list;
- per-rule `enabled` flags so `path/*` or `secret/entropy` can be switched
  off without touching the others.

### 2.4 T2 — value-free environment/file references (Q3 answered)

The problem: detect `echo "$OPENAI_API_KEY"` without the guard ever
learning the key.

**Answer: never read the value. Score the *reference*.**

Detector inputs, all available to a pre-command hook:

1. **The name.** A variable whose *name* matches the secret-name lexicon
   (`*KEY*`, `*TOKEN*`, `*SECRET*`, `*PASSWORD*`, `*PASSWD*`, `*CRED*`,
   `*AUTH*`, `*APIKEY*`) is treated as secret-bearing **by name
   classification only**. The guard does not `getenv` it. This is the
   property that makes the whole detector safe: the guard's output, logs,
   audit, and error messages are all value-free by construction.
2. **A reference, not a value.** `echo "$OPENAI_API_KEY"`, `printenv
   GITHUB_TOKEN`, `env | grep TOKEN`, `${AWS_SECRET_ACCESS_KEY}` inside a
   heredoc-bound string.
3. **A file read whose path is a known secret store** — `.env`, `.env.*`,
   `*.pem`, `id_rsa*`, `*.p12`, `.npmrc`, `.netrc`, `credentials`,
   `*.tfvars`, `kubeconfig`. Path *names* are not secret; this is a path
   fact the existing classifier vocabulary can already express.
4. **An expansion form, not a name** — `env`, `printenv` with no argument,
   `set`, `export -p`, `cat /proc/self/environ`. Undeterminable content
   (B1 applies: we cannot know what the environment holds). Fail-closed
   option: ASK, not BLOCK (see Q2 resolution, §4.4).

Detector output is *only* `(rule_id, span of the reference expression)` —
**never** a value. The audit record stores the variable **name**, not its
content. That single constraint keeps exfil-guard from becoming its own
leak vector (analysis threat #7).

**Anti-loop rule:** detection must never require reading the thing it
protects. State it as a hard invariant, in the same register as "restore
is non-destructive": *the guard reads names and shapes, never secret
values.*

**Undefined/non-secret variables (the honest corner):** if
`$OPENAI_API_KEY` happens to hold `"test"`, the guard still ASKs. This is
acceptable because the cost of a false ASK is one keystroke and the cost of
a false ALLOW is irreversible (§0.1) — the asymmetry decision, applied
consistently. It is *not* acceptable to resolve the variable to reduce
false ASKs, because that would violate the invariant above.

### 2.5 Detector outputs are spans, not booleans

Every detector returns offsets. Reasons:

- **Redaction needs positions** (`sanitize.py`).
- **`--json` output must not contain the match**, only its span and rule —
  otherwise the guard leaks into CI logs. Conformance tests can then assert
  the *absence* of the value in all guard output, which is a testable
  security property (see design §6 test plan).
- **Idempotence is checkable:** scanning `<REDACTED>` text must yield zero
  spans.

---

## 3. Path detection strategy

### 3.1 The workspace-relative exemption is the core rule

Direct reuse of the delete-guard insight (`docs/architecture.md` #5,
"Lexical boundaries"): *a path is only a host identifier if it is outside
the workspace.* `./src/main.py` says nothing about the machine;
`/home/alice/work/agent-guard/src/main.py` does.

```
path span
  │
  ├─ inside workspace (lexical compare, existing _physical/inside_path)?
  │       → ALLOW  (rewrite to workspace-relative only if the channel
  │                 wants it — never as a security action)
  ├─ workspace root path itself          → SANITIZE (the root is the host id)
  ├─ outside workspace                   → SANITIZE (path/host-absolute)
  ├─ under the home directory            → SANITIZE (path/home-absolute)
  ├─ system prefix (/usr,/etc,/opt,     → ALLOW by default (not host-
  │  C:\Windows, /Library, …)              identifying), configurable
  └─ unreachable/exotic (UNC, device)    → SANITIZE (conservative)
```

This inverts the naive approach: instead of enumerating every
host-identifying prefix (unbounded, unmaintainable), the guard uses the one
boundary it already computes confidently — the workspace — and treats
everything else as suspect **unless** it is a well-known system prefix.

### 3.2 Absolute-path regexes

| OS | Shape | Notes |
|---|---|---|
| POSIX | `/(?:[A-Za-z0-9._@+-]+/)*[A-Za-z0-9._@+-]*` | Require ≥2 components OR a known-root tie-in, else `/tmp` matches |
| macOS home | `/Users/<name>/…`, `/Volumes/<name>/…` | Distinctive, high-signal; `/Users/Shared` exempt |
| Linux home | `/home/<name>/…`, `/root/…` | `/root` is the container default — often *not* identifying; configurable |
| CI runners | `/runner/_work/…`, `/builds/…`, `/github/workspace/…`, `/var/lib/jenkins/…` | **This repo's own CI leaks these** — see §3.4 |
| Sandboxes | `/tmp/<random>`, `/var/folders/xx/…` (macOS `mkdtemp`), `C:\Users\<u>\AppData\Local\Temp\…` | Random segments are ephemeral, low value; the base is the signal |
| Windows | `[A-Za-z]:\\…`, UNC `\\\\host\\share\\…`, device `\\\\?\\…`, `\\.\pipe\…` | Windows home = `C:\Users\<u>\…`; UNC hostnames are identifiers |
| Windows env | `%USERPROFILE%`, `%LOCALAPPDATA%` | Interpolation → existing `has_interpolation` fact → undeterminable |

Reuse `classifier._is_windows_absolute()` and the `%VAR%`/`$env:` handling
from `dialects.py` verbatim: the Windows absolute-path semantics this
feature needs are already implemented and tested, dialect-independently
(the module comment says it explicitly — a Linux CI must still reason about
`C:/Windows`).

### 3.3 Host identity: whose name is "the host's"?

The guard must not hard-code `/home/<user>`; it must derive the set of
identifying names **at runtime**:

| Source | Portability | Trust |
|---|---|---|
| `getpass.getuser()` | already used by `audit.session_id()` | high, but empty under some container/CI setups |
| `os.environ["USER"]` / `USERNAME` / `LOGNAME` | universal, spoofable | medium |
| `expanduser("~")` basename | good on macOS/Linux | medium |
| `platform.node()`, `socket.gethostname()` | hostname identifiers (in prompts, tracebacks, UNC) | medium |
| `os.environ["HOME"]`, `USERPROFILE`, `LOCALAPPDATA`, `TEMP` | the *actual* prefixes to redact, better than any username guess | high |
| The workspace path itself | **best** — its ancestors are by definition not to be echoed raw | high |
| Harness-provided overrides | `AGENT_GUARD_HOSTNAMES`, `AGENT_GUARD_HOME_MARKERS` | explicit config |

Design rule: **derive a deny-set, not a pattern** — a set of literal
prefixes built from HOME/USERPROFILE/TEMP/workspace-ancestors plus
`getpass.getuser()`. A prefix-set match is deterministic (high confidence,
auto-SANITIZE); a *generic* absolute path with no prefix correlation is
contextual (ASK). This gives the two-tier structure §1.3 needs, and it
portably degrades: on a host where `getpass` fails, the guard still has
HOME/USERPROFILE to build a smaller but still-useful deny-set, and the
`generic-absolute-path` rule remains as a low-confidence backstop.

### 3.4 Host identity ≠ machine identity, and CI is the counter-example

This repository's own artifacts make the case: `docs/test-report-*.md` and
CI logs routinely contain
`/home/runner/work/agent-guard/agent-guard`, `/workspace`, `/tmp/agent-guard-test-*`.
Those leak the *runner image's* layout, not a person. Consequences the
design must honour:

- **Default severity for path rules is low**, and the default decision for
  a bare host path in a *non-persistent* channel is ALLOW-with-note or ASK,
  never BLOCK.
- **The workspace itself is the exemption**, and in CI the workspace *is*
  `/home/runner/work/…` — so the guard's own primary rule already covers
  the most common CI leak without a runner-specific list.
- **`.agent-trash/`** must be scanned as suspicious content (it holds
  quarantined `.env` copies — analysis S8) but its *paths* are workspace
  paths and exempt.

---

## 4. Interception points and dispositions

### 4.1 Channel taxonomy (Q1 answered)

A channel is defined by two facts, and the decision table keys off both:

| Channel | `rewritable` | `persistence` | Reached via | Candidate default |
|---|---|---|---|---|
| `llm-request` | yes (proxy/hook) | none (but third party) | pre-request hook | SANITIZE |
| `file-write` | yes | workspace | pre-write hook / tool arg | SANITIZE |
| `git-commit-message` | yes (rewrite argv) | git history | pre-command hook | BLOCK |
| `git-push-payload` | no (would need history rewrite) | **remote** | pre-command hook | BLOCK |
| `forge-comment` / `issue-body` / `pr-description` | yes (before send) | public | pre-request hook | SANITIZE |
| `shell-stdout` | **no** | transcript/log | pre-command hook | ASK |
| `shell-file-redirect` (`> out.log`) | yes (file not yet written) | local | pre-command hook | ASK (or SANITIZE on the write) |
| `archive-upload` (`tar`, `git bundle`, `gh release upload`) | yes | remote | pre-command hook | ASK |
| `process-argv` (`foo --token=…`) | yes | ps/logs | pre-command hook | ASK |

The `rewritable` column is what makes `SANITIZE` meaningful; the
`persistence` column is what justifies BLOCK. A channel that is neither
rewritable nor persistent (stdout to a local terminal) can only ASK.

### 4.2 Interception-point inventory

| # | Point | Scenarios | Mechanism | Status |
|---|---|---|---|---|
| 1 | **pre-command** (existing) | S3, S7, P4, S2-partial, S8 | new shapes in `classifier.py` + span scan of command literals | reuse + extend |
| 2 | **pre-write** (new) | S6, P3 | adapter hook on Write/Edit tool args; `check_span.py --channel file-write` | new adapter point |
| 3 | **pre-request** (new) | S1, P1, S5 | adapter hook or local proxy (precedent: `adapters/claude/harness/mock_anthropic_api.mjs`) | new adapter point |
| 4 | **pre-push / pre-commit** (new, subtle) | S2, S7 | `git diff --cached` / `git log @{u}..HEAD --name-only` scan, then `check_span.py --channel git-push-payload` | new, high value |
| 5 | **artifact/packaging** (deferred) | S8 | enumerate then scan, same shape as `RELOCATE_VIA_CLEAN_ENUMERATE` | MVP-excluded (§6) |

Point 4 deserves emphasis: it is the only place where the guard can see a
*whole outgoing payload* rather than one literal, and it is a natural
extension of the existing `git push` classification (which today only
checks for `--force`/ref deletion). "Scan what this push actually sends"
reuses `recovery.RecoveryEngine.enumerate_git_clean()`'s precedent of
dry-run enumeration before action.

### 4.3 Dispositions

| Decision | Meaning | Semantics |
|---|---|---|
| `ALLOW` | nothing matched, or matches are provably benign (workspace-relative path, placeholder) | run unchanged |
| `SANITIZE` | the guard rewrites the *payload*, not the command | **new**, SAFE tier |
| `ASK` | single-execution authorization (unchanged semantics from delete-guard) | the human decides; never a rule exemption |
| `BLOCK` | refuse | reserved for: immutable-history channels with a deterministic secret, scanner failure, invalid config |

`SANITIZE` is deliberately **not** an adapter-mapped decision in the same
way as RELOCATE/SNAPSHOT, because it does not apply to a *command*: it
applies to a *payload*, and the caller of `check_span.py` is the one holding
the payload. The contract is:

```
check_span.py --channel file-write --json < text
  → {"decision":"SANITIZE", "code":"SANITIZE_KNOWN_SECRET_PATTERN",
     "spans":[{"start":104,"end":151,"rule_id":"secret/openai-key",
               "confidence":"deterministic"}],
     "redaction_plan":[...]}          # offsets + placeholder, NO bytes
```

The payload owner then calls `sanitize.py` (or applies the plan inline) and
writes the **sanitized** text. This keeps the guard out of the payload
path, keeps check_span a pure function (testable, no I/O), and makes the
"never persist matched bytes" invariant structurally enforceable: the JSON
schema simply has no field for matched content.

### 4.4 Q2, answered: SANITIZE on a non-rewritable channel

The fail-closed instinct says BLOCK. This design says:

- **immutable/persistent channel** (`git-commit-message`, `git-push-payload`)
  → **BLOCK**. Rationale: the emission cannot be taken back, and unlike a
  delete it is not compensable, so "refuse and explain" is proportionate.
  The explanation names the remediation (amend/rewrite before push).
- **rewritable channel** → `SANITIZE`, and if the caller declines to apply
  the plan, the API contract is "you were told"; the audit records
  `sanitize-declined`.
- **non-rewritable, non-persistent channel** (stdout) → **ASK**, because
  BLOCK here would block `echo` on a variable the human has every right to
  print, and delete-guard's ASK tier is exactly "well-understood operation
  whose safe *automation* is unavailable" — a perfect description of
  "cannot un-print".
- **unreachable channel** → no verdict at all; documented (§2.4 of the
  analysis), never implied to be covered.

This resolves the tension in analysis §4.4 in the direction of the repo's
existing principle: *uncertainty increases restriction, but the guard never
pretends to control what it does not*.

### 4.5 Adapter mapping

| Core decision | Claude Code (`PreToolUse`) | DSH (`PreToolDecision`) | No-hook harness |
|---|---|---|---|
| ALLOW | exit 0 | run | run |
| SANITIZE | rewrite the tool arg, `permissionDecision: allow` + reason naming the rule (never the value) | rewrite the arg | **degrade to ASK/deny**, never silently proceed |
| ASK | `permissionDecision: ask` | native ask → deny | deny + explanation |
| BLOCK | exit 2, stderr to the model | deny + explanation | deny + explanation |

The "no re-write capability" row is the degradation rule that keeps the
feature honest: a harness that cannot rewrite must not be told it can.

---

## 5. False-positive handling

Five instruments, in the order they should be tried:

**5.1 Static placeholder allowlist (deterministic, no config).**
`YOUR_API_KEY_HERE`, `xxxxxxxx`, `<REDACTED>`, `sk-xxxx…`, `changeme`,
`dummy`, `example`, `test`, `fake`, `placeholder`, `TODO`, `*`-runs,
sequential runs, all-same-char runs, and *the literal variable name*
(`$OPENAI_API_KEY` inside docs about how to set it). Existing repo
precedent: `SECURITY.md`'s "Expected scanner hits". The allowlist is
**in-repo and versioned**, so a deployment scans this repository without
failing on its own test fixtures.

**5.2 Repo-local exemption file.** `.agent-guard/exfil-allow.toml` (or
`.agent-guardignore`, mirroring `.gitignore` semantics that this codebase
already reasons about) with:

- `rule_id` globs to disable,
- path globs exempted from scanning (e.g. `tests/fixtures/secrets/*`),
- literal-value exemptions **by hash** — never by value. A human who needs
  a specific value exempted provides `sha256:…`, and the guard compares
  hashes. This is the only way to write an exemption without writing the
  secret into a tracked file, which is itself the bug the feature exists to
  prevent.

**5.3 Confidence gating** (the primary lever). Auto-`SANITIZE` only on
`deterministic` confidence; `contextual`/`entropy` matches default to
`SANITIZE` **only** when the context gate fired, else `ASK`. Configurable
per rule.

**5.4 Entropy threshold + length floor.** Raise `H_high`/`H_mid` and the
minimum token length to trade recall for quiet. Ship the defaults that
keep this repository's own corpus clean (the acceptance corpus in §6.4 is
the tuning instrument, not vibes).

**5.5 Audit records every sanitize/block with enough to debug, and no
secret.** Record shape:

```json
{"event":"exfil-sanitize","rule_id":"secret/openai-key",
 "confidence":"deterministic","channel":"file-write",
 "family":"secret","span":[104,151],"length":47,
 "exempted":false,"plan_id":"…","ts":"…","session":"…"}
```

No `raw`, no prefix, no suffix, no hash of the secret (a hash of a
low-entropy secret is offline-crackable — do not store it). Rule id +
offsets + length is enough to reproduce a false positive from the original
payload and is worthless to an attacker.

**5.6 Noise budget as a design constraint.** State an explicit target, so
"it's too noisy" becomes measurable: on this repository's own text corpus
(README, docs/, tests/, skills/), the default rule set must produce **zero
BLOCK and a bounded number of ASK/SANITIZE**; anything above the bound is a
bug in the rule, not a tuning problem. `docs/compatibility.md`'s promise
that "default policy verdict changes for an existing shape → minor +
friction entry" applies: rule changes that alter verdicts get a
`docs/friction.md` entry.

---

## 6. MVP scope

### 6.1 In scope (ordered by yield, not by ease)

**MVP-1 — `SANITIZE` on file-write and pre-request channels with T1
patterns.** Covers S6, P3, S1/P1 on harnesses with a hook. Highest coverage
gain (0% → real), lowest FP risk (deterministic patterns only), and it
establishes the new decision class plus the span/redaction plumbing.

**MVP-2 — `check_span.py` as a standalone CLI + `sanitize.py`.** Makes the
feature usable *today* by any harness or script without adapter changes
(pipe a diff, a prompt, or a file through it). Small, self-contained,
immediately testable, and it de-risks the adapter work that follows.

**MVP-3 — commit-message and push-payload scan (Class A, point 4).**
Covers S7, P4, S2. Pure extension of existing pre-command interception; the
push scan reuses the dry-run-enumeration precedent.

**MVP-4 — T2 (value-free env reference) on the pre-command hook.** Covers
S3's highest-signal form. Small rule, no value access, ASK-only.

**MVP-5 — path rules (`path/*`) with workspace exemption, ALLOW/ASK/SANITIZE
(defaults biased to quiet).** Lowest severity, cheapest to get wrong;
ships last, and only after the noise budget (§5.6) is measured on real
corpora.

### 6.2 Explicitly NOT in MVP (scope boundary)

1. **No LLM-request interception for harnesses that lack a hook or proxy.**
   Documented as a coverage gap, with the "you may not claim" sentence from
   `threat-model.md` reused verbatim.
2. **No git-history rewriting / no `filter-repo` integration.** Detecting a
   secret already in history is a *report* at most; rewriting is a human
   action with its own destructive risk (`docs/architecture.md` #4 logic).
3. **No T3 entropy detector in the first release** unless the acceptance
   corpus (§6.4) shows it clears the noise budget. It is the single largest
   FP source and the least necessary for the named seeds.
4. **No archive/packaging enumeration** (`tar`, `git bundle`, `gh release
   upload`) — MVP-excluded; it is the natural MVP-6.
5. **No database/cloud egress point.** Those belong to V3+ skills; the
   RedactionRecord is the shared interface, not the rules.
6. **No new mode/state machine.** NORMAL/RESTRICTED is reused; the only new
   authorization surface is "rule config is host-side" (agent cannot
   disable a rule).
7. **No encrypted/entropy-proof detection, no obfuscation resistance.** §0
   positioning.
8. **No second skill.** One skill, two families (analysis §3.1).

### 6.3 Delivery constraint (inherited, non-negotiable)

- Zero third-party dependencies (repo-wide invariant; a hashing/exemption
  scheme must use `hashlib`).
- Python 3.9+ compatible (no `match`, no `X | Y` annotations at runtime
  unless `from __future__ import annotations` — already the house style).
- Latency: the prefilter must keep non-matching text at regex cost
  (friction F4's ~0.3 s/command budget applies to the new prefilter too,
  and span scanning of large payloads needs an explicit size cap +
  streaming/chunking rule).
- Every failure mode fails **closed for BLOCK-class channels** and is
  reported, never silent (`BLOCK_EXFIL_SCANNER_UNAVAILABLE`).

### 6.4 Acceptance corpus (the thing that makes "MVP done" checkable)

1. **Positive corpus:** this repository's documented examples, plus
   synthesised patterns per T1 rule, plus the S1–S8/P1–P5 scenario list
   turned into literal payloads.
2. **Negative corpus:** README, `docs/*.md`, `tests/**`, `skills/**`,
   `package.json` lockfile-shaped text, `git log` output, base64 blobs
   (images), `sha512-…` integrity strings, IPv4/IPv6, semver triplets,
   long prose. **Zero BLOCK, bounded ASK/SANITIZE** (§5.6).
3. **Leak-freedom corpus:** assert that no guard output (stdout, `--json`,
   audit line, error message, explanation, traceback) contains any matched
   substring. This is the test that keeps the guard from becoming the leak.

---

## 7. Position in the roadmap

Current roadmap (`docs/architecture.md`): V1 delete-guard → V1.x dialects →
V2 `git-guard` → V3+ `database-guard`/`cloud-guard`.

exfil-guard is a **new X-guard branch**, not a slot inside the existing
sequence, for one reason: it extends the core with a *new input type*
(text spans) and a *new decision class*, while `git-guard` is explicitly
"remote ref protection with lease semantics" — a different axis that reuses
the existing deletion machinery. Sequencing:

```
V1   delete-guard            ✅ shipped
V1.x dialects                ✅ phase 1 shipped
V1.y exfil-guard             ◀── this feature (Y = 2)
       MVP-1/2  new decision class SANITIZE + span classifier
       MVP-3/4  pre-command extensions (commit msg, push payload, env refs)
       MVP-5    path rules
V2   git-guard               (independent; can proceed in parallel)
V3+  database-guard / cloud-guard
```

Justification for placing it **before** `git-guard`, despite `git-guard`
being further along the stated roadmap:

1. **It is upstream of git-guard's own failure modes.** A `git-guard` that
   protects remote refs is still useless if the pushed content is a
   credential or a host path. Push-payload scanning is the missing half of
   the same "protect what leaves" story.
2. **It shares the `git push` interception point** already present in
   `classifier._parse_git()` — one hook, two concerns; building
   `git-guard` first means rebuilding the point twice.
3. **The new decision class is a core-surface change.** `docs/compatibility.md`
   says a new decision class is a minor release "announced in README";
   landing it before V2 means `git-guard` is authored against the *final*
   decision vocabulary rather than a partially frozen one. Doing it after
   V2 risks a second, concurrent protocol change.
4. **It is the feature the user actually asked for now**, and its MVP-2
   (standalone `check_span.py`) delivers value with zero adapter changes —
   the lowest-risk way to extend the core.

**Versioning/positioning obligations** (from `docs/compatibility.md`):

- New decision class → **minor**, README announcement, adapter mapping
  table updated.
- New reason codes → **minor**, documented in
  `skills/exfil-guard/references/rules.md` and the compatibility page's
  "Additive surfaces" table.
- Any default-verdict change for an existing shape → minor + a
  `docs/friction.md` entry.
- `README.md` / `README.zh-CN.md` must gain the honest limitation section
  (§2.4 of the analysis) — a feature that arrives without its documented
  blind spots would violate the repo's own threat-model discipline.
