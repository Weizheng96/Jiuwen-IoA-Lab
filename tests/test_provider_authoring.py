"""Original A2C drafts and one HTTP error contract, through real local IPC/HTTP."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
import uuid

import httpx
import pytest
from a2c.codec import WIRE_VERSION, parse_time, utcnow
from ioalab_sdk.provider import ProviderRole
from ioalab_sdk.runtime.config import RuntimeConfig
from ioalab_sdk.runtime.ipc import LocalClient
from ioalab_sdk.runtime.resident import ResidentRuntime
from conftest import wait_for
from test_resident_runtime import configuration


@pytest.fixture
def author(platform,tmp_path):
    path,_=configuration(platform,tmp_path)
    settings=RuntimeConfig.load(path)
    runtime=ResidentRuntime(settings,modules=(ProviderRole(),)).start()
    try:
        wait_for(lambda:runtime.connection.connected)
        yield runtime
    finally:runtime.close()


def terms():
    return {'formation':'proposal','deliverables':[{'name':'program','description':'实际代码','specSchema':{}},
            {'name':'manual','description':'实际使用说明','specSchema':{}}], 'inputs':[],
            'deadlines':{'respondWithin':'PT10M','startWithin':'PT30M','deliverWithin':{'program':'PT30M','manual':'PT10M'}},
            'pricing':{'currency':'CNY','depositFormula':'0','proposalFee':0,'paymentWeights':{'program':1,'manual':1}},'samples':[]}


def test_draft_preserves_multi_deliverable_business_clauses_and_never_signs_or_publishes(author,platform,monkeypatch):
    original=terms();args={'profile':'service','terms':deepcopy(original)};now=utcnow()
    def no_sign(*args,**kwargs):raise AssertionError('Draft must not sign')
    monkeypatch.setattr('ioalab_sdk.provider.publication.sign',no_sign)
    monkeypatch.setattr('ioalab_sdk.provider.publication.sign_template',no_sign)
    client=LocalClient(author.config.home);value=client.call('provider.template.draft',args)
    assert value['status']=='completed',value
    draft=value['data']['template'];pricing=draft['pricing'];pay=platform.payment_provider
    assert draft['a2cVersion']==WIRE_VERSION and draft['kind']=='template'
    assert now+timedelta(days=29)<parse_time(draft['validUntil'])<now+timedelta(days=31)
    assert pricing['paymentProviders']==[pay]
    assert {key:value for key,value in pricing.items() if key!='paymentProviders'}==original['pricing']
    assert all(draft[key]==original[key] for key in original if key!='pricing')
    assert not {'provider','templateDigest','signatures'} & draft.keys()
    assert args['terms']==original
    actor=author.identities[1].agent_id
    assert not platform.db.rows('SELECT 1 FROM registry_publications WHERE actor=?',actor)
    assert not author.confirmations.list()
    service={'profile':'service','name':'多交付件业务','description':'公开业务说明','tags':[],
             'categoryId':platform.discovery.categories()[0]['id'],'templates':[draft]}
    assert client.call('provider.preview',{'service':service})['status']=='completed'


def test_preview_rejects_local_resources_and_acceptance_with_actionable_fields(author):
    service={'profile':'service','name':'业务名称','description':'公开服务说明','tags':[],'templates':[],
             'resources':{'local':'business-only'},'acceptance':{}}
    result=LocalClient(author.config.home).call('provider.preview',{'service':service})
    assert result['status']=='rejected' and result['error']['code']=='invalid_input',result
    assert result['error']['recovery']=='correct_input'
    assert "unknown=['acceptance', 'resources']" in result['error']['message']


def test_draft_concurrent_replay_restart_and_changed_terms_have_one_original_validity(author):
    args={'profile':'service','terms':terms()};operation=str(uuid.uuid4())
    def call(_):return LocalClient(author.config.home).call('provider.template.draft',args,operation_id=operation)
    with ThreadPoolExecutor(max_workers=4) as pool:values=list(pool.map(call,range(4)))
    assert all(value['status']=='completed' and value==values[0] for value in values),values
    settings=author.config;author.close()
    fresh=ResidentRuntime(settings,modules=(ProviderRole(),)).start()
    try:
        repeated=LocalClient(settings.home).call('provider.template.draft',args,operation_id=operation)
        assert repeated==values[0]
        changed=deepcopy(args);changed['terms']['pricing']['proposalFee']=1
        result=LocalClient(settings.home).call('provider.template.draft',changed,operation_id=operation)
        assert result['status']=='rejected' and result['error']['code']=='id_conflict'
    finally:fresh.close()


@pytest.mark.parametrize('bad',['amount','version','payment','missing','price_type'])
def test_draft_rejects_invented_or_runtime_owned_fields_without_side_effects(author,bad):
    value=terms()
    if bad=='amount':value['amount']=299
    elif bad=='version':value['a2cVersion']='invented'
    elif bad=='payment':value['pricing']['paymentProviders']=[{'id':'foreign'}]
    elif bad=='missing':value.pop('deadlines')
    else:value['pricing']='not-an-object'
    result=LocalClient(author.config.home).call('provider.template.draft',{'profile':'service','terms':value})
    assert result['status']=='rejected' and result['error']['code']=='invalid_input',result


def test_countersign_uses_real_package_reference_and_preapproval_is_not_an_ordinary_draft(author):
    client=LocalClient(author.config.home)
    uploaded=client.call('provider.verifier.publish',{'profile':'service','verifierId':'authoring_check@1.0',
        'source':"def verify(ctx):\n    raise RuntimeError('Drafting must not execute verifier')\n",'kind':'script'})
    assert uploaded['status']=='completed',uploaded
    value=terms();value['formation']='countersign';value['pricing'].pop('proposalFee')
    value['pricing'].update(priceFactors={'program':{},'manual':{}},priceFormula={'program':'7','manual':'2'})
    value['deadlines']['acceptWithin']={'program':'PT10M','manual':'PT10M'}
    value['acceptance']={name:{'verifiers':{'script':uploaded['data']},'notCovered':[]} for name in ('program','manual')}
    result=client.call('provider.template.draft',{'profile':'service','terms':value})
    assert result['status']=='completed',result
    assert result['data']['template']['acceptance']==value['acceptance']
    value['formation']='preapproval'
    denied=client.call('provider.template.draft',{'profile':'service','terms':value})
    assert denied['status']=='rejected' and denied['error']['code']=='invalid_input'


@pytest.mark.parametrize('body',[
    {'verifierId':'miniapp-delivery-check','kind':'text','source':'自然语言不是源码'},
    {'verifierId':'check@1.0','kind':'text','source':'def verify(ctx): pass'},
    {'source':'missing identifier'},
    {'verifierId':'check@1.0','source':None},
])
def test_invalid_verifier_is_an_explicit_input_rejection_not_unknown(author,body):
    result=LocalClient(author.config.home).call('provider.verifier.publish',{'profile':'service',**body})
    assert result['status']=='rejected' and result['error']['code']=='invalid_input',result
    assert result['error']['message'] and result['error']['recovery']=='correct_input'


@pytest.mark.parametrize('kind',['unauthorized','missing','invalid_structure','cross_origin'])
def test_http_framework_errors_use_one_public_object(author,kind):
    if kind=='unauthorized':response=author.account.http.post(author.account.base+'/api/v1/provider/packages',json={})
    elif kind=='missing':response=author.account.http.get(author.account.base+'/api/v1/route-that-does-not-exist')
    elif kind=='invalid_structure':response=author.account.http.post(author.account.base+'/api/v1/provider/packages',json=[],
        headers={'Authorization':'Bearer '+author.account.token})
    else:response=author.account.http.post(author.account.base+'/api/v1/provider/packages',json={},headers={'Origin':'https://foreign.example'})
    assert response.status_code in (401,403,404,422)
    body=response.json();assert set(body)=={'error'}
    assert set(body['error'])=={'code','message','recovery'}
    assert all(isinstance(value,str) and value for value in body['error'].values())


@pytest.mark.parametrize('status',[400,503])
def test_invalid_upstream_envelope_is_not_parsed_as_a_success_or_native_exception(author,monkeypatch,status):
    def broken(*args,**kwargs):
        response=httpx.Response(status,json={'error':'unexpected external error format'},request=httpx.Request('POST','https://example.test/packages'))
        response.raise_for_status()
    monkeypatch.setattr(author.account,'api',broken)
    result=LocalClient(author.config.home).call('provider.verifier.publish',{'profile':'service','verifierId':'check@1.0','source':'def verify(ctx): pass'})
    assert result['status']==('unknown' if status==503 else 'rejected'),result
    assert result['error']['code']==('unknown_result' if status==503 else 'protocol_error')
