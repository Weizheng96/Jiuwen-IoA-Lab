"""Release original SDK writers through authenticated RPC and actual OS ownership."""
import json
import signal
import threading
import time
import uuid

from ioalab_sdk.runtime import Lease
from ioalab_sdk.runtime import data_directory
from ioalab_sdk.runtime import LocalClient,public_error
from ioalab_sdk.runtime import RuntimeFault


def released(home):
    path=home/'client.lock'
    if not home.is_dir() or not path.is_file() or path.is_symlink():
        raise RuntimeFault('not_ready','Original runtime ownership evidence is unavailable; preserve its directory','inspect_local_log')
    try:lease=Lease(path,timeout=0)
    except RuntimeError:return False
    lease.close();return True


class Shutdown:
    def __init__(self,deployment):
        self.platform=deployment.value['platform_url'].rstrip('/')
        domains={d['account']:d for d in deployment.accounts}
        self.targets=[]
        for account,entry in deployment.state(prepared=True)['accounts'].items():
            home=data_directory(deployment.config_path(account),deployment.base(domains[account]))
            self.targets.append({'account':account,'home':home,'expected':self.identities(entry['identities']),
                                 'bootId':None,'operationId':str(uuid.uuid4()),'acknowledged':False,'released':False})

    @staticmethod
    def identities(values):
        result={p['profile']:(p['agentId'],p['protocolId'],p['role']) for p in values}
        if len(result)!=len(values):raise RuntimeFault('identity_conflict','Original runtime identity profiles are not distinct')
        return result

    def poll(self):
        rows=[]
        for target in self.targets:
            row={'account':target['account'],'bootId':target['bootId'],'phase':'waiting'};rows.append(row)
            try:
                if released(target['home']):target['released']=True;row['phase']='released';continue
                if target['released']:
                    raise RuntimeFault('instance_changed','A released runtime was replaced; inspect its new ownership before stopping it')
                live=LocalClient(target['home'],timeout=2,boot_id=target['bootId']).call('runtime.status')
                if live['status']!='completed':raise RuntimeFault('not_ready','Original runtime status is unavailable')
                live=live['data'];account=live['account']
                if account['username']!=target['account'] or account['platform']!=self.platform or self.identities(live['identities'])!=target['expected']:
                    raise RuntimeFault('identity_conflict','Original official runtime ownership does not match preparation')
                if target['bootId'] is not None and live['bootId']!=target['bootId']:
                    raise RuntimeFault('instance_changed','Original runtime was replaced; inspect the new instance before stopping it')
                target['bootId']=live['bootId'];row['bootId']=live['bootId']
                if not target['acknowledged']:
                    reply=LocalClient(target['home'],timeout=2,boot_id=target['bootId']).call('runtime.stop',
                        {'bootId':target['bootId']},operation_id=target['operationId'])
                    if reply['status'] not in ('waiting','completed'):
                        error=reply.get('error') or {}
                        raise RuntimeFault(error.get('code','not_ready'),error.get('message','Original runtime did not accept draining'))
                    target['acknowledged']=True
                target['released']=released(target['home'])
                row['phase']='released' if target['released'] else 'draining'
            except RuntimeFault as exc:
                # A lost stop reply can still be resolved by the original OS lease.
                gone=False
                if exc.code not in ('identity_conflict','instance_changed','forbidden','protocol_error'):
                    try:gone=released(target['home'])
                    except RuntimeFault:pass
                if gone:target['released']=True;row['phase']='released';continue
                row.update(phase='blocked' if exc.code in ('identity_conflict','instance_changed','forbidden','protocol_error') else 'waiting',error=public_error(exc))
        complete=all(row['phase']=='released' for row in rows)
        return {'status':'completed' if complete else 'rejected' if any(r['phase']=='blocked' for r in rows) else 'waiting',
                'allRuntimesReleased':complete,'accounts':rows}

    def wait(self,seconds=0,*,on_change=lambda _result:None):
        deadline=None if seconds is None else time.monotonic()+seconds
        previous=None
        while True:
            result=self.poll();encoded=json.dumps(result,sort_keys=True)
            if encoded!=previous:on_change(result);previous=encoded
            if result['status']!='waiting' or deadline is not None and time.monotonic()>=deadline:return result
            time.sleep(.2 if deadline is None else min(.2,max(0,deadline-time.monotonic())))


def stop(deployment,*,wait_seconds=0):
    if type(wait_seconds) not in (int,float) or not 0<=wait_seconds<=30:
        raise RuntimeFault('invalid_input','Stop wait must be between zero and thirty seconds')
    try:guard=deployment.lock()
    except RuntimeError:
        raise RuntimeFault('not_ready','Stop the active deployment coordinator through its service manager first','stop_coordinator') from None
    with guard:return Shutdown(deployment).wait(wait_seconds)


def serve(deployment,*,stop_event=None,interval=60):
    from .coordinator import tick
    event=stop_event if stop_event is not None else threading.Event()
    previous={}
    if stop_event is None:
        for sig in (signal.SIGINT,signal.SIGTERM):
            previous[sig]=signal.signal(sig,lambda *_:event.set())
    emit=lambda result:print(json.dumps(result,ensure_ascii=False),flush=True)
    try:
        with deployment.lock():
            try:
                while not event.is_set():
                    emit(tick(deployment,stop_event=event))
                    event.wait(interval)
            finally:
                result=Shutdown(deployment).wait(None,on_change=emit)
                if result['status']!='completed':
                    raise RuntimeFault('not_ready','Original SDK ownership needs inspection; no process was killed','inspect_original_runtime')
    finally:
        for sig,handler in previous.items():signal.signal(sig,handler)
    return 0
