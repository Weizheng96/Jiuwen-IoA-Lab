"""Public P20/P22/P28 seams with an explicit native boundary fixture, no model."""
import threading
from types import SimpleNamespace
from dataclasses import replace
import pytest

from ioalab_sdk.runtime import (HostRegistry,RuntimeStore,RuntimeFault,HostRegistration,
    HostInput,HostExecution,HostDefinition,HostToolDeclaration,HostToolCall,HostToolResult,
    BindingAck,SubmissionResult,SubmissionEvent,SubmissionError,MachineTerminal,encode,submission_succeeded,
    native_tools_restricted,proposal_tool_declared)


class Adapter:
    registration=HostRegistration('fixture','1')
    def __init__(self):self.calls=[]
    def submit(self,operation_id,input,session_id=None,*,execution=None,on_event=None,on_tool=None):
        self.calls.append((operation_id,input,session_id,execution))
        result=SubmissionResult(operation_id,input.attempt.nonce,'accepted',session_id or 'real-session','real-request','accepted')
        assert on_event(SubmissionEvent('session','session',result))==BindingAck(result.session_id,result.request_id)
        if on_tool and execution and execution.tools:
            value=on_tool(HostToolCall(operation_id,input.attempt.nonce,result.session_id,result.request_id,'call','echo',{'value':7}))
            assert isinstance(value,HostToolResult)
        proof=MachineTerminal(operation_id,input.attempt.nonce,result.session_id,result.request_id,'completed','native-fixture-machine-end')
        return replace(result,state='completed',delivery='handled',terminal=proof)


def test_submit_binds_before_tools_and_replays_original_receipt(tmp_path):
    store=RuntimeStore(tmp_path/'runtime.sqlite','http://local',['actor'],owner_id='owner')
    adapter=Adapter();registry=HostRegistry({'first':adapter,'other':Adapter()},'first');registry.bind(store,identities=[SimpleNamespace(agent_id="actor",profile="assistant")])
    execution=HostExecution('actor','assistant','business','interaction','job',HostDefinition('rule','1','digest','frozen'),tools=(HostToolDeclaration('echo','Echo',{'type':'object'}),))
    effects=[]
    from ioalab_sdk.runtime.sdk_tool_callbacks import CallbackPool
    registry.callback_pool=CallbackPool(1)
    def tool(call):
        assert registry.context('actor','interaction')['sessionId']=='real-session'
        effects.append(call);return {'value':7}
    try:
        result=registry.submit('actor','interaction','operation',HostInput('request'),execution=execution,on_tool=tool)
        assert result.state=='completed' and len(effects)==1
        registry.default='other'
        assert registry.submit('actor','interaction','operation',HostInput('request'),execution=execution,on_tool=tool)==result
        assert len(adapter.calls)==1
        with pytest.raises(RuntimeFault):registry.submit('actor','interaction','operation',HostInput('changed'),execution=execution)
    finally:registry.callback_pool.close();store.close()


def test_claim_survives_transport_unknown_without_second_submission(tmp_path):
    class Disconnect(Adapter):
        def submit(self,*args,**kwargs):self.calls.append(args);raise OSError('fixture disconnect after dispatch')
    path=tmp_path/'runtime.sqlite';adapter=Disconnect()
    store=RuntimeStore(path,'http://local',['actor'],owner_id='owner');registry=HostRegistry({'host':adapter},'host');registry.bind(store,identities=[SimpleNamespace(agent_id="actor",profile="assistant")])
    try:
        result=registry.submit('actor','interaction','operation',HostInput('request'))
        assert result.state=='unknown' and result.request_id is None
        store.close();store=RuntimeStore(path,'http://local',['actor'],owner_id='owner');registry.bind(store,identities=[SimpleNamespace(agent_id="actor",profile="assistant")])
        assert registry.submit('actor','interaction','operation',HostInput('request'))==result
        assert len(adapter.calls)==1
    finally:store.close()


def test_unconfirmed_binding_does_not_allow_sdk_tools(tmp_path):
    class IgnoresFailure(Adapter):
        def submit(self,operation_id,input,session_id=None,*,execution=None,on_event=None,on_tool=None):
            result=SubmissionResult(operation_id,input.attempt.nonce,'accepted','real-session','real-request')
            try:on_event(SubmissionEvent('session','session',result))
            except RuntimeFault:pass
            on_tool(HostToolCall(operation_id,input.attempt.nonce,result.session_id,result.request_id,'call','echo',{}))
            raise AssertionError('An unconfirmed binding admitted a tool')
    store=RuntimeStore(tmp_path/'runtime.sqlite','http://local',['actor'],owner_id='owner')
    registry=HostRegistry({'host':IgnoresFailure()},'host');registry.bind(store,identities=[SimpleNamespace(agent_id="actor",profile="assistant")]);effects=[]
    def reject(event):raise RuntimeFault('not_ready','fixture binding commit failure')
    try:
        registry.submit('actor','interaction','operation',HostInput('request'),on_event=reject,on_tool=lambda call:effects.append(call))
        assert not effects
    finally:store.close()


