"""Recover the same accepted installment through actual resident WS/AP2 calls."""
import copy
from decimal import Decimal

import pytest
from a2c.codec import digest
from ioalab.errors import LabError
from ioalab_sdk.runtime.ipc import LocalClient
from test_assistant_role import user_decision
from test_resident_contracts import candidate,confirm,status
from test_resident_delegation import participants
from test_resident_fulfillment import ProductionHost,VERIFIER,accept_preview,configure_template,invoke,operation,uid
from conftest import wait_for


def test_payment_provider_publishes_both_original_voucher_and_ap2_receipt_keys(platform,client):
    from a2c.payment.ap2 import jwt_sign,jwt_verify
    response=client.get('/a2a/pay/.well-known/jwks.json')
    assert response.status_code==200
    keys={key['kid']:key for key in response.json()['keys']}
    assert keys[platform.pay_signer.jwk['kid']]==platform.pay_signer.jwk
    assert keys[platform.pay_ec.kid]==platform.pay_ec.jwk
    assert all('d' not in key for key in keys.values())
    claims={'iss':platform.pay_id,'payment_id':'isolated-receipt-key-proof'}
    assert jwt_verify(jwt_sign(claims,platform.pay_ec),keys[platform.pay_ec.kid])==claims


@pytest.mark.parametrize('v22_record',[False,True])
def test_rejected_accepted_period_requires_new_confirmation_and_pays_once(platform,tmp_path,monkeypatch,v22_record):
    configure_template(monkeypatch)
    with participants(platform,tmp_path,agent_host=ProductionHost(),verifier_source=VERIFIER) as (provider,assistant,host,template,task):
        quote=candidate(assistant,provider,template,task)
        selected,_,_=confirm(assistant,quote);ref=selected['data']['contractRef']
        wait_for(lambda:status(assistant,ref))
        assert status(assistant,ref)['data']['formed']
        key,_=invoke(assistant,ref,'start')
        wait_for(lambda:operation(assistant,ref,key))
        assert operation(assistant,ref,key)['status']=='completed'
        wait_for(lambda:status(assistant,ref)['data']['stepRequest'])
        step=status(assistant,ref)['data']['stepRequestDigest']
        key,pending=invoke(assistant,ref,'answer_step',stepRequestDigest=step)
        user_decision(assistant,pending['data']['confirmationRef'],decision='answer',answers=[{'selected_options':['allow']}])
        LocalClient(assistant.config.home).call('assistant.delegation.answer_step',{'contractRef':ref,'stepRequestDigest':step},operation_id=key)
        wait_for(lambda:operation(assistant,ref,key))
        wait_for(lambda:status(assistant,ref)['data']['availablePeriods']==1)
        key,_=invoke(assistant,ref,'fetch',period=1)
        wait_for(lambda:operation(assistant,ref,key))
        assert operation(assistant,ref,key)['status']=='completed'
        accept_key,pending=accept_preview(assistant,ref)
        owner=assistant.account.account['id']
        platform.wallets.adjust(owner,-platform.wallets.balance(owner),by='isolated-test',note='Exercise insufficient virtual allowance')
        apply=platform.payment_operations.apply_in
        if v22_record:
            def legacy_rejection(*args,**kwargs):
                try:return apply(*args,**kwargs)
                except LabError as exc:
                    if exc.code!='payment_rejected':raise
                    raise LabError('forbidden','Payment changed or expired before transaction commit',status=403) from None
            monkeypatch.setattr(platform.payment_operations,'apply_in',legacy_rejection)
        user_decision(assistant,pending['data']['confirmationRef'])
        rpc=LocalClient(assistant.config.home)
        rpc.call('assistant.delegation.accept',{'contractRef':ref,'period':1},operation_id=accept_key)
        wait_for(lambda:operation(assistant,ref,accept_key) and operation(assistant,ref,accept_key)['status']=='completed')
        accepted=operation(assistant,ref,accept_key)['data']
        assert accepted['acceptance']=='pass' and accepted['payment']=='unpaid'
        assert accepted['paymentError']['code']==('forbidden' if v22_record else 'payment_rejected')
        protocol,_=assistant.modules[0].fulfillment.client(ref)
        signed=copy.deepcopy(protocol.card(ref)['signatures']['7'])
        assert len(signed)==1
        assert not platform.db.rows("SELECT * FROM payment_records WHERE ref=? AND purpose='installment'",ref)
        if v22_record:
            # A v22 Store froze only fingerprint/body, before typed rejection
            # and parameters existed. This fixture models its persisted shape.
            records=protocol.payment.store.all('ap2_payments')
            for pay_key,record in records.items():
                if record.get('body',{}).get('card',{}).get('signatures',{}).get('7'):
                    protocol.payment.store.put('ap2_payments',pay_key,{k:v for k,v in record.items() if k not in ('parameters','rejection')})
        # An old queued read never obtains the newly introduced payment effect.
        old_key=uid();args={'contractRef':ref,'period':1}
        assistant.store.checkpoint_set(assistant.modules[0].fulfillment.namespace,old_key,{
            'name':'pay','args':args,'fingerprint':digest({'name':'pay','args':args}),
            'phase':'prepared','handler':'assistant.fulfillment'})
        rpc.call('assistant.delegation.pay',args,operation_id=old_key)
        wait_for(lambda:operation(assistant,ref,old_key))
        old=operation(assistant,ref,old_key)
        assert old['status']=='failed' and old['data']['newPaymentOperationRequired']
        assert not platform.db.rows("SELECT * FROM payment_records WHERE ref=? AND purpose='installment'",ref)
        platform.wallets.adjust(owner,Decimal('20'),by='isolated-test',note='Prepare virtual allowance for authorized recovery')
        if v22_record:monkeypatch.setattr(platform.payment_operations,'apply_in',apply)
        new_key,pending=invoke(assistant,ref,'pay',period=1)
        assert pending['status']=='waiting'
        preview=assistant.confirmations.get(pending['data']['confirmationRef'],detail=True)['binding']['preview']
        assert preview['period']==1 and preview['amount']==8 and preview['paymentAttempts']
        assert not platform.db.rows("SELECT * FROM payment_records WHERE ref=? AND purpose='installment'",ref)
        user_decision(assistant,pending['data']['confirmationRef'])
        rpc.call('assistant.delegation.pay',args,operation_id=new_key)
        wait_for(lambda:operation(assistant,ref,new_key) and operation(assistant,ref,new_key)['status']=='completed')
        recovered=operation(assistant,ref,new_key)['data']
        assert recovered['payment']=='paid' and recovered['paymentNotified']
        wait_for(lambda:status(assistant,ref)['data']['closed'])
        assert protocol.card(ref)['signatures']['7']==signed
        assert len(platform.db.rows("SELECT * FROM payment_records WHERE ref=? AND purpose='installment'",ref))==1
        assert len(platform.db.rows("SELECT * FROM transfers WHERE ref=? AND purpose='installment'",ref))==1
        repeats=rpc.call('assistant.delegation.pay',args,operation_id=new_key)
        assert repeats['status']=='completed' and repeats['data']==recovered
        attempts=protocol.transport.payment_recovery(ref,1)['attempts']
        assert len(attempts)==2
        assert sum(item['status']=='paid' for item in attempts)>=1
