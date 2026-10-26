"""平台：把五个组件装配起来，单进程 · 单一通路（所有参与方都是 <PUBLIC_URL>/a2a/<name>；进程内互相调用改走回环 HTTP）。

  市场 /a2a/market · 模拟支付方 /a2a/pay · 每个服务方 /a2a/providers/<agent_id>（只放行本机）
  助手不是端点，只发请求。每个参与方各有自己的 A2ATransport（私有事件循环）并包 TracingTransport。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx
from pathlib import Path
from typing import Any, Mapping

from starlette.responses import JSONResponse

from a2c.binding import A2C_EXTENSION, AP2_EXTENSION, A2ATransport, build_app
from a2c.card import card_digest
from a2c.client import Client
from a2c.codec import utcnow
from a2c.common import Directory
from a2c.errors import A2CError, CARD_INVALID, fail
from a2c.payment.ap2 import AP2Payment
from a2c.provider import Provider

from . import PKG
from .config import Settings
from .events import EventBus, TracingTransport, trace_endpoint
from .keys import KeyStore
from .market import Discovery, MarketService
from .psp import SimPaymentProvider, Wallets, money, thumbprint
from .psp.auth import Auth
from .runtime.assistant import AssistantRuntime, Hooks
from .runtime.llm import LLM
from .runtime.packages import PackageStore, only_script_verifiers
from .runtime.service import ServiceRuntime
from .store import DB, SqliteStore, jdump, jload, now

log = logging.getLogger("ioalab.platform")
_SLUG = re.compile(r"[^a-z0-9-]")
# 第一轮里这些字样的异常视为网络抖动（模型端点连不上 / 超时），退避后重跑；其余失败不重跑
_TRANSIENT = ("Connection error", "APIConnectionError", "APITimeoutError", "ConnectError", "ReadTimeout", "timed out")


class LoopbackTransport(httpx.AsyncHTTPTransport):
    """发往本平台 PUBLIC_URL 的请求改走回环地址（Host 头保留对外地址）：部署在 Nginx 后面时进程内互调不绕圈、不依赖域名解析。"""

    def __init__(self, public: str, local: str) -> None:
        super().__init__()
        self.public, self.local = httpx.URL(public), httpx.URL(local)

    @staticmethod
    def _port(u: httpx.URL) -> int:
        return u.port or (443 if u.scheme == "https" else 80)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        u = request.url
        if self.public != self.local and u.scheme == self.public.scheme and u.host == self.public.host and self._port(u) == self._port(self.public):
            request.url = u.copy_with(scheme=self.local.scheme, host=self.local.host, port=self._port(self.local))
        return await super().handle_async_request(request)


class A2ARouter:
    """ASGI：/a2a/<name>/… 分发到按名字登记的 A2A 应用（服务方可运行期增加）。"""

    def __init__(self) -> None:
        self.apps: dict[str, Any] = {}
        self.local_only: set[str] = set()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await send({"type": "http.response.start", "status": 404, "headers": []})
            await send({"type": "http.response.body", "body": b""})
            return
        from starlette.routing import get_route_path
        path = get_route_path(scope)                 # 相对本挂载点（/a2a）的路径；scope["path"] 保持完整
        parts = path.strip("/").split("/", 2) if path.strip("/") else []
        # /providers/<id>/... 或 /market/... 或 /pay/...
        if parts and parts[0] == "providers" and len(parts) >= 2:
            name = "providers/" + parts[1]
        elif parts:
            name = parts[0]
        else:
            name = ""
        app = self.apps.get(name)
        if app is None:
            resp = JSONResponse({"error": "no such A2A endpoint"}, status_code=404)
            await resp(scope, receive, send)
            return
        if name in self.local_only:
            host = (scope.get("client") or ("", 0))[0]
            if host not in ("127.0.0.1", "::1", "localhost"):
                resp = JSONResponse({"error": "providers are reachable only from the platform host"}, status_code=403)
                await resp(scope, receive, send)
                return
        sub = dict(scope)
        sub["root_path"] = scope.get("root_path", "") + "/" + name    # 子应用按 root_path 之后的部分匹配路由
        await app(sub, receive, send)


class Platform:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        settings.dirs()
        self._pin_public_url()
        settings.write_a2x_key()
        self.db = DB(settings.data / "ioalab.sqlite")
        self.bus = EventBus(self.db, settings.data / "runs")
        self.wallets = Wallets(self.db)
        self.auth = Auth(self.db, settings)
        self.packages = PackageStore(settings.data / "packages")
        self.llm = LLM(settings)
        self.keys = Directory()
        self.keystore = KeyStore(settings.data / "keys")
        self.router = A2ARouter()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.sandbox_gate = threading.Semaphore(settings.sandbox_concurrency)
        self.services: dict[str, ServiceRuntime] = {}
        self.assistants: dict[str, AssistantRuntime] = {}
        self.need_tasks: dict[str, asyncio.Task] = {}
        self.need_gate: asyncio.Semaphore | None = None
        self.started = False

        base = settings.base_url
        self.market_id = f"{base}/a2a/market"
        self.pay_id = f"{base}/a2a/pay"
        self.payment_provider = {"id": self.pay_id, "jwksUri": self.pay_id + "/.well-known/jwks.json"}

        self.market_signer = self.keystore.ed25519(self.market_id, name="market", kid="m1")
        self.pay_signer = self.keystore.ed25519(self.pay_id, name="pay", kid="pay1")
        self.pay_ec = self.keystore.ec("pay")
        self.keys.add_signer(self.market_signer)
        self.keys.add_signer(self.pay_signer)

        self.discovery = Discovery(settings.data / "a2x", taxonomy_seed=PKG / "seed" / "taxonomy", a2x_home=settings.a2x_home,
                                   llm_configured=settings.a2x_configured(), embed_fn=self.llm.embed if self.llm.enabled else None)
        self.market = MarketService(self.market_signer, keys=self.keys, fetch=self.packages.fetch,
                                    store=SqliteStore(self.db, "market"), db=self.db, discovery=self.discovery)
        trace_endpoint(self.market.market, self.bus, self.market_id)
        self.psp = SimPaymentProvider(self.pay_signer, keys=self.keys, ap2_key=self.pay_ec, payee_keys=self._payee_key,
                                      wallets=self.wallets, payee_of=self._payee_of, db=self.db,
                                      on_transfer=lambda t: self.bus.emit("ap2", "transfer", sender=t["payer"], receiver=t["payee"],
                                                                          card_digest=t["ref"], period=t.get("period"),
                                                                          amount=t["amount"], outcome=t["purpose"]))
        self.router.apps["market"] = build_app(self.market.market, name="小艺开放平台 · Agent 交易市场", base_url=self.market_id,
                                               role="market", jwks={"keys": [self.market_signer.jwk]})
        self.router.apps["pay"] = build_app(self.psp, name="模拟支付方", base_url=self.pay_id, role="payment",
                                            jwks={"keys": [self.pay_signer.jwk]}, extensions=(A2C_EXTENSION, AP2_EXTENSION))

    # ── 身份映射 ──────────────────────────────────────────────────────
    def provider_url(self, agent_id: str) -> str:
        return f"{self.s.base_url}/a2a/providers/{agent_id}"

    def _agent_of_provider(self, provider_id: str) -> dict | None:
        aid = provider_id.rsplit("/", 1)[-1]
        return self.db.one("SELECT * FROM agents WHERE id=? AND role='service'", aid)

    def _payee_key(self, provider_id: str) -> Mapping[str, Any]:
        row = self._agent_of_provider(provider_id) or fail(CARD_INVALID, "unknown payee")
        return self.keystore.ec(row["id"]).jwk

    def _payee_of(self, provider_id: str) -> str | None:
        row = self._agent_of_provider(provider_id)
        return row["owner_id"] if row else None

    def _transport(self, party: str) -> TracingTransport:
        # trust_env=False：回环请求不走系统代理（代理会把 127.0.0.1 变成 502）
        public, local = self.s.public_url, self.s.local_url
        return TracingTransport(A2ATransport(http=lambda: httpx.AsyncClient(timeout=120, trust_env=False,
                                                                             transport=LoopbackTransport(public, local))),
                                self.bus, party)

    def _pin_public_url(self) -> None:
        """参与方 id 内嵌对外地址：首次运行记到 data/public_url，之后不一致就拒绝启动（改地址须 reset 重来）。"""
        p = self.s.data / "public_url"
        if p.exists():
            old = p.read_text(encoding="utf-8").strip()
            if old and old != self.s.public_url:
                raise SystemExit(f"[ioalab] IOALAB_PUBLIC_URL 已从 {old} 改为 {self.s.public_url}：参与方 id 内嵌旧地址，"
                                 f"请改回，或 `ioalab reset` 后重新 init")
        else:
            p.write_text(self.s.public_url, encoding="utf-8")

    # ── 启动 ──────────────────────────────────────────────────────────
    async def startup(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.loop.set_default_executor(ThreadPoolExecutor(max_workers=64))
        self.need_gate = asyncio.Semaphore(10)
        self.bus.new_run()
        await asyncio.to_thread(self.discovery.startup)
        for row in self.db.rows("SELECT * FROM agents ORDER BY created"):
            try:
                if row["role"] == "service":
                    self._load_service(row)
                else:
                    self._load_assistant(row)
            except Exception as e:  # noqa: BLE001
                log.exception("failed to load agent %s: %s", row["id"], e)
        # 重启策略 A：进行中的订单中止
        n = self.db.run("UPDATE orders SET state='aborted', phase='重启中止', updated=? WHERE state IN ('started','forming')", now()).rowcount
        self.db.run("UPDATE needs SET state='aborted', updated=? WHERE state IN ('running','started')", now())
        if n:
            self.bus.emit("platform", "restart", outcome=f"中止 {n} 单")
        self.started = True
        self.loop.create_task(self._poll_loop())
        self.loop.create_task(self._purge_loop())
        if self.s.autoseed:
            self.loop.create_task(self._bootstrap())

    async def _bootstrap(self) -> None:
        """首次启动自动注入内部账号与服务（要向市场发 template.endorse，须等 HTTP 服务开始监听）。"""
        await asyncio.sleep(1.0)
        try:
            out = await asyncio.to_thread(self.ensure_seeded)
            if out:
                log.info("seeded: %s", out)
        except Exception as e:  # noqa: BLE001
            log.exception("autoseed failed: %s", e)

    def ensure_seeded(self, *, force: bool = False) -> dict | None:
        """幂等注入种子：已有服务注册且不强制时跳过；返回 seed_all 的汇总。"""
        from .seed import seed_all
        if not force and self.db.one("SELECT 1 FROM agents WHERE role='service' LIMIT 1"):
            return None
        return seed_all(self)

    def reset_transactions(self) -> None:
        """停服后清交易数据，保留账号 · 钱包 · 服务与助手注册 · 已签认的模板 · 认证记录。"""
        import shutil
        for t in ("needs", "orders", "confirmations", "events", "transfers", "paid", "rulings"):
            self.db.run(f"DELETE FROM {t}")
        self.db.run("DELETE FROM kv WHERE tbl NOT IN ('templates', 'snapshots')")
        for d in ("deliveries", "runs", "needs"):
            shutil.rmtree(self.s.data / d, ignore_errors=True)
            (self.s.data / d).mkdir(parents=True, exist_ok=True)

    async def shutdown(self) -> None:
        for t in list(self.need_tasks.values()):
            t.cancel()
        A2ATransport.close_all()
        self.db.close()

    # ── 账号与钱包 ───────────────────────────────────────────────────
    def register_user(self, username: str, password: str = "", *, kind: str = "external", display_name: str = "", note: str = "",
                      invite: str = "", ip: str = "") -> dict:
        u = self.auth.register(username, password, kind=kind, display_name=display_name, note=note, invite=invite, ip=ip)
        self.wallets.ensure(u["id"])
        self.bus.emit("platform", "user.register", sender=u["id"], outcome=kind, detail={"username": username, "displayName": u["display_name"]})
        return u

    def login(self, username: str, password: str, *, ip: str = "") -> dict:
        """登录：核密码 → token；每自然日首次登录赠 IOALAB_LOGIN_CREDIT。"""
        u = self.auth.login(username, password, ip=ip)
        u["credited"] = self.daily_credit(u["id"])
        return u

    def daily_credit(self, user_id: str) -> bool:
        """同账号每自然日最多赠一次（wallet_log.kind='login'）。返回本次是否赠了。"""
        day0 = time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d"))
        if self.db.one("SELECT 1 FROM wallet_log WHERE user_id=? AND kind='login' AND at>=?", user_id, day0):
            return False
        self.grant(user_id, self.s.login_credit, by="platform", note="登录赠送", kind="login")
        return True

    def grant(self, user_id: str, delta: Any, *, by: str, note: str, kind: str = "grant") -> str:
        bal = self.wallets.adjust(user_id, money(delta), by=by, note=note, kind=kind)
        self.bus.emit("platform", "wallet.adjust", sender=by, receiver=user_id, amount=str(delta), outcome=kind, detail={"note": note})
        return str(bal)

    # ── 助手 ──────────────────────────────────────────────────────────
    def register_assistant(self, owner_id: str, name: str = "九问 Swarm") -> dict:
        if self.db.one("SELECT 1 FROM agents WHERE owner_id=? AND role='assistant'", owner_id):
            fail(CARD_INVALID, "该账号已注册个人助手")
        aid = f"asst-{owner_id[2:]}"
        self.db.run("INSERT INTO agents (id, owner_id, role, name, definition, status, created, meta) VALUES (?,?,?,?,?,?,?,?)",
                    aid, owner_id, "assistant", name, "{}", "online", now(), "{}")
        row = self.db.one("SELECT * FROM agents WHERE id=?", aid)
        self._load_assistant(row)
        self.bus.emit("platform", "assistant.register", sender=owner_id, receiver=aid, outcome=name)
        return row

    def _load_assistant(self, row: Mapping[str, Any]) -> AssistantRuntime:
        aid, owner = row["id"], row["owner_id"]
        signer = self.keystore.ed25519(f"urn:ioalab:client:{aid}", name=aid)
        user_signer = self.keystore.ed25519(f"urn:ioalab:user:{owner}", name=f"{aid}.user")
        payer = self.keystore.ec(aid)
        self.wallets.ensure(owner, thumbprint(payer.jwk))
        transport = self._transport(aid)
        client = Client(signer, keys=self.keys, transport=transport, fetch=self.packages.fetch,
                        payment_provider=self.payment_provider, store=SqliteStore(self.db, aid),
                        payment=AP2Payment(transport, psp=self.pay_id, payer=payer, instrument={"id": f"wallet-{owner}", "type": "wallet"},
                                           clock=utcnow),
                        user=user_signer, markets=(self.market_id,))
        hooks = Hooks(search=lambda q, k: self._search(q, k, by=aid), reputation=lambda pid: self.market.reputation(pid), wallet=lambda: str(self.wallets.balance(owner)),
                      confirm=lambda need_id, kind, text: self.confirm(owner, need_id, kind, text),
                      arbitrate=lambda cd, period, ground: self._arbitrate_for(aid, cd, period, ground),
                      on_phase=self._on_phase, market=self.market_id)
        rt = AssistantRuntime(agent_id=aid, name=row["name"], client=client, llm=self.llm, hooks=hooks,
                              home=self.s.data / "deliveries" / aid, loop=self.loop or asyncio.get_event_loop())
        self.assistants[aid] = rt
        return rt

    def _search(self, query: str, top_k: int, *, by: str = "") -> list[dict]:
        """市场检索；事件记下是哪个助手问的（sender）与命中的服务 id，回放才能把检索画到那个助手头上。"""
        hits = self.discovery.search(query, top_k)
        self.bus.emit("platform", "discovery.search", sender=by, receiver=self.market_id, outcome=self.discovery.last_mode,
                      detail={"query": query[:200], "hits": [h["name"] for h in hits], "ids": [h["id"] for h in hits]})
        return hits

    # ── 服务 ──────────────────────────────────────────────────────────
    def register_service(self, owner_id: str, definition: Mapping[str, Any], *, status: str = "online") -> dict:
        d = dict(definition)
        for k in ("id", "name", "description", "deliverable"):
            if k not in d:
                fail(CARD_INVALID, f"服务定义缺少 {k}")
        if d.get("formation", "preapproval") != "proposal" and "verifier" not in d:
            fail(CARD_INVALID, "非提案型服务定义需要 verifier")
        if "verifier" in d:
            for bad in ("uri", "digest"):
                if bad in d["verifier"]:
                    fail(CARD_INVALID, "服务定义不得自带验收器 uri / digest")
        n = self.db.one("SELECT COUNT(*) AS n FROM agents WHERE owner_id=? AND role='service'", owner_id)["n"]
        if n >= 3 and self.db.one("SELECT 1 FROM users WHERE id=? AND kind='external'", owner_id):
            fail(CARD_INVALID, "外部账号最多注册 3 个专业服务")
        aid = "svc-" + _SLUG.sub("-", d["id"].lower())
        if self.db.one("SELECT 1 FROM agents WHERE id=?", aid):
            fail(CARD_INVALID, f"服务 id 已存在：{d['id']}")
        self.db.run("INSERT INTO agents (id, owner_id, role, name, definition, status, created, meta) VALUES (?,?,?,?,?,?,?,?)",
                    aid, owner_id, "service", d["name"], jdump(d), status, now(), "{}")
        row = self.db.one("SELECT * FROM agents WHERE id=?", aid)
        rt = self._load_service(row)
        self._publish_and_index(rt, row)
        return row

    def _load_service(self, row: Mapping[str, Any]) -> ServiceRuntime:
        aid = row["id"]
        pid = self.provider_url(aid)
        signer = self.keystore.ed25519(pid, name=aid, kid="k1")
        self.keys.add_signer(signer)
        provider = Provider(signer, market=self.market_id, keys=self.keys, transport=self._transport(pid),
                            fetch=self.packages.fetch, store=SqliteStore(self.db, aid), ap2_key=self.keystore.ec(aid),
                            name=row["name"])
        trace_endpoint(provider, self.bus, pid)
        rt = ServiceRuntime(jload(row["definition"]), provider, llm=self.llm, packages=self.packages,
                            sandbox_gate=self.sandbox_gate, fuel=self.s.sandbox_fuel, loop=self.loop or asyncio.get_event_loop(),
                            on_delivered=self._on_delivered)
        self.services[aid] = rt
        self.router.apps["providers/" + aid] = build_app(provider, name=row["name"], base_url=pid, role="provider",
                                                         jwks={"keys": [signer.jwk]}, extensions=(A2C_EXTENSION, AP2_EXTENSION))
        self.router.local_only.add("providers/" + aid)
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(rt.start)     # 可能从工作线程调用：由循环自己起服务任务
        existing = list(provider.store.all("templates").values())
        if existing:
            rt.template = existing[0]
        return rt

    def _publish_and_index(self, rt: ServiceRuntime, row: Mapping[str, Any]) -> None:
        """签模板（市场签认）→ 认证记录 → 写入 A2X。要求 HTTP 服务已在监听。"""
        d = rt.definition
        t = rt.publish(self.payment_provider)
        only_script_verifiers(t)
        self.market.certify(rt.id, rt.provider.signer.jwk, d["name"])
        card = {"name": d["name"], "description": d["description"], "url": rt.id, "version": "1",
                "skills": [{"id": f"{rt.provider.signer.kid}-{i}", "name": tag, "description": tag, "tags": [tag]}
                           for i, tag in enumerate(d.get("tags", []))],
                "a2c": {"templates": [{"digest": t["templateDigest"], "formation": t["formation"],
                                       "price": (t["pricing"].get("priceFormula") or {}).get(d["deliverable"]["name"]),
                                       "proposalFee": t["pricing"].get("proposalFee")}], "paymentProvider": self.pay_id},
                "status": row["status"] if d.get("runnable") else "listed"}
        self.discovery.register_one(row["id"], card)
        self.bus.emit("platform", "service.register", sender=row["owner_id"], receiver=rt.id, outcome=card["status"],
                      detail={"name": d["name"], "formation": t["formation"]})

    def seed_services(self, items: list[tuple[str, Mapping[str, Any]]]) -> None:
        """种子：逐个注册 + 签模板，A2X 一次 register_batch。"""
        cards = []
        for owner_id, d in items:
            aid = "svc-" + _SLUG.sub("-", d["id"].lower())
            if self.db.one("SELECT 1 FROM agents WHERE id=?", aid):
                continue
            status = "online" if d.get("runnable") else "listed"
            self.db.run("INSERT INTO agents (id, owner_id, role, name, definition, status, created, meta) VALUES (?,?,?,?,?,?,?,?)",
                        aid, owner_id, "service", d["name"], jdump(d), status, now(), "{}")
            row = self.db.one("SELECT * FROM agents WHERE id=?", aid)
            rt = self._load_service(row)
            t = rt.publish(self.payment_provider)
            only_script_verifiers(t)
            self.market.certify(rt.id, rt.provider.signer.jwk, d["name"])
            cards.append((aid, {"name": d["name"], "description": d["description"], "url": rt.id, "version": "1",
                                "skills": [{"id": f"k1-{i}", "name": tag, "description": tag, "tags": [tag]} for i, tag in enumerate(d.get("tags", []))],
                                "a2c": {"templates": [{"digest": t["templateDigest"], "formation": t["formation"],
                                                       "price": (t["pricing"].get("priceFormula") or {}).get(d["deliverable"]["name"]),
                                                       "proposalFee": t["pricing"].get("proposalFee")}], "paymentProvider": self.pay_id},
                                "status": status}))
        if cards:
            self.discovery.register_batch(cards)
            self.discovery.startup()
        self.bus.emit("platform", "seed.services", outcome=f"{len(cards)} 个")

    # ── 需求与订单 ───────────────────────────────────────────────────
    def create_need(self, owner_id: str, assistant_id: str, text: str, scope: Mapping[str, Any], *, auto_sign: bool = True,
                    materials: list[Mapping[str, Any]] | None = None, by: str = "") -> dict:
        if assistant_id not in self.assistants:
            fail(CARD_INVALID, "助手不存在")
        day = time.strftime("%Y-%m-%d")
        n = self.db.one("SELECT COUNT(*) AS n FROM needs WHERE owner_id=? AND created>=?", owner_id,
                        time.mktime(time.strptime(day, "%Y-%m-%d")))["n"]
        if n >= self.s.needs_per_day and not by:
            fail(CARD_INVALID, f"今日需求已达上限 {self.s.needs_per_day}")
        if self.db.one("SELECT 1 FROM needs WHERE assistant_id=? AND state IN ('queued','running','started')", assistant_id):
            fail(CARD_INVALID, "该助手还有一条需求在进行中")
        nid = "need-" + uuid.uuid4().hex[:8]
        sc = {"category": scope.get("category", "不限"), "budget": scope.get("budget", 30), "perTx": scope.get("perTx", scope.get("budget", 30)),
              "validity": scope.get("validity", "P7D"), "sign1": scope.get("sign1", "client"), "sign7": scope.get("sign7", "client"),
              "nonce": uuid.uuid4().hex}
        self.db.run("INSERT INTO needs (id, owner_id, assistant_id, text, scope, auto_sign, state, created, updated, trace) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    nid, owner_id, assistant_id, text, jdump({**sc, "materials": materials or []}), int(auto_sign), "queued", now(), now(), "[]")
        self.bus.emit("platform", "need.create", sender=by or owner_id, receiver=assistant_id, outcome=nid, detail={"text": text[:120]})
        if self.loop and self.started:
            self.loop.call_soon_threadsafe(lambda: self.need_tasks.__setitem__(nid, self.loop.create_task(self._run_need(nid))))
        return self.need(nid)

    def need(self, nid: str) -> dict:
        row = self.db.one("SELECT * FROM needs WHERE id=?", nid) or fail(CARD_INVALID, "需求不存在")
        sc = jload(row["scope"])
        row["scope"] = {k: v for k, v in sc.items() if k != "materials"}
        row["materials"] = sc.get("materials", [])
        row["trace"] = jload(row["trace"], [])
        row["result"] = jload(row["result"]) if row["result"] else None
        return row

    def _set_need(self, nid: str, **kw) -> None:
        cols = ", ".join(f"{k}=?" for k in kw)
        self.db.run(f"UPDATE needs SET {cols}, updated=? WHERE id=?", *[jdump(v) if isinstance(v, (dict, list)) else v for v in kw.values()],
                    now(), nid)

    async def _run_need(self, nid: str) -> None:
        async with self.need_gate:
            need = self.need(nid)
            rt = self.assistants[need["assistant_id"]]
            self._set_need(nid, state="running")
            self.bus.emit("platform", "need.start", sender=need["assistant_id"], outcome=nid)
            for attempt in range(1, max(1, self.s.round1_attempts) + 1):
                try:
                    # 每条需求在自己的线程 + 事件循环里跑 ReActAgent：不与 uvicorn 的循环共用（openJiuwen 的客户端假设单循环，
                    # 与服务端同循环时模型应答会被拖到超时）
                    out = await asyncio.to_thread(lambda: asyncio.run(rt.run_round1(need)))
                    break
                except Exception as e:  # noqa: BLE001
                    msg = str(e)
                    transient = any(k in msg for k in _TRANSIENT) and attempt < self.s.round1_attempts
                    # 已开工的单不重跑第一轮（重跑会再拟一张卡）
                    # 已开工的单不重跑第一轮；只签了卡还没开工（forming）的，重跑时提示助手直接拿那张卡开工
                    forming = self.db.one("SELECT card_digest, provider_id FROM orders WHERE need_id=? AND state='forming' ORDER BY created DESC LIMIT 1", nid)
                    if transient and self.db.one("SELECT 1 FROM orders WHERE need_id=? AND state NOT IN ('declined','forming')", nid) is None:
                        if forming:
                            need = {**need, "resume": {"cardDigest": forming["card_digest"], "provider": forming["provider_id"]}}
                        log.warning("round 1 attempt %d transient failure, retrying: %s", attempt, msg[:160])
                        self.bus.emit("platform", "need.retry", sender=need["assistant_id"], outcome=nid,
                                      detail={"attempt": attempt, "reason": msg[:160]})
                        await asyncio.sleep(30 * attempt)
                        continue
                    log.exception("round 1 failed: %s", e)
                    out = {"result": {"failed": msg[:300]}, "trace": list(getattr(e, "trace", []) or [])}
                    break
            usage = self.llm.usage.get(nid)
            res = out["result"]
            cd = res.get("cardDigest") if isinstance(res, dict) else None
            # 服务方可能在助手收尾前就交付、结清（几秒内）：按订单当前状态定需求状态，不只认 started
            order = self.db.one("SELECT * FROM orders WHERE need_id=? AND state IN ('started','completed','arbitrated','lapsed') "
                                "ORDER BY created DESC LIMIT 1", nid)
            if order:
                if not (isinstance(res, dict) and res.get("cardDigest")):      # 助手收尾那一问失败，但单已在跑：按订单记结果
                    res = {"cardDigest": order["card_digest"], "provider": order["provider_id"], "price": order["total"],
                           "summary": "已定约开工（助手收尾时模型未应答，按订单状态记录）"}
                state = {"completed": "completed", "arbitrated": "arbitrated", "lapsed": "lapsed"}.get(order["state"], "started")
                self._set_need(nid, state=state, card_digest=order["card_digest"], result=res, trace=out["trace"],
                               llm_calls=usage["calls"] + len(out["trace"]), tokens=usage["tokens"])
                self.bus.emit("platform", "need.contracted", sender=need["assistant_id"], card_digest=order["card_digest"], outcome=nid)
            else:
                self._set_need(nid, state="failed", result=res, trace=out["trace"], llm_calls=len(out["trace"]), tokens=usage["tokens"])
                self.bus.emit("platform", "need.failed", sender=need["assistant_id"], outcome=nid,
                              detail={"reason": (res.get("failed") if isinstance(res, dict) else str(res))[:200]})

    def _on_phase(self, nid: str, phase: str, detail: Mapping[str, Any]) -> None:
        """助手运行时每到一个阶段回调一次：维护 orders 表，发平台事件。"""
        if self.db.one("SELECT 1 FROM needs WHERE id=?", nid) is None:    # 冒烟 / 测试直接驱动 SDK 时没有需求记录
            self.bus.emit("platform", "order." + phase, card_digest=detail.get("cardDigest"), period=detail.get("period"),
                          amount=detail.get("paid"), outcome=str(detail.get("status") or detail.get("liable") or ""))
            return
        need = self.need(nid)
        cd = detail.get("cardDigest")
        if phase == "signed" and cd:
            rt = self.assistants[need["assistant_id"]]
            card = rt.client.card(cd)
            t = rt.client.template(card["templateDigest"])
            pid = detail.get("provider") or card["provider"]
            self.db.run("INSERT OR REPLACE INTO orders (card_digest, need_id, owner_id, assistant_id, provider_id, template_digest, formation, total, state, phase, created, updated, detail) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", cd, nid, need["owner_id"], need["assistant_id"], pid, card["templateDigest"],
                        t["formation"], str(card["derived"]["total"]), "forming", "已签1", now(), now(), jdump({"cardId": card.get("cardId")}))
        elif phase == "declined" and cd:
            self.db.run("UPDATE orders SET state='declined', phase=?, updated=? WHERE card_digest=?", "服务方拒绝：" + str(detail.get("reason"))[:80], now(), cd)
        elif phase == "returned":                           # 提案卡被退回：没有卡，只记事件（助手换家的依据）
            self.bus.emit("platform", "request.returned", sender=need["assistant_id"], receiver=str(detail.get("provider") or ""),
                          outcome=str(detail.get("reason") or "")[:60], detail={"provider": detail.get("provider"), "reason": detail.get("reason")})
            return
        elif phase == "started" and cd:
            self.db.run("UPDATE orders SET state='started', phase='已开工', updated=? WHERE card_digest=?", now(), cd)
        elif phase == "fetched" and cd:
            self.db.run("UPDATE orders SET phase=?, updated=? WHERE card_digest=?", f"第 {detail.get('period')} 期已签收", now(), cd)
        elif phase == "verified" and cd:
            self.db.run("UPDATE orders SET phase=?, updated=? WHERE card_digest=?", f"第 {detail.get('period')} 期验收 {detail.get('status')}", now(), cd)
        elif phase == "accepted" and cd:
            self.db.run("UPDATE orders SET phase=?, updated=? WHERE card_digest=?", f"第 {detail.get('period')} 期已付款", now(), cd)
        elif phase == "arbitrated" and cd:
            self.db.run("UPDATE orders SET state='arbitrated', phase=?, updated=? WHERE card_digest=?", f"已裁定：责任在{'服务方' if detail.get('liable') == 'provider' else '委托方'}", now(), cd)
            self._set_need(nid, state="arbitrated")
        self.bus.emit("platform", "order." + phase, sender=need["assistant_id"], card_digest=cd, period=detail.get("period"),
                      amount=detail.get("paid"), outcome=str(detail.get("status") or detail.get("reason") or detail.get("liable") or ""),
                      detail={k: v for k, v in detail.items() if k not in ("cardDigest", "period", "paid")})

    def _on_delivered(self, cd: str, card: Mapping[str, Any]) -> None:
        self.db.run("UPDATE orders SET phase=?, updated=? WHERE card_digest=?", "服务方已交付（签4 · 签5）", now(), cd)

    def confirm(self, owner_id: str, need_id: str, kind: str, text: str, timeout: float = 900) -> bool:
        """上升给用户确认：写一条待确认记录，轮询到答复或超时（工作线程里调用）。"""
        cid = "cf-" + uuid.uuid4().hex[:8]
        self.db.run("INSERT INTO confirmations (id, owner_id, need_id, card_digest, kind, text, state, created) VALUES (?,?,?,?,?,?,?,?)",
                    cid, owner_id, need_id, None, kind, text, "pending", now())
        self.bus.emit("platform", "confirm.request", receiver=owner_id, outcome=kind, detail={"text": text[:200], "id": cid})
        deadline = time.time() + timeout
        while time.time() < deadline:
            row = self.db.one("SELECT state FROM confirmations WHERE id=?", cid)
            if row and row["state"] != "pending":
                self.bus.emit("platform", "confirm.answer", sender=owner_id, outcome=row["state"], detail={"id": cid, "kind": kind})
                return row["state"] == "granted"
            time.sleep(1.0)
        self.db.run("UPDATE confirmations SET state='timeout', answered=? WHERE id=?", now(), cid)
        return False

    def answer_confirmation(self, owner_id: str, cid: str, granted: bool) -> None:
        cur = self.db.run("UPDATE confirmations SET state=?, answered=? WHERE id=? AND owner_id=? AND state='pending'",
                          "granted" if granted else "refused", now(), cid, owner_id)
        if cur.rowcount != 1:
            fail(CARD_INVALID, "没有待确认的事项")

    def pay_installment(self, assistant_id: str, cd: str, period: int) -> dict:
        """补付第 i 期款：签7(i) 已在卡上而当时余量不足时，充值后由委托方再付（AP2 付款 + payment.notify）。"""
        rt = self.assistants[assistant_id]
        rec = rt.client.store.get("cards", cd) or fail(CARD_INVALID, "unknown card")
        card = rec["card"]
        if len(card["signatures"]["7"]) < period:
            fail(CARD_INVALID, f"第 {period} 期尚未验收，无款可付")
        template = rt.client.template(card["templateDigest"])
        voucher = rt.client.payment.pay(purpose="installment", template=template, card=card, period=period)
        rt.client.transport.send(card["provider"], "payment.notify", {"voucher": voucher})
        rec = rt.client.store.get("cards", cd)
        rec.setdefault("vouchers", []).append(voucher)
        rt.client.store.put("cards", cd, rec)
        self.db.run("UPDATE orders SET phase=?, updated=? WHERE card_digest=?", f"第 {period} 期已补付", now(), cd)
        self.bus.emit("platform", "order.paid-late", sender=assistant_id, card_digest=cd, period=period, amount=voucher["amount"])
        return voucher

    def transfer(self, payer_id: str, payee_id: str, amount: Any, *, purpose: str, ref: str) -> None:
        """普通转账（不读卡）：裁定后付款 ruling:<文书摘要> · 退差额 refund:<卡摘要> · 市场服务费 fee:<市场>。由责任方自己发起。"""
        if not purpose.split(":")[0] in ("ruling", "refund", "fee"):
            fail(CARD_INVALID, "普通转账的用途只能是 ruling / refund / fee")
        self.wallets.transfer(payer_id, payee_id, money(amount), purpose=purpose, ref=ref, by=payer_id)
        self.bus.emit("platform", "transfer.plain", sender=payer_id, receiver=payee_id, amount=str(money(amount)), outcome=purpose,
                      detail={"ref": ref})

    def _arbitrate_for(self, assistant_id: str, cd: str, period: int, ground: str) -> dict:
        rt = self.assistants[assistant_id]
        rec = rt.client.store.get("cards", cd) or fail(CARD_INVALID, "unknown card")
        card = rec["card"]
        files = {}
        home = rt.home / "deliveries" / cd.split(":")[1][:16] / str(period)
        if home.exists():
            files = {p.name: p.read_bytes() for p in home.iterdir() if p.is_file()}
        report = (card["fulfillment"].get("periods") or [{}] * period)[period - 1].get("report")
        ruling = self.market.arbitrate(card=card, period=period, files=files, report=report, applicant=rt.client.signer.id, ground=ground)
        self.bus.emit("platform", "arbitration.ruling", sender=self.market_id, card_digest=cd, period=period,
                      outcome=ruling["liable"], detail={"kind": ruling["rulingKind"], "ground": ground})
        return ruling

    # ── 定时：轮询存证通知与环节请求；清理失效的卡 ─────────────────────
    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(2.5)
            try:
                orders = self.db.rows("SELECT * FROM orders WHERE state='started'")
                await asyncio.gather(*(self._poll_order(o) for o in orders), return_exceptions=True)
            except Exception as e:  # noqa: BLE001
                log.exception("poll loop: %s", e)

    async def _poll_order(self, order: Mapping[str, Any]) -> None:
        rt = self.assistants.get(order["assistant_id"])
        if rt is None:
            return
        cd = order["card_digest"]
        need = self.need(order["need_id"])
        detail = jload(order["detail"])
        if detail.get("busy"):
            return
        try:
            events = await asyncio.to_thread(rt.client.poll, cd)
        except Exception as e:  # noqa: BLE001
            log.warning("poll %s: %s", cd[:20], e)
            return
        if not events:
            return
        detail["busy"] = True
        self.db.run("UPDATE orders SET detail=? WHERE card_digest=?", jdump(detail), cd)
        try:
            for ev in events:
                if ev["type"] == "anchored":
                    out = await asyncio.to_thread(rt.handle_anchored, need, cd, ev["period"])
                    if out.get("accepted"):
                        await self._maybe_close(order["provider_id"], cd)
                elif ev["type"] == "step-request":
                    await asyncio.to_thread(rt.handle_step, need, cd, ev["stepRequest"])
        except Exception as e:  # noqa: BLE001
            log.exception("handling events of %s: %s", cd[:20], e)
            self.bus.emit("platform", "order.error", card_digest=cd, outcome=str(e)[:200])
        finally:
            d = jload(self.db.one("SELECT detail FROM orders WHERE card_digest=?", cd)["detail"])
            d.pop("busy", None)
            self.db.run("UPDATE orders SET detail=? WHERE card_digest=?", jdump(d), cd)

    async def _maybe_close(self, provider_id: str, cd: str) -> None:
        aid = provider_id.rsplit("/", 1)[-1]
        rt = self.services.get(aid)
        if rt is None:
            return
        rec = rt.provider.store.get("cards", cd) or {}
        card = rec.get("card") or {}
        n = len(card.get("signatures", {}).get("7", []))
        if card and n >= len(rt.provider.template(card["templateDigest"])["deliverables"]):
            await asyncio.to_thread(rt.provider.close, cd)
            self.db.run("UPDATE orders SET state='completed', phase='已结清', updated=? WHERE card_digest=?", now(), cd)
            o = self.db.one("SELECT need_id FROM orders WHERE card_digest=?", cd)
            if o:
                self.db.run("UPDATE needs SET state='completed', card_digest=?, updated=? WHERE id=? AND state!='failed'", cd, now(), o["need_id"])
                self.db.run("UPDATE needs SET state='completed', card_digest=?, updated=? WHERE id=? AND card_digest IS NULL", cd, now(), o["need_id"])
            self.bus.emit("platform", "order.completed", sender=provider_id, card_digest=cd, outcome="completed")

    async def _purge_loop(self) -> None:
        while True:
            await asyncio.sleep(60)
            for rt in list(self.services.values()):
                try:
                    gone = await asyncio.to_thread(rt.provider.purge_lapsed)
                    for cd in gone:
                        self.db.run("UPDATE orders SET state='lapsed', phase='开工期限已过，卡失效', updated=? WHERE card_digest=? AND state!='completed'", now(), cd)
                except Exception as e:  # noqa: BLE001
                    log.warning("purge %s: %s", rt.name, e)

    # ── 管理员查询 ───────────────────────────────────────────────────
    def stats(self) -> dict:
        q = lambda sql, *a: self.db.one(sql, *a)["n"]  # noqa: E731
        day0 = time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d"))
        done = q("SELECT COUNT(*) AS n FROM orders WHERE state='completed'")
        total = q("SELECT COUNT(*) AS n FROM orders WHERE state IN ('completed','arbitrated','lapsed','declined')")
        durs = self.db.rows("SELECT updated-created AS d FROM orders WHERE state='completed'")
        return {"assistants": len(self.assistants), "services": q("SELECT COUNT(*) AS n FROM agents WHERE role='service'"),
                "runnable": sum(1 for r in self.services.values() if r.definition.get("runnable")),
                "users": q("SELECT COUNT(*) AS n FROM users"), "needs": q("SELECT COUNT(*) AS n FROM needs"),
                "orders_today": q("SELECT COUNT(*) AS n FROM orders WHERE created>=?", day0),
                "transfers_today": q("SELECT COUNT(*) AS n FROM transfers WHERE at>=?", day0),
                "volume_today": str(sum(money(r["amount"]) for r in self.db.rows("SELECT amount FROM transfers WHERE at>=?", day0))),
                "circulating": str(sum(money(r["balance"]) for r in self.db.rows("SELECT balance FROM wallets"))),
                "completed": done, "arbitrated": q("SELECT COUNT(*) AS n FROM orders WHERE state='arbitrated'"),
                "declined": q("SELECT COUNT(*) AS n FROM orders WHERE state='declined'"),
                "success_rate": round(done / total, 3) if total else None,
                "avg_duration_s": round(sum(r["d"] for r in durs) / len(durs), 1) if durs else None,
                "llm_calls": q("SELECT COALESCE(SUM(llm_calls),0) AS n FROM needs"), "tokens": q("SELECT COALESCE(SUM(tokens),0) AS n FROM needs"),
                "discovery": self.discovery.last_mode or ("a2x-tree" if self.discovery.llm_configured else "vector"),
                "taxonomy": self.discovery.taxonomy_state(), "run": self.bus.run_id, "mode": self.s.mode}

    def order_detail(self, cd: str) -> dict:
        o = self.db.one("SELECT * FROM orders WHERE card_digest=?", cd) or fail(CARD_INVALID, "订单不存在")
        rt = self.assistants.get(o["assistant_id"])
        rec = rt.client.store.get("cards", cd) if rt else None
        card = rec.get("card") if rec else None
        sigs = {}
        if card:
            for slot, v in card["signatures"].items():
                if isinstance(v, list):
                    sigs[slot] = [bool(x) for x in v]
                else:
                    sigs[slot] = bool(v)
        return {**o, "detail": jload(o["detail"]), "card": card, "signatures": sigs,
                "ruling": jload((self.db.one("SELECT ruling FROM rulings WHERE card_digest=?", cd) or {}).get("ruling")),
                "events": self.db.rows("SELECT * FROM events WHERE card_digest=? ORDER BY seq", cd)}


__all__ = ["Platform", "A2ARouter"]
