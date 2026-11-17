"""Reply proposals use the original real SDK reply owner; only native S01 is scripted."""
from dataclasses import replace

import pytest

from conftest import wait_for
from ioalab_sdk.runtime import SubmissionError,RuntimeFault,proposal_tool_declared
from ioalab_sdk.runtime.host import ToolBinding
from submission_fixture import FixtureResult,ScriptedSubmission
from test_assistant_role import INPUT,OUTPUT,publish,runtime_at,send_and_read


class ProposalHost(ScriptedSubmission):
    def __init__(self,mode):self.mode,self.turns,self.receipts,self.schemas=mode,[],[],[]

    def execute(self,turn):
        self.turns.append(turn)
        assert turn.allow_native_tools is True
        turn.on_session(turn.session_id or 'original-proposal-session')
        if self.mode in ('tool','tool_only','conflict','tool_error'):
            self.receipts.append(turn.on_tool('ioa_reply',{'output':{'status':'recorded'}},'original-tool-call'))
        return FixtureResult('unknown' if self.mode=='unknown' else 'completed',turn.session_id or 'original-proposal-session',{})

    def submit(self,op,input,session_id=None,*,execution=None,on_event=None,on_tool=None):
        self.schemas.append(execution.proposal_schema)
        result=super().submit(op,input,session_id,execution=execution,on_event=on_event,on_tool=on_tool)
        # D35: an adapter without a proposal channel submits anyway; the same-key tool carries the reply.
        if self.mode=='tool_only':
            assert proposal_tool_declared(execution)
            return result
        proposal={'output':{'status':'conflict' if self.mode=='conflict' else 'recorded'}}
        if self.mode=='invalid':proposal={'output':{'status':12}}
        if self.mode=='ordinary':return replace(result,output='I replied successfully')
        error=SubmissionError('resource_unavailable','output','accepted','Original native output cannot be collected') if self.mode in ('error','tool_error') else None
        return replace(result,proposal=proposal,error=error)


@pytest.mark.parametrize('mode',['proposal','tool','tool_only'])
def test_reply_proposal_and_tool_share_original_message_receipt(platform,tmp_path,mode):
    host=ProposalHost(mode)
    tool=ToolBinding('reserve_slot','Unused authorized business tool',INPUT,lambda *_:pytest.fail('Unexpected business tool'),output_schema=OUTPUT)
    with runtime_at(platform,tmp_path/'sender') as (_,sender,_,user):
        with runtime_at(platform,tmp_path/'receiver',host,[tool]) as (receiver,inbox,_,target):
            publish(receiver,inbox,user['username'])
            interface=sender.call('assistant.messaging.interfaces',{'account':target['username']})['data'][0]['interfaceRef']
            sent,first=send_and_read(sender,{'interfaceRef':interface,'input':{'slot':'first'}})
            assert first['result']['output']=={'status':'recorded'}
            assert len(host.turns)==1
            assert host.schemas==[{'type':'object','properties':{'output':OUTPUT},'required':['output'],'additionalProperties':False}]
            assert sender.call('assistant.messaging.status',{'messageRef':sent['data']['messageRef']})['data']['result']==first['result']
            _,second=send_and_read(sender,{'interactionRef':first['interactionRef'],'input':{'slot':'second'}})
            assert second['result']['output']==first['result']['output']
            assert len(host.turns)==2 and host.turns[1].session_id=='original-proposal-session'
            wait_for(lambda:receiver.scheduler.idle)


@pytest.mark.parametrize('mode',['invalid','unknown','error','ordinary','conflict','tool_error'])
def test_invalid_unknown_or_collection_error_never_creates_alternate_reply(platform,tmp_path,mode):
    host=ProposalHost(mode)
    tool=ToolBinding('reserve_slot','Unused authorized business tool',INPUT,lambda *_:pytest.fail('Unexpected business tool'),output_schema=OUTPUT)
    with runtime_at(platform,tmp_path/'sender') as (_,sender,_,user):
        with runtime_at(platform,tmp_path/'receiver',host,[tool]) as (receiver,inbox,module,target):
            publish(receiver,inbox,user['username'])
            interface=sender.call('assistant.messaging.interfaces',{'account':target['username']})['data'][0]['interfaceRef']
            sent=sender.call('assistant.messaging.send',{'interfaceRef':interface,'input':{'slot':'first'}})
            assert sent['status']=='accepted'
            job=receiver.scheduler.reference(module.identity.agent_id,'assistant.message',sent['data']['messageRef'])
            def settled():
                try:return receiver.scheduler.get(job)['status'] in ('completed','unknown','failed','rejected') and receiver.scheduler.idle
                except RuntimeFault as exc:
                    if exc.code!='not_ready':raise
                    return False
            wait_for(settled)
            result=sender.call('assistant.messaging.status',{'messageRef':sent['data']['messageRef']})['data']
            if mode in ('conflict','tool_error'):
                assert result['result']['output']=={'status':'recorded'}
                assert len(host.receipts)==1
            else:
                assert (result.get('result') or {}).get('outcome')!='completed'
            assert len(host.turns)==(2 if mode=='ordinary' else 1)
            if mode in ('error','tool_error'):
                original=receiver.scheduler.get(job)
                assert original['status']=='unknown' and original['data']['error']['code']=='resource_unavailable'
                assert original['data']['replySubmitted'] is (mode=='tool_error')

            if mode in ('invalid','conflict'):
                rejected=receiver.scheduler.get(job)
                assert rejected['status']=='unknown'
                assert rejected['data']['proposalError']['code']==('id_conflict' if mode=='conflict' else 'invalid_input')
            if mode in ('tool_error','conflict'):
                # The unknown first round still holds its interaction: a second real
                # request is scheduled but stays queued, with no new S01 or effect.
                before=receiver.scheduler.get(job)
                assert before['data']['replyReceipt'] is not None
                later=sender.call('assistant.messaging.send',{'interactionRef':result['interactionRef'],'input':{'slot':'second'}})
                assert later['status']=='accepted'
                queued=receiver.scheduler.reference(module.identity.agent_id,'assistant.message',later['data']['messageRef'])
                def scheduled():
                    try:return receiver.scheduler.get(queued)['status']=='queued'
                    except RuntimeFault as exc:
                        if exc.code!='not_ready':raise
                        return False
                wait_for(scheduled)
                assert sender.call('assistant.messaging.status',{'messageRef':later['data']['messageRef']})['data']['result'] is None
                held=receiver.scheduler.get(job)
                assert held['status']=='unknown'
                field='submissionError' if mode=='tool_error' else 'proposalError'
                assert held['data'][field]['code']==('resource_unavailable' if mode=='tool_error' else 'id_conflict')
                assert held['data']['replyReceipt']==before['data']['replyReceipt']
                assert len(host.turns)==1 and len(host.receipts)==1
