"""Public/authenticated query ownership and response privacy after query extraction."""
from _support import new_user


def test_account_queries_keep_own_scope_and_do_not_mutate_business_facts(platform, client):
    owner, other = new_user(platform), new_user(platform)
    platform.db.run("INSERT INTO confirmations(id,owner_id,text) VALUES(?,?,?)", 'mine-' + owner['id'], owner['id'], 'own pending')
    platform.db.run("INSERT INTO confirmations(id,owner_id,text) VALUES(?,?,?)", 'foreign-' + other['id'], other['id'], 'foreign secret')
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
        assert [row['text'] for row in me['pending']] == ['own pending']
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
