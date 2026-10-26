"""事件总线：每条 A2C / AP2 报文一条事件（发送侧包 Transport.send，接收侧包 Endpoint.finish），另记平台事件。

事件只是报文的影子，不改变任何协议状态。落 SQLite，环形缓冲供 WebSocket 订阅，每次运行另落 data/runs/<id>/events.jsonl。
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Mapping

from a2c.common import Endpoint, Reply, Transport

from .store import DB, jdump

# 报文 op → 中文标签（设计/委托流程.md 的说法）
LABELS = {
    "template.get": "取合同模板", "template.endorse": "市场签模板", "request.submit": "提交提案卡",
    "card.endorse": "市场签3", "card.countersign": "请求复签（签2）", "work.start": "开工请求",
    "step.authorize": "环节授权", "delivery.anchor": "交付留痕（签5）", "anchor.watch": "订阅存证",
    "delivery.fetch": "取件", "acceptance.notify": "验收通知", "payment.notify": "付款通知",
    "termination.quote": "解除报价", "termination.notify": "解除通知", "card.close": "提交结清",
    "provenance.endorse": "出处加签", "ap2.checkout": "AP2 结账", "ap2.pay": "AP2 付款（读卡过账）",
}


class EventBus:
    def __init__(self, db: DB, runs_dir: Path) -> None:
        self.db = db
        self.runs_dir = runs_dir
        self.run_id = time.strftime("%Y%m%d-%H%M%S")
        self.buffer: deque[dict] = deque(maxlen=2000)
        self.lock = threading.Lock()
        self.subscribers: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []
        self._file = None

    def new_run(self, run_id: str | None = None) -> str:
        self.run_id = run_id or time.strftime("%Y%m%d-%H%M%S")
        (self.runs_dir / self.run_id).mkdir(parents=True, exist_ok=True)
        if self._file:
            self._file.close()
        self._file = (self.runs_dir / self.run_id / "events.jsonl").open("a", encoding="utf-8")
        return self.run_id

    def emit(self, kind: str, op: str, *, sender: str = "", receiver: str = "", card_digest: str | None = None,
             period: int | None = None, amount: Any = None, outcome: str = "", detail: Mapping[str, Any] | None = None) -> dict:
        at = time.time()
        d = {k: v for k, v in dict(detail or {}).items() if v is not None}
        cur = self.db.run("INSERT INTO events (at, run_id, kind, op, sender, receiver, card_digest, period, amount, outcome, detail) "
                          "VALUES (?,?,?,?,?,?,?,?,?,?,?)", at, self.run_id, kind, op, sender, receiver, card_digest, period,
                          None if amount is None else str(amount), outcome, jdump(d))
        ev = {"seq": cur.lastrowid, "at": at, "run": self.run_id, "kind": kind, "op": op, "label": LABELS.get(op, op),
              "sender": sender, "receiver": receiver, "cardDigest": card_digest, "period": period,
              "amount": None if amount is None else str(amount), "outcome": outcome, "detail": d}
        with self.lock:
            self.buffer.append(ev)
            if self._file is None:
                self.new_run(self.run_id)
            self._file.write(json.dumps(ev, ensure_ascii=False) + "\n")
            self._file.flush()
            subs = list(self.subscribers)
        for loop, q in subs:
            loop.call_soon_threadsafe(q.put_nowait, ev)
        return ev

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        with self.lock:
            self.subscribers.append((asyncio.get_running_loop(), q))
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self.lock:
            self.subscribers = [(l, x) for l, x in self.subscribers if x is not q]

    def recent(self, n: int = 200) -> list[dict]:
        with self.lock:
            return list(self.buffer)[-n:]


def _digest_of(body: Mapping[str, Any]) -> str | None:
    if body.get("cardDigest"):
        return body["cardDigest"]
    card = body.get("card")
    if isinstance(card, Mapping):
        try:
            from a2c.card import card_digest
            return card_digest(card)
        except Exception:
            return None
    v = body.get("voucher")
    if isinstance(v, Mapping):
        return v.get("cardDigest")
    return None


class TracingTransport:
    """包一层 Transport：每次 send 记一条事件（发起方由实例确定），错误也记。"""

    def __init__(self, inner: Transport, bus: EventBus, party: str) -> None:
        self.inner = inner
        self.bus = bus
        self.party = party

    def send(self, to: str, op: str, body: Mapping[str, Any], files: Mapping[str, bytes] | None = None,
             task_id: str | None = None) -> Reply:
        cd = _digest_of(body)
        period = body.get("period")
        amount = None
        detail: dict[str, Any] = {}
        if op == "ap2.pay":
            detail["purpose"] = "ap2"
        try:
            reply = self.inner.send(to, op, body, files, task_id)
        except Exception as e:  # noqa: BLE001 — 记下错误再抛
            self.bus.emit("a2c" if not op.startswith("ap2.") else "ap2", op, sender=self.party, receiver=to,
                          card_digest=cd, period=period, outcome="error", detail={"error": str(e)[:300]})
            raise
        rb = reply.body if isinstance(reply.body, Mapping) else {}
        if "voucher" in rb and isinstance(rb["voucher"], Mapping):
            amount = rb["voucher"].get("amount")
            detail["purpose"] = rb["voucher"].get("purpose")
            cd = cd or rb["voucher"].get("cardDigest")
            period = period or rb["voucher"].get("period")
        if op == "card.countersign" or op == "request.submit":
            detail["task"] = reply.task_id
        outcome = reply.state or ("ok" if "error" not in rb else "error")
        self.bus.emit("ap2" if op.startswith("ap2.") else "a2c", op, sender=self.party, receiver=to, card_digest=cd,
                      period=period, amount=amount, outcome=outcome, detail=detail)
        return reply

    def get_task(self, to: str, task_id: str):
        return self.inner.get_task(to, task_id)

    def __getattr__(self, item: str) -> Any:            # jwks()、close() 等透传
        return getattr(self.inner, item)


def trace_endpoint(endpoint: Endpoint, bus: EventBus, party: str) -> None:
    """包接收侧：任务的延后部分完成或失败时记事件（复签结果、开工回执、拒收）。"""
    orig_finish = endpoint.finish

    def finish(reply: Reply) -> Reply:
        op = reply.body.get("op", "")
        try:
            out = orig_finish(reply)
        except Exception as e:  # noqa: BLE001
            bus.emit("a2c", op + ".result", sender=party, receiver="", card_digest=None, outcome="failed",
                     detail={"error": str(e)[:300]})
            raise
        body = out.body if isinstance(out.body, Mapping) else {}
        outcome = out.state or "completed"
        cd = _digest_of(body) or _digest_of(reply.body)
        d: dict[str, Any] = {}
        for k in ("reason", "rejection", "receipt"):
            if k in body:
                d[k] = body[k].get("reason") if isinstance(body[k], Mapping) and "reason" in body[k] else (k if k != "reason" else body[k])
        bus.emit("a2c", op + ".result", sender=party, receiver="", card_digest=cd, outcome=outcome, detail=d)
        return out

    endpoint.finish = finish  # type: ignore[method-assign]


__all__ = ["EventBus", "TracingTransport", "trace_endpoint", "LABELS"]
