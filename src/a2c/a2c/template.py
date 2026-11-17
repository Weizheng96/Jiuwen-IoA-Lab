"""Contract Template validation (design: 设计/合同协议.md §3–§4, 设计/线上编码.md §4)."""
from __future__ import annotations

from typing import Any, Iterator, Mapping

from . import shape
from .codec import digest
from .errors import DIGEST_MISMATCH, TEMPLATE_INVALID, fail
from .pricing import check_formulas, minor_units
from .schema import check_spec_schema

FORMATIONS = ("preapproval", "countersign", "proposal")
VERIFIER_KINDS = ("script", "skill", "service")
EXCLUDE = ("templateDigest", "signatures")


def template_digest(template: Mapping[str, Any]) -> str:
    return digest(template, EXCLUDE)


def deliverable_names(template: Mapping[str, Any]) -> list[str]:
    return [d["name"] for d in template["deliverables"]]


def deliverable_of(template: Mapping[str, Any], period: int) -> str:
    return template["deliverables"][period - 1]["name"]


def material(template: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    for m in template.get("inputs", []):
        if m["name"] == name:
            return m
    fail(TEMPLATE_INVALID, f"no material {name!r}")


def check_verifiers(block: Any, where: str, code: str = TEMPLATE_INVALID, kinds: tuple[str, ...] = VERIFIER_KINDS) -> None:
    shape.members(block, (), kinds, where=where, code=code)
    if not block:
        fail(code, f"{where}: at least one verifier")
    for kind, ref in block.items():
        w = f"{where}.{kind}"
        shape.members(ref, ("id", "uri", "digest"), ("disputeModels",) if kind == "skill" else (), where=w, code=code)
        if not isinstance(ref["id"], str) or not shape.VERIFIER_ID.match(ref["id"]):
            fail(code, f"{w}.id: must be name@version")
        shape.string(ref["uri"], f"{w}.uri", code)
        shape.digest_field(ref["digest"], f"{w}.digest", code)
        if kind == "skill":
            if "disputeModels" not in ref:
                fail(code, f"{w}: a skill needs disputeModels")
            models = shape.str_list(ref["disputeModels"], f"{w}.disputeModels", code, non_empty=True)
            if len(set(models)) != len(models):
                fail(code, f"{w}.disputeModels: duplicates")


def check_acceptance(acceptance: Any, names: list[str], where: str, code: str = TEMPLATE_INVALID) -> None:
    shape.members(acceptance, names, where=where, code=code)
    for d in names:
        entry = shape.members(acceptance[d], ("verifiers", "notCovered"), where=f"{where}.{d}", code=code)
        check_verifiers(entry["verifiers"], f"{where}.{d}.verifiers", code)
        shape.str_list(entry["notCovered"], f"{where}.{d}.notCovered", code)


def check_durations(block: Any, names: list[str], where: str, code: str = TEMPLATE_INVALID) -> None:
    shape.members(block, names, where=where, code=code)
    for d in names:
        shape.duration(block[d], f"{where}.{d}", code)


def acceptance_of(template: Mapping[str, Any], card: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
    """The applicable acceptance: the template's, or for proposal templates the card's."""
    if template["formation"] == "proposal":
        return (card or {}).get("acceptance") or {}
    return template["acceptance"]


def accept_within(template: Mapping[str, Any], card: Mapping[str, Any], deliverable: str) -> str:
    if template["formation"] == "proposal":
        return card["deadlines"]["acceptWithin"][deliverable]
    return template["deadlines"]["acceptWithin"][deliverable]


def verifier_refs(template: Mapping[str, Any], card: Mapping[str, Any] | None = None
                  ) -> Iterator[tuple[str, str, str, Mapping[str, Any]]]:
    """Every applicable verifier reference: (target kind, target name, verifier kind, ref)."""
    for d, entry in acceptance_of(template, card).items():
        for kind, ref in entry["verifiers"].items():
            yield "deliverable", d, kind, ref
    for m in template.get("inputs", []):
        for kind, ref in (m.get("verifiers") or {}).items():
            yield "input", m["name"], kind, ref


def validate(template: Any, *, endorsed: bool = True) -> Mapping[str, Any]:
    """Validate a template; ``endorsed`` requires the Market signature as well."""
    w = "template"
    shape.header(template, "template", w)
    formation = template.get("formation")
    if formation not in FORMATIONS:
        fail(TEMPLATE_INVALID, f"{w}.formation must be one of {FORMATIONS}")
    proposal = formation == "proposal"
    required = ["a2cVersion", "kind", "formation", "provider", "validUntil", "deliverables", "deadlines",
                "pricing", "templateDigest", "signatures"]
    if not proposal:
        required.append("acceptance")
    shape.members(template, required, ("inputs", "authorizableSteps", "samples", "extensions"), where=w)
    shape.string(template["provider"], f"{w}.provider")
    shape.instant(template["validUntil"], f"{w}.validUntil")

    deliverables = template["deliverables"]
    if not isinstance(deliverables, list) or not deliverables:
        fail(TEMPLATE_INVALID, f"{w}.deliverables: at least one")
    for i, d in enumerate(deliverables):
        shape.members(d, ("name", "description", "specSchema"), where=f"{w}.deliverables[{i}]")
        shape.name(d["name"], f"{w}.deliverables[{i}].name")
        shape.string(d["description"], f"{w}.deliverables[{i}].description")
        check_spec_schema(d["specSchema"], f"{w}.deliverables[{i}].specSchema")
    names = deliverable_names(template)
    if len(set(names)) != len(names):
        fail(TEMPLATE_INVALID, f"{w}.deliverables: duplicate names")

    seen: set[str] = set()
    for i, m in enumerate(template.get("inputs", [])):
        mw = f"{w}.inputs[{i}]"
        shape.members(m, ("name", "description", "required", "specSchema"), ("reviewBeforeSigning", "verifiers"), where=mw)
        shape.name(m["name"], f"{mw}.name")
        if m["name"] in seen:
            fail(TEMPLATE_INVALID, f"{mw}: duplicate material name")
        seen.add(m["name"])
        shape.string(m["description"], f"{mw}.description")
        shape.boolean(m["required"], f"{mw}.required")
        check_spec_schema(m["specSchema"], f"{mw}.specSchema")
        if "digest" in m["specSchema"]:
            fail(TEMPLATE_INVALID, f"{mw}.specSchema: a material may not declare a key named digest")
        if shape.boolean(m.get("reviewBeforeSigning", False), f"{mw}.reviewBeforeSigning") and formation == "preapproval":
            fail(TEMPLATE_INVALID, f"{mw}: a preapproval template cannot ask for review before signing")
        if "verifiers" in m:
            check_verifiers(m["verifiers"], f"{mw}.verifiers", kinds=("script", "skill"))

    steps = template.get("authorizableSteps", [])
    if not isinstance(steps, list):
        fail(TEMPLATE_INVALID, f"{w}.authorizableSteps must be a list")
    for i, s in enumerate(steps):
        shape.members(s, ("name", "description"), where=f"{w}.authorizableSteps[{i}]")
        shape.name(s["name"], f"{w}.authorizableSteps[{i}].name")
        shape.string(s["description"], f"{w}.authorizableSteps[{i}].description")
    if len({s["name"] for s in steps}) != len(steps):
        fail(TEMPLATE_INVALID, f"{w}.authorizableSteps: duplicate names")

    if not proposal:
        check_acceptance(template["acceptance"], names, f"{w}.acceptance")

    dl = template["deadlines"]
    keys = ["respondWithin", "startWithin", "deliverWithin"] + ([] if proposal else ["acceptWithin"])
    if steps:
        keys.append("authorizeWithin")
    shape.members(dl, keys, where=f"{w}.deadlines", code=TEMPLATE_INVALID)
    for k in ("respondWithin", "startWithin", "authorizeWithin"):
        if k in dl:
            shape.duration(dl[k], f"{w}.deadlines.{k}", TEMPLATE_INVALID)
    check_durations(dl["deliverWithin"], names, f"{w}.deadlines.deliverWithin")
    if not proposal:
        check_durations(dl["acceptWithin"], names, f"{w}.deadlines.acceptWithin")

    pricing = template["pricing"]
    if proposal:
        shape.members(pricing, ("currency", "depositFormula", "paymentWeights", "paymentProviders"), ("proposalFee",),
                      where=f"{w}.pricing", code=TEMPLATE_INVALID)
        if "proposalFee" in pricing:
            shape.number(pricing["proposalFee"], f"{w}.pricing.proposalFee", TEMPLATE_INVALID, minimum=0)
    else:
        shape.members(pricing, ("currency", "priceFactors", "priceFormula", "depositFormula", "paymentWeights",
                                "paymentProviders"), where=f"{w}.pricing", code=TEMPLATE_INVALID)
        shape.members(pricing["priceFormula"], names, where=f"{w}.pricing.priceFormula", code=TEMPLATE_INVALID)
        if not isinstance(pricing["priceFactors"], Mapping):
            fail(TEMPLATE_INVALID, f"{w}.pricing.priceFactors must be an object")
    minor_units(shape.string(pricing["currency"], f"{w}.pricing.currency"))
    shape.string(pricing["depositFormula"], f"{w}.pricing.depositFormula")
    shape.members(pricing["paymentWeights"], names, where=f"{w}.pricing.paymentWeights", code=TEMPLATE_INVALID)
    for d in names:
        shape.positive_int(pricing["paymentWeights"][d], f"{w}.pricing.paymentWeights.{d}", TEMPLATE_INVALID)
    pps = pricing["paymentProviders"]
    if not isinstance(pps, list) or not pps:
        fail(TEMPLATE_INVALID, f"{w}.pricing.paymentProviders: at least one")
    for i, pp in enumerate(pps):
        shape.members(pp, ("id", "jwksUri"), where=f"{w}.pricing.paymentProviders[{i}]", code=TEMPLATE_INVALID)
        shape.string(pp["id"], f"{w}.pricing.paymentProviders[{i}].id", TEMPLATE_INVALID)
        shape.string(pp["jwksUri"], f"{w}.pricing.paymentProviders[{i}].jwksUri", TEMPLATE_INVALID)
    if len({pp["id"] for pp in pps}) != len(pps):
        fail(TEMPLATE_INVALID, f"{w}.pricing.paymentProviders: duplicate ids")
    check_formulas(template)

    for i, s in enumerate(template.get("samples", [])):
        sw = f"{w}.samples[{i}]"
        shape.members(s, ("name", "deliverable", "uri", "digest", "spec"), ("provenance",), where=sw)
        shape.string(s["name"], f"{sw}.name")
        if s["deliverable"] not in names:
            fail(TEMPLATE_INVALID, f"{sw}.deliverable: unknown")
        schema = next(d["specSchema"] for d in deliverables if d["name"] == s["deliverable"])
        if not isinstance(s["spec"], Mapping) or not set(s["spec"]) <= set(schema):
            fail(TEMPLATE_INVALID, f"{sw}.spec: keys of the deliverable's specSchema only")
        shape.string(s["uri"], f"{sw}.uri")
        shape.digest_field(s["digest"], f"{sw}.digest")
        if "provenance" in s:
            shape.members(s["provenance"], ("a2cVersion", "kind", "cardDigest", "sampleDigest", "signatures"),
                          where=f"{sw}.provenance")
            if s["provenance"]["sampleDigest"] != s["digest"]:
                fail(TEMPLATE_INVALID, f"{sw}.provenance names another sample")
    if "extensions" in template and not isinstance(template["extensions"], Mapping):
        fail(TEMPLATE_INVALID, f"{w}.extensions must be an object")

    if template["templateDigest"] != template_digest(template):
        fail(DIGEST_MISMATCH, "templateDigest does not match the template")
    shape.members(template["signatures"], ("provider", "market") if endorsed else ("provider",), ("market",),
                  where=f"{w}.signatures")
    return template


__all__ = ["FORMATIONS", "VERIFIER_KINDS", "template_digest", "deliverable_names", "deliverable_of", "material",
           "check_verifiers", "check_acceptance", "acceptance_of", "accept_within", "verifier_refs", "validate"]
