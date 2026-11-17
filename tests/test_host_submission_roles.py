"""Real HTTP/WS role effects through Registry/P20/S01, with a native boundary double."""
from dataclasses import replace
from types import SimpleNamespace
import json,uuid
import pytest

from ioalab_sdk.runtime import (HostRegistry,HostRegistration,SubmissionResult,SubmissionEvent,BindingAck,
    MachineTerminal,HostToolCall,HostToolResult,RuntimeConfig,ResidentRuntime,LocalClient,ToolBinding,SubmissionError)
from ioalab_sdk.assistant import AssistantRole
from conftest import wait_for
from test_assistant_role import runtime_at,publish,send_and_read,INPUT,OUTPUT
from test_resident_runtime import configuration


class Native:
    registration=HostRegistration('s01-role-fixture','1')
    def __init__(self):self.calls=[]
    def submit(self,op,input,session_id=None,*,execution=None,on_event=None,on_tool=None):
        self.calls.append((op,session_id,execution))
        sid=session_id or 'native-session-'+str(uuid.uuid4());request='native-request-'+str(uuid.uuid4())
        result=SubmissionResult(op,input.attempt.nonce,'running',sid,request,'accepted')
        assert on_event(SubmissionEvent(request+':session','session',result))==BindingAck(sid,request)
        body=json.loads(input.text.split('\n',1)[1])['input']
        def invoke(name,args,call):
            value=on_tool(HostToolCall(op,input.attempt.nonce,sid,request,call,name,args))
            assert isinstance(value,HostToolResult)
            return value
        effect=invoke('reserve_slot',body,'native-effect')
        if not effect.error:invoke('ioa_reply',{'output':effect.value},'native-reply')
        terminal=MachineTerminal(op,input.attempt.nonce,sid,request,'completed','native-result-'+request)
        return replace(result,state='completed',terminal=terminal,usage={'fixture':True})


def test_real_role_rpc_uses_original_binding_and_frozen_tool_after_default_changes(platform,tmp_path):
    first,second=Native(),Native();effects=[]
    def reserve(args,context):effects.append(context);return {'status':'reserved'}
    tool=ToolBinding('reserve_slot','Original authorized effect',INPUT,reserve,'reservation',output_schema=OUTPUT)
    resource=SimpleNamespace(for_profile=lambda profile:{tool.name:tool})
    with runtime_at(platform,tmp_path/'sender') as (_,sender,_,user):
        directory=tmp_path/'receiver';directory.mkdir();path,target=configuration(platform,directory,service=False)
        registry=HostRegistry({'first':first,'second':second},'first',tools={'first':resource})
        runtime=ResidentRuntime(RuntimeConfig.load(path),modules=(AssistantRole(registry,{tool.name:tool}),),host_registry=registry).start()
        try:
            wait_for(lambda:runtime.connection.connected)
            publish(runtime,LocalClient(runtime.config.home),user['username'])
            interface=sender.call('assistant.messaging.interfaces',{'account':target['username']})['data'][0]['interfaceRef']
            _,initial=send_and_read(sender,{'interfaceRef':interface,'input':{'slot':'first'}})
            assert initial['state']=='completed' and len(effects)==1
        finally:runtime.close()
        registry=HostRegistry({'first':first,'second':second},'second',tools={'first':resource})
        runtime=ResidentRuntime(RuntimeConfig.load(path),modules=(AssistantRole(registry,{}),),host_registry=registry).start()
        try:
            wait_for(lambda:runtime.connection.connected)
            _,continued=send_and_read(sender,{'interactionRef':initial['interactionRef'],'input':{'slot':'second'}})
            assert continued['state']=='completed' and continued['result']['output']=={'status':'reserved'}
            assert len(effects)==2 and len(first.calls)==2 and not second.calls
            assert effects[0]['sessionId']==effects[1]['sessionId']
            assert first.calls[0][1] is None and first.calls[1][1]==effects[0]['sessionId']
        finally:runtime.close()


