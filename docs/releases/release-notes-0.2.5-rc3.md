# Agent Guard 0.2.5-rc3

Source candidate prepared 2026-10-13. Published baselines remain stable
[0.2.4](release-notes-0.2.4.md) and prerelease
[0.2.5-rc2](release-notes-0.2.5-rc2.md). The maintainer authorized the normal
main-branch commit and push on 2026-10-13. Version tags and GitHub/npm package
publication remain separate steps; a main commit is a source preview.

## Scope

- **Blue-team research.** New [research notes](../research/README.md) describe
  data-flow observation, disclosure permissions, delegation provenance and
  independent task/evidence scoring from our controlled experiments.
- **Sanitized observations.** A [stage report](../reports/blue-team-lab-2026-10.md)
  and [aggregate JSON](../research/lab-observations-2026-10.json) preserve
  bounded results, failures and inconclusive trials. Private routes,
  identifying paths, raw sessions and reusable induction payloads are omitted.
  Complete private real-model captures are not published with these notes.
- **Portable calibration example.** The [four-control example](../lab/blue-team-calibration.md)
  reuses the existing Core controls from a checkout or installed package,
  calls no model and requires no host credentials or new dependency.
- **Contribution and navigation.** Both READMEs and contribution guidance
  welcome forks, PRs and independent defensive reproductions. Lab proceeds
  through small, bounded experiments without a promised cadence.
- **Existing fixture-extension hooks.** The candidate retains the local
  private sample/fixture injection parameters used by earlier Lab work.
  Default built-in samples and the normal DSH runner keep their existing path;
  the private host drivers are not packaged.

## Compatibility and evidence boundaries

The Decision Protocol, native-hook coverage, policy verdicts, recovery
strategies, public controller flags and existing evidence levels retain their
contracts. No production dependency or storage/configuration migration is
introduced. The example is a script around current controls, not a new bin
or a general authorization gateway.

Experimental `LOCAL_ONLY`/`MODEL_ALLOWED` labels and private parent/child
methods are research subjects; they do not become production decision
classes or host guarantees. The public package includes the research and
example through `docs/`; personal `experiments/`, raw evidence and backups
remain outside its allowlist.

The latest historical four-member real-model control delivered untrusted
content to child models, but neither enabled nor disabled members attempted
the target read. No real policy-blocking effect was measured. Auxiliary Kimi
output remained inconclusive. Publication of the notes does not create new
L1/L2 claims or new model/host trials.

## Candidate validation

The explicit 188-file public source checkpoint passed 917 Python tests
(1023.105 seconds, no skips) and both Node adapter smoke groups.
The full run froze that source copy; later changes are documentation-only
clarifications and this evidence paragraph. Runtime code and tests retain
their tested bytes. The 108 pre-existing private experiment/test files are
preserved separately and are not part of this public-source checkpoint.

Sixteen focused metadata, routing, example-failure and corpus checks passed.
The sanitized numeric observations were checked against their original
machine summaries. The 134-file local package matched source and offline
installation. Ten CLI entries, adapter import, synthetic recovery round
trips and audit/query checks passed. The installed example completed all
four controls with PASS, HEALTHY and CALIBRATION_ONLY; reuse of its output
directory was refused without changing the retained evidence.
Later Markdown review updates current-version pointers, defensive navigation
and this evidence paragraph. Runnable and test bytes remain the verified
checkpoint; package/source equality is checked again after documentation
changes. No new real-model or native-host trial is credited to this work.

Exact-commit remote CI, GitHub/npm publication, registry availability,
signature and provenance receipts are not established by a local tarball.
Stable-channel and existing version artifacts remain governed by the release
workflow and their original receipts.
