"""Effect-oriented classifier for potentially destructive operations.

The classifier turns raw input (a shell command string, or an explicit list
of paths handed to the safe_delete tool) into structured *facts*:

  OpSpec    - one destructive operation found in a command line
  PathSpec  - one deletion target with resolved boundary facts

It deliberately does NOT decide what to do - that is policy.py's job.
Design rules:

* Classify by effect, dialect only as a front end. The POSIX vocabulary
  (rm/rmdir/unlink/shred, find -delete, git clean/reset/restore/checkout/
  push) covers the overwhelming majority of real agent accidents on
  Linux/macOS. Native Windows shells express the same effects with other
  programs and other lexical rules; those live in `dialects.py` and are
  dispatched by `classify_command(dialect=...)`. The default dialect is
  POSIX, so the pre-dialect behaviour is unchanged for every existing
  caller.
* Fail closed. Unbalanced quotes, shell variables, command substitution,
  unknown flags, stdin-fed target lists, indirect shells (bash -c) - all
  become `undeterminable` facts. The policy layer restricts those.
* Lexical boundary analysis. Workspace containment is decided on the
  lexically normalized path (so deleting a symlink that points outside is
  deleting the *link*, not the target, and stays inside the boundary);
  symlink facts are still recorded for audit and for restore.
"""
from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from . import dialects

# ---------------------------------------------------------------- vocabulary

FS_DELETE_CMDS = {"rm", "rmdir", "unlink", "shred"}
SHELL_PREFIXES = {"sudo", "env", "nice", "nohup", "command", "time", "stdbuf", "xargs"}
INTERPRETER_CMDS = {"sh", "bash", "zsh", "ksh", "eval", "source", "."}
SEPARATORS = {";", "&", "&&", "|", "||"}
GLOB_CHARS = ("*", "?", "[")
INDETERMINACY_CHARS = ("$", "`")

# Single-source keyword prefilter: harness adapters use it to skip
# non-destructive traffic at regex cost before invoking check.py.
DESTRUCTIVE_PREFILTER_RE = re.compile(
    r"(^|[\s;&|(\/])(rm|rmdir|unlink|shred)\b"
    r"|\bfind\b[^\n|;&]*-delete\b"
    r"|\bgit\s+(clean|reset|restore|checkout|push)\b")

# Windows-native prefilter vocabulary. The POSIX regex above never matches
# `ri build -r -fo`/`rd /s /q build`, so a Windows-native harness would hand
# the cheap regex a line it cannot see and skip the guard entirely. Adapters
# only consult this when the *requested* dialect is non-POSIX, so the POSIX
# fast path keeps its exact historical cost and behaviour.
DESTRUCTIVE_PREFILTER_RE_WINDOWS = re.compile(
    r"(^|[\s;&|(\\/])(rm|ri|rd|rmdir|del|erase|remove-item)\b"
    r"|-\s?(recurse|force|whatif|literalpath)\b"
    r"|\bgit\s+(clean|reset|restore|checkout|push)\b",
    re.IGNORECASE)


# Rough prefilter for "does this opaque string smell destructive at all".
# Only used for indirect execution (bash -c '...'), where the guard cannot
# parse structure and therefore only needs a yes/no smell test.
DESTRUCTIVE_SMELL_RE = re.compile(
    r"(?:^|[\s;&|(=/])(rm|rmdir|unlink|shred)\b|find\s+\S.*-delete|"
    r"git\s+clean\b|git\s+reset\b|mkfs\b",
    re.IGNORECASE,
)

# --------------------------------------------------------------------- types

KIND_OTHER = "other"                    # not destructive (or not recognized)
KIND_FS_DELETE = "fs-delete"            # rm family
KIND_GIT_CLEAN = "git-clean"            # git clean -f...
KIND_GIT_RESET_HARD = "git-reset-hard"  # git reset --hard
KIND_GIT_DISCARD = "git-discard"        # git restore <path> / git checkout -- <path>
KIND_GIT_PUSH_FORCE = "git-push-force"  # force / mirror / ref-deletion push
KIND_UNKNOWN = "unknown"                # destructive smell, no parseable shape

# Whole-command-line shape facts (docs/friction.md F1/F2).
CREATION_CMDS = {"touch", "mkdir", "cp", "mv", "install", "ln", "tee"}
# cmd builtins with the same shape (F2 is about "created then destroyed",
# which `copy x a.tmp && del a.tmp` expresses just as literally).
CMD_CREATION_CMDS = {"copy", "xcopy", "robocopy", "mkdir", "md", "mklink",
                     "type", "echo", "ren", "rename", "move"}
