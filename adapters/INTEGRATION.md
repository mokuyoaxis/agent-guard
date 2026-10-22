# Integrating another coding-agent host

Use this checklist to configure Agent Guard for your installed coding-agent
host and version. The [capability matrix](../docs/guides/harness-capabilities.md)
records which paths have been tested. A prompt, Skill, CLI call or adapter
exit code alone does not prove automatic tool interception.

Give the host's **official coding agent** the
[setup prompt](../docs/guides/getting-started.md#set-up-a-coding-agent) and this
guide. It should check the installed version and official hook documentation,
reuse a matching adapter where possible, and prefer a synchronous hook that
can block the call before execution. The project maintains the shared Core,
CLI/Decision Protocol, reference adapters and these verification steps.

We currently focus on **DSH and Kimi Code**. The Claude Code adapter and its
tested-version records remain available; testing new Claude versions is
paused. For other hosts, including Codex, create only the bridge and
configuration needed locally, within the user's authorized scope. Call the
shared Core rather than copying its policy. A local setup does not extend
the project's tested-version list.

**Instructions for the AI agent doing the setup:** Read this guide before
editing settings or writing a bridge, including when an adapter already
exists. Then read the relevant Skill contracts and adapter guide, inspect the
installed host, and check its official hook documentation. Complete the
checks below and report their actual results; a copied configuration is not
an acceptance result. Reuse Core rather than duplicating its policy rules.

## 1. Identify the actual boundary

Record the host name and version, OS, tool names, shell dialect, configuration
scope, and whether calls can come from subagents. Check the installed host's
hook contract: is there a synchronous event **before** the tool runs, and can
the hook prevent that exact call from executing? Determine the payload fields,
exit/response semantics, approval behavior, matcher rules, timeout, and what
happens when the hook is missing or fails to start. A post-tool event cannot
undo an already executed operation.

Check whether the project or individual hook requires trust review. Complete
the host's normal review within the authorized scope, or report activation
as pending; a configured but untrusted hook is not active protection.

Read only the relevant hook configuration. Do not copy authentication
material, private routes, or the full user configuration into model context
or a public report. If the host contract or version is unclear, mark it
`UNVERIFIED` rather than guessing from another host's schema.

<a id="2-choose-the-smallest-honest-integration"></a>

## 2. Choose an integration

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

Verify recovery feedback separately from the allow/deny decision. A successful
hook can authorize a tool while the host omits its explanation from the next
model request. Kimi's [optional Stop feedback](kimi-code/README.md#optional-recovery-feedback-unreleased-checkout-addition)
is part of the unpublished [0.2.6 preparation](../docs/releases/release-notes-0.2.6.md);
it adds a response after
the initial answer. A pre-tool doctor PASS does not verify that later delivery.

## 3. Configure and verify without destructive trials

Stay within the user's already authorized paths and configuration scope.
For additional user-wide settings, security-policy changes, or dependencies
beyond that scope, show the exact proposed change and obtain approval first.
Keep the prior configuration and a rollback path. Prefer a disposable project
and a project-scoped configuration where the host supports it.

Keep the user's selected account and model provider. If model authentication
is unavailable, complete the available local checks and record which live
checks could not run.

Choose checks that are present in the selected root. A full source checkout
includes `tests/`; the npm package omits that directory and provides the
CLIs, adapters and doctor instead. For a packaged Kimi/Claude setup, run the
local doctor probes and any additional fixed-payload checks needed below.
Report the source unit suite as `NOT_INCLUDED` when absent; it is a separate
validation step requiring a full checkout.

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

Keep configuration and fresh-host verification as separate stages. When
hooks load at startup, finish the settings and local probes, then report the
paths, results and pending activation before opening the next authorized
host process. Repeating local probes cannot turn `host_interception` or
`model_delivery` into PASS. At a step limit, leave a checkpoint with completed
work and remaining checks so the next session can continue without repeating
the setup.

`doctor.py` currently supports only `kimi` and `claude`. Its configuration
and local-probe PASS states do **not** prove a live host loaded or enforced a
hook. Do not reuse those states as an unknown-host certification.
The unpublished 0.2.6 Kimi doctor also reports optional Stop under `feedback`.
Its configuration/local probe is separate from PreToolUse, and
`model_delivery` remains `UNVERIFIED`. A missing Stop is optional; an invalid
or locally failing configured Stop exits 1. The importable `check_kimi()`
wrapper retains its original PreToolUse-only scope for existing Lab callers.
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

<a id="4-report-the-evidence-not-a-blanket-guarantee"></a>

## 4. Report results

Report the host/version, tool and event, config scope, root/subagent path,
dialect, model setup if one was used, the Core decision, host outcome, and
independent non-execution observation. Label evidence as `CLI`, `Adapter`,
`Hook observed`, or `Enforcement observed` using the
[matrix definitions](../docs/guides/harness-capabilities.md#evidence-levels).
Unknown versions or untested paths remain `UNVERIFIED`; no blocking event is
`Skill/CLI only` or `UNSUPPORTED`. An agent's self-test is useful setup
evidence, not an independent security boundary against that same agent or a
malicious host with equal OS privileges.

Leave a short handoff so the user or next session can continue:

```text
Host/version, OS and shell dialect:
Agent Guard root/version:
Changed configuration/bridge paths and rollback:
Hook event/tools, trust and activation state:
Core decision mappings, including ASK:
Local checks and evidence locations:
Host interception and recovery-feedback observations:
Unverified checks and next action:
```
