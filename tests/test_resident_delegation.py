"""Real resident/HTTP/WS/A2C/AP2; explicit host and user test doubles."""
from contextlib import contextmanager
import copy
import json
import os
from pathlib import Path
import threading
import uuid
import sqlite3

import pytest
from a2c.card import card_digest
from a2c.slots import verify_card
from a2c.common import Directory
from ioalab_sdk.assistant.role import AssistantRole
from ioalab_sdk.provider import ProviderRole
from ioalab_sdk.runtime.config import RuntimeConfig
from ioalab_sdk.runtime.resident import ResidentRuntime
from ioalab_sdk.runtime.ipc import LocalClient
from ioalab_sdk.runtime.files import private_json,read_json
from ioalab_sdk.runtime.host import HostResult
from ioalab_sdk.runtime.protocol import RuntimeFault
from ioalab_sdk.adapters.endpoint import EndpointSession
from ioalab_sdk.templates import template_from_definition
from conftest import wait_for
from test_resident_runtime import configuration
from test_assistant_role import user_decision
from test_agent_delivery import Peer


def uid():return str(uuid.uuid4())


class QuoteHost:
    def __init__(self):self.turns=[];self.verifier=None;self.wait=None
    def capabilities(self):return {'available':True,'testDouble':True}
    def cancel(self,op):
        if self.wait:self.wait.set()
        return True
    def run(self,turn):
        self.turns.append(turn);turn.on_session(turn.session_id or 'isolated-provider-'+uid())
        if self.wait:assert self.wait.wait(15),'test host was not released'
        if json.loads(turn.input)['phase']=='quote':
            args={'acceptance':{'result':{'verifiers':{'script':self.verifier},'notCovered':[]}},'acceptWithin':{'result':'PT10M'},'lineItems':{'result':9}}
            turn.on_tool('provider.task.quote',args,'isolated-call')
        else:turn.on_tool('provider.task.decision',{'approved':True},'isolated-call')
        return HostResult('completed',turn.session_id,{'testDouble':True})


@contextmanager
def participants(platform,tmp_path,*,formation='proposal',fee=1,capacity=2,models=2,agent_host=None,verifier_source=None):
    pconfig,_=configuration(platform,tmp_path/'provider')
    value=read_json(pconfig);value['identities']=[value['identities'][1]];value['max_model_runs']=models
    value['provider']={'services':{'service':{'instructions':'按照请求提供可执行验收器与确定报价。','skills':[],'max_active_delegations':capacity,'accepting':True}}}
    private_json(pconfig,value);host=agent_host or QuoteHost();prole=ProviderRole(host)
    provider=ResidentRuntime(RuntimeConfig.load(pconfig),modules=(prole,),host_status=host.capabilities).start()
    aconfig,_=configuration(platform,tmp_path/'assistant',service=False);arole=AssistantRole()
    assistant=ResidentRuntime(RuntimeConfig.load(aconfig),modules=(arole,)).start()
    try:
        wait_for(lambda:provider.connection.connected and assistant.connection.connected)
        source=verifier_source or "def verify(ctx):\n    return {'status': 'pass'}\n"
        uploaded=LocalClient(provider.config.home).call('provider.verifier.publish',{'profile':'service','verifierId':'resident_result@1.0','source':source})
        assert uploaded['status']=='completed',uploaded
        host.verifier=uploaded['data']
        resources={'outputVerifier':host.verifier,'quoteAmount':9,'acceptWithin':'PT10M'}
        instructions='本隔离测试的专业能力是提供摘要交付。当前请求可直接报价，无须补充需求。请使用业务资源outputVerifier作script验收器，quoteAmount作确定报价，acceptWithin作验收时长。'
        prole.execution.configs['service'].update(resources=resources,instructions=instructions)
        value['provider']['services']['service'].update(resources=resources,instructions=instructions);private_json(pconfig,value)
        definition={'formation':formation,'deliverable':{'name':'result'},'pricing':{'proposalFee':fee,'price':'9'},
                    'verifier':{'id':'resident_result@1.0','source':source}}
        from types import SimpleNamespace
        def upload(**args):return host.verifier
        template=template_from_definition(definition,packages=SimpleNamespace(put=upload),payment_provider=platform.payment_provider)
        service={'profile':'service','name':'用户配置的专业服务','description':'业务决定来自本机原版宿主的已配置技能','tags':['开发'],
                 'categoryId':platform.discovery.categories()[0]['id'],'templates':[template]}
        prpc=LocalClient(provider.config.home);checked=prpc.call('provider.preview',{'service':service})
        assert checked['status']=='completed',checked
        args={'service':service,'contentDigest':checked['data']['contentDigest'],'expectedRevision':checked['data']['expectedRevision']};operation=uid()
        waiting=prpc.call('provider.publish',args,operation_id=operation);assert waiting['status']=='waiting',waiting
        user_decision(provider,waiting['data']['confirmationRef'])
        published=prpc.call('provider.publish',args,operation_id=operation);assert published['status']=='completed',published
        template=published['data']['publicationReceipt']['templates'][0]
        # Apply the saved professional configuration by the normal runtime restart.
        provider.close();prole=ProviderRole(host)
        provider=ResidentRuntime(RuntimeConfig.load(pconfig),modules=(prole,),host_status=host.capabilities).start()
        wait_for(lambda:provider.connection.connected)
        try:wait_for(lambda:prole.ready('service'))
        except AssertionError:
            # Surface the real refresh boundary instead of lengthening the wait.
            prole.execution.templates(provider.identities[0])
            prole.execution.configure(provider.identities[0])
            raise AssertionError(prole.execution.errors)
        arpc=LocalClient(assistant.config.home)
        task=arpc.call('assistant.delegation.prepare',{'purpose':'请求真实签名提案','budget':500,'proposalFeeLimit':3})['data']['taskRef']
        operation=uid();pending=arpc.call('assistant.delegation.authorize',{'taskRef':task},operation_id=operation)
        user_decision(assistant,pending['data']['confirmationRef'])
        assert arpc.call('assistant.delegation.authorize',{'taskRef':task},operation_id=operation)['status']=='completed'
        yield provider,assistant,host,template,task
    finally:
        if getattr(host,'wait',None):host.wait.set()
        assistant.close();provider.close()


