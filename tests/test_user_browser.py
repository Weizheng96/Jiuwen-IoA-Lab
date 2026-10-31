"""Browser choice is owner configuration; opening never grants user approval."""
from dataclasses import replace
import json
import os
from urllib.parse import urlsplit

import httpx
import pytest

from conftest import wait_for
from ioalab_sdk.runtime.config import RuntimeConfig
from ioalab_sdk.runtime.files import private_json,read_json
from ioalab_sdk.runtime.ipc import LocalClient
from ioalab_sdk.runtime.protocol import RuntimeFault
from ioalab_sdk.runtime.resident import ResidentRuntime
from ioalab_sdk.runtime.__main__ import start
from test_resident_runtime import configuration


def browser_path(tmp_path):
    executable=tmp_path/'Browser with spaces'/('browser.exe' if os.name=='nt' else 'browser')
    executable.parent.mkdir()
    executable.write_bytes(b'isolated launcher test double; never executed')
    executable.chmod(0o700)
    return str(executable.resolve())


@pytest.mark.parametrize('value',['relative/browser.exe','/missing-browser.exe',[],{},1,''])
def test_invalid_browser_is_rejected_before_creating_runtime_state(tmp_path,value):
    config=tmp_path/'settings.json'
    private_json(config,{'platform_url':'http://127.0.0.1:8000','username':'account','password':'private-test',
        'data_dir':'state','identities':[{'key':'personal','role':'assistant','name':'助手'}],'user_browser':value})
    with pytest.raises(RuntimeFault) as error:RuntimeConfig.load(config)
    assert error.value.code=='invalid_input' and 'user_browser' in str(error.value)
    assert not (tmp_path/'state').exists()


def test_browser_is_in_the_configuration_digest_without_leaking_credentials(tmp_path):
    path=tmp_path/'settings.json';base={'platform_url':'http://127.0.0.1:8000','username':'account','password':'private-test',
        'data_dir':'state','identities':[{'key':'personal','role':'assistant','name':'助手'}]}
    original=RuntimeConfig.from_mapping(path,base)
    assert original.user_browser is None
    assert RuntimeConfig.from_mapping(path,{**base,'user_browser':None}).public_summary()==original.public_summary()
    configured=RuntimeConfig.from_mapping(path,{**base,'user_browser':browser_path(tmp_path)})
    assert configured.public_summary()['configurationDigest']!=original.public_summary()['configurationDigest']
    assert replace(configured,password='different-private-test').public_summary()==configured.public_summary()
    assert 'private-test' not in json.dumps(configured.public_summary())
    with pytest.raises(RuntimeFault):RuntimeConfig.from_mapping(path,{**base,'user_browser':configured.user_browser+' --incognito'})
    assert not (tmp_path/'state').exists()


