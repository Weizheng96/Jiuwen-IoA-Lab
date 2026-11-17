"""Original admission rejection proof; never infer absent effects from a timeout."""
import copy
import threading
from types import SimpleNamespace

import httpx
import pytest
from ioalab_sdk.assistant.trading import Trading
from ioalab_sdk.runtime.ipc import LocalClient
from ioalab_sdk.runtime.protocol import RuntimeFault
from conftest import wait_for
from test_assistant_role import user_decision
from test_resident_delegation import participants,request,terminal,uid


@pytest.mark.parametrize('local,remote,expected',[
    (None,404,False),
    ({'ok':False,'error':{'code':'unknown_result'}},404,False),
    ({'ok':False,'error':{'code':'not_ready'}},200,False),
    ({'ok':False,'error':{'code':'not_ready'}},409,False),
    ({'ok':False,'error':{'code':'not_ready'}},503,False),
    ({'ok':False,'error':{'code':'not_ready'}},'offline',False),
    ({'ok':False,'error':{'code':'not_ready'}},404,True),
])
def test_original_negative_receipt_and_absent_acceptance_are_both_required(local,remote,expected):
    trading=object.__new__(Trading)
    trading.identity=SimpleNamespace(agent_id='original-actor')
    trading.store=SimpleNamespace(response=lambda *_:local)
    def lookup(*_):
        req=httpx.Request('GET','http://127.0.0.1/operations/original')
        if remote=='offline':raise httpx.ConnectError('offline',request=req)
        response=httpx.Response(remote,request=req)
        response.raise_for_status()
        return {'messageId':'original-already-accepted'}
    trading.runtime=SimpleNamespace(account=SimpleNamespace(api=lookup))
    result=trading.rejected_intent({'quoteRef':'original-quote'})
    assert bool(result)==expected
    if expected:assert result==local['error']


@pytest.mark.parametrize('interrupt_local_finish',[False,True])
def test_withdrawn_template_rejects_original_quote_without_host_payment_or_replacement(platform,tmp_path,monkeypatch,interrupt_local_finish):
    with participants(platform,tmp_path) as (provider,assistant,host,template,task):
        trading=assistant.modules[0].trading;entered=threading.Event();release=threading.Event()
        original_establish=trading.establish;original_finish=trading.finish
        permit_finish=threading.Event()
        if not interrupt_local_finish:permit_finish.set()
        def establish(*args,**kwargs):
            entered.set();assert release.wait(15)
            return original_establish(*args,**kwargs)
        def finish(*args,**kwargs):
            if not permit_finish.is_set():raise RuntimeFault('unknown_result','test interrupted local receipt persistence')
            return original_finish(*args,**kwargs)
        monkeypatch.setattr(trading,'establish',establish)
        monkeypatch.setattr(trading,'finish',finish)
        operation=uid()
        try:
            started,args=request(assistant,provider,template,task,operation)
            ref=started['data']['quoteRef'];assert entered.wait(10)
            terms={k:copy.deepcopy(template[k]) for k in ('formation','deliverables','inputs','deadlines','pricing','samples') if k in template}
            terms['pricing'].pop('paymentProviders',None)
            terms['deliverables'][0]['description']='New published delivery contract'
            rpc=LocalClient(provider.config.home)
            drafted=rpc.call('provider.template.draft',{'profile':'service','terms':terms})
            assert drafted['status']=='completed',drafted
            service={'profile':'service','name':'Updated provider','description':'Updated contract for new customers','tags':['开发'],
                     'categoryId':platform.discovery.categories()[0]['id'],'templates':[drafted['data']['template']]}
            preview=rpc.call('provider.preview',{'service':service});assert preview['status']=='completed',preview
            publication={k:preview['data'][k] for k in ('service','contentDigest','expectedRevision')}
            publish_operation=uid();pending=rpc.call('provider.publish',publication,operation_id=publish_operation)
            assert pending['status']=='waiting',pending
            user_decision(provider,pending['data']['confirmationRef'])
            assert rpc.call('provider.publish',publication,operation_id=publish_operation)['status']=='completed'
            release.set()
            if interrupt_local_finish:
                wait_for(lambda:terminal(assistant,ref))
                assert terminal(assistant,ref)['status']=='unknown'
                permit_finish.set()
            wait_for(lambda:terminal(assistant,ref) and terminal(assistant,ref)['status']=='rejected')
            result=terminal(assistant,ref)
            assert result['data']['error']['code']=='not_ready',result
            assert not host.turns
            assert not platform.db.rows('SELECT * FROM delegation_admissions WHERE service=?',provider.identities[0].agent_id)
            assert not platform.db.rows('SELECT * FROM transfers WHERE payee_id=?',provider.account.account['id'])
            repeated=LocalClient(assistant.config.home).call('assistant.delegation.request',args,operation_id=operation)
            assert repeated['status']=='rejected' and repeated['data']==result['data']
        finally:release.set();permit_finish.set()


