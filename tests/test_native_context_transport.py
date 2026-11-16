"""Trusted source transport and durable journal effects through public runtime seams."""
import pytest
from ioalab_sdk.runtime import FlowJournal, HostContext, HostRegistry, LocalClient, ResidentRuntime, RuntimeConfig
from test_resident_runtime import configuration


class NativeHost:
    def __init__(self):
        self.resolved=[]

    def resolve_context(self, reference, *, host_ref, config):
        from ioalab_sdk.runtime import RuntimeFault
        self.resolved.append(reference)
        if reference not in ('issued-native-reference','different-valid-reference','single-use-reference'):
            raise RuntimeFault('not_ready', 'Source reference expired or unavailable')
        if reference=='single-use-reference' and self.resolved.count(reference)>1:
            raise RuntimeFault('not_ready','The transport reference has expired after resolution')
        return HostContext(host_ref, 'different-session' if reference=='different-valid-reference' else 'actual-native-session', str(config.home.parent))


def test_ipc_source_is_verified_before_new_effect_and_journal_recovers(platform, tmp_path):
    path, _ = configuration(platform, tmp_path, service=False)
    registry = HostRegistry({'native': NativeHost()}, 'native')
    runtime = ResidentRuntime(RuntimeConfig.load(path), host_registry=registry).start()
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
        result = client.call('assistant.delegation.flow', {'action': 'start'}, operation_id='native-start',
                             context_ref={'hostRef': 'native', 'reference': 'issued-native-reference'})
        assert result['status'] == 'completed' and len(effects) == 1
        original = result['data']['flowRef']
        runtime.close()
        registry=HostRegistry({'native':NativeHost()},'native')
        runtime=ResidentRuntime(RuntimeConfig.load(path),host_registry=registry).start()
        journal=FlowJournal(runtime,actor,'test-source',advance)
        runtime.operations['assistant.delegation.flow']=lambda rt,op,args:journal.call(op,args,('start',))
        client=LocalClient(runtime.config.home)
        assert client.call('assistant.delegation.flow', {'action': 'resume', 'flowRef': original},
                           operation_id='original-recovery')['status'] == 'completed'
        assert client.call('assistant.delegation.flow', {'action':'resume','flowRef':original},operation_id='expired-ref-recovery',context_ref={'hostRef':'native','reference':'expired'})['status']=='completed'
        assert len(effects) == 1
        assert client.call('runtime.status')['status'] == 'completed'
    finally:
        runtime.close()


@pytest.mark.parametrize('reference',[None,'expired','issued-native-reference','single-use-reference'])
def test_reads_precede_lazy_source_gate_and_first_effect_pins_original_source(platform,tmp_path,reference):
    from ioalab_sdk.runtime import native_source
    path,_=configuration(platform,tmp_path,service=False)
    host=NativeHost()
    runtime=ResidentRuntime(RuntimeConfig.load(path),host_registry=HostRegistry({'native':host},'native')).start()
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
        metadata={'hostRef':'native','reference':reference} if reference else None
        result=client.call('assistant.delegation.flow',{'action':'start'},operation_id='initial',context_ref=metadata)
        assert reads==['read']
        if reference not in ('issued-native-reference','single-use-reference'):
            assert result['status']=='waiting' and result['data']['reason']=='not_ready' and effects==[]
            result=client.call('assistant.delegation.flow',{'action':'resume','flowRef':result['data']['flowRef']},
                               context_ref={'hostRef':'native','reference':'issued-native-reference'})
        assert result['status']=='completed' and effects[0].session_id=='actual-native-session'
        ref=result['data']['flowRef']
        replay=client.call('assistant.delegation.flow',{'action':'start','flowRef':ref},
                          context_ref={'hostRef':'native','reference':'different-valid-reference'})
        assert replay['status']=='completed' and len(effects)==2
        assert all(value.session_id=='actual-native-session' for value in effects)
        assert 'different-valid-reference' not in host.resolved
    finally:
        runtime.close()


def test_invalid_native_reference_cannot_create_flow(platform, tmp_path):
    path, _ = configuration(platform, tmp_path, service=False)
    registry = HostRegistry({'native': NativeHost()}, 'native')
    runtime = ResidentRuntime(RuntimeConfig.load(path), host_registry=registry).start()
    called = []
    def effect(*args):
        from ioalab_sdk.runtime import native_source
        native_source()
        called.append(args)
        return {'status':'completed','data':{}}
    runtime.operations['assistant.delegation.flow'] = effect
    try:
        client=LocalClient(runtime.config.home)
        result=client.call('assistant.delegation.flow', {'action': 'start'},
                          context_ref={'hostRef': 'native', 'reference': 'expired'})
        assert result['status']=='rejected' and result['error']['code']=='not_ready' and not called
        assert client.call('runtime.status',context_ref={'hostRef':'native','reference':'expired'})['status']=='completed'
    finally:
        runtime.close()