def test_duplicate_and_late_events_preserve_committed_facts(tmp_path):
    class Events(Adapter):
        def submit(self,operation_id,input,session_id=None,*,execution=None,on_event=None,on_tool=None):
            accepted=SubmissionResult(operation_id,input.attempt.nonce,'accepted','session','request','accepted')
            on_event(SubmissionEvent('session','session',accepted))
            on_event(SubmissionEvent('session','session',accepted))
            proof=MachineTerminal(operation_id,input.attempt.nonce,'session','request','completed','fixture-end')
            completed=replace(accepted,state='completed',delivery='handled',output={'answer':42},usage={'tokens':7},terminal=proof)
            on_event(SubmissionEvent('end','terminal',completed))
            on_event(SubmissionEvent('late','accepted',replace(accepted,session_id=None,request_id=None)))
            return accepted
    store=RuntimeStore(tmp_path/'runtime.sqlite','http://local',['actor'],owner_id='owner')
    registry=HostRegistry({'host':Events()},'host');registry.bind(store,identities=[SimpleNamespace(agent_id="actor",profile="assistant")]);seen=[]
    try:
        result=registry.submit('actor','interaction','operation',HostInput('request'),on_event=seen.append)
        assert [event.event_id for event in seen]==['session','end','late']
        assert seen[-1].result.session_id=='session' and seen[-1].result.request_id=='request'
        assert seen[-1].result.state==result.state=='completed'
        assert result.output=={'answer':42} and result.usage=={'tokens':7} and result.delivery=='handled'
    finally:store.close()


@pytest.mark.parametrize('state,terminal',[('completed',None),('completed','failed')])
def test_unproven_or_conflicting_terminal_never_reports_completed(tmp_path,state,terminal):
    class InvalidEnd(Adapter):
        def submit(self,operation_id,input,session_id=None,**kwargs):
            proof=MachineTerminal(operation_id,input.attempt.nonce,'session','request',terminal,'fixture-end') if terminal else None
            return SubmissionResult(operation_id,input.attempt.nonce,state,'session','request',terminal=proof)
    store=RuntimeStore(tmp_path/'runtime.sqlite','http://local',['actor'],owner_id='owner')
    registry=HostRegistry({'host':InvalidEnd()},'host');registry.bind(store,identities=[SimpleNamespace(agent_id="actor",profile="assistant")])
    try:
        if terminal:
            with pytest.raises(RuntimeFault):registry.submit('actor','interaction','operation',HostInput('request'))
        else:assert registry.submit('actor','interaction','operation',HostInput('request')).state=='unknown'
    finally:store.close()


def test_empty_existing_session_never_turns_into_new_session(tmp_path):
    store=RuntimeStore(tmp_path/'runtime.sqlite','http://local',['actor'],owner_id='owner')
    adapter=Adapter();registry=HostRegistry({'host':adapter},'host');registry.bind(store,identities=[SimpleNamespace(agent_id="actor",profile="assistant")])
    store.checkpoint_set('host-bindings',encode(['actor','interaction']),{'hostRef':'host','sessionId':'','workspace':str(tmp_path),'source':'native'})
    try:
        with pytest.raises(RuntimeFault):registry.submit('actor','interaction','operation',HostInput('request'))
        assert not adapter.calls
    finally:store.close()


def test_optional_query_reconciles_original_failed_request_and_cancel_never_invents_end(tmp_path):
    class Query(Adapter):
        registration=HostRegistration('fixture','1',('get_request','cancel'))
        def submit(self,operation_id,input,session_id=None,*,execution=None,on_event=None,on_tool=None):
            self.calls.append(operation_id)
            self.receipt=SubmissionResult(operation_id,input.attempt.nonce,'accepted','session','request','accepted')
            on_event(SubmissionEvent('session','session',self.receipt));return self.receipt
        def cancel(self,request):
            assert request=='request';return {'accepted':True}  # Unrecognized result is uncertain, not a rejection.
        def get_request(self,request):
            assert request=='request'
            proof=MachineTerminal(self.receipt.operation_id,self.receipt.attempt_id,'session',request,'failed','machine-failure')
            return replace(self.receipt,state='failed',delivery='delivered',terminal=proof)
    store=RuntimeStore(tmp_path/'runtime.sqlite','http://local',['actor'],owner_id='owner')
    host=Query();registry=HostRegistry({'host':host},'host');registry.bind(store,identities=[SimpleNamespace(agent_id="actor",profile="assistant")])
    try:
        original=registry.submit('actor','interaction','operation',HostInput('request'))
        cancel=registry.cancel_submission('actor','interaction','operation')
        assert cancel=={'supported':True,'accepted':None,'ended':False}
        failed=registry.get_submission('actor','interaction','operation')
        assert failed.state=='failed' and failed.delivery=='delivered' and failed.terminal.request_id=='request'
        assert registry.submit('actor','interaction','operation',HostInput('request'))==failed
        assert len(host.calls)==1 and original.attempt_id==failed.attempt_id
    finally:store.close()