REDIRECT_CREATE_TOKENS = {">", ">>"}
# Kinds whose compensation depends on enumerating concrete targets; a target
# created earlier in the same line is invisible to pre-execution compensation.
TARGET_DEPENDENT_KINDS = {KIND_FS_DELETE, KIND_GIT_CLEAN, KIND_GIT_DISCARD}


@dataclass
class OpSpec:
    """One potentially destructive operation extracted from a command line."""

    op: str                      # program name, e.g. 'rm', 'git'
    kind: str                    # KIND_* constant
    sub: Optional[str] = None    # git subcommand when op == 'git'
    targets: List[str] = field(default_factory=list)   # raw target tokens
    recursive: bool = False
    force: bool = False
    dry_run: bool = False
    extra_flags: List[str] = field(default_factory=list)  # scope letters for git clean
    segment_index: int = 0          # position of this op within the command line
    dialect: str = "posix"          # lexical front end that produced this fact
    wildcard: bool = False       # any target contains glob syntax
    undeterminable: bool = False # effect cannot be determined statically
    shape: Optional[str] = None  # 'F1' | 'F2': compound-command shape facts
    notes: List[str] = field(default_factory=list)

    def note(self, text: str) -> None:
        if text not in self.notes:
            self.notes.append(text)


@dataclass
class PathSpec:
    """One explicit deletion target with resolved boundary facts."""

    raw: str
    resolved: Optional[str] = None      # lexically normalized absolute path
    wildcard: bool = False
    indeterminable: bool = False        # contains $, `, ( in direct API
    exists: bool = False
    is_symlink: bool = False
    is_dir: bool = False
    is_file: bool = False
    link_target: Optional[str] = None   # realpath when is_symlink
    inside_workspace: bool = False
    inside_trash: bool = False
    protected: Optional[str] = None     # None | workspace-root | outside-workspace | git-metadata
    error: Optional[str] = None

# ------------------------------------------------------------- path analysis


def _has_glob(text: str) -> bool:
    return any(c in text for c in GLOB_CHARS)


def _has_indeterminacy(text: str) -> bool:
    return any(c in text for c in INDETERMINACY_CHARS) or "(" in text


def inside_path(path: str, root: str) -> bool:
    """True when path is root or lies under root (both absolute, same form).

    Lexical by design - the caller decides whether the two sides are
    lexical or physical. Comparing a lexical path against a physical root
    (or vice versa) is the F9 macOS bug; see `_physical`.
    """
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:  # pragma: no cover - mixed abs/rel or exotic roots
        return False


def _physical(path: str) -> str:
    """``realpath``-normalized comparison form of a path (F8/F9).

    Boundary analysis puts BOTH sides through this one function so they can
    never diverge again: realpath resolves symlinks and ``..`` segments (an
    unresolved ``/a/link/../b`` does not even mean what its own prefix
    says), and normpath canonicalizes the result the way the workspace root
    already was.

    `PathSpec.resolved` deliberately stays lexical for every other use
    (relocation, restore, messages).
    """
    return os.path.normpath(os.path.realpath(path))


def _physical_keep_final(path: str) -> str:
    """Physical form of a path whose FINAL component must not be resolved.

    `_physical` resolves every symlink, which is right for boundary
    questions ("does this path live under the workspace?") but wrong for
    LAYOUT reconstruction: a target that IS a symlink would otherwise be
    stored under its link target's position, so `restore` could no longer
    put the link itself back. Resolve the parent chain - that is where the
    macOS spelling divergence lives - and keep the last name verbatim.
    """
    abspath = os.path.normpath(os.path.abspath(path))
    parent, name = os.path.split(abspath)
    if not name:  # filesystem root
        return abspath
    return os.path.join(os.path.realpath(parent), name)


def workspace_boundary_root(workspace: str) -> str:
    """Physical form of a workspace root, for callers that compare roots."""
    return _physical(os.path.normpath(os.path.abspath(workspace)))


