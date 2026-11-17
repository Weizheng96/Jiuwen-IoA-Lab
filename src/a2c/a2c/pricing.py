"""Derived amounts: line items, total, deposit, periods (design: 设计/线上编码.md §8).

Formulas are evaluated in binary64; every amount is rounded half-even to the currency's
minor unit from the exact binary value; the last period takes the remainder so that
deposit + periods == total.
"""
from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal
from fractions import Fraction
from typing import Any, Mapping

import rfc8785

from . import expr as _expr
from .errors import CARD_INVALID, TEMPLATE_INVALID, fail
from .schema import is_const, is_numeric, value_of

# ISO 4217 minor units for the currencies the SDK accepts.
MINOR_UNITS = {"CNY": 2, "USD": 2, "EUR": 2, "GBP": 2, "HKD": 2, "SGD": 2, "AUD": 2, "CAD": 2, "CHF": 2,
               "JPY": 0, "KRW": 0}
DEPOSIT_NOTICE_RATIO = Decimal("0.2")


def minor_units(currency: str) -> int:
    if currency not in MINOR_UNITS:
        fail(TEMPLATE_INVALID, f"unsupported currency {currency!r}")
    return MINOR_UNITS[currency]


def quantize(x: float | Decimal, currency: str) -> Decimal:
    q = Decimal(1).scaleb(-minor_units(currency))
    return (x if isinstance(x, Decimal) else Decimal(x)).quantize(q, rounding=ROUND_HALF_EVEN)


def _round_fraction(x: Fraction, currency: str) -> Decimal:
    """Round an exact rational half to even at the minor unit."""
    scale = 10 ** minor_units(currency)
    scaled = x * scale
    floor = scaled.numerator // scaled.denominator
    rem = scaled - floor
    if rem > Fraction(1, 2) or (rem == Fraction(1, 2) and floor % 2 == 1):
        floor += 1
    return Decimal(floor).scaleb(-minor_units(currency))


def to_wire(d: Decimal) -> int | float:
    return int(d) if d == d.to_integral_value() else float(d)


def factor_key(value: Any) -> str:
    """Key of ``value`` in a priceFactors table: strings as is, numbers in JCS form."""
    return value if isinstance(value, str) else rfc8785.dumps(value).decode()


def _owner(template: Mapping[str, Any], d: str, key: str) -> str:
    """The deliverable whose spec defines ``key``: ``d`` itself, otherwise the unique other one."""
    schemas = {x["name"]: x["specSchema"] for x in template["deliverables"]}
    if key in schemas[d]:
        return d
    owners = [n for n, s in schemas.items() if key in s]
    if len(owners) != 1:
        fail(TEMPLATE_INVALID, f"formula of {d}: {key!r} matches {len(owners)} deliverables")
    return owners[0]


def check_formulas(template: Mapping[str, Any]) -> None:
    """Template-time check that every variable resolves (no values needed)."""
    pricing = template["pricing"]
    schemas = {x["name"]: x["specSchema"] for x in template["deliverables"]}
    inputs = {x["name"]: x for x in template.get("inputs", [])}
    factors = pricing.get("priceFactors", {})
    for d, formula in pricing.get("priceFormula", {}).items():
        try:
            paths = _expr.variables(formula)
        except _expr.ExprError as e:
            fail(TEMPLATE_INVALID, f"priceFormula.{d}: {e}")
        for path in paths:
            if len(path) == 1:
                owner = _owner(template, d, path[0])
                schema = schemas[owner][path[0]]
                values = [schema["const"]] if is_const(schema) else schema.get("enum")
                table = factors.get(owner, {}).get(path[0])
                if values is None or not isinstance(table, Mapping):
                    fail(TEMPLATE_INVALID, f"priceFormula.{d}: {path[0]} needs an enum or const and priceFactors")
                missing = [v for v in values if factor_key(v) not in table]
                if missing:
                    fail(TEMPLATE_INVALID, f"priceFactors.{owner}.{path[0]} misses {missing}")
            elif len(path) == 3 and path[0] == "spec":
                schema = schemas.get(path[1], {}).get(path[2])
                if schema is None or not is_numeric(schema):
                    fail(TEMPLATE_INVALID, f"priceFormula.{d}: {'.'.join(path)} is not a numeric spec key")
            elif len(path) == 3 and path[0] == "inputs":
                material = inputs.get(path[1])
                schema = (material or {}).get("specSchema", {}).get(path[2])
                if material is None or not material.get("required") or schema is None or not is_numeric(schema):
                    fail(TEMPLATE_INVALID,
                         f"priceFormula.{d}: {'.'.join(path)} is not a numeric key of a required material")
            else:
                fail(TEMPLATE_INVALID, f"priceFormula.{d}: unknown variable {'.'.join(path)}")
    try:
        paths = _expr.variables(pricing["depositFormula"])
    except _expr.ExprError as e:
        fail(TEMPLATE_INVALID, f"depositFormula: {e}")
    if any(p != ["total"] for p in paths):
        fail(TEMPLATE_INVALID, "depositFormula may only use total")


