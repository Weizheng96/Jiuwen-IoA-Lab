"""编码层：I-JSON、JCS、摘要、时刻与时长、JWS 头参数与新鲜度。"""
from datetime import timedelta

import pytest

from a2c.codec import canonical, digest, loads, parse_duration, parse_time
from a2c.errors import A2CError
from a2c.jws import Signer, check_fresh, header_of, sign, verify

from .support import T0


def test_jcs_numbers_and_order():
    """RFC 8785：数字的 ES6 写法、键按 UTF-16 排序。"""
    assert canonical({"b": 1.5, "a": [1e21, 1e-7, -0.0, 333333333.33333329]}) == \
        b'{"a":[1e+21,1e-7,0,333333333.3333333],"b":1.5}'
    assert canonical({"€": 1, "a": 2, "😀": 3}) == '{"a":2,"€":1,"😀":3}'.encode()


def test_digest_excludes_members():
    """摘要按排除规则：去掉的成员不影响摘要。"""
    a = {"x": 1, "signatures": {"1": "s"}}
    assert digest(a, ("signatures",)) == digest({"x": 1})
    assert digest(a) != digest({"x": 1})


@pytest.mark.parametrize("text", ['{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}', "[9007199254740993]", "{"])
def test_loads_rejects_non_ijson(text):
    """重复键、NaN、超出 2^53 的整数、坏 JSON 一律 invalid-object。"""
    with pytest.raises(A2CError) as e:
        loads(text)
    assert e.value.code == "invalid-object"


def test_durations_and_times():
    """时长只认整数 PnDTnHnMnS；时刻须是 UTC 带 Z。"""
    assert parse_duration("P1DT2H3M4S") == timedelta(days=1, hours=2, minutes=3, seconds=4)
    for bad in ("P1Y", "P1W", "PT1.5S", "P", "1D"):
        with pytest.raises(A2CError):
            parse_duration(bad)
    assert parse_time("2026-10-20T09:00:00Z") == T0
    with pytest.raises(A2CError):
        parse_time("2026-10-20T09:00:00+08:00")


def _signer(kid=None):
    return Signer.generate("https://x.example", kid=kid)


def test_sign_verify_and_header_binding():
    """签名覆盖槽位、期号、清单摘要；任一不符都验不过。"""
    s = _signer("k1")
    sig = sign("sha256:" + "a" * 64, s, "4", T0, period=2, manifest="sha256:" + "b" * 64)
    h = verify(sig, "sha256:" + "a" * 64, s.jwk, slot="4", signer=s.id, period=2, manifest="sha256:" + "b" * 64,
               expect_kid=True)
    assert h["crit"] == ["a2c-manifest", "a2c-period", "a2c-signer", "a2c-slot", "a2c-ts"]
    for kw in ({"slot": "5"}, {"period": 3}, {"manifest": "sha256:" + "c" * 64}):
        args = {"slot": "4", "period": 2, "manifest": "sha256:" + "b" * 64, **kw}
        with pytest.raises(A2CError) as e:
            verify(sig, "sha256:" + "a" * 64, s.jwk, signer=s.id, expect_kid=True, **args)
        assert e.value.code == "bad-signature"
    with pytest.raises(A2CError):
        verify(sig, "sha256:" + "f" * 64, s.jwk, slot="4", period=2, manifest="sha256:" + "b" * 64, expect_kid=True)


def test_kid_rules():
    """内联公钥的签名不得带 kid；公钥集里的必须带。"""
    inline, published = _signer(), _signer("k1")
    sig = sign("sha256:" + "a" * 64, inline, "1", T0)
    verify(sig, "sha256:" + "a" * 64, inline.inline_jwk, slot="1")
    with pytest.raises(A2CError):
        verify(sig, "sha256:" + "a" * 64, inline.inline_jwk, slot="1", expect_kid=True)
    sig2 = sign("sha256:" + "a" * 64, published, "2", T0)
    with pytest.raises(A2CError):
        verify(sig2, "sha256:" + "a" * 64, published.jwk, slot="2")


def test_crit_and_alg_enforced():
    """crit 必须恰好列出 a2c-* 头；alg 只认 Ed25519。"""
    import json
    from a2c.codec import b64url, b64url_decode
    s = _signer()
    sig = sign("sha256:" + "a" * 64, s, "1", T0)
    h = json.loads(b64url_decode(sig["protected"]))
    for bad in ({**h, "crit": ["a2c-slot"]}, {**h, "alg": "EdDSA"}):
        forged = {"protected": b64url(json.dumps(bad).encode()), "signature": sig["signature"]}
        with pytest.raises(A2CError):
            header_of(forged)


def test_freshness():
    """新鲜度：相差超过 5 分钟即拒。"""
    s = _signer()
    h = header_of(sign("sha256:" + "a" * 64, s, "6", T0))
    check_fresh(h, T0 + timedelta(minutes=4))
    with pytest.raises(A2CError) as e:
        check_fresh(h, T0 + timedelta(minutes=6))
    assert e.value.code == "bad-signature"
