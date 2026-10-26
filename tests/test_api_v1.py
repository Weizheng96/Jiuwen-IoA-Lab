# -*- coding: utf-8 -*-
"""委托方开放接口 /api/v1（九问 swarm 驾驭）：token → 下需求 → 上传材料 → 检索 → 模板 → 拟卡 → 签1 → 开工 → 平台跑完取件 · 验收 · 付款 · 结清；
验收亲签时的待确认与答复；放弃；归属校验；/api/needs 不再收路径。桩模式，不调模型。"""
from __future__ import annotations

import io
import json
import time

import httpx

from _support import PASSWORD, new_service, username, wait_until


def _client(platform, token=None):
    headers = {"Authorization": "Bearer " + token} if token else {}
    return httpx.Client(base_url=platform.s.base_url, trust_env=False, timeout=60, headers=headers)


def _token(platform, client):
    name = username()
    r = client.post("/api/auth/register", json={"username": name, "password": PASSWORD})
    assert r.status_code == 200
    with _client(platform) as c:
        r = c.post("/api/v1/token", json={"username": name, "password": PASSWORD})
        assert r.status_code == 200 and r.json()["token"] and r.json()["assistant"].startswith("asst-")
        return name, r.json()


def test_external_delegation_end_to_end(platform, client):
    pf = platform
    owner_s, svc_row, provider, d = new_service(pf)          # 朋友圈文案工坊（预批准型，6）
    name, tok = _token(pf, client)
    with _client(pf, tok["token"]) as c:
        me = c.get("/api/v1/me").json()
        assert me["username"] == name and me["wallet"] == "100.00" and me["assistant"] == tok["assistant"]
        # 下需求（external）；带 path 的材料被拒
        assert c.post("/api/v1/delegations", json={"text": "x", "materials": [{"name": "a", "path": "C:/secret"}]}).status_code == 400
        n = c.post("/api/v1/delegations", json={"text": "给小满美甲写三条朋友圈", "scope": {"budget": 30, "category": "文案"}, "autoSign": True}).json()
        nid = n["id"]
        assert n["state"] == "open" and n["driver"] == "external"
        # 上传一份材料（本服务不需要，只验接口）
        r = c.post(f"/api/v1/delegations/{nid}/materials", files={"file": ("brief.txt", io.BytesIO("店名小满美甲".encode()), "text/plain")},
                   data={"name": "brief", "declared": json.dumps({"chars": 6})})
        assert r.status_code == 200 and r.json()["materials"][0]["name"] == "brief" and "path" not in r.json()["materials"][0]
        assert c.get(f"/api/v1/delegations/{nid}/materials").json()[0]["preview"] == "店名小满美甲"
        # 检索 → 模板 → 拟卡
        hits = c.post(f"/api/v1/delegations/{nid}/search", json={"query": "帮奶茶店写三条朋友圈文案", "top_k": 50}).json()
        assert any(h["id"] == svc_row["id"] for h in hits)
        rep = c.get("/api/v1/providers/reputation", params={"provider": provider}).json()
        assert rep["completed"] == 0
        [t] = c.get("/api/v1/providers/templates", params={"provider": provider}).json()
        assert "signatures" not in t and t["formation"] == "preapproval"
        draft = c.post(f"/api/v1/delegations/{nid}/cards", json={"templateDigest": t["templateDigest"], "cardId": "C-API",
                                                                  "spec": {"posts": {"shop": "小满美甲", "theme": "春日上新"}}}).json()
        assert draft["cardDigest"].startswith("sha256:") and str(draft["derived"]["total"]) in ("6", "6.0", "6.00") and "error" not in draft
        cd = draft["cardDigest"]
        # 签1 → 开工
        r = c.post(f"/api/v1/delegations/{nid}/cards/{cd}/sign").json()
        assert r == {"signed": True, "effective": True}
        r = c.post(f"/api/v1/delegations/{nid}/cards/{cd}/start").json()
        assert r == {"started": True}
        det = c.get(f"/api/v1/delegations/{nid}").json()
        assert det["state"] == "started" and det["card_digest"] == cd and det["orders"][0]["state"] == "started"
        # 平台跑完：取件 · 验收 · 付款 · 结清
        wait_until(lambda: c.get(f"/api/v1/orders/{cd}").json()["state"] == "completed", timeout=90, what="结清")
        o = c.get(f"/api/v1/orders/{cd}").json()
        assert o["signatures"]["1"] and o["signatures"]["7"] == [True] and o["provider_name"]
        files = c.get(f"/api/v1/orders/{cd}/deliveries/1").json()["files"]
        assert "posts.json" in files and "小满美甲" in files["posts.json"]
        assert c.get("/api/v1/me").json()["wallet"] == "94.00"
        evs = c.get(f"/api/v1/delegations/{nid}/events").json()
        ops = {e["op"] for e in evs}
        assert {"need.create", "discovery.search", "order.signed", "order.started", "order.accepted", "order.completed"} <= ops
        assert c.get(f"/api/v1/delegations/{nid}").json()["state"] == "completed"
    # 归属：别人的 token 看不到
    with _client(pf, _token(pf, client)[1]["token"]) as other:
        assert other.get(f"/api/v1/delegations/{nid}").status_code == 403
        assert other.get(f"/api/v1/orders/{cd}").status_code == 403


