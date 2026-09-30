"""Configuration scope, private copies and Guard row removal regressions."""

import json
from pathlib import Path
import tempfile
import unittest

from adapters.dsh.harness.profiles import (
    ADAPTER, ROOT, configuration_fingerprint, normalize_composition, prepare_homes,
)
from core.lab import LabError


class DshProfilesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="dsh-lab-profiles-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        (self.source / "profiles/headless").mkdir(parents=True)
        self.manifest = {"dependencies": {}, "dsh": {"profile": {"bundles": [
            "@deepseek-ai/dsh-base", "@deepseek-ai/dsh-headless",
        ]}}}
        (self.source / "profiles/headless/package.json").write_text(json.dumps(self.manifest))
        self.base = b"# == different absolute source path\n- id: main\n  config:\n    value: true\n"
        self.guard = (
            '- id: agent_guard\n  name: ' + json.dumps(str(ADAPTER)) + '\n  config:\n'
            '    repoRoot: ' + json.dumps(str(ROOT)) + "\n    defaultCwd: ''\n"
            "    promptSection: true\n    sectionOrder: 105\n    dialect: ''\n"
        ).encode()

    def test_removes_only_known_active_guard_row_and_provenance_comments(self):
        self.assertEqual(normalize_composition(self.base, "off"),
            normalize_composition(self.base + b"# == guard layer\n" + self.guard, "on"))
        self.assertEqual(normalize_composition(self.base, "off"), normalize_composition(
            self.base + self.guard.replace(str(ADAPTER).encode(), ADAPTER.as_uri().encode()), "on"))
        for payload in (self.base + self.guard + self.guard,
                        self.base + self.guard + b'  disabled: true\n',
                        self.base + self.guard.replace(b'promptSection: true', b'promptSection: false'),
                        self.base + self.guard.replace(str(ADAPTER).encode(), b'/unknown/plugin.js')):
            with self.assertRaises(LabError): normalize_composition(payload, "on")
        with self.assertRaises(LabError): normalize_composition(self.base + self.guard, "off")

    def test_indented_literal_comments_are_configuration_not_provenance(self):
        one = self.base + b'    text: |\n      # a literal value\n'
        two = self.base + b'    text: |\n      # another literal\n'
        self.assertNotEqual(normalize_composition(one, "off"), normalize_composition(two, "off"))
        self.assertNotEqual(normalize_composition(one + b'\n      more text\n', "off"),
            normalize_composition(one + b'      more text\n', "off"))

    def test_private_bootstrap_is_copied_without_sessions_or_original_changes(self):
        (self.source / '.credentials.yaml').write_text('synthetic private bootstrap\n')
        (self.source / 'settings.yaml').write_text('synthetic: true\n')
        (self.source / 'sessions').mkdir()
        (self.source / 'sessions/old').write_text('old session data\n')
        homes = self.root / 'homes'
        metadata = prepare_homes(self.source, homes)
        for member in metadata["members"]:
            home = homes / member
            self.assertFalse((home / 'sessions').exists())
            self.assertEqual((home / '.credentials.yaml').read_bytes(), (self.source / '.credentials.yaml').read_bytes())
            self.assertEqual(home.stat().st_mode & 0o777, 0o700)
            self.assertEqual((home / '.credentials.yaml').stat().st_mode & 0o777, 0o600)
        self.assertTrue((self.source / 'sessions/old').is_file())
        self.assertNotIn('synthetic private bootstrap', (homes / 'homes.json').read_text())

    def test_non_guard_fingerprint_binds_settings_environment_and_rendered_content(self):
        home = self.source
        first = configuration_fingerprint(home, self.base, {"DSH_HOME": str(home), "TEST_SETTING": "a"}, "off")
        self.assertEqual(first, configuration_fingerprint(home, self.base + self.guard,
            {"DSH_HOME": '/another/private/home', "TEST_SETTING": "a"}, "on"))
        self.assertNotEqual(first, configuration_fingerprint(home, self.base,
            {"DSH_HOME": str(home), "TEST_SETTING": "b"}, "off"))
        (home / 'settings.yaml').write_text('changed: true\n')
        self.assertNotEqual(first, configuration_fingerprint(home, self.base,
            {"DSH_HOME": str(home), "TEST_SETTING": "a"}, "off"))

    def test_symlinked_bootstrap_and_extra_dependencies_refuse_new_homes(self):
        (self.source / 'settings.yaml').symlink_to(self.source / 'profiles/headless/package.json')
        with self.assertRaises(LabError): prepare_homes(self.source, self.root / 'homes')
        self.assertFalse((self.root / 'homes').exists())
        (self.source / 'settings.yaml').rename(self.source / 'settings.symlink')
        self.manifest['dependencies'] = {'unknown-extra-package': '1'}
        (self.source / 'profiles/headless/package.json').write_text(json.dumps(self.manifest))
        with self.assertRaises(LabError): prepare_homes(self.source, self.root / 'another-homes')


if __name__ == '__main__':
    unittest.main()
