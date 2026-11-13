"""Install built distributions away from the checkout, without network access."""
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import sysconfig
import venv
import shutil
import zipfile
import pytest
from email.parser import BytesParser
from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]
# A dev venv may itself borrow dependencies via .pth; use their actual installed site.
DEPENDENCY_SITES = list(dict.fromkeys([sysconfig.get_paths()["purelib"], str(importlib.metadata.distribution("httpx").locate_file(""))]))
DEPENDENCY_PATHS = "\n".join(DEPENDENCY_SITES)+"\n"


def test_single_client_wheel_installs_both_roles_away_from_checkout(tmp_path):
    from tools.build_client import build
    wheel=build(tmp_path/'wheels')
    with zipfile.ZipFile(wheel) as archive:
        names=archive.namelist()
        assert 'ioalab_sdk/runtime/web/account.html' in names
        assert 'ioalab_sdk/runtime/trust/platform-trust.json' in names
        assert 'ioalab_sdk/runtime/trust/ioalab-ca.pem' in names
        assert not any(b'PRIVATE KEY' in archive.read(name) for name in names if name.endswith(('.pem', '.key')))
        assert 'ioalab_client/components.json' in names
        assert 'ioalab_client/runtime.py' in names and 'ioalab_codex/assembly.py' in names
        assert 'ioalab_claude_code/assembly.py' in names
        metadata = BytesParser().parsebytes(archive.read(next(name for name in names if name.endswith('.dist-info/METADATA'))))
        requirements = [Requirement(value) for value in metadata.get_all('Requires-Dist', [])]
        for host in ('jiuwen', 'codex', 'claude-code'):
            for platform in ('win32', 'linux', 'darwin'):
                resolved = {req.name for req in requirements if not req.marker or req.marker.evaluate({
                    'extra': host, 'sys_platform': platform, 'python_full_version': '3.11.4'})}
                assert ('claude-agent-sdk' in resolved) == (host == 'claude-code')
                assert ('pywin32' in resolved) == (host == 'claude-code' and platform == 'win32')
        assert 'a2c/__init__.py' in names and 'jiuwenswarm_sdk/__init__.py' in names
        assert not any(name.endswith('/installer.py') or 'settings.local.json' in name for name in names)
    environment=tmp_path/'environment';venv.EnvBuilder(with_pip=True).create(environment)
    python=environment/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    run([str(python),'-I','-m','pip','install','--no-index','--no-deps',str(wheel)],tmp_path)
    purelib=run([str(python),'-I','-c','import sysconfig;print(sysconfig.get_paths()["purelib"])'],tmp_path).strip()
    Path(purelib,'zz-third-party.pth').write_text(DEPENDENCY_PATHS,encoding='utf-8')
    default=run([str(python),'-I','-c',
        'from ioalab_sdk.runtime.config import DEFAULT_PLATFORM_URL; from ioalab_codex import assembled; print(DEFAULT_PLATFORM_URL)'],tmp_path)
    assert default.strip()=='https://ioalab.openjiuwen.com'
    # Both exported launchers bind this interpreter; the host interpreter need not contain the client.
    exported=json.loads(run([str(python),'-I','-m','ioalab_client','skills','--output',str(tmp_path/'skills')],tmp_path))
    assert exported['status']=='completed' and len(exported['data']['files'])==2
    for path in exported['data']['files']:
        with zipfile.ZipFile(path) as archive:
            script=next(name for name in archive.namelist() if name.endswith('/scripts/ioalab.py'))
            launch=tmp_path/Path(path).stem;archive.extractall(launch)
        result=json.loads(run([sys.executable,str(launch/script),'runtime.install.status'],tmp_path))
        assert result['status']=='completed' and Path(result['data']['python'])==python.absolute()
    output=run([str(python),'-I','-c',
        'import json;from ioalab_sdk.runtime.installation import current;print(json.dumps(current("ioalab-jiuwen")))'],tmp_path)
    assert json.loads(output)['versions']['ioalab-client']==(ROOT/'src/client/VERSION').read_text().strip().removeprefix('v')


