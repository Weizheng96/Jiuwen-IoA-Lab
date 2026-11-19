"""Process layout uses granted bytes; the Process boundary is a fixture."""
import asyncio
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from a2c.codec import digest_bytes
from ioalab_jiuwen import ProcessHost
from ioalab_sdk.runtime import (BindingAck,HostDefinition,HostExecution,HostInput,HostRegistry,HostResource,
    HostToolDeclaration,HostToolResult,RuntimeFault,RuntimeStore,SubmissionAttempt,SubmissionResourcePort)
from ioalab_sdk.runtime.sdk_tool_callbacks import CallbackPool


def execution(workspace,resources=(),tools=()):
    return HostExecution('actor','profile','business','interaction','job',
        HostDefinition('frozen','1','definition-digest','Frozen instructions',resources),
        workspace=str(workspace),tools=tools)


def test_regular_provider_rounds_submit_without_a_resource_scope(tmp_path,monkeypatch):
    phases=('quote','decision','production')
    tool_names={'quote':'provider.task.quote','decision':'provider.task.decision','production':'provider.task.deliver'}
    workspace=tmp_path/'work'
    port=SubmissionResourcePort()
    resource_factory=port.for_host('process')
    factory_attempts=[]
    requests=[]
    effects=[]

    def factory(operation,attempt):
        factory_attempts.append(operation)
        return resource_factory(operation,attempt)

    class Process:
        async def run(self,request,**callbacks):
            requests.append(request)
            phase=phases[len(requests)-1]
            assert request['input'].split('\n',1)[0]==phase
            if phase=='quote':
                assert 'session_id' not in request
            else:
                assert request['session_id']=='sid'
            reply=await callbacks['on_tool_call']({'request_id':request['request_id'],'session_id':'sid',
                'payload':{'name':request['host_tools'][0]['name'],'arguments':{},'call_id':phase}})
            assert reply=={'recorded':phase}
            return {'type':'result','request_id':request['request_id'],'session_id':'sid','status':'completed','exit_code':0}

    host=ProcessHost(['process'],tmp_path,resource_factory=factory)
    monkeypatch.setattr(host,'_client',lambda **_:Process())
    store=RuntimeStore(tmp_path/'runtime.sqlite','http://local',['actor'],owner_id='owner')
    pool=CallbackPool(1)
    registry=HostRegistry({'process':host},'process')
    registry.bind(store,identities=[SimpleNamespace(agent_id='actor',profile='profile')])
    registry.callback_pool=pool
    port.bind(registry._resources_for)

    def effect(call):
        assert registry.context('actor','interaction')['sessionId']==call.session_id=='sid'
        assert call.name==tool_names[call.call_id]
        effects.append(call.call_id)
        return {'recorded':call.call_id}

    try:
        for phase in phases:
            schema={'type':'object'}
            definition=execution(workspace,tools=(HostToolDeclaration(tool_names[phase],'Record the outcome',schema),))
            if phase=='production':
                definition=replace(definition,proposal_schema=schema)
            outcome=registry.submit('actor','interaction',phase,HostInput(phase),execution=definition,on_tool=effect)
            assert outcome.state=='completed' and outcome.terminal is not None,outcome
    finally:
        pool.close()
        store.close()
    assert not factory_attempts
    assert effects==list(phases)
    assert not (workspace/'.jiuwen-resources').exists()


def test_exact_reader_bytes_are_present_in_native_turn_without_changing_definition(tmp_path,monkeypatch):
    data='原专业文档'.encode();ref=HostResource('r','skill/SKILL.md','text/markdown',digest_bytes(data),'scope','sdk-definition:r')
    attempts=[];requests=[]
    def factory(op,attempt):
        attempts.append((op,attempt))
        def reader(actual):
            assert actual==ref
            return data
        return reader,lambda *_:pytest.fail('Input mapping invoked output storage')
    class Process:
        async def run(self,request,**callbacks):
            requests.append(request)
            mappings=json.loads(request['input'].split('SDK authorized resource mappings (data): ')[1])
            assert mappings[0]['resource_id']=='r' and mappings[0]['digest']==ref.digest
            assert Path(mappings[0]['path']).read_bytes()==data
            assert request['agent']['instructions']=='Frozen instructions'
            return {'type':'result','request_id':request['request_id'],'session_id':'sid','status':'completed','exit_code':0}
    host=ProcessHost(['process'],tmp_path,resource_factory=factory)
    monkeypatch.setattr(host,'_client',lambda **_:Process())
    attempt=SubmissionAttempt('nonce','scope-digest','actor','profile')
    result=host.submit('op',HostInput('data',attempt=attempt),execution=execution(tmp_path/'work',(ref,)),
        on_event=lambda event:BindingAck(event.result.session_id,event.result.request_id))
    assert result.terminal is not None and attempts==[('op',attempt)] and len(requests)==1


