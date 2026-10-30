"""Real socket tests for participant delivery, ownership and execution fencing."""
import json
import time
import uuid

import pytest
from websockets.sync.client import connect

from ioalab_sdk import LabClient
from _support import new_user, PASSWORD


def uid():
    return str(uuid.uuid4())


def at(seconds=300):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(time.time()+seconds, timezone.utc).isoformat().replace('+00:00', 'Z')


INPUT = {'type':'object', 'properties':{'text':{'type':'string'}},
         'required':['text'], 'additionalProperties':False}
OUTPUT = {'type':'object', 'properties':{'status':{'enum':['conflict','reserved']}},
          'required':['status'], 'additionalProperties':False}


@pytest.fixture
def people(platform, tmp_path):
    result = []
    for i in range(3):
        user = new_user(platform)
        local = LabClient(platform.s.base_url, user['username'], PASSWORD, tmp_path/str(i))
        result.append({**user, 'actor':local.signer.id, 'token':local.token})
        local.close()
    return result


def headers(person):
    return {'Authorization':'Bearer '+person['token']}


def publish(client, person, allow=None, revision=0, **changes):
    definition = {'description':'Structured test interaction', 'inputSchema':INPUT, 'outputSchema':OUTPUT,
                  'allowedSenders':allow, 'enabled':True, 'contentDigest':'sha256:'+'a'*64, **changes}
    return client.post('/api/v1/messaging/interfaces', headers=headers(person),
                       json={'operationId':uid(), 'name':'meeting', 'expectedRevision':revision, 'definition':definition})


class Peer:
    def __init__(self, client, person, runtime=None, cursor=0, ticket=None):
        self.actor = person['actor']
        self.runtime = runtime or uid()
        self.inbox = []
        if ticket is None:
            response = client.post('/api/v1/connections/ticket', headers=headers(person), json={'agentIds':[self.actor]})
            response.raise_for_status()
            ticket = response.json()['ticket']
        self._context = connect(str(client.base_url).rstrip('/').replace('http','ws',1)+'/ws/agents',
                                proxy=None, open_timeout=5, close_timeout=2)
        self.ws = self._context.__enter__()
        self.ws.send(json.dumps({'type':'connection.auth', 'ticket':ticket, 'runtimeId':self.runtime,
                                 'cursors':{self.actor:cursor}}))
        try:
            self.ready = self.receive('connection.ready')
        except BaseException:
            self.close()
            raise
        self.generation = self.ready['generations'][self.actor]

    def receive(self, kind, predicate=lambda value:True, timeout=5):
        deadline = time.monotonic()+timeout
        while time.monotonic()<deadline:
            value = json.loads(self.ws.recv(timeout=max(.01, deadline-time.monotonic())))
            if value['type']=='message.deliver':
                self.inbox.append(value)
            if value['type']==kind and predicate(value):
                return value
            if value['type']=='connection.error':
                raise AssertionError(value)
        raise AssertionError('Missing socket frame '+kind)

    def call(self, kind, payload, operation=None, ok=True):
        operation = operation or uid()
        self.ws.send(json.dumps({'type':kind, 'operationId':operation, 'agentId':self.actor,
                                 'generation':self.generation, 'payload':payload}))
        result = self.receive('operation.result', lambda value:value['operationId']==operation)
        assert result['ok'] is ok, result
        return result.get('result') if ok else result['error']

    def delivery(self, message):
        for value in self.inbox:
            if value['message']['messageId']==message:
                return value
        return self.receive('message.deliver', lambda value:value['message']['messageId']==message)

    def close(self):
        self.ws.close()


@pytest.fixture
def peers(client):
    opened = []
    def factory(person, **kwargs):
        peer = Peer(client, person, **kwargs)
        opened.append(peer)
        return peer
    yield factory
    for peer in opened:
        peer.close()


def request(recipient, **extra):
    return {'messageId':uid(), 'kind':'message.request', 'recipient':recipient,
            'expiresAt':at(), 'body':{'interfaceName':'meeting','revision':1,'input':{'text':'hello'}}, **extra}


