"""Public flow IPC with real platform/protocol; only host, browser and notification edges are doubles."""
import copy
import json
import uuid
import pytest

from conftest import wait_for
from ioalab_sdk.runtime import LocalClient, ToolBinding, HostResult
from test_assistant_role import user_decision, runtime_at, ScriptedHost, INPUT, OUTPUT
from test_resident_delegation import participants
from test_resident_fulfillment import ProductionHost, VERIFIER, configure_template
from test_service_publication import draft


def uid():
    return str(uuid.uuid4())


def observe(rpc, name, ref, **outer):
    return rpc.call(name, {'action':'observe','flowRef':ref,**outer})


def settled(rpc, name, ref, **outer):
    value = observe(rpc,name,ref,**outer)
    return value if value['status'] in ('completed','failed','rejected','unknown') else None


def test_service_flow_preview_confirmation_publish_and_pause_resume(platform,tmp_path,monkeypatch):
    # Reuse the real provider assembly, then publish a new revision through only its flow.
    with participants(platform,tmp_path) as (provider,assistant,host,template,task):
        monkeypatch.setattr(provider.user_ui,'open',lambda:True)
        rpc = LocalClient(provider.config.home)
        raw = draft(platform)
        terms = {k:v for k,v in raw.items() if k in ('formation','deliverables','inputs','deadlines','pricing','acceptance','authorizableSteps','samples','extensions')}
        terms['pricing'] = {k:v for k,v in terms['pricing'].items() if k != 'paymentProviders'}
        service = {'profile':'service','name':'流程发布','description':'软件开发','tags':['开发'],
                   'categoryId':platform.discovery.categories()[0]['id'],'terms':[terms]}
        args = {'profile':'service','action':'publish','input':{'service':service}}
        key = uid()
        pending = rpc.call('provider.flow',args,operation_id=key)
        assert pending['status']=='waiting',pending
        assert pending['data']['reason']=='confirmation'
        ref = pending['data']['flowRef']
        confirmation = pending['data']['evidence']['confirmationRef']
        user_decision(provider,confirmation)
        wait_for(lambda:settled(rpc,'provider.flow',ref,profile='service'))
        done = observe(rpc,'provider.flow',ref,profile='service')
        assert done['status']=='completed',done
        assert done['data']['result']['publicationReceipt']['acceptedRevision']==2
        again = rpc.call('provider.flow',args,operation_id=key)
        assert again['data']['result']==done['data']['result']
        conflict = rpc.call('provider.flow',{**args,'input':{'service':{**service,'name':'changed'}}},operation_id=key)
        assert conflict['error']['code']=='id_conflict'
        for action,expected in [('pause',False),('resume_admission',True)]:
            value = rpc.call('provider.flow',{'profile':'service','flowRef':ref,'action':action})
            assert value['status']=='completed',value
            assert value['data']['result']['currentPolicy']['accepting'] is expected
        foreign = LocalClient(assistant.config.home).call('assistant.delegation.flow',{'flowRef':ref,'action':'resume'})
        assert foreign['error']['code']=='forbidden'


