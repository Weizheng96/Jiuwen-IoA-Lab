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
from ioalab.storage import DB
from _support import new_user


@pytest.fixture
def payment(platform,tmp_path,monkeypatch):
    from _resident_payment import signed_unpaid
    with signed_unpaid(platform,tmp_path,monkeypatch) as (provider,assistant,ref,body):
        yield assistant,ref,provider.identities[0],body


def execute_payment_component(component,body):
    """Exercise PSP prepare/commit/project, below the WS admission application."""
    prepared=component.prepare(body)
    with component.db.tx() as connection:
        reply,created=component.commit(connection,prepared)
    if created:component.project(prepared)
    return reply


def test_prepare_does_not_mutate_and_receipt_failure_rolls_back(platform,payment):
    c,cd,agent,body = payment
    psp = platform.psp
    before = platform.wallets.balance(c.account.account['id'])
    prepared = psp.prepare(body)
    assert platform.wallets.balance(c.account.account['id'])==before
    assert platform.db.one('SELECT 1 FROM paid WHERE ref=?',cd) is None
    platform.db.run("CREATE TRIGGER payment_write_failure BEFORE INSERT ON payment_records BEGIN SELECT RAISE(ABORT,'simulated disk failure'); END")
    try:
        with pytest.raises(sqlite3.IntegrityError,match='disk failure'):
            execute_payment_component(psp,body)
        assert platform.wallets.balance(c.account.account['id'])==before
        assert platform.db.one('SELECT 1 FROM paid WHERE ref=?',cd) is None
        assert platform.db.one('SELECT 1 FROM transfers WHERE ref=?',cd) is None
    finally:
        platform.db.run('DROP TRIGGER payment_write_failure')
    reply = execute_payment_component(psp,body)
    assert reply.body['voucher']['cardDigest']==cd
    assert platform.wallets.balance(c.account.account['id'])==before-prepared.amount


def test_concurrent_replay_restart_and_original_receipt(platform,client,payment):
    c,cd,_,body = payment
    psp = platform.psp
    with ThreadPoolExecutor(max_workers=4) as pool:
        replies = list(pool.map(lambda _:execute_payment_component(psp,body).body,range(4)))
    assert all(value==replies[0] for value in replies)
    records = platform.db.rows('SELECT * FROM transfers WHERE ref=?',cd)
    assert len(records)==1
    _,receipt = jwt_decode(replies[0]['paymentReceipt'])
    assert records[0]['payment_id']==receipt['payment_id']
    reopened = DB(platform.db.path)
    try:
        restored = SimPaymentProvider(psp.signer,keys=psp.keys,ap2_key=psp.ap2_key,parties=psp.parties,
                                      wallets=Wallets(reopened),client_matches=psp.client_matches,db=reopened)
        assert execute_payment_component(restored,body).body==replies[0]
    finally:
        reopened.close()
    # The persisted concurrent/restarted payment is also the original queried
    # after a caller loses its reply; reading it requires the payer's identity.
    token = {'Authorization':'Bearer '+c.account.token}
    query = client.get('/api/v1/payments',headers=token,params={'ref':cd,'purpose':'installment','period':1})
    assert query.status_code==200 and query.json()['paymentReceipt']==replies[0]['paymentReceipt']
    assert client.get('/api/v1/payments/'+receipt['payment_id'],headers=token).json()['voucher']==replies[0]['voucher']
    stranger = new_user(platform)
    denied = client.get('/api/v1/payments/'+receipt['payment_id'],headers={'Authorization':'Bearer '+platform.auth.bearer(stranger['id'])})
    assert denied.status_code==404 and denied.json()['error']['code']=='forbidden'


def test_expired_and_mismatched_signed_checkout_are_rejected(platform,payment):
    c,cd,agent,body = payment
    expired = copy.deepcopy(body)
    _,pm = jwt_decode(expired['paymentMandate'].rstrip('~'))
    pm.update(iat=int(time.time())-1000,exp=int(time.time())-100)
    expired['paymentMandate'] = jwt_sign(pm,c.identities[0].payment,typ='dc+sd-jwt',jwk_header=True)+'~'
    with pytest.raises(A2CError,match='expired'):
        execute_payment_component(platform.psp,expired)
    _,cm = jwt_decode(body['checkoutMandate'].rstrip('~'))
    _,checkout = jwt_decode(cm['checkout_jwt'])
    checkout['a2c']['cardDigest']='sha256:'+'0'*64
    wrong = jwt_sign(checkout,agent.payment)
    bad = {**body,'checkoutMandate':checkout_mandate(wrong,c.identities[0].payment,int(time.time())),
           'paymentMandate':payment_mandate(wrong,c.identities[0].payment,payee=checkout['merchant'],amount=checkout['a2c']['amount'],
                         currency=checkout['currency'],instrument={'id':'ioalab-'+c.account.account['id'],'type':'wallet'},now=int(time.time()))}
    with pytest.raises(A2CError) as wrong_object:
        execute_payment_component(platform.psp,bad)
    assert wrong_object.value.code=='card-invalid'
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
        assert execute_payment_component(psp,body).body==reply.body


