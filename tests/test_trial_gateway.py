"""Real ASGI routes and a real loopback WS peer; no Linux acceptance claim."""
import base64
from contextlib import asynccontextmanager
import hashlib
import hmac
import json
import queue
import threading
from types import SimpleNamespace
import uuid

from fastapi.testclient import TestClient
import httpx
import pytest
from starlette.websockets import WebSocketDisconnect
from websockets.sync.server import serve

from ioalab_trial.coordinator import Coordinator
from ioalab_trial.gateway import create_app
from ioalab_trial.store import Store


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    store=Store(tmp_path/'control.sqlite',deployment_profile='linux-server')
    coordinator=Coordinator(store,audience='https://trial.test',admission_key='k'*40,
                            lease_seconds=100,grace_seconds=20,clock=lambda:1000)
    monkeypatch.setattr(coordinator,'reconcile',lambda sandbox:None)
    sandbox=SimpleNamespace(ui=lambda seat:f'http://127.0.0.1:{19000+seat*2}')
    app=create_app(coordinator,sandbox,confirm_origin='https://confirm.trial.test')
    with TestClient(app,base_url='https://trial.test',raise_server_exceptions=False) as client:
        with store.transaction() as db:
            for seat in range(1,4):
                db.execute("UPDATE seats SET state='ready',epoch=? WHERE id=?",(str(seat)*32,seat))
        def login(owner,audience='https://trial.test',expected=200):
            value={'v':1,'owner':owner,'aud':audience,'iat':1000,'exp':1060,'jti':uuid.uuid4().hex}
            payload=base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=')
            signature=hmac.new(coordinator.key.encode(),payload.encode(),hashlib.sha256).hexdigest()
            response=client.post(audience+'/trial/api/entry',json={'ticket':payload+'.'+signature},headers={'Origin':audience})
            assert response.status_code==expected
            if expected!=200:return response
            return {'Origin':audience,'X-Trial-Csrf':response.json()['csrf']}
        yield client,coordinator,sandbox,login
    store.close()


def test_real_routes_require_origin_csrf_and_session(gateway):
    client,coordinator,_,login=gateway
    assert client.get('/trial/api/status').status_code==403
    assert client.post('/trial/api/entry',json={'ticket':'fake'},headers={'Origin':'https://evil.test'}).status_code==403
    headers=login('alice')
    for invalid in ({}, {'Origin':'https://evil.test','X-Trial-Csrf':headers['X-Trial-Csrf']},
                    {'Origin':'https://trial.test','X-Trial-Csrf':'invented'}):
        assert client.post('/trial/api/join',json={},headers=invalid).status_code==403
    assert coordinator.status('alice')['state']=='idle'
    assert client.post('/trial/api/join',json={},headers=headers).status_code==200


def test_confirmation_origin_is_separate_and_bound_to_current_epoch(gateway):
    client,coordinator,_,login=gateway
    trusted='https://confirm.trial.test'
    assert login('alice',trusted,expected=403).status_code==403
    native=login('alice')
    first=client.post('/trial/api/join',json={},headers=native).json()
    native_cookie=next(cookie.value for cookie in client.cookies.jar if cookie.domain=='trial.test')
    confirmation=login('alice',trusted)
    confirm_cookie=next(cookie.value for cookie in client.cookies.jar if cookie.domain=='confirm.trial.test')
    assert client.get('/trial/api/status').status_code==200  # second audience does not revoke native
    assert client.get('/trial/user/api').status_code==403
    assert client.get('/trial/confirm').status_code==403
    assert client.post('/trial/api/confirmations',json={'method':'list'},headers=native).status_code==403
    assert client.get(trusted+'/files/anything').status_code==403
    assert client.post(trusted+'/trial/api/join',json={},headers=confirmation).status_code==403
    assert client.get(trusted+'/trial/user/api',headers={'Cookie':'__Host-ioa_trial='+native_cookie}).status_code==403
    assert client.get('/trial/api/status',headers={'Host':'unknown.test'}).status_code==403
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect('wss://confirm.trial.test/ws',headers={'Origin':trusted}):pass
    assert client.post('/trial/api/end',json={},headers=native).status_code==200
    with coordinator.store.transaction() as db:
        db.execute("UPDATE seats SET state='ready',owner=NULL,epoch=? WHERE id=?",('e'*32,first['id']))
    fresh=login('alice')
    assert client.post('/trial/api/join',json={},headers=fresh).status_code==200
    assert client.get(trusted+'/trial/user/api',headers={'Cookie':'__Host-ioa_trial='+confirm_cookie}).status_code==403


