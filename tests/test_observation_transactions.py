"""Report acknowledgement and durable history share a transaction; live publication is separate."""
import copy
import uuid
import httpx
import pytest

from _support import as_admin
from ioalab.delegation.contracts import evidence_extends


def report(local_client):
    return {'id':uuid.uuid4().hex,'source':'experiment','actor':local_client.signer.id,
            'op':'participant.activity','detail':{}}


def submit(local_client,body):
    # A deliberately unhandled ASGI fault closes its keep-alive connection.
    # Reconnect for each attempt; the original event ID remains unchanged.
    with httpx.Client(trust_env=False,timeout=5,headers={'Connection':'close'}) as http:
        response=http.post(local_client.base+'/api/v1/observations',json=body,
                           headers={'Authorization':'Bearer '+local_client.token})
        response.raise_for_status()
        return response.json()


def counts(platform,body):
    return (platform.db.one('SELECT COUNT(*) AS n FROM observations WHERE id=?',body['id'])['n'],
            platform.db.one('SELECT COUNT(*) AS n FROM events WHERE sender=? AND op=?',body['actor'],body['op'])['n'])


def test_event_write_failure_rolls_back_original_acknowledgement(platform,local_client,monkeypatch):
    body=report(local_client);original=platform.bus.record_in
    def fail(connection,*args,**kwargs):
        event=original(connection,*args,**kwargs)
        if event['sender']==body['actor']:raise RuntimeError('isolated event write boundary')
        return event
    with monkeypatch.context() as patch:
        patch.setattr(platform.bus,'record_in',fail)
        with pytest.raises(httpx.HTTPStatusError) as failure:
            submit(local_client,body)
        assert failure.value.response.status_code==500
    assert counts(platform,body)==(0,0)
    assert submit(local_client,body)['ok']
    assert counts(platform,body)==(1,1)


def test_postcommit_publication_failure_retains_one_history_and_database_replay(platform,local_client,client,monkeypatch):
    body=report(local_client);original=platform.bus.publish
    def fail(event):
        if event['sender']==body['actor']:raise OSError('isolated publication failure')
        return original(event)
    with monkeypatch.context() as patch:
        patch.setattr(platform.bus,'publish',fail)
        with pytest.raises(httpx.HTTPStatusError) as failure:
            submit(local_client,body)
        assert failure.value.response.status_code==500
    assert counts(platform,body)==(1,1)
    assert submit(local_client,body)['duplicate']
    assert counts(platform,body)==(1,1)
    as_admin(client)
    replay=client.get('/api/admin/replay/'+platform.bus.run_id)
    assert replay.status_code==200
    assert len([row for row in replay.json() if row['sender']==body['actor'] and row['op']==body['op']])==1


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
