"""Real platform/IPC role events, with explicit native transport and model boundaries."""
from contextlib import contextmanager
import json
import threading
import time
import uuid
from datetime import datetime
import httpx
from dataclasses import replace

from conftest import wait_for
from ioalab_sdk.assistant import AssistantRole
from ioalab_sdk.runtime import (HostRegistry,HostRegistration,SubmissionResult,SubmissionError,SubmissionEvent,MachineTerminal,BindingAck,LocalClient,ResidentRuntime,RuntimeConfig,ToolBinding,native_source_descriptor,read_json)
from test_assistant_role import runtime_at,publish,ScriptedHost,INPUT,OUTPUT
from test_resident_runtime import configuration


class NativeTransport:
    registration=HostRegistration('native-event-boundary','1')
    def __init__(self,workspace,driver=None):
        self.workspace=str(workspace);self.notices=[];self.lock=threading.Lock();self.ready=True;self.driver=driver
    def check(self):return {'state':'unknown','reason':'Explicit native boundary'}
    def submit(self,operation,input,session_id=None,*,execution=None,on_event=None,on_tool=None):
        if execution is not None:
            assert self.driver is not None,'A user notice must not create a separate business model turn'
            return self.driver.submit(operation,input,session_id,execution=execution,on_event=on_event,on_tool=on_tool)
        assert session_id=='original-user-session'
        if not self.ready:
            return SubmissionResult(operation,input.attempt.nonce,'unknown',session_id,
                error=SubmissionError('not_ready','native_boundary','not_submitted','No dispatch occurred'))
        request='request-'+operation
        accepted=SubmissionResult(operation,input.attempt.nonce,'accepted',session_id,request,'accepted')
        assert on_event(SubmissionEvent('native-session','session',accepted))==BindingAck(session_id,request)
        with self.lock:self.notices.append((operation,session_id,json.loads(input.text)))
        proof=MachineTerminal(operation,input.attempt.nonce,session_id,request,'completed','fixture-native-terminal-'+operation)
        return replace(accepted,state='completed',delivery='handled',terminal=proof)


@contextmanager
def native_at(platform,path):
    path.mkdir();config,user=configuration(platform,path,service=False)
    transport=NativeTransport(path)
    registry=HostRegistry({'native':transport},'native')
    role=AssistantRole(registry,{})
    runtime=ResidentRuntime(RuntimeConfig.load(config),modules=(role,),host_registry=registry,native_entry_required=True).start()
    try:
        wait_for(lambda:runtime.connection.connected)
        yield runtime,LocalClient(runtime.config.home),role,user,transport
    finally:runtime.close()


def native_context(runtime):
    identity=runtime.identities[0]
    descriptor=read_json(native_source_descriptor(runtime.config.home,'native',identity.profile))
    response=httpx.post(descriptor['url'],json={'sessionId':'original-user-session','workspace':str(runtime.config.source.parent),
        'captureId':'actual-native-fixture-entry'},headers={'Authorization':'Bearer '+descriptor['grant'],
        'X-Ioa-Instance':descriptor['bootId']},trust_env=False)
    response.raise_for_status()
    return response.json()['data']


