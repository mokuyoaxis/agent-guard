# exfil-guard rules reference (V1.y / MVP)

The frozen rule table. Rule ids are **payload**, not verdict codes: adding a
pattern is a minor release and needs no new reason code (design 2.2 rule 3).
Reason codes are the contract third parties build against.

## Decision Protocol

| Decision | Meaning | Tier | Rank |
|---|---|---|---|
| `ALLOW` | nothing matched, or the match is provably benign (placeholder, workspace-relative path, system prefix) | SAFE | 0 |
| `SANITIZE` | the guard returns a **redaction plan**; the payload owner rewrites and emits | SAFE | 1 |
| `ASK` | single-execution authorization; the channel cannot be rewritten and cannot be un-done | AMBIGUOUS | 3 |
| `BLOCK` | refuse: immutable/remote history, un-scannable payload, invalid config | FORBIDDEN | 4 |

`RELOCATE`/`SNAPSHOT` (rank 2) belong to delete-guard and never appear here:
there is no compensation for an emission.

**Why `SANITIZE` ranks *below* `ASK`.** It is automatic (SAFE tier), while ASK
forfeits automation. A payload carrying both a sanitizable secret and an
un-rewritable shape must ASK - you cannot silently proceed when part of the
emission is uninspectable.

Exit codes (`check_span.py` and `sanitize.py`): `0` allow/sanitize · `2` block · `3` ask ·
`1` error.

The checker returns a proposal. The sanitizer outputs a payload only after
ALLOW or a verified SANITIZE; ASK/BLOCK/errors produce no payload on stdout.
External plans must match the freshly derived offsets and placeholders, with
any supplied metadata agreeing too. Missing, extra, overlapping or stale ranges
and arbitrary replacements are refused. Revalidation binds the plan to current
scan facts, not to a byte-identical input fingerprint. The library returns empty
`text` for ASK/BLOCK. Both CLIs share default workspace/mode resolution; the
checker retains its explicit mode/path overrides, which are not authorization
for the sanitizer. Input and plan files are bounded UTF-8 text.

## Reason codes

| Code | Decision | Meaning |
|---|---|---|
| `ALLOW_SECRET_PLACEHOLDER` | ALLOW | every match is a documented placeholder or an `<REDACTED>` marker |
| `ALLOW_PATH_IN_WORKSPACE` | ALLOW | every path is workspace-relative or a system prefix |
| `SANITIZE_SECRET_REDACT` | SANITIZE | a credential would leave; a redaction plan is returned |
| `SANITIZE_PATH_REWRITE` | SANITIZE | a host-identifying path would leave; a plan is returned |
| `ASK_SECRET_EMISSION` | ASK | credential on a channel that cannot be rewritten (stdout cannot be un-printed) |
| `ASK_PATH_EMISSION` | ASK | host path on such a channel; low severity |
| `BLOCK_SECRET_EMISSION` | BLOCK | credential about to enter immutable/remote history |
| `BLOCK_PATH_EMISSION` | BLOCK | host path about to enter immutable/remote history |
| `BLOCK_SECRET_SOURCE_DUMP` | BLOCK | the payload reads a secret store the guard cannot see into |
| `BLOCK_OUTPUT_UNSCANNABLE` | BLOCK | never scanned: unknown channel, over cap, scanner error |

## Rule table (first match wins)

