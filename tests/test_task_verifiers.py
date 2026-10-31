"""Task-scoped verifier authoring: real packages, recovery and complete protocol lifecycle."""
import copy
import json
import os
import uuid

import httpx
import pytest
from a2c.verifier.package import load
from a2c.template import acceptance_of
from ioalab_sdk.provider import ProviderRole
from ioalab_sdk.runtime.host import HostResult
from ioalab_sdk.runtime.resident import ResidentRuntime
from ioalab_sdk.runtime.protocol import RuntimeFault
from ioalab_sdk.runtime.ipc import LocalClient
from conftest import wait_for
from test_provider_authoring import author
from test_resident_delegation import participants
from test_resident_contracts import candidate,confirm,status
from test_resident_fulfillment import ProductionHost,VERIFIER,invoke,operation,accept_preview
from test_assistant_role import user_decision


def body():return {'verifierId':'task_content_check@1.0','source':VERIFIER,'kind':'script'}


def contract_state(assistant,reference):
    value=status(assistant,reference)
    if value is None:return {}
    assert value['status']=='completed',value
    return value['data']


def test_same_task_content_reuses_one_real_upload_and_is_bound_to_its_service(author,monkeypatch):
    publisher=author.modules[0].verifiers;identity=author.identities[1];calls=[];api=author.account.api
    def counted(*args,**kwargs):
        if args[:2]==('POST','/provider/packages'):calls.append(copy.deepcopy(args[2]))
        return api(*args,**kwargs)
    monkeypatch.setattr(author.account,'api',counted)
    first=publisher.task(identity,'original-job',body())
    assert publisher.task(identity,'original-job',body())==first
    assert publisher.prepared(identity,'original-job')==[{'reference':first,'kind':'script'}]
    assert len(calls)==1
    load(author.account.fetch_package(first['uri']),first,'script')
    assert not publisher.prepared(identity,'different-job')
    assert not author.confirmations.list()


@pytest.mark.parametrize('change',['profile','interaction','source_size','kind','reads','missing_source'])
def test_invalid_task_source_has_no_upload_or_pending_side_effect(author,monkeypatch,change):
    publisher=author.modules[0].verifiers;identity=author.identities[1];value=body()
    if change=='profile':value['profile']='another-service'
    elif change=='interaction':value['interactionId']=str(uuid.uuid4())
    elif change=='source_size':value['source']='中'*22000
    elif change=='kind':value['kind']='service'
    elif change=='reads':value['reads']={'not':'an array'}
    else:value.pop('source')
    monkeypatch.setattr(author.account,'api',lambda *_:pytest.fail('Invalid task reached platform'))
    with pytest.raises(RuntimeFault) as error:publisher.task(identity,'invalid-job',value)
    assert error.value.code=='invalid_input' and not publisher.prepared(identity,'invalid-job')


def test_lost_upload_reply_survives_restart_and_recovers_the_same_body(author,platform,monkeypatch):
    identity=author.identities[1];publisher=author.modules[0].verifiers;api=author.account.api;actual=[]
    before=set(platform.packages.root.glob('*.zip'));value={**body(),'verifierId':'recover_'+uuid.uuid4().hex+'@1.0'}
    def lose(*args,**kwargs):
        result=api(*args,**kwargs)
        if args[:2]==('POST','/provider/packages'):
            actual.append(result);raise httpx.ReadTimeout('lost after durable package',request=httpx.Request('POST','http://local.test'))
        return result
    monkeypatch.setattr(author.account,'api',lose)
    with pytest.raises(RuntimeFault) as error:publisher.task(identity,'recoverable-job',value)
    assert error.value.code=='unknown_result' and len(actual)==1
    with pytest.raises(RuntimeFault) as error:publisher.task(identity,'recoverable-job',{**value,'source':VERIFIER+'\n# different'})
    assert error.value.code=='unknown_result' and len(actual)==1
    config=author.config;author.close();role=ProviderRole();fresh=ResidentRuntime(config,modules=(role,)).start()
    try:
        wait_for(lambda:fresh.connection.connected)
        prepared=role.verifiers.prepared(fresh.identities[1],'recoverable-job')
        assert prepared==[{'reference':actual[0],'kind':'script'}]
        assert role.verifiers.task(fresh.identities[1],'recoverable-job',value)==actual[0]
        assert len(set(platform.packages.root.glob('*.zip'))-before)==1
    finally:fresh.close()


