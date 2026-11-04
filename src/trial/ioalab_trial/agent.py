"""PID-1 supervisor inside one ephemeral seat, never a host command service.

The reviewed image owns /opt/trial/plan.json. Its fixed setup and health commands
must use public SDK/Swarm interfaces, not edit private business databases.
"""
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import subprocess
import threading
import time

import httpx


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
    subprocess.run(plan['setup'],env={**environment,'IOALAB_TRIAL_MODEL':model},cwd=root,check=True,timeout=180)
    bridge = root/'native/model-bridge.json'
    bridge.write_text(json.dumps({'key':model_key}))
    bridge.chmod(0o600);os.chown(bridge,10003,10003)
    children = {}
    stop = threading.Event()
    for name, uid in (('core',10001),('native',10003),('assistant',10002),('services',10004)):
        env = {**environment,'HOME':str(root/name),'TMPDIR':str(root/name/'tmp')}
        (root/name/'tmp').mkdir(exist_ok=True)
        os.chown(root/name/'tmp',uid,uid)
        if name == 'assistant':
            env['IOALAB_TRIAL_SDK_KEY'] = sdk_key
        children[name] = subprocess.Popen(plan['processes'][name],env=env,cwd=root/name,
            user=uid,group=uid,extra_groups=[],start_new_session=True,
            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)

    def revoke():
        (control/'revoked').touch(mode=0o644,exist_ok=True)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):
            pass

        def do_POST(self):
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
                elif self.path == '/health' and body == {} and not (control/'revoked').exists():
                    if any(child.poll() is not None for child in children.values()):
                        raise ValueError()
                    observed = subprocess.run(plan['health'],env=environment,cwd=root,
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

    server = ThreadingHTTPServer(('0.0.0.0',8767),Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever,daemon=True).start()
    for sig in (signal.SIGTERM,signal.SIGINT):
        signal.signal(sig,lambda *_:stop.set())
    try:
        while not stop.wait(.2):
            if any(child.poll() is not None for child in children.values()):
                stop.set()
    finally:
        revoke()
        server.shutdown()
        for child in children.values():
            try:
                os.killpg(child.pid,signal.SIGTERM)
            except ProcessLookupError:
                pass
        # Never leave a detached Worker after declaring success. PID1 remains
        # alive if descendants refuse to stop; the host then quarantines the seat.
        while True:
            for child in children.values():
                child.poll()
            try:
                while os.waitpid(-1,os.WNOHANG)[0]:
                    pass
            except ChildProcessError:
                break
            time.sleep(.2)
        server.server_close()


if __name__ == '__main__':
    main()
