"""Execute the original endpoint once; persist tasks and push original replies."""
import base64
from dataclasses import asdict
import time
import threading

from a2c.codec import digest,digest_bytes,parse_time
from a2c.card import card_digest,validate_card
from a2c.jws import header_of
from a2c.slots import verify_card
from a2c.common import Reply,Task
from a2c.errors import A2CError
from ..runtime.files_api import PrivateFiles
from ..runtime.protocol import RuntimeFault,stamp
from ..runtime import NativeSubmissionPending
from ..runtime import MaterialObservationPending,MaterialObservationBlocked
from contextlib import nullcontext
from .transport import stable


def task_data(task,*,local=False):
    value=asdict(task)
    value['files']={name:base64.b64encode(data).decode() for name,data in task.files.items()} if local else {}
    if not local:value.pop('files')
    return value


class EndpointSession:
    def __init__(self,runtime,identity,interaction,provider,*,on_permit=None,material_review=None):
        self.runtime,self.identity,self.interaction,self.provider=runtime,identity,interaction,provider
        self.store=provider.store;self.files=PrivateFiles(runtime.account,identity.agent_id)
        self.on_permit=on_permit
        self.material_review=material_review
        self.lock=threading.RLock()
        self.deferred={}
        for identifier,value in self.store.all('tasks').items():
            self.provider.tasks[identifier]=Task(**{**value,'files':{name:base64.b64decode(data) for name,data in value.get('files',{}).items()}})

    def save_tasks(self):
        with self.lock:
            for identifier,value in self.provider.tasks.items():self.store.put('tasks',identifier,task_data(value,local=True))

    def run(self,message):
        result=self.prepare(message)
        return self.finish(message) if result.get('deferred') else result

    def prepare(self,message):
        with self.lock:return self._prepare(message)

    def _prepare(self,message):
        mid=message['messageId'];payload=message['body'];fingerprint=digest(payload)
        record=self.store.get('calls',mid)
        if record:
            if record['fingerprint']!=fingerprint:raise RuntimeFault('id_conflict','Original endpoint request changed')
            if record.get('reply') is not None:return self.publish(message,record['reply'])
            if mid in self.deferred:return {'messageRef':mid,'deferred':True}
            raise RuntimeFault('unknown_result','The original endpoint began; reconcile its stored task and effects')
        self.runtime.require_execution()
        permit=self.runtime.connection.call(self.identity.agent_id,'request.start',{'messageId':mid,'interactionId':self.interaction},operation_id=stable('start',self.identity.agent_id,mid))
        if self.on_permit:self.on_permit(permit)
        materials={name:self.files.get(ref) for name,ref in payload['files'].items()}
        self.store.put('calls',mid,{'fingerprint':fingerprint,'state':'executing'})
        try:
            reply=self.provider.handle(payload['op'],payload['body'],materials,payload.get('taskId'),new_task_id=stable('task',mid),defer=True)
            self.save_tasks()
            if reply.then is not None:
                self.publish(message,self.pack(reply))
                self.deferred[mid]=reply
                return {'messageRef':mid,'deferred':True}
            self.save_tasks()
            packed=self.pack(reply)
            self.store.put('calls',mid,{'fingerprint':fingerprint,'state':'finished','reply':packed})
            return self.publish(message,packed)
        finally:self.save_tasks()

    def material_owner(self,message):
        if self.material_review is None:return None
        from ..runtime.scheduling import CURRENT_JOB
        from a2c.card import request_digest
        current=CURRENT_JOB.get()
        if not current or current[0] is not self.runtime.scheduler:
            raise RuntimeFault('forbidden','Material review requires its original SDK job')
        subject=message['body']['body'].get('request') or message['body']['body'].get('card')
        checksum=request_digest(subject) if message['body']['op']=='request.submit' else card_digest(subject)
        return self.material_review.owner(message,self.runtime.scheduler.get(current[1]),stable('task',message['messageId']),checksum)

    def finish(self,message,*,resume_operation=None):
        with self.lock:
            mid=message['messageId'];record=self.store.get('calls',mid)
            if record and record.get('reply') is not None:return self.publish(message,record['reply'])
            reply=self.deferred.pop(mid,None)
            material_owner=self.material_owner(message)
            if resume_operation=='material-review':
                if self.material_review is None:raise RuntimeFault('not_ready','Restore the original material recovery owner')
                reply=self.provider.resume_material_review(stable('task',mid),guard=self.material_review.guard(material_owner))
            elif resume_operation is not None:
                if resume_operation!=message['body']['op'] or resume_operation not in ('request.submit','card.countersign'):
                    raise RuntimeFault('forbidden','Only the verified original protocol task can continue')
                reply=(self.provider.resume_request if resume_operation=='request.submit' else self.provider.resume_countersign)(stable('task',mid))
            if reply is None:raise RuntimeFault('unknown_result','The original deferred endpoint needs reconciliation')
            self.runtime.require_execution()
            try:
                try:
                    with self.material_review.scope(material_owner) if self.material_review is not None else nullcontext():
                        reply=self.provider.finish(reply)
                except A2CError as exc:reply=Reply(exc.to_payload(message['body']['op']),task_id=reply.task_id,state='failed')
                self.save_tasks();packed=self.pack(reply)
                self.store.put('calls',mid,{'fingerprint':digest(message['body']),'state':'finished','reply':packed})
                return self.publish(message,packed)
            except MaterialObservationPending as pending:
                task=self.provider.tasks.get(reply.task_id) if reply.task_id else None
                reference=pending.reference
                if (pending.issuer is not self.runtime.host_registry or reference.get('owner')!=material_owner
                    or not task or task.state!='working' or reference.get('child',{}).get('actor')!=self.identity.agent_id):
                    raise RuntimeFault('unknown_result','Restore the original material observation proof') from None
                try:
                    self.material_review.eligible(material_owner)
                    retryable=self.material_review.retry(reference)
                except MaterialObservationBlocked as blocked:
                    # Contention here is still the original uncertain material scope.
                    self.store.put('calls',mid,{'fingerprint':digest(message['body']),'state':'unknown_material',
                        'materialOwner':material_owner,'materialReason':blocked.reason,'taskId':reply.task_id})
                    raise
                if not retryable:
                    raise RuntimeFault('unknown_result','Original material child is not proven undispatched') from None
                self.deferred[mid]=reply
                self.store.put('calls',mid,{'fingerprint':digest(message['body']),'state':'pending_material',
                    'materialObservation':reference,'taskId':reply.task_id})
                raise
            except MaterialObservationBlocked as blocked:
                self.store.put('calls',mid,{'fingerprint':digest(message['body']),'state':'unknown_material',
                    'materialOwner':material_owner,'materialReason':blocked.reason,'taskId':reply.task_id})
                raise
            except NativeSubmissionPending as pending:
                from ..runtime.scheduling import CURRENT_JOB
                current=CURRENT_JOB.get();registry=getattr(self.runtime,'host_registry',None)
                task=self.provider.tasks.get(reply.task_id) if reply.task_id else None
                if (not current or current[0] is not self.runtime.scheduler or pending.issuer is not registry
                    or pending.reference['actor']!=self.identity.agent_id or pending.reference['interaction']!=self.interaction
                    or not registry.pending_retry(pending.reference,current[1]) or not task or task.state!='working'):
                    raise RuntimeFault('unknown_result','Restore the original pending protocol scope; do not re-enter signing') from None
                self.deferred[mid]=reply
                self.store.put('calls',mid,{'fingerprint':digest(message['body']),'state':'pending_native',
                    'nativeSubmission':pending.reference,'taskId':reply.task_id})
                raise
            except Exception:
                task=self.provider.tasks.get(reply.task_id) if reply.task_id else None
                if task and task.state in ('working','auth-required'):
                    unknown={**self.pack(reply),'executionError':{'code':'unknown_result','recovery':'reconcile_original'}}
                    self.store.put('calls',mid,{'fingerprint':digest(message['body']),'state':'unknown','unknownReply':unknown})
                    self.publish(message,unknown)
                raise
            finally:self.save_tasks()

    def pack(self,reply):
        task=self.provider.tasks.get(reply.task_id) if reply.task_id else None
        return {'reply':{'body':reply.body,'taskId':reply.task_id,'state':reply.state,**({'task':task_data(task)} if task else {})},
                'bytes':{name:base64.b64encode(value).decode() for name,value in reply.files.items()},
                'taskBytes':{name:base64.b64encode(value).decode() for name,value in (task.files if task else {}).items()}}

    def reject_deferred_countersign(self,message,error):
        """Close a prepared review whose original job cannot dispatch again."""
        with self.lock:
            mid=message['messageId'];record=self.store.get('calls',mid)
            fingerprint=digest(message['body'])
            if (message['body']['op']!='card.countersign' or not record or record['fingerprint']!=fingerprint):
                raise RuntimeFault('identity_conflict','Restore the original prepared countersign')
            if record.get('reply') is not None:return self.publish(message,record['reply'])
            task=self.provider.tasks.get(stable('task',mid))
            if not task or task.op!='card.countersign' or task.state!='working' or record['state'] not in ('executing','pending_native','pending_material'):
                raise RuntimeFault('unknown_result','Reconcile the original countersign before reporting its outcome')
            failure=error.to_payload(task.op)
            task.state='failed'
            task.artifacts.append({'error':failure['error']})
            self.save_tasks()
            packed=self.pack(Reply(failure,task_id=task.id,state=task.state))
            self.store.put('calls',mid,{**record,'state':'finished','reply':packed})
            self.deferred.pop(mid,None)
            return self.publish(message,packed)

    def recover(self,message):
        """Consume already signed facts. Never replay the original model/callback."""
        with self.lock:
            mid=message['messageId'];record=self.store.get('calls',mid)
            if not record:return None
            if record.get('reply') is not None:return self.publish(message,record['reply'])
            payload=message['body'];op=payload['op']
            subject=payload['body'].get('request') or payload['body'].get('card')
            if op not in ('request.submit','card.countersign') or not subject:return None
            template=self.provider.template(subject['templateDigest'])
            task=self.provider.tasks.get(stable('task',mid))
            if not task:return None
            if op=='request.submit':
                rejected=self.provider.transport._market_rejection()
                if rejected:
                    if rejected['payload']['request']!=subject:
                        raise RuntimeFault('protocol_error','Original market rejection does not match this request')
                    # Preserve the original decision, signed proposal and WS
                    # receipt. No model, signing or market call is repeated.
                    failure=self.provider.transport._market_error(rejected['error']).to_payload(op)
                    task.state='failed'
                    artifact={'error':failure['error']}
                    if artifact not in task.artifacts:task.artifacts.append(artifact)
                    self.save_tasks()
                    packed=self.pack(Reply(failure,task_id=task.id,state='failed'))
                    self.store.put('calls',mid,{'fingerprint':digest(payload),'state':'finished','reply':packed})
                    return self.publish(message,packed)
                receipt=self.provider.transport.control_receipt('a2c.market')
                if not receipt:return None
                card=receipt['result'].get('card')
                if not card or card['signatures']['2']!=receipt['payload']['body']['card']['signatures']['2'] or receipt['payload']['request']!=subject:
                    raise RuntimeFault('protocol_error','Original market receipt does not match this request')
            else:
                saved=self.store.get('cards',card_digest(subject))
                card=saved.get('card') if saved else None
                if not card or card['signatures']['1']!=subject['signatures']['1'] or not card['signatures']['2']:return None
            validate_card(template,card,request=subject if op=='request.submit' else None)
            verify_card(template,card,self.provider.keys,request_ts=parse_time(header_of(subject['signature'])['a2c-ts']) if op=='request.submit' else None)
            review={name:base64.b64encode(self.files.get(ref)).decode() for name,ref in payload['files'].items()}
            self.store.put('cards',card_digest(card),{'card':card,'review':review,'state':'offered' if op=='request.submit' else 'formed','task':task.id,
                           **({'request':subject} if op=='request.submit' else {})})
            task.state='completed';task.artifacts=[{'card':card}];task.status=None
            self.save_tasks();packed=self.pack(Reply({'op':op,'card':card},task_id=task.id,state='completed'))
            self.store.put('calls',mid,{'fingerprint':digest(payload),'state':'finished','reply':packed})
            return self.publish(message,packed)

    def publish(self,message,packed):
        key=digest(packed);mid=message['messageId'];record=self.store.get('results',mid+key)
        if record is None:
            def upload(values):
                return {name:self.files.put(name,base64.b64decode(data),stable('result-file',self.identity.agent_id,mid,name,digest_bytes(base64.b64decode(data)))) for name,data in values.items()}
            refs=upload(packed['bytes']);task_refs=upload(packed.get('taskBytes',{}))
            envelope={'messageId':stable('result',self.identity.agent_id,mid,key),'kind':'a2c.result','recipient':message['sender'],
                      'interactionId':self.interaction,'inReplyTo':mid,'expiresAt':stamp(time.time()+86400),
                      'body':{'reply':packed['reply'],'files':refs,'taskFiles':task_refs,**({'executionError':packed['executionError']} if packed.get('executionError') else {})}}
            record={'envelope':envelope,'operationId':stable('result-send',envelope['messageId'])}
            self.store.put('results',mid+key,record)
        result=self.runtime.connection.call(self.identity.agent_id,'message.send',record['envelope'],operation_id=record['operationId'])
        receipt={'messageRef':mid,'replyReceipt':result,'state':packed['reply'].get('state') or 'completed'}
        if 'error' in packed['reply']['body']:receipt['error']=packed['reply']['body']['error']
        return receipt
