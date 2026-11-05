"""PID-1 supervisor inside one ephemeral seat, never a host command service.

The reviewed image owns /opt/trial/plan.json. Its fixed setup and health commands
must use public SDK/Swarm interfaces, not edit private business databases.
"""
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
from pathlib import Path
import signal
import subprocess
import threading
import time

import httpx


def private_record(path,value):
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False)+'\n');temporary.chmod(0o600);temporary.replace(path)


def redacted(line,secrets):
    for value in secrets:
        if value:line=line.replace(value,'<redacted>')
    line=re.sub(r'''(?i)(["']?(?:api[_-]?key|password|authorization|token|secret)["']?\s*[:=]\s*)(["'])(.*?)\2''',r'\1"<redacted>"',line)
    line=re.sub(r'(?i)(Bearer\s+)[^\s"\',}]+',r'\1<redacted>',line)
    return re.sub(r"(?i)(api[_-]?key|password|authorization|token|secret)([\"']?\s*[:=]\s*[\"']?)([^\s,\"'}]+)",r"\1\2<redacted>",line)


class SeatProcesses:
    def __init__(self,control,secrets):
        from .processes import require_pid1
        require_pid1()
        self.children={};self.identities={};self.readers=[];self.control=control;self.secrets=secrets
        self.logs=control/'logs';self.logs.mkdir(mode=0o700)
        self.record={'phase':'starting','roles':{}}
        self.save()

    def save(self):private_record(self.control/'lifecycle.json',self.record)

    def launch(self,name,command,*,env,cwd,user=None):
        from .processes import identity
        options={} if user is None else {'user':user,'group':user,'extra_groups':[]}
        process=subprocess.Popen(command,env=env,cwd=cwd,start_new_session=True,
            stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,**options)
        self.children[name]=process;self.identities[name]=identity(process.pid)
        def read():
            path=self.logs/(name+'.log')
            with path.open('w',encoding='utf-8') as output:
                path.chmod(0o600)
                for line in iter(process.stdout.readline,b''):
                    output.write(redacted(line.decode('utf-8','replace'),self.secrets));output.flush()
            process.stdout.close()
        reader=threading.Thread(target=read,name='trial-log-'+name,daemon=False)
        reader.start();self.readers.append(reader)
        self.record['roles'][name]={'pid':process.pid,'uid':user if user is not None else 0};self.save()
        return process

    def note_exits(self):
        changed=False;observed=[];timestamp=time.time()
        for name,child in self.children.items():
            code=child.poll()
            if code is not None:
                if self.record['roles'][name].get('exitCode')!=code:
                    self.record['roles'][name]['exitCode']=code;changed=True
                if name not in ('setup','core-ready'):
                    observed.append({'role':name,'exitCode':code,'observedAt':timestamp})
        if observed and self.record['phase'] in ('starting','running') and 'firstObservedExits' not in self.record:
            self.record['firstObservedExits']=observed;changed=True
        if changed:self.save()

    def signal(self,name):
        from .processes import identity
        child=self.children.get(name)
        if child is None or child.poll() is not None:return
        current=identity(child.pid);original=self.identities[name]
        if current is None:return
        if original is None or any(current[key]!=original[key] for key in ('pid','uid','start')):
            raise RuntimeError('The original supervised process changed before TERM')
        child.terminate()

    def wait(self,name,timeout=90):
        child=self.children.get(name)
        if child is not None:child.wait(timeout=timeout)
        self.note_exits()

    def shutdown(self):
        from .processes import terminate_owned,descendants
        self.record['phase']='stopping';self.save()
        for name in ('setup','core-ready'):
            self.signal(name);self.wait(name)
        for name in ('assistant','services'):self.signal(name)
        for name in ('assistant','services'):self.wait(name)
        terminate_owned({10002,10004})
        self.signal('native');self.wait('native')
        terminate_owned({10003})
        self.signal('core');self.wait('core')
        terminate_owned({10001})
        if any(row['state']!='Z' for row in descendants()):raise RuntimeError('Unreleased seat processes remain')
        for reader in self.readers:reader.join(timeout=5)
        if any(reader.is_alive() for reader in self.readers):raise RuntimeError('Private process logs have not released')
        self.record['phase']='stopped';self.save()


