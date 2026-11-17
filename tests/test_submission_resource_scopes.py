"""P26 original-scope bytes and drain through the registered S01 constructor port."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from dataclasses import replace
from types import SimpleNamespace
import threading

import pytest
from a2c.codec import digest,digest_bytes
from ioalab_sdk.runtime import (BindingAck,ExecutionResources,ResourceGrant,HostDefinition,
    HostExecution,HostInput,HostRegistration,HostRegistry,HostResource,MachineTerminal,
    RuntimeFault,RuntimeStore,SubmissionEvent,SubmissionResourcePort,SubmissionResult)
from ioalab_sdk.runtime.scheduling import Scheduler,JobHandler,JobOutcome
from conftest import wait_for


OWNER_CONTEXT=ContextVar('test-p26-original-owner-context',default=None)


@pytest.fixture
def scope(tmp_path):
    store=RuntimeStore(tmp_path/'runtime.sqlite','http://local',['actor'],owner_id='owner')
    port=SubmissionResourcePort()
    class Native:
        registration=HostRegistration('resource-boundary','1')
        action=None
        def submit(self,operation_id,input,session_id=None,*,execution=None,on_event=None,on_tool=None):
            self.attempt=input.attempt
            self.result=SubmissionResult(operation_id,input.attempt.nonce,'running',session_id or 'native-session','native-request','accepted')
            self.event=on_event
            assert on_event(SubmissionEvent('session','session',self.result))==BindingAck(self.result.session_id,self.result.request_id)
            self.reader,self.sink=port.for_host('native')(operation_id,input.attempt)
            return self.action(self) if self.action else self.result
        def finish(self):
            terminal=MachineTerminal(self.result.operation_id,self.attempt.nonce,self.result.session_id,
                self.result.request_id,'completed','original-machine-exit')
            self.result=replace(self.result,state='completed',terminal=terminal)
            return self.result
    native=Native();registry=HostRegistry({'native':native},'native')
    registry.bind(store,identities=[SimpleNamespace(agent_id='actor',profile='personal')])
    # This is the existing SDK Assembly delegate injection, not a new public API.
    port.bind(registry._resources_for)
    source=tmp_path/'approved.txt';source.write_bytes(b'original approved material')
    binding={'actor':'actor','contract':'original-card','period':1}
    material=HostResource('material','approved.txt','text/plain',digest_bytes(source.read_bytes()),digest(binding),'platform:original-file')
    resources=ExecutionResources(binding=binding,operation_id='operation',output_root=tmp_path/'outputs',
        grants=(ResourceGrant(material,source.read_bytes),),authorize=lambda *_:None)
    value=SimpleNamespace(store=store,port=port,native=native,registry=registry,source=source,material=material,resources=resources)
    yield value
    store.close()


def test_async_resource_transport_keeps_original_owner_context_and_actual_bytes(scope,tmp_path):
    original='original-actor-and-period'
    def authorize(*_):
        if OWNER_CONTEXT.get()!=original:raise RuntimeFault('forbidden','Original owner context lost')
    resources=ExecutionResources(binding={'actor':'actor','contract':'original-card','period':1},operation_id='operation',
        output_root=tmp_path/'context-outputs',grants=(ResourceGrant(scope.material,scope.source.read_bytes),),authorize=authorize)
    def native(adapter):
        with ThreadPoolExecutor(max_workers=1) as worker:
            actual=worker.submit(adapter.reader,scope.material).result(timeout=5)
            assert actual==b'original approved material'
            output=worker.submit(adapter.sink,'actual.txt','text/plain',actual,digest_bytes(actual)).result(timeout=5)
        return replace(adapter.finish(),resources=(output,))
    scope.native.action=native
    token=OWNER_CONTEXT.set(original)
    try:
        result=scope.registry.submit('actor','business','operation',HostInput('use original material'),
            resource_io=(resources.read_resource,resources.store_output))
        assert result.state=='completed' and result.terminal
        assert resources.read_output(result.resources[0])==b'original approved material'
    finally:OWNER_CONTEXT.reset(token)


@pytest.mark.parametrize('changed',['host','nonce','scope','actor','profile'])
def test_resource_factory_rejects_foreign_scope_before_owner_reads(scope,changed):
    reads=[]
    def native(adapter):
        attempt=adapter.attempt
        selected='other-host' if changed=='host' else 'native'
        if changed!='host':attempt=replace(attempt,**{{'nonce':'nonce','scope':'scope_digest','actor':'actor','profile':'profile'}[changed]:'foreign'})
        with pytest.raises(RuntimeFault) as error:scope.port.for_host(selected)('operation',attempt)
        assert error.value.code in ('identity_conflict','not_ready')
        assert not reads
        assert adapter.reader(scope.material)==b'original approved material'
        return adapter.finish()
    scope.native.action=native
    result=scope.registry.submit('actor','business','operation',HostInput('original'),
        resource_io=(lambda ref:reads.append(ref) or scope.resources.read_resource(ref),scope.resources.store_output))
    assert result.state=='completed' and reads==[scope.material]


def test_machine_terminal_cannot_release_model_while_actual_collection_is_still_reading(scope):
    entered=threading.Event();release=threading.Event();other=threading.Event();reads=[]
    pool=ThreadPoolExecutor(max_workers=1)
    def read(ref):
        entered.set();assert release.wait(5)
        return scope.resources.read_resource(ref)
    def native(adapter):
        reads.append(pool.submit(adapter.reader,scope.material))
        assert entered.wait(5)
        return adapter.result
    scope.native.action=native
    def first(job):
        execution=HostExecution('actor','personal','business','business',job['id'],HostDefinition('frozen','1','digest','original'))
        result=scope.registry.submit('actor','business','operation',HostInput('original'),execution=execution,
            resource_io=(read,scope.resources.store_output))
        return JobOutcome('completed',{'nativeState':result.state})
    scheduler=Scheduler(scope.store,{'resource':JobHandler(first,'1'),
        'other':JobHandler(lambda job:other.set() or JobOutcome('completed',{}),'1'),
        'barrier':JobHandler(lambda job:JobOutcome('completed',{}),'1',False,control=True)},max_models=1)
    scope.registry.scheduler=scheduler;scheduler.start()
    try:
        initial=scheduler.submit('actor','business','resource',{},operation_id='first')['reference']
        wait_for(lambda:scheduler.get(initial)['status']=='completed')
        scope.native.event(SubmissionEvent('terminal','terminal',scope.native.finish()))
        queued=scheduler.submit('actor','another-business','other',{},operation_id='second')['reference']
        control=scheduler.submit('actor','barrier','barrier',{},operation_id='control')['reference']
        wait_for(lambda:scheduler.get(control)['status']=='completed')
        assert scheduler.get(queued)['status']=='queued' and not other.is_set()
        release.set();assert reads[0].result(timeout=5)==b'original approved material'
        wait_for(lambda:scheduler.get(queued)['status']=='completed')
        assert other.is_set()
    finally:release.set();pool.shutdown(wait=True);scheduler.close()


def test_original_resource_handles_cannot_collect_after_machine_end_or_registry_restart(scope):
    def collect(adapter):
        output=adapter.sink('already-collected.txt','text/plain',b'actual',digest_bytes(b'actual'))
        return replace(adapter.finish(),resources=(output,))
    scope.native.action=collect
    result=scope.registry.submit('actor','business','operation',HostInput('original'),
        resource_io=(scope.resources.read_resource,scope.resources.store_output))
    assert result.state=='completed'
    with pytest.raises(RuntimeFault) as error:scope.native.reader(scope.material)
    assert error.value.code=='closed'
    replacement=HostRegistry({'native':scope.native},'native')
    replacement.bind(scope.store,identities=[SimpleNamespace(agent_id='actor',profile='personal')])
    replacement_port=SubmissionResourcePort();replacement_port.bind(replacement._resources_for)
    with pytest.raises(RuntimeFault):replacement_port.for_host('native')('operation',scope.native.attempt)
    # Recovery reads actual stored output through its original resource authority,
    # never by rerunning production or obtaining a new native collection scope.
    assert scope.resources.read_output(result.resources[0])==b'actual'
