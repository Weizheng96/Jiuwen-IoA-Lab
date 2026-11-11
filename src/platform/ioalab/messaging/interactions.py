"""Interaction establishment, start permits, acknowledgements, closing and system notices. Part of DeliveryBroker."""
import json
import uuid

from ..errors import LabError
from .envelope import SYSTEM, TERMINAL, identifier, stamp, timestamp


class Interactions:
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

    def contract_closed_in(self,conn,iid,reason='contract-closed'):
        """Callers close only after original market/payment/admission validation or a finished ruling."""
        interaction=self._interaction(conn,iid)
        if interaction['kind']!='delegation' or not self.admissions.can_close_in(conn,interaction['origin']):
            raise LabError('not_ready','Reconcile the original contract before closing its delivery relation',status=409)
        self._end(conn,interaction,reason)

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
