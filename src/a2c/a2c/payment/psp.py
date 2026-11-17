"""A reference Payment Provider that reads the card (for tests and demos; a real one is a licensed institution).

It verifies the AP2 mandates and the payee's Checkout JWT, then reads the card exactly as
设计/合同协议.md §7 says — template signatures, formation slots, slot 5(i) and 7(i), or the
termination record — before it "transfers" and signs the A2C voucher. It never holds funds
and has no operation that moves money on the basis of a ruling.
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any, Callable, Mapping

from .. import objects
from ..card import card_digest
from ..codec import digest
from ..common import Clock, Directory, Endpoint, Reply, Store, now_or
from ..errors import BAD_SIGNATURE, CARD_INVALID, OUT_OF_ORDER, fail
from ..jws import Signer
from .ap2 import EcKey, jwt_decode, jwt_verify, receipt, sha256_b64url, to_minor
from .terms import payment_terms


class PaymentProvider(Endpoint):
    OPS = ("ap2.pay",)
    _ids = iter(lambda: uuid.uuid4().hex, None)

    def __init__(self, signer: Signer, *, keys: Directory, ap2_key: EcKey, payee_keys: Callable[[str], Mapping],
                 clock: Clock | None = None, store: Store | None = None) -> None:
        super().__init__()
        self.signer = signer
        self.keys = keys
        self.ap2_key = ap2_key
        self.payee_keys = payee_keys          # either original party id → its registered AP2 JWK
        self.now = now_or(clock)
        self.store = store or Store()
        self.paid: set[tuple] = set()
        self.ledger: list[dict] = []          # transfers made (amount, payer, payee) — the double's "bank"

        for record in self.store.all('payments').values():
            self._project(record)

    def _project(self, record):
        key = tuple(record['key'])
        if key not in self.paid:
            self.paid.add(key)
            self.ledger.append(dict(record['entry']))

    def _key(self, party):
        try: return self.payee_keys(party)
        except (KeyError, TypeError): fail(BAD_SIGNATURE, 'the original payment party has no registered AP2 key')

    def _time(self, claims):
        issued, expires, now = claims.get('iat'), claims.get('exp'), self.now().timestamp()
        if (type(issued) is not int or type(expires) is not int or not issued < expires <= issued + 900
                or issued > now + 60 or expires <= now):
            fail(BAD_SIGNATURE, 'AP2 authorization is expired or has invalid times')

    def op_ap2_pay(self, body, files, task_id) -> Reply:
        with self._lock:
            return self._pay(body, files)

    def _pay(self, body, files):
        if files: fail(CARD_INVALID, 'payment requests contain no files')
        fingerprint = digest(body)
        previous = self.store.get('payments', fingerprint)
        if previous:
            self._project(previous)
            return Reply(previous['reply'])
        pm_jwt = body["paymentMandate"].rstrip("~")
        cm_jwt = body["checkoutMandate"].rstrip("~")
        pm_head, _ = jwt_decode(pm_jwt)
        payer_jwk = pm_head.get("jwk") or fail(BAD_SIGNATURE, "payment mandate carries no payer key")
        pm = jwt_verify(pm_jwt, payer_jwk)
        cm = jwt_verify(cm_jwt, payer_jwk)
        checkout_jwt = cm["checkout_jwt"]
        if cm["checkout_hash"] != sha256_b64url(checkout_jwt) or pm["transaction_id"] != sha256_b64url(checkout_jwt):
            fail(BAD_SIGNATURE, "mandates are not bound to the checkout")
        _h, claims = jwt_decode(checkout_jwt)
        payee = claims["merchant"]
        if pm["payee"] != payee:
            fail(CARD_INVALID, "payment mandate names another payee")
        a2c = claims["a2c"]
        purpose, period = a2c["purpose"], a2c.get("period")
        prior = isinstance(body.get('card'), Mapping) and (card_digest(body['card']), 'deposit', None) in self.paid
        terms = payment_terms(purpose, body, period, keys=self.keys,
                              payment_provider=self.signer.id, deposit_paid=bool(prior))
        template, ref, amount = terms.template, terms.ref, terms.amount
        if payee['id'] != terms.payee:
            fail(CARD_INVALID, 'the payee differs from the original payment direction')
        expected = self._key(terms.payer)
        if any(payer_jwk.get(name) != expected.get(name) for name in ('kty','crv','x','y')):
            fail(BAD_SIGNATURE, 'the mandate is not from the original payer')
        jwt_verify(checkout_jwt, self._key(terms.payee))
        if pm.get('vct') != 'mandate.payment.1' or cm.get('vct') != 'mandate.checkout.1':
            fail(BAD_SIGNATURE, 'use the original AP2 mandate types')
        for claims_to_check in (pm, cm, claims): self._time(claims_to_check)
        currency = template["pricing"]["currency"]
        reference = 'requestDigest' if purpose == 'proposal-fee' else 'cardDigest'
        if (a2c.get(reference) != ref or a2c.get('currency') != currency or claims.get('currency') != currency
                or Decimal(str(a2c["amount"])) != Decimal(str(amount)) or pm["payment_amount"] != {
                "amount": to_minor(amount, currency), "currency": currency}):
            fail(CARD_INVALID, "amount differs from the card")
        if Decimal(str(amount)) <= 0:
            fail(CARD_INVALID, 'zero balance requires no payment or voucher')
        key = (ref, purpose, period)
        if key in self.paid:
            fail(OUT_OF_ORDER, "already paid")
        payment_id = f"pay-{next(self._ids)}"
        ref_key = "requestDigest" if purpose == "proposal-fee" else "cardDigest"
        voucher = objects.signed(objects.new("voucher", purpose=purpose, period=period, amount=amount, currency=currency,
                                             **{ref_key: ref}), self.signer, self.now())
        rcpt = receipt(body["paymentMandate"], self.ap2_key, issuer=self.signer.id, payment_id=payment_id,
                       now=int(self.now().timestamp()))
        result = {"op": "ap2.pay", "voucher": voucher, "paymentReceipt": rcpt}
        record = {'key':list(key), 'reply':result,
                  'entry':{'payer':terms.payer, 'payee':terms.payee, 'amount':amount, 'currency':currency,
                           'purpose':purpose, 'ref':ref, 'paymentId':payment_id}}
        self.store.put('payments', fingerprint, record)
        self._project(record)
        return Reply(result)


__all__ = ["PaymentProvider"]
