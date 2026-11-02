"""Actual short-transaction rollback and public reasons for explicit PSP refusal."""
from decimal import Decimal
from types import SimpleNamespace

import pytest
from a2c.errors import A2CError, BAD_SIGNATURE, OUT_OF_ORDER
from ioalab.errors import LabError
from test_delegation_admission import configure, fee_body, intent, offer, online, opened, uid
from test_service_publication import service


@pytest.fixture
def held_payment(platform, client, local_client, service, monkeypatch):
    local=local_client
    template=offer(platform,service)
    invitation=intent(platform,client,local,service,template)
    with online(client,local,service) as (sender,receiver):
        configure(receiver)
        binding=opened(sender,receiver,invitation)
        held=receiver.call('admission.reserve',binding)
        body=fee_body(local,service[1],template,invitation)
        payload={'admissionId':held['admissionId'],'ap2':body}
        observed=[]
        original=platform.admissions.payment_in

        def observe_admission(conn,*args,**kwargs):
            created=original(conn,*args,**kwargs)
            state=conn.execute('SELECT state FROM delegation_admissions WHERE id=?',(held['admissionId'],)).fetchone()[0]
            rows=conn.execute('SELECT COUNT(*) FROM admission_payments WHERE admission=?',(held['admissionId'],)).fetchone()[0]
            observed.append({'created':created,'state':state,'paymentRows':rows})
            return created

        monkeypatch.setattr(platform.admissions,'payment_in',observe_admission)
        yield SimpleNamespace(sender=sender,receiver=receiver,payload=payload,admission=held['admissionId'],
            owner=local.account['id'],payee=service[0].account['id'],scope=invitation['body']['scopeDigest'],
            ref=invitation['body']['bindingDigest'],observed=observed)


def transaction_facts(platform,payment):
    db=platform.db
    return {
        'wallets':db.rows('SELECT * FROM wallets WHERE user_id IN (?,?) ORDER BY user_id',payment.owner,payment.payee),
        'walletLog':db.rows('SELECT * FROM wallet_log WHERE user_id IN (?,?) ORDER BY id',payment.owner,payment.payee),
        'transfers':db.rows('SELECT * FROM transfers WHERE ref=?',payment.ref),
        'paid':db.rows('SELECT * FROM paid WHERE ref=?',payment.ref),
        'paymentRecords':db.rows('SELECT * FROM payment_records WHERE ref=?',payment.ref),
        'scope':db.rows('SELECT * FROM delegation_scopes WHERE digest=?',payment.scope),
        'scopePayments':db.rows('SELECT * FROM delegation_scope_payments WHERE scope=?',payment.scope),
        'admission':db.rows('SELECT * FROM delegation_admissions WHERE id=?',payment.admission),
        'admissionPayments':db.rows('SELECT * FROM admission_payments WHERE admission=?',payment.admission),
        'operationReceipts':db.rows('SELECT * FROM delegation_payment_operations WHERE actor=?',payment.sender.actor),
    }


def assert_admission_write_was_rolled_back(platform,payment,before):
    assert payment.observed==[{'created':True,'state':'committed','paymentRows':1}]
    assert transaction_facts(platform,payment)==before
    assert not platform.db.conn.in_transaction


def test_insufficient_balance_preserves_actual_public_reason_and_rolls_back_all_facts(platform,held_payment):
    payment=held_payment
    platform.wallets.adjust(payment.owner,-platform.wallets.balance(payment.owner),by='isolated-test',note='commit refusal')
    assert platform.wallets.balance(payment.owner)==Decimal('0.00')
    before=transaction_facts(platform,payment)
    error=payment.sender.call('ap2.pay',payment.payload,uid(),ok=False)
    assert error=={'code':'payment_rejected','message':'card-invalid: 余量不足','recovery':'correct_input'}
    assert_admission_write_was_rolled_back(platform,payment,before)


@pytest.mark.parametrize('code,message',[
    (BAD_SIGNATURE,'Payment expired before commit'),
    (OUT_OF_ORDER,'Payment exists with another original request; retrieve its original receipt'),
])
def test_other_explicit_psp_reasons_preserve_original_code_and_message(platform,held_payment,monkeypatch,code,message):
    payment=held_payment
    prepared=platform.payment_operations.prepare(payment.owner,payment.sender.actor,uid(),payment.payload)
    before=transaction_facts(platform,payment)
    refusal=A2CError(code,message)

    def refuse(conn,original):
        raise refusal

    monkeypatch.setattr(platform.psp,'commit',refuse)
    with pytest.raises(LabError) as rejected:
        with platform.db.tx() as conn:
            platform.payment_operations.apply_in(conn,payment.owner,payment.sender.actor,prepared)
    assert rejected.value.code=='payment_rejected' and rejected.value.status==403
    assert rejected.value.public()=={'code':'payment_rejected','message':str(refusal),'recovery':'correct_input'}
    assert rejected.value.__cause__ is refusal
    assert_admission_write_was_rolled_back(platform,payment,before)


@pytest.mark.parametrize('error',[
    LabError('unknown_result','Original payment outcome is unavailable',status=409,recovery='reconcile_original'),
    OSError('payment storage unavailable'),
],ids=['unknown-result','unexpected-error'])
def test_unknown_and_unexpected_failures_keep_original_semantics_and_roll_back(platform,held_payment,monkeypatch,error):
    payment=held_payment
    prepared=platform.payment_operations.prepare(payment.owner,payment.sender.actor,uid(),payment.payload)
    before=transaction_facts(platform,payment)

    def unavailable(conn,original):
        platform.wallets.transfer_in(conn,original.payer,original.payee,original.amount,
            purpose=original.purpose,ref=original.ref,payment_id=original.payment_id)
        raise error

    monkeypatch.setattr(platform.psp,'commit',unavailable)
    with pytest.raises(type(error)) as failed:
        with platform.db.tx() as conn:
            platform.payment_operations.apply_in(conn,payment.owner,payment.sender.actor,prepared)
    assert failed.value is error
    if isinstance(error,LabError):
        assert error.code=='unknown_result' and error.recovery=='reconcile_original'
    assert_admission_write_was_rolled_back(platform,payment,before)