def request(assistant,provider,template,task,operation=None):
    args={'taskRef':task,'service':provider.identities[0].agent_id,'templateDigest':template['templateDigest'],'spec':{'result':{}}}
    return LocalClient(assistant.config.home).call('assistant.delegation.request',args,operation_id=operation or uid()),args


def terminal(assistant,reference):
    result=LocalClient(assistant.config.home).call('assistant.delegation.status',{'quoteRef':reference})
    return result if result['status'] in ('completed','failed','rejected','unknown') else None


def test_original_client_and_provider_quote_over_resident_ws_with_actual_ap2_and_slot_three(platform,tmp_path):
    with participants(platform,tmp_path) as (provider,assistant,host,template,task):
        operation=uid();started,args=request(assistant,provider,template,task,operation)
        assert started['status']=='accepted',started
        wait_for(lambda:terminal(assistant,started['data']['quoteRef']))
        result=terminal(assistant,started['data']['quoteRef'])
        assert result['status']=='completed',result
        card=result['data']['card'];cd=card_digest(card)
        assert card['signatures']['1'] is None and card['signatures']['2'] and card['signatures']['3']
        keys=Directory();keys.add(provider.identities[0].protocol_id,{'keys':[provider.identities[0].signer.jwk]});keys.add(platform.market_id,{'keys':[platform.market_signer.jwk]})
        assert verify_card(template,card,keys).ts[3]
        assert len(host.turns)==1 and host.turns[0].interaction_id==result['data']['interactionId']
        assert provider.store.interaction(interaction_id=result['data']['interactionId'])['session']
        assert len(platform.db.rows("SELECT * FROM transfers WHERE purpose='proposal-fee' AND payee_id=?",provider.account.account['id']))==1
        assert platform.market.market.store.get('snapshots',cd)['card']['signatures']['2']==card['signatures']['2']
        before=len(host.turns)
        repeated=LocalClient(assistant.config.home).call('assistant.delegation.request',args,operation_id=operation)
        assert repeated['status']=='completed' and repeated['data']['card']==card and len(host.turns)==before
        assert len(platform.db.rows('SELECT * FROM delegation_offers WHERE admission=?',result['data']['admissionId']))==1


