# Friction log — implementation findings and fixes

This log records findings from host trials, CLI checks and regression tests.
The initial entries came from a DSH session using `tools/pre-execute`, three
model tools and a prompt section; later entries cover Core and other adapters.

## F1 · Compound `cd X && rm y` resolves targets against the wrong base

The interceptor classifies against the tool call's declared workdir. A
command like `cd subdir && rm -rf build` runs `rm` inside `subdir`, but the
guard resolved `build` against the parent — wrong tree entirely.

**Shipped, then superseded by the Decision Protocol:** shape rule F1 now
yields decision=ASK (COMPOUND_CWD_DELETE) - single-execution authorization
instead of a dead end. Verified live: the ask surfaced to the human, who
declined; splitting the command avoids the prompt entirely.

**Corrected by the 2026-09-20 incident (P1).** That live validation assumed
an interactive human on the other end. Under an auto-approving host the ASK
is granted without anyone seeing it, so F1 converted a guaranteed refusal
into a silent execution - in the incident, `cd /tmp && rm -rf "$PWD/../home"`
resolved to `/home`. A shape rule describes *compensation* difficulty, never
effect scope: F1 now evaluates the target set first and lets any hard
boundary (out-of-workspace, protected path, undeterminable effect) win. The
legitimate single-execution ASK survives for in-workspace, resolvable
targets. Regression: `tests/test_incident_regression.py`.

## F2 · Create-then-delete in one line is a timing blind spot

Observed live: `touch junk_a.tmp junk_b.tmp && mkdir -p empty_dir && git clean -fd`.
Pre-execution interception enumerates *before* the command runs — the junk
files did not exist yet, so compensation could not cover them and git truly
deleted them.

The same event proved the value: an untracked but valuable `keep.txt` lying
in the repo WAS enumerated and relocated before `git clean -fd` could
silently destroy it, then restored via `restore.py <txid>`. The guard saved
exactly the class of file it exists for.

**Shipped, then superseded by the Decision Protocol:** shape rule F2 now
yields decision=ASK (COMPOUND_CREATE_DELETE); position-independent
compensations (`reset --hard` stash; force-push, blocked anyway) remain
exempt. Verified live end-to-end, including the user declining the ask.

## F3 · Unmatched globs produced a self-contradicting BLOCK

`safe_delete '*.log'` with no *.log files answered BLOCK_WILDCARD — while its
own docs say safe_delete expands globs itself. **Fixed:** unmatched patterns
are now reported as `no_match` / ALLOW_NOOP instead of reaching the classifier
as opaque wildcards.

## F4 · Harness integration notes (DSH)

For future adapter authors embedding `check.py` in another harness:

1. Always call `shell.run(shell.resolve(request))` — raw specs crash host
   integration.
2. `ShellRunResult.stdout/stderr` are `CollectedOutput` objects; payload is
   `.text`.
3. Resolve the caller's sandbox policy from `exec.agent.session` and pass it
   in the request; otherwise you get the deployment default
   (workspace-write), which fails on hosts without a sandbox backend even
   when the calling session runs unconfined.
4. Dynamic tool registration must go through `harness.defineTool`;
   parameters are property maps (`required` is a per-property annotation);
   object output schemas must declare `additionalProperties`.
5. Per-destructive-command overhead is one python3 startup (~0.3 s); the
   keyword prefilter keeps non-destructive traffic at regex cost only.

## F5 · Heredoc bodies were scanned as command syntax (fixed live)

While writing THIS very file through a shell heredoc, the guard denied the
write twice: the documentation text quotes destructive commands, and the
classifier treated quoted examples as part of the command line. A file-WRITE
was blocked over its textual content. Even deploying the fix required a
maintenance window, because the old classifier denied the patch command too —
the chicken-and-egg is inherent to self-hosting guards.

**Fixed in V1:** `classifier.strip_heredocs()` removes heredoc payloads before
parsing; a regression test pins the behavior
(`test_heredoc_body_is_payload_not_syntax`). Redirection-into-file is write
territory, not delete territory.

