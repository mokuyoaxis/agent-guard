---
name: exfil-guard
description: >-
  Disclosure discipline for AI agents. Use before text leaves the machine:
  writing files, sending model requests, posting forge comments, committing or
  pushing, or when inspecting a JSON/dotenv config without exposing its values.
  Scans payloads for credentials and host-identifying paths, and offers an
  explicit read-only, value-free config view.
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

`sanitize.py` uses the same exit codes and emits no payload on ASK/BLOCK or
error. Both CLIs bound stdin while reading (4 MiB by default), support UTF-8,
and resolve the same default workspace/host-side mode. The first-byte wait is
bounded; it is not a deadline for a producer that has already started writing.

## Safe config view

When you need to learn a JSON or dotenv config's shape, use the explicit
read-only view instead of printing the file:

```bash
python3 <repo>/skills/exfil-guard/scripts/view.py --workspace <workspace> .env
python3 <repo>/skills/exfil-guard/scripts/view.py --workspace <workspace> config.json
```

The argument is a workspace-relative path. The JSON result contains field
names, types, and `set`/`empty` states, **never scalar values**. Known
secret-shaped field names are hidden too; arbitrary field names are not a
proof of secrecy. The view accepts only small, valid UTF-8 JSON and a strict
single-line dotenv subset. It refuses symlinks, hardlinks, special files,
unsafe paths, and unsupported platforms instead of falling back to a raw
read. Exit `0` means a view was produced; `2` means refused; `1` is an
internal error. Do not write the value-free view back over the original
config: it is diagnostic output, not an editable copy. This CLI does not
intercept a harness's ordinary file-read tool.

## What you can receive

| Verdict | Meaning | Your move |
|---|---|---|
| `ALLOW` | nothing matched, a documented placeholder, or a workspace-relative path | proceed unchanged |
| `SANITIZE` | the guard produced a redaction plan (`SANITIZE_SECRET_REDACT` / `SANITIZE_PATH_REWRITE`) | apply the plan, then emit. Do not emit the original payload |
| `ASK` (`ASK_SECRET_EMISSION` / `ASK_PATH_EMISSION`) | the channel cannot be rewritten and cannot be taken back (a terminal transcript cannot be un-printed) | stop and ask a human; do not silently proceed |
| `BLOCK` (`BLOCK_SECRET_EMISSION` / `BLOCK_PATH_EMISSION`) | the channel is immutable or remote history (commit message, push payload) | **do not retry.** Remove the value and redo the message/commit |
| `BLOCK_SECRET_SOURCE_DUMP` | the payload reads a whole secret store (`cat .env`, `printenv`) whose content the guard never saw | stop; emit an explicit, reviewed value instead |
| `BLOCK_OUTPUT_UNSCANNABLE` | the payload was never scanned (too large, unknown channel, scanner error) | an unscanned egress is not a clean egress. Do not route around it |

A `BLOCK` is not an obstacle to work around. Re-encoding a payload, splitting
a secret across lines, or piping through a tool the guard does not scan is a
violation of the authorization pillar and is recorded in the audit log.

## Applying a redaction plan

The guard deliberately does not rewrite your payload - you hold it. Apply
the plan back to front so offsets stay valid, or let `sanitize.py` do it.
Redaction **preserves format**: `sk-proj-Ab…` becomes `sk-<REDACTED>`
(so a reviewer can still see *which kind* of credential leaked), and a host
path becomes `<PATH>`.

An external `--plan` is checked against a fresh scan, including offsets,
placeholders and any supplied rule metadata. A partial, stale or arbitrary plan
is refused; the rewritten payload must pass another scan. A plan made with
checker-specific mode/path overrides still has to satisfy current sanitizer
policy. URI passwords use `<REDACTED>` while retaining scheme/account/host;
this is credential redaction, not network-topology hiding or an editable URI.

If you decline to apply a plan on a rewritable channel, the API contract is
"you were told": the audit records `sanitize-declined`. That is a choice
with a record, not a default.

## Value-free detection (why `echo "$TOKEN"` is caught)

The guard detects a secret *reference* without ever reading the secret. It
classifies a variable's **name** by complete credential components (`KEY`,
`TOKEN`, `SECRET`, `PASSWORD`, `CRED`, `AUTH`, including underscores and
camelCase), and a secret-store **file name**
(`.env`, `*.pem`, `id_rsa*`, `.netrc`, `kubeconfig`, …), never a value.
This keeps this scanner from creating a second value copy; it does not
certify unrelated CLI output or existing audit records as secret-free.

Consequence you will notice: if `$OPENAI_API_KEY` actually holds `"test"`,
the guard still BLOCKs its uninspectable source reference. Do not attempt to
resolve the variable to soften that decision. `MONKEY`/`API_KEYBOARD` are
ordinary names, and terminal `COUNT`/`LENGTH`/`ENABLED` denotes metadata.
Public-key names retain conservative handling pending separate review.
Bare lower/title-case `key` is too ambiguous to classify without an explicit
environment reference; uppercase `KEY` and qualified `apiKey` remain protected.

Placeholder exemptions require a whole documented shape or an exact static
example; incidental words inside random credentials do not exempt them.
Vendor credentials adjacent to CJK prose are scanned with original offsets.
GitHub installation credentials support both classic and `ghs_APPID_JWT`
shapes, with complete redaction of long tokens. These are shape facts, not
authentication or token-validity checks. See [rules.md](references/rules.md).

PyPI token bodies follow the documented `pypi-` scanner shape and are redacted
in full, including long bodies. AWS access-key IDs use AKIA/ASIA; IAM resource
IDs have separate types and do not match that credential rule. Generic JWT
headers require a nonempty ASCII string `alg`. Public-key variable names and
the broad legacy `sk-` shape retain conservative handling where their purpose
or complete format has not been established.

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
- **At-rest secrecy.** The explicit safe view does not encrypt files or
  prevent other tools from reading them. The text scanner is not a
  repository-wide file scanner or a gitleaks replacement.
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
