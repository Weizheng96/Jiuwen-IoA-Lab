"""Bundled, deterministic installer and resident IPC entry; no protocol database access."""
from contextlib import contextmanager
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import urlsplit
import venv


@contextmanager
def install_lock(path,timeout=180):
    with path.open('a+b') as stream:
        if not stream.tell():stream.write(b'0');stream.flush()
        until=time.monotonic()+timeout
        while True:
            try:
                stream.seek(0)
                if os.name=='nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic()>=until:raise RuntimeError('Another local installation still owns this release; retry after it finishes')
                time.sleep(.2)
        try:yield
        finally:
            stream.seek(0)
            if os.name=='nt':msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(stream.fileno(),fcntl.LOCK_UN)


def ensure_runtime(assets=None,cache=None):
    assets=Path(assets or Path(__file__).resolve().parents[1]/'assets').resolve()
    manifest_path=assets/'runtime.json'
    if not manifest_path.is_file():raise RuntimeError('Download the complete platform skill ZIP; a source folder is not an installable release')
    raw=manifest_path.read_bytes();manifest=json.loads(raw);fingerprint=hashlib.sha256(raw).hexdigest()
    lockfile=assets/'requirements.lock'
    if not lockfile.is_file() or hashlib.sha256(lockfile.read_bytes()).hexdigest()!=manifest['constraintsSha256']:
        raise RuntimeError('Dependency lock checksum mismatch')
    wheels=[];seen=set()
    for item in manifest['wheels']:
        filename=item['file'];path=assets/'wheels'/filename
        if not isinstance(filename,str) or Path(filename).name!=filename or filename in seen or not filename.endswith('.whl') or path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']:
            raise RuntimeError('Bundled wheel checksum or path mismatch')
        seen.add(filename);wheels.append(str(path))
    cache=Path(cache or os.environ.get('IOALAB_ACCESS_CACHE') or Path.home()/'.ioalab/access-runtimes').expanduser().resolve()
    cache.mkdir(parents=True,exist_ok=True)
    envdir=cache/fingerprint[:24];python=envdir/('Scripts/python.exe' if os.name=='nt' else 'bin/python');ready=envdir/'.ready'
    marker=envdir/'ioalab-release.json'
    identity={'format':'ioalab.access-installation/1','releaseId':fingerprint,'versions':manifest['versions']}
    def installed():
        if not ready.exists():return False
        try:
            valid=(not ready.is_symlink() and not marker.is_symlink() and python.is_file() and ready.stat().st_size<=128
                   and marker.stat().st_size<=32768 and ready.read_text(encoding='utf-8')==fingerprint
                   and json.loads(marker.read_text(encoding='utf-8'))==identity)
        except (OSError,ValueError):valid=False
        if not valid:raise RuntimeError('Completed access environment identity changed; preserve running data and inspect this cache, do not repair it in place')
        verified=invoke(python,['-c','from ioalab_sdk.runtime.installation import current; value=current(); assert value["releaseId"]=='+repr(fingerprint)+' and value["environment"]=='+repr(str(envdir))])
        if verified.returncode:raise RuntimeError('Installed access packages do not match this completed release; preserve it and inspect the cache')
        return True
    if installed():return python
    with install_lock(cache/(fingerprint[:24]+'.install.lock')):
        if installed():return python
        index=os.environ.get('IOALAB_PYPI_INDEX','https://pypi.tuna.tsinghua.edu.cn/simple')
        url=urlsplit(index)
        if url.scheme!='https' or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise RuntimeError('Use an HTTPS package mirror without embedded credentials')
        venv.EnvBuilder(with_pip=True,system_site_packages=False).create(envdir)
        env={key:value for key,value in os.environ.items() if not key.startswith(('PIP_','PYTHONPATH','PYTHONHOME'))}
        env.update(PIP_CONFIG_FILE=os.devnull,PIP_INDEX_URL=index,PYTHONUTF8='1',PYTHONNOUSERSITE='1')
        checks='import importlib.metadata as m; from ioalab_sdk.runtime.resident import ResidentRuntime; from ioalab_jiuwen.process import ProcessHost; expected='+repr(manifest['versions'])+'; assert all(m.version(name)==version for name,version in expected.items())'
        log=envdir/'install.log'
        with log.open('w',encoding='utf-8') as stream:
            try:
                subprocess.run([str(python),'-I','-m','pip','install','--disable-pip-version-check','--no-input','--constraint',str(lockfile),*wheels],env=env,check=True,stdout=stream,stderr=subprocess.STDOUT)
                subprocess.run([str(python),'-I','-c',checks],env=env,check=True,stdout=stream,stderr=subprocess.STDOUT)
                subprocess.run([str(python),'-I','-m','pip','check'],env=env,check=True,stdout=stream,stderr=subprocess.STDOUT)
            except subprocess.CalledProcessError:raise RuntimeError('Installation did not finish; inspect the local log: '+str(log)) from None
        staged=envdir/'ioalab-release.tmp';staged.write_text(json.dumps(identity,sort_keys=True)+'\n',encoding='utf-8');staged.replace(marker)
        staged=envdir/'.ready.tmp';staged.write_text(fingerprint,encoding='utf-8');staged.replace(ready)
    return python


