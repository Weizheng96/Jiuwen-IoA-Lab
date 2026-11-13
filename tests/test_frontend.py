"""Public pages, real cookie gates and minimal public projections."""
import importlib.util
from pathlib import Path

import pytest

from _support import PASSWORD,username,new_user
from test_agent_delivery import people,peers,publish,request,headers

ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('path',['src/platform/ioalab/web/frontend.py','src/platform/ioalab/web/static/people.js','tools/check_frontend.cjs','tools/stamp_static.py'])
def test_frontend_changes_select_authentication_and_pair_privacy_regression(path):
    from tools.check_scope import select_tests
    assert 'tests/test_frontend.py' in select_tests([path])['tests']


def test_public_pages_and_server_side_workbench_gates(client):
    guide=client.get('/connect',follow_redirects=False)
    assert guide.status_code==307
    assert guide.headers['location']=='https://gitcode.com/WeiZheng96/jiuwen-IoA-lab/blob/main/README_AGENT.md'
    for page in ('/','/services','/assistants','/login','/register'):
        response=client.get(page)
        assert response.status_code==200
        assert '九问IoA实验室' in response.text
        assert '/static/session.js' in response.text and '/static/shell.js' in response.text
        assert 'mock-data' not in response.text and '独立前端原型' not in response.text
    for page in ('/workbench','/admin'):
        response=client.get(page,follow_redirects=False)
        assert response.status_code==303 and response.headers['location'].startswith('/login?next=')
    assert client.get('/api/workbench').status_code==401
    # A prototype/local fake session never authenticates the server.
    client.cookies.set('ioa-webui-preview-session','demo')
    assert client.get('/workbench',follow_redirects=False).status_code==303


def test_admin_settings_offer_only_trial_ports_and_describe_deployment_boundary(platform,client):
    assert client.post('/api/admin/login',json={'username':'admin','password':'test'}).status_code==200
    response=client.get('/admin')
    assert response.status_code==200
    assert 'id="cfgNativePort"' in response.text and 'id="cfgConfirmPort"' in response.text
    assert 'id="cfgCorePort"' not in response.text
    assert '保存后需由部署负责人应用' in response.text and '未验证公网可达' in response.text


def test_actual_login_cookie_logout_and_admin_permissions(client,platform):
    name=username()
    registered=client.post('/api/auth/register',json={'username':name,'password':PASSWORD,'operationId':'web-register'})
    assert registered.status_code==200
    cookie=next(cookie for cookie in client.cookies.jar if cookie.name=='ioalab_session')
    assert cookie.has_nonstandard_attr('HttpOnly')
    assert client.get('/workbench').status_code==200
    assert client.get('/api/workbench').json()['account']['username']==name
    assert client.get('/admin').status_code==403
    assert client.post('/api/auth/logout',json={}).status_code==200
    assert client.get('/workbench',follow_redirects=False).status_code==303
    assert client.post('/api/auth/login',json={'username':name,'password':'wrong'}).status_code==401
    request={'username':name,'password':PASSWORD,'operationId':'web-login'}
    assert client.post('/api/auth/login',json=request).status_code==200
    first=client.get('/api/me').json()['wallet']
    assert client.post('/api/auth/login',json=request).status_code==200
    assert client.get('/api/me').json()['wallet']==first
    client.post('/api/auth/logout',json={})
    assert client.post('/api/admin/login',json={'username':'admin','password':platform.s.admin_password}).status_code==200
    assert client.get('/admin').status_code==200
    assert client.get('/workbench',follow_redirects=False).headers['location']=='/admin'


def test_public_projections_do_not_expose_accounts_or_private_fields(client,platform):
    user=new_user(platform)
    secret_name=user['username']
    stats=client.get('/api/public/stats')
    assert stats.status_code==200 and set(stats.json())=={'services','assistants','interactions'}
    assert all(type(value) is int and value>=0 for value in stats.json().values())
    assert stats.json()['interactions']==platform.db.one('SELECT COUNT(*) AS n FROM delivery_interactions')['n']
    catalog=client.get('/api/public/catalog')
    assert catalog.status_code==200
    assert all(set(item)=={'id','name','status','category','description','url','published','indexReady','connected','templateAvailable','accepting','activeDelegations','maxActiveDelegations'} for item in catalog.json())
    categories=client.get('/api/public/categories')
    assert categories.status_code==200
    assert all(set(item)=={'id','name','description'} for item in categories.json())
    for response in (stats,catalog,categories):
        assert secret_name not in response.text and user['id'] not in response.text
        assert 'owner_id' not in response.text and 'pw_hash' not in response.text


