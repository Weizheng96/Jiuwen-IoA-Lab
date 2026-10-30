"""Role-specific, recoverable public-key enrollment; no participants or private keys."""
import json
import secrets
import time
import uuid
from datetime import datetime,timezone

from a2c.codec import digest,utcnow
from a2c.jws import verify,check_fresh
from a2c.objects import object_digest
from ..errors import LabError
from .keys import public_key,fingerprints


SCHEMA='''
CREATE TABLE IF NOT EXISTS identity_keys(fingerprint TEXT PRIMARY KEY,owner TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS identity_enrollments(owner TEXT,operation TEXT,actor TEXT NOT NULL,role TEXT NOT NULL,
 protocol TEXT NOT NULL,challenge TEXT NOT NULL,expires REAL NOT NULL,fingerprint TEXT,result TEXT,
 PRIMARY KEY(owner,operation));
'''


def stamp(value):
    return datetime.fromtimestamp(value,timezone.utc).isoformat().replace('+00:00','Z')


class EnrollmentRegistry:
    def __init__(self,db,settings,*,legacy_keys=(),legacy_certificates=()):
        self.db,self.settings=db,settings
        with db.lock:db.conn.executescript(SCHEMA)
        with db.tx() as conn:
            for owner,key in legacy_keys:self.claim_in(conn,owner,[key])
            for owner,fingerprint in legacy_certificates:
                self._claim(conn,owner,'certificate:'+fingerprint)

    @staticmethod
    def _claim(conn,owner,fingerprint):
        existing=conn.execute('SELECT owner FROM identity_keys WHERE fingerprint=?',(fingerprint,)).fetchone()
        if existing and existing['owner']!=owner:
            raise LabError('id_conflict','Public key is already bound to another account',status=409)
        conn.execute('INSERT OR IGNORE INTO identity_keys VALUES(?,?)',(fingerprint,owner))

    def claim_in(self,conn,owner,keys):
        for key in keys:
            for fingerprint in fingerprints(key):self._claim(conn,owner,fingerprint)

    def claim(self,owner,keys):
        with self.db.tx() as conn:self.claim_in(conn,owner,keys)

    def protocol(self,role,actor):
        if role not in ('assistant','service') or not isinstance(actor,str):
            raise LabError('invalid_input','Specify a supported role and stable agentId')
        if role=='assistant':
            if not actor.startswith('urn:') or not 5<len(actor)<=200:raise LabError('invalid_input','Assistant agentId must be a stable URN')
            return actor
        try:
            if not actor.startswith('svc-') or actor!='svc-'+str(uuid.UUID(actor[4:])):raise ValueError()
        except (ValueError,AttributeError) as exc:raise LabError('invalid_input','Service agentId must contain its stable UUID') from exc
        return self.settings.public_url+'/a2a/agents/'+actor

    def issue(self,owner,body):
        if set(body)!={'operationId','role','agentId'}:raise LabError('invalid_input','Invalid enrollment challenge fields')
        operation=body['operationId']
        if not isinstance(operation,str) or not 1<=len(operation)<=128:raise LabError('invalid_input','A stable enrollment operationId is required')
        role,actor=body['role'],body['agentId']
        protocol=self.protocol(role,actor)
        with self.db.tx() as conn:
            old=conn.execute('SELECT * FROM identity_enrollments WHERE owner=? AND operation=?',(owner,operation)).fetchone()
            if old:
                if (old['actor'],old['role'])!=(actor,role):raise LabError('id_conflict','Enrollment operation parameters changed',status=409)
                if old['result']:
                    return {'phase':'bound','result':json.loads(old['result']),'operationId':operation,'protocolId':protocol}
                if old['expires']<=time.time():raise LabError('expired','Enrollment challenge expired without a binding',status=409,recovery='new_operation')
                challenge,expires=old['challenge'],old['expires']
            else:
                challenge,expires=secrets.token_urlsafe(32),time.time()+120
                conn.execute('INSERT INTO identity_enrollments(owner,operation,actor,role,protocol,challenge,expires) VALUES(?,?,?,?,?,?,?)',
                             (owner,operation,actor,role,protocol,challenge,expires))
        return {'phase':'challenge','operationId':operation,'challenge':challenge,'expiresAt':stamp(expires),'protocolId':protocol}

    def bind_in(self,conn,owner,body):
        if conn is not self.db.conn or not conn.in_transaction:
            raise RuntimeError('Identity binding requires the owned application transaction')
        fields={'operationId','challenge','owner','platform','role','agentId','name','source','keys','proof'}
        if set(body)!=fields or body['owner']!=owner or body['platform']!=self.settings.public_url:
            raise LabError('invalid_input','Enrollment fields, owner or platform do not match')
        operation=body['operationId']
        if not isinstance(operation,str) or not 1<=len(operation)<=128:raise LabError('invalid_input','Invalid enrollment operationId')
        role,actor=body['role'],body['agentId']
        protocol=self.protocol(role,actor)
        if body['source'] not in (('client','experiment') if role=='assistant' else ('provider','experiment')):
            raise LabError('invalid_input','Source does not match the enrolled role')
        if not isinstance(body['name'],str) or not 1<=len(body['name'])<=120:raise LabError('invalid_input','A bounded display name is required')
        keys=body['keys']
        required={'agent','user','payer'} if role=='assistant' else {'agent','payee'}
        if not isinstance(keys,dict) or set(keys)!=required:raise LabError('invalid_input','Role public keys do not match required purposes')
        fingerprint=digest(body)
        # Same proven body can recover an old result without re-signing an expired challenge.
        old=self.db.one('SELECT fingerprint,result FROM identity_enrollments WHERE owner=? AND operation=?',owner,operation)
        if old and old['result']:
            if old['fingerprint']!=fingerprint:raise LabError('id_conflict','Enrollment proof changed',status=409)
            return json.loads(old['result'])
        for purpose,key in keys.items():public_key(key,ec=purpose in ('payer','payee'),strict=True)
        payload={name:value for name,value in body.items() if name!='proof'}
        header=verify(body['proof'],object_digest(payload),keys['agent'],slot='client' if role=='assistant' else 'provider',signer=protocol,expect_kid=False)
        check_fresh(header,utcnow())
        result={'agentId':actor,'id':actor,'protocolId':protocol,'role':role,'keys':keys,'name':body['name']}
        record=conn.execute('SELECT * FROM identity_enrollments WHERE owner=? AND operation=?',(owner,operation)).fetchone()
        if not record or (record['actor'],record['role'],record['challenge'])!=(actor,role,body['challenge']):
            raise LabError('forbidden','Enrollment challenge does not match',status=403)
        if record['result']:
            if record['fingerprint']!=fingerprint:raise LabError('id_conflict','Enrollment proof changed',status=409)
            return json.loads(record['result'])
        if record['expires']<=time.time():raise LabError('expired','Enrollment challenge expired',status=409)
        existing=conn.execute('SELECT * FROM agents WHERE id=?',(actor,)).fetchone()
        if existing:
            current=json.loads(existing['meta'])
            expected={'agent':current.get('clientJwk'),'user':current.get('userJwk'),'payer':current.get('payerJwk')} if role=='assistant' else {'agent':current.get('providerJwk'),'payee':current.get('ap2_jwk')}
            if existing['owner_id']!=owner or existing['role']!=role or expected!=keys:
                raise LabError('id_conflict','Identity or its public keys already belong to another binding',status=409)
        if role=='assistant':
            other=conn.execute("SELECT id FROM agents WHERE owner_id=? AND role='assistant' AND id!=?",(owner,actor)).fetchone()
            if other:raise LabError('id_conflict','Migrate the existing logical assistant instead of replacing its keys',status=409)
        self.claim_in(conn,owner,keys.values())
        meta={'source':body['source'],'protocolId':protocol,'last_driver':'external','lastSeen':time.time()}
        if role=='assistant':
            meta.update(clientJwk=keys['agent'],userJwk=keys['user'],payerJwk=keys['payer'])
        else:meta.update(url=protocol,providerJwk=keys['agent'],ap2_jwk=keys['payee'],external=True)
        if existing:
            conn.execute('UPDATE agents SET name=?,meta=? WHERE id=?',(body['name'],json.dumps({**json.loads(existing['meta']),**meta}),actor))
        else:
            conn.execute("INSERT INTO agents(id,owner_id,role,name,definition,status,created,meta) VALUES(?,?,?,?,?,'registered',?,?)",
                         (actor,owner,role,body['name'],'{}',time.time(),json.dumps(meta)))
        conn.execute('UPDATE identity_enrollments SET fingerprint=?,result=? WHERE owner=? AND operation=?',
                     (fingerprint,json.dumps(result),owner,operation))
        return result

    def service_keys(self,actor):
        row=self.db.one("SELECT meta FROM agents WHERE id=? AND role='service'",actor)
        if not row:raise LabError('not_ready','Service identity is unavailable',status=404)
        key=json.loads(row['meta']).get('providerJwk')
        if not key:raise LabError('not_ready','Service identity needs explicit migration',status=409)
        return {'keys':[key]}