@pytest.mark.parametrize('crash_gap',[False,True])
def test_not_submitted_private_request_waits_and_restarts_on_original_host_without_failure_or_replay(platform,tmp_path,crash_gap):
    class Preparing(Native):
        ready=False
        def __init__(self):super().__init__();self.preparations=[]
        def submit(self,op,input,session_id=None,**kwargs):
            if not self.ready:
                self.preparations.append((op,input.attempt.nonce))
                return SubmissionResult(op,input.attempt.nonce,'waiting',session_id,error=SubmissionError('not_ready','native_bridge','not_submitted','No native dispatch'))
            return super().submit(op,input,session_id,**kwargs)
    first,second=Preparing(),Native();effects=[]
    tool=ToolBinding('reserve_slot','Original effect',INPUT,lambda args,context:(effects.append(context) or {'status':'reserved'}),'reservation',output_schema=OUTPUT)
    resource=SimpleNamespace(for_profile=lambda profile:{tool.name:tool})
    with runtime_at(platform,tmp_path/'sender') as (_,sender,_,user):
        directory=tmp_path/'receiver';directory.mkdir();path,target=configuration(platform,directory,service=False)
        registry=HostRegistry({'first':first,'second':second},'first',tools={'first':resource})
        runtime=ResidentRuntime(RuntimeConfig.load(path),modules=(AssistantRole(registry,{tool.name:tool}),),host_registry=registry).start()
        try:
            wait_for(lambda:runtime.connection.connected)
            publish(runtime,LocalClient(runtime.config.home),user['username'])
            if crash_gap:
                with runtime.store.transaction() as connection:
                    connection.execute("CREATE TEMP TRIGGER crash_before_native_wait BEFORE UPDATE OF state ON runtime_jobs WHEN NEW.state='waiting' AND json_extract(NEW.result,'$.reason')='native_not_submitted' BEGIN SELECT RAISE(ABORT,'fixture crash after no-dispatch receipt'); END")
            interface=sender.call('assistant.messaging.interfaces',{'account':target['username']})['data'][0]['interfaceRef']
            sent=sender.call('assistant.messaging.send',{'interfaceRef':interface,'input':{'slot':'first'}})
            assert sent['status']=='accepted'
            wait_for(lambda:runtime.scheduler.status()['states'].get('unknown' if crash_gap else 'waiting')==1)
            pending=sender.call('assistant.messaging.status',{'messageRef':sent['data']['messageRef']})['data']
            assert pending['result'] is None and not effects and not first.calls
            original_operations={op for op,_ in first.preparations};assert len(original_operations)==1
        finally:runtime.close()
        first.ready=True
        registry=HostRegistry({'first':first,'second':second},'second',tools={'first':resource})
        runtime=ResidentRuntime(RuntimeConfig.load(path),modules=(AssistantRole(registry,{}),),host_registry=registry).start()
        try:
            wait_for(lambda:runtime.connection.connected)
            result=sender.call('assistant.messaging.status',{'messageRef':sent['data']['messageRef'],'waitSeconds':15})['data']
            assert result['state']=='completed' and result['result']['output']=={'status':'reserved'}
            assert len(first.calls)==len(effects)==1 and not second.calls
            assert first.calls[0][0] in original_operations
        finally:runtime.close()


