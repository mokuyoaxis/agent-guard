# DSH compatibility

The default deletion adapter in Agent Guard 0.2.6 admits three
exact DSH versions:

- `0.1.5-rc.1`
- `0.2.0-rc.2`
- `0.2.1-alpha.4`

Version 0.2.5 retains its two-version declaration. For alpha.4, upgrade to
0.2.6 or use a reviewed checkout containing the new declaration.

## Separate feature boundaries

| Feature | Admitted hosts | Additional conditions |
|---|---|---|
| Default deletion interception, recovery tools and prompt guidance | The three exact versions above | Python 3.9+ and Git; successful plugin activation |
| Optional native text-read redaction | `0.1.5-rc.1`, `0.2.0-rc.2` | Node 22, reviewed SDK file hashes, tool/provider shape and explicit opt-in |
| Lab native capture and paired review | `0.1.5-rc.1`, `0.2.0-rc.2` | Separate native-format/config contracts and operator review |

Alpha.4 support concerns the default deletion adapter. It does not admit
alpha.4 to the read prototype or Lab. The other versions examined during SDK
research remain candidates, including alpha.1–alpha.3; adjacent prereleases
and future versions are not implicitly supported.

## Declarations and loading

[`adapters/dsh/compatibility.json`](../../adapters/dsh/compatibility.json)
records the default adapter's exact tested versions and scope. Manifest tests
require its list to match all three declarations in `package.json`:

- Top-level `engines.dsh`, used preferentially by alpha.4's loader.
- Optional `peerDependencies["@deepseek-ai/dsh"]`, used by the older rc.2
  compatibility checker. npm does not install this optional peer for the user.
- Nested `dsh.engines.dsh`, retained as bundle metadata.

The nested field alone did not enforce host-version admission. The two
reviewed compatibility helpers accept the three declared versions and reject
the tested unlisted versions without exemptions. Older loaders without that
helper still require the user to check this list and successful activation.
No adapter fallback bypasses a host compatibility rejection.

Resolve SDK packages from the selected DSH package's module context: SDKs may
be hoisted, and their versions can differ from the CLI root. The reviewed
legacy installation pairs root `0.1.5-rc.1` with key SDK `0.1.5-rc.2` artifacts.

## Evidence and limits

The [alpha.4 report](../reports/test-report-dsh-alpha4.md) records real CLI
Profile/Boot, native Bash, recovery and scripted root/child-agent checks.
The same source was checked on both previously admitted versions. These runs
made no external model requests and used isolated homes/profiles/workspaces.

The existing shell bridge already selects `execute().result()` when available
and retains the legacy `run()` path. Verdicts, compensation, rm wildcard
refusal and recovery formats are unchanged. A checker failure after the hook
registers denies the matched call. Missing/failed plugin initialization still
does not stop the host's other tools.

Native policy-forwarding checks used `danger-full-access`; unit coverage also
checks distinct root/child policies. This does not certify OS sandbox
confinement. Interactive approval UI, arbitrary PTC calls, concurrent agents,
other tools, GUI activation and real-model behavior require separate evidence.
`agent_guard_status` continues to distinguish local registration from live
interception and does not promote itself to a host certificate.
