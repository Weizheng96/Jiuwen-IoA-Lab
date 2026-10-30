"""Public HTTP/WS onboarding, durable authentication and independent role identity."""
import json
from concurrent.futures import ThreadPoolExecutor
import time
import uuid

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from a2c.jws import Signer
from a2c.payment.ap2 import EcKey
from ioalab_sdk import LabClient
from ioalab_sdk.messaging import MailboxClient
from ioalab_sdk.runtime.session import AccountSession
from ioalab_sdk.runtime.identity import IdentityClient
from ioalab_sdk.runtime import AgentConnection,RuntimeStore,RuntimeFault
from _support import new_user,PASSWORD,username


def uid():return str(uuid.uuid4())


def login_body(name):
    return {'username':name,'password':PASSWORD,'operationId':uid()}


def test_registration_and_login_replay_share_one_credit(platform,client):
    body=login_body(username())
    with ThreadPoolExecutor(max_workers=3) as pool:
        results=list(pool.map(lambda _:client.post('/api/auth/register',json=body),range(3)))
    assert all(result.status_code==200 for result in results)
    first=results[0].json()
    assert all(result.json()==first for result in results)
    assert platform.wallets.balance(first['id'])==100
    attempt={**body,'operationId':uid()}
    with ThreadPoolExecutor(max_workers=3) as pool:
        results=list(pool.map(lambda _:client.post('/api/v1/token',json=attempt),range(3)))
    assert all(result.status_code==200 and result.json()==results[0].json() for result in results)
    assert platform.wallets.balance(first['id'])==200
    assert platform.auth.verify(results[0].json()['token'])=='assistant:'+first['id']
    assert platform.auth.verify(first['token'])==first['id']
    assert client.post('/api/auth/login',json=attempt).status_code==409
    row=platform.db.one('SELECT * FROM account_operations WHERE username=? AND kind=? AND id=?',body['username'],'login',attempt['operationId'])
    assert PASSWORD not in json.dumps(row) and 'token' not in row['result']


def test_account_credit_failure_rolls_back_account_and_invite(platform,client):
    invite=platform.auth.new_invite()
    previous=platform.s.registration
    platform.s.registration='invite'
    body={**login_body(username()),'invite':invite}
    platform.db.run("CREATE TRIGGER account_record_failure BEFORE INSERT ON account_operations BEGIN SELECT RAISE(ABORT,'account commit failed'); END")
    try:
        response=client.post('/api/auth/register',json=body)
        assert response.status_code==500
        assert platform.db.one('SELECT 1 FROM users WHERE username=?',body['username']) is None
        assert platform.db.one('SELECT used_by FROM invites WHERE code=?',invite)['used_by'] is None
    finally:
        platform.db.run('DROP TRIGGER account_record_failure')
        platform.s.registration=previous
    result=client.post('/api/auth/register',json=body).raise_for_status().json()
    assert platform.wallets.balance(result['id'])==100


def test_changed_parameters_and_credentials_cannot_reuse_login(platform,client):
    user=new_user(platform)
    body=login_body(user['username'])
    assert client.post('/api/v1/token',json=body).status_code==200
    assert client.post('/api/v1/token',json={**body,'password':'different'}).status_code==409
    old=platform.db.one('SELECT pw_hash FROM users WHERE id=?',user['id'])['pw_hash']
    platform.db.run('UPDATE users SET pw_hash=? WHERE id=?','changed-private-password-record',user['id'])
    try:
        assert client.post('/api/v1/token',json=body).status_code==401
    finally:
        platform.db.run('UPDATE users SET pw_hash=? WHERE id=?',old,user['id'])
    assert platform.wallets.balance(user['id'])==100


def test_shared_roles_and_refresh_do_not_log_in_again(platform,tmp_path):
    user=new_user(platform)
    home=tmp_path/'shared'
    first=LabClient(platform.s.base_url,user['username'],PASSWORD,home)
    first.close()
    box=MailboxClient(platform.s.base_url,user['username'],PASSWORD,home)
    box.close()
    session=AccountSession(platform.s.base_url,user['username'],PASSWORD,home)
    try:
        before=platform.wallets.balance(user['id'])
        count=platform.db.one('SELECT COUNT(*) AS n FROM account_operations WHERE owner=?',user['id'])['n']
        session.refresh()
        assert platform.wallets.balance(user['id'])==before==100
        assert platform.db.one('SELECT COUNT(*) AS n FROM account_operations WHERE owner=?',user['id'])['n']==count
        assert 'token' not in session.status() and PASSWORD not in json.dumps(session.status())
    finally:session.close()


