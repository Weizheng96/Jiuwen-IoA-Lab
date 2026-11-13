"""Device authorization through public HTTP and authenticated WebSocket boundaries."""
import uuid
import json
from contextlib import closing
import pytest
from websockets.sync.client import connect

from _support import new_user, PASSWORD
from test_agent_delivery import people, Peer


def login_device(client, user, *, device=None):
    body = {'username': user['username'], 'password': PASSWORD, 'operationId': str(uuid.uuid4()),
            'deviceId': device or str(uuid.uuid4()), 'deviceName': '隔离测试客户端'}
    response = client.post('/api/v1/token', json=body)
    response.raise_for_status()
    return response.json(), body


def headers(session):
    return {'Authorization': 'Bearer ' + session['token']}


def test_device_rotation_replay_revoke_and_owner_boundary(platform, client):
    user = new_user(platform)
    first, body = login_device(client, user)
    assert first['sessionId'] and first['refreshToken'] and first['deviceId'] == body['deviceId']
    assert client.post('/api/v1/token', json=body).json() == first
    before = platform.wallets.balance(user['id'])
    request = {'refreshToken': first['refreshToken'], 'operationId': str(uuid.uuid4())}
    renewed = client.post('/api/v1/token/refresh', json=request).raise_for_status().json()
    assert renewed['refreshToken'] != first['refreshToken']
    assert renewed['sessionId'] == first['sessionId'] and renewed['credited'] is False
    assert client.post('/api/v1/token/refresh', json=request).json() == renewed
    assert client.post('/api/v1/token/refresh', json={**request, 'operationId': str(uuid.uuid4())}).status_code == 401
    assert platform.wallets.balance(user['id']) == before
    second, _ = login_device(client, user)
    devices = client.get('/api/v1/sessions', headers=headers(second)).raise_for_status().json()['sessions']
    assert {s['sessionId'] for s in devices} == {first['sessionId'], second['sessionId']}
    assert all('token' not in s and 'refreshToken' not in s for s in devices)
    other, _ = login_device(client, new_user(platform))
    endpoint = '/api/v1/sessions/' + first['sessionId'] + '/revoke'
    assert client.post(endpoint, headers=headers(other), json={}).status_code == 403
    assert client.post(endpoint, headers=headers(second), json={}).status_code == 200
    assert client.post(endpoint, headers=headers(second), json={}).status_code == 200
    assert client.get('/api/v1/me', headers=headers(first)).status_code == 401
    assert client.get('/api/v1/me', headers=headers(renewed)).status_code == 401
    assert client.post('/api/v1/token/refresh', json=request).status_code == 401
    assert client.post('/api/v1/token', json=body).status_code == 401
    assert client.get('/api/v1/me', headers=headers(second)).status_code == 200


def test_revocation_invalidates_unused_ticket_and_live_socket(client, people):
    user = people[0]
    session, _ = login_device(client, user)
    controller, _ = login_device(client, user)
    person = {**user, 'token': session['token']}
    ticket = client.post('/api/v1/connections/ticket', headers=headers(session),
                         json={'agentIds': [user['actor']]}).raise_for_status().json()['ticket']
    with closing(Peer(client, person)) as peer:
        client.post('/api/v1/sessions/' + session['sessionId'] + '/revoke',
                    headers=headers(controller), json={}).raise_for_status()
        error = peer.receive('connection.error', timeout=3)
        assert error['error']['code'] == 'authenticate'
    url = str(client.base_url).rstrip('/').replace('http','ws',1) + '/ws/agents'
    with connect(url, proxy=None, open_timeout=5, close_timeout=1) as ws:
        ws.send(json.dumps({'type':'connection.auth','ticket':ticket,'runtimeId':str(uuid.uuid4()),'cursors':{}}))
        assert json.loads(ws.recv(timeout=3))['type'] == 'connection.error'


def test_device_absolute_expiry_password_change_and_same_installation_relogin(platform,client,monkeypatch):
    import time
    from ioalab.identity.devices import DeviceSessions
    clock=[time.time()]
    sessions=DeviceSessions(platform.db,platform.auth,clock=lambda:clock[0])
    monkeypatch.setattr(type(platform.auth),'devices',property(lambda _:sessions))
    user=new_user(platform)
    first,body=login_device(client,user)
    clock[0]+=29*86400
    rotated=client.post('/api/v1/token/refresh',json={'refreshToken':first['refreshToken'],'operationId':str(uuid.uuid4())}).raise_for_status().json()
    assert rotated['sessionExpiresAt']==first['sessionExpiresAt']
    clock[0]+=2*86400
    assert client.get('/api/v1/me',headers=headers(rotated)).status_code==401
    assert client.post('/api/v1/token/refresh',json={'refreshToken':rotated['refreshToken'],'operationId':str(uuid.uuid4())}).status_code==401
    clock[0]=time.time()
    second,_=login_device(client,user,device=body['deviceId'])
    replacement,_=login_device(client,user,device=body['deviceId'])
    assert client.get('/api/v1/me',headers=headers(second)).status_code==401
    assert client.get('/api/v1/me',headers=headers(replacement)).status_code==200
    with platform.db.tx() as conn:
        assert platform.auth.reset_default_password_in(conn,user['username'],PASSWORD,'replacement-test-only-password')
    assert client.get('/api/v1/me',headers=headers(replacement)).status_code==401
    assert client.post('/api/v1/token/refresh',json={'refreshToken':replacement['refreshToken'],'operationId':str(uuid.uuid4())}).status_code==401


def test_legacy_sdk_registration_upgrades_same_receipt_without_new_credit(platform,client):
    import json
    from _support import username,PASSWORD
    body={'username':username(),'password':PASSWORD,'operationId':str(uuid.uuid4())}
    original=client.post('/api/v1/accounts',json=body).raise_for_status().json()
    balance=platform.wallets.balance(original['id'])
    row=platform.db.one('SELECT result FROM account_operations WHERE username=? AND kind=? AND id=?',body['username'],'register',body['operationId'])
    legacy=json.loads(row['result']);legacy.pop('sessionId')
    platform.db.run('UPDATE account_operations SET result=? WHERE username=? AND kind=? AND id=?',json.dumps(legacy),body['username'],'register',body['operationId'])
    restored=client.post('/api/v1/accounts',json=body).raise_for_status().json()
    assert restored['id']==original['id'] and restored['operationId']==body['operationId']
    assert restored['deviceId']==body['operationId'] and restored['refreshToken']
    assert platform.wallets.balance(original['id'])==balance
    assert client.post('/api/v1/accounts',json=body).json()==restored
    assert client.post('/api/v1/accounts',json={**body,'password':'different-password'}).status_code==409
