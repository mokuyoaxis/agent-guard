# Recovery evidence records

Use these schemas when a recovery needs a durable ledger or handoff. Store the
populated records outside product sources by default. Redact credentials,
signed URLs, host-identifying paths, usernames, session identifiers, and
unrelated project names before publishing any derivative.

## Evidence ledger

One row represents one file, exact patch, snapshot, or independently verifiable
unit. Split mixed-confidence material into separate rows.

| Field | Required meaning |
|---|---|
| `unit` | Stable local identifier, not a secret-bearing cache key |
| `target` | Intended repository-relative path or scoped capability |
| `source_kind` | `git`, `ai-session`, `tool-cache`, `snapshot`, `artifact`, `plan`, or `inference` |
| `source_ref` | Local evidence locator; redact it in public derivatives |
| `observed_at` | Timestamp or reliable session ordinal; `unknown` when unavailable |
| `tool_result` | `success`, `failure`, `partial`, or `unknown` as recorded by the source |
| `sha256` | Hash of recovered bytes when exact bytes exist; otherwise empty |
| `status` | `recovered`, `reconstructed`, or `missing` |
| `confidence` | `exact`, `strong`, `partial`, or `inferred` |
| `notes` | Conflict, precedence, replay result, or practical impact |

Never mark a unit `recovered` from prose, a passing test, or a plausible
rewrite. Those can support `reconstructed`, not byte recovery.

## Replay report

Record each mutating event from the authoritative timeline:

| Field | Required meaning |
|---|---|
| `ordinal` | Stable chronological position |
| `source_ref` | Evidence locator |
| `cwd` | Recorded working directory, normalized only for a redacted public copy |
| `operation` | Patch, write, move, command, or snapshot application |
| `recorded_result` | What the original tool/session said happened |
| `replay_result` | `agree_ok`, `agree_fail`, `diverged_ok`, or `diverged_fail` |
| `affected_paths` | Validated repository-relative targets |
| `notes` | Partial effects, rejected targets, or engine correction |

Recovery quality must not be judged while any authoritative successful event
is `diverged_fail`.

## Final handoff

```markdown
# Recovery handoff

- Trusted baseline:
- Recovered endpoint:
- Evidence precedence:
- Recovery stage:

## Coverage

- Recovered units:
- Reconstructed units:
- Missing units:
- Commits beyond the pushed anchor:
- Time windows covered only by secondary evidence:

## Verification

- Deterministic replay:
- Tree comparison:
- Tests and file counts:
- Package/artifact checks:

## Remaining gaps and impact

- ...

## Publication state

- Local commit:
- Remote branch:
- Tag/release:
- Package publication:
```

Use `exact recovery` only when provenance and completeness support it. Otherwise
use `evidence-backed partial recovery` or `reconstructed checkpoint`.
