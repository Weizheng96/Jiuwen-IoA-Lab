"""Single local owner. Host and role operations are injected by the assembly entry."""
import os
import threading
import time

from ..lease import Lease
from .config import RuntimeConfig
from .connection import AgentConnection
from .files import read_json,prepare_home
from .identities import load_identity
from .ipc import IpcServer,public_error
from .protocol import RuntimeFault
from .scheduling import Scheduler
from .session import AccountSession
from .store import RuntimeStore
from .confirmations import Confirmations
from .user_ui import UserUi
from .installation import current as current_installation


class ResidentRuntime:
    def __init__(self,config:RuntimeConfig,*,operations=None,jobs=None,host_status=None,ready_profiles=(),modules=(),assembly=None):
        self.config=config
        self.installation=current_installation(assembly)
        self._lease=self.account=self.store=self.scheduler=self.connection=self.ipc=None
        self.identities=[]
        self.operations=dict(operations or {})
        self.host_status=host_status or (lambda:{'available':False,'reason':'Host adapter is not configured'})
        self.ready_profiles=set(ready_profiles)
        self.modules=tuple(modules)
        self.stop_requested=threading.Event()
        self.closing=threading.Event()
        self._closed=False
        reserved={'runtime.status','runtime.stop','runtime.open_ui','identity.status','operations.get','operations.resume','confirmations.get'}
        if set(self.operations)&reserved or not all(callable(handler) for handler in self.operations.values()):
            raise ValueError('Register explicit non-reserved role operations')
        try:
            prepare_home(config.home)
            # This same OS lock also fences the pre-migration direct client entry.
            self._lease=Lease(config.home/'client.lock',timeout=0)
            index=read_json(config.home/'identity-index.json') if (config.home/'identity-index.json').exists() else {}
            if not set(index).issubset({p['key'] for p in config.identities}):
                raise RuntimeFault('identity_conflict','Keep existing identity profiles until their business is migrated','migrate')
            self.account=AccountSession(config.platform,config.username,config.password,config.home,allow_offline=True)
            self.identities=[load_identity(self.account,config.home,profile) for profile in config.identities]
            actors=[p.agent_id for p in self.identities]
            self.store=RuntimeStore(config.home/'runtime.sqlite',config.platform,actors,owner_id=self.account.account['id'])
            self.connection=AgentConnection(config.platform,self.account.bearer,actors,self.store)
            self.confirmations=Confirmations(self.store)
            job_handlers=dict(jobs or {})
            for module in self.modules:
                registration=module.bind(self)
                registered=registration.get('operations',{})
                if set(registered)&(reserved|set(self.operations)):raise ValueError('Duplicate role operation')
                if set(registration.get('jobs',{}))&set(job_handlers):raise ValueError('Duplicate role job handler')
                self.operations.update(registered)
                job_handlers.update(registration.get('jobs',{}))
                self.ready_profiles.update(registration.get('profiles',()))
            self.scheduler=Scheduler(self.store,job_handlers,max_models=config.max_model_runs,max_pending=config.max_pending_requests,
                                     can_dispatch=lambda:self.connection.connected)
            self.connection.on_delivery=self._wake_roles
            self.user_ui=UserUi(self.confirmations)
            self.ipc=IpcServer(config.home,self.store.runtime_id,self.call,user_ui=self.user_ui)
        except BaseException:
            self.close()
            raise

    def start(self):
        self.scheduler.start()
        self.connection.start(wait=False)
        for module in self.modules:module.start()
        self.ipc.start()
        return self

    def _wake_roles(self):
        for module in self.modules:module.wake()

    def require_execution(self):
        if not self.connection.connected or self.stop_requested.is_set():
            raise RuntimeFault('not_ready','A current execution connection is required','restore_connection')

    def status(self):
        host=self.host_status()
        scheduler=self.scheduler.status()
        connected=self.connection.connected
        available=bool(host.get('available') and connected and not scheduler['error'] and not self.stop_requested.is_set())
        def role_ready(profile):
            return all(module.ready(profile) for module in self.modules
                       if hasattr(module,'ready') and ((getattr(module,'identity',None) and module.identity.profile==profile)
                           or profile in getattr(module,'identities',{})))
        return {'runtimeId':self.store.runtime_id,'bootId':self.ipc.boot_id,'pid':os.getpid(),
                'configurationDigest':self.config.public_summary()['configurationDigest'],
                'installation':self.installation,
                'ipcReady':bool(self.ipc.thread and self.ipc.thread.is_alive()),
                'account':self.account.status(),'platformConnected':connected,
                'connectionError':public_error(self.connection.error) if self.connection.error else None,
                'identities':[{**p.public(),'executionReady':available and p.profile in self.ready_profiles and role_ready(p.profile)} for p in self.identities],
                'host':host,'scheduler':scheduler,'delivery':self.store.summary(),
                'roleErrors':[module.error for module in self.modules if getattr(module,'error',None)],
                'stopping':self.stop_requested.is_set()}

    def _delivery_operation(self,args):
        if set(args)!={'reference'} or not isinstance(args['reference'],dict):
            raise RuntimeFault('invalid_input','An original operation reference is required')
        reference=args['reference']
        if any(not isinstance(value,str) or not 1<=len(value)<=256 for value in reference.values()):
            raise RuntimeFault('invalid_input','Operation reference fields must be bounded strings')
        if reference.get('kind')=='job' and set(reference)=={'kind','id'}:
            return self.scheduler.get(reference['id'])
        if set(reference)!={'kind','agentId','operation','operationId'} or reference['kind']!='delivery':
            raise RuntimeFault('invalid_input','Invalid original operation reference')
        if reference['agentId'] not in self.store.cursors():raise RuntimeFault('forbidden','Identity is outside this instance')
        response=self.store.response(reference['agentId'],reference['operation'],reference['operationId'])
        if response:
            return {'status':'completed' if response['ok'] else 'rejected','response':response,'reference':reference}
        pending=next((row for row in self.store.pending() if (row['actor'],row['kind'],row['id'])==
                      (reference['agentId'],reference['operation'],reference['operationId'])),None)
        if not pending:raise RuntimeFault('not_ready','Original operation is not stored in this instance')
        return {'status':'accepted','response':None,'reference':reference}

    def call(self,operation,operation_id,args):
        try:
            state='completed'
            if operation=='runtime.status':
                if args:raise RuntimeFault('invalid_input','Status takes no arguments')
                data=self.status()
            elif operation=='identity.status':
                if args:raise RuntimeFault('invalid_input','Identity status takes no arguments')
                data=[p.public() for p in self.identities]
            elif operation=='runtime.open_ui':
                if args:raise RuntimeFault('invalid_input','Opening the local user page takes no arguments')
                data={'opened':self.user_ui.open()}
            elif operation=='confirmations.get':
                if set(args)!={'reference'} or not isinstance(args['reference'],str):raise RuntimeFault('invalid_input','Original confirmation reference required')
                data=self.confirmations.get(args['reference'])
            elif operation=='runtime.stop':
                if args!={'bootId':self.ipc.boot_id}:raise RuntimeFault('id_conflict','Stop must reference the intended running instance')
                self.scheduler.drain()
                self.stop_requested.set()
                state='completed' if self.scheduler.idle else 'waiting'
                data={'draining':True,'preserved':True,'bootId':self.ipc.boot_id}
            elif operation in ('operations.get','operations.resume'):
                # Delivery outbox replays itself; unknown role work must reconcile
                # through its role, never by generically calling the model again.
                data=self._delivery_operation(args)
                if operation=='operations.resume' and data['status']=='unknown':
                    raise RuntimeFault('unknown_result','Use the original role to reconcile its external effects','reconcile_role')
            else:
                if self.stop_requested.is_set():raise RuntimeFault('closed','Runtime is draining')
                handler=self.operations.get(operation)
                if not handler:raise RuntimeFault('not_ready','The requested role operation is not configured','configure_role')
                result=handler(self,operation_id,args)
                if not isinstance(result,dict) or result.get('status') not in ('accepted','running','waiting','completed','rejected','failed','unknown'):
                    raise RuntimeFault('protocol_error','Role operation did not return an explicit result')
                return {'operationId':operation_id,'status':result['status'],'data':result.get('data'),'error':result.get('error')}
            return {'operationId':operation_id,'status':state,'data':data,'error':None}
        except RuntimeFault as exc:
            return {'operationId':operation_id,'status':'unknown' if exc.code=='unknown_result' else 'rejected','data':None,'error':public_error(exc)}

    def run(self):
        self.start()
        while not (self.stop_requested.is_set() and self.scheduler.idle):
            time.sleep(.1)

    def close(self):
        if self._closed:return
        self._closed=True
        self.closing.set()
        # Keep the DB and OS ownership until all actual work/RPC threads stop.
        if self.scheduler:self.scheduler.drain()
        for module in self.modules:
            if hasattr(module,'quiesce'):module.quiesce()
        if self.ipc:self.ipc.close()
        if self.scheduler:self.scheduler.close()
        for module in self.modules:module.close()
        if self.connection:self.connection.close()
        if self.store:self.store.close()
        if self.account:self.account.close()
        if self._lease:self._lease.close()

    def __enter__(self):return self.start()
    def __exit__(self,*_):self.close()