def test_countersign_candidate_is_only_an_unsigned_local_quote_before_selection(platform,tmp_path):
    with participants(platform,tmp_path,formation='countersign',fee=0) as (provider,assistant,host,template,task):
        result,_=request(assistant,provider,template,task)
        assert result['status']=='completed',result
        assert result['data']['formation']=='countersign' and all(not sig for sig in result['data']['card']['signatures'].values())
        assert not platform.db.rows('SELECT * FROM delegation_admissions WHERE service=?',provider.identities[0].agent_id) and not host.turns
        assert not platform.db.rows('SELECT * FROM transfers WHERE payee_id=?',provider.account.account['id'])


def test_zero_fee_proposal_still_commits_capacity_and_gets_original_market_endorsement(platform,tmp_path):
    with participants(platform,tmp_path,fee=0) as (provider,assistant,host,template,task):
        started,_=request(assistant,provider,template,task);ref=started['data']['quoteRef']
        wait_for(lambda:terminal(assistant,ref));result=terminal(assistant,ref)
        assert result['status']=='completed',result
        row=platform.db.one('SELECT state FROM delegation_admissions WHERE id=?',result['data']['admissionId'])
        assert row['state']=='committed' and result['data']['card']['signatures']['3']
        assert not platform.db.rows('SELECT * FROM transfers WHERE payee_id=?',provider.account.account['id'])


def test_quote_can_correct_wrong_package_kind_in_the_same_host_turn_before_signing(platform,tmp_path):
    class CorrectingHost(QuoteHost):
        def run(self,turn):
            self.turns.append(turn);turn.on_session('isolated-correcting-session')
            args={'acceptance':{'result':{'verifiers':{'skill':{**self.verifier,'disputeModels':['test-model']}},'notCovered':[]}},
                  'acceptWithin':{'result':'PT10M'},'lineItems':{'result':9}}
            invalid=turn.on_tool('provider.task.quote',args,'invalid-kind')
            assert invalid['status']=='rejected' and 'kind' in invalid['error']['message']
            args['acceptance']['result']['verifiers']={'script':self.verifier}
            assert turn.on_tool('provider.task.quote',args,'corrected-kind')['status']=='recorded'
            return HostResult('completed','isolated-correcting-session',{'testDouble':True})
    with participants(platform,tmp_path,agent_host=CorrectingHost()) as (provider,assistant,host,template,task):
        started,_=request(assistant,provider,template,task);ref=started['data']['quoteRef']
        wait_for(lambda:terminal(assistant,ref));result=terminal(assistant,ref)
        assert result['status']=='completed',result
        assert len(host.turns)==1 and result['data']['card']['acceptance']['result']['verifiers']=={'script':host.verifier}
        assert len(platform.db.rows('SELECT * FROM transfers WHERE payee_id=?',provider.account.account['id']))==1


def test_original_reply_and_task_same_name_files_remain_distinct_over_private_channel(platform,tmp_path,monkeypatch):
    from ioalab_sdk.adapters.transport import SessionTransport
    original=EndpointSession.pack
    def separate_files(endpoint,reply):
        if reply.state=='completed' and reply.body.get('op')=='request.submit':
            reply.files['evidence.bin']=b'original reply bytes'
            endpoint.provider.tasks[reply.task_id].files['evidence.bin']=b'original task bytes'
        return original(endpoint,reply)
    monkeypatch.setattr(EndpointSession,'pack',separate_files)
    with participants(platform,tmp_path) as (provider,assistant,host,template,task):
        started,_=request(assistant,provider,template,task);ref=started['data']['quoteRef']
        wait_for(lambda:terminal(assistant,ref));result=terminal(assistant,ref)
        assert result['status']=='completed',result
        transport=SessionTransport(assistant,assistant.identities[0],result['data']['interactionId'],result['data']['admissionId'],provider.identities[0].protocol_id)
        try:
            original_result=transport.received('request.submit')['result']
            assert original_result['files']['evidence.bin']['digest']!=original_result['taskFiles']['evidence.bin']['digest']
            assert transport._reply(original_result).files=={'evidence.bin':b'original reply bytes'}
            assert transport.get_task(provider.identities[0].protocol_id,original_result['reply']['taskId']).files=={'evidence.bin':b'original task bytes'}
        finally:transport.close()


