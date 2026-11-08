"""Read-only prototype boundaries in disposable, owned fixture repositories."""
import contextlib
import errno
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / 'skills/delete-guard/scripts/cleanup.py'
from core.cleanup_groups import GroupScanner as Scanner, TAG
from tests.helpers import RepoFixture


class CleanupGroupTests(RepoFixture):
    def setUp(self):
        super().setUp()
        self.write('.gitignore', '.internal/\n.agent-trash/\n.venv*/\n.work/\n.pytest_cache/\nbuild/\n*.egg-info/\n')
        self.physical = Path(self.root).resolve()

    def cache(self, name, valid=True):
        self.write(name + '/CACHEDIR.TAG', (TAG.decode() if valid else 'invalid') + '\nDO_NOT_DISCLOSE_TAG_TAIL')

    def test_runtime_markers_override_cache_tag_without_expanding_environment(self):
        for name in ['.venv', '.venv-verify', '.venv-wheel-n12', '.venv-wheel-n13']:
            self.cache(name)
            self.write(name + '/pyvenv.cfg', 'DO_NOT_READ_CONFIG')
            self.write(name + '/lib/build/deep', 'runtime')
        original = os.scandir
        def checked(path):
            self.assertNotIn(Path(path).relative_to(self.physical).parts[:1], [('.venv',), ('.venv-verify',), ('.venv-wheel-n12',), ('.venv-wheel-n13',)])
            return original(path)
        with mock.patch('core.cleanup_groups.os.scandir', side_effect=checked):
            result = Scanner(self.root).discover()
        runtimes = [row for row in result['groups'] if row['kind'] == 'runtime']
        self.assertEqual(len(runtimes), 4)
        self.assertEqual(result['marker_bytes_read'], 0)
        self.assertTrue(all(row['size_bytes'] is None and not row['selectable'] for row in runtimes))

    def test_two_uv_groups_are_visible_without_fragment_suggestions(self):
        for name in ['.work/cache/uv', '.work/session-baseline/cache/uv']:
            self.cache(name)
            self.write(name + '/archive/build/output', 'cache')
        original = os.scandir
        def checked(path):
            self.assertNotIn('uv', Path(path).relative_to(self.physical).parts)
            return original(path)
        with mock.patch('core.cleanup_groups.os.scandir', side_effect=checked):
            result = Scanner(self.root).discover()
        caches = [row for row in result['groups'] if row['kind'] == 'managed_cache']
        self.assertEqual({row['path'] for row in caches}, {'.work/cache/uv', '.work/session-baseline/cache/uv'})
        self.assertFalse(any('/uv/' in row['path'] for row in result['groups']))
        self.assertNotIn('DO_NOT_DISCLOSE_TAG_TAIL', json.dumps(result))
        self.assertFalse(result['execution_supported'])

    def test_protected_subtrees_are_neither_sized_nor_descended(self):
        self.write('.internal/build/evidence', 'private')
        self.write('.agent-trash/old/keep', 'recovery')
        original = os.scandir
        def checked(path):
            self.assertFalse(set(Path(path).relative_to(self.physical).parts) & {'.git', '.internal', '.agent-trash', '.agent-guard'})
            return original(path)
        with mock.patch('core.cleanup_groups.os.scandir', side_effect=checked):
            result = Scanner(self.root).discover()
        rows = {row['path']: row for row in result['groups']}
        for name in ['.git', '.internal', '.agent-trash']:
            self.assertEqual(rows[name]['category'], 'protected')
            self.assertIsNone(rows[name]['size_bytes'])

    def test_cache_signature_is_required_and_directory_name_is_not_permission(self):
        self.cache('valid-cache')
        self.cache('.work/uv', valid=False)
        self.write('.work/uv/unknown/file', 'keep')
        result = Scanner(self.root).discover()
        rows = {row['path']: row for row in result['groups']}
        self.assertEqual(rows['valid-cache']['kind'], 'cache_hint')
        self.assertNotIn('.work/uv', rows)
        self.assertTrue(all(not row['selectable'] for row in rows.values()))

    def test_marker_links_and_fifo_are_not_opened(self):
        self.write('private-tag', TAG.decode())
        for name in ['linked', 'fifo']:
            Path(self.root, name).mkdir()
        Path(self.root, 'linked/CACHEDIR.TAG').symlink_to('../private-tag')
        os.mkfifo(Path(self.root, 'fifo/CACHEDIR.TAG'))
        scanner = Scanner(self.root)
        self.assertFalse(scanner.marker('linked', 'CACHEDIR.TAG', header=True))
        self.assertFalse(scanner.marker('fifo', 'CACHEDIR.TAG', header=True))
        self.assertEqual(scanner.marker_bytes, 0)

    def test_large_directory_limit_does_not_starve_sibling_cache_discovery(self):
        for index in range(10):
            self.write('alpha/' + str(index), 'ordinary')
        self.cache('.work/cache/uv')
        result = Scanner(self.root).discover(per_directory=7)
        self.assertIn('.work/cache/uv', [row['path'] for row in result['groups']])
        self.assertFalse(result['coverage']['frontier_complete'])
        self.assertTrue(any(row['reason'] == 'DIRECTORY_REMAINDER_UNOBSERVED' for row in result['coverage']['unexpanded']))

    def test_cache_priority_preserves_budget_and_incomplete_frontier(self):
        for index in range(25):
            self.write('alpha/tmp' + str(index) + '/keep', 'ordinary')
        self.cache('.work/session-baseline/cache/uv')
        result = Scanner(self.root).discover(max_directories=12)
        self.assertIn('.work/session-baseline/cache/uv', [row['path'] for row in result['groups']])
        self.assertLessEqual(result['coverage']['directories_expanded'], 12)
        self.assertFalse(result['coverage']['frontier_complete'])
        self.assertTrue(result['coverage']['limits'])
        self.assertFalse(any(row['selectable'] for row in result['groups']))

    def test_git_queries_are_batched_and_git_failure_does_not_invent_proof(self):
        for index in range(5):
            self.write('area/' + str(index) + '/build/out', 'generated')
        with mock.patch('subprocess.run', wraps=subprocess.run) as calls:
            result = Scanner(self.root).discover()
        self.assertEqual(calls.call_count, 2)
        self.assertEqual(sum(row['category'] == 'regenerable' for row in result['groups']), 5)
        with mock.patch('subprocess.run', side_effect=FileNotFoundError):
            failed = Scanner(self.root).discover()
        self.assertEqual(failed['warnings'], ['GIT_UNAVAILABLE'])
        self.assertFalse(any(row['category'] == 'regenerable' for row in failed['groups']))

    def test_scope_alias_outside_root_and_overlap_are_refused(self):
        self.write('build/lib/out', 'generated')
        Path(self.root, 'alias').symlink_to('build', target_is_directory=True)
        scanner = Scanner(self.root)
        for scopes in [['.'], ['../outside'], ['alias'], ['alias/lib'], ['build', 'build/lib'], ['.git']]:
            with self.subTest(scopes=scopes), self.assertRaises(ValueError):
                scanner.measure(scopes)

    def test_measurement_deduplicates_hardlinks_and_counts_link_entry_only(self):
        first = self.write('build/a', '12345')
        os.link(first, Path(self.root, 'build/b'))
        self.write('outside.txt', 'DO_NOT_READ_OUTSIDE_SCOPE')
        Path(self.root, 'build/link').symlink_to('../outside.txt')
        result = Scanner(self.root).measure(['build'])
        row = result['scopes'][0]
        self.assertTrue(row['size_complete'])
        self.assertEqual(row['size_bytes'], 5 + len('../outside.txt'))
        self.assertEqual(row['regular_files'], 2)
        self.assertEqual(row['symlinks'], 1)
        self.assertEqual(result['workspace'], str(self.physical))
        self.assertFalse(row['selectable'])
        self.assertEqual(row['approval'], 'NOT_GRANTED')

    def test_internal_and_external_link_boundaries_make_whole_size_unknown(self):
        self.write('build/.internal/evidence', 'private')
        self.write('build/out', '123')
        Path(self.root, 'build/external').symlink_to('/etc/passwd')
        row = Scanner(self.root).measure(['build'])['scopes'][0]
        self.assertTrue(row['observed_subset_complete'])
        self.assertFalse(row['size_complete'])
        self.assertIsNone(row['size_bytes'])
        self.assertEqual(row['observed_bytes'], 3)
        self.assertIn('build/.internal', row['protected_roots'])
        self.assertIn('build/external', row['protected_roots'])
        self.assertIsNone(row['core_policy'])
        self.assertFalse(row['interior_checked'])

    def test_measuring_uv_keeps_application_constraint_and_nested_git(self):
        self.cache('.work/cache/uv')
        self.write('.work/cache/uv/sdists-v9/.git', '')
        self.write('.work/cache/uv/sdists-v9/pkg/build/out', 'cache')
        row = Scanner(self.root).measure(['.work/cache/uv'])['scopes'][0]
        self.assertEqual(row['kind'], 'managed_cache')
        self.assertIsNone(row['core_policy'])
        self.assertIn('.work/cache/uv/sdists-v9/.git', row['protected_roots'])
        self.assertIsNone(row['size_bytes'])

    def test_measurement_budget_is_global_and_reports_omitted_siblings(self):
        self.write('a/dir/one', 'one')
        self.write('a/two', 'two')
        self.write('b/three', 'three')
        result = Scanner(self.root).measure(['a', 'b'], max_entries=3)
        self.assertEqual(result['entries_examined'], 3)
        self.assertFalse(result['scopes'][0]['observed_subset_complete'])
        self.assertFalse(result['scopes'][1]['observed_subset_complete'])
        self.assertTrue(all(row['size_bytes'] is None for row in result['scopes']))

    def test_metadata_errors_and_source_changes_are_explicit(self):
        self.write('build/out', 'generated')
        original = os.scandir
        def changing(path):
            if Path(path) == self.physical / 'build':
                info = os.stat(path)
                os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000_000))
            return original(path)
        with mock.patch('core.cleanup_groups.os.scandir', side_effect=changing):
            row = Scanner(self.root).measure(['build'])['scopes'][0]
        self.assertFalse(row['observed_subset_complete'])
        self.assertIn('SOURCE_CHANGED_DURING_SCAN', [item['code'] for item in row['issues']])
        original_stat = os.stat
        def denied(path, *args, **kwargs):
            if Path(path) == self.physical / 'build/out':
                raise PermissionError(errno.EACCES, 'fixture')
            return original_stat(path, *args, **kwargs)
        with mock.patch('core.cleanup_groups.os.stat', side_effect=denied):
            row = Scanner(self.root).measure(['build'])['scopes'][0]
        self.assertFalse(row['size_complete'])
        self.assertIn(13, [item.get('errno') for item in row['issues']])

    def test_declared_alias_protects_physical_evidence_during_measurement(self):
        self.write('build/evidence/keep', 'private')
        self.write('build/out', '123')
        Path(self.root, '.internal').symlink_to('build/evidence', target_is_directory=True)
        row = Scanner(self.root).measure(['build'])['scopes'][0]
        self.assertIn('build/evidence', row['protected_roots'])
        self.assertEqual(row['observed_bytes'], 3)
        self.assertIsNone(row['size_bytes'])

    def test_depth_limit_and_magic_link_have_explicit_incomplete_results(self):
        self.write('build/deep/nested/out', 'generated')
        row = Scanner(self.root).measure(['build'], max_depth=1)['scopes'][0]
        self.assertFalse(row['observed_subset_complete'])
        self.assertIn('DEPTH_LIMIT_REACHED', [item['code'] for item in row['issues']])
        Path(self.root, 'build/link$literal').symlink_to('../src')
        row = Scanner(self.root).measure(['build'])['scopes'][0]
        self.assertFalse(row['observed_subset_complete'])
        self.assertIn('LINK_API_UNDETERMINABLE', [item['code'] for item in row['issues']])

    def test_cli_runs_with_python_filesystem_mutations_forbidden(self):
        self.cache('.work/cache/uv')
        loader = '''import os,runpy,stat,sys
def guard(event,args):
    if event=='open' and args[2] & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND):
        if isinstance(args[0],int) and stat.S_ISFIFO(os.fstat(args[0]).st_mode):
            return
        raise RuntimeError('unexpected filesystem write')
    if event in ('os.mkdir','os.remove','os.rmdir','os.rename','os.chmod','os.link','os.symlink','os.truncate','os.utime'):
        raise RuntimeError('unexpected filesystem mutation')
sys.addaudithook(guard)
sys.argv=sys.argv[1:]
sys.path.insert(0,os.path.dirname(sys.argv[0]))
runpy.run_path(sys.argv[0],run_name='__main__')
'''
        for mode in [['--discover'], ['--measure', '--scope=.work/cache/uv']]:
            args = [sys.executable, '-B', '-c', loader, str(CLI), *mode, '--root', self.root, '--json']
            result = subprocess.run(args, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(json.loads(result.stdout)['execution_supported'])

    def test_discover_mode_refuses_execute_and_does_not_write(self):
        self.cache('.work/cache/uv')
        before = {str(path): (path.stat().st_size, path.stat().st_mtime_ns) for path in self.physical.rglob('*') if path.is_file()}
        args = [sys.executable, '-B', str(CLI), '--discover', '--root', self.root, '--json']
        result = subprocess.run(args, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)['execution_supported'])
        refused = subprocess.run(args + ['--execute'], capture_output=True, text=True, timeout=30)
        self.assertEqual(refused.returncode, 2)
        after = {str(path): (path.stat().st_size, path.stat().st_mtime_ns) for path in self.physical.rglob('*') if path.is_file()}
        self.assertEqual(before, after)

    def test_explicit_scope_continues_discovery_with_original_workspace(self):
        self.cache('deep/a/b/c/cache/uv')
        scanner = Scanner(self.root)
        first = scanner.discover()
        self.assertNotIn('deep/a/b/c/cache/uv', [row['path'] for row in first['groups']])
        continued = scanner.discover(['deep/a'])
        self.assertIn('deep/a/b/c/cache/uv', [row['path'] for row in continued['groups']])
        self.assertEqual(continued['workspace'], str(self.physical))
        self.assertEqual(continued['coverage']['scopes'], ['deep/a'])
        self.assertFalse(continued['coverage']['workspace_scan_complete'])

    def test_discovery_scopes_cannot_expand_alias_protection_or_root(self):
        self.write('area/one/file', 'keep')
        Path(self.root, 'alias').symlink_to('area', target_is_directory=True)
        for scopes in [['.'], ['../outside'], ['alias'], ['.git'], ['area', 'area/one']]:
            with self.subTest(scopes=scopes), self.assertRaises(ValueError):
                Scanner(self.root).discover(scopes)

    def test_multiple_discovery_scopes_share_one_entry_budget(self):
        self.write('a/one', 'a')
        self.write('b/two', 'b')
        result = Scanner(self.root).discover(['a', 'b'], max_entries=1)
        self.assertEqual(result['coverage']['entries_examined'], 1)
        self.assertFalse(result['coverage']['frontier_complete'])
        self.assertIn('b', [row['path'] for row in result['coverage']['unexpanded']])

    def test_measurement_inode_union_deduplicates_across_scopes(self):
        first = self.write('a/one', 'shared bytes')
        self.write('b/placeholder', '')
        os.link(first, Path(self.root, 'b/two'))
        result = Scanner(self.root).measure(['a', 'b'])
        self.assertEqual([row['observed_bytes'] for row in result['scopes']], [12, 12])
        self.assertEqual(result['unique_observed_bytes_across_scopes'], 12)

    def test_cli_observation_modes_reject_plan_and_execution_arguments(self):
        self.write('build/out', 'generated')
        base = [sys.executable, '-B', str(CLI), '--root', self.root, '--json']
        cases = [['--discover', '--discard', 'build'], ['--measure', '--scope', 'build', '--protect', 'other'],
                 ['--discover', '--select', 'build'], ['--discover', '--plan-id', 'old'],
                 ['--measure'], ['--scope', 'build'], ['--discover', '--measure']]
        for args in cases:
            with self.subTest(args=args):
                result = subprocess.run(base + args, capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertFalse(Path(self.root, '.agent-trash').exists())

    def test_cli_measure_reports_unknown_whole_size_without_execution(self):
        self.cache('.work/cache/uv')
        self.write('.work/cache/uv/.git', '')
        result = subprocess.run([sys.executable, '-B', str(CLI), '--root', self.root,
                                 '--measure', '--scope=.work/cache/uv', '--json'],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertFalse(value['execution_supported'])
        self.assertNotIn('plan_id', value)
        self.assertIsNone(value['scopes'][0]['size_bytes'])
        self.assertFalse(value['scopes'][0]['interior_checked'])

    def test_text_measurement_escapes_control_characters_in_paths(self):
        name = 'odd\n\x1b[31m'
        self.write(name + '/file', 'keep')
        result = subprocess.run([sys.executable, '-B', str(CLI), '--root', self.root,
                                 '--measure', '--scope=' + name], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('\x1b', result.stdout)
        self.assertIn('odd\\n\\u001b[31m', result.stdout)


if __name__ == '__main__':
    unittest.main()
