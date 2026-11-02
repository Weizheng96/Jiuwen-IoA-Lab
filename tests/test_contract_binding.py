"""Formation has its own authenticated durable WS fact, before work or deposit."""
import copy
import json

import pytest

from ioalab_sdk.assistant.contracts import Contracts
from ioalab_sdk.runtime.protocol import RuntimeFault
from test_resident_contracts import candidate,confirm,status
from test_resident_delegation import participants,uid
from conftest import wait_for


@pytest.mark.parametrize('formation',['proposal','countersign'])
def test_formation_is_bound_before_any_work_or_payment_and_cannot_change_participants(platform,tmp_path,formation):
    with participants(platform,tmp_path,formation=formation,fee=0) as (provider,assistant,host,template,task):
        quote=candidate(assistant,provider,template,task)
        begun,_,_=confirm(assistant,quote);ref=begun['data']['contractRef']
        wait_for(lambda:status(assistant,ref));result=status(assistant,ref)
        assert result['status']=='completed',result
        data=result['data'];card=data['card'];aid=data['admissionId'];actor=assistant.identities[0].agent_id
        facts=platform.db.one('SELECT * FROM delegation_contracts WHERE admission=?',aid)
        assert facts['digest']==ref and json.loads(facts['card'])==card
        assert facts['permit'] is None and facts['task'] is None and facts['start_message'] is None
        assert not platform.db.rows('SELECT * FROM payment_records WHERE ref=?',ref)
        record=assistant.modules[0].contracts.record(ref)
        assert record['bindingReceipt']['cardDigest']==ref
        assert not card['fulfillment'] and all(not card['signatures'][str(slot)] for slot in range(4,8))
        before=copy.deepcopy(card['signatures'])
        same=assistant.connection.call(actor,'contract.bind',{'admissionId':aid,'card':card},operation_id=uid())
        assert same['card']['signatures']==before
        assert len(platform.db.rows('SELECT * FROM delegation_contracts WHERE admission=?',aid))==1
        tampered=copy.deepcopy(card);tampered['client']['id']=provider.identities[0].agent_id
        with pytest.raises(RuntimeFault):
            assistant.connection.call(actor,'contract.bind',{'admissionId':aid,'card':tampered},operation_id=uid())
        with pytest.raises(RuntimeFault) as wrong_actor:
            provider.connection.call(provider.identities[0].agent_id,'contract.bind',{'admissionId':aid,'card':card},operation_id=uid())
        assert wrong_actor.value.code=='forbidden'
        assert json.loads(platform.db.one('SELECT card FROM delegation_contracts WHERE admission=?',aid)['card'])==card
        assert len(host.turns)==1


@pytest.mark.parametrize('formation',['proposal','countersign'])
def test_lost_formation_reply_recovers_original_slot_one_and_same_registration(platform,tmp_path,monkeypatch,formation):
    with participants(platform,tmp_path,formation=formation,fee=0) as (provider,assistant,host,template,task):
        quote=candidate(assistant,provider,template,task);lost=[]
        with monkeypatch.context() as patch:
            original=assistant.connection.call
            def lose_reply(actor,kind,payload,**kwargs):
                result=original(actor,kind,payload,**kwargs)
                if kind=='contract.bind' and not lost:
                    lost.append(copy.deepcopy(result))
                    raise RuntimeFault('unknown_result','Test response lost after committed formation')
                return result
            patch.setattr(assistant.connection,'call',lose_reply)
            patch.setattr(Contracts,'recover',lambda *_:None)
            begun,_,_=confirm(assistant,quote);ref=begun['data']['contractRef']
            wait_for(lambda:status(assistant,ref));assert status(assistant,ref)['status']=='unknown'
            assert lost and platform.db.one('SELECT digest FROM delegation_contracts WHERE digest=?',ref)
        wait_for(lambda:status(assistant,ref) and status(assistant,ref)['status']=='completed')
        result=status(assistant,ref)
        assert result['data']['card']['signatures']==lost[0]['card']['signatures']
        assert assistant.modules[0].contracts.record(ref)['bindingReceipt']==lost[0]
        assert len(host.turns)==1 and not platform.db.rows('SELECT * FROM payment_records WHERE ref=?',ref)
        assert len(platform.db.rows('SELECT * FROM delegation_contracts WHERE digest=?',ref))==1
