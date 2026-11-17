"""Actual IPC/WS admission controls and durable pause state; hosts are explicit doubles."""
from concurrent.futures import ThreadPoolExecutor
import uuid

import pytest
from ioalab_sdk.provider import ProviderRole
from ioalab_sdk.runtime.config import RuntimeConfig
from ioalab_sdk.runtime.files import private_json,read_json
from ioalab_sdk.runtime.ipc import LocalClient
from ioalab_sdk.runtime.protocol import RuntimeFault
from ioalab_sdk.runtime.resident import ResidentRuntime
from conftest import wait_for
from test_resident_runtime import configuration
from test_resident_delegation import participants,request,terminal
from test_delegation_admission import online,offer,intent,opened,fee_body
from test_service_publication import service,draft
from test_assistant_role import user_decision
from submission_fixture import bound_runtime


def uid():return str(uuid.uuid4())
def control(runtime,name,operation=None):
    return LocalClient(runtime.config.home).call('provider.'+name,{'profile':'service'},operation_id=operation or uid())


def test_unpublished_service_can_pause_but_cannot_resume_without_business_readiness(platform,tmp_path):
    path,_=configuration(platform,tmp_path);role=ProviderRole()
    runtime=ResidentRuntime(RuntimeConfig.load(path),modules=(role,)).start()
    try:
        wait_for(lambda:runtime.connection.connected)
        paused=control(runtime,'pause');assert paused['status']=='completed',paused
        assert paused['data']['currentPolicy']['accepting'] is False
        revision=paused['data']['policyReceipt']['revision']
        assert control(runtime,'resume')['error']['code']=='not_ready'
        state=control(runtime,'status')['data']
        assert state['published'] is False and state['admissionPolicy']['revision']==revision
        assert not state['admissionPolicy']['accepting'] and not runtime.scheduler.status()['active']
        rpc=LocalClient(runtime.config.home)
        service_config={'profile':'service','name':'暂停中的专业服务','description':'软件开发服务','tags':[],
                        'categoryId':platform.discovery.categories()[0]['id'],'templates':[draft(platform)]}
        preview=rpc.call('provider.preview',{'service':service_config})['data']
        args={key:preview[key] for key in ('service','contentDigest','expectedRevision')};publication=uid()
        pending=rpc.call('provider.publish',args,operation_id=publication)
        user_decision(runtime,pending['data']['confirmationRef'])
        assert rpc.call('provider.publish',args,operation_id=publication)['status']=='completed'
        wait_for(lambda:role.execution.published.get('service'))
        assert control(runtime,'status')['data']['admissionPolicy']['accepting'] is False
        assert LocalClient(runtime.config.home).call('provider.pause',{'profile':'personal'})['status']=='rejected'
        store=runtime.store;store.checkpoint_set('atomic-test','pending',{'keep':True})
        with pytest.raises(TypeError):store.checkpoint_update('atomic-test',{'left':{'value':1},'bad':object()},remove=('pending',))
        assert store.checkpoint('atomic-test','left') is None and store.checkpoint('atomic-test','pending')=={'keep':True}
    finally:runtime.close()


def test_pause_rejects_new_intent_before_fee_or_model_execution(platform,tmp_path):
    with participants(platform,tmp_path) as (provider,assistant,host,template,task):
        assert control(provider,'pause')['status']=='completed'
        started,_=request(assistant,provider,template,task)
        wait_for(lambda:terminal(assistant,started['data']['quoteRef']))
        result=terminal(assistant,started['data']['quoteRef'])
        assert result['status']=='rejected' and result['data']['error']['code']=='service_paused',result
        assert not host.turns
        assert not platform.db.rows('SELECT * FROM delegation_admissions WHERE service=?',provider.identities[0].agent_id)
        assert not platform.db.rows('SELECT * FROM transfers WHERE payee_id=?',provider.account.account['id'])


