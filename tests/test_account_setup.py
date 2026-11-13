"""Public short controller and authenticated browser channel, against the real platform."""
import json
import threading
from pathlib import Path

import httpx
import pytest

from ioalab_sdk.runtime.account_setup import AccountSetupUi
from ioalab_sdk.runtime.ipc import IpcServer
from ioalab_sdk.runtime.files import read_json
from ioalab_sdk.runtime.protocol import RuntimeFault


@pytest.mark.parametrize('configured,override,expected',[
    (None,None,'https://ioalab.openjiuwen.com'),
    (None,'https://chosen.example','https://chosen.example'),
    ('https://existing.example',None,'https://existing.example'),
])
def test_unreachable_platform_is_probed_before_requesting_address_or_credentials(tmp_path,monkeypatch,capsys,configured,override,expected):
    from ioalab_sdk.access import main
    config=tmp_path/'settings.json';calls=[]
    if configured:config.write_text(json.dumps({'platform_url':configured,'password':'private-test-only'}))
    original=config.read_bytes() if config.exists() else None
    def get(client,url,**kwargs):
        calls.append(url)
        assert kwargs['timeout']==5
        return httpx.Response(503,request=httpx.Request('GET',url))
    monkeypatch.setattr(httpx.Client,'get',get)
    arguments=['account-setup','--config',str(config)]
    if override:arguments+=['--platform',override]
    assert main(arguments)==1
    result=json.loads(capsys.readouterr().out)
    assert calls==[expected+'/api/config']
    assert result['data']['nextAction']=='ask_platform_url' and result['data']['platformUrl']==expected
    assert 'private-test-only' not in json.dumps(result)
    assert (config.read_bytes() if config.exists() else None)==original
    assert not config.with_suffix('.data').exists()


def test_runtime_platform_default_is_configurable_without_rewriting_input(tmp_path):
    from ioalab_sdk.runtime import RuntimeConfig
    value={'username':'owner','password':'local-test','data_dir':str(tmp_path/'data'),
           'identities':[{'key':'personal','role':'assistant','name':'Owner'}]}
    assert RuntimeConfig.from_mapping(tmp_path/'settings.json',value).platform=='https://ioalab.openjiuwen.com'
    assert 'platform_url' not in value
    assert RuntimeConfig.from_mapping(tmp_path/'settings.json',{**value,'platform_url':'https://chosen.example'}).platform=='https://chosen.example'


@pytest.mark.parametrize('parameters',[{'role':'assistant'},[]])
def test_account_parameters_cannot_override_bound_skill_role(tmp_path,monkeypatch,capsys,parameters):
    from ioalab_sdk.access import main
    args=tmp_path/'args.json';args.write_text(json.dumps(parameters))
    monkeypatch.setattr(httpx.Client,'get',lambda *a,**k:pytest.fail('Role conflict reached network'))
    assert main(['account-setup','--config',str(tmp_path/'settings.json'),'--role','service','--args-file',str(args)])==1
    assert json.loads(capsys.readouterr().out)['error']['code']=='invalid_input'
from _support import PASSWORD,new_user


