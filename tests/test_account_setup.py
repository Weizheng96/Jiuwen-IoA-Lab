"""Public short controller and authenticated browser channel, against the real platform."""
import json
import threading

import httpx
import pytest

from ioalab_sdk.runtime.onboarding import Onboarding
from ioalab_sdk.runtime.user_ui import UserUi
from ioalab_sdk.runtime.files import private_json
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
    try:
        assert main(['account-setup','--config',str(config),'--platform',platform.s.base_url])==0
        output=capsys.readouterr().out;result=json.loads(output)
        assert len(opened)==2 and result['data']['runId']=='original-worker'
        second=urlsplit(opened[1])
        assert first.netloc==second.netloc and first.fragment!=second.fragment
        assert all(urlsplit(url).fragment not in output for url in opened)
        assert main(['account-setup','--config',str(config),'--platform',platform.s.base_url,'--role','service'])==1
        assert json.loads(capsys.readouterr().out)['error']['code']=='identity_conflict'
        assert len(opened)==2
        with httpx.Client(base_url=origin,trust_env=False) as browser:
            session=browser.post('/user/session',json={'token':second.fragment},headers={'Origin':origin}).raise_for_status().json()
            headers={'Origin':origin,'X-Ioa-Csrf':session['csrf']}
            assert browser.post('/user/session',json={'token':second.fragment},headers={'Origin':origin}).status_code==403
            registered=browser.post('/user/setup',json={'mode':'register'},headers=headers).raise_for_status().json()
            assert registered['phase']=='ready'
            assert browser.post('/user/setup',json={},headers=headers).json()==registered
            assert platform.wallets.balance(registered['account']['id'])==100
            assert main(['account-setup-status','--config',str(config)])==0
            completed=json.loads(capsys.readouterr().out)
            assert completed['status']=='completed' and completed['data']['account']['id']==registered['account']['id']
            assert 'password' not in browser.get('/user/api').text
            assert browser.post('/user/decision',json={},headers=headers).status_code==403
            assert browser.post('/user/setup',json={'mode':'cancel'},headers=headers).json()['cancelled']
    finally:worker.join(22)
    assert not worker.is_alive()
    assert not (Onboarding(config).home/'runtime.json').exists()


def setup_page(path,platform,role='assistant'):
    page=Onboarding(path)
    private_json(page.home/'options.json',{'platform_url':platform.s.base_url,'role':role})
    return page


@pytest.mark.parametrize('role',['assistant','service'])
def test_lost_registration_receipt_resumes_original(platform,tmp_path,monkeypatch,role):
    page=setup_page(tmp_path/'settings.json',platform,role)
    post=httpx.Client.post;lost=[]
    def drop(client,url,*args,**kwargs):
        response=post(client,url,*args,**kwargs)
        if str(url).endswith('/api/v1/accounts') and not lost:
            lost.append(response.json());raise httpx.ReadError('lost response')
        return response
    monkeypatch.setattr(httpx.Client,'post',drop)
    assert page.submit({'mode':'register'})['phase']=='unknown'
    result=Onboarding(page.path).submit({})
    assert result['account']['id']==lost[0]['id']
    assert read_json(page.path)['identities'][0]['role']==role
    assert platform.wallets.balance(result['account']['id'])==100
    assert 'password' not in page.path.read_text()


def test_cancel_does_not_require_stopping_a_resident(platform,tmp_path):
    from ioalab_sdk.runtime.lease import Lease
    page=setup_page(tmp_path/'settings.json',platform)
    with Lease(page.home/'client.lock',timeout=0):assert page.submit({'mode':'cancel'})['cancelled']


def test_reusing_valid_session_preserves_original_credentials(platform,tmp_path):
    from test_resident_runtime import configuration
    from ioalab_sdk.runtime.authentication import login
    config,user=configuration(platform,tmp_path,service=False)
    login(config);original=read_json(config)
    page=Onboarding(config);result=page.submit({})
    assert result['account']['authenticated'] and not result['account']['newLogin']
    assert read_json(config)==original and 'password' not in original


def test_existing_wrong_password_cancel_correct_and_no_secrets(platform,tmp_path):
    user=new_user(platform);page=setup_page(tmp_path/'settings.json',platform)
    assert page.submit({'mode':'login','username':user['username'],'password':'wrong-password'})['phase']=='rejected'
    assert page.submit({'mode':'cancel'})['cancelled']
    page=Onboarding(page.path)
    result=page.submit({'mode':'login','username':user['username'],'password':PASSWORD})
    assert result['phase']=='ready' and result['account']['authenticated']
    assert PASSWORD not in page.path.read_text() and PASSWORD not in (page.home/'pending.json').read_text()
    with pytest.raises(RuntimeFault):page.credentials({})


