"""Public SDK migration and recovery with a disposable external credential mount."""
import json

import httpx
import pytest

from ioalab_sdk.runtime.authentication import login
from ioalab_sdk.runtime.config import RuntimeConfig
from ioalab_sdk.runtime.session import AccountSession
from ioalab_sdk.runtime.protocol import RuntimeFault
from test_resident_runtime import configuration
from _support import PASSWORD


def open_account(path):
    config=RuntimeConfig.load(path)
    return AccountSession(config.platform,config.username,config.password,config.home)


def test_login_migrates_password_and_restart_uses_only_sealed_session(platform,tmp_path):
    path,user=configuration(platform,tmp_path,service=False)
    before=json.loads(path.read_text())
    result=login(path)
    document=json.loads(path.read_text())
    assert 'password' not in document and 'password_env' not in document
    assert document['credential_ref']==result['data']['credentialRef']
    assert all(document[k]==v for k,v in before.items() if k!='password')
    account=open_account(path)
    try:
        assert account.account['id']==user['id']
        token,refresh=account.token,account.account['refreshToken']
        assert account.status()['sessionId']==result['data']['sessionId']
        for name in ('account.sqlite','account.sqlite-wal','credentials.enc'):
            artifact=account.home/name
            if artifact.exists():
                assert all(value.encode() not in artifact.read_bytes() for value in (PASSWORD,token,refresh))
        assert platform.wallets.balance(user['id'])==100
    finally:account.close()


def test_lost_rotation_response_recovers_without_password_or_new_login(platform,tmp_path,monkeypatch):
    path,user=configuration(platform,tmp_path,service=False)
    original=login(path)['data']
    account=open_account(path)
    post=httpx.Client.post
    responses=[]
    def lose(client,url,*args,**kwargs):
        response=post(client,url,*args,**kwargs)
        if str(url).endswith('/token/refresh'):
            responses.append(response.json())
            raise httpx.ReadTimeout('test response loss after committed rotation')
        return response
    try:
        with monkeypatch.context() as patch:
            patch.setattr(httpx.Client,'post',lose)
            with pytest.raises(httpx.ReadTimeout):account.refresh()
    finally:account.close()
    recovered=open_account(path)
    try:
        assert recovered.account['refreshToken']==responses[0]['refreshToken']
        assert recovered.account['sessionId']==original['sessionId']
        assert platform.wallets.balance(user['id'])==100
    finally:recovered.close()


def test_unavailable_key_store_does_not_login_or_rewrite_configuration(platform,tmp_path,monkeypatch):
    import keyring
    from keyring.backends.null import Keyring
    path,user=configuration(platform,tmp_path,service=False)
    original=path.read_bytes()
    monkeypatch.delenv('CREDENTIALS_DIRECTORY')
    monkeypatch.setattr(keyring,'get_keyring',lambda:Keyring())
    with pytest.raises(RuntimeFault) as error:login(path)
    assert error.value.code=='credential_store_unavailable'
    assert path.read_bytes()==original and platform.wallets.balance(user['id'])==0


def test_revoke_current_device_requires_password_for_new_login(platform,tmp_path):
    path,_=configuration(platform,tmp_path,service=False)
    login(path)
    account=open_account(path)
    try:
        session=account.account['sessionId']
        assert account.revoke(session)['status']=='revoked'
        assert not account.status()['authenticated']
    finally:account.close()
    with pytest.raises(RuntimeFault) as error:open_account(path)
    assert error.value.code=='authenticate'
    assert 'password' not in json.loads(path.read_text())


def test_lost_logout_response_recovers_without_restoring_authority(platform,tmp_path,monkeypatch):
    from ioalab_sdk.runtime.onboarding import Onboarding
    path,user=configuration(platform,tmp_path,service=False)
    session=login(path)['data']['sessionId']
    post=httpx.Client.post
    def lose(client,url,*args,**kwargs):
        response=post(client,url,*args,**kwargs)
        if str(url).endswith('/revoke'):
            response.raise_for_status()
            raise httpx.ReadTimeout('test lost logout receipt')
        return response
    setup=Onboarding(path)
    with monkeypatch.context() as patch:
        patch.setattr(httpx.Client,'post',lose)
        with pytest.raises(RuntimeFault,match='unverified'):setup.devices({'action':'revoke','sessionId':session})
    result=setup.devices({'action':'resume'})
    assert result=={'sessionId':session,'status':'revoked'}
    with pytest.raises(RuntimeFault):open_account(path)
    new=setup.devices({'action':'login','password':PASSWORD})
    assert new['sessionId']!=session and new['authenticated']
    assert 'password' not in path.read_text() and platform.wallets.balance(user['id'])==200
    sessions=setup.devices({'action':'list'})['sessions']
    assert len(sessions)==2 and sum(s['status']=='active' for s in sessions)==1


