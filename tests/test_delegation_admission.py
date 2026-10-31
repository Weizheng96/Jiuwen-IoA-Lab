"""Real WS intents and AP2 signatures, atomic capacity and scope accounting."""
import copy
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from decimal import Decimal
import json
import time
from types import SimpleNamespace
import uuid

import pytest
from a2c.card import build_request,request_digest,build_card,card_digest
from a2c.codec import digest,utcnow
from a2c.jws import sign
from a2c.slots import sign_template,sign_slot
from a2c.payment.ap2 import checkout,checkout_mandate,payment_mandate
from ioalab.errors import LabError
from ioalab_sdk.runtime import AgentConnection,RuntimeStore
from ioalab_sdk.templates import template_from_definition
from ioalab_sdk.runtime.config import RuntimeConfig
from ioalab_sdk.runtime.resident import ResidentRuntime
from ioalab_sdk.runtime.ipc import LocalClient
from ioalab_sdk.assistant.role import AssistantRole
from conftest import wait_for
from test_resident_runtime import configuration
from test_assistant_role import user_decision
from test_agent_delivery import Peer,at
from test_service_publication import service,publication,submit
from test_delegation_scopes import authorization,register


def uid():return str(uuid.uuid4())


@contextmanager
def online(client,local,service,*,runtimes=(None,None)):
    account,identity=service
    a=Peer(client,{'actor':local.signer.id,'token':local.token},runtime=runtimes[0])
    b=Peer(client,{'actor':identity.agent_id,'token':account.token},runtime=runtimes[1])
    try:yield a,b
    finally:b.close();a.close()


def offer(platform,service):
    account,identity=service
    response=submit(account,identity,publication(platform,identity));response.raise_for_status()
    return response.json()['templates'][0]


def intent(platform,client,local,service,template,*,fees='3.00',scope=None):
    _,identity=service
    if scope is None:
        response=register(client,local,authorization(local,fees=fees));response.raise_for_status();scope=response.json()['scopeDigest']
    request=build_request(template,request_id=uid(),version=1,client={'id':local.signer.id,'jwk':local.signer.jwk},
                          signers={'slot1':'client','slot7':'client'},spec={'result':{}},inputs={},
                          delegation_scope_digest=scope,payment_provider=platform.payment_provider)
    body={'formation':'proposal','templateDigest':template['templateDigest'],'scopeDigest':scope,
          'bindingDigest':request_digest(request),'request':request}
    return {'messageId':uid(),'kind':'delegation.intent','recipient':identity.agent_id,'expiresAt':at(600),'body':body}


def opened(sender,receiver,value,*,start=True):
    sender.call('message.send',value)
    actual=receiver.delivery(value['messageId'])['message']
    iid=uid();message={'messageId':uid(),'kind':'interaction.opened','recipient':sender.actor,'originMessageId':value['messageId'],
                      'interactionId':iid,'expiresAt':at(600),'body':{'interactionType':'delegation','bindingDigest':value['body']['bindingDigest'],'deadline':value['expiresAt']}}
    receiver.call('message.send',message)
    assert sender.delivery(message['messageId'])['message']['interactionId']==iid
    if start:receiver.call('request.start',{'messageId':actual['messageId'],'interactionId':iid})
    return {'interactionId':iid,'originMessageId':value['messageId'],'bindingDigest':value['body']['bindingDigest']}


def fee_body(local,identity,template,value):
    request=value['body']['request'];now=int(time.time());payee={'id':identity.protocol_id,'name':identity.name}
    amount=template['pricing']['proposalFee']
    token=checkout(payee,identity.payment,purpose='proposal-fee',digest_ref={'requestDigest':request_digest(request)},
                   period=None,amount=amount,currency='CNY',now=now)
    return {'checkoutMandate':checkout_mandate(token,local.payer,now),
            'paymentMandate':payment_mandate(token,local.payer,payee=payee,amount=amount,currency='CNY',instrument={'id':'test','type':'wallet'},now=now),
            'template':template,'request':request}


def configure(receiver,maximum=1):
    return receiver.call('admission.configure',{'expectedRevision':0,'maxActiveDelegations':maximum,'accepting':True})


def frame(peer,kind,payload,operation=None):
    return {'type':kind,'agentId':peer.actor,'generation':peer.generation,'operationId':operation or uid(),'payload':payload}


