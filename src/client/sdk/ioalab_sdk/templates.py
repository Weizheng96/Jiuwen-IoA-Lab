"""Unsigned template construction; business clauses and validation remain A2C."""
from __future__ import annotations
from copy import deepcopy
from datetime import timedelta
from typing import Mapping, Any
from a2c.codec import WIRE_VERSION, utcnow, fmt_time
from a2c.errors import INVALID_OBJECT, fail

TEMPLATE_LIFETIME = timedelta(days=30)
TEMPLATE_RENEW_BEFORE = timedelta(days=7)
DEFAULT_DEADLINES = {"respondWithin":"PT10M","startWithin":"PT30M","deliverWithin":"PT30M","acceptWithin":"PT15M","authorizeWithin":"PT15M"}
TERMS = {'formation', 'deliverables', 'inputs', 'deadlines', 'pricing', 'acceptance', 'authorizableSteps', 'samples', 'extensions'}


def draft_from_terms(terms, *, payment_provider, now=None):
    """Add only runtime-owned metadata to original A2C business fields.

    The provider role validates against its actual identity before returning the
    draft. This pure function neither signs nor performs network/file effects.
    """
    if not isinstance(terms, dict) or set(terms) - TERMS:
        fail(INVALID_OBJECT, 'Use original A2C business fields; SDK supplies version, kind, validity, identity and signatures')
    if not {'formation', 'deliverables', 'deadlines', 'pricing'} <= set(terms):
        fail(INVALID_OBJECT, 'Template terms require formation, deliverables, deadlines and pricing')
    if not isinstance(terms['pricing'], dict) or 'paymentProviders' in terms['pricing']:
        fail(INVALID_OBJECT, 'Use A2C pricing business fields; SDK supplies the platform paymentProviders')
    value = deepcopy(terms)
    value['pricing']['paymentProviders'] = [dict(payment_provider)]
    return {'a2cVersion': WIRE_VERSION, 'kind': 'template',
            'validUntil': fmt_time((now or utcnow()) + TEMPLATE_LIFETIME), **value}


def template_from_definition(d: Mapping[str, Any], *, packages, payment_provider: Mapping[str, Any], now=None) -> dict:
    """Owned definition → unsigned template; reuse the same protocol metadata path."""
    dl = d["deliverable"]
    name = dl["name"]
    formation = d.get("formation", "preapproval")
    deadlines = {**DEFAULT_DEADLINES, **(d.get("deadlines") or {})}
    terms = {
        "formation": formation,
        "deliverables": [{"name": name, "description": dl.get("description", dl.get("title", name)), "specSchema": dl.get("specSchema") or {}}],
        "inputs": [{k: v for k, v in {"name": i["name"], "description": i.get("description", i.get("title", i["name"])),
                    "required": bool(i.get("required", True)), "specSchema": i.get("specSchema") or {},
                    "reviewBeforeSigning": i.get("reviewBeforeSigning")}.items() if v is not None} for i in d.get("inputs", [])],
        "deadlines": {"respondWithin": deadlines["respondWithin"], "startWithin": deadlines["startWithin"], "deliverWithin": {name: deadlines["deliverWithin"]}},
        "pricing": {"currency": "CNY", "depositFormula": str(d.get("pricing", {}).get("deposit", "0")), "paymentWeights": {name: 1}},
        "samples": [],
    }
    if d.get("steps"):
        terms["authorizableSteps"] = [{"name": s["name"], "description": s.get("description", s["name"])} for s in d["steps"]]
        terms["deadlines"]["authorizeWithin"] = deadlines["authorizeWithin"]
    if formation == "proposal":
        terms["pricing"]["proposalFee"] = d.get("pricing", {}).get("proposalFee", 1)
    else:
        terms["deadlines"]["acceptWithin"] = {name: deadlines["acceptWithin"]}
        terms["pricing"]["priceFactors"] = {name: {}}
        terms["pricing"]["priceFormula"] = {name: str(d["pricing"]["price"])}
        ref = packages.put(verifier_id=d["verifier"]["id"], source=d["verifier"]["source"], reads=d["verifier"].get("reads", []))
        terms["acceptance"] = {name: {"verifiers": {"script": ref}, "notCovered": list(d.get("notCovered", []))}}
    return draft_from_terms(terms, payment_provider=payment_provider, now=now)