def test_concurrent_quotes_share_capacity_but_model_queue_does_not_block_checkout_or_progress(platform,tmp_path):
    with participants(platform,tmp_path,capacity=2,models=1) as (provider,assistant,host,template,task):
        host.wait=threading.Event()
        first,_=request(assistant,provider,template,task)
        wait_for(lambda:len(host.turns)==1)
        second,_=request(assistant,provider,template,task)
        wait_for(lambda:len(platform.db.rows('SELECT * FROM transfers WHERE payee_id=?',provider.account.account['id']))==2)
        control=provider.modules[0].execution
        wait_for(lambda:sum(len(ep.provider.tasks) for ep in control.contexts.values())>=2)
        assert len(host.turns)==1
        third,_=request(assistant,provider,template,task);ref3=third['data']['quoteRef']
        wait_for(lambda:terminal(assistant,ref3));denied=terminal(assistant,ref3)
        assert denied['status']=='rejected' and denied['data']['error']['code']=='capacity_full',denied
        assert len(platform.db.rows('SELECT * FROM transfers WHERE payee_id=?',provider.account.account['id']))==2
        host.wait.set()
        refs=[first['data']['quoteRef'],second['data']['quoteRef']]
        for ref in refs:
            wait_for(lambda:terminal(assistant,ref));assert terminal(assistant,ref)['status']=='completed',terminal(assistant,ref)
        assert len(host.turns)==2 and len({turn.interaction_id for turn in host.turns})==2


def test_lost_market_reply_recovers_original_signatures_without_repeating_model_or_payment(platform,tmp_path,monkeypatch):
    with participants(platform,tmp_path) as (provider,assistant,host,template,task):
        original=provider.connection.call;dropped=[]
        def lost(actor,kind,payload,**kwargs):
            result=original(actor,kind,payload,**kwargs)
            if kind=='a2c.market' and not dropped:
                dropped.append(copy.deepcopy(result))
                raise RuntimeFault('unknown_result','test dropped an already committed market response')
            return result
        with monkeypatch.context() as patch:
            patch.setattr(provider.connection,'call',lost)
            patch.setattr(EndpointSession,'recover',lambda *_:None)
            started,args=request(assistant,provider,template,task);ref=started['data']['quoteRef']
            wait_for(lambda:terminal(assistant,ref));unknown=terminal(assistant,ref)
            assert unknown['status']=='unknown',unknown
            assert dropped and len(host.turns)==1
        wait_for(lambda:terminal(assistant,ref) and terminal(assistant,ref)['status']=='completed')
        recovered=terminal(assistant,ref)
        assert recovered['data']['card']==dropped[0]['card'] and len(host.turns)==1
        assert len(platform.db.rows('SELECT * FROM transfers WHERE payee_id=?',provider.account.account['id']))==1
        iid=recovered['data']['interactionId']
        wait_for(lambda:provider.scheduler.status()['states'].get('unknown',0)==0)
        assert provider.store.interaction(interaction_id=iid)['session']
        original_session=provider.store.interaction(interaction_id=iid)['session']
        psettings,asettings=provider.config,assistant.config
        provider.close();assistant.close()
        fresh_host=QuoteHost();fresh_provider=ResidentRuntime(psettings,modules=(ProviderRole(fresh_host),),host_status=fresh_host.capabilities).start()
        fresh_assistant=ResidentRuntime(asettings,modules=(AssistantRole(),)).start()
        try:
            wait_for(lambda:fresh_provider.connection.connected and fresh_assistant.connection.connected)
            assert terminal(fresh_assistant,ref)['data']['card']==dropped[0]['card']
            assert not fresh_host.turns
            assert fresh_provider.store.interaction(interaction_id=iid)['session']==original_session
        finally:fresh_assistant.close();fresh_provider.close()