@pytest.mark.parametrize('custom',[False,True])
def test_real_ipc_opens_only_configured_browser_and_preserves_pending_confirmation(platform,tmp_path,monkeypatch,custom):
    path,_=configuration(platform,tmp_path,service=False)
    executable=browser_path(tmp_path) if custom else None
    value=read_json(path);value['user_browser']=executable;private_json(path,value)
    launched=[]
    def popen(arguments,**options):
        assert arguments[0]==executable and len(arguments)==2
        assert options['shell'] is False
        assert all(options[name]==-3 for name in ('stdin','stdout','stderr'))
        launched.append(arguments[1])
    def system(url):
        assert not custom;launched.append(url);return True
    runtime=ResidentRuntime(RuntimeConfig.load(path)).start()
    try:
        monkeypatch.setattr('ioalab_sdk.runtime.user_ui.subprocess.Popen',popen)
        monkeypatch.setattr('ioalab_sdk.runtime.user_ui.webbrowser.open',system)
        wait_for(lambda:runtime.connection.connected)
        identity=runtime.identities[0]
        binding={'actor':identity.agent_id,'operationId':'real-original-operation','subject':{'digest':'sha256:'+'a'*64},
            'stage':'rules.publish','action':'interface.publish','preview':{'title':'真实待确认对象','tools':[]}}
        pending=runtime.confirmations.ensure(binding)
        client=LocalClient(runtime.config.home)
        denied=client.call('runtime.open_ui',{'browser':'untrusted.exe','url':'https://untrusted.invalid'})
        assert denied['status']=='rejected' and denied['error']['code']=='invalid_input' and not launched
        opened=client.call('runtime.open_ui',{})
        assert opened['status']=='completed' and opened['data']=={'opened':True} and len(launched)==1
        assert runtime.confirmations.get(pending['reference'])['status']=='pending'
        token=urlsplit(launched[0]).fragment
        assert token and token not in json.dumps(opened) and runtime.user_ui._authority not in json.dumps(opened)
        # Explicit isolated browser driver; not a real human/native frontend acceptance claim.
        with httpx.Client(base_url=runtime.user_ui.origin,trust_env=False) as browser:
            entry=browser.get('/user');assert entry.status_code==200
            session=browser.post('/user/session',json={'token':token},headers={'Origin':runtime.user_ui.origin})
            assert session.status_code==200
            decision=browser.post('/user/decision',json={'reference':pending['reference'],
                'bindingDigest':pending['bindingDigest'],'decision':'approve'},
                headers={'Origin':runtime.user_ui.origin,'X-Ioa-Csrf':session.json()['csrf']})
            assert decision.status_code==200
        final=runtime.confirmations.get(pending['reference'])
        assert final['status']=='approved' and final['source'].startswith('local_user_ui:')
        assert final['reference']==pending['reference']
    finally:runtime.close()


def test_custom_browser_failure_is_explicit_sanitized_and_does_not_fall_back(platform,tmp_path,monkeypatch):
    path,_=configuration(platform,tmp_path,service=False)
    value=read_json(path);value['user_browser']=browser_path(tmp_path);private_json(path,value)
    def denied(arguments,**options):raise OSError('Must not expose the browser URL: '+arguments[1])
    runtime=ResidentRuntime(RuntimeConfig.load(path)).start()
    try:
        monkeypatch.setattr('ioalab_sdk.runtime.user_ui.subprocess.Popen',denied)
        monkeypatch.setattr('ioalab_sdk.runtime.user_ui.webbrowser.open',lambda *_:pytest.fail('Unexpected browser fallback'))
        result=LocalClient(runtime.config.home).call('runtime.open_ui',{})
        assert result['status']=='rejected' and result['error']['code']=='not_ready'
        assert result['error']['recovery']=='configure_browser'
        assert '/user#' not in json.dumps(result) and runtime.user_ui._authority not in json.dumps(result)
    finally:runtime.close()


def test_unavailable_system_browser_is_not_reported_as_a_completed_open(platform,tmp_path,monkeypatch):
    path,_=configuration(platform,tmp_path,service=False)
    runtime=ResidentRuntime(RuntimeConfig.load(path)).start()
    try:
        monkeypatch.setattr('ioalab_sdk.runtime.user_ui.webbrowser.open',lambda *_:False)
        result=LocalClient(runtime.config.home).call('runtime.open_ui',{})
        assert result['status']=='rejected' and result['error']['code']=='not_ready'
        assert result['error']['recovery']=='configure_browser' and result['data'] is None
    finally:runtime.close()


def test_changing_browser_requires_safe_restart_instead_of_a_competing_runtime(platform,tmp_path):
    path,_=configuration(platform,tmp_path,service=False)
    runtime=ResidentRuntime(RuntimeConfig.load(path)).start()
    try:
        before=LocalClient(runtime.config.home).call('runtime.status')['data']
        value=read_json(path);value['user_browser']=browser_path(tmp_path);private_json(path,value)
        with pytest.raises(RuntimeFault) as error:start(path)
        assert error.value.code=='configuration_changed'
        after=LocalClient(runtime.config.home).call('runtime.status')['data']
        assert before['bootId']==after['bootId'] and before['configurationDigest']==after['configurationDigest']
        assert runtime.user_ui.browser is None
    finally:runtime.close()
