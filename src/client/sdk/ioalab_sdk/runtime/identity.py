"""Persist the original enrollment operation and proof before sending it."""
import uuid
import httpx
from a2c.codec import digest,utcnow
from a2c.objects import object_digest
from a2c.jws import Signer,sign
from .protocol import RuntimeFault


class IdentityClient:
    def __init__(self,session):
        self.session=session

    def protocol_id(self,role,agent_id):
        return agent_id if role=='assistant' else self.session.config['publicUrl'].rstrip('/')+'/a2a/agents/'+agent_id

    def enroll(self,role,agent_id,signer,keys,*,name,source):
        protocol=self.protocol_id(role,agent_id)
        if signer.id!=protocol:raise RuntimeFault('identity_conflict','Signing identity differs from the derived protocol address')
        intent={'owner':self.session.account['id'],'platform':self.session.config['publicUrl'],'role':role,
                'agentId':agent_id,'name':name,'source':source,'keys':keys}
        fingerprint=digest(intent)
        cache=self.session.cache
        key=role+'|'+agent_id
        with cache.transaction():
            record=cache.get('enrollments',key)
            if record and record['intent']!=fingerprint:
                previous=record.get('result')
                if not previous:raise RuntimeFault('id_conflict','Recover the original enrollment before changing configuration')
                if previous['keys']!=keys:raise RuntimeFault('identity_conflict','Migrate existing identity keys instead of overwriting them')
                record=None
            if record is None:
                record={'intent':fingerprint,'operationId':str(uuid.uuid4())}
                cache.put('enrollments',key,record)
        if record.get('result'):return record['result']
        for attempt in range(2):
            try:
                issued=self.session.api('POST','/identity/challenge',{'operationId':record['operationId'],'role':role,'agentId':agent_id})
                if issued['protocolId']!=protocol:raise RuntimeFault('identity_conflict','Platform protocol address changed')
                if issued['phase']=='bound':
                    result=issued['result']
                else:
                    if not record.get('body'):
                        body={**intent,'operationId':record['operationId'],'challenge':issued['challenge']}
                        body['proof']=sign(object_digest(body),Signer(signer.id,signer.key),
                                           'client' if role=='assistant' else 'provider',utcnow())
                        with cache.transaction():
                            current=cache.get('enrollments',key)
                            if current['operationId']!=record['operationId']:raise RuntimeFault('id_conflict','Concurrent enrollment configuration changed')
                            if not current.get('body'):
                                current['body']=body
                                cache.put('enrollments',key,current)
                            record=current
                    result=self.session.api('POST','/identity',record['body'])
                if result['agentId']!=agent_id or result['protocolId']!=protocol or result['keys']!=keys:
                    raise RuntimeFault('identity_conflict','Enrollment result does not match the local identity')
                record['result']=result
                cache.put('enrollments',key,record)
                return result
            except httpx.HTTPStatusError as exc:
                try:problem=exc.response.json()
                except ValueError:problem=None
                error=problem.get('error') if isinstance(problem,dict) else None
                if attempt or not isinstance(error,dict) or error.get('code')!='expired':raise
                # Only an explicit non-committed expiry permits a new challenge.
                record={'intent':fingerprint,'operationId':str(uuid.uuid4())}
                cache.put('enrollments',key,record)
        raise RuntimeFault('not_ready','Enrollment needs explicit recovery')
