"""Public S01 host binding, original receipt recovery and default isolation."""
from dataclasses import replace
from types import SimpleNamespace
import threading
import pytest

from ioalab_sdk.runtime import (BindingAck,HostContext,HostRegistry,HostRegistration,HostInput,
    SubmissionResult,SubmissionEvent,MachineTerminal,RuntimeFault,RuntimeStore)


class Host:
    registration=HostRegistration('registry-boundary','1',('get_request','cancel'))
    def __init__(self,name):self.name=name;self.calls=[];self.receipts={};self.pending=False
    def check(self):return {'state':'unknown'}
    def submit(self,op,input,session_id=None,*,execution=None,on_event=None,on_tool=None):
        self.calls.append((op,session_id,execution))
        sid=session_id or self.name+'-session';request='request-'+op
        result=SubmissionResult(op,input.attempt.nonce,'accepted',sid,request,'accepted')
        assert on_event(SubmissionEvent('session','session',result))==BindingAck(sid,request)
        if not self.pending:result=replace(result,state='completed',terminal=MachineTerminal(op,input.attempt.nonce,sid,request,'completed','machine:'+request))
        self.receipts[request]=result;return result
    def get_request(self,query):return self.receipts[query.request_id]
    def cancel(self,request):return False


@pytest.fixture
def store(tmp_path):
    value=RuntimeStore(tmp_path/'runtime.sqlite','http://localhost',['a'],owner_id='owner')
    yield value
    value.close()


def registry(store,default='first'):
    hosts={name:Host(name) for name in ('first','second')}
    resources={name:SimpleNamespace(for_profile=lambda profile,selected=name:{'selected':selected}) for name in hosts}
    value=HostRegistry(hosts,default,tools=resources)
    value.bind(store,identities=[SimpleNamespace(agent_id='a',profile='personal')]);return value


def test_default_changes_only_new_business_and_binding_survives_restart(store,tmp_path):
    original=registry(store)
    assert original.tools_for('a','old','personal')=={'selected':'first'}
    assert original.submit('a','old','operation',HostInput('input')).session_id=='first-session'
    resumed=registry(store,'second')
    assert resumed.tools_for('a','old','personal')=={'selected':'first'}
    assert resumed.tools_for('a','new','personal')=={'selected':'second'}
    assert resumed.submit('a','old','next',HostInput('next')).session_id=='first-session'
    source=HostContext('first','first-session',str(tmp_path/'work'),'a','personal')
    resumed.bind_context('a','old',source)
    with pytest.raises(RuntimeFault):resumed.bind_context('a','old',HostContext('first','first-session',str(tmp_path/'other'),'a','personal'))
    assert not resumed.hosts['second'].calls


def test_native_origin_persists_before_interaction_and_cannot_change(store,tmp_path):
    value=registry(store);source=HostContext('first','real-user-session',str(tmp_path),'a','personal')
    value.record_origin('a','original-message',source)
    value=registry(store,'second');value.record_origin('a','original-message',source)
    value.attach_origin('a','original-message','business')
    assert value.context('a','business')['sessionId']=='real-user-session'
    with pytest.raises(RuntimeFault):value.record_origin('a','original-message',HostContext('second','other-session',str(tmp_path),'a','personal'))
    with pytest.raises(RuntimeFault):value.bind_context('a','business',HostContext('first','real-user-session',str(tmp_path/'other'),'a','personal'))
    assert value.submit('a','business','notice',HostInput('notice')).session_id=='real-user-session'
    assert value.context('a','business')['workspace']==str(tmp_path)


def test_missing_or_foreign_source_is_not_inferred(store,tmp_path):
    value=registry(store)
    with pytest.raises(RuntimeFault):value.tools_for('other-account','business','personal')
    with pytest.raises(RuntimeFault):value.attach_origin('a','missing-origin','business')
    assert not any(hasattr(value,name) for name in ('capture_context','resolve_context','run','deliver','for_interaction'))


