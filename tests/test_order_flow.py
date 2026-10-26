# -*- coding: utf-8 -*-
"""一单到底（固定交付件桩）：发现 → 模板 → 拟卡 → 签1 → 开工 → 交付存证 → 取件验收 → 付款 → 结清。

委托方 SDK 驱动定约与开工，orders 表由 platform._on_phase 维护；存证之后交给平台的轮询循环（取件 · 验收 · 付款 · 结清），
与真实运行同一条路。
"""
from __future__ import annotations

from decimal import Decimal

from _support import insert_need, new_assistant, new_service, sign_and_start, wait_until


def test_order_flow(platform):
    pf = platform
    owner_s, svc_row, provider, d = new_service(pf)
    owner_c, a, rt = new_assistant(pf)
    assert pf.wallets.balance(owner_c["id"]) == Decimal("100.00")
    assert pf.wallets.balance(owner_s["id"]) == Decimal("0.00")

    # 发现（向量退路）命中；信誉 0/0 带市场签名；模板由市场签认
    hits = pf._search("帮奶茶店写三条朋友圈文案", 50)
    assert provider in [h["card"]["url"] for h in hits]
    rep = pf.market.reputation(provider)
    assert rep["completed"] == 0 and rep["successRate"] is None and "signature" in rep
    [t] = rt.client.get_templates(provider)
    assert "market" in t["signatures"] and "script" in t["acceptance"]["posts"]["verifiers"]
    assert t["pricing"]["currency"] == "CNY"

    need = insert_need(pf, owner_c["id"], a["id"], "给小满美甲写三条朋友圈")
    cd, card, _t = sign_and_start(rt, provider, card_id="C-FLOW", spec={"posts": {"shop": "小满美甲", "theme": "春日上新"}}, market=pf.market_id)
    assert Decimal(str(card["derived"]["total"])) == Decimal("6")
    pf._on_phase(need["id"], "signed", {"cardDigest": cd, "provider": provider})
    o = pf.db.one("SELECT * FROM orders WHERE card_digest=?", cd)
    assert o and (o["state"], o["phase"], o["need_id"], o["provider_id"], o["formation"]) == ("forming", "已签1", need["id"], provider, "preapproval")
    assert Decimal(o["total"]) == Decimal("6")
    pf._on_phase(need["id"], "started", {"cardDigest": cd})
    assert pf.db.one("SELECT state FROM orders WHERE card_digest=?", cd)["state"] == "started"

    # 平台轮询：收到存证通知后取件 → 验收 → 付款 → 结清
    wait_until(lambda: pf.db.one("SELECT state FROM orders WHERE card_digest=?", cd)["state"] == "completed", what="订单结清")
    o = pf.db.one("SELECT * FROM orders WHERE card_digest=?", cd)
    assert o["phase"] == "已结清"
    assert pf.db.one("SELECT state, card_digest FROM needs WHERE id=?", need["id"]) == {"state": "completed", "card_digest": cd}

    # 钱包过账 = 卡上金额；凭证在委托方卡记录上
    assert pf.wallets.balance(owner_c["id"]) == Decimal("94.00")
    assert pf.wallets.balance(owner_s["id"]) == Decimal("6.00")
    rec = rt.client.store.get("cards", cd)
    sigs = rec["card"]["signatures"]
    assert sigs["1"] and all(len(sigs[k]) == 1 for k in ("4", "5", "6", "7"))
    [v] = rec["vouchers"]
    assert (v["purpose"], v["period"], Decimal(str(v["amount"]))) == ("installment", 1, Decimal("6"))
    assert pf.db.one("SELECT 1 FROM paid WHERE ref=? AND purpose='installment' AND period=1", cd)
    [tr] = pf.db.rows("SELECT * FROM transfers WHERE ref=?", cd)
    assert (tr["payer_id"], tr["payee_id"], tr["amount"]) == (owner_c["id"], owner_s["id"], "6.00")

    # 市场：ledger 完成、信誉 1/1
    assert pf.market.ledger()[cd]["outcome"] == "completed"
    rep = pf.market.reputation(provider)
    assert (rep["completed"], rep["successRate"]) == (1, 1.0)

    # 事件总线：这张卡的报文齐全，模板签认由该服务方发起
    evs = [e for e in pf.bus.recent(5000) if e.get("cardDigest") == cd]
    ops = {e["op"] for e in evs}
    for op in ("work.start", "delivery.anchor", "delivery.fetch", "acceptance.notify", "ap2.pay", "payment.notify", "card.close", "transfer"):
        assert op in ops, op
    assert any(e["op"] == "template.endorse" and e["sender"] == provider for e in pf.bus.recent(5000))
    pay = next(e for e in evs if e["op"] == "ap2.pay" and e["kind"] == "ap2")
    assert pay["sender"] == a["id"] and pay["receiver"] == pf.pay_id and Decimal(pay["amount"]) == Decimal("6")
    tr_ev = next(e for e in evs if e["op"] == "transfer")
    assert (tr_ev["sender"], tr_ev["receiver"], tr_ev["outcome"]) == (owner_c["id"], owner_s["id"], "installment")
    order_ops = [e["op"] for e in evs if e["kind"] == "platform"]
    for op in ("order.signed", "order.started", "order.fetched", "order.verified", "order.accepted", "order.completed"):
        assert op in order_ops, op
    # 管理员视角的订单详情：签名链布尔化、事件按序
    det = pf.order_detail(cd)
    assert det["signatures"]["1"] is True and det["signatures"]["7"] == [True] and det["ruling"] == {}
    assert [e["seq"] for e in det["events"]] == sorted(e["seq"] for e in det["events"])
