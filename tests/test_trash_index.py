"""Bounded, metadata-only quarantine queries and their status CLI wrapper."""
import errno
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from core.trash_index import list_trash_locations


STATUS_CLI = Path(__file__).resolve().parents[1] / (
    "skills/delete-guard/scripts/status.py")


class TrashIndexTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="guard-trash-index-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "projects"
        self.outside = self.base / "external"
        self.root.mkdir()
        self.outside.mkdir()

    def bucket(self, parent, marker="manifest.jsonl", name=".agent-trash"):
        path = parent / name
        path.mkdir(parents=True, exist_ok=True)
        if marker == "sessions":
            (path / marker).mkdir()
        elif marker:
            (path / marker).write_bytes(b"synthetic-private-content\xff\n")
        return path

    def link(self, target, path):
        try:
            os.symlink(target, path, target_is_directory=True)
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f"symlinks unavailable: {exc}")

    def query(self, **kwargs):
        return list_trash_locations([self.root], **kwargs)

    def cli(self, *args, cwd=None, configured=None):
        env = dict(os.environ)
        env.pop("AGENT_GUARD_TRASH", None)
        env.pop("AGENT_GUARD_WORKSPACE", None)
        if configured is not None:
            env["AGENT_GUARD_TRASH"] = str(configured)
        return subprocess.run(
            [sys.executable, str(STATUS_CLI), *args], cwd=cwd or self.root,
            env=env, capture_output=True, text=True, timeout=15)

    def test_locations_and_layout_counts_are_distinct(self):
        identified = self.bucket(self.root / "one")
        unconfirmed = self.bucket(self.root / "two", marker=None)
        result = self.query()
        self.assertTrue(result["complete"], result)
        self.assertEqual((result["count"], result["identified_count"]), (2, 1))
        rows = {r["trash_root"]: r for r in result["entries"]}
        self.assertEqual(rows[str(identified)]["status"], "metadata_present")
        self.assertEqual(rows[str(unconfirmed)]["status"], "unconfirmed")
        self.assertNotIn("synthetic-private-content", json.dumps(result))

    def test_each_existing_layout_marker_is_recognized_without_parsing(self):
        for marker in ("manifest.jsonl", "audit.jsonl", "state.json", "sessions"):
            with self.subTest(marker=marker):
                path = self.bucket(self.root / marker.replace(".", "-"), marker)
                result = list_trash_locations([path.parent], max_depth=0)
                self.assertEqual(result["identified_count"], 1)
                self.assertTrue(result["complete"], result)

    def test_depth_zero_checks_root_and_boundary_depth_is_included(self):
        self.bucket(self.root)
        self.bucket(self.root / "one")
        self.bucket(self.root / "one" / "two")
        for depth, count in ((0, 1), (1, 2), (2, 3)):
            with self.subTest(depth=depth):
                result = self.query(max_depth=depth)
                self.assertEqual(result["count"], count)
                self.assertTrue(result["complete"], result)

    def test_quarantine_subtree_is_never_walked_or_double_counted(self):
        outer = self.bucket(self.root / "one")
        self.bucket(outer / "transaction" / "archived-project")
        real_scandir = os.scandir

        def forbid_payload(path):
            self.assertNotIn(".agent-trash", Path(path).parts)
            return real_scandir(path)

        with mock.patch("core.trash_index.os.scandir", side_effect=forbid_payload):
            result = self.query(max_depth=8)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["entries"][0]["trash_root"], str(outer))

    def test_root_that_is_a_quarantine_is_reported_without_traversal(self):
        outer = self.bucket(self.root)
        self.bucket(outer / "archived")
        with mock.patch("core.trash_index.os.scandir",
                        side_effect=AssertionError("payload must not be listed")):
            result = list_trash_locations([outer], max_depth=0)
        self.assertEqual(result["count"], 1)

    def test_generated_subdirectories_are_skipped_but_explicit_root_is_allowed(self):
        self.bucket(self.root)
        for name in (".git", "node_modules", ".internal", "__pycache__"):
            self.bucket(self.root / name / "fixture")
        result = self.query(max_depth=8)
        self.assertEqual(result["count"], 1)
        explicit = list_trash_locations([self.root / ".internal"], max_depth=1)
        self.assertEqual(explicit["count"], 1)

    def test_ordinary_child_directory_links_are_not_followed(self):
        self.bucket(self.outside)
        alias = self.root / "linked-project"
        self.link(self.outside, alias)
        self.assertEqual(self.query()["count"], 0)
        explicit = list_trash_locations([alias], max_depth=0)
        self.assertEqual(explicit["count"], 1)

    def test_bucket_links_and_explicit_aliases_deduplicate_physical_location(self):
        physical = self.bucket(self.outside, name="custom-store")
        alias = self.root / ".agent-trash"
        self.link(physical, alias)
        result = self.query(trash_paths=[physical])
        self.assertEqual(result["count"], 1)
        row = result["entries"][0]
        self.assertEqual(set(row["locations"]), {str(alias), str(physical)})
        self.assertEqual(row["resolved_trash_root"], os.path.realpath(physical))
        self.assertEqual(row["sources"], ["declared", "discovered"])

    def test_overlapping_roots_can_extend_depth_without_duplicate_buckets(self):
        self.bucket(self.root / "one")
        self.bucket(self.root / "one" / "two")
        result = list_trash_locations([self.root, self.root / "one"], max_depth=1)
        self.assertEqual(result["count"], 2)
        self.assertTrue(result["complete"], result)

    def test_repeated_root_and_root_alias_do_not_duplicate_buckets(self):
        self.bucket(self.root / "one")
        alias = self.base / "root-alias"
        self.link(self.root, alias)
        result = list_trash_locations([self.root, self.root, alias])
        self.assertEqual(result["count"], 1)
        self.assertEqual(len(result["roots"]), 2)

    def test_external_declared_store_requires_no_search_roots(self):
        physical = self.bucket(self.outside, name="custom-store")
        result = list_trash_locations(trash_paths=[physical])
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["roots"], [])
        self.assertEqual(result["entries"][0]["directory"], str(self.outside))

    def test_missing_or_nondirectory_roots_retain_other_results(self):
        self.bucket(self.root)
        ordinary = self.base / "file.txt"
        ordinary.write_text("ordinary", encoding="utf-8")
        result = list_trash_locations([self.base / "missing", ordinary, self.root])
        self.assertFalse(result["complete"])
        self.assertEqual(result["count"], 1)
        self.assertEqual({e["code"] for e in result["errors"]},
                         {"NOT_FOUND", "NOT_DIRECTORY"})

    def test_missing_declared_store_is_not_silently_counted(self):
        result = list_trash_locations(trash_paths=[self.outside / "missing"])
        self.assertEqual(result["count"], 0)
        self.assertFalse(result["complete"])
        self.assertEqual(result["errors"][0]["code"], "NOT_FOUND")

    def test_unreadable_root_reports_partial_results_and_static_reason(self):
        self.bucket(self.root)
        restricted = self.base / "restricted"
        restricted.mkdir()
        real_scandir = os.scandir

        def denied(path):
            if path == str(restricted):
                raise PermissionError(errno.EACCES, "private injected detail")
            return real_scandir(path)

        with mock.patch("core.trash_index.os.scandir", side_effect=denied):
            result = list_trash_locations([restricted, self.root])
        self.assertFalse(result["complete"])
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["errors"], [
            {"path": str(restricted), "code": "PERMISSION_DENIED"}])
        self.assertNotIn("private injected detail", json.dumps(result))

    def test_unreadable_marker_is_distinguished_from_missing_metadata(self):
        bucket = self.bucket(self.root)
        protected = str(bucket / "manifest.jsonl")
        real_lstat = os.lstat

        def denied(path, *args, **kwargs):
            if path == protected:
                raise PermissionError(errno.EACCES, "private detail")
            return real_lstat(path, *args, **kwargs)

        with mock.patch("core.trash_index.os.lstat", side_effect=denied):
            result = self.query(max_depth=0)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["identified_count"], 0)
        self.assertEqual(result["entries"][0]["status"], "unreadable")
        self.assertFalse(result["complete"])

    def test_resolution_failure_retains_partial_results(self):
        self.bucket(self.root)
        failed = self.base / "failed-root"
        failed.mkdir()
        real_realpath = os.path.realpath

        def fail(path, *args, **kwargs):
            if path == str(failed):
                raise OSError(errno.EIO, "private resolver detail")
            return real_realpath(path, *args, **kwargs)

        with mock.patch("core.trash_index.os.path.realpath", side_effect=fail):
            result = list_trash_locations([failed, self.root])
        self.assertFalse(result["complete"])
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["errors"], [{"path": str(failed), "code": "IO_ERROR"}])

    def test_entry_budget_bounds_traversal_and_marks_incomplete(self):
        for number in range(10):
            self.bucket(self.root / str(number))
        result = self.query(max_entries=3)
        self.assertLessEqual(result["examined"], 3)
        self.assertFalse(result["complete"])
        self.assertEqual(result["errors"][-1]["code"], "ENTRY_LIMIT_REACHED")

    def test_bad_parameters_fail_before_filesystem_traversal(self):
        invalid = [dict(roots="/"), dict(roots=None), dict(roots=[""]),
                   dict(roots=[b"bytes"]), dict(roots=["bad\0path"]),
                   dict(roots=[self.root], max_depth=-1),
                   dict(roots=[self.root], max_depth=True),
                   dict(roots=[self.root], max_entries=0),
                   dict(roots=[self.root], max_entries=1.5), dict()]
        for params in invalid:
            with self.subTest(params=params), \
                    mock.patch("core.trash_index.os.scandir") as scan:
                with self.assertRaises(ValueError):
                    list_trash_locations(**params)
                scan.assert_not_called()

    def test_query_reads_no_file_contents_and_preserves_tree_and_git_exclude(self):
        bucket = self.bucket(self.root)
        recovered = bucket / "transaction" / "private.txt"
        recovered.parent.mkdir()
        recovered.write_bytes(b"private-recovered-content")
        exclude = self.root / ".git" / "info" / "exclude"
        exclude.parent.mkdir(parents=True)
        exclude.write_text("unchanged\n", encoding="utf-8")
        before = {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        with mock.patch("builtins.open", side_effect=AssertionError("no file reads")):
            result = self.query()
        after = {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertNotIn("private-recovered-content", json.dumps(result))

    def test_empty_scope_result_does_not_create_quarantine(self):
        result = self.query()
        self.assertTrue(result["complete"], result)
        self.assertEqual(result["count"], 0)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_json_contract_has_consistent_counts_and_sorted_entries(self):
        self.bucket(self.root / "z")
        self.bucket(self.root / "a")
        result = self.query()
        roundtrip = json.loads(json.dumps(result))
        self.assertEqual(roundtrip, result)
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["count"], len(result["entries"]))
        self.assertEqual(result["identified_count"], sum(
            row["status"] == "metadata_present" for row in result["entries"]))
        self.assertEqual([row["resolved_trash_root"] for row in result["entries"]],
                         sorted(row["resolved_trash_root"] for row in result["entries"]))

    def test_cli_defaults_to_current_directory_and_current_external_configuration(self):
        self.bucket(self.root)
        external = self.bucket(self.outside, name="custom")
        proc = self.cli("--trash-index", "--json", configured=external)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        # Default cwd is physical even when TMPDIR has an alias (macOS /var).
        self.assertEqual(result["roots"], [os.path.realpath(self.root)])
        self.assertEqual(result["trash_paths"], [str(external)])
        self.assertEqual(result["count"], 2)

    def test_cli_explicit_root_does_not_add_unrequested_environment_path(self):
        self.bucket(self.root)
        external = self.bucket(self.outside, name="custom")
        proc = self.cli("--trash-index", "--root", str(self.root), "--json",
                        configured=external)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertEqual(result["roots"], [str(self.root)])
        self.assertEqual(result["trash_paths"], [])
        self.assertEqual(result["count"], 1)

    def test_cli_declared_only_store_and_partial_exit(self):
        external = self.bucket(self.outside, name="custom")
        proc = self.cli("--trash-index", "--trash", str(external), "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["roots"], [])
        failed = self.cli("--trash-index", "--root", str(self.root / "missing"), "--json")
        self.assertEqual(failed.returncode, 1, failed.stderr)
        self.assertFalse(json.loads(failed.stdout)["complete"])

    def test_cli_rejects_bad_limits_and_index_flags_without_index_mode(self):
        for flags in (("--trash-index", "--max-depth", "-1"),
                      ("--trash-index", "--max-entries", "0"),
                      ("--root", str(self.root)), ("--max-depth", "3")):
            with self.subTest(flags=flags):
                proc = self.cli(*flags)
                self.assertEqual(proc.returncode, 2)
                self.assertEqual(list(self.root.iterdir()), [])

    def test_cli_text_is_readable_and_does_not_create_audit_or_layout(self):
        before = set(self.root.iterdir())
        proc = self.cli("--trash-index")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("0 candidates", proc.stdout)
        self.assertIn("scope:", proc.stdout)
        self.assertEqual(before, set(self.root.iterdir()))

    def test_cli_json_and_text_preserve_unusual_names_without_line_spoofing(self):
        if sys.platform == "win32":
            self.skipTest("NTFS forbids newline filenames")
        path = self.bucket(self.root / "中文\nquoted\"name")
        proc = self.cli("--trash-index", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        expected = Path(os.path.realpath(self.root)) / path.relative_to(self.root)
        self.assertEqual(json.loads(proc.stdout)["entries"][0]["trash_root"], str(expected))
        human = self.cli("--trash-index")
        self.assertEqual(human.returncode, 0, human.stderr)
        self.assertIn("\\nquoted", human.stdout)
        self.assertNotIn("中文\nquoted", human.stdout)


if __name__ == "__main__":
    unittest.main()