| # | Condition | Decision | Code |
|---|---|---|---|
| 1 | payload reads a secret store (`.env`, `*.pem`, `id_rsa*`, `kubeconfig`, `printenv`, `env \| …`) | BLOCK | `BLOCK_SECRET_SOURCE_DUMP` |
| 2 | match is a placeholder / already-redacted marker | ALLOW | `ALLOW_SECRET_PLACEHOLDER` |
| 3 | path is workspace-relative (and not the root itself) | ALLOW | `ALLOW_PATH_IN_WORKSPACE` |
| 4 | channel unknown or payload over the scan cap / scanner error | BLOCK | `BLOCK_OUTPUT_UNSCANNABLE` |
| 5 | channel is immutable history (`git-commit-message`, `git-push-payload`) | BLOCK | `BLOCK_*_EMISSION` |
| 6 | channel is neither rewritable nor persistent (`shell-stdout`) | ASK | `ASK_*_EMISSION` |
| 7 | secret, `deterministic` (T1) or context gate fired | SANITIZE | `SANITIZE_SECRET_REDACT` |
| 8 | secret, `contextual` without the context gate | ASK | `ASK_SECRET_EMISSION` |
| 9 | path matches a system prefix (`/usr`, `/etc`, `C:\Windows`, `/Users/Shared`) | ALLOW | `ALLOW_PATH_IN_WORKSPACE` |
| 10 | path is deterministic (host prefix, workspace ancestor, CI root) or the workspace root | SANITIZE | `SANITIZE_PATH_REWRITE` |
| 11 | path is contextual (generic absolute path, no host correlation) | ASK (SANITIZE in RESTRICTED) | `ASK_PATH_EMISSION` |

`RESTRICTED` tightens only: it promotes rule 11 to SANITIZE and never
loosens a BLOCK.

## Span rules (`secret/*`)

| Rule id | Shape | Anchor |
|---|---|---|
| `secret/openai-key` | `sk-` + 20+ url-safe chars, incl. `sk-proj-`/`sk-ant-`/`sk-live-` | prefix + length + charset |
| `secret/github-token` | classic `ghp_`/`gho_`/`ghu_`/`ghs_`/`ghr_` + 36; `github_pat_` + 22–255; `ghs_APPID_JWT` installation wrapper | vendor prefix; new wrapper has a structural JWT header |
| `secret/aws-access-key-id` | `AKIA`/`ASIA` + 16 upper-alnum | access-key identifier prefixes |
| `secret/gitlab-token` | `glpat-` + 20+ | prefix |
| `secret/slack-token` | `xox[baprs]-` | prefix |
| `secret/stripe-key` | `sk_live_`/`rk_live_` (never `sk_test_`) | prefix + live mode |
| `secret/pypi-token` | `pypi-` + 85+ base64url body characters, without a fixed maximum | documented vendor scanner shape |
| `secret/jwt` | three base64url segments; header decodes to JSON with a nonempty ASCII string `alg` | **structural**, not just `a.b.c` |
| `secret/private-key-block` | `-----BEGIN [X ]*PRIVATE KEY-----` … `-----END …` | delimiters; whole block withheld |
| `secret/connection-password` | nonempty password between the first userinfo colon and final `@` in a `scheme://` authority | URI credential context; only password withheld |
| `secret/source-reference` | an env-var *name* with a complete credential component, a secret-store file name, or a whole-environment expansion | value-free (T2) |

