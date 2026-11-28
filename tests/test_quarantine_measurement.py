"""On-demand read-only quarantine storage measurement (CLI and MCP parity)."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

from adapters.mcp.operations import Operations
from core import MANIFEST_NAME, admission, query_text, recovery, workspace_query


ROOT = Path(__file__).resolve().parents[1]
STATUS_SCRIPT = ROOT / "skills/delete-guard/scripts/status.py"


def _tree_snapshot(*roots):
    """Capture non-mutating lstat + content inventory without following links."""
    snap = {}
    for base in roots:
        base_path = Path(base)
        if not os.path.lexists(base_path):
            snap[str(base_path)] = "MISSING"
            continue
        stack = [base_path]
        while stack:
            current = stack.pop()
            key = str(current)
            try:
                info = os.lstat(current)
            except OSError as exc:
                snap[key] = ("LSTAT_ERROR", exc.errno)
                continue
            mode = info.st_mode
            is_reparse = bool(
                getattr(info, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            )
            if stat.S_ISLNK(mode) or is_reparse:
                try:
                    target = os.readlink(current)
                except OSError:
                    target = "REPARSE"
                snap[key] = ("LINK", target, info.st_mtime_ns)
            elif stat.S_ISDIR(mode):
                snap[key] = ("DIR", info.st_mode, info.st_mtime_ns)
                try:
                    with os.scandir(current) as listing:
                        for child in listing:
                            stack.append(Path(child.path))
                except OSError as exc:
                    snap[key] = ("DIR_UNREADABLE", info.st_mode, exc.errno)
            elif stat.S_ISREG(mode):
                try:
                    digest = hashlib.sha256(current.read_bytes()).hexdigest()
                except OSError as exc:
                    digest = f"ERR:{exc.errno}"
                snap[key] = ("REG", info.st_mode, info.st_size, info.st_mtime_ns, digest)
            else:
                snap[key] = ("SPECIAL", info.st_mode, info.st_mtime_ns)
    return snap


def _symlinks_supported(base):
    probe = Path(base) / ".symlink-probe"
    try:
        os.symlink("target", probe)
    except (OSError, NotImplementedError):
        return False
    probe.unlink()
    return True


def _hardlinks_supported(base):
    src = Path(base) / ".hl-src"
    dst = Path(base) / ".hl-dst"
    src.write_bytes(b"x")
    try:
        os.link(src, dst)
    except (OSError, NotImplementedError):
        src.unlink(missing_ok=True)
        return False
    dst.unlink()
    src.unlink()
    return True


class QuarantineMeasurementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ag-measure-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "workspace"
        self.root.mkdir()
        self.trash = self.root / ".agent-trash"
        self.session = "SYNTHETIC_MEASURE_SESSION"
        self.clean_env = {
            key: value for key, value in os.environ.items()
            if not key.startswith(("AGENT_GUARD_", "GIT_"))
            and key not in ("PYTHONPATH", "PYTHONHOME")
        }
        self.clean_env.update(
            AGENT_GUARD_WORKSPACE=str(self.root),
            AGENT_GUARD_SESSION=self.session,
            PYTHONDONTWRITEBYTECODE="1",
        )

    def write(self, relpath, data=b"synthetic-bytes"):
        path = self.root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def configure(self, **values):
        self.write(".agent-guard/delete-policy.json", json.dumps(values).encode("utf-8"))

    def cli_overview(self, *extra_args, trash_root=None):
        env = dict(self.clean_env)
        if trash_root is not None:
            env["AGENT_GUARD_TRASH"] = str(trash_root)
        return subprocess.run(
            [sys.executable, "-B", str(STATUS_SCRIPT), "--view", "overview", "--json", *extra_args],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def cli_raw(self, *args, trash_root=None):
        env = dict(self.clean_env)
        if trash_root is not None:
            env["AGENT_GUARD_TRASH"] = str(trash_root)
        return subprocess.run(
            [sys.executable, "-B", str(STATUS_SCRIPT), *args],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def mcp_overview(self, *, trash_root=None, measure_storage=False, max_measure_entries=None):
        ops = Operations(
            str(self.root),
            trash_root=(str(trash_root) if trash_root is not None else None),
            session=self.session,
        )
        args = {"view": "overview"}
        if measure_storage:
            args["measure_storage"] = True
        if max_measure_entries is not None:
            args["max_measure_entries"] = max_measure_entries
        return ops.call("status", args)

    def run_parity(self, *, trash_root=None, measure_storage=True, max_measure_entries=None,
                   snapshot_roots=None):
        roots = snapshot_roots or (self.root, self.trash)
        before = _tree_snapshot(*roots)
        cli_args = []
        if measure_storage:
            cli_args.append("--measure-storage")
        if max_measure_entries is not None:
            cli_args += ["--max-measure-entries", str(max_measure_entries)]
        proc = self.cli_overview(*cli_args, trash_root=trash_root)
        self.assertIn(proc.returncode, (0, 1), proc.stderr)
        cli_data = json.loads(proc.stdout)
        mcp_reply = self.mcp_overview(
            trash_root=trash_root,
            measure_storage=measure_storage,
            max_measure_entries=max_measure_entries,
        )
        self.assertTrue(mcp_reply["ok"], mcp_reply)
        self.assertEqual(mcp_reply["exit_code"], proc.returncode)
        self.assertEqual(mcp_reply["partial"], not cli_data["complete"])
        mcp_data = mcp_reply["result"]
        self.assertEqual(cli_data["complete"], mcp_data["complete"])
        self.assertEqual(cli_data["storage"], mcp_data["storage"])
        self.assertEqual(cli_data["delete_admission"], mcp_data["delete_admission"])
        self.assertEqual(cli_data["transactions"], mcp_data["transactions"])
        self.assertEqual(_tree_snapshot(*roots), before)
        return proc.returncode, cli_data, mcp_reply

    def _create_manifest_tx(self, trash_root, txid, files, *, ts="2026-11-24T00:00:00Z", purged=False):
        """Create a synthetic quarantine transaction directly under trash_root."""
        engine = recovery.RecoveryEngine(str(self.root), str(trash_root))
        tx_dir = Path(trash_root) / txid
        tx_dir.mkdir(parents=True, exist_ok=True)
        records = [{"type": "tx-start", "txid": txid, "ts": ts}]
        for relpath, content in files.items():
            dest = tx_dir / relpath
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(content)
            records.append({
                "type": "relocate",
                "txid": txid,
                "ts": ts,
                "origin_path": str(self.root / "secret-origin-marker" / txid / relpath),
                "trash_path": str(dest),
            })
        if purged:
            records.append({"type": "purged", "txid": txid, "ts": ts})
        engine._manifest_append(records)
        return tx_dir

    def test_default_overview_does_not_walk_quarantine_payloads(self):
        tx_dir = self._create_manifest_tx(self.trash, "tx-zero-walk", {"nested/payload.bin": b"abc123"})
        before = _tree_snapshot(self.root)
        scanned_paths = []
        real_scandir = os.scandir

        def spy_scandir(path="."):
            scanned_paths.append(os.path.abspath(os.fspath(path)))
            return real_scandir(path)

        with mock.patch("core.workspace_query.os.scandir", side_effect=spy_scandir):
            with mock.patch.dict(os.environ, self.clean_env, clear=True):
                direct = workspace_query.workspace_query(str(self.root), str(self.trash))
        self.assertTrue(direct["complete"])
        self.assertFalse(direct["storage"]["measured"])
        self.assertIsNone(direct["storage"]["logical_bytes"])
        self.assertIsNone(direct["storage"]["physical_freed_bytes"])
        self.assertNotIn("top_transactions", direct["storage"])
        self.assertNotIn("admission_ratio", direct["storage"])
        self.assertFalse(any(p.startswith(str(tx_dir)) or p == str(self.trash) for p in scanned_paths), scanned_paths)

        code, cli_data, _ = self.run_parity(measure_storage=False)
        self.assertEqual(code, 0)
        self.assertFalse(cli_data["storage"]["measured"])
        rendered = query_text.format_workspace_query(cli_data)
        self.assertIn("occupancy not measured", rendered)
        self.assertIn("not measured (overview does not walk payload trees)", rendered)
        self.assertEqual(_tree_snapshot(self.root), before)

    def test_uncreated_quarantine_reports_complete_zero_without_creating_directory(self):
        self.write("owned.txt", b"untouched")
        self.assertFalse(self.trash.exists())
        code, data, mcp = self.run_parity(measure_storage=True)
        self.assertEqual(code, 0)
        self.assertFalse(mcp["partial"])
        self.assertTrue(data["complete"])
        storage = data["storage"]
        self.assertTrue(storage["measured"])
        self.assertTrue(storage["complete"])
        self.assertFalse(storage["truncated"])
        self.assertEqual(storage["logical_bytes"], 0)
        self.assertEqual(storage["entries"], 0)
        self.assertEqual(storage["observed_logical_bytes"], 0)
        self.assertEqual(storage["observed_entries"], 0)
        self.assertEqual(storage["attempted_entries"], 0)
        self.assertEqual(storage["issues"], [])
        self.assertEqual(storage["top_transactions"], [])
        self.assertIsNone(storage["top_transactions_unavailable_reason"])
        self.assertIsNotNone(storage["admission_ratio"])
        self.assertEqual(storage["admission_ratio"]["bytes_ratio"], 0.0)
        self.assertEqual(storage["admission_ratio"]["entries_ratio"], 0.0)
        self.assertIsNone(storage["admission_ratio_unavailable_reason"])
        self.assertFalse(os.path.lexists(self.trash))

        rendered = query_text.format_workspace_query(data)
        self.assertIn("0 B (0 entries; logical, admission accounting)", rendered)
        self.assertIn("Top transactions none observed", rendered)

    def test_unreadable_non_dir_and_dangling_symlink_root_return_partial_cli_and_mcp_parity(self):
        # 1. Default .agent-trash is a regular file (not a directory).
        self.trash.write_bytes(b"not a directory")
        code, data, mcp = self.run_parity(measure_storage=True)
        self.assertEqual(code, 1)
        self.assertTrue(mcp["partial"])
        self.assertFalse(data["complete"])
        storage = data["storage"]
        self.assertFalse(storage["complete"])
        self.assertIsNone(storage["logical_bytes"])
        self.assertIsNone(storage["entries"])
        self.assertEqual(storage["observed_logical_bytes"], 0)
        self.assertEqual(storage["observed_entries"], 0)
        self.assertEqual(storage["attempted_entries"], 0)
        self.assertEqual(storage["issues"], ["STORAGE_ROOT_UNREADABLE"])
        self.assertIsNone(storage["admission_ratio"])
        self.assertEqual(storage["admission_ratio_unavailable_reason"], "INCOMPLETE_MEASUREMENT")
        self.assertIsNone(storage["top_transactions"])
        self.assertEqual(storage["top_transactions_unavailable_reason"], "INCOMPLETE_MEASUREMENT")
        self.trash.unlink()

        # 2. Default .agent-trash is a dangling symlink (and symlink to file).
        if _symlinks_supported(self.base):
            os.symlink(str(self.base / "nonexistent-bucket"), self.trash)
            code, data, mcp = self.run_parity(measure_storage=True)
            self.assertEqual(code, 1)
            self.assertTrue(mcp["partial"])
            self.assertFalse(data["complete"])
            self.assertFalse(data["storage"]["complete"])
            self.assertIsNone(data["storage"]["logical_bytes"])
            self.assertIsNone(data["storage"]["top_transactions"])
            self.assertEqual(data["storage"]["issues"], ["STORAGE_ROOT_UNREADABLE"])
            self.trash.unlink()

            target_file = self.base / "plain-file"
            target_file.write_bytes(b"file-target")
            os.symlink(str(target_file), self.trash)
            code, data, mcp = self.run_parity(measure_storage=True)
            self.assertEqual(code, 1)
            self.assertEqual(data["storage"]["issues"], ["STORAGE_ROOT_UNREADABLE"])
            self.assertFalse(data["storage"]["complete"])
            self.trash.unlink()

        # 3. Root lstat PermissionError must NEVER be reported as uncreated zero.
        real_lstat = os.lstat

        def deny_trash_lstat(path, *args, **kwargs):
            if os.path.abspath(os.fspath(path)) == str(self.trash):
                raise PermissionError(13, "permission denied", path)
            return real_lstat(path, *args, **kwargs)

        with mock.patch("core.workspace_query.os.lstat", side_effect=deny_trash_lstat):
            with mock.patch.dict(os.environ, self.clean_env, clear=True):
                result = workspace_query.workspace_query(str(self.root), str(self.trash), measure_storage=True)
        self.assertFalse(result["complete"])
        self.assertFalse(result["storage"]["complete"])
        self.assertIsNone(result["storage"]["logical_bytes"])
        self.assertIsNone(result["storage"]["entries"])
        self.assertEqual(result["storage"]["issues"], ["STORAGE_ROOT_UNREADABLE"])
        self.assertIsNone(result["storage"]["top_transactions"])
        self.assertIsNone(result["storage"]["admission_ratio"])

        # Real POSIX chmod 0o000 parent directory check when running non-root.
        if os.name != "nt" and hasattr(os, "geteuid") and os.geteuid() != 0:
            locked_parent = self.base / "locked-parent"
            locked_parent.mkdir()
            hidden_trash = locked_parent / "bucket"
            hidden_trash.mkdir()
            os.chmod(locked_parent, 0o000)
            self.addCleanup(lambda: os.chmod(locked_parent, 0o700))
            try:
                can_stat = True
                try:
                    os.lstat(hidden_trash)
                except PermissionError:
                    can_stat = False
                if not can_stat:
                    proc = self.cli_overview("--measure-storage", trash_root=hidden_trash)
                    self.assertEqual(proc.returncode, 1, proc.stderr)
                    parsed = json.loads(proc.stdout)
                    self.assertFalse(parsed["complete"])
                    self.assertEqual(parsed["storage"]["issues"], ["STORAGE_ROOT_UNREADABLE"])
                    self.assertIsNone(parsed["storage"]["logical_bytes"])
            finally:
                os.chmod(locked_parent, 0o700)

    def test_custom_trash_root_and_linked_bucket_cli_mcp_parity(self):
        external = self.base / "external-bucket"
        external.mkdir()
        self._create_manifest_tx(external, "tx-ext-1", {"alpha.bin": b"A" * 2048})
        expected = admission._measure([os.path.realpath(str(external))])

        # Custom external bucket via AGENT_GUARD_TRASH and MCP --trash-root.
        code, data, mcp = self.run_parity(
            trash_root=external,
            measure_storage=True,
            snapshot_roots=(self.root, external),
        )
        self.assertEqual(code, 0)
        self.assertFalse(mcp["partial"])
        self.assertEqual(data["storage"]["logical_bytes"], expected["bytes"])
        self.assertEqual(data["storage"]["entries"], expected["entries"])
        self.assertEqual(len(data["storage"]["top_transactions"]), 1)
        self.assertEqual(data["storage"]["top_transactions"][0]["txid"], "tx-ext-1")
        self.assertFalse(os.path.lexists(self.trash))

        if _symlinks_supported(self.base):
            # Default .agent-trash linked to external directory (linked bucket).
            os.symlink(str(external), self.trash)
            code, linked, _ = self.run_parity(
                measure_storage=True,
                snapshot_roots=(self.root, external),
            )
            self.assertEqual(code, 0)
            self.assertTrue(linked["storage"]["complete"])
            self.assertEqual(linked["storage"]["logical_bytes"], expected["bytes"])
            self.assertEqual(linked["storage"]["entries"], expected["entries"])
            self.assertEqual(linked["storage"]["top_transactions"][0]["txid"], "tx-ext-1")
            self.trash.unlink()

            # Custom alias symlink compared against MCP's resolved startup binding.
            alias = self.base / "bucket-alias"
            os.symlink(str(external), alias)
            resolved_binding = str(external.resolve())
            code, aliased, _ = self.run_parity(
                trash_root=resolved_binding,
                measure_storage=True,
                snapshot_roots=(self.root, external),
            )
            self.assertEqual(code, 0)
            self.assertEqual(aliased["storage"]["logical_bytes"], expected["bytes"])
            # Also confirm CLI invoked directly with the alias symlink matches physical totals.
            alias_cli = self.cli_overview("--measure-storage", trash_root=alias)
            self.assertEqual(alias_cli.returncode, 0, alias_cli.stderr)
            alias_data = json.loads(alias_cli.stdout)
            self.assertEqual(alias_data["storage"]["logical_bytes"], expected["bytes"])
            self.assertEqual(alias_data["storage"]["entries"], expected["entries"])

    def test_admission_parity_and_top_transactions_ranking_and_exclusions(self):
        # 12 active transactions (>10) covering size order, entry tie-break, txid tie-break, nested dirs, hardlinks.
        for idx in range(1, 9):
            self._create_manifest_tx(self.trash, f"tx-size-{idx:02d}", {"blob.bin": b"x" * (idx * 500)})

        # Exact byte tie with different entry counts: 2 files of 5000B each (10000B + dir) vs 1 file of 10000B (+ dir).
        self._create_manifest_tx(self.trash, "tx-tie-entries-more", {"a.bin": b"a" * 5000, "b.bin": b"b" * 5000})
        self._create_manifest_tx(self.trash, "tx-tie-entries-less", {"only.bin": b"c" * 10000})

        # Exact byte AND entry tie to verify ascending txid tie-break.
        self._create_manifest_tx(self.trash, "tx-tie-id-b", {"same.bin": b"d" * 7000})
        self._create_manifest_tx(self.trash, "tx-tie-id-a", {"same.bin": b"e" * 7000})

        # Add a hardlink pair inside tx-size-08 so admission per-entry counting is exercised.
        if _hardlinks_supported(self.base):
            src = self.trash / "tx-size-08" / "blob.bin"
            os.link(src, self.trash / "tx-size-08" / "blob-hardlink.bin")

        # Purged transaction with a leftover/recreated direct directory: counted in total, excluded from top_transactions.
        self._create_manifest_tx(self.trash, "tx-purged-huge", {"stale.bin": b"P" * 50000}, purged=True)

        # Unmanaged direct directory: counted in total, excluded from top_transactions.
        unmanaged = self.trash / "unmanaged-huge"
        unmanaged.mkdir()
        (unmanaged / "orphan.bin").write_bytes(b"U" * 60000)

        # Direct symlink named after a manifest-known txid (pointing outside): opaque, excluded from top_transactions.
        if _symlinks_supported(self.base):
            outside = self.base / "outside-linked-dir"
            outside.mkdir()
            (outside / "massive.bin").write_bytes(b"Z" * 200000)
            link_tx = "tx-direct-symlink"
            link_path = self.trash / link_tx
            os.symlink(str(outside), link_path)
            engine = recovery.RecoveryEngine(str(self.root), str(self.trash))
            engine._manifest_append([
                {"type": "tx-start", "txid": link_tx, "ts": "2026-11-24T00:00:00Z"},
                {"type": "relocate", "txid": link_tx, "ts": "2026-11-24T00:00:00Z",
                 "origin_path": str(self.root / "secret-origin-marker" / link_tx),
                 "trash_path": str(link_path)},
            ])

        expected = admission._measure([os.path.realpath(str(self.trash))])
        code, data, mcp = self.run_parity(measure_storage=True)
        self.assertEqual(code, 0)
        self.assertFalse(mcp["partial"])
        storage = data["storage"]
        self.assertTrue(storage["complete"])
        self.assertEqual(storage["logical_bytes"], expected["bytes"])
        self.assertEqual(storage["entries"], expected["entries"])
        self.assertEqual(storage["observed_logical_bytes"], expected["bytes"])
        self.assertEqual(storage["observed_entries"], expected["entries"])
        self.assertEqual(storage["attempted_entries"], expected["entries"])

        top = storage["top_transactions"]
        self.assertIsInstance(top, list)
        self.assertEqual(len(top), 10)
        ranked_ids = [row["txid"] for row in top]
        # Excluded entries must never appear in Top 10.
        for excluded in ("tx-purged-huge", "unmanaged-huge", "tx-direct-symlink"):
            self.assertNotIn(excluded, ranked_ids)
        # Verify exact sorting invariant: (-logical_bytes, -entries, txid).
        self.assertEqual(
            top,
            sorted(top, key=lambda row: (-row["logical_bytes"], -row["entries"], row["txid"])),
        )
        # Equal logical_bytes -> higher entries first.
        self.assertLess(ranked_ids.index("tx-tie-entries-more"), ranked_ids.index("tx-tie-entries-less"))
        # Equal logical_bytes and entries -> ascending txid first.
        self.assertLess(ranked_ids.index("tx-tie-id-a"), ranked_ids.index("tx-tie-id-b"))
        # Strict metadata-only fields; zero origin paths leaked anywhere in payload.
        for row in top:
            self.assertEqual(set(row.keys()), {"txid", "state", "logical_bytes", "entries"})
        self.assertNotIn("secret-origin-marker", json.dumps(data))

        # Both wide and narrow text outputs render cleanly.
        wide = query_text.format_workspace_query(data, width=96)
        narrow = query_text.format_workspace_query(data, width=60)
        self.assertIn("TOP TXID", wide)
        self.assertIn("Admission ratio", wide)
        self.assertIn("tx-tie-entries-more", narrow)
        self.assertNotIn("secret-origin-marker", wide + narrow)

    def test_internal_symlinks_and_simulated_windows_junction_remain_opaque(self):
        tx_dir = self._create_manifest_tx(self.trash, "tx-links", {"real.bin": b"12345"})
        outside_dir = self.base / "outside-secret-target"
        outside_dir.mkdir()
        outside_file = outside_dir / "huge.bin"
        outside_file.write_bytes(b"H" * 500_000)

        if _symlinks_supported(self.base):
            os.symlink(str(outside_file), tx_dir / "link-file")
            os.symlink(str(outside_dir), tx_dir / "link-dir")
            expected = admission._measure([os.path.realpath(str(self.trash))])
            code, data, _ = self.run_parity(measure_storage=True, snapshot_roots=(self.root, outside_dir))
            self.assertEqual(code, 0)
            self.assertEqual(data["storage"]["logical_bytes"], expected["bytes"])
            self.assertEqual(data["storage"]["entries"], expected["entries"])
            self.assertLess(data["storage"]["logical_bytes"], 500_000)

        if os.name == "nt":
            junction_dir = tx_dir / "real-ntfs-junction"
            created = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(junction_dir), str(outside_dir)],
                capture_output=True, text=True, timeout=15,
            )
            self.assertEqual(created.returncode, 0, created.stdout + created.stderr)
            try:
                code, data, _ = self.run_parity(measure_storage=True, snapshot_roots=(self.root, outside_dir))
                self.assertEqual(code, 0)
                self.assertTrue(data["storage"]["complete"])
                self.assertLess(data["storage"]["logical_bytes"], 500_000)
            finally:
                if os.path.lexists(junction_dir):
                    os.rmdir(junction_dir)

        # Simulate a Windows directory junction (S_IFDIR + FILE_ATTRIBUTE_REPARSE_POINT)
        # on the non-descriptor (Windows) code path.
        junction_path = str(tx_dir / "win-junction")
        fake_reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        fake_junction_stat = types.SimpleNamespace(
            st_mode=stat.S_IFDIR | 0o777,
            st_size=0,
            st_dev=1,
            st_ino=99999,
            st_mtime_ns=1,
            st_ctime_ns=1,
            st_file_attributes=fake_reparse_flag,
        )

        class FakeJunctionEntry:
            name = "win-junction"
            path = junction_path

            def stat(self, *, follow_symlinks=True):
                if follow_symlinks:
                    raise AssertionError("must call stat(follow_symlinks=False)")
                return fake_junction_stat

        real_scandir = os.scandir

        class WrappedScandir:
            def __init__(self, target):
                self._target = os.path.abspath(os.fspath(target))
                if self._target == os.path.abspath(junction_path):
                    raise AssertionError("Windows junction must never be recursed into")
                self._ctx = real_scandir(target)

            def __enter__(self):
                entries = list(self._ctx.__enter__())
                if self._target == os.path.abspath(str(tx_dir)):
                    entries.append(FakeJunctionEntry())
                return iter(entries)

            def __exit__(self, exc_type, exc, tb):
                return self._ctx.__exit__(exc_type, exc, tb)

        with mock.patch("core.workspace_query._supports_descriptor_scan", return_value=False), \
                mock.patch("core.workspace_query.os.scandir", side_effect=WrappedScandir), \
                mock.patch.object(stat, "FILE_ATTRIBUTE_REPARSE_POINT", fake_reparse_flag, create=True):
            measured = workspace_query._measure_quarantine(str(self.trash))
        self.assertTrue(measured["complete"])
        self.assertEqual(measured["issues"], [])
        self.assertGreaterEqual(measured["child_dir_totals"]["tx-links"]["entries"], 2)

    def test_windows_direntry_zero_dev_ino_does_not_false_positive_concurrent_change(self):
        # P1 regression: on Windows, DirEntry.stat(follow_symlinks=False) leaves
        # st_dev=0 and st_ino=0 while os.lstat() returns real device/inode IDs.
        # Non-descriptor scanning must use consistent os.lstat() identity.
        self._create_manifest_tx(self.trash, "tx-win-sub", {"nested/deep/file.bin": b"win-bytes"})
        expected = admission._measure([os.path.realpath(str(self.trash))])
        real_scandir = os.scandir

        class WinCachedEntry:
            def __init__(self, wrapped):
                self._wrapped = wrapped
                self.name = wrapped.name
                self.path = wrapped.path

            def stat(self, *, follow_symlinks=True):
                info = self._wrapped.stat(follow_symlinks=follow_symlinks)
                return types.SimpleNamespace(
                    st_dev=0,
                    st_ino=0,
                    st_mode=info.st_mode,
                    st_size=info.st_size,
                    st_mtime_ns=info.st_mtime_ns,
                    st_ctime_ns=info.st_ctime_ns,
                )

        class WinScandir:
            def __init__(self, target):
                self._ctx = real_scandir(target)

            def __enter__(self):
                return (WinCachedEntry(row) for row in self._ctx.__enter__())

            def __exit__(self, exc_type, exc, tb):
                return self._ctx.__exit__(exc_type, exc, tb)

        with mock.patch("core.workspace_query._supports_descriptor_scan", return_value=False), \
                mock.patch("core.workspace_query.os.scandir", side_effect=WinScandir):
            measured = workspace_query._measure_quarantine(str(self.trash))
        self.assertTrue(measured["complete"], measured)
        self.assertEqual(measured["issues"], [])
        self.assertEqual(measured["observed_logical_bytes"], expected["bytes"])
        self.assertEqual(measured["observed_entries"], expected["entries"])
        self.assertIn("tx-win-sub", measured["child_dir_totals"])

    def test_concurrent_directory_change_and_boundary_swap_never_count_outside_entries(self):
        tx_dir = self._create_manifest_tx(self.trash, "tx-racing", {"item.bin": b"abc"})
        outside_dir = self.base / "outside-boundary-target"
        outside_dir.mkdir()
        (outside_dir / "huge-outside.bin").write_bytes(b"O" * 500_000)

        # Case 1 (non-descriptor fallback): post-scan lstat mutation discards the changed subtree
        # from observed_* (both on normal completion and when truncated by max_entries).
        self._create_manifest_tx(self.trash, "tx-clean-keep", {"ok.bin": b"ok"})
        big_mutated = self._create_manifest_tx(
            self.trash, "tx-racing-big",
            {f"chunk-{i}.bin": b"B" * 100_000 for i in range(4)},
        )
        real_lstat = os.lstat
        seen = {"count": 0}

        def shifting_lstat(path, *args, **kwargs):
            info = real_lstat(path, *args, **kwargs)
            if os.path.abspath(os.fspath(path)) == str(big_mutated):
                seen["count"] += 1
                if seen["count"] >= 3:
                    return types.SimpleNamespace(
                        st_dev=info.st_dev,
                        st_ino=info.st_ino,
                        st_mode=info.st_mode,
                        st_size=info.st_size,
                        st_mtime_ns=info.st_mtime_ns + 1,
                        st_ctime_ns=info.st_ctime_ns,
                    )
            return info

        with mock.patch("core.workspace_query._supports_descriptor_scan", return_value=False), \
                mock.patch("core.workspace_query.os.lstat", side_effect=shifting_lstat):
            with mock.patch.dict(os.environ, self.clean_env, clear=True):
                result = workspace_query.workspace_query(str(self.root), str(self.trash), measure_storage=True)
        self.assertFalse(result["complete"])
        storage = result["storage"]
        self.assertFalse(storage["complete"])
        self.assertIn("STORAGE_CONCURRENT_CHANGE", storage["issues"])
        self.assertIsNone(storage["logical_bytes"])
        self.assertIsNone(storage["entries"])
        self.assertIsNone(storage["admission_ratio"])
        self.assertIsNone(storage["top_transactions"])
        self.assertGreater(storage["observed_entries"], 0)
        # Mutated 400 KiB subtree must be discarded from observed_logical_bytes.
        self.assertLess(storage["observed_logical_bytes"], 100_000)

        # Case 1b (non-descriptor fallback): hitting entry budget MUST STILL verify the directory
        # and discard the unverified subtree if it changed mid-scan.
        trunc_trash = self.base / "trunc-verify-trash"
        trunc_sub = trunc_trash / "tx-trunc-mutated"
        trunc_sub.mkdir(parents=True)
        for idx in range(5):
            (trunc_sub / f"part-{idx}.bin").write_bytes(b"T" * 100_000)
        trunc_seen = {"count": 0}

        def trunc_shifting_lstat(path, *args, **kwargs):
            info = real_lstat(path, *args, **kwargs)
            if os.path.abspath(os.fspath(path)) == str(trunc_sub):
                trunc_seen["count"] += 1
                if trunc_seen["count"] >= 3:
                    return types.SimpleNamespace(
                        st_dev=info.st_dev,
                        st_ino=info.st_ino,
                        st_mode=info.st_mode,
                        st_size=info.st_size,
                        st_mtime_ns=info.st_mtime_ns + 1,
                        st_ctime_ns=info.st_ctime_ns,
                    )
            return info

        with mock.patch("core.workspace_query._supports_descriptor_scan", return_value=False), \
                mock.patch("core.workspace_query.os.lstat", side_effect=trunc_shifting_lstat):
            res_trunc = workspace_query._measure_quarantine(str(trunc_trash), max_entries=4)
        self.assertFalse(res_trunc["complete"])
        self.assertTrue(res_trunc["truncated"])
        self.assertIn("STORAGE_ENTRY_LIMIT", res_trunc["issues"])
        self.assertIn("STORAGE_CONCURRENT_CHANGE", res_trunc["issues"])
        self.assertNotIn("tx-trunc-mutated", res_trunc["child_dir_totals"])
        self.assertLess(res_trunc["observed_logical_bytes"], 100_000)

        # Clean up tx-racing-big so subsequent subtests check small observed totals cleanly.
        for f in big_mutated.iterdir():
            f.unlink()
        big_mutated.rmdir()

        # Case 2 (non-descriptor fallback): directory swapped before open verification is rejected before counting.
        if _symlinks_supported(self.base):
            swap_dir = self.trash / "tx-fallback-swap"
            swap_dir.mkdir()
            (swap_dir / "small.bin").write_bytes(b"12")
            backup = self.base / "fallback-backup"
            real_scandir = os.scandir
            swapped = {"done": False}

            def swap_on_scandir(target):
                if not swapped["done"] and os.path.abspath(os.fspath(target)) == str(swap_dir):
                    swapped["done"] = True
                    swap_dir.rename(backup)
                    os.symlink(str(outside_dir), swap_dir)
                return real_scandir(target)

            try:
                with mock.patch("core.workspace_query._supports_descriptor_scan", return_value=False), \
                        mock.patch("core.workspace_query.os.scandir", side_effect=swap_on_scandir):
                    res_fb = workspace_query._measure_quarantine(str(self.trash))
                self.assertFalse(res_fb["complete"])
                self.assertIn("STORAGE_CONCURRENT_CHANGE", res_fb["issues"])
                self.assertLess(res_fb["observed_logical_bytes"], 500_000)
                self.assertNotIn("tx-fallback-swap", res_fb["child_dir_totals"])
            finally:
                if os.path.lexists(swap_dir):
                    swap_dir.unlink()
                if backup.exists():
                    backup.rename(swap_dir)
                    (swap_dir / "small.bin").unlink()
                    swap_dir.rmdir()

        # Case 3 (POSIX descriptor path): real directory swapped for a symlink OR different dir
        # right before os.open(..., O_NOFOLLOW) must fail without counting or entering outside entries.
        if workspace_query._supports_descriptor_scan():
            real_open = os.open

            # 3a: Swapped for symlink to outside_dir -> O_NOFOLLOW raises ELOOP.
            if _symlinks_supported(self.base):
                link_swap = self.trash / "tx-fd-symlink-swap"
                link_swap.mkdir()
                (link_swap / "inner.bin").write_bytes(b"in")
                backup = self.base / "fd-link-backup"
                triggered = {"done": False}

                def swap_to_symlink(path, flags, *args, **kwargs):
                    if not triggered["done"] and path == "tx-fd-symlink-swap" and kwargs.get("dir_fd") is not None:
                        triggered["done"] = True
                        link_swap.rename(backup)
                        os.symlink(str(outside_dir), link_swap)
                    return real_open(path, flags, *args, **kwargs)

                try:
                    with mock.patch("core.workspace_query.os.open", side_effect=swap_to_symlink):
                        res_link = workspace_query._measure_quarantine(str(self.trash))
                    self.assertTrue(triggered["done"])
                    self.assertFalse(res_link["complete"])
                    self.assertIn("STORAGE_CONCURRENT_CHANGE", res_link["issues"])
                    self.assertLess(res_link["observed_logical_bytes"], 500_000)
                    self.assertNotIn("tx-fd-symlink-swap", res_link["child_dir_totals"])
                finally:
                    if link_swap.is_symlink():
                        link_swap.unlink()
                    elif link_swap.is_dir():
                        for f in link_swap.iterdir():
                            f.unlink()
                        link_swap.rmdir()
                    if backup.exists():
                        for f in backup.iterdir():
                            f.unlink()
                        backup.rmdir()

            # 3b: Swapped for a different directory (new inode) -> fstat identity mismatch rejects before counting.
            dir_swap = self.trash / "tx-fd-dir-swap"
            dir_swap.mkdir()
            (dir_swap / "orig.bin").write_bytes(b"ok")
            replacement = self.base / "replacement-dir"
            replacement.mkdir()
            (replacement / "huge.bin").write_bytes(b"R" * 500_000)
            backup2 = self.base / "fd-dir-backup"
            triggered2 = {"done": False}

            def swap_to_other_dir(path, flags, *args, **kwargs):
                if not triggered2["done"] and path == "tx-fd-dir-swap" and kwargs.get("dir_fd") is not None:
                    triggered2["done"] = True
                    dir_swap.rename(backup2)
                    replacement.rename(dir_swap)
                return real_open(path, flags, *args, **kwargs)

            try:
                with mock.patch("core.workspace_query.os.open", side_effect=swap_to_other_dir):
                    res_dir = workspace_query._measure_quarantine(str(self.trash))
                self.assertTrue(triggered2["done"])
                self.assertFalse(res_dir["complete"])
                self.assertIn("STORAGE_CONCURRENT_CHANGE", res_dir["issues"])
                self.assertLess(res_dir["observed_logical_bytes"], 500_000)
                self.assertNotIn("tx-fd-dir-swap", res_dir["child_dir_totals"])
            finally:
                if dir_swap.exists():
                    for f in dir_swap.iterdir():
                        f.unlink()
                    dir_swap.rmdir()
                if backup2.exists():
                    for f in backup2.iterdir():
                        f.unlink()
                    backup2.rmdir()

            # 3c: Mid-scan directory mutation detected by post-scan fstat/stat verification.
            real_fstat = os.fstat
            tx_ino = os.lstat(tx_dir).st_ino
            fstat_hits = {"n": 0}

            def shifting_fstat(fd):
                info = real_fstat(fd)
                if info.st_ino == tx_ino:
                    fstat_hits["n"] += 1
                    if fstat_hits["n"] >= 2:
                        return types.SimpleNamespace(
                            st_dev=info.st_dev,
                            st_ino=info.st_ino,
                            st_mode=info.st_mode,
                            st_size=info.st_size,
                            st_mtime_ns=info.st_mtime_ns + 1,
                            st_ctime_ns=info.st_ctime_ns,
                        )
                return info

            with mock.patch("core.workspace_query.os.fstat", side_effect=shifting_fstat):
                res_post = workspace_query._measure_quarantine(str(self.trash))
            self.assertFalse(res_post["complete"])
            self.assertIn("STORAGE_CONCURRENT_CHANGE", res_post["issues"])

    def test_streaming_scan_never_prereads_scandir_past_global_budget(self):
        # P2 regression: streaming enumeration across nested dirs must share one global budget
        # without per-directory pre-reading. With max_entries=4, scandir must yield <= 4 entries total.
        for idx in range(3):
            self._create_manifest_tx(
                self.trash, f"tx-stream-{idx}",
                {f"f-{j}.bin": b"S" * 16 for j in range(5)},
            )

        modes = [False]
        if workspace_query._supports_descriptor_scan():
            modes.append(True)

        real_scandir = os.scandir
        for use_fd in modes:
            with self.subTest(descriptor_scan=use_fd):
                yielded = {"count": 0}

                class CountingScandir:
                    def __init__(self, target):
                        self._ctx = real_scandir(target)

                    def __enter__(self):
                        it = self._ctx.__enter__()

                        def gen():
                            for item in it:
                                yielded["count"] += 1
                                yield item

                        return gen()

                    def __exit__(self, exc_type, exc, tb):
                        return self._ctx.__exit__(exc_type, exc, tb)

                with mock.patch("core.workspace_query._supports_descriptor_scan", return_value=use_fd), \
                        mock.patch("core.workspace_query.os.scandir", side_effect=CountingScandir):
                    res = workspace_query._measure_quarantine(str(self.trash), max_entries=4)
                self.assertFalse(res["complete"])
                self.assertTrue(res["truncated"])
                self.assertEqual(res["issues"], ["STORAGE_ENTRY_LIMIT"])
                self.assertEqual(res["attempted_entries"], 4)
                # Root consumes 1 budget entry; remaining 3 children + 1 overflow sentinel = at most 4 yielded.
                self.assertLessEqual(yielded["count"], 4, f"scandir over-read: yielded {yielded['count']} entries")

        # P3 regression: depth-limit probes must share the SAME global entry budget.
        depth_budget_trash = self.base / "depth-budget-trash"
        for idx in range(3):
            sub = depth_budget_trash / f"d{idx}"
            sub.mkdir(parents=True)
            (sub / "child.bin").write_bytes(b"D" * 16)

        for use_fd in modes:
            with self.subTest(depth_probe_descriptor_scan=use_fd):
                yielded = {"count": 0}

                class CountingDepthScandir:
                    def __init__(self, target):
                        self._ctx = real_scandir(target)

                    def __enter__(self):
                        it = self._ctx.__enter__()

                        def gen():
                            for item in it:
                                yielded["count"] += 1
                                yield item

                        return gen()

                    def __exit__(self, exc_type, exc, tb):
                        return self._ctx.__exit__(exc_type, exc, tb)

                with mock.patch("core.workspace_query._supports_descriptor_scan", return_value=use_fd), \
                        mock.patch("core.workspace_query.os.scandir", side_effect=CountingDepthScandir):
                    res = workspace_query._measure_quarantine(
                        str(depth_budget_trash), max_entries=4, max_depth=1,
                    )
                self.assertFalse(res["complete"])
                self.assertTrue(res["truncated"])
                self.assertEqual(res["attempted_entries"], 4)
                self.assertIn("STORAGE_DEPTH_LIMIT", res["issues"])
                self.assertIn("STORAGE_ENTRY_LIMIT", res["issues"])
                self.assertLessEqual(
                    yielded["count"], 4,
                    f"depth probe bypassed global budget: yielded {yielded['count']} entries",
                )

    def test_scan_truncation_and_failed_entries_consume_budget(self):
        tx_dir = self._create_manifest_tx(
            self.trash, "tx-many",
            {f"file-{idx}.bin": b"M" * 64 for idx in range(6)},
        )

        # 1. Truncation via --max-measure-entries=3: CLI and MCP parity.
        code, data, mcp = self.run_parity(measure_storage=True, max_measure_entries=3)
        self.assertEqual(code, 1)
        self.assertTrue(mcp["partial"])
        self.assertFalse(data["complete"])
        storage = data["storage"]
        self.assertFalse(storage["complete"])
        self.assertTrue(storage["truncated"])
        self.assertEqual(storage["issues"], ["STORAGE_ENTRY_LIMIT"])
        self.assertEqual(storage["attempted_entries"], 3)
        self.assertEqual(storage["observed_entries"], 3)
        self.assertIsNone(storage["logical_bytes"])
        self.assertIsNone(storage["entries"])
        self.assertIsNone(storage["admission_ratio"])
        self.assertEqual(storage["admission_ratio_unavailable_reason"], "INCOMPLETE_MEASUREMENT")
        self.assertIsNone(storage["top_transactions"])
        self.assertEqual(storage["top_transactions_unavailable_reason"], "INCOMPLETE_MEASUREMENT")
        rendered = query_text.format_workspace_query(data)
        self.assertIn("PARTIAL", rendered)
        self.assertIn("STORAGE_ENTRY_LIMIT", rendered)

        # 2. Failed entry stat STILL deducts from attempted_entries budget while excluded from observed_entries.
        real_stat = os.stat
        real_scandir = os.scandir

        def deny_file0_stat(path, *args, **kwargs):
            if os.fspath(path) == "file-0.bin" or os.path.basename(os.fspath(path)) == "file-0.bin":
                raise PermissionError(13, "denied", path)
            return real_stat(path, *args, **kwargs)

        class DenyFile0Entry:
            def __init__(self, wrapped):
                self._wrapped = wrapped
                self.name = wrapped.name
                self.path = wrapped.path

            def stat(self, *, follow_symlinks=True):
                if self.name == "file-0.bin":
                    raise PermissionError(13, "denied", self.path)
                return self._wrapped.stat(follow_symlinks=follow_symlinks)

        class DenyFile0Scandir:
            def __init__(self, target):
                self._ctx = real_scandir(target)

            def __enter__(self):
                return (DenyFile0Entry(row) for row in self._ctx.__enter__())

            def __exit__(self, exc_type, exc, tb):
                return self._ctx.__exit__(exc_type, exc, tb)

        with mock.patch("core.workspace_query.os.stat", side_effect=deny_file0_stat), \
                mock.patch("core.workspace_query.os.scandir", side_effect=DenyFile0Scandir):
            unbounded = workspace_query._measure_quarantine(str(self.trash), max_entries=100)
            capped = workspace_query._measure_quarantine(str(self.trash), max_entries=4)
        self.assertFalse(unbounded["complete"])
        self.assertIn("STORAGE_UNREADABLE_ENTRY", unbounded["issues"])
        self.assertEqual(unbounded["attempted_entries"], unbounded["observed_entries"] + 1)
        self.assertFalse(capped["complete"])
        self.assertTrue(capped["truncated"])
        self.assertEqual(capped["attempted_entries"], 4)
        self.assertLessEqual(capped["observed_entries"], 4)
        self.assertIn("STORAGE_ENTRY_LIMIT", capped["issues"])

        # 3. Unreadable subdirectory records STORAGE_UNREADABLE_ENTRY without aborting.
        sub_dir = tx_dir / "unreadable-sub"
        sub_dir.mkdir()
        real_open = os.open
        real_scandir = os.scandir

        def deny_sub_open(path, flags, *args, **kwargs):
            if path == "unreadable-sub" and kwargs.get("dir_fd") is not None:
                raise PermissionError(13, "denied", path)
            return real_open(path, flags, *args, **kwargs)

        def deny_sub_scandir(target):
            if not isinstance(target, int) and os.path.abspath(os.fspath(target)) == str(sub_dir):
                raise PermissionError(13, "denied", target)
            return real_scandir(target)

        with mock.patch("core.workspace_query.os.open", side_effect=deny_sub_open), \
                mock.patch("core.workspace_query.os.scandir", side_effect=deny_sub_scandir):
            sub_res = workspace_query._measure_quarantine(str(self.trash))
        self.assertFalse(sub_res["complete"])
        self.assertIn("STORAGE_UNREADABLE_ENTRY", sub_res["issues"])
        self.assertEqual(sub_res["attempted_entries"], sub_res["observed_entries"] + 1)
        sub_dir.rmdir()

        # 4. Special filesystem entry (POSIX FIFO) consumes budget, never hangs/opens, returns partial.
        if hasattr(os, "mkfifo"):
            fifo_path = tx_dir / "pipe.fifo"
            os.mkfifo(fifo_path)
            try:
                code, fifo_data, fifo_mcp = self.run_parity(measure_storage=True)
                self.assertEqual(code, 1)
                self.assertTrue(fifo_mcp["partial"])
                f_storage = fifo_data["storage"]
                self.assertFalse(f_storage["complete"])
                self.assertFalse(f_storage["truncated"])
                self.assertEqual(f_storage["issues"], ["STORAGE_SPECIAL_ENTRY"])
                self.assertEqual(f_storage["attempted_entries"], f_storage["observed_entries"] + 1)
                self.assertIsNone(f_storage["logical_bytes"])
                self.assertIsNone(f_storage["top_transactions"])
            finally:
                fifo_path.unlink()

        # 5. Depth limit: empty dir at depth==max_depth succeeds; child at depth>max_depth triggers STORAGE_DEPTH_LIMIT.
        depth_trash = self.base / "depth-trash"
        deep_empty = depth_trash / "d1" / "d2"
        deep_empty.mkdir(parents=True)
        ok_depth = workspace_query._measure_quarantine(str(depth_trash), max_depth=2)
        self.assertTrue(ok_depth["complete"])
        self.assertFalse(ok_depth["truncated"])
        (deep_empty / "too-deep.txt").write_bytes(b"deep")
        hit_depth = workspace_query._measure_quarantine(str(depth_trash), max_depth=2)
        self.assertFalse(hit_depth["complete"])
        self.assertTrue(hit_depth["truncated"])
        self.assertEqual(hit_depth["issues"], ["STORAGE_DEPTH_LIMIT"])

    def test_independent_gates_invalid_limits_corrupt_manifest_and_missing_manifest(self):
        # 1. Corrupt delete-policy.json (invalid limits) -> admission_ratio=None, logical_bytes & top_transactions kept.
        self._create_manifest_tx(self.trash, "tx-valid", {"data.bin": b"Q" * 128})
        self.write(".agent-guard/delete-policy.json", b"{broken json")
        code, data, mcp = self.run_parity(measure_storage=True)
        self.assertEqual(code, 1)
        self.assertTrue(mcp["partial"])
        self.assertFalse(data["complete"])
        storage = data["storage"]
        self.assertTrue(storage["complete"])
        self.assertIsInstance(storage["logical_bytes"], int)
        self.assertGreater(storage["logical_bytes"], 0)
        self.assertIsNone(storage["admission_ratio"])
        self.assertEqual(storage["admission_ratio_unavailable_reason"], "LIMITS_UNAVAILABLE")
        self.assertEqual([r["txid"] for r in storage["top_transactions"]], ["tx-valid"])
        rendered = query_text.format_workspace_query(data)
        self.assertIn("Admission ratio unavailable (LIMITS_UNAVAILABLE)", rendered)
        (self.root / ".agent-guard/delete-policy.json").unlink()

        # 2. Corrupt manifest.jsonl -> top_transactions=None (MANIFEST_INCOMPLETE), logical_bytes & ratio kept.
        manifest_path = self.trash / MANIFEST_NAME
        with manifest_path.open("ab") as fh:
            fh.write(b"{not-json\n")
        code, data, mcp = self.run_parity(measure_storage=True)
        self.assertEqual(code, 1)
        self.assertTrue(mcp["partial"])
        storage = data["storage"]
        self.assertTrue(storage["complete"])
        self.assertIsInstance(storage["logical_bytes"], int)
        self.assertIsNotNone(storage["admission_ratio"])
        self.assertIsNone(storage["top_transactions"])
        self.assertEqual(storage["top_transactions_unavailable_reason"], "MANIFEST_INCOMPLETE")
        self.assertIn("Top transactions unavailable (MANIFEST_INCOMPLETE)", query_text.format_workspace_query(data))

        # 3. Missing manifest.jsonl WITH existing direct child directory -> top_transactions=None (MANIFEST_MISSING).
        manifest_path.unlink()
        code, data, mcp = self.run_parity(measure_storage=True)
        self.assertEqual(code, 0)
        self.assertFalse(mcp["partial"])
        storage = data["storage"]
        self.assertTrue(storage["complete"])
        self.assertIsInstance(storage["logical_bytes"], int)
        self.assertIsNotNone(storage["admission_ratio"])
        self.assertIsNone(storage["top_transactions"])
        self.assertEqual(storage["top_transactions_unavailable_reason"], "MANIFEST_MISSING")
        self.assertIn("Top transactions unavailable (MANIFEST_MISSING)", query_text.format_workspace_query(data))

        # 4. Missing manifest.jsonl WITH empty quarantine directory (no direct child dirs) -> top_transactions == [].
        (self.trash / "tx-valid" / "data.bin").unlink()
        (self.trash / "tx-valid").rmdir()
        code, data, mcp = self.run_parity(measure_storage=True)
        self.assertEqual(code, 0)
        self.assertFalse(mcp["partial"])
        storage = data["storage"]
        self.assertTrue(storage["complete"])
        self.assertEqual(storage["top_transactions"], [])
        self.assertIsNone(storage["top_transactions_unavailable_reason"])
        self.assertEqual(storage["entries"], 1)  # empty root dir itself

    def test_cli_argument_validation_and_mutual_exclusion(self):
        before = _tree_snapshot(self.root)
        invalid_invocations = (
            ("--json", "--measure-storage"),
            ("--view", "full", "--measure-storage"),
            ("--view", "overview", "--max-measure-entries", "10"),
            ("--view", "overview", "--measure-storage", "--max-measure-entries", "0"),
            ("--view", "overview", "--measure-storage", "--max-measure-entries",
             str(workspace_query.MAX_MEASURE_ENTRIES + 1)),
            ("--trash-index", "--measure-storage"),
            ("--reset-delete-budget", "--measure-storage"),
        )
        for argv in invalid_invocations:
            with self.subTest(argv=argv):
                proc = self.cli_raw(*argv)
                self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertEqual(_tree_snapshot(self.root), before)

        for kwargs in (
            {"measure_storage": "yes"},
            {"measure_storage": 1},
            {"measure_storage": True, "max_measure_entries": True},
            {"measure_storage": True, "max_measure_entries": 0},
            {"measure_storage": True, "max_measure_entries": workspace_query.MAX_MEASURE_ENTRIES + 1},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                workspace_query.workspace_query(str(self.root), str(self.trash), **kwargs)


if __name__ == "__main__":
    unittest.main()
