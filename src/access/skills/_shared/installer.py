"""One background dependency installation per immutable product release."""
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
import uuid
import venv


class Busy(RuntimeError):pass
class Uncertain(RuntimeError):pass


@contextmanager
def lock(path,timeout=0):
    path=Path(path)
    with path.open('a+b') as stream:
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
                if time.monotonic()>=until:raise Busy('Another installation owns this release') from None
                time.sleep(.02)
        try:
            if not path.stat().st_size:stream.write(b'0');stream.flush()
            yield
        finally:
            stream.seek(0)
            if os.name=='nt':msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(stream.fileno(),fcntl.LOCK_UN)


def busy(path):
    try:
        with lock(path):return False
    except Busy:return True


def worker_environment():
    names={'path','pathext','systemroot','windir','comspec','systemdrive','temp','tmp','home','userprofile',
           'appdata','localappdata','programfiles','programfiles(x86)','programdata','lang','lc_all','lc_ctype',
           'os','processor_architecture','processor_architew6432','processor_identifier','number_of_processors'}
    values={key:value for key,value in os.environ.items() if key.lower() in names}
    values.update(PYTHONUTF8='1',PYTHONNOUSERSITE='1')
    if 'IOALAB_PYPI_INDEX' in os.environ:values['IOALAB_PYPI_INDEX']=os.environ['IOALAB_PYPI_INDEX']
    return values