@pytest.mark.parametrize('fee,expired',[(1,False),(1,True),(0,False)])
def test_late_reservation_recovers_original_prepared_quote_once(platform,tmp_path,monkeypatch,fee,expired):
    import time
    from ioalab_sdk.adapters import stable
    with participants(platform,tmp_path,fee=fee) as (provider,assistant,host,template,task):
        trading=assistant.modules[0].trading;connection=assistant.connection
        original_wait=connection.wait_for;original_api=assistant.account.api
        lost=[];permit=threading.Event()
        def lose_original_admission(predicate,**kwargs):
            value=original_wait(predicate,**kwargs)
            if not lost and isinstance(value,tuple) and len(value)==2 and value[1].get('outcome')=='reserved':
                lost.append(value)
                raise RuntimeFault('unknown_result','Original admission arrived after the waiting job ended')
            return value
        def read_original(*args,**kwargs):
            if args[0]=='GET' and args[1].startswith('/messages/') and not permit.is_set():
                raise httpx.ReadTimeout('Original result query temporarily unavailable')
            return original_api(*args,**kwargs)
        monkeypatch.setattr(connection,'wait_for',lose_original_admission)
        monkeypatch.setattr(assistant.account,'api',read_original)
        operation=uid();started,args=request(assistant,provider,template,task,operation)
        ref=started['data']['quoteRef'];job=assistant.scheduler.reference(trading.identity.agent_id,'assistant.quote',ref)
        wait_for(lambda:terminal(assistant,ref) and terminal(assistant,ref)['status']=='unknown' and not assistant.scheduler.is_executing(job))
        record=assistant.store.checkpoint(trading.namespace,'quote:'+ref)
        assert record['phase']=='prepared' and not host.turns
        original=copy.deepcopy(record['subject'])
        sent=assistant.store.operation(trading.identity.agent_id,'message.send',stable('quote-intent',ref))
        assert sent['response']['ok'] is True
        if expired:monkeypatch.setattr(platform.admissions,'clock',lambda:time.time()+121)
        permit.set()
        def reconciled():
            result=terminal(assistant,ref)
            return result and result['status'] in ('completed','rejected')
        wait_for(reconciled)
        result=terminal(assistant,ref)
        assert result['status']==('rejected' if expired else 'completed'),result
        assert assistant.store.checkpoint(trading.namespace,'quote:'+ref)['subject']==original
        for _ in range(3):trading.recover()
        repeated=LocalClient(assistant.config.home).call('assistant.delegation.request',args,operation_id=operation)
        assert repeated['status']==result['status'] and repeated['data']==result['data']
        intents=platform.db.rows("SELECT * FROM delivery_messages WHERE kind='delegation.intent' AND sender=?",trading.identity.agent_id)
        submissions=platform.db.rows("SELECT * FROM delegation_protocol_calls WHERE interaction=? AND op='request.submit'",lost[0][0]['id'])
        assert len(intents)==1 and len(submissions)==(0 if expired else 1)
        assert len(host.turns)==(0 if expired else 1)
        assert len(platform.db.rows('SELECT * FROM transfers WHERE payee_id=?',provider.account.account['id']))==(1 if fee and not expired else 0)


