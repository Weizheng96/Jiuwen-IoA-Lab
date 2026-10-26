# -*- coding: utf-8 -*-
"""管理员接口：创建虚拟用户（带助手送 100）、调整钱包、注册表、统计字段、回放列表、检索带 mode。"""
from __future__ import annotations

from decimal import Decimal

from _support import as_admin, new_service, phone


STATS_KEYS = {"assistants", "services", "runnable", "users", "needs", "orders_today", "transfers_today", "volume_today",
              "circulating", "completed", "arbitrated", "declined", "success_rate", "avg_duration_s", "llm_calls", "tokens",
              "discovery", "taxonomy", "run", "mode"}


def test_admin_api(platform, client):
    pf = platform
    as_admin(client, pf)
    p = phone()
    r = client.post("/api/admin/users", json={"count": 1, "phone": p, "assistant": True, "assistantName": "小艺", "note": "投放"})
    assert r.status_code == 200
    [u] = r.json()
    assert u["phone"] == p
    assert pf.wallets.balance(u["id"]) == Decimal("100.00")
    users = {x["id"]: x for x in client.get("/api/admin/users").json()}
    assert users[u["id"]]["is_virtual"] == 1 and users[u["id"]]["balance"] == "100.00" and "pw_hash" not in users[u["id"]]
    assert [ag["role"] for ag in users[u["id"]]["agents"]] == ["assistant"]
    # 带 credit 的批量创建：送 100 + 发放 20
    r = client.post("/api/admin/users", json={"count": 2, "assistant": True, "credit": "20"})
    assert r.status_code == 200 and len(r.json()) == 2
    for x in r.json():
        assert pf.wallets.balance(x["id"]) == Decimal("120.00")

    # 调整钱包：发放 / 扣减记录
    r = client.post("/api/admin/wallet", json={"user_id": u["id"], "delta": "-40", "note": "测试扣减"})
    assert r.status_code == 200 and r.json()["balance"] == "60.00"
    log = pf.wallets.log(u["id"])
    assert log[0]["delta"] == "-40.00" and log[0]["by"] == "admin" and log[0]["note"] == "测试扣减"
    assert client.post("/api/admin/wallet", json={"user_id": u["id"], "delta": "-1000"}).status_code == 400

    # 注册表
    owner_s, svc_row, provider, d = new_service(pf)
    r = client.get("/api/admin/registry")
    assert r.status_code == 200
    reg = r.json()
    assert "taxonomy" in reg
    me = next(s for s in reg["services"] if s["id"] == svc_row["id"])
    assert me["runnable"] is True and me["formation"] == "preapproval" and me["price"] == "6"
    assert me["reputation"]["completed"] == 0 and "signature" not in me["reputation"]

    # 统计字段齐全
    st = client.get("/api/admin/stats").json()
    assert STATS_KEYS <= set(st), STATS_KEYS - set(st)
    assert st["mode"] == "dev" and st["run"] == pf.bus.run_id and st["users"] >= 3

    # 回放：运行列表含当前 run，事件文件可读
    runs = client.get("/api/admin/runs").json()
    assert pf.bus.run_id in runs
    evs = client.get(f"/api/admin/replay/{pf.bus.run_id}").json()
    assert evs and all("seq" in e and "op" in e for e in evs)
    assert client.get("/api/admin/replay/no-such-run").status_code == 404
    r = client.get("/api/admin/events", params={"limit": 5})
    assert r.status_code == 200 and len(r.json()) <= 5

    # 检索带 mode
    r = client.post("/api/admin/search", json={"query": "朋友圈文案", "top_k": 50})
    assert r.status_code == 200
    assert r.json()["mode"] in ("vector", "a2x-tree")
    assert svc_row["id"] in [h["id"] for h in r.json()["hits"]]

    # 邀请码、订单与需求列表、管理员看订单详情权限
    assert client.post("/api/admin/invite").json()["code"]
    assert client.get("/api/admin/orders").status_code == 200
    assert client.get("/api/admin/needs").status_code == 200
    assert client.get("/api/orders/sha256:" + "0" * 64).status_code == 404