def test_late_conflicting_final_content_does_not_replace_original_result(tmp_path):
    class Conflict(Adapter):
        def submit(self,operation_id,input,session_id=None,*,execution=None,on_event=None,on_tool=None):
            accepted=SubmissionResult(operation_id,input.attempt.nonce,'accepted','session','request')
            proof=MachineTerminal(operation_id,input.attempt.nonce,'session','request','completed','machine-end')
            final=replace(accepted,state='completed',terminal=proof,output={'answer':'original'})
            on_event(SubmissionEvent('end','terminal',final))
            return replace(accepted,output={'answer':'late conflicting text'})
    store=RuntimeStore(tmp_path/'runtime.sqlite','http://local',['actor'],owner_id='owner')
    registry=HostRegistry({'host':Conflict()},'host');registry.bind(store,identities=[SimpleNamespace(agent_id="actor",profile="assistant")])
    try:
        with pytest.raises(RuntimeFault):registry.submit('actor','interaction','operation',HostInput('request'))
        saved=store.checkpoint('host-deliveries',encode(['actor','operation']))['receipt']
        assert saved['output']=={'answer':'original'} and saved['state']=='completed'
    finally:store.close()


def test_proven_not_submitted_retries_same_operation_with_new_attempt(tmp_path):
    class Preparing(Adapter):
        def submit(self,operation_id,input,session_id=None,**kwargs):
            self.calls.append((operation_id,input.attempt.nonce))
            if len(self.calls)==1:
                return SubmissionResult(operation_id,input.attempt.nonce,'waiting',error=SubmissionError('not_ready','native_transport','not_submitted','fixture no native dispatch'))
            return SubmissionResult(operation_id,input.attempt.nonce,'accepted',session_id,'request','accepted')
    store=RuntimeStore(tmp_path/'runtime.sqlite','http://local',['actor'],owner_id='owner')
    host=Preparing();registry=HostRegistry({'host':host},'host');registry.bind(store,identities=[SimpleNamespace(agent_id="actor",profile="assistant")])
    try:
        first=registry.submit('actor','interaction','operation',HostInput('request'))
        second=registry.submit('actor','interaction','operation',HostInput('request'))
        assert first.error.dispatch=='not_submitted' and second.state=='accepted'
        assert [call[0] for call in host.calls]==['operation','operation']
        assert first.attempt_id!=second.attempt_id
        assert store.checkpoint('host-submit-attempts',encode(['actor','operation',first.attempt_id]))['receipt']['error']['dispatch']=='not_submitted'
        assert registry.submit('actor','interaction','operation',HostInput('request'))==second and len(host.calls)==2
    finally:store.close()


def test_accepted_request_cannot_be_washed_into_not_submitted(tmp_path):
    class Contradiction(Adapter):
        def submit(self,operation_id,input,session_id=None,*,execution=None,on_event=None,on_tool=None):
            self.calls.append(operation_id)
            result=SubmissionResult(operation_id,input.attempt.nonce,'accepted','session','request','accepted')
            on_event(SubmissionEvent('session','session',result))
            return replace(result,state='waiting',request_id=None,delivery='unknown',error=SubmissionError('not_ready','native_transport','not_submitted','contradictory fixture'))
    store=RuntimeStore(tmp_path/'runtime.sqlite','http://local',['actor'],owner_id='owner')
    host=Contradiction();registry=HostRegistry({'host':host},'host');registry.bind(store,identities=[SimpleNamespace(agent_id="actor",profile="assistant")])
    try:
        with pytest.raises(RuntimeFault):registry.submit('actor','interaction','operation',HostInput('request'))
        preserved=registry.submit('actor','interaction','operation',HostInput('request'))
        assert preserved.request_id=='request' and preserved.delivery=='accepted' and len(host.calls)==1
    finally:store.close()


def test_authenticated_source_fills_only_unknown_workspace_for_original_session(tmp_path):
    from ioalab_sdk.runtime import HostContext
    store=RuntimeStore(tmp_path/'runtime.sqlite','http://local',['actor'],owner_id='owner')
    registry=HostRegistry({'host':Adapter()},'host');registry.bind(store,identities=[SimpleNamespace(agent_id='actor',profile='assistant')])
    original={'hostRef':'host','sessionId':'session','workspace':None,'source':'external'}
    store.checkpoint_set('host-bindings',encode(['actor','interaction']),original)
    try:
        with pytest.raises(RuntimeFault):registry.bind_context('actor','interaction',HostContext('host','session',str(tmp_path)))
        value=registry.bind_context('actor','interaction',HostContext('host','session',str(tmp_path),'actor','assistant'))
        assert value=={**original,'workspace':str(tmp_path)}
        with pytest.raises(RuntimeFault):registry.bind_context('actor','interaction',HostContext('host','session',str(tmp_path/'other'),'actor','assistant'))
        with pytest.raises(RuntimeFault):registry.bind_context('actor','interaction',HostContext('host','different',str(tmp_path),'actor','assistant'))
    finally:store.close()


