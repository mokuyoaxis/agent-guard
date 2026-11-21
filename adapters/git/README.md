# Local Exfil-guard Git hooks

The development checkout provides two opt-in hooks. `pre-commit` checks UTF-8
text in staged blobs. `commit-msg` checks the proposed message. The hooks use
the installed Exfil-guard rules and leave the index, worktree, and message
file as they found them. [Git hook behavior](https://git-scm.com/docs/githooks)

## Preview and enable

From a working tree, preview its current index with the complete installation.

```sh
python3 /absolute/path/to/agent-guard/adapters/git/hook.py pre-commit --json
git config --get core.hooksPath
git rev-parse --git-path hooks
```

Use the active hook directory, normally `.git/hooks/`. To use a repository-owned
directory instead, set `git config --local core.hooksPath .githooks` after
reviewing any existing hooks. If a hook already exists, chain the corresponding
call and honor both exit statuses. If the hook is new, create an executable
wrapper using the absolute path to the reviewed installation. Example for
`pre-commit` and then for `commit-msg`:

```sh
#!/bin/sh
exec python3 /absolute/path/to/agent-guard/adapters/git/hook.py pre-commit
```

```sh
#!/bin/sh
exec python3 /absolute/path/to/agent-guard/adapters/git/hook.py commit-msg -- "$1"
```

Keep these wrappers executable on POSIX. Git for Windows can use its bundled
shell with a Python executable on PATH. Agent Guard installs no hooks and
sets no Git configuration when the package or Skills are installed.

For projects already using the `pre-commit` framework, add repository-local
entries to `.pre-commit-config.yaml` and enroll both hook types using that
project's setup. [Repository-local hooks](https://pre-commit.com/#repository-local-hooks)

```yaml
repos:
  - repo: local
    hooks:
      - id: agent-guard-staged
        name: Check staged text with Exfil-guard
        entry: python3 /absolute/path/to/agent-guard/adapters/git/hook.py pre-commit
        language: system
        stages: [pre-commit]
        pass_filenames: false
        always_run: true
      - id: agent-guard-commit-message
        name: Check commit message with Exfil-guard
        entry: python3 /absolute/path/to/agent-guard/adapters/git/hook.py commit-msg --
        language: system
        stages: [commit-msg]
```

## Decisions and coverage

`pre-commit` reads added, modified, renamed, copied and type-changed staged
files from Git's object store. It skips symlinks, submodules, reviewed path
exemptions and blobs with a NUL in the first 8 KiB. Text over the scan limit
(4 MiB by default) or with invalid UTF-8 stops the commit. It refuses blocks,
required rewrites and credential questions. A generic absolute path with no
host correlation warns in `NORMAL` mode. Add `--strict-paths` to make that
warning a refusal. `RESTRICTED` also refuses the path. [Rule reference](../../skills/exfil-guard/references/rules.md)

`commit-msg` scans the entire message file under the same size limit. Git can
preserve lines beginning with its comment character and text after a scissors
marker under some cleanup modes. Those lines are checked as well. Verbose
commit templates can contain a diff that Git will later remove. A finding in
that diff can therefore also stop the commit. [Git commit cleanup](https://git-scm.com/docs/git-commit)

Both hooks return 0 to continue and 2 to refuse. Diagnostics contain reason
codes, rule IDs and character offsets. Staged findings use an entry number
from `git diff --cached --raw --diff-filter=ACMRT`, including skipped rows.
Pass `--json` for structured findings and skip counts. `--audit` or
`AGENT_GUARD_EXFIL_AUDIT=1` enables metadata-only audit records. [Audit metadata](../../docs/guides/audit-metadata.md)

These are local commit-time checks. Coverage depends on both hooks being
invoked. Binary contents, existing Git history, remote pushes and other
clients or processes are outside this hook's scan.
