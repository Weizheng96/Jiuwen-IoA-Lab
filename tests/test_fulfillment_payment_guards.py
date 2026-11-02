"""Narrow role guards; real WS/AP2 and signature evidence live in integration tests."""
from contextlib import nullcontext
import copy
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from a2c.payment import PaymentRejected
from ioalab_sdk.assistant import fulfillment as module
from ioalab_sdk.assistant.fulfillment import Fulfillment
from ioalab_sdk.runtime.protocol import RuntimeFault

REF='sha256:'+'a'*64
BODY='sha256:'+'b'*64
VOUCHER={'purpose':'installment','period':1,'amount':299}


class Checkpoints:
    def __init__(self):self.values={}
    def checkpoint(self,namespace,key):return copy.deepcopy(self.values.get((namespace,key)))
    def checkpoint_set(self,namespace,key,value):self.values[namespace,key]=copy.deepcopy(value)
    def checkpoints(self,namespace):return {key:copy.deepcopy(value) for (space,key),value in self.values.items() if space==namespace}


@pytest.fixture
def guard(monkeypatch):
    store=Checkpoints()
    card={'provider':'provider','templateDigest':'template','paymentProvider':{'id':'psp'},
          'signatures':{'5':[{}],'7':[{}]},'derived':{'periods':[{'amount':299}]},'fulfillment':{}}
    source={'card':card,'state':'started','vouchers':[]}
    evidence={'attempts':[],'rejection':None,'response':None,'noPending':True}
    transport=SimpleNamespace(operation=lambda *_:nullcontext(),payment_recovery=Mock(side_effect=lambda *_:copy.deepcopy(evidence)),
                              received=Mock(return_value=None),control_receipt=Mock(return_value=None))
    client=SimpleNamespace(store=SimpleNamespace(get=lambda *_:copy.deepcopy(source)),card=lambda *_:copy.deepcopy(card),
                           transport=transport,pay=Mock(return_value=VOUCHER),keys=object())
    confirmations=SimpleNamespace(ensure=Mock(return_value={'reference':'approval','status':'pending'}),claim=Mock())
    scheduler=SimpleNamespace(submit=Mock(return_value={'status':'queued','reference':'receipt'}),
        reference=Mock(return_value='original-job'),get=Mock(return_value={'status':'unknown','interactionId':'interaction'}),
        resolve=Mock(),resume=Mock(),reject_wait=Mock())
    runtime=SimpleNamespace(confirmations=confirmations,scheduler=scheduler,require_execution=Mock(),
                            account=SimpleNamespace(api=Mock()))
    role=SimpleNamespace(runtime=runtime,store=store,identity=SimpleNamespace(agent_id='assistant'),
                         fields=lambda *_:None)
    owner=Fulfillment(role)
    monkeypatch.setattr(owner,'client',lambda *_:(client,{'interactionId':'interaction','template':{}}))
    monkeypatch.setattr(module,'verify_card',lambda *_:SimpleNamespace(accepted=1))
    return SimpleNamespace(owner=owner,client=client,store=store,runtime=runtime,source=source,
                           card=card,evidence=evidence,transport=transport,scheduler=scheduler,confirmations=confirmations)


def saved(guard,**updates):
    value={'name':'pay','args':{'contractRef':REF,'period':1},'phase':'approved'}
    value.update(updates)
    guard.store.checkpoint_set(guard.owner.namespace,'operation',value)
    return value


def rejection(reference='historical',body=BODY):
    return PaymentRejected(payment_provider='psp',body_digest=body,reference=reference,
                           code='payment_rejected',message='Explicit original refusal')


def preview():
    return {'paymentAttempts':[{'operationId':'historical','bodyDigest':BODY,'status':'rejected','error':{}}]}


def test_new_pay_waits_for_independent_confirmation_before_scheduling(guard):
    result=guard.owner.call('pay','operation',{'contractRef':REF,'period':1})
    assert result['status']=='waiting' and result['data']['confirmationRef']=='approval'
    record=guard.owner.record('operation')
    assert record['paymentActionVersion']==1 and record['phase']=='prepared'
    assert record['preview']['amount']==299 and record['preview']['card']==guard.card
    guard.scheduler.submit.assert_not_called();guard.client.pay.assert_not_called()


def test_new_queued_pay_without_approval_cannot_become_a_payment(guard):
    saved(guard,paymentActionVersion=1,preview={'card':guard.card})
    with pytest.raises(RuntimeFault) as denied:guard.owner.run({'payload':{'operationId':'operation'}})
    assert denied.value.code=='forbidden'
    guard.client.pay.assert_not_called()


def test_new_pay_rechecks_trusted_confirmation_before_execution(guard):
    saved(guard,paymentActionVersion=1,preview={'card':guard.card},approval='approval',binding={'stage':'delegation.pay'})
    guard.confirmations.claim.side_effect=RuntimeFault('forbidden','Approval expired')
    with pytest.raises(RuntimeFault,match='expired'):guard.owner.run({'payload':{'operationId':'operation'}})
    guard.client.pay.assert_not_called()


def test_old_queued_pay_404_stays_readonly_and_requests_new_operation(guard):
    saved(guard)
    request=httpx.Request('GET','http://test/payments')
    guard.runtime.account.api.side_effect=httpx.HTTPStatusError('not found',request=request,response=httpx.Response(404,request=request))
    result=guard.owner.run({'payload':{'operationId':'operation'}})
    assert result.state=='failed' and result.data['newPaymentOperationRequired']
    guard.client.pay.assert_not_called()
    assert guard.runtime.account.api.call_args.args[0]=='GET'


