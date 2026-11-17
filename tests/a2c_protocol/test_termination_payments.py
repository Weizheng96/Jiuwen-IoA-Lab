"""Real signed AP2 settlement, original evidence and restart/response-loss recovery."""
import copy
from decimal import Decimal

import pytest

from a2c.card import card_digest
from a2c.common import Endpoint, Reply, Store
from a2c.errors import A2CError, INVALID_OBJECT, fail
from a2c import objects
from a2c.payment.ap2 import AP2Payment, EcKey, checkout, checkout_mandate, jwt_decode, jwt_verify, payment_mandate
from a2c.payment.psp import PaymentProvider
from a2c.provider import Provider
from .support import MARKET, PAY, PROVIDER, ProposalPolicy, World


class RefundPayee(Endpoint):
    OPS = ('ap2.checkout',)

    def __init__(self, client):
        super().__init__(); self.client = client

    def op_ap2_checkout(self, body, files, task_id):
        if set(body) != {'purpose','cardDigest'} or body['purpose'] != 'termination' or files:
            fail(INVALID_OBJECT, 'this payee only issues original contract refund checkouts')
        return Reply({'op':'ap2.checkout','checkoutJwt':self.client.refund_checkout(body['cardDigest'])})


def formed(world, formation='countersign', deposit='0.1 * total', *, bind=True):
    if formation == 'countersign':
        card = world.formed_card(formation, deposit=deposit)
    else:
        template = world.publish('proposal', deposit=deposit)
        [template] = world.client.get_templates(PROVIDER, template['templateDigest'])
        request = world.client.draft_request(template, request_id='termination-request',
            spec=world.spec(), materials=world.materials(), signers={'slot1':'user','slot7':'client'},
            delegation_scope_digest='sha256:'+'d'*64)
        card = world.client.adopt(world.client.submit_request(request))
    # The host receives the original effective card via the same registry link;
    # binding slot 1 does not manufacture a work.start or production task.
    if bind: world.provider.bind_contract(card)
    world.net.register(world.client.signer.id, RefundPayee(world.client))
    return card


def persisted_world(tmp_path):
    world = World()
    world.provider.store = Store(tmp_path/'provider.json')
    world.provider.payment = AP2Payment(world.net, psp=PAY, payer=world.provider_ap2, keys=world.keys,
        instrument={'id':'provider','type':'account'}, clock=world.clock, store=world.provider.store)
    old = world.psp
    world.psp = PaymentProvider(old.signer, keys=world.keys, ap2_key=old.ap2_key,
        payee_keys=old.payee_keys, clock=world.clock, store=Store(tmp_path/'psp.json'))
    world.net.register(PAY, world.psp)
    return world


@pytest.mark.parametrize('formation',['countersign','proposal'])
@pytest.mark.parametrize('mode',['positive','zero','negative'])
def test_actual_settlement_uses_correct_parties_and_no_fictitious_zero_voucher(formation, mode):
    world = World(policy=ProposalPolicy() if formation=='proposal' else None)
    card = formed(world, formation, deposit='0' if mode=='positive' else '0.1 * total')
    cd = card_digest(card); world.client.start(card)
    paid = card['derived']['deposit']
    fee = paid if mode=='zero' else 100
    world.provider.policy.termination_fee = lambda *args: fee
    before = len(world.psp.ledger)
    term = world.client.terminate(cd, quote=world.client.termination_quote(cd))
    if mode == 'negative':
        voucher = world.provider.refund_termination(cd)
        assert Decimal(str(voucher['amount'])) == Decimal(str(paid)) - Decimal(str(fee))
        assert world.psp.ledger[-1]['payer'] == PROVIDER
        assert world.psp.ledger[-1]['payee'] == card['client']['id']
        assert world.provider.refund_termination(cd) == voucher
        assert len(world.psp.ledger) == before+1
        calls = [op for to,op,body in world.net.log if to==card['client']['id']]
        assert calls == ['ap2.checkout']
    elif mode == 'positive':
        assert len(world.psp.ledger) == before+1
        assert (world.psp.ledger[-1]['payer'],world.psp.ledger[-1]['payee']) == (card['client']['id'],PROVIDER)
    else:
        assert len(world.psp.ledger) == before
        assert not world.provider._record(cd).get('refundVoucher')
        assert not world.client._record(cd).get('terminationVoucher')
    assert card_digest(world.client.card(cd)) == cd
    assert world.client.card(cd)['fulfillment']['termination']['termination'] == term
    assert set(world.client.card(cd)) == set(card)
    world.provider.close(cd)
    assert world.market.store.get('ledger',cd)['outcome'] == 'client-terminated'


