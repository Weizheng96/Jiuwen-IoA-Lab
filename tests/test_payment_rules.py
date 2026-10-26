# -*- coding: utf-8 -*-
"""支付规则：余量不足时支付方拒绝付款（无凭证、不进 paid），签7 仍在卡上；充值后补付第 i 期款，钱包过账。"""
from __future__ import annotations

from decimal import Decimal

import pytest

from _support import new_assistant, new_service, sign_and_start, wait_anchored


def test_insufficient_then_pay_installment(platform):
    pf = platform
    owner_s, svc_row, provider, d = new_service(pf)
    owner_c, a, rt = new_assistant(pf)
    pf.grant(owner_c["id"], "-100", by="test", note="清空")
    assert pf.wallets.balance(owner_c["id"]) == Decimal("0.00")

    cd, card, t = sign_and_start(rt, provider, card_id="C-PAY", spec={"posts": {"shop": "小满美甲", "theme": "周末"}}, market=pf.market_id)
    wait_anchored(rt, cd)
    files = rt.client.fetch_delivery(cd, 1)
    assert set(files) == {"posts.json"}
    assert rt.client.verify(cd, 1).status == "pass"

    with pytest.raises(Exception) as ei:
        rt.client.accept(cd, 1)
    assert "余量不足" in str(ei.value)
    rec = rt.client.store.get("cards", cd)
    assert len(rec["card"]["signatures"]["7"]) == 1 and not rec.get("vouchers")        # 签7 已在卡上、无凭证
    assert pf.db.one("SELECT 1 FROM paid WHERE ref=?", cd) is None                     # 不进 paid
    assert pf.db.rows("SELECT * FROM transfers WHERE ref=?", cd) == []
    assert pf.wallets.balance(owner_c["id"]) == Decimal("0.00") and pf.wallets.balance(owner_s["id"]) == Decimal("0.00")
    err = [e for e in pf.bus.recent(5000) if e.get("cardDigest") == cd and e["op"] == "ap2.pay" and e["outcome"] == "error"]
    assert err and "余量不足" in err[-1]["detail"]["error"]
    # 再付一次仍拒绝（余量没变）
    with pytest.raises(Exception):
        pf.pay_installment(a["id"], cd, 1)
    # 没验收的期数不能付
    with pytest.raises(Exception, match="尚未验收"):
        pf.pay_installment(a["id"], cd, 2)

    # 充值后补付：AP2 付款 + payment.notify，钱包过账
    pf.grant(owner_c["id"], "100", by="test", note="充值")
    v = pf.pay_installment(a["id"], cd, 1)
    assert (v["purpose"], v["period"], Decimal(str(v["amount"]))) == ("installment", 1, Decimal("6"))
    assert pf.wallets.balance(owner_c["id"]) == Decimal("94.00") and pf.wallets.balance(owner_s["id"]) == Decimal("6.00")
    assert pf.db.one("SELECT 1 FROM paid WHERE ref=? AND purpose='installment' AND period=1", cd)
    rec = rt.client.store.get("cards", cd)
    assert len(rec["vouchers"]) == 1 and rec["vouchers"][0]["period"] == 1
    ops = [e["op"] for e in pf.bus.recent(5000) if e.get("cardDigest") == cd]
    assert "payment.notify" in ops and "order.paid-late" in ops
    # 同一期不能付第二次
    with pytest.raises(Exception, match="already paid"):
        pf.pay_installment(a["id"], cd, 1)
    assert pf.wallets.balance(owner_c["id"]) == Decimal("94.00")
    # 服务方收齐末期款后结清
    pf.services[svc_row["id"]].provider.close(cd)
    assert pf.market.ledger()[cd]["outcome"] == "completed"
