"""Provisioning recovery and local-only credential configuration."""
import importlib.util
import json
from pathlib import Path

import httpx
import pytest


@pytest.fixture
def module():
    path=Path(__file__).resolve().parents[1]/'personal-assistant/agentmail.py'
    spec=importlib.util.spec_from_file_location('agentmail_setup',path)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result


def test_signup_reuse_verification_and_config_preserve_other_accounts(module,tmp_path,capsys):
    requests=[]
    def handle(request):
        requests.append(request)
        if request.url.path.endswith('sign-up'):return httpx.Response(200,json={'api_key':'test-local-only-key','inbox_id':'test-ioa@agentmail.to','organization_id':'test-org'})
        assert request.headers['Authorization']=='Bearer test-local-only-key'
        assert json.loads(request.content)=={'otp_code':'123456'}
        return httpx.Response(200,json={'verified':True})
    setup=module.AgentMailSetup(tmp_path,http=httpx.Client(base_url='https://api.agentmail.to/v0/',transport=httpx.MockTransport(handle)))
    public=setup.signup('owner@example.test','test-ioa')
    assert 'api_key' not in public
    assert setup.signup('owner@example.test','test-ioa')==public and len(requests)==1
    env=tmp_path/'assistant.env';env.write_text('IOALAB_HOME="D:/assistant"\nIOALAB_USERNAME=ISC_001\nIOALAB_PASSWORD=platform-test-only\nIOALAB_GRAPH_CLIENT_ID=existing-app\n',encoding='utf-8')
    with pytest.raises(ValueError,match='Verify'):setup.configure(env)
    setup.verify('123456');setup.configure(env)
    values=module.read_env(env)
    assert values['IOALAB_HOME']=='D:/assistant' and values['IOALAB_PASSWORD']=='platform-test-only' and values['IOALAB_GRAPH_CLIENT_ID']=='existing-app'
    assert values['IOALAB_SMTP_FROM']==values['IOALAB_SMTP_USERNAME']=='test-ioa@agentmail.to'
    assert values['IOALAB_SMTP_PASSWORD']=='test-local-only-key' and values['IOALAB_SMTP_PORT']=='465'
    assert 'test-local-only-key' not in capsys.readouterr().out


def test_unknown_signup_stops_automatic_re_registration(module,tmp_path):
    calls=[]
    def fail(request):calls.append(request);raise httpx.ReadTimeout('uncertain response')
    setup=module.AgentMailSetup(tmp_path,http=httpx.Client(base_url='https://api.agentmail.to/v0/',transport=httpx.MockTransport(fail)))
    with pytest.raises(httpx.ReadTimeout):setup.signup('owner@example.test','test-ioa')
    with pytest.raises(RuntimeError,match='unknown'):setup.signup('owner@example.test','test-ioa')
    assert len(calls)==1 and setup.state()['status']=='signup_pending'
    with pytest.raises(ValueError):setup.signup('other@example.test','another-inbox')


def test_failed_verification_keeps_delivery_disabled(module,tmp_path):
    def handle(request):
        if request.url.path.endswith('sign-up'):return httpx.Response(200,json={'api_key':'test-only','inbox_id':'test@agentmail.to','organization_id':'test-org'})
        return httpx.Response(403,json={'code':'verification_failed','message':'not echoed'})
    setup=module.AgentMailSetup(tmp_path,http=httpx.Client(base_url='https://api.agentmail.to/v0/',transport=httpx.MockTransport(handle)))
    setup.signup('owner@example.test','test')
    with pytest.raises(RuntimeError,match='verification_failed'):setup.verify('123456')
    assert not setup.state()['verified']
    with pytest.raises(ValueError):setup.configure(tmp_path/'assistant.env')
    assert not (tmp_path/'assistant.env').exists()