def discover_workspace(start_dir: str) -> str:
    """Nearest ancestor (or start itself) containing .git; else start_dir.

    AGENT_GUARD_WORKSPACE overrides discovery entirely - harness adapters
    that already know the workspace should set it.

    The result is realpath-resolved (F8): on macOS, /var is a symlink to
    /private/var, so an unresolved root string never matches the physical
    cwd that child processes report - every target would look out of
    bounds. Target paths stay LEXICAL by design; only the boundary root
    gets physicalized.
    """
    resolve = lambda pth: os.path.realpath(os.path.normpath(os.path.abspath(pth)))
    env = os.environ.get("AGENT_GUARD_WORKSPACE")
    if env:
        return resolve(env)
    cur = os.path.normpath(os.path.abspath(start_dir))
    while True:
        marker = os.path.join(cur, ".git")
        if os.path.exists(marker):  # dir (normal repo) or file (worktree)
            return resolve(cur)
        parent = os.path.dirname(cur)
        if parent == cur:
            return resolve(start_dir)
        cur = parent


def classify_paths(
    raw_paths: List[str],
    base_dir: str,
    workspace: str,
    trash_root: Optional[str] = None,
) -> List[PathSpec]:
    """Resolve explicit deletion targets into boundary facts (safe_delete path).

    Boundary checks are lexical: `..` escapes are caught, and a symlink is
    judged by the location of the link itself, never by its target - deleting
    a link does not touch what it points to.
    """
    workspace = _physical(os.path.normpath(os.path.abspath(workspace)))
    specs: List[PathSpec] = []
    for raw in raw_paths:
        spec = PathSpec(raw=raw)
        text = raw.strip()
        if not text:
            spec.error = "empty path"
            specs.append(spec)
            continue
        spec.wildcard = _has_glob(text)
        if _is_windows_absolute(text):
            # NOT host-relative and NOT resolvable here: mapping `C:/x` onto
            # the POSIX workspace would invent a containment fact. The
            # dialect layer reports an absolute Windows path as outside the
            # POSIX workspace boundary - fail closed, never a false ALLOW.
            spec.resolved = text
            spec.protected = "outside-workspace"
            specs.append(spec)
            continue
        if _has_indeterminacy(text):
            spec.indeterminable = True
            spec.error = "variables/substitution are not allowed in the direct path API"
            specs.append(spec)
            continue
        expanded = os.path.expanduser(text)
        absolute = (
            os.path.normpath(os.path.abspath(expanded))
            if os.path.isabs(expanded)
            else os.path.normpath(os.path.abspath(os.path.join(base_dir, expanded)))
        )
        spec.resolved = absolute
        spec.exists = os.path.lexists(absolute)
        spec.is_symlink = os.path.islink(absolute)
        if spec.is_symlink:
            spec.link_target = os.path.realpath(absolute)
        elif os.path.isdir(absolute):
            spec.is_dir = True
        elif os.path.isfile(absolute):
            spec.is_file = True
        # Boundary facts are resolved PHYSICALLY on BOTH sides (F9): the
        # target is physicalized for the containment comparison only, while
        # `spec.resolved` keeps the caller's LEXICAL path - compensation
        # budget and error messages depend on the path the caller gave us.
        # Normalizing one side alone is the macOS bug: a fixture that runs
        # under a symlinked directory (/tmp -> /private/tmp, /var/folders
        # -> /private/var/folders) lexically shares no prefix with the
        # physicalized workspace root, so every in-workspace target was
        # reported BLOCK_OUT_OF_WORKSPACE. Symbolic links the target *ends*
        # at are physicalized too, exactly as the workspace root already was
        # (F8): the old comparison was asymmetrically physical anyway.
        physical = _physical(absolute)
        spec.inside_workspace = inside_path(physical, workspace)
        trash_physical = None
        if trash_root:
            trash_physical = _physical(os.path.normpath(trash_root))
            spec.inside_trash = inside_path(physical, trash_physical)
        if physical == workspace or physical == trash_physical:
            # Anchored at a boundary root itself: a workspace root and the
            # quarantine centre are both "the root of their tree", never
            # content. `rm -rf .` stays a hard BLOCK. (When no trash_root is
            # given, trash_physical is None and cannot match a real path.)
            spec.protected = "workspace-root"
        elif not spec.inside_workspace:
            spec.protected = "outside-workspace"
        else:
            rel = os.path.relpath(physical, workspace)
            if any(part == ".git" for part in rel.split(os.sep)):
                spec.protected = "git-metadata"
        specs.append(spec)
    return specs

# ----------------------------------------------------------- shell parsing