@pytest.mark.parametrize('formation',['proposal','countersign'])
@pytest.mark.parametrize('crash_gap',[False,True])
def test_a2c_not_submitted_preserves_working_task_card_fee_and_original_host_across_restart(platform,tmp_path,formation,crash_gap):
    import copy
    from ioalab_sdk.provider import ProviderRole
    from test_resident_delegation import participants,request,terminal
    from test_resident_contracts import confirm
    class Service(Native):
        ready=False
        def __init__(self):super().__init__();self.preparations=[]
        def submit(self,op,input,session_id=None,*,execution=None,on_event=None,on_tool=None):
            if not self.ready:
                self.preparations.append((op,input.attempt.nonce))
                return SubmissionResult(op,input.attempt.nonce,'waiting',session_id,error=SubmissionError('not_ready','execution_bridge','not_submitted','No model or native dispatch'))
            self.calls.append((op,session_id,execution))
            data=json.loads(input.text);sid=session_id or 'original-service-session';req='request-'+op
            receipt=SubmissionResult(op,input.attempt.nonce,'running',sid,req,'accepted')
            assert on_event(SubmissionEvent(req,'session',receipt))==BindingAck(sid,req)
            if data['phase']=='quote':
                name='provider.task.quote';args={'acceptance':{'result':{'verifiers':{'script':self.verifier},'notCovered':[]}},'acceptWithin':{'result':'PT10M'},'lineItems':{'result':9}}
            else:name='provider.task.decision';args={'approved':True}
            result=on_tool(HostToolCall(op,input.attempt.nonce,sid,req,'original-decision',name,args))
            assert result.error is None and result.value['status']=='recorded'
            return replace(receipt,state='completed',terminal=MachineTerminal(op,input.attempt.nonce,sid,req,'completed','original-native-end'))
    first,other=Service(),Service();default=['first'];other.ready=True
    def factory(config,host,tools):
        registry=HostRegistry({'first':host,'other':other},default[0])
        return ResidentRuntime(config,modules=(ProviderRole(registry,tools),),host_registry=registry)
    fresh=None
    with participants(platform,tmp_path,formation=formation,fee=1 if formation=='proposal' else 0,agent_host=first,provider_factory=factory) as (provider,assistant,host,template,task):
        if crash_gap:
            with provider.store.transaction() as connection:
                connection.execute("CREATE TEMP TRIGGER crash_before_native_wait BEFORE UPDATE OF state ON runtime_jobs WHEN NEW.state='waiting' AND json_extract(NEW.result,'$.reason')='native_not_submitted' BEGIN SELECT RAISE(ABORT,'fixture crash after no-dispatch task'); END")
        started,_=request(assistant,provider,template,task);quote=started['data']['quoteRef']
        contract=None
        if formation=='countersign':
            assert started['status']=='completed' and started['data']['formation']=='countersign'
            selected,_,_=confirm(assistant,started['data'])
            contract=selected['data']['contractRef']
        wait_for(lambda:provider.scheduler.status()['states'].get('unknown' if crash_gap else 'waiting')==1)
        endpoint=next(iter(provider.modules[0].execution.contexts.values()))
        original_tasks=set(endpoint.provider.tasks);assert len(original_tasks)==1
        original_task=next(iter(original_tasks));assert endpoint.provider.tasks[original_task].state=='working'
        namespace='pending_requests' if formation=='proposal' else 'pending_countersigns'
        original=copy.deepcopy(endpoint.store.get(namespace,original_task));assert original['reviewed']
        assert not original['draftReturned' if formation=='proposal' else 'decisionReturned']
        initial_fee=len(platform.db.rows("SELECT * FROM transfers WHERE purpose='proposal-fee' AND payee_id=?",provider.account.account['id']))
        assert not first.calls and first.preparations
        config=provider.config;provider.close();default[0]='other';first.ready=True
        fresh=factory(config,first,None).start()
        try:
            wait_for(lambda:fresh.connection.connected)
            if formation=='proposal':
                wait_for(lambda:terminal(assistant,quote) and terminal(assistant,quote)['status']=='completed',timeout=20)
                finished=terminal(assistant,quote)['data']
            else:
                def formed():
                    value=LocalClient(assistant.config.home).call('assistant.delegation.status',{'contractRef':contract})
                    return value['data'] if (value.get('data') or {}).get('formed') else None
                wait_for(formed,timeout=20);finished=formed()
            recovered=next(iter(fresh.modules[0].execution.contexts.values()))
            assert set(recovered.provider.tasks)==original_tasks and recovered.provider.tasks[original_task].state=='completed'
            assert len(first.calls)==1 and not other.calls
            assert first.calls[0][0] in {op for op,_ in first.preparations}
            assert len(platform.db.rows("SELECT * FROM transfers WHERE purpose='proposal-fee' AND payee_id=?",fresh.account.account['id']))==initial_fee
            restored=recovered.store.get(namespace,original_task)
            assert restored['body']==original['body'] and restored['files']==original['files']
            if formation=='countersign':assert finished['card']['signatures']['1']==original['body']['card']['signatures']['1']
        finally:fresh.close()


