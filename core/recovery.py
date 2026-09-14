"""Recoverability pillar: the Compensation Engine.

Two compensation strategies, chosen by the caller (policy decides which):

  relocate     - move targets into the quarantine tree, preserving their
                 workspace-relative structure under one transaction id
  snapshot_git - capture tracked modifications as a stash commit WITHOUT
                 changing the working tree (git stash create + store), so a
                 subsequent reset --hard / restore stays reversible

Everything lands in an append-only manifest (JSONL). Restore is itself
non-destructive: it refuses to overwrite anything that now exists at the
origin path unless the human passes force explicitly.

The quarantine directory must be Git-ignored before it is created. An existing
repository `.gitignore` rule is accepted; otherwise the engine adds a local
`.git/info/exclude` rule without modifying the user's tracked files.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import errno
from typing import Any, Dict, List, Optional, Tuple

from . import MANIFEST_NAME, TRASH_DIRNAME
from .audit import new_txid, utc_now_iso
from .classifier import PathSpec, _physical, _physical_keep_final

# Soft retention policy (docs/references: GC). Thresholds only MARK entries
# GC_ELIGIBLE; actual purging is an explicit maintenance action - a system
# that promises recoverability must never silently destroy its own evidence.
SOFT_RETENTION_DAYS = 30
SOFT_SIZE_LIMIT_BYTES = 5 * 1024 * 1024 * 1024  # 5 GiB

_STORAGE_ERRNOS = {
    getattr(errno, "ENOSPC", None),
    getattr(errno, "EDQUOT", None),
} - {None}


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
    def _move(src: str, dest: str) -> None:
        """Move file/symlink/dir preserving symlink semantics.

        Same-filesystem moves are plain renames. Cross-device fallbacks keep
        symlinks as symlinks (never materialize what they point at).
        """
        try:
            os.rename(src, dest)
            return
        except OSError as exc:
            if exc.errno in _STORAGE_ERRNOS:
                raise StorageUnavailable(
                    f"quarantine storage unavailable: {exc}") from exc
            if exc.errno != errno.EXDEV:
                raise
        try:
            if os.path.islink(src):
                link_target = os.readlink(src)
                os.symlink(link_target, dest)
                os.unlink(src)
            elif os.path.isdir(src):
                shutil.copytree(src, dest, symlinks=True)
                shutil.rmtree(src)
            else:
                shutil.copy2(src, dest)
                os.unlink(src)
        except OSError as exc:
            if exc.errno in _STORAGE_ERRNOS:
                raise StorageUnavailable(
                    f"quarantine storage unavailable: {exc}") from exc
            raise

    # ----------------------------------------------------------- relocate

    def relocate(self, specs: List[PathSpec], txid: Optional[str] = None,
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
            intent = {"type": "relocate-intent", "txid": txid,
                      "origin_path": src, "trash_path": dest,
                      "raw": spec.raw}
            self._manifest_append([intent])
            try:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                self._move(src, dest)
            except StorageUnavailable:
                # Hard principle: capacity limits never downgrade to
                # permanent deletion. Abort; the failed target stays at its
                # origin untouched; callers map this to BLOCK.
                storage_failure = True
                self._manifest_append([{
                    "type": "relocate-failed", "txid": txid,
                    "origin_path": src, "trash_path": dest,
                    "reason": "storage unavailable",
                }])
                break
            except OSError as exc:
                self._manifest_append([{
                    "type": "relocate-failed", "txid": txid,
                    "origin_path": src, "trash_path": dest,
                    "reason": str(exc),
                }])
                raise
            item = {"type": "relocate", "txid": txid,
                    "origin_path": src, "trash_path": dest}
            self._manifest_append([item])
            moved.append({"origin": src, "trash": dest})
        return {"txid": txid, "moved": moved, "skipped": skipped,
                "storage_failure": storage_failure}

    # ------------------------------------------------------ git snapshot

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

    def transactions(self) -> Dict[str, Dict[str, Any]]:
        """Group manifest entries by transaction id (insertion order)."""
        out: Dict[str, Dict[str, Any]] = {}
        for record in self.read_manifest():
            txid = record.get("txid")
            if not txid:
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
            elif record.get("type") == "purged":
                slot["purged"] = True

        # If a move succeeded but its completion record could not be written,
        # the durable intent plus filesystem state is sufficient to recover.
        for slot in out.values():
            completed = {
                (item.get("origin_path"), item.get("trash_path"))
                for item in slot["items"] if item.get("type") == "relocate"
            }
            for intent in slot.pop("_intents", []):
                key = (intent.get("origin_path"), intent.get("trash_path"))
                if key in completed:
                    continue
                origin, trash = key
                if trash and os.path.lexists(trash):
                    slot["items"].append({
                        **intent, "type": "relocate",
                        "recovered_from_intent": True,
                    })
            live_items = 0
            for item in slot["items"]:
                if item.get("type") == "relocate" and \
                        os.path.lexists(item.get("trash_path", "")):
                    live_items += 1
                elif item.get("type") == "snapshot" and item.get("sha"):
                    # Stored stashes are deliberately retained after apply.
                    live_items += 1
            slot["restorable_items"] = live_items
            restored = slot.get("last_restore", {})
            if slot.get("purged"):
                slot["state"] = "PURGED"
            elif restored.get("restored") and not restored.get("errors") and \
                    not restored.get("conflicts"):
                slot["state"] = "RESTORED"
            elif live_items:
                slot["state"] = "RESTORABLE"
            elif slot.get("failures"):
                slot["state"] = "FAILED"
            else:
                slot["state"] = "EMPTY"
        return out

    def restore(self, txid: str, force: bool = False,
                cwd: Optional[str] = None) -> Dict[str, Any]:
        """Undo one transaction. Refuses to overwrite unless force is set.

        Relocate items move back to their origin paths; snapshot items apply
        the stored stash (apply, never drop - the evidence stays until a
        human prunes it).
        """
        tx = self.transactions().get(txid)
        if not tx:
            return {"ok": False, "error": f"unknown transaction: {txid}"}
        restored: List[str] = []
        conflicts: List[str] = []
        errors: List[str] = []
        for item in tx["items"]:
            if item.get("type") == "relocate":
                origin = item["origin_path"]
                trash = item["trash_path"]
                if not os.path.lexists(trash):
                    errors.append(f"quarantine copy vanished: {trash}")
                    continue
                if os.path.lexists(origin):
                    if not force:
                        conflicts.append(origin)
                        continue
                    if os.path.isdir(origin) and not os.path.islink(origin):
                        shutil.rmtree(origin)
                    else:
                        os.unlink(origin)
                parent = os.path.dirname(origin)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                try:
                    self._move(trash, origin)
                    restored.append(origin)
                except OSError as exc:
                    errors.append(f"{origin}: {exc}")
            elif item.get("type") == "snapshot":
                sha = item.get("sha")
                if not sha:
                    continue
                apply = subprocess.run(
                    ["git", "-C", cwd or self.workspace, "stash", "apply", sha],
                    capture_output=True, text=True, timeout=60)
                if apply.returncode == 0:
                    restored.append(f"stash:{sha}")
                else:
                    errors.append(
                        f"stash apply {sha}: {(apply.stderr or '').strip()}")

        self._manifest_append([{
            "type": "restore", "txid": txid, "force": force,
            "restored": restored, "conflicts": conflicts, "errors": errors,
        }])
        # drop empty quarantine directories for tidy listings
        txdir = os.path.join(self.trash_root, txid)
        try:
            if os.path.isdir(txdir) and not os.listdir(txdir):
                os.rmdir(txdir)
        except OSError:
            pass
        return {"ok": not errors and not conflicts,
                "restored": restored, "conflicts": conflicts, "errors": errors}

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
        """Per-transaction age/size facts for retention decisions."""
        inventory: List[Dict[str, Any]] = []
        for txid, tx in self.transactions().items():
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

    def gc_execute(self, txids: List[str]) -> Dict[str, Any]:
        """Purge explicit transactions; write manifest tombstones.

        Audit records are NEVER removed - the QUARANTINED -> RESTORABLE ->
        GC_ELIGIBLE -> PURGED lifecycle stays fully reconstructible.
        """
        purged: List[str] = []
        missing: List[str] = []
        records: List[Dict[str, Any]] = []
        for txid in txids:
            tx_dir = os.path.join(self.trash_root, txid)
            if not os.path.isdir(tx_dir):
                missing.append(txid)
                continue
            shutil.rmtree(tx_dir, ignore_errors=True)
            records.append({"type": "purged", "txid": txid})
            purged.append(txid)
        if records:
            self._manifest_append(records)
        return {"purged": purged, "missing": missing}
