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
