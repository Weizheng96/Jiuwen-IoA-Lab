"""Actual HTTP/WS publication; classification models are explicit doubles here."""
import copy
import json
import threading
from types import SimpleNamespace

import httpx
import pytest
from a2c.codec import digest
from ioalab.errors import LabError
from ioalab.market import taxonomy
from ioalab.registry.categories import CategoryPreview
from ioalab.runtime.llm import LLM
from ioalab_sdk.provider import ProviderRole
from ioalab_sdk.runtime.config import RuntimeConfig
from ioalab_sdk.runtime.resident import ResidentRuntime
from ioalab_sdk.runtime.ipc import LocalClient
from test_service_publication import service,draft,uid
from test_resident_runtime import configuration
from test_assistant_role import user_decision
from conftest import wait_for


PUBLIC={'name':'专业软件开发','description':'按需求设计和交付微信小程序源码与部署说明','tags':['软件开发']}


def tree(tmp_path):
    taxonomy.build([{'id':'code','name':'编程','description':'生产软件','decision_rule':'提供编程能力'}],{},tmp_path)
    path=tmp_path/'taxonomy.json';value=json.loads(path.read_text(encoding='utf-8'))
    value['categories']['root']['children']=['group','cat_misc']
    value['categories']['group']={'children':['code'],'services':[]}
    path.write_text(json.dumps(value),encoding='utf-8')
    path=tmp_path/'class.json';value=json.loads(path.read_text(encoding='utf-8'))
    value['categories']['group']={'name':'数字服务','description':'数字服务'}
    path.write_text(json.dumps(value),encoding='utf-8')
    return taxonomy.categories(tmp_path)


def test_preview_uses_actual_nested_leaf_paths_without_changing_the_tree(tmp_path):
    categories=tree(tmp_path);before={p.name:p.read_bytes() for p in tmp_path.iterdir()}
    assert {item['id'] for item in categories}=={'code','cat_misc'}
    preview=CategoryPreview(lambda:categories,lambda *args:{'categoryId':'code','reason':'提供软件开发能力'})
    result=preview.preview(PUBLIC)
    assert result['status']=='matched' and [part['id'] for part in result['categoryPath']]==['root','group','code']
    assert result['taxonomyDigest']==digest({'categories':result['categories']})
    assert before=={p.name:p.read_bytes() for p in tmp_path.iterdir()}
    with pytest.raises(LabError,match='leaf category'):preview.preview({**PUBLIC,'categoryId':'group'})


@pytest.mark.parametrize('choice',[None,{}, {'categoryId':None,'reason':'跨领域不明确'},
    {'categoryId':'invented','reason':'编造分类'}, {'categoryId':'cat_misc','reason':'不确定'},
    {'categoryId':'code','reason':'','path':['invented']}, {'categoryId':True,'reason':'错误类型'}])
def test_ambiguous_missing_or_invalid_model_results_require_explicit_choice(tmp_path,choice):
    categories=tree(tmp_path)
    result=CategoryPreview(lambda:categories,lambda *args:choice).preview(PUBLIC)
    assert result['status']=='choose_category' and result['categoryId'] is None and result['categoryPath']==[]
    assert result['categories']==[{key:item[key] for key in ('id','name','description','decision_rule','path','isFallback')} for item in categories]


def test_model_failure_is_safe_and_explicit_choice_does_not_call_it(tmp_path):
    def broken(*args):raise RuntimeError('PRIVATE-provider-secret')
    preview=CategoryPreview(lambda:tree(tmp_path),broken)
    failed=preview.preview(PUBLIC)
    assert failed['status']=='choose_category' and 'PRIVATE' not in json.dumps(failed)
    assert preview.preview({**PUBLIC,'categoryId':'code'})['status']=='matched'
    for invalid in ({**PUBLIC,'categoryId':''},{**PUBLIC,'tools':['private-tool']},{**PUBLIC,'description':' '}):
        with pytest.raises(LabError):preview.preview(invalid)


def test_platform_model_adapter_uses_configured_discovery_model_and_json(monkeypatch):
    calls=[]
    def post(url,**kw):
        calls.append((url,kw))
        return httpx.Response(200,json={'choices':[{'message':{'content':'{"categoryId":"code","reason":"提供编程能力"}'}}]},request=httpx.Request('POST',url))
    monkeypatch.setattr(httpx,'post',post)
    model=LLM(SimpleNamespace(llm={'api_key':'explicit-test-key','api_base':'https://example.test/v1'},stub=False,a2x_model='configured-discovery-model'))
    assert model.classify_category(PUBLIC,[])['categoryId']=='code'
    url,request=calls[0]
    assert url.endswith('/chat/completions') and request['json']['model']=='configured-discovery-model'
    assert request['json']['response_format']=={'type':'json_object'} and request['trust_env'] is False
    assert any('json' in message['content'].casefold() for message in request['json']['messages'])
    assert json.loads(request['json']['messages'][1]['content'])['service']==PUBLIC