def open_interaction(peer, sender, origin, iid=None, maximum=8, ok=True):
    iid = iid or uid()
    opened = {'messageId':uid(), 'kind':'interaction.opened', 'recipient':sender,
              'interactionId':iid, 'originMessageId':origin, 'expiresAt':at(),
              'body':{'interactionType':'messaging','interfaceName':'meeting','revision':1,
                      'deadline':at(240),'maxRequests':maximum}}
    value = peer.call('message.send', opened, ok=ok)
    return iid if ok else value


def result(recipient, origin, iid, outcome='completed', **body):
    return {'messageId':uid(), 'kind':'message.result', 'recipient':recipient, 'interactionId':iid,
            'inReplyTo':origin, 'expiresAt':at(), 'body':{'outcome':outcome, **body}}


def test_ticket_is_owned_single_use_and_bearer_only(platform, client, people, peers):
    a,b,_ = people
    denied = client.post('/api/v1/connections/ticket', headers=headers(a), json={'agentIds':[b['actor']]})
    assert denied.status_code==403
    client.cookies.set('ioalab_session', a['token'])
    assert client.post('/api/v1/connections/ticket', json={'agentIds':[a['actor']]}).status_code==401
    client.cookies.clear()
    ticket = client.post('/api/v1/connections/ticket',headers=headers(a),json={'agentIds':[a['actor']]}).json()['ticket']
    p = peers(a, ticket=ticket)
    with pytest.raises(AssertionError, match='already used'):
        Peer(client, a, ticket=ticket)
    assert platform.db.one('SELECT 1 FROM connection_tickets WHERE digest=?', ticket) is None
    assert p.ready['leaseSeconds']>=3*p.ready['heartbeatSeconds']


def test_offline_open_uuid_recovery_conflict_then_retry_same_interaction(platform, client, people, peers):
    a,b,c = people
    assert publish(client,a,[b['username']]).status_code==200
    sender = peers(b)
    original = request(a['actor'])
    operation = uid()
    accepted = sender.call('message.send', original, operation)
    assert sender.call('message.send', original, operation)==accepted
    assert platform.db.one('SELECT COUNT(*) AS n FROM delivery_messages WHERE id=?', original['messageId'])['n']==1
    receiver = peers(a)
    delivery = receiver.delivery(original['messageId'])
    assert delivery['deliveryState']=='queued'
    receiver.call('message.ack', {'messageId':original['messageId'],'through':delivery['recipientSeq']})
    state = client.get('/api/v1/messages/'+original['messageId'],headers=headers(b),params={'agentId':b['actor']}).json()
    assert state['deliveryState']=='delivered' and state['requestState']=='pending'
    iid = open_interaction(receiver,b['actor'],original['messageId'],maximum=2)
    sender.receive('message.deliver',lambda value:value['message'].get('interactionId')==iid)
    recovered = client.get('/api/v1/interactions',headers=headers(b),params={'agentId':b['actor'],'originMessageId':original['messageId']}).json()
    assert recovered['interactionId']==iid
    denied = client.get('/api/v1/interactions',headers=headers(c),params={'agentId':c['actor'],'interactionId':iid})
    assert denied.status_code==404
    assert receiver.call('request.start',{'messageId':original['messageId'],'interactionId':iid})['resumed'] is False
    receiver.call('message.send',result(b['actor'],original['messageId'],iid,output={'status':'conflict'}))
    second = request(a['actor'],interactionId=iid,expiresAt=at(180))
    sender.call('message.send',second)
    receiver.delivery(second['messageId'])
    assert receiver.call('request.start',{'messageId':second['messageId'],'interactionId':iid})['resumed'] is False
    receiver.call('message.send',result(b['actor'],second['messageId'],iid,output={'status':'reserved'}))
    closed = client.get('/api/v1/interactions',headers=headers(b),params={'agentId':b['actor'],'interactionId':iid}).json()
    assert closed['state']=='closed' and closed['requests']==2
    assert sender.call('message.send',request(a['actor'],interactionId=iid,expiresAt=at(120)),ok=False)['code']=='closed'