def test_saved_original_result_recovers_without_host_or_current_service_config(platform,tmp_path,monkeypatch):
    with participants(platform,tmp_path) as (provider,assistant,host,template,task):
        original=provider.connection.call;lost=[]
        def drop(actor,kind,payload,**kwargs):
            result=original(actor,kind,payload,**kwargs)
            if kind=='a2c.market' and not lost:
                lost.append(copy.deepcopy(result))
                raise RuntimeFault('unknown_result','test lost the already committed market result')
            return result
        with monkeypatch.context() as patch:
            patch.setattr(provider.connection,'call',drop)
            patch.setattr(EndpointSession,'recover',lambda *_:None)
            started,_=request(assistant,provider,template,task);ref=started['data']['quoteRef']
            wait_for(lambda:terminal(assistant,ref));assert terminal(assistant,ref)['status']=='unknown'
            assert len(host.turns)==1 and lost
            actor=provider.identities[0].agent_id
            session=host.turns[0].session_id or provider.store.interaction(interaction_id=host.turns[0].interaction_id)['session']
            provider.close()
        # Keep the identity and frozen contract context; remove only the config
        # used for accepting NEW work, and do not install a model host.
        path=tmp_path/'provider'/'config.json'
        values=read_json(path);values['provider']={'services':{}};private_json(path,values)
        role=ProviderRole();recovered=ResidentRuntime(RuntimeConfig.load(path),modules=(role,)).start()
        try:
            wait_for(lambda:recovered.connection.connected)
            wait_for(lambda:terminal(assistant,ref) and terminal(assistant,ref)['status']=='completed')
            result=terminal(assistant,ref)
            assert result['data']['card']==lost[0]['card']
            assert recovered.store.interaction(interaction_id=result['data']['interactionId'])['session']==session
            assert recovered.scheduler.status()['states'].get('unknown',0)==0 and len(host.turns)==1
            assert 'provider.control' in recovered.scheduler.handlers and not role.ready('service')
            assert len(platform.db.rows('SELECT * FROM transfers WHERE payee_id=?',recovered.account.account['id']))==1
            # A fresh intent can be received but cannot start/consume capacity.
            request(assistant,recovered,template,task)
            wait_for(lambda:sum(frame['message']['kind']=='delegation.intent' for frame in recovered.store.messages(actor))==2)
            role.execution.scan()
            with recovered.store.lock:
                assert recovered.store.db.execute("SELECT COUNT(*) FROM runtime_jobs WHERE handler='provider.a2c'").fetchone()[0]==1
            assert len(platform.db.rows('SELECT * FROM delegation_admissions WHERE service=?',actor))==1
        finally:recovered.close()


def test_unknown_host_is_reported_without_faking_failure_or_reexecuting_it(platform,tmp_path,monkeypatch):
    with participants(platform,tmp_path) as (provider,assistant,host,template,task):
        calls=[]
        def unknown(turn):
            calls.append(turn.operation_id);turn.on_session('isolated-unknown-session')
            raise RuntimeFault('unknown_result','test host lost its actual result')
        monkeypatch.setattr(host,'run',unknown)
        started,_=request(assistant,provider,template,task);ref=started['data']['quoteRef']
        wait_for(lambda:terminal(assistant,ref));result=terminal(assistant,ref)
        assert result['status']=='unknown' and len(calls)==1,result
        row=platform.db.one('SELECT * FROM delegation_admissions WHERE service=?',provider.identities[0].agent_id)
        assert row['state']=='committed'
        assert not platform.db.rows('SELECT * FROM delegation_offers WHERE admission=?',row['id'])
        original_tasks=[value for ep in provider.modules[0].execution.contexts.values() for value in ep.provider.tasks.values()]
        assert any(value.state=='working' for value in original_tasks)
        for _ in range(3):provider.modules[0].execution.scan()
        assert len(calls)==1


def test_market_snapshot_offer_and_operation_rollback_together_before_original_call_finishes(platform,tmp_path,monkeypatch):
    with participants(platform,tmp_path) as (provider,assistant,host,template,task):
        actor=provider.identities[0].agent_id;ready=threading.Event();release=threading.Event();captured=[]
        original=platform.market_operations.prepare
        def prepare(owner,who,operation,payload):
            value=original(owner,who,operation,payload)
            if who==actor and not captured:
                captured.append((owner,operation,copy.deepcopy(payload),value));ready.set()
                assert release.wait(10)
            return value
        monkeypatch.setattr(platform.market_operations,'prepare',prepare)
        try:
            started,_=request(assistant,provider,template,task);wait_for(ready.is_set)
            owner,operation,payload,prepared=captured[0];cd=card_digest(payload['body']['card'])
            platform.db.run("CREATE TRIGGER test_market_atomic BEFORE INSERT ON delegation_market_operations WHEN NEW.actor='"+actor+"' BEGIN SELECT RAISE(ABORT,'test endorsement receipt interrupted'); END")
            try:
                lease=platform.db.one('SELECT * FROM delivery_leases WHERE actor=?',actor)
                frame={'type':'a2c.market','agentId':actor,'generation':lease['generation'],'operationId':uid(),'payload':payload}
                with pytest.raises(sqlite3.IntegrityError):platform.messaging.delivery.control(owner,lease['connection'],{actor:lease['generation']},frame)
                assert platform.market.market.store.get('snapshots',cd) is None
                assert not platform.db.rows('SELECT * FROM delegation_offers WHERE admission=?',payload['admissionId'])
                assert not platform.db.rows('SELECT * FROM delegation_market_operations WHERE actor=?',actor)
            finally:platform.db.run('DROP TRIGGER test_market_atomic')
            release.set();ref=started['data']['quoteRef'];wait_for(lambda:terminal(assistant,ref))
            assert terminal(assistant,ref)['status']=='completed',terminal(assistant,ref)
            assert len(platform.db.rows('SELECT * FROM delegation_offers WHERE admission=?',payload['admissionId']))==1
        finally:release.set()


