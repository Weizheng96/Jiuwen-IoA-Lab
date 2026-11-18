"""P16 through the original Scheduler/Store and the public S01 boundary."""
from dataclasses import replace
from types import SimpleNamespace
import json
import pytest

from ioalab_sdk.runtime import (BindingAck,HostContext,HostRegistration,HostRegistry,
    HostToolCall,MachineTerminal,RuntimeFault,RuntimeStore,SubmissionEvent,
    SubmissionResult,SubmissionError,model_port)
from ioalab_sdk.runtime.scheduling import JobHandler,JobOutcome,Scheduler
from conftest import wait_for


class ObservationAdapter:
    def __init__(self,mode,*,output=None,callbacks=(),disconnect=False,skip_ack=False,error=None):
        self.registration=HostRegistration('observation-boundary','1',readonly_context=mode)
        self.output,self.callbacks,self.disconnect,self.skip_ack=output,callbacks,disconnect,skip_ack
        self.calls=[];self.effects=[]
        self.error=error

    def submit(self,operation_id,input,session_id=None,*,execution=None,on_event=None,on_tool=None):
        self.calls.append((operation_id,input,session_id,execution))
        assert execution.authorization.get('nativeToolsEnabled') is True
        sid=session_id or ('real-business-created' if self.registration.readonly_context=='business_session' else 'real-isolated-observer')
        receipt=SubmissionResult(operation_id,input.attempt.nonce,'running',sid,'actual-'+operation_id,'accepted')
        if not self.skip_ack:
            assert on_event(SubmissionEvent('actual-session','session',receipt))==BindingAck(sid,receipt.request_id)
            if self.registration.readonly_context=='business_session':
                assert self.registry.context('actor','business')['sessionId']==sid
        if self.disconnect:raise OSError('actual call result lost')
        for index,args in enumerate(self.callbacks):
            self.effects.append(on_tool(HostToolCall(operation_id,input.attempt.nonce,sid,receipt.request_id,
                'result-'+str(index),'verifier.observe',args)))
        terminal=MachineTerminal(operation_id,input.attempt.nonce,sid,receipt.request_id,'completed','actual-api-completion')
        return replace(receipt,state='completed',terminal=terminal,output=self.output,error=self.error)


@pytest.fixture
def observe(tmp_path):
    opened=[]
    def run(adapter,*,parent_sid='original-business',parent=True,repeat_on_error=False):
        store=RuntimeStore(tmp_path/('runtime-'+str(len(opened))+'.sqlite'),'http://local',['actor'],owner_id='owner')
        other=ObservationAdapter('unavailable')
        registry=HostRegistry({'original':adapter,'other':other},'original')
        identity=SimpleNamespace(agent_id='actor',profile='assistant')
        registry.bind(store,identities=[identity]);adapter.registry=registry
        if parent:
            if parent_sid is None:registry.tools_for('actor','business','assistant')
            else:registry.bind_context('actor','business',HostContext('original',parent_sid,str(tmp_path)))
        registry.default='other'
        runtime=SimpleNamespace(store=store,config=SimpleNamespace(home=tmp_path),require_execution=lambda:None)
        errors=[]
        def execute(job):
            for _ in range(2 if repeat_on_error else 1):
                try:
                    value=model_port(runtime,registry,identity,'business')({'fact':'observe only'}, {})
                    return JobOutcome('completed',{'observation':value})
                except RuntimeFault as exc:errors.append(exc.code)
            return JobOutcome('unknown',{'errors':errors})
        queue=Scheduler(store,{'observe':JobHandler(execute,'1')},max_models=1)
        runtime.scheduler=queue;registry.scheduler=queue;opened.append((queue,store));queue.start()
        reference=queue.submit('actor','business','observe',{},operation_id='original-job')['reference']
        wait_for(lambda:queue.get(reference)['status'] in ('completed','unknown'))
        assert not other.calls
        return queue.get(reference),registry
    yield run
    for queue,store in reversed(opened):queue.close();store.close()


@pytest.mark.parametrize('mode,parent_sid,expected',[
    ('isolated_session','original-business',None),
    ('business_session','original-business','original-business'),
    ('business_session',None,None),
])
def test_declared_mode_uses_original_host_and_p28_binds_actual_business_before_observing(observe,mode,parent_sid,expected):
    adapter=ObservationAdapter(mode,output={'observation':{'fact':42}})
    result,registry=observe(adapter,parent_sid=parent_sid)
    assert result['status']=='completed' and result['data']=={'observation':{'fact':42}}
    assert len(adapter.calls)==1 and adapter.calls[0][2]==expected
    execution=adapter.calls[0][3]
    assert execution.kind=='observation' and execution.business_ref=='business'
    assert execution.interaction_id!='business' and len(execution.tools)==1
    assert execution.tools[0].name=='verifier.observe' and execution.proposal_schema is None
    original=registry.context('actor','business')
    assert original['sessionId']==(parent_sid if mode=='isolated_session' else parent_sid or 'real-business-created')
    if parent_sid is None:
        assert original['workspace'] is None and original['source']=='external'
        assert 'definitionDigest' not in original


