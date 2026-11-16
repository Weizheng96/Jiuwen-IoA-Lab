"""A2C error codes (design: 设计/线上编码.md §6)."""
from __future__ import annotations

from typing import NoReturn

INVALID_OBJECT = "invalid-object"
BAD_SIGNATURE = "bad-signature"
DIGEST_MISMATCH = "digest-mismatch"
TEMPLATE_INVALID = "template-invalid"
TEMPLATE_EXPIRED = "template-expired"
CARD_INVALID = "card-invalid"
NOT_ARBITRABLE = "not-arbitrable"
DEADLINE_PASSED = "deadline-passed"
OUT_OF_ORDER = "out-of-order"
UNKNOWN_CARD = "unknown-card"

CODES = frozenset({
    INVALID_OBJECT, BAD_SIGNATURE, DIGEST_MISMATCH, TEMPLATE_INVALID, TEMPLATE_EXPIRED,
    CARD_INVALID, NOT_ARBITRABLE, DEADLINE_PASSED, OUT_OF_ORDER, UNKNOWN_CARD,
})


class A2CError(ValueError):
    """A protocol error. ``code`` is one of :data:`CODES`."""

    def __init__(self, code: str, message: str = "") -> None:
        if code not in CODES:
            raise ValueError(f"unknown A2C error code {code!r}")
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code
        self.message = message

    def to_payload(self, op: str) -> dict:
        return {"op": op, "error": {"code": self.code, "message": self.message}}


def fail(code: str, message: str) -> NoReturn:
    raise A2CError(code, message)


__all__ = [
    "A2CError", "CODES", "fail", "INVALID_OBJECT", "BAD_SIGNATURE", "DIGEST_MISMATCH", "TEMPLATE_INVALID",
    "TEMPLATE_EXPIRED", "CARD_INVALID", "NOT_ARBITRABLE", "DEADLINE_PASSED", "OUT_OF_ORDER", "UNKNOWN_CARD",
]
