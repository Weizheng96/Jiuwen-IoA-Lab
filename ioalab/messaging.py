"""Account-addressed durable messages. Business decisions stay on recipients' devices."""
from __future__ import annotations
import hashlib
import json
import math
import re
import secrets
import time
import uuid
from fastapi import HTTPException
from jsonschema import Draft202012Validator,FormatChecker
from .store import jdump,jload

SCHEMA='''
CREATE TABLE IF NOT EXISTS message_interfaces(owner TEXT,name TEXT,revision INTEGER,definition TEXT,enabled INTEGER,updated REAL,PRIMARY KEY(owner,name));
CREATE TABLE IF NOT EXISTS message_versions(owner TEXT,name TEXT,revision INTEGER,definition TEXT,PRIMARY KEY(owner,name,revision));
CREATE TABLE IF NOT EXISTS messages(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE,sender TEXT,receiver TEXT,interface TEXT,revision INTEGER,payload TEXT,fingerprint TEXT,idempotency_key TEXT,parent TEXT,depth INTEGER,created REAL,expires REAL,retain_until REAL,state TEXT,result TEXT,lease TEXT,lease_until REAL,updated REAL,UNIQUE(sender,idempotency_key));
CREATE INDEX IF NOT EXISTS ix_message_inbox ON messages(receiver,seq);
CREATE INDEX IF NOT EXISTS ix_message_sender ON messages(sender,created);
'''
KEYWORDS={'type','properties','required','additionalProperties','items','enum','minimum','maximum','minLength','maxLength','minItems','maxItems','description','format'}
TERMINAL={'completed','rejected','expired'}


def error(code,message): raise HTTPException(code,message)


def check_schema(schema,depth=0):
    if depth>8 or not isinstance(schema,(dict,bool)): error(400,'Invalid or overly deep schema')
    if isinstance(schema,bool): return
    if set(schema)-KEYWORDS: error(400,'Unsupported schema keyword')
    if schema.get('type') not in (None,'object','array','string','number','integer','boolean','null'): error(400,'Unsupported schema type')
    if schema.get('format') not in (None,'date-time','email'): error(400,'Unsupported schema format')
    if not isinstance(schema.get('properties',{}),dict): error(400,'properties must be an object')
    for child in schema.get('properties',{}).values(): check_schema(child,depth+1)
    for key in ('items','additionalProperties'):
        if key in schema: check_schema(schema[key],depth+1)
    try: Draft202012Validator.check_schema(schema)
    except Exception: error(400,'Invalid JSON Schema')


def validate(schema,value):
    def bounded(item,depth=0):
        if depth>8: error(400,'Message nesting exceeds eight levels')
        if isinstance(item,dict):
            for child in item.values(): bounded(child,depth+1)
        elif isinstance(item,list):
            for child in item: bounded(child,depth+1)
    bounded(value)
    try: problem=next(Draft202012Validator(schema,format_checker=FormatChecker()).iter_errors(value),None)
    except (RecursionError,ValueError,TypeError): error(400,'Invalid message structure')
    if problem: error(400,'Schema mismatch at '+('/'.join(map(str,problem.absolute_path)) or '/'))


