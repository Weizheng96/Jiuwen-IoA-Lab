"""Explicit opt-in model verification through the SDK's only S01 route.

A real adapter is reached only by ``HostRegistry.submit`` inside an original
Scheduler job: P28 persists the actual session before any SDK tool callback, the
frozen execution snapshot carries the caller's definition, and S04/S05 use the
original request. No adapter run/probe/capabilities call, second execution chain
or test helper inside the product SDK. Importing this module runs no model.
"""
from pathlib import Path
import threading
from types import SimpleNamespace
import uuid

from a2c.codec import digest
from ioalab_sdk.runtime import HostInput,HostRegistry,RuntimeFault,RuntimeStore,execution_snapshot
from ioalab_sdk.runtime.scheduling import JobHandler,JobOutcome,Scheduler
from ioalab_sdk.runtime.sdk_tool_callbacks import CallbackPool


class ExplicitSubmission:
    handler='verification.submit'

    def __init__(self,host,home,*,actor='verification-actor',profile='verification'):
        self.home=Path(home);self.home.mkdir(parents=True,exist_ok=True)
        self.identity=SimpleNamespace(agent_id=actor,profile=profile)
        self.store=RuntimeStore(self.home/'runtime.sqlite','http://localhost',[actor],owner_id='verification')
        self.registry=HostRegistry({'explicit':host},'explicit')
        self.registry.bind(self.store,identities=[self.identity])
        self.pool=CallbackPool(1);self.registry.callback_pool=self.pool
        self.scheduler=Scheduler(self.store,{self.handler:JobHandler(self._run,'verification-submit-v1')},max_models=1)
        self.registry.scheduler=self.scheduler
        self.turns={}
        self.scheduler.start()

    def start(self,interaction,definition,text,*,workspace,on_tool,on_event=None,deadline=None):
        """Queue one original logical operation; later rounds of the same interaction keep its P28 session."""
        operation=str(uuid.uuid4())
        self.turns[operation]={'definition':definition,'text':text,'workspace':str(workspace),'on_tool':on_tool,
                               'on_event':on_event,'deadline':deadline,'done':threading.Event(),'interaction':interaction,
                               'job':Scheduler.reference(self.identity.agent_id,self.handler,operation)}
        job=self.scheduler.submit(self.identity.agent_id,interaction,self.handler,{'operation':operation},operation_id=operation)
        if job['reference']!=self.turns[operation]['job']:raise RuntimeFault('identity_conflict','Scheduler changed the original job reference')
        return operation

    def wait(self,operation,timeout):
        turn=self.turns[operation]
        if not turn['done'].wait(timeout):
            raise RuntimeFault('unknown_result','The original S01 submission has not returned; query it, never resubmit')
        if 'error' in turn:raise turn['error']
        return turn['result']

    def submit(self,interaction,definition,text,*,timeout,**kwargs):
        return self.wait(self.start(interaction,definition,text,**kwargs),timeout)

    def query(self,operation):
        turn=self.turns[operation]
        return self.registry.get_submission(self.identity.agent_id,turn['interaction'],turn['job'])

    def cancel(self,operation):
        turn=self.turns[operation]
        return self.registry.cancel_submission(self.identity.agent_id,turn['interaction'],turn['job'])

    def _run(self,job):
        turn=self.turns[job['payload']['operation']]
        try:
            definition=turn['definition']
            execution=execution_snapshot(self.identity,job['interaction'],job['id'],definition,
                definition_digest=digest({'instructions':definition.instructions,'tools':[item.policy() for item in definition.tools]}),
                version='1',deadline=turn['deadline'],workspace=turn['workspace'])
            result=self.registry.submit(self.identity.agent_id,job['interaction'],job['id'],HostInput(turn['text']),execution=execution,
                on_event=turn['on_event'],on_tool=lambda call:turn['on_tool'](call.name,call.arguments,call.call_id))
            turn['result']=result
        except BaseException as exc:
            turn['error']=exc
            raise
        finally:turn['done'].set()
        # Only a real machine terminal ends the job; accepted/unknown keeps the original request open.
        state={'completed':'completed','failed':'failed','cancelled':'failed'}.get(result.state,'unknown') if result.terminal else 'unknown'
        return JobOutcome(state,{'submissionState':result.state},result.session_id)

    def close(self):
        try:self.scheduler.close()
        finally:
            try:self.pool.close()
            finally:self.store.close()
