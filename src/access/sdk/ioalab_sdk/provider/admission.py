"""Recoverable service admission controls; the platform owns the actual policy."""
import threading
import uuid

from a2c.codec import digest
from ..adapters.transport import stable
from ..runtime.protocol import RuntimeFault


class AdmissionPolicy:
    def __init__(self,execution):
        self.execution,self.runtime=execution,execution.runtime
        self.locks={key:threading.RLock() for key in execution.role.identities}

    def namespace(self,identity):return 'provider-policy:'+identity.agent_id

    def configuration(self,identity):
        config=self.execution.configs.get(identity.profile,{})
        return {'maxActiveDelegations':config.get('max_active_delegations',1),'accepting':config.get('accepting',True)}

    def read(self,identity):
        return self.runtime.connection.call(identity.agent_id,'admission.status',{},operation_id=str(uuid.uuid4()))

    def _recover(self,identity):
        pending=self.runtime.store.checkpoint(self.namespace(identity),'pending')
        if pending:self._finish(identity,pending)

    def initialize(self,identity):
        with self.locks[identity.profile]:
            self._recover(identity)
            namespace=self.namespace(identity);config=self.configuration(identity)
            previous=self.runtime.store.checkpoint(namespace,'configuration')
            if previous==config:return self.read(identity)
            attempt=self.runtime.store.checkpoint(namespace,'initialization')
            operation=attempt['operationId'] if attempt and attempt['configuration']==config else str(uuid.uuid4())
            return self._change(identity,operation,'configuration')['policyReceipt']

    def change(self,identity,operation,accepting):
        with self.locks[identity.profile]:
            self._recover(identity)
            return self._change(identity,operation,'resume' if accepting else 'pause')

    def _change(self,identity,operation,action):
        namespace=self.namespace(identity);store=self.runtime.store
        fingerprint=digest({'action':action})
        old=store.checkpoint(namespace,'operation:'+operation)
        if old:
            if old['fingerprint']!=fingerprint:raise RuntimeFault('id_conflict','Original admission policy action changed')
            if 'error' in old:raise RuntimeFault(**old['error'])
            return {'policyReceipt':old['receipt'],'currentPolicy':self.read(identity)}
        self.runtime.require_execution()
        if action=='resume' and not self.execution.ready(identity.profile):
            raise RuntimeFault('not_ready','Publish a valid service and restore its business host, skills and tools before resuming','restore_service')
        current=self.read(identity);config=self.configuration(identity)
        previous=store.checkpoint(namespace,'configuration')
        maximum=config['maxActiveDelegations'] if not previous or previous['maxActiveDelegations']!=config['maxActiveDelegations'] else current['maxActiveDelegations']
        accepting=config['accepting'] if not previous or previous['accepting']!=config['accepting'] else current['accepting']
        if action!='configuration':accepting=action=='resume'
        pending={'operationId':operation,'fingerprint':fingerprint,'configuration':config,
                 'body':{'expectedRevision':current['revision'],'maxActiveDelegations':maximum,'accepting':accepting}}
        values={'pending':pending}
        if action=='configuration':values['initialization']={'operationId':operation,'configuration':config}
        store.checkpoint_update(namespace,values)
        receipt=self._finish(identity,pending)
        return {'policyReceipt':receipt,'currentPolicy':self.read(identity)}

    def _finish(self,identity,pending):
        namespace=self.namespace(identity);store=self.runtime.store
        operation=pending['operationId'];wire=stable('provider-policy',identity.agent_id,operation)
        try:
            receipt=self.runtime.connection.call(identity.agent_id,'admission.configure',pending['body'],operation_id=wire)
        except RuntimeFault:
            response=store.response(identity.agent_id,'admission.configure',wire)
            if not response or response.get('ok'):
                raise RuntimeFault('unknown_result','Recover the original admission policy operation before changing it','resume_original',operation_id=operation) from None
            error={key:response['error'][key] for key in ('code','message','recovery')}
            store.checkpoint_update(namespace,{'operation:'+operation:{'fingerprint':pending['fingerprint'],'error':error}},remove=('pending',))
            raise RuntimeFault(**error) from None
        expected=pending['body']
        if (receipt.get('service')!=identity.agent_id or receipt.get('revision')!=expected['expectedRevision']+1
                or any(receipt.get(key)!=expected[key] for key in ('maxActiveDelegations','accepting'))):
            raise RuntimeFault('unknown_result','Inspect the original admission policy receipt','resume_original',operation_id=operation)
        store.checkpoint_update(namespace,{'operation:'+operation:{'fingerprint':pending['fingerprint'],'receipt':receipt},
                                          'configuration':pending['configuration']},remove=('pending',))
        return receipt