def test_private_reply_returns_to_original_native_session_once(platform,tmp_path):
    with native_at(platform,tmp_path/'sender') as (sender,client,_,user,transport):
        host=ScriptedHost()
        tool=ToolBinding('reserve_slot','Fixture reservation',INPUT,lambda args,context:{'status':'reserved'},output_schema=OUTPUT)
        with runtime_at(platform,tmp_path/'receiver',host,[tool]) as (receiver,inbox,_,target):
            publish(receiver,inbox,user['username'])
            interface=client.call('assistant.messaging.interfaces',{'account':target['username']})['data'][0]['interfaceRef']
            transport.ready=False
            sent=client.call('assistant.messaging.send',{'interfaceRef':interface,'input':{'slot':'first'}},context_ref=native_context(sender))
            assert sent['status']=='accepted',sent
            wait_for(lambda:sender.status()['nativeEvents']['diagnosticSamples'])
            state=client.call('runtime.status',{})['data']['nativeEvents']
            assert state['pending']==1 and state['diagnosticSamples'][0]['code']=='not_ready'
            assert set(state['diagnosticSamples'][0])=={'eventRef','code'} and not transport.notices
            event=state['diagnosticSamples'][0]['eventRef']
            saved=sender.store.checkpoint('host-deliveries',json.dumps([sender.identities[0].agent_id,event],separators=(',',':')))
            assert saved['released'] and saved['receipt']['request_id'] is None
            assert saved['receipt']['error']['dispatch']=='not_submitted'
            transport.ready=True
            wait_for(lambda:len(transport.notices)==1)
            notice=transport.notices[0][2]
            assert notice['eventKind']=='message.result' and notice['facts']['outcome']=='completed'
            assert notice['businessRef']==sent['data']['messageRef']
            # Reads, heartbeat delivery, and an idempotent status replay do not wake another model.
            for _ in range(3):client.call('assistant.messaging.status',{'messageRef':sent['data']['messageRef']})
            assert len(transport.notices)==1
            status=client.call('assistant.messaging.status',{'messageRef':sent['data']['messageRef']})['data']
            assert client.call('assistant.messaging.close',{'interactionRef':status['interactionRef']})['status']=='accepted'
            wait_for(lambda:len(transport.notices)==2)
            closed=transport.notices[1][2]
            assert closed['eventKind']=='interaction.closed' and closed['businessOperationRef']==sent['data']['operationRef']
            assert closed['businessRef']==sent['data']['messageRef']
            wait_for(lambda:sender.status()['nativeEvents']['pending']==0)
            assert not sender.status()['nativeEvents']['diagnosticSamples']


def test_outbox_rejection_before_interaction_returns_to_original_source(platform,tmp_path):
    from test_assistant_role import user_decision
    with native_at(platform,tmp_path/'sender') as (sender,client,_,user,transport):
        tool=ToolBinding('reserve_slot','Fixture reservation',INPUT,lambda args,context:{'status':'reserved'},output_schema=OUTPUT)
        with runtime_at(platform,tmp_path/'receiver',ScriptedHost(),[tool]) as (receiver,inbox,_,target):
            published=publish(receiver,inbox,user['username'])
            interface=client.call('assistant.messaging.interfaces',{'account':target['username']})['data'][0]['interfaceRef']
            operation=str(uuid.uuid4());args={'name':'slot.request','expectedRevision':published['data']['revision']}
            disabled=inbox.call('assistant.rules.disable',args,operation_id=operation)
            assert disabled['status']=='waiting',disabled
            user_decision(receiver,disabled['data']['confirmationRef'])
            assert inbox.call('assistant.rules.disable',args,operation_id=operation)['status']=='completed'
            sent=client.call('assistant.messaging.send',{'interfaceRef':interface,'input':{'slot':'first'}},context_ref=native_context(sender))
            assert sent['status']=='accepted',sent
            wait_for(lambda:transport.notices)
            assert len(transport.notices)==1
            notice=transport.notices[0][2]
            assert notice['eventKind']=='message.result' and notice['facts']['state']=='rejected'
            assert notice['businessRef']==sent['data']['messageRef']
            assert sender.store.interaction(origin=sent['data']['messageRef']) is None
            for _ in range(3):client.call('assistant.messaging.status',{'messageRef':sent['data']['messageRef']})
            assert len(transport.notices)==1

def test_async_flow_rejection_is_not_lost_after_real_confirmation(platform,tmp_path,monkeypatch):
    import httpx
    from test_assistant_role import user_decision
    with native_at(platform,tmp_path/'native') as (runtime,rpc,_,_,transport):
        monkeypatch.setattr(runtime.user_ui,'open',lambda:True)
        pending=rpc.call('assistant.delegation.flow',{'action':'begin','input':{'purpose':'async rejection boundary','budget':20,'proposalFeeLimit':2}},context_ref=native_context(runtime))
        assert pending['status']=='waiting',pending
        ref=pending['data']['flowRef'];confirmation=pending['data']['evidence']['confirmationRef']
        original=runtime.account.api
        def deny(method,path,*args,**kwargs):
            # External HTTP boundary fixture: exercise a real native flow/RPC and user
            # confirmation; this is not evidence of the platform authorization policy.
            if method=='POST' and path=='/scopes':
                response=httpx.Response(403,json={'error':{'code':'forbidden','message':'fixture remote denial'}},request=httpx.Request('POST',runtime.config.platform+path))
                response.raise_for_status()
            return original(method,path,*args,**kwargs)
        monkeypatch.setattr(runtime.account,'api',deny)
        user_decision(runtime,confirmation)
        wait_for(lambda:transport.notices)
        assert len(transport.notices)==1
        notice=transport.notices[0][2]
        assert notice['facts']['status']=='rejected' and notice['facts']['reason']=='forbidden'
        for _ in range(3):
            result=rpc.call('assistant.delegation.flow',{'action':'observe','flowRef':ref})
            assert result['status']=='rejected',result
        assert len(transport.notices)==1