def test_delivery_accepted_never_implies_handled_and_reference_identity_is_checked(store,tmp_path):
    value=registry(store);value.hosts['first'].pending=True
    value.bind_context('a','business',HostContext('first','native-session',str(tmp_path)))
    receipt=value.submit('a','business','business-operation',HostInput('message'))
    assert receipt.delivery=='accepted' and not receipt.terminal
    host=value.hosts['first']
    host.get_request=lambda request:replace(receipt,operation_id='foreign-operation',delivery='handled')
    with pytest.raises(RuntimeFault):value.get_submission('a','business','business-operation')
    host.get_request=lambda request:replace(receipt,state='unknown',delivery='unknown',request_id=None)
    assert value.get_submission('a','business','business-operation').request_id==receipt.request_id
    assert value.submit('a','business','business-operation',HostInput('message')).request_id==receipt.request_id


def test_concurrent_delivery_claims_once_and_restart_never_resends_unknown(store,tmp_path):
    value=registry(store);value.bind_context('a','business',HostContext('first','native-session',str(tmp_path)))
    entered=threading.Event();release=threading.Event();calls=[];results=[]
    def submit(op,*args,**kwargs):calls.append(op);entered.set();assert release.wait(5);raise TimeoutError('original result lost')
    value.hosts['first'].submit=submit
    thread=threading.Thread(target=lambda:results.append(value.submit('a','business','original',HostInput('message'))))
    thread.start()
    try:
        assert entered.wait(5)
        assert value.submit('a','business','original',HostInput('message')).state=='unknown'
    finally:release.set();thread.join(5)
    resumed=registry(store,'second')
    assert resumed.submit('a','business','original',HostInput('message')).state=='unknown'
    assert calls==['original'] and not resumed.hosts['first'].calls
    resumed.bind_context('a','other-business',HostContext('second','native-session',str(tmp_path)))
    resumed.hosts['second'].get_request=lambda *_:pytest.fail('Cross-business receipt queried')
    with pytest.raises(RuntimeFault):resumed.get_submission('a','other-business','original')


@pytest.mark.parametrize('confirmed',['delivered','handled'])
@pytest.mark.parametrize('late',['unknown','accepted','delivered','exception'])
def test_out_of_order_queries_keep_stronger_confirmed_receipt(store,tmp_path,confirmed,late):
    value=registry(store);host=value.hosts['first'];host.pending=True
    receipt=value.submit('a','business','original',HostInput('message'))
    entered=threading.Event();release=threading.Event();responses=[]
    def query(request):
        if threading.current_thread().name=='late-query':
            entered.set();assert release.wait(5)
            if late=='exception':raise TimeoutError('late query lost')
            return replace(receipt,delivery=late)
        return replace(receipt,delivery=confirmed)
    host.get_request=query
    thread=threading.Thread(name='late-query',target=lambda:responses.append(value.get_submission('a','business','original')))
    thread.start()
    try:
        assert entered.wait(5)
        assert value.get_submission('a','business','original').delivery==confirmed
    finally:release.set();thread.join(5)
    assert responses[0].delivery==confirmed and not responses[0].terminal
    assert value.submit('a','business','original',HostInput('message')).delivery==confirmed


def test_cancel_uses_original_execution_and_rejects_operation_rebinding(store,tmp_path):
    value=registry(store);value.hosts['first'].pending=True;cancelled=[]
    value.hosts['first'].cancel=lambda request:cancelled.append(('first',request)) or True
    value.hosts['second'].cancel=lambda _:pytest.fail('Cancellation broadcast to other registration')
    receipt=value.submit('a','business','original',HostInput('input'));value.default='second'
    assert value.cancel_submission('a','business','original')=={'supported':True,'accepted':True,'ended':False}
    assert cancelled==[('first',receipt.request_id)] and value.cancel_job('unknown-job')==[]
    with pytest.raises(RuntimeFault):value.submit('a','other','original',HostInput('input'))


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
