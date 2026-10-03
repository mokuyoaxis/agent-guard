# WorkBuddy on Windows: community Core/CLI feedback

Report date: 2026-09-27. Summary reviewed: 2026-10-03.

[Issue #7](https://github.com/mokuyoaxis/agent-guard/issues/7) records a community
run against Agent Guard `0.2.2`, commit `274127e`, on Windows 11 with Git Bash,
Python 3.13.15 and Git 2.55.0.windows.3. The reporter
[identified the executor as WorkBuddy](https://github.com/mokuyoaxis/agent-guard/issues/7#issuecomment-5857388494):
an AI coding agent invoked the repository's Python CLI directly.

No adapter, harness wrapper or `PreToolUse` hook was involved. This is evidence
about Core behavior in a real Windows environment, not WorkBuddy native-hook
support. The WorkBuddy version and model were not recorded. This summary does
not represent a new maintainer-run WorkBuddy trial.

## Confirmed findings and regressions

| Finding | Resulting behavior | Regression |
|---|---|---|
| Known destructive vocabulary disappeared under a mismatched dialect; `rm -rf .` became `ALLOW_NOOP` under `cmd` | The unparsed destructive segment becomes non-compensable `UNKNOWN`, producing `BLOCK_UNDETERMINABLE_EFFECT` | [Core CLI tests](https://github.com/mokuyoaxis/agent-guard/blob/main/tests/test_delete.py), `CheckCLI.test_cmd_dialect_cannot_turn_rm_root_delete_into_noop` |
| Windows target separators could be consumed or handled differently depending on the classifier host OS | Supported Windows-native targets normalize consistently; actual target fixtures relocate instead of falling through as a no-op | [Dialect tests](https://github.com/mokuyoaxis/agent-guard/blob/main/tests/test_dialects.py), `DefaultPathUnchanged`; [CLI tests](https://github.com/mokuyoaxis/agent-guard/blob/main/tests/test_delete.py), `WindowsVerbNoDialect` |

The reporter's [final correction](https://github.com/mokuyoaxis/agent-guard/issues/7#issuecomment-5857694703)
narrows the second finding: matching `del` with cmd or `Remove-Item` with
PowerShell already worked. A missing target correctly produces a no-op, so
regressions must create the target and preserve the exact command bytes.

The same report observed working quarantine relocation, manifest and audit
records, and restoration with content intact. The resulting
[Windows Core CI job](https://github.com/mokuyoaxis/agent-guard/blob/main/.github/workflows/ci.yml) covers selected dialect,
CLI, relocation/restore and exact-byte redaction regressions on Windows.
See the [rc1 notes](release-notes-0.2.3-rc1.md) for the fix scope.

## Withdrawn redaction finding

The original third finding was
[withdrawn](https://github.com/mokuyoaxis/agent-guard/issues/7#issuecomment-5857658162).
Git Bash input construction had altered backslashes and a newline before the
payload reached the scanner. Direct Python subprocess input confirmed the
existing Windows path detection and redaction.

The candidate added an exact-byte synthetic-path regression in
[test_exfil_sanitize.py](https://github.com/mokuyoaxis/agent-guard/blob/main/tests/test_exfil_sanitize.py),
`FormatPreservation.test_native_windows_user_path_uses_exact_bytes_and_sanitizes`.
This is an input-contract regression, not a third confirmed exfil fix.

## Evidence boundary

This feedback supports Windows Core/CLI evaluation and the two resulting
fixes. It does not establish a WorkBuddy adapter, mandatory interception of
WorkBuddy tools, all Windows command execution, or protection against an
agent with equal OS privileges. Personal host paths and unrelated local
configuration are omitted from this summary.
