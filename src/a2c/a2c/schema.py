"""Value domains: a JSON Schema 2020-12 subset (design: 设计/线上编码.md §7).

A ``specSchema`` maps each key to a schema that may only use ``const``, ``enum``, ``type``
(string / integer / number), ``minimum``, ``maximum``, ``maxLength`` and ``format: date``.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Mapping

from .errors import CARD_INVALID, TEMPLATE_INVALID, fail

KEYWORDS = frozenset({"const", "enum", "type", "minimum", "maximum", "maxLength", "format"})
TYPES = frozenset({"string", "integer", "number"})
_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _scalar(v: Any) -> bool:
    return isinstance(v, str) or (isinstance(v, (int, float)) and not isinstance(v, bool))


def _number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def check_schema(schema: Any, where: str) -> None:
    """Template-time check of one schema; raises ``template-invalid``."""
    if not isinstance(schema, Mapping) or not schema:
        fail(TEMPLATE_INVALID, f"{where}: schema must be a non-empty object")
    extra = set(schema) - KEYWORDS
    if extra:
        fail(TEMPLATE_INVALID, f"{where}: keywords not allowed: {sorted(extra)}")
    if "const" in schema:
        if len(schema) != 1 or not _scalar(schema["const"]):
            fail(TEMPLATE_INVALID, f"{where}: const stands alone and is a string or number")
        return
    if "enum" in schema:
        values = schema["enum"]
        if (not isinstance(values, list) or not values or not all(_scalar(v) for v in values)
                or len({repr(v) for v in values}) != len(values)):
            fail(TEMPLATE_INVALID, f"{where}: enum is a non-empty list of distinct strings or numbers")
        if set(schema) - {"enum", "type"}:
            fail(TEMPLATE_INVALID, f"{where}: enum may only be combined with type")
        if "type" in schema:
            for v in values:
                _check_type(schema["type"], v, where, TEMPLATE_INVALID)
        return
    t = schema.get("type")
    if t not in TYPES:
        fail(TEMPLATE_INVALID, f"{where}: type must be one of {sorted(TYPES)}")
    if t == "string":
        if set(schema) - {"type", "maxLength", "format"}:
            fail(TEMPLATE_INVALID, f"{where}: string allows maxLength and format only")
        if "format" in schema and schema["format"] != "date":
            fail(TEMPLATE_INVALID, f"{where}: only format date is allowed")
        if "maxLength" in schema:
            ml = schema["maxLength"]
            if type(ml) is not int or ml < 0:
                fail(TEMPLATE_INVALID, f"{where}: maxLength is a non-negative integer")
        elif "format" not in schema:
            fail(TEMPLATE_INVALID, f"{where}: a string needs maxLength or format")
        return
    if set(schema) - {"type", "minimum", "maximum"}:
        fail(TEMPLATE_INVALID, f"{where}: numbers allow minimum and maximum only")
    for k in ("minimum", "maximum"):
        if k in schema and not _number(schema[k]):
            fail(TEMPLATE_INVALID, f"{where}: {k} must be a number")
    if "minimum" in schema and "maximum" in schema and schema["minimum"] > schema["maximum"]:
        fail(TEMPLATE_INVALID, f"{where}: minimum > maximum")


def check_spec_schema(spec_schema: Any, where: str) -> None:
    if not isinstance(spec_schema, Mapping):
        fail(TEMPLATE_INVALID, f"{where}: specSchema must be an object")
    for key, schema in spec_schema.items():
        if not _KEY.match(key):
            fail(TEMPLATE_INVALID, f"{where}.{key}: key must be an identifier")
        check_schema(schema, f"{where}.{key}")


def is_const(schema: Mapping[str, Any]) -> bool:
    return "const" in schema


def is_numeric(schema: Mapping[str, Any]) -> bool:
    if "const" in schema:
        return _number(schema["const"])
    if "enum" in schema:
        return all(_number(v) for v in schema["enum"])
    return schema.get("type") in ("integer", "number")


def _check_type(t: str, value: Any, where: str, code: str) -> None:
    ok = (isinstance(value, str) if t == "string"
          else _number(value) and (t == "number" or float(value).is_integer()))
    if not ok:
        fail(code, f"{where}: {value!r} is not of type {t}")


def _same(a: Any, b: Any) -> bool:
    if _number(a) and _number(b):
        return a == b
    return type(a) is type(b) and a == b


def validate(schema: Mapping[str, Any], value: Any, where: str) -> None:
    """Card-time check of a filled value; raises ``card-invalid``."""
    if "const" in schema:
        if not _same(schema["const"], value):
            fail(CARD_INVALID, f"{where}: must be {schema['const']!r}")
        return
    if "enum" in schema:
        if not any(_same(v, value) for v in schema["enum"]):
            fail(CARD_INVALID, f"{where}: {value!r} not in {schema['enum']!r}")
        return
    _check_type(schema["type"], value, where, CARD_INVALID)
    if "minimum" in schema and value < schema["minimum"]:
        fail(CARD_INVALID, f"{where}: {value!r} < minimum {schema['minimum']!r}")
    if "maximum" in schema and value > schema["maximum"]:
        fail(CARD_INVALID, f"{where}: {value!r} > maximum {schema['maximum']!r}")
    if "maxLength" in schema and len(value) > schema["maxLength"]:
        fail(CARD_INVALID, f"{where}: longer than {schema['maxLength']} characters")
    if schema.get("format") == "date":
        try:
            if not _DATE.match(value):
                raise ValueError
            date.fromisoformat(value)
        except ValueError:
            fail(CARD_INVALID, f"{where}: {value!r} is not a full-date")


def validate_filled(spec_schema: Mapping[str, Any], values: Any, where: str) -> None:
    """Every non-const key has a value that fits; no other keys (invariants 1 and 2)."""
    if not isinstance(values, Mapping):
        fail(CARD_INVALID, f"{where}: must be an object")
    want = {k for k, s in spec_schema.items() if not is_const(s)}
    if set(values) != want:
        missing, extra = sorted(want - set(values)), sorted(set(values) - want)
        fail(CARD_INVALID, f"{where}: missing {missing}, unexpected {extra}")
    for k in want:
        validate(spec_schema[k], values[k], f"{where}.{k}")


def value_of(spec_schema: Mapping[str, Any], values: Mapping[str, Any], key: str) -> Any:
    """Value of ``key``: the const from the template, otherwise the filled value."""
    schema = spec_schema[key]
    return schema["const"] if is_const(schema) else values[key]


__all__ = ["KEYWORDS", "TYPES", "check_schema", "check_spec_schema", "is_const", "is_numeric", "validate",
           "validate_filled", "value_of"]
