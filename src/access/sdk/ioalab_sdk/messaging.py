"""Durable local inbox, independent of the exclusive A2C identity-directory lease."""
import json
import time
import uuid
from pathlib import Path
import httpx
from .store import LocalStore
from .runtime.session import AccountSession

class RetryLater(Exception):
    """No external effect happened; leave the message pending for a later lease."""


class MailboxClient:
    def __init__(self,base,username,password,home,*,connect_identity=False):
        self.base,self.username,self.password=base.rstrip('/'),username,password
        self.home=Path(home);self.store=LocalStore(self.home/'mailbox.sqlite')
        try:
            key=self.base+'|'+username
            self.session=AccountSession(self.base,username,password,self.home,legacy=self.store.get('auth',key))
            self.http,self.account=self.session.http,self.session.account
            self.store.delete('auth',key)
            if connect_identity and not any(r['role']=='assistant' for r in self.api('GET','/me')['identities']):
                from .client import LabClient
                identity=LabClient(base,username,password,self.home)
                identity.close()
            self.flush()
        except BaseException:
            self.close();raise

    def api(self,method,path,body=None):
        return self.session.api(method,path,body)

    @property
    def token(self):return self.session.token

    def publish(self,name,definition):
        # Directory routing identity is read-only and is never part of a declaration.
        writable={key:value for key,value in definition.items() if key!='agentId'}
        return self.api('PUT','/messaging/interfaces/'+name,writable)
    def interfaces(self,account=None):
        from urllib.parse import quote
        return self.api('GET','/messaging/accounts/'+quote(account,safe='')+'/interfaces' if account else '/messaging/interfaces')

    def send(self,to,interface,payload,*,revision=None,idempotency_key=None,expires_at=None,parent_id=None):
        definition=next((d for d in self.interfaces(to) if d['name']==interface),None)
        if not definition: raise ValueError('No authorized interface with that name')
        from jsonschema import Draft202012Validator,FormatChecker
        Draft202012Validator(definition['inputSchema'],format_checker=FormatChecker()).validate(payload)
        key=idempotency_key or uuid.uuid4().hex
        body={'to':to,'interface':interface,'revision':revision or definition['revision'],'payload':payload,'idempotencyKey':key}
        if expires_at is not None: body['expiresAt']=expires_at
        if parent_id: body['parentId']=parent_id
        old=self.store.get('outgoing',key)
        if old and old['request']!=body: raise ValueError('Local idempotency key conflicts')
        if old and old.get('message'): return old['message']
        if old and old.get('failed'): raise ValueError('This outgoing request was rejected; correct it and use a new idempotency key')
        self.store.put('outgoing',key,{'request':body})
        message=self.api('POST','/messaging/messages',body)
        self.store.put('outgoing',key,{'request':body,'message':message})
        return message

    def flush(self):
        for key,record in self.store.all('outgoing').items():
            if record.get('message') or record.get('failed'): continue
            try: record['message']=self.api('POST','/messaging/messages',record['request'])
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (400,403,404,409,422):
                    record['failed']={'statusCode':exc.response.status_code,'at':time.time()};self.store.put('outgoing',key,record)
                continue
            except httpx.HTTPError: continue
            self.store.put('outgoing',key,record)

    def sync(self,*,wait=0,limit=50):
        cursor=self.store.get('meta','cursor',0)
        result=self.api('GET',f'/messaging/inbox?after={cursor}&limit={limit}&wait={min(max(wait,0),25)}')
        # Each message is durable before the cursor advances; crash duplicates are harmless.
        for message in result['messages']:
            self.store.put('inbox',message['id'],message)
            self.api('POST','/messaging/messages/'+message['id']+'/ack')
        self.store.put('meta','cursor',result['cursor'])
        self.flush()
        return result

    def get(self,mid): return self.api('GET','/messaging/messages/'+mid)
    def claim(self,mid): return self.api('POST','/messaging/messages/'+mid+'/claim')
    def complete(self,mid,lease,result,*,state='completed'):
        message=self.api('POST','/messaging/messages/'+mid+'/complete',{'lease':lease,'result':result,'state':state})
        self.store.put('inbox',mid,message)
        return message

    def process(self,handler):
        processed=[]
        for mid,message in self.store.all('inbox').items():
            if message['state'] in ('completed','expired','rejected'): continue
            try: claimed=self.claim(mid)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code==409: continue
                if exc.response.status_code==404:
                    message.update(state='expired',result={'status':'expired','reason':'Server retention ended'})
                    self.store.put('inbox',mid,message);continue
                raise
            if not claimed['lease']:
                self.store.put('inbox',mid,claimed['message']);continue
            message=claimed['message'];lease=claimed['lease']
            # A completed side effect with an unacknowledged result must not run twice.
            receipt=self.store.get('results',mid)
            if receipt is None:
                try: result=handler(message)
                except RetryLater:
                    continue
                except Exception as exc:
                    result={'status':'failed','error':str(exc)[:300]}
                receipt={'result':result,'state':'rejected' if result.get('status') in ('failed','permission_revoked') else 'completed'}
                self.store.put('results',mid,receipt)
            try: processed.append(self.complete(mid,lease,receipt['result'],state=receipt['state']))
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code!=409: raise
                current=self.get(mid)
                if current['state'] not in ('expired','completed','rejected'): raise
                self.store.put('inbox',mid,current)
        return processed

    def close(self):
        if getattr(self,'session',None):self.session.close()
        self.store.close()
