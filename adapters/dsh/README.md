# agent-guard adapter for DeepSeek Harness

Official DSH integration, shipped as a first-class `dsh-plugin` bundle.
The decision rules are NOT implemented here - this adapter translates the
Decision Protocol onto DSH's native mechanisms and delegates every verdict
to the shared Python core (`skills/delete-guard/scripts/check.py`) that
ships inside this very package.

## Install

```bash
dsh plugin --profile <your-profile> add @mokuyoaxis/agent-guard
```

## What gets registered

| Contribution | Mechanism | Behavior |
|---|---|---|
| Interception | `tools/pre-execute` waterfall | destructive-looking bash runs through `check.py --enforce` first; ALLOW proceeds (compensation applied), ASK escalates once to the human, BLOCK denies with explanation and remediation |
| `agent_guard_safe_delete` | model tool | recoverable delete with explicit glob expansion and manifest |
| `agent_guard_restore` | model tool | list / non-overwriting restore of quarantine transactions; forced overwrite remains human-only CLI functionality |
| `agent_guard_status` | model tool | mode, usage, retention view, recent decisions |
| Prompt section | system prompt (order 105) | deletion discipline for the model |

## Configuration (cordis.patch.yml row)

| Key | Default | Meaning |
|---|---|---|
| `repoRoot` | `""` (this package) | absolute path to a live agent-guard checkout for development |
| `defaultCwd` | executor default | working directory for guard invocations |
| `promptSection` | `true` | register the deletion-discipline section |
| `sectionOrder` | `105` | system-prompt section order |
| `dialect` | `""` (posix) | shell dialect for guard invocations: `posix`, `cmd`, or `powershell` |

## Shell dialect

Interception forwards a shell dialect to `check.py` so Windows-native
command lines are lexed with the right rules. Explicitly invalid plugin
configuration (including unknown field names) is rejected by Standard Schema
validation; only omitted fields receive defaults. Precedence for valid
configuration: the tool argument (`dialect`), then `AGENT_GUARD_DIALECT`,
then the `dialect` config
key, then `posix`. The fast prefilter screens both POSIX and Windows
vocabularies regardless of dialect. An unrecognised tool/environment selector
is forwarded verbatim rather than swapped for POSIX, so `check.py` returns
`BLOCK_DIALECT_UNKNOWN` and the call is denied with that code.

## Notes

- The caller's sandbox policy is inherited per-invocation (`exec.agent.session`), so guard subprocesses never gain privileges the calling agent lacks.
- Once the adapter has registered its pre-execute listener, Core process and
  output failures fail closed (deny). An absent/disabled bundle, a changed host
  event contract, or failure before listener registration cannot deny a call
  the adapter never receives.
- The Python core requires `python3` and `git` on PATH.
- Until the scoped npm package is actually published, install from a GitHub
  checkout or release tarball rather than treating the registry command above
  as available.
- DSH `0.1.5-rc.1` package loading and one harmless execution-level `bash`
  block are independently verified in the
  [bounded host report](../../docs/test-report-dsh-0.1.5-rc.1.md). Model,
  subagent, concurrency, ASK UI and non-`bash` paths remain separate tests.
