# exfil-guard — where it sits in the architecture

Short orientation for someone who already knows agent-guard from
`docs/architecture.md`. Full analysis and design:
`secret-guard-analysis.md`, `secret-guard-design.md`.

## One-paragraph summary

`exfil-guard` is the **egress side** of the same reliability promise.
`delete-guard` answers "if this destroys something, can we come back?";
`exfil-guard` answers "if this leaves the machine, was it supposed to?".
It adds one classifier input type (text spans instead of shell commands or
paths), one decision class (`SANITIZE`), and two new adapter interception
points (pre-write, pre-request). Everything else — the Decision Protocol,
`policy.worst()` aggregation, the audit log, the NORMAL/RESTRICTED
capability model, `check.py`'s exit-code contract — is reused unchanged.

## Where the parts live

| Concern | delete-guard (existing) | exfil-guard (new) |
|---|---|---|
| Fact extraction | `core/classifier.py` → `OpSpec`, `PathSpec` | **`core/redaction.py`** → `SpanSpec` (new) — same "facts, not decisions" contract |
| Lexical front end | `core/dialects.py` | *none needed*: text has no dialect. The Windows path/interpolation vocabulary is **imported** from `dialects.py`, not re-implemented |
| Rule table | `core/policy.py` → `decide_ops` | `core/policy.py` → **`decide_spans`** (new function, same `Verdict` type) |
| Compensation | `core/recovery.py` (relocate / snapshot) | `core/redaction.py` → **redaction** (the degraded strategy: it records what was removed; it cannot undo a sent payload) |
| Entry point | `skills/delete-guard/scripts/check.py` | **`skills/exfil-guard/scripts/check_span.py`** (separate entry: different input, no compensation phase) |
| Audit | `core/audit.py` (JSONL, append-only) | *same module*, new event kinds, and one extra rule: **matched bytes are never recorded** |
| Skill surface | `skills/delete-guard/` | `skills/exfil-guard/` (one skill, two rule families: `secret/*`, `path/*`) |
| Adapters | pre-command hook | *same hook* **plus** two new points (pre-write, pre-request) — adapter-side only |

## The data flow, side by side

```
delete-guard   shell command ─▶ check.py ─▶ classify ─▶ policy ─▶ RELOCATE/SNAPSHOT
                                                                         │
                                                              compensation engine
                                                                         ▼
                                                                    MUTATE

exfil-guard    outbound payload ─▶ check_span.py ─▶ detect ─▶ policy ─▶ SANITIZE
                                                                         │
                                                                  redaction plan
                                                                         ▼
                                                                    EMIT
```

Same Decision Protocol, mirrored verb of the compensation:

```
delete-guard:  destroy → compensate → proceed        (recoverable)
exfil-guard:   disclose → redact     → emit          (irreversible)
```

## The fourth pillar, restated

| Pillar | delete-guard | exfil-guard |
|---|---|---|
| Scope | workspace boundary | **egress boundary** (channel taxonomy; see design §4.1) |
| Recoverability | quarantine / snapshot | weak: a RedactionRecord, not a rollback |
| Authorization | NORMAL / RESTRICTED | unchanged; rule config is host-side only |
| Auditability | verdicts, txids | verdicts + rule id/offsets/lengths — **never matched bytes** |

The cross-cutting rule still holds and now bites harder:

> **Uncertainty increases restriction.** An unscanned egress is not a clean
> egress (`BLOCK_EXFIL_SCANNER_UNAVAILABLE`).

## Two invariants that are new here

1. **The guard reads names and shapes, never secret values.** Detection of
   `$OPENAI_API_KEY` classifies the variable's *name*, never its content.
   This keeps the guard's own output, logs, and audit lines value-free — a
   guard that leaks what it protects is worse than no guard.
2. **Irreversibility moves the tolerance, not the architecture.** A false
   ALLOW is permanent, a false SANITIZE is cheap; therefore detection
   defaults to strict and path rules default to quiet, without adding a
   second protocol or a second decision model.

## What this feature may and may not claim

Following `docs/threat-model.md`, and stated in the README when it lands:

- **May claim:** catches *accidental* disclosure on channels the harness
  routes through a hook; one decision trail for every verdict; redaction
  with a reviewable plan; no secrets in the guard's own artifacts.
- **May NOT claim:** prevention of adversarial exfiltration; coverage of
  channels with no hook (hosted LLM calls without a proxy, the model's own
  tool calls, content produced *inside* a program — B1 applies); any
  containment guarantee.

Every reachability claim must be traceable to the `Class A / B / C` table
in `secret-guard-analysis.md` §2. A new interception point moves a scenario
from C to B; it never moves it to "solved".