@pytest.mark.parametrize('notification_available',[True,False])
def test_delegation_flow_push_fetch_preview_and_notification_without_acceptance(platform,tmp_path,monkeypatch,notification_available):
    configure_template(monkeypatch)
    with participants(platform,tmp_path,agent_host=ProductionHost(),verifier_source=VERIFIER) as (provider,assistant,host,template,task):
        monkeypatch.setattr(assistant.user_ui,'open',lambda:True)
        effects = {}
        def notify(args,context):
            old = effects.setdefault(context['operationId'],copy.deepcopy(args))
            assert old==args
            return {'notificationRef':context['operationId'],'status':'queued','read':'unknown'}
        notification = ToolBinding('owner.notify','test notification',{},notify)
        if notification_available:
            assistant.modules[0].tools['owner.notify'] = notification
        rpc = LocalClient(assistant.config.home)
        begun = rpc.call('assistant.delegation.flow',{'action':'begin','input':{
            'purpose':'真实交付流程测试','budget':30,'proposalFeeLimit':2}})
        assert begun['status']=='waiting',begun
        ref = begun['data']['flowRef']
        user_decision(assistant,begun['data']['evidence']['confirmationRef'])
        wait_for(lambda:settled(rpc,'assistant.delegation.flow',ref))
        assert observe(rpc,'assistant.delegation.flow',ref)['nextActions']==[]
        invalid = rpc.call('assistant.delegation.flow',{'flowRef':ref,'action':'request','input':{'service':{},'spec':{}}})
        assert invalid['status']=='rejected' and invalid['error']['code']=='invalid_input'
        inspected = rpc.call('assistant.delegation.flow',{'flowRef':ref,'action':'inspect','input':{'service':provider.identities[0].agent_id}})
        assert inspected['status']=='completed',inspected
        requested = rpc.call('assistant.delegation.flow',{'flowRef':ref,'action':'request','input':{
            'service':provider.identities[0].agent_id,'spec':{'result':{}}}})
        assert requested['status']=='waiting',requested
        wait_for(lambda:settled(rpc,'assistant.delegation.flow',ref))
        compared = observe(rpc,'assistant.delegation.flow',ref)
        assert compared['status']=='completed',compared
        quote = compared['data']['quoteRefs'][0]
        selected = rpc.call('assistant.delegation.flow',{'flowRef':ref,'action':'select','input':{'quoteRef':quote}})
        assert selected['data']['reason']=='confirmation',selected
        user_decision(assistant,selected['data']['evidence']['confirmationRef'])
        wait_for(lambda:settled(rpc,'assistant.delegation.flow',ref))
        contract = observe(rpc,'assistant.delegation.flow',ref)['data']['contractRef']
        wait_for(lambda:rpc.call('assistant.delegation.status',{'contractRef':contract})['data']['stepRequestDigest'])
        question = rpc.call('assistant.delegation.flow',{'flowRef':ref,'action':'answer_step'})
        assert question['data']['reason']=='confirmation',question
        user_decision(assistant,question['data']['evidence']['confirmationRef'],decision='answer',answers=[{'selected_options':['allow']}])
        wait_for(lambda:settled(rpc,'assistant.delegation.flow',ref))
        # No user utterance, status RPC, or model polling discovers this delivery.
        if not notification_available:
            wait_for(lambda:observe(rpc,'assistant.delegation.flow',ref)['data'].get('deliveryWait',{}).get('report'))
            assert not effects
            assistant.modules[0].tools['owner.notify'] = notification
        try:
            wait_for(lambda:effects)
        except AssertionError:
            assistant.modules[0].flows.deliveries.scan()
            raise AssertionError({'role':assistant.modules[0].error,'provider':provider.modules[0].execution.errors,'host':getattr(host,'failure',None),'flow':observe(rpc,'assistant.delegation.flow',ref)})
        observed = observe(rpc,'assistant.delegation.flow',ref)
        evidence = observed['data']['deliveries']['1']
        assert evidence['report']['status']=='pass',evidence
        from pathlib import Path
        assert Path(evidence['files']['result.json']['path']).read_bytes()==b'{"delivered":"actual file bytes"}'
        assert evidence['notification']['status']=='queued'
        facts = rpc.call('assistant.delegation.status',{'contractRef':contract})['data']
        assert len(facts['card']['signatures']['6'])==1
        assert facts['card']['signatures']['7']==[]
        assert not [v for v in facts['vouchers'] if v.get('purpose')=='installment']
        before = copy.deepcopy(facts['card']['signatures'])
        for _ in range(3):
            assistant.modules[0].flows.scan()
        assert len(effects)==1
        assert rpc.call('assistant.delegation.status',{'contractRef':contract})['data']['card']['signatures']==before


