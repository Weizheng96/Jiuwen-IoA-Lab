"""Durable participant delivery and interaction authority. No agent execution."""
from __future__ import annotations
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import logging
import threading
import time
import uuid
from ..errors import LabError
from .interfaces import PublicInterfaces

SYSTEM='urn:ioa:platform'
log=logging.getLogger(__name__)
TERMINAL={'completed','rejected','failed','expired'}


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
    CONTROLS=frozenset(('message.send','message.ack','request.start','interaction.close','contract.bind'))

    def __init__(self,db,identities,settings,*,record_in=None,publish=None,clock=time.time):
        self.db,self.identities,self.s,self.clock=db,identities,settings,clock
        self.record_in,self.publish_event=record_in,publish
        self.interfaces=PublicInterfaces(db,identities)
        self.admissions=None
        self.protocol=None
        self.observe_in=None
        self._listeners={}
        self._listener_lock=threading.RLock()

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
                self.renew_in(conn,actor)

    def active_in(self,conn,actor):
        self.identities.agent(actor)
        row=conn.execute('SELECT expires FROM delivery_leases WHERE actor=?',(actor,)).fetchone()
        return bool(row and row['expires']>self.clock())

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
        """Original result of an operation this module accepted (its control kinds and interface publication)."""
        self.identities.owned(owner,actor)
        if not isinstance(kind,str) or not isinstance(operation,str):
            raise LabError('invalid_input','Operation type and identity must be strings')
        row=self.db.one('SELECT result FROM delivery_operations WHERE actor=? AND kind=? AND id=?',actor,kind,operation)
        if not row:
            key=hashlib.sha256(encode([actor,kind,operation]).encode()).hexdigest()
            if self.db.one('SELECT 1 FROM delivery_operation_ids WHERE digest=?',key):
                raise LabError('history_gap','Operation receipt has left retained history',status=409,recovery='restore_state')
            raise LabError('not_ready','Operation has no accepted result',status=404,recovery='retry_original')
        return json.loads(row['result'])

    @staticmethod
    def control_frame(generations,frame):
        """Check the frame's agent and generation against this connection; return (actor,kind,payload)."""
        actor,kind=frame.get('agentId'),frame.get('type')
        if not isinstance(kind,str):raise LabError('invalid_input','Control operation type must be a string')
        if not isinstance(actor,str) or actor not in generations or not isinstance(frame.get('generation'),int) or isinstance(frame['generation'],bool) or frame['generation']!=generations[actor]:
            raise LabError('stale_generation','Invalid connection identity or generation',status=409,recovery='stop_executor')
        payload=frame.get('payload',{})
        if not isinstance(payload,dict):raise LabError('invalid_input','Control payload must be an object')
        return actor,kind,payload

    def renew_in(self,conn,actor):
        """An accepted control operation renews the execution lease inside its transaction."""
        conn.execute('UPDATE delivery_leases SET expires=? WHERE actor=?',(self.clock()+self.s.ws_lease_seconds,actor))

    def control_in(self,conn,actor,kind,operation,payload,generation):
        """Commit one of CONTROLS after the caller rechecked the lease; returns (result, activity facts or None)."""
        handlers={'message.send':self._send,'message.ack':self._ack,'request.start':self._start,'interaction.close':self._close,'contract.bind':self._bind_contract}
        fresh=not conn.execute('SELECT 1 FROM delivery_operations WHERE actor=? AND kind=? AND id=?',(actor,kind,operation)).fetchone()
        prior=conn.execute('SELECT 1 FROM delivery_messages WHERE id=?',(payload.get('messageId') if isinstance(payload.get('messageId'),str) else None,)).fetchone() if kind=='message.send' else None
        result=self._operation(conn,actor,kind,operation,payload,lambda:handlers[kind](conn,actor,payload,generation))
        if kind=='message.send':return result,{'inserted':not prior and result.get('messageId')==payload.get('messageId')}
        return result,({} if kind=='request.start' and fresh else None)

    def activity_in(self,conn,frame,result,**facts):
        """Record accepted-control activity with the operation; publish_activity runs after commit."""
        if not self.observe_in:return []
        activity=self.observe_in(conn,frame,result,**facts)
        if activity and not self.record_in:raise RuntimeError('Activity history requires its owned transaction writer')
        return [self.record_in(conn,**event) for event in activity]

    def publish_activity(self,recorded):
        if not self.publish_event:return
        for event in recorded:
            try:self.publish_event(event)
            except Exception:
                # The accepted operation, original response and event are already
                # durable. A display/file/subscriber fault must not undo acceptance
                # or cause a caller to execute the business a second time.
                log.exception('Activity is persisted; live publication failed for event %s',event['seq'])

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
                      'pending' if envelope['kind'] in ('message.request','delegation.intent','a2c.request') else 'none',envelope.get('interactionId'),
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
        elif kind=='delegation.intent':
            if not self.admissions:raise LabError('not_ready','Delegation admission is unavailable',status=409)
            if any(key in value for key in ('interactionId','originMessageId','inReplyTo')):raise LabError('invalid_input','A delegation starts with a new original intent')
            self._quota(conn,value)
            self.admissions.intent_in(conn,actor,value['recipient'],mid,value['body'],expires)
        elif kind=='interaction.opened':
            previous=self._opened(conn,value)
            if previous:return self._receipt(previous)
        elif kind=='message.result':
            previous=self._result(conn,value)
            if previous:return self._receipt(previous)
        elif kind=='delegation.result':
            previous=self._delegation_result(conn,value)
            if previous:return self._receipt(previous)
        elif kind=='a2c.request':
            if not self.protocol:raise LabError('not_ready','A2C relay is not configured',status=409)
            interaction=self._interaction(conn,value.get('interactionId'))
            if interaction['kind']!='delegation' or interaction['state']!='open':raise LabError('closed','Delegation no longer accepts protocol requests',status=409)
            self.protocol.request_in(conn,value)
        elif kind=='a2c.result':
            request=self._row(conn,value.get('inReplyTo'))
            if request['kind']!='a2c.request':raise LabError('forbidden','A2C result needs an original protocol request',status=403)
            previous=self.protocol.result_in(conn,value,request)
            if previous:return self._receipt(self._row(conn,previous))
        else:raise LabError('not_ready','This business message type is not enabled',status=409)
        result=self._enqueue(conn,value)
        if kind=='a2c.result':
            state=value['body']['reply'].get('state')
            if state is None:state='completed'
            conn.execute('UPDATE delivery_messages SET request_state=?,result=? WHERE id=?',
                         (state,encode(value['body']),value['inReplyTo']))
        if kind in ('message.result','delegation.result'):
            outcome='completed' if value['body']['outcome']=='reserved' else value['body']['outcome']
            request=self._row(conn,value['inReplyTo'])
            conn.execute('UPDATE delivery_messages SET request_state=?,result=?,result_id=?,result_hash=? WHERE id=?',
                         (outcome,encode(value['body']),mid,hashlib.sha256(encode(value['body']).encode()).hexdigest(),request['id']))
            if outcome in TERMINAL:
                self._retain(conn,request['interaction'],request['id'],request['sender'],request['recipient'])
            if kind=='message.result' and outcome in TERMINAL and request['interaction']:
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
        self._quota(conn,value)

    def _quota(self,conn,value):
        recipients=self.identities.agent_ids(self.identities.agent(value['recipient'])['owner_id'])
        queued=conn.execute("SELECT COUNT(*) FROM delivery_messages WHERE recipient IN ("+','.join('?'*len(recipients))+") AND kind IN ('message.request','delegation.intent') AND request_state NOT IN ('completed','rejected','failed','expired')",recipients).fetchone()[0]
        if queued>=self.s.message_pending_limit:raise LabError('quota_exceeded','Recipient queue is full',status=429)
        senders=self.identities.agent_ids(self.identities.agent(value['sender'])['owner_id'])
        recent=conn.execute("SELECT COUNT(*) FROM delivery_messages WHERE sender IN ("+','.join('?'*len(senders))+") AND created>? AND kind IN ('message.request','delegation.intent')",(*senders,self.clock()-60)).fetchone()[0]
        pair=conn.execute("SELECT COUNT(*) FROM delivery_messages WHERE sender=? AND recipient=? AND created>? AND kind IN ('message.request','delegation.intent')",(value['sender'],value['recipient'],self.clock()-60)).fetchone()[0]
        if recent>=self.s.message_rate or pair>=min(20,self.s.message_rate):raise LabError('quota_exceeded','Message rate exceeded',status=429)

    def _opened(self,conn,value):
        origin=self._row(conn,value.get('originMessageId'))
        body=value['body']
        if origin['kind'] not in ('message.request','delegation.intent') or (value['sender'],value['recipient'])!=(origin['recipient'],origin['sender']):
            raise LabError('forbidden','Only the original recipient can establish the interaction',status=403)
        if origin['expires']<=self.clock() or conn.execute('SELECT 1 FROM delivery_closed_origins WHERE origin=?',(origin['id'],)).fetchone():
            raise LabError('closed','Original request can no longer establish an interaction',status=409)
        original=json.loads(origin['envelope'])['body']
        kind='delegation' if origin['kind']=='delegation.intent' else 'messaging'
        if kind=='messaging':
            if body.get('interactionType')!=kind or body.get('interfaceName')!=original['interfaceName'] or body.get('revision')!=original['revision']:
                raise LabError('id_conflict','Interaction must preserve the original interface',status=409)
            interface,revision=original['interfaceName'],original['revision']
        else:
            if set(body)!={'interactionType','bindingDigest','deadline'} or body.get('interactionType')!=kind or body.get('bindingDigest')!=original['bindingDigest']:
                raise LabError('id_conflict','Interaction must preserve the original delegation binding',status=409)
            interface,revision='',0
        iid=identifier(value.get('interactionId'),'interactionId')
        value['interactionId']=iid
        deadline=timestamp(body.get('deadline'))
        maximum=body.get('maxRequests') if kind=='messaging' else 1
        if not isinstance(maximum,int) or isinstance(maximum,bool) or not 1<=maximum<=8 or not self.clock()<deadline<=origin['expires']:
            raise LabError('invalid_input','Invalid interaction deadline or request limit')
        old=conn.execute('SELECT * FROM delivery_interactions WHERE id=? OR origin=?',(iid,origin['id'])).fetchone()
        if old:
            if (old['id'],old['origin'],old['sender'],old['recipient'],old['kind'],old['interface'],old['revision'],old['deadline'],old['max_requests'])!=(iid,origin['id'],origin['sender'],origin['recipient'],kind,interface,revision,deadline,maximum):
                raise LabError('id_conflict','Interaction identity conflicts',status=409)
        else:
            conn.execute('INSERT INTO delivery_interactions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                         (iid,origin['id'],origin['sender'],origin['recipient'],kind,interface,revision,deadline,maximum,1,'open',None))
            conn.execute('UPDATE delivery_messages SET interaction=? WHERE id=?',(iid,origin['id']))
        if kind=='delegation':self.admissions.link_in(conn,value['sender'],origin['id'],iid,original['bindingDigest'])
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

    def _bind_contract(self,conn,actor,payload,generation):
        if not self.protocol:raise LabError('not_ready','Established contract authority is unavailable',status=409)
        owner=self.identities.agent(actor)['owner_id']
        return self.protocol.bind_in(conn,owner,actor,payload)

    def _start(self,conn,actor,payload,generation):
        request=self._row(conn,payload.get('messageId'))
        if request['recipient']!=actor or request['kind'] not in ('message.request','delegation.intent','a2c.request'):raise LabError('forbidden','Only the recipient can begin this request',status=403)
        if request['interaction']!=payload.get('interactionId'):raise LabError('id_conflict','Request interaction does not match',status=409)
        interaction=self._interaction(conn,request['interaction'])
        authority=self.protocol.context_in(conn,json.loads(request['envelope'])) if request['kind']=='a2c.request' else {}
        if request['started'] is not None:
            return {'messageId':request['id'],'startedAt':stamp(request['started']),'resumed':True,'originalGeneration':request['start_generation'],'requestState':request['request_state'],**authority}
        if request['kind']!='a2c.request':self._open(interaction)
        if request['expires']<=self.clock():raise LabError('expired','Request expired before processing',status=409)
        if request['request_state'] in TERMINAL:raise LabError('closed','Request already has a final result',status=409)
        if request['kind']=='message.request':self.interfaces.current(conn,actor,request['sender'],interaction['interface'])
        elif request['kind']=='delegation.intent':self.admissions.start_in(conn,actor,request['id'])
        else:self.protocol.start_in(conn,json.loads(request['envelope']))
        conn.execute("UPDATE delivery_messages SET started=?,start_generation=?,request_state='processing' WHERE id=?",(self.clock(),generation,request['id']))
        return {'messageId':request['id'],'startedAt':stamp(self.clock()),'resumed':False,'requestState':'processing',**authority}

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

    def _delegation_result(self,conn,value):
        request=self._row(conn,value.get('inReplyTo'))
        if request['kind']!='delegation.intent' or (value['sender'],value['recipient'])!=(request['recipient'],request['sender']):
            raise LabError('forbidden','A delegation result returns only to its original requester',status=403)
        body=value['body'];outcome=body.get('outcome')
        if outcome not in ('reserved','rejected','failed','unknown'):raise LabError('invalid_input','Invalid delegation outcome')
        if request['interaction']:
            if value.get('interactionId')!=request['interaction']:raise LabError('id_conflict','Delegation result changed its interaction',status=409)
        elif value.get('originMessageId')!=request['id'] or outcome not in ('rejected','failed'):
            raise LabError('forbidden','An unestablished intent may only be rejected or failed',status=403)
        fingerprint=hashlib.sha256(encode(body).encode()).hexdigest()
        if request['request_state'] in TERMINAL and request['result_id']:
            if fingerprint==request['result_hash'] or outcome=='unknown':return self._row(conn,request['result_id'])
            raise LabError('id_conflict','Intent already has an immutable terminal result',status=409)
        if request['request_state']=='unknown' and outcome=='unknown':
            if fingerprint==request['result_hash']:return self._row(conn,request['result_id'])
            raise LabError('id_conflict','Recover the original unknown outcome',status=409)
        if outcome in ('reserved','unknown') and request['started'] is None:raise LabError('forbidden','Delegation result requires its original start permit',status=403)
        if outcome=='reserved':
            row=self.admissions.admission(conn,body.get('admissionId'))
            if row['state']=='released' or (row['state']=='held' and row['hold_until']<=self.clock()):raise LabError('expired','The admission can no longer be reported as reserved',status=409)
            receipt=self.admissions.receipt(row)
            if any(body.get(key)!=receipt[key] for key in ('admissionId','bindingDigest','holdUntil')) or row['interaction']!=request['interaction'] or row['origin']!=request['id']:
                raise LabError('forbidden','Result differs from the platform admission receipt',status=403)
        elif not isinstance(body.get('error'),dict):raise LabError('invalid_input','A failed delegation needs a structured error')

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


    def ruling_in(self,conn,admission,ruling):
        """System-only reliable notices commit with the original case and market."""
        row=self.admissions.admission(conn,admission)
        for actor in (row['client'],row['service']):
            self._enqueue(conn,{'messageId':str(uuid.uuid4()),
                'kind':'contract.ruling','sender':SYSTEM,'recipient':actor,'interactionId':row['interaction'],
                'originMessageId':row['origin'],'expiresAt':stamp(self.clock()+self.s.message_ttl_days*86400),
                'body':{'caseRef':ruling['caseRef'],'admissionId':admission,'subject':ruling['subject'],'ruling':ruling}})

    def contract_closed_in(self,conn,iid):
        """Application calls only after original market/payment/admission validation."""
        interaction=self._interaction(conn,iid)
        if interaction['kind']!='delegation' or not self.admissions.can_close_in(conn,interaction['origin']):
            raise LabError('not_ready','Reconcile the original contract before closing its delivery relation',status=409)
        self._end(conn,interaction,'contract-closed')

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
            if interaction['kind']=='delegation':self.admissions.close_in(conn,interaction['origin'])
            self._end(conn,interaction,payload.get('reason','closed'))
            return {'interactionId':interaction['id'],'state':'closed'}
        request=self._row(conn,payload.get('originMessageId'))
        if request['kind'] not in ('message.request','delegation.intent') or actor not in (request['sender'],request['recipient']):raise LabError('forbidden','Original request belongs to other agents',status=403)
        if request['kind']=='delegation.intent':self.admissions.close_in(conn,request['id'])
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
            result={'interactionId':row['id'],'originMessageId':row['origin'],'state':row['state'],
                    'sender':row['sender'],'recipient':row['recipient'],'interfaceName':row['interface'],'revision':row['revision'],
                    'deadline':stamp(row['deadline']),'maxRequests':row['max_requests'],'requests':row['requests'],'interactionType':row['kind']}
            if row['kind']=='delegation':
                with self.db.lock:result['bindingDigest']=self.admissions.relation(self.db.conn,row['id'])['binding']
            return result
        request=self.db.one('SELECT * FROM delivery_messages WHERE id=?',origin)
        if not request or actor not in (request['sender'],request['recipient']):raise LabError('forbidden','Interaction is unavailable',status=404)
        closed=self.db.one('SELECT 1 FROM delivery_closed_origins WHERE origin=?',origin)
        return {'originMessageId':origin,'interactionId':None,'state':'closed' if closed else request['request_state']}

    def protocol_history_in(self,conn,message):
        """Internal original-message reader for the protocol owner; no cleanup."""
        try:request=self._row(conn,message)
        except LabError as exc:raise LabError('history_gap','Original protocol request has left retained history',status=409,recovery='restore_state') from exc
        if request['kind']!='a2c.request':raise LabError('history_gap','Original protocol request is unavailable',status=409,recovery='restore_state')
        replies=conn.execute("SELECT * FROM delivery_messages WHERE in_reply=? AND kind='a2c.result' ORDER BY created,seq",(message,)).fetchall()
        if request['envelope'] is None or any(r['envelope'] is None for r in replies):
            raise LabError('history_gap','Original protocol evidence has left retained history',status=409,recovery='restore_state')
        return {'request':json.loads(request['envelope']),'startedAt':stamp(request['started']) if request['started'] is not None else None,
            'results':[json.loads(r['envelope']) for r in replies]}

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
        now=self.clock();changed=False
        with self.db.tx() as conn:
            conn.execute("UPDATE delivery_messages SET request_state='expired',retain_until=MAX(retain_until,?) WHERE kind IN ('message.request','delegation.intent') AND started IS NULL AND expires<=? AND request_state='pending'",
                         (now+self.s.message_retention_days*86400,now))
            conn.execute("UPDATE delivery_messages SET delivery_state='expired' WHERE expires<=? AND delivery_state='queued'",(now,))
            if self.admissions is not None and self.protocol is not None:
                for admission in self.admissions.expired_proposals_in(conn):
                    interaction=conn.execute('SELECT * FROM delivery_interactions WHERE id=?',(admission['interaction'],)).fetchone()
                    if interaction is None or interaction['state']!='open' or not self.protocol.proposal_finished_in(conn,admission['id'],self.protocol_history_in):continue
                    self.admissions.finish_in(conn,admission['id'])
                    self._end(conn,interaction,'proposal-expired');changed=True
            for interaction in conn.execute("SELECT * FROM delivery_interactions WHERE state='open' AND deadline<=?",(now,)).fetchall():
                if interaction['kind']=='delegation':
                    if not self.admissions.can_close_in(conn,interaction['origin']):continue
                    self.admissions.close_in(conn,interaction['origin'])
                self._end(conn,interaction,'deadline');changed=True
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
        if changed:self.notify()