def test_old_resume_receipt_restart_and_capacity_change_do_not_override_pause(platform,tmp_path):
    with participants(platform,tmp_path) as (provider,assistant,host,template,task):
        control(provider,'pause');resume_id=uid();resumed=control(provider,'resume',resume_id)
        assert resumed['status']=='completed' and resumed['data']['currentPolicy']['accepting']
        paused=control(provider,'pause')['data']['currentPolicy']
        repeated=control(provider,'resume',resume_id)
        assert repeated['data']['policyReceipt']==resumed['data']['policyReceipt']
        assert repeated['data']['currentPolicy']==paused
        identity=provider.identities[0];provider.modules[0].execution.configure(identity)
        assert control(provider,'status')['data']['admissionPolicy']==paused
        assert control(provider,'pause',resume_id)['error']['code']=='id_conflict'
        provider.close();path=tmp_path/'provider/config.json'
        settings=read_json(path);settings['provider']['services']['service']['max_active_delegations']=3
        private_json(path,settings);role=ProviderRole(host)
        restarted=bound_runtime(RuntimeConfig.load(path),host,lambda selected:ProviderRole(selected)).start()
        role=restarted.modules[0]
        try:
            wait_for(lambda:role.ready('service'))
            current=control(restarted,'status')['data']['admissionPolicy']
            assert current['maxActiveDelegations']==3 and current['accepting'] is False
            assert current['revision']==paused['revision']+1
            role.execution.configure(restarted.identities[0])
            assert control(restarted,'status')['data']['admissionPolicy']==current
            assert control(restarted,'resume',resume_id)['data']['currentPolicy']==current
        finally:restarted.close()


@pytest.mark.parametrize('failure',['lost_response','local_commit'])
def test_unknown_policy_change_recovers_one_original_write_after_restart(platform,tmp_path,monkeypatch,failure):
    with participants(platform,tmp_path) as (provider,assistant,host,template,task):
        before=control(provider,'status')['data']['admissionPolicy'];operation=uid()
        complete=provider.store.complete;update=provider.store.checkpoint_update
        with monkeypatch.context() as patch:
            if failure=='lost_response':
                def lost(frame):
                    # Lose the real incoming WS receipt before local storage,
                    # including read-back receipts while the link is impaired.
                    # Raising after call() would leave success already cached.
                    if frame.get('operation') in ('admission.configure','operation.get'):return
                    return complete(frame)
                patch.setattr(provider.store,'complete',lost)
            else:
                def lost_commit(namespace,values,**kwargs):
                    if namespace.startswith('provider-policy:') and 'configuration' in values:raise OSError('Test crash before atomic local completion')
                    return update(namespace,values,**kwargs)
                patch.setattr(provider.store,'checkpoint_update',lost_commit)
            if failure=='local_commit':
                with pytest.raises(RuntimeFault) as error:control(provider,'pause',operation)
                assert error.value.code=='unknown_result'
            else:
                # Server reconciliation waits up to 10s for the lost WS reply.
                # Give this fault case a longer outer RPC budget so its typed
                # role unknown response arrives before the transport times out.
                rpc=LocalClient(provider.config.home,timeout=30)
                unknown=rpc.call('provider.pause',{'profile':'service'},operation_id=operation)
                assert unknown['status']=='unknown' and unknown['operationId']==operation
                assert unknown['error']['code']=='unknown_result'
                assert unknown['error']['recovery']=='resume_original'
                resume_operation=uid()
                blocked=rpc.call('provider.resume',{'profile':'service'},operation_id=resume_operation)
                assert blocked['status']=='unknown' and blocked['operationId']==resume_operation
                assert blocked['error']['code']=='unknown_result'
                assert blocked['error']['recovery']=='resume_original'
            identity=provider.identities[0]
            with platform.db.lock:actual=platform.admissions.policy(platform.db.conn,identity.agent_id)
            assert not actual['accepting'] and actual['revision']==before['revision']+1
            assert provider.store.checkpoint('provider-policy:'+identity.agent_id,'pending')
        provider.close();role=ProviderRole(host)
        restarted=bound_runtime(RuntimeConfig.load(tmp_path/'provider/config.json'),host,lambda selected:ProviderRole(selected)).start()
        role=restarted.modules[0]
        try:
            wait_for(lambda:role.ready('service'))
            result=control(restarted,'pause',operation)
            assert result['status']=='completed' and result['data']['policyReceipt']==actual
            assert result['data']['currentPolicy']==actual
            assert restarted.store.checkpoint('provider-policy:'+identity.agent_id,'pending') is None
        finally:restarted.close()


