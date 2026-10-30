"""Durable local transport tested against the real participant WebSocket."""
import threading

import pytest

from ioalab_sdk.runtime import AgentConnection, RuntimeFault, RuntimeStore
from test_agent_delivery import people, publish, request, result, at, uid, headers, Peer
from conftest import wait_for


def store_at(tmp_path, person, base):
    return RuntimeStore(tmp_path/'runtime.sqlite',base,[person['actor']],owner_id=person['id'])


def test_execution_stops_at_heartbeat_deadline_even_with_a_live_socket(platform,people,tmp_path):
    person=people[0]
    clock=[0.0]
    store=store_at(tmp_path,person,platform.s.base_url)
    connection=AgentConnection(platform.s.base_url,person['token'],[person['actor']],store,clock=lambda:clock[0])
    ended=threading.Event();errors=[]
    def once():
        try:connection._session()
        except OSError as exc:errors.append(str(exc))
        finally:ended.set()
    thread=threading.Thread(target=once)
    thread.start()
    try:
        wait_for(lambda:connection.connected)
        assert connection._socket is not None
        clock[0]=platform.s.ws_lease_seconds+1
        assert not connection.connected
        assert ended.wait(5)
        assert errors==['Execution heartbeat lease expired']
    finally:
        connection.close();thread.join(5);store.close()


def delivery(actor, seq, mid=None, **extra):
    return {'type':'message.deliver','agentId':actor,'recipientSeq':seq,'deliveryState':'queued',
            'message':{'messageId':mid or uid(),'kind':'message.request','recipient':actor,'recipientSeq':seq, **extra}}


def test_local_cursor_only_advances_over_contiguous_saved_messages(tmp_path):
    store = RuntimeStore(tmp_path/'runtime.sqlite','http://localhost',['a'],owner_id='account')
    one,two = delivery('a',1),delivery('a',2)
    assert store.receive(two)==0 and store.pending()==[]
    assert store.receive(one)==2
    assert store.pending()[0]['payload']=={'messageId':two['message']['messageId'],'through':2}
    assert store.receive(two)==2
    assert len(store.messages('a'))==2
    with pytest.raises(RuntimeFault,match='cannot be replaced'):
        store.receive(delivery('a',2))
    assert store.cursors()=={'a':2}
    runtime = store.runtime_id
    store.close()
    reopened = RuntimeStore(tmp_path/'runtime.sqlite','http://localhost',['a','service'],owner_id='account')
    assert reopened.runtime_id==runtime and reopened.cursors()=={'a':2,'service':0}
    reopened.close()
    with pytest.raises(RuntimeFault,match='another platform or account'):
        RuntimeStore(tmp_path/'runtime.sqlite','http://other',['a','service'],owner_id='account')


def test_operation_payload_is_saved_before_send_and_cannot_change(tmp_path):
    store = RuntimeStore(tmp_path/'runtime.sqlite','http://localhost',['a'],owner_id='account')
    operation = store.prepare('a','message.send',{'messageId':uid()})
    original = store.pending()[0]['payload']
    assert store.prepare('a','message.send',original,operation)==operation
    with pytest.raises(RuntimeFault,match='cannot change'):
        store.prepare('a','message.send',{'messageId':uid()},operation)
    store.close()
    reopened = RuntimeStore(tmp_path/'runtime.sqlite','http://localhost',['a'],owner_id='account')
    assert reopened.pending()[0]['id']==operation
    reopened.close()


def test_local_uuid_and_outbox_roll_back_together_on_failure(tmp_path, monkeypatch):
    store = RuntimeStore(tmp_path/'runtime.sqlite','http://localhost',['a'],owner_id='account')
    frame = delivery('a',1,sender='b',expiresAt=at(),body={'interfaceName':'meeting','revision':1})
    store.receive(frame)
    prepare = store._prepare
    def broken(*args, **kwargs):
        raise OSError('disk failure while saving opened outbox')
    monkeypatch.setattr(store,'_prepare',broken)
    with pytest.raises(OSError,match='disk failure'):
        store.prepare_open('a',frame['message']['messageId'])
    assert store.interaction(origin=frame['message']['messageId']) is None
    monkeypatch.setattr(store,'_prepare',prepare)
    assert store.prepare_open('a',frame['message']['messageId'])['interactionId']
    store.close()


