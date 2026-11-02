"""User-only decision authority, object binding and native-question separation."""
import json
from http.cookies import SimpleCookie
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


def test_two_listeners_share_browser_cookie_jar_without_overwriting_or_cross_approving(tmp_path):
    stores=[];servers=[];managers=[];uis=[];cards=[]
    def missing(operation,oid,args):return {'operationId':oid,'status':'rejected','data':None,'error':{'code':'not_ready'}}
    try:
        for label in ('a','b'):
            home=tmp_path/label;home.mkdir()
            actor='actor-'+label
            store=RuntimeStore(home/'runtime.sqlite','http://local',[actor],owner_id='owner-'+label)
            stores.append(store)
            manager=Confirmations(store);managers.append(manager)
            cards.append(manager.ensure({**binding(),'actor':actor}))
            ui=UserUi(manager);uis.append(ui)
            servers.append(IpcServer(home,'runtime-'+label,missing,user_ui=ui).start())
        a,b=uis
        assert urlsplit(a.origin).hostname==urlsplit(b.origin).hostname
        assert urlsplit(a.origin).port!=urlsplit(b.origin).port
        with httpx.Client(trust_env=False) as browser:
            entries=[];cookies=[];tokens=[]
            for ui in uis:
                token=urlsplit(ui.entry_url()).fragment;tokens.append(token)
                response=browser.post(ui.origin+'/user/session',json={'token':token},headers={'Origin':ui.origin})
                assert response.status_code==200
                parsed=SimpleCookie();parsed.load(response.headers['set-cookie'])
                assert len(parsed)==1
                cookie=next(iter(parsed.values()));cookies.append(cookie)
                assert cookie['httponly'] and cookie['samesite']=='Strict'
                assert cookie['path']=='/user' and cookie['max-age']=='14400'
                entries.append(response.json())
            # A real shared jar used to replace A's cookie with B's cookie here.
            first=browser.get(a.origin+'/user/api')
            assert first.status_code==200,first.text
            second=browser.get(b.origin+'/user/api')
            assert second.status_code==200
            assert [card['reference'] for card in first.json()['confirmations']]==[cards[0]['reference']]
            assert [card['reference'] for card in second.json()['confirmations']]==[cards[1]['reference']]
            assert cookies[0].key!=cookies[1].key
            assert {cookie.name for cookie in browser.cookies.jar}=={cookie.key for cookie in cookies}
            for index,ui in enumerate(uis):
                other=1-index
                body={key:cards[index][key] for key in ('reference','bindingDigest')}
                body['decision']='approve'
                headers={'Origin':ui.origin,'X-Ioa-Csrf':entries[index]['csrf']}
                assert browser.post(ui.origin+'/user/session',json={'token':tokens[index]},headers={'Origin':ui.origin}).status_code==403
                assert browser.post(ui.origin+'/user/decision',json=body,headers={**headers,'Origin':uis[other].origin}).status_code==403
                assert browser.post(ui.origin+'/user/decision',json=body,headers={**headers,'Host':urlsplit(uis[other].origin).netloc}).status_code==403
                assert browser.post(ui.origin+'/user/decision',json=body,headers={**headers,'X-Ioa-Csrf':entries[other]['csrf']}).status_code==403
                assert browser.post(ui.origin+'/user/decision',json=body,headers={'Origin':ui.origin}).status_code==403
                assert browser.post(ui.origin+'/user/decision',json={**body,'bindingDigest':'different'},headers=headers).status_code==409
                assert browser.post(ui.origin+'/user/decision',json={**body,'reference':cards[other]['reference'],'bindingDigest':cards[other]['bindingDigest']},headers=headers).status_code==409
                with httpx.Client(trust_env=False) as foreign:
                    foreign_cookie=cookies[other].key+'='+cookies[other].value
                    assert foreign.get(ui.origin+'/user/api',headers={'Cookie':foreign_cookie}).status_code==403
                    renamed=cookies[index].key+'='+cookies[other].value
                    assert foreign.get(ui.origin+'/user/api',headers={'Cookie':renamed}).status_code==403
                    legacy='ioa_user='+cookies[index].value
                    assert foreign.get(ui.origin+'/user/api',headers={'Cookie':legacy}).status_code==403
                assert managers[index].get(cards[index]['reference'])['status']=='pending'
                assert browser.post(ui.origin+'/user/decision',json=body,headers=headers).json()['status']=='approved'
                assert managers[index].get(cards[index]['reference'])['status']=='approved'
                if index==0:assert managers[other].get(cards[other]['reference'])['status']=='pending'
    finally:
        for server in reversed(servers):server.close()
        for store in reversed(stores):store.close()
