# -*- coding: utf-8 -*-
"""账号与会话：用户名密码注册登录拿 cookie 与 token、每次成功登录赠币、Bearer 访问、/api/me 需登录、管理员口令与限速、
trial + 邀请注册的约束、内部账号种子幂等。"""
from __future__ import annotations

import tempfile
from pathlib import Path

import httpx
import pytest

from a2c.errors import A2CError

from _support import COOKIE, PASSWORD, username


def test_register_login_me_and_each_login_credit(platform, client):
    name = username()
    r = client.post("/api/auth/register", json={"username": name, "password": PASSWORD, "display_name": "测试 A"})
    assert r.status_code == 200
    body = r.json()
    assert body["username"] == name and body["kind"] == "external" and body["display_name"] == "测试 A" and body["token"]
    assert COOKIE in client.cookies
    me = client.get("/api/me")
    assert me.status_code == 200 and me.json()["username"] == name and me.json()["kind"] == "external"
    assert me.json()["wallet"] == "100.00", "注册即首次登录，赠 100"
    # 用户名规则与重名
    assert client.post("/api/auth/register", json={"username": "a", "password": PASSWORD}).status_code == 400
    assert client.post("/api/auth/register", json={"username": name, "password": PASSWORD}).status_code == 400
    assert client.post("/api/auth/register", json={"username": username(), "password": "123"}).status_code == 400
    # 失败认证不发放；同日第二次成功认证再赠100。
    with httpx.Client(base_url=platform.s.base_url, trust_env=False) as c2:
        assert c2.post("/api/auth/login", json={"username": name, "password": "wrong"}).status_code == 400
        r = c2.post("/api/auth/login", json={"username": name, "password": PASSWORD})
        assert r.status_code == 200 and COOKIE in c2.cookies and r.json()["token"]
        assert c2.get("/api/me").json()["id"] == me.json()["id"]
        assert c2.get("/api/me").json()["wallet"] == "200.00"
        token = r.json()["token"]
    # Bearer 头等价于 cookie（脚本用）
    with httpx.Client(base_url=platform.s.base_url, trust_env=False) as c3:
        r = c3.get("/api/me", headers={"Authorization": "Bearer " + token})
        assert r.status_code == 200 and r.json()["username"] == name
        assert c3.get("/api/me", headers={"Authorization": "Bearer bad"}).status_code == 401
        assert c3.get('/api/me',headers={'Authorization':'Bearer '+token}).json()['wallet']=='200.00'
    logs=platform.db.rows("SELECT delta FROM wallet_log WHERE user_id=? AND kind='login'",me.json()['id'])
    assert len(logs)==2 and all(str(x['delta'])=='100.00' for x in logs)
    assert client.post("/api/auth/code", json={}).status_code in (404, 405), "不再有验证码接口"


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
                assert r.status_code == 400 and "口令错误" in r.json()["error"]["message"]
            r = c.post("/api/admin/login", json={"password": "wrong"})
            assert r.status_code == 400 and "频繁" in r.json()["error"]["message"]
            r = c.post("/api/admin/login", json={"password": "test"})       # 限速期间对的口令也进不去
            assert r.status_code == 400 and "频繁" in r.json()["error"]["message"]
            pf.auth.rate.hits.clear()
            r = c.post("/api/admin/login", json={"password": "test"})
            assert r.status_code == 200 and COOKIE in c.cookies
            assert c.get("/api/admin/stats").status_code == 200
            # 管理员会话不是用户会话
            assert c.get("/api/me").status_code == 401
    finally:
        pf.auth.rate.hits.clear()


def test_trial_invite_registration():
    """trial 模式 + 邀请注册直接构造 Auth：外部账号须密码 ≥ 8 与邀请码、按 IP 限速；内部账号不受约束。"""
    from ioalab.config import Settings
    from ioalab.identity.auth import Auth
    from ioalab.store import DB

    tmp = Path(tempfile.mkdtemp(prefix="ioalab-trial-"))
    s = Settings(mode="trial", registration="invite", data=tmp)
    assert s.open and s.needs_per_day == 20
    auth = Auth(DB(tmp / "trial.sqlite"), s)
    with pytest.raises(A2CError, match="密码"):
        auth.register("alice", "short", ip="1.2.3.4")
    with pytest.raises(A2CError, match="邀请码"):
        auth.register("alice", "longpassword1", ip="1.2.3.4")
    with pytest.raises(A2CError, match="邀请码"):
        auth.register("alice", "longpassword1", invite="not-a-code", ip="1.2.3.4")
    code = auth.new_invite()
    u = auth.register("alice", "longpassword1", invite=code, ip="1.2.3.4")
    assert u["username"] == "alice" and u["kind"] == "external"
    # 邀请码一次性
    with pytest.raises(A2CError, match="邀请码"):
        auth.register("bob", "longpassword1", invite=code, ip="1.2.3.5")
    # 登录只认密码
    with pytest.raises(A2CError, match="密码"):
        auth.login("alice", "wrong", ip="1.2.3.6")
    tok = auth.login("alice", "longpassword1", ip="1.2.3.6")["token"]
    assert auth.verify(tok) == u["id"]
    # 按 IP 限速（5 次 / 小时，成功的也计）：1.2.3.4 已用 4 次，第 5 次仍按正常规则拒绝，第 6 次被限速
    with pytest.raises(A2CError, match="邀请码"):
        auth.register("carol", "longpassword1", invite="x", ip="1.2.3.4")
    with pytest.raises(A2CError, match="频繁"):
        auth.register("carol", "longpassword1", invite="x", ip="1.2.3.4")
    # 内部账号不受邀请码与限速约束，密码可短
    assert auth.register("ISP_001", "001", kind="isp", display_name="内部服务方 · 测试")["kind"] == "isp"
    assert auth.login("ISP_001", "001", ip="9.9.9.9")["display_name"] == "内部服务方 · 测试"
    # closed：外部不开放，内部仍可建
    s2 = Settings(mode="trial", registration="closed", data=tmp)
    auth2 = Auth(DB(tmp / "closed.sqlite"), s2)
    with pytest.raises(A2CError, match="不开放"):
        auth2.register("dave", "longpassword1", ip="1.2.3.7")
    assert auth2.register("ISC_001", "001", kind="isc")["kind"] == "isc"


def test_internal_accounts_seeded_idempotent(platform):
    from ioalab.seed.accounts import ensure_accounts
    pf = platform
    before = pf.db.one("SELECT COUNT(*) AS n FROM users")["n"]
    acc = ensure_accounts(pf)
    after = pf.db.one("SELECT COUNT(*) AS n FROM users")["n"]
    assert len(acc["isp"]) >= 1 and len(acc["isc"]) == pf.s.isc_count
    assert ensure_accounts(pf) == acc and pf.db.one("SELECT COUNT(*) AS n FROM users")["n"] == after
    assert after - before <= len(acc["isp"]) + len(acc["isc"])
    row = pf.db.one("SELECT username, kind, display_name FROM users WHERE id=?", acc["isp"][0])
    assert row["username"] == "ISP_001" and row["kind"] == "isp" and row["display_name"].startswith("内部服务方")
    assert pf.auth.login("ISC_001", "001")["kind"] == "isc"
    assert not pf.db.one("SELECT id FROM agents WHERE owner_id=?", acc["isc"][0])
