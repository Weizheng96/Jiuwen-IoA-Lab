"""Trusted source transport and durable journal effects through public runtime seams.

Sources enter only through the SDK-issued private native-source descriptor of the
bound entry; the public layer never calls a host resolve/capture method.
"""
import time
from contextlib import contextmanager

import httpx
import pytest
from ioalab_sdk.runtime import (FlowJournal, HostRegistration, HostRegistry, LocalClient, ResidentRuntime,
    RuntimeConfig, native_source_descriptor, read_json)
from test_resident_runtime import configuration


class NativeAdapter:
    registration=HostRegistration('native-source-fixture','1')
    def submit(self,*args,**kwargs):pytest.fail('Source transport dispatched a model')
    def check(self):pytest.fail('Source transport probed a host')


def runtime_for(path,*,modules=None):
    registry=HostRegistry({'native':NativeAdapter()},'native')
    roles=modules(registry) if modules else ()
    runtime=ResidentRuntime(RuntimeConfig.load(path),host_registry=registry,modules=roles,native_entry_required=True).start()
    return runtime,registry


class Entry:
    """The bound native wrapper: private descriptor in, SDK entryRef out."""
    def __init__(self,runtime,workspace):
        self.runtime,self.workspace,self.captures=runtime,str(workspace),0
        self.descriptor=read_json(native_source_descriptor(runtime.config.home,'native',runtime.identities[0].profile))

    def capture(self,session='actual-native-session'):
        self.captures+=1
        response=httpx.post(self.descriptor['url'],json={'sessionId':session,'workspace':self.workspace,
            'captureId':'capture-%d'%self.captures},headers={'Authorization':'Bearer '+self.descriptor['grant'],
            'X-Ioa-Instance':self.descriptor['bootId']},trust_env=False,timeout=5)
        assert response.status_code==200,response.text
        return response.json()['data']

    def expire(self):
        """Advance the SDK source clock beyond every short reference issued so far."""
        later=time.time()+301
        self.runtime.native_sources.clock=lambda:later


UNKNOWN={'entryRef':'never-issued-by-this-entry'}


def test_ipc_source_is_verified_before_new_effect_and_journal_recovers(platform, tmp_path):
    path, _ = configuration(platform, tmp_path, service=False)
    runtime,_=runtime_for(path)
    actor = runtime.identities[0].agent_id
    effects = []
    def advance(journal, record):
        value = journal.step(record, 0, 'assistant.rules.draft',
                             {'declaration': {}, 'instructions': 'own rule'},
                             lambda *args: effects.append(args) or {'status': 'completed', 'data': {}})
        record['active'] = False
        return journal.receipt(record, 'completed', result=value)
    journal = FlowJournal(runtime, actor, 'test-source', advance)
    runtime.operations['assistant.delegation.flow'] = lambda rt, op, args: journal.call(op, args, ('start',))
    try:
        client = LocalClient(runtime.config.home)
        rejected = client.call('assistant.delegation.flow', {'action': 'start'}, operation_id='missing-source')
        assert rejected['status'] in ('waiting', 'rejected') and not effects
        original_entry=Entry(runtime,tmp_path).capture()
        result = client.call('assistant.delegation.flow', {'action': 'start'}, operation_id='native-start',
                             context_ref=original_entry)
        assert result['status'] == 'completed' and len(effects) == 1
        original = result['data']['flowRef']
        runtime.close()
        runtime,_=runtime_for(path)
        journal=FlowJournal(runtime,actor,'test-source',advance)
        runtime.operations['assistant.delegation.flow']=lambda rt,op,args:journal.call(op,args,('start',))
        client=LocalClient(runtime.config.home)
        assert client.call('assistant.delegation.flow', {'action': 'resume', 'flowRef': original},
                           operation_id='original-recovery')['status'] == 'completed'
        # The previous boot's entry reference is stale; recovery never needs it.
        assert client.call('assistant.delegation.flow', {'action':'resume','flowRef':original},operation_id='expired-ref-recovery',
                           context_ref=original_entry)['status']=='completed'
        assert len(effects) == 1
        assert client.call('runtime.status')['status'] == 'completed'
    finally:
        runtime.close()