@pytest.mark.parametrize('failure',['identity','digest','kind','invalid_error'])
def test_bad_platform_package_is_not_saved_as_a_usable_reference(author,monkeypatch,failure):
    identity=author.identities[1];publisher=author.modules[0].verifiers;api=author.account.api
    def altered(*args,**kwargs):
        if failure=='invalid_error':
            response=httpx.Response(400,json={'error':'not an envelope'},request=httpx.Request('POST','http://local.test'));response.raise_for_status()
        value=api(*args,**kwargs)
        if args[:2]==('POST','/provider/packages'):
            if failure=='identity':value={**value,'id':'foreign@1.0'}
            if failure=='digest':value={**value,'digest':'sha256:'+'0'*64,'uri':'ioalab://pkg/sha256:'+'0'*64}
        return value
    monkeypatch.setattr(author.account,'api',altered)
    if failure=='kind':
        from a2c.verifier.package import build
        blob=build({'verify.py':VERIFIER},{'id':body()['verifierId'],'kind':'skill','runtime':'cpython-3.13-wasi','entry':'verify:verify','reads':[]})
        from a2c.codec import digest_bytes
        original=author.account.api
        def wrong_kind(*args,**kwargs):
            value=original(*args,**kwargs)
            if args[:2]==('POST','/provider/packages'):
                value={**value,'digest':digest_bytes(blob),'uri':'ioalab://pkg/'+digest_bytes(blob)}
            return value
        monkeypatch.setattr(author.account,'api',wrong_kind)
        monkeypatch.setattr(author.account,'fetch_package',lambda *_:blob)
    with pytest.raises(RuntimeFault) as error:publisher.task(identity,'bad-response-job',body())
    assert error.value.code in ('protocol_error','not_ready')
    assert not publisher.prepared(identity,'bad-response-job')


class DynamicVerifierHost(ProductionHost):
    def __init__(self):
        super().__init__();self.created=[];self.denied=[]
        self.package={**body(),'verifierId':'generated_'+uuid.uuid4().hex+'@1.0'}
    def execute(self,turn):
        data=json.loads(turn.input)
        if data['phase']=='quote':
            assert self.verifier is None and not data['preparedVerifiers']
            self.turns.append(turn);turn.on_session(turn.session_id or 'dynamic-'+str(uuid.uuid4()))
            first=turn.on_tool('provider.task.verifier.publish',self.package,'first-host-call')
            assert first['status']=='completed',first
            assert turn.on_tool('provider.task.verifier.publish',self.package,'different-host-call')==first
            self.created.append(first['data'])
            args={'acceptance':{'result':{'verifiers':{'script':first['data']},'notCovered':[]}},
                'acceptWithin':{'result':'PT10M'},'lineItems':{'result':9}}
            assert turn.on_tool('provider.task.quote',args,'quote')['status']=='recorded'
            late=turn.on_tool('provider.task.verifier.publish',{**body(),'verifierId':'late@1.0'},'late')
            assert late['status']=='rejected' and late['error']['code']=='forbidden';self.denied.append('after-decision')
            return HostResult('completed',turn.session_id,{})
        denied=turn.on_tool('provider.task.verifier.publish',body(),'wrong-phase')
        assert denied['status']=='rejected' and denied['error']['code']=='forbidden',denied
        self.denied.append(data['phase'])
        if data['phase']=='production':
            self.turns.append(turn);turn.on_session(turn.session_id)
            files={'result.json':{'text':'{"delivered":"actual file bytes"}','mediaType':'application/json'}}
            assert turn.on_tool('provider.task.selftest',{'files':files},'selftest')['status']=='pass'
            assert turn.on_tool('provider.task.deliver',{'files':files},'deliver')['status']=='delivered'
            return HostResult('completed',turn.session_id,{})
        return super().execute(turn)


