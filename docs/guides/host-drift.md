# Host drift checks

Host support is version-, configuration-, tool-, and event-specific. A hook
that worked yesterday may be disabled, moved, or interpreted differently after
a host upgrade. Agent Guard's drift check is a zero-model-call preflight for
that reliability problem; it is not a sandbox or a live-interception proof.

## What is checked

`doctor.py kimi|claude --check-drift` performs three local checks:

1. It queries the installed host's local `--version` command and compares the
   parsed version with the adapter's shipped compatibility profile.
2. It fingerprints only the normalized Agent Guard hook fields: event,
   matcher, bridge command tokens, timeout, dialect, and blocking options.
   Unrelated host configuration is neither copied into the report nor stored
   in a baseline.
3. It fingerprints the local enforcement path (profile, bridge, adapter,
   shared guard entry point, and Core Python files).

This does not start a model or edit host configuration. The ordinary doctor
does not execute the host at all; host version discovery is opt-in through
`--check-drift`, `--baseline`, `--write-baseline`, or `--host-executable`.

```sh
python3 doctor.py claude --probe --check-drift
python3 doctor.py kimi --probe --check-drift --json
```

`--host-executable PATH` selects a non-default executable. It is also useful
when a host is installed under a wrapper name. The selected executable is run
only with the compatibility profile's version arguments.

## Status vocabulary

| Status | Meaning |
|---|---|
| `NOT_CHECKED` | Version discovery and fingerprinting were not requested. |
| `CURRENT` | The detected version is in the shipped tested-version list and the selected configuration is structurally valid. With a baseline, its fingerprints also match. |
| `STALE` | The host version changed or is outside the shipped tested-version list. This is a request to re-test, not a claim of incompatibility. |
| `DRIFTED` | A supplied baseline no longer matches the selected hook configuration, Agent Guard runtime, or compatibility profile. |
| `BROKEN` | The selected configuration or requested local bridge probe failed. |
| `UNVERIFIED` | The host version, profile, baseline, or fingerprints could not be established. |

An explicit drift check exits non-zero unless the status is `CURRENT`, making
it suitable for a local preflight. `CURRENT` is deliberately weaker than
`host_interception`: the latter remains `UNVERIFIED` because a static file,
version command, and local adapter process cannot prove that a live host
loaded or enforced the hook.

## Privacy-minimal baselines

A baseline is optional. Create one only after reviewing a passing installation:

```sh
python3 doctor.py claude --probe --check-drift \
  --write-baseline /existing/private/directory/claude-baseline.json

python3 doctor.py claude --check-drift \
  --baseline /existing/private/directory/claude-baseline.json
```

Baseline creation requires `CURRENT` and a passing local probe. The target's
parent must already exist, and the doctor refuses to overwrite an existing
file. A baseline contains only its schema number, harness/profile identifiers,
the parsed host version, and two SHA-256 fingerprints. It does not contain the
configuration, hook command, model data, or absolute paths. The file is
created with mode `0600`; keep it local rather than committing it as a public
compatibility claim.

After an intentional upgrade, use a new baseline path only after repeating
the review. A matching baseline shows that the checked local inputs did not
change; it is not an independent witness against a malicious process with the
same OS privileges.

## Compatibility profiles and limits

Profiles live beside their adapters as `compatibility.json`. They describe
only the host-facing version command and the narrow contract already covered
by that adapter. Core policy remains shared and is not copied into profiles.
Adding a profile for an unlisted host does not make it supported: the host
still needs a blocking pre-tool event and the bounded acceptance evidence in
the [capability matrix](harness-capabilities.md).

No process running inside an adapter can prove that the host invoked it for
every call. Missing hooks, changed tool names, matcher bypasses, host timeouts,
new subagent routes, and error-semantics changes need bounded live-host
evidence. The optional sentinel below tests one root-agent Bash call only.

## Optional low-token live-host sentinel

`--live-sentinel` starts the selected real Claude Code or Kimi Code CLI in a
fresh, private fixture and asks its configured model to make exactly one Bash
call. It therefore **may consume provider quota**. It is never run by the
ordinary doctor or by `--check-drift`.

```sh
python3 doctor.py claude --config /path/to/settings.json --live-sentinel
python3 doctor.py kimi --config /path/to/config.toml --live-sentinel --json
```

The probe command only creates a uniquely named empty marker in the fixture.
The host process receives a deliberately invalid, nonce-bearing shell dialect:
if the selected hook reaches Agent Guard, Core refuses the call with
`BLOCK_DIALECT_UNKNOWN`. No deletion, remote, repository rewrite, credential,
or user project is part of the probe.

A PASS requires all of the following independent evidence:

1. the adapter wrote a create-only `0600` receipt for the exact Bash command;
   the receipt contains hashes, not the command or working-directory path;
2. the fresh fixture's redacted audit records `enforce-block` and
   `BLOCK_DIALECT_UNKNOWN`;
3. the real host returned Agent Guard's block feedback; and
4. the marker does not exist.

The result and alarm vocabulary is deliberately asymmetric:

| Result | Alarm | Meaning |
|---|---|---|
| `PASS` | `NONE`, or `NOTICE` when the static drift status is not `CURRENT` | The bounded exact call produced all required hook/Core/host evidence and did not execute. The report includes the drift status that caused a notice. |
| `FAIL` | `CRITICAL` | The marker exists: the submitted command executed despite the expected block. |
| `INCONCLUSIVE` | `WARNING` | The host/model did not make the exact call, timed out, failed to start, or any receipt/audit/host-feedback element is missing. It is never promoted to PASS from marker absence alone. |

Exit codes are `0` for PASS, `2` for FAIL, and `1` for INCONCLUSIVE. A
`result.json` evidence summary is always written when the host starts. Raw
host/model stdout and stderr are hashed and counted, then discarded rather
than retained. The evidence directory is kept for user review and is never
automatically deleted or overwritten; `--sentinel-output NEW_PATH` selects it.

For Claude, the runner loads only the selected settings plus project sources,
limits the advertised tools to Bash, and keeps `dontAsk`; it does not enable
permission bypass. For Kimi, the selected file must be named `config.toml`,
and its parent is used as `KIMI_CODE_HOME`. The host may still keep its own
session logs outside Agent Guard's evidence directory.

This sentinel is a reliability check, not an adversarial sandbox. Run it only
with a model/provider you trust enough to receive one fixed prompt: when the
very hook being tested is absent, the host still has its normal OS privileges.
The blank fixture and narrow prompt reduce exposure but cannot contain a
malicious host or model. Subagents, other tools, concurrent calls, and future
host versions require separate evidence.