def test_pip_installs_client_from_git_tag_and_records_its_commit(tmp_path):
    repository=tmp_path/'repository'
    shutil.copytree(ROOT/'src/client',repository/'src/client',
                    ignore=shutil.ignore_patterns('build','dist','*.egg-info','__pycache__','*.pyc'))
    vendor=repository/'vendor';vendor.mkdir()
    manifest=json.loads((ROOT/'vendor/SOURCES.json').read_text(encoding='utf-8'))
    shutil.copy2(ROOT/'vendor/SOURCES.json',vendor/'SOURCES.json')
    shutil.copytree(ROOT/'vendor/licenses',vendor/'licenses')
    for item in manifest['components']:
        if item['id'] in ('a2c','jiuwenswarm-process-sdk'):
            target=vendor/item['wheelInfo']['file'];target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(ROOT/'vendor'/item['wheelInfo']['file'],target)
    run(['git','init','-q',str(repository)],tmp_path)
    run(['git','-C',str(repository),'add','.'],tmp_path)
    run(['git','-C',str(repository),'-c','user.name=Client Test','-c','user.email=client-test@example.invalid',
         'commit','-qm','isolated client tag'],tmp_path)
    run(['git','-C',str(repository),'tag','client-test'],tmp_path)
    commit=run(['git','-C',str(repository),'rev-parse','HEAD'],tmp_path).strip()
    environment=tmp_path/'environment';venv.EnvBuilder(with_pip=True).create(environment)
    python=environment/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    purelib=run([str(python),'-I','-c','import sysconfig;print(sysconfig.get_paths()["purelib"])'],tmp_path).strip()
    # Supply already-verified third-party/build tools offline, after the new client's installed site.
    Path(purelib,'zz-third-party.pth').write_text(DEPENDENCY_PATHS,encoding='utf-8')
    Path(purelib,'zz-third-party.pth').write_text('import sys; sys.path[:0] = '+repr(DEPENDENCY_SITES)+'\n',encoding='utf-8')
    requirement='git+'+repository.as_uri()+'@client-test#subdirectory=src/client'
    run([str(python),'-I','-m','pip','install','--no-index','--no-deps','--no-build-isolation',requirement],tmp_path)
    Path(purelib,'zz-third-party.pth').write_text(DEPENDENCY_PATHS,encoding='utf-8')
    result=json.loads(run([str(python),'-I','-m','ioalab_client','status'],tmp_path))
    assert result['status']=='completed'
    assert result['data']['source']['vcs_info']['commit_id']==commit
    assert result['data']['source']['vcs_info']['requested_revision']=='client-test'
    assert result['data']['source']['subdirectory']=='src/client'


def run(command, cwd):
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True,
                            encoding='utf-8', env={**os.environ, 'PYTHONUTF8':'1'}, timeout=120)
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]
    return result.stdout


@pytest.fixture(scope='module')
def official_snapshot(tmp_path_factory):
    """One immutable baseline wheel; consumers mutate only their own copies."""
    from deploy.wheels import build_wheel
    root=tmp_path_factory.mktemp('official-wheel')
    source=root/'source'
    shutil.copytree(ROOT/'src/official',source,ignore=shutil.ignore_patterns('build','dist','*.egg-info','__pycache__','*.pyc'))
    wheel=build_wheel(source,root/'wheels')
    return source,wheel