def invoke(python,arguments):
    env={key:value for key,value in os.environ.items() if key not in ('PYTHONPATH','PYTHONHOME','PYTHONUSERBASE')}
    return subprocess.run([str(python),'-I','-X','utf8',*arguments],env={**env,'PYTHONUTF8':'1','PYTHONNOUSERSITE':'1'},capture_output=True,text=True,encoding='utf-8')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation')
    parser.add_argument('--config',type=Path)
    parser.add_argument('--args-file',type=Path)
    args=parser.parse_args(argv)
    root=Path(__file__).resolve().parents[1]
    try:
        role=json.loads((root/'assets/skill.json').read_text(encoding='utf-8'))['role']
        prefixes=('runtime.','identity.','confirmations.','operations.','assistant.' if role=='assistant' else 'provider.')
        if role not in ('assistant','service') or not args.operation.startswith(prefixes):raise RuntimeError('Choose an operation belonging to this product role')
        python=ensure_runtime()
        if args.operation=='runtime.install':
            print(json.dumps({'status':'completed','data':{'python':str(python),'sharedEnvironment':True},'error':None}));return 0
        config=(args.config or root/'settings.local.json').expanduser().resolve()
        if args.operation=='runtime.web-extension':
            if not args.args_file:raise RuntimeError('Use an args-file containing the owned profile key')
            payload=json.loads(args.args_file.read_text(encoding='utf-8'))
            values=json.loads(config.read_text(encoding='utf-8'))
            if (not isinstance(payload,dict) or set(payload)!={'profile'} or
                not any(item.get('key')==payload['profile'] and item.get('role')==role for item in values.get('identities',[]))):
                raise RuntimeError('Select a configured profile belonging to this product role')
            result=invoke(python,['-m','ioalab_jiuwen','web-extension','--config',str(config),'--profile',payload['profile']])
        elif args.operation in ('runtime.validate','runtime.start'):
            checked=invoke(python,['-m','ioalab_sdk.runtime','validate','--config',str(config)])
            if checked.returncode:
                print(checked.stdout.strip());return checked.returncode
            summary=json.loads(checked.stdout)['data']
            if not any(item['role']==role for item in summary['identities']):raise RuntimeError('Add this role to the existing shared configuration before starting it')
            if args.operation=='runtime.validate':print(checked.stdout.strip());return 0
            result=invoke(python,['-m','ioalab_jiuwen','start','--config',str(config)])
        else:
            # Only the installer/SDK reads this file. Never print its credentials.
            values=json.loads(config.read_text(encoding='utf-8'));home=Path(values['data_dir']).expanduser()
            if not home.is_absolute():home=config.parent/home
            arguments=['-m','ioalab_sdk.runtime','call',args.operation,'--home',str(home.resolve())]
            if args.args_file:arguments+=['--args-file',str(args.args_file.expanduser().resolve())]
            result=invoke(python,arguments)
        if result.stderr:print(result.stderr,end='',file=sys.stderr)
        print(result.stdout.strip());return result.returncode
    except Exception as exc:
        message=str(exc) if isinstance(exc,RuntimeError) else 'Check the local bundle/configuration; '+type(exc).__name__
        print(json.dumps({'status':'rejected','data':None,'error':{'code':'not_ready','message':message}},ensure_ascii=False));return 1


if __name__=='__main__':raise SystemExit(main())
