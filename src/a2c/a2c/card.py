"""Request Card and Contract Card: building and the invariants (design: 设计/合同协议.md §2)."""
from __future__ import annotations

import copy
from typing import Any, Mapping

from . import shape
from datetime import datetime

from .codec import WIRE_VERSION, canonical, digest
from .errors import CARD_INVALID, DIGEST_MISMATCH, INVALID_OBJECT, fail
from .jws import Signer, sign, verify
from .pricing import derive
from .schema import validate_filled
from .template import check_acceptance, check_durations, deliverable_names, template_digest

CARD_EXCLUDE = ("fulfillment", "signatures")
CLIENT_TERMS = ("provider", "templateDigest", "client", "user", "signers", "delegationScopeDigest",
                "paymentProvider")
SIGNER_VALUES = ("user", "client")


def card_digest(card: Mapping[str, Any]) -> str:
    return digest(card, CARD_EXCLUDE)


def request_digest(request: Mapping[str, Any]) -> str:
    return digest(request, ("signature",))


def empty_signatures() -> dict:
    return {"1": None, "2": None, "3": None, "4": [], "5": [], "6": [], "7": []}


# ── shared checks ───────────────────────────────────────────────────────

def check_spec(template: Mapping[str, Any], spec: Any, where: str) -> None:
    names = deliverable_names(template)
    if not isinstance(spec, Mapping) or set(spec) != set(names):
        fail(CARD_INVALID, f"{where}: one object per deliverable {names}")
    for d in template["deliverables"]:
        validate_filled(d["specSchema"], spec[d["name"]], f"{where}.{d['name']}")


def check_inputs(template: Mapping[str, Any], inputs: Any, where: str) -> None:
    materials = {m["name"]: m for m in template.get("inputs", [])}
    if not isinstance(inputs, Mapping):
        fail(CARD_INVALID, f"{where}: must be an object")
    unknown = set(inputs) - set(materials)
    missing = {n for n, m in materials.items() if m["required"]} - set(inputs)
    if unknown or missing:
        fail(CARD_INVALID, f"{where}: unknown {sorted(unknown)}, missing required {sorted(missing)}")
    for n, decl in inputs.items():
        if not isinstance(decl, Mapping):
            fail(CARD_INVALID, f"{where}.{n}: must be an object")
        shape.digest_field(decl.get("digest"), f"{where}.{n}.digest", CARD_INVALID)
        validate_filled(materials[n]["specSchema"], {k: v for k, v in decl.items() if k != "digest"}, f"{where}.{n}")


def check_client_terms(obj: Mapping[str, Any], where: str) -> None:
    shape.party(obj["client"], f"{where}.client")
    signers = shape.members(obj["signers"], ("slot1", "slot7"), where=f"{where}.signers")
    if any(v not in SIGNER_VALUES for v in signers.values()):
        fail(INVALID_OBJECT, f"{where}.signers: values are user or client")
    needs_user = "user" in signers.values()
    if needs_user != ("user" in obj):
        fail(CARD_INVALID, f"{where}: user is present exactly when signers names the user")
    if needs_user:
        shape.party(obj["user"], f"{where}.user")
    shape.digest_field(obj["delegationScopeDigest"], f"{where}.delegationScopeDigest")
    pp = shape.members(obj["paymentProvider"], ("id", "jwksUri"), where=f"{where}.paymentProvider")
    shape.string(pp["id"], f"{where}.paymentProvider.id")
    shape.string(pp["jwksUri"], f"{where}.paymentProvider.jwksUri")
    if "extensions" in obj and not isinstance(obj["extensions"], Mapping):
        fail(INVALID_OBJECT, f"{where}.extensions must be an object")


def _check_against_template(template: Mapping[str, Any], obj: Mapping[str, Any], where: str) -> None:
    if obj["templateDigest"] != template_digest(template):
        fail(DIGEST_MISMATCH, f"{where}.templateDigest is not this template")
    if obj["provider"] != template["provider"]:
        fail(CARD_INVALID, f"{where}.provider is not the template's provider")
    if dict(obj["paymentProvider"]) not in [dict(pp) for pp in template["pricing"]["paymentProviders"]]:
        fail(CARD_INVALID, f"{where}.paymentProvider is not one the template accepts")


