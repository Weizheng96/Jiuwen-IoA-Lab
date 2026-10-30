"""Public service uploads, signed originals and recoverable A2X projection."""
import copy
from datetime import timedelta
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode
import uuid

import httpx
import pytest
from a2c.binding import A2ATransport
from a2c.codec import digest,fmt_time,utcnow
from a2c.common import Directory
from a2c.jws import sign
from a2c.slots import sign_template,verify_template
from ioalab_sdk.runtime.identities import load_identity
from ioalab_sdk.runtime.session import AccountSession
from ioalab_sdk.runtime.config import RuntimeConfig
from ioalab_sdk.runtime.resident import ResidentRuntime
from ioalab_sdk.runtime.ipc import LocalClient
from ioalab_sdk.provider import ProviderRole
from ioalab_sdk.templates import template_from_definition
from _support import new_user,PASSWORD
from conftest import wait_for
from test_assistant_role import user_decision
from test_resident_runtime import configuration


def uid():return str(uuid.uuid4())


@pytest.fixture
def service(platform,tmp_path):
    user=new_user(platform)
    account=AccountSession(platform.s.base_url,user['username'],PASSWORD,tmp_path/'account')
    identity=load_identity(account,tmp_path/'keys',{'key':'service','role':'service','name':'可接入的专业服务'})
    try:yield account,identity
    finally:account.close()


def draft(platform):
    return template_from_definition({'formation':'proposal','deliverable':{'name':'result','description':'专业服务交付'},
                                     'pricing':{'proposalFee':1}},packages=None,payment_provider=platform.payment_provider)


def publication(platform,identity,*,revision=1,name='测试专业服务',template=None):
    template=template or sign_template({**draft(platform),'provider':identity.protocol_id},identity.signer,'provider',utcnow())
    declaration={'schema':'ioalab.service/1','agentId':identity.agent_id,'protocolId':identity.protocol_id,'revision':revision,
                 'name':name,'description':'提供软件开发与文档整理服务','tags':['软件开发'],
                 'categoryId':platform.discovery.categories()[0]['id'],'templateDigests':[template['templateDigest']]}
    return {'operationId':uid(),'declaration':declaration,'proof':sign(digest(declaration),identity.signer,'ioa-service',utcnow()),'templates':[template]}


def submit(account,identity,body):
    return account.http.post(account.base+'/api/v1/services/'+identity.agent_id+'/publications',
                             headers={'Authorization':'Bearer '+account.token},json=body)


def service_status(account,identity):
    return account.api('GET','/services/'+identity.agent_id+'/status')


def test_published_service_exposes_original_proof_keys_templates_and_actual_category(platform,service):
    account,identity=service
    assert account.http.get(identity.protocol_id+'/.well-known/agent-card.json').status_code==409
    body=publication(platform,identity)
    response=submit(account,identity,body)
    assert response.status_code==200,response.text
    result=response.json();published=account.http.get(identity.protocol_id+'/publication').json()
    assert published['declaration']==body['declaration'] and published['proof']==body['proof']
    assert published['declarationDigest']==digest(body['declaration'])
    status=service_status(account,identity)
    assert status['revision']==status['indexedRevision']==1 and status['indexReady']
    assert status['categoryId']==body['declaration']['categoryId'] and status['executionStatus']=='not_reported'
    assert identity.agent_id in next(c for c in platform.discovery.categories() if c['id']==status['categoryId'])['services']
    assert platform.db.one('SELECT status FROM agents WHERE id=?',identity.agent_id)['status']=='listed'
    card=account.http.get(identity.protocol_id+'/.well-known/agent-card.json').json()
    assert card['name']==body['declaration']['name']
    transport=A2ATransport(http=lambda:httpx.AsyncClient(timeout=15,trust_env=False))
    try:
        originals=transport.send(identity.protocol_id,'template.get',{}).body['templates']
        assert originals==result['templates']
        assert originals[0]['signatures']['provider']==body['templates'][0]['signatures']['provider']
        verify_template(originals[0],Directory(fetch=transport.jwks))
    finally:transport.close()
    assert account.http.get(identity.protocol_id+'/.well-known/jwks.json').json()=={'keys':[identity.signer.jwk]}


