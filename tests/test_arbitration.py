# -*- coding: utf-8 -*-
"""仲裁：一份必败的卡（spec.shop 与桩交付件不符）→ 取件 → 验收不通过 → 签不通过报告 → 市场复算裁定。

裁定到文书为止：rulings 表与市场 ledger 记 arbitrated，信誉按裁定计；不划转。
"""
from __future__ import annotations

from decimal import Decimal

from a2c.card import card_digest

from _support import new_assistant, new_service, sign_and_start, wait_anchored


def test_arbitration_recompute(platform):
    pf = platform
    owner_s, svc_row, provider, d = new_service(pf)
    owner_c, a, rt = new_assistant(pf)
    need = {"id": "need-arb-" + svc_row["id"][-6:], "scope": {"sign1": "client", "sign7": "client"}, "auto_sign": 1, "materials": []}

    # 桩交付件写的是「小满美甲」，卡上店名另写一个 → 验收器「含店名」必败
    cd, card, t = sign_and_start(rt, provider, card_id="C-ARB", spec={"posts": {"shop": "别家美甲", "theme": "周末"}}, market=pf.market_id)
    wait_anchored(rt, cd)
    out = rt.handle_anchored(need, cd, 1)
    assert out["status"] == "fail" and out["arbitrated"] is True
    ruling = out["ruling"]
    assert (ruling["kind"], ruling["rulingKind"], ruling["liable"], ruling["period"], ruling["ground"]) == \
        ("ruling", "recompute", "provider", 1, "acceptance-failed")
    assert ruling["cardDigest"] == cd and ruling["applicant"] == rt.client.signer.id
    assert ruling["evidence"]["recompute"] == "fail" and ruling["evidence"]["runs"][0]["verifier"] == d["verifier"]["id"]
    assert ruling["signature"]                                    # 市场签发的文书

    # 签6 已签收；不通过的验收报告（委托方签名）在卡上，签7 不签；无付款、无凭证
    rec = rt.client.store.get("cards", cd)
    sigs = rec["card"]["signatures"]
    assert len(sigs["6"]) == 1 and len(sigs["7"]) == 0 and not rec.get("vouchers")
    report = rec["card"]["fulfillment"]["periods"][0]["report"]
    assert [r["status"] for r in report["results"]] == ["fail"] and report["signature"] and report["cardDigest"] == cd
    assert pf.db.one("SELECT 1 FROM paid WHERE ref=?", cd) is None
    assert pf.wallets.balance(owner_c["id"]) == Decimal("100.00") and pf.wallets.balance(owner_s["id"]) == Decimal("0.00")

    # 裁定留痕：rulings 表 · 市场 ledger · 信誉
    row = pf.db.one("SELECT * FROM rulings WHERE card_digest=? AND period=1", cd)
    assert row is not None
    assert pf.market.ledger()[cd]["outcome"] == "arbitrated"
    assert pf.market.market.store.get("rulings", cd)["liable"] == "provider"
    rep = pf.market.reputation(provider)
    assert (rep["completed"], rep["successRate"]) == (1, 0.0)
    ops = [e["op"] for e in pf.bus.recent(5000) if e.get("cardDigest") == cd]
    assert "arbitration.ruling" in ops and "order.arbitrated" in ops and "ap2.pay" not in ops
    # 裁定之后不能再结清
    import pytest
    with pytest.raises(Exception):
        pf.services[svc_row["id"]].provider.close(cd)

    # 交付件与签4 清单摘要不符时驳回（不出裁定）
    with pytest.raises(Exception, match="不符"):
        pf.market.arbitrate(card=rec["card"], period=1, files={"posts.json": b"{}"}, report=None, applicant=rt.client.signer.id,
                            ground="acceptance-failed")
    assert card_digest(rec["card"]) == cd
