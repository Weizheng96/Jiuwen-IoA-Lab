"""Real ASGI permissions, browser write protection, hot policy and source parsing."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import httpx
import pytest
from starlette.requests import Request

from ioalab.app import Platform
from ioalab.configuration import PlatformConfiguration,defaults
from ioalab.configuration_permissions import protect_new
from ioalab.config import Settings
from ioalab.errors import LabError
from ioalab.web.app import create_app
from ioalab.web.source import client_ip


@pytest.fixture
def configured(tmp_path):
    home=tmp_path/'config';home.mkdir();protect_new(home,directory=True)
    document=defaults();document['runtime'].update(mode='dev',stub=True,autoseed=False,data=str(tmp_path/'data'),public_url='http://localhost')
    document['admin']['username']='operator'
    config=PlatformConfiguration.create(home/'platform.json',document)
    platform=Platform(config.settings())
    try:yield platform,config
    finally:platform.close()


def test_browser_admin_configuration_roundtrip_and_denied_writes(configured):
    platform,config=configured
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(platform)),base_url='http://localhost') as client:
            assert (await client.get('/api/admin/configuration')).status_code==401
            assert (await client.post('/api/admin/login',json={'password':'admin1234'})).status_code==401
            response=await client.post('/api/admin/login',json={'username':'operator','password':'admin1234'})
            assert response.status_code==200
            view=(await client.get('/api/admin/configuration')).json()
            assert 'admin1234' not in str(view) and view['restart']['adminUsername']=='operator'
            body={'revision':1,'changes':{'credits':{'login':'13.00'},'authentication':{'max_failures':2}}}
            assert (await client.put('/api/admin/configuration',json=body)).status_code==403
            assert (await client.put('/api/admin/configuration',json=body,headers={'Origin':'http://localhost'})).status_code==403
            headers={'Origin':'http://localhost','X-IOALAB-CSRF':view['csrf']}
            response=await client.put('/api/admin/configuration',json=body,headers=headers)
            assert response.status_code==200,response.text
            assert platform.s.policy()['credits']['login']=='13.00'
            assert (await client.get('/api/config')).json()['loginCredit']=='13.00'
            assert (await client.put('/api/admin/configuration',json=body,headers=headers)).status_code==409
            unsafe={'revision':2,'changes':{'admin':{'password':'hacked'}}}
            assert (await client.put('/api/admin/configuration',json=unsafe,headers=headers)).status_code==400
            audit=platform.db.one("SELECT * FROM events WHERE op='configuration.update'")
            assert audit['sender']=='admin' and 'credits.login' in audit['detail'] and 'admin1234' not in audit['detail']
            user=platform.accounts.create_account('ordinary-user','test-password')
            client.cookies.set('ioalab_session',platform.auth.token(user['id']))
            assert (await client.get('/api/admin/configuration')).status_code==401
            client.cookies.clear()
            assert (await client.get('/api/admin/configuration',headers={'Authorization':'Bearer '+platform.auth.token('admin')})).status_code==403
    asyncio.run(scenario())


def test_https_ip_session_uses_one_host_cookie_and_logout_removes_it(configured,monkeypatch):
    platform,_=configured
    monkeypatch.setattr(platform.s,'public_url_env','https://192.0.2.10')
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(platform)),base_url='https://192.0.2.10') as client:
            response=await client.post('/api/admin/login',json={'username':'operator','password':'admin1234'})
            assert response.status_code==200
            value=response.headers['set-cookie']
            assert value.startswith('__Host-ioalab_session=')
            assert all(flag in value for flag in ('HttpOnly','Secure','Path=/')) and 'Domain=' not in value
            assert (await client.get('/api/admin/configuration')).status_code==200
            assert (await client.post('/api/auth/logout',headers={'Origin':'http://192.0.2.10'})).status_code==403
            assert (await client.get('/api/admin/configuration')).status_code==200
            client.cookies.clear();client.cookies.set('ioalab_session',platform.auth.token('admin'))
            assert (await client.get('/api/admin/configuration')).status_code==401
            client.cookies.clear()
            await client.post('/api/admin/login',json={'username':'operator','password':'admin1234'})
            response=await client.post('/api/auth/logout',json={})
            value=response.headers['set-cookie']
            assert value.startswith('__Host-ioalab_session=') and 'Max-Age=0' in value and 'Secure' in value
            assert (await client.get('/api/admin/configuration')).status_code==401
    asyncio.run(scenario())


def test_source_spoofing_and_explicit_distinct_proxy_contract():
    settings=Settings(trusted_proxy_addresses=('127.0.0.2',))
    def request(peer,headers):
        return Request({'type':'http','client':(peer,1234),'headers':headers})
    spoof=[(b'x-forwarded-for',b'127.0.0.1'),(b'x-ioalab-client-ip',b'127.0.0.1')]
    assert client_ip(request('203.0.113.9',spoof),settings)=='203.0.113.9'
    assert client_ip(request('127.0.0.1',[]),settings)=='127.0.0.1'
    assert client_ip(request('127.0.0.2',[(b'x-ioalab-client-ip',b'203.0.113.9')]),settings)=='203.0.113.9'
    for headers in ([],[(b'x-ioalab-client-ip',b'bad')],[(b'x-ioalab-client-ip',b'127.0.0.1,203.0.113.9')],spoof[1:]*2):
        with pytest.raises(LabError):client_ip(request('127.0.0.2',headers),settings)


def test_default_failure_budget_and_local_exemption_still_verify_password(configured):
    platform,config=configured
    auth=platform.auth
    for _ in range(40):
        with pytest.raises(LabError,match='口令错误'):auth.admin_login('wrong',username='operator',ip='203.0.113.5')
    with pytest.raises(LabError,match='频繁'):auth.admin_login('admin1234',username='operator',ip='203.0.113.5')
    for ip in ('127.0.0.1','::1'):
        for _ in range(41):
            with pytest.raises(LabError,match='口令错误'):auth.admin_login('wrong',username='operator',ip=ip)
        assert auth.verify(auth.admin_login('admin1234',username='operator',ip=ip))=='admin'
    config.update(1,{'authentication':{'max_failures':1}})
    with pytest.raises(LabError,match='口令错误'):auth.admin_login('wrong',username='operator',ip='198.51.100.1')
    with pytest.raises(LabError,match='频繁'):auth.admin_login('admin1234',username='operator',ip='198.51.100.1')


def test_configuration_concurrent_writers_publish_one_revision(configured):
    platform,config=configured
    def update(value):
        try:return config.update(1,{'credits':{'login':value}})['revision']
        except LabError as exc:return exc.status
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(update,['11.00','12.00']))
    assert sorted(results)==[2,409]
    assert PlatformConfiguration(config.path).view()==config.view()


def test_sdk_http_source_keeps_public_proxy_clients_out_of_loopback_exemption(configured):
    platform,config=configured
    platform.s.mode='trial';platform.s.trusted_proxy_addresses=('127.0.0.2',)
    platform.accounts.create_account('source-user','test-password',kind='virtual')
    config.update(1,{'authentication':{'max_failures':1}})
    async def scenario():
        app=create_app(platform)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=('127.0.0.2',1234)),base_url='http://localhost') as client:
            assert (await client.post('/api/v1/token',json={'username':'source-user','password':'test-password'})).status_code==403
            headers={'x-ioalab-client-ip':'203.0.113.77','x-forwarded-for':'127.0.0.1'}
            bad={'username':'source-user','password':'wrong','operationId':'remote-bad'}
            assert (await client.post('/api/v1/token',json=bad,headers=headers)).status_code==401
            good={'username':'source-user','password':'test-password','operationId':'remote-good'}
            denied=await client.post('/api/v1/token',json=good,headers=headers)
            assert denied.status_code==429 and '频繁' in denied.json()['error']['message']
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=('127.0.0.1',1234)),base_url='http://localhost') as local:
            for i in range(2):
                assert (await local.post('/api/v1/token',json={**bad,'operationId':f'local-bad-{i}'})).status_code==401
            assert (await local.post('/api/v1/token',json={**good,'operationId':'local-good'})).status_code==200
    asyncio.run(scenario())
