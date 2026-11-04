"""One epoch's fixed OpenAI-compatible endpoint, never an HTTP forward proxy."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import os
from pathlib import Path
import signal
import threading
import time
from urllib.parse import urlsplit

import httpx


PATHS = {('POST','/v1/chat/completions'),('POST','/v1/embeddings'),('GET','/v1/models')}


def provider_base(value):
    parsed=urlsplit(value)
    if (parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password
            or '?' in value or '#' in value or any(ord(char)<32 for char in value)
            or any(part in ('.','..') for part in parsed.path.split('/'))):
        raise ValueError('Use one fixed HTTPS provider base URL without query or credentials')
    return value.rstrip('/')


class ModelServer:
    def __init__(self, *, provider_base_url, provider_key, epoch_token, epoch, listen=('0.0.0.0',8081), transport=None):
        self.base=provider_base(provider_base_url)
        if not provider_key or len(epoch_token)<32 or not epoch:
            raise ValueError('Provider secret and unique epoch credentials are required')
        self.closed=threading.Event()
        self.active=set()
        self.lock=threading.RLock()
        self.client=httpx.Client(trust_env=False,follow_redirects=False,timeout=httpx.Timeout(60,connect=10),transport=transport)
        controller=self
        class Handler(BaseHTTPRequestHandler):
            protocol_version='HTTP/1.0'
            def log_message(self,*args):pass
            def do_CONNECT(self):self.error(403)
            def do_GET(self):self.handle_model()
            def do_POST(self):self.handle_model()
            def error(self,status):
                # Consume a small, declared request body before closing. Leaving
                # it unread may turn a deliberate rejection into TCP RST on
                # Windows. Unknown/chunked/oversized bodies are never drained.
                self.close_connection=True
                previous=self.connection.gettimeout()
                try:
                    if not self.headers.get('Transfer-Encoding'):
                        size=int(self.headers.get('Content-Length','0'))
                        remaining=size-getattr(self,'_body_consumed',0)
                        if 0<size<=2*1024*1024:
                            deadline=time.monotonic()+.25
                            while remaining>0 and time.monotonic()<deadline:
                                self.connection.settimeout(max(.001,deadline-time.monotonic()))
                                chunk=self.rfile.read(min(remaining,64*1024))
                                if not chunk:break
                                remaining-=len(chunk)
                except (ValueError,OSError):
                    pass
                finally:
                    try:self.connection.settimeout(previous)
                    except OSError:pass
                try:
                    self.send_response(status);self.send_header('Content-Length','0')
                    self.send_header('Connection','close');self.end_headers()
                except OSError:pass
            def handle_model(self):
                responded=False
                self._body_consumed=0
                if (controller.closed.is_set() or not hmac.compare_digest(
                        self.headers.get('Authorization','').encode(),('Bearer '+epoch_token).encode())):
                    self.error(403);return
                if self.command=='GET' and self.path=='/health':
                    raw=json.dumps({'epoch':epoch,'ready':True}).encode()
                    self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw);return
                if (self.command,self.path) not in PATHS or self.headers.get('Transfer-Encoding'):
                    self.error(403);return
                try:
                    size=int(self.headers.get('Content-Length','0'))
                    if not 0<=size<=2*1024*1024 or (self.command=='POST' and self.headers.get('Content-Type','').split(';')[0]!='application/json'):
                        self.error(413);return
                    self.connection.settimeout(10)
                    body=self.rfile.read(size)
                    self._body_consumed=len(body)
                    if len(body)!=size:raise ValueError()
                    if self.command=='POST' and not isinstance(json.loads(body),dict):raise ValueError()
                    request=controller.client.build_request(self.command,controller.base+self.path.removeprefix('/v1'),content=body,
                        headers={'Authorization':'Bearer '+provider_key,'Content-Type':'application/json','Accept':'application/json, text/event-stream'})
                    response=controller.client.send(request,stream=True)
                    with controller.lock:controller.active.add(response)
                    try:
                        if controller.closed.is_set():self.error(403);return
                        # Redirects and vendor errors may contain provider details;
                        # never return their Location, headers or raw body.
                        if not 200<=response.status_code<300:self.error(502);return
                        self.send_response(response.status_code)
                        self.send_header('Content-Type',response.headers.get('Content-Type','application/json'))
                        self.send_header('Cache-Control','no-store');self.end_headers()
                        responded=True
                        count=0
                        for chunk in response.iter_bytes():
                            if controller.closed.is_set():break
                            count+=len(chunk)
                            if count>32*1024*1024:break
                            self.wfile.write(chunk);self.wfile.flush()
                    finally:
                        with controller.lock:controller.active.discard(response)
                        response.close()
                except ValueError:
                    if not responded:self.error(400)
                    self.close_connection=True
                except (OSError,httpx.HTTPError):
                    if not responded:self.error(502)
                    # No request bodies, credentials, upstream URLs or traceback.
                    self.close_connection=True
        self.server=ThreadingHTTPServer(listen,Handler)
        self.server.daemon_threads=True
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)

    def start(self):self.thread.start();return self

    def close(self):
        self.closed.set()
        with self.lock:
            for response in tuple(self.active):response.close()
        if self.thread.is_alive():self.server.shutdown();self.thread.join(timeout=5)
        self.server.server_close();self.client.close()


def main():
    # This file is mounted only into this sidecar, never the seat container.
    secret_path=Path('/run/model/provider.json')
    if secret_path.stat().st_size>8192:raise ValueError('Provider secret file is too large')
    secret=json.loads(secret_path.read_text('utf-8'))
    if set(secret)!={'api_key'} or not isinstance(secret['api_key'],str) or not secret['api_key']:
        raise ValueError('The dedicated provider secret is missing')
    server=ModelServer(provider_base_url=os.environ['IOALAB_MODEL_PROVIDER_BASE_URL'],provider_key=secret['api_key'],
        epoch_token=os.environ.pop('IOALAB_MODEL_EPOCH_TOKEN'),epoch=os.environ['IOALAB_TRIAL_EPOCH']).start()
    stop=threading.Event()
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:stop.set())
    try:stop.wait()
    finally:server.close()


if __name__=='__main__':main()
