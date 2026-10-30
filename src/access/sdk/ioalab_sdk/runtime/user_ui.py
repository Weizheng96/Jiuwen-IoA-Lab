"""A user-only browser channel; its authority is never returned to model RPC."""
from http.cookies import SimpleCookie
import hashlib
import hmac
from importlib.resources import files
import json
import secrets
import threading
import time
from urllib.parse import urlsplit
import uuid
import webbrowser

from .protocol import RuntimeFault


class UserUi:
    def __init__(self,confirmations,*,clock=time.time):
        self.confirmations,self.clock=confirmations,clock
        self._authority=confirmations.user_channel()
        self._entries={};self._sessions={};self._lock=threading.RLock()
        self.origin=None

    def entry_url(self):
        if not self.origin:raise RuntimeFault('not_ready','Local user page is not listening')
        token=secrets.token_urlsafe(32)
        with self._lock:
            now=self.clock()
            self._entries={key:until for key,until in self._entries.items() if until>now}
            self._entries[hashlib.sha256(token.encode()).hexdigest()]=now+60
        return self.origin+'/user#'+token

    def open(self):
        return bool(webbrowser.open(self.entry_url()))

    def _session(self,request):
        cookie=SimpleCookie()
        try:cookie.load(request.headers.get('Cookie',''))
        except Exception:raise RuntimeFault('forbidden','Invalid local browser session') from None
        key=cookie['ioa_user'].value if 'ioa_user' in cookie else ''
        with self._lock:record=self._sessions.get(key)
        if not record or record['expires']<=self.clock():raise RuntimeFault('forbidden','Open the local user page again')
        return record

    @staticmethod
    def _body(request):
        try:size=int(request.headers.get('Content-Length',''))
        except ValueError:raise RuntimeFault('invalid_input','JSON body length is required') from None
        if not 0<size<=128*1024 or request.headers.get('Transfer-Encoding'):
            raise RuntimeFault('invalid_input','Invalid user request size')
        if request.headers.get('Content-Type','').split(';')[0]!='application/json':raise RuntimeFault('invalid_input','Use JSON')
        try:
            raw=request.rfile.read(size)
            request._user_body_read=True
            body=json.loads(raw)
        except (ValueError,RecursionError):raise RuntimeFault('invalid_input','Invalid user JSON') from None
        if not isinstance(body,dict):raise RuntimeFault('invalid_input','User request must be an object')
        return body

    def handle(self,request):
        path=urlsplit(request.path)
        if not path.path.startswith('/user'):return False
        try:
            origin=self.origin
            if request.headers.get('Host')!=urlsplit(origin).netloc or path.query:
                raise RuntimeFault('forbidden','User page Host must match the local listener')
            supplied=request.headers.get('Origin')
            if supplied is not None and supplied!=origin:raise RuntimeFault('forbidden','User actions must come from the local page')
            if request.command=='GET' and path.path=='/user':
                page=files('ioalab_sdk').joinpath('runtime/web/user.html').read_bytes()
                request.send_response(200)
                request.send_header('Content-Type','text/html; charset=utf-8')
                request.send_header('Content-Length',str(len(page)))
                request.send_header('Cache-Control','no-store')
                request.send_header('Content-Security-Policy',"default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'")
                request.end_headers();request.wfile.write(page)
                return True
            if request.command=='POST' and path.path=='/user/session':
                if supplied!=origin:raise RuntimeFault('forbidden','Entry exchange requires same Origin')
                body=self._body(request)
                if set(body)!={'token'} or not isinstance(body['token'],str):raise RuntimeFault('forbidden','Invalid one-time user entry')
                digest=hashlib.sha256(body['token'].encode()).hexdigest()
                with self._lock:
                    expires=self._entries.pop(digest,0)
                    if expires<=self.clock():raise RuntimeFault('forbidden','User entry expired or was already used')
                    key=secrets.token_urlsafe(32)
                    self._sessions={key:item for key,item in self._sessions.items() if item['expires']>self.clock()}
                    record={'csrf':secrets.token_urlsafe(32),'expires':self.clock()+14400,'id':str(uuid.uuid4())}
                    self._sessions[key]=record
                request.reply(200,{'csrf':record['csrf']},headers={'Set-Cookie':'ioa_user='+key+'; HttpOnly; SameSite=Strict; Path=/user; Max-Age=14400'})
                return True
            session=self._session(request)
            if request.command=='GET' and path.path=='/user/api':
                request.reply(200,{'csrf':session['csrf'],'confirmations':self.confirmations.list()})
                return True
            if request.command=='POST' and path.path=='/user/decision':
                if supplied!=origin or not hmac.compare_digest(request.headers.get('X-Ioa-Csrf','').encode(),session['csrf'].encode()):
                    raise RuntimeFault('forbidden','A real same-origin user decision is required')
                body=self._body(request)
                if set(body)-{'reference','bindingDigest','decision','answers'}:raise RuntimeFault('invalid_input','Unknown user decision fields')
                result=self.confirmations.decide(body.get('reference'),body.get('bindingDigest'),body.get('decision'),
                    authority=self._authority,source='local_user_ui:'+session['id']+':'+str(uuid.uuid4()),answers=body.get('answers'))
                request.reply(200,result)
                return True
            raise RuntimeFault('forbidden','Unknown local user operation')
        except RuntimeFault as exc:
            if request.command=='POST' and not getattr(request,'_user_body_read',False):
                try:
                    size=int(request.headers.get('Content-Length','0'))
                    if 0<size<=128*1024+1 and not request.headers.get('Transfer-Encoding'):
                        request.connection.settimeout(.25)
                        request.rfile.read(size)
                except (ValueError,OSError):pass
            request.reply(403 if exc.code=='forbidden' else 409 if exc.code in ('expired','id_conflict') else 400,
                          {'error':{'code':exc.code,'message':str(exc),'recovery':exc.recovery}})
            return True
