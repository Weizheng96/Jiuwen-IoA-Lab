"""Original authenticated market rejection is failed, not unknown execution."""
import copy
from types import SimpleNamespace

import pytest
from a2c.codec import digest,utcnow
from a2c.card import card_digest,request_digest
from a2c.errors import A2CError,CARD_INVALID
from a2c.jws import Signer,sign
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from ioalab_sdk.adapters.endpoint import EndpointSession
from ioalab_sdk.adapters.transport import SessionTransport
from ioalab_sdk.runtime.protocol import RuntimeFault,encode
from ioalab_sdk.runtime.store import RuntimeStore
from conftest import wait_for
from test_resident_delegation import QuoteHost, participants, request, terminal


@pytest.fixture
def original_market(tmp_path):
    store=RuntimeStore(tmp_path/'runtime.sqlite','http://test',['provider','other'],owner_id='owner')
    with store.transaction() as conn:
        for actor,interaction in [('provider','original-interaction'),('other','other-interaction')]:
            conn.execute('INSERT INTO runtime_interactions VALUES(?,?,?,?,?,?,?,?,?)',
                         (interaction+'-origin',interaction,actor,'assistant','inbound','delegation',0,'{}',None))
    runtime=SimpleNamespace(store=store,account=SimpleNamespace())
    identity=SimpleNamespace(agent_id='provider',protocol_id='http://test/provider')
    transport=SessionTransport(runtime,identity,'original-interaction','original-admission','assistant',public=object())
    subject={'requestId':'request-1','provider':identity.protocol_id,'templateDigest':'original-template'}
    transport.request=copy.deepcopy(subject)
    payload={'admissionId':'original-admission','op':'card.endorse','request':subject,
             'body':{'card':{'provider':identity.protocol_id,'templateDigest':subject['templateDigest'],
                              'requestDigest':request_digest(subject),'signatures':{'2':None}}}}
    identity.signer=Signer(identity.protocol_id,Ed25519PrivateKey.generate(),'original-key')
    payload['body']['card']['signatures']['2']=sign(card_digest(payload['body']['card']),identity.signer,'2',utcnow())
    record={'payload':copy.deepcopy(payload),'fingerprint':digest({'payload':payload})}
    store.checkpoint_set(transport.namespace,'control:a2c.market',record)
    operation=transport._identity('a2c.market')
    store.prepare('provider','a2c.market',payload,operation)
    error={'code':'invalid_input','message':'Original market rejected the proposal: cannot run dispute models',
           'recovery':'correct_input'}
    frame={'type':'operation.result','agentId':'provider','operation':'a2c.market','operationId':operation,
           'ok':False,'error':error}
    calls=[]
    def reject(actor,kind,body,**kwargs):
        calls.append((actor,kind,body,kwargs))
        raise RuntimeFault(error['code'],error['message'],error['recovery'])
    runtime.connection=SimpleNamespace(call=reject)
    value=SimpleNamespace(store=store,runtime=runtime,transport=transport,payload=payload,record=record,
                          operation=operation,error=error,frame=frame,calls=calls)
    try:yield value
    finally:store.close()


def test_known_original_market_rejection_becomes_a2c_error_without_resubmission(original_market):
    f=original_market;f.store.complete(f.frame)
    before=f.store.operation('provider','a2c.market',f.operation)
    with pytest.raises(A2CError) as rejected:f.transport._control('a2c.market',f.payload)
    assert rejected.value.code==CARD_INVALID and rejected.value.message=='[invalid_input] '+f.error['message']
    assert f.calls==[] and f.store.operation('provider','a2c.market',f.operation)==before


def test_negative_receipt_received_during_original_call_is_converted(original_market):
    f=original_market
    def reject(*args,**kwargs):
        f.store.complete(f.frame)
        raise RuntimeFault(f.error['code'],f.error['message'],f.error['recovery'])
    f.runtime.connection.call=reject
    with pytest.raises(A2CError,match='cannot run dispute models'):
        f.transport._control('a2c.market',f.payload)


