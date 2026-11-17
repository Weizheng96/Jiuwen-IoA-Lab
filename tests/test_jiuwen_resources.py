"""Process layout uses granted bytes; the Process boundary is a fixture."""
from dataclasses import replace
import json
from pathlib import Path
import pytest
from a2c.codec import digest_bytes
from ioalab_jiuwen import ProcessHost
from ioalab_sdk.runtime import (BindingAck,HostDefinition,HostExecution,HostInput,HostResource,
    HostToolDeclaration,HostToolResult,RuntimeFault,SubmissionAttempt)


def execution(workspace,resources=(),tools=()):
    return HostExecution('actor','profile','business','interaction','job',
        HostDefinition('frozen','1','definition-digest','Frozen instructions',resources),
        workspace=str(workspace),tools=tools)


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
    assert result.error.dispatch=='not_submitted' and result.request_id is None and result.terminal is None


def test_callback_resource_failure_preserves_actual_effect_receipt_and_machine_terminal(tmp_path,monkeypatch):
    ref=HostResource('out','result','text/plain',digest_bytes(b'result'),'scope','sdk-output:out')
    effects=[]
    def reader(actual):raise RuntimeFault('not_ready','Recover original output authorization')
    class Process:
        async def run(self,request,**callbacks):
            name=request['host_tools'][0]['name']
            reply=await callbacks['on_tool_call']({'request_id':request['request_id'],'session_id':'sid',
                'payload':{'name':name,'arguments':{},'call_id':'actual-call'}})
            assert reply['value']=={'receipt':'actual-effect'} and reply['error']['phase']=='callback_resources'
            return {'type':'result','request_id':request['request_id'],'session_id':'sid','status':'completed','exit_code':0}
    host=ProcessHost(['process'],tmp_path,resource_factory=lambda op,attempt:(reader,lambda *_:None))
    monkeypatch.setattr(host,'_client',lambda **_:Process())
    def effect(call):
        effects.append(call.call_id)
        return HostToolResult({'receipt':'actual-effect'},(ref,))
    result=host.submit('op',HostInput('data',attempt=SubmissionAttempt('nonce','scope-digest','actor','profile')),
        execution=execution(tmp_path/'work',tools=(HostToolDeclaration('produce','Produce',{'type':'object'}),)),
        on_event=lambda event:BindingAck(event.result.session_id,event.result.request_id),on_tool=effect)
    assert result.terminal is not None and result.state=='completed' and result.error.phase=='callback_resources'
    assert effects==['actual-call']
