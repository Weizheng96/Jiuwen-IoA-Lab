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
        restarted=ResidentRuntime(RuntimeConfig.load(path),modules=(role,),host_status=host.capabilities).start()
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
        original=provider.connection.call;update=provider.store.checkpoint_update
        with monkeypatch.context() as patch:
            if failure=='lost_response':
                def lost(actor,kind,payload,**kwargs):
                    result=original(actor,kind,payload,**kwargs)
                    if kind=='admission.configure':raise RuntimeFault('unknown_result','Test lost committed policy reply')
                    return result
                patch.setattr(provider.connection,'call',lost)
            else:
                def lost_commit(namespace,values,**kwargs):
                    if namespace.startswith('provider-policy:') and 'configuration' in values:raise OSError('Test crash before atomic local completion')
                    return update(namespace,values,**kwargs)
                patch.setattr(provider.store,'checkpoint_update',lost_commit)
            if failure=='local_commit':
                with pytest.raises(RuntimeFault) as error:control(provider,'pause',operation)
                assert error.value.code=='unknown_result'
            else:
                assert control(provider,'pause',operation)['status']=='unknown'
                assert control(provider,'resume')['status']=='unknown'
            identity=provider.identities[0]
            with platform.db.lock:actual=platform.admissions.policy(platform.db.conn,identity.agent_id)
            assert not actual['accepting'] and actual['revision']==before['revision']+1
            assert provider.store.checkpoint('provider-policy:'+identity.agent_id,'pending')
        provider.close();role=ProviderRole(host)
        restarted=ResidentRuntime(RuntimeConfig.load(tmp_path/'provider/config.json'),modules=(role,),host_status=host.capabilities).start()
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
        original=provider.connection.call;competing=[]
        def race(actor,kind,payload,**kwargs):
            if kind=='admission.configure':
                competing.append(original(actor,kind,{**payload,'maxActiveDelegations':3},operation_id=uid()))
            return original(actor,kind,payload,**kwargs)
        operation=uid()
        with monkeypatch.context() as patch:
            patch.setattr(provider.connection,'call',race)
            result=control(provider,'resume',operation)
        assert result['status']=='rejected' and result['error']['code']=='revision_conflict'
        assert control(provider,'resume',operation)['error']['code']=='revision_conflict'
        assert len(competing)==1 and control(provider,'status')['data']['admissionPolicy']==competing[0]


def test_pause_preserves_preexisting_held_fee_commit_but_rejects_a_new_reservation(platform,client,local_client,service):
    local=local_client;template=offer(platform,service);value=intent(platform,client,local,service,template)
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
