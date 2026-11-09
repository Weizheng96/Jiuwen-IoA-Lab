"""Envelope validation, per-kind checks, quotas and enqueueing of business messages. Part of DeliveryBroker."""
import hashlib
import json

from ..errors import LabError
from .envelope import TERMINAL, encode, identifier, stamp, timestamp


class Sending:
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
