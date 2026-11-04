"""User-signed authorization and budget invariants through actual SDK/HTTP/WS."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import copy
from datetime import datetime,timezone,timedelta
from decimal import Decimal
import json
from types import SimpleNamespace
import time
import uuid
from cryptography.hazmat.primitives.asymmetric import ed25519

from a2c.card import card_digest,build_card
from a2c.codec import digest,fmt_time,parse_time
from a2c.jws import Signer,sign
import httpx
import pytest

from ioalab.errors import LabError
from _support import new_user
from test_agent_delivery import Peer
from test_assistant_role import runtime_at,user_decision
from conftest import wait_for
from test_service_publication import service


def uid():return str(uuid.uuid4())


def authorization(local,*,context=None,revision=1,budget='10000.00',fees='3.00',issued=None,days=1):
    context=context or {'schema':'ioalab.scope/1','scopeId':uid(),'platform':local.config.platform,'actor':local.identities[0].signer.id,
        'user':{'id':local.identities[0].user.id,'jwk':local.identities[0].user.jwk},'purpose':'隔离测试委托','categoryIds':None,
        'currency':'CNY','budget':budget,'proposalFeeLimit':fees}
    issued=issued or datetime.now(timezone.utc).replace(microsecond=0)
    grant={'revision':revision,'issuedAt':fmt_time(issued),'expiresAt':fmt_time(issued+timedelta(days=days))}
    payload={'context':context,'grant':grant}
    return {**payload,'proof':sign(digest(payload),local.identities[0].user,'ioa-scope',issued)}


def register(http,local,payload,operation=None):
    return http.post('/api/v1/scopes',headers={'Authorization':'Bearer '+local.account.token},json={'operationId':operation or uid(),**payload})


def status(http,local,scope):
    return http.get('/api/v1/scopes/'+scope,headers={'Authorization':'Bearer '+local.account.token},params={'actor':local.identities[0].signer.id})


def apply(pf,local,kind,payload,operation=None):
    prepared=pf.scopes.prepare(local.account.account['id'],local.identities[0].signer.id,kind,payload)
    with pf.db.tx() as conn:return pf.scopes.apply_in(conn,local.account.account['id'],operation or uid(),prepared)


def test_user_grant_replay_renewal_and_close_never_change_context_or_reopen_from_old_grant(platform,client,protocol_participant,monkeypatch):
    local=protocol_participant;payload=authorization(local);operation=uid()
    first=register(client,local,payload,operation)
    assert first.status_code==200,first.text
    scope=first.json()['scopeDigest']
    assert scope==digest(payload['context']) and first.json()['acceptedRevision']==1
    assert register(client,local,payload,operation).json()==first.json()
    later=parse_time(payload['grant']['expiresAt'])+timedelta(seconds=1)
    monkeypatch.setattr(platform.scopes,'clock',lambda:later.timestamp())
    assert status(client,local,scope).json()['state']=='expired'
    assert register(client,local,payload,operation).json()==first.json()  # Historical receipt, no new authority.
    renewed=authorization(local,context=payload['context'],revision=2,issued=later)
    result=register(client,local,renewed)
    assert result.status_code==200 and result.json()['scopeDigest']==scope,result.text
    assert result.json()['revision']==2 and result.json()['spent']=='0.00'
    closed=client.post('/api/v1/scopes/'+scope+'/close',headers={'Authorization':'Bearer '+local.account.token},json={'actor':local.identities[0].signer.id,'operationId':uid()})
    assert closed.json()['state']=='closed'
    assert register(client,local,payload).status_code==200
    assert status(client,local,scope).json()['state']=='closed'
    assert register(client,local,authorization(local,context=payload['context'],revision=3,issued=later)).status_code==409
    restarted=register(client,local,authorization(local,context=payload['context'],revision=4,issued=later))
    assert restarted.status_code==200 and restarted.json()['state']=='active'
    assert restarted.json()['scopeDigest']==scope and restarted.json()['spent']=='0.00'


def test_scope_authentication_signature_time_and_original_parameters_are_enforced(platform,client,protocol_participant):
    local=protocol_participant;payload=authorization(local);operation=uid()
    assert client.post('/api/v1/scopes',json={'operationId':operation,**payload}).status_code==401
    stranger=new_user(platform)
    assert client.post('/api/v1/scopes',headers={'Authorization':'Bearer '+platform.auth.bearer(stranger['id'],restricted=True)},json={'operationId':operation,**payload}).status_code==403
    bad=copy.deepcopy(payload);bad['context']['budget']='999999.00'
    assert register(client,local,bad).status_code==400
    attacker=Signer('not-the-owner',ed25519.Ed25519PrivateKey.generate())
    bad=copy.deepcopy(payload);bad['context']['user']={'id':attacker.id,'jwk':attacker.jwk}
    bad['proof']=sign(digest({key:bad[key] for key in ('context','grant')}),attacker,'ioa-scope',parse_time(bad['grant']['issuedAt']))
    assert register(client,local,bad).status_code==403
    assert register(client,local,authorization(local,issued=datetime.now(timezone.utc)-timedelta(minutes=20))).status_code==200
    assert register(client,local,authorization(local,issued=datetime.now(timezone.utc)-timedelta(days=8))).status_code==409
    assert register(client,local,authorization(local,issued=datetime.now(timezone.utc)+timedelta(minutes=2))).status_code==409
    assert register(client,local,payload,operation).status_code==200
    changed=authorization(local,context={**payload['context'],'purpose':'另一项委托'})
    assert register(client,local,changed,operation).status_code==409
    assert register(client,local,changed).status_code==409
    assert len(platform.db.rows('SELECT * FROM delegation_scopes WHERE scope_id=?',payload['context']['scopeId']))==1


def test_concurrent_registration_and_grant_revision_have_one_authoritative_result(platform,client,protocol_participant):
    local=protocol_participant;payload=authorization(local);operation=uid()
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses=list(pool.map(lambda _:register(client,local,payload,operation),range(4)))
    assert all(response.status_code==200 and response.json()==responses[0].json() for response in responses)
    scope=responses[0].json()['scopeDigest']
    renewal=authorization(local,context=payload['context'],revision=2)
    alternative=authorization(local,context=payload['context'],revision=2,days=2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda data:register(client,local,data),(renewal,alternative)))
    assert sorted(response.status_code for response in results)==[200,409]
    assert len(platform.db.rows('SELECT * FROM delegation_grants WHERE scope=?',scope))==2
    assert status(client,local,scope).json()['revision']==2


def test_ws_scope_receipt_uses_same_http_recovery_and_rejects_other_owner(platform,client,protocol_participant):
    local=protocol_participant;payload=authorization(local);operation=uid()
    person={'actor':local.identities[0].signer.id,'token':local.account.token}
    with closing(Peer(client,person)) as peer:
        result=peer.call('scope.register',payload,operation)
        assert peer.call('operation.get',{'operation':'scope.register','operationId':operation})==result
        recovered=client.get('/api/v1/operations/'+operation,headers={'Authorization':'Bearer '+local.account.token},
                             params={'agentId':local.identities[0].signer.id,'operation':'scope.register'})
        assert recovered.status_code==200 and recovered.json()==result
        assert peer.call('scope.status',{'scopeDigest':result['scopeDigest']})['revision']==1


def selected(platform,client,local,service,budget='10000.00'):
    payload=authorization(local,budget=budget)
    response=register(client,local,payload);response.raise_for_status()
    scope=response.json()['scopeDigest']
    from test_service_publication import publication,submit,draft
    from a2c.slots import sign_template
    from a2c.codec import utcnow
    from ioalab_sdk.templates import template_from_definition
    account,identity=service
    raw=template_from_definition({'formation':'countersign','deliverable':{'name':'result'},'pricing':{'price':'9'},
        'verifier':{'id':'scope_result@1.0','source':"def verify(ctx):\n    return {'status':'pass'}\n",'kind':'script'}},
        packages=SimpleNamespace(put=lambda **args:account.api('POST','/provider/packages',{'verifierId':args['verifier_id'],'source':args['source'],'reads':args.get('reads',[])})),
        payment_provider=platform.payment_provider)
    signed=sign_template({**raw,'provider':identity.protocol_id},identity.signer,'provider',utcnow())
    existing=account.http.get(identity.protocol_id+'/publication')
    if existing.status_code==200:
        template=existing.json()['templates'][0]
    else:
        response=submit(account,identity,publication(platform,identity,template=signed));response.raise_for_status()
        template=response.json()['templates'][0]
    card=build_card(template,card_id=uid(),client={'id':local.identities[0].agent_id,'jwk':local.identities[0].signer.jwk},
        user={'id':local.identities[0].user.id,'jwk':local.identities[0].user.jwk},signers={'slot1':'user','slot7':'user'},
        spec={'result':{}},inputs={},delegation_scope_digest=scope,payment_provider=platform.payment_provider)
    cd=card_digest(card)
    selection={'scopeDigest':scope,'card':card,'proof':sign(digest({'scopeDigest':scope,'cardDigest':cd}),local.identities[0].user,'ioa-contract-selection',datetime.now(timezone.utc))}
    return payload,template,card,selection


def test_select_reserves_full_price_before_slot1_and_never_selects_two_cards(platform,client,protocol_participant,service):
    local=protocol_participant
    _,template,card,selection=selected(platform,client,local,service)
    before=platform.wallets.balance(local.account.account['id'])
    with closing(Peer(client,{'actor':local.identities[0].signer.id,'token':local.account.token})) as peer:
        result=peer.call('scope.select',selection)
    assert result['selectedCardDigest']==card_digest(card) and Decimal(result['reservedRemaining'])==Decimal(str(card['derived']['total']))
    assert not card['signatures']['1'] and platform.wallets.balance(local.account.account['id'])==before
    assert apply(platform,local,'scope.select',selection)['selectedCardDigest']==result['selectedCardDigest']
    changed=copy.deepcopy(card);changed['cardId']=uid()
    other={'scopeDigest':selection['scopeDigest'],'card':changed,
           'proof':sign(digest({'scopeDigest':selection['scopeDigest'],'cardDigest':card_digest(changed)}),local.identities[0].user,'ioa-contract-selection',datetime.now(timezone.utc))}
    with pytest.raises(LabError,match='another contract'):apply(platform,local,'scope.select',other)


def test_selection_rechecks_bound_key_category_and_deadline_before_commit(platform,client,protocol_participant,service,monkeypatch):
    local=protocol_participant
    payload,_,card,selection=selected(platform,client,local,service)
    prepared=platform.scopes.prepare(local.account.account['id'],local.identities[0].signer.id,'scope.select',selection)
    with monkeypatch.context() as patch:
        patch.setattr(platform.identities,'user_key',lambda owner,actor:Signer('other',ed25519.Ed25519PrivateKey.generate()).jwk)
        with pytest.raises(LabError,match='bound identity'):
            with platform.db.tx() as conn:platform.scopes.apply_in(conn,local.account.account['id'],uid(),prepared)
    until=parse_time(payload['grant']['expiresAt']).timestamp()
    late={**selection,'proof':sign(digest({'scopeDigest':selection['scopeDigest'],'cardDigest':card_digest(card)}),
        local.identities[0].user,'ioa-contract-selection',datetime.fromtimestamp(until-2,timezone.utc))}
    prepared=platform.scopes.prepare(local.account.account['id'],local.identities[0].signer.id,'scope.select',late)
    clock=[until-1];category=platform.scopes.category
    def expires_during_lookup(conn,provider):
        result=category(conn,provider);clock[0]=until+1;return result
    with monkeypatch.context() as patch:
        patch.setattr(platform.scopes,'clock',lambda:clock[0])
        patch.setattr(platform.scopes,'category',expires_during_lookup)
        with pytest.raises(LabError,match='renewal'):
            with platform.db.tx() as conn:platform.scopes.apply_in(conn,local.account.account['id'],uid(),prepared)
    assert status(client,local,selection['scopeDigest']).json()['selectedCardDigest'] is None
    restricted=copy.deepcopy(payload['context']);restricted.update(scopeId=uid(),categoryIds=[])
    response=register(client,local,authorization(local,context=restricted));response.raise_for_status();scope=response.json()['scopeDigest']
    another=copy.deepcopy(card);another['delegationScopeDigest']=scope
    selection={'scopeDigest':scope,'card':another,'proof':sign(digest({'scopeDigest':scope,'cardDigest':card_digest(another)}),local.identities[0].user,'ioa-contract-selection',datetime.now(timezone.utc))}
    with pytest.raises(LabError,match='category'):apply(platform,local,'scope.select',selection)


def payment(local,scope,ref,purpose,amount,*,period=None):
    return SimpleNamespace(payment_id=uid(),fingerprint=uid(),scope_digest=scope,payer=local.account.account['id'],
        currency='CNY',client=json.dumps({'id':local.identities[0].signer.id,'jwk':local.identities[0].signer.jwk}),provider='test-provider',
        ref=ref,purpose=purpose,amount=Decimal(amount),period=period)


def test_scope_budget_atomic_fee_competition_and_separate_installments(platform,client,protocol_participant,service):
    local=protocol_participant
    payload=authorization(local,budget='10.00',fees='1.00')
    response=register(client,local,payload);response.raise_for_status();scope=response.json()['scopeDigest']
    # Explicit test-double category view; this test covers budget atomicity, not catalog classification.
    original_category=platform.scopes.category
    platform.scopes.category=lambda conn,provider:'test-category'
    try:
        fees=[payment(local,scope,uid(),'proposal-fee','1.00') for _ in range(2)]
        def charge(value):
            try:
                with platform.db.tx() as conn:platform.scopes.record_payment_in(conn,local.account.account['id'],local.identities[0].signer.id,scope,value)
                return 'paid'
            except LabError:return 'rejected'
        with ThreadPoolExecutor(max_workers=2) as pool:assert sorted(pool.map(charge,fees))==['paid','rejected']
        assert status(client,local,scope).json()['spent']=='1.00'
    finally:platform.scopes.category=original_category
    _,_,card,selection=selected(platform,client,local,service)
    result=apply(platform,local,'scope.select',selection);scope=result['scopeDigest'];cd=result['selectedCardDigest']
    half=Decimal(result['contractTotal'])/2
    half=half.quantize(Decimal('.01'));remaining=Decimal(result['contractTotal'])-half
    for period,value in ((1,half),(2,remaining)):
        transfer=payment(local,scope,cd,'installment',str(value),period=period)
        with platform.db.tx() as conn:
            platform.scopes.record_payment_in(conn,local.account.account['id'],local.identities[0].signer.id,scope,transfer)
            platform.scopes.record_payment_in(conn,local.account.account['id'],local.identities[0].signer.id,scope,transfer)
    state=status(client,local,scope).json()
    assert state['contractPaid']==state['contractTotal'] and Decimal(state['reservedRemaining'])==0
    with pytest.raises(LabError,match='reserved complete'):
        with platform.db.tx() as conn:platform.scopes.record_payment_in(conn,local.account.account['id'],local.identities[0].signer.id,scope,payment(local,scope,cd,'installment','0.01',period=3))
    assert status(client,local,scope).json()['spent']==state['spent']


def test_select_and_new_fee_cannot_both_consume_the_last_budget(platform,client,protocol_participant,service):
    local=protocol_participant
    _,_,prototype,_=selected(platform,client,local,service)
    total=f"{Decimal(str(prototype['derived']['total'])):.2f}"
    payload=authorization(local,budget=total,fees='1.00')
    registered=register(client,local,payload);registered.raise_for_status();scope=registered.json()['scopeDigest']
    card=copy.deepcopy(prototype);card['cardId']=uid();card['delegationScopeDigest']=scope
    selection={'scopeDigest':scope,'card':card,'proof':sign(digest({'scopeDigest':scope,'cardDigest':card_digest(card)}),local.identities[0].user,'ioa-contract-selection',datetime.now(timezone.utc))}
    prepared=platform.scopes.prepare(local.account.account['id'],local.identities[0].signer.id,'scope.select',selection)
    fee=payment(local,scope,uid(),'proposal-fee','1.00');fee.provider=card['provider']
    def attempt(which):
        try:
            with platform.db.tx() as conn:
                if which=='select':platform.scopes.apply_in(conn,local.account.account['id'],uid(),prepared)
                else:platform.scopes.record_payment_in(conn,local.account.account['id'],local.identities[0].signer.id,scope,fee)
            return 'accepted'
        except LabError:return 'rejected'
    with ThreadPoolExecutor(max_workers=2) as pool:assert sorted(pool.map(attempt,('select','fee')))==['accepted','rejected']
    before=status(client,local,scope).json()
    assert Decimal(before['proposalFeesPaid'])+Decimal(before['contractTotal'])<=Decimal(total)
    renewed=register(client,local,authorization(local,context=payload['context'],revision=2))
    assert renewed.status_code==200
    after=renewed.json()
    assert (after['proposalFeesPaid'],after['contractTotal'],after['selectedCardDigest'])==(before['proposalFeesPaid'],before['contractTotal'],before['selectedCardDigest'])


def test_real_ap2_commit_and_scope_write_roll_back_in_the_same_transaction(platform,client,tmp_path,monkeypatch):
    from _resident_payment import signed_unpaid
    with signed_unpaid(platform,tmp_path,monkeypatch) as (provider,local,cd,ap2):
        scope=platform.psp.prepare(ap2).scope_digest
        prepared=platform.psp.prepare(ap2);before=platform.wallets.balance(local.account.account['id'])
        assert prepared.scope_digest==scope and prepared.amount>0
        platform.db.run("CREATE TRIGGER scope_receipt_failure BEFORE INSERT ON delegation_scope_payments BEGIN SELECT RAISE(ABORT,'scope receipt unavailable'); END")
        try:
            with pytest.raises(Exception,match='scope receipt unavailable'):
                with platform.db.tx() as conn:
                    platform.psp.commit(conn,prepared)
                    platform.scopes.record_payment_in(conn,local.account.account['id'],local.identities[0].signer.id,scope,prepared)
            assert platform.wallets.balance(local.account.account['id'])==before
            assert platform.db.one('SELECT 1 FROM payment_records WHERE ref=?',cd) is None
            assert status(client,local,scope).json()['contractPaid']=='0.00'
        finally:platform.db.run('DROP TRIGGER scope_receipt_failure')
        with platform.db.tx() as conn:
            receipt,created=platform.psp.commit(conn,prepared)
            platform.scopes.record_payment_in(conn,local.account.account['id'],local.identities[0].signer.id,scope,prepared)
        assert created and status(client,local,scope).json()['contractPaid']==str(prepared.amount)
        assert platform.wallets.balance(local.account.account['id'])==before-prepared.amount
        with platform.db.tx() as conn:
            replay,created=platform.psp.commit(conn,prepared)
            platform.scopes.record_payment_in(conn,local.account.account['id'],local.identities[0].signer.id,scope,prepared)
        assert not created and replay.body==receipt.body
        assert len(platform.db.rows('SELECT * FROM transfers WHERE ref=?',cd))==1

def test_resident_authorization_needs_browser_and_recovers_lost_response_without_new_grant(platform,tmp_path,monkeypatch):
    with runtime_at(platform,tmp_path/'resident') as (runtime,client,module,user):
        task=client.call('assistant.delegation.prepare',{'purpose':'制作业务成果','budget':'500','proposalFeeLimit':'5','categoryIds':['cat_dev']})
        assert task['status']=='completed'
        reference=task['data']['taskRef'];operation=uid();args={'taskRef':reference}
        assert client.call('assistant.delegation.authorize',{**args,'confirmed':True},operation_id=operation)['status']=='rejected'
        pending=client.call('assistant.delegation.authorize',args,operation_id=operation)
        assert pending['status']=='waiting'
        assert platform.db.one('SELECT 1 FROM delegation_scopes WHERE scope_id=?',reference) is None
        confirmation=pending['data']['confirmationRef'];user_decision(runtime,confirmation)
        api=runtime.account.api;sent=[]
        def drop(method,path,body=None):
            result=api(method,path,body)
            if method=='POST' and path=='/scopes':sent.append(body);raise httpx.ReadTimeout('Scope accepted; response lost')
            return result
        with monkeypatch.context() as patch:
            patch.setattr(runtime.account,'api',drop)
            assert client.call('assistant.delegation.authorize',{**args,'confirmationRef':confirmation},operation_id=operation)['status']=='unknown'
        with runtime.store.transaction() as conn:conn.execute('UPDATE runtime_confirmations SET expires=0 WHERE id=?',(confirmation,))
        result=client.call('assistant.delegation.authorize',args,operation_id=operation)
        assert result['status']=='completed' and result['data']['authorization']['state']=='active',result
        assert len(sent)==1 and result['data']['scopeDigest']==task['data']['scopeDigest']
        assert len(platform.db.rows('SELECT * FROM delegation_grants WHERE scope=?',result['data']['scopeDigest']))==1
        renewal=uid();waiting=client.call('assistant.delegation.renew',args,operation_id=renewal)
        assert waiting['status']=='waiting'
        user_decision(runtime,waiting['data']['confirmationRef'])
        renewed=client.call('assistant.delegation.renew',args,operation_id=renewal)
        assert renewed['data']['authorization']['revision']==2 and renewed['data']['scopeDigest']==task['data']['scopeDigest']
        closed=client.call('assistant.delegation.close',args)
        assert closed['data']['authorization']['state']=='closed'
        replay=client.call('assistant.delegation.authorize',args,operation_id=operation)
        assert replay['data']['authorization']['state']=='closed'
        again=uid();pending=client.call('assistant.delegation.renew',args,operation_id=again)
        assert pending['status']=='waiting' and pending['data']['preview']['title']=='重新授权原委托'
        user_decision(runtime,pending['data']['confirmationRef'])
        again=client.call('assistant.delegation.renew',args,operation_id=again)
        assert again['data']['authorization']['revision']==4 and again['data']['scopeDigest']==task['data']['scopeDigest']
        assert platform.wallets.balance(user['id'])==100  # No payment or login-credit loop.