def invoke(platform,peer,kind,payload,operation=None):
    owner=platform.identities.agent(peer.actor)['owner_id']
    return platform.messaging.delivery.control(owner,peer.ready['connectionId'],{peer.actor:peer.generation},frame(peer,kind,payload,operation))


def test_ws_intent_opens_receivers_uuid_and_returns_actual_reservation_before_fee(platform,client,local_client,service):
    local=local_client;template=offer(platform,service);value=intent(platform,client,local,service,template)
    with online(client,local,service) as (sender,receiver):
        configure(receiver)
        binding=opened(sender,receiver,value)
        operation=uid();reserved=receiver.call('admission.reserve',binding,operation)
        assert reserved['state']=='held' and reserved['bindingDigest']==value['body']['bindingDigest']
        assert receiver.call('admission.reserve',binding,operation)==reserved
        result={'messageId':uid(),'kind':'delegation.result','recipient':sender.actor,'interactionId':binding['interactionId'],
                'inReplyTo':value['messageId'],'expiresAt':at(600),'body':{'outcome':'reserved',**{k:reserved[k] for k in ('admissionId','bindingDigest','holdUntil')}}}
        receiver.call('message.send',result)
        assert sender.delivery(result['messageId'])['message']['body']==result['body']
        relation=client.get('/api/v1/interactions',headers={'Authorization':'Bearer '+local.token},params={'agentId':sender.actor,'originMessageId':value['messageId']}).json()
        assert relation['interactionId']==binding['interactionId'] and relation['bindingDigest']==value['body']['bindingDigest']
        assert receiver.call('admission.status',{})['active']==1
        assert platform.db.rows('SELECT * FROM transfers WHERE ref=?',value['body']['bindingDigest'])==[]


def test_offline_intent_queues_without_charging_then_delivers_after_connection(platform,client,local_client,service):
    local=local_client;template=offer(platform,service);value=intent(platform,client,local,service,template)
    sender=Peer(client,{'actor':local.signer.id,'token':local.token})
    try:
        sender.call('message.send',value)
        assert platform.db.rows('SELECT * FROM delegation_admissions WHERE origin=?',value['messageId'])==[]
        assert platform.db.rows('SELECT * FROM transfers WHERE ref=?',value['body']['bindingDigest'])==[]
        account,identity=service
        receiver=Peer(client,{'actor':identity.agent_id,'token':account.token})
        try:assert receiver.delivery(value['messageId'])['message']['body']==value['body']
        finally:receiver.close()
    finally:sender.close()


def test_last_capacity_slot_is_atomic_and_fresh_operations_do_not_extend_original_hold(platform,client,local_client,service):
    local=local_client;template=offer(platform,service)
    values=[intent(platform,client,local,service,template) for _ in range(2)]
    with online(client,local,service) as (sender,receiver):
        configure(receiver)
        bindings=[opened(sender,receiver,value) for value in values]
        def reserve(binding):
            try:return invoke(platform,receiver,'admission.reserve',binding)
            except LabError as exc:return exc.code
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(reserve,bindings))
        accepted=next(value for value in results if isinstance(value,dict))
        assert results.count('capacity_full')==1 and accepted['state']=='held'
        assert receiver.call('admission.status',{})['active']==1
        original=bindings[results.index(accepted)]
        assert receiver.call('admission.reserve',original)==accepted
        assert platform.db.rows('SELECT * FROM transfers WHERE ref IN (?,?)',*[v['body']['bindingDigest'] for v in values])==[]