@pytest.mark.parametrize('declared',[False,True])
def test_observation_basis_records_native_tool_restriction_and_unrestricted_still_observes(observe,declared):
    adapter=ObservationAdapter('isolated_session',callbacks=({'observation':1},))
    adapter.registration=replace(adapter.registration,native_tool_restriction=declared)
    result,registry=observe(adapter)
    assert result['status']=='completed' and result['data']=={'observation':1} and len(adapter.calls)==1
    reference=adapter.calls[0][0]
    assert adapter.calls[0][3].authorization['nativeToolsEnabled'] is True
    assert registry.native_tools_restricted('actor',reference) is False
    basis=registry.store.checkpoint('model-observations:actor',reference)
    assert basis['state']=='completed' and basis['nativeToolsRestricted'] is False


@pytest.mark.parametrize('parent,mode',[(True,'unavailable'),(False,'isolated_session')])
def test_missing_parent_or_static_readonly_support_refuses_without_a_model_submission(observe,parent,mode):
    adapter=ObservationAdapter(mode,output={'observation':True})
    result,_=observe(adapter,parent=parent)
    assert result['status']=='unknown' and result['data']['errors']==['not_ready']
    assert not adapter.calls


@pytest.mark.parametrize('value',[['fact',7],7,False,None])
def test_duplicate_scalar_and_list_callback_data_matches_direct_json_output(observe,value):
    body={'observation':value}
    adapter=ObservationAdapter('isolated_session',callbacks=(body,body),output=json.dumps(body))
    result,_=observe(adapter)
    assert result['status']=='completed' and result['data']==body
    assert all(item.error is None for item in adapter.effects)


@pytest.mark.parametrize('content',['done','{"content":"done"}','{"observation":',{'content':'done'}])
def test_valid_callback_with_ordinary_final_content_uses_the_original_observation(observe,content):
    adapter=ObservationAdapter('isolated_session',callbacks=({'observation':[1,2]},),output=content)
    result,_=observe(adapter)
    assert result['status']=='completed' and result['data']=={'observation':[1,2]}


@pytest.mark.parametrize('output',[
    'ordinary prose',{'approved':True},'NaN','{"observation":1,"observation":2}',
    {'observation':'x'*(128*1024)},
])
def test_text_invalid_schema_nonjson_and_oversized_outputs_cannot_become_observation_data(observe,output):
    adapter=ObservationAdapter('isolated_session',output=output)
    result,_=observe(adapter)
    assert result['status']=='unknown' and result['data']['errors']==['invalid_input']


@pytest.mark.parametrize('callback_conflict',[False,True])
def test_conflicting_callback_or_direct_output_is_not_accepted_as_the_original_observation(observe,callback_conflict):
    callbacks=({'observation':[1]}, {'observation':[2]}) if callback_conflict else ({'observation':[1]},)
    adapter=ObservationAdapter('isolated_session',callbacks=callbacks,output={'observation':[1] if callback_conflict else [2]})
    result,_=observe(adapter)
    assert result['status']=='unknown' and result['data']['errors']==['id_conflict']


def test_new_business_session_requires_p28_and_unknown_creation_never_dispatches_a_replacement(observe):
    missing=ObservationAdapter('business_session',skip_ack=True,output={'observation':True})
    result,registry=observe(missing,parent_sid=None)
    assert result['status']=='unknown' and registry.context('actor','business')['sessionId'] is None
    disconnected=ObservationAdapter('business_session',disconnect=True)
    result,registry=observe(disconnected,parent_sid=None,repeat_on_error=True)
    assert result['status']=='unknown' and len(disconnected.calls)==1
    assert registry.context('actor','business')['sessionId']=='real-business-created'


@pytest.mark.parametrize('source',['output','callback'])
def test_completed_round_with_submission_error_never_becomes_an_observation(observe,source):
    body={'observation':{'fact':42}}
    adapter=ObservationAdapter('isolated_session',output=body if source=='output' else None,
        callbacks=(body,) if source=='callback' else (),
        error=SubmissionError('native_guard_failed','native_definition','accepted','Original round guard failed'))
    result,_=observe(adapter)
    assert result['status']=='unknown' and result['data']['errors']==['native_guard_failed']
    assert len(adapter.calls)==1
