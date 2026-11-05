"""Official coordination over actual HTTP/WS/IPC; host is an explicit non-production double."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
import os
import sys
import uuid

import httpx
import pytest
from a2c.codec import fmt_time,utcnow
from ioalab_services import deployment as configuration
from ioalab_services.deployment import Deployment
from ioalab_services.preparation import prepare
from ioalab_services.authorization import authorize
from ioalab_services.coordinator import tick,status
from ioalab_services.catalog import load_domains
from ioalab_sdk.provider import ProviderRole
from ioalab_sdk.runtime.config import RuntimeConfig
from ioalab_sdk.runtime.files import private_json,read_json
from ioalab_sdk.runtime.ipc import LocalClient
from ioalab_sdk.runtime.protocol import RuntimeFault
from ioalab_sdk.runtime.resident import ResidentRuntime
from conftest import wait_for


def uid():return str(uuid.uuid4())


@pytest.fixture
def deployment(platform,tmp_path,monkeypatch):
    domain=next(d for d in load_domains() if any(s['id']=='wechat-posts' for s in d['services']))
    domain=deepcopy(domain);domain['services']=[s for s in domain['services'] if s['id']=='wechat-posts']
    monkeypatch.setattr(configuration,'load_domains',lambda:[deepcopy(domain)])
    path=tmp_path/'official.json'
    private_json(path,{'platform_url':platform.s.base_url,'data_dir':'./deployment',
                      'host':{'command':[sys.executable,'-m','unused-fixture-host'],'data_dir':'./swarm'}})
    return Deployment(path)


class UnusedHost:
    def capabilities(self):return {'available':True,'testDouble':True}
    def cancel(self,*_):return True
    def run(self,*_):raise AssertionError('Publication never starts model production')


@contextmanager
def active(deployment):
    account=deployment.domains[0]['account'];path=deployment.config_path(account)
    config=RuntimeConfig.load(path);host=UnusedHost()
    runtime=ResidentRuntime(config,modules=(ProviderRole(host),),host_status=host.capabilities,assembly='ioalab-jiuwen').start()
    try:
        wait_for(lambda:runtime.connection.connected)
        yield runtime,LocalClient(config.home),account
    finally:runtime.close()


def admin(platform):return platform.auth.token('admin')


def snapshot(home):
    return {p.relative_to(home).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in home.rglob('*') if p.is_file()}


def test_preparation_uploads_original_verifier_and_preserves_complete_configuration(platform,deployment,monkeypatch):
    from ioalab_services import preparation
    monkeypatch.setattr(ProviderRole,'publish',lambda *_:(_ for _ in ()).throw(AssertionError('prepare must never publish')))
    result=prepare(deployment)
    assert result['status']=='completed' and result['accounts']==result['services']==1
    assert not result['publishedDuringPrepare'] and not result['modelStarted']
    state=deployment.state(prepared=True);account=next(iter(state['accounts']));entry=state['accounts'][account]
    service=entry['services']['wechat-posts']
    assert service['verifierRef']['uri'].startswith('ioalab://pkg/sha256:')
    assert not platform.publications.status(platform.identities.agent(entry['identities'][0]['agentId'])['owner_id'],entry['identities'][0]['agentId'])['published']
    path=deployment.config_path(account);config=read_json(path)
    assert config['provider']['require_publication_authorization'] is True
    config['provider']['services']['wechat-posts']['instructions']+=' 保留部署方修改。'
    private_json(path,config)
    before=snapshot(deployment.home)
    prepare(deployment)
    assert snapshot(deployment.home)==before


def test_lost_upload_reply_resumes_same_operation_identity_and_login(platform,deployment,monkeypatch):
    from ioalab_services import preparation
    original=preparation.call;lost=[]
    def lose(client,name,args,*,operation_id):
        result=original(client,name,args,operation_id=operation_id)
        if name=='provider.verifier.publish':lost.append(operation_id);raise RuntimeFault('unknown_result','test upload response lost')
        return result
    with monkeypatch.context() as patch:
        patch.setattr(preparation,'call',lose)
        with pytest.raises(RuntimeFault,match='lost'):prepare(deployment)
    partial=deployment.state();account=next(iter(partial['accounts']));entry=partial['accounts'][account]
    actor=entry['identities'][0]['agentId'];op=entry['services']['wechat-posts']['verifierOperation']
    before=platform.db.one("SELECT COUNT(*) AS n FROM wallet_log WHERE user_id=? AND kind='login'",platform.identities.agent(actor)['owner_id'])['n']
    assert lost==[op]
    assert prepare(deployment)['status']=='completed'
    complete=deployment.state(prepared=True)['accounts'][account]
    assert complete['identities'][0]['agentId']==actor
    assert complete['services']['wechat-posts']['verifierOperation']==op
    after=platform.db.one("SELECT COUNT(*) AS n FROM wallet_log WHERE user_id=? AND kind='login'",platform.identities.agent(actor)['owner_id'])['n']
    assert before==after


def test_crash_after_final_config_write_finishes_without_reopening_sdk(deployment,monkeypatch):
    from ioalab_services import preparation
    original=preparation.private_json
    def crash(path,value):
        original(path,value)
        if 'host' in value:raise OSError('test crash after final configuration replace')
    with monkeypatch.context() as patch:
        patch.setattr(preparation,'private_json',crash)
        with pytest.raises(OSError,match='crash'):prepare(deployment)
    with monkeypatch.context() as patch:
        patch.setattr(preparation,'ResidentRuntime',lambda *_a,**_k:(_ for _ in ()).throw(AssertionError('complete file must be reused')))
        assert prepare(deployment)['status']=='completed'
    assert deployment.state(prepared=True)


def test_authorization_recovers_original_request_and_never_reenables_revoked_scope(platform,deployment,monkeypatch):
    prepare(deployment)
    original=httpx.Client.post
    def lose(self,url,*args,**kwargs):
        result=original(self,url,*args,**kwargs)
        if str(url).endswith('/api/admin/publication-authorizations'):raise httpx.ReadError('test authorization response lost')
        return result
    with monkeypatch.context() as patch:
        patch.setattr(httpx.Client,'post',lose)
        with pytest.raises(httpx.ReadError):authorize(deployment,admin(platform))
    saved=read_json(deployment.home/'authorization.json')
    result=authorize(deployment,admin(platform))
    assert result['authorizationReceipt']['operationId']==saved['body']['operationId']
    current=result['currentAuthorizations'][0]
    platform.publication_authorizations.configure('admin',{'operationId':uid(),'changes':[
        {'agentId':current['agentId'],'expectedRevision':current['revision'],'termsDigest':current['termsDigest'],
         'maxTemplateSeconds':current['maxTemplateSeconds'],'enabled':False}]})
    with pytest.raises(RuntimeFault,match='revoked'):authorize(deployment,admin(platform))
    assert read_json(deployment.home/'authorization.json')==saved
    assert not platform.publication_authorizations.current(current['agentId'])['enabled']


def test_strict_sdk_missing_scope_never_creates_confirmation(platform,deployment):
    prepare(deployment)
    with active(deployment) as (runtime,ipc,account):
        service=deployment.state(prepared=True)['accounts'][account]['services']['wechat-posts']['service']
        checked=ipc.call('provider.preview',{'service':service})['data']
        args={k:checked[k] for k in ('service','contentDigest','expectedRevision')};operation=uid()
        rejected=ipc.call('provider.publish',args,operation_id=operation)
        assert rejected['status']=='rejected' and rejected['error']['recovery']=='configure_publication'
        assert not runtime.confirmations.list()
        authorize(deployment,admin(platform))
        result=ipc.call('provider.publish',args,operation_id=operation)
        assert result['status']=='completed',result
        assert not runtime.confirmations.list()


def test_coordinator_publishes_renews_and_preserves_old_template_and_pause(platform,deployment):
    prepare(deployment);authorize(deployment,admin(platform))
    with active(deployment) as (runtime,ipc,account):
        first=tick(deployment,launch=False)
        service=first['accounts'][0]['services'][0]
        assert service['publication']['status']=='completed',first
        profile='wechat-posts';actor=runtime.identities[0].agent_id
        old=read_json(deployment.home/'publications.json')[actor]
        original_template=platform.publications.templates(actor)[0]
        short=deepcopy(old['args']['service'])
        short['templates'][0]['validUntil']=fmt_time(utcnow()+timedelta(days=2))
        preview=ipc.call('provider.preview',{'service':short})['data']
        args={k:preview[k] for k in ('service','contentDigest','expectedRevision')}
        assert ipc.call('provider.publish',args,operation_id=uid())['status']=='completed'
        assert ipc.call('provider.pause',{'profile':profile})['status']=='completed'
        second=tick(deployment,launch=False)
        renewed=second['accounts'][0]['services'][0]
        assert renewed['publication']['acceptedRevision']==3,second
        assert not second['allServicesReady']
        current=ipc.call('provider.status',{'profile':profile})['data']
        assert current['revision']==3 and current['publishedTermsDigest']==current['publicationAuthorization']['termsDigest']
        assert not current['admissionPolicy']['accepting']
        assert platform.publications.templates(actor,original_template['templateDigest'])==[original_template]
        third=tick(deployment,launch=False)
        assert third['accounts'][0]['services'][0]['status']=='current'
        assert ipc.call('provider.status',{'profile':profile})['data']['revision']==3


def test_coordinator_lost_ipc_reply_reuses_same_operation(platform,deployment,monkeypatch):
    prepare(deployment);authorize(deployment,admin(platform))
    with active(deployment) as (runtime,ipc,_):
        original=LocalClient.call;operations=[]
        def lose(self,name,args=None,*,operation_id=None):
            result=original(self,name,args,operation_id=operation_id)
            if name=='provider.publish':operations.append(operation_id);raise RuntimeFault('unknown_result','test IPC reply lost')
            return result
        with monkeypatch.context() as patch:
            patch.setattr(LocalClient,'call',lose)
            first=tick(deployment,launch=False)
        assert first['accounts'][0]['services'][0]['error']['code']=='unknown_result'
        actor=runtime.identities[0].agent_id
        before=read_json(deployment.home/'publications.json')[actor]
        assert before['status']=='unknown' and operations==[before['operationId']]
        second=tick(deployment,launch=False)
        after=read_json(deployment.home/'publications.json')[actor]
        assert after['operationId']==before['operationId'] and after['args']==before['args']
        assert after['status']=='completed' and after['result']['acceptedRevision']==1


def test_status_is_read_only_and_environment_is_minimal(platform,deployment,monkeypatch):
    prepare(deployment)
    monkeypatch.setenv('IOALAB_ADMIN_PASSWORD','must-not-enter-host')
    monkeypatch.setenv('CUSTOM_ADMIN_TOKEN','must-not-enter-host')
    monkeypatch.setenv('UNRELATED_PRIVATE_KEY','must-not-enter-host')
    env=deployment.environment()
    assert not {'IOALAB_ADMIN_PASSWORD','CUSTOM_ADMIN_TOKEN','UNRELATED_PRIVATE_KEY'} & set(env)
    before=snapshot(deployment.home)
    result=status(deployment)
    assert result['accounts'][0]['error']['code']=='not_ready'
    assert snapshot(deployment.home)==before


def test_deployment_rejects_configuration_change_and_unrelated_data(deployment):
    prepare(deployment)
    value=read_json(deployment.path);value['max_model_runs']=3;private_json(deployment.path,value)
    with pytest.raises(RuntimeFault,match='changed'):prepare(Deployment(deployment.path))
    value['max_model_runs']=2;value['data_dir']='./foreign';private_json(deployment.path,value)
    other=Deployment(deployment.path);other.home.mkdir();(other.home/'unrelated.txt').write_text('keep',encoding='utf-8')
    with pytest.raises(RuntimeFault,match='dedicated'):prepare(other)
    assert (other.home/'unrelated.txt').read_text()=='keep'


def test_current_observation_retains_expired_originals(platform,deployment,monkeypatch):
    prepare(deployment);authorize(deployment,admin(platform))
    with active(deployment) as (runtime,ipc,_):
        tick(deployment,launch=False)
        identity=runtime.identities[0]
        original=platform.publications.status(runtime.account.account['id'],identity.agent_id)
        with monkeypatch.context() as patch:
            patch.setattr(platform.publications,'clock',lambda:(utcnow()+timedelta(days=31)).timestamp())
            expired=platform.publications.status(runtime.account.account['id'],identity.agent_id)
        assert not expired['templateReady']
        assert expired['templateExpiresAt']==original['templateExpiresAt']
        assert expired['publishedTermsDigest']==original['publishedTermsDigest']


def test_manual_terms_change_blocks_old_controller_without_overwrite(platform,deployment):
    from test_service_publication import publication,submit
    prepare(deployment);authorize(deployment,admin(platform))
    with active(deployment) as (runtime,ipc,_):
        tick(deployment,launch=False)
        identity=runtime.identities[0]
        # Explicit test owner signs a manual change through the normal public endpoint.
        body=publication(platform,identity,revision=2,name='所属方手工公示')
        assert submit(runtime.account,identity,body).status_code==200
        result=tick(deployment,launch=False)
        assert result['accounts'][0]['services'][0]['error']['code']=='configuration_changed'
        current=ipc.call('provider.status',{'profile':'wechat-posts'})['data']
        assert current['revision']==2 and not current['publicationAuthorization']['enabled']


def test_start_receives_only_sanitized_environment(platform,deployment,monkeypatch):
    from ioalab_services import coordinator
    prepare(deployment)
    monkeypatch.setenv('CUSTOM_ADMIN_TOKEN','never-inherit-this')
    monkeypatch.setenv('IOALAB_ADMIN_PASSWORD','never-inherit-this')
    with active(deployment) as (runtime,ipc,account):
        seen=[]
        def started(path,**kwargs):
            seen.append(kwargs['environment'])
            return ipc.call('runtime.status')
        monkeypatch.setattr(coordinator,'start',started)
        coordinator.connected(deployment,account,launch=True)
        assert seen and not {'CUSTOM_ADMIN_TOKEN','IOALAB_ADMIN_PASSWORD'} & seen[0].keys()


def test_existing_user_confirmation_cannot_be_bypassed_in_strict_mode(platform,deployment):
    prepare(deployment)
    path=deployment.config_path(deployment.domains[0]['account']);config=read_json(path)
    config['provider']['require_publication_authorization']=False;private_json(path,config)
    with active(deployment) as (runtime,ipc,account):
        service=deployment.state(prepared=True)['accounts'][account]['services']['wechat-posts']['service']
        checked=ipc.call('provider.preview',{'service':service})['data']
        args={k:checked[k] for k in ('service','contentDigest','expectedRevision')};operation=uid()
        result=ipc.call('provider.publish',args,operation_id=operation)
        assert result['status']=='waiting';reference=result['data']['confirmationRef']
    config['provider']['require_publication_authorization']=True;private_json(path,config)
    authorize(deployment,admin(platform))
    with active(deployment) as (runtime,ipc,_):
        result=ipc.call('provider.publish',args,operation_id=operation)
        assert result['status']=='rejected'
        assert runtime.confirmations.get(reference)['status']=='pending'
        assert len(runtime.confirmations.list())==1


def test_administrator_login_uses_existing_cookie_without_persisting_credentials(deployment,monkeypatch):
    from ioalab_services.authorization import administrator_session
    requests=[];original=httpx.Client
    def respond(request):
        requests.append(request)
        return httpx.Response(200,json={'ok':True},headers={'set-cookie':'ioalab_session=fixture-web-session; Path=/; HttpOnly'})
    def client(**kwargs):return original(**kwargs,transport=httpx.MockTransport(respond))
    monkeypatch.setattr(httpx,'Client',client)
    assert administrator_session(deployment,'custom-admin','fixture-admin-password')=='fixture-web-session'
    assert requests[0].url.path=='/api/admin/login'
    assert json.loads(requests[0].content)=={'username':'custom-admin','password':'fixture-admin-password'}
    assert not deployment.home.exists()
    with pytest.raises(RuntimeFault,match='unavailable'):administrator_session(deployment,'custom-admin',None)


def test_sanitized_child_can_identify_architecture_and_import_real_wasmtime(deployment):
    import platform
    import subprocess
    result=subprocess.run([sys.executable,'-I','-X','utf8','-c',
        'import platform,wasmtime;print(platform.machine())'],env=deployment.environment(),
        capture_output=True,text=True,encoding='utf-8',timeout=20)
    assert result.returncode==0,result.stderr
    assert result.stdout.strip()==platform.machine() and result.stdout.strip()


def test_incomplete_completed_recipe_is_not_reported_as_success(deployment):
    prepare(deployment)
    state=deployment.state(prepared=True);account=next(iter(state['accounts']))
    del state['accounts'][account]['services']['wechat-posts']
    deployment.save(state)
    before=snapshot(deployment.home)
    with pytest.raises(RuntimeFault,match='incomplete'):prepare(deployment)
    assert snapshot(deployment.home)==before
