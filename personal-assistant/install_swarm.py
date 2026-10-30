"""Install the pinned original Jiuwen runtime from bundled wheels; no Git or npm."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import venv

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.check_vendor import validate


def run(argv,**kwargs):subprocess.run([str(a) for a in argv],check=True,**kwargs)


def installation_plan(root=ROOT):
    root=Path(root).resolve();manifest=validate(root)
    wheels=[(root/'vendor'/i['wheel']).resolve() for i in manifest['components'] if i['delivery']=='build-wheel']
    lines=[]
    for line in (root/'personal-assistant/requirements-swarm.lock').read_text('utf-8').splitlines():
        lines.append((root/line[2:]).resolve().as_uri() if line.startswith('./vendor/') else line)
    return manifest,wheels,'\n'.join(lines)+'\n'


def ensure_pip(python):
    # An existing uv-created environment may have no pip. Bootstrap from Python's
    # bundled ensurepip without downloading or replacing the user's environment.
    probe=subprocess.run([str(python),'-c','import importlib.util; raise SystemExit(importlib.util.find_spec("pip") is None)'],capture_output=True)
    if probe.returncode:run([python,'-m','ensurepip','--upgrade'])


def initialize_workspace(python,data):
    fresh=not (data/'config/config.yaml').exists()
    environment={**os.environ,'JIUWENSWARM_DATA_DIR':str(data),'IOALAB_NEW_SWARM_WORKSPACE':'1' if fresh else '0'}
    prepare="""from pathlib import Path
import os
from jiuwenswarm.common.utils import prepare_workspace
prepare_workspace(False,preferred_language='zh' if os.environ['IOALAB_NEW_SWARM_WORKSPACE']=='1' else None,workspace_dir=Path(os.environ['JIUWENSWARM_DATA_DIR']))
"""
    run([python,'-c',prepare],env=environment)
    if fresh:
        # Jiuwen caches config paths during import. Start a new interpreter after
        # the workspace exists, so configuration cannot target packaged templates.
        configure="""from jiuwenswarm.common.config import update_config
def configure(document):
    document.setdefault('updater',{})['enabled']=False
    return document
update_config(configure)
"""
        run([python,'-c',configure],env=environment)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--venv',type=Path)
    parser.add_argument('--data',type=Path)
    parser.add_argument('--verify-only',action='store_true',help='Verify bundled inputs without installation or network')
    args=parser.parse_args()
    if sys.version_info[:2]!=(3,11):raise RuntimeError('Use Python 3.11 for the validated dependency lock')
    manifest,wheels,requirements=installation_plan()
    if args.verify_only:
        print('Pinned local Jiuwen inputs verified; Git and Node/npm are not required on the deployment host.');return
    if args.venv is None or args.data is None:parser.error('--venv and --data are required for installation')
    environment=args.venv.resolve();data=args.data.resolve()
    if environment in (Path(sys.prefix).resolve(),(ROOT/'.venv').resolve()):raise ValueError('Use a separate venv outside the Lab core environment')
    data.mkdir(parents=True,exist_ok=True)
    if not environment.exists():venv.EnvBuilder(with_pip=True).create(environment)
    python=environment/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    lock=data/'requirements-swarm.local.lock';lock.write_text(requirements,encoding='utf-8')
    fingerprint={'requirementsSha256':hashlib.sha256(requirements.encode()).hexdigest(),'wheels':{i['id']:i['wheelInfo']['sha256'] for i in manifest['components'] if i['delivery']=='build-wheel'}}
    marker=data/'local-runtime-install.json'
    if not marker.exists() or json.loads(marker.read_text('utf-8'))!=fingerprint:
        ensure_pip(python)
        run([python,'-m','pip','install','--disable-pip-version-check','--no-deps','-r',lock],cwd=ROOT)
        # Same-version wheels must replace an older VCS install or an older frontend build.
        run([python,'-m','pip','install','--disable-pip-version-check','--no-deps','--no-index','--force-reinstall',*wheels],cwd=ROOT)
        initialize_workspace(python,data)
        marker.write_text(json.dumps(fingerprint,indent=2)+'\n',encoding='utf-8')
    else:initialize_workspace(python,data)
    print('Pinned local Jiuwen runtime and original web UI installed. Existing model, account and skill settings are retained.')


if __name__=='__main__':main()