def test_upload_checks_bound_identity_signature_category_and_ordinary_template_policy(platform,service):
    account,identity=service;body=publication(platform,identity)
    url=account.base+'/api/v1/services/'+identity.agent_id+'/publications'
    assert account.http.post(url,json=body).status_code==401
    stranger=new_user(platform)
    assert account.http.post(url,headers={'Authorization':'Bearer '+platform.auth.bearer(stranger['id'])},json=body).status_code==403
    bad=copy.deepcopy(body);bad['declaration']['protocolId']='http://untrusted.invalid/private'
    assert submit(account,identity,bad).status_code==403
    bad=copy.deepcopy(body);bad['declaration']['name']='未经原签名允许的修改'
    assert submit(account,identity,bad).status_code==400
    bad=copy.deepcopy(body);bad['declaration']['categoryId']='nonexistent'
    assert submit(account,identity,bad).json()['error']['code']=='category_required'
    original=copy.deepcopy(body['templates'][0]);original['provider']='http://untrusted.invalid/provider'
    bad=publication(platform,identity,template=original)
    assert submit(account,identity,bad).status_code==400
    original=copy.deepcopy(body['templates'][0]);original['pricing']['paymentProviders']=[{'id':'https://other.invalid/pay','jwksUri':'https://other.invalid/keys'}]
    original=sign_template(original,identity.signer,'provider',utcnow())
    assert submit(account,identity,publication(platform,identity,template=original)).status_code==400
    assert service_status(account,identity)['revision']==0


def test_old_operation_recovers_immutable_receipt_without_reverting_new_source_or_index(platform,service):
    account,identity=service;first=publication(platform,identity)
    receipt=submit(account,identity,first).json()
    second=publication(platform,identity,revision=2,name='更新后的真实服务名')
    assert submit(account,identity,second).status_code==200
    assert submit(account,identity,first).json()==receipt
    assert service_status(account,identity)['revision']==service_status(account,identity)['indexedRevision']==2
    assert platform.discovery.get_card(identity.agent_id)['name']==second['declaration']['name']
    assert account.api('GET','/operations/'+first['operationId']+'?'+urlencode({'agentId':identity.agent_id,'operation':'service.publish'}))==receipt
    changed={**first,'declaration':{**first['declaration'],'name':'改变旧操作'}}
    assert submit(account,identity,changed).status_code==409
    old_template=receipt['templates'][0]
    transport=A2ATransport(http=lambda:httpx.AsyncClient(timeout=15,trust_env=False))
    try:assert transport.send(identity.protocol_id,'template.get',{'templateDigest':old_template['templateDigest']}).body['templates']==[old_template]
    finally:transport.close()


def test_index_failure_commits_original_once_and_replay_repairs_current_projection(platform,service,monkeypatch):
    account,identity=service;body=publication(platform,identity)
    original=platform.discovery.register_one
    with monkeypatch.context() as patch:
        patch.setattr(platform.discovery,'register_one',lambda *a,**kw:(_ for _ in ()).throw(OSError('test index outage')))
        response=submit(account,identity,body)
    assert response.status_code==200,response.text
    receipt=response.json();status=service_status(account,identity)
    assert status['published'] and not status['indexReady'] and status['revision']==1
    assert submit(account,identity,body).json()==receipt
    assert service_status(account,identity)['indexReady']
    assert len(platform.db.rows('SELECT * FROM registry_publications WHERE actor=?',identity.agent_id))==1
    assert platform.discovery.get_card(identity.agent_id)['url']==identity.protocol_id


def test_endorsement_result_loss_recovers_original_signature_without_reendorsing(platform,service,monkeypatch):
    account,identity=service;body=publication(platform,identity)
    # Fail the second write: neither a source version nor its operation may survive.
    platform.db.run("CREATE TRIGGER test_publication_commit BEFORE INSERT ON registry_publication_operations WHEN NEW.actor=? BEGIN SELECT RAISE(ABORT,'test publication receipt outage'); END".replace('?',"'"+identity.agent_id+"'"))
    try:
        assert submit(account,identity,body).status_code==500
    finally:platform.db.run('DROP TRIGGER test_publication_commit')
    assert platform.db.one('SELECT * FROM registry_publications WHERE actor=?',identity.agent_id) is None
    assert platform.db.one('SELECT * FROM registry_publication_operations WHERE actor=?',identity.agent_id) is None
    snapshot=platform.market.market.store.get('snapshots',body['templates'][0]['templateDigest'])['template']
    with monkeypatch.context() as patch:
        patch.setattr(platform.market.market,'op_template_endorse',lambda *a:(_ for _ in ()).throw(AssertionError('must reuse original endorsement')))
        response=submit(account,identity,body)
    assert response.status_code==200,response.text
    assert response.json()['templates']==[snapshot]


