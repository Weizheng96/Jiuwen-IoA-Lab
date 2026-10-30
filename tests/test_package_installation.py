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
    run([sys.executable,str(ROOT/'tools/build_packages.py'),'--out',str(wheels),str(source),str(ROOT/'src/access/sdk')], tmp_path)
    with zipfile.ZipFile(next(wheels.glob('ioalab-*.whl'))) as archive:
        assert not any(name.startswith('ioalab/psp/') for name in archive.namelist())
    environment = tmp_path/'installed'
    venv.EnvBuilder(with_pip=True).create(environment)
    python = environment/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    run([str(python),'-m','pip','install','--no-index','--no-deps',*[str(p) for p in wheels.glob('*.whl')]], tmp_path)
    output = run([str(python),'-I','-c', '''
import importlib.metadata as m, importlib.resources as r, json
from ioalab.config import Settings
page=r.files('ioalab').joinpath('web/static/index.html').read_text(encoding='utf-8')
print(json.dumps({'core':m.version('ioalab'),'sdk':m.version('ioalab-sdk'),
                 'resource':bool(page.strip()),'data':str(Settings().data)}))
'''], tmp_path)
    result = json.loads(output)
    assert result['core'] == (ROOT/'src/platform/VERSION').read_text().strip().removeprefix('v')
    assert result['sdk'] == (ROOT/'src/access/sdk/VERSION').read_text().strip().removeprefix('v')
    assert result['resource']
    assert ROOT.as_posix().lower() not in result['data'].replace('\\','/').lower()