@pytest.mark.parametrize('disconnect',[False,True])
def test_accepted_native_request_is_not_retried_and_late_sdk_reply_then_machine_end_converges(platform,tmp_path,disconnect):
    from ioalab_sdk.runtime import RuntimeFault
    class Deferred(Native):
        def submit(self,op,input,session_id=None,*,execution=None,on_event=None,on_tool=None):
            if self.calls:return super().submit(op,input,session_id,execution=execution,on_event=on_event,on_tool=on_tool)
            self.calls.append((op,session_id,execution))
            result=SubmissionResult(op,input.attempt.nonce,'accepted','original-native-session','actual-request','accepted')
            assert on_event(SubmissionEvent('actual-bound','session',result))==BindingAck(result.session_id,result.request_id)
            self.original,self.event,self.tool=result,on_event,on_tool
            if disconnect:raise RuntimeFault('not_ready','Transport disconnected after native acceptance')
            return result
    host=Deferred();effects=[]
    tool=ToolBinding('reserve_slot','Original effect',INPUT,lambda args,context:(effects.append(context) or {'status':'reserved'}),'reservation',output_schema=OUTPUT)
    resource=SimpleNamespace(for_profile=lambda profile:{tool.name:tool})
    with runtime_at(platform,tmp_path/'sender') as (_,sender,_,user):
        directory=tmp_path/'receiver';directory.mkdir();path,target=configuration(platform,directory,service=False)
        registry=HostRegistry({'native':host},'native',tools={'native':resource})
        runtime=ResidentRuntime(RuntimeConfig.load(path),modules=(AssistantRole(registry,{tool.name:tool}),),host_registry=registry).start()
        try:
            wait_for(lambda:runtime.connection.connected)
            publish(runtime,LocalClient(runtime.config.home),user['username'])
            interface=sender.call('assistant.messaging.interfaces',{'account':target['username']})['data'][0]['interfaceRef']
            sent=sender.call('assistant.messaging.send',{'interfaceRef':interface,'input':{'slot':'first'}})
            wait_for(lambda:runtime.scheduler.status()['states'].get('unknown')==1)
            pending=sender.call('assistant.messaging.status',{'messageRef':sent['data']['messageRef']})['data']
            assert pending['result'] is None and len(host.calls)==1 and not effects
            original=host.original
            def sdk(name,args,call):return host.tool(HostToolCall(original.operation_id,original.attempt_id,original.session_id,original.request_id,call,name,args))
            effect=sdk('reserve_slot',{'slot':'first'},'actual-effect')
            assert effect.error is None and len(effects)==1
            assert sdk('reserve_slot',{'slot':'first'},'actual-effect')==effect and len(effects)==1
            reply=sdk('ioa_reply',{'output':effect.value},'actual-reply')
            assert reply.error is None
            first=sender.call('assistant.messaging.status',{'messageRef':sent['data']['messageRef'],'waitSeconds':15})['data']
            assert first['state']=='completed' and first['result']['output']=={'status':'reserved'}
            wait_for(lambda:runtime.scheduler.status()['states'].get('completed')==1)
            next_send=sender.call('assistant.messaging.send',{'interactionRef':first['interactionRef'],'input':{'slot':'second'}})
            wait_for(lambda:runtime.scheduler.status()['states'].get('queued')==1)
            assert len(host.calls)==1  # Business reply alone did not free the native SID.
            terminal=MachineTerminal(original.operation_id,original.attempt_id,original.session_id,original.request_id,'completed','actual-native-turn-end')
            host.event(SubmissionEvent('actual-native-ended','terminal',replace(original,state='completed',terminal=terminal)))
            second=sender.call('assistant.messaging.status',{'messageRef':next_send['data']['messageRef'],'waitSeconds':15})['data']
            assert second['state']=='completed' and len(host.calls)==len(effects)==2
            assert effects[0]['sessionId']==effects[1]['sessionId']
        finally:runtime.close()
