"""Three real processes: platform and two resident participants over HTTP/WS/IPC.

This deterministic lifecycle probe does not approve contracts or execute production.
The resident integration suite verifies those separate business boundaries.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import httpx
from ioalab_sdk.runtime.config import RuntimeConfig
from ioalab_sdk.runtime.files import private_json,read_json
from ioalab_sdk.runtime.ipc import LocalClient
from ioalab_sdk.runtime.lease import Lease
from ioalab_sdk.runtime.protocol import RuntimeFault


def _port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));return sock.getsockname()[1]


def _wait(predicate,process,seconds=30):
    until=time.monotonic()+seconds
    while time.monotonic()<until:
        if process.poll() is not None:raise RuntimeError('A smoke process exited before readiness')
        try:
            value=predicate()
            if value:return value
        except (httpx.HTTPError,RuntimeFault):pass
        time.sleep(.1)
    raise TimeoutError('Smoke process did not become ready')


def _parent_pid(pid):
    if os.name!='nt':return None
    if type(pid) is not int or pid<=0:raise ValueError('A positive runtime PID is required')
    command=f"(Get-CimInstance Win32_Process -Filter 'ProcessId = {pid}').ParentProcessId"
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',command],
                          capture_output=True,text=True,timeout=5,check=True,
                          creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    parent=result.stdout.strip()
    if not parent.isdecimal():raise RuntimeError('Cannot verify the Windows runtime parent PID')
    return int(parent)


def _assert_owner(process,state,descriptor):
    pid=state['pid']
    assert process.poll() is None, 'Launched participant exited before owner verification'
    assert type(pid) is int and pid>0, 'Runtime status has an invalid execution PID'
    for field in ('pid','bootId','runtimeId'):
        assert descriptor[field]==state[field], f'Authenticated runtime status and descriptor disagree on {field}'
    assert pid==process.pid or _parent_pid(pid)==process.pid, f'Runtime PID {pid} is not owned by launcher PID {process.pid}'


def _assert_lease_held(home):
    try:probe=Lease(home/'client.lock',timeout=0)
    except RuntimeError:return
    probe.close()
    raise AssertionError('Running smoke runtime does not own its OS lease')


def _stop_participant(rpc,state,child,home):
    stopped=rpc.call('runtime.stop',{'bootId':state['bootId']})
    assert stopped['status']=='completed', 'Idle smoke runtime did not acknowledge its original stop'
    assert child.wait(timeout=10)==0, 'Smoke runtime did not exit cleanly after stop'
    with Lease(home/'client.lock',timeout=0):pass


def _terminate(process):
    if process.poll() is not None:return
    if os.name=='nt':
        # Windows venv launchers own a separate Python child. Restrict cleanup
        # to this still-live Popen tree, never a process name or an observed PID.
        subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True,
                       timeout=10,check=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    else:process.terminate()
    try:process.wait(timeout=10)
    except subprocess.TimeoutExpired:process.kill();process.wait()


def run_smoke():
    root=Path(__file__).resolve().parents[1];port=_port();base=f'http://127.0.0.1:{port}'
    processes=[];logs=[];participants=[]
    with tempfile.TemporaryDirectory(prefix='ioalab-smoke-') as temporary:
        folder=Path(temporary)
        env={**os.environ,'PYTHONUTF8':'1','IOALAB_STUB':'1','IOALAB_MODE':'dev','IOALAB_ADMIN_PASSWORD':'smoke-password',
             'IOALAB_TRAFFIC':'off','IOALAB_PORT':str(port),'IOALAB_PUBLIC_URL':base,'IOALAB_AUTOSEED':'1',
             'IOALAB_DATA':str(folder/'core'),'IOALAB_SEED_FILE':str(root/'deploy/default-seed.json')}
        def launch(command):
            log=(folder/f'process-{len(processes)}.log').open('w',encoding='utf-8');logs.append(log)
            process=subprocess.Popen(command,cwd=root,env=env,stdout=log,stderr=log,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0));processes.append(process);return process
        code=0
        try:
            core=launch([sys.executable,'-m','ioalab','serve'])
            _wait(lambda:httpx.get(base+'/api/config',trust_env=False,timeout=1).raise_for_status(),core)
            for index in range(2):
                name=f'resident-smoke-{index}';password='isolated-smoke-password'
                httpx.post(base+'/api/auth/register',json={'username':name,'password':password},trust_env=False,timeout=10).raise_for_status()
                path=folder/f'participant-{index}.json'
                private_json(path,{'platform_url':base,'username':name,'password':password,'data_dir':f'./participant-{index}',
                    'identities':[{'key':'personal','role':'assistant','name':'Explicit smoke participant'}]})
                config=RuntimeConfig.load(path);rpc=LocalClient(config.home,timeout=1)
                child=launch([sys.executable,'-m','ioalab_sdk.runtime','serve','--config',str(path)])
                def ready():
                    result=rpc.call('runtime.status')
                    return result['data'] if result['status']=='completed' and result['data']['platformConnected'] else None
                state=_wait(ready,child)
                rpc=LocalClient(config.home,timeout=1,boot_id=state['bootId'])
                participants.append((rpc,state,child,config.home))
                _assert_owner(child,state,read_json(config.home/'runtime.json'))
                assert state['ipcReady'], f'participant-{index}: authenticated IPC is not ready'
                assert state['identities'][0]['bound'], f'participant-{index}: identity is not bound'
                assert not state['identities'][0]['executionReady'], f'participant-{index}: unconfigured execution is unexpectedly ready'
                _assert_lease_held(config.home)
                refreshed=rpc.call('account.refresh')
                assert refreshed['status']=='completed', f'participant-{index}: account refresh did not complete'
                assert rpc.call('identity.status')['data'][0]['agentId']==state['identities'][0]['agentId'], f'participant-{index}: identity changed after refresh'
            assert len({state['pid'] for _,state,_,_ in participants}|{core.pid,os.getpid()})==4, 'Participant, platform and smoke process IDs are not distinct'
            assert len({state['identities'][0]['agentId'] for _,state,_,_ in participants})==2, 'Participant identity IDs are not distinct'
        except Exception as exc:
            print('✗ Smoke failed: '+type(exc).__name__+': '+str(exc),flush=True)
            traceback.print_exc(file=sys.stdout)
            code=1
        finally:
            for rpc,state,child,home in reversed(participants):
                try:
                    _stop_participant(rpc,state,child,home)
                except Exception as exc:
                    print('✗ Smoke participant shutdown failed: '+type(exc).__name__+': '+str(exc),flush=True)
                    traceback.print_exc(file=sys.stdout);code=1
            for process in reversed(processes):
                try:_terminate(process)
                except Exception as exc:
                    print('✗ Smoke process cleanup failed: '+type(exc).__name__+': '+str(exc),flush=True)
                    traceback.print_exc(file=sys.stdout);code=1
            for log in logs:log.close()
        if code:
            for path in folder.glob('process-*.log'):print(path.name+': '+path.read_text('utf-8',errors='replace')[-3000:])
        else:
            print('✓ Three-process resident lifecycle passed: actual HTTP login/enrollment, WS connection, authenticated IPC, original stop and released OS leases; no business authorization or model execution',flush=True)
        return code


if __name__=='__main__':sys.exit(run_smoke())