@pytest.mark.parametrize('observer',[False,True])
@pytest.mark.parametrize('business_state',['unknown','completed','failed'])
def test_unknown_job_retains_one_permit_until_machine_and_actual_callbacks_end(tmp_path,observer,business_state):
    from ioalab_sdk.runtime.scheduling import Scheduler,JobHandler,JobOutcome
    from ioalab_sdk.runtime.sdk_tool_callbacks import CallbackPool
    from conftest import wait_for
    entered=threading.Event();release=threading.Event();exited=threading.Event()
    class Async(Adapter):
        registration=HostRegistration('fixture','1',('get_request',),readonly_context='isolated_session')
        def submit(self,operation_id,input,session_id=None,*,execution=None,on_event=None,on_tool=None):
            self.calls.append(operation_id)
            self.receipt=SubmissionResult(operation_id,input.attempt.nonce,'accepted',session_id or 'observed','request','accepted')
            on_event(SubmissionEvent('session','session',self.receipt))
            self.worker=threading.Thread(target=lambda:on_tool(HostToolCall(operation_id,input.attempt.nonce,self.receipt.session_id,'request','call','verifier.observe' if observer else 'echo',{'observation':7} if observer else {})))
            self.worker.start();assert entered.wait(5)
            return self.receipt
        def get_request(self,request):
            terminal=MachineTerminal(self.receipt.operation_id,'wrong-attempt' if getattr(self,'wrong',False) else self.receipt.attempt_id,self.receipt.session_id,request,'failed','real-machine-end')
            return replace(self.receipt,state='failed',terminal=terminal)
    store=RuntimeStore(tmp_path/'runtime.sqlite','http://local',['actor'],owner_id='owner')
    host=Async();registry=HostRegistry({'original':host,'other':Adapter()},'original');registry.bind(store,identities=[SimpleNamespace(agent_id='actor',profile='assistant')])
    registry.callback_pool=CallbackPool(1)
    store.checkpoint_set('host-bindings',encode(['actor','parent']),{'hostRef':'original','sessionId':'parent-session','workspace':None,'source':'external'})
    registry.default='other' if observer else 'original'
    route='observation' if observer else 'parent'
    def tool(call):
        entered.set();assert release.wait(5);exited.set();return {'effect':'original'}
    def run(job):
        from ioalab_sdk.runtime.observations import SCHEMA
        execution=HostExecution('actor','assistant','parent',route,job['id'],HostDefinition('rule','1','digest','frozen'),
            kind='observation' if observer else 'business',tools=(HostToolDeclaration('verifier.observe' if observer else 'echo','Observe' if observer else 'Echo',SCHEMA if observer else {'type':'object'}),))
        registry.submit('actor',route,'operation',HostInput('request'),execution=execution,on_tool=tool)
        return JobOutcome(business_state,{'businessEffect':'original business fact'})
    queue=Scheduler(store,{'model':JobHandler(run,'1'),'other':JobHandler(lambda job:JobOutcome('completed',{}),'1')},max_models=1)
    registry.scheduler=queue;queue.start()
    try:
        first=queue.submit('actor','parent','model',{},operation_id='first')['reference']
        wait_for(lambda:queue.get(first)['status']==business_state)
        second=queue.submit('actor','different','other',{},operation_id='second')['reference']
        assert entered.is_set() and queue.get(second)['status']=='queued'
        host.wrong=True
        with pytest.raises(RuntimeFault):registry.get_submission('actor',route,'operation')
        assert queue.get(second)['status']=='queued'
        host.wrong=False
        failed=registry.get_submission('actor',route,'operation')
        assert failed.state=='failed' and failed.delivery=='accepted'
        assert queue.get(second)['status']=='queued'
        release.set();assert exited.wait(5);host.worker.join(5)
        registry.get_submission('actor',route,'operation')
        wait_for(lambda:queue.get(second)['status']=='completed')
        assert queue.get(first)['status']==business_state and len(host.calls)==1
        if observer:assert registry.context('actor','parent')['sessionId']=='parent-session'
    finally:
        release.set();queue.close();registry.callback_pool.close();store.close()


def test_real_native_factory_assembly_status_does_not_probe_or_launch(platform,tmp_path,monkeypatch):
    from ioalab_claude_code import native_adapter
    from ioalab_sdk.access import Assembly
    from ioalab_sdk.runtime.config import RuntimeConfig
    from test_resident_runtime import configuration
    path,_=configuration(platform,tmp_path,service=False)
    bundle=native_adapter(host_ref='native',resource_root=(tmp_path/'native').resolve())
    host=bundle['host']
    monkeypatch.setattr(host,'check',lambda:pytest.fail('Startup/status called S02'))
    monkeypatch.setattr(host.processes,'create',lambda:pytest.fail('Startup/status launched a native session'))
    registry=HostRegistry({'native':host},'native')
    assembly=Assembly(RuntimeConfig.load(path),registry,native_handlers=bundle['native_handlers'],native_resources=bundle['native_resources'],native_entry_required=True)
    try:
        for resource in assembly.resources.values():resource.start()
        result=assembly.runtime.call('runtime.status','status',{})
        assert result['status']=='completed'
        projected=result['data']['host']
        assert projected['configured'] and projected['state']=='unknown' and not projected['available']
        assert assembly.runtime.native_sources is not None
    finally:
        assembly.runtime.close()
        for resource in assembly.resources.values():resource.close()


