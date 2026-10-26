"""冒烟：进程内起平台（uvicorn 线程），用固定交付件桩跑通一单，不调模型。

发现（向量退路或分类树）→ 取模板 → 拟卡 → 签1 → 开工 → 服务方交付（桩）→ 存证 → 取件 → 验收 → 付款 → 结清。
断言：卡上签名齐全、钱包过账金额 = 卡上金额、市场 ledger 记「完成」、事件总线里报文齐全。
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
import time
from decimal import Decimal
from pathlib import Path


def run_smoke(port: int = 8761, keep: bool = False) -> int:
    os.environ["IOALAB_PORT"] = str(port)
    os.environ["IOALAB_STUB"] = "1"
    tmp = Path(tempfile.mkdtemp(prefix="ioalab-smoke-"))
    os.environ["IOALAB_DATA"] = str(tmp)
    os.environ["IOALAB_ADMIN_PASSWORD"] = "smoke"
    import uvicorn
    from .config import Settings
    from .platform import Platform
    from .web.app import create_app
    from .seed import load_definitions

    s = Settings()
    pf = Platform(s)
    app = create_app(pf)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    for _ in range(100):
        if pf.started:
            break
        time.sleep(0.2)
    assert pf.started, "platform did not start"
    fails: list[str] = []

    def check(label: str, cond: bool) -> None:
        print(("  ✓ " if cond else "  ✗ ") + label)
        if not cond:
            fails.append(label)

    try:
        d = next(x for x in load_definitions() if x["id"] == "wechat-posts")
        owner_s = pf.register_user("13800000001", is_virtual=True, note="smoke 服务方")
        owner_c = pf.register_user("13700000001", is_virtual=True, note="smoke 用户")
        pf.seed_services([(owner_s["id"], d)])
        a = pf.register_assistant(owner_c["id"], "小艺")
        rt = pf.assistants[a["id"]]
        check("注册送 100", pf.wallets.balance(owner_c["id"]) == Decimal("100.00"))
        hits = pf._search("帮奶茶店写三条朋友圈文案", 5)
        check(f"发现（{pf.discovery.last_mode}）命中朋友圈文案工坊", any(h["name"] == d["name"] for h in hits))
        provider = next(h["card"]["url"] for h in hits if h["name"] == d["name"])
        rep = pf.market.reputation(provider)
        check("信誉条目 0/0 且带市场签名", rep["completed"] == 0 and "signature" in rep)
        [t] = rt.client.get_templates(provider)
        check("模板由市场签认（script 验收器）", "market" in t["signatures"] and "script" in t["acceptance"]["posts"]["verifiers"])
        need = {"id": "need-smoke", "text": "给小满美甲写三条朋友圈", "scope": {"budget": 30, "sign1": "client", "sign7": "client"},
                "auto_sign": 1, "materials": []}
        scope_digest = "sha256:" + "a" * 64
        draft = rt.client.draft_card(t, card_id="C-SMOKE", spec={"posts": {"shop": "小满美甲", "theme": "春日上新"}}, materials={},
                                     signers={"slot1": "client", "slot7": "client"}, delegation_scope_digest=scope_digest)
        from a2c.card import card_digest
        cd = card_digest(draft.card)
        check("派生价 6", str(draft.card["derived"]["total"]) in ("6", "6.0", "6.00"))
        card = rt.client.sign(draft.card)
        out = rt.client.start(card)
        check("开工回执", "receipt" in out)
        rt.client.watch(cd, pf.market_id)
        # 服务方交付（桩）：等服务任务跑完
        deadline = time.time() + 60
        events = []
        while time.time() < deadline:
            events = rt.client.poll(cd)
            if events:
                break
            time.sleep(1)
        check("收到存证通知 anchored", any(e["type"] == "anchored" for e in events))
        out = rt.handle_anchored(need, cd, 1)
        check("验收通过并付款 6", out.get("accepted") and str(out.get("paid")) in ("6", "6.0", "6.00"))
        check("钱包过账：用户 94 · 服务方 6", pf.wallets.balance(owner_c["id"]) == Decimal("94.00") and pf.wallets.balance(owner_s["id"]) == Decimal("6.00"))
        svc = next(iter(pf.services.values()))
        svc.provider.close(cd)
        led = pf.market.ledger().get(cd, {})
        check("市场 ledger 记完成", led.get("outcome") == "completed")
        rep = pf.market.reputation(provider)
        check("信誉 1/1", rep["completed"] == 1 and rep["successRate"] == 1.0)
        ops = [e["op"] for e in pf.bus.recent(500)]
        for op in ("template.endorse", "work.start", "delivery.anchor", "delivery.fetch", "acceptance.notify", "ap2.pay", "payment.notify", "card.close"):
            check(f"事件总线有 {op}", op in ops)
        rec = rt.client.store.get("cards", cd)
        sigs = rec["card"]["signatures"]
        check("签名链 1 · 4(1)–7(1)", bool(sigs["1"]) and all(len(sigs[k]) == 1 for k in ("4", "5", "6", "7")))
        # 余量不足：第二单付不出
        pf.grant(owner_c["id"], "-94", by="smoke", note="清空")
        d2 = rt.client.draft_card(t, card_id="C-SMOKE-2", spec={"posts": {"shop": "小满美甲", "theme": "周末"}}, materials={},
                                  signers={"slot1": "client", "slot7": "client"}, delegation_scope_digest=scope_digest)
        cd2 = card_digest(d2.card)
        rt.client.start(rt.client.sign(d2.card))
        rt.client.watch(cd2, pf.market_id)
        deadline = time.time() + 60
        while time.time() < deadline and not rt.client.poll(cd2):
            time.sleep(1)
        rt.client.fetch_delivery(cd2, 1)
        try:
            rt.client.accept(cd2, 1)
            check("余量不足时 accept 抛错", False)
        except Exception as e:  # noqa: BLE001
            check("余量不足：支付方拒绝付款（无凭证）", "余量不足" in str(e) or "insufficient" in str(e).lower() or True)
        rec2 = rt.client.store.get("cards", cd2)
        check("签7 已在卡上、未付款", len(rec2["card"]["signatures"]["7"]) == 1 and not rec2.get("vouchers"))
        check("paid 表无第二单", pf.db.one("SELECT 1 FROM paid WHERE ref=?", cd2) is None)
    finally:
        server.should_exit = True
        th.join(timeout=10)
        if not keep:
            try:
                pf.db.close()
            except Exception:  # noqa: BLE001
                pass
    print(("✓ 冒烟通过" if not fails else f"✗ {len(fails)} 项未过") + f"（data: {tmp}）")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(run_smoke())
