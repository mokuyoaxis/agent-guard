# Security policy

## Reporting

Open a GitHub issue for non-sensitive issues, or contact the maintainer
directly for anything you believe is exploitable. Please include the
verdict JSON and audit lines involved.

## What agent-guard claims to be

Reliability infrastructure: it makes destructive actions reversible by
default and records evidence. It is **not a security sandbox** - an agent
with the same OS privileges as the guard can bypass it. See
[docs/design/threat-model.md](docs/design/threat-model.md) before relying on it.

## Expected scanner hits on this repository

This repository documents, tests, and demonstrates destructive commands -
that is its subject matter. Static scanners (including `dsh-plugin-gate`)
will therefore report hits such as:

- `rm_recursive` / command patterns inside `README*`, `docs/`, and
  especially `tests/` fixtures, which must contain real command strings to
  exercise the classifier;
- `dynamic_require` in `core/policy.py` (the `subprocess` call backing
  `git check-ignore`);
- credential-adjacent wording in `docs/guides/publishing-from-ephemeral-environments.md`
  (a guide about *avoiding* credential leaks).

As of v0.1.0 a full self-scan reports 61 hits (29 high / 10 medium / 22
low) with **zero in runtime decision paths** - all are documentation or
test fixtures. If you find a hit outside docs/tests/fixtures, please
report it.

## Data handling

The command guards store recovery and audit data locally. The default bucket
is the workspace's `.agent-trash/`; `AGENT_GUARD_TRASH` can select a custom or
external bucket. Recovery may also write Git metadata, including local exclude
rules and retained stashes. Linked worktrees can share Git metadata outside
the selected worktree. Diagnostics and Lab commands use their declared output
locations. A dry-run assessment can still write metadata.

There is no automatic telemetry. Built-in Lab controls call no model and use
only bounded loopback observation. Explicit live-host/model tests can contact
the configured provider through the host and consume quota; they are separate
from local command checks and require a selected experiment scope.

Recovery manifests, CLI diagnostics and historical audit records can contain
exact paths or other private text. Audit minimization in the 0.2.5-rc1 source
candidate does not make every output or old record secret-free. The exfil text
CLIs return decisions/plans or sanitized stdout; they do not automatically
persist egress audit events. Review and redact evidence before sharing it.