def test_capacity_one_parent_sdk_tool_can_run_real_model_port_without_second_pool_slot(tmp_path):
    from ioalab_sdk.runtime import model_port
    from ioalab_sdk.runtime.scheduling import Scheduler,JobHandler,JobOutcome
    from ioalab_sdk.runtime.sdk_tool_callbacks import CallbackPool
    from conftest import wait_for
    identity=SimpleNamespace(agent_id='actor',profile='personal')
    class Nested(Adapter):
        registration=HostRegistration('fixture','1',readonly_context='isolated_session')
        def submit(self,operation_id,input,session_id=None,*,execution=None,on_event=None,on_tool=None):
            self.calls.append((operation_id,session_id,execution.kind))
            sid='observer-session' if execution.kind=='observation' else 'business-session'
            request='request-'+operation_id
            result=SubmissionResult(operation_id,input.attempt.nonce,'running',sid,request,'accepted')
            assert on_event(SubmissionEvent(request,'session',result))==BindingAck(sid,request)
            if execution.kind=='observation':
                assert session_id is None
                assert execution.deadline=='2099-01-01T00:00:00Z'
                answer=on_tool(HostToolCall(operation_id,input.attempt.nonce,sid,request,'observe','verifier.observe',{'observation':{'fact':42}}))
            else:answer=on_tool(HostToolCall(operation_id,input.attempt.nonce,sid,request,'selftest','selftest',{}))
            assert answer.error is None
            terminal=MachineTerminal(operation_id,input.attempt.nonce,sid,request,'completed','machine-end-'+operation_id)
            return replace(result,state='completed',terminal=terminal,output='done')
    store=RuntimeStore(tmp_path/'runtime.sqlite','http://local',['actor'],owner_id='owner')
    host=Nested();registry=HostRegistry({'original':host,'other':Adapter()},'original');registry.bind(store,identities=[identity]);registry.callback_pool=CallbackPool(1)
    runtime=SimpleNamespace(store=store,config=SimpleNamespace(home=tmp_path),require_execution=lambda:None)
    observations=[]
    def tool(call):
        registry.default='other'
        observations.append(model_port(runtime,registry,identity,'parent')({'read':'fact'},{}))
        return {'observed':observations[-1]}
    def run(job):
        execution=HostExecution('actor','personal','parent','parent',job['id'],HostDefinition('original','1','digest','frozen'),
            tools=(HostToolDeclaration('selftest','Original read-only observation',{'type':'object'}),),deadline='2099-01-01T00:00:00Z')
        result=registry.submit('actor','parent','parent-op',HostInput('request'),execution=execution,on_tool=tool)
        return JobOutcome('completed' if result.state=='completed' else 'unknown',{'nativeState':result.state})
    queue=Scheduler(store,{'model':JobHandler(run,'1')},max_models=1);runtime.scheduler=queue;registry.scheduler=queue;queue.start()
    try:
        job=queue.submit('actor','parent','model',{},operation_id='parent')['reference']
        wait_for(lambda:queue.get(job)['status']=='completed')
        assert observations==[{'fact':42}]
        assert [item[2] for item in host.calls]==['business','observation']
        assert registry.context('actor','parent')['sessionId']=='business-session'
        assert registry.callback_pool.drain(0)
    finally:queue.close();registry.callback_pool.close();store.close()


def test_completed_business_keeps_native_sid_and_pending_admission_until_original_machine_end(tmp_path):
    from ioalab_sdk.runtime import HostContext
    from ioalab_sdk.runtime.scheduling import Scheduler,JobHandler,JobOutcome
    from conftest import wait_for
    class Running(Adapter):
        registration=HostRegistration('fixture','1',('get_request',))
        def submit(self,op,input,session_id=None,*,execution=None,on_event=None,on_tool=None):
            self.receipt=SubmissionResult(op,input.attempt.nonce,'running',session_id,'request','accepted')
            on_event(SubmissionEvent('bound','session',self.receipt));return self.receipt
        def get_request(self,request):
            terminal=MachineTerminal(self.receipt.operation_id,self.receipt.attempt_id,self.receipt.session_id,request,'failed','machine-end')
            return replace(self.receipt,state='failed',terminal=terminal)
    store=RuntimeStore(tmp_path/'runtime.sqlite','http://local',['actor'],owner_id='owner');host=Running()
    registry=HostRegistry({'host':host},'host');registry.bind(store,identities=[SimpleNamespace(agent_id='actor',profile='personal')])
    registry.bind_context('actor','one',HostContext('host','same-sid',str(tmp_path)))
    registry.bind_context('actor','two',HostContext('host','same-sid',str(tmp_path)))
    def run(job):
        registry.submit('actor','one','operation',HostInput('request'))
        return JobOutcome('completed',{'reply':'already truly sent'})
    queue=Scheduler(store,{'original':JobHandler(run,'1'),'next':JobHandler(lambda job:JobOutcome('completed',{'ran':True}),'1')},max_models=2,max_pending=2)
    registry.scheduler=queue;queue.start()
    try:
        first=queue.submit('actor','one','original',{},operation_id='first')['reference']
        wait_for(lambda:queue.get(first)['status']=='completed')
        same=queue.submit('actor','two','next',{},operation_id='same')['reference']
        assert queue.get(same)['status']=='queued'
        with pytest.raises(RuntimeFault) as error:queue.submit('actor','different','next',{},operation_id='third')
        assert error.value.code=='quota_exceeded'
        receipt=registry.get_submission('actor','one','operation')
        assert receipt.state=='failed'
        wait_for(lambda:queue.get(same)['status']=='completed')
        assert queue.get(first)['status']=='completed' and queue.get(first)['data']=={'reply':'already truly sent'}
    finally:queue.close();store.close()


