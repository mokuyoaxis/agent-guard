"""Published notes keep source prose/code and use existing tagged link targets."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".github/scripts"
with patch.object(sys, "path", [str(SCRIPTS), *sys.path]):
    SPEC = importlib.util.spec_from_file_location("guard_release_notes", SCRIPTS / "release_notes.py")
    notes_module = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(notes_module)


class ReleaseNotesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.notes = self.root / "docs/releases/notes.md"
        self.notes.parent.mkdir(parents=True)
        for name in ("docs/guide.md", "docs/space name.md", "assets/pixel.svg"):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture\n")
        self.tracked = b"docs/guide.md\0docs/space name.md\0assets/pixel.svg\0"

    def render(self, source, version="0.3.0"):
        self.notes.write_text(source)
        with patch.object(notes_module.subprocess, "check_output", return_value=self.tracked):
            return notes_module.render_notes(self.notes, self.root, "owner/project", version)

    def test_relative_link_preserves_prose_title_query_and_fragment(self):
        source = 'Read [guide](../guide.md?view=1#topic "The guide").\n'
        self.assertEqual(self.render(source),
                         'Read [guide](https://github.com/owner/project/blob/0.3.0/docs/guide.md?view=1#topic "The guide").\n')

    def test_rc_and_hotfix_use_the_requested_ref(self):
        for version in ("0.3.1-rc2", "0.3.0-hotfix.1"):
            with self.subTest(version=version):
                self.assertIn("/blob/" + version + "/docs/guide.md", self.render("[x](../guide.md)", version))

    def test_external_fragment_and_protocol_relative_links_stay_unchanged(self):
        source = "[web](https://example.invalid/a) [mail](mailto:dev@example.invalid) [local](#topic) [cdn](//example.invalid/a)\n"
        self.assertEqual(self.render(source), source)

    def test_fences_and_inline_code_are_not_link_destinations(self):
        source = "```md\n[x](missing.md)\n```\n~~~\n[y](missing.md)\n~~~\n`[z](missing.md)` [guide](../guide.md)\n"
        rendered = self.render(source)
        self.assertIn("[x](missing.md)", rendered)
        self.assertIn("[y](missing.md)", rendered)
        self.assertIn("`[z](missing.md)`", rendered)
        self.assertIn("https://github.com/owner/project/blob/0.3.0/docs/guide.md", rendered)

    def test_reference_link_and_angle_wrapped_space_path(self):
        source = '[guide]: <../space name.md> "Reference"\n'
        self.assertEqual(self.render(source),
                         '[guide]: <https://github.com/owner/project/blob/0.3.0/docs/space%20name.md> "Reference"\n')

    def test_code_in_a_link_label_does_not_hide_its_destination(self):
        source = '[`--option`](../guide.md) and `[literal](missing.md)`\n'
        self.assertEqual(self.render(source),
                         '[`--option`](https://github.com/owner/project/blob/0.3.0/docs/guide.md) and `[literal](missing.md)`\n')

    def test_root_relative_links_and_inline_images(self):
        rendered = self.render("[guide](/docs/guide.md) ![pixel](../../assets/pixel.svg)\n")
        self.assertIn("/blob/0.3.0/docs/guide.md", rendered)
        self.assertIn("https://raw.githubusercontent.com/owner/project/0.3.0/assets/pixel.svg", rendered)

    def test_directory_links_require_tracked_contents(self):
        self.assertIn("/blob/0.3.0/docs", self.render("[docs](../)"))
        (self.root / "empty").mkdir()
        with self.assertRaisesRegex(ValueError, "no tracked target"):
            self.render("[empty](../../empty/)")

    def test_missing_ignored_and_repository_escape_targets_fail(self):
        ignored = self.notes.parent / "private.md"
        ignored.write_text("not tracked")
        for source, message in (("[x](missing.md)", "no tracked target"),
                                ("[x](private.md)", "no tracked target"),
                                ("[x](../../../outside.md)", "leaves the repository")):
            with self.subTest(source=source), self.assertRaisesRegex(ValueError, message):
                self.render(source)

    def test_current_notes_resolve_all_four_document_links_without_editing_source(self):
        source = ROOT / "docs/releases/release-notes-0.3.0.md"
        before = source.read_bytes()
        rendered = notes_module.render_notes(source, ROOT, "mokuyoaxis/agent-guard", "0.3.0")
        self.assertNotIn("](../", rendered)
        self.assertEqual(rendered.count("/blob/0.3.0/docs/"), 4)
        self.assertEqual(source.read_bytes(), before)

    def test_cli_refuses_overwriting_the_source(self):
        self.notes.write_text("source\n")
        result = subprocess.run([sys.executable, "-B", str(SCRIPTS / "release_notes.py"),
                                 "--repository", "owner/project", "--version", "0.3.0",
                                 "--notes", str(self.notes), "--output", str(self.notes)],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.notes.read_text(), "source\n")


if __name__ == "__main__":
    unittest.main()
