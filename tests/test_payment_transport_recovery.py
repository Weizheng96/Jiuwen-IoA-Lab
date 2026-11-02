"""Owned payment evidence, exact old attempts and safe AP2 recovery boundaries."""
import copy
import hashlib
import threading
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from a2c import objects
from a2c.card import card_digest
from a2c.codec import digest, utcnow
from a2c.common import Reply
from a2c.jws import Signer
from a2c.payment import PaymentRejected
from a2c.payment.ap2 import EcKey, checkout, checkout_mandate, payment_mandate, receipt
from ioalab_sdk.adapters.store import ProtocolStore
from ioalab_sdk.adapters.transport import SessionTransport, stable
from ioalab_sdk.runtime import RuntimeStore, RuntimeFault
from ioalab_sdk.runtime.protocol import encode


@pytest.fixture
def payments(tmp_path):
    store=RuntimeStore(tmp_path/'runtime.sqlite','http://local',['assistant'],owner_id='owner')
    with store.transaction() as conn:
        conn.execute('INSERT INTO runtime_interactions VALUES(?,?,?,?,?,?,?,?,NULL)',
                     ('origin','interaction','assistant','service','outbound','delegation',0,'{}'))
    signer=Signer.generate('assistant');payer=EcKey('payer');psp=Signer.generate('pay','payment-signing');psp_ap2=EcKey('psp')
    identity=SimpleNamespace(agent_id='assistant',protocol_id='assistant',signer=signer,payment=payer)
    template={'templateDigest':'sha256:'+'a'*64,'provider':'service','pricing':{'currency':'CNY'}}
    card={'templateDigest':template['templateDigest'],'provider':'service','client':{'id':signer.id,'jwk':signer.inline_jwk},
          'paymentProvider':{'id':'pay','jwksUri':'pay/.well-known/jwks.json'},
          'derived':{'periods':[{'amount':5},{'amount':7}]},'fulfillment':{},'signatures':{}}
    ref=card_digest(card);calls=[];queries=[];actual={};operations={};next_error=[]
    def missing(path):
        response=httpx.Response(404,request=httpx.Request('GET','http://local/api/v1'+path))
        raise httpx.HTTPStatusError('unavailable',request=response.request,response=response)
    def api(method,path):
        assert method=='GET';queries.append(path);parts=urlsplit(path);params=parse_qs(parts.query)
        if parts.path=='/payments':
            key=(params['ref'][0],params['purpose'][0],int(params['period'][0]))
            if key in actual:return copy.deepcopy(actual[key])
        elif parts.path.startswith('/operations/'):
            operation=parts.path.split('/')[-1]
            if operation in operations:return copy.deepcopy(operations[operation])
        return missing(path)
    runtime=SimpleNamespace(store=store,account=SimpleNamespace(config={'market':'market','pay':'pay'},api=api),closing=threading.Event())
    public=SimpleNamespace(jwks=lambda uri:{'keys':[psp.jwk,psp_ap2.jwk]})
    transport=SessionTransport(runtime,identity,'interaction','admission','service',public=public)
    protocol=ProtocolStore(store,'assistant','interaction')
    protocol.put('cards',ref,{'card':card});protocol.put('templates',template['templateDigest'],template)
    def response(body,period=1):
        voucher=objects.signed(objects.new('voucher',purpose='installment',period=period,amount=5 if period==1 else 7,
                                         currency='CNY',cardDigest=ref),psp,utcnow())
        return {'op':'ap2.pay','voucher':voucher,'paymentReceipt':receipt(body['paymentMandate'],psp_ap2,
                issuer='pay',payment_id='payment-one',now=int(utcnow().timestamp()))}
    def body(period=1):
        jwt=checkout({'id':'service'},EcKey('merchant'),purpose='installment',digest_ref={'cardDigest':ref},
                     period=period,amount=5 if period==1 else 7,currency='CNY',now=int(utcnow().timestamp()))
        return {'template':copy.deepcopy(template),'card':copy.deepcopy(card),
                'checkoutMandate':checkout_mandate(jwt,payer,int(utcnow().timestamp())),
                'paymentMandate':payment_mandate(jwt,payer,payee={'id':'service'},amount=5 if period==1 else 7,
                     currency='CNY',instrument={'id':'virtual'},now=int(utcnow().timestamp()))}
    def call(actor,kind,payload,*,operation_id):
        store.prepare(actor,kind,payload,operation_id);calls.append((operation_id,copy.deepcopy(payload)))
        frame=store.response(actor,kind,operation_id)
        if frame is None:
            frame={'agentId':actor,'operation':kind,'operationId':operation_id,'ok':not next_error}
            if next_error:frame['error']=next_error.pop(0)
            else:frame['result']=response(payload['ap2'])
            store.complete(frame)
        if not frame['ok']:
            problem=frame['error'];raise RuntimeFault(problem['code'],problem['message'])
        return frame['result']
    runtime.connection=SimpleNamespace(call=call)
    def remember(parent,value,*,error=None,pending=False,frame_updates=None,payload_updates=None):
        key='control:'+(parent+':' if parent is not None else '')+'ap2.pay'
        operation=stable('assistant','interaction',*((parent,) if parent is not None else ()),'ap2.pay')
        payload={'admissionId':'admission','ap2':copy.deepcopy(value)}
        # The actual v22 shape contains neither operationId nor rejection.
        store.checkpoint_set(transport.namespace,key,{'fingerprint':digest({'payload':payload}),'payload':payload})
        store.prepare('assistant','ap2.pay',{**payload,**(payload_updates or {})},operation)
        if not pending:
            frame={'agentId':'assistant','operation':'ap2.pay','operationId':operation,'ok':error is None}
            frame.update({'error':error} if error else {'result':response(value)})
            frame.update(frame_updates or {});store.complete(frame)
        return operation
    yield SimpleNamespace(store=store,runtime=runtime,transport=transport,body=body,response=response,remember=remember,
                          ref=ref,actual=actual,operations=operations,next_error=next_error,calls=calls,queries=queries)
    store.close()


