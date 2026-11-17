"""Recover accepted installments without another report, signature or charge."""
import copy

import pytest

from a2c.card import card_digest
from a2c.errors import A2CError
from a2c.codec import digest
from a2c.payment import PaymentRejected, PaymentReconciliation
from .support import PAY, PROVIDER, World


def ready():
    world = World()
    card = world.formed_card('preapproval', deposit='0', steps=False)
    world.client.start(card)
    reference = card_digest(card)
    world.deliver(reference, 1)
    world.client.fetch_delivery(reference, 1)
    return world, reference


def test_unknown_payment_reuses_the_original_mandates_without_another_acceptance(monkeypatch):
    world, reference = ready()
    original = world.net.send
    lost = []
    def send(to, op, body, *args, **kwargs):
        if to == PAY and op == 'ap2.pay' and not lost:
            lost.append(copy.deepcopy(body))
            raise TimeoutError('Original submission outcome unavailable')
        return original(to, op, body, *args, **kwargs)
    monkeypatch.setattr(world.net, 'send', send)
    with pytest.raises(TimeoutError):
        world.client.accept(reference, 1)
    signatures = copy.deepcopy(world.client.card(reference)['signatures']['7'])
    monkeypatch.setattr(world.client, '_run_period', lambda *_: pytest.fail('Payment reran acceptance'))
    voucher = world.client.pay(reference, 1)
    assert voucher['purpose'] == 'installment' and voucher['period'] == 1
    assert world.client.card(reference)['signatures']['7'] == signatures
    assert len(world.psp.ledger) == 1
    assert [body for to, op, body in world.net.log if to == PAY and op == 'ap2.pay'] == [{'op': 'ap2.pay', **lost[0]}]
    assert len([1 for to, op, _ in world.net.log if op == 'ap2.checkout']) == 1
    assert world.client.pay(reference, 1) == voucher
    assert len(world.psp.ledger) == 1


@pytest.mark.parametrize('after_effect', [False, True])
def test_paid_voucher_survives_a_lost_notification_before_the_method_returns(monkeypatch, after_effect):
    world, reference = ready()
    original = world.net.send
    blocked = []
    def send(to, op, body, *args, **kwargs):
        if to == PROVIDER and op == 'payment.notify' and not blocked:
            blocked.append(copy.deepcopy(body))
            if after_effect:
                original(to, op, body, *args, **kwargs)
            raise TimeoutError('Payment notification response lost')
        return original(to, op, body, *args, **kwargs)
    monkeypatch.setattr(world.net, 'send', send)
    with pytest.raises(TimeoutError):
        world.client.accept(reference, 1)
    record = world.client.store.get('cards', reference)
    assert record['vouchers'] == [blocked[0]['voucher']]
    assert not record.get('paymentNotifiedPeriods')
    signed = copy.deepcopy(record['card']['signatures']['7'])
    monkeypatch.setattr(world.client, '_run_period', lambda *_: pytest.fail('Payment reran acceptance'))
    world.clock.advance(hours=1)
    voucher = world.client.pay(reference, 1)
    assert voucher == blocked[0]['voucher']
    assert world.client.card(reference)['signatures']['7'] == signed
    assert world.client.store.get('cards', reference)['paymentNotifiedPeriods'] == [1]
    assert world.provider.store.get('cards', reference)['vouchers'] == [voucher]
    count = len(world.net.log)
    assert world.client.pay(reference, 1) == voucher and len(world.net.log) == count
    assert len(world.psp.ledger) == 1


@pytest.mark.parametrize('period', [False, 0, 1, 2, '1', None])
def test_payment_requires_the_actual_already_accepted_period(period):
    world, reference = ready()
    before = len(world.net.log)
    with pytest.raises(A2CError):
        world.client.pay(reference, period)
    assert len(world.net.log) == before and not world.psp.ledger


def test_unknown_expired_attempt_does_not_obtain_a_new_checkout(monkeypatch):
    world, reference = ready()
    original = world.net.send
    def unavailable(to, op, body, *args, **kwargs):
        if to == PAY and op == 'ap2.pay':
            raise TimeoutError('Original attempt is still unknown')
        return original(to, op, body, *args, **kwargs)
    monkeypatch.setattr(world.net, 'send', unavailable)
    with pytest.raises(TimeoutError):
        world.client.accept(reference, 1)
    original_record = copy.deepcopy(world.client.store.all('ap2_payments'))
    world.clock.advance(minutes=20)
    monkeypatch.setattr(world.net, 'send', original)
    with pytest.raises(A2CError):
        world.client.pay(reference, 1)
    assert world.client.store.all('ap2_payments') == original_record
    assert len([1 for _, op, _ in world.net.log if op == 'ap2.checkout']) == 1
    assert not world.psp.ledger


