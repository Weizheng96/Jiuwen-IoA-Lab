"""Production boundaries, content changes, target locks and failed-install evidence."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
import zipfile

import pytest

from deploy.environment import Environment, atomic, writer
from deploy.release import FORMAT, UNITS, current_target, digest, load, pins, sha
from deploy.build import assemble
from deploy.dependencies import inputs


def wheel(folder, name, *, requirements=(), payload='original'):
    path = folder / (name.replace('-', '_') + '-1.0-py3-none-any.whl')
    metadata = 'Metadata-Version: 2.1\nName: ' + name + '\nVersion: 1.0\nRequires-Python: >=3.11\n'
    metadata += ''.join('Requires-Dist: ' + requirement + '\n' for requirement in requirements)
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr(name.replace('-', '_') + '-1.0.dist-info/METADATA', metadata)
        archive.writestr(name.replace('-', '_') + '/__init__.py', payload)
    return path


@pytest.fixture
def source(tmp_path):
    root = tmp_path / 'source'
    pool = root / 'wheels'
    pool.mkdir(parents=True)
    for name in sorted(set().union(*UNITS.values())):
        wheel(pool, name, requirements=['ordinary>=1'])
    (root / 'deploy').mkdir()
    (root / 'deploy/default-seed.json').write_text('{}', encoding='utf-8')
    (root / 'vendor/licenses').mkdir(parents=True)
    (root / 'vendor/SOURCES.json').write_text('{}', encoding='utf-8')
    (root / 'vendor/licenses/LICENSE').write_text('upstream license', encoding='utf-8')
    return root


@pytest.fixture
def environment_root(tmp_path_factory):
    # Keep Windows native dependency probes independent of pytest test-name length.
    return tmp_path_factory.getbasetemp() / ('e-' + uuid.uuid4().hex[:8])


def release(source, output, target=None):
    target = target or current_target()
    wheels = sorted((source / 'wheels').glob('*.whl'))
    locks = output / 'locks'
    locks.mkdir(parents=True)
    for unit in UNITS:
        lock = locks / (unit + '.lock')
        lock.write_text('ordinary==1.0\n', encoding='utf-8')
        metadata = {'format': 'ioalab.deployment-lock/1', 'unit': unit, 'target': target, 'python': '3.11',
                    'inputSha256': inputs(unit, target, wheels)['inputSha256'], 'lockSha256': sha(lock), 'resolver': 'test fixture'}
        (locks / (unit + '.json')).write_text(json.dumps(metadata), encoding='utf-8')
    skills = output / 'skills'
    skills.mkdir()
    for role in ('assistant', 'provider'):
        with zipfile.ZipFile(skills / ('ioalab-' + role + '-skill.zip'), 'w') as archive:
            for path in wheels:
                if path.stem.split('-')[0].replace('_', '-') in UNITS['participant']:
                    archive.write(path, 'fixture/assets/wheels/' + path.name)
    folder = output / 'staging'
    folder.mkdir()
    metadata = assemble(source, folder, target, locks, wheels, skills)
    result = output / metadata['releaseId']
    folder.rename(result)
    return result


def resign(folder, value):
    value['releaseId'] = digest({key: data for key, data in value.items() if key != 'releaseId'})
    (folder / 'release.json').write_text(json.dumps(value), encoding='utf-8')
    destination = folder.with_name(value['releaseId'])
    folder.rename(destination)
    return destination


def test_target_markers_extras_and_local_direct_urls(source):
    pool = source / 'wheels'
    wheel(pool, 'ioalab', requirements=['a2c[server] @ https://github.com/example/a2c', 'ordinary>=1'])
    wheel(pool, 'a2c', requirements=['linux-only==2; sys_platform == "linux"', 'extra-package>=3; extra == "server"'])
    linux = inputs('core', 'linux-x86_64', pool.glob('*.whl'))
    windows = inputs('core', 'windows-x86_64', pool.glob('*.whl'))
    assert linux['requirements'] == ['extra-package>=3', 'linux-only==2', 'ordinary>=1']
    assert windows['requirements'] == ['extra-package>=3', 'ordinary>=1']
    assert linux['inputSha256'] != windows['inputSha256']
    wheel(pool, 'a2c', requirements=['ioalab-sdk>=1'])
    with pytest.raises(ValueError, match='crosses runtime boundary'):
        inputs('core', 'linux-x86_64', pool.glob('*.whl'))
    wheel(pool, 'a2c', requirements=['unbundled @ https://github.com/example/missing'])
    with pytest.raises(ValueError, match='Unbundled direct URL'):
        inputs('core', 'linux-x86_64', pool.glob('*.whl'))


def test_participant_installed_catalog_matches_verified_release_ids(source,tmp_path,environment_root,monkeypatch):
    """Exercise generated install checks in a child, with a non-50 catalog and wrong-ID negatives."""
    seed={'domains':[{'account':'provider-a','services':[{'id':'alpha'},{'id':'beta'}]}]}
    (source/'deploy/default-seed.json').write_text(json.dumps(seed),encoding='utf-8')
    folder=release(source,tmp_path/'catalog')
    selected=Environment(folder,'participant',environment_root)
    prefix=selected.folder/'check-prefix';prefix.mkdir(parents=True)
    actual=['alpha','beta'];calls=[]
    def checked_child(python,args,work,log):
        calls.append(args)
        if args[:2]==['-m','pip']:
            assert args[-1]=='check'
            return
        assert args[0]=='-c'
        # Owned fixture modules isolate this validator from the developer's packages.
        bootstrap="""import importlib.metadata as metadata,sys,types
