import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
project_arg = os.environ.get('AGENT_GUARD_PROJECT')
if not project_arg:
    raise SystemExit('set AGENT_GUARD_PROJECT to the agent-guard checkout path')
PROJECT = Path(project_arg).expanduser().resolve(strict=True)
SCRIPTS = PROJECT / 'skills/delete-guard/scripts'
if not (SCRIPTS / 'check.py').is_file():
    raise SystemExit('AGENT_GUARD_PROJECT has no delete-guard check.py')
ENV = {k: v for k, v in os.environ.items() if not k.startswith('AGENT_GUARD_')}
ENV['AGENT_GUARD_SESSION'] = 'astra-high-acceptance'
EVENTS = []
PASSED = []


def run(cwd, argv, expected=0):
    started = time.monotonic()
    p = subprocess.run([str(x) for x in argv], cwd=cwd, env=ENV,
                       capture_output=True, text=True, timeout=45)
    event = dict(cwd=str(cwd), argv=[str(x) for x in argv], exit=p.returncode,
                 stdout=p.stdout, stderr=p.stderr,
                 elapsed_s=round(time.monotonic() - started, 3))
    EVENTS.append(event)
    with (ROOT / 'events.jsonl').open('a') as fh:
        fh.write(json.dumps(event, ensure_ascii=True) + '\n')
    assert p.returncode == expected, event
    return p.stdout


def cli(cwd, name, *args, expected=0):
    return json.loads(run(cwd, [sys.executable, SCRIPTS / (name + '.py'),
                               *args, '--json'], expected))


def check(cwd, command, expected=0, enforce=True):
    args = [sys.executable, SCRIPTS / 'check.py', '--json']
    if enforce:
        args.append('--enforce')
    return json.loads(run(cwd, [*args, '--', command], expected))


def write(cwd, name, content):
    p = cwd / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    return p


def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def repo(name, ignored=True):
    p = ROOT / name
    p.mkdir()
    run(p, ['git', 'init', '-q'])
    for key, value in [('user.name', 'Guard Fixture'), ('user.email', 'fixture@local'),
                       ('commit.gpgsign', 'false')]:
        run(p, ['git', 'config', key, value])
    write(p, '.gitignore', b'node_modules/\n*.log\n' +
          (b'.agent-trash/\n' if ignored else b''))
    write(p, 'src/main.txt', b'baseline\n')
    run(p, ['git', 'add', '.'])
    run(p, ['git', 'commit', '-qm', 'fixture baseline'])
    return p


def state(cwd, txid):
    return next(t['state'] for t in cli(cwd, 'restore', 'list')['transactions']
                if t['txid'] == txid)


def passed(name):
    PASSED.append(name)
    print('PASS ' + name, flush=True)


def roundtrip(cwd, names):
    before = {n: digest(cwd / n) for n in names}
    result = cli(cwd, 'safe_delete', *names)
    assert result['verdict']['decision'] == 'RELOCATE', result
    txid = result['txid']
    assert all(not (cwd / n).exists() for n in names)
    assert state(cwd, txid) == 'RESTORABLE'
    restored = cli(cwd, 'restore', txid)
    assert restored['ok'], restored
    assert {n: digest(cwd / n) for n in names} == before
    assert state(cwd, txid) == 'RESTORED'
    return txid