def test_built_packages_have_own_versions_and_platform_resources(tmp_path,official_snapshot):
    wheels = tmp_path/'wheels'
    source = tmp_path/'platform-source'
    shutil.copytree(ROOT/'src/platform',source,ignore=shutil.ignore_patterns('build','dist','*.egg-info','__pycache__','*.pyc'))
    stale = source/'build/lib/ioalab/psp/obsolete.py'
    stale.parent.mkdir(parents=True)
    stale.write_text("raise RuntimeError('obsolete module must never ship')\n",encoding='utf-8')
    run([sys.executable,str(ROOT/'tools/build_packages.py'),'--out',str(wheels),str(source),str(ROOT/'src/client/sdk'),str(ROOT/'src/client/hosts/jiuwen')], tmp_path)
    shutil.copy2(official_snapshot[1],wheels/official_snapshot[1].name)
    with zipfile.ZipFile(next(wheels.glob('ioalab-*.whl'))) as archive:
        assert not any(name.startswith('ioalab/psp/') for name in archive.namelist())
    with zipfile.ZipFile(next(wheels.glob('ioalab_sdk-*.whl'))) as archive:
        assert 'ioalab_sdk/runtime/web/user.html' in archive.namelist()
        assert 'ioalab_sdk/runtime/__main__.py' not in archive.namelist()
        assert not any(name.startswith('ioalab_jiuwen/') for name in archive.namelist())
        assert 'ioalab_sdk/assistant.py' not in archive.namelist()
        assert 'ioalab_sdk/provider.py' not in archive.namelist()
        assert 'ioalab_sdk/provider/publication.py' in archive.namelist()
        retired={'client.py','messaging.py','cli.py','message_cli.py','workflow.py','scope.py',
                 'transport.py','observation.py','swarm_web.py','provider_app.py','provider_capability.py','provider_cli.py'}
        assert not {'ioalab_sdk/'+name for name in retired}.intersection(archive.namelist())
        assert 'ioalab_sdk/provider/agent.py' not in archive.namelist()
        entries=archive.read(next(name for name in archive.namelist() if name.endswith('.dist-info/entry_points.txt'))).decode()
        assert 'ioalab-access = ioalab_sdk.access:main' in entries
        assert 'ioalab-client' not in entries
        assert not any(name.startswith('ioalab_tools/') for name in archive.namelist())
        assert not {'ioalab_sdk/calendar.py','ioalab_sdk/agentmail.py','ioalab_sdk/schedule.py','ioalab_sdk/schedule_cli.py'}.intersection(archive.namelist())
    with zipfile.ZipFile(next(wheels.glob('ioalab_official-*.whl'))) as archive:
        assert {'ioalab_tools/agentmail_setup.py','ioalab_tools/appointments.py','ioalab_tools/calendar.py','ioalab_tools/agentmail.py','ioalab_tools/schedule_cli.py'}.issubset(archive.namelist())
        assert 'ioalab_tools/handlers.py' not in archive.namelist()
        assert 'ioalab_services/catalog.py' in archive.namelist()
        assert 'ioalab_services/profiles.py' in archive.namelist()
        assert 'ioalab_admin_assistant/preparation.py' in archive.namelist()
        assert 'ioalab_admin_assistant/presets.json' in archive.namelist()
        assert len([name for name in archive.namelist() if name.startswith('ioalab_services/assets/domains/') and name.endswith('.json')]) == 8
        entries=archive.read(next(name for name in archive.namelist() if name.endswith('.dist-info/entry_points.txt'))).decode()
        assert 'calendar = ioalab_tools.appointments:configure' in entries and 'ioalab-schedule = ioalab_tools.schedule_cli:main' in entries
        assert 'ioalab-outlook = ioalab_tools.outlook_login:main' in entries and 'ioalab_tools/outlook_login.py' in archive.namelist()
    environment = tmp_path/'installed'
    venv.EnvBuilder(with_pip=True).create(environment)
    python = environment/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    run([str(python),'-m','pip','install','--no-index','--no-deps',*[str(p) for p in wheels.glob('*.whl')]], tmp_path)
    # Package entries import their third-party dependencies; borrow them from the checkout's
    # environment after the installed site, so own modules and resources still load from the wheels.
    purelib=run([str(python),'-I','-c','import sysconfig;print(sysconfig.get_paths()["purelib"])'],tmp_path).strip()
    Path(purelib,'zz-third-party.pth').write_text(DEPENDENCY_PATHS,encoding='utf-8')
    output = run([str(python),'-I','-c', '''
import importlib.metadata as m, importlib.resources as r, json
import ioalab.config as core_config
import ioalab_services.catalog as service_catalog
from ioalab.config import Settings
from ioalab_services.catalog import definitions,seed_manifest
page=r.files('ioalab').joinpath('web/static/index.html').read_text(encoding='utf-8')
print(json.dumps({'core':m.version('ioalab'),'sdk':m.version('ioalab-sdk'),'host':m.version('ioalab-jiuwen'),'official':m.version('ioalab-official'),
                 'ownedServices':len(definitions()),'ownedSeed':len(seed_manifest()['domains']),
                 'resource':bool(page.strip()),'data':str(Settings().data),
                 'coreModuleFile':core_config.__file__,'catalogModuleFile':service_catalog.__file__,
                 'userPage':bool(m.distribution('ioalab-sdk').locate_file('ioalab_sdk/runtime/web/user.html').read_text(encoding='utf-8').strip()),
                 'hostEntry':{e.name:e.value for e in m.distribution('ioalab-jiuwen').entry_points if e.group=='console_scripts'}.get('ioalab-jiuwen'),
                 'accessEntry':{e.name:e.value for e in m.distribution('ioalab-sdk').entry_points if e.group=='console_scripts'}.get('ioalab-access')}))
'''], tmp_path)
    result = json.loads(output)
    assert result['core'] == (ROOT/'src/platform/VERSION').read_text().strip().removeprefix('v')
    assert result['sdk'] == (ROOT/'src/client/sdk/VERSION').read_text().strip().removeprefix('v')
    assert result['host'] == (ROOT/'src/client/hosts/jiuwen/VERSION').read_text().strip().removeprefix('v')
    assert result['official'] == (ROOT/'src/official/VERSION').read_text().strip().removeprefix('v')
    assert result['resource'] and result['userPage']
    assert result['ownedServices'] == 52 and result['ownedSeed'] == 8
    assert result['hostEntry']=='ioalab_jiuwen.__main__:main'
    assert result['accessEntry']=='ioalab_sdk.access:main'
    assert Path(result['data']).resolve() == (tmp_path/'data').resolve()
    for name in ('coreModuleFile', 'catalogModuleFile'):
        assert environment.resolve() in Path(result[name]).resolve().parents


def test_official_wheel_is_reproducible_when_source_mtimes_change(tmp_path,official_snapshot):
    from deploy.wheels import build_wheel
    source=tmp_path/'official'
    shutil.copytree(official_snapshot[0],source)
    first=official_snapshot[1].read_bytes()
    for path in source.rglob('*'):
        if path.is_file():os.utime(path,(1700000000,1700000000))
    second=build_wheel(source,tmp_path/'second').read_bytes()
    assert first==second
    # Resources also load through zipimport, with no checkout or installed SDK.
    wheel=next((tmp_path/'second').glob('*.whl'))
    output=run([sys.executable,'-I','-c',
        "import sys;sys.path.insert(0,sys.argv[1]);from ioalab_services.catalog import definitions,seed_manifest;print(len(definitions()),len(seed_manifest()['domains']))",str(wheel)],tmp_path)
    assert output.strip()=='52 8'