def _split_segments(tokens: List[str]) -> List[List[str]]:
    segments: List[List[str]] = [[]]
    for tok in tokens:
        if tok in SEPARATORS:
            segments.append([])
        else:
            segments[-1].append(tok)
    out: List[List[str]] = []
    for seg in segments:
        # Subshell/grouping parens attach to adjacent words under shlex
        # ((rm -rf x) tokenizes as ['(rm', '-rf', 'x)']); strip them from
        # segment edges so the dispatcher sees the real command head.
        while seg and seg[0][:1] in ("(", "{"):
            seg[0] = seg[0][1:]
            if not seg[0]:
                seg.pop(0)
        while seg and seg[-1][-1:] in (")", "}"):
            seg[-1] = seg[-1][:-1]
            if not seg[-1]:
                seg.pop()
        if seg:
            out.append(seg)
    return out


def _basename(path: str) -> str:
    return os.path.basename(path)


# Windows path shapes, meaningful to the dialect layer on ANY host (a
# Linux CI run must still reason about `C:/Windows` and `/etc`-style
# targets arriving in a cmd line).
_WIN_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_WIN_UNC_RE = re.compile(r"^(?:\\\\|//)[^/\\]+[/\\][^/\\]+")


def _is_windows_absolute(target: str) -> bool:
    """True for `C:/x`, `C:\\x`, UNC `\\\\server\\share\\x`, `//server/share`."""
    return bool(_WIN_DRIVE_RE.match(target) or _WIN_UNC_RE.match(target))


def _scan_targets(spec: OpSpec) -> None:
    for target in spec.targets:
        if _has_glob(target):
            spec.wildcard = True
        if _has_indeterminacy(target):
            spec.undeterminable = True
            spec.note(f"target '{target}' contains variable/substitution")


def _parse_fs_delete(head: str, segment: List[str], from_xargs: bool) -> OpSpec:
    spec = OpSpec(op=head, kind=KIND_FS_DELETE)
    after_dd = False
    for tok in segment[1:]:
        if not after_dd and tok == "--":
            after_dd = True
            continue
        if not after_dd and tok.startswith("-") and len(tok) > 1:
            if tok.startswith("--"):
                name = tok[2:]
                if name == "recursive":
                    spec.recursive = True
                elif name == "force":
                    spec.force = True
                elif name in ("interactive", "verbose", "one-file-system",
                              "preserve-root", "prompt"):
                    pass  # semantics-neutral for classification
                elif name == "no-preserve-root":
                    spec.note("no-preserve-root requested")
                else:
                    spec.undeterminable = True
                    spec.note(f"unknown flag --{name}")
            else:
                for ch in tok[1:]:
                    if ch in "rR":
                        spec.recursive = True
                    elif ch == "f":
                        spec.force = True
                    elif ch in "vidI":
                        pass
                    else:
                        spec.undeterminable = True
                        spec.note(f"unknown flag -{ch}")
            continue
        spec.targets.append(tok)
    if from_xargs:
        spec.undeterminable = True
        spec.note("target list arrives via stdin (xargs)")
    _scan_targets(spec)
    return spec


def _parse_find(segment: List[str]) -> OpSpec:
    rest = segment[1:]
    spec = OpSpec(op="find", kind=KIND_OTHER, targets=rest[:1])
    for i, tok in enumerate(rest):
        if tok == "-delete":
            spec.kind = KIND_FS_DELETE
            spec.recursive = True
            spec.undeterminable = True
            spec.note("find -delete: matched set depends on predicates")
            break
        if tok in ("-exec", "-execdir"):
            nxt = rest[i + 1] if i + 1 < len(rest) else ""
            if _basename(nxt) in {"rm", "sh", "bash", "xargs"}:
                spec.kind = KIND_FS_DELETE
                spec.recursive = True
                spec.undeterminable = True
                spec.note("find -exec rm: matched set depends on predicates")
                break
    return spec