LEGACY={'code':'forbidden','message':'Payment changed or expired before transaction commit','recovery':'correct_input'}
REJECTED={'code':'payment_rejected','message':'card-invalid: 余量不足','recovery':'confirm_payment_retry'}


def test_v22_old_shape_rejection_recovers_across_a_new_parent_without_submitting(payments):
    p=payments;body=p.body();operation=p.remember('accept-one',body,error=LEGACY)
    with p.transport.operation('new-pay'):
        with pytest.raises(PaymentRejected) as caught:p.transport.send('pay','ap2.pay',body)
    assert caught.value.reference==operation and caught.value.body_digest==digest(body)
    assert not p.calls
    saved=p.store.checkpoint(p.transport.namespace,'control:accept-one:ap2.pay')
    assert set(saved)=={'fingerprint','payload'}
    state=p.transport.payment_recovery(p.ref,1)
    assert state['noPending'] is True and state['rejection']['reference']==operation
    assert state['attempts']==[{'operationId':operation,'bodyDigest':digest(body),'status':'rejected',
                                'error':{'code':LEGACY['code'],'message':LEGACY['message']}}]


def test_first_commit_rejection_is_typed_only_after_its_actual_saved_frame(payments):
    p=payments;body=p.body();p.next_error.append(REJECTED)
    with p.transport.operation('pay-one'):
        with pytest.raises(PaymentRejected) as caught:p.transport.send('pay','ap2.pay',body)
    assert len(p.calls)==1 and caught.value.reference==p.calls[0][0]
    assert p.store.operation('assistant','ap2.pay',caught.value.reference)['response']['error']==REJECTED


def test_unknown_original_body_keeps_its_control_identity_under_a_new_parent(payments):
    p=payments;body=p.body();operation=p.remember('accept-one',body,pending=True)
    with p.transport.operation('new-pay'):
        p.transport.send('pay','ap2.pay',body)
        assert p.transport.control_receipt('ap2.pay')['payload']['ap2']==body
    assert p.calls==[(operation,{'admissionId':'admission','ap2':body})]
    with p.transport.operation('accept-one'):
        assert p.transport.control_receipt('ap2.pay')['result']['voucher']['period']==1
    assert len(p.store.pending())==0


def test_unknown_attempt_cannot_fork_a_different_body_under_another_parent(payments):
    p=payments;body=p.body();operation=p.remember('accept-one',body,pending=True)
    with p.transport.operation('new-pay'):
        with pytest.raises(RuntimeFault,match='original payment body'):p.transport.send('pay','ap2.pay',p.body())
    assert not p.calls and p.store.pending()[0]['id']==operation
    assert len(p.transport.payment_attempts(p.ref,period=1))==1


@pytest.mark.parametrize('change',[{'agentId':'foreign'},{'operation':'message.send'},{'outcome':'unknown'},
                                 {'error':{'code':'forbidden','message':'arbitrary denial'}}])
def test_wrong_or_generic_rejection_receipt_never_proves_no_pending(payments,change,monkeypatch):
    p=payments;p.remember('accept-one',p.body(),error=REJECTED)
    original=p.store.operation
    def operation(*args):
        value=original(*args)
        if value and value['response']:value['response'].update(change)
        return value
    monkeypatch.setattr(p.store,'operation',operation)
    state=p.transport.payment_recovery(p.ref,1)
    assert state['noPending'] is False and state['rejection'] is None and state['attempts'][0]['status']=='unknown'