def test_external_accept_confirmation_and_abandon(platform, client):
    pf = platform
    owner_s, svc_row, provider, d = new_service(pf)
    name, tok = _token(pf, client)
    with _client(pf, tok["token"]) as c:
        n = c.post("/api/v1/delegations", json={"text": "再来三条", "scope": {"budget": 30, "sign7": "user"}, "autoSign": False}).json()
        nid = n["id"]
        [t] = c.get("/api/v1/providers/templates", params={"provider": provider}).json()
        cd = c.post(f"/api/v1/delegations/{nid}/cards", json={"templateDigest": t["templateDigest"], "cardId": "C-CF",
                                                               "spec": {"posts": {"shop": "小满美甲", "theme": "夏日"}}}).json()["cardDigest"]
        # swarm 驾驭：签字不再上升确认（技能包要求 swarm 自己先问用户）
        assert c.post(f"/api/v1/delegations/{nid}/cards/{cd}/sign").json()["signed"] is True
        assert c.post(f"/api/v1/delegations/{nid}/cards/{cd}/start").json() == {"started": True}
        # 验收亲签 + 关代签：交付后平台写一条待确认，swarm 答复后才付款结清
        wait_until(lambda: any(p["kind"] == "accept" for p in c.get("/api/v1/confirmations").json()), timeout=90, what="验收确认")
        [cf] = [p for p in c.get("/api/v1/confirmations").json() if p["kind"] == "accept"]
        assert cf["need_id"] == nid and c.get("/api/v1/me").json()["pending"]
        assert c.get(f"/api/v1/orders/{cd}").json()["state"] == "started"
        assert c.post(f"/api/v1/confirmations/{cf['id']}", json={"granted": True}).json()["ok"]
        wait_until(lambda: c.get(f"/api/v1/orders/{cd}").json()["state"] == "completed", timeout=90, what="结清")
        # 已开工 / 已结清的需求不能放弃；新开一条可以放弃，放弃后不能再操作
        assert c.post(f"/api/v1/delegations/{nid}/abandon").status_code == 400
        n2 = c.post("/api/v1/delegations", json={"text": "算了", "scope": {"budget": 10}}).json()
        assert c.post(f"/api/v1/delegations/{n2['id']}/abandon").json()["ok"]
        assert c.get(f"/api/v1/delegations/{n2['id']}").json()["state"] == "abandoned"
        assert c.post(f"/api/v1/delegations/{n2['id']}/search", json={"query": "x"}).status_code == 400


def test_external_countersign_declined_counts_switch(platform, client):
    """待批准型被「已接满」拒绝：返回 reason 与换家计数；/api/needs（用户端）不再接受材料路径。"""
    pf = platform
    owner_s, svc_row, provider, d = new_service(pf, "resume-polish", capacity=0)      # capacity 0 → 一律接满
    name, tok = _token(pf, client)
    with _client(pf, tok["token"]) as c:
        n = c.post("/api/v1/delegations", json={"text": "润色简历", "scope": {"budget": 30}}).json()
        nid = n["id"]
        r = c.post(f"/api/v1/delegations/{nid}/materials", files={"file": ("resume.json", json.dumps({"name": "李", "experience": [{"a": 1}]}).encode(), "application/json")},
                   data={"name": "resume", "declared": json.dumps({"entries": 1, "chars": 10})})
        assert r.status_code == 200
        [t] = c.get("/api/v1/providers/templates", params={"provider": provider}).json()
        draft = c.post(f"/api/v1/delegations/{nid}/cards", json={"templateDigest": t["templateDigest"], "cardId": "C-RS",
                                                                  "spec": {"resume": {"target": "后端"}}, "materials": ["resume"]}).json()
        assert "cardDigest" in draft and draft["inputPrecheck"].get("resume") in ("pass", None)
        r = c.post(f"/api/v1/delegations/{nid}/cards/{draft['cardDigest']}/sign").json()
        assert r["signed"] is True and r["countersigned"] is False and "接满" in r["reason"] and r["switches"] == 1 and r["hint"] == "换下一家"
        assert c.get(f"/api/v1/delegations/{nid}").json()["tried"] == [provider]
    assert client.post("/api/needs", json={"text": "x", "materials": [{"name": "a", "path": "/etc/passwd"}]}).status_code in (400, 401)