@pytest.mark.parametrize('business_state',['completed','unknown'])
@pytest.mark.parametrize('observation_waiting',[False,True])
def test_accepted_scope_allows_only_actual_async_callback_to_reuse_original_observer_permit(tmp_path,business_state,observation_waiting):
    from contextvars import copy_context
    from ioalab_sdk.runtime import model_port
    from ioalab_sdk.runtime.scheduling import Scheduler,JobHandler,JobOutcome,_CALLBACK_MODEL_SCOPE
    from ioalab_sdk.runtime.sdk_tool_callbacks import CallbackPool
    from conftest import wait_for
    identity=SimpleNamespace(agent_id='actor',profile='personal');captured=[]
    class Async(Adapter):
        registration=HostRegistration('fixture','1',readonly_context='isolated_session')
        def submit(self,op,input,session_id=None,*,execution=None,on_event=None,on_tool=None):
            sid='observation-session' if execution.kind=='observation' else 'business-session';request='request-'+op
            receipt=SubmissionResult(op,input.attempt.nonce,'running',sid,request,'accepted')
            on_event(SubmissionEvent(request,'session',receipt))
            if execution.kind!='observation':
                self.receipt,self.event,self.tool=receipt,on_event,on_tool
                return receipt
            assert session_id is None
            if observation_waiting:
                self.observation_calls=getattr(self,'observation_calls',0)+1
                self.observation_receipt,self.observation_event=receipt,on_event
                return receipt
            result=on_tool(HostToolCall(op,input.attempt.nonce,sid,request,'observation-result','verifier.observe',{'observation':{'fact':42}}))
            assert result.error is None
            return replace(receipt,state='completed',terminal=MachineTerminal(op,input.attempt.nonce,sid,request,'completed','observation-machine-end'))
    store=RuntimeStore(tmp_path/'runtime.sqlite','http://local',['actor'],owner_id='owner');host=Async()
    registry=HostRegistry({'original':host},'original');registry.bind(store,identities=[identity]);registry.callback_pool=CallbackPool(1)
    runtime=SimpleNamespace(store=store,config=SimpleNamespace(home=tmp_path),require_execution=lambda:None)
    def tool(call):
        captured.append(copy_context())
        actual=_CALLBACK_MODEL_SCOPE.get();assert runtime.scheduler.require_model('actor','parent')==first
        # A copied job id, stale boot/attempt, or fabricated active marker is no permit.
        for index,invalid in ((1,'wrong-attempt'),(2,'old-boot'),(3,object())):
            proof=list(actual[1]);proof[index]=invalid
            token=_CALLBACK_MODEL_SCOPE.set((registry,tuple(proof)))
            try:
                with pytest.raises(RuntimeFault):runtime.scheduler.require_model('actor','parent')
            finally:_CALLBACK_MODEL_SCOPE.reset(token)
        observe=model_port(runtime,registry,identity,'parent')
        if observation_waiting:
            with pytest.raises(RuntimeFault):observe({'read':'fact'}, {})
            with pytest.raises(RuntimeFault):observe({'read':'fact'}, {})
            assert host.observation_calls==1
            return {'result':'original observation still pending'}
        return {'result':observe({'read':'fact'}, {})}
    def run(job):
        execution=HostExecution('actor','personal','parent','parent',job['id'],HostDefinition('rule','1','digest','frozen'),
            tools=(HostToolDeclaration('selftest','Original observation',{'type':'object'}),))
        registry.submit('actor','parent','original-op',HostInput('request'),execution=execution,on_tool=tool)
        return JobOutcome(business_state,{'business':'original fact'})
    queue=Scheduler(store,{'original':JobHandler(run,'1'),'other':JobHandler(lambda job:JobOutcome('completed',{}),'1')},max_models=1)
    registry.scheduler=queue;runtime.scheduler=queue;queue.start()
    try:
        first=queue.submit('actor','parent','original',{},operation_id='first')['reference']
        wait_for(lambda:queue.get(first)['status']==business_state)
        other=queue.submit('actor','other','other',{},operation_id='other')['reference']
        assert queue.get(other)['status']=='queued'
        receipt=host.receipt
        answer=host.tool(HostToolCall(receipt.operation_id,receipt.attempt_id,receipt.session_id,receipt.request_id,'selftest','selftest',{}))
        assert answer.error is None and answer.value=={'result':'original observation still pending' if observation_waiting else {'fact':42}}
        with pytest.raises(RuntimeFault):captured[0].run(queue.require_model,'actor','parent')
        assert queue.get(other)['status']=='queued'
        terminal=MachineTerminal(receipt.operation_id,receipt.attempt_id,receipt.session_id,receipt.request_id,'failed','original-machine-failed')
        host.event(SubmissionEvent('actual-machine-end','terminal',replace(receipt,state='failed',terminal=terminal)))
        if observation_waiting:
            assert queue.get(other)['status']=='queued'
            pending=host.observation_receipt
            ended=MachineTerminal(pending.operation_id,pending.attempt_id,pending.session_id,pending.request_id,'failed','original-observation-end')
            host.observation_event(SubmissionEvent('observation-machine-end','terminal',replace(pending,state='failed',terminal=ended)))
        wait_for(lambda:queue.get(other)['status']=='completed')
        assert queue.get(first)['status']==business_state
    finally:queue.close();registry.callback_pool.close();store.close()


