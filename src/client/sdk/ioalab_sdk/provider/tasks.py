"""Owner queries and verified continuation of the original provider job."""
import json
import threading
import uuid

from a2c.errors import A2CError

from ..adapters import stable
from ..runtime import RuntimeFault,JobOutcome


class ProviderTasks:
    HANDLERS=('provider.a2c','provider.production','provider.settlement')

    def __init__(self,execution):
        self.execution=execution;self.runtime=execution.runtime
        from .production_recovery import ProductionRecovery
        self.production=ProductionRecovery(execution)
        self.lock=threading.RLock()

    @staticmethod
    def namespace(identity):return 'provider-recovery:'+identity.agent_id

    def wait(self,identity,job,session,definition,host_status,*,native_operation,reason='verifier_upload_round_finished'):
        if host_status not in ('completed','failed'):raise ValueError('A known terminal host result is required')
        proof={'roundId':str(uuid.uuid4()),'messageId':job['payload']['messageId'],
               'sessionId':session,'definitionDigest':definition,'hostStatus':host_status,'nativeOperation':native_operation,'reason':reason}
        self.runtime.store.checkpoint_set(self.namespace(identity),job['id'],proof)

    def clear(self,identity,job):self.runtime.store.checkpoint_delete(self.namespace(identity),job)

    def proof(self,identity,job):return self.runtime.store.checkpoint(self.namespace(identity),job)

    def native_operation(self,identity,job):
        """A native child changes only under the original SDK continuation proof."""
        continuation=job.get('continuation')
        if not continuation:return job['id']
        if set(continuation)=={'materialObservation'}:
            # Material verification has not entered the original business model yet.
            return job['id']
        if set(continuation)=={'nativeSubmission'}:
            pending=continuation['nativeSubmission']
            if not self.execution.host.pending_retry(pending,job['id']):
                raise RuntimeFault('identity_conflict','Restore the original undispatched native operation')
            return pending['operation']
        proof=self.proof(identity,job['id'])
        if (continuation.get('kind')!='provider.verifier' or not proof or continuation.get('proof')!=proof
            or proof.get('hostStatus') not in ('completed','failed') or not proof.get('roundId')):
            raise RuntimeFault('identity_conflict','Restore the original verifier continuation proof')
        if not self.execution.host.original_execution_ended(identity.agent_id,job['interaction'],proof.get('nativeOperation',job['id']),job['id']):
            raise RuntimeFault('unknown_result','The original native execution has not ended and drained')
        return stable('native-verifier',job['id'],proof['roundId'])

    def records(self,identity,reference=None):
        if reference is not None and (not isinstance(reference,str) or not 1<=len(reference)<=128):
            raise RuntimeFault('invalid_input','Use the original task reference','correct_input')
        values=self.runtime.scheduler.records(identity.agent_id,self.HANDLERS,reference=reference)
        if reference is not None and not values:raise RuntimeFault('not_ready','The original service task is unavailable')
        return values

    def source(self,identity,job):
        relation=self.runtime.store.interaction(interaction_id=job['interaction'])
        if not relation or relation['actor']!=identity.agent_id or relation['direction']!='inbound' or relation['name']!='delegation':
            raise RuntimeFault('identity_conflict','Restore the original service interaction')
        frame=self.runtime.store.message(identity.agent_id,job['payload']['messageId'])
        message=frame['message'] if frame else None
        if not message or message.get('kind')!='a2c.request' or message.get('interactionId')!=relation['id']:
            raise RuntimeFault('not_ready','Restore the original protocol message')
        return relation,message

    def eligible(self,identity,job):
        if job['state']!='unknown':return False,'not_unknown',None
        proof=self.proof(identity,job['id'])
        if not proof or proof.get('hostStatus') not in ('completed','failed'):return False,'original_model_result_unknown',None
        relation,message=self.source(identity,job)
        if job['handler']!='provider.a2c' or message['body']['op']!='request.submit':return False,'wrong_phase',None
        if (proof['messageId']!=message['messageId'] or proof['sessionId']!=relation['session']
                or job['session']!=relation['session'] or relation['details'].get('definitionDigest')!=proof['definitionDigest']):
            return False,'identity_conflict',None
        if not self.execution.host.original_execution_ended(identity.agent_id,relation['id'],proof.get('nativeOperation',job['id']),job['id']):
            return False,'original_model_result_unknown',None
        if not self.execution.model_available():return False,'host_unavailable',None
        if not self.runtime.connection.connected or self.runtime.stop_requested.is_set():return False,'not_ready',None
        endpoint=self.execution.context(identity,relation,job)
        try:endpoint.provider.resume_request(stable('task',message['messageId']))
        except A2CError as exc:return False,exc.code,None
        return True,proof.get('reason','verifier_upload_round_finished'),proof

    def snapshot(self,identity,job):
        if job['handler']=='provider.production':
            candidate=None
            try:
                candidate=self.production.candidate(identity,job)
                reason='runtime_verified' if candidate['mode']=='runtime_verified' else 'owner_review_required'
            except (RuntimeFault,A2CError) as exc:reason=exc.code
            mode=candidate['mode'] if candidate else None
            return {'taskRef':job['id'],'phase':'production','status':job['state'],'interactionId':job['interaction'],
                    'sessionId':job['session'],'canResume':mode=='runtime_verified','resumeReason':reason,
                    'recoveryMode':mode,'requiresConfirmation':mode=='owner_review',
                    'result':json.loads(job['result']) if job['result'] else None}
        try:
            allowed,reason,_=self.eligible(identity,job)
        except RuntimeFault as exc:allowed,reason=False,exc.code
        phase={'provider.production':'production','provider.settlement':'termination'}.get(job['handler'],'unknown')
        if phase=='unknown':
            try:
                _,message=self.source(identity,job)
                phase={'request.submit':'quote','card.countersign':'decision'}.get(message['body']['op'],'control')
            except RuntimeFault:pass
        return {'taskRef':job['id'],'phase':phase,'status':job['state'],'interactionId':job['interaction'],
                'sessionId':job['session'],'canResume':allowed,'resumeReason':reason,
                'result':json.loads(job['result']) if job['result'] else None}

    def get(self,identity,reference=None):
        return {'items':[self.snapshot(identity,job) for job in self.records(identity,reference)]}

    def resume(self,identity,reference,operation,confirmation_ref=None):
        with self.lock:return self._resume(identity,reference,operation,confirmation_ref)

    def _resume(self,identity,reference,operation,confirmation_ref):
        job=self.records(identity,reference)[0]
        receipt=self.runtime.scheduler.resumption(identity.agent_id,operation,reference)
        if receipt is None:
            if job['handler']=='provider.production':
                candidate=self.production.candidate(identity,job)
                pending=self.production.approve(identity,job,operation,candidate,confirmation_ref)
                if pending:return pending
                current=self.production.candidate(identity,job)
                if current!=candidate:raise RuntimeFault('id_conflict','Original production state changed before continuation')
                continuation={'kind':'provider.production','previewDigest':candidate['previewDigest'],'mode':candidate['mode']}
            else:
                if confirmation_ref is not None:raise RuntimeFault('id_conflict','This quote uses its original runtime evidence')
                allowed,reason,proof=self.eligible(identity,job)
                if not allowed:raise RuntimeFault('unknown_result','Original task cannot be resumed safely: '+reason,'reconcile_original')
                continuation={'kind':'provider.verifier','proof':proof}
            receipt=self.runtime.scheduler.requeue(reference,operation,continuation)
        current=self.records(identity,reference)[0]
        return {'status':'accepted','data':{'resumeReceipt':receipt,'currentTask':self.snapshot(identity,current),
                'nextAction':{'operation':'provider.tasks.get','purpose':'observe_original_task'}}}

    def require_continuation(self,identity,job,relation,message):
        continuation=job.get('continuation')
        if not continuation:return False
        if set(continuation)=={'materialObservation'}:
            reference=continuation['materialObservation']
            endpoint=self.execution.context(identity,relation,job)
            owner=endpoint.material_owner(message)
            if reference.get('owner')!=owner:
                raise RuntimeFault('identity_conflict','Restore the original sealed material task owner')
            endpoint.material_review.eligible(owner)
            if not endpoint.material_review.continuation_ready(reference):
                raise RuntimeFault('unknown_result','Original material scope is not qualified to continue')
            return 'material-review'
        if set(continuation)=={'nativeSubmission'}:
            pending=continuation['nativeSubmission']
            if (job['handler']!='provider.a2c' or message['body']['op'] not in ('request.submit','card.countersign')
                or pending.get('actor')!=identity.agent_id or pending.get('interaction')!=relation['id']
                or not self.execution.host.pending_retry(pending,job['id'])):
                raise RuntimeFault('identity_conflict','Restore the original undispatched protocol task')
            return message['body']['op']
        proof=self.proof(identity,job['id'])
        if (set(continuation)!={'kind','proof'} or continuation['kind']!='provider.verifier' or not proof
                or continuation['proof']!=proof or proof.get('hostStatus') not in ('completed','failed') or proof['messageId']!=message['messageId']
                or proof['sessionId']!=relation['session'] or proof['definitionDigest']!=relation['details'].get('definitionDigest')
                or job['handler']!='provider.a2c' or message['body']['op']!='request.submit'):
            raise RuntimeFault('identity_conflict','Restore the original verified quote continuation')
        return 'request.submit'

    def resume_native(self,identity,reference):
        """The original role owns its task; only proven no-dispatch can resume it."""
        job=self.records(identity,reference)[0]
        if job['state'] not in ('waiting','unknown') or self.runtime.scheduler.is_executing(reference):return False
        result=json.loads(job['result']) if job['result'] else {}
        material=result.get('materialObservation')
        if not material and job['state']=='unknown':
            relation,message=self.source(identity,job)
            endpoint=self.execution.context(identity,relation,job)
            original=endpoint.store.get('calls',message['messageId']) or {}
            if original.get('state')=='unknown_material':return self.resume_material(identity,job,None)
            material=original.get('materialObservation') if original.get('state')=='pending_material' else None
        if material or result.get('reason')=='material_observation_unknown':
            return self.resume_material(identity,job,material)
        pending=result.get('nativeSubmission')
        relation,message=self.source(identity,job)
        endpoint=self.execution.context(identity,relation,job)
        if job['state']=='unknown':
            original=endpoint.store.get('calls',message['messageId'])
            proven=self.execution.host.pending_for_job(reference,identity.agent_id,relation['id'])
            if not original or original.get('state')!='pending_native' or not proven or original.get('nativeSubmission')!=proven:return False
            pending=proven
        if not pending:return False
        if (job['handler']!='provider.a2c' or message['body']['op'] not in ('request.submit','card.countersign')
            or pending.get('actor')!=identity.agent_id or pending.get('interaction')!=relation['id']
            or not self.execution.host.pending_retry(pending,reference)):
            raise RuntimeFault('identity_conflict','Original pending task/source changed')
        # Public A2C resume validates original task/card/material/decision/terminal/deadline.
        resume=endpoint.provider.resume_request if message['body']['op']=='request.submit' else endpoint.provider.resume_countersign
        try:resume(stable('task',message['messageId']))
        except A2CError:return False
        if job['state']=='unknown':
            self.runtime.scheduler.resolve(reference,JobOutcome('waiting',{'reason':'native_not_submitted','nativeSubmission':pending},job['session']))
            return True
        if not self.execution.host.pending_retry(pending,reference,claim=True):return False
        self.runtime.scheduler.resume(reference,{'nativeSubmission':pending})
        return True

    def resume_material(self,identity,job,reference):
        from ..runtime import MaterialObservationBlocked
        relation,message=self.source(identity,job)
        endpoint=self.execution.context(identity,relation,job)
        original=endpoint.store.get('calls',message['messageId']) or {}
        reference=reference or original.get('materialObservation')
        if original.get('state') not in ('pending_material','unknown_material'):return False
        owner=(reference or {}).get('owner') or original.get('materialOwner') or {}
        if (owner.get('jobId'),owner.get('actor'),owner.get('profile'),owner.get('interaction'),owner.get('messageId'),owner.get('taskId'))!=(job['id'],identity.agent_id,identity.profile,relation['id'],message['messageId'],stable('task',message['messageId'])):
            raise RuntimeFault('identity_conflict','Original material scope changed')
        try:
            # Actual S04 reconciliation is outside the A2C preparation/guard lock.
            endpoint.material_review.reconcile(owner)
            if original.get('state')=='unknown_material':
                if original.get('materialOwner')!=owner:
                    raise RuntimeFault('identity_conflict','Original uncertain material owner changed')
                proven=endpoint.material_review.recovery_reference(owner)
                if reference and reference!=proven:return False
                reference=proven
            if not reference:return False
            endpoint.material_review.eligible(owner)
            if not endpoint.material_review.continuation_ready(reference):return False
            # Pure public pre-review preparation validates original A2C seal;
            # it does not run material/model/policy or change reviewed state.
            endpoint.provider.resume_material_review(stable('task',message['messageId']),guard=endpoint.material_review.guard(owner))
            if job['state']=='unknown':
                reason='material_reconciled' if reference['proof']=='completed_scope' else 'material_not_submitted'
                self.runtime.scheduler.resolve(job['id'],JobOutcome('waiting',{'reason':reason,'materialObservation':reference},job['session']))
                return True
            if not endpoint.material_review.continuation_ready(reference,claim=True):return False
            self.runtime.scheduler.resume(job['id'],{'materialObservation':reference})
            return True
        except (A2CError,MaterialObservationBlocked):return False
