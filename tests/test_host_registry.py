"""Public host binding contract; adapters are external-system boundary substitutes."""
import pytest

from ioalab_sdk.runtime import (AgentDefinition, DeliveryReceipt, HostContext, HostRegistry,
                              HostResult, HostTurn, RuntimeFault, RuntimeStore)


class Host:
    def __init__(self, name):self.name=name;self.turns=[]
    def capabilities(self):return {'available':True,'skillSources':{'skill':self.name}}
    def probe(self):return self.capabilities()
    def run(self, turn):
        self.turns.append(turn)
        session=turn.session_id or self.name+'-session'
        turn.on_session(session)
        return HostResult('completed',session,{})
    def cancel(self, operation):return False


@pytest.fixture
def store(tmp_path):
    value=RuntimeStore(tmp_path/'runtime.sqlite','http://localhost',['a'],owner_id='owner')
    yield value
    value.close()


def registry(store, default='first'):
    value=HostRegistry({'first':Host('first'),'second':Host('second')},default)
    value.bind(store)
    return value


def test_default_changes_only_new_business_and_binding_survives_restart(store,tmp_path):
    original=registry(store)
    old=original.for_interaction('a','old')
    assert old.capabilities()['skillSources']=={'skill':'first'}
    turn=HostTurn('operation','a','old',AgentDefinition('test','instructions',()),'input',str(tmp_path/'work'))
    assert old.run(turn).session_id=='first-session'
    restarted=registry(store,'second')
    assert restarted.for_interaction('a','old').capabilities()['skillSources']=={'skill':'first'}
    assert restarted.for_interaction('a','new').capabilities()['skillSources']=={'skill':'second'}
    assert restarted.for_interaction('a','old').run(HostTurn('next','a','old',turn.definition,'next',turn.workspace,session_id='first-session')).session_id=='first-session'
    with pytest.raises(RuntimeFault,match='original workspace'):
        restarted.run(HostTurn('next','a','old',turn.definition,'next',str(tmp_path/'other'),session_id='first-session'))


def test_native_origin_persists_before_interaction_and_cannot_change(store,tmp_path):
    value=registry(store)
    source=HostContext('first','real-user-session',str(tmp_path))
    value.record_origin('a','original-message',source)
    value=registry(store,'second')
    value.record_origin('a','original-message',source)
    value.attach_origin('a','original-message','business')
    assert value.context('a','business')['sessionId']=='real-user-session'
    with pytest.raises(RuntimeFault,match='native source'):
        value.record_origin('a','original-message',HostContext('second','other-session',str(tmp_path)))
    with pytest.raises(RuntimeFault,match='replace the user conversation'):
        value.run(HostTurn('op','a','business',AgentDefinition('x','x',()),'x',str(tmp_path),session_id='real-user-session'))


def test_missing_or_foreign_source_is_not_inferred(store,tmp_path):
    value=registry(store)
    with pytest.raises(RuntimeFault,match='another identity'):
        value.for_interaction('other-account','business')
    with pytest.raises(RuntimeFault,match='not available'):
        value.capture_context('first')
    with pytest.raises(RuntimeFault,match='not available'):
        value.resolve_context('first','opaque',config=None)
    with pytest.raises(RuntimeFault,match='original source'):
        value.attach_origin('a','missing-origin','business')


def test_delivery_accepted_never_implies_handled_and_reference_identity_is_checked(store,tmp_path):
    value=registry(store)
    value.bind_context('a','business',HostContext('first','native-session',str(tmp_path)))
    host=value.hosts['first']
    host.deliver=lambda operation,session,message:DeliveryReceipt(operation,'first',session,'accepted','queue-1')
    receipt=value.deliver('a','business','business-operation','message')
    assert receipt.status=='accepted' and receipt.reference=='queue-1'
    with pytest.raises(RuntimeFault,match='not available'):
        value.delivery_status('a','business','business-operation','queue-1')
    host.delivery_status=lambda operation,session,reference:DeliveryReceipt('another-op','first',session,'handled',reference)
    with pytest.raises(RuntimeFault,match='original host delivery'):
        value.delivery_status('a','business','business-operation','queue-1')
    host.delivery_status=lambda operation,session,reference:DeliveryReceipt(operation,'first',session,'unknown')
    assert value.delivery_status('a','business','business-operation','queue-1').reference=='queue-1'
    assert value.deliver('a','business','business-operation','message').reference=='queue-1'


