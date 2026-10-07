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

The guard writes only inside the workspace it protects:
`.agent-trash/` (quarantine, manifest, state, audit). Nothing is sent
anywhere; there is no telemetry.