def test_no_preset_reference_forms_and_completes_a_real_contract(platform,tmp_path):
    before=set(platform.packages.root.glob('*.zip'))
    host=DynamicVerifierHost();business={'resources':{},'instructions':'按当前请求提供真实内容验收和确定报价，不使用预置验收引用。'}
    with participants(platform,tmp_path,agent_host=host,business=business,preload_verifier=False) as (provider,assistant,host,template,task):
        assert set(platform.packages.root.glob('*.zip'))==before
        quote=candidate(assistant,provider,template,task);selected,_,_=confirm(assistant,quote);reference=selected['data']['contractRef']
        wait_for(lambda:contract_state(assistant,reference).get('formed'))
        key,_=invoke(assistant,reference,'start');wait_for(lambda:operation(assistant,reference,key))
        wait_for(lambda:contract_state(assistant,reference).get('availablePeriods')==1)
        key,_=invoke(assistant,reference,'fetch',period=1);wait_for(lambda:operation(assistant,reference,key))
        key,pending=accept_preview(assistant,reference)
        assert assistant.confirmations.get(pending['data']['confirmationRef'],detail=True)['binding']['preview']['report']['status']=='pass'
        user_decision(assistant,pending['data']['confirmationRef'])
        LocalClient(assistant.config.home).call('assistant.delegation.accept',{'contractRef':reference,'period':1},operation_id=key)
        wait_for(lambda:operation(assistant,reference,key));assert operation(assistant,reference,key)['status']=='completed'
        wait_for(lambda:contract_state(assistant,reference).get('closed'))
        actual=status(assistant,reference)['data']
        assert actual['payment']=='paid' and len(host.created)==1
        assert len(set(platform.packages.root.glob('*.zip'))-before)==1
        assert acceptance_of(template,actual['card'])['result']['verifiers']['script']==host.created[0]
        assert platform.market.reputation(provider.identities[0].protocol_id)['completed']==1
        assert 'after-decision' in host.denied and 'production' in host.denied


@pytest.mark.skipif(not os.environ.get('IOALAB_REAL_JIUWEN_COMMAND'),reason='Opt-in original Jiuwen model invocation')
def test_original_jiuwen_creates_a_task_verifier_without_preset_references(platform,tmp_path):
    from ioalab_jiuwen.process import ProcessHost
    from dataclasses import replace
    host=ProcessHost(json.loads(os.environ['IOALAB_REAL_JIUWEN_COMMAND']),os.environ['IOALAB_REAL_JIUWEN_DATA_DIR'],model=os.environ.get('IOALAB_REAL_JIUWEN_MODEL'))
    assert host.probe()['available'],host.capabilities()
    turns=[];events=[];original=host.run
    def event(value):
        if value.get('event_type') in ('chat.tool_call','chat.tool_result'):
            events.append(value)
    def execute(turn):
        turns.append(turn);return original(replace(turn,on_event=event))
    host.run=execute
    business={'resources':{},'instructions':
        '本服务提供结构化摘要，交付文件result.json，必须有delivered字段，值严格等于actual file bytes。'
        '交付报价固定9虚拟币，验收时长10分钟。未预置任何验收引用，按这项实际JSON内容要求准备本单L1 script验收器；不得只查文件存在。'
        '这是完整业务需求，不需要补充材料或外部操作。生产可直接用files中text提供真实JSON字节，先自测通过再交付。'}
    with participants(platform,tmp_path,agent_host=host,business=business,preload_verifier=False) as (provider,assistant,host,template,task):
        quote=candidate(assistant,provider,template,task,timeout=180)
        selected,_,_=confirm(assistant,quote);reference=selected['data']['contractRef']
        wait_for(lambda:contract_state(assistant,reference).get('formed'),timeout=180)
        key,_=invoke(assistant,reference,'start');wait_for(lambda:operation(assistant,reference,key),timeout=180)
        wait_for(lambda:contract_state(assistant,reference).get('availablePeriods')==1,timeout=180)
        key,_=invoke(assistant,reference,'fetch',period=1);wait_for(lambda:operation(assistant,reference,key))
        key,pending=accept_preview(assistant,reference)
        preview=assistant.confirmations.get(pending['data']['confirmationRef'],detail=True)['binding']['preview']['report']
        assert preview['status']=='pass',preview
        user_decision(assistant,pending['data']['confirmationRef'])
        LocalClient(assistant.config.home).call('assistant.delegation.accept',{'contractRef':reference,'period':1},operation_id=key)
        wait_for(lambda:operation(assistant,reference,key));assert operation(assistant,reference,key)['status']=='completed'
        wait_for(lambda:contract_state(assistant,reference).get('closed'))
        actual=status(assistant,reference)['data'];assert actual['payment']=='paid'
        quote_turns=[turn for turn in turns if json.loads(turn.input)['phase']=='quote']
        assert quote_turns and not json.loads(quote_turns[0].input)['preparedVerifiers']
        prepared=provider.modules[0].verifiers.prepared(provider.identities[0],quote_turns[0].operation_id)
        assert prepared and acceptance_of(template,actual['card'])['result']['verifiers']['script'] in [item['reference'] for item in prepared]
        assert len({turn.session_id for turn in turns if turn.session_id})==1
        assert platform.market.reputation(provider.identities[0].protocol_id)['completed']==1
        report={'scope':'Original Jiuwen public Process host generates task verifier without preset references and completes HTTP/WS/A2C/WASM/AP2; isolated trusted user confirmations',
            'model':host.capabilities()['model'],'interactionId':actual['interactionId'],'contractRef':reference,
            'preparedVerifiers':prepared,'wasm':preview,'payment':'paid','closed':True,'reputationCompleted':1,
            'limits':['Backend Process verification; not ordinary native frontend onboarding or new film','No external mail or Linux deployment proof']}
        if os.environ.get('IOALAB_REAL_TASK_VERIFIER_REPORT'):
            from pathlib import Path
            from ioalab_sdk.runtime.files import private_json
            private_json(Path(os.environ['IOALAB_REAL_TASK_VERIFIER_REPORT']),report)


