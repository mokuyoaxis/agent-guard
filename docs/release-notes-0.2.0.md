# Agent Guard 0.2.0 — GitHub Release notes

These notes describe the `0.2.0` source. See
[GitHub Releases](https://github.com/mokuyoaxis/agent-guard/releases) for
publication status. The `0.2.0-rc1` and `0.2.0-rc2` tags were source
previews; npm publication is a separate decision.

## What 0.2.0 brings

- A harness-neutral Python Core and Decision Protocol for recoverable
  destructive actions. Supported file deletion can relocate targets to
  `.agent-trash/`; supported destructive Git operations can take a snapshot
  before proceeding. Unrecoverable or indeterminate effects are refused.
- Stronger incident regressions for transaction IDs, GC, command boundaries,
  protected paths, and hard refusals. Shell classification has opt-in `cmd`
  and PowerShell dialects alongside POSIX; real Windows host end-to-end
  coverage is still open.
- `exfil-guard` text CLI: a cooperative pre-emission check for known credential
  shapes and host-identifying paths, with channel-specific decisions and a
  redaction plan for the caller to apply. It does not own arbitrary output
  channels or transparently intercept model traffic.
- An explicit, read-only safe view for selected JSON and strict single-line
  dotenv configs. It returns structure, types, and `set`/`empty` state rather
  than scalar values, and refuses unsafe paths/files and unsupported formats.
  This is **new since `0.2.0-rc2`**. It does not stop a harness from reading
  the original file by another route; unknown secret-bearing field names
  may still be visible. Never write the view back over the configuration.
- A `recovery-audit` Skill for evidence-led reconstruction after accidental
  deletion. It can organize surviving Git, sessions, and caches; it cannot
  recreate bytes that have no surviving copy.

## Integration evidence and limits

The [harness capability matrix](harness-capabilities.md) separates CLI use,
adapter tests, observed hooks, and observed execution-level enforcement.
Claude Code has a bounded real CLI/hook test with a scripted model endpoint.
DSH 0.1.5-rc.1 has adapter smoke tests, not a confirmed current-version
plugin-load and rejected-execution trial. Kimi Code 0.42.0 has two isolated
`local/kimi-k3` sandboxes showing sampled hook and compensation paths,
including sampled subagents; execution-level `BLOCK` remains unproven. In
that Kimi version, `ASK` is mapped to a hard refusal, not an approval prompt.
Codex and ZCode evidence covers cooperative Skill/CLI use, not native
interception in this repository.

Agent Guard is not a sandbox against a malicious agent or harness with the
same OS privileges. A hook can govern only calls that reach it. No-hook
channels, untested tools or versions, arbitrary subagents, hidden harness
uploads, and general prompt-injection resistance are **not** release claims.
See the [threat model](threat-model.md) and
[compatibility contract](compatibility.md).

New `check.py` results, audit events, and compensation metadata replace the
raw command with `<redacted>` and carry a `check_id` for correlation. Its
agent-visible result omits raw policy notes and dynamic exception text;
`status --json` also masks free-form fields in legacy audit records. This
does **not** rewrite historical append-only records or recovery manifests:
older local files may still contain commands, and recovery metadata must
retain target paths to restore them. The original command can also appear
in the harness's own logs or process arguments outside Agent Guard's control.
Review local evidence before sharing it, and do not put credentials in
command arguments.

The offline `guard-lab` honeytoken experiment, comprehensive audit/output
redaction, trusted file-read interception, and universal harness adaptation
are future work. The root npm manifest remains private and DSH-oriented as
an entry point; this GitHub Release is **not** an npm package announcement.

## Start here

Use the [quick start](../README.md#quick-start-with-your-coding-agent) and
the [adapter matrix](harness-capabilities.md). Configure only the entry path
your host actually supports, then verify it with harmless, bounded probes.