def shutdown_preserving(owner,retry):
    """A failed normal stop retains PID1 and the original writers for inspection."""
    while True:
        retry.clear()
        try:owner.shutdown();return
        except Exception as error:
            owner.record.update(phase='stop_unverified',shutdownError={'type':type(error).__name__})
            try:owner.save()
            except OSError:pass  # A full diagnostic volume must not terminate PID1.
            while not retry.wait(1):
                try:owner.note_exits()
                except OSError:pass


def start_roles(owner,plan,root,environment,stop,startup_deadline,sdk_key):
    for name, uid in (('core',10001),('native',10003),('assistant',10002),('services',10004)):
        env = {**environment,'HOME':str(root/name),'TMPDIR':str(root/name/'tmp')}
        (root/name/'tmp').mkdir(exist_ok=True)
        os.chown(root/name/'tmp',uid,uid)
        if name == 'assistant':
            env['IOALAB_TRIAL_SDK_KEY'] = sdk_key
        owner.launch(name,plan['processes'][name],env=env,cwd=root/name,user=uid)
        if name=='core':
            probe=owner.launch('core-ready',['/opt/core/bin/python','-I','-m','ioalab.readiness',
                '--url','http://127.0.0.1:8750','--expected-public-url','http://127.0.0.1:8750',
                '--timeout',str(max(1,startup_deadline-time.monotonic()-5)), '--request-timeout','3'],env=env,cwd=root/name,user=uid)
            while probe.poll() is None and not stop.wait(.1):
                if time.monotonic()>=startup_deadline:raise RuntimeError('Trial core readiness exceeded the startup deadline')
            if stop.is_set():raise RuntimeError('Trial core startup was stopped')
            owner.wait('core-ready',timeout=5)
            if probe.returncode:raise RuntimeError('The original trial core is not publicly ready')


