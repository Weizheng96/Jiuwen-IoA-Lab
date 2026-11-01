"""Explicit login owns one account writer, preserves unknown results and never reads keys."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

import httpx
import pytest

from conftest import wait_for
from ioalab_sdk.lease import Lease
from ioalab_sdk.runtime.authentication import login
from ioalab_sdk.runtime.config import RuntimeConfig
from ioalab_sdk.runtime.files import private_json,read_json
from ioalab_sdk.runtime.ipc import LocalClient
from ioalab_sdk.runtime.protocol import RuntimeFault
from ioalab_sdk.runtime.resident import ResidentRuntime
from ioalab_sdk.runtime.session import AccountSession
from test_access_products import bootstrap
from test_resident_runtime import configuration
from _support import PASSWORD


def session_at(config):
    value=RuntimeConfig.load(config)
    return AccountSession(value.platform,value.username,value.password,value.home,connect=False)


def expired(platform,config):
    account=session_at(config)
    try:
        account.account.update(token=platform.auth.token('assistant:'+account.account['id'],expires_at=time.time()-10),expiresAt='2000-01-01T00:00:00Z')
        account.cache.put('session','current',account.account)
    finally:account.close()


def test_short_controller_only_authenticates_and_reuses_valid_credit(platform,tmp_path,monkeypatch):
    config,user=configuration(platform,tmp_path,service=False)
    import ioalab_sdk.runtime.identities as identities
    monkeypatch.setattr(identities,'load_identity',lambda *args:pytest.fail('login loaded a role key'))
    first=login(config);second=login(config)
    assert first['data']['newLogin'] is True and second['data']['newLogin'] is False
    assert first['data']['operationId']==second['data']['operationId']
    assert platform.wallets.balance(user['id'])==100
    assert not (tmp_path/'state/identities').exists() and not (tmp_path/'state/runtime.sqlite').exists()
    assert PASSWORD not in json.dumps(first) and 'token' not in json.dumps(first)
    with Lease(tmp_path/'state/client.lock',timeout=0):pass


def test_expired_credentials_recover_without_touching_keys_or_business(platform,tmp_path,monkeypatch):
    config,user=configuration(platform,tmp_path)
    runtime=ResidentRuntime(RuntimeConfig.load(config));runtime.close()
    home=tmp_path/'state'
    retained={path:path.read_bytes() for path in home.rglob('*') if path.is_file() and
              (path.is_relative_to(home/'identities') or path.name in ('runtime.sqlite','identity-index.json'))}
    expired(platform,config)
    from ioalab_sdk.runtime.store import RuntimeStore
    monkeypatch.setattr(RuntimeStore,'__init__',lambda *args,**kwargs:pytest.fail('login opened business state'))
    result=login(config)
    assert result['data']['authenticated'] and result['data']['newLogin']
    assert login(config)['data']['newLogin'] is False
    assert platform.wallets.balance(user['id'])==200
    assert all(path.read_bytes()==value for path,value in retained.items())


def test_live_owner_prevents_even_account_network_calls(platform,tmp_path,monkeypatch):
    config,_=configuration(platform,tmp_path)
    runtime=ResidentRuntime(RuntimeConfig.load(config))
    before=(runtime.config.home/'account.sqlite').read_bytes()
    monkeypatch.setattr(httpx.Client,'get',lambda *args,**kwargs:pytest.fail('competing login queried platform'))
    try:
        with pytest.raises(RuntimeFault) as error:login(config)
        assert error.value.recovery=='runtime.stop'
        assert (runtime.config.home/'account.sqlite').read_bytes()==before
    finally:runtime.close()


def test_lost_response_recovers_same_operation_without_second_credit(platform,tmp_path,monkeypatch):
    config,user=configuration(platform,tmp_path,service=False)
    original=httpx.Client.post;sent=[]
    def lose(client,url,*args,**kwargs):
        response=original(client,url,*args,**kwargs)
        if str(url).endswith('/api/v1/token'):
            sent.append(kwargs['json']['operationId']);raise httpx.ReadTimeout('isolated response loss')
        return response
    with monkeypatch.context() as patch:
        patch.setattr(httpx.Client,'post',lose)
        with pytest.raises(RuntimeFault) as error:login(config)
        assert error.value.code=='result_unknown' and error.value.operation_id==sent[0]
    assert platform.wallets.balance(user['id'])==100
    restored=login(config)
    assert restored['data']['operationId']==sent[0] and restored['data']['newLogin'] is False
    assert platform.wallets.balance(user['id'])==100


@pytest.mark.parametrize('status',[409,429,500])
def test_unverified_http_result_stays_pending_and_keeps_original_input(platform,tmp_path,monkeypatch,status):
    config,user=configuration(platform,tmp_path,service=False);sent=[]
    def fail(client,url,*args,**kwargs):
        sent.append(kwargs['json']['operationId'])
        return httpx.Response(status,json={'error':{'code':'id_conflict' if status==409 else 'unavailable'}},request=httpx.Request('POST',url))
    with monkeypatch.context() as patch:
        patch.setattr(httpx.Client,'post',fail)
        for _ in range(2):
            with pytest.raises(RuntimeFault) as error:login(config)
            assert error.value.code=='result_unknown'
    assert len(sent)==2 and sent[0]==sent[1] and platform.wallets.balance(user['id'])==0
    restored=login(config)
    assert restored['data']['operationId']==sent[0] and not restored['data']['newLogin']
    assert platform.wallets.balance(user['id'])==100


def test_changed_pending_credentials_never_reach_server(platform,tmp_path,monkeypatch):
    config,_=configuration(platform,tmp_path,service=False)
    def timeout(*args,**kwargs):raise httpx.ConnectError('no request result')
    with monkeypatch.context() as patch:
        patch.setattr(httpx.Client,'post',timeout)
        with pytest.raises(RuntimeFault):login(config)
    account=session_at(config)
    try:pending=account.cache.get('session','pending')
    finally:account.close()
    value=read_json(config);value['password']='changed-after-unknown';private_json(config,value)
    monkeypatch.setattr(httpx.Client,'post',lambda *args,**kwargs:pytest.fail('changed original password was sent'))
    with pytest.raises(RuntimeFault) as error:login(config)
    assert error.value.code=='result_unknown'
    account=session_at(config)
    try:assert account.cache.get('session','pending')==pending
    finally:account.close()


def test_known_rejection_allows_corrected_explicit_login_but_not_automatic_restart(platform,tmp_path,monkeypatch):
    config,user=configuration(platform,tmp_path,service=False)
    value=read_json(config);value['password']='wrong-first-password';private_json(config,value)
    original=httpx.Client.post;requests=[]
    def count(client,url,*args,**kwargs):
        if str(url).endswith('/api/v1/token'):requests.append(kwargs['json']['operationId'])
        return original(client,url,*args,**kwargs)
    monkeypatch.setattr(httpx.Client,'post',count)
    with pytest.raises(RuntimeFault) as error:login(config)
    assert error.value.code=='authenticate' and len(requests)==1
    account=session_at(config)
    try:
        rejected=account.cache.get('session','pending');assert rejected['phase']=='rejected'
        with pytest.raises(RuntimeFault):account.login()
        assert len(requests)==1
    finally:account.close()
    value['password']=PASSWORD;private_json(config,value)
    result=login(config)
    assert result['data']['newLogin'] and result['data']['operationId']!=rejected['operationId']
    account=session_at(config)
    try:assert account.cache.get('login-attempts',rejected['operationId'])==rejected
    finally:account.close()
    assert len(requests)==2 and platform.wallets.balance(user['id'])==100


def test_binding_survives_token_row_loss_and_rejects_same_name_new_owner(platform,tmp_path,monkeypatch):
    config,user=configuration(platform,tmp_path,service=False);login(config)
    account=session_at(config)
    try:
        owner=account.cache.get('meta','owner_id');account.cache.delete('session','current')
    finally:account.close()
    def wrong(client,url,*args,**kwargs):
        return httpx.Response(200,json={'id':'different-owner','username':user['username'],'token':'synthetic-only'},request=httpx.Request('POST',url))
    monkeypatch.setattr(httpx.Client,'post',wrong)
    with pytest.raises(RuntimeFault) as error:login(config)
    assert error.value.code=='identity_conflict'
    account=session_at(config)
    try:
        assert account.cache.get('meta','owner_id')==owner and account.cache.get('session','current') is None
        assert account.cache.get('session','pending')['phase']=='pending'
    finally:account.close()


def test_cli_login_uses_same_controller_and_returns_no_credentials(platform,tmp_path):
    config,user=configuration(platform,tmp_path,service=False)
    result=subprocess.run([sys.executable,'-X','utf8','-m','ioalab_sdk.runtime','login','--config',str(config)],
                          cwd=tmp_path,capture_output=True,text=True,encoding='utf-8',timeout=20)
    assert result.returncode==0,result.stderr+result.stdout
    value=json.loads(result.stdout)
    assert value['status']=='completed' and value['data']['newLogin']
    assert PASSWORD not in result.stdout and 'token' not in result.stdout
    assert not (tmp_path/'state/runtime.json').exists() and platform.wallets.balance(user['id'])==100


def test_resident_refresh_is_authenticated_bounded_and_never_credits(platform,tmp_path):
    config,user=configuration(platform,tmp_path,service=False)
    runtime=ResidentRuntime(RuntimeConfig.load(config)).start()
    try:
        client=LocalClient(runtime.config.home)
        before=platform.db.one('SELECT COUNT(*) AS n FROM account_operations WHERE owner=?',user['id'])['n']
        assert client.call('account.refresh',{'token':'cannot-supply'})['status']=='rejected'
        result=client.call('account.refresh',{})
        assert result['status']=='completed' and result['data']['authenticated']
        assert 'token' not in json.dumps(result) and platform.wallets.balance(user['id'])==100
        assert platform.db.one('SELECT COUNT(*) AS n FROM account_operations WHERE owner=?',user['id'])['n']==before
        client.call('runtime.stop',{'bootId':runtime.ipc.boot_id})
        assert client.call('account.refresh',{})['error']['code']=='closed'
    finally:runtime.close()


@pytest.mark.parametrize('role',['assistant','service'])
def test_both_skill_roles_route_login_to_installed_sdk_without_starting_host(tmp_path,monkeypatch,capsys,role):
    product=bootstrap();fake=tmp_path/'skill'/'scripts'/'ioalab.py';fake.parent.mkdir(parents=True)
    assets=fake.parent.parent/'assets';assets.mkdir();(assets/'skill.json').write_text(json.dumps({'role':role}))
    monkeypatch.setattr(product,'__file__',str(fake))
    class Installed:
        def prepare(self,*args,**kwargs):return {'status':'completed','data':{'python':sys.executable}}
    monkeypatch.setattr(product,'installation',lambda:Installed())
    calls=[]
    def invoke(python,arguments):
        calls.append(arguments);return subprocess.CompletedProcess(arguments,0,'{"status":"completed","data":{"authenticated":true}}','')
    monkeypatch.setattr(product,'invoke',invoke)
    config=tmp_path/'outside.json'
    assert product.main(['account.login','--config',str(config)])==0
    assert calls==[['-m','ioalab_sdk.runtime','login','--config',str(config.resolve())]]


@pytest.mark.parametrize('kind',['array','invalid-json','missing-token'])
def test_invalid_success_response_remains_unknown_and_reuses_original_operation(platform,tmp_path,monkeypatch,kind):
    config,user=configuration(platform,tmp_path,service=False);sent=[]
    def broken(client,url,*args,**kwargs):
        sent.append(kwargs['json']['operationId']);request=httpx.Request('POST',url)
        if kind=='invalid-json':return httpx.Response(200,content=b'not-json',request=request)
        return httpx.Response(200,json=[] if kind=='array' else {'id':'unverified'},request=request)
    with monkeypatch.context() as patch:
        patch.setattr(httpx.Client,'post',broken)
        with pytest.raises(RuntimeFault) as error:login(config)
        assert error.value.code=='result_unknown' and error.value.operation_id==sent[0]
    result=login(config)
    assert result['data']['operationId']==sent[0] and not result['data']['newLogin']
    assert platform.wallets.balance(user['id'])==100


def test_lost_reauthentication_reply_keeps_original_new_credit(platform,tmp_path,monkeypatch):
    config,user=configuration(platform,tmp_path,service=False);first=login(config)
    expired(platform,config);original=httpx.Client.post;sent=[]
    def lose(client,url,*args,**kwargs):
        response=original(client,url,*args,**kwargs)
        sent.append(kwargs['json']['operationId']);raise httpx.ReadError('lost renewed login reply')
    with monkeypatch.context() as patch:
        patch.setattr(httpx.Client,'post',lose)
        with pytest.raises(RuntimeFault) as error:login(config)
        assert error.value.code=='result_unknown'
    assert sent[0]!=first['data']['operationId'] and platform.wallets.balance(user['id'])==200
    restored=login(config)
    assert restored['data']['operationId']==sent[0] and not restored['data']['newLogin']
    assert platform.wallets.balance(user['id'])==200


def test_missing_pending_input_key_is_not_regenerated(platform,tmp_path,monkeypatch):
    config,_=configuration(platform,tmp_path,service=False)
    with monkeypatch.context() as patch:
        patch.setattr(httpx.Client,'post',lambda *args,**kwargs:(_ for _ in ()).throw(httpx.ConnectError('unknown')))
        with pytest.raises(RuntimeFault):login(config)
    account=session_at(config)
    try:
        pending=account.cache.get('session','pending');account.cache.delete('meta','login_input_key')
    finally:account.close()
    monkeypatch.setattr(httpx.Client,'post',lambda *args,**kwargs:pytest.fail('sent unverifiable original input'))
    with pytest.raises(RuntimeFault) as error:login(config)
    assert error.value.code=='result_unknown'
    account=session_at(config)
    try:
        assert account.cache.get('meta','login_input_key') is None
        assert account.cache.get('session','pending')==pending
    finally:account.close()


def test_failed_online_cache_check_does_not_turn_into_password_login(platform,tmp_path,monkeypatch):
    config,user=configuration(platform,tmp_path,service=False);initial=login(config)
    original=httpx.Client.get
    def unavailable(client,url,*args,**kwargs):
        if str(url).endswith('/api/v1/me'):
            return httpx.Response(500,json={'error':{'code':'unavailable'}},request=httpx.Request('GET',url))
        return original(client,url,*args,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(httpx.Client,'get',unavailable)
        patch.setattr(httpx.Client,'post',lambda *args,**kwargs:pytest.fail('network error caused fresh login'))
        with pytest.raises(RuntimeFault) as error:login(config)
        assert error.value.code=='not_ready' and error.value.recovery=='restore_connection'
    assert login(config)['data']['operationId']==initial['data']['operationId']
    assert platform.wallets.balance(user['id'])==100


def test_competing_explicit_login_cannot_acquire_the_live_account_writer(platform,tmp_path,monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    config,user=configuration(platform,tmp_path,service=False)
    entered=threading.Event();release=threading.Event();original=httpx.Client.post;sent=[]
    def hold(client,url,*args,**kwargs):
        sent.append(kwargs['json']['operationId']);entered.set();assert release.wait(10)
        return original(client,url,*args,**kwargs)
    monkeypatch.setattr(httpx.Client,'post',hold)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first=pool.submit(login,config)
        try:
            assert entered.wait(5)
            with pytest.raises(RuntimeFault) as error:login(config)
            assert error.value.recovery=='runtime.stop' and len(sent)==1
        finally:release.set()
        assert first.result(timeout=15)['data']['newLogin']
    assert not login(config)['data']['newLogin'] and platform.wallets.balance(user['id'])==100


def test_sdk_credential_check_rejects_browser_sessions_without_breaking_web_me(platform,tmp_path):
    config,user=configuration(platform,tmp_path,service=False);login(config)
    browser=platform.login(user['username'],PASSWORD,operation_id=str(uuid.uuid4()),audience='browser')
    account=session_at(config)
    try:
        sdk=account.account['token']
        account.account.update(browser);account.cache.put('session','current',account.account)
    finally:account.close()
    with httpx.Client(base_url=platform.s.base_url,trust_env=False) as http:
        assert http.get('/api/v1/me',headers={'Authorization':'Bearer '+sdk}).status_code==200
        assert http.get('/api/v1/me',headers={'Authorization':'Bearer '+browser['token']}).status_code==401
        http.cookies.set('ioalab_session',browser['token'])
        assert http.get('/api/v1/me').status_code==401
        assert http.get('/api/me').status_code==200
    restored=login(config)
    assert restored['data']['newLogin'] and restored['data']['id']==user['id']
    assert platform.wallets.balance(user['id'])==300
