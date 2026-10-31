"""Task-scoped production tools on the original host/session and A2C Provider."""
import base64
from pathlib import Path
import time

from a2c.card import card_digest
from a2c.codec import digest
from a2c.common import Reply
from a2c.template import acceptance_of,deliverable_of
from a2c.verifier.acceptance import run_target
from jsonschema import Draft202012Validator
from ..runtime.host import HostTurn,ToolBinding
from ..runtime.questions import native_answer
from ..runtime.scheduling import JobOutcome
from ..runtime.protocol import RuntimeFault,encode
from ..adapters.transport import stable
from ..adapters.reports import report
from .policy import definition_digest


FILE={'type':'object','properties':{'mediaType':{'type':'string'},'text':{'type':'string'},'bytesBase64':{'type':'string'},'artifactPath':{'type':'string'}},'required':['mediaType'],'oneOf':[{'required':[key]} for key in ('text','bytesBase64','artifactPath')],'additionalProperties':False}
FILES={'type':'object','properties':{'files':{'type':'object','minProperties':1,'additionalProperties':FILE}},'required':['files'],'additionalProperties':False}
STEP={'type':'object','properties':{'step':{'type':'string'}},'required':['step'],'additionalProperties':False}


def tools():
    return (ToolBinding('provider.task.request_step','请求合同声明的用户授权环节，收到等待结果后结束本轮',STEP,lambda *_:None),
            ToolBinding('provider.task.selftest','在原WASM沙箱自测本单交付文件',FILES,lambda *_:None),
            ToolBinding('provider.task.deliver','提交真实文件，取得签4/5后结束本轮',FILES,lambda *_:None))


