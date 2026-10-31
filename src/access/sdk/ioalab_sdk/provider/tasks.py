"""Owner queries and verified continuation of the original provider job."""
import json
import uuid

from a2c.errors import A2CError

from ..adapters.transport import stable
from ..runtime.protocol import RuntimeFault


class ProviderTasks:
    HANDLERS=('provider.a2c','provider.production')

    def __init__(self,execution):
        self.execution=execution;self.runtime=execution.runtime

    @staticmethod
    def namespace(identity):return 'provider-recovery:'+identity.agent_id

    def wait(self,identity,job,session,definition,host_status):
        if host_status not in ('completed','failed'):raise ValueError('A known terminal host result is required')
        proof={'roundId':str(uuid.uuid4()),'messageId':job['payload']['messageId'],
               'sessionId':session,'definitionDigest':definition,'hostStatus':host_status}
        self.runtime.store.checkpoint_set(self.namespace(identity),job['id'],proof)

    def clear(self,identity,job):self.runtime.store.checkpoint_delete(self.namespace(identity),job)

    def proof(self,identity,job):return self.runtime.store.checkpoint(self.namespace(identity),job)

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
        message=next((value['message'] for value in self.runtime.store.messages(identity.agent_id)
                      if value['message']['messageId']==job['payload']['messageId']),None)
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
        if not self.execution.model_available():return False,'host_unavailable',None
        if not self.runtime.connection.connected or self.runtime.stop_requested.is_set():return False,'not_ready',None
        endpoint=self.execution.context(identity,relation,job)
        try:endpoint.provider.resume_request(stable('task',message['messageId']))
        except A2CError as exc:return False,exc.code,None
        return True,'verifier_upload_round_finished',proof

    def snapshot(self,identity,job):
        try:
            allowed,reason,_=self.eligible(identity,job)
        except RuntimeFault as exc:allowed,reason=False,exc.code
        phase='production' if job['handler']=='provider.production' else 'unknown'
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

    def resume(self,identity,reference,operation):
        job=self.records(identity,reference)[0]
        receipt=self.runtime.scheduler.resumption(identity.agent_id,operation,reference)
        if receipt is None:
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
        proof=self.proof(identity,job['id'])
        if (set(continuation)!={'kind','proof'} or continuation['kind']!='provider.verifier' or not proof
                or continuation['proof']!=proof or proof.get('hostStatus') not in ('completed','failed') or proof['messageId']!=message['messageId']
                or proof['sessionId']!=relation['session'] or proof['definitionDigest']!=relation['details'].get('definitionDigest')
                or job['handler']!='provider.a2c' or message['body']['op']!='request.submit'):
            raise RuntimeFault('identity_conflict','Restore the original verified quote continuation')
        return True