def test_real_ap2_scope_and_admission_commit_together_and_restore_original_after_revocation(platform,client,local_client,service):
    local=local_client;template=offer(platform,service);value=intent(platform,client,local,service,template)
    with online(client,local,service) as (sender,receiver):
        configure(receiver);binding=opened(sender,receiver,value);reserved=receiver.call('admission.reserve',binding)
        body=fee_body(local,service[1],template,value);payload={'admissionId':reserved['admissionId'],'ap2':body};operation=uid()
        receipt=sender.call('ap2.pay',payload,operation)
        assert receipt['op']=='ap2.pay' and receipt['voucher']['purpose']=='proposal-fee'
        assert receiver.call('admission.status',{'admissionId':reserved['admissionId']})['state']=='committed'
        assert len(platform.db.rows('SELECT * FROM transfers WHERE ref=?',value['body']['bindingDigest']))==1
        state=client.get('/api/v1/scopes/'+value['body']['scopeDigest'],params={'actor':sender.actor},headers={'Authorization':'Bearer '+local.token}).json()
        assert state['proposalFeesPaid']=='1.00'
        sender.call('scope.close',{'scopeDigest':value['body']['scopeDigest']})
        assert sender.call('ap2.pay',payload,operation)==receipt
        assert sender.call('ap2.pay',payload)==receipt
        assert sender.call('interaction.close',{'interactionId':binding['interactionId']},ok=False)['code']=='not_ready'
        assert receiver.call('admission.status',{})['active']==1
        changed=copy.deepcopy(payload);changed['admissionId']=uid()
        assert sender.call('ap2.pay',changed,operation,ok=False)['code']=='id_conflict'
        assert client.get('/api/v1/operations/'+operation,params={'agentId':sender.actor,'operation':'ap2.pay'},headers={'Authorization':'Bearer '+local.token}).json()==receipt


def test_budget_write_failure_rolls_back_fee_and_capacity_and_public_ap2_cannot_bypass_it(platform,client,local_client,service):
    local=local_client;template=offer(platform,service);value=intent(platform,client,local,service,template)
    with online(client,local,service) as (sender,receiver):
        configure(receiver);binding=opened(sender,receiver,value);reserved=receiver.call('admission.reserve',binding)
        body=fee_body(local,service[1],template,value)
        reply=platform.psp.handle('ap2.pay',body)
        assert reply.body['error']['code']=='out-of-order'
        owner=local.account['id'];payload={'admissionId':reserved['admissionId'],'ap2':body};operation=uid()
        prepared=platform.payment_operations.prepare(owner,sender.actor,operation,payload)
        original=platform.scopes.record_payment_in
        def fail_after_write(*args):original(*args);raise RuntimeError('test scope receipt failure')
        from unittest.mock import patch
        with patch.object(platform.scopes,'record_payment_in',side_effect=fail_after_write):
            with pytest.raises(RuntimeError,match='scope receipt failure'):
                with platform.db.tx() as conn:platform.payment_operations.apply_in(conn,owner,sender.actor,prepared)
        assert receiver.call('admission.status',{'admissionId':reserved['admissionId']})['state']=='held'
        assert platform.db.rows('SELECT * FROM transfers WHERE ref=?',value['body']['bindingDigest'])==[]
        assert platform.db.rows('SELECT * FROM admission_payments WHERE admission=?',reserved['admissionId'])==[]
        assert platform.db.rows('SELECT * FROM delegation_payment_operations WHERE id=?',operation)==[]
        assert sender.call('ap2.pay',payload,operation)['op']=='ap2.pay'


def test_expiry_and_close_prevent_fee_after_admission(platform,client,local_client,service,monkeypatch):
    local=local_client;template=offer(platform,service);value=intent(platform,client,local,service,template)
    with online(client,local,service) as (sender,receiver):
        configure(receiver);binding=opened(sender,receiver,value);reserved=receiver.call('admission.reserve',binding)
        payload={'admissionId':reserved['admissionId'],'ap2':fee_body(local,service[1],template,value)}
        with monkeypatch.context() as patch:
            patch.setattr(platform.admissions,'clock',lambda:time.time()+121)
            assert sender.call('ap2.pay',payload,ok=False)['code']=='expired'
            assert receiver.call('admission.status',{'admissionId':reserved['admissionId']})['state']=='released'
        assert platform.db.rows('SELECT * FROM transfers WHERE ref=?',value['body']['bindingDigest'])==[]
        assert sender.call('interaction.close',{'interactionId':binding['interactionId']})['state']=='closed'


def test_started_intent_and_original_binding_are_required_and_two_uuids_cannot_replace_it(platform,client,local_client,service):
    local=local_client;template=offer(platform,service);value=intent(platform,client,local,service,template)
    with online(client,local,service) as (sender,receiver):
        configure(receiver);binding=opened(sender,receiver,value,start=False)
        assert receiver.call('admission.reserve',binding,ok=False)['code']=='forbidden'
        altered={**binding,'bindingDigest':'sha256:'+'a'*64}
        assert receiver.call('admission.reserve',altered,ok=False)['code']=='forbidden'
        assert sender.call('admission.configure',{'expectedRevision':0,'maxActiveDelegations':100,'accepting':True},ok=False)['code']=='forbidden'
        opened_again={'messageId':uid(),'kind':'interaction.opened','recipient':sender.actor,'originMessageId':value['messageId'],
                      'interactionId':uid(),'expiresAt':at(600),'body':{'interactionType':'delegation','bindingDigest':value['body']['bindingDigest'],'deadline':value['expiresAt']}}
        assert receiver.call('message.send',opened_again,ok=False)['code']=='id_conflict'


