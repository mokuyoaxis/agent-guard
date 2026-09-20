"""Decision Protocol: turn classified facts into decisions.

The stable cross-harness interface is NOT allow/block. It is:

    Effect -> Classifier -> Policy -> Decision
                                    + ReasonCode
                                    + Explanation
                                    + RecoveryPlan (payload)

Decision classes (docs/architecture.md):

    ALLOW      safe to run as-is (noop / provably regenerable / trash GC)
    RELOCATE   compensate by quarantine, then run
    SNAPSHOT   compensate by git snapshot, then run
    SANITIZE   rewrite the PAYLOAD (not the command) before it is emitted:
               the degraded compensation of an irreversible channel. It
               restores nothing; it only records what was removed
               (core/redaction.py, exfil-guard). Ranks below ASK: it is
               automatic (SAFE tier), while ASK forfeits automation
    ASK        guard cannot safely automate, but user intent may be legit:
               single-execution authorization (ASK_ONCE, never a rule
               exemption); adapters map to their native ask, or degrade to
               BLOCK with the explanation
    BLOCK      refuse (policy violations and true effect-uncertainty)

Interaction tiers: SAFE (auto) / AMBIGUOUS (occasional ASK) / FORBIDDEN
(BLOCK). Hard boundaries NEVER ask - a user who truly wants them acts
outside the agent, or changes configuration/mode explicitly.

Authorization model:

    workspace: policy, artifact patterns, quarantine, audit
    session/agent: NORMAL / RESTRICTED capability (one agent's veto must
    not degrade concurrent agents - the guard must not become a DoS source)

    NORMAL --(user veto / high-risk event)--> RESTRICTED
    RESTRICTED --(human explicit action ONLY)--> NORMAL

The portable on-disk state is advisory by construction; harness adapters
SHOULD hold the authoritative mode in host memory. force_mode() exists for
host-side callers (slash commands, settings) and skips the TTY guard on
purpose - agents never get it as a model tool.

Guiding rule: uncertainty increases restriction. ASK is reserved for
well-understood operations whose safe *automation* is unavailable - never
for operations whose *effect* is unknown.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import STATE_NAME, TRASH_DIRNAME
from . import redaction
from .redaction import get_channel
from .classifier import (
    KIND_FS_DELETE,
    KIND_GIT_CLEAN,
    KIND_GIT_DISCARD,
    KIND_GIT_PUSH_FORCE,
    KIND_GIT_RESET_HARD,
    KIND_UNKNOWN,
    PathSpec,
    OpSpec,
    _physical,
    _physical_keep_final,
    classify_paths,
)

# --------------------------------------------------------- decision classes

DECISION_ALLOW = "ALLOW"
DECISION_RELOCATE = "RELOCATE"
DECISION_SNAPSHOT = "SNAPSHOT"
DECISION_SANITIZE = "SANITIZE"
DECISION_ASK = "ASK"
DECISION_BLOCK = "BLOCK"

# ------------------------------------------------------------- reason codes

CODE_ALLOW_NOOP = "ALLOW_NOOP"
CODE_ALLOW_REGENERABLE = "ALLOW_REGENERABLE"
CODE_ALLOW_TRASH_GC = "ALLOW_TRASH_GC"
CODE_RELOCATE_PATHS = "RELOCATE_PATHS"
CODE_RELOCATE_TREE = "RELOCATE_TREE"
CODE_RELOCATE_NARROW = "RELOCATE_NARROW"
CODE_RELOCATE_VIA_CLEAN_ENUMERATE = "RELOCATE_VIA_CLEAN_ENUMERATE"
CODE_SNAPSHOT_GIT_STASH = "SNAPSHOT_GIT_STASH"
CODE_ASK_COMPOUND_CWD_DELETE = "COMPOUND_CWD_DELETE"       # F1
CODE_ASK_COMPOUND_CREATE_DELETE = "COMPOUND_CREATE_DELETE"  # F2
CODE_BLOCK_UNDETERMINABLE_EFFECT = "BLOCK_UNDETERMINABLE_EFFECT"
CODE_BLOCK_OUT_OF_WORKSPACE = "BLOCK_OUT_OF_WORKSPACE"
CODE_BLOCK_PROTECTED_PATH = "BLOCK_PROTECTED_PATH"
CODE_BLOCK_WILDCARD = "BLOCK_WILDCARD"
CODE_BLOCK_RESTRICTED_MODE = "BLOCK_RESTRICTED_MODE"
CODE_BLOCK_FORCE_PUSH = "BLOCK_FORCE_PUSH"
CODE_BLOCK_DIALECT_UNKNOWN = "BLOCK_DIALECT_UNKNOWN"        # config
CODE_BLOCK_DIALECT_INVALID = "BLOCK_DIALECT_INVALID"        # config
CODE_BLOCK_RELOCATE_FAILED_STORAGE = "RELOCATE_FAILED_STORAGE"
# exfil-guard (span decisions). Rule ids (`secret/*`, `path/*`) are payload,
# never verdict codes; these codes name the *disposition*, not the pattern.
CODE_SANITIZE_SECRET_REDACT = "SANITIZE_SECRET_REDACT"
CODE_SANITIZE_PATH_REWRITE = "SANITIZE_PATH_REWRITE"
CODE_BLOCK_SECRET_EMISSION = "BLOCK_SECRET_EMISSION"
CODE_BLOCK_PATH_EMISSION = "BLOCK_PATH_EMISSION"
CODE_BLOCK_SECRET_SOURCE_DUMP = "BLOCK_SECRET_SOURCE_DUMP"
CODE_BLOCK_OUTPUT_UNSCANNABLE = "BLOCK_OUTPUT_UNSCANNABLE"
CODE_ASK_SECRET_EMISSION = "ASK_SECRET_EMISSION"
CODE_ASK_PATH_EMISSION = "ASK_PATH_EMISSION"
CODE_ALLOW_SECRET_PLACEHOLDER = "ALLOW_SECRET_PLACEHOLDER"
CODE_ALLOW_PATH_IN_WORKSPACE = "ALLOW_PATH_IN_WORKSPACE"
CODE_BLOCK_COMPENSATION_FAILED = "COMPENSATION_FAILED"

# Human-facing one-liners: why the guard cannot just do it (or did do it).
EXPLANATIONS: Dict[str, str] = {
    CODE_ALLOW_NOOP: "Nothing to delete; command may run unchanged.",
    CODE_ALLOW_REGENERABLE: "All targets are git-ignored and match known "
                            "artifact patterns; deleted directly.",
    CODE_ALLOW_TRASH_GC: "Targets live inside the quarantine; housekeeping "
                         "is permitted.",
    CODE_RELOCATE_PATHS: "Named targets were relocated to quarantine before "
                         "execution; restorable via txid.",
    CODE_RELOCATE_TREE: "Rooted directory relocated to quarantine whole; "
                        "restorable via txid.",
    CODE_RELOCATE_NARROW: "Restricted mode: named files relocated to "
                          "quarantine.",
    CODE_RELOCATE_VIA_CLEAN_ENUMERATE: "git clean was dry-run enumerated and "
                                       "every match relocated before the "
                                       "real command ran.",
    CODE_SNAPSHOT_GIT_STASH: "Tracked modifications snapshotted as a stash "
                             "before the command; apply it to recover.",
    CODE_ASK_COMPOUND_CWD_DELETE: "This line combines a working-directory "
                                  "change with deletion, so the guard "
                                  "cannot safely relocate the target before "
                                  "execution. Allow once to run it as-is, "
                                  "or split the deletion into its own "
                                  "command.",
    CODE_ASK_COMPOUND_CREATE_DELETE: "This line creates files and then "
                                     "destroys them, so pre-execution "
                                     "compensation cannot see the targets. "
                                     "Allow once to run it as-is, or split "
                                     "the deletion into its own command.",
    CODE_BLOCK_UNDETERMINABLE_EFFECT: "The effect of this command cannot be "
                                      "determined statically; allowing it "
                                      "would forfeit the guard's core "
                                      "guarantee. Restate with explicit "
                                      "paths.",
    CODE_BLOCK_OUT_OF_WORKSPACE: "Target lies outside the workspace "
                                 "boundary. Hard boundary - not askable.",
    CODE_BLOCK_PROTECTED_PATH: "Target is the workspace root or git "
                               "metadata. Hard boundary - not askable.",
    CODE_BLOCK_WILDCARD: "Glob target sets are opaque; use safe_delete, "
                         "which expands globs explicitly.",
    CODE_BLOCK_RESTRICTED_MODE: "This session is in RESTRICTED mode; the "
                                "operation exceeds its remaining "
                                "capability.",
    CODE_BLOCK_FORCE_PUSH: "Remote history destruction is never automated "
                           "by the guard. Hard boundary - not askable.",
    CODE_BLOCK_DIALECT_UNKNOWN: "The requested command dialect is not "
                                 "recognised, so the command line was never "
                                 "classified. This is a configuration "
                                 "problem, not one-off authorization: fix the "
                                 "dialect selector (posix | cmd | powershell) "
                                 "and retry.",
    CODE_BLOCK_DIALECT_INVALID: "The requested command dialect is malformed "
                                "(expected a single dialect name such as "
                                "posix, cmd or powershell), so the command "
                                "line was never classified. Fix the selector "
                                "and retry.",
    CODE_BLOCK_RELOCATE_FAILED_STORAGE: "Quarantine could not accept the "
                                        "relocation (storage). Refusing to "
                                        "fall back to permanent deletion.",
    CODE_BLOCK_COMPENSATION_FAILED: "Compensation failed before execution; "
                                    "refusing to proceed unrecoverable.",
    CODE_SANITIZE_SECRET_REDACT: "A credential would leave the machine on this "
                                 "payload; it is replaced in place before "
                                 "emission. Apply the redaction plan to the "
                                 "payload you hold - the guard never rewrites "
                                 "it for you.",
    CODE_SANITIZE_PATH_REWRITE: "A host-identifying path would leave the "
                                "machine on this payload; it is rewritten to a "
                                "stable placeholder before emission. Apply the "
                                "redaction plan to the payload you hold.",
    CODE_BLOCK_SECRET_EMISSION: "A credential would enter an immutable or "
                                "remote history on this channel, where it "
                                "cannot be taken back. Remove it and retry "
                                "(amend the message / rewrite before pushing).",
    CODE_BLOCK_PATH_EMISSION: "A host-identifying path would enter an "
                              "immutable or remote history on this channel. "
                              "Remove it and retry.",
    CODE_BLOCK_SECRET_SOURCE_DUMP: "This payload reads a secret source (a "
                                   "credential file, or the whole "
                                   "environment) whose content cannot be "
                                   "scanned or rewritten here. Fail closed: "
                                   "such a dump is not a clean emission.",
    CODE_BLOCK_OUTPUT_UNSCANNABLE: "The payload was never scanned (scanner "
                                   "unavailable or the text exceeds the "
                                   "supported size). An unscanned egress is "
                                   "not a clean egress.",
    CODE_ASK_SECRET_EMISSION: "A credential would leave the machine on a "
                              "channel the guard cannot rewrite and cannot "
                              "take back (it cannot un-print). Allow once to "
                              "proceed as-is, or remove the value first.",
    CODE_ASK_PATH_EMISSION: "A host-identifying path would leave the machine "
                            "on a channel the guard cannot rewrite. Low "
                            "severity; allow once to proceed as-is.",
    CODE_ALLOW_SECRET_PLACEHOLDER: "Every match is a documented placeholder "
                                   "or an already-redacted marker; nothing to "
                                   "remove.",
    CODE_ALLOW_PATH_IN_WORKSPACE: "Every path lies inside the workspace, so "
                                  "it identifies no host.",
}

# ------------------------------------------------------------------- verdict


@dataclass
class Verdict:
    decision: str                     # DECISION_* constant
    code: str                         # stable machine-readable reason code
    reasons: List[str] = field(default_factory=list)   # technical detail
    explanation: str = ""             # human-facing one-liner
    payload: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.explanation:
            self.explanation = EXPLANATIONS.get(
                self.code, "Guard decision: " + self.code)

    @property
    def blocked(self) -> bool:
        return self.decision == DECISION_BLOCK

    @property
    def asks(self) -> bool:
        return self.decision == DECISION_ASK

    @property
    def sanitizes(self) -> bool:
        return self.decision == DECISION_SANITIZE


def worst(verdicts: List[Verdict]) -> Verdict:
    """Aggregate: BLOCK > ASK > RELOCATE/SNAPSHOT > SANITIZE > ALLOW.

    SANITIZE ranks *below* ASK deliberately (design 1.3): it is automatic
    (SAFE tier, like RELOCATE/SNAPSHOT) while ASK forfeits automation. A
    payload carrying both a sanitizable secret and an uninspectable,
    un-rewritable shape must ASK - you cannot silently proceed when part of
    the emission cannot be inspected.
    """
    rank = {DECISION_ALLOW: 0, DECISION_SANITIZE: 1, DECISION_RELOCATE: 2,
            DECISION_SNAPSHOT: 2, DECISION_ASK: 3, DECISION_BLOCK: 4}
    if not verdicts:
        return Verdict(DECISION_ALLOW, CODE_ALLOW_NOOP,
                       explanation=EXPLANATIONS[CODE_ALLOW_NOOP])
    return max(verdicts, key=lambda v: rank[v.decision])

# --------------------------------------------------------------------- modes

MODE_NORMAL = "NORMAL"
MODE_RESTRICTED = "RESTRICTED"

_SESSION_SANITIZE_RE = re.compile(r"[^A-Za-z0-9._-]")


class AuthorizationRequired(RuntimeError):
    """Raised when a mode change requires a human and none is present."""


def _session_key(session: Optional[str]) -> str:
    sid = session or os.environ.get("AGENT_GUARD_SESSION") or ""
    sid = _SESSION_SANITIZE_RE.sub("_", sid)[:80]
    return sid


def state_path(trash_root: str, session: Optional[str] = None) -> str:
    """Session-scoped authorization state; legacy file when sessionless."""
    sid = _session_key(session)
    if sid:
        return os.path.join(trash_root, "sessions", sid + ".json")
    return os.path.join(trash_root, STATE_NAME)


def load_mode(trash_root: str, session: Optional[str] = None) -> Dict[str, Any]:
    """Read one session's capability; corrupt/missing falls back to NORMAL.

    Sessions start NORMAL by design: authorization is a per-agent
    capability, not workspace pollution (friction: one degraded agent must
    not DoS concurrent agents).
    """
    path = state_path(trash_root, session)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        mode = data.get("mode")
        if mode in (MODE_NORMAL, MODE_RESTRICTED):
            return {"mode": mode, "since": data.get("since"),
                    "set_by": data.get("set_by"), "degraded": False}
    except (OSError, json.JSONDecodeError, AttributeError):
        pass
    return {"mode": MODE_NORMAL, "since": None, "set_by": None,
            "degraded": False}


def _save_mode(trash_root: str, mode: str, actor: str,
               session: Optional[str]) -> Dict[str, Any]:
    path = state_path(trash_root, session)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    state = {"mode": mode,
             "since": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
             "set_by": actor}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return state


def request_mode(trash_root: str, new_mode: str, actor: str = "unknown",
                 session: Optional[str] = None) -> Dict[str, Any]:
    """Change one session's capability, enforcing the one-way downgrade.

    Downgrade is always safe. Promotion demands an interactive human
    terminal - agents running non-interactively can never satisfy it.
    Host-side callers with real authority use force_mode() instead.
    """
    current = load_mode(trash_root, session)
    if new_mode == current["mode"]:
        return current
    if new_mode == MODE_RESTRICTED:
        return _save_mode(trash_root, MODE_RESTRICTED, actor, session)
    if new_mode == MODE_NORMAL:
        if not sys.stdin.isatty():
            raise AuthorizationRequired(
                "RESTRICTED -> NORMAL requires an interactive human "
                "terminal or a host-side command; the agent cannot "
                "promote itself.")
        return _save_mode(trash_root, MODE_NORMAL, actor, session)
    raise ValueError(f"unknown mode: {new_mode}")


def force_mode(trash_root: str, new_mode: str, actor: str,
               session: Optional[str] = None) -> Dict[str, Any]:
    """Host-side authority entry point (slash commands, settings UI).

    Deliberately skips the TTY guard: the caller is the host, not the
    agent. Never expose this as a model tool.
    """
    current = load_mode(trash_root, session)
    if new_mode == current["mode"]:
        return current
    return _save_mode(trash_root, new_mode, actor, session)

# -------------------------------------------------------------------- config

DEFAULT_ARTIFACT_PATTERNS = [
    "node_modules", "dist", "build", "out", "target", "__pycache__",
    ".cache", "coverage", ".next", ".nuxt", ".venv", "venv",
    ".pytest_cache", ".mypy_cache", ".tox", ".turbo", ".parcel-cache",
    "*.pyc", "*.pyo", "*.egg-info",
]


@dataclass
class GuardConfig:
    artifact_patterns: List[str] = field(
        default_factory=lambda: list(DEFAULT_ARTIFACT_PATTERNS))
    allow_regenerable: bool = True

    @classmethod
    def from_env(cls) -> "GuardConfig":
        cfg = cls()
        extra = os.environ.get("AGENT_GUARD_ARTIFACTS")
        if extra:
            cfg.artifact_patterns.extend(
                p for p in extra.split(os.pathsep) if p)
        if os.environ.get("AGENT_GUARD_ALLOW_REGENERABLE", "").strip() in (
                "0", "false", "no"):
            cfg.allow_regenerable = False
        return cfg


@dataclass
class PolicyContext:
    workspace: str
    trash_root: str
    base_dir: str
    mode: str = MODE_NORMAL
    config: GuardConfig = field(default_factory=GuardConfig.from_env)

# ------------------------------------------------------------- regenerability


def _matches_artifact(rel: str, patterns: List[str]) -> bool:
    parts = rel.split(os.sep)
    for pat in patterns:
        if any(fnmatch.fnmatch(part, pat) for part in parts):
            return True
        if fnmatch.fnmatch(rel, pat):
            return True
    return False


def is_git_ignored(workspace: str, path: str) -> bool:
    """True when git ignores path. Outside a work tree -> NOT ignored
    (uncertainty increases restriction)."""
    try:
        proc = subprocess.run(
            ["git", "-C", workspace, "check-ignore", "-q", "--", path],
            capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def regenerable(specs: List[PathSpec], ctx: PolicyContext) -> bool:
    """True when every target is git-ignored AND matches an artifact pattern.

    The workspace-relative path used for artifact matching is computed from
    PHYSICAL spellings on BOTH sides (F9c). ``ctx.workspace`` comes from
    ``discover_workspace()`` and is realpath-resolved, while
    ``PathSpec.resolved`` deliberately keeps the caller's LEXICAL spelling
    (MR !7) - mixing the two yields a ``..``-laden relpath whenever the
    workspace sits under a symlinked ancestor (macOS: /var -> /private/var,
    /tmp -> /private/tmp), so the segmented artifact match silently failed
    and the fast RELOCATE path degraded into a full compensation.

    The FINAL component stays verbatim (``_physical_keep_final``): artifact
    matching is about where the ENTRY lives, so a target that is itself a
    symlink is classified by the link's own location, never by its target
    (the same choice relocation makes for the quarantine layout).

    The effect direction is conservative either way - a false negative only
    buys a relocation - and this is a pure spelling fix: patterns, defaults
    and the decision table are untouched. ``spec.resolved`` is still handed
    to git unchanged.
    """
    if not ctx.config.allow_regenerable:
        return False
    for spec in specs:
        if not spec.resolved or not spec.exists:
            return False
        rel = os.path.relpath(_physical_keep_final(spec.resolved),
                              _physical(ctx.workspace))
        if not _matches_artifact(rel, ctx.config.artifact_patterns):
            return False
        if not is_git_ignored(ctx.workspace, spec.resolved):
            return False
    return True

# ------------------------------------------------------------------ decisions


def _block_undeterminable_effect(spec: OpSpec) -> Verdict:
    return Verdict(
        decision=DECISION_BLOCK,
        code=CODE_BLOCK_UNDETERMINABLE_EFFECT,
        reasons=["effect cannot be determined statically"] + spec.notes,
    )


def decide_path_batch(specs: List[PathSpec], ctx: PolicyContext,
                      recursive: bool) -> Verdict:
    """One batch of concrete deletion targets through the rule table."""
    reasons: List[str] = []

    errored = [s for s in specs if s.error or s.indeterminable]
    if errored:
        reasons.extend(s.error or "indeterminable target" for s in errored)
        return Verdict(DECISION_BLOCK, CODE_BLOCK_UNDETERMINABLE_EFFECT,
                       reasons)

    if specs and all(s.inside_trash for s in specs):
        return Verdict(DECISION_ALLOW, CODE_ALLOW_TRASH_GC)

    protected_outside = [s for s in specs
                         if s.protected == "outside-workspace"]
    if protected_outside:
        return Verdict(DECISION_BLOCK, CODE_BLOCK_OUT_OF_WORKSPACE,
                       [f"outside workspace boundary: {s.raw}"
                        for s in protected_outside[:5]])

    protected_other = [s for s in specs
                       if s.protected in ("workspace-root", "git-metadata")]
    if protected_other:
        return Verdict(DECISION_BLOCK, CODE_BLOCK_PROTECTED_PATH,
                       [f"protected: {s.protected} ({s.raw})"
                        for s in protected_other[:5]])

    wild = [s for s in specs if s.wildcard]
    if wild:
        return Verdict(DECISION_BLOCK, CODE_BLOCK_WILDCARD,
                       ["glob targets are opaque; enumerate explicitly "
                        "(safe_delete expands globs itself)"])

    missing = [s for s in specs if not s.exists]
    if len(missing) == len(specs):
        return Verdict(DECISION_ALLOW, CODE_ALLOW_NOOP)

    if ctx.mode == MODE_RESTRICTED:
        if all((s.is_file or s.is_symlink) for s in specs):
            return Verdict(DECISION_RELOCATE, CODE_RELOCATE_NARROW,
                           ["restricted mode: named files only, quarantined"])
        return Verdict(DECISION_BLOCK, CODE_BLOCK_RESTRICTED_MODE,
                       ["restricted mode permits only explicit single-file "
                        "deletes inside the workspace"])

    if recursive and regenerable(specs, ctx):
        return Verdict(DECISION_ALLOW, CODE_ALLOW_REGENERABLE,
                       ["all targets are git-ignored and match known "
                        "artifact patterns"])
    if recursive:
        dirs = [s.raw for s in specs if s.is_dir]
        if dirs:
            return Verdict(DECISION_RELOCATE, CODE_RELOCATE_TREE,
                           ["rooted recursive delete relocated whole"])
        return Verdict(DECISION_RELOCATE, CODE_RELOCATE_PATHS,
                       ["named targets relocated"])
    return Verdict(DECISION_RELOCATE, CODE_RELOCATE_PATHS,
                   ["named targets relocated"])


def _ask_compound(spec: OpSpec, code: str) -> Verdict:
    return Verdict(
        decision=DECISION_ASK,
        code=code,
        reasons=list(spec.notes),
        payload={"op": spec.op, "kind": spec.kind},
    )


def decide_dialect_failure(resolution, source: str = "cli") -> Verdict:
    """A BLOCK verdict for an unusable dialect selector.

    Called *before* classification, because an unusable selector means the
    command line was never classified at all. The guard must not silently
    lex a PowerShell line with the POSIX lexer (or vice versa): that is the
    exact failure the dialect layer exists to prevent, and it would let a
    destructive line through as an ordinary - or, worse, mis-parsed -
    delete.

    BLOCK rather than ASK: a bad dialect selector is a configuration defect
    that recurs on every command, not a one-time authorization a human can
    meaningfully grant per execution. The explanation names the selector so
    the fix is mechanical.
    """
    if resolution is not None and resolution.ok:
        raise ValueError("decide_dialect_failure called with a usable dialect")
    reason = getattr(resolution, "reason", None) or \
        f"{source}: unusable dialect selector"
    # The resolver owns the unknown-vs-invalid classification; policy only
    # mirrors it onto a reason code (single source of truth).
    kind = getattr(resolution, "kind", None)
    code = (CODE_BLOCK_DIALECT_INVALID if kind == "invalid"
            else CODE_BLOCK_DIALECT_UNKNOWN)
    return Verdict(decision=DECISION_BLOCK, code=code,
                   reasons=[reason, "command line was not classified"])


def decide_op(spec: OpSpec, ctx: PolicyContext) -> Optional[Verdict]:
    """Map one classified operation to its decision. None => not destructive."""
    if spec.kind == KIND_UNKNOWN:
        return _block_undeterminable_effect(spec)

    # Authorization gate first. In RESTRICTED mode nothing may escalate to
    # ASK: the capability was already narrowed by a human veto.
    if ctx.mode == MODE_RESTRICTED and (
            spec.kind != KIND_FS_DELETE or spec.shape):
        return Verdict(DECISION_BLOCK, CODE_BLOCK_RESTRICTED_MODE,
                       [f"restricted mode disables {spec.kind} operations"])

    # Shape facts ask; they never masquerade as effect-uncertainty.
    if spec.shape == "F1":
        return _ask_compound(spec, CODE_ASK_COMPOUND_CWD_DELETE)
    if spec.shape == "F2":
        return _ask_compound(spec, CODE_ASK_COMPOUND_CREATE_DELETE)

    if spec.kind == KIND_FS_DELETE:
        if spec.undeterminable:
            return _block_undeterminable_effect(spec)
        if spec.dry_run:
            # A dry run mutates nothing: PowerShell's `-WhatIf` (and only a
            # literal `$true`, never a variable - that stays undeterminable)
            # reaches this branch. Nothing to relocate, nothing to refuse.
            return Verdict(DECISION_ALLOW, CODE_ALLOW_NOOP,
                           list(spec.notes) or ["dry run: no effect"])
        if not spec.targets:
            return Verdict(DECISION_ALLOW, CODE_ALLOW_NOOP)
        path_specs = classify_paths(
            spec.targets, ctx.base_dir, ctx.workspace, ctx.trash_root)
        return decide_path_batch(path_specs, ctx, recursive=spec.recursive)

    if spec.kind == KIND_GIT_CLEAN:
        if spec.dry_run:
            return Verdict(DECISION_ALLOW, CODE_ALLOW_NOOP,
                           ["git clean dry run"])
        if spec.undeterminable:
            return _block_undeterminable_effect(spec)
        if spec.wildcard:
            return Verdict(DECISION_BLOCK, CODE_BLOCK_WILDCARD,
                           ["git clean path globs are opaque"])
        return Verdict(
            DECISION_RELOCATE, CODE_RELOCATE_VIA_CLEAN_ENUMERATE,
            ["enumerate via git clean -n, relocate matches, then let the "
             "command run"],
            payload={"paths": spec.targets},
        )

    if spec.kind == KIND_GIT_RESET_HARD:
        return Verdict(
            DECISION_SNAPSHOT, CODE_SNAPSHOT_GIT_STASH,
            ["snapshot tracked modifications via git stash create/store "
             "before reset --hard"],
        )

    if spec.kind == KIND_GIT_DISCARD:
        if spec.undeterminable:
            return _block_undeterminable_effect(spec)
        if spec.wildcard:
            return Verdict(DECISION_BLOCK, CODE_BLOCK_WILDCARD,
                           ["discard globs are opaque"])
        return Verdict(
            DECISION_SNAPSHOT, CODE_SNAPSHOT_GIT_STASH,
            ["snapshot tracked modifications before discarding "
             "working-tree changes"],
        )

    if spec.kind == KIND_GIT_PUSH_FORCE:
        return Verdict(DECISION_BLOCK, CODE_BLOCK_FORCE_PUSH,
                       list(spec.notes) or ["remote history destruction"])

    return None


def decide_ops(specs: List[OpSpec], ctx: PolicyContext) -> List[Verdict]:
    """All decisions for a command line; empty list means nothing destructive."""
    verdicts = []
    for spec in specs:
        verdict = decide_op(spec, ctx)
        if verdict is not None:
            verdict.payload.setdefault("op", spec.op)
            verdict.payload.setdefault("kind", spec.kind)
            verdicts.append(verdict)
    return verdicts


# --------------------------------------------------------------------------
# exfil-guard: text-span decisions (design 1.3)
# --------------------------------------------------------------------------
#
# Same Decision Protocol, different input type. `decide_spans` is a pure
# function of (spans, channel, workspace, mode) - it holds no payload bytes
# and returns verdicts whose payload carries only offsets, rule ids and
# placeholders (never the matched content). First-match-wins, like the
# delete-guard rule table.
#
# The asymmetric tolerance from the analysis (0.1) is encoded here: secret
# rules are strict (a false ALLOW is a permanent leak), path rules are quiet
# (a false SANITIZE corrupts output and erodes trust).


def _span_verdict(decision: str, code: str, span, reasons=None,
                  **payload) -> Verdict:
    verdict = Verdict(decision=decision, code=code, reasons=list(reasons or []))
    verdict.payload.update({
        "rule_id": span.rule_id,
        "family": span.family,
        "confidence": span.confidence,
        "channel": span.channel,
        "span": [span.start, span.end],
        "length": span.length,
    })
    verdict.payload.update(payload)
    return verdict


def _span_is_unrewritable(channel) -> bool:
    """True when the guard cannot hand back a rewritten payload."""
    return not channel.rewritable


def _blocked_channel(channel) -> bool:
    """Immutable + persistent: refuse rather than emit what cannot be undone."""
    return channel.default_decision == DECISION_BLOCK


def decide_span(span, ctx, channel=None) -> Verdict:
    """Map one SpanSpec to a Verdict (facts -> decision, first match wins)."""
    if channel is None and getattr(span, "channel", ""):
        # The caller may pass the channel on the span instead of as a
        # Channel object; resolve it once, so an *unknown* name still
        # reaches the fail-closed branch below rather than being ignored.
        channel = get_channel(span.channel)
    reasons = list(getattr(span, "notes", []) or [])
    reasons.append("rule " + span.rule_id)

    is_secret = span.family == redaction.FAMILY_SECRET
    deterministic = span.confidence == redaction.CONFIDENCE_DETERMINISTIC

    # --- 1. a secret source we cannot scan: fail closed -------------------
    if getattr(span, "source_dump", False):
        return _span_verdict(DECISION_BLOCK, CODE_BLOCK_SECRET_SOURCE_DUMP,
                             span, reasons)

    # --- 2. placeholder / provably benign --------------------------------
    if getattr(span, "placeholder", False):
        code = (CODE_ALLOW_SECRET_PLACEHOLDER if is_secret
                else CODE_ALLOW_PATH_IN_WORKSPACE)
        return _span_verdict(DECISION_ALLOW, code, span, reasons)

    # --- 3. paths inside the workspace are not host identifiers ----------
    if not is_secret and span.rule_id == redaction.RULE_WORKSPACE_PATH and \
            not getattr(span, "at_workspace_root", False):
        return _span_verdict(DECISION_ALLOW, CODE_ALLOW_PATH_IN_WORKSPACE,
                             span, reasons)

    # --- 4. RESTRICTED tightens, never loosens ---------------------------
    restricted = ctx is not None and getattr(ctx, "mode", None) == \
        MODE_RESTRICTED

    # --- 5. channel shape decides SANITIZE vs ASK vs BLOCK ---------------
    if channel is None:
        # Invalid configuration: the guard cannot say what would carry the
        # bytes, so it cannot say it is safe. Fail closed.
        return _span_verdict(DECISION_BLOCK, CODE_BLOCK_OUTPUT_UNSCANNABLE,
                             span, reasons + ["unknown egress channel"])

    if _blocked_channel(channel):
        # Immutable history: the emission cannot be taken back, so refuse
        # rather than rewrite (design 4.4, first bullet). This outranks
        # `rewritable`, which is why a commit message BLOCKs even though
        # rewriting the argv would be technically possible.
        code = (CODE_BLOCK_SECRET_EMISSION if is_secret
                else CODE_BLOCK_PATH_EMISSION)
        return _span_verdict(DECISION_BLOCK, code, span, reasons)

    if _span_is_unrewritable(channel):
        code = (CODE_ASK_SECRET_EMISSION if is_secret
                else CODE_ASK_PATH_EMISSION)
        return _span_verdict(DECISION_ASK, code, span, reasons)

    # --- 6. rewritable: secrets are redacted, paths rewritten ------------
    if is_secret:
        # Deterministic (T1) matches auto-SANITIZE. A contextual match is
        # still SANITIZE behind the context gate (design 5.3), but only
        # where the rule is enabled; otherwise a human decides.
        if deterministic or getattr(span, "context_gate", False):
            return _span_verdict(DECISION_SANITIZE,
                                 CODE_SANITIZE_SECRET_REDACT, span, reasons)
        return _span_verdict(DECISION_ASK, CODE_ASK_SECRET_EMISSION,
                             span, reasons)

    if span.rule_id == redaction.RULE_SYSTEM_PATH:
        # System prefixes are not host-identifying (design 3.1).
        return _span_verdict(DECISION_ALLOW, CODE_ALLOW_PATH_IN_WORKSPACE,
                             span, reasons)

    if deterministic or getattr(span, "at_workspace_root", False):
        return _span_verdict(DECISION_SANITIZE, CODE_SANITIZE_PATH_REWRITE,
                             span, reasons)

    # A generic absolute path with no prefix correlation is contextual: the
    # default is quiet (design 3.4), so a human decides whether it matters.
    if restricted:
        return _span_verdict(DECISION_SANITIZE, CODE_SANITIZE_PATH_REWRITE,
                             span, reasons + ["restricted mode: strict"])
    return _span_verdict(DECISION_ASK, CODE_ASK_PATH_EMISSION, span, reasons)


def decide_spans(spans, ctx=None, channel=None, workspace=None,
                 mode=None) -> List[Verdict]:
    """All decisions for a payload; empty list means nothing matched.

    `ctx` is a PolicyContext when the caller has one (reuse of the existing
    mode state), otherwise `mode` alone is accepted so `check_span.py` stays
    usable without a workspace or quarantine.
    """
    if ctx is None:
        ctx = PolicyContext(workspace=workspace or "",
                            trash_root="", base_dir=workspace or "",
                            mode=mode or MODE_NORMAL)
    verdicts = []
    for span in spans:
        if channel is not None:
            span.channel = channel.name
        verdicts.append(decide_span(span, ctx, channel))
    return verdicts
