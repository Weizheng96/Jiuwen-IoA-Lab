"""取值域（JSON Schema 子集）、价格表达式与算价。"""
import pytest

from a2c.errors import A2CError
from a2c.expr import ExprError, evaluate, variables
from a2c.pricing import deposit_notice, derive, quantize
from a2c.schema import check_schema, validate

from .support import template_draft


@pytest.mark.parametrize("schema", [
    {"enum": ["a", "b"]}, {"const": "mp4"}, {"type": "integer", "minimum": 1, "maximum": 8},
    {"type": "string", "maxLength": 300}, {"type": "string", "format": "date"}, {"enum": [12, 24], "type": "integer"},
])
def test_allowed_schemas(schema):
    """七个关键字的合法组合放行。"""
    check_schema(schema, "x")


@pytest.mark.parametrize("schema", [
    {"type": "string"}, {"type": "boolean"}, {"pattern": "a"}, {"type": "string", "format": "email", "maxLength": 3},
    {"const": "a", "type": "string"}, {"enum": []}, {"type": "number", "maxLength": 3}, {"$ref": "#/x"},
    {"type": "integer", "minimum": 5, "maximum": 1}, {"minLength": 1, "type": "string", "maxLength": 3},
])
def test_rejected_schemas(schema):
    """子集外的关键字或组合即 template-invalid。"""
    with pytest.raises(A2CError) as e:
        check_schema(schema, "x")
    assert e.value.code == "template-invalid"


def test_values():
    """取值校验：闭区间、码点长度、日期、整数可为 1.0。"""
    validate({"type": "integer", "minimum": 1, "maximum": 8}, 8, "x")
    validate({"type": "integer"}, 1.0, "x")
    validate({"type": "string", "maxLength": 2}, "😀😀", "x")
    validate({"type": "string", "format": "date"}, "2028-02-29", "x")
    for schema, value in (({"type": "integer", "maximum": 8}, 9), ({"type": "string", "maxLength": 2}, "abc"),
                          ({"type": "string", "format": "date"}, "2026-02-29"), ({"type": "integer"}, 1.5),
                          ({"enum": [12, 24]}, 13), ({"type": "number"}, True)):
        with pytest.raises(A2CError) as e:
            validate(schema, value, "x")
        assert e.value.code == "card-invalid"


@pytest.mark.parametrize("expr", ["1e3", "0x10", "1_000", ".5", "5.", "2 ** 3", "f(1)", "a[0]", "--1", "", "1 +"])
def test_expr_rejects(expr):
    """表达式白名单：指数、十六进制、下划线、幂、调用、下标、重复一元运算都拒。"""
    with pytest.raises(ExprError):
        variables(expr)


def test_expr_eval():
    assert evaluate("300 * episodes + 0.5 * (a.b - 2)", lambda p: {"episodes": 1.8, "a.b": 4}[".".join(p)]) == 541.0
    with pytest.raises(ExprError):
        evaluate("1 / x", lambda p: 0)


def test_rounding_half_even():
    """按最小单位银行家舍入，对双精度的精确值舍入。"""
    assert str(quantize(0.125, "CNY")) == "0.12"
    assert str(quantize(0.375, "CNY")) == "0.38"
    assert str(quantize(2.675, "CNY")) == "2.67"
    assert str(quantize(2.5, "JPY")) == "2" and str(quantize(3.5, "JPY")) == "4"


def test_derive_matches_design_example():
    """与 设计/示例 同一套公式：300 + 3360（含 0.002 × 480000）= 3660，定金 366，余款 1:8。"""
    t = template_draft("countersign")
    t["pricing"]["priceFactors"]["final"]["quality"]["1080p"] = 1.2   # 1080p 系数取 1.2，使成片 = 2400 + 960
    d = derive(t, {"outline": {"episodes": 12}, "final": {"quality": "1080p", "notes": ""}},
               {"novel": {"format": "md", "chars": 480000, "digest": "sha256:" + "0" * 64}})
    assert d == {"lineItems": {"outline": 300, "final": 3360}, "total": 3660, "deposit": 366,
                 "periods": [{"deliverable": "outline", "amount": 366}, {"deliverable": "final", "amount": 2928}]}


def test_remainder_goes_to_last_period():
    t = template_draft("proposal", deposit="0")
    t["pricing"]["paymentWeights"] = {"outline": 1, "final": 2}
    d = derive(t, {}, {}, line_items={"outline": 50, "final": 50})
    assert [p["amount"] for p in d["periods"]] == [33.33, 66.67]


def test_deposit_bounds_and_notice():
    """定金须在 0 与总价之间；超过 20% 只提示。"""
    t = template_draft("proposal", deposit="1.5 * total")
    with pytest.raises(A2CError) as e:
        derive(t, {}, {}, line_items={"outline": 10, "final": 10})
    assert e.value.code == "card-invalid"
    assert deposit_notice({"total": 100, "deposit": 20}) is None
    assert "586" in deposit_notice({"total": 100, "deposit": 20.01})