@pytest.mark.parametrize('formation',['countersign','proposal'])
def test_before_start_declared_deposit_is_not_mistaken_for_a_paid_deposit(formation):
    world = World(policy=ProposalPolicy() if formation=='proposal' else None)
    card = formed(world,formation); cd = card_digest(card)
    world.provider.policy.termination_fee = lambda *args: 100
    world.client.terminate(cd,quote=world.client.termination_quote(cd))
    assert not any(entry['purpose']=='deposit' for entry in world.psp.ledger)
    assert world.psp.ledger[-1]['amount'] == 100
    assert world.psp.ledger[-1]['payer'] == card['client']['id']
    assert not any(op=='work.start' for _,op,_ in world.net.log)
    world.provider.close(cd)


def test_lost_termination_ack_reuses_original_signature_and_pays_only_once(monkeypatch):
    world = World(); card = formed(world,deposit='0'); cd = card_digest(card)
    world.provider.policy.termination_fee = lambda *args: 100
    quote = world.client.termination_quote(cd); original = world.net.send; lost = []
    def send(to,op,body,*args,**kwargs):
        reply = original(to,op,body,*args,**kwargs)
        if op=='termination.notify' and not lost:
            lost.append(copy.deepcopy(body['termination'])); raise OSError('response lost after original acceptance')
        return reply
    monkeypatch.setattr(world.net,'send',send)
    with pytest.raises(OSError): world.client.terminate(cd,quote=quote)
    assert world.client._record(cd)['state'] == 'terminated'
    world.clock.advance(minutes=10)
    assert world.client.terminate(cd,quote=quote) == lost[0]
    assert world.client.terminate(cd,quote=quote) == lost[0]
    assert len([entry for entry in world.psp.ledger if entry['purpose']=='termination']) == 1
    assert world.provider.card(cd)['fulfillment']['termination']['termination'] == lost[0]


def test_refund_recovers_exact_ap2_receipt_after_restart_and_authorization_expiry(tmp_path,monkeypatch):
    world = persisted_world(tmp_path); card = formed(world); cd = card_digest(card); world.client.start(card)
    world.provider.policy.termination_fee = lambda *args: 100
    world.client.terminate(cd,quote=world.client.termination_quote(cd))
    original = world.net.send; lost = []
    def send(to,op,body,*args,**kwargs):
        reply = original(to,op,body,*args,**kwargs)
        if to==PAY and op=='ap2.pay' and not lost:
            lost.append(copy.deepcopy(reply.body)); raise OSError('original refund committed; response lost')
        return reply
    monkeypatch.setattr(world.net,'send',send)
    with pytest.raises(OSError): world.provider.refund_termination(cd)
    assert len(world.psp.ledger) == 2
    world.clock.advance(minutes=16)
    old = world.psp
    world.psp = PaymentProvider(old.signer,keys=world.keys,ap2_key=old.ap2_key,
        payee_keys=old.payee_keys,clock=world.clock,store=Store(tmp_path/'psp.json'))
    world.net.register(PAY,world.psp)
    saved = Store(tmp_path/'provider.json')
    port = AP2Payment(world.net,psp=PAY,payer=world.provider_ap2,keys=world.keys,
        instrument={'id':'provider','type':'account'},clock=world.clock,store=saved)
    world.provider = Provider(world.provider_signer,market=MARKET,keys=world.keys,transport=world.net,
        fetch=world.fetch,clock=world.clock,store=saved,ap2_key=world.provider_ap2,payment=port)
    world.net.register(PROVIDER,world.provider)
    voucher = world.provider.refund_termination(cd)
    assert voucher == lost[0]['voucher'] and len(world.psp.ledger)==2
    response = next(row['response'] for row in saved.all('ap2_payments').values() if row.get('response'))
    assert response == lost[0]
    receipt = jwt_verify(response['paymentReceipt'],world.psp.ap2_key.jwk)
    assert receipt['payment_id'] == world.psp.ledger[-1]['paymentId']