class Release:
    def __init__(self,assets,cache=None):
        self.assets=Path(assets).resolve();manifest=self.assets/'runtime.json'
        if not manifest.is_file():raise RuntimeError('Download the complete platform skill ZIP')
        raw=manifest.read_bytes();self.manifest=json.loads(raw);self.id=hashlib.sha256(raw).hexdigest()
        self.constraints=self.assets/'requirements.lock'
        if not self.constraints.is_file() or hashlib.sha256(self.constraints.read_bytes()).hexdigest()!=self.manifest['constraintsSha256']:
            raise RuntimeError('Dependency lock checksum mismatch')
        self.wheels=[];seen=set()
        for item in self.manifest['wheels']:
            name=item['file'];path=self.assets/'wheels'/name
            if (not isinstance(name,str) or Path(name).name!=name or name in seen or not name.endswith('.whl')
                    or path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']):
                raise RuntimeError('Bundled wheel checksum or path mismatch')
            seen.add(name);self.wheels.append(str(path))
        self.cache=Path(cache or os.environ.get('IOALAB_ACCESS_CACHE') or Path.home()/'.ioalab/access-runtimes').expanduser().resolve()
        self.cache.mkdir(mode=0o700,parents=True,exist_ok=True)
        prefix=self.id[:24];self.envdir=self.cache/prefix
        if self.envdir.is_symlink():raise RuntimeError('Do not replace or repair a linked access environment')
        self.python=self.envdir/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
        self.ready=self.envdir/'.ready';self.marker=self.envdir/'ioalab-release.json'
        self.launch_lock=self.cache/(prefix+'.launch.lock');self.install_lock=self.cache/(prefix+'.install.lock')
        self.metadata_lock=self.cache/(prefix+'.metadata.lock')
        self.task=self.cache/(prefix+'.install.json')
        self.identity={'format':'ioalab.access-installation/1','releaseId':self.id,'versions':self.manifest['versions']}

    def log(self,attempt):return self.cache/(self.id[:24]+'.'+attempt+'.install.log')

    def save(self,state):
        with lock(self.metadata_lock,timeout=3):
            staged=self.task.with_suffix('.tmp')
            staged.write_text(json.dumps(state,sort_keys=True)+'\n',encoding='utf-8')
            if os.name!='nt':os.chmod(staged,0o600)
            staged.replace(self.task)

    def state(self):
        if not self.task.exists():return None
        try:
            with lock(self.metadata_lock,timeout=3):
                if self.task.is_symlink() or self.task.stat().st_size>32768:raise ValueError()
                value=json.loads(self.task.read_text(encoding='utf-8'))
                if (not isinstance(value,dict) or value.get('releaseId')!=self.id
                        or value.get('phase') not in ('launching','installing','failed','completed')
                        or str(uuid.UUID(value['attemptId']))!=value['attemptId']
                        or value.get('logPath')!=str(self.log(value['attemptId']))):raise ValueError()
                return value
        except (Busy,OSError,ValueError,KeyError,TypeError,AttributeError) as exc:
            raise Uncertain('Installation metadata changed ('+type(exc).__name__+'); preserve this cache and inspect its logs') from None

    def installed(self):
        if not self.ready.exists():
            if not self.marker.exists() or busy(self.install_lock):return False
        try:
            valid=(not self.ready.is_symlink() and not self.marker.is_symlink() and self.python.is_file()
                   and self.ready.stat().st_size<=128 and self.marker.stat().st_size<=32768
                   and self.ready.read_text(encoding='utf-8')==self.id
                   and json.loads(self.marker.read_text(encoding='utf-8'))==self.identity)
        except (OSError,ValueError):valid=False
        if not valid:raise RuntimeError('Completed access environment identity changed; do not repair it in place')
        checks='from ioalab_sdk.runtime.installation import current; v=current(); assert v["releaseId"]=='+repr(self.id)+' and v["environment"]=='+repr(str(self.envdir))
        result=subprocess.run([str(self.python),'-I','-c',checks],env=worker_environment(),capture_output=True)
        if result.returncode:raise RuntimeError('Installed packages changed; preserve this completed release and inspect its cache')
        return True

    def result(self,status,state=None,message=None):
        data={'stage':'installation','releaseId':self.id,'requestSubmitted':False}
        if state:data.update({key:state[key] for key in ('attemptId','logPath')})
        error=None
        if status=='completed':data.update(python=str(self.python),sharedEnvironment=True)
        elif status=='waiting':data['nextAction']={'operation':'runtime.install.status','purpose':'inspect_installation'}
        else:
            code='unknown_result' if status=='unknown' else 'installation_failed' if status=='failed' else 'not_ready'
            error={'code':code,'message':message or 'Prepare this release before using its SDK',
                   'recovery':'inspect_installation' if status=='unknown' else 'runtime.install'}
        return {'status':status,'data':data,'error':error}

    def inspect(self,*,include_launch=True):
        if self.installed():return self.result('completed')
        try:state=self.state()
        except Uncertain as exc:return self.result('unknown',message=str(exc))
        if busy(self.install_lock) or include_launch and busy(self.launch_lock):return self.result('waiting',state)
        if not state:return self.result('rejected')
        if state['phase']=='failed':return self.result('failed',state,state.get('message','Inspect the installation log'))
        return self.result('unknown',state,'No live installation owner or verified completion; preserve this attempt and inspect its log')

    def install(self):
        index=os.environ.get('IOALAB_PYPI_INDEX','https://pypi.tuna.tsinghua.edu.cn/simple');url=urlsplit(index)
        if url.scheme!='https' or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise RuntimeError('Use an HTTPS package mirror without embedded credentials')
        venv.EnvBuilder(with_pip=True,system_site_packages=False).create(self.envdir)
        env=worker_environment();env.update(PIP_CONFIG_FILE=os.devnull,PIP_INDEX_URL=index)
        checks='import importlib.metadata as m; from ioalab_sdk.runtime.resident import ResidentRuntime; from ioalab_jiuwen.process import ProcessHost; expected='+repr(self.manifest['versions'])+'; assert all(m.version(k)==v for k,v in expected.items())'
        commands=[['-m','pip','install','--disable-pip-version-check','--no-input','--constraint',str(self.constraints),*self.wheels],
                  ['-c',checks],['-m','pip','check']]
        try:
            for command in commands:subprocess.run([str(self.python),'-I',*command],env=env,check=True)
        except subprocess.CalledProcessError:raise RuntimeError('Dependency installation failed; inspect this attempt log') from None
        staged=self.envdir/'ioalab-release.tmp';staged.write_text(json.dumps(self.identity,sort_keys=True)+'\n',encoding='utf-8');staged.replace(self.marker)
        staged=self.envdir/'.ready.tmp';staged.write_text(self.id,encoding='utf-8');staged.replace(self.ready)


def prepare(assets,cache=None,*,launch=False,retry=False):
    release=Release(assets,cache)
    if release.installed():return release.result('completed')
    if not launch:return release.inspect()
    try:
        with lock(release.launch_lock,timeout=3):
            current=release.inspect(include_launch=False)
            if current['status'] in ('completed','waiting','unknown') or current['status']=='failed' and not retry:return current
            attempt=str(uuid.uuid4());state={'releaseId':release.id,'attemptId':attempt,'phase':'launching','logPath':str(release.log(attempt))}
            release.save(state)
            try:
                with release.log(attempt).open('ab') as log:
                    child=subprocess.Popen([sys.executable,'-I','-X','utf8',str(Path(__file__).resolve()),'--worker','--assets',str(release.assets),
                        '--cache',str(release.cache),'--attempt',attempt],stdin=subprocess.DEVNULL,stdout=log,stderr=log,
                        env=worker_environment(),creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),start_new_session=os.name!='nt')
            except OSError as exc:
                release.save({**state,'phase':'failed','message':'Cannot start the installation worker: '+type(exc).__name__})
                return release.inspect(include_launch=False)
            until=time.monotonic()+3
            while time.monotonic()<until:
                if busy(release.install_lock):return release.result('waiting',state)
                if child.poll() is not None:return release.inspect(include_launch=False)
                time.sleep(.02)
            # This invocation still holds an actual child handle, not just a PID file.
            return release.result('waiting',state)
    except Busy:return release.inspect()


def work(assets,cache,attempt):
    release=Release(assets,cache)
    with lock(release.install_lock):
        state=release.state()
        if not state or state['attemptId']!=attempt or state['phase']!='launching':raise RuntimeError('Original installation launch changed')
        state={**state,'phase':'installing','pid':os.getpid()};release.save(state)
        try:
            release.install()
            if not release.installed():raise RuntimeError('Installation has no verified completion marker')
        except Exception as exc:
            message=str(exc) if isinstance(exc,RuntimeError) else 'Inspect the local installation log: '+type(exc).__name__
            release.save({**state,'phase':'failed','message':message});return 1
        release.save({**state,'phase':'completed'});return 0


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker',action='store_true',required=True);parser.add_argument('--assets',type=Path,required=True)
    parser.add_argument('--cache',type=Path,required=True);parser.add_argument('--attempt',required=True)
    options=parser.parse_args();raise SystemExit(work(options.assets,options.cache,options.attempt))