def test_concurrent_same_control_has_one_policy_effect_and_conflicting_revision_is_not_rebased(platform,tmp_path,monkeypatch):
    with participants(platform,tmp_path) as (provider,assistant,host,template,task):
        before=control(provider,'status')['data']['admissionPolicy'];operation=uid()
        with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(lambda _:control(provider,'pause',operation),range(4)))
        assert all(result['status']=='completed' for result in results)
        assert all(result['data']['policyReceipt']==results[0]['data']['policyReceipt'] for result in results)
        assert results[0]['data']['policyReceipt']['revision']==before['revision']+1
        original=provider.connection.call;prepare=provider.store.prepare_checkpointed;competing=[]
        def race(actor,kind,payload,operation,namespace,values):
            if kind=='admission.configure':
                competing.append(original(actor,kind,{**payload,'maxActiveDelegations':3},operation_id=uid()))
            return prepare(actor,kind,payload,operation,namespace,values)
        operation=uid()
        with monkeypatch.context() as patch:
            # The freeze/prepare seam precedes original outbox visibility;
            # injecting at call() is too late once atomic preparation exists.
            patch.setattr(provider.store,'prepare_checkpointed',race)
            result=control(provider,'resume',operation)
        assert result['status']=='rejected' and result['error']['code']=='revision_conflict'
        assert control(provider,'resume',operation)['error']['code']=='revision_conflict'
        assert len(competing)==1 and control(provider,'status')['data']['admissionPolicy']==competing[0]


def test_pause_preserves_preexisting_held_fee_commit_but_rejects_a_new_reservation(platform,client,protocol_participant,service):
    local=protocol_participant;template=offer(platform,service);value=intent(platform,client,local,service,template)
    with online(client,local,service) as (sender,receiver):
        receiver.call('admission.configure',{'expectedRevision':0,'maxActiveDelegations':2,'accepting':True})
        binding=opened(sender,receiver,value);held=receiver.call('admission.reserve',binding)
        receiver.call('admission.configure',{'expectedRevision':1,'maxActiveDelegations':2,'accepting':False})
        other=intent(platform,client,local,service,template);new_binding=opened(sender,receiver,other)
        assert receiver.call('admission.reserve',new_binding,ok=False)['code']=='service_paused'
        assert receiver.call('admission.reserve',binding)==held
        payload={'admissionId':held['admissionId'],'ap2':fee_body(local,service[1],template,value)}
        operation=uid();paid=sender.call('ap2.pay',payload,operation)
        assert paid['op']=='ap2.pay' and sender.call('ap2.pay',payload,operation)==paid
        assert receiver.call('admission.status',{'admissionId':held['admissionId']})['state']=='committed'
        assert receiver.call('admission.status',{})['accepting'] is False
        assert len(platform.db.rows('SELECT * FROM transfers WHERE ref=?',value['body']['bindingDigest']))==1


def health_policy(runtime,state,*,ready=None):
    return runtime.modules[0].execution.admission_policy.system_health(
        runtime.identities[0],runtime.host_registry.default,state,recovery_ready=ready)