sys.prefix=PREFIX
versions=VERSIONS
metadata.distributions=lambda:[types.SimpleNamespace(metadata={'Name':k},version=v) for k,v in versions.items()]
for name in ('ioalab_sdk','ioalab_sdk.runtime','ioalab_sdk.runtime.resident','ioalab_jiuwen','ioalab_jiuwen.process','ioalab_services','ioalab_services.catalog','ioalab_admin_assistant','ioalab_admin_assistant.presets'):
 module=types.ModuleType(name);module.__path__=[];module.__file__=sys.prefix+'/'+name.replace('.','/')+'.py'
 sys.modules[name]=module
 if '.' in name:setattr(sys.modules[name.rsplit('.',1)[0]],name.rsplit('.',1)[1],module)
sys.modules['ioalab_sdk.runtime.resident'].ResidentRuntime=object
sys.modules['ioalab_jiuwen.process'].ProcessHost=object
sys.modules['ioalab_services.catalog'].definitions=lambda:[{'id':item} for item in ACTUAL]
sys.modules['ioalab_admin_assistant.presets'].load=lambda **kw:(None,['one','two'])
""".replace('PREFIX',repr(str(prefix))).replace('VERSIONS',repr(selected.versions)).replace('ACTUAL',repr(actual))
        result=subprocess.run([sys.executable,'-I','-c',bootstrap+args[1],*args[2:]],capture_output=True,text=True)
        if result.returncode:raise subprocess.CalledProcessError(result.returncode,result.args,output=result.stdout,stderr=result.stderr)
    monkeypatch.setattr('deploy.environment.execute',checked_child)
    assert selected.validate('check-prefix',prefix/'probe.log')==prefix
    assert calls[-1][-1]=='check'
    for wrong in (['alpha','wrong'],['alpha'],['alpha','beta','extra'],['alpha','alpha']):
        actual[:]=wrong
        with pytest.raises(subprocess.CalledProcessError) as rejected:
            selected.validate('check-prefix',prefix/'probe.log')
        assert 'Installed official catalog differs' in rejected.value.stderr
    for index,bad in enumerate(({'domains':[]},
            {'domains':[seed['domains'][0],seed['domains'][0]]},
            {'domains':[{'account':'a','services':[{'id':'same'},{'id':'same'}]}]},
            {'domains':[{'account':'a','services':[{'id':1}]}]})):
        (source/'deploy/default-seed.json').write_text(json.dumps(bad),encoding='utf-8')
        invalid=Environment(release(source,tmp_path/('invalid-'+str(index))),'participant',environment_root)
        (invalid.folder/'check-prefix').mkdir(parents=True,exist_ok=True)
        before=len(calls)
        with pytest.raises(ValueError,match='catalog'):
            invalid.validate('check-prefix',prefix/'probe.log')
        assert len(calls)==before,'Bad release declarations must be rejected before running installed code'


def test_release_and_unit_identity_change_for_same_version_new_bytes(source, tmp_path, environment_root):
    first = release(source, tmp_path / 'one')
    one = Environment(first, 'participant', environment_root)
    previous = inputs('participant', current_target(), (source / 'wheels').glob('*.whl'))
    wheel(source / 'wheels', 'ioalab-sdk', requirements=['ordinary>=1'], payload='fixed implementation')
    second = release(source, tmp_path / 'two')
    two = Environment(second, 'participant', environment_root)
    assert first.name != second.name and one.id != two.id
    assert previous == inputs('participant', current_target(), (source / 'wheels').glob('*.whl'))
    assert load(first)['format'] == FORMAT
    assert load(second)['format'] == FORMAT
    one.prepare_folder()
    owner = (one.folder / 'owner.json').read_bytes()
    two.folder = one.folder  # A deliberately forced short-prefix collision.
    with pytest.raises(RuntimeError, match='unknown ownership'):
        two.prepare_folder()
    assert (one.folder / 'owner.json').read_bytes() == owner


def test_release_tampering_paths_role_mixing_and_exact_locks(source, tmp_path):
    folder = release(source, tmp_path / 'first')
    document = load(folder)
    selected = folder / document['units']['core']['lock']['path']
    selected.write_text('ordinary==2\n', encoding='utf-8')
    with pytest.raises(ValueError, match='checksum'):
        load(folder)
    selected.write_text('ordinary==1.0\n', encoding='utf-8')
    document['units']['core']['lock']['path'] = '../outside.lock'
    folder = resign(folder, document)
    with pytest.raises(ValueError, match='escapes'):
        load(folder)
    folder = release(source, tmp_path / 'second')
    document = load(folder)
    document['units']['core']['wheels'].append(document['units']['swarm']['wheels'][0])
    folder = resign(folder, document)
    with pytest.raises(ValueError, match='boundary'):
        load(folder)
    for text in ('ordinary>=1', '-r elsewhere', 'ordinary @ https://example.com/pkg', 'ordinary==1; sys_platform=="win32"', 'a_b==1\na-b==1'):
        with pytest.raises(ValueError):
            pins(text)


def test_changed_requirements_reject_old_lock(source, tmp_path):
    folder = release(source, tmp_path / 'prepared')
    assert load(folder)
    wheel(source / 'wheels', 'ioalab-sdk', requirements=['different>=2'])
    (tmp_path / 'new').mkdir()
    with pytest.raises(ValueError, match='Rebuild the target lock'):
        assemble(source, tmp_path / 'new', current_target(), tmp_path / 'prepared/locks',
                 sorted((source / 'wheels').glob('*.whl')), tmp_path / 'prepared/skills')


def test_unknown_owner_unknown_attempt_and_os_writer_do_not_repair(source, tmp_path, environment_root):
    folder = release(source, tmp_path / 'release')
    environment = Environment(folder, 'core', environment_root)
    environment.folder.mkdir(parents=True)
    sentinel = environment.folder / 'user-evidence.txt'
    sentinel.write_text('must survive', encoding='utf-8')
    with pytest.raises(RuntimeError, match='unknown ownership'):
        environment.install()
    assert sentinel.read_text() == 'must survive'
    atomic(environment.folder / 'owner.json', environment.identity)
    with pytest.raises(RuntimeError, match='Unrecorded'):
        environment.install(retry_failed=True)
    state = {'environmentId': environment.id, 'phase': 'installing', 'attempt': 'a-' + 'a' * 16}
    atomic(environment.folder / 'state.json', state)
    with pytest.raises(RuntimeError, match='result is unknown'):
        environment.install(retry_failed=True)
    with writer(environment.folder / 'writer.lock'):
        with pytest.raises(RuntimeError, match='holds this environment writer lock'):
            environment.install()
    assert sentinel.read_text() == 'must survive'


def test_failed_retry_uses_new_prefix_and_preserves_original(source, tmp_path, monkeypatch, environment_root):
    folder = release(source, tmp_path / 'release')
    environment = Environment(folder, 'participant', environment_root)
    def fake_venv(self, path):
        Path(path).mkdir()
        (Path(path) / 'failure-evidence').write_text('original', encoding='utf-8')
    monkeypatch.setattr('deploy.environment.venv.EnvBuilder.create', fake_venv)
    def fail(*args):
        raise subprocess.CalledProcessError(1, ['fixture'])
    monkeypatch.setattr('deploy.environment.execute', fail)
    with pytest.raises(RuntimeError, match='preserved'):
        environment.install()
    first = environment.state()
    assert first['phase'] == 'failed'
    with pytest.raises(RuntimeError, match='explicitly retry'):
        environment.install()
    with pytest.raises(RuntimeError, match='preserved'):
        environment.install(retry_failed=True)
    second = environment.state()
    assert first['attempt'] != second['attempt']
    assert (environment.folder / first['attempt'] / 'failure-evidence').read_text() == 'original'
    assert (environment.folder / second['attempt'] / 'failure-evidence').read_text() == 'original'


def test_target_mismatch_and_mirror_credentials_are_rejected_before_install(source, tmp_path, environment_root):
    other = 'linux-x86_64' if os.name == 'nt' else 'windows-x86_64'
    folder = release(source, tmp_path / 'other', other)
    with pytest.raises(ValueError, match='target differs'):
        Environment(folder, 'core', environment_root)
    folder = release(source, tmp_path / 'native')
    environment = Environment(folder, 'core', environment_root)
    with pytest.raises(ValueError, match='embedded credentials'):
        environment.install(index='https://private:secret@example.com/simple')
    assert not environment.root.exists()


def test_changed_ready_identity_is_rejected_before_executing_python(source, tmp_path, monkeypatch, environment_root):
    folder = release(source, tmp_path / 'release')
    environment = Environment(folder, 'core', environment_root)
    environment.prepare_folder()
    state = {'environmentId': environment.id, 'phase': 'ready', 'attempt': 'a-' + 'b' * 16}
    attempt = environment.folder / state['attempt']
    attempt.mkdir()
    atomic(attempt / 'deployment.json', {'identity': 'changed'})
    atomic(environment.folder / 'state.json', state)
    def forbidden(*args):
        pytest.fail('An unverified environment must not execute its interpreter')
    monkeypatch.setattr('deploy.environment.execute', forbidden)
    with pytest.raises(RuntimeError, match='identity has changed'):
        environment.install()
    assert environment.state() == state


def test_checked_in_target_lock_records():
    from tools.check_deployment import validate
    records = validate()
    assert len(records) == 6
    assert all(record['ordinaryPackages'] > 0 for record in records)


def test_generated_lock_digest_survives_git_lf_normalization(tmp_path):
    from tools.lock_deployment import write_lock
    repository = tmp_path / 'git-roundtrip'
    repository.mkdir()
    environment = {key: value for key, value in os.environ.items() if not key.upper().startswith('GIT_')}
    def git(*arguments):
        return subprocess.check_output(['git', *arguments], cwd=repository, env=environment, stderr=subprocess.PIPE)
    git('init', '--quiet')
    (repository / '.gitattributes').write_bytes(b'*.lock text eol=lf\n')
    lock = repository / 'dependencies.lock'
    write_lock(lock, {'ordinary': '1.0'})
    expected = sha(lock)
    git('-c', 'core.autocrlf=true', 'add', '--', '.gitattributes', 'dependencies.lock')
    stored = git('show', ':dependencies.lock')
    import hashlib
    assert stored == b'ordinary==1.0\n'
    assert hashlib.sha256(stored).hexdigest() == expected


@pytest.mark.skipif(os.name != 'nt', reason='Windows native loader path budget')
def test_long_windows_root_is_rejected_before_creating_any_installation(source, tmp_path):
    folder = release(source, tmp_path / 'release')
    too_long = tmp_path / ('long-root-' + 'x' * 150)
    with pytest.raises(ValueError, match='shorter --root'):
        Environment(folder, 'swarm', too_long)
    assert not too_long.exists()