# ── Request Card ────────────────────────────────────────────────────────

def build_request(template: Mapping[str, Any], *, request_id: str, version: int, client: Mapping[str, Any],
                  signers: Mapping[str, str], spec: Mapping[str, Any], inputs: Mapping[str, Any],
                  delegation_scope_digest: str, payment_provider: Mapping[str, Any],
                  user: Mapping[str, Any] | None = None, extensions: Mapping[str, Any] | None = None) -> dict:
    request = {"a2cVersion": WIRE_VERSION, "kind": "request", "requestId": request_id, "version": version,
               "templateDigest": template_digest(template), "provider": template["provider"],
               "client": dict(client), "signers": dict(signers), "spec": copy.deepcopy(spec),
               "inputs": copy.deepcopy(inputs), "delegationScopeDigest": delegation_scope_digest,
               "paymentProvider": dict(payment_provider)}
    if user is not None:
        request["user"] = dict(user)
    if extensions:
        request["extensions"] = dict(extensions)
    return request


def sign_request(request: Mapping[str, Any], signer: Signer, ts: datetime) -> dict:
    """The Client's signature over the Request Card (``a2c-slot: request``)."""
    out = {k: v for k, v in request.items() if k != "signature"}
    out["signature"] = sign(request_digest(out), signer, "request", ts)
    return out


def verify_request(request: Mapping[str, Any]) -> dict:
    return verify(request["signature"], request_digest(request), request["client"]["jwk"], slot="request",
                  signer=request["client"]["id"])


def validate_request(template: Mapping[str, Any], request: Any) -> Mapping[str, Any]:
    w = "request"
    shape.header(request, "request", w)
    shape.members(request, ("a2cVersion", "kind", "requestId", "version", "templateDigest", "provider", "client",
                            "signers", "spec", "inputs", "delegationScopeDigest", "paymentProvider", "signature"),
                  ("user", "extensions"), where=w)
    if template["formation"] != "proposal":
        fail(CARD_INVALID, "a Request Card needs a proposal template")
    shape.string(request["requestId"], f"{w}.requestId")
    shape.positive_int(request["version"], f"{w}.version")
    check_client_terms(request, w)
    _check_against_template(template, request, w)
    check_spec(template, request["spec"], f"{w}.spec")
    check_inputs(template, request["inputs"], f"{w}.inputs")
    return request


# ── Contract Card ───────────────────────────────────────────────────────

def build_card(template: Mapping[str, Any], *, card_id: str, client: Mapping[str, Any], signers: Mapping[str, str],
               spec: Mapping[str, Any], inputs: Mapping[str, Any], delegation_scope_digest: str,
               payment_provider: Mapping[str, Any], user: Mapping[str, Any] | None = None,
               extensions: Mapping[str, Any] | None = None) -> dict:
    """A card drafted by the Client (preapproval and countersign templates)."""
    check_spec(template, spec, "card.spec")
    check_inputs(template, inputs, "card.inputs")
    card = {"a2cVersion": WIRE_VERSION, "kind": "card", "cardId": card_id,
            "templateDigest": template_digest(template), "provider": template["provider"], "client": dict(client),
            "signers": dict(signers), "spec": copy.deepcopy(spec), "inputs": copy.deepcopy(inputs),
            "derived": derive(template, spec, inputs), "delegationScopeDigest": delegation_scope_digest,
            "paymentProvider": dict(payment_provider), "fulfillment": {}, "signatures": empty_signatures()}
    if user is not None:
        card["user"] = dict(user)
    if extensions:
        card["extensions"] = dict(extensions)
    return card


