"""Public deployment scopes, immutable decisions and original publication recovery."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager,closing
from copy import deepcopy
from datetime import timedelta
import json
from types import SimpleNamespace
import uuid

import httpx
import pytest
from a2c.codec import digest,fmt_time,utcnow
from a2c.jws import sign
from a2c.slots import sign_template
from ioalab.errors import LabError
from ioalab.registry.authorizations import publication_terms as core_terms
from ioalab_sdk.provider import ProviderRole,publication_terms
from ioalab_sdk.runtime.config import RuntimeConfig
from ioalab_sdk.runtime.identities import load_identity
from ioalab_sdk.runtime.ipc import LocalClient
from ioalab_sdk.runtime.resident import ResidentRuntime
from ioalab_sdk.runtime.session import AccountSession
from _support import PASSWORD,new_user
from test_service_publication import draft,publication,submit,service_status
from test_assistant_role import user_decision


def uid():return str(uuid.uuid4())


def admin(client,platform):
    # Reuse an authenticated web-admin session; repeated password login is rate limited.
    client.headers['Authorization']='Bearer '+platform.auth.token('admin')


@pytest.fixture
def internal(platform,tmp_path):
    user=new_user(platform,kind='isp')
    account=AccountSession(platform.s.base_url,user['username'],PASSWORD,tmp_path/'account')
    identity=load_identity(account,tmp_path/'keys',{'key':'service','role':'service','name':'独立官方服务'})
    try:yield account,identity
    finally:account.close()


def scope(identity,body,*,revision=0,enabled=True,lifetime=31*86400):
    service={**body['declaration'],'templates':body['templates']} if 'declaration' in body else body
    return {'agentId':identity.agent_id,'expectedRevision':revision,'termsDigest':publication_terms(identity.protocol_id,service),
            'maxTemplateSeconds':lifetime,'enabled':enabled}


def configure(client,*changes,operation=None):
    return client.post('/api/admin/publication-authorizations',json={'operationId':operation or uid(),'changes':list(changes)})


def test_scope_requires_current_web_admin_and_bound_internal_service(platform,client,internal):
    account,identity=internal;body=publication(platform,identity);change=scope(identity,body)
    assert configure(client,change).status_code==401
    token=platform.auth.token('assistant:'+account.account['id'])
    assert client.post('/api/admin/publication-authorizations',headers={'Authorization':'Bearer '+token},
                       json={'operationId':uid(),'changes':[change]}).status_code==401
    admin(client,platform)
    ordinary=new_user(platform)
    with closing(AccountSession(platform.s.base_url,ordinary['username'],PASSWORD,account.home.parent/'ordinary')) as other:
        foreign=load_identity(other,account.home.parent/'ordinary-keys',{'key':'service','role':'service','name':'外部服务'})
        assert configure(client,scope(foreign,body)).status_code==403
    first=configure(client,change)
    assert first.status_code==200,first.text
    current=service_status(account,identity)['publicationAuthorization']
    assert current['revision']==1 and current['enabled']
    assert current in client.get('/api/admin/publication-authorizations').json()['currentAuthorizations']
    assert not {'privateKey','password','token'} & current.keys()
    disabled=new_user(platform,kind='isp')
    # An internal account without a bound service is not sufficient.
    assert configure(client,{**change,'agentId':disabled['id']}).status_code==403


def test_batch_is_atomic_and_old_receipt_is_separate_from_current_revocation(platform,client,internal):
    admin(client,platform);account,identity=internal;body=publication(platform,identity);change=scope(identity,body)
    operation=uid()
    assert configure(client,change,{**change,'agentId':'unavailable-identity'}).status_code==403
    assert service_status(account,identity)['publicationAuthorization'] is None
    first=configure(client,change,operation=operation).json()
    revoked=configure(client,{**change,'expectedRevision':1,'enabled':False}).json()
    assert revoked['currentAuthorizations'][0]['revision']==2
    replay=configure(client,change,operation=operation).json()
    assert replay['authorizationReceipt']==first['authorizationReceipt']
    assert replay['currentAuthorizations'][0]['enabled'] is False
    assert replay['currentAuthorizations'][0]['revision']==2
    assert configure(client,{**change,'enabled':False},operation=operation).status_code==409
    assert configure(client,change).status_code==409
    assert configure(client,change,change).status_code==400


@pytest.mark.parametrize('field,value',[
    ('expectedRevision',True),('maxTemplateSeconds',True),('maxTemplateSeconds',59),('maxTemplateSeconds',31622401),
    ('enabled',1),('termsDigest','sha256:bad'),('extra',True),('agentId',[]),
])
def test_authorization_rejects_invalid_fields(platform,client,internal,field,value):
    admin(client,platform);_,identity=internal
    change=scope(identity,publication(platform,identity));change[field]=value
    assert configure(client,change).status_code==400


def test_concurrent_scope_revision_and_admin_revocation_are_checked_at_commit(platform,client,internal,monkeypatch):
    admin(client,platform);_,identity=internal;change=scope(identity,publication(platform,identity))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:configure(client,change),range(2)))
    assert sorted(r.status_code for r in results)==[200,409]
    operator=new_user(platform)
    platform.auth.set_roles('admin',operator['id'],['admin'],[])
    original=platform.db.tx
    class RevokeBeforeTransaction:
        def __enter__(self):
            platform.db.run("DELETE FROM user_roles WHERE user_id=?",operator['id'])
            self.tx=original();return self.tx.__enter__()
        def __exit__(self,*args):return self.tx.__exit__(*args)
    with monkeypatch.context() as patch:
        patch.setattr(platform.db,'tx',RevokeBeforeTransaction)
        with pytest.raises(LabError,match='administrator'):
            platform.publication_authorizations.configure(operator['id'],{'operationId':uid(),'changes':[{**change,'expectedRevision':1,'enabled':False}]})
    assert platform.publication_authorizations.current(identity.agent_id)['enabled']


def test_managed_publication_rechecks_scope_and_original_signature_lifetime(platform,client,internal,monkeypatch):
    admin(client,platform);account,identity=internal
    body=publication(platform,identity);change=scope(identity,body)
    assert configure(client,change).status_code==200
    managed={**body,'authorizationRevision':1}
    prepared=platform.publications.prepare(account.account['id'],identity.agent_id,managed)
    templates=platform.market.endorse_service_templates(identity.protocol_id,identity.signer.jwk,body['templates'],platform.payment_provider)
    assert configure(client,{**change,'expectedRevision':1,'enabled':False}).status_code==200
    with pytest.raises(LabError,match='authorization'):
        with platform.db.tx() as conn:platform.publications.commit_in(conn,account.account['id'],prepared,templates)
    assert service_status(account,identity)['revision']==0
    assert configure(client,{**change,'expectedRevision':2}).status_code==200
    assert submit(account,identity,{**body,'authorizationRevision':3}).status_code==200
    assert submit(account,identity,{**body,'authorizationRevision':3}).status_code==200
    changed=publication(platform,identity,revision=2,name='扩大公示范围')
    assert submit(account,identity,{**changed,'authorizationRevision':3}).status_code==403
    assert submit(account,identity,{**changed,'authorizationRevision':True}).status_code==403
    # A valid signature with an overlong expiry is still outside the policy.
    long=sign_template({**draft(platform),'provider':identity.protocol_id,'validUntil':fmt_time(utcnow()+timedelta(days=32))},identity.signer,'provider',utcnow())
    longer=publication(platform,identity,revision=2,template=long)
    assert submit(account,identity,{**longer,'authorizationRevision':3}).status_code==403
    # The policy never replaces a provider's original signature.
    forged=deepcopy(body);forged['operationId']=uid();forged['declaration']['revision']=2
    assert submit(account,identity,{**forged,'authorizationRevision':3}).status_code==400


def test_human_terms_change_disables_policy_but_expiry_only_renewal_preserves_it(platform,client,internal):
    admin(client,platform);account,identity=internal
    first=publication(platform,identity);change=scope(identity,first)
    configure(client,change)
    assert submit(account,identity,{**first,'authorizationRevision':1}).status_code==200
    second=publication(platform,identity,revision=2)
    assert submit(account,identity,second).status_code==200
    assert service_status(account,identity)['publicationAuthorization']['revision']==1
    third=publication(platform,identity,revision=3,name='所属方手工修改的服务')
    assert submit(account,identity,third).status_code==200
    policy=service_status(account,identity)['publicationAuthorization']
    assert not policy['enabled'] and policy['revision']==2
    stale=publication(platform,identity,revision=4)
    assert submit(account,identity,{**stale,'authorizationRevision':1}).status_code==403
    # Restoring the old accepted operation returns its receipt, not authority or source rollback.
    assert submit(account,identity,{**first,'authorizationRevision':1}).status_code==200
    assert service_status(account,identity)['revision']==3
    assert not service_status(account,identity)['publicationAuthorization']['enabled']


@contextmanager
def resident(platform,tmp_path):
    user=new_user(platform,kind='isp')
    path=tmp_path/'official.json'
    path.write_text(json.dumps({'platform_url':platform.s.base_url,'username':user['username'],'password':PASSWORD,
        'data_dir':'./resident','identities':[{'key':'service','role':'service','name':'官方测试服务'}]}),encoding='utf-8')
    settings=RuntimeConfig.load(path)
    runtime=ResidentRuntime(settings,modules=(ProviderRole(),)).start()
    try:yield runtime,LocalClient(settings.home),settings
    finally:runtime.close()


def preview(client,platform):
    service={'profile':'service','name':'官方测试服务','description':'软件开发与文档服务','tags':['开发'],
             'categoryId':platform.discovery.categories()[0]['id'],'templates':[draft(platform)]}
    result=client.call('provider.preview',{'service':service})
    assert result['status']=='completed',result
    return {key:result['data'][key] for key in ('service','contentDigest','expectedRevision')}


@pytest.mark.parametrize('decision',['pending','rejected','expired'])
def test_new_policy_cannot_bypass_existing_user_decision(platform,client,tmp_path,decision,monkeypatch):
    admin(client,platform)
    with resident(platform,tmp_path) as (runtime,ipc,_):
        args=preview(ipc,platform);op=uid();identity=runtime.identities[0]
        waiting=ipc.call('provider.publish',args,operation_id=op)
        assert waiting['status']=='waiting'
        ref=waiting['data']['confirmationRef']
        if decision=='rejected':
            item=runtime.confirmations.get(ref)
            runtime.confirmations.decide(ref,item['bindingDigest'],'reject',authority=runtime.confirmations.user_channel(),source='test-only-owner-rejection')
        if decision=='expired':
            runtime.store.db.execute('UPDATE runtime_confirmations SET expires=0 WHERE id=?',(ref,))
        configure(client,scope(identity,args['service']))
        result=ipc.call('provider.publish',args,operation_id=op)
        assert result['status']==('waiting' if decision=='pending' else 'rejected'),result
        assert result['data']['confirmationStatus']==decision
        assert service_status(runtime.account,identity)['revision']==0


def test_sdk_managed_scope_recovers_lost_reply_without_another_signature_or_confirmation(platform,client,tmp_path,monkeypatch):
    admin(client,platform)
    with resident(platform,tmp_path) as (runtime,ipc,settings):
        args=preview(ipc,platform);op=uid();identity=runtime.identities[0]
        configure(client,scope(identity,args['service']))
        original=runtime.account.api
        def lose(method,path,body=None):
            result=original(method,path,body)
            if method=='POST' and path.endswith('/publications'):raise httpx.ReadError('test original result lost')
            return result
        with monkeypatch.context() as patch:
            patch.setattr(runtime.account,'api',lose)
            result=ipc.call('provider.publish',args,operation_id=op)
            assert result['status']=='unknown',result
        assert not runtime.confirmations.list()
        saved=runtime.store.checkpoint('provider:'+identity.agent_id,'publish:'+op)['body']
        assert saved['authorizationRevision']==1
        runtime.close()
        restarted=ResidentRuntime(settings,modules=(ProviderRole(),)).start()
        try:
            with monkeypatch.context() as patch:
                patch.setattr('ioalab_sdk.provider.publication.sign_template',lambda *a,**kw:(_ for _ in ()).throw(AssertionError('must recover the original signatures')))
                completed=LocalClient(settings.home).call('provider.publish',args,operation_id=op)
            assert completed['status']=='completed',completed
            assert completed['data']['publicationReceipt']['acceptedRevision']==1
            assert restarted.store.checkpoint('provider:'+identity.agent_id,'publish:'+op)['body']==saved
            assert not restarted.confirmations.list()
        finally:restarted.close()


def test_sdk_cached_success_repairs_latest_projection_without_resigning_or_rolling_back(platform,client,tmp_path,monkeypatch):
    admin(client,platform)
    with resident(platform,tmp_path) as (runtime,ipc,_):
        args=preview(ipc,platform);op=uid();identity=runtime.identities[0]
        configure(client,scope(identity,args['service']))
        with monkeypatch.context() as patch:
            patch.setattr(platform.discovery,'register_one',lambda *a,**kw:(_ for _ in ()).throw(OSError('test temporary index outage')))
            first=ipc.call('provider.publish',args,operation_id=op)
        assert first['status']=='completed' and not first['data']['currentPublication']['indexReady']
        # The owner publishes a newer name before the old operation resumes.
        newer=publication(platform,identity,revision=2,name='较新的人工公示')
        with monkeypatch.context() as patch:
            patch.setattr(platform.discovery,'register_one',lambda *a,**kw:(_ for _ in ()).throw(OSError('test temporary index outage')))
            assert submit(runtime.account,identity,newer).status_code==200
        with monkeypatch.context() as patch:
            patch.setattr('ioalab_sdk.provider.publication.sign_template',lambda *a,**kw:(_ for _ in ()).throw(AssertionError('must not sign during projection recovery')))
            result=ipc.call('provider.publish',args,operation_id=op)
        assert result['status']=='completed',result
        assert result['data']['publicationReceipt']==first['data']['publicationReceipt']
        assert result['data']['currentPublication']['revision']==2
        assert result['data']['currentPublication']['indexReady']
        assert platform.discovery.get_card(identity.agent_id)['name']=='较新的人工公示'


def test_all_official_terms_match_core_and_expiry_is_the_only_renewable_business_field(tmp_path):
    from ioalab_services import prepare
    from ioalab_services.catalog import definitions
    from ioalab.runtime.packages import PackageStore
    packages=PackageStore(tmp_path/'packages')
    pay={'id':'https://lab.example/pay','jwksUri':'https://lab.example/pay/.well-known/jwks.json'}
    for definition in definitions():
        verifier=definition['verifier']
        ref=packages.put(verifier_id=verifier['id'],source=verifier['source'],reads=verifier.get('reads',[]))
        service=prepare(definition['id'],profile='service',verifier_ref=ref,payment_provider=pay)['service']
        protocol='https://lab.example/agents/'+definition['id']
        terms=publication_terms(protocol,service)
        assert terms==core_terms(protocol,service)
        renewed=deepcopy(service);renewed['templates'][0]['validUntil']=fmt_time(utcnow()+timedelta(days=30))
        assert publication_terms(protocol,renewed)==terms
        for field,value in [('deadlines',{'respondWithin':'PT10S'}),('signers',{'changed':True}),('newTerm','future-rule')]:
            changed=deepcopy(service);changed['templates'][0][field]=value
            assert publication_terms(protocol,changed)!=terms


def test_preexisting_confirmation_has_priority_over_later_policy(platform,client,tmp_path):
    admin(client,platform)
    with resident(platform,tmp_path) as (runtime,ipc,_):
        args=preview(ipc,platform);op=uid();identity=runtime.identities[0]
        checked=ipc.call('provider.preview',{'service':args['service']})['data']
        binding={'actor':identity.agent_id,'operationId':op,
                 'subject':{'contentDigest':args['contentDigest'],'expectedRevision':args['expectedRevision']},
                 'stage':'provider.publish','action':'service.publish','preview':checked['preview']}
        original=runtime.confirmations.ensure(binding)
        user_decision(runtime,original['reference'],decision='reject')
        assert configure(client,scope(identity,args['service'])).status_code==200
        result=ipc.call('provider.publish',args,operation_id=op)
        assert result['status']=='rejected' and result['data']['confirmationRef']==original['reference']
        assert service_status(runtime.account,identity)['revision']==0


def test_failed_manual_publication_does_not_revoke_existing_scope(platform,client,internal):
    admin(client,platform);account,identity=internal
    first=publication(platform,identity);change=scope(identity,first)
    assert configure(client,change).status_code==200
    assert submit(account,identity,{**first,'authorizationRevision':1}).status_code==200
    actor=identity.agent_id
    platform.db.run("CREATE TRIGGER test_manual_scope_commit BEFORE INSERT ON registry_publication_operations WHEN NEW.actor='"+actor+"' BEGIN SELECT RAISE(ABORT,'test result outage'); END")
    try:
        assert submit(account,identity,publication(platform,identity,revision=2,name='提交失败的新条款')).status_code==500
    finally:platform.db.run('DROP TRIGGER test_manual_scope_commit')
    current=service_status(account,identity)
    assert current['revision']==1
    assert current['publicationAuthorization']['revision']==1 and current['publicationAuthorization']['enabled']


def test_disabled_internal_service_cannot_receive_or_use_policy(platform,client,internal):
    admin(client,platform);account,identity=internal
    first=publication(platform,identity);change=scope(identity,first)
    assert configure(client,change).status_code==200
    platform.db.run("UPDATE agents SET status='disabled' WHERE id=?",identity.agent_id)
    assert configure(client,{**change,'expectedRevision':1}).status_code==403
    assert submit(account,identity,{**first,'authorizationRevision':1}).status_code==403