def test_health_pause_and_early_recovery_require_actual_readiness_fence(platform,tmp_path):
    with participants(platform,tmp_path) as (provider,assistant,host,template,task):
        before=control(provider,'status')['data']['admissionPolicy']
        assert health_policy(provider,'unavailable')['status']=='paused'
        paused=control(provider,'status')['data']['admissionPolicy']
        assert paused['revision']==before['revision']+1 and paused['accepting'] is False
        assert health_policy(provider,'available')['status']=='waiting'
        assert control(provider,'status')['data']['admissionPolicy']==paused
        assert health_policy(provider,'available',ready=lambda profile:{'ready':True})['status']=='resumed'
        restored=control(provider,'status')['data']['admissionPolicy']
        assert restored['accepting'] and restored['revision']==paused['revision']+1
        health_policy(provider,'available',ready=lambda profile:{'ready':True})
        assert control(provider,'status')['data']['admissionPolicy']==restored


@pytest.mark.parametrize('manual_when',['before','after'])
def test_health_recovery_preserves_manual_pause_and_later_revision(platform,tmp_path,manual_when):
    with participants(platform,tmp_path) as (provider,assistant,host,template,task):
        if manual_when=='before':control(provider,'pause')
        health_policy(provider,'unavailable')
        if manual_when=='after':control(provider,'pause')
        manual=control(provider,'status')['data']['admissionPolicy']
        health_policy(provider,'available',ready=lambda profile:{'ready':True})
        assert control(provider,'status')['data']['admissionPolicy']==manual
        assert manual['accepting'] is False


def test_system_pause_survives_restart_and_only_resumes_original_policy(platform,tmp_path):
    with participants(platform,tmp_path) as (provider,assistant,host,template,task):
        health_policy(provider,'unavailable')
        paused=control(provider,'status')['data']['admissionPolicy']
        original_runtime=provider.store.runtime_id
        provider.close()
        restarted=bound_runtime(RuntimeConfig.load(tmp_path/'provider/config.json'),host,lambda selected:ProviderRole(selected)).start()
        try:
            wait_for(lambda:restarted.modules[0].ready('service'))
            assert restarted.store.runtime_id==original_runtime
            assert health_policy(restarted,'available',ready=lambda profile:{'ready':True})['status']=='resumed'
            current=control(restarted,'status')['data']['admissionPolicy']
            assert current['accepting'] and current['revision']==paused['revision']+1
        finally:restarted.close()


def test_lost_system_pause_response_recovers_original_receipt_before_resuming(platform,tmp_path,monkeypatch):
    with participants(platform,tmp_path) as (provider,assistant,host,template,task):
        original=provider.connection.call;writes=[];lost=[False]
        def boundary(actor,kind,payload,**kwargs):
            result=original(actor,kind,payload,**kwargs)
            if kind=='admission.configure':
                writes.append((kwargs['operation_id'],payload['accepting']))
                if not lost[0]:
                    lost[0]=True
                    raise RuntimeFault('unknown_result','Lost original committed pause response')
            return result
        with monkeypatch.context() as patch:
            patch.setattr(provider.connection,'call',boundary)
            with pytest.raises(RuntimeFault,match='original admission policy operation'):
                health_policy(provider,'unavailable')
            assert health_policy(provider,'available',ready=lambda profile:{'ready':True})['status']=='resumed'
        assert len(writes)==2 and [item[1] for item in writes]==[False,True]


def test_automatic_resume_revision_race_never_rebases_over_user_change(platform,tmp_path,monkeypatch):
    with participants(platform,tmp_path) as (provider,assistant,host,template,task):
        health_policy(provider,'unavailable')
        original=provider.connection.call;prepare=provider.store.prepare_checkpointed;competing=[]
        def boundary(actor,kind,payload,operation,namespace,values):
            if kind=='admission.configure' and payload['accepting']:
                competing.append(original(actor,kind,{**payload,'accepting':False,'maxActiveDelegations':3},operation_id=uid()))
            return prepare(actor,kind,payload,operation,namespace,values)
        with monkeypatch.context() as patch:
            patch.setattr(provider.store,'prepare_checkpointed',boundary)
            with pytest.raises(RuntimeFault) as error:
                health_policy(provider,'available',ready=lambda profile:{'ready':True})
            assert error.value.code=='revision_conflict'
        health_policy(provider,'available',ready=lambda profile:{'ready':True})
        assert len(competing)==1
        assert control(provider,'status')['data']['admissionPolicy']==competing[0]