@pytest.mark.parametrize('reference',[None,'expired','issued','consumed-after-first-effect'])
def test_reads_precede_lazy_source_gate_and_first_effect_pins_original_source(platform,tmp_path,reference):
    from ioalab_sdk.runtime import native_source
    path,_=configuration(platform,tmp_path,service=False)
    runtime,_=runtime_for(path)
    entry=Entry(runtime,tmp_path)
    reads=[];effects=[]
    def advance(journal,record):
        journal.step(record,0,'assistant.tools.list',{},lambda *args:reads.append('read') or {'status':'completed','data':{}})
        journal.step(record,1,'assistant.rules.draft',{'declaration':{},'instructions':'own rule'},
                     lambda *args:effects.append(native_source()) or {'status':'completed','data':{}})
        record['active']=False
        return journal.receipt(record,'completed')
    journal=FlowJournal(runtime,runtime.identities[0].agent_id,'lazy-effect',advance)
    runtime.operations['assistant.delegation.flow']=lambda rt,op,args:journal.call(op,args,('start',))
    try:
        client=LocalClient(runtime.config.home)
        metadata=None
        if reference:
            metadata=entry.capture()
            if reference=='expired':entry.expire()
        result=client.call('assistant.delegation.flow',{'action':'start'},operation_id='initial',context_ref=metadata)
        assert reads==['read']
        if reference not in ('issued','consumed-after-first-effect'):
            assert result['status']=='waiting' and result['data']['reason']=='not_ready' and effects==[]
            result=client.call('assistant.delegation.flow',{'action':'resume','flowRef':result['data']['flowRef']},
                               context_ref=entry.capture())
        assert result['status']=='completed' and effects[0].session_id=='actual-native-session'
        if reference=='consumed-after-first-effect':entry.expire()
        ref=result['data']['flowRef']
        replay=client.call('assistant.delegation.flow',{'action':'start','flowRef':ref},
                          context_ref=entry.capture('different-session'))
        assert replay['status']=='completed' and len(effects)==2
        # An unresolvable reference proves the pinned flow never consults a new source.
        unresolved=client.call('assistant.delegation.flow',{'action':'start','flowRef':ref},context_ref=UNKNOWN)
        assert unresolved['status']=='completed' and len(effects)==3
        assert all(value.session_id=='actual-native-session' for value in effects)
    finally:
        runtime.close()


def test_invalid_native_reference_cannot_create_flow(platform, tmp_path):
    path, _ = configuration(platform, tmp_path, service=False)
    runtime,_=runtime_for(path)
    entry=Entry(runtime,tmp_path)
    called = []
    def effect(*args):
        from ioalab_sdk.runtime import native_source
        native_source()
        called.append(args)
        return {'status':'completed','data':{}}
    runtime.operations['assistant.delegation.flow'] = effect
    try:
        client=LocalClient(runtime.config.home)
        stale=entry.capture();entry.expire()
        for metadata in (UNKNOWN,stale):
            result=client.call('assistant.delegation.flow', {'action': 'start'},context_ref=metadata)
            assert result['status']=='rejected' and result['error']['code']=='not_ready' and not called
            assert client.call('runtime.status',context_ref=metadata)['status']=='completed'
    finally:
        runtime.close()


def assistant(registry):
    from ioalab_sdk.assistant import AssistantRole
    return (AssistantRole(registry),)


def test_direct_original_intent_uses_saved_task_source_with_expired_new_ref(platform,tmp_path,monkeypatch):
    path,_=configuration(platform,tmp_path,service=False)
    runtime,_=runtime_for(path,modules=assistant)
    entry=Entry(runtime,tmp_path)
    try:
        client=LocalClient(runtime.config.home)
        task=client.call('assistant.delegation.prepare',{'purpose':'original intent','budget':10,'proposalFeeLimit':1},
                         context_ref=entry.capture())['data']
        sent=[]
        def platform_call(actor,operation,payload,*,operation_id):
            sent.append((actor,operation,payload,operation_id))
            return {'accepted':True}
        monkeypatch.setattr(runtime.connection,'call',platform_call)
        args={'taskRef':task['taskRef'],'service':'svc-test','request':{'client':{'id':runtime.identities[0].agent_id},
              'delegationScopeDigest':task['scopeDigest'],'templateDigest':'test-template'}}
        first=client.call('assistant.delegation.intent',args,operation_id='original-intent')
        stale=entry.capture('replacement-session');entry.expire()
        replay=client.call('assistant.delegation.intent',args,operation_id='original-intent',context_ref=stale)
        assert first['status']==replay['status']=='completed'
        assert sent[0]==sent[1]
    finally:runtime.close()


