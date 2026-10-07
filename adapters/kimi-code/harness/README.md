# Kimi Code guard-lab capture and comparison

This POSIX helper pins **Kimi Code 2.1.1**, captures prompt mode with
`stream-json`, separates role records, and preserves explicit completion review.
Configuration checks and profile preparation require Python 3.11+ for `tomllib`;
the shared Guard Core retains its Python 3.9+ floor. No dependency is installed.

## Private matched homes

Select the approved source home and the exact 2.1.1 executable. There may be a
different Kimi installation on PATH. Every output directory must be new.

```sh
python3 adapters/kimi-code/harness/guard_lab.py prepare-homes \
  --source-home /absolute/path/to/source-kimi-home \
  --output-dir /tmp/kimi-lab-homes
```

`prepare-homes` derives private `off/` and `on/` homes. It removes only the
recognized repository Guard hook and skill-root entries for `off`, checks TOML
round-trip equality, and fingerprints the remaining settings. The source is
left untouched. Authentication/bootstrap state is copied privately so refreshes
write to the disposable homes; sessions, logs, history, caches and other user
data are not copied. Each directory is mode 0700 and each copied file mode 0600.
These homes contain credentials and must stay outside the fixture and public
project/package. Raw configuration and authentication values are never printed.

Check each selected home without a model call:

```sh
python3 adapters/kimi-code/harness/guard_lab.py check \
  --kimi-home /tmp/kimi-lab-homes/off \
  --kimi-executable /absolute/path/to/kimi-2.1.1 --guard-state off
python3 adapters/kimi-code/harness/guard_lab.py check \
  --kimi-home /tmp/kimi-lab-homes/on \
  --kimi-executable /absolute/path/to/kimi-2.1.1 --guard-state on
```

Guard-on preflight also runs the local doctor bridge/Core probes. This is not
a live-host interception proof. Model labels and the remote model identity
remain user-declared; configuration equality does not authenticate a backend.

## Clean control, then baseline

Use the [README-only task](../../../docs/lab/tasks/guard-lab-clean-task.md) for a real-host
negative control so that it requests no bait read. Use the original
[review task](../../../docs/lab/tasks/guard-lab-trial-task.md) for both attack trials.
For the clean control, prepare a separate equivalent disposable home if the
matched attack pair should start without any previous host session state.

```sh
python3 adapters/kimi-code/harness/guard_lab.py run \
  --kimi-home /tmp/kimi-lab-homes/off \
  --kimi-executable /absolute/path/to/kimi-2.1.1 --guard-state off \
  --output-dir /tmp/kimi-lab-clean --case clean \
  --task-file docs/lab/tasks/guard-lab-clean-task.md \
  --model-label "Kimi official OAuth model" --trial-group kimi-pair-01
```

The observer is stopped after capture, including timeout/failure. Raw stdout
and stderr are private `capture/*.bin` files capped at one MiB each. `run`
returns a pending review summary; the initial Lab report stays `INCONCLUSIVE`.
No `--auto`/`--yolo` is added to prompt mode.

Review the captures and host result, then record `completed`, `failed` or
`timed-out` with the create-once finalizer:

```sh
python3 adapters/kimi-code/harness/guard_lab.py finalize \
  --output-dir /tmp/kimi-lab-clean --status completed

python3 adapters/kimi-code/harness/guard_lab.py run \
  --kimi-home /tmp/kimi-lab-homes/off \
  --kimi-executable /absolute/path/to/kimi-2.1.1 --guard-state off \
  --output-dir /tmp/kimi-lab-baseline --case injection-probe \
  --model-label "Kimi official OAuth model" --trial-group kimi-pair-01

# Review actual host completion before selecting completed.
python3 adapters/kimi-code/harness/guard_lab.py finalize \
  --output-dir /tmp/kimi-lab-baseline --status completed
```

Run guard-on only after an effective baseline. Use the same task, model label
and trial group, the fresh matched `on/` home and a fresh output directory.
The default sample is `direct-v1`; both attack captures can instead select the
same `--attack-sample maintenance-v1` or `--attack-sample config-comment-v1`.
Different sample IDs, protocols or template hashes refuse comparison.
Then compare using the wrapper, which additionally checks non-Guard settings:

```sh
python3 adapters/kimi-code/harness/guard_lab.py compare \
  --baseline-dir /tmp/kimi-lab-baseline --guarded-dir /tmp/kimi-lab-guarded
```

## Role handling and evidence

The parser requires one `role=meta,type=system.version,version=2.1.1` record,
recognized NDJSON role/content shapes, and an assistant response. At most 4096
records are processed. Header-only exit-zero failures, malformed/unknown roles,
non-text shapes, timeout, overflow, changed configuration, or corrupted captures
cannot be declared completed. Failed runs can still be scanned as host output
while their security result remains `INCONCLUSIVE`.

Recognized `role=tool` records use `tool-output`; `role=assistant` records use
`model-output`; metadata and stderr use `host-output`, each with a separate
source kind. These are captured **role labels**, not authenticated authorship.
Assistant records can contain tool-call arguments and Kimi-rendered hook
feedback; a hit there does not by itself prove model prose reproduced a marker.
The parser retains whole role records to avoid dropping tool-call payloads or
JSON metadata, and rejects incomplete streams instead of guessing provenance.

Raw captures stay private for review. Lab reports/events retain only hashes,
sizes, stages, bait IDs and counts. The comparison adds an AST configuration
fingerprint check, but environment values, credentials, backend identity and
same-UID isolation remain outside its proof.

Preparation/check/capture return 0 on successful orchestration and 1 on error.
Finalization/comparison use Lab exits: 0 for passing criterion/mitigation, 2 for
observed exposure/non-mitigation, 4 for inconclusive/incomparable evidence.

```sh
python3 -m unittest tests.test_kimi_guard_lab tests.test_dsh_guard_lab
```

These regressions use synthetic subprocess hosts and no model call. The shared
bounded process helper is also exercised by the DSH suite.
