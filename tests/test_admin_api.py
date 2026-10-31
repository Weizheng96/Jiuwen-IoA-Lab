"""Retain account, ledger, replay and discovery regressions across execution-boundary migration."""
from _support import as_admin,username
from test_resident_delegation import participants

def test_admin_api(platform,client,tmp_path):
    as_admin(client)
    u=client.post('/api/admin/users',json={'username':username(),'credit':'100'}).json()[0]
    assert platform.auth.login(u['username'],u['password'])['id']==u['id']
    users={x['id']:x for x in client.get('/api/admin/users').json()}
    assert users[u['id']]['agents']==[] and 'pw_hash' not in users[u['id']]
    assert client.post('/api/admin/wallet',json={'user_id':u['id'],'delta':'-40','note':'test'}).json()['balance']=='60.00'
    assert client.post('/api/admin/wallet',json={'user_id':u['id'],'delta':'-1000'}).status_code==400
    with participants(platform,tmp_path,formation='countersign',fee=0) as (provider,assistant,host,template,task):
        reg=client.get('/api/admin/registry').json()
        service=next(s for s in reg['services'] if s['id']==provider.identities[0].agent_id)
        assert service['url']==provider.identities[0].protocol_id and service['connected'] and service['published']
        assert 'signature' not in service['reputation']
        found=client.post('/api/admin/search',json={'query':service['name'],'top_k':20}).json()['hits']
        assert service['id'] in {hit['id'] for hit in found}
    assert platform.bus.run_id in client.get('/api/admin/runs').json()
    assert client.get('/api/admin/replay/'+platform.bus.run_id).json()
    assert client.get('/api/admin/replay/no-such-run').status_code==404
    assert len(client.get('/api/admin/events?limit=5').json())<=5
    assert client.post('/api/admin/invite').json()['code']
    assert client.get('/api/admin/orders').status_code==200

def test_components_traffic_and_pool(platform,client):
    as_admin(client)
    components=client.get('/api/admin/components').json()
    assert components['assistants']['runtime'] is False
    assert components['services']['location']=='independent-process'
    assert components['llm']['key_configured'] is False
    assert client.get('/api/admin/traffic').json()['enabled'] is False
    assert client.post('/api/admin/traffic',json={'level':'low'}).status_code==405
    assert client.post('/api/admin/dispatch-pool',json={'count':2}).status_code==404


def test_origin_host_must_match_exactly(platform,client):
    host=platform.s.base_url.split('://',1)[1]
    assert client.post('/api/admin/login',json={'password':'test'},headers={'Origin':'http://'+host+'.attacker.example'}).status_code==403
    assert client.post('/api/admin/login',json={'password':'test'},headers={'Origin':platform.s.base_url}).status_code==200
