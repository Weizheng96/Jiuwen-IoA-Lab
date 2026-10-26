"""模拟支付方（含虚拟币账本）——持牌支付方的替身，协议外。

只在 ``ap2.pay`` 读卡核过用途与金额后过账：先核付款方钱包与收款方账号、核余量，再调参考实现签凭证，
最后在同一 SQLite 事务里扣付款方、加收款方。不按裁定划转、不代任何一方发起划转。
「普通转账」（裁定后付款、退差额、市场服务费）由责任方自己发起，不读卡。
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from decimal import Decimal
from typing import Any, Callable, Mapping

from a2c.common import Clock, Directory, Reply
from a2c.errors import BAD_SIGNATURE, CARD_INVALID, OUT_OF_ORDER, fail
from a2c.jws import Signer
from a2c.payment.ap2 import EcKey, jwt_decode
from a2c.payment.psp import PaymentProvider

from ..store import DB, jdump, now


def thumbprint(jwk: Mapping[str, Any]) -> str:
    """RFC 7638 JWK 指纹（EC：crv · kty · x · y）。"""
    core = {k: jwk[k] for k in ("crv", "kty", "x", "y")}
    return hashlib.sha256(json.dumps(core, separators=(",", ":"), sort_keys=True).encode()).hexdigest()


def money(x: Any) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.01"))


class Wallets:
    """虚拟币账本：每个账号一个钱包；只有过账、发放 / 扣减、普通转账三种改法，全部留痕。"""

    def __init__(self, db: DB) -> None:
        self.db = db
        self.lock = threading.RLock()

    def ensure(self, user_id: str, payer_thumbprint: str | None = None) -> None:
        with self.db.tx() as c:
            c.execute("INSERT OR IGNORE INTO wallets (user_id, balance) VALUES (?, '0')", (user_id,))
            if payer_thumbprint:
                c.execute("UPDATE wallets SET payer_thumbprint=? WHERE user_id=?", (payer_thumbprint, user_id))

    def balance(self, user_id: str) -> Decimal:
        row = self.db.one("SELECT balance FROM wallets WHERE user_id=?", user_id)
        return money(row["balance"]) if row else Decimal("0.00")

    def by_thumbprint(self, tp: str) -> str | None:
        row = self.db.one("SELECT user_id FROM wallets WHERE payer_thumbprint=?", tp)
        return row["user_id"] if row else None

    def _log(self, c, user_id: str, delta: Decimal, kind: str, ref: str, by: str, note: str) -> None:
        bal = c.execute("SELECT balance FROM wallets WHERE user_id=?", (user_id,)).fetchone()["balance"]
        c.execute("INSERT INTO wallet_log (at, user_id, delta, balance, kind, ref, by, note) VALUES (?,?,?,?,?,?,?,?)",
                  (now(), user_id, str(delta), bal, kind, ref, by, note))

    def adjust(self, user_id: str, delta: Decimal, *, by: str, note: str, kind: str = "grant") -> Decimal:
        """管理员发放 / 扣减（kind grant）或注册赠送（kind welcome）：只追加记录，不是交易。"""
        delta = money(delta)
        with self.lock, self.db.tx() as c:
            c.execute("INSERT OR IGNORE INTO wallets (user_id, balance) VALUES (?, '0')", (user_id,))
            cur = c.execute("UPDATE wallets SET balance = printf('%.2f', balance + ?) WHERE user_id=? AND CAST(balance AS REAL) + CAST(? AS REAL) >= 0",
                            (str(delta), user_id, str(delta)))
            if cur.rowcount != 1:
                fail(CARD_INVALID, "余量不足")
            self._log(c, user_id, delta, kind, "", by, note)
        return self.balance(user_id)

    def transfer(self, payer_id: str, payee_id: str, amount: Decimal, *, purpose: str, ref: str,
                 payment_id: str = "", by: str = "") -> None:
        """同一事务：扣付款方（余量不足即失败）、加收款方、记 transfers。"""
        amount = money(amount)
        if amount <= 0:
            fail(CARD_INVALID, "金额须为正")
        with self.lock, self.db.tx() as c:
            c.execute("INSERT OR IGNORE INTO wallets (user_id, balance) VALUES (?, '0')", (payee_id,))
            cur = c.execute("UPDATE wallets SET balance = printf('%.2f', balance - ?) WHERE user_id=? AND CAST(balance AS REAL) >= CAST(? AS REAL)",
                            (str(amount), payer_id, str(amount)))
            if cur.rowcount != 1:
                fail(CARD_INVALID, "余量不足")
            c.execute("UPDATE wallets SET balance = printf('%.2f', balance + ?) WHERE user_id=?", (str(amount), payee_id))
            self._log(c, payer_id, -amount, purpose, ref, by or payer_id, "")
            self._log(c, payee_id, amount, purpose, ref, by or payer_id, "")
            c.execute("INSERT INTO transfers (at, payer_id, payee_id, amount, purpose, ref, payment_id) VALUES (?,?,?,?,?,?,?)",
                      (now(), payer_id, payee_id, str(amount), purpose, ref, payment_id or str(uuid.uuid4())))

    def log(self, user_id: str, limit: int = 50) -> list[dict]:
        return self.db.rows("SELECT * FROM wallet_log WHERE user_id=? ORDER BY id DESC LIMIT ?", user_id, limit)


class _PersistentPaid:
    """``PaymentProvider.paid`` 的持久化替身：重启后同一期不能再过账。"""

    def __init__(self, db: DB) -> None:
        self.db = db

    def __contains__(self, key: tuple) -> bool:
        ref, purpose, period = key
        return self.db.one("SELECT 1 FROM paid WHERE ref=? AND purpose=? AND period IS ?", ref, purpose, period) is not None

    def add(self, key: tuple) -> None:
        ref, purpose, period = key
        self.db.run("INSERT OR IGNORE INTO paid (ref, purpose, period, at) VALUES (?,?,?,?)", ref, purpose, period, now())


class SimPaymentProvider(PaymentProvider):
    """读卡过账的支付方替身。``payee_of``：模板 provider → 账号；``payer_thumbprint``：mandate 头 jwk → 账号。"""

    def __init__(self, signer: Signer, *, keys: Directory, ap2_key: EcKey, payee_keys: Callable[[str], Mapping],
                 wallets: Wallets, payee_of: Callable[[str], str | None], db: DB, clock: Clock | None = None,
                 on_transfer: Callable[[dict], None] | None = None) -> None:
        super().__init__(signer, keys=keys, ap2_key=ap2_key, payee_keys=payee_keys, clock=clock)
        self.paid = _PersistentPaid(db)          # type: ignore[assignment]
        self.wallets = wallets
        self.payee_of = payee_of
        self.on_transfer = on_transfer
        self._pay_lock = threading.Lock()

    def op_ap2_pay(self, body, files, task_id) -> Reply:
        pm_head, _ = jwt_decode(body["paymentMandate"].rstrip("~"))
        payer_jwk = pm_head.get("jwk") or fail(BAD_SIGNATURE, "payment mandate carries no payer key")
        payer_id = self.wallets.by_thumbprint(thumbprint(payer_jwk)) or fail(BAD_SIGNATURE, "付款方密钥未登记")
        _h, claims = jwt_decode(jwt_decode(body["checkoutMandate"].rstrip("~"))[1]["checkout_jwt"])
        a2c = claims["a2c"]
        purpose, period = a2c["purpose"], a2c.get("period")
        template = body["template"]
        payee_id = self.payee_of(template["provider"]) or fail(CARD_INVALID, "收款方未登记")
        with self._pay_lock:
            _t, ref, amount = self._amount(purpose, body, period)      # 读卡核用途与金额，不改状态
            if (ref, purpose, period) in self.paid:
                fail(OUT_OF_ORDER, "already paid")
            if self.wallets.balance(payer_id) < money(amount):
                fail(CARD_INVALID, "余量不足：支付方拒绝付款")           # 不进 paid、不签凭证
            reply = super().op_ap2_pay(body, files, task_id)
            payment_id = str(uuid.uuid4())
            self.wallets.transfer(payer_id, payee_id, money(amount), purpose=purpose, ref=ref, payment_id=payment_id)
        if self.on_transfer:
            self.on_transfer({"payer": payer_id, "payee": payee_id, "amount": str(money(amount)), "purpose": purpose,
                              "ref": ref, "period": period, "paymentId": payment_id})
        return reply


__all__ = ["SimPaymentProvider", "Wallets", "thumbprint", "money"]
