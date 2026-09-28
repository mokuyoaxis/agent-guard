# Agent Guard 0.2.3-rc1 — Windows Core fail-closed candidate

These notes describe the `0.2.3-rc1` source candidate. A version in the
repository is not proof that its tag, GitHub prerelease, or npm package has
been published. The latest stable npm installation remains `0.2.2` unless the
registry says otherwise.

## Issue #7: two confirmed Core fixes

[Issue #7](https://github.com/mokuyoaxis/agent-guard/issues/7) reported two
fail-open paths from a Windows 11 CLI run. The runner was WorkBuddy, but every
reproduction called Core directly; these were not adapter findings.

1. A known destructive command could disappear when its vocabulary did not
   match the configured dialect. In particular, `rm -rf .` changed from
   `BLOCK_PROTECTED_PATH` under POSIX to `ALLOW_NOOP` under `cmd`.
2. Windows-native delete targets kept or lost backslashes according to the OS
   running the classifier. On Windows, `del build\o.js` could become either
   `buildo.js` or a host-specific target fact and fall through as a no-op.

The classifier now treats the selected dialect's command segmentation as the
authoritative boundary, then checks every segment for known destructive
vocabulary that produced no operation. A mismatch becomes a target-free,
non-compensable `UNKNOWN` fact and therefore
`BLOCK_UNDETERMINABLE_EFFECT`. It does not borrow targets or flags from a
different shell grammar. Mixed lines are checked segment by segment, so an
earlier recognized delete cannot hide a later mismatch.

This safety floor covers the existing filesystem-delete vocabulary and the
already supported destructive Git/`find -delete` shapes. It does not block
all unknown commands. Negative regressions keep ordinary `echo`,
`Write-Host`, `git status`, quoted command text and cmd-literal `$()` forms
quiet.

Windows-native delete targets are now normalized to `/` after their own
tokenizer has identified them, on every host OS. The default POSIX bridge
performs the same rewrite only for its existing standalone Windows-verb
compatibility path. Normal POSIX escape semantics such as `rm a\ b` are not
globally rewritten.

## Withdrawn exfil report

The issue's third finding was withdrawn by its reporter after an exact-byte
rerun. Git Bash `printf` had changed the backslashes and newline before the
payload reached `check_span.py`. Python `subprocess` input confirmed that
native, forward-slash, mixed-separator and UNC Windows absolute paths were
already classified and sanitized correctly.

This candidate does not change `core/redaction.py` or expand exfil policy. It
adds a regression that constructs the native Windows path in Python, supplies
matching `USERPROFILE`/`USERNAME` markers, and requires
`SANITIZE_PATH_REWRITE` without reproducing the matched bytes in output.

## Focused Windows CI

CI now includes a blocking `windows-latest` / Python 3.13 Core job. It runs
the cmd/PowerShell dialect suite, the two Issue #7 CLI regressions, a safe
temporary-directory relocate/restore round trip, and the exact-byte exfil
regression. Test inputs are passed by Python rather than shell `printf` or
`echo`, so Git Bash cannot silently rewrite them. The existing Linux/macOS
matrix continues to run the full suite.

A passing run is bounded real-Windows evidence for the listed Core paths,
not completion of the entire Windows roadmap. It does not prove native
cmd/PowerShell execution by every harness, hook-selected dialect wiring,
privileged symlink behavior, all UNC/device paths, or full-suite portability.

## Prerelease publishing

The release workflow now accepts the project's exact `X.Y.Z-rcN` tag shape in
addition to stable `X.Y.Z` tags. A tested channel classifier routes RCs to a
GitHub prerelease and npm's `rc` dist-tag; stable versions use `latest`. The
tag and `package.json` version must still match exactly, and GitHub Release
plus npm still receive the same packed tarball with provenance.

If published, install this candidate explicitly as
`@mokuyoaxis/agent-guard@0.2.3-rc1` or through
`@mokuyoaxis/agent-guard@rc`. An RC publication must not move npm `latest`
away from `0.2.2`.

## Verification and limits

The local candidate gate passes 450 Python tests plus the DSH adapter smoke
test. [GitHub Actions run 36366616252](https://github.com/mokuyoaxis/agent-guard/actions/runs/36366616252)
passed all 12 jobs for implementation commit `7b83649`, including the focused
Windows Core gate on `windows-latest` / Python 3.13. This is the bounded
evidence described above, not a universal Windows-harness claim. No tag,
GitHub prerelease, npm publication, or Issue closure is implied by these
notes.

`guard-lab` is not included in `0.2.3-rc1`. It remains planned for the next
available 0.2.3 candidate after the Windows fix is stable.