def test_entry_and_confirmation_inputs_are_bounded(gateway):
    client,_,_,login=gateway
    assert client.post('/trial/api/entry',content=b'x'*8193,headers={'Origin':'https://trial.test'}).status_code==403
    native=login('alice');client.post('/trial/api/join',json={},headers=native)
    confirmation=login('alice','https://confirm.trial.test')
    assert client.post('https://confirm.trial.test/trial/user/decision',content=b'x'*(128*1024+1),headers=confirmation).status_code==403
    assert client.post('https://confirm.trial.test/trial/user/decision',json=[],headers=confirmation).status_code==403


@pytest.mark.parametrize('code',[403,409,503])
def test_sdk_control_rejection_and_unknown_are_distinct(gateway,code):
    client,_,sandbox,login=gateway
    native=login('alice');client.post('/trial/api/join',json={},headers=native)
    trusted='https://confirm.trial.test'
    headers=login('alice',trusted)
    def fail(*args):
        response=httpx.Response(code,request=httpx.Request('POST','http://private-control/confirmations'),
                                json={'error':{'message':'private backend detail'}})
        response.raise_for_status()
    sandbox.control=fail
    response=client.post(trusted+'/trial/user/decision',json={'reference':'original','bindingDigest':'original-digest','decision':'approve'},headers=headers)
    assert response.status_code==code
    assert response.json()['error']['code']==('unknown_result' if code==503 else 'confirmation_rejected')
    assert 'private backend detail' not in response.text


@pytest.mark.parametrize('origin',['https://trial.test','https://trial.test:9999','http://confirm.trial.test'])
def test_confirmation_origin_cannot_share_native_hostname(tmp_path,origin):
    store=Store(tmp_path/'control.sqlite',deployment_profile='linux-server')
    try:
        coordinator=Coordinator(store,audience='https://trial.test',admission_key='k'*40)
        with pytest.raises(ValueError):create_app(coordinator,SimpleNamespace(),confirm_origin=origin)
    finally:store.close()


def test_owner_and_old_cookie_cannot_select_another_seat(gateway,monkeypatch):
    client,coordinator,sandbox,login=gateway
    alice=login('alice')
    original=client.post('/trial/api/join',json={},headers=alice).json()
    cookie=client.cookies.get('__Host-ioa_trial')
    bob=login('bob')
    current=client.post('/trial/api/join',json={},headers=bob).json()
    assert current['id']!=original['id']
    observed=[]
    class Upstream:
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        @asynccontextmanager
        async def stream(self,method,url,**kwargs):
            observed.append((url,kwargs))
            yield httpx.Response(200,content=b'own-seat')
    monkeypatch.setattr('ioalab_trial.gateway.httpx.AsyncClient',lambda **kwargs:Upstream())
    response=client.get('/native/query',params={'seat':original['id'],'epoch':original['epoch'],'owner':'alice'},headers={'Authorization':'Bearer attacker'})
    assert response.content==b'own-seat'
    assert observed[0][0].startswith(sandbox.ui(current['id'])+'/')
    assert 'authorization' not in observed[0][1]['headers']
    coordinator.end('alice')
    client.cookies.clear()
    client.cookies.set('__Host-ioa_trial',cookie)
    assert client.get('/native/query').status_code==403


@pytest.mark.parametrize('location',['https://other.test/file','//other.test/file'])
def test_proxy_rejects_external_redirects_and_keeps_query(gateway,monkeypatch,location):
    client,_,_,login=gateway
    headers=login('alice');client.post('/trial/api/join',json={},headers=headers)
    observed=[]
    class Upstream:
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        @asynccontextmanager
        async def stream(self,method,url,**kwargs):
            observed.append((method,url,kwargs))
            yield httpx.Response(302,headers={'Location':location},content=b'')
    def factory(**kwargs):
        assert kwargs['trust_env'] is False and kwargs['follow_redirects'] is False
        return Upstream()
    monkeypatch.setattr('ioalab_trial.gateway.httpx.AsyncClient',factory)
    assert client.get('/files/download?reference=original').status_code==403
    assert observed[0][2]['params']['reference']=='original'
    assert client.post('/native/action',json={},headers={'Origin':'https://evil.test'}).status_code==403
    assert len(observed)==1


def test_revocation_during_download_discards_buffer(gateway,monkeypatch):
    client,coordinator,_,login=gateway
    headers=login('alice');lease=client.post('/trial/api/join',json={},headers=headers).json()
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'private-first-chunk'
            coordinator.revoke(lease['id'],'test-revoke')
            yield b'private-second-chunk'
    class Upstream:
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        @asynccontextmanager
        async def stream(self,*args,**kwargs):
            yield httpx.Response(200,stream=Stream())
    monkeypatch.setattr('ioalab_trial.gateway.httpx.AsyncClient',lambda **kwargs:Upstream())
    response=client.get('/files/private')
    assert response.status_code==403
    assert b'private-first-chunk' not in response.content
    assert b'private-second-chunk' not in response.content


