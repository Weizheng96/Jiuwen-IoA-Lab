"""Signing keys and JWS signatures (design: 设计/线上编码.md §3).

Every A2C signature is a flattened JWS with a detached payload: the payload is the UTF-8
bytes of the digest string being signed, ``alg`` is ``Ed25519`` (RFC 9864) and the
``a2c-*`` header parameters say what is signed, by whom and when.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from .codec import b64url, b64url_decode, canonical, fmt_time, loads, parse_time
from .errors import BAD_SIGNATURE, fail

ALG = "Ed25519"
FRESHNESS = timedelta(minutes=5)
_A2C_PARAMS = ("a2c-slot", "a2c-signer", "a2c-ts", "a2c-period", "a2c-manifest", "a2c-report")


def public_jwk(key: Ed25519PrivateKey | Ed25519PublicKey, kid: str | None = None) -> dict:
    pub = key.public_key() if isinstance(key, Ed25519PrivateKey) else key
    raw = pub.public_bytes(Encoding.Raw, PublicFormat.Raw)
    jwk = {"kty": "OKP", "crv": "Ed25519", "x": b64url(raw)}
    if kid:
        jwk["kid"] = kid
    return jwk


def key_from_jwk(jwk: Mapping[str, Any]) -> Ed25519PublicKey:
    if not isinstance(jwk, Mapping) or jwk.get("kty") != "OKP" or jwk.get("crv") != "Ed25519":
        fail(BAD_SIGNATURE, "public key is not an OKP/Ed25519 JWK")
    try:
        raw = b64url_decode(jwk["x"])
        return Ed25519PublicKey.from_public_bytes(raw)
    except (KeyError, ValueError):
        fail(BAD_SIGNATURE, "malformed Ed25519 JWK")


@dataclass(frozen=True)
class Signer:
    """A party's signing identity. ``kid`` is set when its key is published in a JWKS."""

    id: str
    key: Ed25519PrivateKey
    kid: str | None = None

    @classmethod
    def generate(cls, id: str, kid: str | None = None) -> "Signer":
        return cls(id=id, key=Ed25519PrivateKey.generate(), kid=kid)

    @property
    def jwk(self) -> dict:
        """Public key; carries ``kid`` only for JWKS-published keys."""
        return public_jwk(self.key, self.kid)

    @property
    def inline_jwk(self) -> dict:
        """Public key as written inline on a card or object (never with ``kid``)."""
        return public_jwk(self.key)


def sign(payload: str, signer: Signer, slot: str, ts: datetime, *, period: int | None = None,
         manifest: str | None = None, report: str | None = None) -> dict:
    header: dict[str, Any] = {"alg": ALG, "a2c-slot": slot, "a2c-signer": signer.id, "a2c-ts": fmt_time(ts)}
    if signer.kid:
        header["kid"] = signer.kid
    if period is not None:
        header["a2c-period"] = period
    if manifest is not None:
        header["a2c-manifest"] = manifest
    if report is not None:
        header["a2c-report"] = report
    header["crit"] = sorted(k for k in header if k.startswith("a2c-"))
    protected = b64url(canonical(header))
    signature = signer.key.sign(f"{protected}.{b64url(payload.encode('utf-8'))}".encode("ascii"))
    return {"protected": protected, "signature": b64url(signature)}