def main():
    p = repo('workflow')
    outside = write(ROOT, 'outside.txt', b'outside sentinel\n')
    before = digest(p / 'src/main.txt')
    for command, code in [
        ('rm -rf .', 'BLOCK_PROTECTED_PATH'),
        ('rm -rf .git', 'BLOCK_PROTECTED_PATH'),
        ('rm ../outside.txt', 'BLOCK_OUT_OF_WORKSPACE'),
        ('rm -rf $CLEAN_TARGET', 'BLOCK_UNDETERMINABLE_EFFECT'),
        ('rm *.log', 'BLOCK_WILDCARD'),
        ('git push --force', 'BLOCK_FORCE_PUSH'),
        ('bash -c "rm -rf src"', 'BLOCK_UNDETERMINABLE_EFFECT'),
    ]:
        result = check(p, command, expected=2)
        assert (result['decision'], result['code']) == ('BLOCK', code), result
        assert digest(p / 'src/main.txt') == before and outside.exists()
        passed('block: ' + code + ' / ' + command)
    result = check(p, 'cd src && rm main.txt', expected=3)
    assert result['decision'] == 'ASK' and digest(p / 'src/main.txt') == before
    passed('compound command asks without execution')
    result = check(p, 'rm -rf .', enforce=False)
    assert result['decision'] == 'BLOCK' and result['exit'] == 0
    passed('advisory verdict differs from enforcement exit status')
    roundtrip(p, ['src/main.txt'])
    passed('tracked file hash and RESTORABLE -> RESTORED lifecycle')
    write(p, 'build/nested/output.bin', bytes(range(256)))
    result = check(p, 'rm -rf build')
    txid = result['compensations'][0]['txid']
    assert result['decision'] == 'ALLOW' and state(p, txid) == 'RESTORABLE'
    assert not (p / 'build').exists()
    cli(p, 'restore', txid)
    assert (p / 'build/nested/output.bin').read_bytes() == bytes(range(256))
    passed('check --enforce quarantines nonignored directory')
    write(p, 'session.log', b'valuable ignored log\n')
    roundtrip(p, ['session.log'])
    passed('ignored log is recoverable rather than regenerable')
    write(p, 'node_modules/fixture/index.js', b'fixture dependency\n')
    result = cli(p, 'safe_delete', 'node_modules')
    assert result['verdict']['code'] == 'ALLOW_REGENERABLE'
    assert not (p / 'node_modules').exists() and 'txid' not in result
    passed('ignored dependency artifact deletes through safe_delete')
    write(p, 'notes.txt', b'original notes\n')
    result = cli(p, 'safe_delete', 'notes.txt')
    txid = result['txid']
    write(p, 'notes.txt', b'new content\n')
    result = cli(p, 'restore', txid, expected=3)
    assert result['conflicts'] and (p / 'notes.txt').read_bytes() == b'new content\n'
    assert state(p, txid) == 'RESTORABLE'
    passed('restore conflict preserves new content and quarantine')
    p = repo('clean')
    names = ['\u4e2d\u6587\u7b14\u8bb0.txt', 'space name.txt', 'line\nbreak.txt',
             'tab\tname.txt', 'quote"name.txt', 'back\\slash.txt']
    hashes = {n: digest(write(p, n, ('valuable ' + n).encode())) for n in names}
    result = check(p, 'git clean -fd')
    txid = result['compensations'][0]['txid']
    assert result['compensations'][0]['moved'] == len(names)
    assert state(p, txid) == 'RESTORABLE'
    run(p, ['git', 'clean', '-fd'])
    cli(p, 'restore', txid)
    assert {n: digest(p / n) for n in names} == hashes
    passed('git clean special filenames and SHA-256 restoration')
    p = repo('snapshot')
    dirty = write(p, 'src/main.txt', b'uncommitted valuable edit\n')
    before = digest(dirty)
    result = check(p, 'git reset --hard')
    snapshot = result['compensations'][0]
    assert snapshot['strategy'] == 'snapshot' and snapshot['sha']
    assert state(p, snapshot['txid']) == 'RESTORABLE'
    assert digest(dirty) == before
    run(p, ['git', 'reset', '--hard', 'HEAD'])
    assert dirty.read_bytes() == b'baseline\n'
    cli(p, 'restore', snapshot['txid'])
    assert digest(dirty) == before
    assert snapshot['sha'] in run(p, ['git', 'stash', 'list', '--format=%H'])
    passed('git snapshot before reset and hash-verified restore; stash retained')
    p = repo('audit-failure')
    check(p, 'git status')
    target = write(p, 'node_modules/fixture/index.js', b'keep on audit failure\n')
    audit = p / '.agent-trash/audit.jsonl'
    audit.chmod(0o400)
    try:
        result = cli(p, 'safe_delete', 'node_modules', expected=2)
        assert result['verdict']['decision'] == 'BLOCK' and target.exists()
        assert 'audit intent failed' in result['verdict']['reasons'][0]
    finally:
        audit.chmod(0o600)
    passed('audit intent failure blocks regenerable deletion')
    p = repo('readonly-preignored')
    info = p / '.git/info'
    exclude = info / 'exclude'
    info.chmod(0o500)
    exclude.chmod(0o400)
    try:
        roundtrip(p, ['src/main.txt'])
    finally:
        info.chmod(0o700)
        exclude.chmod(0o600)
    passed('preignored quarantine works with readonly Git exclude metadata')
    p = repo('readonly-unignored', ignored=False)
    info = p / '.git/info'
    exclude = info / 'exclude'
    info.chmod(0o500)
    exclude.chmod(0o400)
    before = digest(p / 'src/main.txt')
    try:
        result = cli(p, 'safe_delete', 'src/main.txt', expected=2)
        assert result['verdict']['decision'] == 'BLOCK'
        assert digest(p / 'src/main.txt') == before
        assert not (p / '.agent-trash').exists()
    finally:
        info.chmod(0o700)
        exclude.chmod(0o600)
    passed('unignored quarantine fails before move with readonly Git metadata')
    assert outside.read_bytes() == b'outside sentinel\n'
    for folder in ROOT.iterdir():
        if not folder.is_dir() or not (folder / '.git').exists():
            continue
        assert '.agent-trash' not in run(folder, ['git', 'status', '--porcelain'])
        for file in (folder / '.agent-trash').glob('*.jsonl'):
            assert all(isinstance(json.loads(line), dict) for line in file.read_text().splitlines())
    passed('all audit and manifest records parse; quarantine stays ignored')
    (ROOT / 'summary.json').write_text(json.dumps(
        {'passed': len(PASSED), 'checks': PASSED, 'commands': len(EVENTS)}, indent=2) + '\n')
    print('Completed: ' + str(len(PASSED)) + ' checks', flush=True)


if __name__ == '__main__':
    main()
