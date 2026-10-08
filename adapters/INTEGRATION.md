# Integrating another coding-agent host

This is a checklist for a user or their agent, **not** a claim that an
unlisted host is supported. Start with the [capability and evidence
matrix](../docs/guides/harness-capabilities.md). A prompt, Skill, CLI call, or
adapter exit code alone does not prove automatic tool interception.

## 1. Identify the actual boundary

Record the host name and version, OS, tool names, shell dialect, configuration
scope, and whether calls can come from subagents. Check the installed host's
hook contract: is there a synchronous event **before** the tool runs, and can
the hook prevent that exact call from executing? Determine the payload fields,
exit/response semantics, approval behavior, matcher rules, timeout, and what
happens when the hook is missing or fails to start. A post-tool event cannot
undo an already executed operation.

Read only the relevant hook configuration. Do not copy authentication
material, private routes, or the full user configuration into model context
or a public report. If the host contract or version is unclear, mark it
`UNVERIFIED` rather than guessing from another host's schema.

## 2. Choose the smallest honest integration

- **Documented host:** choose its adapter README from the
  [integration matrix](../README.md#integration-and-validation-matrix) and
  preserve existing settings. Claude, Kimi, and DSH have distinct host
  decision semantics; their configuration snippets are not interchangeable.
- **Blocking pre-tool event exists:** build a thin adapter that normalizes
  the command, working directory, tool name, and dialect, calls the shared
  Core through `skills/delete-guard/scripts/check.py`, then maps the returned
  decision to the host's documented response. Keep policy in Core, not in a
  second copy of the rule table. Inspect the [Claude](claude/pre_tool_use.py),
  [Kimi](kimi-code/pre_tool_use.py), and [DSH](dsh/lib/index.js) adapters as
  examples of host-specific mapping, not drop-in templates.
- **No verified blocking event:** use the [Skills](../skills) and explicit
  CLI cooperatively. Say `Skill/CLI only`; do not claim native interception.
  A hook limited to some tools does not cover other tools or hidden uploads.

Core `ASK` must reach a real human-approval path or become a refusal for that
call. It must never silently become allow. Treat malformed payloads and
adapter failures conservatively **when the adapter is invoked**; this does
not protect a call when the host never starts the hook. For a shell tool,
`check.py --enforce` may relocate files or snapshot Git state, so use it only
in an authorized execution path. Advisory mode assesses without changing
command targets, but may initialize quarantine/audit metadata and a Git local
exclude rule. Its exit 0 does not authorize a BLOCK or ASK decision. See the
[assessment contract](../skills/delete-guard/references/policy.md#assessment-and-dry-run-side-effects).

## 3. Configure and verify without destructive trials

Before editing user-wide settings, changing security policy, or installing
dependencies, show the exact proposed change and obtain user approval. Keep
the prior configuration and a rollback path. Prefer a disposable project and
a project-scoped configuration where the host supports it.

1. Run adapter contract tests with fixed payloads: allowed call, policy
   refusal, compensable call, malformed payload, `ASK`, and a failed runtime.
   Record the Core reason code and host response separately.
2. In an isolated host session, confirm a harmless call reaches the hook and
   continues. Confirm that a policy-denied **synthetic** sentinel-creation
   call does not execute, checking the sentinel independently of hook logs.
   Do not use deletion of real files, `$HOME`, or a user's repository as a
   test. Use a controlled failure injection to check the path where the hook
   runs but its interpreter fails.
3. Repeat for each claimed tool, host version, root/subagent path, and
   relevant dialect. Test host upgrades again. If a hook is missing,
   mismatched, timed out, or bypassed, record that as a gap; adapter tests
   cannot turn those host boundaries into fail-closed behavior.

`doctor.py` currently supports only `kimi` and `claude`. Its configuration
and local-probe PASS states do **not** prove a live host loaded or enforced a
hook. Do not reuse those states as an unknown-host certification.
For those two hosts, `--check-drift` adds local version/profile comparison and
privacy-safe hook/runtime fingerprints. `STALE` asks for re-testing after a
version change; it does not assert incompatibility. `CURRENT` still does not
promote `host_interception` above `UNVERIFIED`. See the
[host drift contract](../docs/guides/host-drift.md).

An explicit `--live-sentinel` can spend one configured model call to test one
real root-agent Bash path in a private fixture. PASS requires a hashed exact
hook receipt, matching Core audit, host block feedback, and an absent marker;
missing evidence is INCONCLUSIVE. It is not an adversarial sandbox and must
not be generalized to subagents or other tools.

## 4. Report the evidence, not a blanket guarantee

Report the host/version, tool and event, config scope, root/subagent path,
dialect, model setup if one was used, the Core decision, host outcome, and
independent non-execution observation. Label evidence as `CLI`, `Adapter`,
`Hook observed`, or `Enforcement observed` using the
[matrix definitions](../docs/guides/harness-capabilities.md#evidence-levels).
Unknown versions or untested paths remain `UNVERIFIED`; no blocking event is
`Skill/CLI only` or `UNSUPPORTED`. An agent's self-test is useful setup
evidence, not an independent security boundary against that same agent or a
malicious host with equal OS privileges.