def _parse_git(segment: List[str]) -> OpSpec:
    tokens = segment[1:]
    sub = tokens[0] if tokens else ""
    rest = tokens[1:]
    spec = OpSpec(op="git", kind=KIND_OTHER, sub=sub)

    if sub == "clean":
        spec.kind = KIND_GIT_CLEAN
        dry = False
        force = False
        force_count = 0
        after_dd = False
        idx = 0
        while idx < len(rest):
            tok = rest[idx]
            idx += 1
            if not after_dd and tok == "--":
                after_dd = True
                continue
            if not after_dd and tok.startswith("--"):
                name = tok[2:].split("=")[0]
                if name == "force":
                    force = True
                    force_count += 1
                elif name == "dry-run":
                    dry = True
                elif name == "directory":
                    spec.extra_flags.append("-d")
                elif name == "ignored":
                    spec.extra_flags.append("-x")
                elif name == "quiet":
                    pass
                elif name == "exclude":
                    spec.undeterminable = True
                    spec.note("git clean exclude patterns are not safely mirrored")
                    if "=" not in tok:
                        idx += 1  # --exclude <pattern> consumes an argument
                else:
                    spec.undeterminable = True
                    spec.note(f"unknown git clean flag --{name}")
                continue
            if not after_dd and tok.startswith("-") and len(tok) > 1:
                for ch in tok[1:]:
                    if ch == "n":
                        dry = True
                    elif ch == "f":
                        force = True
                        force_count += 1
                    elif ch == "d":
                        spec.extra_flags.append("-d")
                    elif ch == "x":
                        spec.extra_flags.append("-x")
                    elif ch == "X":
                        spec.extra_flags.append("-X")
                    elif ch == "q":
                        pass
                    elif ch == "i":
                        spec.undeterminable = True
                        spec.note("interactive git clean selection")
                    elif ch == "e":
                        spec.undeterminable = True
                        spec.note("git clean exclude patterns are not safely mirrored")
                    else:
                        spec.undeterminable = True
                        spec.note(f"unknown git clean flag -{ch}")
                continue
            spec.targets.append(tok)
        spec.dry_run = dry
        spec.force = force and not dry
        if force_count > 1:
            spec.undeterminable = True
            spec.note("double-force git clean may remove nested repositories")
        if not spec.targets and not spec.force and not dry:
            spec.dry_run = True  # git clean without -f is a dry run anyway
        _scan_targets(spec)
        return spec

    if sub == "reset":
        if "--hard" in rest:
            spec.kind = KIND_GIT_RESET_HARD
            spec.force = True
            spec.targets = [
                t for t in rest if t != "--hard" and not t.startswith("-")
            ]
            if not spec.targets:
                spec.note("whole-tree reset (no path limit)")
        return spec

    if sub == "restore":
        staged_only = ("--staged" in rest or "-S" in rest) and (
            "--worktree" not in rest and "-W" not in rest
        )
        if staged_only:
            spec.note("staged-only restore does not touch working tree files")
            return spec  # kind stays OTHER
        if "-p" in rest or "--patch" in rest:
            spec.kind = KIND_GIT_DISCARD
            spec.undeterminable = True
            spec.note("interactive patch selection")
            return spec
        spec.kind = KIND_GIT_DISCARD
        spec.targets = [t for t in rest if not t.startswith("-") or t == "--"]
        spec.targets = [t for t in spec.targets if t != "--"]
        _scan_targets(spec)
        return spec

    if sub == "checkout":
        if "--" in rest:
            spec.kind = KIND_GIT_DISCARD
            spec.targets = rest[rest.index("--") + 1:]
            _scan_targets(spec)
        elif "-p" in rest or "--patch" in rest:
            spec.kind = KIND_GIT_DISCARD
            spec.undeterminable = True
            spec.note("interactive patch selection")
        return spec

    if sub == "push":
        destructive: List[str] = []
        for tok in rest:
            if not tok.startswith("-"):
                if tok.startswith(":"):
                    destructive.append(f"ref-deletion {tok}")
                elif tok.startswith("+"):
                    destructive.append(f"force-refspec {tok}")
            elif tok in ("--force", "-f", "--mirror", "--delete", "-d"):
                destructive.append(tok)
            elif tok.startswith("--force-with-lease"):
                destructive.append("--force-with-lease")
        if destructive:
            spec.kind = KIND_GIT_PUSH_FORCE
            spec.force = True
            spec.note("remote mutation: " + ", ".join(destructive[:4]))
        return spec

    return spec  # other git subcommands are out of V1 scope


HEREDOC_OP_RE = re.compile(r"<<-?\s*([\"']?)([A-Za-z_][A-Za-z0-9_-]*)\1")


