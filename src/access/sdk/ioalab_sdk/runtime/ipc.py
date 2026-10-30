"""Restricted loopback RPC. CLI reads one descriptor, never identity databases."""
import hmac
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import threading
from urllib.parse import urlsplit
import uuid

import httpx

from .files import private_json,read_json
from .protocol import RuntimeFault,encode


LIMIT=128*1024


def public_error(exc):
    return {'code':exc.code,'message':str(exc),'recovery':exc.recovery}


class IpcServer:
    def __init__(self,home,runtime_id,dispatch,*,user_ui=None):
        self.home,self.dispatch=Path(home),dispatch
        self.token=secrets.token_urlsafe(32)
        self.boot_id=str(uuid.uuid4())
        self.runtime_id=runtime_id
        self.user_ui=user_ui
        self.server=ThreadingHTTPServer(('127.0.0.1',0),self._handler())
        self.server.daemon_threads=False
        self.thread=None
        self.descriptor={'schemaVersion':1,'url':f'http://127.0.0.1:{self.server.server_port}',
                         'token':self.token,'pid':os.getpid(),'runtimeId':runtime_id,'bootId':self.boot_id}
        if user_ui:user_ui.origin=self.descriptor['url']

    def _handler(self):
        owner=self
        class Handler(BaseHTTPRequestHandler):
            server_version='IoALabLocal'
            sys_version=''

            def log_message(self,*args):pass

            def setup(self):
                super().setup()
                self.connection.settimeout(3)

            def reply(self,status,value,*,headers=None):
                body=encode(value).encode()
                self.send_response(status)
                self.send_header('Content-Type','application/json; charset=utf-8')
                self.send_header('Content-Length',str(len(body)))
                self.send_header('Cache-Control','no-store')
                self.send_header('X-Ioa-Instance',owner.boot_id)
                for name,value in (headers or {}).items():self.send_header(name,value)
                self.end_headers()
                try:self.wfile.write(body)
                except (BrokenPipeError,ConnectionResetError):pass

            def do_POST(self):
                request=None
                consumed=False
                try:
                    if owner.user_ui and owner.user_ui.handle(self):return
                    if self.path!='/v1/call' or self.headers.get('Origin') is not None:
                        raise RuntimeFault('forbidden','Local RPC does not accept browser or cross-origin calls')
                    if self.headers.get('Host')!=f'127.0.0.1:{owner.server.server_port}':
                        raise RuntimeFault('forbidden','Local RPC Host does not match its listener')
                    auth=self.headers.get('Authorization','')
                    if not hmac.compare_digest(auth.encode(),('Bearer '+owner.token).encode()) or self.headers.get('X-Ioa-Instance')!=owner.boot_id:
                        raise RuntimeFault('authenticate','Local access credentials are unavailable','restart_client')
                    if self.headers.get('Transfer-Encoding') or self.headers.get('Content-Type','').split(';')[0]!='application/json':
                        raise RuntimeFault('invalid_input','A length-bounded JSON request is required')
                    try:size=int(self.headers.get('Content-Length',''))
                    except ValueError:raise RuntimeFault('invalid_input','Content-Length is required') from None
                    if size>LIMIT:
                        # Bounded discard avoids a Windows TCP reset swallowing
                        # the rejection for an otherwise normal, slightly large body.
                        self.rfile.read(min(size,LIMIT+1))
                        consumed=True
                        raise RuntimeFault('invalid_input','Local request exceeds 128 KiB')
                    if size<=0:raise RuntimeFault('invalid_input','A nonempty JSON request is required')
                    try:
                        body=self.rfile.read(size)
                        consumed=True
                        request=json.loads(body)
                    except (ValueError,RecursionError):raise RuntimeFault('invalid_input','Invalid local JSON request') from None
                    if not isinstance(request,dict) or set(request)!={'operation','operationId','arguments'}:
                        raise RuntimeFault('invalid_input','Expected operation, operationId and arguments')
                    if not isinstance(request['operation'],str) or not 1<=len(request['operation'])<=100 or not isinstance(request['operationId'],str) or not 1<=len(request['operationId'])<=128 or not isinstance(request['arguments'],dict):
                        raise RuntimeFault('invalid_input','Invalid local operation fields')
                    result=owner.dispatch(request['operation'],request['operationId'],request['arguments'])
                    self.reply(200,result)
                except RuntimeFault as exc:
                    if not consumed and not self.headers.get('Transfer-Encoding'):
                        # Header denial still consumes a bounded already-sent body;
                        # otherwise Windows can reset TCP before the peer sees it.
                        try:
                            size=int(self.headers.get('Content-Length','0'))
                            if 0<size<=LIMIT+1:
                                self.connection.settimeout(.25)
                                self.rfile.read(size)
                        except (ValueError,OSError,TimeoutError):pass
                    status=401 if exc.code=='authenticate' else 403 if exc.code=='forbidden' else 400
                    self.reply(status,{'operationId':request.get('operationId') if isinstance(request,dict) else None,
                                       'status':'unknown' if exc.code=='unknown_result' else 'rejected','data':None,'error':public_error(exc)})
                except (OSError,TimeoutError):
                    self.close_connection=True
                except Exception:
                    self.reply(500,{'status':'unknown','data':None,'error':{'code':'unknown_result',
                        'message':'Original operation needs local recovery','recovery':'resume_original'}})

            def do_GET(self):
                if owner.user_ui and owner.user_ui.handle(self):return
                self.reply(405,{'error':{'code':'forbidden','message':'Use authenticated local RPC','recovery':'use_sdk'}})
        return Handler

    def start(self):
        if self.thread:raise RuntimeError('Local RPC already started')
        self.thread=threading.Thread(target=self.server.serve_forever,name='ioa-ipc',daemon=True)
        self.thread.start()
        private_json(self.home/'runtime.json',self.descriptor)
        return self

    def close(self):
        if self.thread:
            self.server.shutdown()
            self.thread.join(5)
        self.server.server_close()
        path=self.home/'runtime.json'
        try:
            if path.exists() and read_json(path).get('bootId')==self.boot_id:path.unlink()
        except (OSError,ValueError):pass


