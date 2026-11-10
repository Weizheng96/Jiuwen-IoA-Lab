"""Owner-fenced proxy for fixed native UI targets; never exposes general RPC."""
import asyncio
from contextlib import asynccontextmanager
from importlib.resources import files
import secrets
import time
import threading
import ipaddress
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Request, WebSocket, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response
import websockets

from .coordinator import Denied


HOP = {'connection','keep-alive','proxy-authenticate','proxy-authorization','te','trailer','transfer-encoding','upgrade',
       'host','cookie','authorization','content-length','content-encoding','set-cookie','clear-site-data','origin'}


def create_app(coordinator, sandbox, *, secure=True, confirm_origin=None, formal_origin=None):
    confirm_origin = (confirm_origin or coordinator.confirm_audience or '').rstrip('/')
    native = urlsplit(coordinator.audience)
    confirm = urlsplit(confirm_origin)
    def origin_key(value):
        if (value.scheme!='https' or not value.hostname or value.path not in ('','/')
                or value.query or value.fragment or value.username or value.password):
            raise ValueError('Use an explicit HTTPS origin')
        return value.hostname.lower(),value.port or 443
    native_key,confirm_key=origin_key(native),origin_key(confirm)
    if native_key==confirm_key:raise ValueError('Trusted confirmation requires a different HTTPS origin')
    if native_key[0]==confirm_key[0]:
        try:ipaddress.ip_address(native_key[0])
        except ValueError:raise ValueError('Shared hostname is allowed only for an explicit IP and distinct HTTPS ports') from None
    coordinator.confirm_audience = confirm_origin
    origins = {native.netloc.lower():coordinator.audience,confirm.netloc.lower():confirm_origin}
    if formal_origin:
        formal = urlsplit(formal_origin)
        if origin_key(formal) in (native_key,confirm_key):
            raise ValueError('Formal, native and confirmation origins must be distinct')
        if (formal.scheme != 'https' or not formal.hostname or formal.path not in ('','/')
                or formal.query or formal.fragment or formal.username or formal.password):
            raise ValueError('Formal origin must be a fixed HTTPS origin')
    sockets = {}
    tasks = set()

    @asynccontextmanager
    async def lifespan(app):
        loop = asyncio.get_running_loop()
        def revoke(seat):
            for socket in tuple(sockets.get(seat,set())):
                loop.call_soon_threadsafe(lambda ws=socket: tasks.add(asyncio.create_task(ws.close(code=4403))))
        coordinator.listeners.add(revoke)
        # A restarted gateway cannot assume old browser or execution ownership.
        for row in coordinator.store.seats():
            if row['state'] in ('ready','leased','reconnect_grace'):
                coordinator.revoke(row['id'],'gateway_restarted')
        stopping = threading.Event()
        def worker():
            while not stopping.is_set():
                coordinator.reconcile(sandbox)
                stopping.wait(1)
        thread = threading.Thread(target=worker,daemon=False)
        thread.start()
        try:
            yield
        finally:
            coordinator.begin_shutdown()
            stopping.set()
            await asyncio.to_thread(thread.join)
            await asyncio.gather(*tasks,return_exceptions=True)
            try:await asyncio.to_thread(coordinator.shutdown,sandbox)
            finally:coordinator.listeners.discard(revoke)

    app = FastAPI(lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
    def cookie_name(target):
        purpose='confirm' if target==confirm_origin else 'native'
        return ('__Host-ioa_trial_' if secure else 'ioa_trial_dev_')+purpose

    @app.get('/health')
    def health():
        rows=coordinator.store.seats()
        ready=(not coordinator.stopping.is_set() and len(rows)==coordinator.store.capacity and
               all(row['state'] in ('ready','leased','reconnect_grace') and row['epoch'] for row in rows))
        return JSONResponse({'ready':bool(ready),'capacity':coordinator.store.capacity},status_code=200 if ready else 503)

    @app.exception_handler(Denied)
    async def denied(request,exc):
        return JSONResponse({'error':str(exc)},status_code=403,headers={'Cache-Control':'no-store'})

    @app.exception_handler(httpx.HTTPStatusError)
    async def upstream_rejection(request,exc):
        code = exc.response.status_code
        if code in (400,403,409):
            return JSONResponse({'error':{'code':'confirmation_rejected','message':'原确认请求被拒绝，请核对原对象与答复'}},status_code=code)
        return JSONResponse({'error':{'code':'unknown_result','message':'原请求结果待核对，请勿更换操作标识重试'}},status_code=503)

    def audience(request):
        value = origins.get(request.headers.get('host','').lower())
        if value is None:
            raise Denied('Unknown trial hostname')
        return value

    @app.middleware('http')
    async def separate_origins(request,call_next):
        try:
            target = audience(request)
            if target == confirm_origin:
                if request.url.path not in ('/entry','/trial','/trial/api/entry','/trial/confirm','/trial/user/api','/trial/user/decision'):
                    raise Denied('Native content is unavailable on the trusted confirmation origin')
            elif request.url.path in ('/trial/confirm','/trial/user/api','/trial/user/decision','/trial/api/confirmations'):
                raise Denied('Open the trusted confirmation origin from the formal platform')
        except Denied as exc:
            return JSONResponse({'error':str(exc)},status_code=403,headers={'Cache-Control':'no-store'})
        return await call_next(request)

    def session(request, *, write=False):
        target = audience(request)
        record = coordinator.session(request.cookies.get(cookie_name(target),''),audience=target)
        if write and (request.headers.get('origin') != target
                or not secrets.compare_digest(request.headers.get('x-trial-csrf',request.headers.get('x-ioa-csrf','')),record['csrf'])):
            raise Denied('Real same-origin browser action required')
        return record

    def access(request, *, touch=False):
        actor = session(request)
        if actor['purpose'] == 'confirm':
            lease = coordinator.access(actor['owner'],actor['epoch'],touch=touch)
            if lease['id'] != actor['seat']:
                raise Denied('Confirmation seat changed')
            return lease
        status = coordinator.status(actor['owner'])
        return coordinator.access(actor['owner'],status.get('epoch'),touch=touch)

    async def read_object(request, maximum):
        import json
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > maximum:
                raise Denied('User request exceeds the bounded input size')
        try:
            value = json.loads(raw)
        except (ValueError,UnicodeError,RecursionError):
            raise Denied('User request must contain a JSON object') from None
        if not isinstance(value,dict):
            raise Denied('User request must contain a JSON object')
        return value

    @app.get('/entry')
    @app.get('/trial')
    async def page(request:Request):
        if audience(request) == confirm_origin:
            return HTMLResponse("""<!doctype html><html lang="zh"><meta charset="utf-8"><title>试用可信确认</title>
<p id="state">正在核对确认准入…</p><script>(async()=>{try{let ticket=location.hash.slice(1);history.replaceState(null,'','/entry');if(!ticket)throw Error('请从正式平台打开确认入口');let r=await fetch('/trial/api/entry',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ticket})});let v=await r.json();if(!r.ok)throw Error(v.error||'确认准入失败');location.replace('/trial/confirm')}catch(e){document.querySelector('#state').textContent=e.message}})();</script></html>""",
                headers={'Cache-Control':'no-store','Content-Security-Policy':"default-src 'self'; script-src 'unsafe-inline'; frame-ancestors 'none'"})
        content = files('ioalab_trial').joinpath('trial.html').read_text('utf-8')
        if formal_origin:
            from html import escape
            content = content.replace('请回正式平台点击', '请<a href="'+escape(formal_origin.rstrip('/')+'/trial',quote=True)+'" target="_blank" rel="noopener">回正式平台</a>点击')
        return HTMLResponse(content,
                            headers={'Cache-Control':'no-store','Content-Security-Policy':"default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; frame-src 'self'; frame-ancestors 'none'"})

    @app.post('/trial/api/entry')
    async def entry(request:Request):
        if request.headers.get('origin') != audience(request):
            raise Denied('Same-origin entry required')
        value = await read_object(request,8192)
        if set(value) != {'ticket'} or not isinstance(value['ticket'],str):
            raise Denied('Invalid admission body')
        token, csrf = coordinator.consume(value['ticket'],audience=audience(request))
        response = JSONResponse({'csrf':csrf})
        response.set_cookie(cookie_name(audience(request)),token,httponly=True,secure=secure,samesite='strict',path='/',max_age=coordinator.lease + coordinator.grace)
        return response

    @app.get('/trial/api/status')
    async def status(request:Request):
        actor = session(request)
        value = coordinator.status(actor['owner'])
        if value['state'] in ('leased','reconnect_grace'):
            coordinator.access(actor['owner'],value['epoch'],touch=True)
        return {**value,'csrf':actor['csrf']}

    @app.post('/trial/api/join')
    async def join(request:Request):
        return coordinator.enqueue(session(request,write=True)['owner'])

    @app.post('/trial/api/end')
    async def end(request:Request):
        coordinator.end(session(request,write=True)['owner'])
        return {'state':'ended'}

    @app.get('/trial/confirm')
    async def confirmation_page(request:Request):
        access(request)
        # Reuse the SDK's full renderer, including closed questions and unknown
        # decision recovery. Only its fixed transport paths are adapted.
        page = files('ioalab_sdk').joinpath('runtime/web/user.html').read_text('utf-8')
        start = "const token=location.hash.slice(1);history.replaceState(null,'','/user');if(token)await api('/user/session',{token});"
        if page.count(start) != 1:
            raise HTTPException(503,'SDK confirmation page is incompatible')
        page = page.replace(start,"history.replaceState(null,'','/trial/confirm');")
        page = page.replace("api('/user/", "api('/trial/user/")
        page = page.replace('本机用户确认','试用用户确认')
        return HTMLResponse(page,headers={'Cache-Control':'no-store','Content-Security-Policy':"default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; frame-ancestors 'none'"})

    @app.get('/trial/user/api')
    async def user_pending(request:Request):
        actor = session(request)
        lease = access(request)
        def authorized():
            session(request)
            coordinator.access(actor['owner'],lease['epoch'])
        result = await asyncio.to_thread(sandbox.control,lease['id'],lease['epoch'],'confirmations',
                                       {'generation':lease['epoch'],'method':'list'},before_send=authorized)
        session(request)
        coordinator.access(actor['owner'],lease['epoch'])
        return {'csrf':actor['csrf'],'confirmations':result['data']}

    @app.post('/trial/user/decision')
    async def user_decision(request:Request):
        actor = session(request,write=True)
        lease = access(request)
        data = await read_object(request,128*1024)
        if not isinstance(data,dict) or set(data) - {'reference','bindingDigest','decision','answers'}:
            raise Denied('Invalid SDK user decision')
        data.update(method='decide',generation=lease['epoch'],eventId=secrets.token_hex(16))
        def authorized():
            session(request,write=True)
            coordinator.access(actor['owner'],lease['epoch'])
        def call():
            authorized()
            return sandbox.control(lease['id'],lease['epoch'],'confirmations',data,before_send=authorized)
        result = await asyncio.to_thread(call)
        return result['data']

    @app.api_route('/{path:path}',methods=['GET','HEAD','POST','PUT','PATCH','DELETE'])
    async def proxy(path:str,request:Request):
        lease = access(request,touch=True)
        from urllib.parse import unquote
        decoded = unquote(path)
        if (decoded.startswith(('/','\\')) or '\\' in decoded
                or any(part in ('.','..') for part in decoded.split('/'))
                or any(ord(char) < 32 for char in decoded) or '://' in decoded):
            raise Denied('Invalid native path')
        if path.startswith('trial/') or path == 'entry':
            raise HTTPException(404)
        if request.method not in ('GET','HEAD') and request.headers.get('origin') != coordinator.audience:
            raise Denied('Same-origin native action required')
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 16 * 1024 * 1024:
                raise HTTPException(413)
        # Fixed upstream, normalized relative path; absolute URL and Host cannot override it.
        origin = await asyncio.to_thread(sandbox.ui,lease['id'],lease['epoch'])
        session(request)
        coordinator.access(lease['owner'],lease['epoch'])
        target = origin + '/' + path
        headers = {key:value for key,value in request.headers.items() if key.lower() not in HOP}
        headers['origin'] = origin
        async with httpx.AsyncClient(follow_redirects=False,timeout=30,trust_env=False) as client:
            async with client.stream(request.method,target,params=request.query_params,content=bytes(body),headers=headers) as stream:
                content = bytearray()
                async for chunk in stream.aiter_bytes():
                    session(request)
                    coordinator.access(lease['owner'],lease['epoch'])
                    content.extend(chunk)
                    if len(content) > 64 * 1024 * 1024:
                        raise HTTPException(413)
                response = httpx.Response(stream.status_code,headers=stream.headers,content=bytes(content))
        # Buffer before sending so revocation during a slow download discards all bytes.
        session(request)
        coordinator.access(lease['owner'],lease['epoch'])
        outgoing = {key:value for key,value in response.headers.items() if key.lower() not in HOP and key.lower() != 'location'}
        if 'location' in response.headers:
            from urllib.parse import urlsplit
            location = response.headers['location']
            parsed = urlsplit(location)
            if parsed.scheme or parsed.netloc or not location.startswith('/') or location.startswith('//'):
                raise Denied('Unexpected upstream redirect')
            outgoing['location'] = location
        outgoing['cache-control'] = 'no-store'
        return Response(response.content,response.status_code,headers=outgoing)

    @app.websocket('/{path:path}')
    async def websocket(path:str,websocket:WebSocket):
        try:
            if audience(websocket) != coordinator.audience or websocket.headers.get('origin') != coordinator.audience:
                raise Denied('Same-origin websocket required')
            lease = access(websocket,touch=True)
            origin = await asyncio.to_thread(sandbox.ui,lease['id'],lease['epoch'])
            session(websocket)
            coordinator.access(lease['owner'],lease['epoch'])
            target = origin.replace('http:','ws:',1) + '/' + path
            if websocket.url.query:
                target += '?' + websocket.url.query
            await websocket.accept()
            sockets.setdefault(lease['id'],set()).add(websocket)
            session(websocket)
            coordinator.access(lease['owner'],lease['epoch'])
            async with websockets.connect(target,origin=origin,max_size=16*1024*1024) as upstream:
                async def outbound():
                    while True:
                        message = await websocket.receive()
                        session(websocket)
                        coordinator.access(lease['owner'],lease['epoch'],touch=True)
                        if message['type'] == 'websocket.disconnect':
                            break
                        await upstream.send(message.get('text') if message.get('text') is not None else message['bytes'])
                async def inbound():
                    async for message in upstream:
                        session(websocket)
                        coordinator.access(lease['owner'],lease['epoch'])
                        if isinstance(message,str):
                            await websocket.send_text(message)
                        else:
                            await websocket.send_bytes(message)
                async def fence():
                    while True:
                        await asyncio.sleep(.5)
                        session(websocket)
                        coordinator.access(lease['owner'],lease['epoch'])
                running = [asyncio.create_task(fn()) for fn in (outbound,inbound,fence)]
                try:
                    await asyncio.wait(running,return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for task in running:
                        task.cancel()
                    await asyncio.gather(*running,return_exceptions=True)
        except (Denied,websockets.WebSocketException,OSError):
            pass
        finally:
            for group in sockets.values():
                group.discard(websocket)
            try:
                await websocket.close(code=4403)
            except RuntimeError:
                pass
    return app