def test_outbox_payload_must_match_the_frozen_admission_and_body(payments):
    p=payments;p.remember('accept-one',p.body(),error=REJECTED,payload_updates={'admissionId':'another'})
    with pytest.raises(RuntimeFault,match='outbox differs'):p.transport.payment_recovery(p.ref,1)


def test_one_rejection_and_a_pending_attempt_do_not_allow_retry(payments):
    p=payments;p.remember('accept-one',p.body(),error=REJECTED);p.remember('other',p.body(),pending=True)
    state=p.transport.payment_recovery(p.ref,1)
    assert state['noPending'] is False and state['rejection'] is None
    assert {item['status'] for item in state['attempts']}=={'rejected','unknown'}


def test_existing_verified_payment_recovers_without_replaying_a_rejected_submission(payments):
    p=payments;body=p.body();p.remember('accept-one',body,error=LEGACY)
    actual=p.response(body);p.actual[(p.ref,'installment',1)]=actual
    with p.transport.operation('new-pay'):
        assert p.transport.send('pay','ap2.pay',body).body==actual
    state=p.transport.payment_recovery(p.ref,1)
    assert state['response']==actual and state['noPending'] is False and state['rejection'] is None
    assert not p.calls


def test_corrupt_actual_voucher_is_not_trusted_payment_evidence(payments):
    p=payments;body=p.body();p.remember('accept-one',body,error=REJECTED)
    actual=p.response(body);actual['voucher']['amount']=6;p.actual[(p.ref,'installment',1)]=actual
    with pytest.raises(Exception):p.transport.payment_recovery(p.ref,1)
    assert not p.calls


def test_new_body_attempt_in_one_parent_retains_the_original_rejected_checkpoint(payments):
    p=payments;old=p.body();operation=p.remember('pay-one',old,error=REJECTED)
    before=p.store.checkpoint(p.transport.namespace,'control:pay-one:ap2.pay')
    fresh=p.body()
    with p.transport.operation('pay-one'):
        p.transport.send('pay','ap2.pay',fresh)
        assert p.transport.control_receipt('ap2.pay')['payload']['ap2']==fresh
    assert p.calls[0][0]!=operation and p.calls[0][1]['ap2']==fresh
    assert p.store.checkpoint(p.transport.namespace,'control:pay-one:ap2.pay')==before
    assert {item['bodyDigest'] for item in p.transport.payment_attempts(p.ref,period=1)}=={digest(old),digest(fresh)}


def test_reconciliation_matches_the_original_key_and_every_upstream_attempt(payments):
    p=payments;body=p.body();operation=p.remember('accept-one',body,error=REJECTED)
    value={'paymentProvider':'pay','ref':{'cardDigest':p.ref},'purpose':'installment','period':1,
           'attempts':[{'body':body,'rejection':{'reference':operation,'bodyDigest':digest(body)}}]}
    value['key']=digest({'psp':'pay','payer':p.transport.identity.payment.jwk,'ref':value['ref'],'purpose':'installment','period':1})
    assert p.transport.reconcile_payment(value).no_pending is True
    wrong=copy.deepcopy(value);wrong['attempts'][0]['rejection']['reference']='foreign'
    with pytest.raises(RuntimeFault,match='every original'):p.transport.reconcile_payment(wrong)
    wrong=copy.deepcopy(value);wrong['key']='another'
    with pytest.raises(RuntimeFault,match='original key'):p.transport.reconcile_payment(wrong)


def test_http_404_alone_and_missing_local_control_cannot_authorize_retry(payments):
    p=payments
    assert p.transport.payment_recovery(p.ref,1)=={'attempts':[],'rejection':None,'response':None,'noPending':False}
    body=p.body();operation=p.remember('accept-one',body,pending=True)
    p.store.checkpoint_delete(p.transport.namespace,'control:accept-one:ap2.pay')
    state=p.transport.payment_recovery(p.ref,1)
    assert state['attempts'][0]['operationId']==operation and state['noPending'] is False


def test_local_operation_evidence_is_an_owned_snapshot_using_the_original_fingerprint(payments):
    p=payments;body=p.body();operation=p.remember('accept-one',body,error=REJECTED)
    row=p.store.operation('assistant','ap2.pay',operation)
    assert row['fingerprint']==hashlib.sha256(encode(row['payload']).encode()).hexdigest()
    row['payload']['admissionId']='changed';row['response']['error']['message']='changed'
    assert p.store.operation('assistant','ap2.pay',operation)['payload']['admissionId']=='admission'
    assert p.store.operation('foreign','ap2.pay',operation) is None
