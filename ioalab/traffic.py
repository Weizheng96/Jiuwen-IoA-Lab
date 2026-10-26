"""背景流量：按档位从需求池给空闲的内部用户（isc）助手投需求，让试用时的全景与交易流不空。

档位 IOALAB_TRAFFIC=off｜low｜normal（管理员页可切）；连续失败自动降档；助手钱包低于阈值时补币（wallet_log.kind=grant，by=traffic）。
只投给平台驾驭、当前没有进行中需求的 isc 助手；需求出自 data/dispatch.json（seed_all 写）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time

log = logging.getLogger("ioalab.traffic")
LEVELS = {"off": 0.0, "low": 180.0, "normal": 60.0}       # 两次投放的间隔（秒）
FLOOR = 40.0                                              # 余量低于它就补到 login_credit


class Traffic:
    def __init__(self, platform) -> None:
        self.pf = platform
        self.level = platform.s.traffic if platform.s.traffic in LEVELS else "off"
        self.task: asyncio.Task | None = None
        self.rng = random.Random(20261026)
        self.stats = {"dispatched": 0, "failed": 0, "downshifts": 0, "last": None}
        self._recent: list[str] = []

    # ── 控制 ─────────────────────────────────────────────────────────
    def set_level(self, level: str) -> dict:
        if level not in LEVELS:
            from a2c.errors import CARD_INVALID, fail
            fail(CARD_INVALID, "档位只能是 off / low / normal")
        self.level = level
        self.pf.bus.emit("platform", "traffic.level", sender="admin", outcome=level)
        self.ensure_running()
        return self.status()

    def status(self) -> dict:
        return {"level": self.level, "interval_s": LEVELS[self.level], **self.stats,
                "idle_assistants": len(self.idle_assistants()), "pool": len(self.pool())}

    def ensure_running(self) -> None:
        loop = self.pf.loop
        if loop is None or not loop.is_running():
            return
        if self.level != "off" and (self.task is None or self.task.done()):
            self.task = loop.create_task(self._loop())

    async def _loop(self) -> None:
        while self.level != "off":
            await asyncio.sleep(LEVELS[self.level])
            if self.level == "off":
                break
            try:
                await asyncio.to_thread(self.tick)
            except Exception as e:  # noqa: BLE001
                log.warning("traffic tick failed: %s", e)

    # ── 一次投放 ─────────────────────────────────────────────────────
    def pool(self) -> list[dict]:
        p = self.pf.s.data / "dispatch.json"
        try:
            return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
        except ValueError:
            return []

    def idle_assistants(self) -> list[dict]:
        return self.pf.db.rows("SELECT a.id, a.owner_id FROM agents a JOIN users u ON u.id=a.owner_id WHERE a.role='assistant' AND u.kind='isc' "
                               "AND NOT EXISTS (SELECT 1 FROM needs n WHERE n.assistant_id=a.id AND n.state IN ('queued','running','started','open'))")

    def tick(self, *, by: str = "traffic") -> dict | None:
        """挑一个空闲 isc 助手与一条需求投出去；返回需求记录或 None（没人空闲 / 池空）。"""
        idle, pool = self.idle_assistants(), self.pool()
        if not idle or not pool:
            return None
        a = self.rng.choice(idle)
        n = self.rng.choice(pool)
        budget = float((n.get("scope") or {}).get("budget", 30) or 30)
        if float(self.pf.wallets.balance(a["owner_id"])) < max(FLOOR, budget * 2):
            self.pf.grant(a["owner_id"], self.pf.s.login_credit, by=by, note="背景流量补币", kind="grant")
        need = self.pf.create_need(a["owner_id"], a["id"], n["text"], n.get("scope") or {}, auto_sign=bool(n.get("autoSign", True)),
                                   materials=n.get("materials") or [], by=by)
        self.stats["dispatched"] += 1
        self.stats["last"] = time.time()
        return need

    def observe(self, need_id: str, state: str) -> None:
        """需求收尾时由平台回调：连续 3 条未成就降一档。"""
        if state not in ("failed",) and state not in ("completed", "arbitrated", "started", "lapsed"):
            return
        self._recent.append("bad" if state == "failed" else "ok")
        self._recent = self._recent[-3:]
        if state == "failed":
            self.stats["failed"] += 1
        if self.level != "off" and len(self._recent) == 3 and all(x == "bad" for x in self._recent):
            self.level = "low" if self.level == "normal" else "off"
            self.stats["downshifts"] += 1
            self._recent.clear()
            self.pf.bus.emit("platform", "traffic.level", sender="traffic", outcome=self.level, detail={"reason": "连续 3 条需求未成，自动降档"})


__all__ = ["Traffic", "LEVELS"]