def test_messaging_flow_inbox_publication_and_two_rounds(platform,tmp_path,monkeypatch):
    def reserve(args,context):
        return {'status':'reserved'}
    tool = ToolBinding('reserve_slot','test',INPUT,reserve,output_schema=OUTPUT)
    with runtime_at(platform,tmp_path/'sender') as (sender,rpc,_,user):
        with runtime_at(platform,tmp_path/'receiver',ScriptedHost(),[tool]) as (receiver,inbox,module,target):
            monkeypatch.setattr(receiver.user_ui,'open',lambda:True)
            declaration = {'name':'slot.request','description':'测试规则','enabled':True,'allowedSenders':[user['username']],
                           'inputSchema':INPUT,'outputSchema':OUTPUT,'tools':['reserve_slot']}
            pending = inbox.call('assistant.messaging.flow',{'action':'configure_inbox','input':{
                'declaration':declaration,'instructions':'用reserve_slot处理请求并回复。'}})
            assert pending['data']['reason']=='confirmation',pending
            user_decision(receiver,pending['data']['evidence']['confirmationRef'])
            wait_for(lambda:settled(inbox,'assistant.messaging.flow',pending['data']['flowRef']))
            started = rpc.call('assistant.messaging.flow',{'action':'send','input':{
                'account':target['username'],'interface':'slot.request','input':{'slot':'first'}}})
            ref = started['data']['flowRef']
            wait_for(lambda:settled(rpc,'assistant.messaging.flow',ref))
            first = observe(rpc,'assistant.messaging.flow',ref)
            assert first['data']['result']['result']['output']=={'status':'reserved'},first
            continued = rpc.call('assistant.messaging.flow',{'action':'send','flowRef':ref,'input':{'input':{'slot':'second'}}})
            wait_for(lambda:settled(rpc,'assistant.messaging.flow',ref))
            second = observe(rpc,'assistant.messaging.flow',ref)
            assert second['data']['result']['interactionRef']==first['data']['result']['interactionRef']
            assert second['data']['messageRef']!=first['data']['messageRef']


@pytest.mark.parametrize('decision',['approve','reject'])
def test_flow_wait_survives_runtime_restart_and_preserves_confirmation(platform,tmp_path,monkeypatch,decision):
    from ioalab_sdk.assistant import AssistantRole
    from ioalab_sdk.runtime import RuntimeConfig, ResidentRuntime
    with runtime_at(platform,tmp_path/'owner') as (original,rpc,module,user):
        monkeypatch.setattr(original.user_ui,'open',lambda:True)
        args = {'action':'begin','input':{'purpose':'原授权恢复','budget':10,'proposalFeeLimit':1}}
        key = uid()
        first = rpc.call('assistant.delegation.flow',args,operation_id=key)
        assert first['status']=='waiting'
        confirmation = first['data']['evidence']['confirmationRef']
        ref = first['data']['flowRef']
        settings = RuntimeConfig.load(original.config.source)
        original.close()
        with ResidentRuntime(settings,modules=(AssistantRole(),)) as restored:
            monkeypatch.setattr(restored.user_ui,'open',lambda:True)
            wait_for(lambda:restored.connection.connected)
            rpc = LocalClient(restored.config.home)
            again = rpc.call('assistant.delegation.flow',args,operation_id=key)
            assert again['data']['flowRef']==ref
            assert again['data']['evidence']['confirmationRef']==confirmation
            user_decision(restored,confirmation,decision=decision)
            wait_for(lambda:settled(rpc,'assistant.delegation.flow',ref))
            final = observe(rpc,'assistant.delegation.flow',ref)
            assert final['status']==('completed' if decision=='approve' else 'rejected'),final
            assert len(restored.confirmations.list())==1
            changed = rpc.call('assistant.delegation.flow',{**args,'input':{**args['input'],'budget':11}},operation_id=key)
            assert changed['error']['code']=='id_conflict'


