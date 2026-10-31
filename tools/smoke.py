"""Source-tree integration check, outside the platform runtime package."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import httpx


def _port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));return sock.getsockname()[1]


def run_smoke():
    root=Path(__file__).resolve().parents[1]
    sys.path[:0]=[str(root/'src/access/sdk'),str(root/'official-services'),str(root/'src/official/services'),str(root/'experiments')]
    from ioalab_services.catalog import definitions
    from ioalab_sdk import LabClient
    from traffic import run_order
    ports=[_port(),_port()]
    base,service=[f'http://127.0.0.1:{p}' for p in ports]
    processes=[]; client=None
    with tempfile.TemporaryDirectory(prefix='ioalab-smoke-') as temp:
        temp=Path(temp)
        env={**os.environ,'PYTHONUTF8':'1','IOALAB_STUB':'1','IOALAB_MODE':'dev','IOALAB_ADMIN_PASSWORD':'smoke-password',
             'IOALAB_TRAFFIC':'off','IOALAB_PORT':str(ports[0]),'IOALAB_PUBLIC_URL':base,'IOALAB_AUTOSEED':'1','IOALAB_DATA':str(temp/'core'),
             'IOALAB_SEED_FILE':str(root/'deploy/default-seed.json'),
             'IOALAB_BASE_URL':base,'IOALAB_PROVIDER_PORT':str(ports[1]),'IOALAB_PROVIDER_PUBLIC_URL':service,'IOALAB_PROVIDER_DATA':str(temp/'providers')}
        logs=[]
        try:
            commands=[[sys.executable,'-m','ioalab','serve'],[sys.executable,str(root/'official-services/serve.py'),'--only','med-reminder,rehab-plan']]
            for i,command in enumerate(commands):
                log=(temp/f'process-{i}.log').open('w',encoding='utf-8');logs.append(log)
                proc=subprocess.Popen(command,cwd=root,env=env,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                processes.append(proc)
                deadline=time.monotonic()+45
                while time.monotonic()<deadline:
                    if proc.poll() is not None: raise RuntimeError(f'Participant process {i} exited')
                    try:
                        r=httpx.get(base+'/api/config' if i==0 else service+'/health',trust_env=False,timeout=1)
                        if r.status_code==200 and (i==0 or r.json()['ready']): break
                        if i==1 and r.status_code==200 and r.json().get('error'): raise RuntimeError(r.json()['error'])
                    except httpx.HTTPError: pass
                    time.sleep(.1)
                else: raise TimeoutError(f'Participant process {i} did not become ready')
            print('  ✓ Core and providers are separate processes',flush=True)
            client=LabClient(base,'ISC_001','001',temp/'client',source='experiment')
            ds=definitions()
            cards=[]
            for formation in ('countersign','proposal'):
                d=next(d for d in ds if d.get('formation','preapproval')==formation)
                result=run_order(client,d,service+'/a2a/providers/svc-'+d['id'])
                if result['status']!='accepted': raise RuntimeError(str(result))
                cards.append(result['cardDigest']);print('  ✓ Local acceptance: '+formation,flush=True)
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                views=[client.api('GET','/orders/'+cd) for cd in cards]
                if all(v['state']=='completed' for v in views): break
                time.sleep(.1)
            else: raise RuntimeError('Provider did not close all default-mode contracts')
            assert not client.outbox.all('pending')
            assert all(v['signatures']['7']==[True] for v in views)
            print('  ✓ Signed evidence, payment and market completion observed',flush=True)
            print('✓ Three-process smoke passed',flush=True)
            return 0
        except Exception as exc:
            print('✗ Smoke failed: '+type(exc).__name__+': '+str(exc),flush=True)
            for log in logs: log.flush()
            for path in temp.glob('process-*.log'):
                print(path.name+': '+path.read_text(encoding='utf-8',errors='replace')[-3000:])
            return 1
        finally:
            if client: client.close()
            for proc in reversed(processes):
                if proc.poll() is not None: continue
                if os.name=='nt':
                    subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],capture_output=True,
                                   creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                else: proc.terminate()
                try: proc.wait(timeout=10)
                except subprocess.TimeoutExpired: proc.kill();proc.wait()
            for log in logs: log.close()


if __name__ == '__main__':
    sys.exit(run_smoke())