def test_sdk_round_trip_restart_and_receiver_uuid_is_one_transaction(platform, client, people, tmp_path):
    a,b,_ = people
    publish(client,a,[b['username']]).raise_for_status()
    a_store,b_store = store_at(tmp_path/'a',a,platform.s.base_url),store_at(tmp_path/'b',b,platform.s.base_url)
    sender = AgentConnection(platform.s.base_url,b['token'],[b['actor']],b_store)
    receiver = AgentConnection(platform.s.base_url,a['token'],[a['actor']],a_store)
    try:
        sender.start()
        original = request(a['actor'])
        receipt = sender.call(b['actor'],'message.send',original)
        assert receipt['status']=='accepted' and a_store.messages(a['actor'])==[]
        receiver.start()
        wait_for(lambda:a_store.messages(a['actor']))
        opened = a_store.prepare_open(a['actor'],original['messageId'],deadline=at(240),max_requests=2)
        iid = opened['interactionId']
        # Saved together before sending; restarting the receiver cannot invent a new UUID.
        assert a_store.prepare_open(a['actor'],original['messageId'])==opened
        receiver.wait(a['actor'],'message.send',opened['operationId'])
        wait_for(lambda:b_store.interaction(origin=original['messageId']))
        assert b_store.interaction(origin=original['messageId'])['id']==iid
        receiver.call(a['actor'],'request.start',{'messageId':original['messageId'],'interactionId':iid})
        receiver.call(a['actor'],'message.send',result(b['actor'],original['messageId'],iid,output={'status':'conflict'}))
        second = request(a['actor'],interactionId=iid,expiresAt=at(180))
        sender.call(b['actor'],'message.send',second)
        wait_for(lambda:any(frame['message']['messageId']==second['messageId'] for frame in a_store.messages(a['actor'])))
        receiver.call(a['actor'],'request.start',{'messageId':second['messageId'],'interactionId':iid})
        receiver.call(a['actor'],'message.send',result(b['actor'],second['messageId'],iid,output={'status':'reserved'}))
        receiver.close()
        a_store.close()
        a_store = store_at(tmp_path/'a',a,platform.s.base_url)
        assert a_store.prepare_open(a['actor'],original['messageId'])==opened
        receiver = AgentConnection(platform.s.base_url,a['token'],[a['actor']],a_store).start()
        assert receiver.connected
        assert len([v for v in a_store.messages(a['actor']) if v['message']['kind']=='message.request'])==2
    finally:
        sender.close()
        receiver.close()
        a_store.close()
        b_store.close()


def test_reconnect_replays_original_operation_after_lost_response(platform, client, people, tmp_path, monkeypatch):
    a,b,_ = people
    publish(client,a).raise_for_status()
    store = store_at(tmp_path,b,platform.s.base_url)
    connection = AgentConnection(platform.s.base_url,b['token'],[b['actor']],store)
    original = request(a['actor'])
    broker = platform.messaging.delivery
    apply = broker.control
    committed = threading.Event()
    release = threading.Event()
    def drop(owner, socket_id, generations, frame):
        value = apply(owner,socket_id,generations,frame)
        if frame['type']=='message.send' and frame['payload']['messageId']==original['messageId'] and not committed.is_set():
            committed.set()
            release.wait(10)
        return value
    monkeypatch.setattr(broker,'control',drop)
    try:
        connection.start()
        operation = connection.submit(b['actor'],'message.send',original)
        assert committed.wait(5)
        connection._socket.close()
        release.set()
        assert connection.wait(b['actor'],'message.send',operation,timeout=10)['messageId']==original['messageId']
        assert platform.db.one('SELECT COUNT(*) AS n FROM delivery_messages WHERE id=?',original['messageId'])['n']==1
    finally:
        release.set()
        connection.close()
        store.close()


def test_sender_can_recover_uuid_when_opened_frame_was_not_saved(platform, client, people, tmp_path):
    a,b,_ = people
    publish(client,a).raise_for_status()
    store = store_at(tmp_path,b,platform.s.base_url)
    original = request(a['actor'])
    saved_operation = store.prepare(b['actor'],'message.send',original)
    # Platform accepted while this sender's local response was lost.
    sender,receiver = Peer(client,b),Peer(client,a)
    try:
        sender.call('message.send',original,operation=saved_operation)
        from test_agent_delivery import open_interaction
        iid = open_interaction(receiver,b['actor'],original['messageId'])
        connection = AgentConnection(platform.s.base_url,b['token'],[b['actor']],store)
        try:
            assert connection.recover_interaction(b['actor'],original['messageId'])['interactionId']==iid
            assert store.interaction(origin=original['messageId'])['id']==iid
        finally:
            connection.close()
    finally:
        sender.close()
        receiver.close()
        store.close()
