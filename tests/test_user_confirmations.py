"""User-only decision authority, object binding and native-question separation."""
import json
from urllib.parse import urlsplit
import uuid

import httpx
import pytest

from ioalab_sdk.runtime import RuntimeStore,RuntimeFault
from ioalab_sdk.runtime.confirmations import Confirmations
from ioalab_sdk.runtime.ipc import IpcServer,LocalClient
from ioalab_sdk.runtime.user_ui import UserUi


@pytest.fixture
def manager(tmp_path):
    store=RuntimeStore(tmp_path/'runtime.sqlite','http://local',['actor'],owner_id='owner')
    clock=[1000.0]
    manager=Confirmations(store,clock=lambda:clock[0])
    yield manager,clock
    store.close()


def binding():
    return {'actor':'actor','operationId':str(uuid.uuid4()),'subject':{'digest':'sha256:'+'a'*64},
            'stage':'rules.publish','action':'interface.publish','preview':{'title':'发布本地收信接口','tools':[]}}


def approve(manager,value):
    return manager.decide(value['reference'],value['bindingDigest'],'approve',authority=manager.user_channel(),source='isolated-user-event')


def test_confirmation_is_immutable_and_bound_to_actual_preview_and_operation(manager):
    manager,clock=manager
    actual=binding()
    value=manager.ensure(actual,ttl=30)
    clock[0]+=1
    assert manager.ensure(actual,ttl=30)==value
    with pytest.raises(RuntimeFault,match='changed'):manager.ensure({**actual,'preview':{'title':'隐藏原动作'}},ttl=30)
    with pytest.raises(RuntimeFault,match='trusted user'):manager.decide(value['reference'],value['bindingDigest'],'approve',authority='model-confirmed',source='model')
    assert approve(manager,value)['status']=='approved'
    with pytest.raises(RuntimeFault,match='actual object'):manager.claim(value['reference'],{**actual,'operationId':'another'})
    manager.claim(value['reference'],actual)
    manager.claim(value['reference'],actual)
    with pytest.raises(RuntimeFault,match='final'):manager.decide(value['reference'],value['bindingDigest'],'reject',authority=manager.user_channel(),source='other-event')
    clock[0]+=31
    assert manager.get(value['reference'])['status']=='expired'
    with pytest.raises(RuntimeFault,match='expired'):manager.claim(value['reference'],actual)
    assert manager.user_channel() not in json.dumps(manager.get(value['reference'],detail=True))


def test_native_answer_cannot_approve_an_sdk_action(manager):
    manager,_=manager
    actual=binding()
    actual.update(stage='host.interaction',action='native.answer')
    value=manager.ensure(actual,kind='question',questions=[{'question':'请选择','options':[{'value':'yes','label':'是'}]}])
    with pytest.raises(RuntimeFault,match='nonempty'):
        manager.decide(value['reference'],value['bindingDigest'],'answer',authority=manager.user_channel(),source='user',answers=[{'selected_options':[],'custom_input':'  '}])
    with pytest.raises(RuntimeFault,match='displayed options'):
        manager.decide(value['reference'],value['bindingDigest'],'answer',authority=manager.user_channel(),source='user',answers=[{'selected_options':['invented']}])
    answer=[{'selected_options':['yes'],'custom_input':'真实用户答复'}]
    manager.decide(value['reference'],value['bindingDigest'],'answer',authority=manager.user_channel(),source='user',answers=answer)
    assert manager.native_answer(value['reference'])==answer
    with pytest.raises(RuntimeFault,match='actual object'):manager.claim(value['reference'],actual)


def test_browser_channel_is_separate_from_rpc_token_and_requires_origin_csrf(manager,tmp_path):
    manager,_=manager
    value=manager.ensure(binding())
    ui=UserUi(manager)
    def missing(operation,oid,args):return {'operationId':oid,'status':'rejected','data':None,'error':{'code':'not_ready'}}
    server=IpcServer(tmp_path,'test-runtime',missing,user_ui=ui).start()
    try:
        url=ui.entry_url()
        token=urlsplit(url).fragment
        body={'reference':value['reference'],'bindingDigest':value['bindingDigest'],'decision':'approve'}
        with httpx.Client(base_url=ui.origin,trust_env=False) as browser:
            html=browser.get('/user')
            assert html.status_code==200 and 'frame-ancestors' in html.headers['content-security-policy']
            # Possession of the script token grants no browser approval authority.
            assert browser.post('/user/decision',json=body,headers={'Origin':ui.origin,'Authorization':'Bearer '+server.token}).status_code==403
            entered=browser.post('/user/session',json={'token':token},headers={'Origin':ui.origin})
            assert entered.status_code==200
            assert 'HttpOnly' in entered.headers['set-cookie'] and 'SameSite=Strict' in entered.headers['set-cookie']
            csrf=entered.json()['csrf']
            assert browser.post('/user/session',json={'token':token},headers={'Origin':ui.origin}).status_code==403
            assert browser.post('/user/decision',json=body,headers={'Origin':'http://attacker.invalid','X-Ioa-Csrf':csrf}).status_code==403
            assert browser.post('/user/decision',json=body,headers={'Origin':ui.origin}).status_code==403
            headers={'Origin':ui.origin,'X-Ioa-Csrf':csrf}
            assert browser.post('/user/decision',json={**body,'bindingDigest':'altered'},headers=headers).status_code==409
            assert browser.post('/user/decision',json=body,headers=headers).json()['status']=='approved'
            assert manager.get(value['reference'])['source'].startswith('local_user_ui:')
            assert browser.post('/user/decision',json={**body,'decision':'reject'},headers=headers).status_code==409
        result=LocalClient(tmp_path).call('confirmations.answer',{'confirmed':True})
        assert result['status']=='rejected'
        assert token not in json.dumps(result) and ui._authority not in json.dumps(server.descriptor)
    finally:server.close()