@pytest.mark.parametrize('reference',[None,'expired','unknown'])
def test_direct_new_task_fails_closed_and_can_retry_original_operation_with_real_source(platform,tmp_path,reference):
    path,_=configuration(platform,tmp_path,service=False)
    runtime,_=runtime_for(path,modules=assistant)
    entry=Entry(runtime,tmp_path)
    try:
        client=LocalClient(runtime.config.home)
        args={'purpose':'source required','budget':10,'proposalFeeLimit':1}
        metadata={'expired':lambda:entry.capture(),'unknown':lambda:UNKNOWN}.get(reference,lambda:None)()
        if reference=='expired':entry.expire()
        first=client.call('assistant.delegation.prepare',args,operation_id='first-task',context_ref=metadata)
        assert first['status']=='rejected' and first['error']['code']=='not_ready'
        prepared=client.call('assistant.delegation.prepare',args,operation_id='first-task',context_ref=entry.capture())
        assert prepared['status']=='completed' and prepared['data']['taskRef']
    finally:runtime.close()


def test_direct_original_message_does_not_resolve_replacement_reference(platform,tmp_path,monkeypatch):
    path,_=configuration(platform,tmp_path,service=False)
    runtime,registry=runtime_for(path,modules=assistant)
    entry=Entry(runtime,tmp_path)
    try:
        original_api=runtime.account.api
        def api(method,path,*args,**kwargs):
            if path.startswith('/messaging/accounts/'):
                return [{'agentId':runtime.identities[0].agent_id,'name':'own-interface','revision':1,
                         'inputSchema':{'type':'object'}}]
            return original_api(method,path,*args,**kwargs)
        monkeypatch.setattr(runtime.account,'api',api)
        client=LocalClient(runtime.config.home)
        interface=client.call('assistant.messaging.interfaces',{'account':'test-recipient'})['data'][0]['interfaceRef']
        args={'interfaceRef':interface,'input':{}}
        first=client.call('assistant.messaging.send',args,operation_id='original-message',context_ref=entry.capture())
        for replacement in (entry.capture('replacement-session'),UNKNOWN):
            replay=client.call('assistant.messaging.send',args,operation_id='original-message',context_ref=replacement)
            assert first['status']==replay['status']=='accepted' and first['data']==replay['data']
    finally:runtime.close()


def test_direct_message_continuation_uses_original_interaction_source(platform,tmp_path):
    from ioalab_sdk.runtime import ToolBinding
    from test_assistant_role import runtime_at,ScriptedHost,publish,INPUT,OUTPUT
    path,user=configuration(platform,tmp_path/'sender',service=False)
    runtime,registry=runtime_for(path,modules=assistant)
    entry=Entry(runtime,tmp_path)
    tool=ToolBinding('reserve_slot','native source test',INPUT,lambda args,context:{'status':'reserved'},'reservation',output_schema=OUTPUT)
    try:
        with runtime_at(platform,tmp_path/'receiver',ScriptedHost(),[tool]) as (receiver,inbox,module,target):
            publish(receiver,inbox,user['username'])
            client=LocalClient(runtime.config.home)
            interface=client.call('assistant.messaging.interfaces',{'account':target['username']})['data'][0]['interfaceRef']
            first=client.call('assistant.messaging.send',{'interfaceRef':interface,'input':{'slot':'first'}},
                              context_ref=entry.capture())
            status=client.call('assistant.messaging.status',{'messageRef':first['data']['messageRef'],'waitSeconds':15})
            interaction=status['data']['interactionRef']
            assert interaction
            for name,reference in (('unknown',UNKNOWN),('different-valid',entry.capture('different-session'))):
                continued=client.call('assistant.messaging.send',{'interactionRef':interaction,'input':{'slot':name}},
                                      context_ref=reference)
                assert continued['status']=='accepted',continued
                assert continued['data']['interactionRef']==interaction
            assert registry.context(runtime.identities[0].agent_id,interaction)['sessionId']=='actual-native-session'
    finally:runtime.close()