def strip_heredocs(cmd: str) -> str:
    """Remove heredoc bodies before classification (F5/F6).

    A redirection like cat-over-heredoc writes a FILE; its body is payload
    text, not executable syntax. Two rules keep this safe and terminating:

    * the operator token is REPLACED (not retained) so the scan can never
      re-match the same heredoc;
    * an unterminated heredoc removes only its own operator line - later
      command lines survive intact (the old truncating fallback once cut a
      sed line mid-quote and manufactured the very danger it guarded
      against).
    """
    out = cmd
    while True:
        m = HEREDOC_OP_RE.search(out)
        if not m:
            return out
        tag = m.group(2)
        start = m.start()          # cut from the operator itself
        after = m.end()
        nl = out.find("\n", after)
        if nl == -1:
            out = out[:start] + " " + out[after:]
            continue               # operator gone: no re-match possible
        terminator = re.compile(r"^\s*" + re.escape(tag) + r"\s*$",
                                re.MULTILINE)
        end_m = terminator.search(out, nl + 1)
        if end_m:
            # end_m was searched from nl+1, so end_m.end() is ALREADY an
            # absolute index in out. Adding nl+1 again double-offsets the
            # cut deep into the following command (friction F7).
            out = out[:start] + " " + out[end_m.end():]
        else:
            out = out[:start] + " " + out[nl + 1:]


def _has_create_redirect(segment: List[str]) -> bool:
    return any(tok in REDIRECT_CREATE_TOKENS for tok in segment)


def _apply_shape_rules(specs: List[OpSpec], cd_positions: List[int],
                       creation_positions: List[int]) -> None:
    """Whole-command-line restrictions (docs/friction.md F1/F2).

    F1: a destructive op preceded by cd resolves against the wrong working
        directory - compensation would enumerate/snapshot the wrong tree.
        Applies to every destructive kind.
    F2: a target created earlier in the same line does not exist yet at
        interception time, so target-dependent compensation cannot cover it.
        Position-independent compensations (reset --hard whole-tree stash,
        force-push which is blocked anyway) are exempt.
    Shapes are DECISION-CLASS facts, not effect-uncertainty: the operation
    itself is well understood, only its safe automatic compensation is not.
    Policy maps them to ASK (single-execution authorization), keeping true
    effect-undeterminable cases on the BLOCK path.
    """
    for spec in specs:
        if spec.kind in (KIND_OTHER, KIND_UNKNOWN):
            continue
        idx = spec.segment_index
        if any(pos < idx for pos in cd_positions):
            spec.shape = "F1"
            spec.note(
                "destructive operation follows 'cd' within the same "
                "command line; targets cannot be resolved against the "
                "declared working directory")
        if spec.kind in TARGET_DEPENDENT_KINDS and any(
                pos < idx for pos in creation_positions):
            spec.shape = "F2"
            spec.note(
                "this command line creates files before destroying "
                "them; pre-execution compensation cannot see targets that "
                "do not exist yet")


def _parse_error_spec(exc: str,
                      dialect: str = dialects.DIALECT_POSIX) -> OpSpec:
    """One undeterminable UNKNOWN spec for an unparseable command line."""
    spec = OpSpec(op="<unparseable>", kind=KIND_UNKNOWN, undeterminable=True,
                  dialect=dialect)
    spec.note(f"shell parse error: {exc}")
    return spec


def _classify_posix(cmd: str) -> Tuple[List[OpSpec], Optional[str]]:
    """The historical POSIX/`shlex` pipeline (unchanged behaviour)."""
    try:
        tokens = shlex.split(strip_heredocs(cmd), posix=True)
    except ValueError as exc:
        return [_parse_error_spec(str(exc))], str(exc)
    if not tokens:
        return [], None

    specs: List[OpSpec] = []
    cd_positions: List[int] = []
    creation_positions: List[int] = []

    def emit(spec: OpSpec, index: int) -> None:
        spec.segment_index = index
        specs.append(spec)

    for index, segment in enumerate(_split_segments(tokens)):
        seg = segment
        from_xargs = False
        while seg and _basename(seg[0]) in SHELL_PREFIXES:
            if _basename(seg[0]) == "xargs":
                from_xargs = True
            seg = seg[1:]
        if not seg:
            continue
        head = _basename(seg[0])

        if head == "cd":
            cd_positions.append(index)
        elif head in CREATION_CMDS or _has_create_redirect(seg):
            creation_positions.append(index)

        if head in INTERPRETER_CMDS:
            inner = " ".join(seg[1:])
            if DESTRUCTIVE_SMELL_RE.search(inner):
                spec = OpSpec(op=head, kind=KIND_UNKNOWN, undeterminable=True,
                              targets=[inner[:200]])
                spec.note("indirect shell execution with destructive smell")
                emit(spec, index)
            continue

        if head in FS_DELETE_CMDS:
            emit(_parse_fs_delete(head, seg, from_xargs), index)
        elif head == "find":
            emit(_parse_find(seg), index)
        elif head == "git":
            emit(_parse_git(seg), index)
        # anything else: kind OTHER, intentionally ignored by policy

    for spec in specs:
        spec.dialect = dialects.DIALECT_POSIX
    _apply_shape_rules(specs, cd_positions, creation_positions)
    return [s for s in specs if s.kind != KIND_OTHER], None


