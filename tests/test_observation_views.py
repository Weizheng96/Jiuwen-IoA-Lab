"""Real HTTP/WS read models; native host/user actions here are explicit test drivers."""
import json

import pytest
from websockets.sync.client import connect

from _support import as_admin,new_user
from conftest import wait_for
from ioalab_sdk.runtime.host import ToolBinding
from test_assistant_role import runtime_at,ScriptedHost,INPUT,publish,send_and_read


def headers(platform,user,*,restricted=False):
    return {'Authorization':'Bearer '+platform.auth.bearer(user['id'],restricted=restricted)}


def test_web_roles_are_explicit_atomic_and_not_granted_to_restricted_sdk(platform,client):
    administrator={'Authorization':'Bearer '+platform.auth.token('admin')}
    target=new_user(platform);url='/api/admin/users/'+target['id']+'/roles'
    initial={'roles':['admin','auditor'],'expectedRoles':[]}
    restricted={'Authorization':'Bearer '+platform.auth.bearer(platform.db.one("SELECT id FROM users WHERE username='ISC_001'")['id'],restricted=True)}
    assert client.put(url,headers=restricted,json=initial).status_code==401
    assert client.put(url,headers=administrator,json={'roles':['auditor'],'expectedRoles':[]}).status_code==400
    first=client.put(url,headers=administrator,json=initial)
    assert first.status_code==200 and first.json()['changed'] is True
    again=client.put(url,headers=administrator,json=initial)
    assert again.status_code==200 and again.json()['changed'] is False
    assert platform.db.one('SELECT COUNT(*) AS n FROM identity_role_changes WHERE target=?',target['id'])['n']==1
    assert client.put(url,headers=administrator,json={'roles':['admin'],'expectedRoles':[]}).status_code==409
    assert client.put(url,headers=administrator,json={'roles':['unknown'],'expectedRoles':['admin','auditor']}).status_code==400
    assert platform.auth.is_auditor(target['id']) and not platform.auth.is_auditor('assistant:'+target['id'])
    revoked=client.put(url,headers=administrator,json={'roles':['admin'],'expectedRoles':['admin','auditor']})
    assert revoked.status_code==200 and not platform.auth.is_auditor(target['id'])
    assert not platform.auth.is_auditor('admin')


def test_admin_current_history_replay_and_websocket_share_metadata_boundary(platform,client):
    as_admin(client)
    marker='PRIVATE-CONTENT-MUST-NOT-APPEAR-IN-MONITORING'
    event=platform.bus.emit('observation','discovery.compared',sender='admin',detail={
        'source':'client','trust':'reported','data':{'query':marker},'password':marker,
        'payload':{'message':marker},'card':{'spec':marker}})
    current=client.get('/api/admin/events',params={'after':event['seq']-1})
    assert current.status_code==200 and marker not in current.text
    assert any(row['seq']==event['seq'] for row in current.json())
    replay=client.get('/api/admin/replay/'+platform.bus.run_id)
    assert replay.status_code==200 and marker not in replay.text
    assert any(row['seq']==event['seq'] for row in replay.json())
    cookie='; '.join(name+'='+value for name,value in client.cookies.items())
    address=platform.s.base_url.replace('http://','ws://').replace('https://','wss://')+'/ws/admin'
    with connect(address,additional_headers={'Cookie':cookie},origin=platform.s.base_url,proxy=None) as socket:
        seen=False
        for _ in range(100):
            frame=socket.recv(timeout=5)
            assert marker not in frame
            if json.loads(frame)['seq']==event['seq']:seen=True;break
        assert seen