def test_wrong_payer_and_wrong_refund_direction_cannot_move_any_additional_value():
    world = World(); card = formed(world); cd = card_digest(card); world.client.start(card)
    world.provider.policy.termination_fee = lambda *args: 100
    world.client.terminate(cd,quote=world.client.termination_quote(cd))
    world.provider.refund_termination(cd)
    original = next(row['body'] for row in world.provider.store.all('ap2_payments').values())
    _, mandate = jwt_decode(original['checkoutMandate'].rstrip('~'))
    checkout_jwt = mandate['checkout_jwt']; _,claims = jwt_decode(checkout_jwt)
    impostor = EcKey('not-the-original-provider'); now = int(world.clock().timestamp())
    wrong = copy.deepcopy(original)
    wrong.update(checkoutMandate=checkout_mandate(checkout_jwt,impostor,now),
        paymentMandate=payment_mandate(checkout_jwt,impostor,payee=claims['merchant'],
            amount=claims['a2c']['amount'],currency='CNY',instrument={'id':'attacker'},now=now))
    assert world.psp.handle('ap2.pay',wrong).body['error']['code']=='bad-signature'
    reversed_checkout = checkout({'id':PROVIDER,'name':'incorrect refund receiver'},world.provider_ap2,
        purpose='termination',digest_ref={'cardDigest':cd},period=None,amount=claims['a2c']['amount'],currency='CNY',now=now)
    wrong.update(checkoutMandate=checkout_mandate(reversed_checkout,world.client_ap2,now),
        paymentMandate=payment_mandate(reversed_checkout,world.client_ap2,payee={'id':PROVIDER,'name':'incorrect refund receiver'},
            amount=claims['a2c']['amount'],currency='CNY',instrument={'id':'client'},now=now))
    assert world.psp.handle('ap2.pay',wrong).body['error']['code']=='card-invalid'
    assert len(world.psp.ledger)==2


def test_unilateral_termination_cannot_be_used_to_collect_a_refund():
    world = World(); card = formed(world); cd = card_digest(card); world.client.start(card)
    world.client.terminate(cd)
    with pytest.raises(A2CError): world.provider.refund_termination(cd)
    with pytest.raises(A2CError): world.client.refund_checkout(cd)
    assert [entry['purpose'] for entry in world.psp.ledger]==['deposit']


def test_old_period_quote_is_rejected_before_the_provider_records_termination():
    world = World(); card = formed(world); cd = card_digest(card); world.client.start(card)
    quote = world.client.termination_quote(cd)
    world.deliver(cd,1); world.client.fetch_delivery(cd,1); world.client.accept(cd,1)
    term = objects.signed(objects.new('termination',cardDigest=cd,
        terminationQuoteDigest=objects.object_digest(quote)),world.client.signer,world.clock())
    result = world.provider.handle('termination.notify',{'termination':term})
    assert result.body['error']['code']=='card-invalid'
    assert world.provider._record(cd)['state']=='started'
    assert 'termination' not in world.provider.card(cd)['fulfillment']


def test_unilateral_notice_cannot_predate_existing_delivery_and_acceptance():
    world = World(); card = formed(world); cd = card_digest(card); world.client.start(card)
    term = objects.signed(objects.new('termination',cardDigest=cd),world.client.signer,world.clock())
    world.clock.advance(seconds=1)
    world.deliver(cd,1); world.client.fetch_delivery(cd,1); world.client.accept(cd,1)
    result = world.provider.handle('termination.notify',{'termination':term})
    assert result.body['error']['code']=='out-of-order'
    assert world.provider._record(cd)['state']=='started'
    assert 'termination' not in world.provider.card(cd)['fulfillment']


def test_another_signed_object_cannot_masquerade_as_a_termination_notice():
    world = World(); card = formed(world); cd = card_digest(card)
    wrong = objects.signed(objects.new('receipt',cardDigest=cd),world.client.signer,world.clock())
    result = world.provider.handle('termination.notify',{'termination':wrong})
    assert result.body['error']['code']=='card-invalid'
    altered = copy.deepcopy(card); altered['fulfillment']['termination'] = {'termination':wrong}
    result = world.market.handle('card.close',{'card':altered})
    assert result.body['error']['code']=='card-invalid'
    assert not world.market.store.get('ledger',cd)
    assert world.provider._record(cd)['state']=='formed'


