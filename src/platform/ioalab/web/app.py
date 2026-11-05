"""FastAPI 应用：用户端与管理员端 API、WebSocket 实时交易流、A2A 端点挂载、静态页面。"""
from __future__ import annotations

import asyncio
import json
import logging
from urllib.parse import urlsplit
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from a2c.errors import A2CError
from ..errors import LabError

from ..app import Platform
from ..payments import money
from ..storage import jdump, jload
from ..observation import event_view

log = logging.getLogger("ioalab.web")
STATIC = Path(__file__).parent / "static"
from .cookies import session_cookie_name, session_cookie_options


def create_app(platform: Platform) -> FastAPI:
    s = platform.s
    cookie = session_cookie_name(s.public_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            await platform.startup()
            yield
        finally:
            await platform.shutdown()

    app = FastAPI(title="九问IoA实验平台（Jiuwen IoA Lab）", lifespan=lifespan, docs_url="/api/docs" if not s.open else None, redoc_url=None)
    app.mount("/a2a", platform.router)

    class OriginCheck(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            if request.method in ("POST", "PUT", "DELETE") and request.url.path.startswith("/api/"):
                origin = request.headers.get("origin") or request.headers.get("referer") or ""
                host = request.headers.get("host", "")
                if origin and (urlsplit(origin).netloc != host or urlsplit(origin).scheme != urlsplit(s.public_url).scheme):
                    return JSONResponse({'error':LabError('forbidden','跨站请求被拒绝').public()},status_code=403)
            return await call_next(request)

    app.add_middleware(OriginCheck)
    from .api_v1 import build_router

    @app.exception_handler(A2CError)
    async def _a2c_error(request: Request, exc: A2CError):
        return JSONResponse({'error':LabError('invalid_input',str(exc)).public()},status_code=400)

    @app.exception_handler(LabError)
    async def lab_error(request,exc):
        return JSONResponse({'error':exc.public()},status_code=exc.status)

    @app.exception_handler(ValueError)
    @app.exception_handler(KeyError)
    @app.exception_handler(TypeError)
    async def invalid_request(request, exc):
        return JSONResponse({'error':LabError('invalid_input','Invalid request fields').public()},status_code=400)

    @app.exception_handler(RequestValidationError)
    async def invalid_structure(request,exc):
        return JSONResponse({'error':LabError('invalid_input','Invalid request structure').public()},status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request,exc):
        code=('unknown_result' if exc.status_code>=500 else 'forbidden' if exc.status_code in (401,403) else
              'not_found' if exc.status_code==404 else 'id_conflict' if exc.status_code==409 else 'invalid_input')
        recovery='reconcile_result' if exc.status_code>=500 else 'reauthenticate' if exc.status_code==401 else 'correct_input'
        message=exc.detail if isinstance(exc.detail,str) else 'Request rejected'
        return JSONResponse({'error':LabError(code,message,recovery=recovery).public()},status_code=exc.status_code,headers=exc.headers)

    @app.exception_handler(Exception)
    async def server_failure(request,exc):
        logging.getLogger(__name__).error('Unhandled platform request',exc_info=exc)
        # ServerErrorMiddleware reraises after sending; Uvicorn then closes HTTP/1.
        headers={'Connection':'close'} if request.scope.get('http_version') in ('1.0','1.1') else None
        return JSONResponse({'error':LabError('unknown_result','Platform operation needs verification',recovery='reconcile_result').public()},status_code=500,headers=headers)

    # ── 会话 ──────────────────────────────────────────────────────────
    def subject(request: Request) -> str | None:
        auth = request.headers.get("authorization", "")
        bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        return platform.auth.verify(request.cookies.get(cookie) or bearer or request.headers.get("x-ioalab-token"))

    def current_user(request: Request) -> str:
        sub = subject(request)
        if not sub or sub == "admin":
            raise HTTPException(401, "请先登录")
        return sub.removeprefix("assistant:")

    def require_admin(request: Request) -> str:
        operator=subject(request)
        if not platform.auth.is_admin(operator):
            raise HTTPException(401, "需要管理员登录")
        return operator

    def require_auditor(request:Request) -> str:
        operator=subject(request)
        if not platform.auth.is_auditor(operator):raise HTTPException(403,"需要消息正文审计权限")
        return operator

    def set_cookie(resp: Response, token: str) -> None:
        resp.set_cookie(cookie, token, **session_cookie_options(s.public_url), max_age=7 * 86400)

    def ip(request: Request) -> str:
        from .source import client_ip
        return client_ip(request,s)

    async def body(request: Request) -> dict:
        try:
            return await request.json()
        except Exception:  # noqa: BLE001
            return {}

    # ── 用户端 API ────────────────────────────────────────────────────
    @app.get("/api/config")
    async def config():
        credits=s.policy()['credits']
        return {"mode": s.mode, "registration": s.registration, "loginCredit": credits['login'], "initialCredit": credits['initial'],
                "repeatDailyCredit":credits['repeat_daily'],"creditDayTimezone":credits['day_timezone'],"currency": "虚拟币", "llm": platform.discovery_models.enabled,
                "publicUrl": s.public_url, "market": platform.market_id, "pay": platform.pay_id,
                "discovery": platform.discovery.last_mode or ("a2x-tree" if platform.discovery.llm_configured else "vector")}

    @app.get("/api/session")
    async def session(request: Request):
        """页面载入时探测会话（只加：避免未登录时 /api/me、/api/admin/stats 的 401 进浏览器控制台）。"""
        sub = subject(request)
        return {"role":None if not sub else ("admin" if platform.auth.is_admin(sub) else "user"),"id":None if sub in (None,"admin") else sub.removeprefix("assistant:")}

    @app.post("/api/auth/register")
    async def register(request: Request):
        """用户名密码注册（external）；自动建立首次登录并赠送登录虚拟币。"""
        b = await body(request)
        u = await platform._worker(platform.accounts.register,b.get('username',''),b.get('password',''),
                                    display_name=b.get('display_name',''),invite=b.get('invite',''),ip=ip(request),operation_id=b.get('operationId'))
        resp = JSONResponse(u)
        set_cookie(resp,u['token'])
        return resp

    @app.post("/api/auth/login")
    async def login(request: Request):
        b = await body(request)
        u = await platform._worker(platform.accounts.login,b.get('username',''),b.get('password',''),ip=ip(request),operation_id=b.get('operationId'))
        resp = JSONResponse(u)
        set_cookie(resp, u["token"])
        return resp

    @app.post("/api/auth/logout")
    async def logout():
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(cookie, **session_cookie_options(s.public_url))
        return resp

    @app.get("/api/me")
    async def me(uid: str = Depends(current_user)):
        return platform.account_views.me(uid)

    @app.get("/api/wallet")
    async def wallet(uid: str = Depends(current_user)):
        return {"balance": str(platform.wallets.balance(uid)), "log": platform.wallets.log(uid)}


    @app.get("/api/services/definitions")
    async def definitions():
        from ..seed import load_definitions
        if platform.s.seed_file is None:
            return []
        return [{"id": d["id"], "name": d["name"], "description": d["description"], "formation": d.get("formation", "preapproval"),
                 "runnable": bool(d.get("runnable")), "price": d.get("pricing", {}).get("price"), "category": d.get("category")}
                for d in load_definitions(platform.s.seed_file) if d.get("runnable")]



    @app.get("/api/needs")
    async def my_needs(uid: str = Depends(current_user)):
        return platform.account_views.needs(uid)


    @app.get("/api/orders")
    async def my_orders(uid: str = Depends(current_user)):
        return [row for row in platform.contract_views.list(uid,private=True) if row['owner_id']==uid]

    @app.get("/api/orders/{cd}")
    async def order_detail(cd: str, request: Request):
        sub=subject(request);uid=(sub or '').removeprefix('assistant:')
        if not sub:raise HTTPException(401,'请先登录')
        candidates=platform.contract_views.rows(None if platform.auth.is_admin(sub) else uid,digest=cd,limit=1)
        row=candidates[0] if candidates else None
        if not row:raise HTTPException(404,'合同不可用')
        private=uid in (row['owner_id'],row['service_owner_id'])
        return platform.contract_views.view(row,private=private)


    app.include_router(build_router(platform, current_user))
    from .arbitrations import build_router as arbitration_router
    app.include_router(arbitration_router(platform,require_admin))
    from .workbench import build_router as workbench_router
    from ..messaging.directory import build_router as messaging_router
    app.include_router(messaging_router(platform.interface_directory,current_user))
    from .agents import build_router as agents_router
    app.include_router(agents_router(platform,current_user))
    app.include_router(workbench_router(platform,current_user,require_admin,require_auditor))
    from .trial import build_router as trial_router
    app.include_router(trial_router(platform))

    # ── 管理员 API ────────────────────────────────────────────────────
    @app.post("/api/admin/login")
    async def admin_login(request: Request):
        b = await body(request)
        token = platform.auth.admin_login(b.get("password", ""), username=b.get('username',''), ip=ip(request))
        resp = JSONResponse({"ok": True})
        set_cookie(resp, token)
        return resp

    from .configuration import build_router as configuration_router
    app.include_router(configuration_router(platform,require_admin,cookie))

    @app.get("/api/admin/stats")
    async def stats(_: str = Depends(require_admin)):
        return platform.overview.stats()

    @app.get("/api/admin/events")
    async def events(_: str = Depends(require_admin), after: int = 0, limit: int = 300, run: str = ""):
        return platform.account_views.events(run,after,limit)

    @app.get("/api/admin/runs")
    async def runs(_: str = Depends(require_admin)):
        return platform.account_views.runs()

    @app.get("/api/admin/orders")
    async def admin_orders(_: str = Depends(require_admin)):
        return platform.contract_views.list()

    @app.get('/api/admin/delegation-intents')
    async def admin_intents(_:str=Depends(require_admin)):
        return platform.contract_views.intents()

    @app.get("/api/admin/users")
    async def users(_: str = Depends(require_admin)):
        return platform.account_views.admin_users()

    @app.get('/api/admin/publication-authorizations')
    async def publication_authorizations(_:str=Depends(require_admin)):
        return {'currentAuthorizations':platform.publication_authorizations.list()}

    @app.post('/api/admin/publication-authorizations')
    async def configure_publication_authorizations(request:Request,operator=Depends(require_admin)):
        return platform.publication_authorizations.configure(operator,await request.json())

    @app.put('/api/admin/users/{userId}/roles')
    async def user_roles(userId:str,request:Request,operator=Depends(require_admin)):
        body=await request.json()
        if not isinstance(body,dict) or set(body)!={'roles','expectedRoles'}:
            raise HTTPException(400,'提交roles和expectedRoles')
        result=platform.auth.set_roles(operator,userId,body['roles'],body['expectedRoles'])
        if result['changed']:
            platform.bus.emit('platform','identity.roles_changed',sender=operator,receiver=userId,
                detail={'targetId':userId,'trust':'verified'})
        return result

    @app.post("/api/admin/users")
    async def create_virtual_user(request: Request, _: str = Depends(require_admin)):
        b = await body(request)
        """管理员创建临时账号（kind virtual）：用户名默认 virtual_<序号>，密码随机并只在本次响应里返回。"""
        import secrets as _secrets
        n = int(b.get("count", 1))
        out = []
        for i in range(n):
            name = b.get("username") if n == 1 and b.get("username") else platform.auth.next_virtual_name()
            pw = b.get("password") or _secrets.token_urlsafe(8)
            u = await platform._worker(platform.accounts.create_account, name, pw, kind="virtual", display_name=b.get("display_name", ""),
                                        note=b.get("note", "管理员创建"))
            if b.get("credit"):
                platform.ledger_operations.adjust(u["id"], b["credit"], by="admin", note="创建时发放")
            out.append({**u, "password": pw})
        return out

    @app.post("/api/admin/wallet")
    async def adjust(request: Request, _: str = Depends(require_admin)):
        b = await body(request)
        return {"balance": platform.ledger_operations.adjust(b["user_id"], b["delta"], by="admin", note=b.get("note", "管理员调整"))}

    @app.post("/api/admin/invite")
    async def invite(_: str = Depends(require_admin)):
        return {"code": platform.auth.new_invite()}

    @app.get("/api/admin/registry")
    async def registry(_: str = Depends(require_admin)):
        rows=platform.service_views.list(include_unpublished=True)
        for row in rows:
            row['reputation']=platform.market.reputation(row['url']) if row['url'] else None
            if row['reputation']:row['reputation'].pop('signature',None)
            row['observation']='connected' if row['connected'] else 'not_connected'
        return {"taxonomy": platform.discovery.taxonomy_state(), "categories": platform.discovery.categories(), "services": rows}

    @app.post("/api/admin/search")
    async def admin_search(request: Request, _: str = Depends(require_admin)):
        b = await body(request)
        hits = await platform._worker(platform.discovery_operations.search, b.get("query", ""), int(b.get("top_k", 8)))
        return {"mode": platform.discovery.last_mode, "hits": [{"id": h["id"], "name": h["name"], "status": h["card"].get("status")} for h in hits]}


    @app.get("/api/admin/components")
    async def components(_: str = Depends(require_admin)):
        return await platform._worker(platform.overview.components)

    @app.get("/api/admin/traffic")
    async def traffic_get(_: str = Depends(require_admin)):
        return {"level":"external","enabled":False}




    @app.post("/api/admin/seed")
    async def seed(request: Request, _: str = Depends(require_admin)):
        from ..seed import seed_all
        b = await body(request)
        return await platform._worker(seed_all, platform, only=b.get("only"))

    @app.post("/api/admin/run")
    async def new_run(_: str = Depends(require_admin)):
        return {"run": platform.bus.new_run()}

    @app.get("/api/admin/replay/{run}")
    async def replay(run: str, _: str = Depends(require_admin)):
        if Path(run).name != run or run in (".", ".."):
            raise HTTPException(400)
        return platform.account_views.replay(run)

    @app.websocket("/ws/admin")
    async def ws_admin(ws: WebSocket):
        if not platform.auth.is_admin(platform.auth.verify(ws.cookies.get(cookie))):
            await ws.close(code=4401)
            return
        origin, host = ws.headers.get("origin", ""), ws.headers.get("host", "")
        if origin and (urlsplit(origin).netloc != host or urlsplit(origin).scheme != urlsplit(s.public_url).scheme):
            await ws.close(code=4403)
            return
        await ws.accept()
        q = platform.bus.subscribe()
        async def send_events():
            for ev in platform.bus.recent(100):
                await ws.send_text(json.dumps(event_view(ev), ensure_ascii=False))
            while True:
                ev = await q.get()
                await ws.send_text(json.dumps(event_view(ev), ensure_ascii=False))

        async def receive_disconnect():
            while (await ws.receive())["type"] != "websocket.disconnect":
                pass

        tasks = (asyncio.create_task(send_events()), asyncio.create_task(receive_disconnect()))
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                await task
        except (WebSocketDisconnect, RuntimeError, OSError):
            pass
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            platform.bus.unsubscribe(q)

    # ── 页面 ──────────────────────────────────────────────────────────
    from .frontend import build_router as frontend_router
    app.include_router(frontend_router(platform,subject,current_user))

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/admin")
    async def admin_page(request: Request):
        actor=subject(request)
        if not actor:
            from fastapi.responses import RedirectResponse
            return RedirectResponse('/login?next=%2Fadmin',status_code=303)
        if not platform.auth.is_admin(actor):raise HTTPException(403,'需要管理员权限')
        return FileResponse(STATIC / "admin.html")

    @app.get('/downloads/{bundle}')
    def access_skill_download(bundle:str):
        if bundle not in ('ioalab-assistant-skill.zip','ioalab-provider-skill.zip'):raise HTTPException(404,'Unknown public bundle')
        path=platform.s.bundles_dir/bundle
        if not path.is_file():raise HTTPException(503,'Product skill bundle is not built')
        return FileResponse(path,filename=bundle,media_type='application/zip')

    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
    return app


__all__ = ["create_app"]