def test_recovered_package_time_cannot_extend_the_quote_deadline(platform,tmp_path,monkeypatch):
    import time
    from types import SimpleNamespace
    import ioalab_sdk.provider.policy as policy
    from test_resident_delegation import request,terminal
    now=[time.time()];host=DynamicVerifierHost()
    with participants(platform,tmp_path,agent_host=host,business={'resources':{}},preload_verifier=False) as (provider,assistant,host,template,task):
        monkeypatch.setattr(policy,'time',SimpleNamespace(time=lambda:now[0]))
        def elapsed(*args):now[0]+=1000;return []
        monkeypatch.setattr(provider.modules[0].verifiers,'prepared',elapsed)
        started,_=request(assistant,provider,template,task);reference=started['data']['quoteRef']
        wait_for(lambda:terminal(assistant,reference))
        assert terminal(assistant,reference)['status']!='completed' and not host.turns


def test_unknown_upload_blocks_further_decisions_and_never_restarts_the_host(platform,tmp_path,monkeypatch):
    from test_resident_delegation import request,terminal
    host=DynamicVerifierHost();turns=[];attempts=[];uploaded=[]
    with participants(platform,tmp_path,agent_host=host,business={'resources':{}},preload_verifier=False) as (provider,assistant,host,template,task):
        api=provider.account.api
        def lose(*args,**kwargs):
            value=api(*args,**kwargs)
            if args[:2]==('POST','/provider/packages'):
                uploaded.append(value)
                raise httpx.ReadTimeout('reply lost after actual upload',request=httpx.Request('POST','http://local.test'))
            return value
        def execute(turn):
            turns.append(turn);turn.on_session('unknown-upload-session')
            first=turn.on_tool('provider.task.verifier.publish',host.package,'upload')
            assert first['status']=='unknown',first
            attempts.append(turn.on_tool('provider.task.quote',{'reason':'cannot verify the original upload'},'must-not-decide'))
            attempts.append(turn.on_tool('provider.task.verifier.publish',{**host.package,'source':VERIFIER+'\n# changed'},'must-not-replace'))
            return HostResult('completed','unknown-upload-session',{})
        monkeypatch.setattr(provider.account,'api',lose);monkeypatch.setattr(host,'run',execute)
        started,_=request(assistant,provider,template,task);reference=started['data']['quoteRef']
        wait_for(lambda:terminal(assistant,reference))
        assert terminal(assistant,reference)['status']=='unknown' and len(uploaded)==1
        assert len(attempts)==2 and all(value['status']=='unknown' for value in attempts)
        for _ in range(3):provider.modules[0].execution.scan()
        assert len(turns)==1
        for endpoint in provider.modules[0].execution.contexts.values():
            assert not endpoint.store.all('business')
        monkeypatch.setattr(provider.account,'api',api)
        recovered=provider.modules[0].verifiers.prepared(provider.identities[0],turns[0].operation_id)
        assert recovered==[{'reference':uploaded[0],'kind':'script'}]
        # A recovered package is not evidence that an unknown model job resumed.
        assert terminal(assistant,reference)['status']=='unknown' and len(turns)==1