def build_proposal_card(template: Mapping[str, Any], request: Mapping[str, Any], *, card_id: str,
                        acceptance: Mapping[str, Any], accept_within: Mapping[str, str],
                        line_items: Mapping[str, Any]) -> dict:
    """A card drafted by the Provider from a Request Card; the Client's values and terms are kept."""
    card = {"a2cVersion": WIRE_VERSION, "kind": "card", "cardId": card_id,
            "templateDigest": request["templateDigest"], "requestDigest": request_digest(request),
            "provider": request["provider"], "client": copy.deepcopy(request["client"]),
            "signers": dict(request["signers"]), "spec": copy.deepcopy(request["spec"]),
            "inputs": copy.deepcopy(request["inputs"]), "acceptance": copy.deepcopy(acceptance),
            "deadlines": {"acceptWithin": dict(accept_within)},
            "derived": derive(template, request["spec"], request["inputs"], line_items),
            "delegationScopeDigest": request["delegationScopeDigest"],
            "paymentProvider": copy.deepcopy(request["paymentProvider"]),
            "fulfillment": {}, "signatures": empty_signatures()}
    if "user" in request:
        card["user"] = copy.deepcopy(request["user"])
    if "extensions" in request:
        card["extensions"] = copy.deepcopy(request["extensions"])
    return card


def validate_card(template: Mapping[str, Any], card: Any, *, request: Mapping[str, Any] | None = None
                  ) -> Mapping[str, Any]:
    """Invariants 1, 2, 3 and (proposal) 6. Signatures are checked by :mod:`a2c.slots`."""
    w = "card"
    shape.header(card, "card", w)
    proposal = template["formation"] == "proposal"
    required = ["a2cVersion", "kind", "cardId", "templateDigest", "provider", "client", "signers", "spec", "inputs",
                "derived", "delegationScopeDigest", "paymentProvider", "fulfillment", "signatures"]
    if proposal:
        required += ["requestDigest", "acceptance", "deadlines"]
    shape.members(card, required, ("user", "extensions"), where=w)
    shape.string(card["cardId"], f"{w}.cardId")
    check_client_terms(card, w)
    _check_against_template(template, card, w)
    check_spec(template, card["spec"], f"{w}.spec")
    check_inputs(template, card["inputs"], f"{w}.inputs")
    names = deliverable_names(template)
    if proposal:
        check_acceptance(card["acceptance"], names, f"{w}.acceptance", CARD_INVALID)
        shape.members(card["deadlines"], ("acceptWithin",), where=f"{w}.deadlines", code=CARD_INVALID)
        check_durations(card["deadlines"]["acceptWithin"], names, f"{w}.deadlines.acceptWithin", CARD_INVALID)
    line_items = (card["derived"] or {}).get("lineItems") if proposal else None
    if canonical(card["derived"]) != canonical(derive(template, card["spec"], card["inputs"], line_items)):
        fail(CARD_INVALID, "derived does not match the template's formulas")
    if not isinstance(card["fulfillment"], Mapping):
        fail(INVALID_OBJECT, f"{w}.fulfillment must be an object")
    sigs = shape.members(card["signatures"], ("1", "2", "3", "4", "5", "6", "7"), where=f"{w}.signatures")
    for k in ("4", "5", "6", "7"):
        if not isinstance(sigs[k], list):
            fail(INVALID_OBJECT, f"{w}.signatures.{k} must be an array")
    if proposal and request is not None:
        if card["requestDigest"] != request_digest(request):
            fail(CARD_INVALID, "requestDigest is not the Request Card")
        for k in ("spec", "inputs", *CLIENT_TERMS, "extensions"):
            if canonical(card.get(k)) != canonical(request.get(k)):
                fail(CARD_INVALID, f"card changes the Client's {k}")
    return card


__all__ = ["CARD_EXCLUDE", "CLIENT_TERMS", "card_digest", "request_digest", "empty_signatures", "check_spec",
           "check_inputs", "check_client_terms", "build_request", "sign_request", "verify_request",
           "validate_request", "build_card",
           "build_proposal_card", "validate_card"]
