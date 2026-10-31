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
from .transport import stable


def task_data(task,*,local=False):
    value=asdict(task)
    value['files']={name:base64.b64encode(data).decode() for name,data in task.files.items()} if local else {}
    if not local:value.pop('files')
    return value


class EndpointSession:
    def __init__(self,runtime,identity,interaction,provider):
        self.runtime,self.identity,self.interaction,self.provider=runtime,identity,interaction,provider
        self.store=provider.store;self.files=PrivateFiles(runtime.account,identity.agent_id)
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
        self.runtime.connection.call(self.identity.agent_id,'request.start',{'messageId':mid,'interactionId':self.interaction},operation_id=stable('start',self.identity.agent_id,mid))
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

    def finish(self,message,*,resume_request=False):
        with self.lock:
            mid=message['messageId'];record=self.store.get('calls',mid)
            if record and record.get('reply') is not None:return self.publish(message,record['reply'])
            reply=self.deferred.pop(mid,None)
            if resume_request:
                if message['body']['op']!='request.submit':raise RuntimeFault('forbidden','Only the verified original quote can continue')
                reply=self.provider.resume_request(stable('task',mid))
            if reply is None:raise RuntimeFault('unknown_result','The original deferred endpoint needs reconciliation')
            self.runtime.require_execution()
            try:
                try:reply=self.provider.finish(reply)
                except A2CError as exc:reply=Reply(exc.to_payload(message['body']['op']),task_id=reply.task_id,state='failed')
                self.save_tasks();packed=self.pack(reply)
                self.store.put('calls',mid,{'fingerprint':digest(message['body']),'state':'finished','reply':packed})
                return self.publish(message,packed)
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
        return {'messageRef':mid,'replyReceipt':result,'state':packed['reply'].get('state') or 'completed'}