def test_static_readonly_modes_do_not_reinterpret_existing_registration_arguments():
    from ioalab_sdk.runtime import HostResource
    asset=HostResource('entry','README.md','text/markdown',None,'installation','package:entry')
    registration=HostRegistration('fixture','1',('cancel',),(asset,))
    assert registration.optional_methods==('cancel',) and registration.resources==(asset,)
    assert registration.readonly_context=='unavailable'
    for mode in ('isolated_session','business_session'):
        assert HostRegistration('fixture','1',readonly_context=mode).readonly_context==mode
    with pytest.raises(RuntimeFault):HostRegistration('fixture','1',readonly_context='infer_from_last_chat')


def test_native_tool_restriction_is_an_optional_static_boolean_declaration():
    assert HostRegistration('fixture','1').native_tool_restriction is False
    assert HostRegistration('fixture','1',native_tool_restriction=True).native_tool_restriction is True
    for value in (None,1,'true'):
        with pytest.raises(RuntimeFault) as error:HostRegistration('fixture','1',native_tool_restriction=value)
        assert error.value.code=='invalid_input'


@pytest.mark.parametrize('declared',[False,True])
@pytest.mark.parametrize('enabled',[False,True,None])
def test_p20_records_native_tool_restriction_and_never_gates_or_reroutes_the_round(tmp_path,declared,enabled):
    class Declared(Adapter):
        registration=HostRegistration('fixture','1',native_tool_restriction=declared)
    store=RuntimeStore(tmp_path/'runtime.sqlite','http://local',['actor'],owner_id='owner')
    adapter=Declared();registry=HostRegistry({'host':adapter},'host')
    registry.bind(store,identities=[SimpleNamespace(agent_id='actor',profile='assistant')])
    authorization={'nativeTools':{'write_file':'allow'},**({} if enabled is None else {'nativeToolsEnabled':enabled})}
    execution=HostExecution('actor','assistant','business','interaction','job',HostDefinition('quote','1','digest','frozen'),authorization=authorization)
    expected=declared and enabled is not True
    try:
        assert registry.native_tools_restricted('actor','operation') is None
        result=registry.submit('actor','interaction','operation',HostInput('review'),execution=execution)
        # Restricted or not, the round is submitted once on the original host, unchanged.
        assert submission_succeeded(result) and len(adapter.calls)==1
        assert adapter.calls[0][3].authorization==authorization and adapter.calls[0][2] is None
        assert native_tools_restricted(adapter.registration,execution) is expected
        assert registry.native_tools_restricted('actor','operation') is expected
        assert registry.submit('actor','interaction','operation',HostInput('review'),execution=execution)==result
        notice=registry.submit('actor','interaction','notice',HostInput('notice'))
        assert notice.error is None and registry.native_tools_restricted('actor','notice') is False
        assert len(adapter.calls)==2
    finally:store.close()


def test_proposal_channel_is_optional_only_when_a_same_schema_sdk_tool_carries_the_effect():
    schema={'type':'object','properties':{'output':{}},'required':['output'],'additionalProperties':False}
    base=HostExecution('actor','assistant','business','interaction','job',HostDefinition('rule','1','digest','frozen'),
        tools=(HostToolDeclaration('ioa_reply','Reply',schema),))
    assert proposal_tool_declared(replace(base,proposal_schema=schema)) is True
    assert proposal_tool_declared(base) is False
    assert proposal_tool_declared(replace(base,proposal_schema={'type':'object'})) is False
    assert proposal_tool_declared(replace(base,tools=(),proposal_schema=schema)) is False


@pytest.mark.parametrize('enabled',[None,1,'true',[],{}])
def test_native_phase_authorization_rejects_nonboolean_values(enabled):
    with pytest.raises(RuntimeFault) as error:
        HostExecution('actor','assistant','business','business','job',HostDefinition('rule','1','digest','frozen'),
            authorization={'nativeTools':{},'nativeToolsEnabled':enabled})
    assert error.value.code=='invalid_input'