def test_zero_fee_requires_committed_capacity_before_any_valid_proposal(platform,client,local_client,service):
    local=local_client;account,identity=service
    raw=template_from_definition({'formation':'proposal','deliverable':{'name':'result'},'pricing':{'proposalFee':0}},packages=None,payment_provider=platform.payment_provider)
    signed=sign_template({**raw,'provider':identity.protocol_id},identity.signer,'provider',utcnow())
    response=submit(account,identity,publication(platform,identity,template=signed));response.raise_for_status();template=response.json()['templates'][0]
    value=intent(platform,client,local,service,template)
    with online(client,local,service) as (sender,receiver):
        configure(receiver);binding=opened(sender,receiver,value);held=receiver.call('admission.reserve',binding)
        assert receiver.call('admission.commit',{'admissionId':held['admissionId']})['state']=='committed'
        assert receiver.call('admission.status',{})['active']==1
        assert platform.db.rows('SELECT * FROM transfers WHERE ref=?',value['body']['bindingDigest'])==[]


def test_countersign_intent_needs_selected_card_and_commit_needs_fresh_original_slot_one(platform,client,local_client,service):
    local=local_client;account,identity=service
    def upload(**args):return account.api('POST','/provider/packages',{'verifierId':args['verifier_id'],'source':args['source'],'reads':args.get('reads',[])})
    raw=template_from_definition({'formation':'countersign','deliverable':{'name':'result'},'pricing':{'price':'9'},
                                 'verifier':{'id':'admission_test@1.0','source':"def verify(ctx):\n    return {'pass': True}\n"}},
                                 packages=SimpleNamespace(put=upload),payment_provider=platform.payment_provider)
    signed=sign_template({**raw,'provider':identity.protocol_id},identity.signer,'provider',utcnow())
    response=submit(account,identity,publication(platform,identity,template=signed));response.raise_for_status();template=response.json()['templates'][0]
    response=register(client,local,authorization(local));response.raise_for_status();scope=response.json()['scopeDigest']
    card=build_card(template,card_id=uid(),client={'id':local.signer.id,'jwk':local.signer.jwk},signers={'slot1':'client','slot7':'client'},
                    spec={'result':{}},inputs={},delegation_scope_digest=scope,payment_provider=platform.payment_provider)
    value={'messageId':uid(),'kind':'delegation.intent','recipient':identity.agent_id,'expiresAt':at(600),
           'body':{'formation':'countersign','templateDigest':template['templateDigest'],'scopeDigest':scope,'bindingDigest':card_digest(card),'card':card}}
    with online(client,local,service) as (sender,receiver):
        configure(receiver)
        assert sender.call('message.send',value,ok=False)['code']=='forbidden'
        proof=sign(digest({'scopeDigest':scope,'cardDigest':card_digest(card)}),local.user,'ioa-contract-selection',utcnow())
        sender.call('scope.select',{'scopeDigest':scope,'card':card,'proof':proof})
        binding=opened(sender,receiver,value);held=receiver.call('admission.reserve',binding)
        assert receiver.call('admission.commit',{'admissionId':held['admissionId'],'card':card},ok=False)['code']=='invalid_input'
        signed_card=sign_slot(card,1,local.signer,utcnow())
        assert receiver.call('admission.commit',{'admissionId':held['admissionId'],'card':signed_card})['state']=='committed'
        assert platform.db.rows('SELECT * FROM transfers WHERE ref=?',card_digest(card))==[]


