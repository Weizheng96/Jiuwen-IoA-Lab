"""Intent bindings and capacity. No participant execution or copied A2C state machine."""
from decimal import Decimal
from datetime import datetime,timezone
import json
import time
import uuid

from a2c.card import card_digest,request_digest,validate_card,validate_request,verify_request
from a2c.codec import digest,parse_time,parse_duration
from a2c.common import Directory
from a2c.jws import check_fresh,header_of
from a2c.errors import A2CError
from a2c.slots import verify_card

from ..errors import LabError


SCHEMA='''
CREATE TABLE IF NOT EXISTS admission_policies(service TEXT PRIMARY KEY,revision INTEGER NOT NULL,maximum INTEGER NOT NULL,accepting INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS delegation_intents(origin TEXT PRIMARY KEY,sender TEXT NOT NULL,service TEXT NOT NULL,
 scope TEXT NOT NULL,binding TEXT NOT NULL,template TEXT NOT NULL,expires REAL NOT NULL,details TEXT NOT NULL,started REAL,closed INTEGER NOT NULL DEFAULT 0,
 UNIQUE(sender,service,binding));
CREATE TABLE IF NOT EXISTS delegation_links(id TEXT PRIMARY KEY,origin TEXT UNIQUE NOT NULL);
CREATE TABLE IF NOT EXISTS delegation_admissions(id TEXT PRIMARY KEY,interaction TEXT UNIQUE NOT NULL,origin TEXT UNIQUE NOT NULL,
 service TEXT NOT NULL,client TEXT NOT NULL,scope TEXT NOT NULL,binding TEXT NOT NULL,template TEXT NOT NULL,
 state TEXT NOT NULL,hold_until REAL NOT NULL,created REAL NOT NULL,committed REAL);
CREATE TABLE IF NOT EXISTS admission_operations(owner TEXT NOT NULL,actor TEXT NOT NULL,kind TEXT NOT NULL,id TEXT NOT NULL,
 fingerprint TEXT NOT NULL,result TEXT NOT NULL,PRIMARY KEY(actor,kind,id));
CREATE TABLE IF NOT EXISTS admission_payments(payment_id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,admission TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS delegation_signed_requests(admission TEXT PRIMARY KEY,message TEXT NOT NULL,request TEXT NOT NULL,started REAL);
CREATE TABLE IF NOT EXISTS delegation_offers(admission TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,result TEXT NOT NULL);
'''


