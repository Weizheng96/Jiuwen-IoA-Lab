"""One background dependency installation per immutable product release."""
from contextlib import contextmanager
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import urlsplit
import uuid


class Busy(RuntimeError):pass
class Uncertain(RuntimeError):pass


def present(path):return path.exists() or path.is_symlink()


def write_record(path,value):
    staged=path.with_name('.'+path.name+'-'+uuid.uuid4().hex)
    with staged.open('x',encoding='utf-8') as stream:
        if os.name!='nt':os.chmod(staged,0o600)
        json.dump(value,stream,sort_keys=True);stream.write('\n')
        stream.flush();os.fsync(stream.fileno())
    staged.replace(path)


def read_record(path):
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size>32768:raise ValueError()
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):raise Uncertain('Installation ownership is unavailable; preserve the original environment') from None


def interpreter_info(python,*,pip=False):
    code=('import json,sys'+(',pip' if pip else '')+';print(json.dumps({"implementation":sys.implementation.name,'
          '"version":list(sys.version_info[:2]),"prefix":sys.prefix}))')
    try:
        result=subprocess.run([str(python),'-I','-c',code],env=worker_environment(),capture_output=True,text=True,timeout=15)
        if result.returncode:raise ValueError()
        return json.loads(result.stdout)
    except (OSError,ValueError,subprocess.TimeoutExpired):
        raise RuntimeError('Python/venv is unavailable; use the absolute path to an installed CPython3.11 with venv') from None


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
        if (not isinstance(self.manifest,dict) or self.manifest.get('format')!=1
                or not isinstance(self.manifest.get('skillVersion'),str)
                or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+',self.manifest['skillVersion'])):
            raise RuntimeError('Download a product manifest with format 1 and a valid skillVersion')
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
        self.environment_owner=self.cache/(prefix+'.environment.json')
        self.identity={'format':'ioalab.access-installation/1','releaseId':self.id,'versions':self.manifest['versions']}

    def log(self,attempt):return self.cache/(self.id[:24]+'.'+attempt+'.install.log')

    def save(self,state):
        with lock(self.metadata_lock,timeout=3):
            write_record(self.task,state)

    def ownership(self,attempt):
        return {'format':'ioalab.access-environment/1','releaseId':self.id,
                'attemptId':attempt,'environment':str(self.envdir)}

    def failed_environment(self,state):
        """Under both installer locks, retain a known failed attempt before retry."""
        if self.state()!=state or state['phase']!='failed':raise Uncertain('Original failed attempt changed')
        attempt=state['attemptId'];base=self.id[:24]+'.'+attempt
        target=self.cache/(base+'.failed');receipt=self.cache/(base+'.attempt.json')
        owner_archive=self.cache/(base+'.environment.json');expected=self.ownership(attempt)
        if present(self.ready) or present(self.marker):raise Uncertain('A completed environment cannot be archived')
        if present(receipt):
            record=read_record(receipt)
            if (not isinstance(record,dict) or record.get('format')!='ioalab.failed-installation/1'
                    or record.get('originalTask')!=state or record.get('phase') not in ('moving','archived')
                    or record.get('environment') not in (None,str(target))
                    or record.get('ownership') not in (None,expected)):
                raise Uncertain('Original failed-attempt receipt changed')
        else:
            if present(target) or present(owner_archive):raise Uncertain('Unrecorded failed-attempt archive exists')
            owner=read_record(self.environment_owner) if present(self.environment_owner) else None
            if owner is not None and owner!=expected:raise Uncertain('Failed environment belongs to another attempt')
            if self.envdir.exists() and (not self.envdir.is_dir() or owner!=expected):
                raise Uncertain('Existing environment has no matching installer ownership')
            record={'format':'ioalab.failed-installation/1','phase':'moving','originalTask':state,
                    'ownership':owner,'environment':str(target) if self.envdir.exists() else None}
            write_record(receipt,record)
        if record['environment'] is not None:
            if self.envdir.exists() and not present(target):
                if record['phase']!='moving' or read_record(self.environment_owner)!=expected:
                    raise Uncertain('Original environment cannot be moved')
                self.envdir.rename(target)
            elif self.envdir.exists() or not target.is_dir() or target.is_symlink():
                raise Uncertain('Failed environment/archive location is ambiguous')
        elif self.envdir.exists() or present(target):raise Uncertain('Unexpected environment appeared during retention')
        if record['ownership'] is not None:
            if present(self.environment_owner) and not present(owner_archive):
                if read_record(self.environment_owner)!=expected:raise Uncertain('Original environment ownership changed')
                self.environment_owner.rename(owner_archive)
            elif present(self.environment_owner) or not present(owner_archive):
                raise Uncertain('Failed ownership archive is ambiguous')
            if read_record(owner_archive)!=expected:raise Uncertain('Archived ownership changed')
        elif present(self.environment_owner) or present(owner_archive):raise Uncertain('Unexpected ownership record appeared')
        write_record(receipt,{**record,'phase':'archived'})
        return {'attemptId':attempt,'logPath':state['logPath'],
                'environment':record['environment'],'receiptPath':str(receipt)}

    def state(self):
        if not present(self.task):return None
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
        if not present(self.ready):
            if not present(self.marker) or busy(self.install_lock):return False
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
        if state and state.get('retainedAttempt'):data['retainedAttempt']=state['retainedAttempt']
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
        if self.envdir.exists():
            try:
                if (not state or not self.envdir.is_dir()
                        or read_record(self.environment_owner)!=self.ownership(state['attemptId'])):
                    raise Uncertain('Existing environment has no matching installer ownership')
            except Uncertain as exc:return self.result('unknown',state,str(exc))
        if not state:return self.result('rejected')
        if state['phase']=='failed':return self.result('failed',state,state.get('message','Inspect the installation log'))
        return self.result('unknown',state,'No live installation owner or verified completion; preserve this attempt and inspect its log')

    def install(self,attempt):
        index=os.environ.get('IOALAB_PYPI_INDEX','https://pypi.tuna.tsinghua.edu.cn/simple');url=urlsplit(index)
        if url.scheme!='https' or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise RuntimeError('Use an HTTPS package mirror without embedded credentials')
        expected=list(sys.version_info[:2]);base=getattr(sys,'_base_executable',None)
        if sys.implementation.name!='cpython' or expected<[3,11] or not base:
            raise RuntimeError('Use an installed CPython3.11 or later, with venv')
        information=interpreter_info(base)
        if information.get('implementation')!='cpython' or information.get('version')!=expected:
            raise RuntimeError('Bootstrap base Python differs from the current interpreter; use the absolute CPython3.11 path')
        if present(self.envdir) or present(self.environment_owner):
            raise Uncertain('A new attempt requires an unoccupied, installer-owned destination')
        write_record(self.environment_owner,self.ownership(attempt))
        subprocess.run([base,'-I','-m','venv','--copies',str(self.envdir)],env=worker_environment(),check=True)
        created=interpreter_info(self.python,pip=True)
        if (created.get('implementation')!='cpython' or created.get('version')!=expected
                or Path(created.get('prefix','')).resolve()!=self.envdir):
            raise RuntimeError('Created environment Python/pip identity differs; preserve this failed attempt')
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
            retained=None
            if current['status']=='failed':
                try:
                    with lock(release.install_lock):retained=release.failed_environment(release.state())
                except Busy:return release.inspect()
                except Uncertain as exc:return release.result('unknown',release.state(),str(exc))
            attempt=str(uuid.uuid4());state={'releaseId':release.id,'attemptId':attempt,'phase':'launching','logPath':str(release.log(attempt))}
            if retained:state['retainedAttempt']=retained
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
    # Status readers briefly probe this same mutex; they are not installers.
    # Acquire ownership before judging the original attempt, not during a probe.
    with lock(release.install_lock,timeout=3):
        state=release.state()
        if not state or state['attemptId']!=attempt or state['phase']!='launching':raise RuntimeError('Original installation launch changed')
        state={**state,'phase':'installing','pid':os.getpid()};release.save(state)
        try:
            release.install(attempt)
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
