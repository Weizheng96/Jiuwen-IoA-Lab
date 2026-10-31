"""Install built distributions away from the checkout, without network access."""
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import venv
import shutil
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def run(command, cwd):
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True,
                            encoding='utf-8', env={**os.environ, 'PYTHONUTF8':'1'}, timeout=120)
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]
    return result.stdout


def test_built_packages_have_own_versions_and_platform_resources(tmp_path):
    wheels = tmp_path/'wheels'
    source = tmp_path/'platform-source'
    shutil.copytree(ROOT/'src/platform',source,ignore=shutil.ignore_patterns('build','dist','*.egg-info','__pycache__','*.pyc'))
    stale = source/'build/lib/ioalab/psp/obsolete.py'
    stale.parent.mkdir(parents=True)
    stale.write_text("raise RuntimeError('obsolete module must never ship')\n",encoding='utf-8')
    run([sys.executable,str(ROOT/'tools/build_packages.py'),'--out',str(wheels),str(source),str(ROOT/'src/access/sdk'),str(ROOT/'src/hosts/jiuwen'),str(ROOT/'src/official')], tmp_path)
    with zipfile.ZipFile(next(wheels.glob('ioalab-*.whl'))) as archive:
        assert not any(name.startswith('ioalab/psp/') for name in archive.namelist())
    with zipfile.ZipFile(next(wheels.glob('ioalab_sdk-*.whl'))) as archive:
        assert 'ioalab_sdk/runtime/web/user.html' in archive.namelist()
        assert not any(name.startswith('ioalab_jiuwen/') for name in archive.namelist())
        assert 'ioalab_sdk/assistant.py' not in archive.namelist()
        assert 'ioalab_sdk/provider.py' not in archive.namelist()
        assert 'ioalab_sdk/provider/publication.py' in archive.namelist()
        assert not any(name.startswith('ioalab_tools/') for name in archive.namelist())
        assert not {'ioalab_sdk/calendar.py','ioalab_sdk/agentmail.py','ioalab_sdk/schedule.py','ioalab_sdk/schedule_cli.py'}.intersection(archive.namelist())
    with zipfile.ZipFile(next(wheels.glob('ioalab_official-*.whl'))) as archive:
        assert {'ioalab_tools/handlers.py','ioalab_tools/appointments.py','ioalab_tools/calendar.py','ioalab_tools/agentmail.py','ioalab_tools/schedule_cli.py'}.issubset(archive.namelist())
        assert 'ioalab_services/catalog.py' in archive.namelist()
        assert 'ioalab_services/profiles.py' in archive.namelist()
        assert len([name for name in archive.namelist() if name.startswith('ioalab_services/assets/domains/') and name.endswith('.json')]) == 8
        entries=archive.read(next(name for name in archive.namelist() if name.endswith('.dist-info/entry_points.txt'))).decode()
        assert 'calendar = ioalab_tools.appointments:configure' in entries and 'ioalab-schedule = ioalab_tools.schedule_cli:main' in entries
    environment = tmp_path/'installed'
    venv.EnvBuilder(with_pip=True).create(environment)
    python = environment/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    run([str(python),'-m','pip','install','--no-index','--no-deps',*[str(p) for p in wheels.glob('*.whl')]], tmp_path)
    output = run([str(python),'-I','-c', '''
import importlib.metadata as m, importlib.resources as r, json
from ioalab.config import Settings
from ioalab_services.catalog import definitions,seed_manifest
page=r.files('ioalab').joinpath('web/static/index.html').read_text(encoding='utf-8')
print(json.dumps({'core':m.version('ioalab'),'sdk':m.version('ioalab-sdk'),'host':m.version('ioalab-jiuwen'),'official':m.version('ioalab-official'),
                 'ownedServices':len(definitions()),'ownedSeed':len(seed_manifest()['domains']),
                 'resource':bool(page.strip()),'data':str(Settings().data),
                 'userPage':bool(m.distribution('ioalab-sdk').locate_file('ioalab_sdk/runtime/web/user.html').read_text(encoding='utf-8').strip()),
                 'hostEntry':{e.name:e.value for e in m.distribution('ioalab-jiuwen').entry_points if e.group=='console_scripts'}.get('ioalab-jiuwen'),
                 'accessEntry':{e.name:e.value for e in m.distribution('ioalab-sdk').entry_points if e.group=='console_scripts'}.get('ioalab-access')}))
'''], tmp_path)
    result = json.loads(output)
    assert result['core'] == (ROOT/'src/platform/VERSION').read_text().strip().removeprefix('v')
    assert result['sdk'] == (ROOT/'src/access/sdk/VERSION').read_text().strip().removeprefix('v')
    assert result['host'] == (ROOT/'src/hosts/jiuwen/VERSION').read_text().strip().removeprefix('v')
    assert result['official'] == (ROOT/'src/official/VERSION').read_text().strip().removeprefix('v')
    assert result['resource'] and result['userPage']
    assert result['ownedServices'] == 50 and result['ownedSeed'] == 8
    assert result['hostEntry']=='ioalab_jiuwen.__main__:main'
    assert result['accessEntry']=='ioalab_sdk.runtime.__main__:main'
    assert ROOT.as_posix().lower() not in result['data'].replace('\\','/').lower()


def test_official_wheel_is_reproducible_when_source_mtimes_change(tmp_path):
    from tools.build_packages import build_wheel
    source=tmp_path/'official'
    shutil.copytree(ROOT/'src/official',source,ignore=shutil.ignore_patterns('build','dist','*.egg-info','__pycache__','*.pyc'))
    first=build_wheel(source,tmp_path/'first').read_bytes()
    for path in source.rglob('*'):
        if path.is_file():os.utime(path,(1700000000,1700000000))
    second=build_wheel(source,tmp_path/'second').read_bytes()
    assert first==second
    # Resources also load through zipimport, with no checkout or installed SDK.
    wheel=next((tmp_path/'second').glob('*.whl'))
    output=run([sys.executable,'-I','-c',
        "import sys;sys.path.insert(0,sys.argv[1]);from ioalab_services.catalog import definitions,seed_manifest;print(len(definitions()),len(seed_manifest()['domains']))",str(wheel)],tmp_path)
    assert output.strip()=='50 8'
