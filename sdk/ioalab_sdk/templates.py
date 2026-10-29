"""Provider template construction; all validation remains in A2C."""
from __future__ import annotations
from typing import Mapping, Any
from a2c.codec import WIRE_VERSION
from datetime import timedelta
from a2c.codec import utcnow, fmt_time
TEMPLATE_LIFETIME = timedelta(days=30)
TEMPLATE_RENEW_BEFORE = timedelta(days=7)
DEFAULT_DEADLINES = {"respondWithin":"PT10M","startWithin":"PT30M","deliverWithin":"PT30M","acceptWithin":"PT15M","authorizeWithin":"PT15M"}

def template_from_definition(d: Mapping[str, Any], *, packages, payment_provider: Mapping[str, Any], now=None) -> dict:
    """服务定义 → 未签名的合同模板草稿（Provider.publish 会补 provider/摘要/签名）。"""
    dl = d["deliverable"]
    name = dl["name"]
    formation = d.get("formation", "preapproval")
    deadlines = {**DEFAULT_DEADLINES, **(d.get("deadlines") or {})}
    t: dict[str, Any] = {
        "a2cVersion": WIRE_VERSION, "kind": "template", "formation": formation, "validUntil": fmt_time((now or utcnow()) + TEMPLATE_LIFETIME),
        "deliverables": [{"name": name, "description": dl.get("description", dl.get("title", name)),
                          "specSchema": dl.get("specSchema") or {}}],
        "inputs": [{k: v for k, v in {"name": i["name"], "description": i.get("description", i.get("title", i["name"])),
                                      "required": bool(i.get("required", True)), "specSchema": i.get("specSchema") or {},
                                      "reviewBeforeSigning": i.get("reviewBeforeSigning")}.items() if v is not None}
                   for i in d.get("inputs", [])],
        "deadlines": {"respondWithin": deadlines["respondWithin"], "startWithin": deadlines["startWithin"],
                      "deliverWithin": {name: deadlines["deliverWithin"]}},
        "pricing": {"currency": "CNY", "depositFormula": str(d.get("pricing", {}).get("deposit", "0")),
                    "paymentWeights": {name: 1}, "paymentProviders": [dict(payment_provider)]},
        "samples": [],
    }
    if d.get("steps"):
        t["authorizableSteps"] = [{"name": s["name"], "description": s.get("description", s["name"])} for s in d["steps"]]
        t["deadlines"]["authorizeWithin"] = deadlines["authorizeWithin"]
    if formation == "proposal":
        t["pricing"]["proposalFee"] = d.get("pricing", {}).get("proposalFee", 1)
    else:
        t["deadlines"]["acceptWithin"] = {name: deadlines["acceptWithin"]}
        t["pricing"]["priceFactors"] = {name: {}}
        t["pricing"]["priceFormula"] = {name: str(d["pricing"]["price"])}
        ref = packages.put(verifier_id=d["verifier"]["id"], source=d["verifier"]["source"], reads=d["verifier"].get("reads", []))
        t["acceptance"] = {name: {"verifiers": {"script": ref}, "notCovered": list(d.get("notCovered", []))}}
    return t
