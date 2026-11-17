"""A user-only browser channel; its authority is never returned to model RPC."""
from http.cookies import SimpleCookie
import hashlib
import hmac
from importlib.resources import files
import json
import secrets
import subprocess
import threading
import time
from urllib.parse import urlsplit,parse_qs
import uuid
import webbrowser

from .protocol import RuntimeFault, instant
from .confirmation_view import present


class UserUi:
    page_name='user.html'

    def snapshot(self,*,view='pending',cursor=None,reference=None):
        if reference is not None:
            if not isinstance(reference,str) or not 1<=len(reference)<=256:
                raise RuntimeFault('invalid_input','Original confirmation reference required')
            return {'confirmation':present(self.confirmations.get(reference,detail=True))}
        result=self.confirmations.page(view=view,cursor=cursor)
        return {**result,'confirmations':[present(item) for item in result['confirmations']]}

    def decision(self,body,session):
        if set(body)-{'reference','bindingDigest','decision','answers'}:raise RuntimeFault('invalid_input','Unknown user decision fields')
        return self.confirmations.decide(body.get('reference'),body.get('bindingDigest'),body.get('decision'),
            authority=self._authority,source='local_user_ui:'+session['id']+':'+str(uuid.uuid4()),answers=body.get('answers'))

    def __init__(self,confirmations=None,*,browser=None,clock=time.time,forms=None,onboarding=None,open_setup=None):
        self.confirmations,self.clock,self.browser=confirmations,clock,browser
        self.forms=forms
        self.onboarding=onboarding
        self.open_setup=open_setup
        self._authority=confirmations.user_channel() if confirmations else None
        self._confirmation_apps={};self._app_sessions={};self._entries={};self._sessions={};self._lock=threading.RLock()
        self.origin=None

    def confirmation_app_entry(self, reference=None):
        """Mint a single-original-confirmation capability for a trusted App renderer."""
        if not self.forms or not self.confirmations or not self.origin:
            raise RuntimeFault('not_ready', 'Confirmation presentation is unavailable')
        result = {'origin': self.origin}
        if reference is None:
            return result
        form = self.forms.get(reference)
        ref = ((form.get('result') or {}).get('data') or {}).get('confirmationRef')
        if not form['kind'].startswith('delegation.') or not ref:
            return result
        item = self.confirmations.get(ref, detail=True)
        if (not item['stage'].startswith('delegation.')
                or item['kind'] == 'question' and item['stage'] != 'delegation.answer_step'):
            return result
        result.update(formReference=reference, revision=form['revision'], reference=ref,
                      bindingDigest=item['bindingDigest'], status=item['status'], expiresAt=item['expiresAt'])
        if item['status'] != 'pending' or form['phase'] != 'waiting':
            return result
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._confirmation_apps = {key: value for key, value in self._confirmation_apps.items() if value['expires'] > self.clock()}
            self._confirmation_apps[hashlib.sha256(token.encode()).hexdigest()] = {
                'id': str(uuid.uuid4()), 'form': reference, 'revision': form['revision'],
                'reference': ref, 'bindingDigest': item['bindingDigest'],
                # Presentation access may outlive the business deadline to read its
                # expired state. Confirmations still checks that deadline on every decision.
                'expires': self.clock() + 900}
        return {**result, 'token': token, 'formReference': reference, 'revision': form['revision'],
                'reference': ref, 'bindingDigest': item['bindingDigest']}

    def _confirmation_app(self, request, path):
        headers = {'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
                   'Access-Control-Allow-Headers': 'Authorization, Content-Type', 'Access-Control-Allow-Private-Network': 'true'}
        try:
            if path not in ('/user/confirmation-app/status', '/user/confirmation-app/decision'):
                raise RuntimeFault('forbidden', 'Unknown confirmation App operation')
            if request.command == 'OPTIONS':
                request.reply(200, {}, headers=headers)
                return True
            auth = request.headers.get('Authorization', '')
            with self._lock:
                scope = self._confirmation_apps.get(hashlib.sha256(auth.removeprefix('Bearer ').encode()).hexdigest())
            if not auth.startswith('Bearer ') or not scope or scope['expires'] <= self.clock():
                raise RuntimeFault('forbidden', '内嵌连接已失效，请重新连接原确认。此提示不代表业务确认已过期。',
                                   'refresh_confirmation_entry')
            item = self.confirmations.get(scope['reference'], detail=True)
            if item['bindingDigest'] != scope['bindingDigest']:
                raise RuntimeFault('forbidden', 'Original confirmation changed')
            if request.command == 'GET' and path.endswith('/status'):
                value = present(item)
            elif request.command == 'POST' and path.endswith('/decision'):
                body = self._body(request)
                expected = {'reference', 'bindingDigest', 'decision'} | ({'answers'} if item['kind'] == 'question' else set())
                if (set(body) != expected
                        or body.get('reference') != scope['reference'] or body.get('bindingDigest') != scope['bindingDigest']):
                    raise RuntimeFault('forbidden', 'Decision must match this displayed original confirmation')
                if item['status'] == 'pending':
                    form = self.forms.get(scope['form'])
                    ref = ((form.get('result') or {}).get('data') or {}).get('confirmationRef')
                    if form['phase'] != 'waiting' or form['revision'] != scope['revision'] or ref != scope['reference']:
                        raise RuntimeFault('forbidden', '原表单确认已变化，请刷新核对，不可复用旧确认卡片。')
                value = self.decision(body, scope)
            else:
                raise RuntimeFault('forbidden', 'Unknown confirmation App operation')
            request.reply(200, value, headers=headers)
        except RuntimeFault as exc:
            request.reply(403 if exc.code == 'forbidden' else 409,
                          {'error': {'code': exc.code, 'message': str(exc), 'recovery': exc.recovery}}, headers=headers)
        return True

    def account_app_entry(self):
        """Account-only capability, delivered exclusively to the App, never model content."""
        if not self.onboarding or not self.origin:
            raise RuntimeFault('not_ready', 'Account page is not listening')
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._app_sessions = {key: value for key, value in self._app_sessions.items() if value > self.clock()}
            self._app_sessions[hashlib.sha256(token.encode()).hexdigest()] = self.clock() + 900
        return {'origin': self.origin, 'token': token}

    def _account_app(self, request, path):
        # Cross-origin access is limited to this capability-authenticated account surface.
        # No cookies, general IPC, device management or business approval are accepted.
        headers = {'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
                   'Access-Control-Allow-Headers': 'Authorization, Content-Type', 'Access-Control-Allow-Private-Network': 'true'}
        try:
            if not self.onboarding or path not in ('/user/app/api', '/user/app/setup', '/user/app/credentials'):
                raise RuntimeFault('forbidden', 'Unknown account App operation')
            if request.command == 'OPTIONS':
                request.reply(200, {}, headers=headers)
                return True
            auth = request.headers.get('Authorization', '')
            digest = hashlib.sha256(auth.removeprefix('Bearer ').encode()).hexdigest()
            with self._lock:
                expires = self._app_sessions.get(digest, 0)
            if not auth.startswith('Bearer ') or expires <= self.clock():
                raise RuntimeFault('forbidden', '账号界面已过期，请在聊天中重新打开接入界面。')
            if request.command == 'GET' and path == '/user/app/api':
                value = self.onboarding.status()
            elif request.command == 'POST' and path != '/user/app/api':
                body = self._body(request)
                operation = self.onboarding.submit if path.endswith('/setup') else self.onboarding.credentials
                value = operation(body)
            else:
                raise RuntimeFault('forbidden', 'Unknown account App operation')
            request.reply(200, value, headers=headers)
        except RuntimeFault as exc:
            request.reply(403 if exc.code == 'forbidden' else 400,
                          {'error': {'code': exc.code, 'message': str(exc), 'recovery': exc.recovery}}, headers=headers)
        return True

    def entry_url(self,form_reference=None):
        if not self.origin:raise RuntimeFault('not_ready','Local user page is not listening')
        token=secrets.token_urlsafe(32)
        with self._lock:
            now=self.clock()
            self._entries={key:until for key,until in self._entries.items() if until>now}
            self._entries[hashlib.sha256(token.encode()).hexdigest()]=now+60
        if form_reference is not None:
            if self.forms is None:raise RuntimeFault('not_ready','Forms are not configured')
            self.forms.get(form_reference)
        path='/user' if form_reference is None else '/user/forms/'+form_reference
        return self.origin+path+'#'+token

    def open(self,form_reference=None):
        url=self.entry_url(form_reference)
        try:
            if self.browser:
                subprocess.Popen([self.browser,url],shell=False,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,close_fds=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                return True
            if not webbrowser.open(url):
                raise RuntimeFault('not_ready','The local user browser could not be opened','configure_browser')
            return True
        except (OSError,webbrowser.Error):
            # OS errors can contain the one-time URL; never return them over RPC.
            raise RuntimeFault('not_ready','The local user browser could not be opened','configure_browser') from None

    def _cookie_name(self):
        # Browser cookies do not isolate ports; this origin comes from our listener.
        return 'ioa_user_'+hashlib.sha256(self.origin.encode()).hexdigest()

    def _session(self,request):
        cookie=SimpleCookie()
        try:cookie.load(request.headers.get('Cookie',''))
        except Exception:raise RuntimeFault('forbidden','Invalid local browser session') from None
        name=self._cookie_name()
        key=cookie[name].value if name in cookie else ''
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
            if request.headers.get('Host')!=urlsplit(origin).netloc or (path.query and not (request.command=='GET' and path.path=='/user/api')):
                raise RuntimeFault('forbidden','User page Host must match the local listener')
            if path.path.startswith('/user/confirmation-app/'):
                return self._confirmation_app(request, path.path)
            if path.path.startswith('/user/app/'):
                return self._account_app(request, path.path)
            supplied=request.headers.get('Origin')
            if supplied is not None and supplied!=origin:raise RuntimeFault('forbidden','User actions must come from the local page')
            form_page_request=path.path.startswith('/user/forms/') and path.path!='/user/forms/api'
            if request.command=='GET' and (path.path=='/user' or form_page_request):
                if form_page_request:
                    from .form_view import form_page
                    page=form_page('browser').encode('utf-8')
                else:page=files('ioalab_sdk').joinpath('runtime/web/identity/setup.html' if self.onboarding else 'runtime/web/user.html').read_bytes()
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
                request.reply(200,{'csrf':record['csrf']},headers={'Set-Cookie':self._cookie_name()+'='+key+'; HttpOnly; SameSite=Strict; Path=/user; Max-Age=14400'})
                return True
            session=self._session(request)
            if request.command=='POST' and path.path=='/user/setup/open' and self.open_setup:
                if supplied!=origin or not hmac.compare_digest(request.headers.get('X-Ioa-Csrf','').encode(),session['csrf'].encode()):
                    raise RuntimeFault('forbidden','A same-origin user session is required')
                if self._body(request):raise RuntimeFault('invalid_input','Setup opening takes no credential fields')
                request.reply(200,self.open_setup())
                return True
            if self.onboarding:
                if request.command=='GET' and path.path=='/user/api':
                    request.reply(200,{'csrf':session['csrf'],**self.onboarding.status(),'client':self.onboarding.configuration()})
                    return True
                if request.command=='POST' and path.path in ('/user/setup','/user/configuration','/user/credentials','/user/devices'):
                    if supplied!=origin or not hmac.compare_digest(request.headers.get('X-Ioa-Csrf','').encode(),session['csrf'].encode()):
                        raise RuntimeFault('forbidden','A same-origin account session is required')
                    operation={'/user/setup':self.onboarding.submit,'/user/configuration':self.onboarding.control,
                               '/user/credentials':self.onboarding.credentials,'/user/devices':self.onboarding.devices}[path.path]
                    request.reply(200,operation(self._body(request)))
                    return True
                raise RuntimeFault('forbidden','Account setup does not grant business confirmation')
            if request.command=='POST' and path.path=='/user/forms/api':
                if supplied!=origin or not hmac.compare_digest(request.headers.get('X-Ioa-Csrf','').encode(),session['csrf'].encode()):
                    raise RuntimeFault('forbidden','A same-origin form session is required')
                if self.forms is None:raise RuntimeFault('not_ready','Forms are not configured')
                body=self._body(request)
                action=body.pop('action',None)
                shapes={'list':set(),'get':{'reference'},'update':{'reference','revision','values'},
                        'recover':{'reference','revision'},'preview':{'reference','revision'},'submit':{'reference','revision'}}
                if not isinstance(action,str) or action not in shapes or set(body)!=shapes[action]:
                    raise RuntimeFault('invalid_input','Use a documented form action')
                request.reply(200,getattr(self.forms,action)(**body))
                return True
            if request.command=='GET' and path.path=='/user/api':
                try:
                    query=parse_qs(path.query,strict_parsing=True,max_num_fields=3,keep_blank_values=True)
                    if (set(query)-{'view','cursor','reference'} or any(len(v)!=1 or not v[0] for v in query.values())
                        or ('reference' in query and len(query)!=1)):raise ValueError()
                except ValueError:
                    raise RuntimeFault('invalid_input','Invalid confirmation page query') from None
                request.reply(200,{'csrf':session['csrf'],**self.snapshot(**{key:value[0] for key,value in query.items()})})
                return True
            if request.command=='POST' and path.path=='/user/decision':
                if supplied!=origin or not hmac.compare_digest(request.headers.get('X-Ioa-Csrf','').encode(),session['csrf'].encode()):
                    raise RuntimeFault('forbidden','A real same-origin user decision is required')
                body=self._body(request)
                result=self.decision(body,session)
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
