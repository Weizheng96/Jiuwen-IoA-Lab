"""Original A2C Transport over the resident WS and private file channel."""
import base64
import copy
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar

from a2c.binding import A2ATransport
from a2c.codec import digest,digest_bytes
from a2c.common import Reply,Task
from a2c.errors import A2CError
from ..runtime.files_api import PrivateFiles
from ..runtime.protocol import RuntimeFault,stamp


def stable(*values):return str(uuid.uuid5(uuid.NAMESPACE_URL,digest({'values':list(values)})))


class SessionTransport:
    def __init__(self,runtime,identity,interaction,admission,peer_protocol,*,request=None,public=None):
        relation=runtime.store.interaction(interaction_id=interaction)
        if not relation or relation['actor']!=identity.agent_id or relation['name']!='delegation':
            raise RuntimeFault('forbidden','Use an original local delegation interaction')
        self.runtime,self.identity,self.relation,self.admission,self.peer=runtime,identity,relation,admission,peer_protocol
        self.request=copy.deepcopy(request)
        self.namespace='a2c-transport:'+identity.agent_id+':'+interaction
        self.files=PrivateFiles(runtime.account,identity.agent_id)
        self.public=public or A2ATransport()
        self.owns_public=public is None
        self._operation=ContextVar('ioa-original-operation',default=None)

    @contextmanager
    def operation(self,reference):
        if reference is not None and (not isinstance(reference,str) or not 1<=len(reference)<=128):
            raise RuntimeFault('invalid_input','Use a saved local operation identity')
        token=self._operation.set(reference)
        try:yield self
        finally:self._operation.reset(token)

    def _key(self,kind,method):
        parent=self._operation.get()
        return kind+':'+(parent+':' if parent is not None else '')+method

    def _identity(self,kind):
        parent=self._operation.get()
        values=[self.identity.agent_id,self.relation['id']]
        if parent is not None:values.append(parent)
        return stable(*values,kind)

    def _saved(self,key,payload):
        record=self.runtime.store.checkpoint(self.namespace,key)
        fingerprint=digest(payload)
        if record:
            if record['fingerprint']!=fingerprint:raise RuntimeFault('id_conflict','Recover the original protocol parameters without re-signing')
            return record
        record={'fingerprint':fingerprint,**copy.deepcopy(payload)}
        self.runtime.store.checkpoint_set(self.namespace,key,record)
        return record

    def jwks(self,uri):
        if uri in (self.identity.protocol_id,self.identity.protocol_id+'/.well-known/jwks.json'):
            return {'keys':[self.identity.signer.jwk,self.identity.payment.jwk]}
        allowed={self.runtime.account.config['market'],self.runtime.account.config['pay'],self.peer}
        if uri not in allowed and uri not in {value+'/.well-known/jwks.json' for value in allowed}:
            raise RuntimeFault('forbidden','Key lookup is outside the configured protocol parties')
        return self.public.jwks(uri)

    def send(self,to,op,body,files=None,task_id=None):
        if to==self.runtime.account.config['market'] and op in ('card.endorse','delivery.anchor','card.close'):
            if op!='card.endorse' and self._operation.get() is None:raise RuntimeFault('invalid_input','Bind the original business operation before this market action')
            payload={'admissionId':self.admission,'op':op,'body':dict(body),**({'request':self.request} if op=='card.endorse' else {})}
            return self._control('a2c.market',payload)
        if to==self.runtime.account.config['pay'] and op=='ap2.pay':
            # Original AP2 carries the purpose inside its signed checkout, not
            # as a top-level field. Legacy proposal fees carry a request object.
            if not isinstance(body.get('request'),dict) and self._operation.get() is None:raise RuntimeFault('invalid_input','Bind the saved local payment operation before sending')
            return self._control('ap2.pay',{'admissionId':self.admission,'ap2':dict(body)})
        if to!=self.peer:raise RuntimeFault('forbidden','Protocol call is outside this delegation')
        if op=='template.get':return self.public.send(to,op,body)
        initial=op in ('request.submit','card.countersign') or op=='ap2.checkout' and body.get('purpose')=='proposal-fee'
        if op not in ('request.submit','card.countersign','ap2.checkout','work.start','step.authorize','delivery.fetch','acceptance.notify','payment.notify','termination.quote','termination.notify'):
            raise RuntimeFault('not_ready','This original method is outside the delegation adapter')
        if not initial and self._operation.get() is None:raise RuntimeFault('invalid_input','Bind the saved local business operation before sending')
        original={'op':op,'body':dict(body),'taskId':task_id,'files':{name:base64.b64encode(data).decode() for name,data in (files or {}).items()}}
        key=self._key('request',op)
        record=self._saved(key,{'original':original})
        if 'envelope' not in record:
            message=self._identity(op)
            references={}
            for name,data in record['original']['files'].items():
                decoded=base64.b64decode(data)
                # Keep an already-uploaded legacy file recoverable even when the
                # process died before it could save its outgoing envelope.
                upload=stable(self.identity.agent_id,self.relation['id'],op,name,digest_bytes(decoded)) if self._operation.get() is None else stable('file',message,name,digest_bytes(decoded))
                references[name]=self.files.put(name,decoded,upload)
            payload={'op':op,'body':record['original']['body'],'files':references,'admissionId':self.admission}
            if task_id is not None:payload['taskId']=task_id
            envelope={'messageId':message,'kind':'a2c.request',
                      'recipient':self.relation['peer'],'interactionId':self.relation['id'],'expiresAt':stamp(time.time()+3600),'body':payload}
            record={**record,'envelope':envelope}
            self.runtime.store.checkpoint_set(self.namespace,key,record)
        envelope=record['envelope']
        self.runtime.connection.call(self.identity.agent_id,'message.send',envelope,operation_id=stable('send',envelope['messageId']))
        raw=self.runtime.connection.wait_for(lambda:self._latest(envelope['messageId']))
        return self._reply(raw)

    def _control(self,kind,payload):
        record=self._saved(self._key('control',kind),{'payload':payload})
        result=self.runtime.connection.call(self.identity.agent_id,kind,record['payload'],operation_id=self._identity(kind))
        if 'error' in result:raise A2CError(result['error']['code'],result['error'].get('message',''))
        return Reply(result)

    def _latest(self,message=None,task=None):
        if self.runtime.closing.is_set():raise RuntimeFault('unknown_result','Runtime closed while the original call was waiting','resume_original')
        found=None
        for frame in self.runtime.store.messages(self.identity.agent_id):
            value=frame['message'];raw=value.get('body',{}).get('reply',{})
            if value.get('kind')=='a2c.result' and value.get('interactionId')==self.relation['id'] and value.get('sender')==self.relation['peer'] and ((message and value.get('inReplyTo')==message) or (task and raw.get('taskId')==task)):
                found=value['body']
        return found

    def _reply(self,payload):
        if payload.get('executionError'):raise RuntimeFault('unknown_result','The original service execution needs reconciliation','reconcile_original')
        raw=payload['reply'];body=raw['body']
        if 'error' in body:raise A2CError(body['error']['code'],body['error'].get('message',''))
        files={name:self.files.get(ref) for name,ref in payload['files'].items()}
        return Reply(body,files,raw.get('taskId'),raw.get('state'))

    def get_task(self,to,task_id):
        if to!=self.peer:raise RuntimeFault('forbidden','Task belongs to another protocol party')
        payload=self._latest(task=task_id)
        if payload is None:raise RuntimeFault('unknown_result','Restore the original pushed task snapshot')
        if payload.get('executionError'):raise RuntimeFault('unknown_result','The original service execution needs reconciliation','reconcile_original')
        reply=payload['reply'];raw=reply.get('task')
        if not isinstance(raw,dict) or raw.get('id')!=task_id:raise RuntimeFault('protocol_error','Original task snapshot is missing')
        task=Task(**raw);task.files={name:self.files.get(ref) for name,ref in payload.get('taskFiles',{}).items()}
        return task

    def close(self):
        if self.owns_public:self.public.close()

    def control_receipt(self,kind):
        record=self.runtime.store.checkpoint(self.namespace,self._key('control',kind))
        response=self.runtime.store.response(self.identity.agent_id,kind,self._identity(kind))
        return {'payload':record['payload'],'result':response['result']} if record and response and response['ok'] else None

    def received(self,op):
        record=self.runtime.store.checkpoint(self.namespace,self._key('request',op))
        if not record or 'envelope' not in record:return None
        result=self._latest(record['envelope']['messageId'])
        return {'call':record['original'],'result':result} if result is not None else None
