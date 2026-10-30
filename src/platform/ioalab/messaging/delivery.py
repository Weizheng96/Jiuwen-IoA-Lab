"""Durable participant delivery and interaction authority. No agent execution."""
from __future__ import annotations
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import threading
import time
import uuid
from ..errors import LabError
from .interfaces import PublicInterfaces

SYSTEM='urn:ioa:platform'
TERMINAL={'completed','rejected','failed','expired'}
SCHEMA='''
CREATE TABLE IF NOT EXISTS delivery_leases(actor TEXT PRIMARY KEY,runtime TEXT,connection TEXT,generation INTEGER NOT NULL,expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS delivery_mailboxes(actor TEXT PRIMARY KEY,last_seq INTEGER NOT NULL DEFAULT 0,ack_seq INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS delivery_messages(id TEXT PRIMARY KEY,sender TEXT NOT NULL,recipient TEXT NOT NULL,seq INTEGER NOT NULL,kind TEXT NOT NULL,envelope TEXT,fingerprint TEXT NOT NULL,created REAL NOT NULL,expires REAL NOT NULL,retain_until REAL NOT NULL,delivery_state TEXT NOT NULL,request_state TEXT NOT NULL,interaction TEXT,origin TEXT,in_reply TEXT,started REAL,start_generation INTEGER,result TEXT,result_id TEXT,result_hash TEXT,UNIQUE(recipient,seq));
CREATE TABLE IF NOT EXISTS delivery_ids(digest TEXT PRIMARY KEY);
CREATE INDEX IF NOT EXISTS ix_delivery_recipient ON delivery_messages(recipient,seq);
CREATE INDEX IF NOT EXISTS ix_delivery_interaction ON delivery_messages(interaction,kind);
CREATE TABLE IF NOT EXISTS delivery_interactions(id TEXT PRIMARY KEY,origin TEXT UNIQUE NOT NULL,sender TEXT NOT NULL,recipient TEXT NOT NULL,kind TEXT NOT NULL,interface TEXT NOT NULL,revision INTEGER NOT NULL,deadline REAL NOT NULL,max_requests INTEGER NOT NULL,requests INTEGER NOT NULL,state TEXT NOT NULL,closed_at REAL);
CREATE TABLE IF NOT EXISTS delivery_closed_origins(origin TEXT PRIMARY KEY,reason TEXT NOT NULL,closed_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS delivery_operations(actor TEXT NOT NULL,kind TEXT NOT NULL,id TEXT NOT NULL,fingerprint TEXT NOT NULL,result TEXT NOT NULL,created REAL NOT NULL,retain_until REAL NOT NULL,PRIMARY KEY(actor,kind,id));
CREATE TABLE IF NOT EXISTS delivery_operation_ids(digest TEXT PRIMARY KEY);
'''


def encode(value):
    try:return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
    except (TypeError,ValueError,RecursionError) as exc:raise LabError('invalid_input','Invalid JSON value') from exc


def stamp(value):
    return datetime.fromtimestamp(value,timezone.utc).isoformat().replace('+00:00','Z')


def timestamp(value):
    try:
        date=datetime.fromisoformat(value.replace('Z','+00:00'))
        if date.utcoffset() is None:raise ValueError()
        return date.timestamp()
    except (AttributeError,TypeError,ValueError,OverflowError) as exc:
        raise LabError('invalid_input','A UTC timestamp with timezone is required') from exc


def identifier(value,name='messageId'):
    try:return str(uuid.UUID(value))
    except (TypeError,ValueError,AttributeError) as exc:raise LabError('invalid_input',name+' must be a UUID') from exc