class LocalClient:
    def __init__(self,home,*,timeout=10):
        self.home,self.timeout=Path(home),timeout

    def call(self,operation,arguments=None,*,operation_id=None):
        if arguments is not None and not isinstance(arguments,dict):raise RuntimeFault('invalid_input','Local arguments must be an object')
        operation_id=operation_id or str(uuid.uuid4())
        try:descriptor=read_json(self.home/'runtime.json')
        except (OSError,ValueError):raise RuntimeFault('not_ready','Start the configured local runtime first','runtime.start') from None
        if not isinstance(descriptor,dict):raise RuntimeFault('protocol_error','Invalid local endpoint descriptor')
        try:
            url=urlsplit(descriptor.get('url',''))
            port=url.port
        except (ValueError,TypeError):raise RuntimeFault('protocol_error','Invalid local endpoint address') from None
        if descriptor.get('schemaVersion')!=1 or url.scheme!='http' or url.hostname!='127.0.0.1' or not port or url.username or url.password or url.path or url.query or url.fragment:
            raise RuntimeFault('protocol_error','Local endpoint must be a credential-free loopback address')
        if not all(isinstance(descriptor.get(key),str) and descriptor[key] for key in ('token','bootId','runtimeId')):
            raise RuntimeFault('protocol_error','Local endpoint identity is incomplete')
        payload={'operation':operation,'operationId':operation_id,'arguments':arguments or {}}
        if len(encode(payload).encode())>LIMIT:raise RuntimeFault('invalid_input','Local request exceeds 128 KiB')
        try:
            response=httpx.post(descriptor['url']+'/v1/call',json=payload,
                headers={'Authorization':'Bearer '+descriptor['token'],'X-Ioa-Instance':descriptor['bootId']},
                timeout=self.timeout,trust_env=False)
        except httpx.TransportError:
            raise RuntimeFault('unknown_result','Local response unavailable; keep the original operation',
                               'resume_original',operation_id=operation_id) from None
        if response.headers.get('X-Ioa-Instance')!=descriptor['bootId']:
            raise RuntimeFault('protocol_error','Local response belongs to another instance')
        try:value=response.json()
        except ValueError:raise RuntimeFault('protocol_error','Local response is not JSON') from None
        if response.status_code!=200:
            error=value.get('error',{}) if isinstance(value,dict) else {}
            raise RuntimeFault(error.get('code','protocol_error'),error.get('message','Local RPC failed'),
                               error.get('recovery','inspect'),operation_id=operation_id)
        if not isinstance(value,dict) or value.get('operationId')!=operation_id or value.get('status') not in ('accepted','running','waiting','completed','rejected','failed','unknown'):
            raise RuntimeFault('protocol_error','Local response does not match the original operation')
        return value
