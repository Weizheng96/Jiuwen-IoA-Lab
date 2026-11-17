"""SDK causal metadata for the original A2C material ModelPort calls only."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict
import copy
import hashlib
import json
import uuid
import time

from a2c.codec import digest
from .protocol import RuntimeFault,encode

_OWNER=ContextVar('ioa-material-owner',default=None)
_CALL=ContextVar('ioa-material-call',default=None)


def _plain(value):return json.loads(encode(value))
def _hash(value):return hashlib.sha256(encode(value).encode()).hexdigest()


class MaterialObservationPending(RuntimeError):
    def __init__(self,issuer,reference):
        super().__init__('Original material observation is proven undispatched')
        self.issuer,self.reference=issuer,copy.deepcopy(reference)
        self.code='native_not_submitted'


class MaterialObservationBlocked(RuntimeError):
    code='unknown_result'

    def __init__(self,message,*,reason='execution_unknown'):
        super().__init__(message)
        self.reason=reason


def current_material():return _CALL.get()


class MaterialObservations:
    def __init__(self,runtime,registry,identity,interaction,*,handler):
        self.runtime,self.registry,self.identity,self.interaction=runtime,registry,identity,interaction
        self.namespace='model-observations:'+identity.agent_id
        self.handler=handler

    def owner(self,message,job,task_id,subject_digest):
        binding=self.registry.context(self.identity.agent_id,self.interaction)
        if (not binding or job['agentId']!=self.identity.agent_id or job['interactionId']!=self.interaction):
            raise RuntimeFault('identity_conflict','Restore the original material task owner and host')
        original=self.runtime.scheduler.records(self.identity.agent_id,(self.handler,),reference=job['reference'])
        if len(original)!=1 or original[0]['payload'].get('messageId')!=message['messageId']:
            raise RuntimeFault('identity_conflict','Material review changed its original SDK job/message')
        body=message['body'];subject=body['body'].get('request') or body['body'].get('card')
        value={'actor':self.identity.agent_id,'profile':self.identity.profile,'interaction':self.interaction,
            'hostRef':binding['hostRef'],'jobId':job['reference'],'messageId':message['messageId'],
            'taskId':task_id,'op':body['op'],'subjectDigest':subject_digest,
            'bodyDigest':digest(body['body']),'templateDigest':subject['templateDigest'],
            'provider':subject['provider'],'client':subject['client']['id'],
            'inputDigests':{name:value['digest'] for name,value in subject['inputs'].items()}}
        return {**value,'scopeId':_hash(value)}

    @contextmanager
    def scope(self,owner):
        token=_OWNER.set((self,owner))
        try:yield
        finally:_OWNER.reset(token)

    @contextmanager
    def context(self,call):
        original=_OWNER.get();value=_plain(asdict(call))
        if not original or original[0] is not self:
            raise RuntimeFault('not_ready','Restore the original material-review owner before observing')
        owner=original[1]
        if (value['task_id'],value['op'],value['subject_digest'])!=(owner['taskId'],owner['op'],owner['subjectDigest']):
            raise RuntimeFault('identity_conflict','Verifier observation belongs to another material task')
        if type(value['ordinal']) is not int or value['ordinal']<0:
            raise RuntimeFault('invalid_input','Use the actual verifier runner ordinal')
        key='material:'+owner['scopeId']
        with self.runtime.store.transaction() as conn:
            row=conn.execute('SELECT value FROM runtime_checkpoints WHERE namespace=? AND key=?',(self.namespace,key)).fetchone()
            record=json.loads(row['value']) if row else {'owner':owner,'children':[]}
            if record['owner']!=owner:raise RuntimeFault('identity_conflict','Original material scope changed')
            conn.execute('INSERT OR IGNORE INTO runtime_checkpoints VALUES(?,?,?)',(self.namespace,key,encode(record)))
        token=_CALL.set((self,owner,value))
        try:yield
        finally:_CALL.reset(token)

    def prepare(self,owner,call,request_digest,files,job):
        if owner['jobId']!=job:raise RuntimeFault('identity_conflict','Material observation changed its original job')
        if dict(call['files'])!=files:raise RuntimeFault('identity_conflict','Original material bytes changed')
        causal={key:call[key] for key in ('task_id','op','subject_digest','material_name','verifier_id','verifier_digest','ordinal')}
        reference=str(uuid.uuid5(uuid.NAMESPACE_URL,encode({'owner':owner,'call':causal})))
        expected={'materialOwner':owner,'materialCall':call,'requestDigest':request_digest,'files':files,
                  'jobId':job,'interactionId':self.interaction}
        group_key='material:'+owner['scopeId']
        with self.runtime.store.transaction() as conn:
            row=conn.execute('SELECT value FROM runtime_checkpoints WHERE namespace=? AND key=?',(self.namespace,reference)).fetchone()
            saved=json.loads(row['value']) if row else None
            if saved and any(saved.get(key)!=value for key,value in expected.items()):
                raise RuntimeFault('identity_conflict','Original material observation parameters changed')
            group=json.loads(conn.execute('SELECT value FROM runtime_checkpoints WHERE namespace=? AND key=?',(self.namespace,group_key)).fetchone()['value'])
            if reference not in group['children']:group['children'].append(reference)
            record=saved or {**expected,'state':'prepared','materialFormat':1,'submissionAttempt':None}
            conn.execute('INSERT OR IGNORE INTO runtime_checkpoints VALUES(?,?,?)',(self.namespace,reference,encode(record)))
            conn.execute('UPDATE runtime_checkpoints SET value=? WHERE namespace=? AND key=?',(encode(group),self.namespace,group_key))
        self.eligible(owner)
        return reference,record

    def execution(self,reference,candidate):
        from .host_submission import HostExecution,HostDefinition,HostToolDeclaration,HostResource
        with self.runtime.store.transaction() as conn:
            row=conn.execute('SELECT value FROM runtime_checkpoints WHERE namespace=? AND key=?',(self.namespace,reference)).fetchone()
            record=json.loads(row['value']) if row else None
            if not record or record.get('materialFormat')!=1:
                raise MaterialObservationBlocked('Restore the original observation snapshot',reason='snapshot_missing')
            if 'execution' not in record:
                if 'submissionAttempt' not in record or record['submissionAttempt'] is not None:
                    raise MaterialObservationBlocked('Claimed observation has no original execution snapshot',reason='snapshot_missing')
                record['execution']=_plain(asdict(candidate))
                conn.execute('UPDATE runtime_checkpoints SET value=? WHERE namespace=? AND key=?',(encode(record),self.namespace,reference))
            value=copy.deepcopy(record['execution']);owner=record['materialOwner']
        definition=value['definition']
        definition['resources']=tuple(HostResource(**ref) for ref in definition.get('resources',()))
        value['definition']=HostDefinition(**definition)
        value['tools']=tuple(HostToolDeclaration(**tool) for tool in value.get('tools',()))
        frozen=HostExecution(**value)
        if (frozen.actor,frozen.profile,frozen.interaction_id,frozen.job_id,frozen.business_ref,frozen.kind)!=(owner['actor'],owner['profile'],reference,owner['jobId'],self.interaction,'observation'):
            raise MaterialObservationBlocked('Frozen observation execution belongs to another scope',reason='owner_conflict')
        return frozen

    def _original_evidence(self,conn,reference,owner):
        row=conn.execute('SELECT value FROM runtime_checkpoints WHERE namespace=? AND key=?',(self.namespace,reference)).fetchone()
        saved=json.loads(row['value']) if row else None
        if not saved or saved.get('materialOwner')!=owner:
            raise MaterialObservationBlocked('Restore all original material observation records',reason='claim_missing')
        row=conn.execute('SELECT value FROM runtime_checkpoints WHERE namespace=? AND key=?',
            ('host-deliveries',encode([owner['actor'],reference]))).fetchone()
        delivery=json.loads(row['value']) if row else None
        if not delivery:
            if saved.get('materialFormat')!=1 or 'submissionAttempt' not in saved or saved['submissionAttempt'] is not None:
                raise MaterialObservationBlocked('Original submission authority is missing; never replay',reason='claim_missing')
            return saved,None
        receipt=delivery.get('receipt') or {};attempt=delivery.get('attempt') or {}
        parameters=json.loads(delivery.get('parameters','{}')).get('execution') or {}
        if (delivery.get('format')!=2 or delivery.get('legacyUnverified') or
            delivery.get('kind')!='observation' or delivery.get('jobId')!=owner['jobId'] or
            delivery.get('target')!=[owner['actor'],reference,owner['hostRef']] or
            parameters.get('business_ref')!=self.interaction or attempt.get('nonce')!=saved.get('submissionAttempt') or
            attempt.get('actor')!=owner['actor'] or attempt.get('profile')!=owner['profile'] or
            receipt.get('operation_id')!=reference or receipt.get('attempt_id')!=attempt.get('nonce')):
            raise MaterialObservationBlocked('Material child changed its original SDK claim',reason='owner_conflict')
        row=conn.execute('SELECT value FROM runtime_checkpoints WHERE namespace=? AND key=?',
            ('host-bindings',encode([owner['actor'],reference]))).fetchone()
        binding=json.loads(row['value']) if row else None
        if not binding or binding.get('hostRef')!=owner['hostRef'] or binding.get('sessionId')!=receipt.get('session_id'):
            raise MaterialObservationBlocked('Original observation session binding changed',reason='binding_conflict')
        if not saved.get('execution') or saved['execution']!=parameters:
            raise MaterialObservationBlocked('Original observation execution snapshot is missing or changed',reason='snapshot_conflict')
        return saved,delivery

    def _eligibility(self,conn,reference,owner):
        saved,delivery=self._original_evidence(conn,reference,owner)
        if delivery is None:return 'prepared',saved,None
        if not delivery.get('released'):raise MaterialObservationBlocked('Original material model or callbacks remain active',reason='model_unsettled')
        receipt=delivery['receipt'];attempt=delivery['attempt']
        terminal=receipt.get('terminal')
        if (terminal and terminal.get('state')=='completed' and receipt.get('state')=='completed' and
            (terminal.get('operation_id'),terminal.get('attempt_id'),terminal.get('session_id'),terminal.get('request_id'))==
            (reference,attempt['nonce'],receipt.get('session_id'),receipt.get('request_id')) and
            not saved.get('observationConflict') and not saved.get('observationInvalid') and ('observation' in saved or 'observationCandidate' in saved)):
            from .observations import SCHEMA
            from jsonschema import Draft202012Validator
            data={'observation':saved['observation'] if 'observation' in saved else saved['observationCandidate']}
            if len(encode(data).encode())>128*1024 or next(Draft202012Validator(SCHEMA).iter_errors(data),None):
                raise MaterialObservationBlocked('Original observation data is invalid',reason='observation_invalid')
            return 'completed',{**saved,'observation':data['observation']},delivery
        error=receipt.get('error') or {}
        if (error.get('dispatch')=='not_submitted' and receipt.get('request_id') is None and
            receipt.get('delivery')=='unknown' and terminal is None and not delivery.get('toolEntered')):
            return 'undispatched',saved,delivery
        raise MaterialObservationBlocked('Reconcile original uncertain material observation; do not submit again')

    def eligible(self,owner):
        # Nonblocking: this is also called from A2C's guarded local preparation.
        if not self.runtime.store.lock.acquire(blocking=False):
            raise MaterialObservationBlocked('Original SDK material evidence is busy',reason='evidence_busy')
        try:
            conn=self.runtime.store.db
            row=conn.execute('SELECT value FROM runtime_checkpoints WHERE namespace=? AND key=?',
                (self.namespace,'material:'+owner['scopeId'])).fetchone()
            group=json.loads(row['value']) if row else None
            if not group or group.get('owner')!=owner:raise MaterialObservationBlocked('Original material scope is unavailable',reason='scope_missing')
            return [(reference,*self._eligibility(conn,reference,owner)) for reference in group['children']]
        finally:self.runtime.store.lock.release()

    def pending(self,owner,reference):
        values=self.eligible(owner)
        candidate=next((value for value in values if value[0]==reference),None)
        if not candidate or candidate[1] not in ('prepared','undispatched'):
            raise MaterialObservationBlocked('Only a proven undispatched original child may wait')
        proof=self._pending_reference(owner,candidate)
        if not self.retry(proof):raise MaterialObservationBlocked('Original material child is not safely retryable')
        raise MaterialObservationPending(self.registry,proof)

    @staticmethod
    def _pending_reference(owner,candidate):
        reference=candidate[0]
        delivery=candidate[3]
        child={'actor':owner['actor'],'interaction':reference,'operation':reference,'attempt':delivery['attempt']['nonce'] if delivery else None}
        return {'owner':owner,'child':child,'proof':'not_submitted' if delivery else 'prepared_unclaimed'}

    def recovery_reference(self,owner):
        """Only qualified original facts can authorize this same task's continuation."""
        values=self.eligible(owner)
        remaining=[value for value in values if value[1]!='completed']
        if values and not remaining:return {'owner':owner,'child':None,'proof':'completed_scope'}
        if len(remaining)!=1 or remaining[0][1] not in ('prepared','undispatched'):return None
        reference=self._pending_reference(owner,remaining[0])
        return reference if self.retry(reference) else None

    def continuation_ready(self,reference,*,claim=False):
        if not isinstance(reference,dict) or set(reference)!={'owner','child','proof'}:return False
        if reference['proof']!='completed_scope':return self.retry(reference,claim=claim)
        values=self.eligible(reference['owner'])
        return reference['child'] is None and bool(values) and all(value[1]=='completed' for value in values)

    def reconcile(self,owner):
        """Outside A2C locks, query original receipts only; never dispatch or grant retry."""
        group_key='material:'+owner['scopeId'];now=time.time()
        with self.runtime.store.transaction() as conn:
            row=conn.execute('SELECT value FROM runtime_checkpoints WHERE namespace=? AND key=?',(self.namespace,group_key)).fetchone()
            group=json.loads(row['value']) if row else None
            if not group or group.get('owner')!=owner:
                raise MaterialObservationBlocked('Original material scope is unavailable',reason='scope_missing')
            originals=[(reference,*self._original_evidence(conn,reference,owner)) for reference in group['children']]
            query=now>=group.get('nextReconcileAt',0)
            pending=any(delivery and delivery['receipt'].get('request_id') and
                not (delivery['receipt'].get('terminal') and delivery.get('released')) for _,_,delivery in originals)
            if query and pending:
                delay=min(30.,max(1.,group.get('reconcileDelay',1.)))
                group.update(nextReconcileAt=now+delay,reconcileDelay=min(30.,delay*2))
                conn.execute('UPDATE runtime_checkpoints SET value=? WHERE namespace=? AND key=?',(encode(group),self.namespace,group_key))
        for reference,_,delivery in originals:
            if not delivery:continue
            receipt=delivery['receipt']
            if receipt.get('request_id') and not (receipt.get('terminal') and delivery.get('released')):
                if not query:continue
                result=self.registry.get_submission(owner['actor'],reference,reference)
                receipt=_plain(asdict(result))
            if receipt.get('output') is None:continue
            from .observations import observation_output
            invalid=False
            try:data=observation_output(receipt['output'])
            except RuntimeFault:
                data=None;invalid=True
            if data is None and not invalid:continue
            with self.runtime.store.transaction() as conn:
                saved,current=self._original_evidence(conn,reference,owner)
                if not current or current['attempt']!=delivery['attempt']:
                    raise MaterialObservationBlocked('Original observation generation changed',reason='owner_conflict')
                if invalid:saved['observationInvalid']=True
                else:
                    value=data['observation']
                    known=[saved[key] for key in ('observation','observationCandidate') if key in saved]
                    if any(digest({'observation':old})!=digest({'observation':value}) for old in known):
                        saved['observationConflict']=True
                    else:saved['observationCandidate']=value
                conn.execute('UPDATE runtime_checkpoints SET value=? WHERE namespace=? AND key=?',(encode(saved),self.namespace,reference))

    def retry(self,reference,*,claim=False):
        if not isinstance(reference,dict) or set(reference)!={'owner','child','proof'}:return False
        owner,child=reference['owner'],reference['child']
        values=self.eligible(owner)
        candidate=next((value for value in values if value[0]==child.get('operation')),None)
        if not candidate:return False
        state,delivery=candidate[1],candidate[3]
        expected={'actor':owner['actor'],'interaction':candidate[0],'operation':candidate[0],
                  'attempt':delivery['attempt']['nonce'] if delivery else None}
        if child!=expected:return False
        if state=='undispatched' and reference['proof']=='not_submitted':
            return self.registry.pending_retry(child,owner['jobId'],claim=claim)
        if state!='prepared' or reference['proof']!='prepared_unclaimed':return False
        if not claim:return True
        key=encode([owner['actor'],child['operation']]);now=time.time()
        with self.runtime.store.transaction() as conn:
            if not self.retry(reference):return False
            row=conn.execute('SELECT value FROM runtime_checkpoints WHERE namespace=? AND key=?',('host-submit-retry',key)).fetchone()
            saved=json.loads(row['value']) if row else {'nextAt':0,'delay':1.}
            if saved['nextAt']>now:return False
            delay=min(30.,max(1.,saved['delay']))
            conn.execute('INSERT OR REPLACE INTO runtime_checkpoints VALUES(?,?,?)',
                ('host-submit-retry',key,encode({'nextAt':now+delay,'delay':min(30.,delay*2)})))
        return True

    def guard(self,owner):
        def check(scope):
            value=_plain(asdict(scope))
            if ((value['task_id'],value['op'],value['subject_digest'],value['template_digest'],value['body_digest'],value['provider'],value['client'])!=
                (owner['taskId'],owner['op'],owner['subjectDigest'],owner['templateDigest'],owner['bodyDigest'],owner['provider'],owner['client']) or
                any(owner['inputDigests'].get(name)!=checksum for name,checksum in value['materials'])):
                raise RuntimeFault('identity_conflict','Original A2C material scope changed')
            # A2C separately validates original sealed body/template/auth/bytes.
            self.eligible(owner)
        return check