def test_frozen_revision_and_current_permissions(platform, client, people, peers):
    a,b,_ = people
    assert publish(client,a,[b['username']]).status_code==200
    sender,receiver = peers(b),peers(a)
    original = request(a['actor'])
    sender.call('message.send', original)
    receiver.delivery(original['messageId'])
    changed = {'type':'object','properties':{'number':{'type':'integer'}},'required':['number'],'additionalProperties':False}
    assert publish(client,a,[b['username']],revision=1,inputSchema=changed).status_code==200
    iid = open_interaction(receiver,b['actor'],original['messageId'])
    assert receiver.call('request.start',{'messageId':original['messageId'],'interactionId':iid})['requestState']=='processing'
    receiver.call('message.send',result(b['actor'],original['messageId'],iid,output={'status':'conflict'}))
    followup = request(a['actor'],interactionId=iid,expiresAt=at(180))
    sender.call('message.send',followup)
    assert publish(client,a,[],revision=2,inputSchema=changed).status_code==200
    assert receiver.call('request.start',{'messageId':followup['messageId'],'interactionId':iid},ok=False)['code']=='forbidden'


def test_fencing_reconnect_and_no_extra_login_credit(platform, client, people, peers):
    a,_,_ = people
    before = platform.db.one('SELECT balance FROM wallets WHERE user_id=?',a['id'])['balance']
    old = peers(a)
    with pytest.raises(AssertionError,match='Another local instance'):
        Peer(client,a)
    fresh = peers(a,runtime=old.runtime)
    assert fresh.generation==old.generation+1
    error = json.loads(old.ws.recv(timeout=5))
    assert error['error']['code']=='stale_generation'
    from websockets.exceptions import ConnectionClosed
    with pytest.raises(ConnectionClosed) as closed:
        old.ws.recv(timeout=5)
    assert closed.value.rcvd.code==4409
    assert platform.db.one('SELECT balance FROM wallets WHERE user_id=?',a['id'])['balance']==before


def test_close_before_open_and_before_start(platform, client, people, peers):
    a,b,_ = people
    publish(client,a,[b['username']]).raise_for_status()
    sender,receiver = peers(b),peers(a)
    original = request(a['actor'])
    sender.call('message.send',original)
    sender.call('interaction.close',{'originMessageId':original['messageId'],'reason':'cancelled'})
    assert open_interaction(receiver,b['actor'],original['messageId'],ok=False)['code']=='closed'
    second = request(a['actor'])
    sender.call('message.send',second)
    iid = open_interaction(receiver,b['actor'],second['messageId'])
    sender.call('interaction.close',{'interactionId':iid})
    assert receiver.call('request.start',{'messageId':second['messageId'],'interactionId':iid},ok=False)['code']=='closed'


def test_unknown_does_not_downgrade_final_result(platform, client, people, peers):
    a,b,_ = people
    publish(client,a).raise_for_status()
    sender,receiver = peers(b),peers(a)
    original = request(a['actor'])
    sender.call('message.send',original)
    iid = open_interaction(receiver,b['actor'],original['messageId'])
    unknown = result(b['actor'],original['messageId'],iid,'unknown',error={'code':'mail_receipt_pending'})
    assert receiver.call('message.send',unknown,ok=False)['code']=='forbidden'
    receiver.call('request.start',{'messageId':original['messageId'],'interactionId':iid})
    receiver.call('message.send',unknown)
    complete = result(b['actor'],original['messageId'],iid,output={'status':'reserved'})
    final = receiver.call('message.send',complete)
    assert receiver.call('message.send',{**unknown,'messageId':uid()})==final
    assert receiver.call('message.send',result(b['actor'],original['messageId'],iid,output={'status':'conflict'}),ok=False)['code']=='id_conflict'
    assert platform.db.one('SELECT request_state FROM delivery_messages WHERE id=?',original['messageId'])['request_state']=='completed'


def test_ticket_expiry_and_invalid_json_are_explicit(platform, client, people):
    a,_,_ = people
    ticket = client.post('/api/v1/connections/ticket',headers=headers(a),json={'agentIds':[a['actor']]}).json()['ticket']
    import hashlib
    platform.db.run('UPDATE connection_tickets SET expires=? WHERE digest=?',time.time()-1,hashlib.sha256(ticket.encode()).hexdigest())
    with pytest.raises(AssertionError,match='expired'):
        Peer(client,a,ticket=ticket)
    for bad in ('{bad',b'{"type":"connection.auth"}'):
        with connect(platform.s.base_url.replace('http','ws',1)+'/ws/agents',proxy=None) as ws:
            ws.send(bad)
            value = json.loads(ws.recv(timeout=5))
            assert value['type']=='connection.error' and value['error']['code']=='invalid_input'


