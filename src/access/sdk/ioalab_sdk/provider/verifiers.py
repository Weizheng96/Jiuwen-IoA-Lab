"""One verifier-authoring owner for local management and task-scoped proposals."""
import threading

import httpx
from a2c.codec import digest
from a2c.errors import A2CError
from a2c.verifier.package import load
from jsonschema import Draft202012Validator

from ..adapters.transport import stable
from ..runtime.protocol import RuntimeFault


TASK_SOURCE_LIMIT=64*1024
SCHEMA={'type':'object','properties':{
    'verifierId':{'type':'string','pattern':r'^[a-z][a-z0-9_]*@\d+\.\d+(\.\d+)?$','maxLength':160},
    'source':{'type':'string','minLength':1},
    'reads':{'type':'array','maxItems':256,'items':{'type':'string','maxLength':256}},
    'kind':{'enum':['script','skill']}},'required':['verifierId','source'],'additionalProperties':False}
TASK_SCHEMA={**SCHEMA,'properties':{**SCHEMA['properties'],'source':{**SCHEMA['properties']['source'],'maxLength':TASK_SOURCE_LIMIT}}}


def platform_fault(exc):
    if isinstance(exc,A2CError):return RuntimeFault('invalid_input',exc.message,'correct_input')
    if isinstance(exc,httpx.HTTPStatusError):
        if exc.response.status_code>=500:return RuntimeFault('unknown_result','Recover the original provider operation','resume_original')
        try:problem=exc.response.json().get('error',{})
        except (ValueError,AttributeError):problem={}
        if (not isinstance(problem,dict) or not {'code','message','recovery'}<=set(problem)
                or not all(isinstance(problem[key],str) and problem[key] for key in ('code','message','recovery'))):
            return RuntimeFault('protocol_error','Platform returned an invalid error envelope','inspect_platform')
        return RuntimeFault(problem['code'],problem['message'],problem['recovery'])
    return RuntimeFault('unknown_result','Recover the original provider operation','resume_original')


class VerifierPublisher:
    def __init__(self,runtime):self.runtime=runtime;self.lock=threading.RLock()

    @staticmethod
    def validate(body,limit):
        problem=next(Draft202012Validator(SCHEMA).iter_errors(body),None)
        if problem:raise RuntimeFault('invalid_input',problem.message,'correct_input')
        try:bounded=len(body['source'].encode('utf-8'))<=limit
        except UnicodeError:bounded=False
        if not bounded:raise RuntimeFault('invalid_input','Verifier source exceeds the UTF-8 size bound','correct_input')

    def publish(self,identity,operation,body,*,limit=256*1024):
        self.validate(body,limit)
        namespace='provider:'+identity.agent_id;key='verifier:'+operation;store=self.runtime.store
        fingerprint=digest({'profile':identity.profile,**body})
        with self.lock:
            record=store.checkpoint(namespace,key)
            if record and record['fingerprint']!=fingerprint:raise RuntimeFault('id_conflict','Original verifier upload changed')
            if not record:
                record={'fingerprint':fingerprint,'body':body};store.checkpoint_set(namespace,key,record)
            if 'result' in record:return record['result']
        try:
            result=self.runtime.account.api('POST','/provider/packages',record['body'])
            if (not isinstance(result,dict) or set(result)!={'id','uri','digest'} or result.get('id')!=body['verifierId']
                    or not isinstance(result.get('digest'),str) or result.get('uri')!='ioalab://pkg/'+result['digest']):
                raise RuntimeFault('protocol_error','Verifier upload returned a different reference','inspect_platform')
            try:load(self.runtime.account.fetch_package(result['uri']),result,body.get('kind','script'))
            except (A2CError,ValueError):raise RuntimeFault('protocol_error','Verifier package does not match its reference or kind','inspect_platform') from None
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (400,404):raise RuntimeFault('protocol_error','Returned verifier package is not readable','inspect_platform') from None
                raise
        except (A2CError,httpx.HTTPError) as exc:raise platform_fault(exc) from None
        with self.lock:
            saved=store.checkpoint(namespace,key)
            if 'result' not in saved:store.checkpoint_set(namespace,key,{**saved,'result':result})
            return store.checkpoint(namespace,key)['result']

    def task(self,identity,job,body):
        self.validate(body,TASK_SOURCE_LIMIT)
        namespace='provider:'+identity.agent_id;key='verifier-job:'+job;store=self.runtime.store;fingerprint=digest(body)
        with self.lock:
            state=store.checkpoint(namespace,key) or {'receipts':{}}
            if fingerprint in state['receipts']:return state['receipts'][fingerprint]['reference']
            pending=state.get('pending')
            if pending and pending['fingerprint']!=fingerprint:
                raise RuntimeFault('unknown_result','Resolve the original task verifier before preparing different content','resume_original')
            if len(state['receipts'])>=32:raise RuntimeFault('invalid_input','A quote may prepare at most 32 verifier packages','correct_input')
            if not pending:
                pending={'fingerprint':fingerprint,'operation':stable('task-verifier',job,fingerprint),'body':body}
                state={**state,'pending':pending};store.checkpoint_set(namespace,key,state)
        try:result=self.publish(identity,pending['operation'],pending['body'],limit=TASK_SOURCE_LIMIT)
        except RuntimeFault as exc:
            if exc.code not in ('unknown_result','result_unknown'):
                with self.lock:
                    current=store.checkpoint(namespace,key)
                    if current.get('pending')==pending:
                        current.pop('pending');store.checkpoint_set(namespace,key,current)
            raise
        with self.lock:
            current=store.checkpoint(namespace,key)
            current['receipts'][fingerprint]={'reference':result,'kind':body.get('kind','script')}
            current.pop('pending',None);store.checkpoint_set(namespace,key,current)
        return result

    def prepared(self,identity,job):
        namespace='provider:'+identity.agent_id;key='verifier-job:'+job
        state=self.runtime.store.checkpoint(namespace,key) or {'receipts':{}}
        if state.get('pending'):
            self.task(identity,job,state['pending']['body'])
            state=self.runtime.store.checkpoint(namespace,key)
        return list(state['receipts'].values())