class MessageCenter:
    def __init__(self,platform):
        self.p,self.db,self.s=platform,platform.db,platform.s
        with self.db.lock: self.db.conn.executescript(SCHEMA)

    def user(self,username):
        row=self.db.one('SELECT id,username FROM users WHERE username=?',username)
        if not row: error(404,'Account not found')
        return row

    def actor(self,uid):
        return (self.db.one("SELECT id FROM agents WHERE owner_id=? AND role='assistant'",uid) or {}).get('id',uid)

    def publish(self,uid,name,body):
        if not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_.-]{0,63}',name): error(400,'Invalid interface name')
        if len(jdump(body).encode())>16384: error(413,'Interface exceeds 16 KiB')
        if set(body)-{'description','inputSchema','outputSchema','allowedSenders','enabled'}: error(400,'Unknown interface fields; message interfaces cannot charge')
        if not self.db.one("SELECT id FROM agents WHERE owner_id=? AND role='assistant'",uid): error(409,'Connect your local assistant identity first')
        if 'inputSchema' not in body: error(400,'An explicit inputSchema is required')
        if not isinstance(body['inputSchema'],dict) or body['inputSchema'].get('type') not in ('object','array','string','number','integer','boolean','null'): error(400,'inputSchema must declare its root type')
        definition={'description':str(body.get('description',''))[:1000],'inputSchema':body['inputSchema'],
                    'outputSchema':body.get('outputSchema',{}),'allowedSenders':body.get('allowedSenders')}
        for key in ('inputSchema','outputSchema'): check_schema(definition[key])
        allow=definition['allowedSenders']
        if allow is not None:
            if not isinstance(allow,list) or len(allow)>100 or not all(isinstance(n,str) for n in allow): error(400,'Invalid allowedSenders')
            definition['allowedIds']=sorted({self.user(n)['id'] for n in allow})
        enabled=body.get('enabled',True)
        if not isinstance(enabled,bool): error(400,'enabled must be boolean')
        encoded=jdump(definition);now=time.time()
        with self.db.tx() as conn:
            old=conn.execute('SELECT * FROM message_interfaces WHERE owner=? AND name=?',(uid,name)).fetchone()
            revision=old['revision'] if old and old['definition']==encoded else (old['revision']+1 if old else 1)
            conn.execute('INSERT OR REPLACE INTO message_versions VALUES(?,?,?,?)',(uid,name,revision,encoded))
            conn.execute('INSERT OR REPLACE INTO message_interfaces VALUES(?,?,?,?,?,?)',(uid,name,revision,encoded,int(enabled),now))
        self.p.bus.emit('message','interface.updated',sender=self.actor(uid),detail={'interface':name,'revision':revision})
        return {'name':name,'revision':revision,**definition,'enabled':enabled}

    @staticmethod
    def permitted(row,uid):
        d=jload(row['definition']);return bool(row['enabled']) and (d.get('allowedIds') is None or uid in d['allowedIds'])

    def interfaces(self,owner,viewer):
        rows=self.db.rows('SELECT * FROM message_interfaces WHERE owner=? ORDER BY name',owner)
        return [{'name':r['name'],'revision':r['revision'],**{k:v for k,v in jload(r['definition']).items() if k not in ('allowedIds','allowedSenders') or owner==viewer},
                 'enabled':bool(r['enabled'])} for r in rows if owner==viewer or self.permitted(r,viewer)]

    def cleanup(self):
        now=time.time()
        with self.db.tx() as conn:
            conn.execute("UPDATE messages SET state='expired',lease=NULL,lease_until=0,updated=? WHERE expires<=? AND state NOT IN ('completed','rejected','expired')",(now,now))
            conn.execute('UPDATE messages SET payload=NULL,result=NULL WHERE retain_until<=?',(now,))
            conn.execute('DELETE FROM messages WHERE created<?',(now-self.s.message_metadata_days*86400,))

    def send(self,uid,body):
        self.cleanup();now=time.time()
        receiver=self.user(body.get('to',''))['id'];name=body.get('interface');revision=body.get('revision')
        if not isinstance(name,str) or not isinstance(revision,int) or isinstance(revision,bool): error(400,'Interface name and integer revision are required')
        key=body.get('idempotencyKey')
        if not isinstance(key,str) or not 1<=len(key)<=128: error(400,'A bounded idempotencyKey is required')
        payload=body.get('payload')
        try: encoded=json.dumps(payload,ensure_ascii=False,allow_nan=False)
        except (ValueError,TypeError,RecursionError): error(400,'Invalid payload')
        if len(encoded.encode())>32768: error(413,'Message exceeds 32 KiB')
        parent=body.get('parentId');depth=0
        if parent:
            prior=self.get(uid,parent)
            if {uid,receiver}!={prior['sender'],prior['receiver']}: error(403,'Reply parties do not match')
            depth=prior['depth']+1
            if depth>4: error(400,'Message chain limit reached')
        fingerprint=hashlib.sha256(jdump({'to':receiver,'interface':name,'revision':revision,'payload':payload,'parent':parent,'expiresAt':body.get('expiresAt')}).encode()).hexdigest()
        with self.db.tx() as conn:
            old=conn.execute('SELECT * FROM messages WHERE sender=? AND idempotency_key=?',(uid,key)).fetchone()
            if old:
                if old['fingerprint']!=fingerprint: error(409,'Idempotency key conflicts with another message')
                return self.view(dict(old),uid)
            interface=conn.execute('SELECT * FROM message_interfaces WHERE owner=? AND name=?',(receiver,name)).fetchone()
            if not interface or not self.permitted(interface,uid): error(403,'Interface is unavailable to this sender')
            if revision!=interface['revision']: error(409,'Interface changed; read its definition again')
            validate(jload(interface['definition'])['inputSchema'],payload)
            recent=conn.execute('SELECT COUNT(*) FROM messages WHERE sender=? AND created>?',(uid,now-60)).fetchone()[0]
            pair=conn.execute('SELECT COUNT(*) FROM messages WHERE sender=? AND receiver=? AND created>?',(uid,receiver,now-60)).fetchone()[0]
            if recent>=self.s.message_rate or pair>=min(20,self.s.message_rate): error(429,'Message rate exceeded')
            pending=conn.execute("SELECT COUNT(*) FROM messages WHERE receiver=? AND state NOT IN ('completed','rejected','expired')",(receiver,)).fetchone()[0]
            if pending>=self.s.message_pending_limit: error(409,'Recipient mailbox is full')
            expires=body.get('expiresAt',now+self.s.message_ttl_days*86400)
            if isinstance(expires,bool) or not isinstance(expires,(int,float)) or not math.isfinite(expires) or not now<expires<=now+self.s.message_retention_days*86400: error(400,'Invalid expiresAt')
            mid=uuid.uuid4().hex
            conn.execute('INSERT INTO messages(id,sender,receiver,interface,revision,payload,fingerprint,idempotency_key,parent,depth,created,expires,retain_until,state,result,lease,lease_until,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                         (mid,uid,receiver,name,revision,encoded,fingerprint,key,parent,depth,now,expires,now+self.s.message_retention_days*86400,'queued',None,None,0,now))
        self.event(mid,'message.queued')
        return self.get(uid,mid)

    def view(self,row,uid,admin=False):
        if not admin and uid not in (row['sender'],row['receiver']): error(403,'Message belongs to other accounts')
        row=dict(row)
        for key in ('payload','result'): row[key]=jload(row[key]) if row[key] else None
        row.pop('fingerprint',None);row.pop('idempotency_key',None)
        row.pop('lease',None)
        for side in ('sender','receiver'):
            row[side+'Username']=(self.db.one('SELECT username FROM users WHERE id=?',row[side]) or {}).get('username','')
        return row

    def get(self,uid,mid,admin=False):
        self.cleanup();row=self.db.one('SELECT * FROM messages WHERE id=?',mid)
        if not row: error(404,'Message not found')
        return self.view(row,uid,admin)

    def inbox(self,uid,after=0,limit=50):
        self.cleanup()
        rows=self.db.rows('SELECT * FROM messages WHERE receiver=? AND seq>? ORDER BY seq LIMIT ?',uid,max(0,after),min(max(limit,1),100))
        return {'messages':[self.view(r,uid) for r in rows],'cursor':rows[-1]['seq'] if rows else after}

    def list(self,uid,admin=False):
        self.cleanup()
        rows=self.db.rows('SELECT * FROM messages '+('' if admin else 'WHERE sender=? OR receiver=? ')+'ORDER BY seq DESC LIMIT 200',*(() if admin else (uid,uid)))
        return [self.view(r,uid,admin) for r in rows]

    def own(self,uid,mid,conn):
        row=conn.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
        if not row: error(404,'Message not found')
        if row['receiver']!=uid: error(403,'Only the recipient can acknowledge or process')
        return row

    def ack(self,uid,mid):
        self.cleanup()
        with self.db.tx() as conn:
            row=self.own(uid,mid,conn)
            if row['state']=='queued': conn.execute("UPDATE messages SET state='synced',updated=? WHERE id=?",(time.time(),mid))
        return self.get(uid,mid)

    def claim(self,uid,mid):
        self.cleanup();now=time.time()
        with self.db.tx() as conn:
            row=self.own(uid,mid,conn)
            if row['state'] in TERMINAL: return {'message':self.view(dict(row),uid),'lease':None}
            interface=conn.execute('SELECT * FROM message_interfaces WHERE owner=? AND name=?',(uid,row['interface'])).fetchone()
            if not interface or not self.permitted(interface,row['sender']):
                conn.execute("UPDATE messages SET state='rejected',result=?,updated=? WHERE id=?",(jdump({'status':'permission_revoked'}),now,mid))
                return {'message':{**self.view(dict(row),uid),'state':'rejected','result':{'status':'permission_revoked'}},'lease':None}
            if row['lease_until']>now: error(409,'Another processor holds this message')
            lease=secrets.token_urlsafe(24)
            conn.execute("UPDATE messages SET state='processing',lease=?,lease_until=?,updated=? WHERE id=?",(lease,now+300,now,mid))
        self.event(mid,'message.processing')
        return {'message':self.get(uid,mid),'lease':lease}

    def complete(self,uid,mid,body):
        self.cleanup();now=time.time();result=body.get('result',{});state=body.get('state','completed')
        if state not in ('completed','rejected'): error(400,'Invalid completion state')
        try: json.dumps(result,allow_nan=False)
        except (ValueError,TypeError,RecursionError): error(400,'Invalid result')
        validate({},result)
        if len(jdump(result).encode())>32768: error(413,'Result exceeds 32 KiB')
        with self.db.tx() as conn:
            row=self.own(uid,mid,conn)
            if row['state']=='expired': error(409,'Message expired before completion')
            if row['state'] in TERMINAL:
                if row['result']==jdump(result) and row['state']==state: return self.view(dict(row),uid)
                error(409,'Result is already final')
            if not body.get('lease') or row['lease']!=body['lease'] or row['lease_until']<now: error(409,'Processing lease is invalid or expired')
            version=conn.execute('SELECT definition FROM message_versions WHERE owner=? AND name=? AND revision=?',(uid,row['interface'],row['revision'])).fetchone()
            if state=='completed': validate(jload(version['definition']).get('outputSchema',{}),result)
            conn.execute('UPDATE messages SET state=?,result=?,lease=NULL,lease_until=0,updated=? WHERE id=?',(state,jdump(result),now,mid))
        self.event(mid,'message.'+state)
        return self.get(uid,mid)

    def event(self,mid,op):
        row=self.db.one('SELECT sender,receiver,interface,state FROM messages WHERE id=?',mid)
        self.p.bus.emit('message',op,sender=self.actor(row['sender']),receiver=self.actor(row['receiver']),
                        detail={'messageId':mid,'interface':row['interface'],'state':row['state'],'trust':'verified'})


def build_router(center,current_user):
    from fastapi import APIRouter,Depends
    import asyncio
    router=APIRouter(prefix='/api/v1/messaging')
    @router.get('/interfaces')
    def mine(uid=Depends(current_user)): return center.interfaces(uid,uid)
    @router.put('/interfaces/{name}')
    def publish(name:str,body:dict,uid=Depends(current_user)): return center.publish(uid,name,body)
    @router.get('/accounts/{username}/interfaces')
    def lookup(username:str,uid=Depends(current_user)): return center.interfaces(center.user(username)['id'],uid)
    @router.post('/messages')
    def send(body:dict,uid=Depends(current_user)): return center.send(uid,body)
    @router.get('/messages')
    def listing(uid=Depends(current_user)): return center.list(uid)
    @router.get('/messages/{mid}')
    def get(mid:str,uid=Depends(current_user)): return center.get(uid,mid)
    @router.get('/inbox')
    async def inbox(after:int=0,limit:int=50,wait:float=0,uid=Depends(current_user)):
        if not math.isfinite(wait): error(400,'wait must be finite')
        until=time.monotonic()+min(max(wait,0),25)
        while True:
            result=center.inbox(uid,after,limit)
            if result['messages'] or time.monotonic()>=until: return result
            await asyncio.sleep(.25)
    @router.post('/messages/{mid}/ack')
    def ack(mid:str,uid=Depends(current_user)): return center.ack(uid,mid)
    @router.post('/messages/{mid}/claim')
    def claim(mid:str,uid=Depends(current_user)): return center.claim(uid,mid)
    @router.post('/messages/{mid}/complete')
    def complete(mid:str,body:dict,uid=Depends(current_user)): return center.complete(uid,mid,body)
    return router