def test_schema_publication_idempotence_and_directory_target(platform, client, people, peers):
    a,b,c = people
    assert publish(client,a,[b['username']]).status_code==200
    listing = client.get('/api/v1/messaging/accounts/'+a['username']+'/interfaces',headers=headers(b)).json()
    assert listing[0]['agentId']==a['actor'] and 'allowedSenders' not in listing[0]
    repeated = {'operationId':uid(),'name':'receipt','expectedRevision':0,
                'definition':{'inputSchema':INPUT,'outputSchema':OUTPUT,'allowedSenders':[b['username']],'contentDigest':'sha256:'+'b'*64}}
    first = client.post('/api/v1/messaging/interfaces',headers=headers(a),json=repeated)
    assert first.status_code==200 and first.json()['revision']==1
    assert client.post('/api/v1/messaging/interfaces',headers=headers(a),json=repeated).json()==first.json()
    assert client.get('/api/v1/messaging/accounts/'+a['username']+'/interfaces',headers=headers(c)).json()==[]
    assert publish(client,a,revision=0).status_code==409
    assert publish(client,a,revision=1,outputSchema=True).status_code==400
    sender = peers(b)
    malformed = request(a['actor'],body={'interfaceName':'meeting','revision':1,'input':{'text':5}})
    assert sender.call('message.send',malformed,ok=False)['code']=='invalid_input'
    spoofed = {**request(a['actor']),'sender':c['actor']}
    assert sender.call('message.send',spoofed,ok=False)['code']=='invalid_input'
    assert sender.call('message.send',request(a['actor'],body={'interfaceName':'meeting','revision':0,'input':{'text':'x'}}),ok=False)['code']=='revision_conflict'


def test_message_id_is_global_and_operation_parameters_are_immutable(platform, client, people, peers):
    a,b,c = people
    publish(client,a).raise_for_status()
    sender,other = peers(b),peers(c)
    value = request(a['actor'])
    operation = uid()
    sender.call('message.send',value,operation)
    assert other.call('message.send',value,ok=False)['code']=='id_conflict'
    assert sender.call('message.send',{**value,'messageId':uid()},operation,ok=False)['code']=='id_conflict'
    recovered = client.get('/api/v1/operations/'+operation,headers=headers(b),params={'agentId':b['actor'],'operation':'message.send'})
    assert recovered.json()['messageId']==value['messageId']


def test_preopen_rejection_and_expiry_do_not_allow_execution(platform, client, people, peers):
    a,b,_ = people
    publish(client,a).raise_for_status()
    sender,receiver = peers(b),peers(a)
    value = request(a['actor'])
    sender.call('message.send',value)
    receiver.call('message.send',{'messageId':uid(),'kind':'message.result','recipient':b['actor'],
                                 'originMessageId':value['messageId'],'inReplyTo':value['messageId'],'expiresAt':at(),
                                 'body':{'outcome':'rejected','error':{'code':'local_policy'}}})
    assert open_interaction(receiver,b['actor'],value['messageId'],ok=False)['code']=='closed'
    second = request(a['actor'])
    sender.call('message.send',second)
    platform.db.run('UPDATE delivery_messages SET expires=? WHERE id=?',time.time()-1,second['messageId'])
    query = client.get('/api/v1/messages/'+second['messageId'],headers=headers(b),params={'agentId':b['actor']}).json()
    assert query['requestState']=='expired'
    assert open_interaction(receiver,b['actor'],second['messageId'],ok=False)['code']=='closed'