def test_corrupt_ciphertext_preserves_identity_and_never_falls_back_to_password(platform,tmp_path,monkeypatch):
    path,_=configuration(platform,tmp_path,service=False)
    login(path)
    config=RuntimeConfig.load(path)
    ciphertext=config.home/'credentials.enc'
    damaged=bytearray(ciphertext.read_bytes());damaged[len(damaged)//2]^=1;ciphertext.write_bytes(damaged)
    original=path.read_bytes()
    monkeypatch.setattr(httpx.Client,'post',lambda *a,**k:pytest.fail('Corrupt credentials must not trigger password login'))
    with pytest.raises(RuntimeFault) as error:login(path)
    assert error.value.code=='credential_store_unavailable'
    assert ciphertext.read_bytes()==damaged and path.read_bytes()==original


def test_setup_migration_recover_after_configuration_was_written(platform,tmp_path,monkeypatch):
    from ioalab_sdk.runtime.onboarding import Onboarding
    setup=Onboarding(tmp_path/'config.json')
    save=setup._save
    def fail(record):
        if record.get('phase')=='ready':raise OSError('test interruption after config write')
        return save(record)
    with monkeypatch.context() as patch:
        patch.setattr(setup,'_save',fail)
        with pytest.raises(OSError):setup.submit({'mode':'register','platform':platform.s.base_url})
    original=setup.path.read_bytes()
    restored=Onboarding(setup.path).submit({})
    assert restored['phase']=='ready' and setup.path.read_bytes()==original
    assert platform.wallets.balance(restored['account']['id'])==100  # Reuse the one registration session; no second login or replay credit.


def test_access_expiry_renews_on_demand_without_new_login(platform,tmp_path,monkeypatch):
    import time
    path,user=configuration(platform,tmp_path,service=False)
    original=login(path)['data']
    account=open_account(path)
    old=account.token
    clock=time.time
    try:
        with monkeypatch.context() as patch:
            patch.setattr(time,'time',lambda:clock()+16*60)
            assert account.api('GET','/me')['id']==user['id']
            assert account.status()['authenticated'] and account.token!=old
            assert account.status()['sessionId']==original['sessionId']
        assert platform.wallets.balance(user['id'])==100
    finally:account.close()


@pytest.mark.parametrize('malformed',['array','missing-token','invalid-expiry'])
def test_invalid_rotation_receipt_preserves_original_recovery(platform,tmp_path,monkeypatch,malformed):
    path,user=configuration(platform,tmp_path,service=False)
    login(path)
    account=open_account(path)
    post=httpx.Client.post
    def broken(client,url,*args,**kwargs):
        response=post(client,url,*args,**kwargs)
        if str(url).endswith('/token/refresh'):
            body=response.json()
            if malformed=='array':body=[]
            elif malformed=='missing-token':body.pop('token')
            else:body['expiresAt']='invalid'
            return httpx.Response(200,json=body,request=response.request)
        return response
    try:
        with monkeypatch.context() as patch:
            patch.setattr(httpx.Client,'post',broken)
            with pytest.raises(RuntimeFault):account.refresh()
    finally:account.close()
    recovered=open_account(path)
    try:assert recovered.status()['authenticated'] and platform.wallets.balance(user['id'])==100
    finally:recovered.close()


def test_upgrade_recovers_legacy_pending_login_without_second_credit(platform,tmp_path,monkeypatch):
    import hashlib,hmac
    path,user=configuration(platform,tmp_path,service=False)
    post=httpx.Client.post
    original=[]
    def lost(client,url,*args,**kwargs):
        response=post(client,url,*args,**kwargs)
        if str(url).endswith('/token'):
            original.append(response.json())
            raise httpx.ReadTimeout('legacy login response was not received')
        return response
    with monkeypatch.context() as patch:
        patch.setattr(httpx.Client,'post',lost)
        with pytest.raises(RuntimeFault):login(path)
    # Persisted pre-device-release fixture: original operation and credited result,
    # with the old input digest and no device-session reference.
    with platform.db.tx() as conn:
        row=conn.execute('SELECT result FROM account_operations WHERE owner=? AND kind=?',(user['id'],'login')).fetchone()
        result=json.loads(row['result']);session=result.pop('sessionId')
        canonical=json.dumps({'username':user['username'],'password':PASSWORD,'audience':'sdk'},ensure_ascii=False,sort_keys=True,separators=(',',':'))
        fingerprint=hmac.new(platform.auth.secret,b'account-params\0'+canonical.encode(),hashlib.sha256).hexdigest()
        conn.execute('UPDATE account_operations SET params=?,result=? WHERE owner=? AND kind=?',
                     (fingerprint,json.dumps(result),user['id'],'login'))
        conn.execute('DELETE FROM device_sessions WHERE id=?',(session,))
    receipt=login(path)['data']
    assert receipt['operationId']==original[0]['operationId'] and receipt['sessionId']!=session
    assert platform.wallets.balance(user['id'])==100
    assert 'password' not in path.read_text()
    assert login(path)['data']['sessionId']==receipt['sessionId']


def test_legacy_registration_password_is_sealed_until_one_user_retrieval(platform,tmp_path):
    from ioalab_sdk.runtime.onboarding import Onboarding
    from ioalab_sdk.runtime.files import private_json
    path,user=configuration(platform,tmp_path,service=False)
    setup=Onboarding(path)
    private_json(setup.home/'pending.json',{'phase':'ready','mode':'register','config':json.loads(path.read_text()),
                                          'registered':{'id':user['id']},'account':{'username':user['username']}})
    login(path)
    assert PASSWORD not in path.read_text()
    assert PASSWORD not in (setup.home/'pending.json').read_text()
    assert setup.credentials({})['password']==PASSWORD
    with pytest.raises(RuntimeFault):setup.credentials({})


def test_migration_does_not_adopt_another_accounts_registration_record(platform,tmp_path):
    from ioalab_sdk.runtime.onboarding import Onboarding
    from ioalab_sdk.runtime.files import private_json
    path,_=configuration(platform,tmp_path,service=False)
    original=path.read_bytes()
    setup=Onboarding(path)
    private_json(setup.home/'pending.json',{'phase':'ready','mode':'register',
        'config':{**json.loads(path.read_text()),'username':'another-account'}})
    record=(setup.home/'pending.json').read_bytes()
    with pytest.raises(RuntimeFault) as error:login(path)
    assert error.value.code=='identity_conflict'
    assert path.read_bytes()==original and (setup.home/'pending.json').read_bytes()==record


def test_resumed_setup_preserves_already_rotated_device_session(platform,tmp_path,monkeypatch):
    from ioalab_sdk.runtime.onboarding import Onboarding
    from ioalab_sdk.runtime.session import AccountSession
    setup=Onboarding(tmp_path/'settings.json');save=setup._save
    def interrupted(record):
        if record['phase']=='ready':raise OSError('test interruption')
        save(record)
    with monkeypatch.context() as patch:
        patch.setattr(setup,'_save',interrupted)
        with pytest.raises(OSError):setup.submit({'mode':'register','platform':platform.s.base_url})
    config=RuntimeConfig.load(setup.path)
    account=AccountSession(config.platform,config.username,'',config.home)
    try:
        account.refresh();before=account.status();devices=account.sessions()
    finally:account.close()
    resumed=Onboarding(setup.path).submit({})
    assert resumed['phase']=='ready' and resumed['account']['sessionId']==before['sessionId']
    account=AccountSession(config.platform,config.username,'',config.home)
    try:
        assert account.sessions()==devices
        assert account.status()['authenticated']
    finally:account.close()
    assert platform.wallets.balance(resumed['account']['id'])==100