def test_product_health_sampling_pauses_and_resumes_only_through_the_recovery_fence(platform,tmp_path):
    """Resident P34 sampling -> ProviderRole -> P35 with the product recovery_ready; S02 is a boundary double."""
    with participants(platform,tmp_path) as (provider,assistant,host,template,task):
        role=provider.modules[0];health=provider.health;wait_for(lambda:role.ready('service'))
        health.interval=0;evidence={'state':'unavailable','basis':'Controlled host command is missing'}
        host.check=lambda:dict(evidence)
        def sample():
            health.poll();wait_for(lambda:not health.sampling);return health.poll()
        before=control(provider,'status')['data']
        assert before['systemHealthPause'] is None and before['admissionPolicy']['accepting']
        assert sample()['state']=='unavailable'
        paused=control(provider,'status')['data']
        assert paused['systemHealthPause']=={'state':'paused','reason':'default_host_unavailable'}
        assert not paused['admissionPolicy']['accepting'] and paused['admissionPolicy']['revision']==before['admissionPolicy']['revision']+1
        evidence.update(state='available',basis='Controlled host command is present')
        role.execution.errors['profile:service']={'code':'not_ready','type':'ControlledProfileFault'}
        assert role.execution.recovery_ready('service')=={'ready':False,'missing':['default_host','profile_error']}
        sample()
        assert role.execution.recovery_ready('service')=={'ready':False,'missing':['profile_error']}
        waiting=control(provider,'status')['data']
        assert waiting['systemHealthPause']=={'state':'paused','reason':'restore_original_definition_and_resources'}
        assert waiting['admissionPolicy']==paused['admissionPolicy']
        role.execution.errors.pop('profile:service')
        assert role.execution.recovery_ready('service')=={'ready':True,'missing':[]}
        sample()
        resumed=control(provider,'status')['data']
        assert resumed['systemHealthPause'] is None and resumed['admissionPolicy']['accepting']
        assert resumed['admissionPolicy']['revision']==paused['admissionPolicy']['revision']+1
        assert 'health:service' not in role.execution.errors


def test_publication_expiring_during_system_pause_is_reread_before_automatic_resume(platform,tmp_path):
    """A long pause outlives the locally recorded validity; the renewed platform templates are re-read, then resumed."""
    with participants(platform,tmp_path) as (provider,assistant,host,template,task):
        role=provider.modules[0];health=provider.health;wait_for(lambda:role.ready('service'))
        health.interval=0;evidence={'state':'unavailable','basis':'Controlled host command is missing'}
        host.check=lambda:dict(evidence)
        def sample():
            health.poll();wait_for(lambda:not health.sampling);return health.poll()
        sample();paused=control(provider,'status')['data']['admissionPolicy']
        assert not paused['accepting']
        # The recorded validity has passed; the platform holds a currently valid (renewed) publication.
        role.execution.published['service']=0
        evidence.update(state='available',basis='Controlled host command is present')
        sample()
        assert control(provider,'status')['data']['systemHealthPause']=={'state':'paused','reason':'restore_original_definition_and_resources'}
        wait_for(lambda:role.ready('service'),timeout=5)
        sample()
        resumed=control(provider,'status')['data']
        assert resumed['systemHealthPause'] is None and resumed['admissionPolicy']['accepting']
        assert resumed['admissionPolicy']['revision']==paused['revision']+1