class Production:
    def __init__(self,execution):self.execution,self.runtime=execution,execution.runtime

    def workspace(self,identity,relation):return self.runtime.config.home/'roles'/identity.profile/'sessions'/relation['id']

    def files(self,args,workspace):
        if next(Draft202012Validator(FILES).iter_errors(args),None):raise RuntimeFault('invalid_input','Use bounded declared delivery files')
        result={};root=workspace/'artifacts'
        for name,item in args['files'].items():
            if not name or len(name)>200 or any(char in name for char in ('/','\\','\x00')) or name in ('.','..'):raise RuntimeFault('invalid_input','Use a plain delivery filename')
            if 'artifactPath' in item:
                relative=Path(item['artifactPath']);source=root/relative
                if relative.is_absolute() or '..' in relative.parts or not root.resolve().is_relative_to(workspace.resolve()) or not source.resolve().is_relative_to(root.resolve()) or any(part.is_symlink() for part in (source,*source.parents) if part!=workspace.parent):raise RuntimeFault('forbidden','Artifact is outside this task workspace')
                with source.open('rb') as stream:data=stream.read(16*1024*1024+1)
            else:
                try:data=item['text'].encode('utf-8') if 'text' in item else base64.b64decode(item['bytesBase64'],validate=True)
                except (ValueError,AttributeError,TypeError):raise RuntimeFault('invalid_input','Invalid delivery bytes') from None
            if len(data)>16*1024*1024:raise RuntimeFault('invalid_input','A delivery file exceeds 16 MiB')
            result[name]=(data,item['mediaType'])
        return result

    def start(self,policy,card):
        cd=card_digest(card);provider=policy.provider
        provider.store.put('production',cd,{'messageId':policy.job['payload']['messageId'],'profile':policy.identity.profile})

    def sync(self,endpoint,cd):
        provider=endpoint.provider;record=provider.store.get('cards',cd);meta=provider.store.get('production',cd)
        if not meta or not record:return
        task=provider.tasks[record['task']]
        task.artifacts=[item for item in task.artifacts if 'card' not in item]+[{'card':record['card']}]
        endpoint.save_tasks()
        message=next(frame['message'] for frame in self.runtime.store.messages(endpoint.identity.agent_id) if frame['message']['messageId']==meta['messageId'])
        packed=endpoint.pack(Reply({'op':'work.start','receipt':record['card']['fulfillment']['receipt']},task_id=task.id,state=task.state))
        call=provider.store.get('calls',meta['messageId'])
        provider.store.put('calls',meta['messageId'],{**call,'state':'finished','reply':packed})
        endpoint.publish(message,packed)

    def advance(self,endpoint):
        for cd,meta in endpoint.store.all('production').items():
            record=endpoint.store.get('cards',cd)
            if not record or record['state']!='started':continue
            card=record['card'];template=endpoint.provider.template(card['templateDigest']);accepted=len(card['signatures']['7']);delivered=len(card['signatures']['4'])
            paid={voucher.get('period') for voucher in record.get('vouchers',[]) if voucher.get('purpose')=='installment'}
            if accepted and any(i+1 not in paid and period['amount']>0 for i,period in enumerate(card['derived']['periods'][:accepted])):continue
            if accepted==len(template['deliverables']):
                with endpoint.lock,endpoint.provider.transport.operation(stable('close',cd)):
                    endpoint.provider.close(cd);self.sync(endpoint,cd)
                continue
            if delivered>accepted:continue
            if record.get('steps') and record['steps'][-1]['answer'] is None:continue
            if record.get('steps') and record['steps'][-1]['answer'].get('granted') is False:continue
            relation=self.runtime.store.interaction(interaction_id=endpoint.interaction);operation=stable('produce',cd,accepted+1)
            handler='provider.production';reference=self.runtime.scheduler.reference(endpoint.identity.agent_id,handler,operation)
            try:
                job=self.runtime.scheduler.get(reference)
                if job['status']=='waiting':self.runtime.scheduler.resume(reference,{'stepAnswer':record['steps'][-1]['answer']})
            except RuntimeFault as exc:
                if exc.code!='not_ready':raise
                self.runtime.scheduler.submit(endpoint.identity.agent_id,endpoint.interaction,handler,{'profile':meta['profile'],'cardDigest':cd,'period':accepted+1},operation_id=operation,session=relation['session'],admitted=True)

    def run(self,job):
        identity=self.execution.role.identities[job['payload']['profile']];relation=self.runtime.store.interaction(interaction_id=job['interaction'])
        endpoint=self.execution.context(identity,relation,job);provider=endpoint.provider;cd=job['payload']['cardDigest'];card=provider.card(cd)
        template=provider.template(card['templateDigest']);definition=provider.policy.definition(template);workspace=self.workspace(identity,relation)
        definition_hash=definition_digest(definition)
        if relation['details'].get('definitionDigest')!=definition_hash:raise RuntimeFault('identity_conflict','Restore the original production agent definition')
        state={'outcome':None}
        def session(value):
            self.runtime.store.bind_session(relation['id'],value,definition_hash);self.runtime.scheduler.bind_session(job['id'],value)
        def invoke(name,args,call):
            self.runtime.require_execution()
            if state['outcome']:return {'status':'rejected','error':{'code':'forbidden','message':'本轮已交付或等待授权，请结束本轮'}}
            if name not in {tool.name for tool in tools()}:return {'status':'rejected','error':{'code':'forbidden','message':'当前为production阶段，仅可调用本单生产工具'}}
            key=job['id']+':'+call;fingerprint=digest({'name':name,'args':args})
            with endpoint.lock,provider.transport.operation(stable('production-tool',key)):
                if state['outcome']:return {'status':'rejected','error':{'code':'forbidden','message':'本轮已交付或等待授权，请结束本轮'}}
                old=endpoint.store.get('production_calls',key)
                if old:
                    if old['fingerprint']!=fingerprint:raise RuntimeFault('id_conflict','Original production tool input changed')
                    if old.get('result') is not None:return old['result']
                    raise RuntimeFault('unknown_result','Reconcile the original production effect without repeating it')
                current=provider.store.get('cards',cd)
                if any(item['answer'] is None or not item['answer'].get('granted') for item in current.get('steps',[])):
                    return {'status':'rejected','error':{'code':'forbidden','message':'用户尚未同意当前受保护环节'}}
                endpoint.store.put('production_calls',key,{'fingerprint':fingerprint,'name':name,'args':args,'cardDigest':cd})
                if name.endswith('request_step'):
                    if next(Draft202012Validator(STEP).iter_errors(args),None):raise RuntimeFault('invalid_input','Use a declared step name')
                    result={'status':'waiting','stepRequest':provider.request_step(cd,args['step'])};state['outcome']='waiting'
                else:
                    values=self.files(args,workspace)
                    if not name.endswith('selftest'):
                        endpoint.store.put('production_files',key,{name:{'bytesBase64':base64.b64encode(data).decode(),'mediaType':media} for name,(data,media) in values.items()})
                        delivered=provider.deliver(cd,values);result={'status':'delivered','card':delivered};state['outcome']='completed'
                if not name.endswith('selftest'):
                    saved=endpoint.store.get('production_calls',key);endpoint.store.put('production_calls',key,{**saved,'result':result})
                    self.sync(endpoint,cd)
                    return result
            target=deliverable_of(template,job['payload']['period'])
            result=report(run_target(acceptance_of(template,card)[target]['verifiers'],fetch=self.runtime.account.fetch_package,template=template,card=card,target=target,files={name:data for name,(data,_) in values.items()},period=job['payload']['period'],anchored_at=None,model=provider.model))
            with endpoint.lock:
                saved=endpoint.store.get('production_calls',key);endpoint.store.put('production_calls',key,{**saved,'result':result})
            return result
        prompt=encode({'phase':'production','card':card,'period':job['payload']['period'],'artifactDirectory':str(workspace/'artifacts'),'materials':{name:base64.b64encode(data).decode() for name,data in provider.materials(cd).items()},'steps':provider.store.get('cards',cd).get('steps',[]),
                       'instruction':'按合同和已配置业务能力生产。需要用户批准的环节先调用provider.task.request_step并结束本轮；交付前可selftest，deliver取得回执后结束本轮。不得把文字当交付或自行批准环节。'})
        self.execution.active.add(job['id'])
        try:result=self.execution.host.run(HostTurn(job['id'],identity.agent_id,relation['id'],definition,prompt,str(workspace),300,relation['session'],session,lambda value:native_answer(self.runtime,identity.agent_id,job['id'],value,self.execution.stop),invoke,allow_native_tools=True))
        finally:self.execution.active.discard(job['id'])
        if result.status!='completed' or state['outcome'] is None:raise RuntimeFault('unknown_result','Inspect original production; do not rerun unknown business')
        return JobOutcome(state['outcome'],{'cardDigest':cd,'period':job['payload']['period']},relation['session'])