def test_http_preview_authentication_validation_and_no_publication_effects(platform,service,monkeypatch):
    account,identity=service;calls=[]
    def choose(value,categories):
        calls.append(value);return {'categoryId':'cat_dev','reason':'公开说明提供软件开发能力'}
    monkeypatch.setattr(platform.llm,'classify_category',choose)
    counts=lambda:{name:platform.db.one('SELECT COUNT(*) AS n FROM '+name)['n'] for name in ('registry_publications','registry_publication_operations','registry_projection')}
    before=counts();entries=platform.discovery.entries()
    result=account.api('POST','/services/category-preview',PUBLIC)
    assert result['categoryId']=='cat_dev' and result['categoryPath'][-1]['name']=='软件开发'
    assert counts()==before and platform.discovery.entries()==entries
    assert calls==[PUBLIC]
    url=account.base+'/api/v1/services/category-preview'
    assert account.http.post(url,json=PUBLIC).status_code==401
    assert account.http.post(url,headers={'Authorization':'Bearer '+account.token},json={**PUBLIC,'skills':['private']}).status_code==400
    assert calls==[PUBLIC]


def test_resident_automatic_preview_is_async_frozen_and_publish_uses_real_user_confirmation(platform,tmp_path,monkeypatch):
    config,_=configuration(platform,tmp_path);settings=RuntimeConfig.load(config)
    entered=threading.Event();release=threading.Event();calls=[]
    def choose(value,categories):
        calls.append(value);entered.set();assert release.wait(10)
        return {'categoryId':'cat_dev','reason':'公开说明提供软件开发能力'}
    monkeypatch.setattr(platform.llm,'classify_category',choose)
    service={**PUBLIC,'profile':'service','templates':[draft(platform)]};operation=uid()
    runtime=ResidentRuntime(settings,modules=(ProviderRole(),)).start()
    try:
        wait_for(lambda:runtime.connection.connected);client=LocalClient(settings.home)
        started=client.call('provider.preview',{'service':service},operation_id=operation)
        assert started['status']=='accepted',started
        assert entered.wait(3)
        job=started['data']['job']['reference']
        assert client.call('runtime.status')['status']=='completed'
        assert runtime.scheduler.get(job)['status']=='running'
        changed=client.call('provider.preview',{'service':{**service,'description':'改变说明'}},operation_id=operation)
        assert changed['status']=='rejected' and changed['error']['code']=='id_conflict'
        release.set();wait_for(lambda:runtime.scheduler.get(job)['status']=='completed')
        checked=client.call('provider.preview',{'service':service},operation_id=operation)
        assert checked['status']=='completed',checked
        result=checked['data'];assert result['service']['categoryId']=='cat_dev' and len(calls)==1
        assert result['contentDigest']==digest(result['service'])
        assert runtime.account.api('GET','/services/'+runtime.identities[1].agent_id+'/status')['revision']==0
    finally:
        release.set();runtime.close()
    # Restart resumes the exact preview, without a second model decision.
    monkeypatch.setattr(platform.llm,'classify_category',lambda *a:(_ for _ in ()).throw(AssertionError('Do not reclassify confirmed content')))
    runtime=ResidentRuntime(settings,modules=(ProviderRole(),)).start()
    try:
        wait_for(lambda:runtime.connection.connected);client=LocalClient(settings.home)
        assert client.call('provider.preview',{'service':service},operation_id=operation)['data']==result
        args={'service':result['service'],'contentDigest':result['contentDigest'],'expectedRevision':result['expectedRevision']}
        action=uid();waiting=client.call('provider.publish',args,operation_id=action)
        assert waiting['status']=='waiting'
        preview=runtime.confirmations.get(waiting['data']['confirmationRef'],detail=True)['binding']['preview']
        assert preview['categoryId']=='cat_dev' and not runtime.store.checkpoint('provider:'+runtime.identities[1].agent_id,'publish:'+action)
        user_decision(runtime,waiting['data']['confirmationRef'])
        published=client.call('provider.publish',args,operation_id=action)
        assert published['status']=='completed',published
        actual=runtime.account.http.get(runtime.identities[1].protocol_id+'/publication').json()['declaration']
        assert actual['categoryId']=='cat_dev' and published['data']['currentPublication']['indexedCategoryId']=='cat_dev'
        assert client.call('provider.publish',args,operation_id=action)['data']['publicationReceipt']==published['data']['publicationReceipt']
    finally:runtime.close()


def test_unmatched_preview_has_no_publishable_digest_and_manual_selection_remains_available(platform,tmp_path):
    config,_=configuration(platform,tmp_path)
    runtime=ResidentRuntime(RuntimeConfig.load(config),modules=(ProviderRole(),)).start()
    try:
        wait_for(lambda:runtime.connection.connected);client=LocalClient(runtime.config.home)
        service={**PUBLIC,'profile':'service','templates':[draft(platform)]};operation=uid()
        first=client.call('provider.preview',{'service':service},operation_id=operation)
        job=first['data']['job']['reference'];wait_for(lambda:runtime.scheduler.get(job)['status']=='completed')
        result=client.call('provider.preview',{'service':service},operation_id=operation)['data']
        assert result['category']['status']=='choose_category' and result['readyToPublish'] is False
        assert 'contentDigest' not in result and 'service' not in result
        manual=client.call('provider.preview',{'service':{**service,'categoryId':'cat_dev'}})
        assert manual['status']=='completed' and manual['data']['service']['categoryId']=='cat_dev'
        assert client.call('provider.preview',{'service':{**service,'categoryId':'missing'}})['error']['code']=='category_required'
    finally:runtime.close()