@pytest.mark.parametrize('failure',['authorization','digest'])
def test_reader_rejection_or_wrong_bytes_never_dispatches_native_process(tmp_path,monkeypatch,failure):
    ref=HostResource('r','material','text/plain',digest_bytes(b'original'),'scope','sdk-material:r')
    def reader(actual):
        if failure=='authorization':raise RuntimeFault('forbidden','Wrong original business scope')
        return b'changed'
    host=ProcessHost(['process'],tmp_path,resource_factory=lambda op,attempt:(reader,lambda *_:None))
    monkeypatch.setattr(host,'_client',lambda **_:pytest.fail('Invalid resource started a Process'))
    result=host.submit('op',HostInput('data',(ref,),SubmissionAttempt('nonce','scope-digest','actor','profile')),
                       execution=execution(tmp_path/'work'))
    assert (result.error.code,result.error.phase,result.error.dispatch)==(
        'forbidden' if failure=='authorization' else 'identity_conflict','resources','not_submitted')
    assert result.request_id is None and result.terminal is None


@pytest.mark.parametrize('with_input_resource',[False,True])
def test_callback_resources_map_on_demand_and_share_the_original_attempt(tmp_path,monkeypatch,with_input_resource):
    material=HostResource('in','material','text/plain',digest_bytes(b'input'),'scope','sdk-material:in')
    output=HostResource('out','result','text/plain',digest_bytes(b'output'),'scope','sdk-output:out')
    attempt=SubmissionAttempt('nonce','scope-digest','actor','profile')
    factory_calls=[]
    effects=[]

    def factory(operation,original_attempt):
        factory_calls.append((operation,original_attempt))
        values={material:b'input',output:b'output'}
        return values.__getitem__,lambda *_:pytest.fail('Mapping invoked output storage')

    class Process:
        async def run(self,request,**callbacks):
            if with_input_resource:
                mappings=json.loads(request['input'].split('SDK authorized resource mappings (data): ')[1])
                assert Path(mappings[0]['path']).read_bytes()==b'input'
                assert factory_calls==[('op',attempt)]
            else:
                assert request['input']=='data' and not factory_calls
            await callbacks['on_event']({'type':'event','request_id':request['request_id'],'session_id':'sid'})
            async def call(call_id):
                return await callbacks['on_tool_call']({'request_id':request['request_id'],'session_id':'sid',
                    'payload':{'name':request['host_tools'][0]['name'],'arguments':{},'call_id':call_id}})
            replies=await asyncio.gather(call('first'),call('second'))
            for call_id,reply in zip(('first','second'),replies):
                assert reply['value']=={'receipt':call_id} and reply['error'] is None
                assert Path(reply['resources'][0]['path']).read_bytes()==b'output'
            assert replies[0]['resources']==replies[1]['resources']
            return {'type':'result','request_id':request['request_id'],'session_id':'sid','status':'completed','exit_code':0}

    host=ProcessHost(['process'],tmp_path,resource_factory=factory)
    monkeypatch.setattr(host,'_client',lambda **_:Process())

    def effect(call):
        effects.append(call.call_id)
        return HostToolResult({'receipt':call.call_id},(output,))

    result=host.submit('op',HostInput('data',(material,) if with_input_resource else (),attempt),
        execution=execution(tmp_path/'work',tools=(HostToolDeclaration('produce','Produce',{'type':'object'}),)),
        on_event=lambda event:BindingAck(event.result.session_id,event.result.request_id),on_tool=effect)
    assert result.state=='completed' and result.terminal is not None and result.error is None,result
    assert factory_calls==[('op',attempt)] and sorted(effects)==['first','second']