Vendor/JWT boundaries use ASCII identifier characters so credentials adjacent
to CJK prose are detected while English identifier substrings stay quiet.
The new GitHub installation rule retains the complete `ghs_APPID_JWT` wrapper,
including long signatures, under the normal payload cap. App ID is opaque;
the JWT header must decode to JSON with a nonempty ASCII string `alg`. Generic
JWT detection requires that same declared header type. No business claims,
issuer, expiry or signature are authenticated, and algorithms are not limited
to a hardcoded enum. Format references: [GitHub's format notice](https://github.blog/changelog/2026-04-24-notice-about-upcoming-new-format-for-github-app-installation-tokens/)
and [updated format guidance](https://github.blog/changelog/2026-05-15-github-app-installation-tokens-per-request-override-header/).

AWS group/user/role/policy IDs (`AGPA`/`AIDA`/`AROA`/`ANPA`) are resource
identifiers and are excluded from the access-key rule, following the
[AWS prefix table](https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_identifiers.html#identifiers-unique-ids).
Their appearance inside a URI password or another credential context does not
exempt that enclosing credential. The JWT header type follows
[RFC 7515 §4.1.1](https://www.rfc-editor.org/rfc/rfc7515#section-4.1.1).

PyPI detection follows its [official scanner format](https://docs.pypi.org/api/secrets/#detecting-the-pypi-secret-format).
The entire body is captured under the normal payload cap, including longer
Macaroon caveats. `pypi-client-library`, short examples and whole placeholders
remain quiet. This is shape detection, without deserializing the Macaroon,
verifying authorization or reporting/revoking tokens through PyPI's API.
The legacy broad `sk-` shape remains protected pending a complete format
review; ordinary long component names with that shape can still match.

Variable references support `$NAME`, `${NAME}`, `$env:NAME` and `%NAME%`.
Underscores and camelCase/acronym boundaries separate complete components:
`TOKEN`, `PASSWORD`, `API_KEY` and `accessToken` are credential names;
`MONKEY` and `API_KEYBOARD` are not. A terminal `COUNT`, `LENGTH` or `ENABLED`
component denotes metadata (`TOKEN_COUNT`, `KEY_ENABLED`). Arbitrarily glued
lowercase/all-capital names are not split by substring guessing. Public-key
names retain the existing conservative behavior; there is no `PUBLIC*`
exemption. A benign first reference cannot hide a later credential reference.
Names are classified without resolving or reading their values.
Bare lower/title-case `key` (`$key`, `${key}`) is ambiguous with an ordinary
dictionary/schema key and is not classified by name alone. Uppercase `KEY`,
qualified names such as `apiKey`, and explicit `$env:key`/`%key%` references
remain protected. This narrow name boundary does not exempt source files or
template expressions as a whole; stronger names still cause refusal.

The URI rule uses original text coordinates. It supports percent encoding,
JSON-escaped scheme slashes, empty usernames (e.g. Redis), and literal `@`
inside a password by selecting the final authority separator. Complete
template expressions (`<PASSWORD>`, `${VAR}`, `%VAR%`) and redaction markers are
exempt; plain short/weak passwords are not. Variable-reference facts still
apply independently and cannot be swallowed by an overlapping URI span.
Raw `/`, `?`, `#`, whitespace and quote delimiters end the authority: encode
them inside a password. This rule does not cover non-URI DSNs, fully encoded
URLs or arbitrary nested serialization. Accounts and hosts remain visible.
Syntax reference: [RFC 3986 §3.2.1](https://www.rfc-editor.org/rfc/rfc3986.html#section-3.2.1).

Two design rules that keep T1 deterministic:

1. **Structural validation** where the format allows it. A JWT is only a JWT
   if segment 1 base64-decodes to JSON with the declared ASCII string `alg`; otherwise it is a version
   string (`1.2.3`) or a hostname (`api.example.com`), both of which occur
   constantly in this repository's own text.
2. **No generic keyword patterns.** `password=` / `secret=` are *context*
   (T3's job). In T1 they would destroy the "deterministic ⇒ auto-sanitize"
   property the whole design depends on.

## Span rules (`path/*`)

| Rule id | Shape | Default |
|---|---|---|
| `path/workspace-relative` | inside the workspace | ALLOW (the root itself: SANITIZE) |
| `path/system` | `/usr`, `/etc`, `/opt`, `/var/...`, `/dev/null`, `C:\Windows`, `/Users/Shared` | ALLOW |
| `path/host-absolute` | under a host prefix, a CI root, or a workspace ancestor | SANITIZE |
| `path/generic-absolute` | an absolute path with no host correlation | ASK |
| `path/device` | UNC (`\\host\share`), device (`\\?\`, `\\.\pipe\`) | SANITIZE |

**The workspace-relative exemption is the core rule.** A path is only a host
identifier if it is outside the workspace. The boundary is the one
delete-guard already computes (`classifier.discover_workspace`).

**Host identity is derived, never guessed.** The deny-set is built at runtime
from `HOME`/`USERPROFILE`/`TEMP`, `getpass.getuser()`, `USER`/`LOGNAME`/
`USERNAME`, workspace ancestors (which are by definition not to be echoed
raw), and `AGENT_GUARD_HOSTNAMES`/`AGENT_GUARD_HOME_MARKERS`. The
filesystem root is deliberately excluded - `/` is an ancestor of everything
and therefore identifies nothing.

**The component floor (design 3.2).** A POSIX match needs ≥ 2 components;
`/` alone and `/project` are conventions, not hosts. This is what keeps this
repository's own prose quiet.

## Placeholder allowlist (design 5.1)

Deterministic, in-repo, no config: exact static words such as
`YOUR_API_KEY_HERE`, `EXAMPLE_SECRET`, `dummy-value`, `changeme`, `test`,
`fake`, `placeholder` and `TODO`; whole `<REDACTED>`/`[REDACTED]` markers,
identifier templates (`<TOKEN>`, `${NAME}`), and whole trivial-alphabet bodies.
Known public vendor prefixes can be removed before checking the entire body,
so explicit vendor examples (`sk-test`, a vendor prefix followed only by `x`)
remain quiet. The two fixed AWS documentation example IDs are exact entries.
An arbitrary random credential containing `xxx`, `dummy` or `redact`, or
ending in `none`/`example`, is not a placeholder. A marker word inside a
private-key block cannot exempt the entire block. Independent secret-source
facts still apply to variable templates.

## Configuration (environment)

| Variable | Meaning |
|---|---|
| `AGENT_GUARD_EXFIL_CHANNEL` | default egress channel for both text CLIs |
| `AGENT_GUARD_EXFIL_MAX_BYTES` | input/scan cap (default 4 MiB); over-cap input is `BLOCK_OUTPUT_UNSCANNABLE`; sanitizer plan files use the same bound |
| `AGENT_GUARD_HOSTNAMES` / `AGENT_GUARD_HOME_MARKERS` | extra literal host names / home markers |
| `AGENT_GUARD_HOME_PREFIXES` | extra literal prefixes to treat as host-identifying |
| `AGENT_GUARD_SYSTEM_PREFIXES` | override the system-prefix allow set |
| `AGENT_GUARD_WORKSPACE` | workspace boundary (shared with delete-guard) |
| `--path` (`check_span.py`) | the path the payload will be written to; enables the repo-local exemption file for that path |

## Repo-local exemptions (design 5.2)

`.agent-guard/exfil-allow.toml` (or `.agent-guardignore`) is read from the
**workspace root only**, so an agent cannot add one on the fly.

```toml
[rules]
disable = []                       # rule-id globs; [] means none
[values]
sha256 =                          # exempt a literal WITHOUT tracking it
  sha256:8ab1...e7
[paths]
ignore =                           # path globs skipped entirely
  docs/*.md
  tests/fixtures/secrets/*
```

Three properties matter:

1. **Value exemptions are by hash.** A human who needs one specific literal
   exempted provides `sha256:...`; the guard compares hashes. Writing the
   value into a tracked file *is* the bug this feature exists to prevent.
2. **Path exemptions, not rule amnesty.** Scoping an exemption to a path
   keeps the rule fully active everywhere else. Disabling the rule wholesale
   is what would blind the guard.
3. **A broken allowfile fails closed.** Malformed input is ignored and the
   rules stay on - never the reverse.

This repository ships such a file for its own documentation and fixtures,
exactly as `SECURITY.md` documents "Expected scanner hits on this
repository". The `[paths]` list is a *reviewed* list: it names the files
that discuss the topic, and the noise-budget test still measures the whole
corpus.

## Audit record shape

```json
{"event":"exfil-sanitize","rule_id":"secret/openai-key",
 "confidence":"deterministic","channel":"file-write",
 "family":"secret","span":[104,151],"length":47,
 "exempted":false,"plan_id":"…","ts":"…","session":"…"}
```

No `raw`, no prefix, no suffix, **no hash of the secret** (a hash of a
low-entropy secret is offline-crackable). Rule id + offsets + length is
enough to reproduce a false positive and worthless to an attacker.

## Noise budget (design 5.6)

On this repository's own corpus (README, `docs/`, `tests/`, `skills/`) the
default rule set must produce **zero BLOCK** and only path-family matches.
Anything else is a rule bug, not a tuning problem. Verified by
`tests/test_exfil_classifier.py::RepoCorpusNoise`.

## Known limitations (V1.y)

- T3 (generic entropy with a context gate) is **not in this release**: it is
  the single largest false-positive source, and the named scenarios do not
  require it (design 6.2 item 3).
- No archive/packaging enumeration (`tar`, `git bundle`, `gh release
  upload`); the natural MVP-6.
- No git-history rewriting. Detection is not remediation.
- Channels with no hook remain uncovered, by construction. See
  `channels.md`.
