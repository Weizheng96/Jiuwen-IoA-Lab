"""Payment: the voucher check every party needs, and the port the Client SDK pays through.

A2C never moves money. A licensed Payment Provider reads the card, transfers and signs a
voucher (design: 设计/合同协议.md §7). :mod:`a2c.payment.ap2` binds the port to AP2.
"""
from __future__ import annotations

from decimal import Decimal
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from ..errors import BAD_SIGNATURE, CARD_INVALID, fail
from ..jws import header_of
from ..objects import check, verify_signed


class PaymentRejected(RuntimeError):
    """An authenticated adapter receipt proving this exact submission did not commit.

    Generic protocol/network exceptions do not provide this guarantee. The
    trusted adapter binds its original receipt to the PSP and exact body digest.
    This is a local SDK value, not an A2C error code or a model-supplied decision.
    """

    def __init__(self, *, payment_provider: str, body_digest: str, reference: str,
                 code: str, message: str):
        if not all(isinstance(v, str) and v for v in (payment_provider, body_digest, reference, code, message)):
            raise ValueError('A complete authenticated payment rejection is required')
        super().__init__(message)
        self.payment_provider, self.body_digest, self.reference = payment_provider, body_digest, reference
        self.code, self.message = code, message


@dataclass(frozen=True)
class PaymentReconciliation:
    """A trusted adapter's current result for one payment key, never model input.

    ``no_pending`` requires authoritative attempt/outbox reconciliation as well
    as the actual payment lookup; a missing voucher/HTTP 404 alone is not proof.
    """

    key: str
    response: Mapping[str, Any] | None = None
    no_pending: bool = False


class PaymentPort(Protocol):
    """Pays one A2C amount and returns the Payment Provider's voucher."""

    def pay(self, *, purpose: str, template: Mapping[str, Any], card: Mapping[str, Any] | None = None,
            request: Mapping[str, Any] | None = None, period: int | None = None,
            termination: Mapping[str, Any] | None = None, deposit_paid: bool = False) -> dict: ...


def verify_voucher(voucher: Mapping[str, Any], keys, *, payment_provider: Mapping[str, Any], purpose: str,
                   amount: Any, currency: str, card_digest: str | None = None, request_digest: str | None = None,
                   period: int | None = None) -> dict:
    """Check a voucher: issuer, signature and that it pays exactly this amount for this card."""
    check(voucher)
    if voucher.get("kind") != "voucher":
        fail(CARD_INVALID, "not a voucher")
    h = header_of(voucher.get("signature"))
    if h["a2c-signer"] != payment_provider["id"]:
        fail(BAD_SIGNATURE, "voucher is not from the card's payment provider")
    verify_signed(voucher, keys.jwk(payment_provider["id"], h.get("kid"), payment_provider["jwksUri"]),
                  signer=payment_provider["id"], expect_kid=True)
    expected = {"purpose": purpose, "cardDigest": card_digest, "requestDigest": request_digest, "period": period,
                "currency": currency}
    for k, v in expected.items():
        if voucher.get(k) != v:
            fail(CARD_INVALID, f"voucher {k} is {voucher.get(k)!r}, expected {v!r}")
    if Decimal(str(voucher["amount"])) != Decimal(str(amount)):
        fail(CARD_INVALID, f"voucher pays {voucher['amount']}, the card says {amount}")
    return h


__all__ = ["PaymentPort", "PaymentRejected", "PaymentReconciliation", "verify_voucher"]