@pytest.mark.parametrize('code',[None,'unknown_result','result_unknown'])
def test_missing_or_unknown_market_receipt_remains_runtime_unknown(original_market,code):
    f=original_market
    if code:
        f.frame['error']['code']=code;f.store.complete(f.frame)
    f.runtime.connection.call=lambda *_args,**_kwargs: (_ for _ in ()).throw(
        RuntimeFault('unknown_result','The original response is unknown','resume_original'))
    assert f.transport._market_rejection() is None
    with pytest.raises(RuntimeFault) as unknown:f.transport._control('a2c.market',f.payload)
    assert unknown.value.code=='unknown_result'


@pytest.mark.parametrize('changed', ['payload','fingerprint','admission','request','method','actor','interaction',
                                    'outcome','malformed-error','current-request','protocol-party',
                                    'frame-actor','frame-operation','frame-id'])
def test_market_rejection_requires_original_outbox_and_binding(original_market,changed):
    f=original_market
    if changed=='outcome':f.frame['outcome']='unknown'
    elif changed=='malformed-error':f.frame['error'].pop('recovery')
    f.store.complete(f.frame)
    if changed=='payload':
        record=copy.deepcopy(f.record);record['payload']['body']['card']['changed']=True
        record['fingerprint']=digest({'payload':record['payload']})
        f.store.checkpoint_set(f.transport.namespace,'control:a2c.market',record)
    elif changed=='fingerprint':
        f.store.checkpoint_set(f.transport.namespace,'control:a2c.market',{**f.record,'fingerprint':'wrong'})
    elif changed in ('admission','request','method'):
        record=copy.deepcopy(f.record)
        if changed=='admission':record['payload']['admissionId']='different-admission'
        elif changed=='request':record['payload']['request']=None
        else:record['payload']['op']='card.close'
        record['fingerprint']=digest({'payload':record['payload']})
        f.store.checkpoint_set(f.transport.namespace,'control:a2c.market',record)
    elif changed=='actor':
        f.transport=SessionTransport(f.runtime,SimpleNamespace(agent_id='other',protocol_id='http://test/other'),'other-interaction',
                                     'original-admission','assistant',public=object())
    elif changed=='interaction':
        # A same-actor but different original namespace must not find this receipt.
        f.transport.namespace+='another-interaction'
    elif changed=='current-request':f.transport.request['requestId']='different-request'
    elif changed=='protocol-party':f.transport.identity.protocol_id='http://test/different-provider'
    elif changed.startswith('frame-'):
        frame=copy.deepcopy(f.frame)
        frame[{'frame-actor':'agentId','frame-operation':'operation','frame-id':'operationId'}[changed]]='different'
        with f.store.transaction() as conn:
            conn.execute('UPDATE runtime_operations SET response=? WHERE actor=? AND kind=? AND id=?',
                         (encode(frame),'provider','a2c.market',f.operation))
    assert f.transport._market_rejection() is None


@pytest.mark.parametrize('code',[CARD_INVALID,'not-arbitrable'])
def test_existing_a2c_market_error_code_and_message_are_preserved(original_market,code):
    f=original_market;f.frame['error']['code']=code;f.store.complete(f.frame)
    with pytest.raises(A2CError) as rejected:f.transport._control('a2c.market',f.payload)
    assert rejected.value.code==code and rejected.value.message==f.error['message']


def test_endpoint_does_not_apply_a_different_original_request_rejection(original_market):
    from ioalab_sdk.adapters.store import ProtocolStore
    from ioalab_sdk.adapters.transport import stable
    import threading
    f=original_market;f.store.complete(f.frame)
    store=ProtocolStore(f.store,'provider','original-interaction')
    body={'op':'request.submit','body':{'request':{**f.payload['request'],'requestId':'different'}}}
    message={'messageId':'original-message','body':body}
    store.put('calls',message['messageId'],{'fingerprint':digest(body),'state':'executing'})
    task=SimpleNamespace(id=stable('task',message['messageId']),state='working')
    endpoint=EndpointSession.__new__(EndpointSession);endpoint.lock=threading.RLock();endpoint.store=store
    endpoint.provider=SimpleNamespace(tasks={task.id:task},template=lambda _: {},transport=f.transport)
    before=copy.deepcopy(store.get('calls',message['messageId']))
    with pytest.raises(RuntimeFault,match='does not match this request'):endpoint.recover(message)
    assert task.state=='working' and store.get('calls',message['messageId'])==before and not f.calls