def main():
    epoch = os.environ.pop('IOALAB_TRIAL_EPOCH')
    key = os.environ.pop('IOALAB_TRIAL_CONTROL_KEY')
    sdk_key = hmac.new(key.encode(),b'sdk-confirmations',hashlib.sha256).hexdigest()
    model_key = hmac.new(key.encode(),b'model-bridge',hashlib.sha256).hexdigest()
    plan = json.loads(Path('/opt/trial/plan.json').read_text())
    if set(plan) != {'setup','health','processes'} or set(plan['processes']) != {'core','native','assistant','services'}:
        raise ValueError('A reviewed four-component trial plan is required')
    root = Path('/epoch')
    control = root/'control'
    control.mkdir(mode=0o755)
    for leaf in ('root','tmp'):
        (control/leaf).mkdir(mode=0o700)
    for name, uid in (('core',10001),('assistant',10002),('native',10003),('services',10004)):
        home = root/name
        home.mkdir(mode=0o700)
        os.chown(home,uid,uid)
    environment = {name:value for name,value in os.environ.items()
                   if name in ('PATH','LANG','SSL_CERT_FILE','SSL_CERT_DIR','PYTHONUTF8')}
    environment.update(PYTHONUTF8='1',IOALAB_TRIAL_EPOCH=epoch,
                       HOME=str(control/'root'),TMPDIR=str(control/'tmp'))
    model = os.environ.pop('IOALAB_TRIAL_MODEL','')
    secret_values=[key,sdk_key,model_key]
    try:secret_values.append(json.loads(model).get('api_key',''))
    except ValueError:pass
    owner=SeatProcesses(control,secret_values);children=owner.children
    from .processes import STARTUP_TIMEOUT
    startup_deadline=time.monotonic()+STARTUP_TIMEOUT
    stop=threading.Event();retry=threading.Event()
    def request_stop(*_):stop.set();retry.set()
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,request_stop)
    try:
        setup=owner.launch('setup',plan['setup'],env={**environment,'IOALAB_TRIAL_MODEL':model},cwd=root)
        deadline=min(startup_deadline,time.monotonic()+180)
        while setup.poll() is None and not stop.wait(.1):
            if time.monotonic()>=deadline:raise RuntimeError('Trial setup has not completed; preserve private logs')
        if stop.is_set():raise RuntimeError('Trial setup was stopped')
        if setup.returncode:raise RuntimeError('Trial setup failed; inspect its private role log')
    except BaseException:
        shutdown_preserving(owner,retry);raise
    try:
        bridge = root/'native/model-bridge.json'
        bridge.write_text(json.dumps({'key':model_key}))
        bridge.chmod(0o600);os.chown(bridge,10003,10003)
        start_roles(owner,plan,root,environment,stop,startup_deadline,sdk_key)
    except BaseException as error:
        owner.record['startupFailure']={'errorType':type(error).__name__}
        owner.note_exits();owner.save()
        shutdown_preserving(owner,retry);raise

    def revoke():
        (control/'revoked').touch(mode=0o644,exist_ok=True)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):
            pass

        def do_POST(self):
            self.connection.settimeout(10)
            status = 200
            try:
                expected = model_key if self.path == '/model' else key
                if not hmac.compare_digest(self.headers.get('Authorization',''), 'Bearer '+expected):
                    raise ValueError()
                if self.headers.get('Origin') or self.headers.get('Transfer-Encoding'):
                    raise ValueError()
                size = int(self.headers.get('Content-Length','0'))
                if not 0 < size <= 128*1024:
                    raise ValueError()
                body = json.loads(self.rfile.read(size))
                if self.path == '/model' and not (control/'revoked').exists():
                    from .model_bridge import forward
                    result = forward(body)
                elif self.path == '/revoke' and body == {}:
                    revoke()
                    result = {'revoked':True}
                elif self.path in ('/health','/runtime-health') and body == {} and not (control/'revoked').exists():
                    if stop.is_set() or any(child.poll() is not None for name,child in children.items() if name not in ('setup','core-ready')):
                        raise ValueError()
                    observed = subprocess.run([*plan['health'],*(['--runtime'] if self.path=='/runtime-health' else [])],env=environment,cwd=root,
                        check=True,capture_output=True,text=True,timeout=20)
                    result = {**json.loads(observed.stdout),'epoch':epoch}
                elif self.path == '/confirmations' and not (control/'revoked').exists():
                    if body.get('generation') != epoch:
                        raise ValueError()
                    response = httpx.post('http://127.0.0.1:8768/confirmations',json=body,
                        headers={'Authorization':'Bearer '+sdk_key},timeout=5,trust_env=False)
                    if response.status_code not in (200,400,403,409):
                        response.raise_for_status()
                    status = response.status_code
                    result = response.json()
                else:
                    raise ValueError()
                raw = json.dumps(result).encode()
                self.send_response(status)
            except Exception:
                raw = b'{"error":"sandbox_not_ready"}'
                self.send_response(503)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server=None
    try:
        server=ThreadingHTTPServer(('0.0.0.0',8767),Handler)
        server.daemon_threads=False
        threading.Thread(target=server.serve_forever,daemon=True).start()
        owner.record['phase']='running';owner.save()
        while not stop.wait(.2):
            owner.note_exits()
            if any(child.poll() is not None for name,child in children.items() if name not in ('setup','core-ready')):
                stop.set()
    finally:
        revoke()
        if server is not None:
            server.shutdown();server.server_close()
        shutdown_preserving(owner,retry)
    if owner.record.get('firstObservedExits'):
        raise RuntimeError('An owned role exited unexpectedly; inspect private lifecycle and role logs')


if __name__ == '__main__':
    main()