def test_native_flow_leaf_dedup_and_post_formation_step_result(platform,tmp_path,monkeypatch):
    from test_resident_delegation import participants
    from test_resident_fulfillment import ProductionHost,VERIFIER,configure_template
    from test_assistant_role import user_decision
    configure_template(monkeypatch)
    with participants(platform,tmp_path/'provider-fixture',agent_host=ProductionHost(),verifier_source=VERIFIER) as (provider,_,_,_,_):
        with native_at(platform,tmp_path/'native') as (runtime,rpc,_,_,transport):
            monkeypatch.setattr(runtime.user_ui,'open',lambda:True)
            def flow(action,*,ref=None,values=None,operation=None):
                args={'action':action}
                if ref:args['flowRef']=ref
                if values is not None:args['input']=values
                return rpc.call('assistant.delegation.flow',args,operation_id=operation,context_ref=native_context(runtime))
            begun=flow('begin',values={'purpose':'native protocol event','budget':30,'proposalFeeLimit':2})
            assert begun['status']=='waiting',begun
            ref=begun['data']['flowRef']
            user_decision(runtime,begun['data']['evidence']['confirmationRef'])
            wait_for(lambda:any(n[2]['eventKind']=='business.result' for n in transport.notices))
            observed=flow('observe',ref=ref);assert observed['status']=='completed',observed
            inspected=flow('inspect',ref=ref,values={'service':provider.identities[0].agent_id})
            assert inspected['status']=='completed',inspected
            request_op=str(uuid.uuid4())
            requested=flow('request',ref=ref,values={'service':provider.identities[0].agent_id,'spec':{'result':{}}},operation=request_op)
            assert requested['status']=='waiting',requested
            wait_for(lambda:any(n[2]['businessOperationRef']==request_op for n in transport.notices))
            compared=flow('observe',ref=ref);assert compared['status']=='completed',compared
            assert len([n for n in transport.notices if n[2]['businessOperationRef']==request_op])==1
            assert not [n for n in transport.notices if n[2]['eventKind']=='business.candidate']
            select_op=str(uuid.uuid4());quote=compared['data']['quoteRefs'][0]
            selected=flow('select',ref=ref,values={'quoteRef':quote},operation=select_op)
            assert selected['data']['reason']=='confirmation',selected
            user_decision(runtime,selected['data']['evidence']['confirmationRef'])
            wait_for(lambda:any(n[2]['businessOperationRef']==select_op for n in transport.notices))
            selected=flow('observe',ref=ref);contract=selected['data']['contractRef']
            wait_for(lambda:rpc.call('assistant.delegation.status',{'contractRef':contract})['data']['stepRequestDigest'])
            step_op=str(uuid.uuid4());question=flow('answer_step',ref=ref,operation=step_op)
            assert question['data']['reason']=='confirmation',question
            user_decision(runtime,question['data']['evidence']['confirmationRef'],decision='answer',answers=[{'selected_options':['allow']}])
            wait_for(lambda:any(n[2]['businessOperationRef']==step_op for n in transport.notices))
            step_notices=[n[2] for n in transport.notices if n[2]['businessOperationRef']==step_op]
            assert len(step_notices)==1 and step_notices[0]['facts']['status']=='completed'
            for _ in range(3):assert flow('observe',ref=ref)['status']=='completed'
            assert len([n for n in transport.notices if n[2]['businessOperationRef']==step_op])==1
            wait_for(lambda:any(n[2]['eventKind']=='business.delivery_ready' for n in transport.notices))
            facts=rpc.call('assistant.delegation.status',{'contractRef':contract})['data']
            assert len(facts['card']['signatures']['6'])==1 and facts['card']['signatures']['7']==[]
            assert not [v for v in facts['vouchers'] if v.get('purpose')=='installment']

import pytest

