"""AP2 v0.2 binding of the payment port (design: 设计/线上编码.md §9; informative, not part of A2C).

The payer's SDK takes a Checkout JWT from the payee (it carries an ``a2c`` member naming
purpose, digest, period and amount), signs a closed Checkout Mandate and Payment Mandate,
and hands them with the template and card to the Payment Provider, which reads the card,
transfers and returns an AP2 Payment Receipt plus the A2C voucher.

AP2 artefacts use ES256 compact JWS; mandates are SD-JWTs without disclosures (``<jwt>~``).
"""
from __future__ import annotations

import hashlib
import copy
import json
from decimal import Decimal
import threading
from typing import Any, Callable, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature, encode_dss_signature

from ..codec import b64url, b64url_decode, digest
from ..common import Directory, Store, Transport
from ..errors import BAD_SIGNATURE, CARD_INVALID, INVALID_OBJECT, OUT_OF_ORDER, fail
from ..pricing import minor_units

AP2_EXTENSION = "https://github.com/google-agentic-commerce/ap2/v1"


# ── ES256 JWT ───────────────────────────────────────────────────────────

class EcKey:
    """An ES256 key pair with a kid (AP2 artefacts are ES256)."""

    def __init__(self, kid: str, key: ec.EllipticCurvePrivateKey | None = None) -> None:
        self.kid = kid
        self.key = key or ec.generate_private_key(ec.SECP256R1())

    @property
    def jwk(self) -> dict:
        nums = self.key.public_key().public_numbers()
        return {"kty": "EC", "crv": "P-256", "kid": self.kid, "x": b64url(nums.x.to_bytes(32, "big")),
                "y": b64url(nums.y.to_bytes(32, "big"))}


def _public(jwk: Mapping[str, Any]) -> ec.EllipticCurvePublicKey:
    if jwk.get("kty") != "EC" or jwk.get("crv") != "P-256":
        fail(BAD_SIGNATURE, "not a P-256 JWK")
    x = int.from_bytes(b64url_decode(jwk["x"]), "big")
    y = int.from_bytes(b64url_decode(jwk["y"]), "big")
    return ec.EllipticCurvePublicNumbers(x, y, ec.SECP256R1()).public_key()


