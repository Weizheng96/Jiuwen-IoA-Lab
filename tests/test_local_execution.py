import copy
import json
import uuid
import httpx
import pytest
from conftest import wait_for
from a2c.card import card_digest
from a2c.codec import utcnow
from a2c.jws import sign
from a2c.objects import object_digest
from traffic import run_order


def test_core_has_no_participant_runtime(platform,client):
    assert not hasattr(platform,'assistants') and not hasattr(platform,'services')
    assert set(platform.router.apps)=={'market','pay','agents'}
    # The new agents mount publishes keys and gates unpublished service calls;
    # it is a transport/identity gateway, not a participant executor in the core.
    assert client.post('/a2a/agents/unpublished',json={}).json()['error']['code']=='not_ready'
    assert not hasattr(platform,'register_assistant')
    for path in ['/api/assistant','/api/needs','/api/v1/delegations','/api/v1/cards/sign','/api/admin/traffic']:
        assert client.post(path,json={}).status_code in (404,405)
    for row in platform.db.rows("SELECT id FROM users WHERE kind='isc'"):
        assert not platform.db.one("SELECT id FROM agents WHERE owner_id=?",row['id'])


@pytest.mark.parametrize('formation',['preapproval','countersign','proposal'])
def test_local_contract_flow(platform,providers,local_client,formation):
    definition=next(d for d in providers.selected if d.get('formation','preapproval')==formation)
    result=run_order(local_client,definition,providers.public_url+'/a2a/providers/svc-'+definition['id'])
    assert result['status']=='accepted'
    cd=result['cardDigest']
    wait_for(lambda:bool(platform.market.market.store.get('ledger',cd)))
    order=local_client.api('GET','/orders/'+cd)
    assert order['state']=='completed'
    assert not local_client.outbox.all('pending')
    assert local_client.client.card(cd)['signatures']['7']
    assert local_client.store.get('cards',cd)['vouchers']
    # The local verifier package is independently resolved over HTTP and locked by its digest.
    verifier=local_client.client.template(order['template_digest']).get('acceptance') or order['card'].get('acceptance')
    ref=next(iter(verifier.values()))['verifiers']['script']
    assert local_client.fetch_package(ref['uri'])
    assert local_client.accept(cd,1,confirmed=True)['status']=='already-accepted'
    assert not list((platform.s.data/'keys').glob('*'+local_client.signer.id+'*'))


def test_confirmation_required(local_client):
    for method,args in [(local_client.sign,('unknown',)),(local_client.start,('unknown',)),
                        (local_client.accept,('unknown',1)),(local_client.terminate,('unknown',))]:
        with pytest.raises(ValueError,match='confirmation'): method(*args)


def test_enrollment_challenge_and_public_keys(local_client,platform):
    c=local_client
    operation=uuid.uuid4().hex
    nonce=c.api('POST','/identity/challenge',{'operationId':operation,'role':'assistant','agentId':c.signer.id})['challenge']
    body={'operationId':operation,'challenge':nonce,'owner':c.account['id'],'platform':c.base,'agentId':c.signer.id,
          'role':'assistant','name':'实验客户端','keys':{'agent':c.signer.jwk,'user':c.user.jwk,'payer':c.payer.jwk},'source':'experiment'}
    body['proof']=sign(object_digest(body),c.signer,'client',utcnow())
    assert c.api('POST','/identity',body)['id']==c.signer.id
    assert c.api('POST','/identity',body)['id']==c.signer.id
    invalid={**body,'operationId':uuid.uuid4().hex}
    with pytest.raises(httpx.HTTPStatusError): c.api('POST','/identity',invalid)
    operation=uuid.uuid4().hex
    nonce=c.api('POST','/identity/challenge',{'operationId':operation,'role':'assistant','agentId':c.signer.id})['challenge']
    body.update(operationId=operation,challenge=nonce)
    body['keys']={**body['keys'],'payer':{**c.payer.jwk,'d':'private'}}
    body.pop('proof');body['proof']=sign(object_digest(body),c.signer,'client',utcnow())
    with pytest.raises(httpx.HTTPStatusError): c.api('POST','/identity',body)


def test_observation_owner_replay_and_tamper(platform,providers,local_client,tmp_path):
    c=local_client;d=providers.selected[0]
    result=run_order(c,d,providers.public_url+'/a2a/providers/svc-'+d['id']);cd=result['cardDigest']
    body={'id':uuid.uuid4().hex,'source':'experiment','op':'order.report','card':c.client.card(cd)}
    assert c.api('POST','/observations',body)['ok']
    assert c.api('POST','/observations',body)['duplicate']
    from ioalab_sdk import LabClient
    from _support import new_user,PASSWORD
    u=new_user(platform)
    other=LabClient(c.base,u['username'],PASSWORD,tmp_path/'other')
    try:
        with pytest.raises(httpx.HTTPStatusError): other.api('POST','/observations',body)
        with pytest.raises(httpx.HTTPStatusError): other.api('GET','/orders/'+cd)
        with pytest.raises(httpx.HTTPStatusError): other.api('POST','/arbitrations',{'card':body['card'],'period':1,'files':{}})
    finally: other.close()
    forged=copy.deepcopy(body);forged['id']=uuid.uuid4().hex;forged['card']['derived']['total']+=1
    with pytest.raises(httpx.HTTPStatusError): c.api('POST','/observations',forged)
    stale=copy.deepcopy(body);stale['id']=uuid.uuid4().hex
    stale['card']['signatures']['7']=[]
    # Depending on evidence binding, an old copy is either rejected or ignored; never rolls back.
    try: c.api('POST','/observations',stale)
    except httpx.HTTPStatusError: pass
    assert len(c.api('GET','/orders/'+cd)['card']['signatures']['7'])==1


def test_service_registration_idempotent_and_owner_checked(platform,providers,local_client):
    agent=next(iter(providers.agents.values()))
    a=agent.account
    body={'url':agent.url,'name':agent.definition['name'],'ap2Jwk':agent.provider.ap2_key.jwk}
    row=a.api('POST','/provider/register',body)
    assert row['owner_id']==a.user['id']
    with pytest.raises(httpx.HTTPStatusError): local_client.api('POST','/provider/register',body)
    with pytest.raises(httpx.HTTPStatusError): local_client.api('POST','/provider/refresh',{'id':row['id']})


def test_budget_guard(providers,local_client):
    d=providers.selected[0];t=local_client.templates(providers.public_url+'/a2a/providers/svc-'+d['id'])[0]
    name=d['deliverable']['name']
    with pytest.raises(ValueError,match='budget'):
        local_client.draft(t['templateDigest'],{name:d['sample_spec']},budget=0)


def test_no_swarm_activation_for_browsing(providers,local_client):
    before=providers.health['done']
    agent=next(iter(providers.agents.values()))
    local_client.templates(agent.url);local_client.templates(agent.url)
    assert providers.health['done']==before


def test_provider_persistent_queue_and_task_state(providers,local_client):
    from _support import prepare,fetch_when_ready
    _,_,cd,agent=prepare(local_client,providers)
    fetch_when_ready(local_client,cd)
    wait_for(lambda:all(j['state'] in ('done','failed') for j in agent.store.all('jobs').values()))
    assert agent.store.all('tasks')
    assert all(j['state'] in ('done','failed') for j in agent.store.all('jobs').values())
    for task in agent.store.all('tasks').values():
        assert task['id'] in agent.provider.tasks