def test_direct_original_intent_uses_saved_task_source_with_expired_new_ref(platform,tmp_path,monkeypatch):
    from ioalab_sdk.assistant import AssistantRole
    path,_=configuration(platform,tmp_path,service=False)
    host=NativeHost();registry=HostRegistry({'native':host},'native')
    runtime=ResidentRuntime(RuntimeConfig.load(path),host_registry=registry,modules=(AssistantRole(registry),)).start()
    try:
        client=LocalClient(runtime.config.home)
        task=client.call('assistant.delegation.prepare',{'purpose':'original intent','budget':10,'proposalFeeLimit':1},
                         context_ref={'hostRef':'native','reference':'issued-native-reference'})['data']
        sent=[]
        def platform_call(actor,operation,payload,*,operation_id):
            sent.append((actor,operation,payload,operation_id))
            return {'accepted':True}
        monkeypatch.setattr(runtime.connection,'call',platform_call)
        args={'taskRef':task['taskRef'],'service':'svc-test','request':{'client':{'id':runtime.identities[0].agent_id},
              'delegationScopeDigest':task['scopeDigest'],'templateDigest':'test-template'}}
        first=client.call('assistant.delegation.intent',args,operation_id='original-intent')
        replay=client.call('assistant.delegation.intent',args,operation_id='original-intent',
                          context_ref={'hostRef':'native','reference':'expired'})
        assert first['status']==replay['status']=='completed'
        assert sent[0]==sent[1] and host.resolved==['issued-native-reference']
    finally:runtime.close()


@pytest.mark.parametrize('reference',[None,'expired'])
def test_direct_new_task_fails_closed_and_can_retry_original_operation_with_real_source(platform,tmp_path,reference):
    from ioalab_sdk.assistant import AssistantRole
    path,_=configuration(platform,tmp_path,service=False)
    host=NativeHost();registry=HostRegistry({'native':host},'native')
    runtime=ResidentRuntime(RuntimeConfig.load(path),host_registry=registry,modules=(AssistantRole(registry),)).start()
    try:
        client=LocalClient(runtime.config.home)
        args={'purpose':'source required','budget':10,'proposalFeeLimit':1}
        metadata={'hostRef':'native','reference':reference} if reference else None
        first=client.call('assistant.delegation.prepare',args,operation_id='first-task',context_ref=metadata)
        assert first['status']=='rejected' and first['error']['code']=='not_ready'
        prepared=client.call('assistant.delegation.prepare',args,operation_id='first-task',
                             context_ref={'hostRef':'native','reference':'issued-native-reference'})
        assert prepared['status']=='completed' and prepared['data']['taskRef']
    finally:runtime.close()


def test_direct_original_message_does_not_resolve_replacement_reference(platform,tmp_path,monkeypatch):
    from ioalab_sdk.assistant import AssistantRole
    path,_=configuration(platform,tmp_path,service=False)
    host=NativeHost();registry=HostRegistry({'native':host},'native')
    runtime=ResidentRuntime(RuntimeConfig.load(path),host_registry=registry,modules=(AssistantRole(registry),)).start()
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
        first=client.call('assistant.messaging.send',args,operation_id='original-message',
                          context_ref={'hostRef':'native','reference':'issued-native-reference'})
        replay=client.call('assistant.messaging.send',args,operation_id='original-message',
                           context_ref={'hostRef':'native','reference':'expired'})
        assert first['status']==replay['status']=='accepted' and first['data']==replay['data']
        assert host.resolved==['issued-native-reference']
    finally:runtime.close()


def test_direct_message_continuation_uses_original_interaction_source(platform,tmp_path):
    from ioalab_sdk.assistant import AssistantRole
    from ioalab_sdk.runtime import ToolBinding
    from test_assistant_role import runtime_at,ScriptedHost,publish,INPUT,OUTPUT
    path,user=configuration(platform,tmp_path/'sender',service=False)
    host=NativeHost();registry=HostRegistry({'native':host},'native')
    runtime=ResidentRuntime(RuntimeConfig.load(path),host_registry=registry,modules=(AssistantRole(registry),)).start()
    tool=ToolBinding('reserve_slot','native source test',INPUT,lambda args,context:{'status':'reserved'},'reservation',output_schema=OUTPUT)
    try:
        with runtime_at(platform,tmp_path/'receiver',ScriptedHost(),[tool]) as (receiver,inbox,module,target):
            publish(receiver,inbox,user['username'])
            client=LocalClient(runtime.config.home)
            interface=client.call('assistant.messaging.interfaces',{'account':target['username']})['data'][0]['interfaceRef']
            first=client.call('assistant.messaging.send',{'interfaceRef':interface,'input':{'slot':'first'}},
                              context_ref={'hostRef':'native','reference':'issued-native-reference'})
            status=client.call('assistant.messaging.status',{'messageRef':first['data']['messageRef'],'waitSeconds':15})
            interaction=status['data']['interactionRef']
            assert interaction
            for reference in ('expired','different-valid-reference'):
                continued=client.call('assistant.messaging.send',{'interactionRef':interaction,'input':{'slot':reference}},
                                      context_ref={'hostRef':'native','reference':reference})
                assert continued['status']=='accepted',continued
                assert continued['data']['interactionRef']==interaction
            assert host.resolved==['issued-native-reference']
            assert registry.context(runtime.identities[0].agent_id,interaction)['sessionId']=='actual-native-session'
    finally:runtime.close()
