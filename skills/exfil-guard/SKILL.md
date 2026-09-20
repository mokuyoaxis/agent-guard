---
name: exfil-guard
description: >-
  Disclosure discipline for AI agents. Use before text leaves the machine:
  writing files, sending model requests, posting forge comments, committing or
  pushing. Scans payloads for credentials and host-identifying paths, returns
  a redaction plan instead of the bytes, and refuses emissions that cannot be
  taken back.
---

# exfil-guard

You operate where what leaves the machine cannot be recalled. This skill is
the egress side of the same promise `delete-guard` makes for destruction:
deletion asks "can we come back?", disclosure asks "was this supposed to
leave?". The four pillars, restated for egress:

- **Scope** - the egress boundary. The workspace is exempt; everything
  outside it is a host identifier until proven otherwise.
- **Recoverability** - **weak, and do not pretend otherwise.** There is no
  undo for a sent payload. The guard returns a *redaction plan* and an audit
  record; it cannot un-send anything.
- **Authorization** - unchanged. `NORMAL`/`RESTRICTED` is reused; rule
  configuration is host-side, so you cannot disable a rule.
- **Auditability** - every verdict is recorded as rule id, offsets and
  length. **Never the matched bytes** - not even a hash, which would be
  offline-crackable for a low-entropy secret.

## The one rule

**Never put a credential in an outbound payload, and never hand a raw host
path to anyone outside the workspace.**

```bash
# scan whatever you are about to emit:
python3 <repo>/skills/exfil-guard/scripts/check_span.py --channel file-write < draft.md
python3 <repo>/skills/exfil-guard/scripts/check_span.py --channel llm-request --json < prompt.txt

# apply the plan the guard returned:
python3 <repo>/skills/exfil-guard/scripts/sanitize.py --channel file-write < draft.md
```

`check_span.py` reads the payload on **stdin** and is a pure function: it
never writes, never rewrites, and never prints the match. Exit codes are the
contract: `0` allow/sanitize, `2` block, `3` ask, `1` error.

## What you can receive

| Verdict | Meaning | Your move |
|---|---|---|
| `ALLOW` | nothing matched, a documented placeholder, or a workspace-relative path | proceed unchanged |
| `SANITIZE` | the guard produced a redaction plan (`SANITIZE_SECRET_REDACT` / `SANITIZE_PATH_REWRITE`) | apply the plan, then emit. Do not emit the original payload |
| `ASK` (`ASK_SECRET_EMISSION` / `ASK_PATH_EMISSION`) | the channel cannot be rewritten and cannot be taken back (a terminal transcript cannot be un-printed) | stop and ask a human; do not silently proceed |
| `BLOCK` (`BLOCK_SECRET_EMISSION` / `BLOCK_PATH_EMISSION`) | the channel is immutable or remote history (commit message, push payload) | **do not retry.** Remove the value and redo the message/commit |
| `BLOCK_SECRET_SOURCE_DUMP` | the payload reads a whole secret store (`cat .env`, `printenv`) whose content the guard never saw | stop; emit an explicit, reviewed value instead |
| `BLOCK_OUTPUT_UNSCANNABLE` | the payload was never scanned (too large, non-ASCII, unknown channel, scanner error) | an unscanned egress is not a clean egress. Do not route around it |

A `BLOCK` is not an obstacle to work around. Re-encoding a payload, splitting
a secret across lines, or piping through a tool the guard does not scan is a
violation of the authorization pillar and is recorded in the audit log.

## Applying a redaction plan

The guard deliberately does not rewrite your payload - you hold it. Apply
the plan back to front so offsets stay valid, or let `sanitize.py` do it.
Redaction **preserves format**: `sk-proj-Ab…` becomes `sk-proj-<REDACTED>`
(so a reviewer can still see *which kind* of credential leaked), and a host
path becomes `<PATH>`.

If you decline to apply a plan on a rewritable channel, the API contract is
"you were told": the audit records `sanitize-declined`. That is a choice
with a record, not a default.

## Value-free detection (why `echo "$TOKEN"` is caught)

The guard detects a secret *reference* without ever reading the secret. It
classifies a variable's **name** (`*KEY*`, `*TOKEN*`, `*SECRET*`,
`*PASSWORD*`, `*CRED*`, `*AUTH*`) and a secret-store **file name**
(`.env`, `*.pem`, `id_rsa*`, `.netrc`, `kubeconfig`, …), never a value.
This is the invariant that keeps the guard's own output, logs and audit
lines value-free - a guard that leaks what it protects is worse than none.

Consequence you will notice: if `$OPENAI_API_KEY` actually holds `"test"`,
the guard still ASKs. That is intentional. The cost of a false ASK is one
keystroke; the cost of a false ALLOW is permanent. Do not attempt to
"resolve" the variable to reduce prompts.

## Path rules

A path is only a host identifier if it is **outside the workspace**:
`./src/main.py` says nothing about the machine, while
`/home/alice/work/agent-guard/src/main.py` does. System prefixes
(`/usr`, `/etc`, `C:\Windows`, …) are not host-identifying and are allowed.
Host paths are low severity by default - a bare absolute path with no host
correlation asks, it does not block.

## What this skill does NOT cover

State this plainly; it is the difference between a reliability tool and a
false security claim:

- **Adversarial exfiltration.** An agent that obfuscates a secret to evade
  the scanner is out of scope. This catches *accidents*.
- **Channels with no hook.** A hosted model call with no proxy, the model's
  own tool calls, content produced *inside* a program, and the human
  clipboard are unreachable by construction.
- **Secrets at rest.** This is not a file scanner and not a gitleaks
  replacement. It scans what the guard can see on the way out.
- **Rewriting history.** Detecting a secret already in git history is a
  report at most; rewriting it is a human action with its own risks.

## Exemptions

`.agent-guard/exfil-allow.toml` at the workspace root declares reviewed false
positives (path globs, and literal values **by sha256 hash only**). It is a
host-side file: read from the workspace root, not settable per call. If a
scan hits something you believe is benign, report it - do not weaken the
rule, and do not route around the guard.

See `references/rules.md` for the frozen rule table, the exemption format and
the audit record shape; `references/channels.md` for the egress taxonomy.
