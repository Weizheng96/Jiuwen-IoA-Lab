"""Authenticated notification RPC exposed by the official Jiuwen extension API."""
import json
import os
import time
import uuid
from urllib.parse import urlsplit
from .messaging import RetryLater

class SwarmWebBridge:
    def __init__(self,url,store,*,secret=None):
        parts=urlsplit(url)
        if parts.scheme not in ('ws','wss') or not parts.hostname: raise ValueError('Expected Jiuwen WebChannel URL')
        if parts.scheme=='ws' and parts.hostname not in ('localhost','127.0.0.1','::1'): raise ValueError('Remote Jiuwen must use authenticated TLS proxy')
        self.url,self.store=url,store
        self.secret=secret if secret is not None else os.getenv('IOALAB_SWARM_BRIDGE_SECRET','')
        if not self.secret: raise ValueError('Configure shared local IOALAB_SWARM_BRIDGE_SECRET')

    @staticmethod
    def request(ws,method,params,*,timeout=20):
        rid='ioa-'+uuid.uuid4().hex;ws.send(json.dumps({'type':'req','id':rid,'method':method,'params':params},ensure_ascii=False))
        until=time.monotonic()+timeout
        while time.monotonic()<until:
            item=json.loads(ws.recv(timeout=max(.1,until-time.monotonic())))
            if item.get('type')=='res' and item.get('id')==rid:
                if not item.get('ok'): raise RuntimeError(method+': '+str(item.get('error') or item.get('code') or 'rejected'))
                return item.get('payload',{})
        raise TimeoutError('Jiuwen request timed out')

    def forward(self,message):
        from websockets.sync.client import connect
        mid=message['id'];saved=self.store.get('swarm',mid)
        if saved: return saved
        try: ws=connect(self.url,open_timeout=5,close_timeout=2,proxy=None)
        except (OSError,TimeoutError) as exc: raise RetryLater('Jiuwen is offline') from exc
        with ws:
            # Extension-side durable idempotency makes a lost client ACK retry safe.
            try: result=self.request(ws,'ioa.notification.deliver',{'bridge_token':self.secret,'messageId':mid,'sender':message['senderUsername'],'text':message['payload']['text']},timeout=30)
            except (TimeoutError,OSError) as exc: raise RetryLater('Jiuwen receipt unavailable') from exc
            if result.get('status') not in ('delivered','unknown'): raise RuntimeError('Jiuwen notification was not persisted')
            self.store.put('swarm',mid,result)
            return result
