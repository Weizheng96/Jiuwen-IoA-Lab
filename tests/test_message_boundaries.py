"""Current WS admission limits and metadata privacy, without legacy inbox executors."""
import json

from test_agent_delivery import people,peers,publish,request


def test_queue_limit_and_public_events_do_not_reveal_message_body(platform,client,people,peers,monkeypatch):
    recipient,sender,_=people
    publish(client,recipient,[sender['username']]).raise_for_status()
    monkeypatch.setattr(platform.s,'message_pending_limit',1)
    peer=peers(sender)
    value=request(recipient['actor'],body={'interfaceName':'meeting','revision':1,'input':{'text':'PRIVATE-BODY-MARKER'}})
    assert peer.call('message.send',value)['messageId']==value['messageId']
    assert peer.call('message.send',request(recipient['actor']),ok=False)['code']=='quota_exceeded'
    events=[event for event in platform.bus.recent(100) if event['sender']==sender['actor']]
    assert any(event['op']=='message.queued' for event in events)
    assert 'PRIVATE-BODY-MARKER' not in json.dumps(events)
    assert platform.db.one('SELECT COUNT(*) AS n FROM delivery_messages WHERE recipient=?',recipient['actor'])['n']==1


def test_restricted_account_cannot_read_admin_message_audit(platform,client):
    admin=platform.db.one("SELECT id FROM users WHERE username='ISC_001'")['id']
    assert platform.auth.is_admin(admin)
    assert not platform.auth.is_admin('assistant:'+admin)
    denied=client.get('/api/admin/messages',headers={'Authorization':'Bearer '+platform.accounts.login('ISC_001',platform.s.assistant_password or '001',audience='sdk')['token']})
    assert denied.status_code==401
    allowed=client.get('/api/admin/messages',headers={'Authorization':'Bearer '+platform.auth.bearer(admin)})
    assert allowed.status_code==200 and all('payload' not in row for row in allowed.json())


def test_retired_polling_queue_routes_cannot_start_a_second_execution_chain(platform,client,people):
    actor=people[0]
    headers={'Authorization':'Bearer '+actor['token']}
    for path in ('messages','inbox','ack','claim','complete'):
        response=client.post('/api/v1/messaging/'+path,headers=headers,json={})
        assert response.status_code in (404,405),path
    assert client.put('/api/v1/messaging/interfaces',headers=headers,json={}).status_code in (404,405)
