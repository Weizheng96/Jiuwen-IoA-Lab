"""Durability, permissions and separation between inbox synchronization and effects."""
import time
import uuid
import httpx
import pytest
from jsonschema.exceptions import ValidationError
from fastapi import HTTPException
from ioalab_sdk import LabClient
from ioalab_sdk.messaging import MailboxClient
from _support import new_user,PASSWORD

INPUT={'type':'object','properties':{'text':{'type':'string','maxLength':100}},'required':['text'],'additionalProperties':False}

@pytest.fixture
def pair(platform,tmp_path):
    users=[new_user(platform) for _ in range(3)]
    for i,u in enumerate(users):
        c=LabClient(platform.s.base_url,u['username'],PASSWORD,tmp_path/str(i));c.close()
    boxes=[MailboxClient(platform.s.base_url,u['username'],PASSWORD,tmp_path/str(i)) for i,u in enumerate(users)]
    boxes[0].publish('message.forward',{'inputSchema':INPUT,'allowedSenders':[users[1]['username']]})
    yield users,boxes
    for b in boxes: b.close()

def test_schema_acl_and_exact_lookup(platform,pair):
    users,(a,b,c)=pair
    assert c.interfaces(users[0]['username'])==[]
    assert b.interfaces(users[0]['username'])[0]['inputSchema']==INPUT
    assert 'allowedSenders' not in b.interfaces(users[0]['username'])[0]
    with pytest.raises(ValidationError): b.send(users[0]['username'],'message.forward',{'text':3})
    for definition in ({}, {'inputSchema':{}}, {'inputSchema':True}, {'inputSchema':{'type':'object','properties':[]}}, {'inputSchema':{'$ref':'https://example.com/schema'}}, {'inputSchema':INPUT,'price':1}):
        with pytest.raises(httpx.HTTPStatusError) as exc: a.publish('bad',definition)
        assert exc.value.response.status_code==400
    with pytest.raises(httpx.HTTPStatusError) as exc:
        c.api('POST','/messaging/messages',{'to':users[0]['username'],'interface':'message.forward','revision':1,'payload':{'text':'private'},'idempotencyKey':'blocked'})
    assert exc.value.response.status_code==403

def test_offline_cursor_ack_and_effect_once(platform,pair,tmp_path):
    users,(a,b,c)=pair
    message=b.send(users[0]['username'],'message.forward',{'text':'offline'},idempotency_key='stable')
    assert message['state']=='queued'
    assert b.send(users[0]['username'],'message.forward',{'text':'offline'},idempotency_key='stable')['id']==message['id']
    with pytest.raises(ValueError): b.send(users[0]['username'],'message.forward',{'text':'changed'},idempotency_key='stable')
    # A UI read must not acknowledge or consume the inbox.
    assert a.get(message['id'])['state']=='queued'
    a.sync();assert b.get(message['id'])['state']=='synced'
    called=[]
    a.process(lambda m: called.append(m['id']) or {'status':'delivered'})
    a.sync();a.process(lambda m: called.append('duplicate') or {})
    assert called==[message['id']]
    assert b.get(message['id'])['result']=={'status':'delivered'}
    with pytest.raises(httpx.HTTPStatusError) as exc: c.get(message['id'])
    assert exc.value.response.status_code==403
    # Reopening the durable mailbox preserves cursor and results.
    reopened=MailboxClient(a.base,a.username,PASSWORD,a.home)
    assert reopened.sync()['messages']==[]
    reopened.close()

def test_claim_lease_expiry_acl_revocation_and_revision(platform,pair):
    users,(a,b,c)=pair
    m=b.send(users[0]['username'],'message.forward',{'text':'lease'})
    lease=a.claim(m['id'])['lease']
    with pytest.raises(httpx.HTTPStatusError) as exc: a.claim(m['id'])
    assert exc.value.response.status_code==409
    with pytest.raises(httpx.HTTPStatusError): a.complete(m['id'],'wrong',{})
    a.complete(m['id'],lease,{'status':'okay'})
    m=b.send(users[0]['username'],'message.forward',{'text':'revoke'})
    a.publish('message.forward',{'inputSchema':INPUT,'allowedSenders':[]})
    result=a.claim(m['id']);assert result['lease'] is None
    assert result['message']['result']['status']=='permission_revoked'
    a.publish('message.forward',{'inputSchema':INPUT})
    m=b.send(users[0]['username'],'message.forward',{'text':'expiry'})
    platform.db.run('UPDATE messages SET expires=? WHERE id=?',time.time()-1,m['id'])
    assert a.get(m['id'])['state']=='expired'
    with pytest.raises(httpx.HTTPStatusError): a.complete(m['id'],lease,{})