def test_concurrent_delivery_claims_once_and_restart_never_resends_unknown(store,tmp_path):
    import threading
    value=registry(store)
    value.bind_context('a','business',HostContext('first','native-session',str(tmp_path)))
    entered=threading.Event();release=threading.Event();calls=[];results=[]
    def deliver(operation,session,message):
        calls.append(operation);entered.set();assert release.wait(5)
        raise TimeoutError('effect is unknown')
    value.hosts['first'].deliver=deliver
    thread=threading.Thread(target=lambda:results.append(value.deliver('a','business','original','message')))
    thread.start()
    try:
        assert entered.wait(5)
        assert value.deliver('a','business','original','message').status=='unknown'
    finally:release.set();thread.join(5)
    value=registry(store,'second')
    value.hosts['first'].deliver=deliver
    assert value.deliver('a','business','original','message').status=='unknown'
    assert calls==['original']
    value.bind_context('a','other-business',HostContext('second','native-session',str(tmp_path)))
    value.hosts['second'].delivery_status=lambda *args:pytest.fail('Cross-business receipt was queried')
    with pytest.raises(RuntimeFault,match='another business session'):
        value.delivery_status('a','other-business','original',None)


@pytest.mark.parametrize('confirmed',['delivered','handled'])
@pytest.mark.parametrize('late',['unknown','accepted','delivered','exception'])
def test_out_of_order_queries_keep_stronger_confirmed_receipt(store,tmp_path,confirmed,late):
    import threading
    value=registry(store)
    value.bind_context('a','business',HostContext('first','native-session',str(tmp_path)))
    host=value.hosts['first']
    host.deliver=lambda operation,session,message:DeliveryReceipt(operation,'first',session,'accepted','queue-1')
    value.deliver('a','business','original','message')
    entered=threading.Event();release=threading.Event();responses=[]
    def query(operation,session,reference):
        if threading.current_thread().name=='late-host-query':
            entered.set();assert release.wait(5)
            if late=='exception':raise TimeoutError('late query timeout')
            return DeliveryReceipt(operation,'first',session,late,reference)
        return DeliveryReceipt(operation,'first',session,confirmed,reference)
    host.delivery_status=query
    thread=threading.Thread(name='late-host-query',target=lambda:responses.append(value.delivery_status('a','business','original','queue-1')))
    thread.start()
    try:
        assert entered.wait(5)
        assert value.delivery_status('a','business','original','queue-1').status==confirmed
    finally:release.set();thread.join(5)
    assert responses[0].status==confirmed
    assert value.deliver('a','business','original','message').status==confirmed


def test_cancel_uses_original_execution_and_rejects_operation_rebinding(store,tmp_path):
    value=registry(store);cancelled=[]
    value.hosts['first'].cancel=lambda operation:cancelled.append(('first',operation)) or True
    value.hosts['second'].cancel=lambda operation:pytest.fail('Cancellation broadcast to unrelated host')
    turn=HostTurn('original','a','business',AgentDefinition('test','test',()),'input',str(tmp_path))
    value.run(turn)
    value.default='second'
    assert value.cancel('original') is True and cancelled==[('first','original')]
    assert value.cancel('unknown-operation') is False
    with pytest.raises(RuntimeFault,match='another business context'):
        value.run(HostTurn('original','a','other',turn.definition,'input',str(tmp_path)))


def test_origin_binding_is_applied_with_real_interaction_link(store,tmp_path):
    value=registry(store)
    source=HostContext('first','user-session',str(tmp_path))
    value.record_origin('a','mid',source)
    store.prepare('a','message.send',{'messageId':'mid','recipient':'peer','kind':'message.request',
                  'body':{'interfaceName':'echo','revision':1}},'operation')
    store.recover_link('a',{'interactionId':'interaction','sender':'a','recipient':'peer','originMessageId':'mid',
                       'interfaceName':'echo','revision':1,'deadline':'2099-01-01T00:00:00Z','maxRequests':8})
    assert registry(store,'second').context('a','interaction')=={
        'hostRef':'first','sessionId':'user-session','workspace':str(tmp_path),'source':'native'}


