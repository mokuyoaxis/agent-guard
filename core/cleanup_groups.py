"""Read-only cleanup groups; observation never grants deletion permission.

Discovery is a bounded directory frontier. Measurement keeps the original
workspace and omits protected subtrees. Neither produces an executable plan.
"""
from collections import Counter
import errno
import hashlib
import heapq
import json
import os
from pathlib import Path
import stat

from core import classifier, policy
from core.cleanup import PROTECTED_NAMES, _git_facts
from core.delete_config import load_config

TAG = b'Signature: 8a477f597d28d172789f06886806bc55'
LABELS = {'protected': '受保护', 'runtime': '运行环境', 'managed_cache': '应用管理缓存',
          'cache_hint': '缓存提示', 'artifact': '构建工件', 'link': '未跟随链接'}


def under(path, parent):
    return parent == '.' or path == parent or path.startswith(parent + '/')


def signature(info):
    return [info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def positive(value):
    if type(value) is not int or value < 1:
        raise ValueError('scan limits must be positive integers')
    return value


class GroupScanner:
    def __init__(self, workspace):
        self.root = Path(workspace).resolve(strict=True)
        if not self.root.is_dir() or classifier.protected_ancestor_root(str(self.root)):
            raise ValueError('workspace must be an existing non-root project directory')
        self.config = load_config(str(self.root))
        self.guard_config = policy.GuardConfig.from_env()
        self.protect = set(PROTECTED_NAMES) | {name.replace(os.sep, '/') for name in self.config.protected_paths}
        self.trash = os.path.abspath(os.environ.get('AGENT_GUARD_TRASH', self.root / '.agent-trash'))
        if classifier.inside_path(self.trash, str(self.root)):
            relative = os.path.relpath(self.trash, self.root).replace(os.sep, '/')
            self.protect.add(relative.split('/')[0])
        for name in list(self.protect):
            physical = os.path.realpath(self.root / name)
            if classifier.inside_path(physical, str(self.root)):
                self.protect.add(os.path.relpath(physical, self.root).replace(os.sep, '/'))
        self.marker_bytes = 0

    def protected(self, relative):
        return bool(set(relative.split('/')) & PROTECTED_NAMES) or any(under(relative, path) for path in self.protect)

    def marker(self, relative, name, *, header=False):
        path = self.root / relative / name
        try:
            info = path.lstat()
        except FileNotFoundError:
            return False
        if not stat.S_ISREG(info.st_mode):
            return False
        if not header:
            return True
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise OSError(errno.EAGAIN, 'cache marker changed to a non-regular file')
            data = os.read(descriptor, len(TAG))
            self.marker_bytes += len(data)
            return data == TAG
        finally:
            os.close(descriptor)

    def describe(self, relative):
        if self.protected(relative):
            kind, evidence = 'protected', ['Protected project, configuration or recovery boundary']
        elif self.marker(relative, 'pyvenv.cfg'):
            kind, evidence = 'runtime', ['Regular pyvenv.cfg; environment, not proof of obsolescence or activity']
        elif self.marker(relative, 'CACHEDIR.TAG', header=True):
            if Path(relative).name == 'uv':
                kind, evidence = 'managed_cache', ['Valid CACHEDIR.TAG and name uv; ownership hint, not authentication']
            elif policy._matches_artifact(relative, self.guard_config.artifact_patterns):
                kind, evidence = 'artifact', ['Valid CACHEDIR.TAG and known artifact pattern']
            else:
                kind, evidence = 'cache_hint', ['Valid CACHEDIR.TAG; owner and cleanup protocol unverified']
        elif policy._matches_artifact(relative, self.guard_config.artifact_patterns):
            kind, evidence = 'artifact', ['Known artifact pattern; Git proof and interior inspection pending']
        else:
            return None
        return {'path': relative, 'kind': kind, 'category': 'protected' if kind == 'protected' else 'uncertain',
                'managed_by': 'uv (hint)' if kind == 'managed_cache' else None,
                'evidence': evidence, 'size_bytes': None, 'size_complete': False,
                'interior_checked': False, 'selectable': False}

    def classify(self, groups):
        candidates = [row['path'] for row in groups if row['kind'] != 'protected']
        tracked, ignored, warning = _git_facts(str(self.root), candidates) if candidates else (set(), set(), None)
        for row in groups:
            path = row['path']
            declared_inside = any(other != path and under(other, path) for other in self.protect)
            tracked_inside = any(under(other, path) for other in tracked)
            row['git_ignored'] = path in ignored
            row['tracked_descendant'] = tracked_inside
            row['declared_protected_descendant'] = declared_inside
            if row['kind'] == 'artifact' and path in ignored and not tracked_inside and not declared_inside and not row.get('protected_roots'):
                row['category'] = 'regenerable'
                row['evidence'].append('Git-ignored with no tracked descendants; read-only hint only')
        return warning, ignored

    def discover(self, values=(), *, max_depth=4, max_entries=10000, max_directories=256, per_directory=512):
        for value in [max_depth, max_entries, max_directories, per_directory]:
            positive(value)
        scopes = self.scopes(values) if values else ['.']
        queue = [(1, 0, scope) for scope in scopes]
        heapq.heapify(queue)
        groups, deferred, issues, limits = [], [], [], []
        branches = Counter()
        examined = expanded = 0
        while queue:
            _, depth, relative = heapq.heappop(queue)
            try:
                if relative != '.':
                    info = (self.root / relative).lstat()
                    if stat.S_ISLNK(info.st_mode):
                        deferred.append({'path': relative, 'reason': 'LINK_NOT_FOLLOWED'})
                        continue
                    if not stat.S_ISDIR(info.st_mode):
                        issues.append({'path': relative, 'code': 'SOURCE_TYPE_CHANGED'})
                        continue
                    group = self.describe(relative)
                    if group:
                        groups.append(group)
                        deferred.append({'path': relative, 'reason': group['kind'].upper() + '_NOT_EXPANDED'})
                        continue
                else:
                    info = self.root.lstat()
                    if self.protected('.'):
                        groups.append(self.describe('.'))
                        deferred.append({'path': '.', 'reason': 'PROTECTED_NOT_EXPANDED'})
                        continue
                if depth >= max_depth:
                    deferred.append({'path': relative, 'reason': 'DEPTH_FRONTIER'})
                    continue
                if expanded >= max_directories or examined >= max_entries:
                    limits.append({'path': relative, 'code': 'GLOBAL_DISCOVERY_LIMIT'})
                    deferred.extend({'path': name, 'reason': 'GLOBAL_DISCOVERY_LIMIT'} for name in [relative, *(item[2] for item in queue)])
                    break
                expanded += 1
                children = []
                with os.scandir(self.root / relative) as entries:
                    for index, entry in enumerate(entries):
                        if index >= per_directory or examined >= max_entries:
                            limits.append({'path': relative, 'code': 'DIRECTORY_ENTRY_LIMIT' if index >= per_directory else 'GLOBAL_ENTRY_LIMIT'})
                            deferred.append({'path': relative, 'reason': 'DIRECTORY_REMAINDER_UNOBSERVED'})
                            break
                        examined += 1
                        path = entry.name if relative == '.' else relative + '/' + entry.name
                        if self.protected(path):
                            groups.append(self.describe(path))
                            deferred.append({'path': path, 'reason': 'PROTECTED_NOT_EXPANDED'})
                        elif entry.is_symlink():
                            deferred.append({'path': path, 'reason': 'LINK_NOT_FOLLOWED'})
                        elif entry.is_dir(follow_symlinks=False):
                            children.append(path)
                        else:
                            branches[path.split('/')[0] if '/' in path else '.'] += 1
                if signature((self.root / relative).lstat()) != signature(info):
                    issues.append({'path': relative, 'code': 'SOURCE_CHANGED_DURING_SCAN'})
                for path in children:
                    # Hints affect scheduling only. Markers, Git facts and
                    # protection still determine the read-only group label.
                    hinted = (bool(set(path.split('/')) & {'cache', '.cache', 'uv'})
                              or policy._matches_artifact(path, self.guard_config.artifact_patterns))
                    heapq.heappush(queue, (0 if hinted else 1, depth + 1, path))
            except OSError as error:
                issues.append({'path': relative, 'code': 'OS_ERROR', 'errno': error.errno})
        groups.sort(key=lambda row: row['path'])
        warning, _ = self.classify(groups)
        overview = []
        for kind, label in LABELS.items():
            paths = [row['path'] for row in groups if row['kind'] == kind]
            if paths:
                overview.append({'label': label, 'kind': kind, 'groups': len(paths), 'examples': paths[:3], 'size_bytes': None})
        overview.append({'label': '待复核目录／根文件', 'kind': 'review', 'branches': len(branches), 'observed_files': sum(branches.values()), 'size_bytes': None})
        result = {'schema': 'cleanup_group_discovery/v1', 'workspace': str(self.root), 'operation': 'read_only_discovery',
                  'execution_supported': False, 'groups': groups, 'overview': overview,
                  'coverage': {'scopes': scopes, 'frontier_depth': max_depth,
                               'budgets': {'max_entries': max_entries, 'max_directories': max_directories, 'per_directory': per_directory}, 'frontier_complete': not issues and not limits,
                               'workspace_scan_complete': False, 'entries_examined': examined,
                               'directories_expanded': expanded, 'unexpanded': deferred, 'limits': limits, 'issues': issues},
                  'review_branches': dict(sorted(branches.items())), 'warnings': [warning] if warning else [],
                  'marker_bytes_read': self.marker_bytes,
                  'notice': 'Read-only directory overview. Unknown sizes are not zero; unexpanded interiors and execution remain unverified.'}
        result['observation_id'] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
        return result

    def scopes(self, values):
        if not values:
            raise ValueError('measure requires at least one explicit scope')
        selected = []
        for value in values:
            absolute = os.path.abspath(self.root / value)
            if absolute == str(self.root) or not classifier.inside_path(absolute, str(self.root)):
                raise ValueError('scope must be below the fixed workspace')
            relative = os.path.relpath(absolute, self.root).replace(os.sep, '/')
            path = self.root
            for component in Path(relative).parts:
                path /= component
                info = path.lstat()
                if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                    raise ValueError('scope components must be real directories, without aliases')
            if self.protected(relative):
                raise ValueError('protected scope cannot be measured')
            if any(under(relative, other) or under(other, relative) for other in selected):
                raise ValueError('scopes must not overlap')
            selected.append(relative)
        return sorted(selected)

    def measure(self, values, *, max_entries=20000, max_depth=32):
        positive(max_entries)
        positive(max_depth)
        scopes = self.scopes(values)
        rows, all_inodes = [], {}
        total_examined = 0
        for scope in scopes:
            description = self.describe(scope) or {'path': scope, 'kind': 'review', 'category': 'uncertain', 'managed_by': None, 'evidence': ['No cache or regeneration evidence'], 'selectable': False}
            seen, protected, issues, special = {}, {}, [], []
            digest = hashlib.sha256()
            count = regular = links = directories = 0
            for path in sorted(self.protect):
                if path != scope and under(path, scope):
                    protected[path] = 'Declared protected descendant; size unmeasured'

            def visit(relative, depth):
                nonlocal count, regular, links, directories, total_examined
                if self.protected(relative):
                    protected[relative] = 'Protected subtree; not traversed or sized'
                    return
                if total_examined >= max_entries:
                    issues.append({'path': relative, 'code': 'ENTRY_LIMIT_REACHED'})
                    return
                count += 1
                total_examined += 1
                try:
                    info = (self.root / relative).lstat()
                    if stat.S_ISLNK(info.st_mode):
                        spec = classifier.classify_paths(['./' + relative], str(self.root), str(self.root), self.trash)[0]
                        if spec.error or spec.indeterminable or spec.wildcard:
                            issues.append({'path': relative, 'code': 'LINK_API_UNDETERMINABLE'})
                            return
                        if not spec.is_symlink:
                            issues.append({'path': relative, 'code': 'SOURCE_TYPE_CHANGED'})
                            return
                        destination = os.path.relpath(spec.link_target, self.root).replace(os.sep, '/')
                        if spec.protected or self.protected(destination):
                            protected[relative] = 'Protected link boundary; destination not traversed'
                            return
                    digest.update(json.dumps([relative, signature(info)], separators=(',', ':')).encode() + b'\n')
                    if stat.S_ISDIR(info.st_mode):
                        directories += 1
                        if depth >= max_depth:
                            issues.append({'path': relative, 'code': 'DEPTH_LIMIT_REACHED'})
                            return
                        names = []
                        with os.scandir(self.root / relative) as entries:
                            for entry in entries:
                                if total_examined + len(names) >= max_entries:
                                    issues.append({'path': relative, 'code': 'ENTRY_LIMIT_REACHED'})
                                    break
                                names.append(entry.name)
                        for name in sorted(names):
                            if total_examined >= max_entries:
                                issues.append({'path': relative + '/' + name, 'code': 'ENTRY_LIMIT_REACHED'})
                                break
                            visit(relative + '/' + name, depth + 1)
                        if signature((self.root / relative).lstat()) != signature(info):
                            issues.append({'path': relative, 'code': 'SOURCE_CHANGED_DURING_SCAN'})
                    elif stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
                        regular += stat.S_ISREG(info.st_mode)
                        links += stat.S_ISLNK(info.st_mode)
                        seen[info.st_dev, info.st_ino] = info.st_size
                        all_inodes[info.st_dev, info.st_ino] = info.st_size
                    else:
                        special.append(relative)
                except OSError as error:
                    issues.append({'path': relative, 'code': 'OS_ERROR', 'errno': error.errno})

            visit(scope, 0)
            observed_complete = not issues and not special
            whole_complete = observed_complete and not protected
            description.update(entries_examined=count, regular_files=regular, symlinks=links, directories=directories,
                               observed_bytes=sum(seen.values()), size_bytes=sum(seen.values()) if whole_complete else None,
                               size_complete=whole_complete, observed_subset_complete=observed_complete,
                               protected_roots=protected, issues=issues, special_entries=special,
                               metadata_digest=digest.hexdigest(), interior_checked=whole_complete,
                               approval='NOT_GRANTED', budget='NOT_CHECKED', core_policy=None)
            rows.append(description)
        warning, ignored = self.classify(rows)
        for row in rows:
            if row['category'] == 'regenerable' and row['size_complete']:
                specs = classifier.classify_paths(['./' + row['path']], str(self.root), str(self.root), self.trash)
                ctx = policy.PolicyContext(workspace=str(self.root), trash_root=self.trash, base_dir=str(self.root), mode=policy.load_mode(self.trash)['mode'], config=self.guard_config)
                def regenerable(batch, context):
                    return policy._regenerable(batch, context, lambda workspace, absolute: os.path.relpath(absolute, self.root).replace(os.sep, '/') in ignored)
                verdict = policy._decide_path_batch(specs, ctx, recursive=True, config_loader=lambda workspace: self.config, regenerable_check=regenerable)
                row['core_policy'] = {'decision': verdict.decision, 'code': verdict.code}
        result = {'schema': 'cleanup_group_measurement/v1', 'workspace': str(self.root), 'operation': 'read_only_measurement',
                'execution_supported': False, 'scopes': rows, 'unique_observed_bytes_across_scopes': sum(all_inodes.values()),
                'warnings': [warning] if warning else [], 'marker_bytes_read': self.marker_bytes,
                'entries_examined': total_examined, 'limits': {'max_entries': max_entries, 'max_depth': max_depth},
                'notice': 'Metadata observation only; protected bytes omitted, links not followed, no deletion permission or atomic content snapshot.'}
        result['observation_id'] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
        return result



def discover_groups(workspace, scopes=(), **limits):
    """Observe a bounded frontier below the original workspace or exact scopes."""
    return GroupScanner(workspace).discover(scopes, **limits)


def measure_groups(workspace, scopes, **limits):
    """Measure explicit non-overlapping directories, never following links."""
    return GroupScanner(workspace).measure(scopes, **limits)
