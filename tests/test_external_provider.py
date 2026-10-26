# -*- coding: utf-8 -*-
"""外部服务方接入：样例服务方在另一个端口起 A2A 应用 → 上传验收器包 → 向平台市场公示模板 → 登记（Agent Card · JWKS · AP2 收款公钥 · 归叶类）
→ 平台的助手（swarm 驾驭，走 /api/v1）对它拟卡 · 签1 · 开工 → 它交付 → 平台取件验收付款 → 它向市场结清 → 平台按 ledger 标完成；
收款归登记账号；信誉 1/1；登记校验（地址不可达 · 没公示模板 · 重复）。桩模式。"""
from __future__ import annotations

import socket
import sys
import tempfile
from pathlib import Path

import httpx
import pytest

from _support import PASSWORD, username, wait_until

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "examples" / "external_provider"))


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _register_user(platform, client):
    name = username()
    assert client.post("/api/auth/register", json={"username": name, "password": PASSWORD}).status_code == 200
    with httpx.Client(base_url=platform.s.base_url, trust_env=False, timeout=60) as c:
        tok = c.post("/api/v1/token", json={"username": name, "password": PASSWORD}).json()["token"]
    return name, tok


def test_external_provider_end_to_end(platform, client):
    from provider import ExternalProvider
    pf = platform
    pf.ensure_seeded(force=True)                       # 分类树要先有（幂等）
    owner, owner_tok = _register_user(pf, client)
    port = _free_port()
    ep = ExternalProvider(platform=pf.s.base_url, public_url=f"http://127.0.0.1:{port}", data_dir=Path(tempfile.mkdtemp(prefix="ioalab-ext-")), stub=True)
    ep.start(owner, PASSWORD, port=port, category="cat_writing")
    try:
        reg = ep.registration
        assert reg["id"].startswith("ext-") and reg["url"] == ep.public_url
        # 平台侧：登记行 · A2X 索引 · 分类树归叶类 · 认证记录 · 收款公钥
        row = pf.db.one("SELECT * FROM agents WHERE id=?", reg["id"])
        assert row["owner_id"] == pf.db.one("SELECT id FROM users WHERE username=?", owner)["id"] and row["status"] == "online"
        assert pf.is_external(ep.public_url) and pf._payee_key(ep.public_url)["kty"] == "EC"
        assert pf.discovery.get_card(reg["id"])["url"] == ep.public_url
        cats = {c["id"]: c for c in pf.discovery.categories()}
        assert reg["id"] in cats["cat_writing"]["services"]
        assert pf.db.one("SELECT 1 FROM certifications WHERE agent_id=?", ep.public_url)
        with httpx.Client(base_url=pf.s.base_url, trust_env=False, timeout=60, headers={"Authorization": "Bearer " + owner_tok}) as c:
            mine = c.get("/api/v1/provider/mine").json()
            assert any(x["id"] == reg["id"] and x["external"] and x["url"] == ep.public_url for x in mine)
        # 委托方（另一账号的 swarm）对它走一单
        cname, ctok = _register_user(pf, client)
        with httpx.Client(base_url=pf.s.base_url, trust_env=False, timeout=90, headers={"Authorization": "Bearer " + ctok}) as c:
            n = c.post("/api/v1/delegations", json={"text": "写一段关于春天的短文", "scope": {"budget": 10, "category": "文案"}, "autoSign": True}).json()
            nid = n["id"]
            hits = c.post(f"/api/v1/delegations/{nid}/search", json={"query": "外部回声写手 短文", "top_k": 60}).json()
            assert any(h["id"] == reg["id"] and h["provider"] == ep.public_url for h in hits)
            [t] = c.get("/api/v1/providers/templates", params={"provider": ep.public_url}).json()
            assert t["provider"] == ep.public_url and "market" not in t.get("signatures", {})   # 摘要去掉签名
            draft = c.post(f"/api/v1/delegations/{nid}/cards", json={"templateDigest": ep.public_url, "cardId": "C-EXT", "spec": {"note": {"topic": "春天"}}}).json()
            cd = draft["cardDigest"]
            assert str(draft["derived"]["total"]) in ("3", "3.0", "3.00") and draft["provider"] == ep.public_url
            assert c.post(f"/api/v1/delegations/{nid}/cards/{cd}/sign").json() == {"signed": True, "effective": True}
            assert c.post(f"/api/v1/delegations/{nid}/cards/{cd}/start").json() == {"started": True}
            wait_until(lambda: c.get(f"/api/v1/orders/{cd}").json()["state"] == "completed", timeout=120, what="外部服务方结清")
            o = c.get(f"/api/v1/orders/{cd}").json()
            assert o["signatures"]["7"] == [True] and o["provider_id"] == ep.public_url
            files = c.get(f"/api/v1/orders/{cd}/deliveries/1").json()["files"]
            assert "note.json" in files and "春天" in files["note.json"]
            assert c.get(f"/api/v1/delegations/{nid}").json()["state"] == "completed"
            assert c.get("/api/v1/me").json()["wallet"] == "97.00"
        # 收款归登记账号；信誉 1/1；样例自己把卡结清了
        assert str(pf.wallets.balance(row["owner_id"])) == "103.00"
        rep = pf.market.reputation(ep.public_url)
        assert rep["completed"] == 1 and rep["successRate"] == 1.0
        assert ep.closed == [cd]
        # 登记校验
        with httpx.Client(base_url=pf.s.base_url, trust_env=False, timeout=60, headers={"Authorization": "Bearer " + owner_tok}) as c:
            body = {"url": ep.public_url, "name": "again", "description": "", "ap2Jwk": ep.ap2_jwk}
            assert "已登记" in c.post("/api/v1/provider/register", json=body).json()["error"]
            body["url"] = f"http://127.0.0.1:{_free_port()}"
            assert "取不到" in c.post("/api/v1/provider/register", json=body).json()["error"]
            body["url"] = pf.s.base_url
            assert "平台自己" in c.post("/api/v1/provider/register", json=body).json()["error"]
            assert "ap2_jwk" in c.post("/api/v1/provider/register", json={**body, "url": "http://127.0.0.1:1", "ap2Jwk": {"kty": "OKP"}}).json()["error"]
    finally:
        ep.stop()