def encode(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def stamp(value):return datetime.fromtimestamp(value,timezone.utc).isoformat().replace('+00:00','Z')
def identifier(value):
    try:return str(uuid.UUID(value))
    except (TypeError,ValueError,AttributeError):raise LabError('invalid_input','Use an original UUID reference') from None


class Admissions:
    def __init__(self,db,identities,publications,scopes,payment_provider,*,clock=time.time):
        self.db,self.identities,self.publications,self.scopes=db,identities,publications,scopes
        self.payment_provider,self.clock=payment_provider,clock
        self.active=lambda conn,actor:False
        with db.lock:db.conn.executescript(SCHEMA)

    def owned_service(self,owner,actor):
        row=self.identities.owned(owner,actor)
        if row['role']!='service':raise LabError('forbidden','Only the service manages its admission policy',status=403)
        return row

    def offered(self,service):
        owner=self.identities.agent(service)['owner_id']
        if not self.publications.status(owner,service)['indexReady']:
            raise LabError('not_ready','The current service publication is not indexed yet',status=409,recovery='recover_publication')
        return self.publications.templates(service)

    def intent_in(self,conn,sender,service,origin,body,expires):
        self.identities.agent(sender)
        target=self.identities.agent(service)
        if target['role']!='service' or self.identities.agent(sender)['role']!='assistant':
            raise LabError('forbidden','A delegation intent requires an assistant and a service',status=403)
        formation=body.get('formation')
        subject_name='request' if formation=='proposal' else 'card'
        if formation not in ('proposal','countersign') or set(body)!={'formation','templateDigest','scopeDigest','bindingDigest',subject_name}:
            raise LabError('invalid_input','Use the original unsigned request or card and its protocol binding')
        templates=self.offered(service)
        template=next((t for t in templates if t['templateDigest']==body['templateDigest']),None)
        if not template or template['formation']!=formation:
            raise LabError('not_ready','The service does not currently offer that valid template',status=409)
        subject=body[subject_name]
        try:
            if formation=='proposal':
                if not isinstance(subject,dict) or 'signature' in subject:raise ValueError()
                validate_request(template,{**subject,'signature':{}})
                actual=request_digest(subject)
            else:
                validate_card(template,subject)
                if any(item for value in subject['signatures'].values() for item in (value if isinstance(value,list) else [value])):raise ValueError()
                actual=card_digest(subject)
        except (A2CError,TypeError,KeyError,ValueError):
            raise LabError('invalid_input','Intent failed unsigned A2C object validation') from None
        if actual!=body['bindingDigest'] or subject['paymentProvider']!=self.payment_provider:
            raise LabError('forbidden','Protocol digest or payment provider does not match the intent',status=403)
        owner=self.identities.agent(sender)['owner_id']
        self.scopes.eligibility_in(conn,owner,sender,body['scopeDigest'],subject,template['provider'],template['pricing']['currency'],selected=formation=='countersign')
        if conn.execute('SELECT 1 FROM delegation_intents WHERE sender=? AND service=? AND binding=?',(sender,service,actual)).fetchone():
            raise LabError('id_conflict','Recover the original intent for this protocol object',status=409)
        conn.execute('INSERT INTO delegation_intents(origin,sender,service,scope,binding,template,expires,details) VALUES(?,?,?,?,?,?,?,?)',
                     (origin,sender,service,body['scopeDigest'],actual,template['templateDigest'],expires,encode({'body':body,'template':template})))

    def intent(self,conn,origin):
        row=conn.execute('SELECT * FROM delegation_intents WHERE origin=?',(origin,)).fetchone()
        if not row:raise LabError('not_ready','Original delegation intent is unavailable',status=409)
        return row

    def link_in(self,conn,service,origin,iid,binding):
        intent=self.intent(conn,origin)
        if (intent['service'],intent['binding'])!=(service,binding):raise LabError('forbidden','Only the original service may bind this intent',status=403)
        old=conn.execute('SELECT * FROM delegation_links WHERE id=? OR origin=?',(iid,origin)).fetchone()
        if old and (old['id'],old['origin'])!=(iid,origin):raise LabError('id_conflict','Delegation already has another interaction',status=409)
        if not old:conn.execute('INSERT INTO delegation_links VALUES(?,?)',(iid,origin))

    def relation(self,conn,iid):
        iid=identifier(iid)
        row=conn.execute('SELECT i.* FROM delegation_intents i JOIN delegation_links l ON l.origin=i.origin WHERE l.id=?',(iid,)).fetchone()
        if not row:raise LabError('not_ready','Delegation interaction is unavailable',status=409)
        return row

    def _eligible(self,conn,intent):
        if intent['closed']:raise LabError('closed','Original delegation intent has closed',status=409)
        original=json.loads(intent['details']);body,template=original['body'],original['template']
        if intent['expires']<=self.clock() or parse_time(template['validUntil']).timestamp()<=self.clock():
            raise LabError('expired','Original intent or template expired',status=409)
        if not any(t['templateDigest']==intent['template'] for t in self.offered(intent['service'])):
            raise LabError('not_ready','The original template is no longer offered for new work',status=409)
        owner=self.identities.agent(intent['sender'])['owner_id']
        subject=body['request' if body['formation']=='proposal' else 'card']
        state=self.scopes.eligibility_in(conn,owner,intent['sender'],intent['scope'],subject,template['provider'],template['pricing']['currency'],selected=body['formation']=='countersign')
        return original,state

    def start_in(self,conn,service,origin):
        intent=self.intent(conn,origin)
        if intent['service']!=service:raise LabError('forbidden','Only the intent recipient may start it',status=403)
        self._eligible(conn,intent)
        if intent['started'] is None:conn.execute('UPDATE delegation_intents SET started=? WHERE origin=?',(self.clock(),origin))

    def expire_in(self,conn):
        conn.execute("UPDATE delegation_admissions SET state='released' WHERE state='held' AND hold_until<=?",(self.clock(),))

    def can_close_in(self,conn,origin):
        return not conn.execute("SELECT 1 FROM delegation_admissions WHERE origin=? AND state='committed'",(origin,)).fetchone()

    def close_in(self,conn,origin):
        if not self.can_close_in(conn,origin):raise LabError('not_ready','Reconcile the committed A2C obligation before closing',status=409,recovery='reconcile_original')
        conn.execute("UPDATE delegation_admissions SET state='released' WHERE origin=? AND state='held'",(origin,))
        conn.execute('UPDATE delegation_intents SET closed=1 WHERE origin=?',(origin,))

    def policy(self,conn,service):
        row=conn.execute('SELECT * FROM admission_policies WHERE service=?',(service,)).fetchone()
        active=conn.execute("SELECT COUNT(*) FROM delegation_admissions WHERE service=? AND state!='released' AND (state!='held' OR hold_until>?)",(service,self.clock())).fetchone()[0]
        return {'service':service,'revision':row['revision'] if row else 0,'maxActiveDelegations':row['maximum'] if row else 0,
                'accepting':bool(row and row['accepting']),'active':active}

    def receipt(self,row):
        return {'admissionId':row['id'],'interactionId':row['interaction'],'originMessageId':row['origin'],
                'service':row['service'],'client':row['client'],'scopeDigest':row['scope'],'bindingDigest':row['binding'],
                'state':row['state'],'holdUntil':stamp(row['hold_until'])}

    def admission(self,conn,identifier):
        try:identifier=str(uuid.UUID(identifier))
        except (TypeError,ValueError,AttributeError):raise LabError('invalid_input','Use an original admission UUID') from None
        row=conn.execute('SELECT * FROM delegation_admissions WHERE id=?',(identifier,)).fetchone()
        if not row:raise LabError('not_ready','Original admission is unavailable',status=404)
        return row

    def operation(self,owner,actor,kind,operation):
        self.identities.owned(owner,actor)
        row=self.db.one('SELECT * FROM admission_operations WHERE actor=? AND kind=? AND id=?',actor,kind,operation)
        if not row or row['owner']!=owner:raise LabError('not_ready','Original admission operation is unavailable',status=404,recovery='retry_original')
        return json.loads(row['result'])

    def apply_in(self,conn,owner,actor,kind,operation,payload):
        self.identities.owned(owner,actor)
        self.expire_in(conn)
        if kind=='admission.status':
            if set(payload)=={'admissionId'}:
                row=self.admission(conn,payload['admissionId'])
                if actor not in (row['service'],row['client']):raise LabError('forbidden','Admission belongs to other participants',status=403)
                return self.receipt(row)
            if payload:raise LabError('invalid_input','Choose an admission or the current service policy')
            self.owned_service(owner,actor)
            return self.policy(conn,actor)
        if not isinstance(operation,str) or not 1<=len(operation)<=128:raise LabError('invalid_input','Use a stable admission operationId')
        previous=conn.execute('SELECT * FROM admission_operations WHERE actor=? AND kind=? AND id=?',(actor,kind,operation)).fetchone()
        fingerprint=digest(payload)
        if previous:
            if previous['owner']!=owner or previous['fingerprint']!=fingerprint:raise LabError('id_conflict','Original admission operation changed',status=409)
            return json.loads(previous['result'])
        self.owned_service(owner,actor)
        self.expire_in(conn)
        if kind=='admission.configure':result=self._configure(conn,actor,payload)
        elif kind=='admission.reserve':result=self._reserve(conn,actor,payload)
        elif kind=='admission.commit':result=self._commit(conn,actor,payload)
        else:raise LabError('not_ready','Admission operation is not implemented',status=409)
        conn.execute('INSERT INTO admission_operations VALUES(?,?,?,?,?,?)',(owner,actor,kind,operation,fingerprint,encode(result)))
        return result

    def _configure(self,conn,actor,payload):
        if set(payload)!={'expectedRevision','maxActiveDelegations','accepting'} or type(payload['expectedRevision']) is not int or type(payload['maxActiveDelegations']) is not int or not 1<=payload['maxActiveDelegations']<=1000 or type(payload['accepting']) is not bool:
            raise LabError('invalid_input','Configure a revision, capacity between one and 1000, and accepting flag')
        if self.policy(conn,actor)['revision']!=payload['expectedRevision']:raise LabError('revision_conflict','Read the current admission policy first',status=409)
        if payload['accepting'] and not self.publications.templates(actor):raise LabError('not_ready','Publish a valid template before accepting new work',status=409)
        conn.execute('INSERT OR REPLACE INTO admission_policies VALUES(?,?,?,?)',(actor,payload['expectedRevision']+1,payload['maxActiveDelegations'],int(payload['accepting'])))
        return self.policy(conn,actor)

    def _reserve(self,conn,actor,payload):
        if set(payload)!={'interactionId','originMessageId','bindingDigest'}:raise LabError('invalid_input','Use the original interaction, intent and binding')
        intent=self.relation(conn,payload['interactionId'])
        if (intent['service'],intent['origin'],intent['binding'])!=(actor,payload['originMessageId'],payload['bindingDigest']):raise LabError('forbidden','Admission does not match the original parties and intent',status=403)
        old=conn.execute('SELECT * FROM delegation_admissions WHERE interaction=?',(payload['interactionId'],)).fetchone()
        if old:return self.receipt(old)
        if intent['started'] is None:raise LabError('forbidden','Obtain the original start permit before reserving',status=403)
        original,scope=self._eligible(conn,intent)
        policy=self.policy(conn,actor)
        if not policy['accepting']:raise LabError('not_ready','Service is not accepting new delegations',status=409)
        if policy['active']>=policy['maxActiveDelegations']:raise LabError('capacity_full','All service admission slots are occupied',status=409)
        until=min(self.clock()+120,intent['expires'],parse_time(original['template']['validUntil']).timestamp(),parse_time(scope['expiresAt']).timestamp())
        identifier=str(uuid.uuid5(uuid.NAMESPACE_URL,'ioa-admission:'+payload['interactionId']))
        conn.execute("INSERT INTO delegation_admissions(id,interaction,origin,service,client,scope,binding,template,state,hold_until,created) VALUES(?,?,?,?,?,?,?,?,'held',?,?)",
                     (identifier,payload['interactionId'],intent['origin'],actor,intent['sender'],intent['scope'],intent['binding'],intent['template'],until,self.clock()))
        return self.receipt(self.admission(conn,identifier))

    def _held(self,conn,row):
        if row['state']!='held' or row['hold_until']<=self.clock():raise LabError('expired','Original unpaid admission is no longer eligible',status=409)
        if not self.active(conn,row['service']):raise LabError('not_ready','Service execution connection is offline',status=409)
        if not self.policy(conn,row['service'])['accepting']:raise LabError('not_ready','Service paused new commitments',status=409)
        return self._eligible(conn,self.intent(conn,row['origin']))

    def _commit(self,conn,actor,payload):
        if set(payload)-{'admissionId','card'} or 'admissionId' not in payload:raise LabError('invalid_input','Use the original admission and required signature evidence')
        row=self.admission(conn,payload['admissionId'])
        if row['service']!=actor:raise LabError('forbidden','Only the admitted service commits a zero-fee request',status=403)
        if row['state']=='committed':
            if 'card' in payload and card_digest(payload['card'])!=row['binding']:raise LabError('id_conflict','Committed admission cannot change its card',status=409)
            return self.receipt(row)
        original,_=self._held(conn,row)
        template=original['template']
        if template['formation']=='proposal':
            if template['pricing'].get('proposalFee',0):raise LabError('forbidden','Commit a paid proposal through the AP2 application transaction',status=403)
        else:
            card=payload.get('card')
            try:
                validate_card(template,card)
                if any(item for slot,value in card['signatures'].items() if slot!='1' for item in (value if isinstance(value,list) else [value])):raise ValueError()
                # Only slot 1 is legal here: all signer keys are inline, so a
                # malicious future slot cannot trigger remote key I/O in this TX.
                chain=verify_card(template,card,Directory())
                if card_digest(card)!=row['binding'] or not chain.ts.get(1) or chain.ts.get(2):raise ValueError()
                check_fresh(header_of(card['signatures']['1']),datetime.fromtimestamp(self.clock(),timezone.utc))
                if self.clock()>chain.ts[1].timestamp()+parse_duration(template['deadlines']['respondWithin']).total_seconds():raise ValueError()
            except (A2CError,TypeError,KeyError,ValueError):raise LabError('invalid_input','Countersign admission needs the original valid slot 1 card') from None
        conn.execute("UPDATE delegation_admissions SET state='committed',committed=? WHERE id=?",(self.clock(),row['id']))
        return self.receipt(self.admission(conn,row['id']))

    def payment_in(self,conn,owner,actor,identifier,payment):
        row=self.admission(conn,identifier)
        if row['client']!=actor or self.identities.agent(actor)['owner_id']!=owner:raise LabError('forbidden','Payment admission belongs to another client',status=403)
        old=conn.execute('SELECT * FROM admission_payments WHERE payment_id=?',(payment.payment_id,)).fetchone()
        if old:
            if (old['admission'],old['fingerprint'])!=(identifier,payment.fingerprint):raise LabError('id_conflict','Original payment admission changed',status=409)
            return False
        if not payment.expires:raise LabError('unknown_result','Historical payment lacks its admission association',status=409,recovery='reconcile_original')
        original,_=self._held(conn,row)
        template=original['template']
        if template['formation']!='proposal' or payment.purpose!='proposal-fee' or payment.ref!=row['binding'] or payment.scope_digest!=row['scope'] or payment.provider!=template['provider'] or payment.payer!=owner or json.loads(payment.client).get('id')!=actor or payment.amount!=Decimal(str(template['pricing'].get('proposalFee',0))) or payment.amount<=0:
            raise LabError('forbidden','Payment differs from the admitted proposal fee and original protocol object',status=403)
        conn.execute("UPDATE delegation_admissions SET state='committed',committed=? WHERE id=?",(self.clock(),row['id']))
        conn.execute('INSERT INTO admission_payments VALUES(?,?,?)',(payment.payment_id,payment.fingerprint,identifier))
        return True

    def initial_protocol_in(self,conn,actor,identifier,op,body):
        row=self.admission(conn,identifier)
        if row['client']!=actor:raise LabError('forbidden','Protocol request belongs to another delegation client',status=403)
        if op not in ('request.submit','card.countersign','ap2.checkout'):
            raise LabError('not_ready','This A2C execution stage is not enabled yet',status=409)
        if row['state']=='released' or (op=='request.submit' and row['state']!='committed'):
            raise LabError('not_ready','Commit the original admission before submitting a signed obligation',status=409)
        original,_=self._held(conn,row) if row['state']=='held' else self._eligible(conn,self.intent(conn,row['origin']))
        template=original['template'];intent=original['body']
        try:
            if op=='ap2.checkout':
                if intent['formation']!='proposal' or body.get('purpose')!='proposal-fee' or body.get('templateDigest')!=row['template'] or body.get('requestDigest')!=row['binding']:raise ValueError()
            elif op=='request.submit':
                request=body['request'];validate_request(template,request)
                if request_digest(request)!=row['binding']:raise ValueError()
                check_fresh(verify_request(request),datetime.fromtimestamp(self.clock(),timezone.utc))
            else:
                card=body['card'];validate_card(template,card)
                if card_digest(card)!=row['binding'] or not card['signatures']['1'] or any(item for slot,value in card['signatures'].items() if slot!='1' for item in (value if isinstance(value,list) else [value])):raise ValueError()
                verify_card(template,card,Directory())
                check_fresh(header_of(card['signatures']['1']),datetime.fromtimestamp(self.clock(),timezone.utc))
        except (A2CError,TypeError,KeyError,ValueError):raise LabError('invalid_input','Original A2C request or signature failed validation') from None
        return row,original

    def countersign_result_in(self,conn,identifier,card,original_slot_one):
        row=self.admission(conn,identifier)
        if row['state']!='committed':raise LabError('not_ready','Commit admission after review and before signing slot 2',status=409)
        template=json.loads(self.intent(conn,row['origin'])['details'])['template']
        party=self.identities.service_party(row['service'])
        keys=Directory();keys.add(party['id'],{'keys':[party['jwk']]})
        try:
            validate_card(template,card)
            if template['formation']!='countersign' or card_digest(card)!=row['binding'] or card['provider']!=party['id'] or card['signatures']['1']!=original_slot_one or not card['signatures']['2']:
                raise ValueError()
            if any(item for slot,value in card['signatures'].items() if slot not in ('1','2') for item in (value if isinstance(value,list) else [value])):raise ValueError()
            if verify_card(template,card,keys).effective is None:raise ValueError()
        except (A2CError,TypeError,KeyError,ValueError):raise LabError('invalid_input','Countersign result changed its bound card or original signature chain') from None

    def signed_request_in(self,conn,identifier,message,request,*,started=False):
        identifier=self.admission(conn,identifier)['id']
        original=conn.execute('SELECT * FROM delegation_signed_requests WHERE admission=?',(identifier,)).fetchone()
        if original:
            if original['message']!=message or json.loads(original['request'])!=request:raise LabError('id_conflict','Original signed request cannot change',status=409)
        else:
            conn.execute('INSERT INTO delegation_signed_requests(admission,message,request) VALUES(?,?,?)',(identifier,message,encode(request)))
        if started:conn.execute('UPDATE delegation_signed_requests SET started=COALESCE(started,?) WHERE admission=?',(self.clock(),identifier))

    def issued_offer_in(self,conn,owner,actor,identifier,fingerprint):
        self.owned_service(owner,actor)
        admission=self.admission(conn,identifier);identifier=admission['id']
        if admission['service']!=actor:raise LabError('forbidden','This service does not own that admission',status=403)
        row=conn.execute('SELECT * FROM delegation_offers WHERE admission=?',(identifier,)).fetchone()
        if row and row['fingerprint']!=fingerprint:raise LabError('id_conflict','An admission already has its original signed offer',status=409)
        return json.loads(row['result']) if row else None

    def proposal_eligible_in(self,conn,owner,actor,identifier,request,card):
        self.owned_service(owner,actor)
        row=self.admission(conn,identifier)
        identifier=row['id']
        if row['service']!=actor or row['state']!='committed':raise LabError('forbidden','The original service needs committed admission before market endorsement',status=403)
        original,_=self._eligible(conn,self.intent(conn,row['origin']))
        source=conn.execute('SELECT * FROM delegation_signed_requests WHERE admission=?',(identifier,)).fetchone()
        if not source or source['started'] is None or json.loads(source['request'])!=request:raise LabError('forbidden','Use the original signed request that actually started',status=403)
        party=self.identities.service_party(actor);keys=Directory();keys.add(party['id'],{'keys':[party['jwk']]})
        try:
            header=verify_request(request)
            template=original['template'];validate_card(template,card,request=request)
            if template['formation']!='proposal' or request_digest(request)!=row['binding'] or card['provider']!=party['id'] or not card['signatures']['2']:
                raise ValueError()
            if any(item for slot,value in card['signatures'].items() if slot!='2' for item in (value if isinstance(value,list) else [value])):raise ValueError()
            requested=parse_time(header['a2c-ts'])
            verify_card(template,card,keys,request_ts=requested)
            now=datetime.fromtimestamp(self.clock(),timezone.utc)
            check_fresh(header_of(card['signatures']['2']),now)
            if now>requested+parse_duration(template['deadlines']['respondWithin']):raise ValueError()
        except (A2CError,TypeError,KeyError,ValueError):raise LabError('invalid_input','Proposal changed the original request or its signature/window') from None
        return template

    def record_offer_in(self,conn,identifier,fingerprint,result):
        identifier=self.admission(conn,identifier)['id']
        conn.execute('INSERT INTO delegation_offers VALUES(?,?,?)',(identifier,fingerprint,encode(result)))
