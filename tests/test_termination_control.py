"""Original A2C SDK over real resident HTTP/WS; no native user-decision proof."""
import copy
import json

import pytest

from ioalab_sdk.runtime.protocol import RuntimeFault
from test_resident_contracts import candidate,confirm,status
from test_resident_delegation import participants,uid
from ioalab_sdk.provider.policy import HostPolicy
import test_resident_delegation as assembly
from conftest import wait_for


@pytest.mark.parametrize('prepaid',[False,True])
@pytest.mark.parametrize('formation',['proposal','countersign'])
@pytest.mark.parametrize('settlement',['positive','zero','unilateral'])
def test_original_termination_before_work_uses_actual_deposit_and_closes_without_fake_anchor(platform,tmp_path,monkeypatch,formation,settlement,prepaid):
    # Isolate original financial gates; public autonomous settlement has its own tests.
    from ioalab_sdk.provider.termination import Termination
    monkeypatch.setattr(Termination,'advance',lambda *_:None)
    original=assembly.template_from_definition
    def declared_deposit(value,**kwargs):
        value=copy.deepcopy(value);value['pricing']['deposit']='1'
        return original(value,**kwargs)
    monkeypatch.setattr(assembly,'template_from_definition',declared_deposit)
    if settlement=='zero':monkeypatch.setattr(HostPolicy,'termination_fee',lambda *args:1 if prepaid else 0)
    with participants(platform,tmp_path,formation=formation,fee=0) as (provider,assistant,host,template,task):
        selected=candidate(assistant,provider,template,task)
        begun,_,_=confirm(assistant,selected);ref=begun['data']['contractRef']
        wait_for(lambda:status(assistant,ref));formed=status(assistant,ref)
        assert formed['status']=='completed',formed
        data=formed['data'];card=data['card'];admission=data['admissionId'];interaction=data['interactionId']
        record=assistant.modules[0].contracts.record(ref)
        client=assistant.modules[0].trading.protocol(record,interaction,admission)
        if prepaid:
            class BeforeStart(Exception):pass
            send=client.transport.send
            def stop_before_send(to,op,body,*args,**kwargs):
                if op=='work.start':raise BeforeStart('Actual deposit paid; work.start not submitted')
                return send(to,op,body,*args,**kwargs)
            with monkeypatch.context() as patch:
                patch.setattr(client.transport,'send',stop_before_send)
                with client.transport.operation(uid()):
                    with pytest.raises(BeforeStart):client.start(card)
            assert client.store.get('cards',ref)['depositVoucher']
        quote=None
        if settlement!='unilateral':
            with client.transport.operation(uid()):quote=client.termination_quote(ref)
            assert platform.db.one('SELECT 1 FROM delegation_termination_quotes WHERE admission=?',admission)
        with client.transport.operation(uid()):notice=client.terminate(ref,quote=quote)
        actual=platform.db.one('SELECT * FROM delegation_contracts WHERE admission=?',admission)
        assert actual['permit'] is None and actual['start_message'] is None
        assert json.loads(actual['card'])==card
        assert json.loads(actual['progress'])['fulfillment']['termination']['termination']==notice
        payments=platform.db.rows('SELECT * FROM payment_records WHERE ref=?',ref)
        deposits=[value for value in payments if value['purpose']=='deposit']
        assert len(deposits)==int(prepaid)
        paid=[value for value in payments if value['purpose']=='termination']
        if settlement=='positive':
            assert len(paid)==1 and float(paid[0]['amount'])==quote['fee']-int(prepaid)
            assert (paid[0]['payer_id'],paid[0]['payee_id'])==(assistant.account.account['id'],provider.account.account['id'])
        else:assert not paid
        with client.transport.operation(uid()):
            with pytest.raises(RuntimeFault) as stopped:client.transport.send(card['provider'],'work.start',{'card':card})
        assert stopped.value.code=='closed'
        endpoint=provider.modules[0].execution.contexts[interaction]
        assert endpoint.provider.card(ref)['signatures']['1']==card['signatures']['1']
        assert bool(endpoint.provider.store.get('cards',ref).get('depositVoucher'))==prepaid
        assert not endpoint.provider.card(ref)['fulfillment'].get('receipt')
        expected=quote['fee'] if quote else int(prepaid)
        # Drop only the derivative display row, keeping all authority and money.
        platform.db.run('DELETE FROM observation_contracts WHERE admission=?',admission)
        platform.contract_views.rebuild()
        view=next(row for row in platform.contract_views.list(assistant.account.account['id'],private=True) if row['card_digest']==ref)
        assert view['state']=='terminated' and view['phase']=='已解除 · 待结清'
        assert float(view['paid'])==expected
        income=platform.contract_views.income(provider.account.account['id']).get(provider.identities[0].agent_id,{})
        assert float(income.get('earned',0))==expected
        with platform.db.tx() as conn:assert not platform.contract_views.record_in(conn,admission,card)
        operation=uid()
        with endpoint.provider.transport.operation(operation):endpoint.provider.close(ref)
        ledger=platform.market.market.store.get('ledger',ref)
        assert ledger['outcome']=='client-terminated'
        assert platform.db.one('SELECT state FROM delegation_admissions WHERE id=?',admission)['state']=='released'
        with endpoint.provider.transport.operation(operation):endpoint.provider.close(ref)
        assert platform.market.market.store.get('ledger',ref)==ledger
        assert not platform.db.rows("SELECT 1 FROM delegation_protocol_calls WHERE admission=? AND op='work.start'",admission)
        assert len(host.turns)==1