@pytest.mark.parametrize('policy',['closed','invite'])
def test_registration_policy_reports_real_next_step(platform,tmp_path,policy):
    previous=platform.s.registration;platform.s.registration=policy
    try:
        page=setup_page(tmp_path/'settings.json',platform)
        rejected=page.submit({'mode':'register'})
        assert rejected['phase']=='rejected'
        assert ('未开放注册' if policy=='closed' else '有效邀请码') in rejected['error']['message']
        if policy=='invite':
            result=page.submit({'mode':'register','invite':platform.auth.new_invite()})
            assert result['phase']=='ready' and platform.wallets.balance(result['account']['id'])==100
    finally:platform.s.registration=previous


def test_changed_platform_cannot_reuse_completed_registration(platform,tmp_path):
    page=setup_page(tmp_path/'settings.json',platform)
    assert page.submit({'mode':'register'})['phase']=='ready'
    settings=read_json(page.path);settings['platform_url']='https://another.invalid';private_json(page.path,settings)
    with pytest.raises(RuntimeFault) as error:Onboarding(page.path).submit({})
    assert error.value.code=='invalid_input'
    assert read_json(page.path)==settings


@pytest.mark.parametrize('code',[429,500,401])
def test_uncertain_registration_never_changes_account_or_operation(platform,tmp_path,monkeypatch,code):
    page=setup_page(tmp_path/'settings.json',platform)
    post=httpx.Client.post;calls=[]
    def unavailable(client,url,*args,**kwargs):
        if str(url).endswith('/api/v1/accounts'):
            calls.append(kwargs['json'].copy())
            if len(calls)==1:return httpx.Response(code,json={'error':{'code':'forbidden','message':'retry'}},request=httpx.Request('POST',url))
        return post(client,url,*args,**kwargs)
    monkeypatch.setattr(httpx.Client,'post',unavailable)
    assert page.submit({'mode':'register'})['phase']=='unknown'
    with pytest.raises(RuntimeFault):page.submit({'mode':'register','name':'replacement'})
    result=Onboarding(page.path).submit({})
    assert result['phase']=='ready' and calls[0]==calls[1]
    assert platform.wallets.balance(result['account']['id'])==100


def test_bound_page_cannot_change_platform_or_role(platform,tmp_path):
    page=setup_page(tmp_path/'settings.json',platform,'service')
    for values in ({'mode':'register','role':'assistant'},{'mode':'register','platform':'https://other.invalid'}):
        with pytest.raises(RuntimeFault):page.submit(values)
    assert not page.path.exists()


@pytest.mark.parametrize("interrupted",[False,True])
def test_legacy_registration_migrates_and_recovers_same_operation(platform,tmp_path,monkeypatch,interrupted):
    import uuid
    config=tmp_path/'settings.json';home=config.with_suffix('.data')
    body={'operationId':str(uuid.uuid4()),'username':'legacy_'+uuid.uuid4().hex[:12],'password':PASSWORD,'invite':''}
    with httpx.Client(trust_env=False) as http:
        registered=http.post(platform.s.base_url+'/api/v1/accounts',json=body).raise_for_status().json()
    journal=home/'account-setup.json'
    private_json(journal,{'mode':'register','phase':'pending','body':body,'platform':platform.s.base_url,
        'configuration':{'platform_url':platform.s.base_url,'data_dir':str(home),'identities':[{'key':'service','role':'service','name':'Legacy'}]}})
    assert Onboarding(config).status()['phase']=='unknown'
    if interrupted:
        import ioalab_sdk.runtime.onboarding as module
        original=module.private_json
        def fail(path,value):
            if path==journal:raise OSError('test interrupted after encryption')
            return original(path,value)
        with monkeypatch.context() as patch:
            patch.setattr(module,'private_json',fail)
            with pytest.raises(OSError):Onboarding(config).submit({})
    result=Onboarding(config).submit({})
    assert result['phase']=='ready' and result['account']['id']==registered['id']
    assert platform.wallets.balance(registered['id'])==100
    assert PASSWORD not in config.read_text() and PASSWORD not in journal.read_text()
    assert read_json(config)['data_dir']==str(home)