def header_of(sig: Any) -> dict:
    """Decode and syntax-check a protected header (no signature check)."""
    if not isinstance(sig, Mapping) or set(sig) != {"protected", "signature"}:
        fail(BAD_SIGNATURE, "signature must be {protected, signature}")
    try:
        header = loads(b64url_decode(sig["protected"]))
    except (ValueError, TypeError):
        fail(BAD_SIGNATURE, "malformed protected header")
    if not isinstance(header, dict) or header.get("alg") != ALG:
        fail(BAD_SIGNATURE, f"alg must be {ALG}")
    present = sorted(k for k in header if k.startswith("a2c-"))
    if any(k not in _A2C_PARAMS for k in present):
        fail(BAD_SIGNATURE, "unknown a2c header parameter")
    crit = header.get("crit")
    if not isinstance(crit, list) or len(crit) != len(set(crit)) or set(crit) != set(present):
        fail(BAD_SIGNATURE, "crit must list exactly the a2c-* parameters present")
    for k in ("a2c-slot", "a2c-signer", "a2c-ts"):
        if not isinstance(header.get(k), str):
            fail(BAD_SIGNATURE, f"missing {k}")
    parse_time(header["a2c-ts"])
    if "a2c-period" in header and (type(header["a2c-period"]) is not int or header["a2c-period"] < 1):
        fail(BAD_SIGNATURE, "a2c-period must be a positive integer")
    return header


def ts_of(sig: Any) -> datetime:
    return parse_time(header_of(sig)["a2c-ts"])


def verify(sig: Any, payload: str, jwk: Mapping[str, Any], *, slot: str, signer: str | None = None,
           period: int | None = None, manifest: str | None = None, report: str | None = None,
           expect_kid: bool = False) -> dict:
    """Verify ``sig`` over ``payload`` and the expected header values; return the header."""
    header = header_of(sig)
    expected = {"a2c-slot": slot, "a2c-period": period, "a2c-manifest": manifest, "a2c-report": report}
    for k, v in expected.items():
        if header.get(k) != v:
            fail(BAD_SIGNATURE, f"{k} is {header.get(k)!r}, expected {v!r}")
    if signer is not None and header["a2c-signer"] != signer:
        fail(BAD_SIGNATURE, f"a2c-signer is {header['a2c-signer']!r}, expected {signer!r}")
    if expect_kid:
        if not header.get("kid") or header.get("kid") != jwk.get("kid"):
            fail(BAD_SIGNATURE, "kid missing or not the JWKS key")
    elif "kid" in header:
        fail(BAD_SIGNATURE, "an inline key signature must not carry kid")
    key = key_from_jwk(jwk)
    try:
        signing_input = f"{sig['protected']}.{b64url(payload.encode('utf-8'))}".encode("ascii")
        key.verify(b64url_decode(sig["signature"]), signing_input)
    except (InvalidSignature, ValueError):
        fail(BAD_SIGNATURE, f"signature for {slot} does not verify")
    return header


def check_fresh(header: Mapping[str, Any], now: datetime, tolerance: timedelta = FRESHNESS) -> None:
    """Signatures that are sent as soon as they are made must carry a current a2c-ts."""
    ts = parse_time(header["a2c-ts"])
    if abs(now - ts) > tolerance:
        fail(BAD_SIGNATURE, f"a2c-ts {header['a2c-ts']} is not within {tolerance} of now")


def check_window(header: Mapping[str, Any], now: datetime, *, not_before: datetime | None = None,
                 tolerance: timedelta = FRESHNESS) -> None:
    """A signature the Client retrieves later (a task result or status) is not checked for freshness: its
    a2c-ts may not lie in the future, nor before the last signed instant already on the card."""
    ts = parse_time(header["a2c-ts"])
    if ts > now + tolerance:
        fail(BAD_SIGNATURE, f"a2c-ts {header['a2c-ts']} is in the future")
    if not_before is not None and ts < not_before:
        fail(BAD_SIGNATURE, f"a2c-ts {header['a2c-ts']} precedes the previous signature")


def find_key(jwks: Mapping[str, Any], kid: str | None) -> dict:
    for k in (jwks or {}).get("keys", []):
        if k.get("kid") == kid:
            return dict(k)
    fail(BAD_SIGNATURE, f"no key {kid!r} in JWKS")


__all__ = ["ALG", "FRESHNESS", "Signer", "public_jwk", "key_from_jwk", "sign", "header_of", "ts_of", "check_window",
           "verify", "check_fresh", "find_key"]
