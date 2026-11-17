"""Task-scoped production tools on the original host/session and A2C Provider."""
from ..runtime import HostInput,execution_snapshot,stamp,NativeSubmissionPending,submission_succeeded
import base64
from dataclasses import asdict,replace
import hashlib
from pathlib import Path
import time

from a2c.card import card_digest
from a2c.codec import digest
from a2c.common import Reply
from a2c.template import acceptance_of,deliverable_of
from a2c.verifier.acceptance import run_target
from jsonschema import Draft202012Validator
from ..runtime import ToolBinding,validate_output
from ..runtime import JobOutcome
from ..runtime import RuntimeFault,encode
from ..adapters import stable
from ..adapters import report
from .policy import definition_digest
from .material_files import material_files, artifact_directory


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
                if job['status']=='waiting':
                    pending=(job.get('data') or {}).get('nativeSubmission')
                    if pending:
                        if self.execution.host.pending_retry(pending,reference,claim=True):self.runtime.scheduler.resume(reference,{'nativeSubmission':pending})
                    elif record.get('steps'):self.runtime.scheduler.resume(reference,{'stepAnswer':record['steps'][-1]['answer']})
            except RuntimeFault as exc:
                if exc.code!='not_ready':raise
                self.runtime.scheduler.submit(endpoint.identity.agent_id,endpoint.interaction,handler,{'profile':meta['profile'],'cardDigest':cd,'period':accepted+1},operation_id=operation,session=relation['session'],admitted=True)

    def run(self,job):
        identity=self.execution.role.identities[job['payload']['profile']];relation=self.runtime.store.interaction(interaction_id=job['interaction'])
        endpoint=self.execution.context(identity,relation,job,for_execution=True);provider=endpoint.provider;cd=job['payload']['cardDigest'];card=provider.card(cd)
        if provider.store.get('cards',cd).get('state')!='started':return JobOutcome('rejected',{'cardDigest':cd,'reason':'contract_not_started'},relation['session'])
        if self.execution.rulings.ruled(endpoint):return JobOutcome('rejected',{'cardDigest':cd,'reason':'contract_ruled'},relation['session'])
        template=provider.template(card['templateDigest']);definition=provider.policy.definition(template);workspace=self.workspace(identity,relation)
        definition_hash=definition_digest(definition)
        if relation['details'].get('definitionDigest')!=definition_hash:raise RuntimeFault('identity_conflict','Restore the original production agent definition')
        self.execution.tasks.production.require_continuation(identity,job)
        business=self.execution.business_tools(identity.profile,provider.policy.config.get('businessTools',[]),interaction=relation['id'],actor=identity.agent_id)
        state={'outcome':None,'toolError':None,'session':relation['session']}
        period=job['payload']['period']
        def session(value):
            self.runtime.store.bind_session(relation['id'],value,definition_hash);self.runtime.scheduler.bind_session(job['id'],value)
            state['session']=value
        def deliver(args):
            # The sole delivery owner for both the native SDK tool and a typed
            # proposal: one card+period effect key, original bytes, original gates.
            self.runtime.require_execution()
            values=self.files(args,workspace)
            content=digest({name:{'mediaType':media,'contentDigest':'sha256:'+hashlib.sha256(data).hexdigest()} for name,(data,media) in values.items()})
            key='delivery:'+cd+':'+str(period);operation=stable('production-delivery',cd,str(period))
            with endpoint.lock,provider.transport.operation(operation):
                old=endpoint.store.get('production_calls',key)
                if old:
                    if old.get('contentDigest')!=content:raise RuntimeFault('id_conflict','The original period delivery cannot change')
                    if 'result' not in old:raise RuntimeFault('unknown_result','Reconcile the original delivery without repeating it')
                    state['outcome']=state['outcome'] or 'completed'
                    return old['result']
                if state['outcome']:return {'status':'rejected','error':{'code':'forbidden','message':'本轮已交付或等待授权，请结束本轮'}}
                current=provider.store.get('cards',cd)
                if current.get('state')!='started' or self.execution.rulings.ruled(endpoint):return {'status':'rejected','error':{'code':'closed','message':'本合同已停止履约，不再发起新的生产动作'}}
                if any(item['answer'] is None or not item['answer'].get('granted') for item in current.get('steps',[])):
                    return {'status':'rejected','error':{'code':'forbidden','message':'用户尚未同意当前受保护环节'}}
                context={'operationId':operation,'agentId':identity.agent_id,'messageId':job['payload'].get('messageId') or provider.store.get('production',cd)['messageId'],
                         'interactionId':relation['id'],'sessionId':relation['session'],'contractRef':cd,'period':period,'peerAgentId':relation['peer']}
                name='provider.task.deliver'
                endpoint.store.put('production_calls',key,{'fingerprint':digest({'name':name,'args':args}),'name':name,'args':args,'contentDigest':content,'cardDigest':cd,'context':context})
                endpoint.store.put('production_files',key,{file:{'bytesBase64':base64.b64encode(data).decode(),'mediaType':media} for file,(data,media) in values.items()})
                result={'status':'delivered','card':provider.deliver(cd,values)};state['outcome']='completed'
                saved=endpoint.store.get('production_calls',key);endpoint.store.put('production_calls',key,{**saved,'result':result})
                self.sync(endpoint,cd)
                return result
        def invoke(name,args,call):
            self.runtime.require_execution()
            if name=='provider.task.deliver':return deliver(args)
            if state['outcome']:return {'status':'rejected','error':{'code':'forbidden','message':'本轮已交付或等待授权，请结束本轮'}}
            if name not in ({tool.name for tool in tools()}|set(business)):return {'status':'rejected','error':{'code':'forbidden','message':'当前为production阶段，仅可调用本单生产工具'}}
            key=job['id']+':'+call;fingerprint=digest({'name':name,'args':args})
            with endpoint.lock,provider.transport.operation(stable('production-tool',key)):
                if state['outcome']:return {'status':'rejected','error':{'code':'forbidden','message':'本轮已交付或等待授权，请结束本轮'}}
                old=endpoint.store.get('production_calls',key)
                if old:
                    if old['fingerprint']!=fingerprint:raise RuntimeFault('id_conflict','Original production tool input changed')
                    if 'result' in old:return old['result']
                    raise RuntimeFault('unknown_result','Reconcile the original production effect without repeating it')
                current=provider.store.get('cards',cd)
                if current.get('state')!='started' or self.execution.rulings.ruled(endpoint):return {'status':'rejected','error':{'code':'closed','message':'本合同已停止履约，不再发起新的生产动作'}}
                if any(item['answer'] is None or not item['answer'].get('granted') for item in current.get('steps',[])):
                    return {'status':'rejected','error':{'code':'forbidden','message':'用户尚未同意当前受保护环节'}}
                tool=business.get(name)
                context={'operationId':stable('production-tool',key),'agentId':identity.agent_id,'messageId':job['payload'].get('messageId') or provider.store.get('production',cd)['messageId'],
                         'interactionId':relation['id'],'sessionId':relation['session'],'contractRef':cd,'period':job['payload']['period'],'peerAgentId':relation['peer']}
                if tool:
                    if next(Draft202012Validator(tool.input_schema).iter_errors(args),None):return {'status':'rejected','error':{'code':'invalid_input','message':'业务工具输入不符合已配置格式'}}
                    for step in tool.requires_steps:
                        answer=next((item.get('answer') for item in reversed(current.get('steps',[])) if item['request']['step']==step and item['request']['period']==job['payload']['period']),None)
                        if not answer or not answer.get('granted'):return {'status':'rejected','error':{'code':'requires_authorization','message':'业务动作需要本卡本期的真实环节同意：'+step}}
                endpoint.store.put('production_calls',key,{'fingerprint':fingerprint,'name':name,'args':args,'cardDigest':cd,'context':context})
                if tool:pass
                elif name.endswith('request_step'):
                    if next(Draft202012Validator(STEP).iter_errors(args),None):raise RuntimeFault('invalid_input','Use a declared step name')
                    result={'status':'waiting','stepRequest':provider.request_step(cd,args['step'])};state['outcome']='waiting'
                else:values=self.files(args,workspace)
                if not tool and not name.endswith('selftest'):
                    saved=endpoint.store.get('production_calls',key);endpoint.store.put('production_calls',key,{**saved,'result':result})
                    self.sync(endpoint,cd)
                    return result
            if tool:
                try:
                    result=tool.invoke(args,context)
                    try:encode(result).encode('utf-8')
                    except Exception:
                        raise RuntimeFault('invalid_tool_output','Tool output is not serializable JSON; inspect the original effect','inspect_tool_effect') from None
                    with endpoint.lock:
                        saved=endpoint.store.get('production_calls',key)
                        endpoint.store.put('production_calls',key,{**saved,'observedResult':result})
                    validate_output(tool,result)
                except Exception as exc:
                    problem={'code':exc.code,'message':str(exc),'recovery':exc.recovery} if isinstance(exc,RuntimeFault) and exc.code=='invalid_tool_output' else None
                    with endpoint.lock:
                        saved=endpoint.store.get('production_calls',key)
                        endpoint.store.put('production_calls',key,{**saved,'state':'unknown',**({'error':problem} if problem else {})})
                    state['outcome']='unknown'
                    state['toolError']=problem
                    self.execution.host.cancel_submission(identity.agent_id,relation['id'],native_operation)
                    raise
                with endpoint.lock:
                    saved=endpoint.store.get('production_calls',key);endpoint.store.put('production_calls',key,{**saved,'result':result})
                return result
            target=deliverable_of(template,job['payload']['period'])
            result=report(run_target(acceptance_of(template,card)[target]['verifiers'],fetch=self.runtime.account.fetch_package,template=template,card=card,target=target,files={name:data for name,(data,_) in values.items()},period=job['payload']['period'],anchored_at=None,model=provider.model))
            with endpoint.lock:
                saved=endpoint.store.get('production_calls',key);endpoint.store.put('production_calls',key,{**saved,'result':result})
            return result
        prompt=encode({'phase':'production','card':card,'period':job['payload']['period'],'artifactDirectory':str(artifact_directory(workspace)),'materials':material_files(workspace,{'cardDigest':cd,'period':period},provider.materials(cd)),'capabilityReferences':list(definition.skills),'steps':provider.store.get('cards',cd).get('steps',[]),
                       'instruction':'按合同和已配置业务能力生产。需要用户批准的环节先调用provider.task.request_step并结束本轮；交付前可selftest，deliver取得回执后结束本轮。不得把文字当交付或自行批准环节。'})
        native_operation=self.execution.tasks.production.native_operation(identity,job,relation)
        round_record=self.execution.tasks.production.begin(identity,job,relation,definition_hash,native_operation=native_operation)
        self.execution.active.add(job['id'])
        bound=[None]
        def event(value):
            actual=value.result.session_id
            if actual and bound[0]!=actual:session(actual);bound[0]=actual
        try:
            execution=execution_snapshot(identity,relation['id'],job['id'],definition,definition_digest=definition_hash,
                version=relation['revision'],deadline=stamp(job['deadline']) if job.get('deadline') else None,workspace=str(workspace),native_tools_enabled=True)
            execution=replace(execution,proposal_schema=FILES)
            result=self.execution.host.submit(identity.agent_id,relation['id'],native_operation,HostInput(prompt),execution=execution,
                on_event=event,on_tool=lambda call:invoke(call.name,call.arguments,call.transport_reference))
        except NativeSubmissionPending as pending:
            return JobOutcome('waiting',{'cardDigest':cd,'period':job['payload']['period'],'reason':'native_not_submitted','nativeSubmission':pending.reference,'error':pending.details},relation['session'])
        finally:self.execution.active.discard(job['id'])
        proposal_error=None
        # A typed delivery proposal is business data only after a real, matching
        # machine end of this original round; it never retries another path.
        if (result.proposal is not None and submission_succeeded(result) and result.operation_id==native_operation
                and not state['toolError'] and state['outcome']!='waiting'):
            try:
                if result.session_id!=state['session']:raise RuntimeFault('identity_conflict','The proposal session differs from the original binding')
                value=deliver(result.proposal)
                if value.get('status')=='rejected':proposal_error=dict(value['error'])
            except RuntimeFault as exc:proposal_error={'code':exc.code,'message':str(exc),'recovery':exc.recovery}
        self.execution.tasks.production.finish(identity,job,round_record,result)
        if state['toolError']:
            return JobOutcome('unknown',{'cardDigest':cd,'period':job['payload']['period'],'error':state['toolError']},relation['session'])
        delivered=state['outcome']=='completed'
        if result.error is not None and state['outcome']!='waiting':
            # A whole-round error is never success: keep any real delivery receipt
            # and the machine facts, without another round, signature or payment.
            return JobOutcome('unknown',{'cardDigest':cd,'period':period,'delivered':delivered,
                'error':{**asdict(result.error),'source':'submission'}},relation['session'])
        if proposal_error:
            return JobOutcome('unknown',{'cardDigest':cd,'period':period,'delivered':delivered,'error':{**proposal_error,'source':'proposal'}},relation['session'])
        if provider.store.get('cards',cd).get('state')=='terminated' and result.terminal and result.state in ('completed','failed'):
            incomplete=any(item.get('cardDigest')==cd and 'result' not in item for item in endpoint.store.all('production_calls').values())
            if not incomplete:return JobOutcome('rejected',{'cardDigest':cd,'reason':'contract_terminated','hostStatus':result.state},relation['session'])
        if state['outcome']=='waiting':
            # The protected step really exists even if its native turn is still
            # ending. P19 keeps that original model/SID obligation; an early
            # authorized answer queues this same job until actual native drain.
            return JobOutcome('waiting',{'cardDigest':cd,'period':job['payload']['period']},relation['session'])
        if result.state!='completed' or state['outcome'] is None:raise RuntimeFault('unknown_result','Inspect original production; do not rerun unknown business')
        return JobOutcome(state['outcome'],{'cardDigest':cd,'period':job['payload']['period']},relation['session'])