def test_resident_messages_are_visible_only_to_parties_and_explicit_auditors_without_consuming_inbox(platform,client,tmp_path):
    marker='PRIVATE-RESIDENT-REQUEST'
    instructions='PRIVATE-LOCAL-HANDLING-INSTRUCTIONS'
    host=ScriptedHost();effects=[]
    def reserve(arguments,context):effects.append(context);return {'status':'reserved'}
    tool=ToolBinding('reserve_slot','Isolated business driver',INPUT,reserve,'reservation')
    with runtime_at(platform,tmp_path/'sender') as (sender,rpc,_,user):
        with runtime_at(platform,tmp_path/'receiver',host,[tool]) as (receiver,inbox,module,target):
            publish(receiver,inbox,user['username'],instructions=instructions)
            interface=rpc.call('assistant.messaging.interfaces',{'account':target['username']})['data'][0]['interfaceRef']
            sent,result=send_and_read(rpc,{'interfaceRef':interface,'input':{'slot':marker}})
            mid=sent['data']['messageRef']
            actors=(sender.identities[0].agent_id,receiver.identities[0].agent_id)
            wait_for(lambda:all(r['ack_seq']==r['last_seq'] for r in platform.db.rows('SELECT * FROM delivery_mailboxes WHERE actor IN (?,?)',*actors)))
            def saved():
                return platform.db.rows('SELECT actor,last_seq,ack_seq FROM delivery_mailboxes WHERE actor IN (?,?) ORDER BY actor',*actors)
            before=saved()
            for account in (user,target):
                response=client.get('/api/workbench',headers=headers(platform,account))
                assert response.status_code==200 and marker in response.text and instructions not in response.text
                message=next(row for row in response.json()['messages'] if row['id']==mid)
                assert message['interactionId']==result['interactionRef']
                assert message['deliveryState']=='delivered' and message['requestState']=='completed'
                assert message['readState']=='unknown' and message['result']['output']=={'status':'reserved'}
            assert saved()==before and len(effects)==1
            outsider=new_user(platform)
            response=client.get('/api/workbench',headers=headers(platform,outsider))
            assert response.status_code==200 and marker not in response.text and mid not in response.text
            admin={'Authorization':'Bearer '+platform.auth.token('admin')}
            metadata=client.get('/api/admin/messages',headers=admin)
            assert metadata.status_code==200 and marker not in metadata.text and instructions not in metadata.text
            assert next(row for row in metadata.json() if row['id']==mid)['canAudit'] is False
            assert client.get('/api/admin/messages/'+mid,headers=admin).status_code==403
            auditor=new_user(platform);url='/api/admin/users/'+auditor['id']+'/roles'
            assert client.put(url,headers=admin,json={'roles':['admin','auditor'],'expectedRoles':[]}).status_code==200
            response=client.get('/api/admin/messages/'+mid,headers=headers(platform,auditor))
            assert response.status_code==200 and marker in response.text and instructions not in response.text
            assert client.get('/api/admin/messages/'+mid,headers=headers(platform,auditor,restricted=True)).status_code==403
            assert platform.db.one("SELECT sender FROM events WHERE op='message.audit' ORDER BY seq DESC LIMIT 1")['sender']==auditor['id']
            events=client.get('/api/admin/events',headers=admin)
            assert marker not in events.text and instructions not in events.text
            assert client.put(url,headers=admin,json={'roles':['admin'],'expectedRoles':['admin','auditor']}).status_code==200
            assert client.get('/api/admin/messages/'+mid,headers=headers(platform,auditor)).status_code==403


def test_quote_fee_and_pending_intent_are_visible_before_a_contract_card_exists(platform,client,tmp_path):
    import threading
    from test_resident_delegation import participants,QuoteHost,request,terminal
    host=QuoteHost();host.wait=threading.Event()
    with participants(platform,tmp_path,agent_host=host) as (provider,assistant,_,template,task):
        begun,_=request(assistant,provider,template,task)
        try:
            wait_for(lambda:bool(host.turns))
            service_id=provider.identities[0].agent_id
            owner_headers={'Authorization':'Bearer '+platform.auth.bearer(provider.account.account['id'])}
            data=client.get('/api/workbench',headers=owner_headers).json()
            service=next(row for row in data['services'] if row['id']==service_id)
            assert service['earned']=='1.00' and service['proposalFeesEarned']=='1.00' and service['commissionEarned']=='0.00'
            assert service['orders']==[] and len(service['intents'])==1
            intent=service['intents'][0]
            assert intent['phase']=='等待正式提案' and intent['proposalFeePaid']=='1.00' and intent['admissionState']=='committed'
            assert intent['interactionId'] and 'card_digest' not in intent and 'total' not in intent
            admin={'Authorization':'Bearer '+platform.auth.token('admin')}
            all_intents=client.get('/api/admin/delegation-intents',headers=admin)
            assert all_intents.status_code==200 and any(row['originMessageId']==intent['originMessageId'] for row in all_intents.json())
            assert all('details' not in row and 'request' not in row and 'inputs' not in row for row in all_intents.json())
            assert client.get('/api/admin/needs',headers=admin).status_code==404
        finally:host.wait.set()
        wait_for(lambda:terminal(assistant,begun['data']['quoteRef']))
        completed=client.get('/api/workbench',headers=owner_headers).json()
        service=next(row for row in completed['services'] if row['id']==service_id)
        assert service['intents']==[] and len(service['orders'])==1
        assert service['orders'][0]['effective'] is False and service['earned']=='1.00'


def test_role_update_rechecks_operator_after_waiting_for_transaction(platform,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager
    import threading
    from ioalab.errors import LabError
    operator=new_user(platform);target=new_user(platform)
    platform.auth.set_roles('admin',operator['id'],['admin'],[])
    entered=threading.Event();release=threading.Event();original=platform.db.tx
    @contextmanager
    def delayed():
        if threading.current_thread().name.startswith('role-change'):
            entered.set();assert release.wait(5)
        with original() as conn:yield conn
    monkeypatch.setattr(platform.db,'tx',delayed)
    with ThreadPoolExecutor(max_workers=1,thread_name_prefix='role-change') as pool:
        future=pool.submit(platform.auth.set_roles,operator['id'],target['id'],['admin','auditor'],[])
        try:
            assert entered.wait(5)
            platform.auth.set_roles('admin',operator['id'],[],['admin'])
        finally:release.set()
        with pytest.raises(LabError) as rejected:future.result(timeout=5)
    assert rejected.value.code=='forbidden' and platform.auth.roles(target['id'])==[]
