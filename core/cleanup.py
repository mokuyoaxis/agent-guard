"""Metadata-only cleanup proposals; deletion remains owned by safe_delete.

The plan is a local review aid, not permission to bypass policy. It does not
read candidate contents, follow directory links, or create guard metadata.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import TRASH_DIRNAME, classifier, policy


CATEGORIES = ("regenerable", "discarded", "uncertain", "protected")
PROTECTED_NAMES = frozenset({".git", ".internal", TRASH_DIRNAME})
DEFAULT_MAX_ENTRIES = 10_000
DEFAULT_MAX_DEPTH = 32


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=True,
                         separators=(",", ":")).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _under(path: str, parent: str) -> bool:
    return parent == "." or path == parent or path.startswith(parent + "/")


def _declared_paths(values: Iterable[str], root: str, *, must_exist: bool) -> List[str]:
    result = set()
    for value in values:
        absolute = os.path.normpath(os.path.join(root, os.fspath(value)))
        if not classifier.inside_path(absolute, root):
            raise ValueError("declared cleanup paths must be inside the workspace")
        if must_exist and absolute == root:
            raise ValueError("the workspace root cannot be declared discarded")
        if must_exist and not os.path.lexists(absolute):
            raise ValueError("discard target does not exist: " + os.fspath(value))
        result.add(os.path.relpath(absolute, root).replace(os.sep, "/"))
    return sorted(result)


@dataclass
class _Node:
    path: str
    kind: str = "unknown"
    signature: Optional[List[int]] = None
    protected: Optional[str] = None
    complete: bool = True
    children: List["_Node"] = field(default_factory=list)
    files: Dict[Tuple[int, int], int] = field(default_factory=dict)
    fingerprint: str = ""
    has_protected: bool = False


def _git_facts(root: str, paths: List[str]):
    """Batch queries; an unavailable Git supplies no regenerability proof."""
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")
    try:
        tracked = subprocess.run(
            ["git", "-C", root, "ls-files", "--cached", "-z"],
            capture_output=True, timeout=10, env=env)
        if tracked.returncode:
            return set(), set(), "GIT_UNAVAILABLE"
        ignored = subprocess.run(
            ["git", "-C", root, "check-ignore", "--stdin", "-z"],
            input=b"".join(os.fsencode(p) + b"\0" for p in paths),
            capture_output=True, timeout=10, env=env)
        if ignored.returncode not in (0, 1):
            return set(), set(), "GIT_IGNORE_UNAVAILABLE"
    except (OSError, subprocess.TimeoutExpired):
        return set(), set(), "GIT_UNAVAILABLE"
    return ({os.fsdecode(p).replace(os.sep, "/") for p in tracked.stdout.split(b"\0") if p},
            {os.fsdecode(p).replace(os.sep, "/") for p in ignored.stdout.split(b"\0") if p},
            None)


def plan_cleanup(
        workspace: str, *, discard: Iterable[str] = (), protect: Iterable[str] = (),
        max_entries: int = DEFAULT_MAX_ENTRIES, max_depth: int = DEFAULT_MAX_DEPTH,
        ) -> Dict[str, Any]:
    """Propose disjoint targets under a bounded workspace scan.

    Protected subtrees are represented without traversal or size estimates.
    Ordinary unknown subtrees collapse to one review row; generated or
    explicitly discarded children remain visible. Sizes deduplicate hardlinks
    and exclude directory metadata; they do not predict released disk blocks.
    """
    if type(max_entries) is not int or max_entries < 1:
        raise ValueError("max_entries must be a positive integer")
    if type(max_depth) is not int or not 1 <= max_depth <= 128:
        raise ValueError("max_depth must be between 1 and 128")
    root = os.path.realpath(os.path.abspath(os.fspath(workspace)))
    if not os.path.isdir(root) or classifier.protected_ancestor_root(root):
        raise ValueError("provide an existing workspace below a filesystem root")
    discard = _declared_paths(discard, root, must_exist=True)
    protect = sorted(set(_declared_paths(protect, root, must_exist=False)) | PROTECTED_NAMES)
    # A declared path may cross a directory alias. Protect both spellings:
    # deleting a physical ancestor must not cover the declared evidence.
    physical_protection = []
    for path in protect:
        physical = os.path.realpath(os.path.join(root, path))
        if classifier.inside_path(physical, root):
            physical_protection.append(os.path.relpath(physical, root).replace(os.sep, "/"))
    protect = sorted(set(protect + physical_protection))
    trash = os.path.abspath(os.environ.get("AGENT_GUARD_TRASH", os.path.join(root, TRASH_DIRNAME)))
    if classifier.inside_path(trash, root):
        protect = sorted(set(protect + [os.path.relpath(trash, root).replace(os.sep, "/")]))
    ctx = policy.PolicyContext(workspace=root, trash_root=trash, base_dir=root,
                               mode=policy.load_mode(trash)["mode"])
    errors = []
    nodes: List[_Node] = []
    examined = 0

    def problem(node: _Node, code: str) -> None:
        node.complete = False
        record = {"path": node.path, "code": code}
        if record not in errors:
            errors.append(record)

    def visit(relative: str, depth: int) -> _Node:
        nonlocal examined
        node = _Node(relative)
        nodes.append(node)
        absolute = os.path.join(root, relative)
        if (set(relative.split("/")) & PROTECTED_NAMES
                or any(_under(relative, p) for p in protect)):
            node.protected = "protected project or recovery subtree; not traversed"
            physical = os.path.realpath(absolute)
            if physical != absolute and classifier.inside_path(physical, root):
                physical_relative = os.path.relpath(physical, root).replace(os.sep, "/")
                if not any(_under(physical_relative, p) for p in protect):
                    # A nested protected alias was not known before traversal.
                    # Require an upfront declaration before selecting ancestors.
                    problem(node, "PROTECTED_ALIAS_REQUIRES_EXPLICIT_PROTECT")
        else:
            try:
                info = os.lstat(absolute)
                node.signature = [info.st_dev, info.st_ino, info.st_mode,
                                  info.st_size, info.st_mtime_ns, info.st_ctime_ns]
                spec = (classifier.classify_paths(["./" + relative], root, root, trash)[0]
                        if relative != "." else None)
                physical = os.path.relpath(os.path.realpath(absolute), root).replace(os.sep, "/")
                if ((spec and spec.protected) or set(physical.split("/")) & PROTECTED_NAMES
                        or any(_under(physical, p) for p in protect)
                        or (stat.S_ISLNK(info.st_mode)
                            and any(_under(p, relative) for p in protect))):
                    node.protected = "protected boundary or link destination; not traversed"
                elif stat.S_ISDIR(info.st_mode):
                    node.kind = "directory"
                    if depth >= max_depth:
                        problem(node, "DEPTH_LIMIT_REACHED")
                    else:
                        names = []
                        with os.scandir(absolute) as entries:
                            for entry in entries:
                                if examined + len(names) >= max_entries:
                                    problem(node, "ENTRY_LIMIT_REACHED")
                                    break
                                names.append(entry.name)
                        for name in sorted(names):
                            if examined >= max_entries:
                                problem(node, "ENTRY_LIMIT_REACHED")
                                break
                            examined += 1
                            child = visit(name if relative == "." else relative + "/" + name,
                                          depth + 1)
                            node.children.append(child)
                            node.files.update(child.files)
                            node.complete &= child.complete
                        after = os.lstat(absolute)
                        if [after.st_dev, after.st_ino, after.st_mode, after.st_size,
                            after.st_mtime_ns, after.st_ctime_ns] != node.signature:
                            problem(node, "SOURCE_CHANGED_DURING_SCAN")
                elif stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
                    node.kind = "symlink" if stat.S_ISLNK(info.st_mode) else "file"
                    node.files[(info.st_dev, info.st_ino)] = info.st_size
                else:
                    node.protected = "special filesystem entry; manual review required"
            except OSError as exc:
                problem(node, "OS_ERROR_" + str(exc.errno))
        node.has_protected = (bool(node.protected)
                              or any(c.has_protected for c in node.children)
                              or (node.kind == "directory"
                                  and any(_under(p, node.path) for p in protect)))
        node.fingerprint = _digest([node.path, node.signature if not node.protected else None,
                                    node.protected, node.complete,
                                    [c.fingerprint for c in node.children]])
        return node

    # The workspace root is a boundary, but its children are scanned.
    tree = visit(".", 0)
    tracked, ignored, git_warning = _git_facts(
        root, [n.path for n in nodes if n.path != "." and not n.protected])
    tracked_ancestors = set(tracked)
    for path in tracked:
        parts = path.split("/")
        tracked_ancestors.update("/".join(parts[:i]) for i in range(1, len(parts)))

    def row(node: _Node, category: str, reason: str) -> Dict[str, Any]:
        result = {
            "path": node.path, "kind": node.kind, "category": category,
            "size_bytes": None if node.protected else sum(node.files.values()),
            "size_complete": node.complete and not node.has_protected,
            "reason": reason, "selectable": False, "effect": "review",
            "policy_code": None, "fingerprint": node.fingerprint,
        }
        if category == "protected":
            result["effect"] = "keep"
        elif category in ("regenerable", "discarded"):
            # Concrete, relative spelling also used by the execution CLI.
            spelling = "./" + node.path
            specs = classifier.classify_paths([spelling], root, root, trash)
            verdict = policy.decide_path_batch(specs, ctx, recursive=True)
            result["policy_code"] = verdict.code
            literal = (not specs[0].wildcard and not specs[0].indeterminable
                       and specs[0].resolved == os.path.join(root, node.path))
            if literal and not verdict.blocked:
                result["selectable"] = True
                result["effect"] = ("delete" if verdict.code == policy.CODE_ALLOW_REGENERABLE
                                    else "quarantine")
            else:
                result["effect"] = "blocked"
                result["reason"] += "; existing path API or policy refuses execution"
        return result

    def flatten(node: _Node) -> List[Dict[str, Any]]:
        if node.protected:
            return [row(node, "protected", node.protected)]
        if not node.complete and node.path != ".":
            return [row(node, "uncertain", "incomplete scan; not executable")]
        if node.path != "." and node.complete and not node.has_protected:
            if any(_under(node.path, p) for p in discard):
                return [row(node, "discarded", "explicitly declared discarded by caller")]
            if (node.path in ignored and node.path not in tracked_ancestors
                    and policy._matches_artifact(node.path.replace("/", os.sep),
                                                 ctx.config.artifact_patterns)):
                return [row(node, "regenerable", "Git-ignored and matches an artifact pattern")]
        children = [entry for child in node.children for entry in flatten(child)]
        if (node.path == "."
                or any(entry["category"] != "uncertain" for entry in children)):
            return children
        return [row(node, "uncertain", "tracked content or no explicit discard/regeneration evidence")]

    entries = sorted(flatten(tree), key=lambda entry: entry["path"])
    summary = {}
    by_path = {n.path: n for n in nodes}
    for category in CATEGORIES:
        subset = [e for e in entries if e["category"] == category]
        unique_files = {}
        for entry in subset:
            unique_files.update(by_path[entry["path"]].files)
        summary[category] = {"count": len(subset), "size_bytes": sum(unique_files.values())}
    result = {
        "schema_version": 1, "tool": "cleanup", "workspace": root,
        "trash_root": trash, "mode": ctx.mode, "complete": not errors,
        "examined": examined, "options": {"discard": discard, "protect": protect,
                                           "max_entries": max_entries, "max_depth": max_depth},
        "summary": summary, "entries": entries, "errors": errors,
        "protected_names": sorted(PROTECTED_NAMES),
        "warnings": [git_warning] if git_warning else [],
        "size_notice": "Logical bytes, hardlinks counted once per category; protected subtrees omitted. Quarantine retains storage; bytes are not a freed-space estimate.",
    }
    result["plan_id"] = _digest({"plan": result, "tree": tree.fingerprint,
                                 "config": vars(ctx.config)})
    return result


def select_entries(plan: Dict[str, Any], paths: Iterable[str], plan_id: str) -> List[Dict[str, Any]]:
    """Validate the exact reviewed selection before requesting confirmation."""
    if plan_id != plan["plan_id"]:
        raise ValueError("plan changed; review a fresh plan before executing")
    if not plan["complete"]:
        raise ValueError("incomplete scan; resolve scan limits/errors before executing")
    selected = _declared_paths(paths, plan["workspace"], must_exist=True)
    if not selected:
        raise ValueError("--execute requires at least one explicit --select path")
    rows = {entry["path"]: entry for entry in plan["entries"]}
    result = []
    for path in selected:
        entry = rows.get(path)
        if entry is None or not entry["selectable"]:
            raise ValueError("target is not a selectable plan entry: " + path)
        if any(_under(path, other["path"]) for other in result):
            raise ValueError("overlapping selections are not allowed")
        result.append(entry)
    return result