def test_public_people_and_pair_history_enforce_viewer_ownership(client,platform,people,peers):
    recipient,sender,third=people
    public=client.get('/api/public/assistants')
    assert public.status_code==200
    assert all(set(row)=={'username','displayName','assistantNames'} for row in public.json())
    assert next(row for row in public.json() if row['username']==sender['username'])['assistantNames']==[platform.identities.agent(sender['actor'])['name']]
    assert 'pw_hash' not in public.text and sender['id'] not in public.text
    path='/api/people/'+recipient['username']+'/history'
    assert client.get(path).status_code==401
    publish(client,recipient).raise_for_status();publish(client,sender).raise_for_status()
    sent=request(recipient['actor'],body={'interfaceName':'meeting','revision':1,'input':{'text':'这两天一起聚个餐，什么时候有时间？'}})
    reverse=request(sender['actor'])
    first=peers(sender);second=peers(recipient);unrelated=peers(third)
    first.call('message.send',sent);second.call('message.send',reverse)
    other=request(recipient['actor'],body={'interfaceName':'meeting','revision':1,'input':{'text':'THIRD-PARTY-PRIVATE'}})
    unrelated.call('message.send',other)
    response=client.get(path,headers=headers(sender))
    assert response.status_code==200 and set(response.json())=={'messages','contracts','intents'}
    assert {row['id'] for row in response.json()['messages']}=={sent['messageId'],reverse['messageId']}
    assert '一起聚个餐' in response.text and 'THIRD-PARTY-PRIVATE' not in response.text
    outsider=client.get('/api/people/'+sender['username']+'/history',headers=headers(third)).json()
    assert outsider=={'messages':[],'contracts':[],'intents':[]}
    # Pair filtering must precede LIMIT even after a more recent third-party message.
    assert len(platform.message_views.rows(recipient['id'],peer=sender['id'],limit=1))==1
    assert client.get('/api/people/unknown-frontend-user/history',headers=headers(sender)).status_code==404
    before=platform.db.one('SELECT delivery_state FROM delivery_messages WHERE id=?',sent['messageId'])
    client.get(path,headers=headers(sender))
    assert platform.db.one('SELECT delivery_state FROM delivery_messages WHERE id=?',sent['messageId'])==before


def test_login_return_target_rejects_foreign_and_unrecognized_routes():
    import shutil
    import subprocess
    if not shutil.which('node'):pytest.skip('Node is required for the browser-independent JS contract check')
    script=r'''
const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const context={window:{},URL,URLSearchParams,location:{origin:'https://lab.example'},
document:{addEventListener(){},querySelector(){return null;}},addEventListener(){},
fetch:async()=>({ok:true,json:async()=>({role:null})})};
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),context);
const safe=context.window.WebSession.safeNext;
for(const value of ['https://evil.example/assistants','//evil.example/workbench','javascript:alert(1)','/api/admin/users','/unknown','https://name@lab.example/workbench'])assert.equal(safe(value),'/workbench');
assert.equal(safe('/assistants#alice%2Bfriend'),'/assistants#alice%2Bfriend');
assert.equal(safe('/workbench?view=provider&token=secret'),'/workbench?view=provider');
'''
    subprocess.run(['node','-e',script,str(ROOT/'src/platform/ioalab/web/static/session.js')],check=True,capture_output=True,text=True)
    subprocess.run(['node',str(ROOT/'tools/check_frontend.cjs')],check=True,capture_output=True,text=True)


def test_pair_contract_history_excludes_third_party_even_with_public_profiles(platform,client,tmp_path):
    from _support import new_user
    from conftest import wait_for
    from test_resident_delegation import participants,request as delegate,terminal
    with participants(platform,tmp_path,fee=0) as (provider,assistant,host,template,task):
        started,_=delegate(assistant,provider,template,task)
        assert started['status']=='accepted'
        wait_for(lambda:terminal(assistant,started['data']['quoteRef']))
        result=terminal(assistant,started['data']['quoteRef'])
        assert result['status']=='completed'
        owner=assistant.account.account;peer=provider.account.account
        path='/api/people/'+peer['username']+'/history'
        history=client.get(path,headers={'Authorization':'Bearer '+assistant.account.token})
        assert history.status_code==200
        assert any(row['admissionId']==result['data']['admissionId'] for row in history.json()['contracts'])
        third=new_user(platform)
        outsider=client.get(path,headers={'Authorization':'Bearer '+platform.auth.bearer(third['id'])}).json()
        assert outsider=={'messages':[],'contracts':[],'intents':[]}
        assert platform.contract_views.rows(owner['id'],peer=third['id'])==[]
        assert platform.contract_views.intents(owner['id'],peer=third['id'])==[]


def test_all_html_assets_are_stamped_and_stale_changes_fail(tmp_path,monkeypatch):
    spec=importlib.util.spec_from_file_location('stamp_frontend',ROOT/'tools/stamp_static.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.setattr(module,'ROOT',tmp_path)
    (tmp_path/'site.js').write_text('const ready=true;\n',encoding='utf-8')
    (tmp_path/'logo.svg').write_text('<svg/>',encoding='utf-8')
    for name in ('index.html','workbench.html','auth.html'):
        (tmp_path/name).write_text('<script src="/static/site.js"></script><link rel="icon" href="/static/logo.svg">',encoding='utf-8')
    monkeypatch.setattr('sys.argv',['stamp_static'])
    module.main()
    monkeypatch.setattr('sys.argv',['stamp_static','--check'])
    module.main()
    (tmp_path/'site.js').write_text('const ready=false;\n',encoding='utf-8')
    with pytest.raises(SystemExit,match='stamp_static'):module.main()
    monkeypatch.setattr('sys.argv',['stamp_static'])
    module.main()
    monkeypatch.setattr('sys.argv',['stamp_static','--check'])
    module.main()

    (tmp_path/'logo.svg').write_text('<svg viewBox="0 0 27 27"/>',encoding='utf-8')
    with pytest.raises(SystemExit,match='stamp_static'):module.main()
    monkeypatch.setattr('sys.argv',['stamp_static'])
    module.main()
    monkeypatch.setattr('sys.argv',['stamp_static','--check'])
    module.main()