def _line_item(template: Mapping[str, Any], d: str, spec: Mapping[str, Any], inputs: Mapping[str, Any]) -> float:
    schemas = {x["name"]: x["specSchema"] for x in template["deliverables"]}
    materials = {x["name"]: x["specSchema"] for x in template.get("inputs", [])}
    factors = template["pricing"].get("priceFactors", {})

    def resolve(path: list[str]) -> float:
        if len(path) == 1:
            owner = _owner(template, d, path[0])
            value = value_of(schemas[owner], spec.get(owner, {}), path[0])
            return factors[owner][path[0]][factor_key(value)]
        if path[0] == "spec":
            return value_of(schemas[path[1]], spec.get(path[1], {}), path[2])
        return value_of(materials[path[1]], inputs[path[1]], path[2])

    try:
        value = _expr.evaluate(template["pricing"]["priceFormula"][d], resolve)
    except _expr.ExprError as e:            # the formula passed the template check: the values make it fail
        fail(CARD_INVALID, f"priceFormula.{d}: {e}")
    except (KeyError, TypeError) as e:
        fail(CARD_INVALID, f"priceFormula.{d}: no value or factor for {e}")
    if value < 0:
        fail(CARD_INVALID, f"priceFormula.{d} is negative for these values")
    return value


def derive(template: Mapping[str, Any], spec: Mapping[str, Any], inputs: Mapping[str, Any],
           line_items: Mapping[str, Any] | None = None) -> dict:
    """Compute ``derived``. For proposal templates the provider supplies ``line_items``."""
    pricing = template["pricing"]
    currency = pricing["currency"]
    names = [x["name"] for x in template["deliverables"]]
    items: dict[str, Decimal] = {}
    if template["formation"] == "proposal":
        if line_items is None or set(line_items) != set(names):
            fail(CARD_INVALID, "a proposal card needs a line item for every deliverable")
        for d in names:
            v = line_items[d]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
                fail(CARD_INVALID, f"lineItems.{d} must be a non-negative amount")
            written = Decimal(repr(v)) if isinstance(v, float) else Decimal(v)
            if written != quantize(written, currency):
                fail(CARD_INVALID, f"lineItems.{d} has more decimals than the currency's minor unit")
            items[d] = written
    else:
        for d in names:
            items[d] = quantize(_line_item(template, d, spec, inputs), currency)
    total = sum(items.values(), Decimal(0))
    try:
        deposit = quantize(_expr.evaluate(pricing["depositFormula"], lambda p: float(total)), currency)
    except _expr.ExprError as e:
        fail(CARD_INVALID, f"depositFormula: {e}")
    if deposit < 0 or deposit > total:
        fail(CARD_INVALID, f"deposit {deposit} is outside 0..{total}")
    rest = total - deposit
    weights = [int(pricing["paymentWeights"][d]) for d in names]
    whole = sum(weights)
    amounts = [_round_fraction(Fraction(rest) * w / whole, currency) for w in weights[:-1]]
    amounts.append(rest - sum(amounts, Decimal(0)))
    if amounts[-1] < 0:
        fail(CARD_INVALID, "rounding leaves the last period negative")
    return {
        "lineItems": {d: to_wire(items[d]) for d in names},
        "total": to_wire(total),
        "deposit": to_wire(deposit),
        "periods": [{"deliverable": d, "amount": to_wire(a)} for d, a in zip(names, amounts)],
    }


def deposit_notice(derived: Mapping[str, Any]) -> str | None:
    """Client-side notice (not a rule): a deposit above 20 % of the total."""
    total, deposit = Decimal(str(derived["total"])), Decimal(str(derived["deposit"]))
    if total > 0 and deposit > total * DEPOSIT_NOTICE_RATIO:
        return ("定金超过总价的 20%：《民法典》第 586 条，定金不得超过主合同标的额的 20%，"
                "超过部分不产生定金效力")
    return None


__all__ = ["MINOR_UNITS", "minor_units", "quantize", "to_wire", "factor_key", "check_formulas", "derive",
           "deposit_notice"]
