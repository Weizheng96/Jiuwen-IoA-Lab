"""Real A2C acceptance and signed AP2 messages, with failures at transaction boundaries."""
from concurrent.futures import ThreadPoolExecutor
import copy
from datetime import timedelta
import sqlite3
import time

import pytest
from a2c.errors import A2CError
from a2c.codec import fmt_time
from a2c.jws import header_of
from a2c.payment.ap2 import jwt_decode, jwt_sign, checkout_mandate, payment_mandate
from ioalab.payments import SimPaymentProvider, Wallets
from ioalab.store import DB
from _support import prepare, fetch_when_ready, new_user


@pytest.fixture
def payment(platform,providers,local_client,monkeypatch):
    c = local_client
    _,template,cd,agent = prepare(c,providers)
    fetch_when_ready(c,cd)
    original_send = c.transport.send
    captured = []
    class BeforePayment(Exception):
        pass
    def capture(to,op,body,*args,**kwargs):
        if to==platform.pay_id and op=='ap2.pay':
            captured.append(copy.deepcopy(body))
            raise BeforePayment()
        return original_send(to,op,body,*args,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(c.transport,'send',capture)
        with pytest.raises(BeforePayment):
            c.accept(cd,1,confirmed=True)
    assert captured and c.client.card(cd)['signatures']['7']
    assert platform.db.one('SELECT 1 FROM paid WHERE ref=?',cd) is None
    return c,cd,agent,captured[0]


def test_prepare_does_not_mutate_and_receipt_failure_rolls_back(platform,payment):
    c,cd,agent,body = payment
    psp = platform.psp
    before = platform.wallets.balance(c.account['id'])
    prepared = psp.prepare(body)
    assert platform.wallets.balance(c.account['id'])==before
    assert platform.db.one('SELECT 1 FROM paid WHERE ref=?',cd) is None
    platform.db.run("CREATE TRIGGER payment_write_failure BEFORE INSERT ON payment_records BEGIN SELECT RAISE(ABORT,'simulated disk failure'); END")
    try:
        with pytest.raises(sqlite3.IntegrityError,match='disk failure'):
            psp.op_ap2_pay(body,{},None)
        assert platform.wallets.balance(c.account['id'])==before
        assert platform.db.one('SELECT 1 FROM paid WHERE ref=?',cd) is None
        assert platform.db.one('SELECT 1 FROM transfers WHERE ref=?',cd) is None
    finally:
        platform.db.run('DROP TRIGGER payment_write_failure')
    reply = psp.op_ap2_pay(body,{},None)
    assert reply.body['voucher']['cardDigest']==cd
    assert platform.wallets.balance(c.account['id'])==before-prepared.amount


def test_concurrent_replay_restart_and_original_receipt(platform,payment):
    c,cd,_,body = payment
    psp = platform.psp
    with ThreadPoolExecutor(max_workers=4) as pool:
        replies = list(pool.map(lambda _:psp.op_ap2_pay(body,{},None).body,range(4)))
    assert all(value==replies[0] for value in replies)
    records = platform.db.rows('SELECT * FROM transfers WHERE ref=?',cd)
    assert len(records)==1
    _,receipt = jwt_decode(replies[0]['paymentReceipt'])
    assert records[0]['payment_id']==receipt['payment_id']
    reopened = DB(platform.db.path)
    try:
        restored = SimPaymentProvider(psp.signer,keys=psp.keys,ap2_key=psp.ap2_key,payee_keys=psp.payee_keys,
                                      wallets=Wallets(reopened),payee_of=psp.payee_of,client_matches=psp.client_matches,db=reopened)
        assert restored.op_ap2_pay(body,{},None).body==replies[0]
    finally:
        reopened.close()


def test_response_lost_lookup_is_private_and_uses_original(platform,client,payment):
    c,cd,agent,body = payment
    reply = platform.psp.op_ap2_pay(body,{},None)
    _,receipt = jwt_decode(reply.body['paymentReceipt'])
    token = {'Authorization':'Bearer '+c.token}
    query = client.get('/api/v1/payments',headers=token,params={'ref':cd,'purpose':'installment','period':1})
    assert query.status_code==200 and query.json()['paymentReceipt']==reply.body['paymentReceipt']
    assert client.get('/api/v1/payments/'+receipt['payment_id'],headers=token).json()['voucher']==reply.body['voucher']
    stranger = new_user(platform)
    denied = client.get('/api/v1/payments/'+receipt['payment_id'],headers={'Authorization':'Bearer '+platform.auth.bearer(stranger['id'])})
    assert denied.status_code==404 and denied.json()['error']['code']=='forbidden'


def test_expired_and_mismatched_signed_checkout_are_rejected(platform,payment):
    c,cd,agent,body = payment
    expired = copy.deepcopy(body)
    _,pm = jwt_decode(expired['paymentMandate'].rstrip('~'))
    pm.update(iat=int(time.time())-1000,exp=int(time.time())-100)
    expired['paymentMandate'] = jwt_sign(pm,c.payer,typ='dc+sd-jwt',jwk_header=True)+'~'
    with pytest.raises(A2CError,match='expired'):
        platform.psp.op_ap2_pay(expired,{},None)
    _,cm = jwt_decode(body['checkoutMandate'].rstrip('~'))
    _,checkout = jwt_decode(cm['checkout_jwt'])
    checkout['a2c']['cardDigest']='sha256:'+'0'*64
    wrong = jwt_sign(checkout,agent.provider.ap2_key)
    bad = {**body,'checkoutMandate':checkout_mandate(wrong,c.payer,int(time.time())),
           'paymentMandate':payment_mandate(wrong,c.payer,payee=checkout['merchant'],amount=checkout['a2c']['amount'],
                         currency=checkout['currency'],instrument={'id':'ioalab-'+c.account['id'],'type':'wallet'},now=int(time.time()))}
    with pytest.raises(A2CError,match='actual protocol object'):
        platform.psp.op_ap2_pay(bad,{},None)
    assert platform.db.one('SELECT 1 FROM paid WHERE ref=?',cd) is None


def test_commit_rechecks_expiry_and_paid_result_survives_expiry(platform,payment,monkeypatch):
    _,cd,_,body = payment
    psp = platform.psp
    prepared = psp.prepare(body)
    original_now = psp.now
    with monkeypatch.context() as patch:
        patch.setattr(psp,'now',lambda:original_now()+timedelta(minutes=16))
        with pytest.raises(A2CError,match='expired before commit'):
            with platform.db.tx() as conn:
                psp.commit(conn,prepared)
    clock = [original_now()]
    ownership = psp.client_matches
    def identity_check_reaches_deadline(owner,party):
        clock[0] = original_now()+timedelta(minutes=16)
        return ownership(owner,party)
    with monkeypatch.context() as patch:
        patch.setattr(psp,'now',lambda:clock[0])
        patch.setattr(psp,'client_matches',identity_check_reaches_deadline)
        with pytest.raises(A2CError,match='expired before commit'):
            with platform.db.tx() as conn:
                psp.commit(conn,prepared)
    assert platform.db.one('SELECT 1 FROM paid WHERE ref=?',cd) is None
    committed_at = original_now()+timedelta(seconds=20)
    with monkeypatch.context() as patch:
        patch.setattr(psp,'now',lambda:committed_at)
        with platform.db.tx() as conn:
            reply,created = psp.commit(conn,prepared)
    assert created
    _,receipt = jwt_decode(reply.body['paymentReceipt'])
    assert receipt['iat']==int(committed_at.timestamp())
    assert header_of(reply.body['voucher']['signature'])['a2c-ts']==fmt_time(committed_at)
    assert platform.db.one('SELECT created FROM payment_records WHERE ref=?',cd)['created']==committed_at.timestamp()
    with monkeypatch.context() as patch:
        patch.setattr(psp,'now',lambda:original_now()+timedelta(days=1))
        assert psp.op_ap2_pay(body,{},None).body==reply.body


def test_projection_failure_and_legacy_paid_are_not_repaid(platform,payment,monkeypatch):
    c,cd,_,body = payment
    psp = platform.psp
    platform.db.run('INSERT INTO paid(ref,purpose,period,at) VALUES(?,?,?,?)',cd,'installment',1,time.time())
    try:
        with pytest.raises(A2CError,match='reconciliation'):
            psp.op_ap2_pay(body,{},None)
        assert platform.db.one('SELECT 1 FROM payment_records WHERE ref=?',cd) is None
    finally:
        platform.db.run('DELETE FROM paid WHERE ref=?',cd)
    legacy = platform.db.run('INSERT INTO transfers(at,payer_id,payee_id,amount,purpose,ref,payment_id) VALUES(?,?,?,?,?,?,?)',
                              time.time(),c.account['id'],'historical-account','1','installment',cd,'historical-without-receipt').lastrowid
    try:
        with pytest.raises(A2CError,match='reconciliation'):
            psp.op_ap2_pay(body,{},None)
        assert len(platform.db.rows('SELECT * FROM transfers WHERE ref=?',cd))==1
    finally:
        platform.db.run('DELETE FROM transfers WHERE id=?',legacy)
    def failed_observation(_):
        raise OSError('observation disk failure')
    monkeypatch.setattr(psp,'on_transfer',failed_observation)
    reply = psp.op_ap2_pay(body,{},None)
    assert psp.op_ap2_pay(body,{},None).body==reply.body
    assert len(platform.db.rows('SELECT * FROM transfers WHERE ref=?',cd))==1
    with pytest.raises(A2CError,match='付款证据'):
        platform.reset_transactions()


def test_new_mandate_for_an_existing_payment_does_not_create_another_receipt(platform,payment):
    _,cd,_,body = payment
    psp = platform.psp
    original = psp.op_ap2_pay(body,{},None)
    c = payment[0]
    _,claims = jwt_decode(body['paymentMandate'].rstrip('~'))
    changed = {**body,'paymentMandate':jwt_sign(claims,c.payer,typ='dc+sd-jwt',jwk_header=True)+'~'}
    assert changed['paymentMandate']!=body['paymentMandate']
    with pytest.raises(A2CError,match='another original request'):
        psp.op_ap2_pay(changed,{},None)
    with pytest.raises(A2CError,match='another original request'):
        psp.op_ap2_pay({**body,'signature':'not-part-of-the-original-message'},{},None)
    assert len(platform.db.rows('SELECT * FROM transfers WHERE ref=?',cd))==1
    assert psp.op_ap2_pay(body,{},None).body==original.body


def test_identity_changes_between_prepare_and_commit_are_fenced(platform,payment,monkeypatch):
    c,cd,_,body = payment
    psp = platform.psp
    prepared = psp.prepare(body)
    before = platform.wallets.balance(c.account['id'])
    with monkeypatch.context() as patch:
        patch.setattr(psp,'payee_of',lambda provider:'another-owner')
        with pytest.raises(A2CError,match='binding changed'):
            with platform.db.tx() as conn:
                psp.commit(conn,prepared)
    assert platform.wallets.balance(c.account['id'])==before
    assert platform.db.one('SELECT 1 FROM paid WHERE ref=?',cd) is None
    assert platform.db.one('SELECT 1 FROM transfers WHERE ref=?',cd) is None


def test_valid_registered_payer_cannot_pay_another_accounts_contract(platform,payment,tmp_path):
    from ioalab_sdk import LabClient
    from _support import PASSWORD
    _,cd,_,body = payment
    stranger = new_user(platform)
    with_client = LabClient(platform.s.base_url,stranger['username'],PASSWORD,tmp_path/'other-payer')
    try:
        _,cm = jwt_decode(body['checkoutMandate'].rstrip('~'))
        checkout = cm['checkout_jwt']
        _,claims = jwt_decode(checkout)
        changed = {**body,'checkoutMandate':checkout_mandate(checkout,with_client.payer,int(time.time())),
                   'paymentMandate':payment_mandate(checkout,with_client.payer,payee=claims['merchant'],amount=claims['a2c']['amount'],
                                     currency=claims['currency'],instrument={'id':'ioalab-'+stranger['id'],'type':'wallet'},now=int(time.time()))}
        with pytest.raises(A2CError,match='does not own'):
            platform.psp.op_ap2_pay(changed,{},None)
        assert platform.db.one('SELECT 1 FROM paid WHERE ref=?',cd) is None
    finally:
        with_client.close()
