"""Verified card-derived payment amounts and parties; no money or mutable state."""
from __future__ import annotations

import copy
from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping

from .. import objects
from ..card import card_digest, request_digest, validate_card, validate_request
from ..errors import CARD_INVALID, OUT_OF_ORDER, fail
from ..jws import header_of
from ..slots import verify_card, verify_template
from ..template import validate


@dataclass(frozen=True)
class PaymentTerms:
    template: Mapping
    ref: str
    amount: int | float
    payer: str
    payee: str


def verify_quote(template: Mapping, card: Mapping, quote: Mapping, keys) -> dict:
    """Verify the quote's original party, card, current period and amount cap."""
    objects.check(quote)
    if quote['kind'] != 'termination-quote':
        fail(CARD_INVALID, 'expected a signed termination quote')
    validate_card(template, card)
    chain = verify_card(template, card, keys)
    if chain.effective is None:
        fail(OUT_OF_ORDER, "termination requires an effective card")
    header = objects.verify_signed(
        quote, keys.jwk(template["provider"], header_of(quote.get("signature", {})).get("kid")),
        signer=template["provider"], expect_kid=True)
    period = chain.accepted + 1
    if (quote["kind"] != "termination-quote" or quote["cardDigest"] != card_digest(card)
            or quote["period"] != period or period > len(template["deliverables"])):
        fail(CARD_INVALID, "the quote must name this card and its first unaccepted period")
    if not 0 <= Decimal(str(quote["fee"])) <= Decimal(str(card["derived"]["periods"][period - 1]["amount"])):
        fail(CARD_INVALID, "termination fee outside the current period's amount")
    return header


def termination_balance(template: Mapping, card: Mapping, termination: Mapping, keys, *, deposit_paid: bool) -> Decimal:
    """Return fee minus the *actual* paid deposit, after validating both signatures.

    ``deposit_paid`` comes from the caller's verified payment records, never from
    the card's declared deposit alone. A negative result pays the client; zero
    requires no transfer. Historical evidence is checked at its signed times.
    """
    if type(deposit_paid) is not bool:
        fail(CARD_INVALID, "use verified actual deposit payment state")
    verify_template(template, keys)
    if not isinstance(termination, Mapping) or set(termination) != {"termination", "quote"}:
        fail(CARD_INVALID, "only an agreed termination has a settlement balance")
    term, quote = termination["termination"], termination["quote"]
    if not isinstance(term, Mapping) or 'terminationQuoteDigest' not in term:
        fail(CARD_INVALID, 'only an agreed termination has a settlement balance')
    existing = card["fulfillment"].get("termination")
    if existing is not None and existing != termination:
        fail(CARD_INVALID, "settlement differs from the card's original termination")
    complete = copy.deepcopy(card)
    complete["fulfillment"]["termination"] = copy.deepcopy(termination)
    verify_card(template, complete, keys)  # Owns notice/quote signatures, period, cap and time rules.
    paid = Decimal(str(card["derived"]["deposit"])) if deposit_paid else Decimal(0)
    return Decimal(str(quote["fee"])) - paid


def payment_terms(purpose: str, body: Mapping, period: int | None, *, keys,
                  payment_provider: str, deposit_paid: bool = False) -> PaymentTerms:
    """Derive the actual payer/payee and positive transfer amount from signed facts.

    The payment implementation supplies its verified prior deposit state. Its
    transaction, idempotency and payer authentication remain its own concerns.
    """
    if purpose not in objects.VOUCHER_PURPOSES:
        fail(CARD_INVALID, 'unsupported payment purpose')
    if ((purpose == 'installment' and (type(period) is not int or period < 1))
            or purpose != 'installment' and period is not None):
        fail(CARD_INVALID, 'only installments have a positive period')
    template = validate(body['template'])
    verify_template(template, keys)
    if payment_provider not in [party['id'] for party in template['pricing']['paymentProviders']]:
        fail(CARD_INVALID, 'this payment provider is not listed by the template')
    if purpose == 'proposal-fee':
        request = body['request']
        validate_request(template, {**request, 'signature':request.get('signature', {})})
        return PaymentTerms(template, request_digest(request), template['pricing'].get('proposalFee', 0),
                            request['client']['id'], template['provider'])
    card = validate_card(template, body['card'])
    chain = verify_card(template, card, keys)
    if card['paymentProvider']['id'] != payment_provider:
        fail(CARD_INVALID, 'use the payment provider selected by the card')
    ref = card_digest(card)
    payer, payee = card['client']['id'], template['provider']
    if purpose == 'deposit':
        if chain.effective is None:
            fail(OUT_OF_ORDER, 'deposit before the card is effective')
        amount = card['derived']['deposit']
    elif purpose == 'installment':
        if period > len(template['deliverables']) or chain.period_ts(5, period) is None or chain.period_ts(7, period) is None:
            fail(OUT_OF_ORDER, 'installment requires its actual slot 5 and slot 7')
        amount = card['derived']['periods'][period - 1]['amount']
    else:
        balance = termination_balance(template, card, body.get('termination'), keys, deposit_paid=deposit_paid)
        if balance < 0: payer, payee = payee, payer
        amount = abs(balance)
    number = Decimal(str(amount))
    value = int(number) if number == number.to_integral_value() else float(number)
    return PaymentTerms(template, ref, value, payer, payee)
