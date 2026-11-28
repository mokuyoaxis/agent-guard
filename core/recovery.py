"""Recoverability pillar: the Compensation Engine.

Two compensation strategies, chosen by the caller (policy decides which):

  relocate     - move targets into the quarantine tree, preserving their
                 workspace-relative structure under one transaction id
  snapshot_git - capture tracked modifications as a stash commit WITHOUT
                 changing the working tree (git stash create + store), so a
                 subsequent tracked-file changes stay reversible; hard
                 reset also requires the untracked-collision preflight

Everything lands in an append-only manifest (JSONL). Restore is itself
non-destructive: it refuses to overwrite anything that now exists at the
origin path unless the human passes force explicitly.

The quarantine directory must be Git-ignored before it is created. An existing
repository `.gitignore` rule is accepted; otherwise the engine adds a local
`.git/info/exclude` rule without modifying the user's tracked files.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
import time
import errno
import re
from typing import Any, Dict, List, Optional, Tuple

from . import MANIFEST_NAME, TRASH_DIRNAME
from .audit import new_txid, utc_now_iso
from .classifier import PathSpec, _physical, _physical_keep_final, inside_path
from . import admission

# Soft retention policy (docs/references: GC). Thresholds only MARK entries
# GC_ELIGIBLE; actual purging is an explicit maintenance action - a system
# that promises recoverability must never silently destroy its own evidence.
SOFT_RETENTION_DAYS = 30
SOFT_SIZE_LIMIT_BYTES = 5 * 1024 * 1024 * 1024  # 5 GiB

_STORAGE_ERRNOS = {
    getattr(errno, "ENOSPC", None),
    getattr(errno, "EDQUOT", None),
} - {None}

# A transaction directory is one direct child of the quarantine, never a
# caller-supplied path. Accept simple historical identifiers too, but no
# separators or dots that could turn an ID into a path traversal. Restore
# uses the same identifier constraint as GC.
_GC_TXID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")
RESTORE_ITEM_COUNTS = (
    "live_relocate_items", "pending_snapshot_items",
    "retained_snapshot_items", "missing_relocate_items",
)


def _recorded_path_key(path: Any) -> Optional[str]:
    """Compare recorded locations without re-resolving their current parents."""
    if isinstance(path, str) and "\0" not in path and os.path.isabs(path):
        return os.path.normcase(os.path.normpath(path))
    return None


def _snapshot_restore_status(item: Dict[str, Any], applied) -> str:
    if item.get("stored", True) is not True:
        return "failed"
    sha = item.get("sha")
    if not isinstance(sha, str) or not sha:
        return "empty"
    return "already_applied" if sha in applied else "ready"


class StorageUnavailable(OSError):
    """Quarantine cannot accept new relocations (disk full / quota)."""


class RecoveryEngine:
    def __init__(self, workspace: str, trash_root: Optional[str] = None):
        self.workspace = os.path.normpath(os.path.abspath(workspace))
        self.trash_root = os.path.normpath(os.path.abspath(
            trash_root or os.path.join(self.workspace, TRASH_DIRNAME)))

    # ------------------------------------------------------------- layout

    @staticmethod
    def _contained_dest(dest: str, tx_dir: str) -> str:
        """Return dest, refusing anything that escapes trash_root/<txid>/.

        Relocation must be RECOVERABLE: a destination outside the
        transaction directory is evidence that the origin/trash spelling
        pair diverged (the macOS /var link bug) or that a symlink moved
        under us. Never silently write there - raise OSError, which the
        caller turns into a controlled failure plus a manifest record.
        """
        root = _physical(os.path.normpath(os.path.abspath(tx_dir)))
        candidate = _physical(os.path.normpath(os.path.abspath(dest)))
        if candidate == root:
            raise OSError(
                f"refusing to relocate onto the transaction directory: {dest}")
        if os.path.commonpath([candidate, root]) != root:
            raise OSError(
                f"destination escapes quarantine transaction directory "
                f"{root}: {dest}")
        return candidate

    @property
    def manifest_path(self) -> str:
        return os.path.join(self.trash_root, MANIFEST_NAME)

    def ensure_layout(self) -> None:
        # Establish the ignore rule first. If protected Git metadata prevents
        # this, fail without leaving a new untracked quarantine directory.
        self._exclude_from_git()
        os.makedirs(self.trash_root, exist_ok=True)

    def _trash_is_git_ignored(self) -> bool:
        """Whether Git already excludes the quarantine path.

        Codex and other harness sandboxes commonly protect `.git` from writes.
        A repository-level `.gitignore` is sufficient in that environment, so
        do not require a redundant `.git/info/exclude` mutation.
        """
        try:
            rel = os.path.relpath(_physical(self.trash_root),
                                  _physical(self.workspace))
            if rel == os.pardir or rel.startswith(os.pardir + os.sep):
                return True  # external quarantine cannot pollute this repo
            git_rel = rel.replace(os.sep, "/").rstrip("/") + "/"
            proc = subprocess.run(
                ["git", "-C", self.workspace, "check-ignore", "-q",
                 "--no-index", "--", git_rel],
                capture_output=True, timeout=10)
            return proc.returncode == 0
        except (OSError, subprocess.TimeoutExpired, ValueError):
            return False

    def _exclude_from_git(self) -> None:
        """Hide the quarantine from git status via .git/info/exclude."""
        git_marker = os.path.join(self.workspace, ".git")
        if not os.path.exists(git_marker):  # dir or worktree pointer file
            return
        if self._trash_is_git_ignored():
            return
        resolved = subprocess.run(
            ["git", "-C", self.workspace, "rev-parse", "--git-path",
             "info/exclude"], capture_output=True, text=True, timeout=10)
        if resolved.returncode != 0 or not resolved.stdout.strip():
            raise OSError((resolved.stderr or
                           "cannot resolve Git exclude path").strip())
        exclude = resolved.stdout.strip()
        if not os.path.isabs(exclude):
            exclude = os.path.join(self.workspace, exclude)
        info = os.path.dirname(exclude)
        os.makedirs(info, exist_ok=True)
        try:
            with open(exclude, "r", encoding="utf-8") as fh:
                existing = fh.read()
        except OSError:
            existing = ""
        # Both sides physical: an external AGENT_GUARD_TRASH given in a
        # non-physical spelling (symlinked ancestor) would otherwise yield
        # a `..`-laden pattern that excludes the wrong path from git status.
        rel = os.path.relpath(
            _physical(self.trash_root),
            _physical(self.workspace)).replace(os.sep, "/")
        pattern = f"/{rel.strip('/')}/"
        for line in existing.splitlines():
            if line.strip() in (TRASH_DIRNAME, pattern):
                return
        with open(exclude, "a", encoding="utf-8") as fh:
            if existing and not existing.endswith("\n"):
                fh.write("\n")
            fh.write(f"# added by agent-guard (quarantine is not project data)\n")
            fh.write(f"{pattern}\n")

    # ------------------------------------------------------------ manifest

    def _manifest_append(self, records: List[Dict[str, Any]]) -> None:
        self.ensure_layout()
        with open(self.manifest_path, "a", encoding="utf-8") as fh:
            for record in records:
                record.setdefault("ts", utc_now_iso())
                fh.write(json.dumps(record, ensure_ascii=False,
                                    sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def read_manifest(self) -> List[Dict[str, Any]]:
        path = self.manifest_path
        if not os.path.exists(path):
            return []
        out: List[Dict[str, Any]] = []
        with open(path, "r", encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    out.append(json.loads(raw))
                except json.JSONDecodeError:
                    continue
        return out

    # -------------------------------------------------------------- move

    @staticmethod
    def _move(src: str, dest: str) -> Optional[os.stat_result]:
        """Rename or publish a complete copy of a file, directory or link.

        Return the source metadata when a cross-device copy still needs
        source cleanup. Relocation can journal the copy before removing the source.
        """
        try:
            os.rename(src, dest)
        except OSError as exc:
            if exc.errno in _STORAGE_ERRNOS:
                raise StorageUnavailable(
                    f"quarantine storage unavailable: {exc}") from exc
            if exc.errno != errno.EXDEV:
                raise
        else:
            return None

        parent = os.path.dirname(dest)
        try:
            source = os.lstat(src)
            staging = tempfile.mkdtemp(prefix=".agent-guard-stage-", dir=parent)
            try:
                staged = os.path.join(staging, "payload")
                if stat.S_ISLNK(source.st_mode):
                    os.symlink(os.readlink(src), staged)
                elif stat.S_ISDIR(source.st_mode):
                    shutil.copytree(src, staged, symlinks=True)
                else:
                    shutil.copy2(src, staged)
                    with open(staged, "rb") as copied:
                        os.fsync(copied.fileno())
                if os.path.lexists(dest):
                    raise FileExistsError(errno.EEXIST, "destination appeared while copying", dest)
                os.rename(staged, dest)
                if os.name == "posix":
                    descriptor = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                    try:
                        os.fsync(descriptor)
                    finally:
                        os.close(descriptor)
            finally:
                shutil.rmtree(staging, ignore_errors=True)
        except OSError as exc:
            if exc.errno in _STORAGE_ERRNOS:
                raise StorageUnavailable(
                    f"quarantine storage unavailable: {exc}") from exc
            raise
        return source

    @staticmethod
    def _remove_copied_source(src: str, source: os.stat_result) -> None:
        if admission._signature(os.lstat(src)) != admission._signature(source):
            raise OSError(errno.EBUSY, "source changed while copying", src)
        if stat.S_ISDIR(source.st_mode):
            shutil.rmtree(src)
        else:
            os.unlink(src)

    # ----------------------------------------------------------- relocate

    def relocate(self, specs: List[PathSpec], txid: Optional[str] = None,
                 meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Admit one relocation batch before starting its recovery journal."""
        self.ensure_layout()
        paths = [spec.resolved for spec in specs
                 if spec.resolved and not spec.wildcard and not spec.indeterminable]
        with admission.admit(self.workspace, self.trash_root, paths):
            return self._relocate_admitted(specs, txid=txid, meta=meta)

    def _relocate_admitted(self, specs: List[PathSpec], txid: Optional[str] = None,
                          meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Move every concrete target into trash/<txid>/<workspace-relative>.

        Layout arithmetic uses PHYSICAL spellings on BOTH sides: the
        workspace root is realpath-resolved (F8) while ``spec.resolved`` is
        deliberately LEXICAL, so a lexically-spelled target under a
        symlinked ancestor (macOS /var -> /private/var) used to produce a
        multi-segment ``..`` relpath and a destination OUTSIDE the
        transaction directory. ``origin_path`` stays lexical: restore and
        every message must speak the caller's spelling.
        """
        txid = txid or new_txid()
        # Write-ahead journal: no source mutation may happen until the
        # transaction exists durably. Each target intent is durable before its
        # move, so an interrupted completion write remains discoverable.
        self._manifest_append([{
            "type": "tx-start", "txid": txid, "strategy": "relocate",
            "meta": meta or {},
        }])
        moved: List[Dict[str, Any]] = []
        skipped: List[Dict[str, Any]] = []
        storage_failure = False
        for spec in specs:
            src = spec.resolved
            entry_base = {"raw": spec.raw}
            if spec.wildcard or spec.indeterminable or not src:
                skipped.append({**entry_base, "reason": "not concretely resolved"})
                continue
            if not os.path.lexists(src):
                skipped.append({**entry_base, "reason": "already absent"})
                continue
            tx_dir = os.path.join(self.trash_root, txid)
            # Parent chain physical, final component verbatim: a target
            # that is itself a symlink keeps its own location (F9).
            rel = os.path.relpath(_physical_keep_final(src),
                                  _physical(self.workspace))
            dest = os.path.join(tx_dir, rel)
            try:
                # Defence in depth (F9): a symlink race or an exotic
                # spelling must never let a target land outside its own
                # transaction directory. Fail closed, with evidence.
                dest = self._contained_dest(dest, tx_dir)
            except OSError as exc:
                skipped.append({**entry_base,
                                "reason": f"uncontained destination: {exc}"})
                self._manifest_append([{
                    "type": "relocate-failed", "txid": txid,
                    "origin_path": src, "trash_path": dest,
                    "reason": str(exc),
                }])
                continue
            paths = {"txid": txid, "origin_path": src, "trash_path": dest}
            self._manifest_append([{**paths, "type": "relocate-intent", "raw": spec.raw}])
            try:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                copied_source = self._move(src, dest)
            except StorageUnavailable:
                # Hard principle: capacity limits never downgrade to
                # permanent deletion. Abort; the failed target stays at its
                # origin untouched; callers map this to BLOCK.
                storage_failure = True
                self._manifest_append([{
                    **paths, "type": "relocate-failed",
                    "reason": "storage unavailable",
                }])
                break
            except OSError as exc:
                self._manifest_append([{
                    **paths, "type": "relocate-failed",
                    "reason": str(exc),
                }])
                raise
            # A complete copy is recoverable even if clearing the source
            # fails. An interrupted completion write leaves only the intent.
            self._manifest_append([{**paths, "type": "relocate"}])
            if copied_source is not None:
                try:
                    self._remove_copied_source(src, copied_source)
                except OSError as exc:
                    reason = f"source cleanup failed after publishing the quarantine copy ({exc})"
                    self._manifest_append([{**paths, "type": "relocate-failed", "reason": reason}])
                    skipped.append({**entry_base, "reason": reason})
                    break
            moved.append({"origin": src, "trash": dest})
        return {"txid": txid, "moved": moved, "skipped": skipped,
                "storage_failure": storage_failure}

    # ------------------------------------------------------ git snapshot

    def preflight_git_reset_hard(
            self, cwd: Optional[str] = None, target: str = "HEAD",
            ) -> Tuple[List[str], str]:
        """Read-only check for content a tracked-only stash cannot protect.

        Compare the target tree with Git's untracked paths (including ignored
        paths and nested repositories). A collision includes either side
        being an ancestor of the other: Git can replace a directory with a
        file, or remove a file/symlink to create a directory. Refuse such a
        reset; do not move originals or claim their bytes are in the stash.
        Errors are static so callers can report them without leaking paths.
        """
        cwd = cwd or self.workspace
        git_cwd = cwd
        if re.match(r"(?:refs/)?stash(?:$|[~^@])", target):
            return [], "Stash-relative targets change when a snapshot is stored"

        def git(*args: str, optional: bool = False) -> bytes:
            proc = subprocess.run(["git", "-C", git_cwd, *args],
                                  capture_output=True, timeout=30)
            if proc.stderr or (proc.returncode != 0 and not
                               (optional and proc.returncode == 1)):
                raise ValueError("Git preflight command failed")
            return proc.stdout

        def paths(data: bytes) -> List[str]:
            if data and not data.endswith(b"\0"):
                raise ValueError("incomplete Git path enumeration")
            return [os.fsdecode(p) for p in data.split(b"\0") if p]

        def parents(path: str) -> List[str]:
            parts = path.split("/")
            return ["/".join(parts[:i]) for i in range(1, len(parts))]

        try:
            # Whole-tree reset cannot be confined to a narrower workspace.
            root = os.fsdecode(git("rev-parse", "--show-toplevel")).rstrip("\n")
            if _physical(root) != _physical(self.workspace):
                return [], "Git repository does not match the workspace"
            # ls-files is scoped by its working directory. Reset is always
            # whole-tree, even when invoked from a repository subdirectory.
            git_cwd = root
            sha = git("rev-parse", "--verify", "--end-of-options",
                      target + "^{commit}").strip().decode("ascii")
            tree = paths(git("ls-tree", "-r", "-z", "--full-tree", sha))
            index = paths(git("ls-files", "--stage", "-z"))
            # Submodule working trees are not captured by stash create.
            if any(row.startswith("160000 ") for row in tree + index):
                return [], "Submodule reset effects cannot be protected"
            ignorecase = git("config", "--type=bool", "--get", "core.ignorecase",
                             optional=True).strip() == b"true"

            def key(path: str) -> str:
                return path.casefold() if ignorecase else path

            leaves = {key(row.split("\t", 1)[1]) for row in tree}
            directories = {p for leaf in leaves for p in parents(leaf)}
            # Intentionally omit --exclude-standard: ignored data is at risk
            # too. -z preserves newlines, quoting, and non-ASCII path bytes.
            others = paths(git("ls-files", "--others", "-z"))

            def collides(path: str) -> bool:
                path = key(path.rstrip("/"))
                return (path in leaves or path in directories or
                        any(p in leaves for p in parents(path)))

            collisions = [p for p in others if collides(p)]
            # Include future audit/snapshot files even before layout exists.
            # Preserve both storage and its configured spelling: replacing
            # a tracked symlink to external trash would sever recovery access.
            for trash_path in {self.trash_root, _physical(self.trash_root)}:
                for workspace_path in {self.workspace, _physical(self.workspace)}:
                    trash = os.path.relpath(trash_path, workspace_path)
                    if trash == ".":
                        return [], "Quarantine overlaps the workspace root"
                    if trash == os.pardir or trash.startswith(os.pardir + os.sep):
                        continue
                    trash = trash.replace(os.sep, "/")
                    if collides(trash) or any(p.startswith(key(trash) + "/")
                                             for p in leaves):
                        collisions.append(trash)
            return sorted(set(collisions)), ""
        except (OSError, subprocess.TimeoutExpired, ValueError, IndexError):
            return [], "Git hard reset preflight could not determine effects"

    def snapshot_git(self, cwd: Optional[str] = None,
                     txid: Optional[str] = None,
                     meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Stash-create tracked modifications without touching the tree.

        Returns sha=None when there is nothing to snapshot (clean tree) -
        that is success, not failure.
        """
        cwd = cwd or self.workspace
        txid = txid or new_txid()
        create = subprocess.run(["git", "-C", cwd, "stash", "create"],
                                capture_output=True, text=True, timeout=30)
        if create.returncode != 0:
            return {"ok": False, "clean": False, "txid": txid,
                    "sha": None, "stored": False,
                    "error": (create.stderr or
                              "git stash create failed").strip()}
        sha = create.stdout.strip()
        if not sha:
            self._manifest_append([{
                "type": "snapshot", "txid": txid, "sha": None,
                "note": "working tree clean; nothing to snapshot",
                "meta": meta or {},
            }])
            return {"ok": True, "clean": True, "txid": txid,
                    "sha": None, "stored": False, "error": ""}
        store = subprocess.run(
            ["git", "-C", cwd, "stash", "store", "-m", f"agent-guard:{txid}", sha],
            capture_output=True, text=True, timeout=30)
        stored = store.returncode == 0
        error = "" if stored else (
            store.stderr or "git stash store failed").strip()
        self._manifest_append([{
            "type": "snapshot", "txid": txid, "sha": sha,
            "stored": stored, "error": error, "meta": meta or {},
        }])
        return {"ok": stored, "clean": False, "txid": txid, "sha": sha,
                "stored": stored, "error": error}

    # --------------------------------------------------- git clean enum

    @staticmethod
    def _decode_git_quoted_path(value: str) -> str:
        """Decode Git's double-quoted C-style path representation."""
        if len(value) < 2 or not (value.startswith('"') and
                                  value.endswith('"')):
            return value
        data = value[1:-1]
        result = bytearray()
        escapes = {
            "a": 7, "b": 8, "t": 9, "n": 10, "v": 11,
            "f": 12, "r": 13, "\\": 92, '"': 34,
        }
        i = 0
        while i < len(data):
            ch = data[i]
            if ch != "\\":
                result.extend(os.fsencode(ch))
                i += 1
                continue
            i += 1
            if i >= len(data):
                raise ValueError("trailing backslash in Git-quoted path")
            esc = data[i]
            if esc in "01234567":
                digits = esc
                i += 1
                while i < len(data) and len(digits) < 3 and data[i] in "01234567":
                    digits += data[i]
                    i += 1
                result.append(int(digits, 8))
                continue
            if esc not in escapes:
                raise ValueError(f"unknown Git path escape: \\{esc}")
            result.append(escapes[esc])
            i += 1
        return os.fsdecode(bytes(result))

    def enumerate_git_clean(self, cwd: Optional[str],
                            clean_flags: List[str],
                            targets: Optional[List[str]] = None,
                            ) -> Tuple[List[str], str]:
        """Dry-run `git clean` mirroring the caller's scope flags.

        Returns (untracked_paths, stderr). Nested repositories are reported
        by git as 'Would skip' and are intentionally left alone.
        """
        cwd = cwd or self.workspace
        mirror = ["-n"]
        allowed = {"-d", "-x", "-X", "--directory", "--ignored"}
        for flag in clean_flags:
            if flag in allowed:
                mirror.append(flag)
        if targets:
            mirror.extend(["--", *targets])
        env = dict(os.environ)
        env["LC_ALL"] = "C"
        proc = subprocess.run(["git", "-C", cwd, "clean"] + mirror,
                              capture_output=True, text=True, timeout=60,
                              env=env)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or
                      "git clean dry-run failed").strip()
            return [], detail
        paths: List[str] = []
        unparsed: List[str] = []
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("Would skip repository "):
                continue
            if not line.startswith("Would remove "):
                unparsed.append(line)
                continue
            target = line[len("Would remove "):].strip()
            try:
                paths.append(self._decode_git_quoted_path(target))
            except ValueError as exc:
                return [], f"cannot decode git clean path {target!r}: {exc}"
        if unparsed:
            return [], "unrecognized git clean dry-run output: " + \
                "; ".join(unparsed[:3])
        return paths, proc.stderr.strip()

    # ------------------------------------------------------------- restore

    def transactions(self, records: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Dict[str, Any]]:
        """Group records and reconcile recorded copy paths (insertion order).

        Bounded query readers may supply validated records. Restore/GC and
        existing callers omit them and retain the full-manifest behavior.
        """
        out: Dict[str, Dict[str, Any]] = {}
        for record in self.read_manifest() if records is None else records:
            txid = record.get("txid") if isinstance(record, dict) else None
            if not isinstance(txid, str) or not txid:
                continue
            slot = out.setdefault(txid, {"txid": txid, "items": []})
            if record.get("type") == "tx-start":
                slot["meta"] = record.get("meta", {})
                slot["ts"] = record.get("ts")
            elif record.get("type") in ("relocate", "snapshot"):
                slot["items"].append(record)
            elif record.get("type") == "relocate-intent":
                slot.setdefault("_intents", []).append(record)
            elif record.get("type") == "relocate-failed":
                slot.setdefault("failures", []).append(record)
            elif record.get("type") == "restore":
                slot["last_restore"] = record
                restored = record.get("restored")
                if isinstance(restored, list):
                    slot.setdefault("_restore_receipts", set()).update(
                        value for value in restored if isinstance(value, str))
            elif record.get("type") == "purged":
                slot["purged"] = True

        for slot in out.values():
            self._reconcile_transaction(slot)
        return out

    @staticmethod
    def _reconcile_transaction(slot: Dict[str, Any]) -> None:
        items = slot["items"]
        purged = bool(slot.get("purged"))
        completed = {
            (item.get("origin_path"), item.get("trash_path"))
            for item in items if item.get("type") == "relocate"
            and all(isinstance(item.get(field), str) for field in ("origin_path", "trash_path"))
        }
        failed = {
            (item["origin_path"], item["trash_path"])
            for item in slot.get("failures", ())
            if all(isinstance(item.get(field), str) for field in ("origin_path", "trash_path"))
        }
        intents = {}
        for intent in slot.pop("_intents", ()):
            pair = (intent.get("origin_path"), intent.get("trash_path"))
            if all(_recorded_path_key(path) is not None for path in pair):
                if pair not in completed and pair not in failed:
                    intents.setdefault(pair, intent)

        # Legacy receipts identify a relocation by origin. Attribute them
        # only when that origin identifies a single recorded copy in this tx.
        origin_counts = Counter(_recorded_path_key(origin)
                                for origin, _ in completed | intents.keys())
        receipts = slot.pop("_restore_receipts", ())
        restored = {_recorded_path_key(receipt) for receipt in receipts}
        restored = {origin for origin in restored if origin is not None and origin_counts[origin] == 1}
        for (origin, trash), intent in intents.items():
            if _recorded_path_key(origin) in restored or (not purged and os.path.lexists(trash)):
                items.append({**intent, "type": "relocate", "recovered_from_intent": True})

        applied = sorted({
            item["sha"] for item in items
            if item.get("type") == "snapshot" and _snapshot_restore_status(item, ()) == "ready"
            and f"stash:{item['sha']}" in receipts
        })
        origin_status = {}
        live_positions = []
        snapshot_failed = False
        slot.update(dict.fromkeys(RESTORE_ITEM_COUNTS, 0))
        for position, item in enumerate(items):
            if item.get("type") == "snapshot":
                status = _snapshot_restore_status(item, applied)
                if status == "already_applied":
                    slot["retained_snapshot_items"] += 1
                elif status == "ready" and not purged:
                    slot["pending_snapshot_items"] += 1
                snapshot_failed |= status == "failed"
            else:
                origin = _recorded_path_key(item.get("origin_path"))
                copy = item.get("trash_path")
                present = not purged and isinstance(copy, str) and os.path.lexists(copy)
                if origin in restored:
                    origin_status[origin] = "blocked" if present else "already_restored"
                elif present:
                    live_positions.append(position)
                elif not purged:
                    slot["missing_relocate_items"] += 1

        unconfirmed = failed - completed if not purged else set()
        slot["manual_review_items"] = len(unconfirmed)
        slot["unconfirmed_payload_exists"] = any(os.path.lexists(trash) for _, trash in unconfirmed)
        slot["live_relocate_items"] = len(live_positions)
        slot["restorable_items"] = slot["live_relocate_items"] + slot["pending_snapshot_items"]
        slot["_live_relocate_positions"] = live_positions
        slot["_restore_origins"] = origin_status
        slot["_applied_stashes"] = applied
        # A failed move without a residual copy stays in the diagnostic count.
        unresolved = bool(slot["unconfirmed_payload_exists"] or snapshot_failed
                          or slot["missing_relocate_items"] or "blocked" in origin_status.values())
        last = slot.get("last_restore", {})
        if purged:
            slot["state"] = "PURGED"
        elif slot["restorable_items"]:
            slot["state"] = "RESTORABLE"
        elif (restored or applied) and not unresolved:
            slot["state"] = "RESTORED"
        elif unresolved or slot.get("failures") or last.get("errors") or last.get("conflicts"):
            slot["state"] = "FAILED"
        else:
            slot["state"] = "EMPTY"

    def _validate_restore_target(self, origin: str) -> str:
        """Check the parent chain, retaining the final symlink's location.

        Recorded paths are absolute and normalized. Resolve both sides for
        comparison so workspace aliases remain valid, but refuse a parent
        that now points outside. This preflight is not an atomic filesystem
        boundary against concurrent changes.
        """
        if (not isinstance(origin, str) or not origin or "\0" in origin or
                not os.path.isabs(origin) or os.path.normpath(origin) != origin):
            raise ValueError("invalid restore target path")
        root = os.path.normcase(_physical(self.workspace))
        target = os.path.normcase(_physical_keep_final(origin))
        if target == root or not inside_path(target, root):
            raise ValueError("restore target is outside workspace")
        return target

    def _restore_transaction_dir(self, txid: str) -> str:
        """Bind an ID to its own direct child of the selected quarantine."""
        if not isinstance(txid, str) or not _GC_TXID_RE.fullmatch(txid):
            raise ValueError("invalid quarantine transaction id")
        tx_dir = os.path.join(self.trash_root, txid)
        expected = os.path.join(_physical(self.trash_root), txid)
        if (os.path.islink(tx_dir) or
                os.path.normcase(_physical(tx_dir)) != os.path.normcase(expected)):
            raise ValueError("uncontained quarantine transaction directory")
        return tx_dir

    def _validate_restore_source(self, trash: str, txid: str) -> None:
        """Check the source parent chain without following a leaf link."""
        tx_dir = self._restore_transaction_dir(txid)
        if (not isinstance(trash, str) or not trash or "\0" in trash or
                not os.path.isabs(trash) or os.path.normpath(trash) != trash):
            raise ValueError("invalid restore source path")
        root = os.path.normcase(_physical(tx_dir))
        source = os.path.normcase(_physical_keep_final(trash))
        if source == root or not inside_path(source, root):
            raise ValueError("restore source is outside its transaction")

    def _validate_restore_preservation(self, origin: str) -> None:
        """Never relocate the quarantine or an alias used to reach it."""
        current = _physical_keep_final(origin)
        pairs = ((origin, self.trash_root),
                 (current, _physical_keep_final(self.trash_root)),
                 (current, _physical(self.trash_root)))
        for target, storage in pairs:
            target, storage = os.path.normcase(target), os.path.normcase(storage)
            if inside_path(target, storage) or inside_path(storage, target):
                raise ValueError("force restore target overlaps quarantine storage")

    @staticmethod
    def _preview_entry_metadata(path: str) -> Dict[str, Any]:
        """Inspect one path without following symlinks or reading contents."""
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            return {"kind": "missing", "size_bytes": None, "mtime_ns": None}
        except OSError:
            return {"kind": "unreadable", "size_bytes": None, "mtime_ns": None}
        mode = info.st_mode
        if stat.S_ISLNK(mode):
            kind = "symlink"
        elif stat.S_ISREG(mode):
            kind = "file"
        elif stat.S_ISDIR(mode):
            kind = "dir"
        else:
            kind = "special"
        return {
            "kind": kind,
            "size_bytes": info.st_size if kind == "file" else None,
            "mtime_ns": info.st_mtime_ns,
        }

    @staticmethod
    def _preview_regular_sha256(path: str, max_bytes: int) -> Optional[str]:
        """Bounded, no-follow SHA-256 for internal equality checks only (never emitted)."""
        flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                 | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
        try:
            fd = os.open(path, flags)
        except OSError:
            return None
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > max_bytes:
                return None
            digest = hashlib.sha256()
            total = 0
            while True:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    return None
                digest.update(chunk)
            return digest.hexdigest()
        except OSError:
            return None
        finally:
            os.close(fd)

    @classmethod
    def _compare_preview_entries(cls, trash: str, origin: str,
                                 q_meta: Dict[str, Any], o_meta: Dict[str, Any],
                                 hash_max_bytes: int) -> Dict[str, Any]:
        """Compare quarantine copy and current occupant without leaking content or hashes."""
        same_kind = q_meta["kind"] == o_meta["kind"]
        same_size = (
            q_meta["size_bytes"] == o_meta["size_bytes"]
            if same_kind and q_meta["kind"] == "file" else None
        )
        if not same_kind:
            return {"same_kind": False, "same_size": None,
                    "content_equal": False, "basis": "kind_diff"}
        kind = q_meta["kind"]
        if kind == "dir":
            return {"same_kind": True, "same_size": None,
                    "content_equal": None, "basis": "directory_uncompared"}
        if kind == "symlink":
            return {"same_kind": True, "same_size": None,
                    "content_equal": None, "basis": "symlink_target_uncompared"}
        if kind != "file":
            return {"same_kind": True, "same_size": None,
                    "content_equal": None, "basis": "special_uncompared"}
        if not same_size:
            return {"same_kind": True, "same_size": False,
                    "content_equal": False, "basis": "size_diff"}
        if q_meta["size_bytes"] is None or q_meta["size_bytes"] > hash_max_bytes:
            return {"same_kind": True, "same_size": True,
                    "content_equal": None, "basis": "exceeds_hash_cap"}
        left = cls._preview_regular_sha256(trash, hash_max_bytes)
        right = cls._preview_regular_sha256(origin, hash_max_bytes)
        if left is None or right is None:
            return {"same_kind": True, "same_size": True,
                    "content_equal": None, "basis": "unreadable"}
        return {"same_kind": True, "same_size": True,
                "content_equal": left == right, "basis": "sha256_regular_file"}

    def _restore_parent_blocker(self, origin: str) -> Optional[str]:
        """Return a reason when an existing ancestor of origin blocks restoring into it."""
        parent = os.path.dirname(origin)
        if not parent or parent == origin:
            return None
        root = os.path.normcase(_physical(self.workspace))
        chain: List[str] = []
        current = parent
        while current and os.path.normcase(_physical_keep_final(current)) != root:
            chain.append(current)
            nxt = os.path.dirname(current)
            if nxt == current:
                break
            current = nxt
        for ancestor in reversed(chain):
            try:
                info = os.lstat(ancestor)
            except FileNotFoundError:
                return None
            except OSError:
                return f"restore target parent path is not accessible: {ancestor}"
            mode = info.st_mode
            if stat.S_ISLNK(mode):
                try:
                    target_info = os.stat(ancestor)
                except OSError:
                    return f"restore target parent is a broken or unreadable symlink: {ancestor}"
                if not stat.S_ISDIR(target_info.st_mode):
                    return f"restore target parent is not a directory: {ancestor}"
            elif not stat.S_ISDIR(mode):
                return f"restore target parent is not a directory: {ancestor}"
        return None

    @staticmethod
    def _restore_review_reason(tx: Dict[str, Any]) -> Optional[str]:
        count = tx["manual_review_items"]
        if count and (not tx["items"] or tx["unconfirmed_payload_exists"]):
            return f"manual review required for {count} failed relocation item(s) without a completion record"
        return None

    def _restore_selection(self, tx, paths, *, cwd=None):
        if paths is None:
            return tx["items"], {}
        selection = {"selected_paths": []}
        if (not isinstance(paths, (list, tuple)) or not paths
                or any(not isinstance(path, str) or not path or "\0" in path for path in paths)):
            return [], {**selection, "error": "restore paths must be nonempty literal paths"}
        try:
            base = os.path.abspath(cwd) if cwd is not None else self.workspace
        except (OSError, TypeError, ValueError):
            return [], {**selection, "error": "restore selection is outside workspace or cannot be resolved"}

        index = {}
        for position, item in enumerate(tx["items"]):
            if item.get("type") != "relocate":
                continue
            origin = item.get("origin_path")
            key = _recorded_path_key(origin)
            if key is None:
                continue
            index.setdefault(key, set()).add(position)
            # A literal match still has to pass the filesystem preflight.
            try:
                physical = os.path.normcase(_physical_keep_final(origin))
            except (OSError, ValueError):
                continue
            index.setdefault(physical, set()).add(position)

        positions = set()
        unmatched, ambiguous, already_restored = [], [], []
        for path in paths:
            try:
                absolute = os.path.normpath(os.path.join(base, path))
                physical = self._validate_restore_target(absolute)
            except (OSError, ValueError):
                return [], {**selection, "error": "restore selection is outside workspace or cannot be resolved"}
            matches = index.get(os.path.normcase(absolute), set()) | index.get(physical, set())
            if not matches:
                unmatched.append(path)
            elif len(matches) != 1:
                ambiguous.append(path)
            else:
                position, = matches
                origin = tx["items"][position]["origin_path"]
                if _recorded_path_key(origin) in tx["_restore_origins"]:
                    already_restored.append(origin)
                else:
                    positions.add(position)

        problems = []
        for field, values, reason in (
            ("unmatched_paths", unmatched, "restore paths did not match any relocation entry"),
            ("ambiguous_paths", ambiguous, "restore paths matched more than one relocation entry"),
            ("already_restored", already_restored, "selected relocation has already been restored"),
        ):
            if values:
                selection[field] = list(dict.fromkeys(values))
                problems.append(reason)
        if problems:
            return [], {**selection, "error": ". ".join(problems)}

        items = [tx["items"][position] for position in sorted(positions)]
        selection.update(
            selected_paths=[item["origin_path"] for item in items],
            omitted_relocate_count=sum(position not in positions for position in tx["_live_relocate_positions"]),
            omitted_snapshot_count=tx["pending_snapshot_items"],
        )
        return items, selection

    def _inspect_restore_item(self, txid, tx, item, tx_dir_ok):
        if item.get("type") == "snapshot":
            status = _snapshot_restore_status(item, tx["_applied_stashes"])
            notes = {
                "ready": "git stash apply; stored stash is retained",
                "already_applied": "stash already applied and retained",
                "empty": "working tree was clean; nothing to apply",
                "failed": "Git snapshot was not stored",
            }
            return {"type": "snapshot", "sha": item.get("sha"), "status": status, "note": notes[status]}

        origin, trash = item.get("origin_path"), item.get("trash_path")
        row = {
            "type": "relocate", "origin_path": origin, "trash_path": trash,
            "recovered_from_intent": bool(item.get("recovered_from_intent")),
            "status": "ready", "quarantine_entry": None, "occupant_entry": None,
            "comparison": None, "force_preservable": False, "note": "",
        }
        history = tx["_restore_origins"].get(_recorded_path_key(origin))
        if not tx_dir_ok:
            row.update(status="boundary_error", note="restore source is outside its transaction or cannot be resolved")
        elif history == "already_restored":
            row.update(status=history, note="already restored according to the recovery journal")
        elif history == "blocked":
            row.update(status=history, note="a copy reappeared after restore; manual review required")
        else:
            try:
                self._validate_restore_target(origin)
            except (OSError, ValueError):
                row.update(status="boundary_error", note="restore target is outside workspace or cannot be resolved")
                return row
            try:
                self._validate_restore_source(trash, txid)
            except (OSError, ValueError):
                row.update(status="boundary_error", note="restore source is outside its transaction or cannot be resolved")
                return row

            q_meta = self._preview_entry_metadata(trash)
            parent_blocker = self._restore_parent_blocker(origin)
            o_meta = self._preview_entry_metadata(origin)
            row.update(quarantine_entry=q_meta, occupant_entry=o_meta)
            if q_meta["kind"] == "missing":
                row.update(status="missing_copy", note="quarantine copy is missing")
            elif parent_blocker is not None:
                row.update(status="blocked", note=parent_blocker)
            elif q_meta["kind"] == "unreadable":
                row.update(status="blocked", note=f"quarantine copy is unreadable: {trash}")
            elif o_meta["kind"] == "unreadable":
                row.update(status="blocked", note=f"restore target path is not accessible: {origin}")
            elif o_meta["kind"] != "missing":
                row["status"] = "conflict"
                try:
                    self._validate_restore_preservation(origin)
                except (OSError, ValueError):
                    row["note"] = "force restore target overlaps quarantine storage"
                else:
                    row.update(force_preservable=True,
                               note="origin path is occupied; ordinary restore refuses, --force preserves occupant first")
            else:
                row.update(force_preservable=True, note="ready to move back to origin path")
        return row

    @staticmethod
    def _restore_selection_overlaps(tx, items):
        def locations(item):
            return [os.path.normcase(_physical_keep_final(item[field]))
                    for field in ("origin_path", "trash_path")
                    if _recorded_path_key(item.get(field)) is not None]

        selected_ids = {id(item) for item in items}
        selected = [path for item in items for path in locations(item)]
        others = [path for item in tx["items"] if item.get("type") == "relocate"
                  and id(item) not in selected_ids for path in locations(item)]
        return any(inside_path(path, other) or inside_path(other, path)
                   for path in selected for other in others)

    def _restore_preflight(self, txid, tx, items, *, selective):
        errors = []
        try:
            self._restore_transaction_dir(txid)
        except (OSError, ValueError):
            errors.append("restore source is outside its transaction or cannot be resolved")
        tx_dir_ok = not errors
        review_reason = self._restore_review_reason(tx)
        if review_reason:
            errors.append(review_reason)

        rows = [self._inspect_restore_item(txid, tx, item, tx_dir_ok) for item in items]
        blocking = {"blocked", "failed"}
        if selective and tx_dir_ok:
            blocking.update(("boundary_error", "missing_copy"))
        errors.extend(row["note"] for row in rows if row["status"] in blocking)
        if selective and not errors:
            try:
                overlaps = self._restore_selection_overlaps(tx, items)
            except (OSError, ValueError):
                errors.append("restore selection scope could not be resolved")
            else:
                if overlaps:
                    errors.append("restore selection overlaps an unselected relocation entry")
        return rows, errors, tx_dir_ok, bool(review_reason)

    def preview_restore(self, txid: str, *, force: bool = False,
                        hash_max_bytes: int = 4 * 1024 * 1024, paths=None,
                        cwd: Optional[str] = None) -> Dict[str, Any]:
        """Read-only restore preflight and conflict comparison; writes nothing."""
        from .messages import restore_guidance

        schema = "restore_preview/v1"
        refused = {"schema": schema, "ok": False}
        if paths is not None:
            refused.update(selected_paths=[], can_restore=False, can_restore_with_force=False)
        if not isinstance(txid, str) or not _GC_TXID_RE.fullmatch(txid):
            return {**refused, "error": "invalid quarantine transaction id"}
        tx = self.transactions().get(txid)
        if not tx:
            return {**refused, "error": f"unknown transaction: {txid}"}
        if tx.get("purged"):
            return {**refused, "state": "PURGED", "error": "quarantine transaction was purged"}

        items, selection = self._restore_selection(tx, paths, cwd=cwd)
        if selection.get("error"):
            return {**refused, **selection}
        rows, errors, tx_dir_ok, manual_review_blocks = self._restore_preflight(
            txid, tx, items, selective=paths is not None)

        completed = {(item.get("origin_path"), item.get("trash_path"))
                     for item in tx["items"] if item.get("type") == "relocate"}
        failed_map = {}
        for failure in tx.get("failures", ()):
            origin, trash = failure.get("origin_path"), failure.get("trash_path")
            if isinstance(origin, str) and isinstance(trash, str) and (origin, trash) not in completed:
                failed_map[(origin, trash)] = str(failure.get("error") or "relocation failed")
        manual_review_rows = [
            {"origin_path": origin, "trash_path": trash,
             "payload_exists": os.path.lexists(trash), "error": failed_map[(origin, trash)]}
            for origin, trash in sorted(failed_map)
        ]

        conflicts = [row for row in rows if row["status"] == "conflict"]
        identical = 0
        for row in conflicts:
            comparison = self._compare_preview_entries(
                row["trash_path"], row["origin_path"], row["quarantine_entry"],
                row["occupant_entry"], hash_max_bytes)
            row["comparison"] = comparison
            if row["force_preservable"] and comparison["content_equal"] is True:
                row["note"] = "occupant matches quarantine copy byte-for-byte; --force preserves occupant first"
                identical += 1

        relocations = Counter(row["status"] for row in rows if row["type"] == "relocate")
        snapshots = Counter(row["status"] for row in rows if row["type"] == "snapshot")
        summary = {
            "total_items": len(items),
            "ready_count": relocations["ready"],
            "conflict_count": len(conflicts),
            "identical_conflict_count": identical,
            "missing_copy_count": relocations["missing_copy"],
            "boundary_error_count": relocations["boundary_error"],
            "blocked_count": relocations["blocked"],
            "snapshot_count": snapshots["ready"],
            "manual_review_count": len(manual_review_rows),
            "already_restored_count": relocations["already_restored"],
            "retained_snapshot_count": snapshots["already_applied"],
        }
        has_actionable = bool(relocations["ready"] or conflicts or snapshots["ready"])
        has_blocking = bool(errors or relocations["boundary_error"] or relocations["missing_copy"])
        all_conflicts_preservable = all(row["force_preservable"] for row in conflicts)
        can_restore = not has_blocking and not conflicts and has_actionable
        can_restore_with_force = not has_blocking and has_actionable and all_conflicts_preservable
        if force and conflicts and not all_conflicts_preservable:
            errors.append("force restore target overlaps quarantine storage or cannot be resolved")
        return {
            "schema": schema,
            "ok": tx_dir_ok and not relocations["boundary_error"],
            "txid": txid,
            "ts": tx.get("ts"),
            "state": tx["state"],
            "workspace": self.workspace,
            "trash_root": self.trash_root,
            "force_requested": bool(force),
            "can_restore": can_restore,
            "can_restore_with_force": can_restore_with_force,
            "summary": summary,
            "items": rows,
            "manual_review": manual_review_rows,
            "errors": errors,
            **selection,
            "next_step": restore_guidance(
                txid, self.workspace, self.trash_root,
                can_restore=can_restore,
                can_restore_with_force=can_restore_with_force,
                has_conflicts=bool(conflicts),
                has_manual_review=manual_review_blocks,
                selected_paths=selection.get("selected_paths"),
            ),
        }

    def _legacy_restore_boundary_error(self, txid, items, force):
        try:
            self._restore_transaction_dir(txid)
        except (OSError, ValueError):
            return "restore source is outside its transaction or cannot be resolved"
        relocations = [item for item in items if item.get("type") == "relocate"]
        try:
            for item in relocations:
                self._validate_restore_target(item.get("origin_path"))
        except (OSError, ValueError):
            return "restore target is outside workspace or cannot be resolved"
        try:
            for item in relocations:
                self._validate_restore_source(item.get("trash_path"), txid)
        except (OSError, ValueError):
            return "restore source is outside its transaction or cannot be resolved"
        if force:
            try:
                for item in relocations:
                    if os.path.lexists(item["origin_path"]):
                        self._validate_restore_preservation(item["origin_path"])
            except (OSError, ValueError):
                return "force restore target overlaps quarantine storage or cannot be resolved"
        return None

    def _prune_restored_parents(self, txid, tx, moved_sources):
        """Remove only empty, unowned parents left by journaled moves."""
        if not moved_sources:
            return
        try:
            root = os.path.normcase(_physical(self._restore_transaction_dir(txid)))
            protected = {
                os.path.normcase(_physical_keep_final(item["trash_path"]))
                for group in (tx["items"], tx.get("failures", ())) for item in group
                if _recorded_path_key(item.get("trash_path")) is not None
            }
        except (OSError, ValueError):
            return
        for source in moved_sources:
            parent = os.path.dirname(source)
            while parent:
                try:
                    location = os.path.normcase(_physical_keep_final(parent))
                    if location in protected or not inside_path(location, root):
                        break
                    info = os.lstat(parent)
                    attributes = getattr(info, "st_file_attributes", 0)
                    if not stat.S_ISDIR(info.st_mode) or attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                        break
                    os.rmdir(parent)
                except (OSError, ValueError):
                    break
                if location == root:
                    break
                parent = os.path.dirname(parent)

    def restore(self, txid: str, force: bool = False,
                cwd: Optional[str] = None, *, paths=None) -> Dict[str, Any]:
        """Undo one transaction or preflight and restore a selection of entries.

        Relocations return to their recorded origins; Git snapshots are
        applied and retained. Force preserves an existing occupant in a
        separate transaction first. backup_txids records those attempts.
        """
        refused = {"ok": False}
        if paths is not None:
            refused.update(selected_paths=[], restored=[])
        if not isinstance(txid, str) or not _GC_TXID_RE.fullmatch(txid):
            return {**refused, "error": "invalid quarantine transaction id"}
        transactions = self.transactions()
        tx = transactions.get(txid)
        if not tx:
            return {**refused, "error": f"unknown transaction: {txid}"}
        if tx.get("purged"):
            return {**refused, "state": "PURGED", "error": "quarantine transaction was purged"}

        items, selection = self._restore_selection(tx, paths, cwd=cwd)
        if selection.get("error"):
            return {**refused, **selection}
        restored: List[str] = []
        conflicts: List[str] = []
        errors: List[str] = []
        report = {"ok": False, "restored": restored, "conflicts": conflicts, "errors": errors, **selection}
        if paths is not None:
            rows, blocking, _, _ = self._restore_preflight(txid, tx, items, selective=True)
            errors.extend(blocking)
            occupied = [row for row in rows if row["status"] == "conflict"]
            if force and any(not row["force_preservable"] for row in occupied):
                errors.append("force restore target overlaps quarantine storage or cannot be resolved")
            if not force:
                conflicts.extend(row["origin_path"] for row in occupied)
            if errors or conflicts:
                return report
        else:
            review_reason = self._restore_review_reason(tx)
            if review_reason:
                errors.append(review_reason)
            pending = []
            for item in items:
                if item.get("type") == "relocate":
                    origin = item.get("origin_path")
                    status = tx["_restore_origins"].get(_recorded_path_key(origin))
                    if status == "already_restored":
                        report.setdefault("already_restored", []).append(origin)
                    elif status == "blocked":
                        errors.append("a copy reappeared after restore; manual review required")
                    else:
                        pending.append(item)
                else:
                    status = _snapshot_restore_status(item, tx["_applied_stashes"])
                    if status == "already_applied":
                        report.setdefault("already_applied", []).append(item["sha"])
                    elif status == "failed":
                        errors.append("Git snapshot was not stored")
                    elif status == "ready":
                        pending.append(item)
            boundary_error = self._legacy_restore_boundary_error(txid, pending, force)
            if boundary_error:
                errors.append(boundary_error)
                pending = []
            items = pending
            if not items and not errors:
                report["ok"] = True
                return report

        backup_txids: List[str] = []
        moved_sources: List[str] = []
        known_txids = set(transactions)
        applied = set()
        for item in items:
            if item.get("type") == "relocate":
                origin = item["origin_path"]
                trash = item["trash_path"]
                try:
                    # An earlier restore can itself recreate a parent link.
                    self._validate_restore_target(origin)
                except (OSError, ValueError):
                    errors.append(
                        "restore target is outside workspace or cannot be resolved")
                    continue
                try:
                    self._validate_restore_source(trash, txid)
                except (OSError, ValueError):
                    errors.append(
                        "restore source is outside its transaction or cannot be resolved")
                    continue
                if not os.path.lexists(trash):
                    errors.append(f"quarantine copy vanished: {trash}")
                    continue
                if os.path.lexists(origin):
                    if not force:
                        conflicts.append(origin)
                        continue
                    try:
                        self._validate_restore_preservation(origin)
                        backup_txid = new_txid()
                        backup_dir = self._restore_transaction_dir(backup_txid)
                        if (backup_txid in known_txids or
                                os.path.lexists(backup_dir)):
                            raise ValueError("backup transaction already exists")
                    except (OSError, ValueError) as exc:
                        errors.append(f"cannot establish current-version backup: {exc}")
                        continue
                    known_txids.add(backup_txid)
                    backup_txids.append(backup_txid)
                    try:
                        saved = self.relocate(
                            [PathSpec(raw=origin, resolved=origin)],
                            txid=backup_txid,
                            meta={"tool": "restore", "restoring_txid": txid})
                        if saved.get("storage_failure") or len(saved["moved"]) != 1:
                            errors.append(
                                f"current-version preservation failed; "
                                f"inspect backup transaction {backup_txid}")
                            continue
                    except OSError as exc:
                        errors.append(
                            f"current-version preservation failed "
                            f"(backup transaction {backup_txid}): {exc}")
                        continue
                    try:
                        self._validate_restore_target(origin)
                        self._validate_restore_source(trash, txid)
                    except (OSError, ValueError):
                        errors.append("restore paths changed after preservation")
                        continue
                    # Do not silently overwrite a version created while the
                    # occupant was being preserved, even with force set.
                    if os.path.lexists(origin):
                        conflicts.append(origin)
                        continue
                try:
                    parent = os.path.dirname(origin)
                    if parent:
                        os.makedirs(parent, exist_ok=True)
                    copied_source = self._move(trash, origin)
                    if copied_source is not None:
                        self._remove_copied_source(trash, copied_source)
                    restored.append(origin)
                    moved_sources.append(trash)
                except OSError as exc:
                    errors.append(f"{origin}: {exc}")
            elif item.get("type") == "snapshot":
                sha = item["sha"]
                if sha in applied:
                    continue
                try:
                    apply = subprocess.run(
                        ["git", "-C", cwd or self.workspace, "stash", "apply", sha],
                        capture_output=True, text=True, timeout=60)
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    errors.append("git stash apply could not complete; inspect Git state")
                    continue
                if apply.returncode == 0:
                    applied.add(sha)
                    restored.append(f"stash:{sha}")
                else:
                    errors.append(
                        f"stash apply {sha}: {(apply.stderr or '').strip()}")

        try:
            self._manifest_append([{
                "type": "restore", "txid": txid, "force": force,
                "restored": restored, "conflicts": conflicts, "errors": errors,
                **({"selected_paths": selection["selected_paths"]} if paths is not None else {}),
            }])
        except OSError:
            errors.append("restore journal could not be stored; inspect filesystem state")
        else:
            self._prune_restored_parents(txid, tx, moved_sources)
        report["ok"] = not errors and not conflicts
        if backup_txids:
            report["backup_txids"] = backup_txids
        return report

    # ------------------------------------------------------------- status

    def usage(self) -> Dict[str, Any]:
        """Size/count summary of the quarantine tree."""
        files = 0
        total = 0
        for root, _dirs, names in os.walk(self.trash_root):
            for name in names:
                path = os.path.join(root, name)
                if os.path.islink(path):
                    files += 1
                    continue
                try:
                    total += os.lstat(path).st_size
                    files += 1
                except OSError:
                    pass
        transactions = self.transactions()
        return {
            "files": files,
            "bytes": total,
            "transactions": len(transactions),
            "restorable_transactions": sum(
                1 for tx in transactions.values()
                if tx.get("state") == "RESTORABLE"),
            "restorable_items": sum(
                tx.get("restorable_items", 0)
                for tx in transactions.values()),
            "manual_review_items": sum(tx["manual_review_items"] for tx in transactions.values()),
        }

    # ------------------------------------------------------ GC lifecycle

    @staticmethod
    def _parse_ts(value: Optional[str]) -> Optional[float]:
        if not value:
            return None
        try:
            return time.mktime(time.strptime(
                value, "%Y-%m-%dT%H:%M:%SZ")) - time.timezone
        except (ValueError, TypeError):
            return None

    def tx_inventory(self) -> List[Dict[str, Any]]:
        """Active transaction age/size facts; purged history stays in the manifest."""
        inventory: List[Dict[str, Any]] = []
        for txid, tx in self.transactions().items():
            # A tombstone ends our ownership for planning. A directory later
            # recreated under this ID is unmanaged, not another purge target.
            if tx.get("purged"):
                continue
            tx_dir = os.path.join(self.trash_root, txid)
            total = 0
            for root, _dirs, names in os.walk(tx_dir):
                for name in names:
                    p = os.path.join(root, name)
                    if not os.path.islink(p):
                        try:
                            total += os.lstat(p).st_size
                        except OSError:
                            pass
            created = self._parse_ts(tx.get("ts"))
            inventory.append({
                "txid": txid, "ts": tx.get("ts"), "created_epoch": created,
                "bytes": total, "items": len(tx["items"]),
            })
        inventory.sort(key=lambda e: e.get("created_epoch") or 0)
        return inventory

    def gc_plan(self, now: Optional[float] = None,
                retention_days: int = SOFT_RETENTION_DAYS,
                size_limit_bytes: int = SOFT_SIZE_LIMIT_BYTES,
                ) -> Dict[str, Any]:
        """Mark GC_ELIGIBLE entries. Never deletes anything by itself.

        Eligibility: older than the soft retention window, or - when the
        quarantine exceeds the soft size cap - oldest-first until back
        under the cap. The audit log is never eligible.
        """
        now = now if now is not None else time.time()
        cutoff = now - retention_days * 86400
        inventory = self.tx_inventory()
        total = sum(e["bytes"] for e in inventory)
        eligible: List[Dict[str, Any]] = []
        for entry in inventory:
            created = entry.get("created_epoch")
            if created is not None and created < cutoff:
                eligible.append({**entry, "reason": "age"})
        remaining = total - sum(e["bytes"] for e in eligible)
        if remaining > size_limit_bytes:
            for entry in inventory:
                if any(e["txid"] == entry["txid"] for e in eligible):
                    continue
                if remaining <= size_limit_bytes:
                    break
                if entry["bytes"] <= 0 and entry["items"] == 0:
                    continue
                eligible.append({**entry, "reason": "capacity"})
                remaining -= entry["bytes"]
        return {
            "retention_days": retention_days,
            "size_limit_bytes": size_limit_bytes,
            "total_bytes": total,
            "eligible": eligible,
            "remaining_bytes_after_gc": max(remaining, 0),
        }

    def validate_gc_targets(self, txids: List[str]) -> List[Tuple[str, str]]:
        """Resolve only manifest-owned direct children of the quarantine."""
        # Validate the whole batch before touching anything: an unsafe ID
        # after a valid one must not cause a partial purge. A directory that
        # is not backed by a manifest transaction is not ours to delete.
        known = self.transactions()
        trash_physical = os.path.realpath(self.trash_root)
        targets: List[Tuple[str, str]] = []
        for txid in txids:
            if not isinstance(txid, str) or not _GC_TXID_RE.fullmatch(txid):
                raise ValueError(f"invalid quarantine transaction id: {txid!r}")
            tx_dir = os.path.join(self.trash_root, txid)
            if os.path.islink(tx_dir):
                raise ValueError(f"refusing symlinked transaction: {txid}")
            if os.path.isdir(tx_dir):
                if txid not in known or known[txid].get("purged"):
                    raise ValueError(f"unmanaged quarantine directory: {txid}")
                if os.path.dirname(os.path.realpath(tx_dir)) != trash_physical:
                    raise ValueError(f"transaction escapes quarantine: {txid}")
            targets.append((txid, tx_dir))

        return targets

    def gc_execute(self, txids: List[str]) -> Dict[str, Any]:
        """Purge validated transactions; write tombstones after removal.

        Audit records are NEVER removed - the QUARANTINED -> RESTORABLE ->
        GC_ELIGIBLE -> PURGED lifecycle stays fully reconstructible.
        """
        targets = self.validate_gc_targets(txids)
        purged: List[str] = []
        missing: List[str] = []
        for txid, tx_dir in targets:
            if not os.path.isdir(tx_dir):
                missing.append(txid)
                continue
            # Never report PURGED after a failed or partial filesystem remove.
            shutil.rmtree(tx_dir)
            self._manifest_append([{"type": "purged", "txid": txid}])
            purged.append(txid)
        return {"purged": purged, "missing": missing}
