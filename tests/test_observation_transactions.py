"""Current WS message receipt and durable event share a transaction."""
import copy
from contextlib import closing
import pytest
from _support import as_admin
from ioalab.delegation.contracts import evidence_extends
from test_agent_delivery import people,publish,request,Peer,uid
from test_delegation_admission import invoke


def counts(platform,sender,message,operation):
    return (platform.db.one('SELECT COUNT(*) AS n FROM delivery_messages WHERE id=?',message)['n'],
            platform.db.one('SELECT COUNT(*) AS n FROM delivery_operations WHERE actor=? AND kind=? AND id=?',sender,'message.send',operation)['n'],
            platform.db.one('SELECT COUNT(*) AS n FROM events WHERE sender=? AND op=?',sender,'message.queued')['n'])


def test_event_write_failure_rolls_back_original_acknowledgement(platform,client,people,monkeypatch):
    recipient,sender=people[:2]
    publish(client,recipient,[sender['username']]).raise_for_status()
    value=request(recipient['actor']);operation=uid();original=platform.bus.record_in
    def fail(connection,*args,**kwargs):
        event=original(connection,*args,**kwargs)
        if event['sender']==sender['actor'] and event['op']=='message.queued':raise RuntimeError('isolated event write boundary')
        return event
    with closing(Peer(client,sender)) as peer:
        with monkeypatch.context() as patch:
            patch.setattr(platform.bus,'record_in',fail)
            with pytest.raises(RuntimeError,match='event write boundary'):invoke(platform,peer,'message.send',value,operation)
        assert counts(platform,sender['actor'],value['messageId'],operation)==(0,0,0)
        receipt=peer.call('message.send',value,operation=operation)
        assert receipt['messageId']==value['messageId']
        assert counts(platform,sender['actor'],value['messageId'],operation)==(1,1,1)


def test_postcommit_publication_failure_retains_one_history_and_database_replay(platform,client,people,monkeypatch):
    recipient,sender=people[:2]
    publish(client,recipient,[sender['username']]).raise_for_status()
    value=request(recipient['actor']);operation=uid();original=platform.bus.publish
    def fail(event):
        if event['sender']==sender['actor'] and event['op']=='message.queued':raise OSError('isolated publication failure')
        return original(event)
    with closing(Peer(client,sender)) as peer:
        with monkeypatch.context() as patch:
            patch.setattr(platform.bus,'publish',fail)
            result=invoke(platform,peer,'message.send',value,operation)
            assert result['messageId']==value['messageId']
        assert counts(platform,sender['actor'],value['messageId'],operation)==(1,1,1)
        assert peer.call('message.send',value,operation=operation)['messageId']==value['messageId']
        assert counts(platform,sender['actor'],value['messageId'],operation)==(1,1,1)
    as_admin(client)
    replay=client.get('/api/admin/replay/'+platform.bus.run_id)
    assert replay.status_code==200
    assert len([row for row in replay.json() if row['sender']==sender['actor'] and row['op']=='message.queued'])==1


def test_snapshot_cannot_remove_or_replace_original_receipt_or_equal_length_signature():
    original={'signatures':{'1':'original','2':'provider','3':'market','4':[]},
              'fulfillment':{'receipt':{'signature':'original-receipt'}}}
    older=copy.deepcopy(original);older['fulfillment']['receipt']=None
    assert not evidence_extends(original,older) and evidence_extends(older,original)
    fork=copy.deepcopy(original);fork['fulfillment']['receipt']={'signature':'another-receipt'}
    assert not evidence_extends(original,fork) and not evidence_extends(fork,original)
    fork=copy.deepcopy(original);fork['signatures']['1']='another-valid-looking-signature'
    assert not evidence_extends(original,fork) and not evidence_extends(fork,original)
    advanced=copy.deepcopy(original);advanced['signatures']['4']=['delivery']
    assert evidence_extends(original,advanced) and not evidence_extends(advanced,original)