class DeliveryBroker:
    def __init__(self,db,identities,settings,*,emit=None,clock=time.time):
        self.db,self.identities,self.s,self.emit,self.clock=db,identities,settings,emit,clock
        self.interfaces=PublicInterfaces(db,identities)
        self._listeners={}
        self._listener_lock=threading.RLock()
        with db.lock:db.conn.executescript(SCHEMA)

    def listen(self,connection,loop,event):
        with self._listener_lock:self._listeners[connection]=(loop,event)

    def unlisten(self,connection):
        with self._listener_lock:self._listeners.pop(connection,None)

    def notify(self):
        with self._listener_lock:
            for loop,event in list(self._listeners.values()):
                if not loop.is_closed():loop.call_soon_threadsafe(event.set)

    def connect(self,owner,actors,runtime,cursors,connection):
        runtime=identifier(runtime,'runtimeId')
        if not isinstance(cursors,dict) or set(cursors)-set(actors):raise LabError('invalid_input','Invalid recovery cursors')
        generations,acks={},{}
        now=self.clock()
        with self.db.tx() as conn:
            for actor in actors:
                self.identities.owned(owner,actor)
                lease=conn.execute('SELECT * FROM delivery_leases WHERE actor=?',(actor,)).fetchone()
                if lease and lease['expires']>now and lease['runtime']!=runtime:
                    raise LabError('not_ready','Another local instance still owns the execution lease',status=409,recovery='migrate_local_state')
                conn.execute('INSERT OR IGNORE INTO delivery_mailboxes(actor) VALUES(?)',(actor,))
                mailbox=conn.execute('SELECT * FROM delivery_mailboxes WHERE actor=?',(actor,)).fetchone()
                cursor=cursors.get(actor,0)
                if not isinstance(cursor,int) or isinstance(cursor,bool) or not 0<=cursor<=mailbox['last_seq']:
                    raise LabError('invalid_input','Recovery cursor exceeds accepted history')
                retained=conn.execute('SELECT COUNT(*) FROM delivery_messages WHERE recipient=? AND seq>?',(actor,cursor)).fetchone()[0]
                if retained!=mailbox['last_seq']-cursor:
                    raise LabError('history_gap','Delivery history no longer covers the local cursor',status=409,recovery='restore_state')
                generation=(lease['generation'] if lease else 0)+1
                conn.execute('INSERT OR REPLACE INTO delivery_leases VALUES(?,?,?,?,?)',(actor,runtime,connection,generation,now+self.s.ws_lease_seconds))
                generations[actor],acks[actor]=generation,mailbox['ack_seq']
        self.notify()
        return {'connectionId':connection,'generations':generations,'cursors':acks,
                'heartbeatSeconds':self.s.ws_heartbeat_seconds,'leaseSeconds':self.s.ws_lease_seconds}

    def lease(self,conn,owner,actor,connection,generation):
        self.identities.owned(owner,actor)
        lease=conn.execute('SELECT * FROM delivery_leases WHERE actor=?',(actor,)).fetchone()
        if not lease or lease['connection']!=connection or lease['generation']!=generation or lease['expires']<=self.clock():
            raise LabError('stale_generation','The connection no longer owns execution',status=409,recovery='stop_executor')
        return lease

    def heartbeat(self,owner,actors,connection,generations):
        with self.db.tx() as conn:
            for actor in actors:
                self.lease(conn,owner,actor,connection,generations[actor])
                conn.execute('UPDATE delivery_leases SET expires=? WHERE actor=?',(self.clock()+self.s.ws_lease_seconds,actor))

    def disconnect(self,connection):
        self.db.run('UPDATE delivery_leases SET expires=? WHERE connection=?',self.clock(),connection)
        self.unlisten(connection)

    def _operation(self,conn,actor,kind,operation,payload,apply):
        if not isinstance(operation,str) or not 1<=len(operation)<=128:raise LabError('invalid_input','A stable operationId is required')
        fingerprint=hashlib.sha256(encode(payload).encode()).hexdigest()
        old=conn.execute('SELECT * FROM delivery_operations WHERE actor=? AND kind=? AND id=?',(actor,kind,operation)).fetchone()
        if old:
            if old['fingerprint']!=fingerprint:raise LabError('id_conflict','Operation parameters changed',status=409)
            return json.loads(old['result'])
        key=hashlib.sha256(encode([actor,kind,operation]).encode()).hexdigest()
        if conn.execute('SELECT 1 FROM delivery_operation_ids WHERE digest=?',(key,)).fetchone():
            raise LabError('history_gap','Operation receipt has left retained history',status=409,recovery='restore_state')
        result=apply()
        conn.execute('INSERT INTO delivery_operation_ids VALUES(?)',(key,))
        conn.execute('INSERT INTO delivery_operations VALUES(?,?,?,?,?,?,?)',
                     (actor,kind,operation,fingerprint,encode(result),self.clock(),self.clock()+self.s.message_metadata_days*86400))
        return result

    def publish(self,owner,operation,payload):
        actor=self.identities.assistant(owner)
        with self.db.tx() as conn:
            result=self._operation(conn,actor,'interface.publish',operation,payload,lambda:self.interfaces.publish(conn,owner,payload))
        return result

    def operation(self,owner,actor,kind,operation):
        self.identities.owned(owner,actor)
        if not isinstance(kind,str) or not isinstance(operation,str):
            raise LabError('invalid_input','Operation type and identity must be strings')
        row=self.db.one('SELECT result FROM delivery_operations WHERE actor=? AND kind=? AND id=?',actor,kind,operation)
        if not row:raise LabError('not_ready','Operation has no accepted result',status=404,recovery='retry_original')
        return json.loads(row['result'])

    def control(self,owner,connection,generations,frame):
        actor,kind=frame.get('agentId'),frame.get('type')
        if not isinstance(kind,str):raise LabError('invalid_input','Control operation type must be a string')
        if not isinstance(actor,str) or actor not in generations or not isinstance(frame.get('generation'),int) or isinstance(frame['generation'],bool) or frame['generation']!=generations[actor]:
            raise LabError('stale_generation','Invalid connection identity or generation',status=409,recovery='stop_executor')
        payload=frame.get('payload',{})
        if not isinstance(payload,dict):raise LabError('invalid_input','Control payload must be an object')
        handlers={'message.send':self._send,'message.ack':self._ack,'request.start':self._start,'interaction.close':self._close}
        self.cleanup()
        inserted=False
        with self.db.tx() as conn:
            self.lease(conn,owner,actor,connection,generations[actor])
            if kind=='operation.get':
                result=self.operation(owner,actor,payload.get('operation'),payload.get('operationId'))
            elif kind in handlers:
                prior=conn.execute('SELECT 1 FROM delivery_messages WHERE id=?',(payload.get('messageId') if isinstance(payload.get('messageId'),str) else None,)).fetchone() if kind=='message.send' else None
                result=self._operation(conn,actor,kind,frame.get('operationId'),payload,
                                       lambda:handlers[kind](conn,actor,payload,generations[actor]))
                inserted=kind=='message.send' and not prior and result.get('messageId')==payload.get('messageId')
            else:raise LabError('protocol_error','Unsupported control operation')
            conn.execute('UPDATE delivery_leases SET expires=? WHERE actor=?',(self.clock()+self.s.ws_lease_seconds,actor))
        self.notify()
        if inserted and self.emit:
            message=self.db.one('SELECT recipient,kind,interaction FROM delivery_messages WHERE id=?',result['messageId'])
            if message:
                self.emit('message','message.queued',sender=actor,receiver=message['recipient'],
                          detail={'messageId':result['messageId'],'interactionId':message['interaction'],'kind':message['kind'],'trust':'verified'})
        return result

    @staticmethod
    def _row(conn,mid):
        mid=identifier(mid)
        row=conn.execute('SELECT * FROM delivery_messages WHERE id=?',(mid,)).fetchone()
        if not row:raise LabError('not_ready','Message is unavailable',status=404)
        return row

    def _enqueue(self,conn,envelope):
        recipient,mid=envelope['recipient'],envelope['messageId']
        digest=hashlib.sha256(mid.encode()).hexdigest()
        if conn.execute('SELECT 1 FROM delivery_ids WHERE digest=?',(digest,)).fetchone():
            raise LabError('id_conflict','Message identity has already been used',status=409)
        conn.execute('INSERT INTO delivery_ids VALUES(?)',(digest,))
        conn.execute('INSERT OR IGNORE INTO delivery_mailboxes(actor) VALUES(?)',(recipient,))
        conn.execute('UPDATE delivery_mailboxes SET last_seq=last_seq+1 WHERE actor=?',(recipient,))
        seq=conn.execute('SELECT last_seq FROM delivery_mailboxes WHERE actor=?',(recipient,)).fetchone()[0]
        now=self.clock()
        value={**envelope,'acceptedAt':stamp(now),'recipientSeq':seq}
        conn.execute('''INSERT INTO delivery_messages(id,sender,recipient,seq,kind,envelope,fingerprint,created,expires,retain_until,
                        delivery_state,request_state,interaction,origin,in_reply) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                     (mid,envelope['sender'],recipient,seq,envelope['kind'],encode(value),hashlib.sha256(encode(envelope).encode()).hexdigest(),
                      now,timestamp(envelope['expiresAt']),now+self.s.message_retention_days*86400,'queued',
                      'pending' if envelope['kind']=='message.request' else 'none',envelope.get('interactionId'),
                      envelope.get('originMessageId'),envelope.get('inReplyTo')))
        return self._receipt(self._row(conn,mid))

    @staticmethod
    def _receipt(row):
        return {'messageId':row['id'],'status':'accepted','recipientSeq':row['seq'],'acceptedAt':stamp(row['created'])}

    def _send(self,conn,actor,envelope,generation):
        allowed={'messageId','kind','recipient','interactionId','originMessageId','inReplyTo','expiresAt','body'}
        if set(envelope)-allowed or not isinstance(envelope.get('body'),dict):raise LabError('invalid_input','Invalid message envelope')
        if not isinstance(envelope.get('recipient'),str) or not isinstance(envelope.get('kind'),str):raise LabError('invalid_input','Recipient and kind are required')
        value={**envelope,'messageId':identifier(envelope.get('messageId')),'sender':actor}
        for key in ('interactionId','originMessageId','inReplyTo'):
            if key in value:value[key]=identifier(value[key],key)
        mid=value['messageId']
        old=conn.execute('SELECT * FROM delivery_messages WHERE id=?',(mid,)).fetchone()
        fingerprint=hashlib.sha256(encode(value).encode()).hexdigest()
        if old:
            if old['sender']!=actor or old['fingerprint']!=fingerprint:raise LabError('id_conflict','Message identity conflicts',status=409)
            return self._receipt(old)
        self.identities.agent(value.get('recipient'))
        expires=timestamp(value.get('expiresAt'))
        if not self.clock()<expires<=self.clock()+self.s.message_ttl_days*86400+1:
            raise LabError('expired','Message expiry is outside the delivery window',status=409)
        if len(encode(value['body']).encode())>32768:raise LabError('invalid_input','Message body exceeds 32 KiB')
        kind=value.get('kind')
        if kind=='message.request':self._request(conn,value)
        elif kind=='interaction.opened':
            previous=self._opened(conn,value)
            if previous:return self._receipt(previous)
        elif kind=='message.result':
            previous=self._result(conn,value)
            if previous:return self._receipt(previous)
        else:raise LabError('not_ready','This business message type is not enabled',status=409)
        result=self._enqueue(conn,value)
        if kind=='message.result':
            outcome=value['body']['outcome']
            request=self._row(conn,value['inReplyTo'])
            conn.execute('UPDATE delivery_messages SET request_state=?,result=?,result_id=?,result_hash=? WHERE id=?',
                         (outcome,encode(value['body']),mid,hashlib.sha256(encode(value['body']).encode()).hexdigest(),request['id']))
            if outcome in TERMINAL:
                self._retain(conn,request['interaction'],request['id'],request['sender'],request['recipient'])
            if outcome in TERMINAL and request['interaction']:
                interaction=conn.execute('SELECT * FROM delivery_interactions WHERE id=?',(request['interaction'],)).fetchone()
                pending=conn.execute("SELECT 1 FROM delivery_messages WHERE interaction=? AND kind='message.request' AND request_state NOT IN ('completed','rejected','failed','expired') LIMIT 1",(interaction['id'],)).fetchone()
                if interaction['requests']>=interaction['max_requests'] and not pending:
                    self._end(conn,interaction,'max_requests')
        return result

    def _request(self,conn,value):
        body=value['body']
        if set(body)!={'interfaceName','revision','input'}:raise LabError('invalid_input','Invalid assistant request body')
        if not isinstance(body['interfaceName'],str):raise LabError('invalid_input','Interface name must be a string')
        revision=body['revision']
        if not isinstance(revision,int) or isinstance(revision,bool):raise LabError('invalid_input','Integer revision required')
        current=self.interfaces.current(conn,value['recipient'],value['sender'],body['interfaceName'])
        if value.get('interactionId'):
            interaction=self._interaction(conn,value['interactionId'])
            if (value['sender'],value['recipient'],body['interfaceName'],revision)!=(interaction['sender'],interaction['recipient'],interaction['interface'],interaction['revision']):
                raise LabError('forbidden','Request does not belong to this interaction',status=403)
            self._open(interaction)
            if interaction['requests']>=interaction['max_requests']:raise LabError('quota_exceeded','Interaction request limit reached',status=429)
            if timestamp(value['expiresAt'])>interaction['deadline']:raise LabError('expired','Request cannot extend the interaction deadline',status=409)
            conn.execute('UPDATE delivery_interactions SET requests=requests+1 WHERE id=?',(interaction['id'],))
        elif revision!=current['revision']:
            raise LabError('revision_conflict','Read the current interface revision',status=409)
        if 'inReplyTo' in value or 'originMessageId' in value:raise LabError('invalid_input','New requests cannot impersonate replies')
        definition=self.interfaces.version(conn,value['recipient'],body['interfaceName'],revision)
        self.interfaces.check(definition['inputSchema'],body['input'])
        owner=self.identities.agent(value['recipient'])['owner_id']
        queued=conn.execute("SELECT COUNT(*) FROM delivery_messages m JOIN agents a ON m.recipient=a.id WHERE a.owner_id=? AND m.kind='message.request' AND m.request_state NOT IN ('completed','rejected','failed','expired')",(owner,)).fetchone()[0]
        if queued>=self.s.message_pending_limit:raise LabError('quota_exceeded','Recipient queue is full',status=429)
        source_owner=self.identities.agent(value['sender'])['owner_id']
        recent=conn.execute('SELECT COUNT(*) FROM delivery_messages m JOIN agents a ON m.sender=a.id WHERE a.owner_id=? AND m.created>? AND m.kind=?',(source_owner,self.clock()-60,'message.request')).fetchone()[0]
        pair=conn.execute("SELECT COUNT(*) FROM delivery_messages WHERE sender=? AND recipient=? AND created>? AND kind='message.request'",(value['sender'],value['recipient'],self.clock()-60)).fetchone()[0]
        if recent>=self.s.message_rate or pair>=min(20,self.s.message_rate):raise LabError('quota_exceeded','Message rate exceeded',status=429)

    def _opened(self,conn,value):
        origin=self._row(conn,value.get('originMessageId'))
        body=value['body']
        if origin['kind']!='message.request' or (value['sender'],value['recipient'])!=(origin['recipient'],origin['sender']):
            raise LabError('forbidden','Only the original recipient can establish the interaction',status=403)
        if origin['expires']<=self.clock() or conn.execute('SELECT 1 FROM delivery_closed_origins WHERE origin=?',(origin['id'],)).fetchone():
            raise LabError('closed','Original request can no longer establish an interaction',status=409)
        original=json.loads(origin['envelope'])['body']
        if body.get('interactionType')!='messaging' or body.get('interfaceName')!=original['interfaceName'] or body.get('revision')!=original['revision']:
            raise LabError('id_conflict','Interaction must preserve the original interface',status=409)
        iid=identifier(value.get('interactionId'),'interactionId')
        value['interactionId']=iid
        deadline=timestamp(body.get('deadline'))
        maximum=body.get('maxRequests')
        if not isinstance(maximum,int) or isinstance(maximum,bool) or not 1<=maximum<=8 or not self.clock()<deadline<=origin['expires']:
            raise LabError('invalid_input','Invalid interaction deadline or request limit')
        old=conn.execute('SELECT * FROM delivery_interactions WHERE id=? OR origin=?',(iid,origin['id'])).fetchone()
        if old:
            if (old['id'],old['origin'],old['sender'],old['recipient'],old['interface'],old['revision'],old['deadline'],old['max_requests'])!=(iid,origin['id'],origin['sender'],origin['recipient'],original['interfaceName'],original['revision'],deadline,maximum):
                raise LabError('id_conflict','Interaction identity conflicts',status=409)
        else:
            conn.execute('INSERT INTO delivery_interactions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                         (iid,origin['id'],origin['sender'],origin['recipient'],'messaging',original['interfaceName'],original['revision'],deadline,maximum,1,'open',None))
            conn.execute('UPDATE delivery_messages SET interaction=? WHERE id=?',(iid,origin['id']))
        if old:
            self._open(old)
        if origin['request_state'] in TERMINAL:
            raise LabError('closed','Original request already has a terminal outcome',status=409)
        return conn.execute("SELECT * FROM delivery_messages WHERE origin=? AND kind='interaction.opened' ORDER BY seq LIMIT 1",(origin['id'],)).fetchone()

    @staticmethod
    def _interaction(conn,iid):
        iid=identifier(iid,'interactionId')
        row=conn.execute('SELECT * FROM delivery_interactions WHERE id=?',(iid,)).fetchone()
        if not row:raise LabError('not_ready','Interaction has not been established',status=409,recovery='await_open')
        return row

    def _open(self,interaction):
        if interaction['state']!='open' or interaction['deadline']<=self.clock():
            raise LabError('closed','Interaction no longer accepts new work',status=409)

    def _start(self,conn,actor,payload,generation):
        request=self._row(conn,payload.get('messageId'))
        if request['recipient']!=actor or request['kind']!='message.request':raise LabError('forbidden','Only the recipient can begin this request',status=403)
        if request['interaction']!=payload.get('interactionId'):raise LabError('id_conflict','Request interaction does not match',status=409)
        interaction=self._interaction(conn,request['interaction'])
        if request['started'] is not None:
            return {'messageId':request['id'],'startedAt':stamp(request['started']),'resumed':True,'originalGeneration':request['start_generation'],'requestState':request['request_state']}
        self._open(interaction)
        if request['expires']<=self.clock():raise LabError('expired','Request expired before processing',status=409)
        if request['request_state'] in TERMINAL:raise LabError('closed','Request already has a final result',status=409)
        self.interfaces.current(conn,actor,request['sender'],interaction['interface'])
        conn.execute("UPDATE delivery_messages SET started=?,start_generation=?,request_state='processing' WHERE id=?",(self.clock(),generation,request['id']))
        return {'messageId':request['id'],'startedAt':stamp(self.clock()),'resumed':False,'requestState':'processing'}

    def _result(self,conn,value):
        request=self._row(conn,value.get('inReplyTo'))
        if request['kind']!='message.request' or (value['sender'],value['recipient'])!=(request['recipient'],request['sender']):
            raise LabError('forbidden','Result must return to the original requester',status=403)
        body=value['body'];outcome=body.get('outcome')
        if not isinstance(outcome,str) or outcome not in {'completed','rejected','failed','unknown'}:raise LabError('invalid_input','Invalid business outcome')
        if request['interaction']:
            if value.get('interactionId')!=request['interaction']:raise LabError('id_conflict','Result interaction does not match',status=409)
        elif value.get('originMessageId')!=request['id'] or outcome not in {'rejected','failed'}:
            raise LabError('invalid_input','Pre-establishment rejection must reference the origin')
        if request['request_state'] in TERMINAL and request['result_id']:
            if hashlib.sha256(encode(body).encode()).hexdigest()==request['result_hash'] or outcome=='unknown':return self._row(conn,request['result_id'])
            raise LabError('id_conflict','Request already has an immutable result',status=409)
        if request['request_state']=='unknown' and outcome=='unknown':
            if hashlib.sha256(encode(body).encode()).hexdigest()==request['result_hash']:
                return self._row(conn,request['result_id'])
            raise LabError('id_conflict','An unknown report already exists; reconcile the original operation',status=409)
        if outcome=='unknown' and request['started'] is None:raise LabError('forbidden','An unknown execution outcome needs an original start permit',status=403)
        if outcome=='completed':
            if request['started'] is None:raise LabError('forbidden','A completed result needs a start permit',status=403)
            original=json.loads(request['envelope'])['body']
            definition=self.interfaces.version(conn,request['recipient'],original['interfaceName'],original['revision'])
            self.interfaces.check(definition['outputSchema'],body.get('output'))
        elif not isinstance(body.get('error'),dict):raise LabError('invalid_input','Non-success outcome needs a structured error')

    def _ack(self,conn,actor,payload,generation):
        message=self._row(conn,payload.get('messageId'))
        through=payload.get('through')
        if message['recipient']!=actor:raise LabError('forbidden','Only the recipient can acknowledge delivery',status=403)
        mailbox=conn.execute('SELECT * FROM delivery_mailboxes WHERE actor=?',(actor,)).fetchone()
        if not isinstance(through,int) or isinstance(through,bool) or through!=message['seq'] or not mailbox['ack_seq']<=through<=mailbox['last_seq']:
            raise LabError('invalid_input','Invalid continuous acknowledgement cursor')
        conn.execute('UPDATE delivery_mailboxes SET ack_seq=? WHERE actor=?',(through,actor))
        conn.execute("UPDATE delivery_messages SET delivery_state='delivered' WHERE recipient=? AND seq<=? AND delivery_state='queued'",(actor,through))
        return {'through':through}

    def _end(self,conn,interaction,reason):
        if interaction['state']=='closed':return
        conn.execute("UPDATE delivery_interactions SET state='closed',closed_at=? WHERE id=?",(self.clock(),interaction['id']))
        conn.execute("UPDATE delivery_messages SET request_state='rejected' WHERE interaction=? AND started IS NULL AND request_state='pending'",(interaction['id'],))
        self._retain(conn,interaction['id'],interaction['origin'],interaction['sender'],interaction['recipient'])
        self._closed_notices(conn,interaction['origin'],interaction['sender'],interaction['recipient'],reason,interaction['id'])

    def _retain(self,conn,interaction,origin,sender,recipient):
        conn.execute('UPDATE delivery_messages SET retain_until=MAX(retain_until,?) WHERE interaction=? OR id=? OR origin=?',
                     (self.clock()+self.s.message_retention_days*86400,interaction,origin,origin))
        conn.execute('UPDATE delivery_operations SET retain_until=MAX(retain_until,?) WHERE actor IN (?,?)',
                     (self.clock()+self.s.message_metadata_days*86400,sender,recipient))

    def _closed_notices(self,conn,origin,sender,recipient,reason,iid=None):
        for actor in {sender,recipient}:
            value={'messageId':str(uuid.uuid4()),'kind':'interaction.closed','sender':SYSTEM,'recipient':actor,
                   'originMessageId':origin,'expiresAt':stamp(self.clock()+self.s.message_ttl_days*86400),
                   'body':{'reason':str(reason)[:200],'closedAt':stamp(self.clock())}}
            if iid:value['interactionId']=iid
            self._enqueue(conn,value)

    def _close(self,conn,actor,payload,generation):
        if payload.get('interactionId'):
            interaction=self._interaction(conn,payload['interactionId'])
            if actor not in (interaction['sender'],interaction['recipient']):raise LabError('forbidden','Interaction belongs to other agents',status=403)
            self._end(conn,interaction,payload.get('reason','closed'))
            return {'interactionId':interaction['id'],'state':'closed'}
        request=self._row(conn,payload.get('originMessageId'))
        if request['kind']!='message.request' or actor not in (request['sender'],request['recipient']):raise LabError('forbidden','Original request belongs to other agents',status=403)
        if request['interaction']:
            self._end(conn,self._interaction(conn,request['interaction']),payload.get('reason','closed'))
        elif not conn.execute('SELECT 1 FROM delivery_closed_origins WHERE origin=?',(request['id'],)).fetchone():
            conn.execute('INSERT INTO delivery_closed_origins VALUES(?,?,?)',(request['id'],str(payload.get('reason','closed'))[:200],self.clock()))
            conn.execute("UPDATE delivery_messages SET request_state='rejected' WHERE id=? AND started IS NULL AND request_state='pending'",(request['id'],))
            self._retain(conn,None,request['id'],request['sender'],request['recipient'])
            self._closed_notices(conn,request['id'],request['sender'],request['recipient'],payload.get('reason','closed'))
        return {'originMessageId':request['id'],'state':'closed'}

    def interaction(self,owner,actor,*,origin=None,iid=None):
        self.cleanup()
        self.identities.owned(owner,actor)
        row=self.db.one('SELECT * FROM delivery_interactions WHERE '+('id=?' if iid else 'origin=?'),iid or origin)
        if row:
            if actor not in (row['sender'],row['recipient']):raise LabError('forbidden','Interaction is unavailable',status=404)
            return {'interactionId':row['id'],'originMessageId':row['origin'],'state':'closed' if row['deadline']<=self.clock() else row['state'],
                    'sender':row['sender'],'recipient':row['recipient'],'interfaceName':row['interface'],'revision':row['revision'],
                    'deadline':stamp(row['deadline']),'maxRequests':row['max_requests'],'requests':row['requests']}
        request=self.db.one('SELECT * FROM delivery_messages WHERE id=?',origin)
        if not request or actor not in (request['sender'],request['recipient']):raise LabError('forbidden','Interaction is unavailable',status=404)
        closed=self.db.one('SELECT 1 FROM delivery_closed_origins WHERE origin=?',origin)
        return {'originMessageId':origin,'interactionId':None,'state':'closed' if closed else request['request_state']}

    def message(self,owner,actor,mid):
        self.cleanup()
        self.identities.owned(owner,actor)
        row=self.db.one('SELECT * FROM delivery_messages WHERE id=?',mid)
        if not row or actor not in (row['sender'],row['recipient']):raise LabError('forbidden','Message is unavailable',status=404)
        return {'message':json.loads(row['envelope']) if row['envelope'] else None,'messageId':mid,'deliveryState':row['delivery_state'],
                'requestState':row['request_state'],'interactionId':row['interaction'],'result':json.loads(row['result']) if row['result'] else None}

    def deliveries(self,owner,actor,connection,generation,after,limit=100):
        with self.db.lock:
            self.lease(self.db.conn,owner,actor,connection,generation)
            rows=self.db.conn.execute('SELECT * FROM delivery_messages WHERE recipient=? AND seq>? ORDER BY seq LIMIT ?',(actor,after,limit)).fetchall()
            return [{'type':'message.deliver','agentId':actor,'recipientSeq':r['seq'],'deliveryState':r['delivery_state'],
                     'message':json.loads(r['envelope']) if r['envelope'] else {'messageId':r['id'],'kind':r['kind'],'recipientSeq':r['seq'],'payloadAvailable':False}}
                    for r in rows]

    def cleanup(self):
        now=self.clock()
        with self.db.tx() as conn:
            conn.execute("UPDATE delivery_messages SET request_state='expired',retain_until=MAX(retain_until,?) WHERE kind='message.request' AND started IS NULL AND expires<=? AND request_state='pending'",
                         (now+self.s.message_retention_days*86400,now))
            conn.execute("UPDATE delivery_messages SET delivery_state='expired' WHERE expires<=? AND delivery_state='queued'",(now,))
            for interaction in conn.execute("SELECT * FROM delivery_interactions WHERE state='open' AND deadline<=?",(now,)).fetchall():
                self._end(conn,interaction,'deadline')
            protected="COALESCE(interaction IN (SELECT id FROM delivery_interactions WHERE state='open'),0) OR COALESCE(interaction IN (SELECT interaction FROM delivery_messages WHERE kind='message.request' AND request_state IN ('processing','unknown')),0) OR (kind='message.request' AND request_state IN ('processing','unknown'))"
            conn.execute(f'UPDATE delivery_messages SET envelope=NULL,result=NULL WHERE retain_until<=? AND NOT ({protected})',(now,))
            cutoff=now-self.s.message_metadata_days*86400
            # Retain the complete relation while any request may still have external effects.
            active="SELECT sender FROM delivery_messages WHERE "+protected+" UNION SELECT recipient FROM delivery_messages WHERE "+protected
            conn.execute(f'DELETE FROM delivery_operations WHERE retain_until<? AND actor NOT IN ({active})',(now,))
            eligible=f'MAX(created,retain_until-?)<? AND NOT ({protected})'
            # Only remove a continuous prefix. A retained earlier sequence is a barrier,
            # including when its body is already redacted or its execution is unknown.
            conn.execute(f'''WITH barriers AS (
                SELECT recipient,MIN(seq) AS first_kept FROM delivery_messages
                WHERE NOT ({eligible}) GROUP BY recipient
            ) DELETE FROM delivery_messages WHERE {eligible}
              AND seq<COALESCE((SELECT first_kept FROM barriers WHERE barriers.recipient=delivery_messages.recipient),9223372036854775807)''',
                         (self.s.message_retention_days*86400,cutoff,self.s.message_retention_days*86400,cutoff))
            conn.execute('DELETE FROM delivery_closed_origins WHERE closed_at<? AND origin NOT IN (SELECT id FROM delivery_messages)',(cutoff,))
            conn.execute("DELETE FROM delivery_interactions WHERE state='closed' AND closed_at<? AND id NOT IN (SELECT interaction FROM delivery_messages WHERE interaction IS NOT NULL)",(cutoff,))