def test_restricted_admin_sdk_and_private_events(platform,client,pair):
    users,(a,b,c)=pair
    admin=platform.db.one("SELECT id FROM users WHERE username='ISC_001'")['id']
    assert platform.auth.is_admin(admin)
    assert not platform.auth.is_admin('assistant:'+admin)
    r=client.get('/api/admin/messages',headers={'Authorization':'Bearer '+platform.auth.bearer(admin,restricted=True)})
    assert r.status_code==401
    r=client.get('/api/admin/messages',headers={'Authorization':'Bearer '+platform.auth.bearer(admin)})
    assert r.status_code==200 and all('payload' not in m for m in r.json())
    r=client.get('/api/workbench',headers={'Authorization':'Bearer '+platform.auth.bearer(users[0]['id'])})
    assert r.status_code==200 and r.json()['account']['id']==users[0]['id']

def test_mail_first_identity_is_shared(platform,tmp_path):
    u=new_user(platform)
    box=MailboxClient(platform.s.base_url,u['username'],PASSWORD,tmp_path,connect_identity=True)
    identity=box.api('GET','/me')['identities'];box.close()
    local=LabClient(platform.s.base_url,u['username'],PASSWORD,tmp_path)
    assert any(i['id']==local.signer.id for i in identity)
    local.close()

def test_capacity_retention_and_private_event_metadata(platform,pair,monkeypatch):
    users,(a,b,c)=pair
    monkeypatch.setattr(platform.s,'message_pending_limit',1)
    m=b.send(users[0]['username'],'message.forward',{'text':'PRIVATE-BODY-MARKER'})
    with pytest.raises(httpx.HTTPStatusError) as exc: b.send(users[0]['username'],'message.forward',{'text':'overflow'})
    assert exc.value.response.status_code==409
    events=[e for e in platform.bus.recent(100) if e['op'].startswith('message.')]
    import json
    assert 'PRIVATE-BODY-MARKER' not in json.dumps(events)
    lease=a.claim(m['id'])['lease'];a.complete(m['id'],lease,{'status':'okay'})
    platform.db.run('UPDATE messages SET retain_until=? WHERE id=?',time.time()-1,m['id'])
    retained=a.get(m['id']);assert retained['payload'] is None and retained['result'] is None
    assert retained['state']=='completed'

def test_windows_json_file_cli(platform,pair,tmp_path):
    import os,sys,json,subprocess
    from pathlib import Path
    users,(a,b,c)=pair
    params=tmp_path/'message.json';params.write_text(json.dumps({'to':users[0]['username'],'interface':'message.forward','payload':{'text':'从UTF-8文件传参'}}),encoding='utf-8-sig')
    env={**os.environ,'PYTHONUTF8':'1','IOALAB_BASE_URL':platform.s.base_url,'IOALAB_USERNAME':b.username,'IOALAB_PASSWORD':PASSWORD,'IOALAB_HOME':str(b.home),'PYTHONPATH':str(Path(__file__).resolve().parents[1]/'sdk')}
    result=subprocess.run([sys.executable,'-m','ioalab_sdk.cli','mail','send','--args-file',str(params)],env=env,capture_output=True,text=True,encoding='utf-8',timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
    assert json.loads(result.stdout)['payload']['text']=='从UTF-8文件传参'

def test_client_offline_beyond_server_retention_and_permanent_outbox_error(platform,pair):
    users,(a,b,c)=pair
    m=b.send(users[0]['username'],'message.forward',{'text':'old local copy'});a.sync()
    platform.db.run('UPDATE messages SET created=? WHERE id=?',time.time()-100*86400,m['id'])
    a.process(lambda m:pytest.fail('A purged message must never produce an effect'))
    assert a.store.get('inbox',m['id'])['state']=='expired'
    b.store.put('outgoing','offline-expired',{'request':{'to':users[0]['username'],'interface':'message.forward','revision':1,'payload':{'text':'late'},'idempotencyKey':'offline-expired','expiresAt':time.time()-1}})
    b.flush();assert b.store.get('outgoing','offline-expired')['failed']['statusCode']==400