def test_projection_failure_and_legacy_paid_are_not_repaid(platform,payment,monkeypatch):
    c,cd,_,body = payment
    psp = platform.psp
    platform.db.run('INSERT INTO paid(ref,purpose,period,at) VALUES(?,?,?,?)',cd,'installment',1,time.time())
    try:
        with pytest.raises(A2CError,match='reconciliation'):
            execute_payment_component(psp,body)
        assert platform.db.one('SELECT 1 FROM payment_records WHERE ref=?',cd) is None
    finally:
        platform.db.run('DELETE FROM paid WHERE ref=?',cd)
    legacy = platform.db.run('INSERT INTO transfers(at,payer_id,payee_id,amount,purpose,ref,payment_id) VALUES(?,?,?,?,?,?,?)',
                              time.time(),c.account.account['id'],'historical-account','1','installment',cd,'historical-without-receipt').lastrowid
    try:
        with pytest.raises(A2CError,match='reconciliation'):
            execute_payment_component(psp,body)
        assert len(platform.db.rows('SELECT * FROM transfers WHERE ref=?',cd))==1
    finally:
        platform.db.run('DELETE FROM transfers WHERE id=?',legacy)
    def failed_observation(_):
        raise OSError('observation disk failure')
    monkeypatch.setattr(psp,'on_transfer',failed_observation)
    reply = execute_payment_component(psp,body)
    assert execute_payment_component(psp,body).body==reply.body
    assert len(platform.db.rows('SELECT * FROM transfers WHERE ref=?',cd))==1
    from ioalab.cli import main
    accounts=platform.db.rows('SELECT * FROM users ORDER BY id')
    payments=platform.db.rows('SELECT * FROM payment_records WHERE ref=?',cd)
    transfers=platform.db.rows('SELECT * FROM transfers WHERE ref=?',cd)
    with pytest.raises(SystemExit) as rejected:main(['reset','--keep-accounts'])
    assert rejected.value.code==2
    assert platform.db.rows('SELECT * FROM users ORDER BY id')==accounts
    assert platform.db.rows('SELECT * FROM payment_records WHERE ref=?',cd)==payments
    assert platform.db.rows('SELECT * FROM transfers WHERE ref=?',cd)==transfers


def test_new_mandate_for_an_existing_payment_does_not_create_another_receipt(platform,payment):
    _,cd,_,body = payment
    psp = platform.psp
    original = execute_payment_component(psp,body)
    c = payment[0]
    _,claims = jwt_decode(body['paymentMandate'].rstrip('~'))
    changed = {**body,'paymentMandate':jwt_sign(claims,c.identities[0].payment,typ='dc+sd-jwt',jwk_header=True)+'~'}
    assert changed['paymentMandate']!=body['paymentMandate']
    with pytest.raises(A2CError,match='another original request'):
        execute_payment_component(psp,changed)
    with pytest.raises(A2CError,match='another original request'):
        execute_payment_component(psp,{**body,'signature':'not-part-of-the-original-message'})
    assert len(platform.db.rows('SELECT * FROM transfers WHERE ref=?',cd))==1
    assert execute_payment_component(psp,body).body==original.body


def test_identity_changes_between_prepare_and_commit_are_fenced(platform,payment,monkeypatch):
    c,cd,_,body = payment
    psp = platform.psp
    prepared = psp.prepare(body)
    before = platform.wallets.balance(c.account.account['id'])
    with monkeypatch.context() as patch:
        original_parties=psp.parties
        patch.setattr(psp,'parties',lambda party:{**original_parties(party),'owner':'another-owner'}
                      if party==prepared.payee_party else original_parties(party))
        with pytest.raises(A2CError,match='binding changed'):
            with platform.db.tx() as conn:
                psp.commit(conn,prepared)
    assert platform.wallets.balance(c.account.account['id'])==before
    assert platform.db.one('SELECT 1 FROM paid WHERE ref=?',cd) is None
    assert platform.db.one('SELECT 1 FROM transfers WHERE ref=?',cd) is None


def test_valid_registered_payer_cannot_pay_another_accounts_contract(platform,payment,tmp_path):
    from ioalab_sdk.runtime.config import RuntimeConfig
    from ioalab_sdk.runtime.resident import ResidentRuntime
    from ioalab_sdk.runtime.files import private_json
    from _support import PASSWORD
    _,cd,_,body = payment
    stranger = new_user(platform)
    config=tmp_path/'payer.json'
    private_json(config,{'platform_url':platform.s.base_url,'username':stranger['username'],'password':PASSWORD,'data_dir':'./other-payer','identities':[{'key':'personal','role':'assistant','name':'不同付款人'}]})
    with_client=ResidentRuntime(RuntimeConfig.load(config))
    try:
        _,cm = jwt_decode(body['checkoutMandate'].rstrip('~'))
        checkout = cm['checkout_jwt']
        _,claims = jwt_decode(checkout)
        changed = {**body,'checkoutMandate':checkout_mandate(checkout,with_client.identities[0].payment,int(time.time())),
                   'paymentMandate':payment_mandate(checkout,with_client.identities[0].payment,payee=claims['merchant'],amount=claims['a2c']['amount'],
                                     currency=claims['currency'],instrument={'id':'ioalab-'+stranger['id'],'type':'wallet'},now=int(time.time()))}
        with pytest.raises(A2CError,match='does not own'):
            execute_payment_component(platform.psp,changed)
        assert platform.db.one('SELECT 1 FROM paid WHERE ref=?',cd) is None
    finally:
        with_client.close()



def test_bare_public_ap2_cannot_bypass_the_current_connection_and_admission(platform,payment):
    _,ref,_,body=payment
    with pytest.raises(A2CError,match='execution connection and admission context') as rejected:
        platform.psp.op_ap2_pay(body,{},None)
    assert rejected.value.code=='out-of-order'
    assert platform.db.one('SELECT 1 FROM paid WHERE ref=?',ref) is None
    assert platform.db.one('SELECT 1 FROM transfers WHERE ref=?',ref) is None