def test_unknown_payment_is_not_retried_or_forked(guard):
    unknown=RuntimeFault('unknown_result','Exact attempt remains unresolved')
    guard.client.pay.side_effect=unknown
    with pytest.raises(RuntimeFault) as failure:guard.owner.payment(guard.client,REF,1,approved=preview())
    assert failure.value is unknown and guard.client.pay.call_count==1


def test_new_payment_rejection_is_not_automatically_retried(guard):
    refused=rejection('new-attempt')
    guard.client.pay.side_effect=[refused,VOUCHER]
    with pytest.raises(PaymentRejected) as failure:guard.owner.payment(guard.client,REF,1,approved=preview())
    assert failure.value is refused and guard.client.pay.call_count==1


def test_historical_rejection_with_unresolved_outbox_is_not_retried(guard):
    guard.evidence['noPending']=False
    guard.client.pay.side_effect=[rejection(),VOUCHER]
    with pytest.raises(PaymentRejected):guard.owner.payment(guard.client,REF,1,approved=preview())
    assert guard.client.pay.call_count==1


def test_exact_frozen_historical_rejection_can_advance_only_once(guard):
    guard.client.pay.side_effect=[rejection(),VOUCHER]
    result=guard.owner.payment(guard.client,REF,1,approved=preview())
    assert result['payment']=='paid' and guard.client.pay.call_count==2


def test_same_reference_with_different_body_is_not_the_approved_rejection(guard):
    refused=rejection(body='sha256:'+'c'*64)
    guard.client.pay.side_effect=[refused,VOUCHER]
    with pytest.raises(PaymentRejected) as failure:guard.owner.payment(guard.client,REF,1,approved=preview())
    assert failure.value is refused and guard.client.pay.call_count==1


@pytest.mark.parametrize('notice',[
    None,
    {'result':{'reply':{'body':{'ok':False}}}},
    {'result':{'reply':{'body':{'ok':True}},'executionError':{'code':'unknown_result'}}},
],ids=['missing','negative','execution-unknown'])
def test_voucher_without_positive_notification_does_not_finish_original_job(guard,notice):
    saved(guard,name='accept',phase='executing')
    guard.source['vouchers']=[VOUCHER]
    guard.transport.received.return_value=notice
    guard.owner.recover()
    guard.scheduler.resolve.assert_not_called()
    assert guard.scheduler.submit.call_args.args[2]=='assistant.receipt'
    guard.client.pay.assert_not_called()


def test_paid_pay_uses_existing_voucher_without_new_confirmation(guard):
    guard.evidence['response']={'voucher':VOUCHER}
    guard.source['vouchers']=[VOUCHER]
    result=guard.owner.call('pay','operation',{'contractRef':REF,'period':1})
    assert result['status']=='accepted'
    record=guard.owner.record('operation')
    assert 'preview' not in record and 'paymentActionVersion' not in record
    outcome=guard.owner.run({'payload':{'operationId':'operation'}})
    assert outcome.state=='completed' and outcome.data['payment']=='paid'
    guard.confirmations.ensure.assert_not_called();guard.confirmations.claim.assert_not_called()
    guard.runtime.account.api.assert_not_called()
    guard.client.pay.assert_called_once_with(REF,1)


def test_paid_lookup_that_loses_receipt_does_not_become_a_new_payment(guard):
    guard.evidence['response']={'voucher':VOUCHER}
    guard.owner.call('pay','operation',{'contractRef':REF,'period':1})
    request=httpx.Request('GET','http://test/payments')
    guard.runtime.account.api.side_effect=httpx.HTTPStatusError('not found',request=request,response=httpx.Response(404,request=request))
    outcome=guard.owner.run({'payload':{'operationId':'operation'}})
    assert outcome.state=='failed' and outcome.data['newPaymentOperationRequired']
    guard.confirmations.ensure.assert_not_called();guard.client.pay.assert_not_called()


def test_zero_amount_pay_has_no_confirmation_lookup_or_actual_transfer(guard,monkeypatch):
    from a2c.client import Client
    from a2c.common import Store
    import a2c.client as upstream_module

    guard.card['derived']['periods'][0]['amount']=0
    signer=SimpleNamespace(id='assistant',inline_jwk={'kty':'isolated-unit-key'})
    guard.card['client']={'id':signer.id,'jwk':signer.inline_jwk}
    store=Store()
    store.put('cards',REF,copy.deepcopy(guard.source))
    store.put('templates','template',{'pricing':{'currency':'CNY'}})
    port=SimpleNamespace(pay=Mock(side_effect=AssertionError('Zero amount must not request a transfer')))
    wire=SimpleNamespace(send=Mock(side_effect=AssertionError('Zero amount must not send payment traffic')))
    upstream=Client(signer,keys=object(),transport=wire,fetch=Mock(),payment_provider={'id':'psp'},store=store,payment=port)
    monkeypatch.setattr(upstream_module,'verify_card',lambda *_:SimpleNamespace(accepted=1))
    guard.client.pay.side_effect=upstream.pay
    result=guard.owner.call('pay','operation',{'contractRef':REF,'period':1})
    assert result['status']=='accepted'
    record=guard.owner.record('operation')
    assert 'preview' not in record and 'paymentActionVersion' not in record
    outcome=guard.owner.run({'payload':{'operationId':'operation'}})
    assert outcome.state=='completed' and outcome.data['payment']=='not_required' and outcome.data['voucher'] is None
    guard.confirmations.ensure.assert_not_called();guard.confirmations.claim.assert_not_called()
    guard.runtime.account.api.assert_not_called();port.pay.assert_not_called();wire.send.assert_not_called()