@pytest.mark.parametrize('change',['deleted','renamed'])
def test_directory_changes_after_user_preview_never_sign_unreviewed_content(platform,tmp_path,monkeypatch,change):
    config,_=configuration(platform,tmp_path)
    runtime=ResidentRuntime(RuntimeConfig.load(config),modules=(ProviderRole(),)).start()
    try:
        wait_for(lambda:runtime.connection.connected);client=LocalClient(runtime.config.home)
        service={**PUBLIC,'profile':'service','categoryId':'cat_dev','templates':[draft(platform)]}
        checked=client.call('provider.preview',{'service':service})['data']
        args={'service':checked['service'],'contentDigest':checked['contentDigest'],'expectedRevision':checked['expectedRevision']}
        action=uid();pending=client.call('provider.publish',args,operation_id=action)
        assert pending['status']=='waiting'
        actual=runtime.confirmations.get(pending['data']['confirmationRef'],detail=True)['binding']['preview']
        assert actual['categoryPath']==checked['category']['categoryPath']
        user_decision(runtime,pending['data']['confirmationRef'])
        categories=copy.deepcopy(platform.discovery.categories())
        if change=='deleted':categories=[item for item in categories if item['id']!='cat_dev']
        else:
            target=next(item for item in categories if item['id']=='cat_dev')
            target['name']='更改后的分类';target['path'][-1]['name']='更改后的分类'
        monkeypatch.setattr(platform.category_preview,'categories',lambda:categories)
        rejected=client.call('provider.publish',args,operation_id=action)
        assert rejected['status']=='rejected' and rejected['error']['code']==('category_required' if change=='deleted' else 'id_conflict')
        assert not runtime.store.checkpoint('provider:'+runtime.identities[1].agent_id,'publish:'+action)
        assert runtime.account.api('GET','/services/'+runtime.identities[1].agent_id+'/status')['revision']==0
    finally:runtime.close()


def test_real_configured_platform_model_classifies_public_capability_via_http(platform,service,monkeypatch):
    import os
    from pathlib import Path
    from a2c.codec import digest_bytes
    if not os.environ.get('IOALAB_REAL_CATEGORY_TEST'):
        pytest.skip('requires explicitly configured platform discovery model')
    key=os.environ['IOALAB_REAL_CATEGORY_API_KEY']
    base=os.environ['IOALAB_REAL_CATEGORY_BASE_URL'].rstrip('/')
    model_name=os.environ['IOALAB_REAL_CATEGORY_MODEL']
    model=LLM(SimpleNamespace(llm={'api_key':key,'api_base':base},stub=False,a2x_model=model_name))
    decisions=[]
    def choose(public,categories):
        try:value=model.classify_category(public,categories)
        except httpx.HTTPStatusError as exc:
            decisions.append({'errorType':type(exc).__name__,'httpStatus':exc.response.status_code})
            raise
        decisions.append(value)
        return value
    monkeypatch.setattr(platform.llm,'classify_category',choose)
    account,_=service
    before=platform.db.one('SELECT COUNT(*) AS n FROM registry_publications')['n']
    result=account.api('POST','/services/category-preview',{**PUBLIC,'name':'码上飞'})
    assert result['status']=='matched' and result['categoryId']=='cat_dev',{'status':result['status'],'reason':result['reason'],'modelOutput':decisions}
    assert result['categoryPath'][-1]['name']=='软件开发'
    assert platform.db.one('SELECT COUNT(*) AS n FROM registry_publications')['n']==before
    if os.environ.get('IOALAB_REAL_CATEGORY_REPORT'):
        from ioalab.registry import categories
        from ioalab.runtime import llm
        from ioalab_sdk.provider import publication
        sources={label:digest_bytes(Path(module.__file__).read_bytes()) for label,module in (
            ('src/platform/ioalab/registry/categories.py',categories),
            ('src/platform/ioalab/market/taxonomy.py',taxonomy),
            ('src/platform/ioalab/runtime/llm.py',llm),
            ('src/access/sdk/ioalab_sdk/provider/publication.py',publication))}
        Path(os.environ['IOALAB_REAL_CATEGORY_REPORT']).write_text(json.dumps({
            'scope':'Actual platform HTTP category preview with configured model and actual A2X tree; not native Jiuwen onboarding or film',
            'model':model_name,'categoryId':result['categoryId'],'categoryPath':result['categoryPath'],
            'reason':result['reason'],'taxonomyDigest':result['taxonomyDigest'],'sources':sources,
            'publicationUnchanged':True},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