@pytest.mark.parametrize('second_host',['first','second'])
def test_scheduler_holds_unknown_native_session_but_not_same_id_on_other_host(store,tmp_path,second_host):
    from ioalab_sdk.runtime.scheduling import Scheduler,JobHandler,JobOutcome
    from conftest import wait_for
    value=registry(store)
    value.bind_context('a','one',HostContext('first','same-native-id',str(tmp_path)))
    value.bind_context('a','two',HostContext(second_host,'same-native-id',str(tmp_path)))
    scheduler=Scheduler(store,{'model':JobHandler(lambda job:JobOutcome('unknown' if job['interaction']=='one' else 'completed',{}),'1')}).start()
    try:
        first=scheduler.submit('a','one','model',{},operation_id='one')['reference']
        wait_for(lambda:scheduler.get(first)['status']=='unknown')
        second=scheduler.submit('a','two','model',{},operation_id='two')['reference']
        if second_host=='second':wait_for(lambda:scheduler.get(second)['status']=='completed')
        else:
            control=scheduler.submit('a','unrelated','model',{},operation_id='unrelated')['reference']
            wait_for(lambda:scheduler.get(control)['status']=='completed')
            assert scheduler.get(second)['status']=='queued'
    finally:scheduler.close()


def test_assistant_resumes_original_rule_tools_after_restart_with_other_default(platform,tmp_path):
    from types import SimpleNamespace
    from ioalab_sdk.assistant import AssistantRole
    from ioalab_sdk.runtime import LocalClient,ResidentRuntime,RuntimeConfig,ToolBinding
    from test_assistant_role import runtime_at,publish,send_and_read,ScriptedHost,INPUT,OUTPUT
    from test_resident_runtime import configuration
    from conftest import wait_for
    first_host,second_host=ScriptedHost(),ScriptedHost()
    effects=[]
    def reserve(args,context):
        effects.append(context)
        return {'status':'conflict','suggested':'second'} if args['slot']=='first' else {'status':'reserved'}
    tool=ToolBinding('reserve_slot','Frozen tool',INPUT,reserve,'reservation',output_schema=OUTPUT)
    resource=SimpleNamespace(for_profile=lambda profile:{tool.name:tool})
    with runtime_at(platform,tmp_path/'sender') as (_,sender,_,user):
        directory=tmp_path/'receiver';directory.mkdir()
        path,target=configuration(platform,directory,service=False)
        hosts={'first':first_host,'second':second_host}
        registry=HostRegistry(hosts,'first',tools={'first':resource})
        role=AssistantRole(registry,{tool.name:tool})
        runtime=ResidentRuntime(RuntimeConfig.load(path),modules=(role,),host_registry=registry).start()
        try:
            wait_for(lambda:runtime.connection.connected)
            inbox=LocalClient(runtime.config.home)
            publish(runtime,inbox,user['username'])
            interface=sender.call('assistant.messaging.interfaces',{'account':target['username']})['data'][0]['interfaceRef']
            _,first=send_and_read(sender,{'interfaceRef':interface,'input':{'slot':'first'}})
            assert first['state']=='completed'
        finally:runtime.close()
        registry=HostRegistry(hosts,'second',tools={'first':resource})
        # The newly selected default has no original rule tools. The old binding still owns them.
        role=AssistantRole(registry,{})
        runtime=ResidentRuntime(RuntimeConfig.load(path),modules=(role,),host_registry=registry).start()
        try:
            wait_for(lambda:runtime.connection.connected)
            _,second=send_and_read(sender,{'interactionRef':first['interactionRef'],'input':{'slot':'second'}})
            assert second['state']=='completed' and second['result']['output']=={'status':'reserved'}
            assert len(effects)==2 and not second_host.turns
            assert effects[0]['sessionId']==effects[1]['sessionId']
        finally:runtime.close()