def test_concurrent_account_caches_reuse_the_same_pending_login(platform,tmp_path):
    user=new_user(platform)
    def connect(_):
        session=AccountSession(platform.s.base_url,user['username'],PASSWORD,tmp_path)
        try:return session.account['operationId']
        finally:session.close()
    with ThreadPoolExecutor(max_workers=4) as pool:
        operations=list(pool.map(connect,range(4)))
    assert len(set(operations))==1
    assert platform.wallets.balance(user['id'])==100


def test_lost_login_response_recovers_original_credit(platform,tmp_path,monkeypatch):
    user=new_user(platform)
    original=httpx.Client.post
    operations=[]
    def drop(http,url,*args,**kwargs):
        result=original(http,url,*args,**kwargs)
        if str(url).endswith('/api/v1/token'):
            operations.append(kwargs['json']['operationId'])
            raise httpx.ReadTimeout('Login response lost after commit')
        return result
    with monkeypatch.context() as patch:
        patch.setattr(httpx.Client,'post',drop)
        with pytest.raises(httpx.ReadTimeout):AccountSession(platform.s.base_url,user['username'],PASSWORD,tmp_path)
    session=AccountSession(platform.s.base_url,user['username'],PASSWORD,tmp_path,connect=False)
    try:
        with pytest.raises(RuntimeFault,match='pending login'):session.login(new_operation=True)
        session.login()
        assert session.account['operationId']==operations[0]
        assert platform.wallets.balance(user['id'])==100
    finally:session.close()


def test_expired_cache_needs_explicit_login_without_blocking_reauthentication(platform,tmp_path):
    user=new_user(platform)
    session=AccountSession(platform.s.base_url,user['username'],PASSWORD,tmp_path)
    session.account.update(token=platform.auth.token('assistant:'+user['id'],expires_at=time.time()-1),
                           expiresAt='2000-01-01T00:00:00Z')
    session.cache.put('session','current',session.account)
    session.close()
    with pytest.raises(RuntimeFault,match='explicitly log in'):
        AccountSession(platform.s.base_url,user['username'],PASSWORD,tmp_path)
    assert platform.wallets.balance(user['id'])==100
    session=AccountSession(platform.s.base_url,user['username'],PASSWORD,tmp_path,connect=False)
    try:
        assert session.status()['authenticated'] is False
        assert session.login(new_operation=True)['authenticated']
        assert platform.wallets.balance(user['id'])==200
    finally:session.close()


def service(session,*,aid=None,payee=None):
    aid=aid or 'svc-'+uid()
    signer=Signer(IdentityClient(session).protocol_id('service',aid),Ed25519PrivateKey.generate(),'agent')
    payee=payee or EcKey('payee')
    return aid,signer,{'agent':signer.jwk,'payee':payee.jwk}


def bind(session,parts):
    aid,signer,keys=parts
    try:
        return IdentityClient(session).enroll('service',aid,signer,keys,name='用户服务',source='provider')
    except httpx.HTTPStatusError as exc:
        exc.add_note(exc.response.text)
        raise


@pytest.fixture
def service_account(platform,tmp_path):
    user=new_user(platform)
    session=AccountSession(platform.s.base_url,user['username'],PASSWORD,tmp_path/'account')
    yield user,session
    session.close()


