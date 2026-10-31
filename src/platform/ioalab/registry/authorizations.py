"""Bound deployment publication scope; all authority changes have durable receipts."""
import json
import re
import time
from datetime import datetime,timezone

from a2c.codec import digest,parse_time
from a2c.jws import header_of
from ..errors import LabError

SCHEMA='''
CREATE TABLE IF NOT EXISTS registry_publication_authorizations(
 actor TEXT PRIMARY KEY,revision INTEGER NOT NULL,terms TEXT NOT NULL,lifetime INTEGER NOT NULL,
 enabled INTEGER NOT NULL,operator TEXT NOT NULL,updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS registry_authorization_operations(
 id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,result TEXT NOT NULL);
'''


def publication_terms(protocol_id,service):
    templates=[{k:v for k,v in t.items() if k not in ('provider','templateDigest','signatures','validUntil')}
               for t in service['templates']]
    return digest({'protocolId':protocol_id,**{k:service[k] for k in ('name','description','tags','categoryId')},'templates':templates})


class PublicationAuthorizations:
    def __init__(self,db,identities,auth,*,clock=time.time):
        self.db,self.identities,self.auth,self.clock=db,identities,auth,clock
        with db.lock:db.conn.executescript(SCHEMA)

    def current(self,actor):
        row=self.db.one('SELECT * FROM registry_publication_authorizations WHERE actor=?',actor)
        if row:return {'agentId':row['actor'],'revision':row['revision'],'termsDigest':row['terms'],
                       'maxTemplateSeconds':row['lifetime'],'enabled':bool(row['enabled'])}

    def list(self):
        return [self.current(row['actor']) for row in self.db.rows('SELECT actor FROM registry_publication_authorizations ORDER BY actor')]

    def configure(self,operator,body):
        if not isinstance(body,dict) or set(body)!={'operationId','changes'}:
            raise LabError('invalid_input','Use operationId and explicit publication authorization changes')
        operation,changes=body['operationId'],body['changes']
        if not isinstance(operation,str) or not 1<=len(operation)<=128 or not isinstance(changes,list) or not 1<=len(changes)<=64:
            raise LabError('invalid_input','A stable operation and one to 64 changes are required')
        actors=[]
        for item in changes:
            if not isinstance(item,dict) or set(item)!={'agentId','expectedRevision','termsDigest','maxTemplateSeconds','enabled'}:
                raise LabError('invalid_input','Use the documented authorization fields')
            if (not isinstance(item['agentId'],str) or not 1<=len(item['agentId'])<=256 or
                type(item['expectedRevision']) is not int or item['expectedRevision']<0 or
                not isinstance(item['termsDigest'],str) or not re.fullmatch(r'sha256:[0-9a-f]{64}',item['termsDigest']) or
                type(item['maxTemplateSeconds']) is not int or not 60<=item['maxTemplateSeconds']<=31622400 or
                type(item['enabled']) is not bool):
                raise LabError('invalid_input','Invalid publication scope, revision or template lifetime')
            actors.append(item['agentId'])
        if len(set(actors))!=len(actors):raise LabError('invalid_input','Each authorization identity must be distinct')
        fingerprint=digest(body)
        with self.db.tx() as conn:
            if not self.auth.is_admin(operator):raise LabError('forbidden','Current web administrator permission is required',status=403)
            old=conn.execute('SELECT * FROM registry_authorization_operations WHERE id=?',(operation,)).fetchone()
            if old:
                if old['fingerprint']!=fingerprint:raise LabError('id_conflict','Original authorization parameters changed',status=409)
                receipt=json.loads(old['result'])
            else:
                for item in changes:
                    self.identities.internal_service(item['agentId'])
                    current=self.current(item['agentId'])
                    if item['expectedRevision']!=(current['revision'] if current else 0):
                        raise LabError('revision_conflict','Read the current publication authorization before changing it',status=409)
                now=self.clock()
                for item in changes:
                    conn.execute('INSERT OR REPLACE INTO registry_publication_authorizations VALUES(?,?,?,?,?,?,?)',
                        (item['agentId'],item['expectedRevision']+1,item['termsDigest'],item['maxTemplateSeconds'],int(item['enabled']),operator,now))
                receipt={'operationId':operation,'authorizations':[self.current(actor) for actor in actors],
                         'acceptedAt':datetime.fromtimestamp(now,timezone.utc).isoformat().replace('+00:00','Z')}
                conn.execute('INSERT INTO registry_authorization_operations VALUES(?,?,?)',(operation,fingerprint,json.dumps(receipt)))
            return {'authorizationReceipt':receipt,'currentAuthorizations':[self.current(actor) for actor in actors]}

    def check(self,actor,body,templates,now):
        self.identities.internal_service(actor)
        policy=self.current(actor)
        revision=body['authorizationRevision']
        service={**body['declaration'],'templates':templates}
        if (type(revision) is not int or revision<1 or not policy or not policy['enabled'] or
            policy['revision']!=revision or policy['termsDigest']!=publication_terms(body['declaration']['protocolId'],service)):
            raise LabError('forbidden','Deployment publication authorization changed or does not cover these terms',status=403,recovery='configure_publication')
        signed_at=parse_time(header_of(body['proof'])['a2c-ts']).timestamp()
        if any(not now<parse_time(t['validUntil']).timestamp()<=signed_at+policy['maxTemplateSeconds'] for t in templates):
            raise LabError('forbidden','Template lifetime exceeds the deployment publication authorization',status=403,recovery='configure_publication')

    def manual_in(self,conn,actor,body,templates):
        policy=self.current(actor)
        if policy and policy['enabled'] and policy['termsDigest']!=publication_terms(body['declaration']['protocolId'],{**body['declaration'],'templates':templates}):
            conn.execute('UPDATE registry_publication_authorizations SET revision=revision+1,enabled=0,operator=?,updated=? WHERE actor=?',
                         ('owner-publication:'+body['operationId'],self.clock(),actor))
