"""The formal account grants entry only, never a trial business credential."""
import base64
import hashlib
import hmac
import json
import asyncio
from urllib.parse import urlsplit

import pytest
import httpx

from _support import new_user
from ioalab.identity.trial import entry
from ioalab.errors import LabError
from ioalab.web.app import create_app
from ioalab.web.cookies import session_cookie_name


def test_trial_entry_requires_real_cookie_and_same_origin(client, platform, monkeypatch):
    assert client.get('/trial', follow_redirects=False).headers['location'] == '/login?next=/trial'
    origin = {'origin': platform.s.base_url}
    assert client.post('/api/trial/entry', headers=origin).status_code == 401
    user = new_user(platform)
    client.cookies.set('ioalab_session', platform.auth.token(user['id']))
    assert client.get('/trial').status_code == 200
    assert client.post('/api/trial/entry').status_code == 403
    assert client.post('/api/trial/entry', headers={'origin': 'https://other.test'}).status_code == 403
    monkeypatch.setattr(platform.s, 'trial_url', 'https://trial.example.test')
    monkeypatch.setattr(platform.s, 'trial_admission_key', 'a' * 40)
    result = client.post('/api/trial/entry', headers=origin)
    assert result.status_code == 200
    token = urlsplit(result.json()['url']).fragment
    payload, signature = token.split('.')
    assert hmac.compare_digest(signature, hmac.new(b'a' * 40, payload.encode(), hashlib.sha256).hexdigest())
    body = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
    assert body['owner'] == user['id'] and body['aud'] == platform.s.trial_url
    assert body['exp'] - body['iat'] == 60
    second = client.post('/api/trial/entry', headers=origin).json()
    assert second['url'] != result.json()['url']
    monkeypatch.setattr(platform.s, 'trial_confirm_url', 'https://confirm.example.test')
    trusted = client.post('/api/trial/confirmation-entry', headers=origin)
    assert trusted.status_code == 200
    encoded = urlsplit(trusted.json()['url']).fragment.split('.')[0]
    assert json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))['aud'] == 'https://confirm.example.test'
    assert client.post('/api/trial/confirmation-entry', headers={'origin': 'https://trial.example.test'}).status_code == 403
    monkeypatch.setattr(platform.s, 'trial_confirm_url', 'https://trial.example.test:9443')
    assert client.post('/api/trial/confirmation-entry', headers=origin).status_code == 503
    client.cookies.set('ioalab_session', platform.auth.token('assistant:' + user['id']))
    assert client.post('/api/trial/entry', headers=origin).status_code == 401


@pytest.mark.parametrize('url,key', [('', ''), ('https://trial.test', 'short'),
    ('http://public.test', 'a' * 40), ('https://trial.test/path', 'a' * 40),
    ('https://user:pass@trial.test', 'a' * 40), ('https://trial.test?x=1', 'a' * 40)])
def test_trial_unconfigured_or_unsafe_destination_stays_unready(platform, url, key, monkeypatch):
    monkeypatch.setattr(platform.s, 'trial_url', url)
    monkeypatch.setattr(platform.s, 'trial_admission_key', key)
    with pytest.raises(LabError) as error:
        entry(platform.s, 'owner')
    assert error.value.code == 'not_ready'


@pytest.mark.parametrize('confirm,expected',[
    ('https://192.0.2.10:9444',200),
    ('https://192.0.2.10:9443',503),
    ('https://192.0.2.10',503),
    ('https://192.0.2.10:443',503),
    ('https://192.0.2.10:0',503),
    ('https://192.0.2.10:invalid',503),
    ('http://192.0.2.10:9444',503)])
def test_https_ip_formal_buttons_issue_only_distinct_origin_tickets(platform,monkeypatch,confirm,expected):
    formal='https://192.0.2.10'
    monkeypatch.setattr(platform.s,'public_url_env',formal)
    monkeypatch.setattr(platform.s,'trial_url',formal+':9443')
    monkeypatch.setattr(platform.s,'trial_confirm_url',confirm)
    monkeypatch.setattr(platform.s,'trial_admission_key','a'*40)
    user=new_user(platform)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(platform)),base_url=formal) as client:
            client.cookies.set(session_cookie_name(formal),platform.auth.token(user['id']))
            assert (await client.get('/trial')).status_code==200
            native=await client.post('/api/trial/entry',headers={'Origin':formal})
            assert native.status_code==200
            trusted=await client.post('/api/trial/confirmation-entry',headers={'Origin':formal})
            assert trusted.status_code==expected
            if expected==200:
                token=urlsplit(trusted.json()['url']).fragment.split('.')[0]
                body=json.loads(base64.urlsafe_b64decode(token+'='*(-len(token)%4)))
                assert body['aud']==confirm and body['owner']==user['id']
            assert (await client.post('/api/trial/confirmation-entry',headers={'Origin':formal+':9443'})).status_code==403
    asyncio.run(scenario())