def reject_once(world, monkeypatch, *, provider=PAY, wrong_digest=False):
    original = world.net.send
    rejected = []
    def send(to, op, body, *args, **kwargs):
        if to == PAY and op == 'ap2.pay' and not rejected:
            rejected.append(copy.deepcopy(body))
            raise PaymentRejected(payment_provider=provider,
                body_digest='sha256:'+'0'*64 if wrong_digest else digest(body),
                reference='authenticated-original-rejection',code='credit_unavailable',
                message='This original simulated submission did not commit')
        return original(to, op, body, *args, **kwargs)
    monkeypatch.setattr(world.net, 'send', send)
    return original, rejected


def test_definite_rejection_keeps_the_original_attempt_before_authorized_retry(monkeypatch):
    world, reference = ready()
    _, rejected = reject_once(world, monkeypatch)
    with pytest.raises(PaymentRejected):
        world.client.accept(reference, 1)
    signatures = copy.deepcopy(world.client.card(reference)['signatures']['7'])
    before = copy.deepcopy(world.client.store.all('ap2_payments'))
    with pytest.raises(A2CError, match='reconcile'):
        world.client.pay(reference, 1)
    assert world.client.store.all('ap2_payments') == before
    reconciliations = []
    def reconcile(value):
        reconciliations.append(value)
        return PaymentReconciliation(value['key'], no_pending=True)
    world.client.payment.reconcile = reconcile
    world.clock.advance(minutes=20)
    monkeypatch.setattr(world.client, '_run_period', lambda *_: pytest.fail('Payment reran acceptance'))
    voucher = world.client.pay(reference, 1)
    assert len(world.psp.ledger) == 1 and voucher['period'] == 1
    assert world.client.card(reference)['signatures']['7'] == signatures
    current = next(iter(world.client.store.all('ap2_payments').values()))
    assert current['attempts'][0]['body'] == rejected[0]
    assert current['attempts'][0]['rejection']['reference'] == 'authenticated-original-rejection'
    assert current['body']['paymentMandate'] != rejected[0]['paymentMandate']
    assert len(reconciliations) == 1 and reconciliations[0]['ref'] == {'cardDigest': reference}


@pytest.mark.parametrize('wrong_key', [False, True])
def test_missing_success_receipt_does_not_prove_no_unresolved_attempt(monkeypatch, wrong_key):
    world, reference = ready()
    reject_once(world, monkeypatch)
    with pytest.raises(PaymentRejected):
        world.client.accept(reference, 1)
    before = copy.deepcopy(world.client.store.all('ap2_payments'))
    world.client.payment.reconcile = lambda value: PaymentReconciliation(
        'another-payment-key' if wrong_key else value['key'], no_pending=False)
    with pytest.raises(A2CError):
        world.client.pay(reference, 1)
    assert world.client.store.all('ap2_payments') == before
    assert len([1 for _, op, _ in world.net.log if op == 'ap2.checkout']) == 1
    assert not world.psp.ledger


@pytest.mark.parametrize('foreign_provider,wrong_digest', [(True,False),(False,True)])
def test_rejection_must_bind_the_exact_provider_and_original_submission(monkeypatch, foreign_provider, wrong_digest):
    world, reference = ready()
    reject_once(world, monkeypatch, provider='https://foreign-psp.test' if foreign_provider else PAY, wrong_digest=wrong_digest)
    with pytest.raises(A2CError, match='rejection does not bind'):
        world.client.accept(reference, 1)
    current = next(iter(world.client.store.all('ap2_payments').values()))
    assert 'body' in current and 'rejection' not in current and not world.psp.ledger


def test_authoritative_lookup_recovers_an_existing_payment_instead_of_a_new_checkout(monkeypatch):
    world, reference = ready()
    original, rejected = reject_once(world, monkeypatch)
    with pytest.raises(PaymentRejected):
        world.client.accept(reference, 1)
    actual = original(PAY, 'ap2.pay', rejected[0]).body
    world.client.payment.reconcile = lambda value: PaymentReconciliation(value['key'], response=actual)
    world.clock.advance(hours=1)
    voucher = world.client.pay(reference, 1)
    assert voucher == actual['voucher'] and len(world.psp.ledger) == 1
    assert len([1 for _, op, _ in world.net.log if op == 'ap2.checkout']) == 1


