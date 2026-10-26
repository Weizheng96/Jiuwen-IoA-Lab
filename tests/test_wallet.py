# -*- coding: utf-8 -*-
"""虚拟币账本：发放 / 扣减只追加记录；转账余量不足则两边都不变；余量按数值比较（不是字符串）。"""
from __future__ import annotations

from decimal import Decimal

import pytest

from a2c.errors import A2CError

from _support import new_user


def _log(pf, uid):
    return pf.db.rows("SELECT * FROM wallet_log WHERE user_id=? ORDER BY id", uid)


def test_adjust_appends_only(platform):
    pf = platform
    u = new_user(pf)["id"]
    assert pf.wallets.balance(u) == Decimal("0.00")
    assert pf.wallets.adjust(u, Decimal("100"), by="admin", note="发放") == Decimal("100.00")
    assert pf.wallets.adjust(u, Decimal("-30"), by="admin", note="扣减") == Decimal("70.00")
    log = _log(pf, u)
    assert [(r["delta"], r["balance"], r["kind"], r["by"]) for r in log] == [("100.00", "100.00", "grant", "admin"), ("-30.00", "70.00", "grant", "admin")]
    # 扣到负数：拒绝，且不留记录
    with pytest.raises(A2CError):
        pf.wallets.adjust(u, Decimal("-70.01"), by="admin", note="超扣")
    assert pf.wallets.balance(u) == Decimal("70.00")
    assert len(_log(pf, u)) == 2


def test_transfer_insufficient_leaves_both_unchanged(platform):
    pf = platform
    a, b = new_user(pf)["id"], new_user(pf)["id"]
    pf.wallets.adjust(a, Decimal("6"), by="admin", note="发放")
    pf.wallets.adjust(b, Decimal("1"), by="admin", note="发放")
    with pytest.raises(A2CError):
        pf.wallets.transfer(a, b, Decimal("6.01"), purpose="fee:test", ref="r1")
    assert (pf.wallets.balance(a), pf.wallets.balance(b)) == (Decimal("6.00"), Decimal("1.00"))
    assert pf.db.rows("SELECT * FROM transfers WHERE payer_id=?", a) == []
    assert len(_log(pf, a)) == 1 and len(_log(pf, b)) == 1
    with pytest.raises(A2CError):
        pf.wallets.transfer(a, b, Decimal("0"), purpose="fee:test", ref="r0")


def test_transfer_compares_numerically(platform):
    pf = platform
    a, b = new_user(pf)["id"], new_user(pf)["id"]
    pf.wallets.adjust(a, Decimal("100.00"), by="admin", note="发放")
    # 字符串比较会判 '100.00' < '6.00'；CAST 后按数值比较，应成功
    pf.wallets.transfer(a, b, Decimal("6.00"), purpose="ruling:test", ref="r2", payment_id="p-1")
    assert (pf.wallets.balance(a), pf.wallets.balance(b)) == (Decimal("94.00"), Decimal("6.00"))
    [tr] = pf.db.rows("SELECT * FROM transfers WHERE payer_id=?", a)
    assert (tr["payee_id"], tr["amount"], tr["purpose"], tr["payment_id"]) == (b, "6.00", "ruling:test", "p-1")
    # 反向：'6.00' 付 '100.00'，字符串比较会误判为够付；数值比较应拒绝
    with pytest.raises(A2CError):
        pf.wallets.transfer(b, a, Decimal("100.00"), purpose="refund:test", ref="r3")
    assert (pf.wallets.balance(a), pf.wallets.balance(b)) == (Decimal("94.00"), Decimal("6.00"))
    # 发放的 CAST：余量 '6.00' 扣 '-100.00' 应拒绝
    with pytest.raises(A2CError):
        pf.wallets.adjust(b, Decimal("-100.00"), by="admin", note="超扣")
    assert pf.wallets.balance(b) == Decimal("6.00")