def test_independent_service_binds_public_keys_and_connects_without_assistant(platform,service_account,tmp_path):
    user,session=service_account
    parts=service(session)
    result=bind(session,parts)
    assert result['role']=='service'
    assert platform.db.one("SELECT 1 FROM agents WHERE owner_id=? AND role='assistant'",user['id']) is None
    response=session.http.get(result['protocolId']+'/.well-known/jwks.json').raise_for_status().json()
    assert response=={'keys':[parts[1].jwk]}
    assert session.http.get(result['protocolId']+'/.well-known/agent-card.json').status_code==409
    catalog=session.http.get(session.base+'/api/catalog',headers={'Authorization':'Bearer '+session.token}).raise_for_status().json()
    assert all(row['id']!=result['agentId'] for row in catalog)
    store=RuntimeStore(tmp_path/'runtime.sqlite',session.base,[result['agentId']],owner_id=user['id'])
    try:
        with AgentConnection(session.base,session.bearer,[result['agentId']],store) as connection:
            assert connection.connected
    finally:store.close()
    assert platform.wallets.balance(user['id'])==100


def test_enrollment_lost_response_and_expiry_restore_original_binding(platform,service_account,monkeypatch):
    _,session=service_account
    parts=service(session)
    aid=parts[0]
    original=session.api
    seen=[]
    def drop(method,path,body=None):
        result=original(method,path,body)
        if path=='/identity':
            assert session.cache.get('enrollments','service|'+aid)['body']==body
            seen.append(body['operationId'])
            raise httpx.ReadTimeout('response lost after commit')
        return result
    with monkeypatch.context() as patch:
        patch.setattr(session,'api',drop)
        with pytest.raises(httpx.ReadTimeout):bind(session,parts)
    platform.db.run('UPDATE identity_enrollments SET expires=? WHERE owner=? AND operation=?',time.time()-1,session.account['id'],seen[0])
    result=bind(session,parts)
    assert result['agentId']==aid
    assert platform.db.one('SELECT COUNT(*) AS n FROM agents WHERE id=?',aid)['n']==1


def test_payer_binding_failure_rolls_back_identity_and_challenge_consumption(platform,service_account,tmp_path):
    from a2c.integrations.jiuwenswarm.mcp_server import load_identity
    user,session=service_account
    agent,human,payer=load_identity(tmp_path/'assistant')
    keys={'agent':agent.jwk,'user':human.jwk,'payer':payer.jwk}
    def enroll():
        return IdentityClient(session).enroll('assistant',agent.id,agent,keys,name='本地助手',source='client')
    platform.db.run("CREATE TRIGGER payer_binding_failure BEFORE UPDATE OF payer_thumbprint ON wallets BEGIN SELECT RAISE(ABORT,'payer binding failed'); END")
    try:
        with pytest.raises(httpx.HTTPStatusError) as failure:enroll()
        assert failure.value.response.status_code==500
        assert platform.db.one('SELECT 1 FROM agents WHERE id=?',agent.id) is None
        assert platform.db.one('SELECT result FROM identity_enrollments WHERE actor=?',agent.id)['result'] is None
        assert platform.db.one('SELECT payer_thumbprint FROM wallets WHERE user_id=?',user['id'])['payer_thumbprint'] is None
    finally:platform.db.run('DROP TRIGGER payer_binding_failure')
    assert enroll()['agentId']==agent.id


def test_foreign_public_key_and_private_key_fields_are_rejected(platform,service_account,tmp_path):
    _,first=service_account
    shared=EcKey('payee')
    original=service(first,payee=shared)
    bind(first,original)
    stranger=new_user(platform)
    other=AccountSession(platform.s.base_url,stranger['username'],PASSWORD,tmp_path/'stranger')
    try:
        with pytest.raises(httpx.HTTPStatusError) as failure:bind(other,service(other,payee=shared))
        assert failure.value.response.status_code==409
        aid,signer,keys=service(other)
        keys['payee']={**keys['payee'],'d':'must-not-be-accepted'}
        with pytest.raises(httpx.HTTPStatusError) as failure:bind(other,(aid,signer,keys))
        assert failure.value.response.status_code==400
        assert platform.db.one('SELECT 1 FROM agents WHERE id=?',aid) is None
    finally:other.close()


def test_valid_bearer_required_for_refresh_and_enrollment(platform,client):
    user=new_user(platform)
    response=client.post('/api/auth/login',json=login_body(user['username']))
    assert response.status_code==200
    assert client.post('/api/v1/token/refresh',json={}).status_code==401
    assert client.post('/api/v1/identity/challenge',json={'operationId':uid(),'role':'service','agentId':'svc-'+uid()}).status_code==401
