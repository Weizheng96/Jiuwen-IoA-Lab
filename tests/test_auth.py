# -*- coding: utf-8 -*-
"""账号与会话：dev 固定验证码、注册登录拿 cookie、/api/me 需登录、管理员口令与限速、open 模式的注册约束。"""
from __future__ import annotations

import tempfile
from pathlib import Path

import httpx
import pytest

from a2c.errors import A2CError

from _support import COOKIE, phone


def test_dev_code_register_login_me(platform, client):
    p = phone()
    r = client.post("/api/auth/code", json={"phone": p})
    assert r.status_code == 200 and r.json()["code"] == "123456"
    r = client.post("/api/auth/register", json={"phone": p, "code": "123456"})
    assert r.status_code == 200 and r.json()["phone"] == p
    assert COOKIE in client.cookies
    me = client.get("/api/me")
    assert me.status_code == 200 and me.json()["phone"] == p and me.json()["wallet"] == "0.00"
    # 错验证码不能注册
    assert client.post("/api/auth/register", json={"phone": phone(), "code": "000000"}).status_code == 400
    # 重新登录拿到新 cookie
    with httpx.Client(base_url=platform.s.base_url, trust_env=False) as c2:
        r = c2.post("/api/auth/login", json={"phone": p, "code": "123456"})
        assert r.status_code == 200 and COOKIE in c2.cookies
        assert c2.get("/api/me").json()["id"] == me.json()["id"]


def test_me_requires_login(platform):
    with httpx.Client(base_url=platform.s.base_url, trust_env=False) as c:
        assert c.get("/api/me").status_code == 401
        assert c.get("/api/wallet").status_code == 401
        assert c.get("/api/orders").status_code == 401
        # 伪造 cookie 不被接受
        c.cookies.set(COOKIE, "bm90LWEtdG9rZW4")
        assert c.get("/api/me").status_code == 401


def test_admin_requires_password_and_rate_limit(platform):
    pf = platform
    pf.auth.rate.hits.clear()
    try:
        with httpx.Client(base_url=pf.s.base_url, trust_env=False) as c:
            for path in ("/api/admin/stats", "/api/admin/users", "/api/admin/registry", "/api/admin/runs"):
                assert c.get(path).status_code == 401, path
            assert c.post("/api/admin/wallet", json={"user_id": "x", "delta": "1"}).status_code == 401
            # 错口令 400；5 次之后同一 IP 被限速（仍是 400，但理由不同）
            for _ in range(5):
                r = c.post("/api/admin/login", json={"password": "wrong"})
                assert r.status_code == 400 and "口令错误" in r.json()["error"]
            r = c.post("/api/admin/login", json={"password": "wrong"})
            assert r.status_code == 400 and "频繁" in r.json()["error"]
            r = c.post("/api/admin/login", json={"password": "test"})       # 限速期间对的口令也进不去
            assert r.status_code == 400 and "频繁" in r.json()["error"]
            pf.auth.rate.hits.clear()
            r = c.post("/api/admin/login", json={"password": "test"})
            assert r.status_code == 200 and COOKIE in c.cookies
            assert c.get("/api/admin/stats").status_code == 200
            # 管理员会话不是用户会话
            assert c.get("/api/me").status_code == 401
    finally:
        pf.auth.rate.hits.clear()


def test_open_mode_auth():
    """trial 模式 + 邀请注册直接构造 Auth：注册必须密码 + 邀请码，不提供验证码。"""
    from ioalab.config import Settings
    from ioalab.psp.auth import Auth
    from ioalab.store import DB

    tmp = Path(tempfile.mkdtemp(prefix="ioalab-open-"))
    s = Settings(mode="trial", registration="invite", data=tmp)
    assert s.open and s.needs_per_day == 20
    auth = Auth(DB(tmp / "open.sqlite"), s)
    with pytest.raises(A2CError):
        auth.send_code("13800000000", "1.2.3.4")
    with pytest.raises(A2CError, match="密码"):
        auth.register("13800000000", code="123456", ip="1.2.3.4")
    with pytest.raises(A2CError, match="邀请码"):
        auth.register("13800000000", password="longpassword1", ip="1.2.3.4")
    with pytest.raises(A2CError, match="邀请码"):
        auth.register("13800000000", password="longpassword1", invite="not-a-code", ip="1.2.3.4")
    code = auth.new_invite()
    u = auth.register("13800000000", password="longpassword1", invite=code, ip="1.2.3.4")
    assert u["phone"] == "13800000000"
    # 邀请码一次性
    with pytest.raises(A2CError, match="邀请码"):
        auth.register("13800000001", password="longpassword1", invite=code, ip="1.2.3.5")
    # 登录只认密码，固定码无效
    with pytest.raises(A2CError, match="密码"):
        auth.login("13800000000", code="123456", ip="1.2.3.6")
    tok = auth.login("13800000000", password="longpassword1", ip="1.2.3.6")["token"]
    assert auth.verify(tok) == u["id"]
    # 按 IP 限速（5 次 / 小时，成功的也计）：1.2.3.4 已用 4 次，第 5 次仍按正常规则拒绝，第 6 次被限速
    with pytest.raises(A2CError, match="邀请码"):
        auth.register("13800000003", password="longpassword1", invite="x", ip="1.2.3.4")
    with pytest.raises(A2CError, match="频繁"):
        auth.register("13800000003", password="longpassword1", invite="x", ip="1.2.3.4")
    # 虚拟用户（管理员投放）不受邀请码约束
    assert auth.register("13800000002", is_virtual=True)["phone"] == "13800000002"