def _attribute_targets(spec: OpSpec) -> None:
    """Per-target facts (wildcard / interpolation) for a dialect op.

    The POSIX path did this inside `_parse_fs_delete`; dialect classifiers
    only enumerate targets, so the shared attribution lives here. A target
    like `%BUILD_DIR%/x` or `$env:TEMP` is not a path the guard may
    resolve, and a target like `*.log` is a set the *tool* expands - both
    must reach policy as uncertainty, never as a filename.
    """
    for target in spec.targets:
        if _has_glob(target):
            spec.wildcard = True
        if dialects.has_interpolation(target):
            spec.undeterminable = True
            spec.note(f"target '{target}' contains variable/substitution")


def _classify_windows_dialect(cmd: str, dialect: str
                              ) -> Tuple[List[OpSpec], Optional[str]]:
    """Native Windows dialects: lex, map effects, then shared annotations.

    Rewriting the path separators before classification is what makes the
    existing POSIX boundary/glob machinery work unchanged: on Linux a naive
    `deploy\build` is one filename, so the boundary check would silently
    treat a Windows tree as a file inside the workspace. Host-native
    commands keep their separators - the running host already resolves
    them.

    Shape rules (docs/friction.md F1/F2) apply to Windows lines too: `cd`
    and the interpreter prefix set are shared shell concepts, so F1 means
    the same thing in cmd and PowerShell.
    """
    stream = dialects.tokenize(cmd, dialect)
    if not stream.ok:
        return [_parse_error_spec(stream.error or "parse error", dialect)], \
            stream.error
    specs = dialects.classify_stream(stream)
    cd_positions: List[int] = []
    creation_positions: List[int] = []
    for index, segment in enumerate(stream.segments):
        head = _basename(segment[0]).lower()
        if head in ("cd", "chdir", "set-location", "sl", "pushd", "popd"):
            cd_positions.append(index)
        elif (head.lower() in CREATION_CMDS
              or head.lower() in CMD_CREATION_CMDS
              or _has_create_redirect(segment)):
            creation_positions.append(index)
    for spec in specs:
        spec.dialect = dialect
        if os.name != "nt":
            # Path separation is a host concern, not a dialect concern:
            # only rewrite on a non-Windows host, where `\` is an ordinary
            # filename character and would defeat boundary analysis.
            spec.targets = [t.replace("\\", "/") for t in spec.targets]
        _attribute_targets(spec)
    _apply_shape_rules(specs, cd_positions, creation_positions)
    return [s for s in specs if s.kind != KIND_OTHER], None


def classify_command(cmd: str, dialect: str = dialects.DEFAULT_DIALECT
                     ) -> Tuple[List[OpSpec], Optional[str]]:
    """Parse one shell command line into destructive OpSpecs.

    `dialect` selects the lexical front end: `posix` (default, identical to
    pre-dialect behaviour), `cmd`, or `powershell`. Unknown dialect names
    raise ValueError rather than silently falling back to POSIX - parsing a
    Windows command line with the POSIX lexer is the exact failure the
    dialect layer exists to prevent.

    Heredoc bodies are stripped before POSIX parsing (they are written
    payload, not commands - see docs/friction.md F5).

    Returns (specs, parse_error). Segments that are not destructive are
    returned as kind=OTHER and ignored by policy. A parse_error (unbalanced
    quoting) yields one undeterminable UNKNOWN spec - fail closed.
    """
    resolved = dialects.normalize_dialect(dialect)
    if resolved == dialects.DIALECT_POSIX:
        return _classify_posix(cmd)
    return _classify_windows_dialect(cmd, resolved)