@pytest.mark.parametrize('decision',['reject','expired'])
def test_native_provider_sync_result_and_async_decision_terminal(platform,tmp_path,monkeypatch,decision):
    from ioalab_sdk.provider import ProviderRole
    from test_resident_delegation import participants
    from test_service_publication import draft
    from test_assistant_role import user_decision
    def factory(config,host,tools):
        transport=NativeTransport(config.source.parent,driver=host)
        registry=HostRegistry({'native':transport},'native')
        return ResidentRuntime(config,modules=(ProviderRole(registry,tools),),host_registry=registry,
            host_status=registry.status_snapshot,native_entry_required=True)
    with participants(platform,tmp_path/'fixture',provider_factory=factory) as (provider,_,_,_,_):
        transport=provider.host_registry.hosts['native'];rpc=LocalClient(provider.config.home)
        monkeypatch.setattr(provider.user_ui,'open',lambda:True)
        paused=rpc.call('provider.flow',{'profile':'service','action':'pause'},context_ref=native_context(provider))
        assert paused['status']=='completed',paused
        ref=paused['data']['flowRef']
        resumed=rpc.call('provider.flow',{'profile':'service','flowRef':ref,'action':'resume_admission'},context_ref=native_context(provider))
        assert resumed['status']=='completed',resumed
        for _ in range(2):assert rpc.call('provider.flow',{'profile':'service','flowRef':ref,'action':'observe'})['status']=='completed'
        assert not transport.notices
        category=platform.discovery.categories()[0]['id']
        original=provider.account.api;entered=threading.Event();release=threading.Event()
        def controlled_category(method,path,payload=None,**kwargs):
            # Explicit HTTP boundary: keep the real service/category response but select
            # the fixture category, preventing any external model/classifier invocation.
            if method=='POST' and path=='/services/category-preview' and 'categoryId' not in payload:
                entered.set();assert release.wait(5)
                payload={**payload,'categoryId':category}
            return original(method,path,payload,**kwargs) if payload is not None else original(method,path,**kwargs)
        monkeypatch.setattr(provider.account,'api',controlled_category)
        raw=draft(platform)
        terms={k:v for k,v in raw.items() if k in ('formation','deliverables','inputs','deadlines','pricing','acceptance','authorizableSteps','samples','extensions')}
        terms['pricing']={k:v for k,v in terms['pricing'].items() if k!='paymentProviders'}
        service={'profile':'service','name':'native flow publication','description':'fixture publication','tags':['开发'],'terms':[terms]}
        try:
            waiting=rpc.call('provider.flow',{'profile':'service','action':'publish','input':{'service':service}},context_ref=native_context(provider))
            assert waiting['status']=='waiting' and waiting['data']['reason']=='external_processing',waiting
            assert entered.wait(3) and not transport.notices
            release.set()
            wait_for(lambda:len(transport.notices)==1)
            request=transport.notices[0][2]
            assert request['eventKind']=='business.decision' and request['facts']['status']=='waiting'
            confirmation=request['facts']['confirmationRef'];flow_ref=request['businessRef']
            # Keep `original` as the real HTTP boundary used by controlled_category.
            pending=rpc.call('confirmations.get',{'reference':confirmation})['data']
            assert pending['stage']=='provider.publish' and pending['expiresAt'] is not None
            assert pending['status']=='pending'
            # No business deadline: the generic public confirmation TTL (900s) still applies.
            remaining=datetime.fromisoformat(pending['expiresAt'].replace('Z','+00:00')).timestamp()-time.time()
            assert 0<remaining<=900,remaining
            if decision=='reject':user_decision(provider,confirmation,decision='reject')
            else:
                monkeypatch.setattr(provider.confirmations,'clock',lambda:time.time()+10**9)
                assert rpc.call('confirmations.get',{'reference':confirmation})['data']['status']=='expired'
            wait_for(lambda:len(transport.notices)==2)
            result=transport.notices[1][2]
            assert result['eventKind']=='business.result' and result['facts']['status']=='rejected'
            assert result['facts']['confirmationRef']==confirmation
            assert result['eventRef']!=request['eventRef']
            for _ in range(3):
                receipt=rpc.call('provider.flow',{'profile':'service','flowRef':flow_ref,'action':'observe'})
                assert receipt['status']=='rejected',receipt
            assert len(transport.notices)==2
        finally:release.set()
