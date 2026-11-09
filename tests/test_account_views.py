"""Public/authenticated query ownership and response privacy after query extraction."""
from _support import new_user, username


def test_account_queries_keep_own_scope_and_do_not_mutate_business_facts(platform, client):
    owner, other = new_user(platform), new_user(platform)
    own_event = platform.bus.emit('platform', 'query.own', sender=owner['id'], detail={'private': 'hidden'})
    other_event = platform.bus.emit('platform', 'query.foreign', sender=other['id'], detail={'private': 'foreign secret'})
    client.cookies.clear()
    assert client.get('/api/workbench').status_code == 401
    token = platform.auth.token(owner['id'])
    client.headers['authorization'] = 'Bearer ' + token
    try:
        changes = platform.db.conn.total_changes
        me = client.get('/api/me').json()
        summary = client.get('/api/workbench').json()
        assert me['id'] == owner['id'] and 'pw_hash' not in me
        assert 'pending' not in me
        assert summary['account']['id'] == owner['id']
        assert own_event['seq'] in {event['seq'] for event in summary['events']}
        assert other_event['seq'] not in {event['seq'] for event in summary['events']}
        assert all('detail' not in event for event in summary['events'])
        assert client.get('/api/admin/users').status_code == 401
        assert client.get('/api/people/' + other['username'] + '/history').json() == {'messages': [], 'intents': [], 'contracts': []}
        assert platform.db.conn.total_changes == changes
        client.headers['authorization'] = 'Bearer ' + platform.auth.token('admin')
        admin = client.get('/api/admin/users').json()
        assert {owner['id'], other['id']} <= {row['id'] for row in admin}
        assert all('pw_hash' not in row for row in admin)
        assert platform.db.conn.total_changes == changes
        client.headers.pop('authorization')
        public = client.get('/api/public/assistants').json()
        assert all(set(row) == {'username', 'displayName', 'assistantNames'} for row in public)
        assert 'pw_hash' not in str(public)
    finally:
        client.headers.pop('authorization', None)


def test_v1_owner_queries_preserve_shape_scope_and_search_actor(platform, client, monkeypatch):
    owner, other = new_user(platform), new_user(platform)
    assistant, service, foreign = username(), username(), username()
    # Keep disabled assistant attribution and raw provider metadata: these public
    # query contracts differ deliberately from execution-admission checks.
    platform.db.run('INSERT INTO agents(id,owner_id,role,name,status,meta) VALUES(?,?,?,?,?,?)',
                    assistant, owner['id'], 'assistant', '助手', 'disabled', '{"protocolId":"original"}')
    platform.db.run('INSERT INTO agents(id,owner_id,role,name,status,meta) VALUES(?,?,?,?,?,?)',
                    service, owner['id'], 'service', '服务', 'online', '{"public":"original"}')
    platform.db.run('INSERT INTO agents(id,owner_id,role,name,status,meta) VALUES(?,?,?,?,?,?)',
                    foreign, other['id'], 'service', '他人服务', 'online', '{"private":"foreign"}')
    calls = []
    def search(query, top_k, *, by):
        calls.append((query, top_k, by))
        return []
    monkeypatch.setattr(platform.discovery_operations, 'search', search)
    client.cookies.clear()
    client.headers.pop('authorization', None)
    try:
        for path in ('/api/v1/me', '/api/v1/provider/mine'):
            assert client.get(path).status_code == 401
        assert client.post('/api/v1/search', json={'query': 'test'}).status_code == 401
        client.headers['authorization'] = 'Bearer ' + platform.auth.token('assistant:' + owner['id'])
        changes = platform.db.conn.total_changes
        response = client.get('/api/v1/me')
        assert response.status_code == 200
        assert response.json() == {
            'id': owner['id'], 'username': owner['username'], 'display_name': owner['display_name'],
            'kind': owner['kind'], 'wallet': str(platform.wallets.balance(owner['id'])),
            'roles': [], 'identities': [
                {'id': assistant, 'role': 'assistant', 'name': '助手', 'status': 'disabled', 'protocolId': 'original'},
                {'id': service, 'role': 'service', 'name': '服务', 'status': 'online', 'protocolId': service},
            ]}
        assert client.get('/api/v1/provider/mine').json() == [
            {'id': service, 'name': '服务', 'status': 'online', 'meta': '{"public":"original"}'}]
        assert client.post('/api/v1/search', json={'query': 'test', 'top_k': 100}).json() == {
            'hits': [], 'mode': platform.discovery.last_mode}
        assert calls == [('test', 20, assistant)]
        assert platform.db.conn.total_changes == changes
        client.headers['authorization'] = 'Bearer ' + platform.auth.token('assistant:' + other['id'])
        assert client.get('/api/v1/provider/mine').json() == [
            {'id': foreign, 'name': '他人服务', 'status': 'online', 'meta': '{"private":"foreign"}'}]
        assert client.post('/api/v1/search', json={'query': 'fallback', 'top_k': 0}).status_code == 200
        assert calls[-1] == ('fallback', 1, other['id'])
        client.headers['authorization'] = 'Bearer ' + platform.auth.token(owner['id'])
        assert client.get('/api/v1/me').status_code == 401
    finally:
        client.headers.pop('authorization', None)


def test_missing_history_and_replay_keep_original_not_found_responses(platform, client):
    owner = new_user(platform)
    client.cookies.clear()
    client.headers.pop('authorization', None)
    try:
        client.headers['authorization'] = 'Bearer ' + platform.auth.token(owner['id'])
        missing = client.get('/api/people/' + username() + '/history')
        assert missing.status_code == 404
        assert missing.json() == {'error': {'code': 'not_found', 'message': '用户不存在', 'recovery': 'correct_input'}}
        assert client.get('/api/admin/replay/' + username()).status_code == 401
        client.headers['authorization'] = 'Bearer ' + platform.auth.token('admin')
        missing = client.get('/api/admin/replay/' + username())
        assert missing.status_code == 404
        assert missing.json() == {'error': {'code': 'not_found', 'message': 'Not Found', 'recovery': 'correct_input'}}
    finally:
        client.headers.pop('authorization', None)


def test_identity_lookups_for_other_modules_locate_accounts_and_all_owned_agents(platform):
    owner, other = new_user(platform), new_user(platform)
    first, second, foreign = username(), username(), username()
    for actor, account, status in ((first, owner, 'registered'), (second, owner, 'disabled'), (foreign, other, 'registered')):
        platform.db.run('INSERT INTO agents(id,owner_id,role,name,status,meta) VALUES(?,?,?,?,?,?)',
                        actor, account['id'], 'service', actor, status, '{}')
    changes = platform.db.conn.total_changes
    assert platform.identities.account(owner['username']) == {'id': owner['id'], 'username': owner['username']}
    assert platform.identities.account(username()) is None and platform.identities.account(None) is None
    assert platform.identities.agent_ids(owner['id']) == sorted([first, second])
    assert platform.identities.agent_ids(username()) == []
    assert platform.db.conn.total_changes == changes
