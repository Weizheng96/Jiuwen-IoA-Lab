"""First access uses the real platform and private client persistence."""
import json
import os

import pytest
import httpx

from ioalab_sdk.runtime.onboarding import Onboarding
from ioalab_sdk.runtime.config import RuntimeConfig
from ioalab_sdk.runtime.protocol import RuntimeFault


@pytest.mark.skipif(os.name == 'nt', reason='POSIX terminal session hangup')
def test_setup_page_survives_launcher_session_hangup(platform,tmp_path):
    """A finished host command must leave its already opened account page usable."""
    import signal
    import subprocess
    import sys
    import shlex
    from ioalab_sdk.runtime import LocalClient
    from conftest import wait_for

    path = tmp_path / 'settings.json'
    receipt = tmp_path / 'opened.json'
    browser = tmp_path / 'browser.py'
    browser.write_text('raise SystemExit(0)\n')
    script = ("import json,sys,time; from pathlib import Path; from ioalab_sdk.runtime import setup; "
              "result=setup(sys.argv[1],open_browser=True,options={'platform_url':sys.argv[3]}); Path(sys.argv[2]).write_text(json.dumps(result)); time.sleep(30)")
    service_pid = None
    with (tmp_path / 'launcher.log').open('w') as log:
        launcher = subprocess.Popen([sys.executable, '-c', script, str(path), str(receipt),platform.s.base_url],
            env={**os.environ, 'BROWSER': shlex.join([sys.executable, str(browser)]) + ' %s'},
            stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    try:
        wait_for(receipt.is_file, timeout=15)
        assert json.loads(receipt.read_text())['data']['opened'] is True
        home = Onboarding(path).home
        descriptor = json.loads((home / 'runtime.json').read_text())
        service_pid = descriptor['pid']  # Only for cleaning up this test's own service.
        client = LocalClient(home, timeout=2)
        assert client.call('account.setup.status')['data']['phase'] == 'input'
        os.killpg(launcher.pid, signal.SIGHUP)
        launcher.wait(timeout=5)
        with httpx.Client(trust_env=False, timeout=2) as browser_client:
            assert browser_client.get(descriptor['url'] + '/user').status_code == 200
        assert client.call('account.setup.status')['data']['phase'] == 'input'
    finally:
        if launcher.poll() is None:
            os.killpg(launcher.pid, signal.SIGTERM)
            launcher.wait(timeout=5)
        if service_pid:
            try: os.kill(service_pid, signal.SIGTERM)
            except ProcessLookupError: pass


def test_registration_creates_reusable_private_config_without_returning_credentials(platform, tmp_path):
    path = tmp_path / 'settings.json'
    onboarding = Onboarding(path)
    assert onboarding.status()['phase'] == 'input'
    result = onboarding.submit({'mode': 'register', 'platform': platform.s.base_url,
                                'role': 'assistant', 'name': '我的助手'})
    assert result['phase'] == 'ready'
    assert platform.db.one('SELECT display_name FROM users WHERE id=?',result['account']['id'])['display_name']=='我的助手'
    config = RuntimeConfig.load(path)
    assert not config.password and len(config.credential_ref)==64
    secret=onboarding.credentials({})['password']
    assert len(secret)>=24 and secret not in json.dumps(result)
    assert secret not in path.read_text()
    assert secret not in (onboarding.home/'pending.json').read_text()
    with pytest.raises(RuntimeFault):onboarding.credentials({})
    assert Onboarding(path).submit({}) == result
    assert Onboarding(path).status() == result
    with pytest.raises(RuntimeFault):
        onboarding.submit({'mode': 'register', 'platform': platform.s.base_url, 'role': 'service', 'name': '替换'})
    assert RuntimeConfig.load(path) == config


def test_lost_registration_response_restores_same_account_and_credit(platform, tmp_path, monkeypatch):
    path = tmp_path / 'settings.json'
    send = httpx.Client.send
    lost = []
    def once(client, request, **kwargs):
        response = send(client, request, **kwargs)
        if request.url.path == '/api/v1/accounts' and not lost:
            lost.append(response.json())
            raise httpx.ReadError('isolated lost response', request=request)
        return response
    monkeypatch.setattr(httpx.Client, 'send', once)
    result = Onboarding(path).submit({'mode': 'register', 'platform': platform.s.base_url})
    assert result['phase'] == 'unknown' and not path.exists()
    with pytest.raises(RuntimeFault):
        Onboarding(path).submit({'mode': 'register', 'platform': platform.s.base_url, 'name': 'another'})
    resumed = Onboarding(path).submit({})
    assert resumed['phase'] == 'ready'
    assert resumed['account']['id'] == lost[0]['id']
    assert RuntimeConfig.load(path).username == lost[0]['username']


def test_setup_browser_protects_credential_input_and_does_not_offer_decisions(platform, tmp_path):
    from ioalab_sdk.runtime.user_ui import UserUi
    from ioalab_sdk.runtime.ipc import IpcServer
    from urllib.parse import urlsplit
    onboarding = Onboarding(tmp_path / 'config.json')
    onboarding.home.mkdir(parents=True)
    ui = UserUi(onboarding=onboarding)
    server = IpcServer(onboarding.home, 'setup-test', lambda *_: {}, user_ui=ui).start()
    try:
        with httpx.Client(base_url=ui.origin, trust_env=False) as browser:
            body = {'mode': 'register', 'platform': platform.s.base_url}
            assert browser.post('/user/setup', json=body, headers={'Origin': ui.origin}).status_code == 403
            assert browser.post('/user/credentials', json={}, headers={'Origin': ui.origin}).status_code == 403
            token = urlsplit(ui.entry_url()).fragment
            session = browser.post('/user/session', json={'token': token}, headers={'Origin': ui.origin}).json()
            headers = {'Origin': ui.origin, 'X-Ioa-Csrf': session['csrf']}
            assert browser.post('/user/setup', json=body, headers={**headers, 'Origin': 'https://example.org'}).status_code == 403
            assert browser.post('/user/decision', json={}, headers=headers).status_code == 403
            assert browser.post('/user/setup', json=body, headers=headers).json()['phase'] == 'ready'
            assert 'password' not in browser.get('/user/api').text
            assert browser.get('/user/credentials').status_code == 403
            assert browser.post('/user/credentials', json={}, headers={**headers, 'Origin': 'https://example.org'}).status_code == 403
            secret=browser.post('/user/credentials', json={}, headers=headers).json()['password']
            assert len(secret)>=24 and not RuntimeConfig.load(onboarding.path).password
            assert browser.post('/user/credentials', json={}, headers=headers).status_code!=200
            assert browser.post('/user/devices',json={'action':'list'},headers={**headers,'Origin':'https://example.org'}).status_code==403
            devices=browser.post('/user/devices',json={'action':'list'},headers=headers).raise_for_status().json()
            assert len(devices['sessions'])==1 and devices['sessions'][0]['current']
    finally: server.close()


def test_controlled_configuration_preserves_credentials_and_refuses_live_owner(platform, tmp_path):
    from ioalab_sdk.runtime.files import read_json
    from ioalab_sdk.runtime.lease import Lease
    path = tmp_path / 'config.json'
    onboarding = Onboarding(path)
    assert onboarding.submit({'mode': 'register', 'platform': platform.s.base_url, 'role': 'service'})['phase'] == 'ready'
    before = read_json(path)
    state = onboarding.configuration()['configuration']
    change = {'action': 'configure', 'expectedDigest': state['configurationDigest'], 'changes': {
        'provider': {'services': {'service': {'instructions': '根据合同生产业务交付', 'skills': [], 'max_active_delegations': 3}}}}}
    with Lease(RuntimeConfig.load(path).home / 'client.lock', timeout=0):
        assert onboarding.control(change)['status'] == 'rejected'
    assert read_json(path) == before
    assert onboarding.control(change)['status'] == 'completed'
    assert 'password' not in read_json(path)
    assert read_json(path)['credential_ref'] == before['credential_ref']
    assert read_json(path)['provider']['services']['service']['max_active_delegations'] == 3


def test_registered_account_is_retained_if_initial_login_is_rejected(platform, tmp_path, monkeypatch):
    send = httpx.Client.send
    registered = []
    denied = []
    def once(client, request, **kwargs):
        if request.url.path == '/api/v1/me' and not denied:
            denied.append(True)
            return httpx.Response(401, request=request, json={'error': {'code': 'authenticate'}})
        response = send(client, request, **kwargs)
        if request.url.path == '/api/v1/accounts': registered.append(response.json()['id'])
        return response
    monkeypatch.setattr(httpx.Client, 'send', once)
    onboarding = Onboarding(tmp_path / 'config.json')
    values = {'mode': 'register', 'platform': platform.s.base_url}
    assert onboarding.submit(values)['phase'] == 'unknown'
    with pytest.raises(RuntimeFault): onboarding.submit({**values, 'name': 'another-account'})
    result = onboarding.submit({})
    assert result['phase'] == 'ready' and registered == [result['account']['id']]