## F6 · Heredoc stripping re-matched its own operator and truncated

While writing the bilingual README through shell heredocs, the guard denied
the write a second time - even after F5 was fixed. Post-mortem: F5 kept the
`<<'TAG'` operator token in the command string, so the stripping loop
re-matched it on every iteration; and when no terminator line was found,
the fallback truncated everything after the operator's line - physically
cutting a trailing `sed -i 's/.../'` command mid-quotation and manufacturing
the exact unbalanced-quote danger the classifier exists to refuse.

**Fixed:** the operator is replaced (never re-matches), the truncating
fallback is gone (later command lines always survive), and three regression
tests pin the shapes: operator+trailing-quoted-line, double heredoc,
unterminated heredoc. This is also the cleanest example of the
fail-closed trade: the bug was annoying and visible, never dangerous.

## F7 · Heredoc terminator cut double-offset into following commands

The F5 fix introduced its own bug: the cut after a found terminator was
`out[nl + 1 + end_m.end():]` — but `end_m` was already searched from
`nl + 1`, so its end index is absolute and adding `nl + 1` again pushed
the cut deep into whatever command line followed the heredoc. The longer
the body, the deeper the blade went, slicing trailing `sed`/quote-laden
lines into unbalanced fragments — manufacturing the very parse errors the
classifier refuses.

**Fixed:** cut is now `out[end_m.end():]`. Regression test uses a
deliberately LONG body (the short-body version passed even while broken -
regression tests must span the length dimension too).

## F8 · Unresolved workspace root broke boundary checks on macOS