def test_parallel_fees_cannot_both_spend_the_last_authorized_fee_budget(platform,client,local_client,service):
    local=local_client;template=offer(platform,service)
    first=intent(platform,client,local,service,template,fees='1.00')
    second=intent(platform,client,local,service,template,scope=first['body']['scopeDigest'])
    with online(client,local,service) as (sender,receiver):
        configure(receiver,2)
        payloads=[]
        for value in (first,second):
            binding=opened(sender,receiver,value);held=receiver.call('admission.reserve',binding)
            payloads.append({'admissionId':held['admissionId'],'ap2':fee_body(local,service[1],template,value)})
        def pay(payload):
            try:return invoke(platform,sender,'ap2.pay',payload)
            except LabError as exc:return exc.code
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(pay,payloads))
        assert sum(isinstance(result,dict) and result.get('op')=='ap2.pay' for result in results)==1
        assert results.count('quota_exceeded')==1
        assert len(platform.db.rows('SELECT * FROM transfers WHERE ref IN (?,?)',first['body']['bindingDigest'],second['body']['bindingDigest']))==1
        states=[receiver.call('admission.status',{'admissionId':payload['admissionId']})['state'] for payload in payloads]
        assert sorted(states)==['committed','held']


def test_sdk_intent_and_both_local_uuid_mappings_recover_without_reopening(platform,client,service,tmp_path):
    config,user=configuration(platform,tmp_path,service=False)
    settings=RuntimeConfig.load(config)
    module=AssistantRole();runtime=ResidentRuntime(settings,modules=(module,)).start()
    provider_store=provider_connection=None
    try:
        wait_for(lambda:runtime.connection.connected)
        rpc=LocalClient(settings.home);task=rpc.call('assistant.delegation.prepare',{'purpose':'测试真实接入意向','budget':'500','proposalFeeLimit':'3'})['data']['taskRef']
        operation=uid();waiting=rpc.call('assistant.delegation.authorize',{'taskRef':task},operation_id=operation)
        user_decision(runtime,waiting['data']['confirmationRef'])
        authorized=rpc.call('assistant.delegation.authorize',{'taskRef':task},operation_id=operation)['data']
        template=offer(platform,service);identity=runtime.identities[0]
        request=build_request(template,request_id=uid(),version=1,client={'id':identity.agent_id,'jwk':identity.signer.jwk},
                              signers={'slot1':'client','slot7':'client'},spec={'result':{}},inputs={},
                              delegation_scope_digest=authorized['scopeDigest'],payment_provider=platform.payment_provider)
        account,provider=service
        provider_store=RuntimeStore(tmp_path/'provider.sqlite',platform.s.base_url,[provider.agent_id],owner_id=account.account['id'])
        provider_connection=AgentConnection(platform.s.base_url,account.bearer,[provider.agent_id],provider_store).start()
        args={'taskRef':task,'service':provider.agent_id,'request':request};operation=uid()
        accepted=rpc.call('assistant.delegation.intent',args,operation_id=operation)
        assert accepted['status']=='completed',accepted
        origin=accepted['data']['messageRef']
        wait_for(lambda:any(m['message']['messageId']==origin for m in provider_store.messages(provider.agent_id)))
        opened=provider_store.prepare_open(provider.agent_id,origin)
        provider_connection.wait(provider.agent_id,'message.send',opened['operationId'])
        wait_for(lambda:runtime.store.interaction(origin=origin) is not None)
        original_id=opened['interactionId']
        assert runtime.store.interaction(origin=origin)['id']==original_id
        assert provider_store.prepare_open(provider.agent_id,origin)==opened
        assert rpc.call('assistant.delegation.intent',args,operation_id=operation)==accepted
    finally:
        if provider_connection:provider_connection.close()
        if provider_store:provider_store.close()
        runtime.close()
    runtime=ResidentRuntime(settings,modules=(AssistantRole(),)).start()
    try:
        wait_for(lambda:runtime.connection.connected)
        result=LocalClient(settings.home).call('assistant.delegation.intent_status',{'messageRef':origin})
        assert result['status']=='completed' and result['data']['interaction']['interactionId']==original_id
        assert runtime.store.interaction(origin=origin)['id']==original_id
    finally:runtime.close()