def test_cli_reopens_original_account_page_without_exposing_browser_authority(platform,tmp_path,monkeypatch,capsys):
    from urllib.parse import urlsplit
    from conftest import wait_for
    from ioalab_sdk.access import main
    from ioalab_sdk.runtime.account_setup import serve,status

    config=tmp_path/'settings.json';opened=[]
    monkeypatch.setattr('ioalab_sdk.runtime.user_ui.webbrowser.open',lambda url:opened.append(url) or True)
    worker=threading.Thread(target=serve,args=(config,{'platform_url':platform.s.base_url},'original-worker'),kwargs={'timeout':20})
    worker.start()
    wait_for(lambda:bool(opened) and status(config).get('data',{}).get('runId')=='original-worker')
    first=urlsplit(opened[0]);origin=first.scheme+'://'+first.netloc
    browser=httpx.Client(base_url=origin,trust_env=False)
    csrf=browser.post('/user/session',json={'token':first.fragment},headers={'Origin':origin}).json()['csrf']
    try:
        assert main(['account-setup','--config',str(config),'--platform',platform.s.base_url])==0
        output=capsys.readouterr().out;result=json.loads(output)
        count=len(opened)
        assert count==2
        assert result['status']=='waiting' and result['data']['runId']=='original-worker'
        second=urlsplit(opened[1])
        same_listener=(first.scheme,first.netloc)==(second.scheme,second.netloc)
        fresh_entry=first.fragment!=second.fragment
        leaked=any(urlsplit(url).fragment in output for url in opened)
        assert same_listener and fresh_entry and not leaked
        assert browser.post('/user/session',json={'token':first.fragment},headers={'Origin':origin}).status_code==403
        with httpx.Client(base_url=origin,trust_env=False) as reopened:
            session=reopened.post('/user/session',json={'token':second.fragment},headers={'Origin':origin})
            assert session.status_code==200
            headers={'Origin':origin,'X-Ioa-Csrf':session.json()['csrf']}
            registered=reopened.post('/user/decision',json={'mode':'register'},headers=headers).json()
            assert registered['authenticated']
            assert reopened.post('/user/decision',json={'mode':'register'},headers=headers).json()==registered
            assert platform.wallets.balance(registered['id'])==100
        assert main(['account-setup','--config',str(config)])==0
        completed=json.loads(capsys.readouterr().out)
        assert completed['status']=='completed' and completed['data']['id']==registered['id']
        count=len(opened)
        assert count==2
        assert not (config.with_suffix('.data')/'runtime.json').exists()
    finally:
        browser.post('/user/decision',json={'mode':'cancel'},headers={'Origin':origin,'X-Ioa-Csrf':csrf})
        browser.close();worker.join(5)
    assert not worker.is_alive()
    assert not (config.with_suffix('.data')/'account-ui/runtime.json').exists()


def test_register_browser_and_replay_keep_one_account(platform,tmp_path):
    config=tmp_path/'settings.json'
    ui=AccountSetupUi(config,{'platform_url':platform.s.base_url,'role':'assistant'})
    server=IpcServer(ui.home,'account-setup',lambda *args:pytest.fail('No RPC'),user_ui=ui)
    thread=threading.Thread(target=server.server.serve_forever,daemon=True);thread.start()
    try:
        origin=ui.origin
        with httpx.Client(base_url=origin,trust_env=False) as browser:
            assert browser.post('/user/decision',json={'mode':'register'},headers={'Origin':origin}).status_code==403
            entry=ui.entry_url().split('#')[1]
            csrf=browser.post('/user/session',json={'token':entry},headers={'Origin':origin}).json()['csrf']
            headers={'Origin':origin,'X-Ioa-Csrf':csrf}
            assert browser.post('/user/decision',json={'mode':'register'},headers={**headers,'Origin':'https://other.test'}).status_code==403
            first=browser.post('/user/decision',json={'mode':'register'},headers=headers)
            assert first.status_code==200,first.text
            result=first.json()
            assert result['authenticated'] and 'password' not in result and 'token' not in result
            account=result['id']
            balance=platform.wallets.balance(account)
            assert browser.post('/user/decision',json={'mode':'register'},headers=headers).json()==result
            assert platform.wallets.balance(account)==balance
            assert read_json(config)['username']==result['username']
            private=browser.get('/user/api').json()
            assert private['credentials']['password']==read_json(config)['password']
    finally:
        server.server.shutdown();thread.join();server.close()


@pytest.mark.parametrize('role',['assistant','service'])
def test_lost_registration_receipt_resumes_original(platform,tmp_path,monkeypatch,role):
    import sys
    browser=str(Path(sys.executable).resolve())
    ui=AccountSetupUi(tmp_path/'settings.json',{'platform_url':platform.s.base_url,'role':role,'user_browser':browser})
    post=httpx.Client.post
    lost=[]
    def drop(client,url,*args,**kwargs):
        response=post(client,url,*args,**kwargs)
        if str(url).endswith('/api/v1/accounts') and not lost:
            lost.append(response.json())
            raise httpx.ReadError('lost response')
        return response
    monkeypatch.setattr(httpx.Client,'post',drop)
    with pytest.raises(RuntimeFault,match='unverified'):ui.decision({'mode':'register'},None)
    original=read_json(ui.journal)
    assert original['phase']=='pending'
    ui=AccountSetupUi(ui.path,{})
    result=ui.decision({'mode':'register'},None)
    assert result['id']==lost[0]['id']
    assert read_json(ui.journal)['body']==original['body']
    assert read_json(ui.path)['identities'][0]['role']==role
    assert read_json(ui.path)['user_browser']==browser
    assert platform.wallets.balance(result['id'])==100