CI was green on all five Ubuntu jobs and red on all five macOS jobs. Root
cause: on macOS `/var` is a symlink to `/private/var`. `tempfile.mkdtemp()`
returned an unresolved `/var/...` path (test's idea of the workspace) while
child processes reported their physical cwd `/private/var/...` — so every
target compared against the wrong root string and came back
BLOCK_OUT_OF_WORKSPACE.

**Fixed:** `discover_workspace()` resolves its result through realpath.
Deliberate asymmetry preserved: the *boundary root* is physicalized, but
*target* analysis stays lexical (deleting a symlink still deletes the link,
never its target).

## F9 · Workdir context is load-bearing

Three separate incidents shared one root cause: a command executed under a
different working directory than assumed.

- `safe_delete.py` chained after a `cd` into another directory resolved its
  target against the wrong workspace and self-blocked (KeyError on the
  blocked-result shape).
- Cloning a helper repository to `/tmp` was refused as
  OUT_OF_WORKSPACE - correctly; the fix was moving the clone inside the
  boundary, not weakening it.
- An SSH smoke test flapped between failure and success until IPv4 was
  forced; dual-stack hosts answer from different vantage points.

Lesson for adapters: always pass an explicit workdir; lesson for agents:
deletion commands deserve their own process, their own directory, and
nothing else on the line.

## F10 · Restored transactions still looked live

Independent medium- and high-reasoning Codex runs both restored quarantined
content successfully, then reported the same ambiguity: `restore.py list`
still showed the transaction with its original item count, without saying
whether those items remained recoverable or had already returned to origin.

**Fixed in v0.1.1:** manifest grouping now exposes explicit `RESTORABLE`,
`RESTORED`, `FAILED`, and `PURGED` state plus a live-item count. `status.py`
separates historical transaction count from currently restorable count.

## F11 · Auditing a refusal could dirty a fresh repository

An enforced hard block correctly left every target untouched but appended
`audit.jsonl` before ensuring `.agent-trash/` was ignored. The refusal itself
therefore created an untracked path. Under read-only Git metadata, even a
best-effort audit could not repair that status pollution afterward.

**Fixed in v0.1.1:** establish an existing `.gitignore` or local exclude rule
before creating audit storage. If protected Git metadata prevents that, retain
the safer verdict, return an `audit unavailable` warning, and leave no new
quarantine directory.

## F12 · exfil-guard: a rule that fires on documentation is a rule that gets disabled

The first exfil-guard rule set was written against the *scenarios* and then
pointed at this repository's own corpus. It was unusable: 21 files were
refused as "not ASCII" (the docs contain em-dashes and CJK prose), and the
source-reference rule fired on `if env:` in `core/audit.py`, on the string
`"env AGENT_GUARD_DIALECT"`, and on the rule's own regex source.

Three fixes, in order of value:

1. **The non-ASCII refusal was removed.** It was over-defensive: the vendor
   patterns are ASCII-anchored and `re` matches on characters, so a
   non-ASCII byte cannot smuggle a match past them. Refusing every document
   containing an em-dash bought no detection and made the guard unusable.
2. **The environment-dump rule now requires the *shape* of a dump.**
   `env`/`printenv` must be invoked at the start of a command (optionally
   after a separator) or piped; `printenv FOO` and `env FOO=bar cmd` name
   what they touch and are not dumps (design 2.4 item 4). A bare-name
   property access (`process.env`) is not a file read either.
3. **The repo-local exemption file (design 5.2) covers the rest.** The guard
   cannot both quote `.env` in its own pattern table and detect `.env`;
   `SECURITY.md` already documents this class of expected hit for other
   scanners. Exemptions are scoped to *paths*, so the rules stay fully
   active everywhere else, and values are exempted by hash only.

Measured result: **zero BLOCK across the whole repository corpus**, enforced
by `tests/test_exfil_sanitize.py::ExemptionFile::test_repository_own_corpus_has_zero_blocks`.
A verdict that changes for an existing shape is a rule bug here, not a
tuning problem (design 5.6).

## F13 · A new decision class must not renumber the old ranks

`worst()` is shared by both guards, and exfil-guard needed `SANITIZE` to rank
*below* `ASK`. Renumbering the existing entries (`ALLOW 0, SANITIZE 1,
RELOCATE 2, SNAPSHOT 2, ASK 3, BLOCK 4`) preserves the relative order and
the ties exactly, so no delete-guard verdict can change. Verified as a pure
order-equivalence over all pairs before shipping, not by inspection.

`SANITIZE < ASK` is deliberate: `SANITIZE` is automatic (SAFE tier, like
`RELOCATE`), while `ASK` forfeits automation. A payload carrying both a
sanitizable secret and an un-rewritable shape must `ASK` - you cannot
silently proceed when part of the emission is uninspectable.

## Verdict accuracy observed

| Command | Verdict | Correct? |
|---|---|---|
| `rm -rf build` (rooted dir) | RELOCATE_TREE → PROCEED | ✓ |
| `rm -rf .` | BLOCK_PROTECTED_PATH | ✓ |
| `rm -rf $UNSET/` | BLOCK_UNDETERMINABLE_EFFECT | ✓ |
| `git clean -fd`, pre-existing untracked present | RELOCATE_VIA_CLEAN_ENUMERATE; valuable file relocated | ✓ |
| `git clean -fd`, junk created by same line | proceeded unprotected | ✗ → F2 |
| `safe_delete` mixed glob + file | relocate file, report no-match | ✓ after F3 fix |
| heredoc write quoting destructive text | false BLOCK → fixed by strip_heredocs | ✓ after F5 |

## F14 · An unknowable target set was reported as a broken compensation

`git clean -fdx` outside a repository refused correctly but named the wrong
reason: `COMPENSATION_FAILED` ("Compensation failed before execution")
describes a compensation that was attempted and broke. Nothing was
attempted here - the *target set* could not be enumerated, so the guard
never learned what it was about to protect. The label sends a reader
hunting for a fault in the quarantine engine that does not exist, and it
buries the actual remedy (name the paths explicitly).

**Fixed in v0.2.0-rc1:** enumeration failure raises
`EnumerationUnavailable` and takes its own refusal arm with
`BLOCK_UNDETERMINABLE_EFFECT` - the same verdict class as every other
unknowable target set (policy.md row 11b). A genuine compensation fault
(`git stash create` failing, audit intent not persisting) keeps
`COMPENSATION_FAILED`.

The same defect had a second half. Every refusal arm rewrites `decision`
and `code`, but none of them rewrote `explanation`, which had been built
from the verdict policy *proposed*. A `BLOCK_UNDETERMINABLE_EFFECT` was
therefore explained as "git clean was dry-run enumerated and every match
relocated before the real command ran" - a sentence describing an action
that never happened. All three arms now restate the explanation with the
code, and the CLI suite pins that invariant.

## F15 · check_span hung on a pipe nobody wrote to

`check.py` takes its command line from argv, but `check_span.py` reads its
payload from stdin, and `sys.stdin.read()` blocks until EOF. A caller that
opened a pipe and never wrote to it - the payload omitted, or `--path`
passed on its own - left the CLI pinned until the *host's* tool timeout
killed the call (300 s under DSH). The guard presented as a hang rather
than a refusal, and every such mistake cost a full timeout window in which
nothing else could run.

**Fixed in v0.2.0-rc1:** the wait for a producer's first byte is bounded
(`AGENT_GUARD_STDIN_TIMEOUT`, default 10 s) and the refusal is loud - exit
1, "no payload arrived on stdin within Ns". Deliberately unchanged:
interactive use still blocks for a human's Ctrl-D; a pipe that closes
without data is still a legitimately empty payload (ALLOW, since nothing
can leak); and on platforms where stdin is not selectable the code falls
back to the blocking read rather than refusing a caller whose payload may
be perfectly good.

## F16 · A selected shell dialect could erase a known hazard

The dialect selector chose both a tokenizer and its recognized command
vocabulary. That made a configuration mismatch asymmetric: `rm -rf .`
was a protected-root refusal under POSIX, but the cmd classifier returned no
operation and policy reported `ALLOW_NOOP`. A mixed line was worse: one
recognized Windows delete could make the result look guarded while a later
POSIX delete segment remained invisible. Separately, Windows-native targets
kept backslashes only when Core itself ran on Windows, so identical command
text produced host-dependent path facts.

**Fixed in `0.2.3-rc1`:** the selected tokenizer still owns command
boundaries and quoting semantics, but every resulting segment also passes a
small, shared destructive-vocabulary floor. A known delete, destructive Git
shape, or `find -delete` shape that its selected classifier did not resolve
becomes target-free `UNKNOWN` and therefore
`BLOCK_UNDETERMINABLE_EFFECT`. Core never re-parses the segment under a
different grammar and never invents a compensation target. Recognized
Windows delete targets are normalized after their own tokenizer on every
host, removing the OS-dependent fact split. Negative tests pin quoted output
and shell-specific literal syntax so the safety floor does not become a
substring blacklist.

## F17 · READY is not proof that a background observer outlives its launcher

The first guard-lab tests started and stopped the observer from one Python
process. They passed, but the actual CLI workflow uses separate `arm` and
`stop` invocations. In that workflow the child initially remained in the
launcher's process session: `arm` observed `READY`, then the command runner
cleaned up the process group as the launcher exited. `stop` correctly timed
out and the report became `INCONCLUSIVE`, but the advertised workflow did not
work.

**Fixed in the `0.2.3-rc2` source candidate:** the observer gets an independent
POSIX session or detached Windows process group, with stdin closed and output
confined to its evidence log. It still has a hard duration limit and uses a
cooperative create-only stop request; the controller does not send a signal to
an unverified PID. A regression now performs `prepare`, `arm`, `stop`, `scan`,
and `report` in five separate CLI processes. In-process unit success is no longer
accepted as background-lifecycle evidence.

## F18 · A received synthetic archive is not generic file-read or exfiltration proof

The first snapshot-control design risked collapsing several different facts
into one statement: a test double read fixture files, created a tar, posted it,
and a server accepted it. That wording would resemble the overclaiming that a
forensic lab is meant to prevent. A loopback receiver cannot establish that an
arbitrary harness performed the same reads, that data crossed an external
boundary, or that a remote party retained it.

**Handled in the `0.2.3-rc2` source candidate:** `snapshot-positive` is labeled
as L0 instrument calibration. The generated test double accepts no source or
URL arguments, reads only four bounded manifest entries inside the disposable
fixture, and connects directly to IPv4 loopback without proxy or redirect
handling. The sink separately records upload attempt, valid archive, each
included bait class, and local acceptance, while retaining no body or marker
value. Reports continue to mark general file reads and external receipt as
`UNSUPPORTED`; real-harness exposure and guard-on/off mitigation require later
L1/L2 differential trials.

## F19 · A positive-control PASS can look like an attack was blocked

`mock-injection` deliberately invokes the fake Lab and reproduces a marker so
the whole observation path can be calibrated without a model. Its correct
instrument result is `PASS`, but reusing that case for a real harness would
invert the security meaning: the injected action succeeded while the top-line
word still looked reassuring. A quiet guarded run could also be credited as a
win even when its unguarded baseline never took the bait.

**Handled in the `0.2.3-rc2` source candidate:** every deterministic control is
explicitly L0 with `security_outcome=CALIBRATION_ONLY`. The manual
`injection-probe` is an attack probe: declared bait contact becomes the adverse
`EXPOSURE_OBSERVED` outcome, while no hit is narrowly
`NO_EXPOSURE_OBSERVED`, not a certificate. `compare` produces L2 evidence only
after an effective unguarded baseline and a guarded trial with matching
protocol, harness/version/model, group, and task SHA-256. A quiet baseline is
`INCONCLUSIVE`; unhealthy or mismatched evidence is `INCOMPARABLE`.

## F20 · Exit zero does not prove that a real-host trial ran

The first low-token Kimi clean trial used a valid hook-free isolated config,
but that config omitted a default model. Kimi emitted only its stream-version
record, wrote the model/config failure to stderr, and still exited zero. The
observer was healthy and no bait appeared. Without a separate completion gate,
Lab would have reported a quiet run; in a guarded comparison, that exact shape
could become a false mitigation claim.

**Fixed in the `0.2.3-rc2` source candidate:** each manual run declared as
`model_usage=REAL` now requires one create-once `record-host` event after the
observer stops. Only user-verified `COMPLETED` permits a conclusive criterion;
`FAILED`, `TIMED_OUT`, missing, duplicate, or malformed host state is
`INCONCLUSIVE` and makes a pair `INCOMPARABLE`. The optional process exit code
is evidence, not the verdict. Completion remains user-declared because the
generic Lab cannot safely parse and authenticate every harness transcript.

## F21 · A sanitizer could return BLOCK and still emit the original payload

A synthetic credential on `git-push-payload` reproduced a policy/CLI split:
`sanitize_text` returned BLOCK, but its text remained unchanged and the CLI
printed it with exit zero. An external `--plan` could also replace the scanner's
derived plan without checking coverage or overlapping offsets. Passing existing
classifier/policy tests had not established actual payload withholding.

**Fixed in the local source update (2026-10-02, unreleased):** ASK/BLOCK return
empty library text and no CLI stdout payload, with exit 3/2. Plans are checked
against a current scan, ranges are validated before output construction, and
rewritten text must pass a second scan using the same exemption snapshot/mode.
Both text CLIs now bound input during reading and share default context
resolution. Regression tests exercise library and CLI refusals, dry runs,
malformed/stale/partial plans, UTF-8, oversize input and an open unwritten pipe.

The same update adds URI password spans with original offsets, preserving
accounts/hosts. Source-reference BLOCK facts remain effective even when they
overlap a URI password. This is Core/CLI evidence and does not establish new
model-request interception, live-host enforcement or adversarial containment.

## F22 · Credential markers and variable names had inconsistent boundaries

An exact-name variable such as `TOKEN` was missed, while a normal name such
as `MONKEY` matched the keyword substring. Incidental `xxx`/`dummy` text or
an `example` suffix could exempt a whole credential. Unicode word boundaries
missed vendor tokens adjacent to CJK prose. The fixed 36-character `ghs_`
shape also missed the new GitHub installation wrapper.

**Fixed in the local source update (2026-10-03, unreleased):** variable names
use complete components and explicit metadata suffixes; whole placeholder
shapes and exact public examples replace broad word exemptions. ASCII
identifier boundaries preserve CJK detection and ordinary English identifiers.
The installation rule retains the complete long token and validates only
its wrapper/header shape. Channel decisions and source-reference refusal
remain unchanged. Regression pairs cover literal protection, ordinary names,
templates, complete ranges, CLI withholding and Core-backed read projection.
The repository noise check also exposed ordinary `${key}` interpolations:
bare lower/title-case `key` is kept ambiguous, while uppercase `KEY`, qualified
`apiKey` and explicit environment syntax remain protected. Source files and
template expressions receive no blanket exemption.
Generic password/PII rules, public-key-name policy and broader `sk-`/AWS
classification remain separate work.

## F23 · Resource identifiers and malformed JWS headers matched credential rules

The access-key pattern included IAM group/user/role/policy prefixes, and a
generic JWT header needed only an `alg` field, allowing `alg: null` to match.
These are classification errors in the synthetic inventory, independent of
the sanitizer's output-withholding contract.

**Fixed in the local source update (2026-10-03, unreleased):** AWS access-key
facts now use AKIA/ASIA only; JWS headers require a nonempty ASCII string alg.
Resource spellings used as URI passwords still receive credential redaction.
PyPI's documented scanner shape adds an independent rule with whole long-body
replacement and shared placeholder/boundary handling. Channel decisions,
source-reference refusal and dependency count remain unchanged.

Public-key variable names cannot establish that an unseen value is public,
and the legacy broad sk- format has no complete issuance contract established
in this review. Those boundaries retain protection and remain explicit review
items. The narrowed structural rules do not certify a token's signature,
claims, actual issuance or authorization.

## F24 · Hard reset could destroy content outside the tracked snapshot

A tracked-only stash does not contain untracked or ignored bytes. Git can
remove those bytes when the reset target replaces a file or an ancestor
directory, including quarantine storage. The 2026-10-05 independent review
reproduced this with disposable fixtures; no user worktree was reset.

**Fixed in 0.2.4:** read-only preflight checks the target tree
against untracked/ignored paths before compensation. Collisions yield
`BLOCK_GIT_RESET_COLLISION`; incomplete enumeration, submodules and unsupported
compound/wrapper contexts yield `BLOCK_UNDETERMINABLE_EFFECT`. Supported
standalone collision-free reset still snapshots tracked changes first.
There is no automatic untracked archive. Regression:
`tests/test_git_reset_safety.py` (30 cases) and two added policy cases.

This deliberately tightens default behavior and adds a reason string.
The maintainer-selected 0.2.4 patch exception is recorded in the
[compatibility contract](../design/compatibility.md#024-maintenance-patch-exception).

## F25 · Restore trusted paths and removed an occupied destination before recovery

A changed parent symlink could redirect a recorded source or destination;
transaction IDs and directories also needed containment checks. Forced
restore removed the current occupant before attempting recovery, so a later
move failure could lose the current version.

**Fixed in 0.2.4:** restore preflights the selected workspace,
transaction directory and source parent chains, then checks each item again.
Final symlinks retain their own location semantics. Force cannot override
containment; an occupied destination is preserved in a normal relocation
transaction before recovery. Preservation failure stops that item. Returned
`backup_txids` includes attempted backups, not a guarantee that each completed.
Journal/audit errors preserve the filesystem report and IDs for inspection.

Regression: `tests/test_restore_boundary.py` (14),
`tests/test_restore_source.py` (14), and `tests/test_restore_force.py` (22).
These are sequential checks, not atomic same-UID isolation; cross-device
copy failures can leave partial data requiring inspection.