@pytest.mark.parametrize('case',['valid','committed_fee','pending_send','calling','bad_subject','bad_task','bad_recipient','bad_binding','pending_intent','current_binding','missing_outbox'])
def test_prepared_recovery_requires_original_proof_and_consumes_resume_once(tmp_path,case):
    from a2c.card import request_digest
    from ioalab_sdk.assistant.delegation import DelegationTasks
    from ioalab_sdk.adapters import stable
    from ioalab_sdk.runtime import RuntimeStore
    from ioalab_sdk.runtime.scheduling import Scheduler,JobHandler,JobOutcome
    actor='original-actor';service='svc-'+uid();ref=uid();origin=uid();iid=uid();admission=uid()
    subject={'templateDigest':'template','delegationScopeDigest':'scope'};binding=request_digest(subject)
    relation={'interactionId':iid,'originMessageId':origin,'sender':actor,'recipient':service,'state':'open',
              'interactionType':'delegation','bindingDigest':binding,'deadline':'2099-01-01T00:00:00Z'}
    result={'outcome':'reserved','bindingDigest':binding,'admissionId':admission}
    current={'admissionId':admission,'interactionId':iid,'originMessageId':origin,'client':actor,'service':service,
             'scopeDigest':'scope','bindingDigest':binding,'state':'committed' if case=='committed_fee' else 'held'}
    message={'requestState':'completed','result':result};queries=[];runs=[]
    store=RuntimeStore(tmp_path/'runtime.sqlite','http://local',[actor],owner_id='owner')
    scheduler=Scheduler(store,{'assistant.quote':JobHandler(lambda job:runs.append(job) or JobOutcome('unknown',{}),'v1',False)}).start()
    account=SimpleNamespace(base='http://local',network=None,api=lambda *_:message)
    def query(*args,**kwargs):queries.append((args,kwargs));return current
    connection=SimpleNamespace(call=query,recover_interaction=lambda *args:store.recover_link(actor,relation))
    runtime=SimpleNamespace(store=store,scheduler=scheduler,account=account,connection=connection)
    identity=SimpleNamespace(agent_id=actor);delegation=DelegationTasks(runtime,identity)
    trading=Trading(SimpleNamespace(runtime=runtime,identity=identity,delegation=delegation))
    record={'quoteRef':ref,'taskRef':'task','service':service,'subject':subject,'template':{'templateDigest':'template','formation':'proposal','pricing':{'proposalFee':1}},'phase':'prepared'}
    payload={'messageId':origin,'kind':'delegation.intent','recipient':service,'body':{'formation':'proposal',
        'templateDigest':'template','scopeDigest':'scope','bindingDigest':binding,'request':copy.deepcopy(subject)}}
    store.checkpoint_set(delegation.namespace,'intent:'+origin,{'taskRef':'other' if case=='bad_task' else 'task'})
    if case=='bad_subject':payload['body']['request']['templateDigest']='other'
    if case=='bad_recipient':relation['recipient']='other'
    if case=='bad_binding':result['bindingDigest']='other'
    if case=='pending_intent':message['requestState']='processing'
    if case=='current_binding':current['bindingDigest']='other'
    if case!='missing_outbox':store.prepare(actor,'message.send',payload,stable('quote-intent',ref))
    if case=='pending_send':store.prepare(actor,'message.send',{'messageId':uid(),'kind':'a2c.request','interactionId':iid,'body':{'op':'request.submit'}},uid())
    if case=='calling':
        store.recover_link(actor,relation);record.update(phase='calling',interactionId=iid,admissionId=admission)
    store.checkpoint_set(trading.namespace,'quote:'+ref,record)
    job=scheduler.submit(actor,ref,'assistant.quote',{'quoteRef':ref},operation_id=ref)['reference']
    try:
        wait_for(lambda:scheduler.get(job)['status']=='unknown' and not scheduler.is_executing(job))
        if case=='bad_recipient':
            with pytest.raises(RuntimeFault):trading.recover()
        else:trading.recover()
        if case=='valid':
            wait_for(lambda:len(runs)==2 and scheduler.get(job)['status']=='unknown' and not scheduler.is_executing(job))
            for _ in range(3):trading.recover()
            assert len(runs)==2 and len(queries)==1
            scheduler.close();scheduler=Scheduler(store,{'assistant.quote':JobHandler(lambda job:runs.append(job) or JobOutcome('unknown',{}),'v1',False)}).start()
            runtime.scheduler=scheduler;trading.recover()
            assert scheduler.get(job)['status']=='unknown' and len(runs)==2 and len(queries)==1
        else:
            assert scheduler.get(job)['status']=='unknown' and len(runs)==1
            assert len(queries)==(1 if case in ('committed_fee','current_binding') else 0)
    finally:scheduler.close();store.close()