def test_connection_takeover_during_payment_prepare_fences_the_old_generation(platform,client,local_client,service,monkeypatch):
    import threading
    local=local_client;template=offer(platform,service);value=intent(platform,client,local,service,template)
    with online(client,local,service) as (sender,receiver):
        configure(receiver);binding=opened(sender,receiver,value);held=receiver.call('admission.reserve',binding)
        payload={'admissionId':held['admissionId'],'ap2':fee_body(local,service[1],template,value)}
        ready,release=threading.Event(),threading.Event()
        original=platform.payment_operations.prepare
        def paused(*args):
            prepared=original(*args);ready.set();assert release.wait(10);return prepared
        errors=[]
        def pay():
            try:invoke(platform,sender,'ap2.pay',payload)
            except LabError as exc:errors.append(exc.code)
        replacement=None
        with monkeypatch.context() as patch:
            patch.setattr(platform.payment_operations,'prepare',paused)
            worker=threading.Thread(target=pay);worker.start()
            try:
                assert ready.wait(5)
                replacement=Peer(client,{'actor':local.signer.id,'token':local.token},runtime=sender.runtime)
                assert replacement.generation>sender.generation
            finally:release.set();worker.join(10)
        try:
            assert not worker.is_alive() and errors==['stale_generation']
            assert platform.db.rows('SELECT * FROM transfers WHERE ref=?',value['body']['bindingDigest'])==[]
            assert receiver.call('admission.status',{'admissionId':held['admissionId']})['state']=='held'
            assert replacement.call('ap2.pay',payload)['op']=='ap2.pay'
        finally:
            if replacement:replacement.close()


def test_committed_admission_and_relation_survive_ordinary_message_retention(platform,client,local_client,service,monkeypatch):
    local=local_client;template=offer(platform,service);value=intent(platform,client,local,service,template)
    with online(client,local,service) as (sender,receiver):
        configure(receiver);binding=opened(sender,receiver,value);held=receiver.call('admission.reserve',binding)
        sender.call('ap2.pay',{'admissionId':held['admissionId'],'ap2':fee_body(local,service[1],template,value)})
    # Retention is the subject here. Advancing the SAME clock while a live WS
    # worker validates leases also (correctly) expires that connection, so test
    # recovery on a fresh connection rather than racing a temporary clock jump.
    with monkeypatch.context() as patch:
        patch.setattr(platform.messaging.delivery,'clock',lambda:time.time()+90*86400)
        platform.messaging.delivery.cleanup()
    previous=(sender,receiver)
    # Ordinary reconnect keeps the durable runtime IDs; inventing new IDs here
    # races server-side close cleanup and correctly trips the competing-instance fence.
    with online(client,local,service,runtimes=tuple(peer.runtime for peer in previous)) as (sender,receiver):
        assert sender.generation>previous[0].generation and receiver.generation>previous[1].generation
        assert receiver.call('admission.status',{'admissionId':held['admissionId']})['state']=='committed'
        relation=client.get('/api/v1/interactions',headers={'Authorization':'Bearer '+local.token},params={'agentId':sender.actor,'originMessageId':value['messageId']}).json()
        assert relation['state']=='open' and relation['interactionId']==binding['interactionId']
        assert platform.db.one('SELECT envelope FROM delivery_messages WHERE id=?',value['messageId'])['envelope']


def test_pending_reclassification_cannot_use_the_old_category_to_accept_new_work(platform,client,local_client,service,monkeypatch):
    local=local_client;account,identity=service;template=offer(platform,service)
    categories=platform.discovery.categories();allowed=categories[0]['id'];next_category=categories[1]['id']
    payload=authorization(local);payload['context']['categoryIds']=[allowed]
    payload=authorization(local,context=payload['context'])
    registered=register(client,local,payload);registered.raise_for_status();scope=registered.json()['scopeDigest']
    update=publication(platform,identity,revision=2)
    update['declaration']['categoryId']=next_category
    update['proof']=sign(digest(update['declaration']),identity.signer,'ioa-service',utcnow())
    with monkeypatch.context() as patch:
        patch.setattr(platform.discovery,'register_one',lambda *a,**kw:(_ for _ in ()).throw(OSError('test index outage')))
        response=submit(account,identity,update)
    response.raise_for_status();updated=response.json()['templates'][0]
    value=intent(platform,client,local,service,updated,scope=scope)
    with online(client,local,service) as (sender,receiver):
        assert sender.call('message.send',value,ok=False)['code']=='not_ready'
        assert submit(account,identity,update).status_code==200
        assert sender.call('message.send',value,ok=False)['code']=='forbidden'
        assert platform.db.rows('SELECT * FROM delegation_intents WHERE origin=?',value['messageId'])==[]