def test_unknown_uncommitted_refund_preserves_original_mandates_when_they_expire(monkeypatch):
    world = World(); card = formed(world); cd = card_digest(card); world.client.start(card)
    world.provider.policy.termination_fee = lambda *args: 100
    world.client.terminate(cd,quote=world.client.termination_quote(cd))
    original = world.net.send; lost = []
    def send(to,op,body,*args,**kwargs):
        if to==PAY and op=='ap2.pay' and not lost:
            lost.append(copy.deepcopy(body)); raise OSError('no definitive commit response')
        return original(to,op,body,*args,**kwargs)
    monkeypatch.setattr(world.net,'send',send)
    with pytest.raises(OSError): world.provider.refund_termination(cd)
    world.clock.advance(minutes=16)
    with pytest.raises(A2CError) as failure: world.provider.refund_termination(cd)
    assert failure.value.code=='bad-signature'
    assert len(world.psp.ledger)==1 and not world.provider._record(cd).get('refundVoucher')
    prepared = next(iter(world.provider.store.all('ap2_payments').values()))
    assert prepared['body']==lost[0] and 'response' not in prepared
    assert len([op for to,op,_ in world.net.log if to==world.client.signer.id and op=='ap2.checkout'])==1


@pytest.mark.parametrize('formation',['countersign','proposal'])
def test_paid_deposit_before_undelivered_work_can_be_bound_and_refunded_without_starting(monkeypatch,formation):
    world = World(policy=ProposalPolicy() if formation=='proposal' else None)
    card = formed(world,formation);cd = card_digest(card);original = world.net.send;before = len(world.psp.ledger)
    def no_start(to,op,body,*args,**kwargs):
        if op=='work.start':raise OSError('paid, but work.start never reached the provider')
        return original(to,op,body,*args,**kwargs)
    monkeypatch.setattr(world.net,'send',no_start)
    with pytest.raises(OSError):world.client.start(card)
    voucher = world.client._record(cd)['depositVoucher']
    assert len(world.psp.ledger)==before+1 and not world.provider._record(cd).get('depositVoucher')
    assert len([entry for entry in world.psp.ledger if entry['purpose']=='deposit'])==1
    world.provider.bind_contract(card,deposit_voucher=voucher)
    world.provider.bind_contract(card,deposit_voucher=voucher)
    world.provider.policy.termination_fee = lambda *args: 100
    world.client.terminate(cd,quote=world.client.termination_quote(cd))
    refund = world.provider.refund_termination(cd)
    assert Decimal(str(refund['amount']))==Decimal(str(card['derived']['deposit']))-100
    assert (world.psp.ledger[-1]['payer'],world.psp.ledger[-1]['payee'])==(PROVIDER,card['client']['id'])
    assert not world.provider.card(cd)['fulfillment'].get('receipt')
    assert not any(op=='work.start' for _,op,_ in world.net.log)
    world.provider.close(cd)
    assert world.market.store.get('ledger',cd)['outcome']=='client-terminated'


@pytest.mark.parametrize('wrong',['amount','card','signer'])
def test_bad_payment_evidence_cannot_partially_bind_an_original_proposal(wrong):
    world = World(policy=ProposalPolicy());card = formed(world,'proposal',bind=False);cd = card_digest(card)
    voucher = world.client.payment.pay(purpose='deposit',template=world.client.template(card['templateDigest']),card=card)
    from a2c.jws import Signer
    fields = {'purpose':'deposit','period':None,'amount':card['derived']['deposit'],'currency':'CNY','cardDigest':cd}
    if wrong=='amount':fields['amount']+=1
    if wrong=='card':fields['cardDigest']='sha256:'+'0'*64
    signer = Signer.generate('https://untrusted.invalid/pay') if wrong=='signer' else world.psp.signer
    bad = objects.signed(objects.new('voucher',**fields),signer,world.clock())
    before = world.provider._record(cd)
    assert before['card']['signatures']['1'] is None
    with pytest.raises(A2CError):world.provider.bind_contract(card,deposit_voucher=bad)
    assert world.provider._record(cd)==before
    world.provider.bind_contract(card,deposit_voucher=voucher)
    assert world.provider.card(cd)['signatures']['1']==card['signatures']['1']
    assert world.provider._record(cd)['depositVoucher']==voucher