def jwt_sign(payload: Mapping[str, Any], key: EcKey, typ: str = "JWT", jwk_header: bool = False) -> str:
    header = {"alg": "ES256", "typ": typ, "kid": key.kid}
    if jwk_header:
        header["jwk"] = key.jwk
    h = b64url(json.dumps(header, separators=(",", ":")).encode())
    p = b64url(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode())
    der = key.key.sign(f"{h}.{p}".encode(), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    return f"{h}.{p}.{b64url(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


def jwt_decode(token: str) -> tuple[dict, dict]:
    try:
        h, p, _s = token.split(".")
        return json.loads(b64url_decode(h)), json.loads(b64url_decode(p))
    except (ValueError, AttributeError):
        fail(INVALID_OBJECT, "malformed JWT")


def jwt_verify(token: str, jwk: Mapping[str, Any]) -> dict:
    header, payload = jwt_decode(token)
    if header.get("alg") != "ES256":
        fail(BAD_SIGNATURE, "AP2 artefacts are ES256")
    h, p, s = token.split(".")
    raw = b64url_decode(s)
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    try:
        _public(jwk).verify(der, f"{h}.{p}".encode(), ec.ECDSA(hashes.SHA256()))
    except InvalidSignature:
        fail(BAD_SIGNATURE, "AP2 signature does not verify")
    return payload


def sha256_b64url(text: str) -> str:
    return b64url(hashlib.sha256(text.encode()).digest())


def to_minor(amount: Any, currency: str) -> int:
    return int(Decimal(str(amount)).scaleb(minor_units(currency)))


# ── artefacts ───────────────────────────────────────────────────────────

def checkout(payee: Mapping[str, str], key: EcKey, *, purpose: str, digest_ref: Mapping[str, str],
             period: int | None, amount: Any, currency: str, now: int) -> str:
    """The payee's Checkout JWT (UCP-shaped, with the A2C binding in ``a2c``)."""
    minor = to_minor(amount, currency)
    a2c = {"purpose": purpose, **digest_ref, "amount": amount, "currency": currency}
    if period is not None:
        a2c["period"] = period
    ref = next(iter(digest_ref.values()))
    return jwt_sign({
        "id": f"{ref}:{purpose}:{period or 0}", "merchant": dict(payee), "status": "ready_for_complete",
        "currency": currency, "links": [],
        "line_items": [{"id": "li_0", "item": {"id": f"a2c.{purpose}", "title": f"A2C {purpose}", "price": minor},
                        "quantity": 1, "totals": [{"type": "total", "amount": minor}]}],
        "totals": [{"type": "total", "amount": minor}], "a2c": a2c, "iat": now, "exp": now + 900}, key)


def checkout_mandate(checkout_jwt: str, payer: EcKey, now: int) -> str:
    return jwt_sign({"vct": "mandate.checkout.1", "checkout_jwt": checkout_jwt,
                     "checkout_hash": sha256_b64url(checkout_jwt), "iat": now, "exp": now + 900},
                    payer, typ="dc+sd-jwt", jwk_header=True) + "~"


def payment_mandate(checkout_jwt: str, payer: EcKey, *, payee: Mapping[str, str], amount: Any, currency: str,
                    instrument: Mapping[str, str], now: int) -> str:
    return jwt_sign({"vct": "mandate.payment.1", "transaction_id": sha256_b64url(checkout_jwt),
                     "payee": dict(payee), "payment_amount": {"amount": to_minor(amount, currency), "currency": currency},
                     "payment_instrument": dict(instrument), "iat": now, "exp": now + 900},
                    payer, typ="dc+sd-jwt", jwk_header=True) + "~"


def receipt(mandate: str, psp: EcKey, *, issuer: str, payment_id: str, now: int) -> str:
    return jwt_sign({"status": "Success", "iss": issuer, "iat": now, "reference": sha256_b64url(mandate.rstrip("~")),
                     "payment_id": payment_id, "psp_confirmation_id": payment_id, "network_confirmation_id": payment_id},
                    psp)


class AP2Payment:
    """The payer side of the payment port over AP2.

    Signed facts determine where to ask for the Checkout JWT (``ap2.checkout``),
    including a client receiving a termination refund. The caller supplies its
    existing Store to preserve the exact mandates and response across restarts.
    """

    def __init__(self, transport: Transport, *, psp: str, payer: EcKey, instrument: Mapping[str, str],
                 clock: Callable[[], Any], keys: Directory, store: Store | None = None,
                 reconcile: Callable[[dict], Any] | None = None) -> None:
        self.transport = transport
        self.psp = psp
        self.payer = payer
        self.instrument = dict(instrument)
        self.clock = clock
        self.keys = keys
        self.store = store or Store()
        self.lock = threading.RLock()
        self.reconcile = reconcile

    def pay(self, *, purpose, template, card=None, request=None, period=None, termination=None,
            deposit_paid=False) -> dict:
        with self.lock:
            return self._pay(purpose,template,card,request,period,termination,deposit_paid)

    def _pay(self, purpose, template, card, request, period, termination, deposit_paid):
        from . import PaymentRejected, PaymentReconciliation
        from ..card import card_digest, request_digest
        from .terms import payment_terms
        subject = card if card is not None else request
        ref = {"requestDigest": request_digest(request)} if purpose == "proposal-fee" else {"cardDigest": card_digest(card)}
        parameters = {'template':template, 'card':card, 'request':request, 'termination':termination,
                      'purpose':purpose, 'period':period, 'depositPaid':deposit_paid}
        fingerprint = digest(parameters)
        key = digest({'psp':self.psp,'payer':self.payer.jwk,'ref':ref,'purpose':purpose,'period':period})
        record = self.store.get('ap2_payments', key)
        terms = payment_terms(purpose, {'template':template, 'card':card, 'request':request,
                                        'termination':termination}, period, keys=self.keys,
                              payment_provider=self.psp, deposit_paid=deposit_paid)
        if record and record['fingerprint'] != fingerprint:
            # Later periods append signed facts to the same card. Recover this
            # period's frozen input/body; never replace an existing mandate with
            # the latest full card or accept changed original signatures.
            original = record.get('parameters')
            if original is None and record.get('body'):
                body = record['body']
                original = {'template': body['template'], 'card': body.get('card'), 'request': body.get('request'),
                            'termination': body.get('termination'), 'purpose': purpose, 'period': period,
                            'depositPaid': deposit_paid}
            if (purpose != 'installment' or original is None or digest(original) != record['fingerprint']
                    or original['template']['templateDigest'] != template['templateDigest']
                    or any(digest({'value': original[name]}) != digest({'value': parameters[name]})
                           for name in ('request', 'termination', 'purpose', 'period', 'depositPaid'))
                    or not self._extends(original['card'], card)):
                fail(OUT_OF_ORDER, 'recover the original payment objects without changing their signatures')
            parameters = original
            template, card, subject = original['template'], original['card'], original['card']
            fingerprint = record['fingerprint']
            original_terms = payment_terms(purpose, {'template':template, 'card':card, 'request':request,
                                                   'termination':termination}, period, keys=self.keys,
                                           payment_provider=self.psp, deposit_paid=deposit_paid)
            if (original_terms.amount, original_terms.payer, original_terms.payee) != (terms.amount, terms.payer, terms.payee):
                fail(OUT_OF_ORDER, 'the current card changed this original payment obligation')
            terms = original_terms
        if Decimal(str(terms.amount)) <= 0:
            fail(CARD_INVALID, 'zero balance requires no transfer')
        if record and record.get('response'):
            return self._voucher(record['response'],terms,purpose,ref,period)
        if record and record.get('rejection'):
            if self.reconcile is None:
                fail(OUT_OF_ORDER, 'reconcile the rejected payment and all original attempts before retrying')
            attempts = [*record.get('attempts', []), {'body': record['body'], 'rejection': record['rejection']}]
            state = self.reconcile({'key': key, 'paymentProvider': self.psp, 'ref': ref,
                                    'purpose': purpose, 'period': period, 'attempts': copy.deepcopy(attempts)})
            if not isinstance(state, PaymentReconciliation) or state.key != key:
                fail(OUT_OF_ORDER, 'payment reconciliation belongs to another original payment key')
            if state.response is not None:
                voucher = self._voucher(state.response, terms, purpose, ref, period)
                self.store.put('ap2_payments', key, {**record, 'response': dict(state.response)})
                return voucher
            if state.no_pending is not True:
                fail(OUT_OF_ORDER, 'an original payment attempt is unresolved; do not create another authorization')
            record = {'fingerprint': fingerprint, 'parameters': parameters, 'attempts': attempts}
            self.store.put('ap2_payments', key, record)
        ask: dict[str, Any] = {"purpose": purpose}
        if purpose == "proposal-fee":
            ask.update(templateDigest=request["templateDigest"], requestDigest=ref["requestDigest"])
        elif purpose == "termination":
            ask["cardDigest"] = ref["cardDigest"]
        else:
            ask["card"] = card
            if period:
                ask["period"] = period
        if record is None:
            record = {'fingerprint':fingerprint, 'parameters': parameters}
            self.store.put('ap2_payments', key, record)
        if 'body' not in record:
            reply = self.transport.send(terms.payee, 'ap2.checkout', ask)
            checkout_jwt = reply.body['checkoutJwt']
            header, claims = jwt_decode(checkout_jwt)
            jwt_verify(checkout_jwt, self.keys.jwk(terms.payee, header.get('kid')))
            payee = claims['merchant']; binding = claims.get('a2c', {})
            currency = template['pricing']['currency']; now = int(self.clock().timestamp())
            if (payee.get('id') != terms.payee or binding.get('purpose') != purpose
                    or binding.get('period') != period or any(binding.get(k) != v for k,v in ref.items())
                    or binding.get('currency') != currency or claims.get('currency') != currency
                    or Decimal(str(binding.get('amount'))) != Decimal(str(terms.amount))):
                fail(CARD_INVALID, 'checkout differs from the actual payment amount, direction or object')
            issued, expires = claims.get('iat'), claims.get('exp')
            if (type(issued) is not int or type(expires) is not int or not issued < expires <= issued + 900
                    or issued > now + 60 or expires <= now):
                fail(BAD_SIGNATURE, 'checkout is expired or has invalid times')
            body = {'checkoutMandate':checkout_mandate(checkout_jwt, self.payer, now),
                    'paymentMandate':payment_mandate(checkout_jwt, self.payer, payee=payee,
                        amount=terms.amount, currency=currency, instrument=self.instrument, now=now),
                    'template':template}
            body['card' if card is not None else 'request'] = subject
            if termination is not None: body['termination'] = termination
            record = {**record,'body':body}
            self.store.put('ap2_payments', key, record)
        try:
            out = self.transport.send(self.psp, 'ap2.pay', record['body']).body
        except PaymentRejected as rejected:
            if rejected.payment_provider != self.psp or rejected.body_digest != digest(record['body']):
                fail(BAD_SIGNATURE, 'the rejection does not bind this payment provider and exact original submission')
            self.store.put('ap2_payments', key, {**record, 'rejection': {
                'reference': rejected.reference, 'bodyDigest': rejected.body_digest,
                'code': rejected.code, 'message': rejected.message}})
            raise
        voucher = self._voucher(out,terms,purpose,ref,period)
        self.store.put('ap2_payments', key, {**record,'response':out})
        return voucher

    @staticmethod
    def _extends(original, current):
        """Current facts may append, but cannot change any frozen payment fact."""
        if isinstance(original, dict):
            return isinstance(current, dict) and all(key in current and AP2Payment._extends(value, current[key])
                                                     for key, value in original.items())
        if isinstance(original, list):
            return isinstance(current, list) and len(current) >= len(original) and all(
                AP2Payment._extends(value, current[index]) for index, value in enumerate(original))
        return digest({'value': original}) == digest({'value': current})

    def _voucher(self, response, terms, purpose, ref, period):
        from . import verify_voucher
        provider = next(p for p in terms.template['pricing']['paymentProviders'] if p['id']==self.psp)
        verify_voucher(response['voucher'],self.keys,payment_provider=provider,purpose=purpose,
            amount=terms.amount,currency=terms.template['pricing']['currency'],
            card_digest=ref.get('cardDigest'),request_digest=ref.get('requestDigest'),period=period)
        return response['voucher']


__all__ = ["AP2_EXTENSION", "EcKey", "jwt_sign", "jwt_decode", "jwt_verify", "sha256_b64url", "to_minor", "checkout",
           "checkout_mandate", "payment_mandate", "receipt", "AP2Payment"]