def test_closed_confirmation_uses_actual_sdk_and_original_digest(gateway,tmp_path):
    from ioalab_sdk.runtime.store import RuntimeStore
    from ioalab_sdk.runtime.confirmations import Confirmations
    from ioalab_sdk.runtime.trusted_user import TrustedUserPort,TrustedUserServer
    client,coordinator,sandbox,login=gateway
    headers=login('alice');lease=client.post('/trial/api/join',json={},headers=headers).json()
    headers=login('alice','https://confirm.trial.test')
    trusted='https://confirm.trial.test'
    store=RuntimeStore(tmp_path/'sdk.sqlite','http://local',['actor'],owner_id='trial')
    manager=Confirmations(store,clock=lambda:1000)
    binding={'actor':'actor','operationId':'question','subject':{'contractRef':'original'},
             'stage':'step','action':'native.answer','preview':{'title':'原合同环节'}}
    pending=manager.ensure(binding,kind='question',questions=[{'question':'允许这个环节？',
        'allow_custom_input':False,'options':[{'value':'allow','label':'允许'},{'value':'deny','label':'拒绝'}]}])
    port=TrustedUserPort(manager,actor='actor',generation=lease['epoch'],authorize=lambda context,generation:True)
    server=TrustedUserServer(port,key='sdk-transport-key-'+'x'*32).start()
    sent=[]
    def control(seat,epoch,method,body):
        sent.append(dict(body))
        assert seat==lease['id'] and epoch==lease['epoch'] and method=='confirmations'
        response=httpx.post(server.address,json=body,headers={'Authorization':'Bearer '+'sdk-transport-key-'+'x'*32},trust_env=False,timeout=3)
        response.raise_for_status()
        return response.json()
    sandbox.control=control
    try:
        rendered=client.get(trusted+'/trial/confirm')
        assert rendered.status_code==200
        assert '/trial/user/api' in rendered.text and '/trial/user/decision' in rendered.text
        assert "api('/user/session'" not in rendered.text
        assert "frame-ancestors 'none'" in rendered.headers['content-security-policy']
        assert client.get(trusted+'/trial/user/api').json()['confirmations'][0]['reference']==pending['reference']
        base={'reference':pending['reference'],'bindingDigest':pending['bindingDigest'],'decision':'answer'}
        for invalid in ([{'selected_options':['allow'],'custom_input':'extra permission'}],
                        [{'selected_options':['invented'],'custom_input':''}]):
            assert client.post(trusted+'/trial/user/decision',json={**base,'answers':invalid},headers=headers).status_code==409
            assert manager.get(pending['reference'])['status']=='pending'
        assert client.post(trusted+'/trial/user/decision',json={**base,'bindingDigest':'changed','answers':[{'selected_options':['allow']}]},headers=headers).status_code==409
        response=client.post(trusted+'/trial/user/decision',json={**base,'answers':[{'selected_options':['deny'],'custom_input':''}]},headers=headers)
        assert response.status_code==200 and response.json()['status']=='answered'
        assert manager.native_answer(pending['reference'])[0]['selected_options']==['deny']
        assert sent[-1]['reference']==pending['reference'] and sent[-1]['bindingDigest']==pending['bindingDigest']
        assert sent[-1]['generation']==lease['epoch'] and sent[-1]['eventId']
        assert client.post(trusted+'/trial/user/decision',json={**base,'actor':'other'},headers=headers).status_code==403
    finally:
        server.close();store.close()


def test_ws_rejects_cross_origin_and_revokes_active_connection(gateway):
    client,coordinator,sandbox,login=gateway
    headers=login('alice');client.post('/trial/api/join',json={},headers=headers)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect('wss://trial.test/ws',headers={'Origin':'https://evil.test'}):pass
    released=threading.Event()
    def peer(socket):
        socket.send('{"ready":true}')
        released.wait(3)
    with serve(peer,'127.0.0.1',0) as server:
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        sandbox.ui=lambda seat:'http://127.0.0.1:'+str(server.socket.getsockname()[1])
        try:
            with client.websocket_connect('wss://trial.test/ws?user_id=trial',headers={'Origin':'https://trial.test'}) as socket:
                assert socket.receive_json()=={'ready':True}
                assert client.post('/trial/api/end',json={},headers=headers).status_code==200
                outcome=queue.Queue()
                def receive():
                    try:outcome.put(socket.receive())
                    except Exception as exc:outcome.put(exc)
                reader=threading.Thread(target=receive,daemon=True);reader.start()
                message=outcome.get(timeout=2)
                assert isinstance(message,dict) and message['type']=='websocket.close' and message['code']==4403
        finally:
            released.set();server.shutdown();thread.join(timeout=3)
