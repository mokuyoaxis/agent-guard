"""Exemption provenance and diagnostics preserve legacy matching and privacy."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from core import redaction

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills/exfil-guard/scripts'


class ExemptionConfiguration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='agent-guard-exemption-')
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)
        self.secret = 'ghp_' + ('Q7mN4pR2tVH8kL3bD6sF9wZ5aC0uE1xYz' * 2)[:36]

    def write(self, source, text):
        p = self.workspace / source
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding='utf-8')
        return p

    def load(self):
        return redaction.load_exemption(str(self.workspace))

    def codes(self, exemption):
        return [row['code'] for row in exemption.safe()['diagnostics']]

    def cli(self, name, *arguments):
        env = {k: v for k, v in os.environ.items()
               if not k.startswith('AGENT_GUARD_')}
        return subprocess.run(
            [sys.executable, str(SCRIPTS / name), '--workspace', str(self.workspace),
             '--channel', 'llm-request', *arguments],
            input=self.secret, cwd=self.workspace, env=env,
            text=True, capture_output=True, timeout=30)

    def test_missing_files_are_quiet_and_have_no_source(self):
        info = self.load().safe()
        self.assertIsNone(info['source'])
        self.assertFalse(info['active'])
        self.assertEqual(info['diagnostics'], [])

    def test_repository_config_is_supported_without_warnings(self):
        exemption = redaction.load_exemption(str(ROOT))
        self.assertTrue(exemption.active)
        self.assertEqual(exemption.safe()['source'], redaction.DEFAULT_ALLOWFILE)
        self.assertEqual(exemption.safe()['diagnostics'], [])

    def test_sections_arrays_comments_and_continuations_keep_matching(self):
        digest = hashlib.sha256(self.secret.encode()).hexdigest()
        self.write(redaction.DEFAULT_ALLOWFILE,
                   '[paths]\nignore = ["docs/*.md", "fixtures/*"] # note\n'
                   '  more/x.py\n[rules]\ndisable = []\n[values]\n'
                   'sha256 = sha256:' + digest + '\n')
        exemption = self.load()
        self.assertEqual(exemption.paths, ['docs/*.md', 'fixtures/*', 'more/x.py'])
        self.assertEqual(exemption.rules, [])
        self.assertTrue(exemption.value_exempt(self.secret))
        self.assertEqual(exemption.safe()['counts'], {'rules': 0, 'paths': 3, 'hashes': 1})
        self.assertEqual(exemption.safe()['diagnostics'], [])

    def test_top_level_legacy_aliases_still_work(self):
        self.write(redaction.DEFAULT_IGNOREFILE,
                   'rule = secret/github-token\npath = docs/*\nhash = sha256:' + 'a' * 64)
        exemption = self.load()
        self.assertEqual(exemption.rules, ['secret/github-token'])
        self.assertEqual(exemption.paths, ['docs/*'])
        self.assertEqual(exemption.hashes, ['sha256:' + 'a' * 64])
        self.assertEqual(exemption.safe()['diagnostics'], [])

    def test_primary_wins_and_secondary_is_reported_without_reading_it(self):
        self.write(redaction.DEFAULT_ALLOWFILE, '[paths]\nignore = primary/*\n')
        secondary = self.write(redaction.DEFAULT_IGNOREFILE, '[paths]\nignore = secondary/*\n')
        real_open = open

        def guarded_open(path, *args, **kwargs):
            if str(path) == str(secondary):
                raise AssertionError('shadowed file must not be read')
            return real_open(path, *args, **kwargs)

        with mock.patch('builtins.open', side_effect=guarded_open):
            exemption = self.load()
        self.assertEqual(exemption.paths, ['primary/*'])
        self.assertIn('EXEMPTION_SHADOWED', self.codes(exemption))

    def test_empty_primary_keeps_precedence(self):
        self.write(redaction.DEFAULT_ALLOWFILE, '# intentionally empty\n')
        self.write(redaction.DEFAULT_IGNOREFILE, '[rules]\ndisable = secret/*\n')
        exemption = self.load()
        self.assertFalse(exemption.active)
        self.assertEqual(exemption.source, redaction.DEFAULT_ALLOWFILE)
        self.assertIn('EXEMPTION_SHADOWED', self.codes(exemption))

    def test_invalid_primary_does_not_activate_secondary(self):
        self.write(redaction.DEFAULT_ALLOWFILE, 'bare/path\n')
        self.write(redaction.DEFAULT_IGNOREFILE, '[rules]\ndisable = secret/*\n')
        exemption = self.load()
        self.assertFalse(exemption.active)
        self.assertEqual(exemption.source, redaction.DEFAULT_ALLOWFILE)
        self.assertIn('EXEMPTION_IGNORED_LINE', self.codes(exemption))
        self.assertTrue(redaction.scan_text(self.secret, workspace=str(self.workspace)).spans)

    def test_gitignore_lines_are_diagnosed_and_do_not_exempt(self):
        self.write(redaction.DEFAULT_IGNOREFILE, '# comment\n' + self.secret + '\n!docs/*\n')
        exemption = self.load()
        self.assertFalse(exemption.active)
        rows = exemption.safe()['diagnostics']
        self.assertEqual([r['line'] for r in rows], [2, 3])
        self.assertEqual(set(self.codes(exemption)), {'EXEMPTION_IGNORED_LINE'})
        self.assertNotIn(self.secret, json.dumps(exemption.safe()))

    def test_unknown_top_level_key_is_ignored_with_line_number(self):
        self.write(redaction.DEFAULT_ALLOWFILE, self.secret + ' = secret/*\n')
        exemption = self.load()
        self.assertFalse(exemption.active)
        self.assertEqual(exemption.safe()['diagnostics'][0]['line'], 1)
        self.assertIn('EXEMPTION_UNKNOWN_KEY', self.codes(exemption))
        self.assertNotIn(self.secret, json.dumps(exemption.safe()))

    def test_unknown_section_does_not_change_legacy_key_behavior(self):
        self.write(redaction.DEFAULT_ALLOWFILE, '[' + self.secret + ']\npath = docs/*\n')
        exemption = self.load()
        self.assertEqual(exemption.paths, ['docs/*'])
        self.assertIn('EXEMPTION_UNKNOWN_SECTION', self.codes(exemption))
        self.assertNotIn(self.secret, json.dumps(exemption.safe()))

    def test_legacy_section_key_is_diagnosed_but_still_applies(self):
        self.write(redaction.DEFAULT_ALLOWFILE, '[paths]\ncustom = docs/*\n')
        exemption = self.load()
        self.assertEqual(exemption.paths, ['docs/*'])
        self.assertIn('EXEMPTION_LEGACY_SECTION_KEY', self.codes(exemption))

    def test_invalid_hash_is_diagnosed_without_echoing_value(self):
        self.write(redaction.DEFAULT_ALLOWFILE, '[values]\nsha256 = ' + self.secret + '\n')
        exemption = self.load()
        self.assertEqual(exemption.hashes, [self.secret])
        self.assertFalse(exemption.value_exempt(self.secret))
        self.assertIn('EXEMPTION_INVALID_HASH', self.codes(exemption))
        self.assertNotIn(self.secret, json.dumps(exemption.safe()))

    def test_negation_is_not_gitignore_reinclude(self):
        self.write(redaction.DEFAULT_ALLOWFILE, '[paths]\nignore = !docs/*\n')
        exemption = self.load()
        self.assertFalse(exemption.path_exempt('docs/a.md'))
        self.assertIn('EXEMPTION_UNSUPPORTED_NEGATION', self.codes(exemption))

    def test_malformed_section_preserves_existing_parse(self):
        self.write(redaction.DEFAULT_ALLOWFILE, '[paths]]\nignore = docs/*\n')
        exemption = self.load()
        self.assertEqual(exemption.paths, ['docs/*'])
        self.assertIn('EXEMPTION_MALFORMED_SECTION', self.codes(exemption))

    def test_invalid_utf8_uses_fallback_and_reports_static_error(self):
        primary = self.write(redaction.DEFAULT_ALLOWFILE, '')
        primary.write_bytes(b'\xff\xfe')
        self.write(redaction.DEFAULT_IGNOREFILE, '[paths]\nignore = docs/*\n')
        exemption = self.load()
        self.assertEqual(exemption.source, redaction.DEFAULT_IGNOREFILE)
        self.assertEqual(exemption.paths, ['docs/*'])
        self.assertIn('EXEMPTION_ENCODING_INVALID', self.codes(exemption))

    def test_read_failure_uses_fallback_without_exception_text(self):
        primary = self.write(redaction.DEFAULT_ALLOWFILE, '')
        self.write(redaction.DEFAULT_IGNOREFILE, '[paths]\nignore = docs/*\n')
        real_open = open

        def denied_open(path, *args, **kwargs):
            if str(path) == str(primary):
                raise PermissionError(self.secret)
            return real_open(path, *args, **kwargs)

        with mock.patch('builtins.open', side_effect=denied_open):
            exemption = self.load()
        self.assertEqual(exemption.source, redaction.DEFAULT_IGNOREFILE)
        self.assertIn('EXEMPTION_READ_FAILED', self.codes(exemption))
        self.assertNotIn(self.secret, json.dumps(exemption.safe()))

    def test_both_failed_reads_leave_scanning_enabled(self):
        (self.workspace / '.agent-guard/exfil-allow.toml').mkdir(parents=True)
        (self.workspace / redaction.DEFAULT_IGNOREFILE).mkdir()
        exemption = self.load()
        self.assertFalse(exemption.active)
        self.assertIsNone(exemption.safe()['source'])
        self.assertEqual(self.codes(exemption), ['EXEMPTION_READ_FAILED'] * 2)
        scan = redaction.scan_text(self.secret, workspace=str(self.workspace))
        self.assertTrue(scan.spans)

    def test_diagnostics_are_bounded(self):
        self.write(redaction.DEFAULT_ALLOWFILE, (self.secret + '\n') * 100)
        info = self.load().safe()
        self.assertEqual(len(info['diagnostics']), 32)
        self.assertEqual(info['diagnostics_omitted'], 68)
        self.assertNotIn(self.secret, json.dumps(info))

    def test_arbitrary_library_source_is_not_echoed(self):
        exemption = redaction._parse_allowfile('ignored\n', self.secret)
        self.assertNotIn(self.secret, json.dumps(exemption.safe()))

    def test_check_json_reports_source_counts_and_diagnostics(self):
        self.write(redaction.DEFAULT_IGNOREFILE, self.secret + '\n')
        result = self.cli('check_span.py', '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual(out['decision'], 'SANITIZE')
        self.assertEqual(out['exemption']['source'], redaction.DEFAULT_IGNOREFILE)
        self.assertEqual(out['exempted'], 0)
        self.assertIn('EXEMPTION_IGNORED_LINE',
                      [d['code'] for d in out['exemption']['diagnostics']])
        self.assertNotIn(self.secret, result.stdout + result.stderr)

    def test_check_human_output_includes_safe_warning(self):
        self.write(redaction.DEFAULT_IGNOREFILE, self.secret + '\n')
        result = self.cli('check_span.py')
        self.assertIn('EXEMPTION_IGNORED_LINE', result.stderr)
        self.assertNotIn(self.secret, result.stdout + result.stderr)

    def test_sanitizer_warns_on_stderr_and_keeps_payload_stdout(self):
        self.write(redaction.DEFAULT_IGNOREFILE, self.secret + '\n')
        result = self.cli('sanitize.py')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'ghp_<REDACTED>')
        self.assertIn('EXEMPTION_IGNORED_LINE', result.stderr)
        self.assertNotIn(self.secret, result.stdout + result.stderr)

    def test_sanitizer_dry_run_remains_a_json_plan(self):
        self.write(redaction.DEFAULT_IGNOREFILE, 'bare/path\n')
        result = self.cli('sanitize.py', '--dry-run')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsInstance(json.loads(result.stdout), list)
        self.assertIn('EXEMPTION_IGNORED_LINE', result.stderr)

    def test_warning_does_not_prevent_valid_rule_exemption(self):
        self.write(redaction.DEFAULT_ALLOWFILE, 'bare/path\n[rules]\ndisable = secret/github-token\n')
        result = self.cli('check_span.py', '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual(out['decision'], 'ALLOW')
        self.assertEqual(out['exempted'], 1)
        self.assertTrue(out['exemption']['diagnostics'])

    def test_valid_path_exemption_reports_actual_removed_span(self):
        self.write(redaction.DEFAULT_IGNOREFILE, '[paths]\nignore = docs/*.md\n')
        result = self.cli('check_span.py', '--json', '--path', 'docs/api.md')
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual((out['decision'], out['exempted']), ('ALLOW', 1))
        self.assertEqual(out['exemption']['counts']['paths'], 1)
        self.assertEqual(out['exemption']['diagnostics'], [])

    def test_valid_hash_exemption_applies_without_echoing_hash(self):
        digest = hashlib.sha256(self.secret.encode()).hexdigest()
        self.write(redaction.DEFAULT_ALLOWFILE, '[values]\nsha256 = sha256:' + digest + '\n')
        result = self.cli('check_span.py', '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual((out['decision'], out['exempted']), ('ALLOW', 1))
        self.assertEqual(out['exemption']['counts']['hashes'], 1)
        self.assertNotIn(digest, result.stdout + result.stderr)

    def test_missing_configuration_does_not_warn_in_cli(self):
        result = self.cli('check_span.py', '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, '')
        self.assertIsNone(json.loads(result.stdout)['exemption']['source'])

    def test_sanitizer_library_returns_safe_metadata_without_printing(self):
        self.write(redaction.DEFAULT_IGNOREFILE, 'bare/path\n')
        sys.path.insert(0, str(SCRIPTS))
        self.addCleanup(sys.path.remove, str(SCRIPTS))
        import sanitize
        with mock.patch('sys.stderr') as stderr:
            result = sanitize.sanitize_text(self.secret, 'llm-request', str(self.workspace))
        stderr.write.assert_not_called()
        self.assertIn('EXEMPTION_IGNORED_LINE',
                      [d['code'] for d in result['exemption']['diagnostics']])


if __name__ == '__main__':
    unittest.main()
