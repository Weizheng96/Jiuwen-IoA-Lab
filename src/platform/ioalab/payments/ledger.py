"""Virtual ledger with explicit, short application transactions."""
from __future__ import annotations
from decimal import Decimal
from typing import Any
import threading
import uuid
from datetime import datetime, timezone, timedelta
from a2c.errors import CARD_INVALID,fail
from ..storage import DB,now


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

    @staticmethod
    def reward_day(timestamp):
        return datetime.fromtimestamp(timestamp,timezone(timedelta(hours=8))).date().isoformat()

    def initial_credit_in(self,conn,user_id,amount):
        if conn is not self.db.conn or not conn.in_transaction:raise RuntimeError('Account grants need the owned transaction')
        value=money(amount)
        if not value.is_finite() or value<0:fail(CARD_INVALID,'Invalid account grant')
        if conn.execute("SELECT 1 FROM wallet_log WHERE user_id=? AND kind='initial' AND ref=?",(user_id,user_id)).fetchone():return '0.00'
        conn.execute("INSERT OR IGNORE INTO wallets(user_id,balance) VALUES(?,'0')",(user_id,))
        if value>0:self.adjust_in(conn,user_id,value,by='platform',note='账号初始发放',kind='initial',ref=user_id)
        return format(value,'.2f')

    def login_credit_in(self,conn,user_id,operation,policy):
        if conn is not self.db.conn or not conn.in_transaction:raise RuntimeError('Login grants need the owned transaction')
        amount=money(policy['login']); current=now(); day=self.reward_day(current)
        if not amount.is_finite() or amount<0:fail(CARD_INVALID,'Invalid login grant')
        old=conn.execute("SELECT delta FROM wallet_log WHERE user_id=? AND kind='login' AND ref=?",(user_id,operation)).fetchall()
        if old:return {'login':format(sum((money(row[0]) for row in old),Decimal('0')),'.2f'),'day':day,'reason':'restored'}
        if amount<=0:return {'login':'0.00','day':day,'reason':'disabled'}
        # A legacy grant without a verifiable day cannot justify a second grant.
        if conn.execute("SELECT 1 FROM wallet_log WHERE user_id=? AND kind='login' AND CAST(delta AS REAL)>0 AND strftime('%Y-%m-%d',at,'unixepoch','+8 hours') IS NULL",(user_id,)).fetchone():
            return {'login':'0.00','day':day,'reason':'history_unverified'}
        already=conn.execute('SELECT 1 FROM login_reward_days WHERE user_id=? AND day=?',(user_id,day)).fetchone()
        if already and not policy['repeat_daily']:return {'login':'0.00','day':day,'reason':'already_claimed'}
        conn.execute('INSERT OR IGNORE INTO login_reward_days VALUES(?,?,?,?)',(user_id,day,operation,current))
        self.adjust_in(conn,user_id,amount,by='platform',note='登录赠送',kind='login',ref=operation)
        return {'login':format(amount,'.2f'),'day':day,'reason':'granted'}

    def balance(self, user_id: str) -> Decimal:
        row = self.db.one("SELECT balance FROM wallets WHERE user_id=?", user_id)
        return money(row["balance"]) if row else Decimal("0.00")

    def bind_payer_in(self,conn,owner,thumbprint):
        if conn is not self.db.conn or not conn.in_transaction:
            raise RuntimeError('Payer registration requires the owned application transaction')
        conn.execute("INSERT OR IGNORE INTO wallets(user_id,balance) VALUES(?,'0')",(owner,))
        conn.execute('UPDATE wallets SET payer_thumbprint=? WHERE user_id=?',(thumbprint,owner))

    def _log(self, c, user_id: str, delta: Decimal, kind: str, ref: str, by: str, note: str) -> None:
        bal = c.execute("SELECT balance FROM wallets WHERE user_id=?", (user_id,)).fetchone()["balance"]
        c.execute("INSERT INTO wallet_log (at, user_id, delta, balance, kind, ref, by, note) VALUES (?,?,?,?,?,?,?,?)",
                  (now(), user_id, str(delta), bal, kind, ref, by, note))

    def adjust(self, user_id: str, delta: Decimal, *, by: str, note: str, kind: str = "grant") -> Decimal:
        """管理员发放 / 扣减（kind grant）或注册赠送（kind welcome）：只追加记录，不是交易。"""
        with self.lock, self.db.tx() as c:
            return self.adjust_in(c,user_id,delta,by=by,note=note,kind=kind)

    def adjust_in(self,c,user_id,delta,*,by,note,kind='grant',ref=''):
        if c is not self.db.conn or not c.in_transaction:
            raise RuntimeError('Ledger changes require the owned platform transaction')
        delta=money(delta)
        c.execute("INSERT OR IGNORE INTO wallets(user_id,balance) VALUES(?,'0')",(user_id,))
        changed=c.execute("UPDATE wallets SET balance=printf('%.2f',balance+?) WHERE user_id=? AND CAST(balance AS REAL)+CAST(? AS REAL)>=0",(str(delta),user_id,str(delta)))
        if changed.rowcount!=1:fail(CARD_INVALID,'余量不足')
        self._log(c,user_id,delta,kind,ref,by,note)
        return money(c.execute('SELECT balance FROM wallets WHERE user_id=?',(user_id,)).fetchone()[0])

    def transfer(self,payer_id,payee_id,amount,*,purpose,ref,payment_id='',by=''):
        with self.lock,self.db.tx() as conn:
            self.transfer_in(conn,payer_id,payee_id,amount,purpose=purpose,ref=ref,payment_id=payment_id,by=by)

    def transfer_in(self,c,payer_id,payee_id,amount,*,purpose,ref,payment_id='',by=''):
        """Use the application transaction; no nested transaction or extra wallet lock."""
        if c is not self.db.conn or not c.in_transaction:
            raise RuntimeError('Ledger changes require the owned platform transaction')
        amount = money(amount)
        if amount <= 0:
            fail(CARD_INVALID, "金额须为正")
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
