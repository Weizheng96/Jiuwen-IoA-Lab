"""Narrow authenticated RPC to the original Jiuwen notification extension."""
import hashlib
import json
import re
import time
import uuid
from urllib.parse import parse_qs,urlsplit

from websockets.sync.client import connect
from ioalab_sdk.runtime.protocol import RuntimeFault

PROTOCOL='ioa.web-notifications/1'


def body(value):
    if not isinstance(value,dict) or set(value)!={'messageId','sender','title','text','group'}:
        raise RuntimeFault('invalid_input','Use the declared notification fields')
    limits={'messageId':64,'sender':256,'title':120,'text':32768,'group':512}
    if any(not isinstance(value[key],str) or not value[key] or len(value[key].encode('utf-8'))>limit for key,limit in limits.items()):
        raise RuntimeFault('invalid_input','Notification fields are missing or too large')
    if not re.fullmatch('[A-Za-z0-9_-]{1,64}',value['messageId']) or any(char in value['title'] for char in '\r\n\x00'):
        raise RuntimeFault('invalid_input','Invalid notification identifier or title')
    return dict(value)


def fingerprint(value):
    return 'sha256:'+hashlib.sha256(json.dumps(body(value),ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


class NotSent(Exception):pass


class WebClient:
    def __init__(self,url,user,secret,*,timeout=5):
        parts=urlsplit(url)
        if (parts.scheme not in ('ws','wss') or not parts.hostname or parts.username or parts.password or parts.fragment
            or not parts.path or parse_qs(parts.query)!={'user_id':[user]}
            or parts.scheme=='ws' and parts.hostname not in ('localhost','127.0.0.1','::1')):
            raise RuntimeFault('invalid_input','Configure a fixed Jiuwen WebChannel URL and matching user_id; remote connections require wss')
        if not isinstance(user,str) or not 1<=len(user)<=128 or not isinstance(secret,str) or not secret:
            raise RuntimeFault('not_ready','Configure the notification owner and local bridge credential')
        self.url,self.user,self.secret,self.timeout=url,user,secret,timeout
        self.store_id=None

    def rpc(self,method,params):
        try:socket=connect(self.url,open_timeout=self.timeout,close_timeout=1,proxy=None,max_size=131072)
        except Exception as exc:raise NotSent(type(exc).__name__) from None
        with socket:
            expected=urlsplit(self.url);actual=urlsplit(expected.scheme+'://'+socket.request.headers['Host'])
            default=443 if expected.scheme=='wss' else 80
            if (actual.hostname.casefold(),actual.port or default)!=(expected.hostname.casefold(),expected.port or default) or socket.request.path!=(expected.path+('?'+expected.query if expected.query else '')):
                raise NotSent('Notification redirects are not allowed')
            request='ioa-notify-'+uuid.uuid4().hex
            socket.send(json.dumps({'type':'req','id':request,'method':method,'params':{'bridge_token':self.secret,**params}},ensure_ascii=False))
            deadline=time.monotonic()+self.timeout
            for _ in range(100):
                remaining=deadline-time.monotonic()
                if remaining<=0:raise TimeoutError('Notification receipt deadline elapsed')
                result=json.loads(socket.recv(timeout=remaining))
                if result.get('id')!=request:continue
                if not result.get('ok'):
                    raise RuntimeFault(result.get('code') if result.get('code') in ('UNAUTHORIZED','BAD_REQUEST','STORE_CONFLICT','ID_CONFLICT') else 'not_ready','Jiuwen notification RPC rejected the request','inspect_web_bridge')
                return result.get('payload') or {}
        raise RuntimeFault('protocol_error','Jiuwen notification response was not received')

    def probe(self):
        value=self.rpc('ioa.notification.capabilities',{})
        if value.get('protocol')!=PROTOCOL or value.get('userId')!=self.user or not re.fullmatch('[a-f0-9]{32}',str(value.get('receiptStoreId',''))):
            raise RuntimeFault('not_ready','Load the matching notification extension for the configured user','configure_web_bridge')
        return value['receiptStoreId']

    def receipt(self,value,notice):
        if (value.get('messageId')!=notice['messageId'] or value.get('contentDigest')!=fingerprint(notice)
            or value.get('userId')!=self.user or value.get('receiptStoreId')!=self.store_id
            or value.get('status') not in ('absent','session_received','unknown')):
            raise RuntimeFault('protocol_error','Notification receipt does not match its original target and content')
        if value['status']=='session_received' and (not isinstance(value.get('sessionId'),str) or not value['sessionId']):
            raise RuntimeFault('protocol_error','The native session receipt is incomplete')
        return value

    def get(self,notice):
        return self.receipt(self.rpc('ioa.notification.get',{'messageId':notice['messageId'],'contentDigest':fingerprint(notice),'receiptStoreId':self.store_id}),notice)

    def deliver(self,notice):
        if not self.store_id:raise NotSent('Notification store has not been checked')
        return self.receipt(self.rpc('ioa.notification.deliver',{**notice,'contentDigest':fingerprint(notice),'receiptStoreId':self.store_id}),notice)