def test_competing_revision_and_commit_time_key_expiry_fences_preserve_single_source(platform,service,monkeypatch):
    account,identity=service
    bodies=[publication(platform,identity,name=name) for name in ('并发甲','并发乙')]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(lambda body:submit(account,identity,body),bodies))
    assert sorted(r.status_code for r in responses)==[200,409]
    assert len(platform.db.rows('SELECT * FROM registry_publications WHERE actor=?',identity.agent_id))==1
    short=sign_template({**draft(platform),'provider':identity.protocol_id,'validUntil':fmt_time(utcnow()+timedelta(seconds=30))},identity.signer,'provider',utcnow())
    future=publication(platform,identity,revision=2,template=short)
    prepared=platform.publications.prepare(account.account['id'],identity.agent_id,future)
    signed=platform.market.endorse_service_templates(identity.protocol_id,identity.signer.jwk,future['templates'],platform.payment_provider)
    from ioalab.errors import LabError
    with monkeypatch.context() as patch:
        patch.setattr(platform.publications,'clock',lambda:(utcnow()+timedelta(seconds=40)).timestamp())
        with pytest.raises(LabError,match='expired'):
            with platform.db.tx() as conn:platform.publications.commit_in(conn,account.account['id'],prepared,signed)
    binding=platform.publications.binding
    def changed(owner,actor):
        row,meta=binding(owner,actor)
        return row,{**meta,'providerJwk':{**meta['providerJwk'],'kid':'changed-after-preparation'}}
    with monkeypatch.context() as patch:
        patch.setattr(platform.publications,'binding',changed)
        with pytest.raises(LabError,match='key changed'):
            with platform.db.tx() as conn:platform.publications.commit_in(conn,account.account['id'],prepared,signed)
    assert service_status(account,identity)['revision']==1


def test_countersign_publication_snapshots_real_uploaded_verifier_without_executing_it(platform,service):
    account,identity=service
    definition={'formation':'countersign','deliverable':{'name':'result','description':'可验证交付'},'pricing':{'price':'9'},
                'verifier':{'id':'publication_check@1.0','source':"def verify(ctx):\n    return {'pass': True}\n"}}
    def upload(**args):
        return account.api('POST','/provider/packages',{'verifierId':args['verifier_id'],'source':args['source'],'reads':args.get('reads',[])})
    raw=template_from_definition(definition,packages=SimpleNamespace(put=upload),payment_provider=platform.payment_provider)
    signed=sign_template({**raw,'provider':identity.protocol_id},identity.signer,'provider',utcnow())
    response=submit(account,identity,publication(platform,identity,template=signed))
    assert response.status_code==200,response.text
    template=response.json()['templates'][0]
    assert template['formation']=='countersign' and template['acceptance']==signed['acceptance']
    snapshot=platform.market.market.store.get('snapshots',template['templateDigest'])
    assert snapshot['template']==template and snapshot['packages']


def test_resident_publish_needs_user_channel_and_recovers_lost_reply_after_restart(platform,tmp_path,monkeypatch):
    config,user=configuration(platform,tmp_path)
    settings=RuntimeConfig.load(config)
    module=ProviderRole()
    runtime=ResidentRuntime(settings,modules=(module,)).start()
    try:
        wait_for(lambda:runtime.connection.connected)
        client=LocalClient(settings.home)
        config_service={'profile':'service','name':'普通用户专业服务','description':'使用本机业务技能提供软件开发服务','tags':['开发'],
                        'categoryId':platform.discovery.categories()[0]['id'],'templates':[draft(platform)]}
        checked=client.call('provider.preview',{'service':config_service})
        assert checked['status']=='completed'
        args={'service':config_service,'contentDigest':checked['data']['contentDigest'],'expectedRevision':checked['data']['expectedRevision']}
        operation=uid()
        waiting=client.call('provider.publish',args,operation_id=operation)
        assert waiting['status']=='waiting'
        identity=runtime.identities[1]
        assert service_status(runtime.account,identity)['revision']==0
        assert client.call('provider.publish',{**args,'confirmed':True},operation_id=operation)['status']=='rejected'
        user_decision(runtime,waiting['data']['confirmationRef'])
        original=runtime.account.api
        def lost(method,path,body=None):
            result=original(method,path,body)
            if method=='POST' and path.endswith('/publications'):raise httpx.ReadError('test dropped publication response')
            return result
        with monkeypatch.context() as patch:
            patch.setattr(runtime.account,'api',lost)
            assert client.call('provider.publish',args,operation_id=operation)['status']=='unknown'
        assert service_status(runtime.account,identity)['revision']==1
        request=runtime.store.checkpoint('provider:'+identity.agent_id,'publish:'+operation)['body']
    finally:runtime.close()
    runtime=ResidentRuntime(settings,modules=(ProviderRole(),)).start()
    try:
        wait_for(lambda:runtime.connection.connected)
        monkeypatch.setattr(runtime.confirmations,'ensure',lambda *a,**kw:(_ for _ in ()).throw(AssertionError('restore original request, not a new confirmation')))
        result=LocalClient(settings.home).call('provider.publish',args,operation_id=operation)
        assert result['status']=='completed',result
        receipt=result['data']['publicationReceipt']
        assert receipt['acceptedRevision']==1 and result['data']['currentPublication']['indexReady']
        assert runtime.store.checkpoint('provider:'+identity.agent_id,'publish:'+operation)['body']==request
        assert receipt['templates'][0]['signatures']['provider']==request['templates'][0]['signatures']['provider']
        assert all(not value['executionReady'] for value in runtime.status()['identities'])
    finally:runtime.close()
