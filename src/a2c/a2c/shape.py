"""Small structural checks shared by the object modules."""
from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from .codec import WIRE_VERSION, is_digest, parse_duration, parse_time
from .errors import INVALID_OBJECT, fail

NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
VERIFIER_ID = re.compile(r"^[A-Za-z0-9_.-]+@[A-Za-z0-9_.+-]+$")


def members(obj: Any, required: Iterable[str], optional: Iterable[str] = (), *, where: str,
            code: str = INVALID_OBJECT) -> Mapping[str, Any]:
    """``obj`` is an object whose members are exactly ``required`` plus any of ``optional``."""
    if not isinstance(obj, Mapping):
        fail(code, f"{where}: must be an object")
    req, opt = set(required), set(optional)
    missing = req - set(obj)
    extra = set(obj) - req - opt
    if missing or extra:
        fail(code, f"{where}: missing {sorted(missing)}, unexpected {sorted(extra)}")
    return obj


def header(obj: Mapping[str, Any], kind: str, where: str) -> None:
    if obj.get("a2cVersion") != WIRE_VERSION:
        fail(INVALID_OBJECT, f"{where}: a2cVersion must be {WIRE_VERSION}")
    if obj.get("kind") != kind:
        fail(INVALID_OBJECT, f"{where}: kind must be {kind}")


def string(v: Any, where: str, code: str = INVALID_OBJECT) -> str:
    if not isinstance(v, str) or not v:
        fail(code, f"{where}: must be a non-empty string")
    return v


def name(v: Any, where: str, code: str = INVALID_OBJECT) -> str:
    if not isinstance(v, str) or not NAME.match(v):
        fail(code, f"{where}: must be an identifier")
    return v


def digest_field(v: Any, where: str, code: str = INVALID_OBJECT) -> str:
    if not is_digest(v):
        fail(code, f"{where}: must be sha256:<64 hex>")
    return v


def boolean(v: Any, where: str, code: str = INVALID_OBJECT) -> bool:
    if not isinstance(v, bool):
        fail(code, f"{where}: must be a boolean")
    return v


def number(v: Any, where: str, code: str = INVALID_OBJECT, *, minimum: float | None = None,
           exclusive: bool = False) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        fail(code, f"{where}: must be a number")
    if minimum is not None and (v <= minimum if exclusive else v < minimum):
        fail(code, f"{where}: must be {'>' if exclusive else '≥'} {minimum}")
    return v


def positive_int(v: Any, where: str, code: str = INVALID_OBJECT) -> int:
    if type(v) is not int or v < 1:
        fail(code, f"{where}: must be a positive integer")
    return v


MAX_DURATION_DAYS = 3660


def duration(v: Any, where: str, code: str = INVALID_OBJECT):
    try:
        d = parse_duration(v)
    except Exception:  # noqa: BLE001 - re-raised with the caller's code
        fail(code, f"{where}: not a duration PnDTnHnMnS")
    if d.total_seconds() <= 0 or d.days > MAX_DURATION_DAYS:
        fail(code, f"{where}: must be positive and at most {MAX_DURATION_DAYS} days")
    return d


def instant(v: Any, where: str, code: str = INVALID_OBJECT):
    try:
        return parse_time(v)
    except Exception:  # noqa: BLE001
        fail(code, f"{where}: not an RFC 3339 UTC instant")


def str_list(v: Any, where: str, code: str = INVALID_OBJECT, *, non_empty: bool = False) -> list[str]:
    if not isinstance(v, list) or not all(isinstance(x, str) and x for x in v) or (non_empty and not v):
        fail(code, f"{where}: must be a{' non-empty' if non_empty else ''} list of strings")
    return v


def party(v: Any, where: str) -> Mapping[str, Any]:
    members(v, ("id", "jwk"), where=where)
    string(v["id"], f"{where}.id")
    members(v["jwk"], ("kty", "crv", "x"), where=f"{where}.jwk")
    if v["jwk"]["kty"] != "OKP" or v["jwk"]["crv"] != "Ed25519":
        fail(INVALID_OBJECT, f"{where}.jwk: must be OKP/Ed25519")
    return v


__all__ = ["NAME", "VERIFIER_ID", "members", "header", "string", "name", "digest_field", "boolean", "number",
           "positive_int", "duration", "instant", "str_list", "party"]