def test_two_period_flow_waits_for_independent_acceptance_before_next_delivery(platform,tmp_path,monkeypatch):
    from test_resident_multiperiod import TwoPeriodHost, terms, VERIFIER as MULTI_VERIFIER
    with participants(platform,tmp_path,agent_host=TwoPeriodHost(),verifier_source=MULTI_VERIFIER,
                      template_terms=lambda verifier:terms('proposal',verifier)) as (provider,assistant,host,template,task):
        monkeypatch.setattr(assistant.user_ui,'open',lambda:True)
        notices = {}
        def notify(args,context):
            notices.setdefault(context['operationId'],args)
            return {'notificationRef':context['operationId'],'status':'queued','read':'unknown'}
        assistant.modules[0].tools['owner.notify'] = ToolBinding('owner.notify','test',{},notify)
        rpc = LocalClient(assistant.config.home)
        first = rpc.call('assistant.delegation.flow',{'action':'begin','input':{
            'purpose':'两期成果','budget':30,'proposalFeeLimit':2}})
        ref = first['data']['flowRef']
        user_decision(assistant,first['data']['evidence']['confirmationRef'])
        wait_for(lambda:settled(rpc,'assistant.delegation.flow',ref))
        inspected = rpc.call('assistant.delegation.flow',{'flowRef':ref,'action':'inspect','input':{'service':provider.identities[0].agent_id}})
        assert inspected['status']=='completed',inspected
        rpc.call('assistant.delegation.flow',{'action':'request','flowRef':ref,'input':{
            'service':provider.identities[0].agent_id,
            'spec':{'draft':{},'release':{}}}})
        wait_for(lambda:settled(rpc,'assistant.delegation.flow',ref))
        quote = observe(rpc,'assistant.delegation.flow',ref)['data']['quoteRefs'][0]
        selected = rpc.call('assistant.delegation.flow',{'action':'select','flowRef':ref,'input':{'quoteRef':quote}})
        user_decision(assistant,selected['data']['evidence']['confirmationRef'])
        wait_for(lambda:settled(rpc,'assistant.delegation.flow',ref))
        contract = observe(rpc,'assistant.delegation.flow',ref)['data']['contractRef']
        for period in (1,2):
            wait_for(lambda:rpc.call('assistant.delegation.status',{'contractRef':contract})['data']['stepRequestDigest'])
            question = rpc.call('assistant.delegation.flow',{'action':'answer_step','flowRef':ref})
            user_decision(assistant,question['data']['evidence']['confirmationRef'],decision='answer',answers=[{'selected_options':['allow']}])
            wait_for(lambda:len(notices)==period)
            evidence = observe(rpc,'assistant.delegation.flow',ref)['data']['deliveries'][str(period)]
            assert evidence['report']['status']=='pass'
            facts = rpc.call('assistant.delegation.status',{'contractRef':contract})['data']
            assert len(facts['card']['signatures']['6'])==period
            assert len(facts['card']['signatures']['7'])==period-1
            if period==1:
                wait_for(lambda:settled(rpc,'assistant.delegation.flow',ref))
                accepted = rpc.call('assistant.delegation.flow',{'action':'accept','flowRef':ref,'input':{'period':1}})
                wait_for(lambda:observe(rpc,'assistant.delegation.flow',ref)['data'].get('reason')=='confirmation')
                pending = observe(rpc,'assistant.delegation.flow',ref)
                user_decision(assistant,pending['data']['evidence']['confirmationRef'])
                wait_for(lambda:settled(rpc,'assistant.delegation.flow',ref))


def test_messaging_flow_unknown_lookup_recovers_original_message_without_resending(platform,tmp_path,monkeypatch):
    import httpx
    from test_assistant_role import publish
    tool = ToolBinding('reserve_slot','test',INPUT,lambda *_:{'status':'reserved'},output_schema=OUTPUT)
    with runtime_at(platform,tmp_path/'sender') as (sender,rpc,_,user):
        with runtime_at(platform,tmp_path/'receiver',ScriptedHost(),[tool]) as (receiver,inbox,module,target):
            publish(receiver,inbox,user['username'])
            receiver.close()
            sent = rpc.call('assistant.messaging.flow',{'action':'send','input':{
                'account':target['username'],'interface':'slot.request','input':{'slot':'first'},'ttlSeconds':1}})
            ref = sent['data']['flowRef']
            wait_for(lambda:observe(rpc,'assistant.messaging.flow',ref)['data'].get('evidence',{}).get('platformReceipt'))
            # Lose push delivery, then fail only the external original-message lookup.
            sender.connection.close()
            api = sender.account.api
            def unavailable(method,path,*args,**kwargs):
                if path.startswith('/messages/'):
                    raise httpx.ConnectError('isolated lookup outage')
                return api(method,path,*args,**kwargs)
            with monkeypatch.context() as patch:
                patch.setattr(sender.account,'api',unavailable)
                wait_for(lambda:observe(rpc,'assistant.messaging.flow',ref)['status']=='unknown')
                unknown = observe(rpc,'assistant.messaging.flow',ref)
                assert unknown['data']['reason']=='unknown_result'
                mid = unknown['data']['messageRef']
                blocked = rpc.call('assistant.messaging.flow',{'flowRef':ref,'action':'send','input':{'input':{'slot':'second'}}})
                assert blocked['status']=='rejected' and blocked['error']['code']=='not_ready'
            wait_for(lambda:observe(rpc,'assistant.messaging.flow',ref)['status']=='completed')
            final = observe(rpc,'assistant.messaging.flow',ref)
            assert final['data']['messageRef']==mid
            assert final['data']['result']['requestState']=='expired',final
