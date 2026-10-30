"""Resident WebSocket transport. Never invokes a model or repeats password login."""
import json
import random
import threading
import time
from urllib.parse import urlsplit

import httpx
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

from .protocol import RuntimeFault, encode


class AgentConnection:
    def __init__(self, base_url, token, actors, store):
        parsed = urlsplit(base_url)
        if parsed.scheme not in ('http','https') or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('An HTTP(S) platform address without credentials or query is required')
        self.base = base_url.rstrip('/')
        self.actors, self.store = list(actors), store
        if set(self.actors)!=set(store.cursors()):
            raise ValueError('Connection identities must match the local instance')
        self.http = httpx.Client(base_url=self.base, headers={'Authorization':'Bearer '+token}, timeout=10, trust_env=False)
        self._stop = threading.Event()
        self._changed = threading.Condition()
        self._thread = None
        self._socket = None
        self._ready = False
        self.error = None

    @property
    def connected(self):
        with self._changed:
            return self._ready and self.error is None

    def start(self, timeout=10):
        with self._changed:
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name='ioa-delivery', daemon=True)
                self._thread.start()
            deadline = time.monotonic()+timeout
            while not self._ready and self.error is None:
                remaining = deadline-time.monotonic()
                if remaining<=0:
                    raise RuntimeFault('not_ready', 'Connection is still recovering; local outbox is preserved')
                self._changed.wait(remaining)
            if self.error:
                raise self.error
        return self

    def submit(self, actor, kind, payload, operation_id=None):
        operation = self.store.prepare(actor,kind,payload,operation_id)
        with self._changed:
            self._changed.notify_all()
        return operation

    def call(self, actor, kind, payload, *, operation_id=None, timeout=10):
        operation = self.submit(actor,kind,payload,operation_id)
        return self.wait(actor,kind,operation,timeout=timeout)

    def wait(self, actor, kind, operation_id, *, timeout=10):
        deadline = time.monotonic()+timeout
        with self._changed:
            while True:
                response = self.store.response(actor,kind,operation_id)
                if response:
                    if response['ok']:
                        return response['result']
                    error = response['error']
                    raise RuntimeFault(error['code'],error['message'],error['recovery'],operation_id=operation_id)
                if self.error:
                    raise self.error
                remaining = deadline-time.monotonic()
                if remaining<=0 or self._stop.is_set():
                    raise RuntimeFault('unknown_result', 'Original operation is saved; resume it without changing its identity',
                                       'resume_original', operation_id=operation_id)
                self._changed.wait(min(remaining,.25))

    def recover_interaction(self, actor, origin):
        response = self.http.get('/api/v1/interactions',params={'agentId':actor,'originMessageId':origin})
        response.raise_for_status()
        return self.store.recover_link(actor,response.json())

    def _run(self):
        attempt = 0
        while not self._stop.is_set():
            try:
                self._session()
                attempt = 0
            except RuntimeFault as exc:
                with self._changed:
                    self.error = exc
                    self._changed.notify_all()
                return
            except ConnectionClosed as exc:
                if exc.rcvd and exc.rcvd.code in (4401,4403,4409):
                    code = 'stale_generation' if exc.rcvd.code==4409 else 'authenticate'
                    with self._changed:
                        self.error = RuntimeFault(code,'Execution connection was closed by the platform','stop_executor')
                        self._changed.notify_all()
                    return
                attempt += 1
            except (httpx.TransportError, OSError, TimeoutError):
                attempt += 1
            except Exception:
                with self._changed:
                    self.error = RuntimeFault('protocol_error','Connection failed protocol validation; inspect the local state')
                    self._changed.notify_all()
                return
            finally:
                with self._changed:
                    self._ready = False
                    self._socket = None
                    self._changed.notify_all()
            self._stop.wait(min(15,.2*2**min(attempt,6))*random.uniform(.7,1.3))

    def _session(self):
        response = self.http.post('/api/v1/connections/ticket',json={'agentIds':self.actors})
        if response.status_code in (429,500,502,503,504):
            raise OSError('Ticket endpoint is temporarily unavailable')
        if response.status_code!=200:
            raise RuntimeFault('authenticate','The current SDK login cannot obtain a connection ticket','authenticate')
        ticket = response.json()['ticket']
        url = self.base.replace('http','ws',1)+'/ws/agents'
        with connect(url,proxy=None,open_timeout=10,close_timeout=2,max_size=131072) as ws:
            self._socket = ws
            ws.send(encode({'type':'connection.auth','ticket':ticket,'runtimeId':self.store.runtime_id,'cursors':self.store.cursors()}))
            ready = json.loads(ws.recv(timeout=10))
            self._error_frame(ready)
            if ready.get('type')!='connection.ready' or set(ready.get('generations',{}))!=set(self.actors):
                raise RuntimeFault('protocol_error','Invalid connection identity response')
            generations = ready['generations']
            if any(not isinstance(value,int) or isinstance(value,bool) or value<1 for value in generations.values()):
                raise RuntimeFault('protocol_error','Invalid execution generation')
            interval = ready['heartbeatSeconds']/2
            if not interval>0 or ready['leaseSeconds']<3*ready['heartbeatSeconds']:
                raise RuntimeFault('protocol_error','Invalid heartbeat interval')
            sent = set()
            ping_at = time.monotonic()+interval
            with self._changed:
                self._ready = True
                self._changed.notify_all()
            while not self._stop.is_set():
                for operation in self.store.pending():
                    key = (operation['actor'],operation['kind'],operation['id'])
                    if key in sent:
                        continue
                    ws.send(encode({'type':operation['kind'],'operationId':operation['id'],'agentId':operation['actor'],
                                    'generation':generations[operation['actor']],'payload':operation['payload']}))
                    sent.add(key)
                if time.monotonic()>=ping_at:
                    ws.send(encode({'type':'connection.ping','connectionId':ready['connectionId']}))
                    ping_at = time.monotonic()+interval
                try:
                    frame = json.loads(ws.recv(timeout=min(.25,max(.01,ping_at-time.monotonic()))))
                except TimeoutError:
                    continue
                self._error_frame(frame)
                if frame.get('type')=='message.deliver':
                    self.store.receive(frame)
                elif frame.get('type')=='operation.result':
                    self.store.complete(frame)
                    if not frame['ok'] and frame['error']['code']=='stale_generation':
                        self._error_frame({'type':'connection.error','error':frame['error']})
                elif frame.get('type')!='connection.pong':
                    raise RuntimeFault('protocol_error','Unexpected server frame')
                with self._changed:
                    self._changed.notify_all()

    @staticmethod
    def _error_frame(frame):
        if frame.get('type')=='connection.error':
            error = frame['error']
            raise RuntimeFault(error['code'],error['message'],error['recovery'])

    def close(self):
        self._stop.set()
        socket = self._socket
        if socket:
            socket.close()
        if self._thread:
            self._thread.join(15)
            if self._thread.is_alive():
                raise RuntimeError('The connection thread did not stop; keep its local database open')
        self.http.close()
        with self._changed:
            self._changed.notify_all()

    def __enter__(self):
        return self.start()

    def __exit__(self, *_):
        self.close()
