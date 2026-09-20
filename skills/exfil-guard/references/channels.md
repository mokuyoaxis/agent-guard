# exfil-guard egress channel taxonomy (design 4.1)

A channel is defined by exactly **two** facts, and the decision table keys
off both. `rewritable` is what makes `SANITIZE` meaningful (the guard can
hand back a plan); `persistence` is what justifies `BLOCK` (the emission
cannot be taken back).

| Channel | `rewritable` | `persistence` | Reached via | Default |
|---|---|---|---|---|
| `llm-request` | yes (proxy/hook) | remote (third party) | pre-request hook | SANITIZE |
| `file-write` | yes | workspace | pre-write hook / tool arg | SANITIZE |
| `forge-comment` | yes (before send) | public | pre-request hook | SANITIZE |
| `issue-body` | yes | public | pre-request hook | SANITIZE |
| `pr-description` | yes | public | pre-request hook | SANITIZE |
| `git-commit-message` | yes (rewrite argv) | remote history | pre-command hook | **BLOCK** |
| `git-push-payload` | no | **remote** | pre-command hook | **BLOCK** |
| `shell-stdout` | **no** | local transcript/log | pre-command hook | ASK |
| `shell-file-redirect` | yes (file not yet written) | local | pre-command hook | ASK |
| `archive-upload` | yes | remote | pre-command hook | ASK |
| `process-argv` | yes | local (ps/logs) | pre-command hook | ASK |

Rules the table encodes:

1. **`BLOCK` outranks `rewritable`.** A commit message is technically
   rewritable (rewrite the argv), but it lands in immutable history. Design
   4.4 chooses refuse-and-explain over silent rewrite, because the emission
   cannot be undone.
2. **A channel that is neither rewritable nor persistent can only ASK.** It
   cannot un-print. `BLOCK` here would refuse `echo` on a variable a human
   has every right to print.
3. **An unknown channel name is a configuration defect, not "no risk".**
   `check_span.py` returns `BLOCK_OUTPUT_UNSCANNABLE`, never an implicit
   ALLOW.

## Unreachable channels (never implied to be covered)

`hosted-llm-no-proxy`, `agent-tool-call`, `program-internal-output`,
`human-clipboard`. These are **not** in the table above on purpose. Naming
them here is the opposite of a claim: it is the coverage gap, stated so no
reader infers coverage the guard does not have.

Per `docs/threat-model.md`, the guard "may not claim" prevention of
adversarial exfiltration, and per design 4.4 an unreachable channel gets
**no verdict at all**.

## Adapter mapping (design 4.5)

| Core decision | Claude Code (`PreToolUse`) | DSH (`PreToolDecision`) | No-hook harness |
|---|---|---|---|
| ALLOW | exit 0 | run | run |
| SANITIZE | rewrite the tool arg, `permissionDecision: allow` + reason naming the rule (**never the value**) | rewrite the arg | **degrade to ASK/deny**, never silently proceed |
| ASK | `permissionDecision: ask` | native ask → deny | deny + explanation |
| BLOCK | exit 2, stderr to the model | deny + explanation | deny + explanation |

The last column is the honest degradation rule: a harness that cannot
rewrite must not be told it can. `SANITIZE` is not an adapter-mapped
decision the way `RELOCATE`/`SNAPSHOT` are - it applies to a *payload*, not
a command, and the caller holding the payload is the one that applies it.