def test_retention_protects_unknown_work_and_old_ids_cannot_be_reused(platform, client, people, peers):
    a,b,_ = people
    publish(client,a).raise_for_status()
    sender,receiver = peers(b),peers(a)
    value = request(a['actor'])
    sender.call('message.send',value)
    iid = open_interaction(receiver,b['actor'],value['messageId'])
    receiver.call('request.start',{'messageId':value['messageId'],'interactionId':iid})
    unknown = result(b['actor'],value['messageId'],iid,'unknown',error={'code':'check_original'})
    first = receiver.call('message.send',unknown)
    assert receiver.call('message.send',{**unknown,'messageId':uid()})==first
    sender.call('interaction.close',{'interactionId':iid})
    old = time.time()-100*86400
    platform.db.run('UPDATE delivery_messages SET created=?,retain_until=? WHERE interaction=?',old,old,iid)
    platform.messaging.delivery.cleanup()
    assert platform.db.one('SELECT envelope FROM delivery_messages WHERE id=?',value['messageId'])['envelope']
    receiver.call('message.send',result(b['actor'],value['messageId'],iid,output={'status':'reserved'}))
    platform.db.run('UPDATE delivery_messages SET created=?,retain_until=? WHERE interaction=?',old,old,iid)
    platform.messaging.delivery.cleanup()
    assert platform.db.one('SELECT 1 FROM delivery_messages WHERE id=?',value['messageId']) is None
    assert sender.call('message.send',value,ok=False)['code']=='id_conflict'


def test_reconnect_replays_saved_messages_and_history_gap_is_not_hidden(platform, client, people, peers):
    a,b,_ = people
    publish(client,a).raise_for_status()
    sender = peers(b)
    value = request(a['actor'])
    sender.call('message.send',value)
    receiver = peers(a)
    first = receiver.delivery(value['messageId'])
    receiver.call('message.ack',{'messageId':value['messageId'],'through':first['recipientSeq']})
    receiver.close()
    repeated = peers(a,runtime=receiver.runtime,cursor=0)
    assert repeated.delivery(value['messageId'])['message']['messageId']==value['messageId']
    repeated.close()
    old = time.time()-100*86400
    platform.db.run("UPDATE delivery_messages SET created=?,retain_until=?,expires=?,request_state='expired' WHERE id=?",old,old,old,value['messageId'])
    platform.messaging.delivery.cleanup()
    with pytest.raises(AssertionError,match='history_gap'):
        Peer(client,a,runtime=receiver.runtime,cursor=0)


def test_malformed_controls_return_errors_without_breaking_valid_connection(platform, client, people, peers):
    a,b,_ = people
    publish(client,a).raise_for_status()
    sender,receiver = peers(b),peers(a)
    assert sender.call([],{},ok=False)['code']=='invalid_input'
    assert sender.call('request.start',{'messageId':[]},ok=False)['code']=='invalid_input'
    assert sender.call('operation.get',{'operation':[],'operationId':uid()},ok=False)['code']=='invalid_input'
    value = request(a['actor'])
    sender.call('message.send',value)
    bad = {'messageId':uid(),'kind':'message.result','recipient':b['actor'],'inReplyTo':value['messageId'],
           'originMessageId':value['messageId'],'expiresAt':at(),'body':{'outcome':[],'error':{}}}
    assert receiver.call('message.send',bad,ok=False)['code']=='invalid_input'
    assert receiver.delivery(value['messageId'])['message']['body']['input']=={'text':'hello'}


def test_cleanup_cannot_leave_an_interior_sequence_gap(platform, client, people, peers):
    a,b,_ = people
    publish(client,a).raise_for_status()
    sender,receiver = peers(b),peers(a)
    first,second = request(a['actor']),request(a['actor'])
    sender.call('message.send',first)
    iid = open_interaction(receiver,b['actor'],first['messageId'])
    receiver.call('request.start',{'messageId':first['messageId'],'interactionId':iid})
    sender.call('message.send',second)
    sender.call('interaction.close',{'originMessageId':second['messageId']})
    old = time.time()-100*86400
    platform.db.run('UPDATE delivery_messages SET created=?,retain_until=?,expires=? WHERE id IN (?,?)',old,old,old,first['messageId'],second['messageId'])
    platform.messaging.delivery.cleanup()
    kept = platform.db.one('SELECT envelope FROM delivery_messages WHERE id=?',second['messageId'])
    assert kept is not None and kept['envelope'] is None
    receiver.close()
    recovered = peers(a,runtime=receiver.runtime,cursor=0)
    assert recovered.delivery(second['messageId'])['message']['payloadAvailable'] is False
