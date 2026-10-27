"""IoA infrastructure only. Contract clients and service workers live outside this process."""
from __future__ import annotations
import asyncio
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Mapping
import httpx
from starlette.responses import JSONResponse
from a2c.binding import A2C_EXTENSION, AP2_EXTENSION, A2ATransport, build_app
from a2c.common import Directory
from a2c.errors import CARD_INVALID, fail
from a2c.jws import header_of
from .config import Settings
from .events import EventBus, TracingTransport, trace_endpoint
from .keys import KeyStore
from .market import Discovery, MarketService
from .psp import SimPaymentProvider, Wallets, money, thumbprint
from .psp.auth import Auth
from .runtime.llm import LLM
from .runtime.packages import PackageStore
from .store import DB, SqliteStore, jdump, jload, now
log = logging.getLogger("ioalab.platform")

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
    def __init__(self, settings: Settings):
        self.s = settings
        settings.dirs()
        self._pin_public_url()
        settings.write_a2x_key()
        self.db = DB(settings.data / "ioalab.sqlite")
        self.bus = EventBus(self.db, settings.data / "runs")
        self.wallets, self.auth = Wallets(self.db), Auth(self.db, settings)
        self.packages = PackageStore(settings.data / "packages")
        self.llm = LLM(settings)  # discovery only
        self.router = A2ARouter()
        self.transports=[]
        self.keys = Directory(fetch=self._transport("directory").jwks)
        self.keystore = KeyStore(settings.data / "keys")
        self.market_id, self.pay_id = settings.base_url + "/a2a/market", settings.base_url + "/a2a/pay"
        self.payment_provider = {"id": self.pay_id, "jwksUri": self.pay_id + "/.well-known/jwks.json"}
        self.market_signer = self.keystore.ed25519(self.market_id, name="market", kid="m1")
        self.pay_signer = self.keystore.ed25519(self.pay_id, name="pay", kid="pay1")
        self.pay_ec = self.keystore.ec("pay")
        self.keys.add_signer(self.market_signer)
        self.keys.add_signer(self.pay_signer)
        self.discovery = Discovery(settings.data / "a2x", a2x_home=settings.a2x_home,
                                   llm_configured=settings.a2x_configured(), embed_fn=self.llm.embed if self.llm.enabled else None)
        self.market = MarketService(self.market_signer, keys=self.keys, fetch=self.packages.fetch,
                                    store=SqliteStore(self.db, "market"), db=self.db, discovery=self.discovery)
        trace_endpoint(self.market.market, self.bus, self.market_id)
        self.psp = SimPaymentProvider(self.pay_signer, keys=self.keys, ap2_key=self.pay_ec, payee_keys=self._payee_key,
                                      wallets=self.wallets, payee_of=self._payee_of, db=self.db,
                                      on_transfer=lambda t: self.bus.emit("ap2", "transfer", sender=t["payer"], receiver=t["payee"],
                                          card_digest=t["ref"], period=t.get("period"), amount=t["amount"], outcome=t["purpose"]))
        self.router.apps["market"] = build_app(self.market.market, name="IoA Lab Agent 交易市场", base_url=self.market_id,
                                               role="market", jwks={"keys": [self.market_signer.jwk]})
        self.router.apps["pay"] = build_app(self.psp, name="模拟支付方", base_url=self.pay_id, role="payment",
                                            jwks={"keys": [self.pay_signer.jwk]}, extensions=(A2C_EXTENSION, AP2_EXTENSION))
        self.started = False
        self.background = []
        from .messaging import MessageCenter
        self.messaging=MessageCenter(self)

    def _agent_of_provider(self, provider_id):
        return next((r for r in self.db.rows("SELECT * FROM agents WHERE role='service'")
                     if jload(r["meta"]).get("url") == provider_id), None)

    def provider_url(self, agent_id):
        row = self.db.one("SELECT meta FROM agents WHERE id=?", agent_id)
        return jload(row["meta"]).get("url", "") if row else ""

    def _payee_key(self, provider_id):
        row = self._agent_of_provider(provider_id) or fail(CARD_INVALID, "unknown payee")
        return jload(row["meta"])["ap2_jwk"]

    async def startup(self):
        self.bus.new_run()
        await asyncio.to_thread(self.discovery.startup)
        if self.s.autoseed:
            await asyncio.to_thread(self.ensure_seeded)
        self.started = True
        self.background.append(asyncio.create_task(self._sync_loop()))

    async def shutdown(self):
        for task in self.background:
            task.cancel()
        await asyncio.gather(*self.background, return_exceptions=True)
        for transport in self.transports:
            await asyncio.to_thread(transport.close)
        self.db.close()

    def ensure_seeded(self, *, force=False):
        from .seed import seed_all
        return seed_all(self)

    def reset_transactions(self):
        for name in ("orders", "events", "observations", "transfers", "paid", "rulings"):
            self.db.run(f"DELETE FROM {name}")
        self.db.run("DELETE FROM kv WHERE party='market' AND tbl NOT IN ('templates','snapshots')")

    async def _sync_loop(self):
        while True:
            await asyncio.sleep(1)
            self.sync_orders()
            self.messaging.cleanup()

    def sync_orders(self):
        for row in self.db.rows("SELECT card_digest,state FROM orders"):
            entry = self.market.market.store.get("ledger", row["card_digest"])
            if entry and entry.get("outcome") != row["state"]:
                self.db.run("UPDATE orders SET state=?,phase=?,updated=? WHERE card_digest=?", entry["outcome"], entry["outcome"], now(), row["card_digest"])
                self.bus.emit("platform", "order." + entry["outcome"], sender=self.market_id,
                              card_digest=row["card_digest"], outcome=entry["outcome"], detail={"trust": "verified"})

    def _payee_of(self, provider_id: str) -> str | None:
        row = self._agent_of_provider(provider_id)
        return row["owner_id"] if row else None


    def _transport(self, party: str) -> TracingTransport:
        # trust_env=False：回环请求不走系统代理（代理会把 127.0.0.1 变成 502）
        public, local = self.s.public_url, self.s.local_url
        transport = TracingTransport(A2ATransport(http=lambda: httpx.AsyncClient(timeout=120, trust_env=False,
                                                                             transport=LoopbackTransport(public, local))),
                                self.bus, party)
        self.transports.append(transport)
        return transport


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


    def _search(self, query: str, top_k: int, *, by: str = "") -> list[dict]:
        """市场检索；事件记下是哪个助手问的（sender）与命中的服务 id，回放才能把检索画到那个助手头上。"""
        hits = self.discovery.search(query, top_k)
        self.bus.emit("platform", "discovery.search", sender=by, receiver=self.market_id, outcome=self.discovery.last_mode,
                      detail={"query": query[:200], "hits": [h["name"] for h in hits], "ids": [h["id"] for h in hits]})
        return hits


    def transfer(self, payer_id: str, payee_id: str, amount: Any, *, purpose: str, ref: str) -> None:
        """普通转账（不读卡）：裁定后付款 ruling:<文书摘要> · 退差额 refund:<卡摘要> · 市场服务费 fee:<市场>。由责任方自己发起。"""
        if not purpose.split(":")[0] in ("ruling", "refund", "fee"):
            fail(CARD_INVALID, "普通转账的用途只能是 ruling / refund / fee")
        self.wallets.transfer(payer_id, payee_id, money(amount), purpose=purpose, ref=ref, by=payer_id)
        self.bus.emit("platform", "transfer.plain", sender=payer_id, receiver=payee_id, amount=str(money(amount)), outcome=purpose,
                      detail={"ref": ref})


    def register_external_provider(self, owner_id: str, *, url: str, name: str, description: str, tags: list[str] | None = None,
                                   category: str | None = None, ap2_jwk: Mapping[str, Any] | None = None) -> dict:
        """登记一个外部 A2A 服务方：取 Agent Card 与 JWKS → 市场认证其公钥 → 登记 AP2 收款公钥 → 取其公示的模板 → 写入 A2X 并归叶类。"""
        from a2c.binding import A2C_EXTENSION, CARD_PATH, JWKS_PATH
        from a2c.slots import verify_template
        url = (url or "").strip().rstrip("/")
        if not url.startswith("http"):
            fail(CARD_INVALID, "url 须是服务方 A2A 应用的地址（http(s)://…）")
        if url in (self.market_id, self.pay_id):
            fail(CARD_INVALID, "不能把平台自己的地址登记为外部服务方")
        if not name:
            fail(CARD_INVALID, "缺 name")
        if not (isinstance(ap2_jwk, Mapping) and ap2_jwk.get("kty") == "EC"):
            fail(CARD_INVALID, "ap2_jwk 须是 ES256 公钥（kty EC）——收款用")
        from .participants import public_key
        public_key(dict(ap2_jwk), ec=True)
        existing = self._agent_of_provider(url)
        if existing:
            if existing["owner_id"] != owner_id or jload(existing["meta"])["ap2_jwk"] != ap2_jwk:
                fail(CARD_INVALID, "Service identity already belongs to another owner or key")
            return existing
        try:
            with httpx.Client(timeout=15, trust_env=False) as http:
                card_json = http.get(url + CARD_PATH).json()
                jwks = http.get(url + JWKS_PATH).json()
        except Exception as e:  # noqa: BLE001
            fail(CARD_INVALID, f"取不到服务方的 Agent Card / JWKS：{str(e)[:160]}")
        if A2C_EXTENSION not in json.dumps(card_json):
            fail(CARD_INVALID, "服务方的 Agent Card 没有声明 A2C 扩展")
        okp = [k for k in jwks.get("keys", []) if k.get("kty") == "OKP"]
        if not okp:
            fail(CARD_INVALID, "服务方 JWKS 里没有 Ed25519 公钥")
        public_key(okp[0])
        self.market.certify(url, okp[0], name)                     # 公钥入 Directory（市场签认它的模板要用），认证记录
        import hashlib
        aid = "ext-" + hashlib.sha1(url.encode()).hexdigest()[:10]
        d = {"id": aid, "name": name, "description": description or name, "tags": list(tags or []), "runnable": True,
             "formation": "preapproval", "category": category, "external": True, "url": url, "deliverable": {"name": "deliverable"}}
        self.db.run("INSERT INTO agents (id, owner_id, role, name, definition, status, created, meta) VALUES (?,?,?,?,?,?,?,?)",
                    aid, owner_id, "service", name, jdump(d), "listed", now(), jdump({"url": url, "ap2_jwk": dict(ap2_jwk), "external": True}))
        card = {"name": name, "description": d["description"], "url": url, "version": "1",
                "skills": [{"id": f"ext-{i}", "name": tag, "description": tag, "tags": [tag]} for i, tag in enumerate(d["tags"])],
                "a2c": {"templates": [], "paymentProvider": self.pay_id}, "status": "listed"}
        self.discovery.register_one(aid, card, category=category)
        status = self.refresh_external_provider(aid, quiet=True)
        self.bus.emit("platform", "service.register", sender=owner_id, receiver=url, outcome=status,
                      detail={"name": name, "category": category, "external": True})
        return self.db.one("SELECT * FROM agents WHERE id=?", aid)


    def refresh_external_provider(self, aid: str, *, quiet: bool = False) -> str:
        """拉外部服务方公示的模板（须经本平台市场签认）进 A2X 索引；有模板即 online，没有保持 listed。返回状态。"""
        from a2c.slots import verify_template
        row = self.db.one("SELECT * FROM agents WHERE id=? AND role='service'", aid) or fail(CARD_INVALID, "没有这个服务方")
        meta, d = jload(row["meta"] or "{}"), jload(row["definition"])
        if not meta.get("external"):
            fail(CARD_INVALID, "不是外部服务方")
        url = meta["url"]
        try:
            reply = self._transport("platform").send(url, "template.get", {})
            templates = [t for t in reply.body.get("templates", []) if t.get("provider") == url]
            for t in templates:
                verify_template(t, self.keys)
                if header_of(t["signatures"]["market"])["a2c-signer"] != self.market_id:
                    fail(CARD_INVALID, "Template was not endorsed by this market")
            templates = [t for t in templates if not t.get("pricing", {}).get("paymentProviders")
                         or any(p.get("id") == self.pay_id for p in t["pricing"]["paymentProviders"])]
        except Exception as e:  # noqa: BLE001
            if quiet:
                return row["status"]
            fail(CARD_INVALID, f"取不到服务方公示的模板（须先向本平台市场 template.endorse）：{str(e)[:160]}")
        status = "online" if templates else "listed"
        if templates:
            d["formation"] = templates[0].get("formation", "preapproval")
            d["deliverable"] = {"name": next(iter(templates[0].get("deliverables") or [{"name": "deliverable"}]))["name"]}
            d["pricing"] = {"price": (templates[0].get("pricing", {}).get("priceFormula") or {}).get(d["deliverable"]["name"])}
        self.db.run("UPDATE agents SET status=?, definition=? WHERE id=?", status, jdump(d), aid)
        self.discovery.update_card(aid, {"status": status, "a2c": {"templates": [
            {"digest": t["templateDigest"], "formation": t.get("formation"), "price": (t.get("pricing", {}).get("priceFormula") or {}).get(d["deliverable"]["name"]),
             "proposalFee": t.get("pricing", {}).get("proposalFee")} for t in templates], "paymentProvider": self.pay_id}})
        if status == "online" and row["status"] != "online":
            self.bus.emit("platform", "service.online", sender=row["owner_id"], receiver=url, outcome=status,
                          detail={"name": row["name"], "templates": len(templates), "external": True})
        return status


    def stats(self):
        self.sync_orders()
        count = lambda table, where="1": self.db.one(f"SELECT COUNT(*) AS n FROM {table} WHERE {where}")["n"]
        day0=time.mktime(time.strptime(time.strftime('%Y-%m-%d'),'%Y-%m-%d'))
        transfers=self.db.rows('SELECT amount FROM transfers WHERE at>=?',day0)
        return {"assistants": count("agents", "role='assistant'"), "services": count("agents", "role='service'"),
                "runnable": count("agents", "role='service' AND status='online'"), "users": count("users"), "needs": 0,
                "orders_today": count("orders",f'created>={day0}'), "transfers_today": len(transfers), "completed": count("orders", "state='completed'"),
                "arbitrated": count("rulings"), "declined": 0, "success_rate": None, "avg_duration_s": None,
                "volume_today": str(sum(money(r["amount"]) for r in transfers)),
                "circulating": str(sum(money(r["balance"]) for r in self.db.rows("SELECT balance FROM wallets"))),
                "llm_calls": None, "tokens": None, "discovery": self.discovery.last_mode, "taxonomy": self.discovery.taxonomy_state(),
                "run": self.bus.run_id, "mode": self.s.mode}

    def components(self):
        stats = self.stats()
        reports=[jload(r['meta']) for r in self.db.rows("SELECT meta FROM agents WHERE role='service'")]
        connected=[r for r in reports if now()-r.get('lastSeen',0)<45 and 'execution' in r]
        self.messaging.cleanup()
        message_states={r['state']:r['n'] for r in self.db.rows('SELECT state,COUNT(*) AS n FROM messages GROUP BY state')}
        return {"messaging":{"interfaces":self.db.one('SELECT COUNT(*) AS n FROM message_interfaces')['n'],"states":message_states,"pending":sum(v for k,v in message_states.items() if k not in ('completed','rejected','expired'))},"registry": {"services": stats["services"], "online": stats["runnable"], "categories": len(self.discovery.categories()),
                             "taxonomy": stats["taxonomy"], "last_mode": self.discovery.last_mode},
                "templates": {"endorsed": len(self.market.market.store.all("templates")), "snapshots": len(self.market.market.store.all("snapshots")),
                              "certifications": len(self.db.rows("SELECT agent_id FROM certifications"))},
                "ledger": {"completed": stats["completed"], "rulings": stats["arbitrated"], "declined": 0},
                "payment": {"transfers_today": stats["transfers_today"], "volume_today": stats["volume_today"],
                            "circulating": stats["circulating"], "wallets": stats["users"], "transfers_total":len(self.db.rows('SELECT id FROM transfers'))},
                "services": {"online": stats["runnable"],"observed":len(connected),"unknown":len(reports)-len(connected),
                             "working":sum(r['execution'].get('running',0) for r in connected) if connected else None,
                             "queued":sum(r['execution'].get('queued',0) for r in connected) if connected else None,
                             "sessions":sum(r['execution'].get('sessions',0) for r in connected) if connected else None,
                             "busy": [], "location": "independent-process"},
                "assistants": {"total": stats["assistants"], "external_driven": stats["assistants"], "active_needs": 0,
                               "pending_confirmations": 0, "runtime": False},
                "llm": {"key_configured": self.llm.enabled, "model": self.s.a2x_model, "a2x_model": self.s.a2x_model,
                        "embed_model": self.s.embedding_model, "calls": None, "tokens": None, "concurrency": self.s.llm_concurrency},
                "sandbox": {"concurrency": self.s.sandbox_concurrency, "available": None},
                "bus": {"run": self.bus.run_id, "seq": (self.db.one("SELECT MAX(seq) AS n FROM events") or {}).get("n") or 0,
                        "events": len(self.bus.recent(2000)), "subscribers": len(self.bus.subscribers)},
                "traffic": {"level": "external", "enabled": False},
                "config": {"mode": self.s.mode, "public_url": self.s.public_url, "registration": self.s.registration,
                           "login_credit": self.s.login_credit, "isc_count": self.s.isc_count,
                           "version": (Path(__file__).parents[1] / "设计/VERSION").read_text(encoding="utf-8").strip()}}

    def order_detail(self, cd):
        self.sync_orders()
        row = self.db.one("SELECT * FROM orders WHERE card_digest=?", cd) or fail(CARD_INVALID, "Order not observed")
        detail = jload(row["detail"])
        card = detail.get("card")
        sigs = {k: [bool(x) for x in v] if isinstance(v, list) else bool(v) for k,v in card["signatures"].items()} if card else {}
        return {**row, "detail": detail, "card": card, "signatures": sigs, "paidPeriods": [r["period"] for r in self.db.rows("SELECT period FROM paid WHERE ref=? AND purpose='installment'", cd)],
                "ruling": jload((self.db.one("SELECT ruling FROM rulings WHERE card_digest=?", cd) or {}).get("ruling")),
                "events": self.db.rows("SELECT * FROM events WHERE card_digest=? ORDER BY seq", cd)}
