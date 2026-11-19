from ioalab_sdk.runtime import SubmissionQuery
"""S01 boundary against the external Process SDK; no native/model acceptance."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import pytest
from ioalab_jiuwen import ProcessHost
from ioalab_sdk.runtime import (BindingAck, HostDefinition, HostExecution, HostInput,
    HostToolDeclaration, HostToolResult, SubmissionAttempt)


def scope(tmp_path,**changes):
    value=HostExecution('actor','profile','business','interaction','job',
        HostDefinition('definition','1','digest','Only this frozen definition'),workspace=str(tmp_path))
    return replace(value,**changes)


def payload():return HostInput('data',attempt=SubmissionAttempt('attempt','scope-digest','actor','profile'))


def ack(event):return BindingAck(event.result.session_id,event.result.request_id)


def test_original_machine_result_and_exit_contract_is_required(tmp_path,monkeypatch):
    requests=[]
    class Process:
        async def run(self,request,**callbacks):
            requests.append(request)
            record={'type':'result','request_id':request['request_id'],'session_id':'real-session','status':'completed','exit_code':0}
            await callbacks['on_event']({**record,'type':'event'})
            return record
    host=ProcessHost(['original-process'],tmp_path)
    monkeypatch.setattr(host,'_client',lambda **_:Process())
    result=host.submit('op',payload(),execution=scope(tmp_path/'work'),on_event=ack)
    assert result.terminal and result.state=='completed' and result.session_id=='real-session'
    assert host.get_request(SubmissionQuery(result.operation_id,result.attempt_id,result.session_id,result.request_id))==result
    assert host.submit('op',payload(),execution=scope(tmp_path/'work'),on_event=ack)==result
    assert len(requests)==1


def test_binding_ack_must_match_both_real_session_and_original_request(tmp_path,monkeypatch):
    called=[]
    class Process:
        async def run(self,request,**callbacks):
            name=request['host_tools'][0]['name']
            await callbacks['on_tool_call']({'request_id':request['request_id'],'session_id':'real-session',
                'payload':{'name':name,'arguments':{},'call_id':'real-call'}})
    host=ProcessHost(['process'],tmp_path)
    monkeypatch.setattr(host,'_client',lambda **_:Process())
    result=host.submit('op',payload(),execution=scope(tmp_path/'work',tools=(HostToolDeclaration('sdk.action','Action',{'type':'object'}),)),
        on_event=lambda event:BindingAck(event.result.session_id,'different-request'),
        on_tool=lambda call:called.append(call) or HostToolResult({'ok':True}))
    assert result.state=='unknown' and not called and result.terminal is None


@pytest.mark.parametrize('deadline',[None,900])
def test_original_deadline_has_no_extra_300_second_business_cap(tmp_path,monkeypatch,deadline):
    requests=[]
    class Process:
        async def run(self,request,**callbacks):
            requests.append(request)
            return {'type':'result','request_id':request['request_id'],'session_id':'sid','status':'completed','exit_code':0}
    host=ProcessHost(['process'],tmp_path)
    monkeypatch.setattr(host,'_client',lambda **_:Process())
    expires=(datetime.now(timezone.utc)+timedelta(seconds=deadline)).isoformat() if deadline else None
    host.submit('op',payload(),execution=scope(tmp_path/'work',deadline=expires),on_event=ack)
    assert requests[0]['timeout_seconds'] is None if deadline is None else requests[0]['timeout_seconds']>800


def test_stream_final_or_model_ack_is_not_a_machine_terminal(tmp_path,monkeypatch):
    class Process:
        async def run(self,request,**callbacks):
            return {'type':'event','event_type':'chat.final','request_id':request['request_id'],'session_id':'sid','status':'completed'}
    host=ProcessHost(['process'],tmp_path)
    monkeypatch.setattr(host,'_client',lambda **_:Process())
    result=host.submit('op',payload(),execution=scope(tmp_path/'work'),on_event=ack)
    assert result.state=='unknown' and result.terminal is None


def test_machine_terminal_survives_late_sdk_event_failure(tmp_path,monkeypatch):
    class Process:
        async def run(self,request,**callbacks):
            return {'type':'result','request_id':request['request_id'],'session_id':'sid','status':'completed','exit_code':0}
    host=ProcessHost(['process'],tmp_path)
    monkeypatch.setattr(host,'_client',lambda **_:Process())
    def events(event):
        if event.kind=='terminal':raise OSError('late event transport failure')
        return ack(event)
    result=host.submit('op',payload(),execution=scope(tmp_path/'work'),on_event=events)
    assert result.state=='completed' and result.terminal and result.error.code=='event_reporting_unknown'


def test_no_protocol_echo_keeps_planned_request_private_and_never_resubmits(tmp_path,monkeypatch):
    requests=[];events=[]
    host=ProcessHost(['process'],tmp_path)
    class Process:
        async def run(self,request,**callbacks):
            requests.append(request)
            assert host.get_request(SubmissionQuery('op','attempt',None,request['request_id'])) is None
            raise OSError('Connection lost without a protocol echo')
    monkeypatch.setattr(host,'_client',lambda **_:Process())
    result=host.submit('op',payload(),execution=scope(tmp_path/'work'),on_event=lambda event:events.append(event))
    assert result.state=='unknown' and result.request_id is None and result.terminal is None
    assert result.error.dispatch=='unknown' and not events
    assert host.submit('op',payload(),execution=scope(tmp_path/'work'))==result
    assert len(requests)==1
    journal=json.loads(next((tmp_path/'work/runs').glob('submission-*.json')).read_text())
    assert journal['plannedRequestId']==requests[0]['request_id']
    assert journal['result']['request_id'] is None
    # Restart and a new SDK attempt cannot turn an unverified send into a retry.
    from ioalab_sdk.runtime import RuntimeFault
    restarted=ProcessHost(['process'],tmp_path)
    monkeypatch.setattr(restarted,'_client',lambda **_:pytest.fail('Unknown request was replayed'))
    with pytest.raises(RuntimeFault,match='never resubmit unknown'):
        restarted.submit('op',replace(payload(),attempt=replace(payload().attempt,nonce='new-attempt')),
                         execution=scope(tmp_path/'work'))


def test_validation_before_native_dispatch_has_no_public_request_or_tool_effect(tmp_path,monkeypatch):
    host=ProcessHost(['process'],tmp_path)
    monkeypatch.setattr(host,'_client',lambda **_:pytest.fail('Validation started a Process'))
    duplicate=HostToolDeclaration('duplicate','Same name',{'type':'object'})
    result=host.submit('op',payload(),execution=scope(tmp_path/'work',tools=(duplicate,duplicate)),
                       on_event=ack,on_tool=lambda _:pytest.fail('Validation ran an SDK effect'))
    assert result.error.code=='invalid_input' and result.error.dispatch=='not_submitted'
    assert result.request_id is None and result.terminal is None


def test_exact_protocol_echo_then_disconnect_retains_only_original_actual_request(tmp_path,monkeypatch):
    requests=[];bindings=[]
    host=ProcessHost(['process'],tmp_path)
    class Process:
        async def run(self,request,**callbacks):
            requests.append(request)
            await callbacks['on_event']({'type':'event','event_type':'session.created',
                'request_id':request['request_id'],'session_id':'actual-session'})
            raise OSError('Connection lost after binding')
    monkeypatch.setattr(host,'_client',lambda **_:Process())
    def events(event):
        bindings.append((event.result.session_id,event.result.request_id))
        return ack(event)
    result=host.submit('op',payload(),execution=scope(tmp_path/'work'),on_event=events)
    assert result.state=='unknown' and result.request_id==requests[0]['request_id']
    assert result.session_id=='actual-session' and result.terminal is None
    assert bindings==[('actual-session',requests[0]['request_id'])]
    assert host.get_request(SubmissionQuery(result.operation_id,result.attempt_id,result.session_id,result.request_id))==result
    assert host.submit('op',payload(),execution=scope(tmp_path/'work'))==result and len(requests)==1


def test_same_session_with_different_request_echo_cannot_establish_binding(tmp_path,monkeypatch):
    host=ProcessHost(['process'],tmp_path)
    class Process:
        async def run(self,request,**callbacks):
            await callbacks['on_event']({'request_id':'another-request','session_id':'original-session'})
    monkeypatch.setattr(host,'_client',lambda **_:Process())
    result=host.submit('op',payload(),'original-session',execution=scope(tmp_path/'work'),
                      on_event=lambda _:pytest.fail('Wrong request reached binding acknowledgement'))
    assert result.state=='unknown' and result.request_id is None and result.terminal is None


@pytest.mark.parametrize('kind,enabled',[('business',False),('business',True),('business',None),('observation',False)])
def test_native_tool_phase_is_recorded_but_never_restricts_configured_tools(tmp_path,monkeypatch,kind,enabled):
    # No native_tool_restriction is declared (D37): every phase keeps the
    # owner-configured levels; the frozen boolean is only recorded.
    from ioalab_jiuwen.submission import Submission
    assert Submission.registration.native_tool_restriction is False
    class Process:
        async def run(self,request,**callbacks):
            assert request['permissions']['tools']=={'ioa_propose_result':'deny'}
            assert request['agent']['tools']==['*']
            return {'type':'result','request_id':request['request_id'],'session_id':'sid','status':'completed','exit_code':0}
    host=ProcessHost(['process'],tmp_path)
    monkeypatch.setattr(host,'_client',lambda **_:Process())
    authorization={'nativeTools':{'write_file':'allow','read_file':'ask'}}
    if enabled is not None:authorization['nativeToolsEnabled']=enabled
    result=host.submit('op',payload(),execution=scope(tmp_path/'work',kind=kind,authorization=authorization),on_event=ack)
    assert result.state=='completed' and result.terminal is not None and result.error is None
    record=next(path for path in (tmp_path/'work'/'runs').glob('ioa-*.json'))
    assert json.loads(record.read_text())['nativeToolsEnabled'] is (enabled is True)


PROPOSAL={'type':'object','properties':{'reply':{'type':'string'}},'required':['reply']}


def test_proposal_round_can_use_same_effect_sdk_tool_without_optional_proposal(tmp_path,monkeypatch):
    calls=[]
    class Process:
        async def run(self,request,**callbacks):
            assert [tool['name'] for tool in request['host_tools']]==['ioa_reply','ioa_propose_result']
            await callbacks['on_event']({'type':'event','request_id':request['request_id'],'session_id':'sid'})
            await callbacks['on_tool_call']({'request_id':request['request_id'],'session_id':'sid',
                'payload':{'name':'ioa_reply','arguments':{'reply':'hello'},'call_id':'call'}})
            return {'type':'result','request_id':request['request_id'],'session_id':'sid','status':'completed','exit_code':0}
    host=ProcessHost(['process'],tmp_path)
    monkeypatch.setattr(host,'_client',lambda **_:Process())
    def tool(call):calls.append((call.name,call.arguments));return HostToolResult({'accepted':True})
    execution=scope(tmp_path/'work',proposal_schema=PROPOSAL,tools=(HostToolDeclaration('ioa_reply','Reply once',PROPOSAL),))
    result=host.submit('op',payload(),execution=execution,on_event=ack,on_tool=tool)
    assert result.state=='completed' and result.terminal is not None and result.proposal is None
    assert calls==[('ioa_reply',{'reply':'hello'})]


def test_proposal_collector_is_available_without_effect_tools(tmp_path,monkeypatch):
    class Process:
        async def run(self,request,**callbacks):
            reply=await callbacks['on_tool_call']({'request_id':request['request_id'],'session_id':'sid',
                'payload':{'name':'ioa_propose_result','arguments':{'value':{'reply':'hello'}},'call_id':'proposal'}})
            assert reply=={'status':'recorded'}
            return {'type':'result','request_id':request['request_id'],'session_id':'sid','status':'completed','exit_code':0}
    host=ProcessHost(['process'],tmp_path)
    monkeypatch.setattr(host,'_client',lambda **_:Process())
    result=host.submit('op',payload(),execution=scope(tmp_path/'work',proposal_schema=PROPOSAL),on_event=ack)
    assert result.state=='completed' and result.proposal=={'reply':'hello'}


def test_native_ask_without_trusted_answer_channel_is_never_answered(tmp_path,monkeypatch):
    answered=[]
    class Process:
        async def run(self,request,**callbacks):
            assert request['agent']['tools']==['*']
            await callbacks['on_event']({'type':'event','request_id':request['request_id'],'session_id':'sid'})
            record={'request_id':request['request_id'],'session_id':'sid','event_type':'interaction.requested',
                    'payload':{'interaction_id':'ask','interaction':{'source':'permission_interrupt','questions':[]}}}
            answered.append(await callbacks['on_interaction'](record))
            return {'type':'result','request_id':request['request_id'],'session_id':'sid','status':'completed','exit_code':0}
    host=ProcessHost(['process'],tmp_path)
    monkeypatch.setattr(host,'_client',lambda **_:Process())
    result=host.submit('op',payload(),execution=scope(tmp_path/'work'),on_event=ack)
    assert not answered and result.terminal is None and result.state=='unknown'
    assert result.error.code=='native_interaction_unanswered' and result.error.dispatch=='unknown'


def test_explicit_deadline_bounds_unfinished_process_and_keeps_original_request(tmp_path,monkeypatch):
    import asyncio
    calls=[]
    class Process:
        async def run(self,request,**callbacks):
            calls.append(request)
            await callbacks['on_event']({'session_id':'original-timeout-session','request_id':request['request_id']})
            await asyncio.Event().wait()
    host=ProcessHost(['process'],tmp_path)
    monkeypatch.setattr(host,'_client',lambda **_:Process())
    expires=(datetime.now(timezone.utc)+timedelta(seconds=.1)).isoformat()
    result=host.submit('bounded',payload(),execution=scope(tmp_path/'work',deadline=expires),on_event=ack)
    assert result.state=='unknown' and result.terminal is None
    assert result.session_id=='original-timeout-session' and result.request_id==calls[0]['request_id']
    assert result.error.code=='host_timeout'
    assert host.get_request(SubmissionQuery(result.operation_id,result.attempt_id,result.session_id,result.request_id))==result and len(calls)==1


@pytest.mark.parametrize('terminal,structured,valid', [
    ('completed', {'files': {}}, True), ('completed', {'wrong': True}, False),
    ('failed', {'files': {}}, False), ('event', {'files': {}}, False),
    ('completed', None, False),
])
def test_process_proposal_uses_schema_and_only_original_valid_success(tmp_path,monkeypatch,terminal,structured,valid):
    schema={'type':'object','properties':{'files':{'type':'object'}},'required':['files'],'additionalProperties':False}
    calls=[]
    class Process:
        async def run(self,request,**callbacks):
            calls.append(request)
            assert 'output_schema' not in request
            proposal=request['host_tools'][0]
            assert proposal['input_schema']['properties']['value']=={'type':'object'}
            assert 'Optional proposal schema for this turn' in request['input']
            if structured is not None:
                receipt=await callbacks['on_tool_call']({'request_id':request['request_id'],'session_id':'sid',
                    'payload':{'name':proposal['name'],'arguments':{'value':structured},'call_id':'proposal-call'}})
                assert receipt['status']==('recorded' if structured=={'files':{}} else 'rejected')
            return {'type':'event' if terminal=='event' else 'result','request_id':request['request_id'],
                    'session_id':'sid','status':'failed' if terminal=='failed' else 'completed',
                    'exit_code':1 if terminal=='failed' else 0,'output_json':structured,
                    'output':'{"files":{}}'}
    host=ProcessHost(['process'],tmp_path)
    monkeypatch.setattr(host,'_client',lambda **_:Process())
    execution=scope(tmp_path/'work',proposal_schema=schema)
    result=host.submit('proposal',payload(),execution=execution,on_event=ack)
    assert len(calls)==1
    assert result.proposal==({'files':{}} if valid else None)
    if terminal=='completed' and structured is not None and not valid:
        assert result.terminal and result.error is None
    assert host.submit('proposal',payload(),execution=execution,on_event=ack)==result
    assert len(calls)==1


def test_invalid_proposal_schema_rejects_before_process_dispatch(tmp_path,monkeypatch):
    host=ProcessHost(['process'],tmp_path)
    monkeypatch.setattr(host,'_client',lambda **_:pytest.fail('Invalid schema launched Process'))
    result=host.submit('proposal',payload(),execution=scope(tmp_path/'work',proposal_schema={'$ref':'https://invalid.example/schema'}))
    assert result.error.code=='invalid_proposal_schema' and result.error.dispatch=='not_submitted'


def test_optional_proposal_does_not_force_json_after_sdk_tool_receipt(tmp_path,monkeypatch):
    calls=[]
    class Process:
        async def run(self,request,**callbacks):
            assert 'output_schema' not in request
            record={'type':'result','request_id':request['request_id'],'session_id':'sid','status':'completed','exit_code':0}
            name=request['host_tools'][0]['name']
            receipt=await callbacks['on_tool_call']({**record,'payload':{'name':name,'arguments':{},'call_id':'effect'}})
            assert receipt=={'status':'recorded'}
            return {**record,'output':'Business tool completed; normal summary.'}
    host=ProcessHost(['process'],tmp_path);monkeypatch.setattr(host,'_client',lambda **_:Process())
    execution=scope(tmp_path/'work',tools=(HostToolDeclaration('sdk.effect','Authorized effect',{'type':'object'}),),proposal_schema={'type':'object'})
    result=host.submit('tool-only',payload(),execution=execution,on_event=ack,
        on_tool=lambda call:calls.append(call) or HostToolResult({'status':'recorded'}))
    assert result.state=='completed' and result.error is None and result.proposal is None and result.terminal
    assert len(calls)==1


def test_optional_proposal_keeps_same_native_definition_across_phases(tmp_path,monkeypatch):
    requests=[]
    class Process:
        async def run(self,request,**callbacks):
            requests.append(request)
            if len(requests)>1:
                assert request['agent']==requests[0]['agent']
                assert request['host_tools']==requests[0]['host_tools']
            return {'type':'result','request_id':request['request_id'],'session_id':'original-session','status':'completed','exit_code':0,'output':'done'}
    host=ProcessHost(['process'],tmp_path);monkeypatch.setattr(host,'_client',lambda **_:Process())
    for index,schema in enumerate((None,{'type':'object','properties':{'files':{'type':'object'}}},None)):
        result=host.submit('phase'+str(index),payload(),session_id='original-session' if index else None,
            execution=scope(tmp_path/'work',proposal_schema=schema),on_event=ack)
        assert result.state=='completed' and result.error is None and result.terminal
    name=requests[0]['host_tools'][0]['name']
    assert [r['permissions']['tools'][name] for r in requests]==['deny','allow','deny']