def test_observation_cannot_enable_native_tools_before_any_s01_dispatch(tmp_path):
    from ioalab_sdk.runtime.observations import SCHEMA
    store=RuntimeStore(tmp_path/'runtime.sqlite','http://local',['actor'],owner_id='owner')
    host=Adapter();registry=HostRegistry({'host':host},'host')
    registry.bind(store,identities=[SimpleNamespace(agent_id='actor',profile='assistant')])
    registry.tools_for('actor','business','assistant')
    execution=HostExecution('actor','assistant','business','observation','job',HostDefinition('verifier','1','digest','frozen'),
        kind='observation',tools=(HostToolDeclaration('verifier.observe','Read-only',SCHEMA),),
        authorization={'nativeTools':{},'nativeToolsEnabled':True})
    try:
        with pytest.raises(RuntimeFault) as error:registry.submit('actor','observation','op',HostInput('observe'),execution=execution)
        assert error.value.code=='forbidden' and not host.calls
    finally:store.close()


@pytest.mark.parametrize('pending_kind',['business','observation'])
def test_unreleased_native_scope_cannot_be_bypassed_by_another_operation_on_the_same_job(tmp_path,pending_kind):
    from ioalab_sdk.runtime.observations import SCHEMA
    from ioalab_sdk.runtime.scheduling import Scheduler,JobHandler,JobOutcome
    from conftest import wait_for
    class Pending(Adapter):
        registration=HostRegistration('fixture','1',readonly_context='isolated_session')
        def submit(self,operation_id,input,session_id=None,*,execution=None,on_event=None,on_tool=None):
            self.calls.append(operation_id)
            receipt=SubmissionResult(operation_id,input.attempt.nonce,'accepted',session_id or 'original-native','request-'+operation_id,'accepted')
            on_event(SubmissionEvent('actual-session','session',receipt))
            return receipt
    store=RuntimeStore(tmp_path/'runtime.sqlite','http://local',['actor'],owner_id='owner')
    host=Pending();registry=HostRegistry({'host':host},'host')
    registry.bind(store,identities=[SimpleNamespace(agent_id='actor',profile='assistant')])
    registry.tools_for('actor','business','assistant')
    def execute(job):
        route='observer' if pending_kind=='observation' else 'business'
        tools=(HostToolDeclaration('verifier.observe','Read only',SCHEMA),) if pending_kind=='observation' else ()
        snapshot=HostExecution('actor','assistant','business',route,job['id'],HostDefinition('original','1','digest','frozen'),kind=pending_kind,tools=tools)
        original=registry.submit('actor',route,'original-op',HostInput('original'),execution=snapshot)
        assert registry.submit('actor',route,'original-op',HostInput('original'),execution=snapshot)==original
        child=replace(snapshot,interaction_id='business',kind='business',tools=())
        with pytest.raises(RuntimeFault) as rejected:registry.submit('actor','business','new-name',HostInput('replacement'),execution=child)
        assert rejected.value.code=='not_ready' and host.calls==['original-op']
        assert not registry.original_execution_ended('actor',route,'original-op',job['id'])
        return JobOutcome('unknown',{'original':original.operation_id})
    queue=Scheduler(store,{'model':JobHandler(execute,'1'),'other':JobHandler(lambda job:JobOutcome('completed',{}),'1')},max_models=1)
    registry.scheduler=queue;queue.start()
    try:
        first=queue.submit('actor','business','model',{},operation_id='first')['reference']
        wait_for(lambda:queue.get(first)['status']=='unknown')
        assert queue.get(first)['data']=={'original':'original-op'}
        second=queue.submit('actor','other-business','other',{},operation_id='other')['reference']
        assert queue.get(second)['status']=='queued' and host.calls==['original-op']
    finally:queue.close();store.close()


@pytest.mark.parametrize('error',[None,SubmissionError('execution_error','submission','accepted','Whole submission failed'),
    SubmissionError('definition_error','execution','unknown','Original result uncertain')])
def test_submission_success_is_machine_fact_without_a_whole_submission_error(error):
    terminal=MachineTerminal('op','attempt','sid','request','completed','actual-machine-end')
    receipt=SubmissionResult('op','attempt','completed','sid','request','handled',error=error,terminal=terminal)
    # A whole-round error never becomes success, and the true machine terminal is kept, not rewritten.
    assert submission_succeeded(receipt) is (error is None)
    assert receipt.state=='completed' and receipt.terminal==terminal


@pytest.mark.parametrize('field',['operation_id','attempt_id','session_id','request_id'])
def test_submission_success_requires_the_same_original_machine_request(field):
    terminal=replace(MachineTerminal('op','attempt','sid','request','completed','actual-machine-end'),**{field:'other'})
    assert submission_succeeded(SubmissionResult('op','attempt','completed','sid','request',terminal=terminal)) is False


@pytest.mark.parametrize('state',['accepted','running','waiting','unknown','failed','cancelled'])
def test_handled_or_completed_text_cannot_replace_original_machine_success(state):
    assert submission_succeeded(SubmissionResult('op','attempt',state,'sid','request','handled',output='completed')) is False
    assert submission_succeeded(SubmissionResult('op','attempt','completed','sid','request','handled')) is False