def test_real_socket_takeover_after_market_preparation_cannot_publish_old_generation_signature(platform,client,tmp_path,monkeypatch):
    with participants(platform,tmp_path) as (provider,assistant,host,template,task):
        actor=provider.identities[0].agent_id;ready=threading.Event();release=threading.Event();captured=[]
        original=platform.market_operations.prepare;replacement=None
        def prepare(owner,who,operation,payload):
            value=original(owner,who,operation,payload)
            if who==actor and not captured:
                captured.append(copy.deepcopy(payload));ready.set();assert release.wait(10)
            return value
        monkeypatch.setattr(platform.market_operations,'prepare',prepare)
        try:
            request(assistant,provider,template,task);wait_for(ready.is_set)
            replacement=Peer(client,{'actor':actor,'token':provider.account.token},runtime=provider.store.runtime_id,cursor=provider.store.cursors()[actor])
            release.set();wait_for(lambda:provider.connection.error is not None)
            assert provider.connection.error.code=='stale_generation'
            cd=card_digest(captured[0]['body']['card'])
            assert platform.market.market.store.get('snapshots',cd) is None
            assert not platform.db.rows('SELECT * FROM delegation_offers WHERE admission=?',captured[0]['admissionId'])
            assert platform.db.one('SELECT state FROM delegation_admissions WHERE id=?',captured[0]['admissionId'])['state']=='committed'
        finally:
            release.set()
            if replacement:replacement.close()


@pytest.mark.skipif(not os.environ.get('IOALAB_REAL_JIUWEN_DATA_DIR'),reason='requires explicitly configured original Jiuwen/model')
def test_real_original_jiuwen_returns_a_valid_proposal_through_resident_provider_and_client(platform,tmp_path):
    from dataclasses import replace
    from ioalab_jiuwen.process import ProcessHost
    host=ProcessHost(json.loads(os.environ['IOALAB_REAL_JIUWEN_COMMAND']),os.environ['IOALAB_REAL_JIUWEN_DATA_DIR'],model=os.environ.get('IOALAB_REAL_JIUWEN_MODEL'))
    assert host.probe()['available'],host.capabilities()
    events=[];original_run=host.run
    def observed(turn):
        result=original_run(replace(turn,on_event=events.append))
        private_json(tmp_path/'provider-events.json',events)
        return result
    host.run=observed
    with participants(platform,tmp_path,agent_host=host) as (provider,assistant,host,template,task):
        started,_=request(assistant,provider,template,task);ref=started['data']['quoteRef']
        wait_for(lambda:terminal(assistant,ref),timeout=180);result=terminal(assistant,ref)
        assert result['status']=='completed',{'result':result,'publicEvents':events[-12:]}
        card=result['data']['card'];iid=result['data']['interactionId']
        assert card['signatures']['2'] and card['signatures']['3'] and card['signatures']['1'] is None
        relation=provider.store.interaction(interaction_id=iid)
        assert relation['session'] and relation['details']['definitionDigest']
        if os.environ.get('IOALAB_REAL_JIUWEN_REPORT'):
            report=Path(os.environ['IOALAB_REAL_JIUWEN_REPORT'])
            report.write_text(json.dumps({'scope':'Original Jiuwen business decision with real resident HTTP/WS/A2C/AP2; scripted trusted user; not native web UI, delivery or video acceptance',
                'model':host.capabilities()['model'],'interactionId':iid,'sessionId':relation['session'],'cardDigest':card_digest(card),
                'proposalPrice':card['derived']['total'],'marketSignature':card['signatures']['3'],'userSlotOne':card['signatures']['1'],
                'proposalFeeTransfers':len(platform.db.rows('SELECT * FROM transfers WHERE payee_id=?',provider.account.account['id']))},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
