"""Recoverable admission policy; automatic health recovery uses original revision CAS."""
import threading
import uuid

from a2c.codec import digest
from ..adapters import stable
from ..runtime import RuntimeFault


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
        if pending:self._finish(identity,pending,recover=True)

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

    def _change(self,identity,operation,action,*,expected_revision=None,extra_values=None):
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
        if expected_revision is not None and current['revision']!=expected_revision:
            raise RuntimeFault('revision_conflict','Original automatic policy revision changed','preserve_current_policy')
        previous=store.checkpoint(namespace,'configuration')
        maximum=config['maxActiveDelegations'] if not previous or previous['maxActiveDelegations']!=config['maxActiveDelegations'] else current['maxActiveDelegations']
        accepting=config['accepting'] if not previous or previous['accepting']!=config['accepting'] else current['accepting']
        if action!='configuration':accepting=action=='resume'
        pending={'operationId':operation,'fingerprint':fingerprint,'configuration':config,
                 'body':{'expectedRevision':current['revision'],'maxActiveDelegations':maximum,'accepting':accepting}}
        values={**(extra_values or {}),'pending':pending}
        if action=='configuration':values['initialization']={'operationId':operation,'configuration':config}
        wire=stable('provider-policy',identity.agent_id,operation)
        # The original operation and role intent have no local crash gap.
        store.prepare_checkpointed(identity.agent_id,'admission.configure',pending['body'],wire,namespace,values)
        receipt=self._finish(identity,pending)
        return {'policyReceipt':receipt,'currentPolicy':self.read(identity)}

    def _finish(self,identity,pending,*,recover=False):
        namespace=self.namespace(identity);store=self.runtime.store
        operation=pending['operationId'];wire=stable('provider-policy',identity.agent_id,operation)
        try:
            if recover:
                response=store.response(identity.agent_id,'admission.configure',wire)
                if response:
                    if not response['ok']:raise RuntimeFault(**{key:response['error'][key] for key in ('code','message','recovery')})
                    receipt=response['result']
                else:
                    # Query the original wire operation. The existing reliable
                    # outbox retains its same-ID platform-idempotent delivery.
                    receipt=self.runtime.connection.call(identity.agent_id,'operation.get',
                        {'operation':'admission.configure','operationId':wire})
            else:
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

    def system_health(self,identity,host_ref,state,*,recovery_ready=None):
        """P35, invoked by the existing resident's P34 coordination.

        Only new admission changes here. Existing contract work and held commits
        remain with their original owners. A missing whole-service readiness
        fence never becomes permission to resume.
        """
        if state not in ('available','unavailable'):return {'status':'waiting','reason':state}
        with self.locks[identity.profile]:
            self._recover(identity)
            namespace=self.namespace(identity);store=self.runtime.store
            automatic=store.checkpoint(namespace,'system-health-pause')
            if automatic and automatic.get('state') not in ('finished','superseded','resumed'):
                if automatic.get('owner')!=store.runtime_id or automatic.get('service')!=identity.agent_id:
                    raise RuntimeFault('identity_conflict','Automatic pause belongs to another original instance')
                operation=automatic['pauseOperation']
                original=store.checkpoint(namespace,'operation:'+operation)
                if original and 'error' in original:
                    store.checkpoint_set(namespace,'system-health-pause',{**automatic,'state':'superseded'})
                    return {'status':'preserved','reason':'original_policy_rejected'}
                if not original or 'receipt' not in original:
                    raise RuntimeFault('unknown_result','Recover the original automatic pause receipt','resume_original',operation_id=operation)
                if automatic.get('state')=='pausing':
                    automatic={**automatic,'pauseReceipt':original['receipt'],'state':'paused'}
                    store.checkpoint_set(namespace,'system-health-pause',automatic)
            current=self.read(identity)
            if state=='unavailable':
                if automatic and automatic.get('state') in ('paused','resuming','superseded'):return {'status':'preserved','reason':'original_cycle'}
                if not current['accepting']:return {'status':'preserved','reason':'already_paused'}
                operation=str(uuid.uuid4())
                automatic={'owner':store.runtime_id,'service':identity.agent_id,'hostRef':host_ref,
                           'state':'pausing','beforePolicy':current,'pauseOperation':operation}
                result=self._change(identity,operation,'pause',expected_revision=current['revision'],
                                    extra_values={'system-health-pause':automatic})
                automatic={**automatic,'state':'paused','pauseReceipt':result['policyReceipt']}
                store.checkpoint_set(namespace,'system-health-pause',automatic)
                return {'status':'paused','policyReceipt':result['policyReceipt']}
            if automatic and automatic.get('state')=='superseded':
                store.checkpoint_set(namespace,'system-health-pause',{**automatic,'state':'finished'})
                return {'status':'preserved','reason':'policy_changed'}
            if not automatic or automatic.get('state') in ('finished','resumed'):
                return {'status':'preserved','reason':'not_system_paused'}
            if automatic.get('hostRef')!=host_ref:
                return {'status':'waiting','reason':'restore_original_host'}
            if automatic.get('state')=='resuming':
                original=store.checkpoint(namespace,'operation:'+automatic['resumeOperation'])
                if original and 'error' in original:
                    store.checkpoint_set(namespace,'system-health-pause',{**automatic,'state':'superseded'})
                    return {'status':'preserved','reason':'original_resume_rejected'}
                if not original or 'receipt' not in original:
                    raise RuntimeFault('unknown_result','Recover the original automatic resume receipt','resume_original',
                                       operation_id=automatic['resumeOperation'])
                store.checkpoint_set(namespace,'system-health-pause',{**automatic,'state':'resumed'})
                return {'status':'resumed','policyReceipt':original['receipt']}
            if current['accepting'] or current['revision']!=automatic['pauseReceipt']['revision']:
                store.checkpoint_set(namespace,'system-health-pause',{**automatic,'state':'superseded'})
                return {'status':'preserved','reason':'policy_changed'}
            ready=recovery_ready(identity.profile) if callable(recovery_ready) else None
            if not isinstance(ready,dict) or ready.get('ready') is not True:
                return {'status':'waiting','reason':'restore_original_definition_and_resources'}
            operation=str(uuid.uuid4())
            automatic={**automatic,'state':'resuming','resumeOperation':operation}
            result=self._change(identity,operation,'resume',expected_revision=automatic['pauseReceipt']['revision'],
                                extra_values={'system-health-pause':automatic})
            store.checkpoint_set(namespace,'system-health-pause',{**automatic,'state':'resumed'})
            return {'status':'resumed','policyReceipt':result['policyReceipt']}