@pytest.mark.parametrize('failure',['no_factory','no_scope','reader'])
def test_callback_resource_failure_preserves_actual_effect_receipt_and_machine_terminal(tmp_path,monkeypatch,failure):
    ref=HostResource('out','result','text/plain',digest_bytes(b'result'),'scope','sdk-output:out')
    effects=[]
    def reader(actual):raise RuntimeFault('forbidden','Wrong original output scope')
    def factory(operation,attempt):
        if failure=='no_scope':
            raise RuntimeFault('not_ready','Recover original output authorization')
        return reader,lambda *_:None
    class Process:
        async def run(self,request,**callbacks):
            name=request['host_tools'][0]['name']
            reply=await callbacks['on_tool_call']({'request_id':request['request_id'],'session_id':'sid',
                'payload':{'name':name,'arguments':{},'call_id':'actual-call'}})
            assert reply['value']=={'receipt':'actual-effect'} and reply['error']['phase']=='callback_resources'
            return {'type':'result','request_id':request['request_id'],'session_id':'sid','status':'completed','exit_code':0}
    host=ProcessHost(['process'],tmp_path,resource_factory=None if failure=='no_factory' else factory)
    monkeypatch.setattr(host,'_client',lambda **_:Process())
    def effect(call):
        effects.append(call.call_id)
        return HostToolResult({'receipt':'actual-effect'},(ref,))
    result=host.submit('op',HostInput('data',attempt=SubmissionAttempt('nonce','scope-digest','actor','profile')),
        execution=execution(tmp_path/'work',tools=(HostToolDeclaration('produce','Produce',{'type':'object'}),)),
        on_event=lambda event:BindingAck(event.result.session_id,event.result.request_id),on_tool=effect)
    assert result.terminal is not None and result.state=='completed',result
    assert (result.error.code,result.error.phase,result.error.dispatch)==(
        'forbidden' if failure=='reader' else 'not_ready','callback_resources','accepted')
    assert effects==['actual-call']


def test_text_only_submission_does_not_require_resource_scope(tmp_path,monkeypatch):
    requests=[]
    def factory(*_):raise RuntimeFault('not_ready','No resource authorization for this text-only round')
    class Process:
        async def run(self,request,**callbacks):
            requests.append(request)
            return {'type':'result','request_id':request['request_id'],'session_id':'sid','status':'completed','exit_code':0}
    host=ProcessHost(['process'],tmp_path,resource_factory=factory)
    monkeypatch.setattr(host,'_client',lambda **_:Process())
    result=host.submit('op',HostInput('quote data',attempt=SubmissionAttempt('nonce','scope-digest','actor','profile')),
        execution=execution(tmp_path/'work'),on_event=lambda event:BindingAck(event.result.session_id,event.result.request_id))
    assert result.state=='completed' and result.terminal and len(requests)==1


@pytest.mark.parametrize('restart',[False,True])
def test_proven_unsubmitted_resource_failure_can_retry_original_operation(tmp_path,monkeypatch,restart):
    from dataclasses import replace
    data=b'authorized';ref=HostResource('r','material','text/plain',digest_bytes(data),'scope','sdk-material:r')
    available=[False];requests=[]
    def factory(*_):
        if not available[0]:raise RuntimeFault('not_ready','Original resource not ready')
        return lambda _:data,lambda *_:None
    class Process:
        async def run(self,request,**callbacks):
            requests.append(request)
            return {'type':'result','request_id':request['request_id'],'session_id':'sid','status':'completed','exit_code':0}
    def host():
        value=ProcessHost(['process'],tmp_path,resource_factory=factory)
        monkeypatch.setattr(value,'_client',lambda **_:Process())
        return value
    current=host();original=HostInput('data',(ref,),SubmissionAttempt('first','scope-digest','actor','profile'))
    args={'execution':execution(tmp_path/'work'),'on_event':lambda event:BindingAck(event.result.session_id,event.result.request_id)}
    rejected=current.submit('op',original,**args)
    assert rejected.error.dispatch=='not_submitted' and not requests
    available[0]=True
    if restart:current=host()
    retry=replace(original,attempt=replace(original.attempt,nonce='retry'))
    result=current.submit('op',retry,**args)
    assert result.state=='completed' and result.attempt_id=='retry' and len(requests)==1
    assert current.submit('op',retry,**args)==result and len(requests)==1