class UnsupportedModelHost(QuoteHost):
    def run(self,turn):
        self.verifier={**self.verifier,'disputeModels':['unsupported-market-rejection-test-model']}
        return super().run(turn)


@pytest.mark.parametrize('existing_unknown',[False,True])
def test_real_market_rejection_converges_original_quote_without_new_effects(platform,tmp_path,monkeypatch,existing_unknown):
    host=UnsupportedModelHost()
    with participants(platform,tmp_path,agent_host=host,verifier_kind='skill') as (provider,assistant,host,template,task):
        original_recovery=EndpointSession.recover
        if existing_unknown:
            with monkeypatch.context() as legacy:
                legacy.setattr(SessionTransport,'_market_rejection',lambda *_:None)
                legacy.setattr(EndpointSession,'recover',lambda *_:None)
                started,_=request(assistant,provider,template,task)
                reference=started['data']['quoteRef']
                wait_for(lambda:terminal(assistant,reference))
                assert terminal(assistant,reference)['status']=='unknown'
                wait_for(lambda:provider.scheduler.status()['active']==0)
                endpoint=next(iter(provider.modules[0].execution.contexts.values()))
                # No deferred policy or signing may be repeated during recovery.
                monkeypatch.setattr(endpoint.provider,'finish',lambda *_:pytest.fail('Repeated original finish'))
        else:
            started,_=request(assistant,provider,template,task)
            reference=started['data']['quoteRef']
        wait_for(lambda:terminal(assistant,reference) and terminal(assistant,reference)['status']=='rejected')
        result=terminal(assistant,reference)
        assert result['data']['error']['code']==CARD_INVALID
        assert '[invalid_input]' in result['data']['error']['message']
        assert 'unsupported-market-rejection-test-model' in result['data']['error']['message']
        assert len(host.turns)==1
        endpoint=next(iter(provider.modules[0].execution.contexts.values()))
        transport=endpoint.provider.transport
        original=copy.deepcopy(transport._market_rejection())
        assert original and original['payload']['body']['card']['signatures']['2']
        signature=original['payload']['body']['card']['signatures']['2']
        decision=copy.deepcopy(endpoint.store.all('business'))
        task_ids=set(endpoint.provider.tasks)
        assert len(task_ids)==1
        a2c_task=next(iter(endpoint.provider.tasks.values()))
        assert a2c_task.state=='failed' and a2c_task.artifacts[-1]['error']==result['data']['error']
        fee_count=lambda: len(platform.db.rows("SELECT * FROM transfers WHERE purpose='proposal-fee' AND payee_id=?",provider.account.account['id']))
        assert fee_count()==1
        scope=assistant.modules[0].delegation.task(task)
        assert scope
        before_operations=provider.store.db.execute("SELECT COUNT(*) FROM runtime_operations WHERE kind='a2c.market'").fetchone()[0]
        for _ in range(3):provider.modules[0].execution.scan()
        assert original_recovery is EndpointSession.recover
        assert len(host.turns)==1 and set(endpoint.provider.tasks)==task_ids and fee_count()==1
        assert endpoint.store.all('business')==decision and transport._market_rejection()==original
        assert transport._market_rejection()['payload']['body']['card']['signatures']['2']==signature
        assert provider.store.db.execute("SELECT COUNT(*) FROM runtime_operations WHERE kind='a2c.market'").fetchone()[0]==before_operations==1
        assert len(endpoint.provider.store.all('requests'))==1
