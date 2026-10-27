"""Install the pinned runtime and original web UI outside the Lab core environment."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import venv

HERE=Path(__file__).resolve().parent
def run(argv,**kwargs): subprocess.run([str(a) for a in argv],check=True,**kwargs)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--venv',required=True);ap.add_argument('--data',required=True);ap.add_argument('--source',help='Optional existing clean source at the pinned commit');a=ap.parse_args()
    assert sys.version_info[:2]==(3,11),'Use Python 3.11 for the validated lock'
    spec=json.loads((HERE/'swarm.lock.json').read_text());lock=HERE/'requirements-swarm.lock'
    assert hashlib.sha256(lock.read_bytes()).hexdigest()==spec['requirementsSha256'],'Lock digest mismatch'
    environment=Path(a.venv).resolve();data=Path(a.data).resolve();data.mkdir(parents=True,exist_ok=True)
    if environment==Path(sys.prefix).resolve(): raise ValueError('Use a separate venv; do not install into Lab core')
    if not environment.exists(): venv.EnvBuilder(with_pip=True).create(environment)
    python=environment/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    source=Path(a.source).resolve() if a.source else data/'build-source'
    archive_env={**os.environ,'GIT_LFS_SKIP_SMUDGE':'1'}
    if not source.exists(): run(['git','clone','--no-checkout',spec['repository'],source],env=archive_env);run(['git','-C',source,'checkout','--detach',spec['commit']],env=archive_env)
    archive=subprocess.check_output(['git','-c','safe.directory='+source.as_posix(),'-C',str(source),'archive','--format=tar',spec['commit']],env=archive_env)
    if hashlib.sha256(archive).hexdigest()!=spec['sourceArchiveSha256']: raise ValueError('Source digest mismatch')
    # Build only a fresh archive copy: never write to a caller-supplied reference checkout.
    import tarfile,io
    build=data/'web-build'
    if build.exists(): raise ValueError('Build directory exists; choose a fresh data directory or retain its installed runtime')
    build.mkdir();
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        for member in tar.getmembers():
            target=(build/member.name).resolve()
            if not target.is_relative_to(build) or member.issym() or member.islnk(): raise ValueError('Unsafe source archive entry')
        tar.extractall(build)
    resolved=data/'requirements-swarm.local.lock'
    lines=lock.read_text().splitlines();lines=[('workswarm @ '+build.as_uri()) if l.startswith('workswarm @ ') else l for l in lines]
    resolved.write_text('\n'.join(lines)+'\n')
    run([python,'-m','pip','install','--no-deps','-r',resolved])
    npm=shutil.which('npm.cmd' if os.name=='nt' else 'npm')
    if not npm: raise RuntimeError('Node/npm is required to build the pinned original Jiuwen Web UI')
    frontend=build/'jiuwenswarm/channels/web/frontend'
    run([npm,'ci','--no-audit','--no-fund'],cwd=frontend);run([npm,'run','build'],cwd=frontend)
    package=Path(subprocess.check_output([str(python),'-c','import jiuwenswarm; print(jiuwenswarm.__path__[0])'],text=True).strip())
    shutil.copytree(frontend/'dist',package/'channels/web/frontend/dist',dirs_exist_ok=True)
    env={**os.environ,'JIUWENSWARM_DATA_DIR':str(data)}
    run([python,'-c',"from pathlib import Path; import os; from jiuwenswarm.common.utils import prepare_workspace; prepare_workspace(False,preferred_language='zh',workspace_dir=Path(os.environ['JIUWENSWARM_DATA_DIR']))"],env=env)
    print('Pinned Jiuwen installed. Configure its config/.env, EXTENSION_DIRS, IOALAB_SWARM_USER_ID and shared bridge secret before starting.')

if __name__=='__main__':main()