def test_parallel_authorized_retry_posts_once_and_preserves_the_rejection(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    world, reference = ready()
    reject_once(world, monkeypatch)
    with pytest.raises(PaymentRejected):
        world.client.accept(reference, 1)
    world.client.payment.reconcile = lambda value: PaymentReconciliation(value['key'], no_pending=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        vouchers = list(pool.map(lambda _: world.client.pay(reference, 1), range(8)))
    assert all(voucher == vouchers[0] for voucher in vouchers)
    assert len(world.psp.ledger) == 1
    current = next(iter(world.client.store.all('ap2_payments').values()))
    assert len(current['attempts']) == 1


def test_old_period_payment_recovery_cannot_overwrite_the_next_accepted_period(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    world, reference = ready()
    original_send = world.net.send
    lost = []
    def send(to, op, body, *args, **kwargs):
        result = original_send(to, op, body, *args, **kwargs)
        if to == PROVIDER and op == 'payment.notify' and not lost:
            lost.append(body)
            raise TimeoutError('Original payment notification was applied but its reply was lost')
        return result
    monkeypatch.setattr(world.net, 'send', send)
    with pytest.raises(TimeoutError):
        world.client.accept(reference, 1)
    monkeypatch.setattr(world.net, 'send', original_send)
    world.deliver(reference, 2)
    world.client.fetch_delivery(reference, 2)
    original_get = world.client.store.get
    snapshot_ready, release, accept_started, accept_read = (threading.Event() for _ in range(4))
    reads = [0]
    def get(table, key, default=None):
        value = original_get(table, key, default)
        if table == 'cards' and key == reference:
            if threading.current_thread().name.startswith('old-period'):
                reads[0] += 1
                if reads[0] == 2:
                    snapshot_ready.set()
                    assert release.wait(5), 'Old-period recovery was not released'
            elif threading.current_thread().name.startswith('new-period'):
                accept_read.set()
        return value
    monkeypatch.setattr(world.client.store, 'get', get)
    def accept_next():
        accept_started.set()
        return world.client.accept(reference, 2)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix='old-period') as old, \
         ThreadPoolExecutor(max_workers=1, thread_name_prefix='new-period') as new:
        recovery = old.submit(world.client.pay, reference, 1)
        try:
            assert snapshot_ready.wait(5)
            acceptance = new.submit(accept_next)
            assert accept_started.wait(5)
            assert not accept_read.wait(.15), 'Another lifecycle writer read a card with an unresolved earlier writer'
        finally:
            release.set()
        recovery.result(timeout=5)
        assert acceptance.result(timeout=5).status == 'pass'
    record = world.client.store.get('cards', reference)
    assert len(record['card']['signatures']['7']) == 2
    assert {v['period'] for v in record['vouchers']} == {1, 2}
    assert set(record['paymentNotifiedPeriods']) == {1, 2}
    assert len(world.psp.ledger) == 2


def test_a_corrupt_cached_voucher_cannot_trigger_any_notification_or_payment():
    world, reference = ready()
    world.client.accept(reference, 1)
    record = world.client.store.get('cards', reference)
    record['vouchers'][0]['amount'] += 1
    world.client.store.put('cards', reference, record)
    before = len(world.net.log)
    with pytest.raises(A2CError):
        world.client.pay(reference, 1)
    assert len(world.net.log) == before and len(world.psp.ledger) == 1


def test_foreign_client_cannot_recover_a_stored_accepted_card():
    from a2c.client import Client
    from a2c.jws import Signer
    world, reference = ready()
    world.client.accept(reference, 1)
    foreign = Client(Signer.generate('urn:foreign-client'), keys=world.keys, transport=world.net,
        fetch=world.fetch, payment_provider=world.client.payment_provider, clock=world.clock,
        store=world.client.store, payment=world.client.payment)
    before = len(world.net.log)
    with pytest.raises(A2CError, match='another client'):
        foreign.pay(reference, 1)
    assert len(world.net.log) == before and len(world.psp.ledger) == 1


def test_zero_installment_returns_no_fictitious_payment(monkeypatch):
    from . import support
    original = support.template_draft
    def free_terms(*args, **kwargs):
        template = original(*args, **kwargs)
        template['pricing']['priceFormula'] = {'outline': '0', 'final': '0'}
        return template
    monkeypatch.setattr(support, 'template_draft', free_terms)
    world, reference = ready()
    assert world.client.accept(reference, 1).voucher is None
    before = len(world.net.log)
    assert world.client.pay(reference, 1) is None
    assert len(world.net.log) == before and not world.psp.ledger


@pytest.mark.parametrize('known_rejected', [False, True])
def test_later_delivery_does_not_replace_an_earlier_payment_attempt(monkeypatch, known_rejected):
    world, reference = ready()
    original = world.net.send
    bodies = []
    def send(to, op, body, *args, **kwargs):
        if to == PAY and op == 'ap2.pay' and not bodies:
            bodies.append(copy.deepcopy(body))
            if known_rejected:
                raise PaymentRejected(payment_provider=PAY, body_digest=digest(body), reference='first-attempt',
                                      code='credit_unavailable', message='This exact submission did not commit')
            raise TimeoutError('Original payment result remains unknown')
        return original(to, op, body, *args, **kwargs)
    monkeypatch.setattr(world.net, 'send', send)
    with pytest.raises(PaymentRejected if known_rejected else TimeoutError):
        world.client.accept(reference, 1)
    world.deliver(reference, 2)
    world.client.fetch_delivery(reference, 2)
    current = copy.deepcopy(world.client.card(reference))
    if known_rejected:
        world.client.payment.reconcile = lambda value: PaymentReconciliation(value['key'], no_pending=True)
        world.clock.advance(minutes=20)
    voucher = world.client.pay(reference, 1)
    assert voucher['period'] == 1 and len(world.psp.ledger) == 1
    assert world.client.card(reference) == current
    payments = [body for to, op, body in world.net.log if to == PAY and op == 'ap2.pay']
    assert payments[0]['card'] == bodies[0]['card']
    if not known_rejected:
        assert payments == [{'op': 'ap2.pay', **bodies[0]}]


def test_lifecycle_serialization_keeps_the_original_keyword_calling_convention():
    world = World()
    card = world.formed_card('preapproval', deposit='0', steps=False)
    world.client.start(card=card)
    reference = card_digest(card)
    world.deliver(reference, 1)
    world.client.fetch_delivery(cd=reference, period=1)
    assert world.client.verify(cd=reference, period=1).status == 'pass'
    assert world.client.accept(cd=reference, period=1).status == 'pass'
    assert world.client.pay(cd=reference, period=1)['period'] == 1


@pytest.mark.parametrize('receipt', [{'op':'payment.notify','ok':False}, {'op':'payment.notify'}])
def test_only_a_positive_notification_receipt_marks_the_voucher_notified(monkeypatch, receipt):
    from a2c.common import Reply
    world, reference = ready()
    original = world.net.send
    attempts = []
    def send(to, op, body, *args, **kwargs):
        if to == PROVIDER and op == 'payment.notify' and not attempts:
            attempts.append(body)
            return Reply(receipt)
        return original(to, op, body, *args, **kwargs)
    monkeypatch.setattr(world.net, 'send', send)
    with pytest.raises(A2CError, match='positive receipt'):
        world.client.accept(reference, 1)
    record = world.client.store.get('cards', reference)
    assert len(record['vouchers']) == 1 and not record.get('paymentNotifiedPeriods')
    assert not world.provider.store.get('cards', reference).get('vouchers')
    voucher = world.client.pay(reference, 1)
    assert len(world.psp.ledger) == 1
    assert world.provider.store.get('cards', reference)['vouchers'] == [voucher]


def test_original_attempt_recovery_does_not_ignore_changed_non_card_parameters(monkeypatch):
    world, reference = ready()
    original = world.net.send
    def unavailable(to, op, body, *args, **kwargs):
        if to == PAY and op == 'ap2.pay':
            raise TimeoutError('Original attempt remains unknown')
        return original(to, op, body, *args, **kwargs)
    monkeypatch.setattr(world.net, 'send', unavailable)
    with pytest.raises(TimeoutError):
        world.client.accept(reference, 1)
    card = world.client.card(reference)
    template = world.client.template(card['templateDigest'])
    before = copy.deepcopy(world.client.store.all('ap2_payments'))
    with pytest.raises(A2CError, match='original payment objects'):
        world.client.payment.pay(purpose='installment', template=template, card=card, period=1, deposit_paid=True)
    assert world.client.store.all('ap2_payments') == before and not world.psp.ledger
