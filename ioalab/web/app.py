"""FastAPI 应用：用户端与管理员端 API、WebSocket 实时交易流、A2A 端点挂载、静态页面。"""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from a2c.errors import A2CError

from ..platform import Platform
from ..psp import money
from ..store import jdump, jload

log = logging.getLogger("ioalab.web")
STATIC = Path(__file__).parent / "static"
COOKIE = "ioalab_session"


def create_app(platform: Platform) -> FastAPI:
    s = platform.s

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await platform.startup()
        yield
        await platform.shutdown()

    app = FastAPI(title="九问IoA仿真平台 · Jiuwen IoA Lab", lifespan=lifespan, docs_url="/api/docs" if not s.open else None, redoc_url=None)
    app.mount("/a2a", platform.router)

    class OriginCheck(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            if request.method in ("POST", "PUT", "DELETE") and request.url.path.startswith("/api/"):
                origin = request.headers.get("origin") or request.headers.get("referer") or ""
                host = request.headers.get("host", "")
                if origin and host and host not in origin:
                    return JSONResponse({"error": "跨站请求被拒绝"}, status_code=403)
            return await call_next(request)

    app.add_middleware(OriginCheck)

    @app.exception_handler(A2CError)
    async def _a2c_error(request: Request, exc: A2CError):
        return JSONResponse({"error": str(exc), "code": getattr(exc, "code", "")}, status_code=400)

    # ── 会话 ──────────────────────────────────────────────────────────
    def subject(request: Request) -> str | None:
        auth = request.headers.get("authorization", "")
        bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        return platform.auth.verify(request.cookies.get(COOKIE) or bearer or request.headers.get("x-ioalab-token"))

    def current_user(request: Request) -> str:
        sub = subject(request)
        if not sub or sub == "admin":
            raise HTTPException(401, "请先登录")
        return sub

    def require_admin(request: Request) -> str:
        if subject(request) != "admin":
            raise HTTPException(401, "需要管理员登录")
        return "admin"

    def set_cookie(resp: Response, token: str) -> None:
        resp.set_cookie(COOKIE, token, httponly=True, samesite="lax", secure=s.public_url.startswith("https://"), max_age=7 * 86400)

    def ip(request: Request) -> str:
        return request.client.host if request.client else ""

    async def body(request: Request) -> dict:
        try:
            return await request.json()
        except Exception:  # noqa: BLE001
            return {}

    # ── 用户端 API ────────────────────────────────────────────────────
    @app.get("/api/config")
    async def config():
        return {"mode": s.mode, "registration": s.registration, "loginCredit": s.login_credit, "currency": "虚拟币", "llm": platform.llm.enabled,
                "discovery": platform.discovery.last_mode or ("a2x-tree" if platform.discovery.llm_configured else "vector")}

    @app.get("/api/session")
    async def session(request: Request):
        """页面载入时探测会话（只加：避免未登录时 /api/me、/api/admin/stats 的 401 进浏览器控制台）。"""
        sub = subject(request)
        return {"role": None if not sub else ("admin" if sub == "admin" else "user"), "id": None if sub in (None, "admin") else sub}

    @app.post("/api/auth/register")
    async def register(request: Request):
        """用户名密码注册（external）；注册即首次登录：发 cookie、JSON 里也回 token、赠当日登录币。"""
        b = await body(request)
        u = await asyncio.to_thread(platform.register_user, b.get("username", ""), b.get("password", ""),
                                    display_name=b.get("display_name", ""), invite=b.get("invite", ""), ip=ip(request))
        u["credited"] = await asyncio.to_thread(platform.daily_credit, u["id"])
        u["token"] = platform.auth.bearer(u["id"])
        resp = JSONResponse(u)
        set_cookie(resp, platform.auth.token(u["id"]))
        return resp

    @app.post("/api/auth/login")
    async def login(request: Request):
        b = await body(request)
        u = await asyncio.to_thread(platform.login, b.get("username", ""), b.get("password", ""), ip=ip(request))
        resp = JSONResponse({**u, "token": platform.auth.bearer(u["id"])})
        set_cookie(resp, u["token"])
        return resp

    @app.post("/api/auth/logout")
    async def logout():
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE)
        return resp

    @app.get("/api/me")
    async def me(uid: str = Depends(current_user)):
        u = platform.db.one("SELECT id, username, kind, display_name, created, last_login FROM users WHERE id=?", uid)
        agents = platform.db.rows("SELECT id, role, name, status, created FROM agents WHERE owner_id=?", uid)
        return {**u, "wallet": str(platform.wallets.balance(uid)), "agents": agents,
                "pending": platform.db.rows("SELECT * FROM confirmations WHERE owner_id=? AND state='pending' ORDER BY created", uid)}

    @app.get("/api/wallet")
    async def wallet(uid: str = Depends(current_user)):
        return {"balance": str(platform.wallets.balance(uid)), "log": platform.wallets.log(uid)}

    @app.post("/api/assistant")
    async def register_assistant(request: Request, uid: str = Depends(current_user)):
        b = await body(request)
        return await asyncio.to_thread(platform.register_assistant, uid, b.get("name") or "九问 Swarm")

    @app.get("/api/services/definitions")
    async def definitions():
        from ..seed import load_definitions
        return [{"id": d["id"], "name": d["name"], "description": d["description"], "formation": d.get("formation", "preapproval"),
                 "runnable": bool(d.get("runnable")), "price": d.get("pricing", {}).get("price")} for d in load_definitions() if d.get("runnable")]

    @app.post("/api/service")
    async def register_service(request: Request, uid: str = Depends(current_user)):
        from ..seed import load_definitions
        b = await body(request)
        if b.get("definition"):
            d = b["definition"]
        else:
            d = next((x for x in load_definitions() if x["id"] == b.get("definition_id")), None)
            if d is None:
                raise HTTPException(400, "没有这个服务定义")
            d = {**d, "id": f"{d['id']}-{uid[2:]}", "name": b.get("name") or d["name"]}
        return await asyncio.to_thread(platform.register_service, uid, d)

    @app.post("/api/needs")
    async def create_need(request: Request, uid: str = Depends(current_user)):
        b = await body(request)
        asst = platform.db.one("SELECT id FROM agents WHERE owner_id=? AND role='assistant'", uid)
        if not asst:
            raise HTTPException(400, "先注册个人助手")
        scope = b.get("scope") or {}
        scope.setdefault("sign1", "user" if b.get("sign1") else "client")
        scope.setdefault("sign7", "user" if b.get("sign7") else "client")
        return platform.create_need(uid, asst["id"], b.get("text", ""), scope, auto_sign=bool(b.get("autoSign", False)),
                                    materials=b.get("materials") or [])

    @app.get("/api/needs")
    async def my_needs(uid: str = Depends(current_user)):
        rows = platform.db.rows("SELECT id, text, state, card_digest, created, updated, llm_calls, tokens FROM needs WHERE owner_id=? ORDER BY created DESC", uid)
        return rows

    @app.get("/api/needs/{nid}")
    async def need_detail(nid: str, uid: str = Depends(current_user)):
        n = platform.need(nid)
        if n["owner_id"] != uid:
            raise HTTPException(403)
        return n

    @app.get("/api/orders")
    async def my_orders(uid: str = Depends(current_user)):
        return platform.db.rows("SELECT * FROM orders WHERE owner_id=? ORDER BY created DESC", uid)

    @app.get("/api/orders/{cd}")
    async def order_detail(cd: str, request: Request):
        sub = subject(request)
        o = platform.db.one("SELECT owner_id FROM orders WHERE card_digest=?", cd)
        if not o:
            raise HTTPException(404)
        if sub != "admin" and sub != o["owner_id"]:
            raise HTTPException(403)
        d = platform.order_detail(cd)
        # 只加字段：服务方名字（用户端没有注册表可查）
        d["provider_name"] = (platform.db.one("SELECT name FROM agents WHERE id=?", (d.get("provider_id") or "").rsplit("/", 1)[-1]) or {}).get("name")
        return d

    @app.post("/api/confirmations/{cid}")
    async def answer(cid: str, request: Request, uid: str = Depends(current_user)):
        b = await body(request)
        platform.answer_confirmation(uid, cid, bool(b.get("granted")))
        return {"ok": True}

    # ── 管理员 API ────────────────────────────────────────────────────
    @app.post("/api/admin/login")
    async def admin_login(request: Request):
        b = await body(request)
        platform.auth.rate.check("admin:" + ip(request))
        token = platform.auth.admin_login(b.get("password", ""))
        resp = JSONResponse({"ok": True})
        set_cookie(resp, token)
        return resp

    @app.get("/api/admin/stats")
    async def stats(_: str = Depends(require_admin)):
        return platform.stats()

    @app.get("/api/admin/events")
    async def events(_: str = Depends(require_admin), after: int = 0, limit: int = 300, run: str = ""):
        rid = run or platform.bus.run_id
        rows = platform.db.rows("SELECT * FROM events WHERE run_id=? AND seq>? ORDER BY seq LIMIT ?", rid, after, limit)
        for r in rows:
            r["detail"] = jload(r["detail"])
        return rows

    @app.get("/api/admin/runs")
    async def runs(_: str = Depends(require_admin)):
        return [p.name for p in sorted((s.data / "runs").iterdir()) if p.is_dir()]

    @app.get("/api/admin/orders")
    async def admin_orders(_: str = Depends(require_admin)):
        rows = platform.db.rows("SELECT o.*, n.text AS need_text, a.name AS assistant_name FROM orders o LEFT JOIN needs n ON n.id=o.need_id "
                                "LEFT JOIN agents a ON a.id=o.assistant_id ORDER BY o.created DESC LIMIT 200")
        for r in rows:
            r["detail"] = jload(r["detail"])
            r["provider_name"] = (platform.db.one("SELECT name FROM agents WHERE id=?", r["provider_id"].rsplit("/", 1)[-1]) or {}).get("name")
        return rows

    @app.get("/api/admin/needs")
    async def admin_needs(_: str = Depends(require_admin)):
        rows = platform.db.rows("SELECT n.*, a.name AS assistant_name FROM needs n LEFT JOIN agents a ON a.id=n.assistant_id ORDER BY n.created DESC LIMIT 200")
        for r in rows:
            r["result"] = jload(r["result"]) if r["result"] else None
            r["trace"] = jload(r["trace"], [])
            r["scope"] = {k: v for k, v in jload(r["scope"]).items() if k != "materials"}
        return rows

    @app.get("/api/admin/users")
    async def users(_: str = Depends(require_admin)):
        rows = platform.db.rows("SELECT u.*, w.balance FROM users u LEFT JOIN wallets w ON w.user_id=u.id ORDER BY u.created")
        for r in rows:
            r.pop("pw_hash", None)
            r["agents"] = platform.db.rows("SELECT id, role, name, status FROM agents WHERE owner_id=?", r["id"])
        return rows

    @app.post("/api/admin/users")
    async def create_virtual_user(request: Request, _: str = Depends(require_admin)):
        b = await body(request)
        """管理员创建临时账号（kind virtual）：用户名默认 virtual_<序号>，密码随机并只在本次响应里返回。"""
        import secrets as _secrets
        n = int(b.get("count", 1))
        out = []
        for i in range(n):
            name = b.get("username") if n == 1 and b.get("username") else f"virtual_{platform.db.one('SELECT COUNT(*) AS n FROM users')['n'] + 1:03d}"
            pw = b.get("password") or _secrets.token_urlsafe(8)
            u = await asyncio.to_thread(platform.register_user, name, pw, kind="virtual", display_name=b.get("display_name", ""),
                                        note=b.get("note", "管理员创建"))
            if b.get("assistant"):
                await asyncio.to_thread(platform.register_assistant, u["id"], b.get("assistantName") or "九问 Swarm")
            if b.get("credit"):
                platform.grant(u["id"], b["credit"], by="admin", note="创建时发放")
            out.append({**u, "password": pw})
        return out

    @app.post("/api/admin/wallet")
    async def adjust(request: Request, _: str = Depends(require_admin)):
        b = await body(request)
        return {"balance": platform.grant(b["user_id"], b["delta"], by="admin", note=b.get("note", "管理员调整"))}

    @app.post("/api/admin/invite")
    async def invite(_: str = Depends(require_admin)):
        return {"code": platform.auth.new_invite()}

    @app.get("/api/admin/registry")
    async def registry(_: str = Depends(require_admin)):
        rows = platform.db.rows("SELECT id, owner_id, name, status, created, definition FROM agents WHERE role='service' ORDER BY created")
        for r in rows:
            d = jload(r.pop("definition"))
            r.update(formation=d.get("formation", "preapproval"), runnable=bool(d.get("runnable")), tags=d.get("tags", []),
                     price=d.get("pricing", {}).get("price"), reputation=platform.market.reputation(platform.provider_url(r["id"])))
            r["reputation"].pop("signature", None)
        return {"taxonomy": platform.discovery.taxonomy_state(), "services": rows}

    @app.post("/api/admin/search")
    async def admin_search(request: Request, _: str = Depends(require_admin)):
        b = await body(request)
        hits = await asyncio.to_thread(platform._search, b.get("query", ""), int(b.get("top_k", 8)))
        return {"mode": platform.discovery.last_mode, "hits": [{"id": h["id"], "name": h["name"], "status": h["card"].get("status")} for h in hits]}

    @app.post("/api/admin/dispatch")
    async def dispatch(request: Request, _: str = Depends(require_admin)):
        """投放：给指定助手（或所有空闲助手）各下一条需求。body: {needs: [{assistant_id?, owner_id?, text, scope, autoSign, materials}], gap: 秒}"""
        b = await body(request)
        gap = float(b.get("gap", 2.0))
        out = []
        for i, n in enumerate(b.get("needs", [])):
            aid = n.get("assistant_id")
            row = platform.db.one("SELECT id, owner_id FROM agents WHERE id=? AND role='assistant'", aid) if aid else None
            if row is None:
                raise HTTPException(400, f"助手不存在：{aid}")
            out.append(platform.create_need(row["owner_id"], row["id"], n["text"], n.get("scope") or {}, auto_sign=bool(n.get("autoSign", True)),
                                            materials=n.get("materials") or [], by="admin")["id"])
            if gap and i < len(b.get("needs", [])) - 1:
                await asyncio.sleep(gap)
        return {"needs": out}

    @app.get("/api/admin/seed-needs")
    async def seed_needs(_: str = Depends(require_admin)):
        """seed 时写下的 10 条可投放需求（含助手 id 与材料路径），供「一键投放」用。"""
        p = s.data / "dispatch.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []

    @app.post("/api/admin/seed")
    async def seed(request: Request, _: str = Depends(require_admin)):
        from ..seed import seed_all
        b = await body(request)
        return await asyncio.to_thread(seed_all, platform, only=b.get("only"))

    @app.post("/api/admin/run")
    async def new_run(_: str = Depends(require_admin)):
        return {"run": platform.bus.new_run()}

    @app.get("/api/admin/replay/{run}")
    async def replay(run: str, _: str = Depends(require_admin)):
        p = s.data / "runs" / run / "events.jsonl"
        if not p.exists():
            raise HTTPException(404)
        return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]

    @app.websocket("/ws/admin")
    async def ws_admin(ws: WebSocket):
        if platform.auth.verify(ws.cookies.get(COOKIE)) != "admin":
            await ws.close(code=4401)
            return
        origin, host = ws.headers.get("origin", ""), ws.headers.get("host", "")
        if origin and host not in origin:
            await ws.close(code=4403)
            return
        await ws.accept()
        q = platform.bus.subscribe()
        try:
            for ev in platform.bus.recent(100):
                await ws.send_text(json.dumps(ev, ensure_ascii=False))
            while True:
                ev = await q.get()
                await ws.send_text(json.dumps(ev, ensure_ascii=False))
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            platform.bus.unsubscribe(q)

    # ── 页面 ──────────────────────────────────────────────────────────
    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/admin")
    async def admin_page():
        return FileResponse(STATIC / "admin.html")

    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
    return app


__all__ = ["create_app"]