def test_cancel_does_not_require_stopping_a_resident(platform,tmp_path):
    from ioalab_sdk.runtime.lease import Lease
    ui=AccountSetupUi(tmp_path/'settings.json',{'platform_url':platform.s.base_url})
    with Lease(ui.home/'client.lock',timeout=0):
        assert ui.decision({'mode':'cancel'},None)['cancelled']


def test_reusing_valid_session_preserves_original_credentials(platform,tmp_path):
    from test_resident_runtime import configuration
    from ioalab_sdk.runtime.authentication import login
    config,user=configuration(platform,tmp_path,service=False)
    login(config)
    original=read_json(config)
    ui=AccountSetupUi(config,{})
    result=ui.decision({'mode':'existing','username':user['username'],'password':'not-the-stored-password'},None)
    assert result['authenticated'] and not result['newLogin']
    assert read_json(config)==original


def test_existing_wrong_password_cancel_correct_and_no_secrets(platform,tmp_path):
    user=new_user(platform)
    ui=AccountSetupUi(tmp_path/'settings.json',{'platform_url':platform.s.base_url})
    with pytest.raises(RuntimeFault):ui.decision({'mode':'existing','username':user['username'],'password':'wrong-password'},None)
    assert ui.decision({'mode':'cancel'},None)['cancelled']
    ui=AccountSetupUi(ui.path,{})
    result=ui.decision({'mode':'existing','username':user['username'],'password':PASSWORD},None)
    assert result['authenticated']
    assert PASSWORD not in json.dumps(read_json(ui.receipt))
    assert 'token' not in json.dumps(read_json(ui.receipt))
    assert ui.snapshot()['credentials'] is None


@pytest.mark.parametrize('policy',['closed','invite'])
def test_registration_policy_reports_real_next_step(platform,tmp_path,policy):
    previous=platform.s.registration
    platform.s.registration=policy
    try:
        ui=AccountSetupUi(tmp_path/'settings.json',{'platform_url':platform.s.base_url})
        with pytest.raises(RuntimeFault,match='不开放注册' if policy=='closed' else '邀请码无效'):
            ui.decision({'mode':'register'},None)
        if policy=='invite':
            result=ui.decision({'mode':'register','invite':platform.auth.new_invite()},None)
            assert result['authenticated']
            assert platform.wallets.balance(result['id'])==100
    finally:platform.s.registration=previous


def test_changed_platform_cannot_reuse_completed_registration(platform,tmp_path):
    ui=AccountSetupUi(tmp_path/'settings.json',{'platform_url':platform.s.base_url})
    result=ui.decision({'mode':'register'},None)
    settings=read_json(ui.path);settings['platform_url']='https://another.invalid'
    from ioalab_sdk.runtime.files import private_json
    private_json(ui.path,settings)
    other=AccountSetupUi(ui.path,{})
    with pytest.raises(RuntimeFault,match='original platform'):
        other.decision({'mode':'register'},None)


@pytest.mark.parametrize('code',[429,500,401])
def test_uncertain_registration_never_changes_account_or_operation(platform,tmp_path,monkeypatch,code):
    ui=AccountSetupUi(tmp_path/'settings.json',{'platform_url':platform.s.base_url})
    post=httpx.Client.post
    def unavailable(client,url,*args,**kwargs):
        if str(url).endswith('/api/v1/accounts'):
            return httpx.Response(code,json={'error':{'code':'rate_limited' if code==429 else 'forbidden','message':'retry'}},request=httpx.Request('POST',url))
        return post(client,url,*args,**kwargs)
    monkeypatch.setattr(httpx.Client,'post',unavailable)
    with pytest.raises(RuntimeFault):ui.decision({'mode':'register'},None)
    original=read_json(ui.journal)['body']
    assert read_json(ui.journal)['phase']=='pending'
    monkeypatch.setattr(httpx.Client,'post',post)
    result=ui.decision({'mode':'register'},None)
    assert result['authenticated'] and read_json(ui.journal)['body']==original
    assert platform.wallets.balance(result['id'])==100
