"""REST is infrastructure-only; confirmations and contract actions run locally."""
import httpx
import pytest
from _support import prepare,fetch_when_ready

def test_external_delegation_end_to_end(platform,providers,local_client):
    c=local_client
    info=c.api('GET','/me')
    assert info['wallet']=='100.00' and len(info['identities'])==1
    hits=c.search('医疗',20)['hits']
    assert hits and all('url' in h['card'] for h in hits)
    d,t,cd,agent=prepare(c,providers)
    fetch_when_ready(c,cd)
    assert c.verify(cd,1)['status']=='pass'
    assert c.accept(cd,1,confirmed=True)['status']=='pass'
    assert c.api('GET','/orders/'+cd)['signatures']['7']==[True]

def test_external_accept_confirmation_and_abandon(providers,local_client):
    c=local_client;d,t,cd,agent=prepare(c,providers)
    fetch_when_ready(c,cd)
    assert c.verify(cd,1)['status']=='pass'
    assert not c.client.card(cd)['signatures']['7']
    with pytest.raises(ValueError,match='confirmation'): c.accept(cd,1)
    assert not c.client.card(cd)['signatures']['7']
    c.terminate(cd,confirmed=True)
    assert c.store.get('cards',cd)['card']['fulfillment']['termination']

def test_external_countersign_declined_counts_switch(providers,local_client,client):
    d=next(d for d in providers.selected if d['formation']=='countersign')
    a=providers.agents['svc-'+d['id']]; old=a.definition['capacity'];a.definition['capacity']=0
    try:
        t=local_client.templates(a.url)[0]
        cd=local_client.draft(t['templateDigest'],{d['deliverable']['name']:d['sample_spec']})['cardDigest']
        assert 'capacity' in local_client.sign(cd,confirmed=True)['declined']
        assert not local_client.client.card(cd)['signatures']['2']
    finally: a.definition['capacity']=old
    assert client.post('/api/needs',json={'materials':[{'path':'/etc/passwd'}]}).status_code in (404,405)
