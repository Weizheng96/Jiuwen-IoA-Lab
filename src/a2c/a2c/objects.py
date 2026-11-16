"""Independent signed objects (design: 设计/线上编码.md §4).

Each object carries ``a2cVersion`` and ``kind``; a single-signature object is signed over its
digest without ``signature``, with ``a2c-slot`` equal to its ``kind``. The signing time is the
signature's ``a2c-ts``; objects have no separate time member.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from . import shape
from .codec import WIRE_VERSION, digest
from .errors import BAD_SIGNATURE, INVALID_OBJECT, fail
from .jws import Signer, sign, verify

VOUCHER_PURPOSES = ("proposal-fee", "deposit", "installment", "termination")
REJECTION_REASONS = ("digest-mismatch", "declaration-mismatch")
RESULT_STATUSES = ("pass", "fail")

# kind -> (required members, optional members) besides a2cVersion, kind and the signature
MEMBERS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "receipt": (("cardDigest",), ()),
    "rejection": (("cardDigest", "reason", "inputs"), ("report",)),
    "voucher": (("purpose", "amount", "currency"), ("cardDigest", "requestDigest", "period")),
    "information-authorization": (("inputs", "jwk"), ("cardDigest", "requestDigest")),
    "step-request": (("cardDigest", "period", "step"), ()),
    "step-authorization": (("stepRequestDigest", "granted", "jwk"), ()),
    "termination-quote": (("cardDigest", "period", "fee"), ()),
    "termination": (("cardDigest",), ("terminationQuoteDigest",)),
    "attestation": (("verifier", "cardDigest", "period", "manifestDigest", "statement"), ()),
    "manifest": (("cardDigest", "period", "files"), ()),
    "report": (("cardDigest", "results"), ("period", "input")),
    "provenance": (("cardDigest", "sampleDigest"), ()),
}
UNSIGNED = ("manifest",)


def new(kind: str, **body: Any) -> dict:
    obj = {"a2cVersion": WIRE_VERSION, "kind": kind, **{k: v for k, v in body.items() if v is not None}}
    check(obj)
    return obj


def object_digest(obj: Mapping[str, Any]) -> str:
    return digest(obj, ("signature", "signatures"))


def check(obj: Any) -> Mapping[str, Any]:
    """Structural check of an independent object (signature not verified)."""
    kind = obj.get("kind") if isinstance(obj, Mapping) else None
    if kind not in MEMBERS:
        fail(INVALID_OBJECT, f"unknown object kind {kind!r}")
    shape.header(obj, kind, kind)
    req, opt = MEMBERS[kind]
    sig = () if kind in UNSIGNED else ("signatures",) if kind == "provenance" else ("signature",)
    shape.members(obj, ("a2cVersion", "kind", *req), (*opt, *sig), where=kind)
    for k in ("cardDigest", "requestDigest", "stepRequestDigest", "terminationQuoteDigest", "manifestDigest",
              "sampleDigest"):
        if k in obj:
            shape.digest_field(obj[k], f"{kind}.{k}")
    if "period" in obj:
        shape.positive_int(obj["period"], f"{kind}.period")
    if kind == "voucher":
        if obj["purpose"] not in VOUCHER_PURPOSES:
            fail(INVALID_OBJECT, f"voucher.purpose must be one of {VOUCHER_PURPOSES}")
        ref = "requestDigest" if obj["purpose"] == "proposal-fee" else "cardDigest"
        if ref not in obj or ("cardDigest" in obj and "requestDigest" in obj):
            fail(INVALID_OBJECT, f"a {obj['purpose']} voucher refers to {ref}")
        if (obj["purpose"] == "installment") != ("period" in obj):
            fail(INVALID_OBJECT, "voucher.period is present for installments only")
        shape.number(obj["amount"], "voucher.amount", minimum=0)
    if kind == "information-authorization" and ("cardDigest" in obj) == ("requestDigest" in obj):
        fail(INVALID_OBJECT, "an information authorization binds either cardDigest or requestDigest")
    if kind == "rejection":
        if obj["reason"] not in REJECTION_REASONS:
            fail(INVALID_OBJECT, f"rejection.reason must be one of {REJECTION_REASONS}")
        shape.str_list(obj["inputs"], "rejection.inputs", non_empty=True)
        if (obj["reason"] == "declaration-mismatch") != ("report" in obj):
            fail(INVALID_OBJECT, "only a declaration-mismatch rejection carries the input report")
        if "report" in obj:
            check(obj["report"])
    if kind == "report":
        if ("period" in obj) == ("input" in obj):
            fail(INVALID_OBJECT, "a report is for a period or for an input")
        for i, r in enumerate(obj["results"]):
            shape.members(r, ("verifier", "status"), ("observationsDigest", "attestationDigests"),
                          where=f"report.results[{i}]")
            if r["status"] not in RESULT_STATUSES:
                fail(INVALID_OBJECT, "report status is pass or fail")
    if kind == "manifest":
        if not isinstance(obj["files"], list) or not obj["files"]:
            fail(INVALID_OBJECT, "manifest.files: at least one")
        for i, f in enumerate(obj["files"]):
            shape.members(f, ("name", "digest", "mediaType"), where=f"manifest.files[{i}]")
            shape.digest_field(f["digest"], f"manifest.files[{i}].digest")
        if len({f["name"] for f in obj["files"]}) != len(obj["files"]):
            fail(INVALID_OBJECT, "manifest.files: duplicate names")
    if kind == "step-authorization":
        shape.boolean(obj["granted"], f"{kind}.granted")
    if kind == "information-authorization":
        shape.str_list(obj["inputs"], f"{kind}.inputs")
    if kind == "step-request":
        shape.name(obj["step"], f"{kind}.step")
    if kind == "termination-quote":
        shape.number(obj["fee"], f"{kind}.fee", minimum=0)
    if kind == "voucher":
        shape.string(obj["currency"], "voucher.currency")
    if kind == "report":
        for r in obj["results"]:
            if "observationsDigest" in r:
                shape.digest_field(r["observationsDigest"], "report.observationsDigest")
            for dg in r.get("attestationDigests", []):
                shape.digest_field(dg, "report.attestationDigests")
    return obj


def signed(obj: Mapping[str, Any], signer: Signer, ts: datetime) -> dict:
    """Return ``obj`` with its single ``signature``."""
    out = {k: v for k, v in obj.items() if k != "signature"}
    check(out)
    if out["kind"] in UNSIGNED or out["kind"] == "provenance":
        fail(INVALID_OBJECT, f"{out['kind']} is not a single-signature object")
    out["signature"] = sign(object_digest(out), signer, out["kind"], ts)
    return out


def verify_signed(obj: Mapping[str, Any], jwk: Mapping[str, Any], *, signer: str | None = None,
                  expect_kid: bool = False) -> dict:
    """Verify a single-signature object; returns the protected header."""
    check(obj)
    if "signature" not in obj:
        fail(BAD_SIGNATURE, f"{obj['kind']} is not signed")
    return verify(obj["signature"], object_digest(obj), jwk, slot=obj["kind"], signer=signer, expect_kid=expect_kid)


def sign_role(obj: Mapping[str, Any], role: str, signer: Signer, ts: datetime) -> dict:
    """Add ``signatures.<role>`` to a multi-signature object (provenance)."""
    out = dict(obj)
    sigs = dict(out.get("signatures", {}))
    sigs[role] = sign(object_digest(out), signer, role, ts)
    out["signatures"] = sigs
    return out


def verify_role(obj: Mapping[str, Any], role: str, jwk: Mapping[str, Any], *, signer: str | None = None,
                expect_kid: bool = False) -> dict:
    sig = (obj.get("signatures") or {}).get(role)
    if sig is None:
        fail(INVALID_OBJECT, f"{obj.get('kind')}: no {role} signature")
    return verify(sig, object_digest(obj), jwk, slot=role, signer=signer, expect_kid=expect_kid)


__all__ = ["VOUCHER_PURPOSES", "REJECTION_REASONS", "RESULT_STATUSES", "MEMBERS", "new", "object_digest", "check",
           "signed", "verify_signed", "sign_role", "verify_role"]
