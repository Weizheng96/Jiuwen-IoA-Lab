"""Real local user channel and original contract signatures; explicit host double."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from dataclasses import replace
import json
import os
from pathlib import Path
import time
import uuid

import pytest
from a2c.card import card_digest
from a2c.client import Client
from a2c.slots import verify_card
from ioalab_sdk.runtime.ipc import LocalClient
from ioalab_sdk.runtime.host import HostResult
from ioalab_sdk.runtime.protocol import RuntimeFault
from ioalab_sdk.assistant.contracts import Contracts
from ioalab_sdk.runtime.files import private_json
from test_resident_delegation import participants,request,terminal,uid,QuoteHost
from test_assistant_role import user_decision
from conftest import wait_for


def candidate(assistant,provider,template,task,*,timeout=20):
    started,_=request(assistant,provider,template,task)
    ref=started['data']['quoteRef']
    if started['status']!='completed':wait_for(lambda:terminal(assistant,ref),timeout=timeout)
    result=terminal(assistant,ref)
    assert result['status']=='completed',result
    return result['data']


def status(assistant,reference):
    result=LocalClient(assistant.config.home).call('assistant.delegation.status',{'contractRef':reference})
    return result if result['status'] in ('completed','rejected','failed','unknown') else None


def confirm(assistant,quote,*,approve=True):
    client=LocalClient(assistant.config.home);args={'quoteRef':quote['quoteRef']};operation=uid()
    pending=client.call('assistant.delegation.confirm',args,operation_id=operation)
    assert pending['status']=='waiting',pending
    if not approve:return pending,args,operation
    user_decision(assistant,pending['data']['confirmationRef'])
    accepted=client.call('assistant.delegation.confirm',args,operation_id=operation)
    assert accepted['status']=='accepted',accepted
    return accepted,args,operation


@pytest.mark.parametrize('formation',['proposal','countersign'])
def test_actual_user_confirms_one_exact_card_after_budget_reservation_before_slot_one(platform,tmp_path,monkeypatch,formation):
    with participants(platform,tmp_path,formation=formation,fee=1 if formation=='proposal' else 0) as (provider,assistant,host,template,task):
        quote=candidate(assistant,provider,template,task);client=LocalClient(assistant.config.home)
        assert client.call('assistant.delegation.confirm',{'quoteRef':quote['quoteRef'],'confirmed':True})['status']=='rejected'
        pending,args,operation=confirm(assistant,quote,approve=False);ref=pending['data']['contractRef']
        source=assistant.modules[0].contracts.record(ref)
        assert source['subject']['signatures']['1'] is None and source['phase']=='prepared'
        preview=assistant.confirmations.get(pending['data']['confirmationRef'],detail=True)['binding']['preview']
        assert preview['card']==quote['card']
        authorization=next(value for value in assistant.confirmations.list() if value['stage']=='delegation.authorize')
        wrong=client.call('assistant.delegation.confirm',{**args,'confirmationRef':authorization['reference']},operation_id=operation)
        assert wrong['status']=='rejected' and wrong['error']['code']=='id_conflict'
        assert not client.call('assistant.delegation.status',{'taskRef':task})['data']['authorization']['selectedCardDigest']
        if formation=='countersign':assert not platform.db.rows('SELECT * FROM delegation_admissions WHERE service=?',provider.identities[0].agent_id)
        calls=[];original=Client.sign
        def sign_after_reservation(instance,card):
            selection=client.call('assistant.delegation.status',{'taskRef':task})['data']['authorization']
            assert selection['selectedCardDigest']==card_digest(card) and selection['contractTotal']=='9.00'
            calls.append(card_digest(card));return original(instance,card)
        monkeypatch.setattr(Client,'sign',sign_after_reservation)
        user_decision(assistant,pending['data']['confirmationRef'])
        assert client.call('assistant.delegation.confirm',args,operation_id=operation)['status']=='accepted'
        wait_for(lambda:status(assistant,ref));result=status(assistant,ref)
        assert result['status']=='completed',result
        card=result['data']['card'];relation=assistant.store.interaction(interaction_id=result['data']['interactionId'])
        assert result['data']['formed'] and not result['data']['started'] and calls==[ref]
        protocol=assistant.modules[0].trading.protocol(source,result['data']['interactionId'],result['data']['admissionId'])
        assert verify_card(template,card,protocol.keys).effective
        assert relation['peer']==provider.identities[0].agent_id
        if formation=='proposal':assert result['data']['interactionId']==quote['interactionId']
        assert len(host.turns)==1
        assert client.call('assistant.delegation.confirm',args)['data']['card']['signatures']['1']==card['signatures']['1']
        assert calls==[ref]


@pytest.mark.parametrize('formation',['proposal','countersign'])
def test_lost_signed_result_recovers_original_user_signature_without_repeating_review(platform,tmp_path,monkeypatch,formation):
    with participants(platform,tmp_path,formation=formation,fee=1 if formation=='proposal' else 0) as (provider,assistant,host,template,task):
        quote=candidate(assistant,provider,template,task)
        with monkeypatch.context() as patch:
            patch.setattr(Contracts,'recover',lambda *_:None)
            if formation=='proposal':
                original=Contracts.save
                def lose_saved_result(self,reference,record,**updates):
                    if updates.get('phase')=='signed':raise RuntimeFault('unknown_result','test lost a local result after original slot 1 persisted')
                    return original(self,reference,record,**updates)
                patch.setattr(Contracts,'save',lose_saved_result)
            else:
                original=Client.countersign
                def lose_saved_result(self,card):
                    original(self,card)
                    raise RuntimeFault('unknown_result','test lost the original completed countersign result')
                patch.setattr(Client,'countersign',lose_saved_result)
            started,_,_=confirm(assistant,quote);ref=started['data']['contractRef']
            wait_for(lambda:status(assistant,ref));assert status(assistant,ref)['status']=='unknown'
            record=assistant.modules[0].contracts.record(ref)
            protocol=assistant.modules[0].trading.protocol(record,record['interactionId'],record['admissionId'])
            original_signature=protocol.card(ref)['signatures']['1']
            assert original_signature
        # Expired approval cannot undo the signature already created under real approval.
        monkeypatch.setattr(assistant.confirmations,'clock',lambda:time.time()+1000)
        wait_for(lambda:status(assistant,ref) and status(assistant,ref)['status']=='completed')
        assert status(assistant,ref)['data']['card']['signatures']['1']==original_signature
        assert len(host.turns)==1


def test_full_value_rejection_never_creates_slot_one(platform,tmp_path,monkeypatch):
    with participants(platform,tmp_path) as (provider,assistant,host,template,_):
        client=LocalClient(assistant.config.home)
        task=client.call('assistant.delegation.prepare',{'purpose':'isolated small budget','budget':5,'proposalFeeLimit':1})['data']['taskRef']
        operation=uid();pending=client.call('assistant.delegation.authorize',{'taskRef':task},operation_id=operation)
        user_decision(assistant,pending['data']['confirmationRef'])
        assert client.call('assistant.delegation.authorize',{'taskRef':task},operation_id=operation)['status']=='completed'
        quote=candidate(assistant,provider,template,task);signatures=[]
        monkeypatch.setattr(Client,'sign',lambda *_:signatures.append(True))
        started,_,_=confirm(assistant,quote);ref=started['data']['contractRef']
        wait_for(lambda:status(assistant,ref));result=status(assistant,ref)
        assert result['status']=='rejected' and result['data']['error']['code']=='quota_exceeded',result
        assert not signatures and not client.call('assistant.delegation.status',{'taskRef':task})['data']['authorization']['selectedCardDigest']


@pytest.mark.parametrize('decision',['reject','expired'])
def test_contract_user_rejection_or_expiry_does_not_sign_or_reserve_counter(platform,tmp_path,monkeypatch,decision):
    with participants(platform,tmp_path,formation='countersign',fee=0) as (provider,assistant,host,template,task):
        quote=candidate(assistant,provider,template,task)
        pending,args,operation=confirm(assistant,quote,approve=False)
        if decision=='reject':user_decision(assistant,pending['data']['confirmationRef'],decision='reject')
        else:monkeypatch.setattr(assistant.confirmations,'clock',lambda:time.time()+1000)
        result=LocalClient(assistant.config.home).call('assistant.delegation.confirm',args,operation_id=operation)
        assert result['status']=='rejected',result
        record=assistant.modules[0].contracts.record(pending['data']['contractRef'])
        assert not record['subject']['signatures']['1'] and not record.get('selection') and not host.turns
        assert not platform.db.rows('SELECT * FROM delegation_admissions WHERE service=?',provider.identities[0].agent_id)


def test_two_concurrent_user_choices_cannot_sign_two_cards_in_one_scope(platform,tmp_path):
    with participants(platform,tmp_path,capacity=2) as (provider,assistant,host,template,task):
        quotes=[candidate(assistant,provider,template,task) for _ in range(2)]
        pending=[confirm(assistant,quote,approve=False) for quote in quotes]
        for value,_,_ in pending:user_decision(assistant,value['data']['confirmationRef'])
        client=LocalClient(assistant.config.home)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda value:client.call('assistant.delegation.confirm',value[1],operation_id=value[2]),pending))
        references=[value['data']['contractRef'] for value in results]
        for reference in references:wait_for(lambda:status(assistant,reference))
        finished=[status(assistant,reference) for reference in references]
        assert sorted(value['status'] for value in finished)==['completed','rejected'],finished
        contracts=assistant.modules[0].contracts
        signed=[]
        for reference in references:
            record=contracts.record(reference)
            protocol=assistant.modules[0].trading.protocol(record,record['interactionId'],record['admissionId'])
            if protocol.card(reference)['signatures']['1']:signed.append(reference)
        assert len(signed)==1
        assert client.call('assistant.delegation.status',{'taskRef':task})['data']['authorization']['selectedCardDigest']==signed[0]


def test_wrong_phase_tool_returns_precise_error_and_can_correct_in_original_review(platform,tmp_path):
    class CorrectingHost(QuoteHost):
        def run(self,turn):
            self.turns.append(turn);turn.on_session('isolated-review-session')
            assert json.loads(turn.input)['requiredAction']['tool']=='provider.task.decision'
            rejected=turn.on_tool('provider.task.quote',{},'wrong-phase')
            assert rejected['error']['code']=='forbidden' and 'provider.task.decision' in rejected['error']['message']
            assert platform.db.one('SELECT state FROM delegation_admissions WHERE service=?',turn.actor)['state']=='held'
            assert turn.on_tool('provider.task.decision',{'approved':True},'correct-review')['status']=='recorded'
            return HostResult('completed','isolated-review-session',{'testDouble':True})
    with participants(platform,tmp_path,formation='countersign',fee=0,agent_host=CorrectingHost()) as (provider,assistant,host,template,task):
        quote=candidate(assistant,provider,template,task)
        started,_,_=confirm(assistant,quote);ref=started['data']['contractRef']
        wait_for(lambda:status(assistant,ref));result=status(assistant,ref)
        assert result['status']=='completed' and result['data']['formed'],result
        assert len(host.turns)==1 and platform.db.one('SELECT state FROM delegation_admissions WHERE service=?',provider.identities[0].agent_id)['state']=='committed'


def test_original_native_tool_manifest_is_frozen_and_denied_tools_block_new_service(platform,tmp_path):
    from ioalab_sdk.provider.policy import definition_digest
    host=QuoteHost();host.capabilities=lambda:{'available':True,'testDouble':True,'nativeToolPolicy':{'write_file':'allow','edit_file':'allow','blocked_tool':'deny'}}
    with participants(platform,tmp_path,agent_host=host,business={'native_tools':{'write_file':'ask'}}) as (provider,assistant,host,template,task):
        quote=candidate(assistant,provider,template,task)
        execution=provider.modules[0].execution;endpoint=execution.contexts[quote['interactionId']]
        original=endpoint.provider.policy.definition(template)
        assert original.native_tools==(('write_file','ask'),)
        execution.configs['service']['native_tools']={'write_file':'allow','edit_file':'allow'}
        assert endpoint.provider.policy.definition(template).native_tools==original.native_tools
        from dataclasses import replace
        assert definition_digest(replace(original,native_tools=(('write_file','allow'),)))!=definition_digest(original)
        assert execution.status('service')['executionReady']
        execution.configs['service']['native_tools']={'not_installed':'allow'}
        state=execution.status('service')
        assert state['executionReady'] and state['uncheckedNativeTools']==['not_installed']
        execution.configs['service']['native_tools']={'blocked_tool':'allow'}
        state=execution.status('service')
        assert not state['executionReady'] and state['unavailableNativeTools']==['blocked_tool']
        execution.configs['service']['native_tools']={};execution.configs['service']['skills']=['missing_business_source']
        state=execution.status('service')
        assert not state['executionReady'] and state['unavailableSkills']==['missing_business_source']
        assert endpoint.provider.policy.definition(template).native_tools==original.native_tools


@pytest.mark.skipif(not os.environ.get('IOALAB_REAL_JIUWEN_DATA_DIR'),reason='requires explicitly configured original Jiuwen/model')
@pytest.mark.parametrize('formation',['proposal','countersign'])
def test_real_original_jiuwen_and_trusted_user_form_one_contract(platform,tmp_path,formation):
    from ioalab_jiuwen.process import ProcessHost
    host=ProcessHost(json.loads(os.environ['IOALAB_REAL_JIUWEN_COMMAND']),os.environ['IOALAB_REAL_JIUWEN_DATA_DIR'],model=os.environ.get('IOALAB_REAL_JIUWEN_MODEL'))
    assert host.probe()['available'],host.capabilities()
    original_run=host.run;events=[]
    def observe(event):
        if event.get('event_type') in ('chat.tool_call','chat.tool_result','interaction.requested','chat.ask_user_question'):
            events.append(event);private_json(tmp_path/'public-tool-events.json',events)
    host.run=lambda turn:original_run(replace(turn,on_event=observe))
    with participants(platform,tmp_path,formation=formation,fee=1 if formation=='proposal' else 0,agent_host=host) as (provider,assistant,host,template,task):
        allowed={'ioa_tool_'+uuid.uuid5(uuid.NAMESPACE_URL,name).hex for name in ('provider.task.quote','provider.task.decision')}
        def drive_permissions():
            for question in provider.confirmations.list():
                if question['kind']!='question' or question['status']!='pending':continue
                native=question['binding']['preview']['question']
                calls=[event.get('payload',{}).get('tool',{}) for event in events if event.get('event_type')=='chat.tool_call']
                call=next((value for value in calls if value.get('call_id')==native.get('request_id')),None)
                assert native.get('source')=='permission_interrupt' and call and call.get('name') in allowed,{'unexpectedNativeInteraction':native.get('source'),'toolNames':[value.get('name') for value in calls]}
                user_decision(provider,question['reference'],decision='answer',answers=[{'selected_options':['allow_once']}])
        started,_=request(assistant,provider,template,task);quote_ref=started['data']['quoteRef']
        def quoted():
            drive_permissions();return terminal(assistant,quote_ref)
        wait_for(quoted,timeout=180);quoted_result=terminal(assistant,quote_ref)
        assert quoted_result['status']=='completed',quoted_result
        quote=quoted_result['data']
        started,_,_=confirm(assistant,quote);ref=started['data']['contractRef']
        def formed():
            drive_permissions();return status(assistant,ref)
        wait_for(formed,timeout=180);result=status(assistant,ref)
        assert result['status']=='completed' and result['data']['formed'],result
        card=result['data']['card'];record=assistant.modules[0].contracts.record(ref)
        protocol=assistant.modules[0].trading.protocol(record,record['interactionId'],record['admissionId'])
        assert verify_card(template,card,protocol.keys).effective
        remote=LocalClient(assistant.config.home).call('assistant.delegation.status',{'taskRef':task})['data']['authorization']
        assert remote['selectedCardDigest']==ref and remote['contractTotal']=='9.00'
        relation=provider.store.interaction(interaction_id=record['interactionId'])
        assert relation['session']
        if os.environ.get('IOALAB_REAL_CONTRACT_REPORT'):
            path=Path(os.environ['IOALAB_REAL_CONTRACT_REPORT'])
            report=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
            report[formation]={'date':datetime.now().date().isoformat(),'scope':'Original Jiuwen, resident HTTP/WS, A2C/AP2 and scripted trusted browser user; contract formation only, not native web visibility, work start, delivery, payment or video acceptance',
                'model':host.capabilities()['model'],'interactionId':record['interactionId'],'sessionId':relation['session'],'cardDigest':ref,
                'userSignature':card['signatures']['1'],'providerSignature':card['signatures']['2'],'marketSignature':card['signatures']['3'],
                'proposalFeeTransfers':len(platform.db.rows('SELECT * FROM transfers WHERE payee_id=?',provider.account.account['id'])),
                'reservedContractValue':remote['contractTotal'],'formed':True,'started':False}
            path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
