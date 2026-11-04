"""Actual Core/HTTP/WS/A2C processing; host and owner actions are explicit doubles."""
from contextlib import contextmanager
import json
import threading

import pytest
import uvicorn
from a2c import objects
from a2c.codec import parse_time,digest_bytes
from a2c.jws import header_of,verify
from ioalab.config import Settings
from ioalab.app import Platform
from ioalab.web.app import create_app
from ioalab.delegation.arbitration_cases import ArbitrationCases
from ioalab_sdk.runtime.protocol import RuntimeFault
from test_arbitration_cases import actor,owner,submit,read,due
from test_arbitration_evidence import preview,formed,delivered,DeliverHost
from test_resident_delegation import participants,request,uid
from test_resident_contracts import candidate
from conftest import free_port,wait_for


@contextmanager
def core(settings):
    platform=Platform(settings)
    server=uvicorn.Server(uvicorn.Config(create_app(platform),host='127.0.0.1',port=settings.port,log_level='critical'))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    try:
        wait_for(lambda:server.started)
        yield platform
    finally:
        server.should_exit=True;thread.join(12)
        assert not thread.is_alive(),'Actual Core must finish its writers before ownership is reused'
        assert platform._closed


@pytest.fixture
def platform(tmp_path):
    with core(Settings(port=free_port(),data=tmp_path/'core',autoseed=True)) as value:
        yield value


def process(platform):return ArbitrationCases.process_one(platform.arbitration_cases)
def snapshot(platform,case_ref):return platform.db.one('SELECT * FROM arbitration_cases WHERE id=?',case_ref)


@pytest.mark.parametrize('formation',['proposal','countersign'])
def test_automatic_deadline_ruling_is_one_atomic_document_and_two_notices_without_payment_or_release(platform,tmp_path,monkeypatch,formation):
    with participants(platform,tmp_path,formation=formation,fee=0) as (p,a,host,t,task):
        ref,admission=formed(a,candidate(a,p,t,task));money=platform.db.rows('SELECT * FROM payment_records');host_turns=len(host.turns)
        receipt=submit(a,due(platform,a,admission,monkeypatch))
        wait_for(lambda:snapshot(platform,receipt['caseRef'])['state']=='ruled')
        value=read(a,'/arbitrations',agentId=actor(a),admissionId=admission)
        ruling=value['ruling'];sig=header_of(ruling['signature'])
        verify(ruling['signature'],objects.object_digest(ruling),platform.market_signer.jwk,slot='market',signer=platform.market.id,expect_kid=True)
        assert sig['a2c-ts']==ruling['issuedAt'] and ruling['liable']=='provider'
        assert ruling['cardDigest']==ref and ruling['caseRef']==receipt['caseRef']
        assert ruling['basisDigest']==value['assessment']['basisDigest']
        assert len(value['attempts'])==1 and value['attempts'][0]['state']=='evaluated'
        assert value['evaluation']['rulingKind']=='deadline'
        assert platform.market.market.store.get('rulings',ref)==ruling
        assert platform.market.market.store.get('ledger',ref)['outcome']=='arbitrated'
        notices=platform.db.rows("SELECT * FROM delivery_messages WHERE interaction=? AND kind='contract.ruling'",value['assessment']['evidence']['interactionId'])
        assert {n['recipient'] for n in notices}=={actor(a),actor(p)} and len(notices)==2
        assert platform.db.one('SELECT state FROM delegation_admissions WHERE id=?',admission)['state']=='committed'
        assert platform.db.one('SELECT state FROM delivery_interactions WHERE id=?',value['assessment']['evidence']['interactionId'])['state']=='open'
        assert platform.db.rows('SELECT * FROM payment_records')==money
        assert submit(a,due_payload(value))['caseRef']==receipt['caseRef']
        client,_=a.modules[0].fulfillment.client(ref)
        card=value['assessment']['evidence']['card']
        with pytest.raises(RuntimeFault) as late:
            with client.transport.operation(uid()):client.transport.send(card['provider'],'work.start',{'card':card},files={})
        assert late.value.code=='closed'
        with pytest.raises(RuntimeFault) as close:
            p.connection.call(actor(p),'a2c.market',{'admissionId':admission,'op':'card.close','body':{'card':card}},operation_id=uid())
        assert close.value.code=='closed'
        assert len(host.turns)==host_turns


def due_payload(value):
    return {'admissionId':value['admissionId'],'ground':'start-overdue','basisDigest':value['assessment']['basisDigest']}


def failed_case(platform,tmp_path,monkeypatch):
    return participants(platform,tmp_path,fee=0,agent_host=DeliverHost(),
        verifier_source="def verify(ctx):\n    return {'status':'fail'}\n")


@pytest.mark.parametrize('fault',['returned-fail-with-error','exception-before-return','commit-rollback'])
def test_actual_recompute_result_is_saved_before_finality_and_never_replayed(platform,tmp_path,monkeypatch,fault):
    monkeypatch.setattr(platform.arbitration_cases,'process_one',lambda:False)
    with failed_case(platform,tmp_path,monkeypatch) as (p,a,host,t,task):
        ref,admission=formed(a,candidate(a,p,t,task));delivered(a,ref)
        client,_=a.modules[0].fulfillment.client(ref)
        with client.transport.operation(uid()):assert client.accept(ref,1).status=='fail'
        initial=preview(a,admission,'acceptance-failed',1)
        body=b'actual delivered bytes'
        material=platform.files.put(owner(a),actor(a),uid(),'result.txt',digest_bytes(body),body)
        receipt=submit(a,{'admissionId':admission,'ground':'acceptance-failed','period':1,
            'basisDigest':initial['basisDigest'],'materials':{'deliveryFiles':{'result.txt':material}}})
        case_ref=receipt['caseRef'];calls=[];original=platform.market.market.recompute
        def recompute(*args,**kwargs):
            calls.append(True)
            assert not platform.db.conn.in_transaction
            if fault=='exception-before-return':raise OSError('test-private-token-must-not-be-persisted')
            actual=original(*args,**kwargs)
            assert actual.status=='fail'
            if fault=='returned-fail-with-error':actual.runs[0].outcome.error='A2C already classified this program failure'
            return actual
        monkeypatch.setattr(platform.market.market,'recompute',recompute)
        if fault=='commit-rollback':
            notify=platform.arbitration_cases.notify_in
            def interrupted(*args):raise OSError('isolated failure after original record_ruling, before commit')
            monkeypatch.setattr(platform.arbitration_cases,'notify_in',interrupted)
            with pytest.raises(OSError):process(platform)
            current=snapshot(platform,case_ref)
            assert current['state']=='evaluating' and current['result'] and not current['ruling']
            assert platform.market.market.store.get('ledger',ref) is None
            assert platform.market.market.store.get('rulings',ref) is None
            assert not platform.db.rows("SELECT * FROM delivery_messages WHERE kind='contract.ruling'")
            monkeypatch.setattr(platform.arbitration_cases,'notify_in',notify)
        assert process(platform)
        current=snapshot(platform,case_ref)
        assert len(calls)==1
        if fault=='exception-before-return':
            assert current['state']=='unknown' and current['result'] is None and current['ruling'] is None
            assert not process(platform)
            attempt=platform.db.one('SELECT * FROM arbitration_attempts WHERE case_id=?',case_ref)
            assert attempt['state']=='unknown' and attempt['error']=='OSError'
            assert 'test-private-token' not in json.dumps(attempt)
            assert platform.market.market.store.get('ledger',ref) is None
        else:
            assert current['state']=='ruled' and json.loads(current['ruling'])['liable']=='provider'
            assert json.loads(current['result'])['evidence']['status']=='fail'
            assert not process(platform)
        assert platform.db.one('SELECT state FROM delegation_admissions WHERE id=?',admission)['state']=='committed'
        assert not platform.db.rows('SELECT * FROM payment_records WHERE ref=?',ref)


def short_terms(reference):
    return {'formation':'countersign','deliverables':[{'name':'result','description':'result','specSchema':{}}],
        'inputs':[],'deadlines':{'respondWithin':'PT10M','startWithin':'PT1S','deliverWithin':{'result':'PT30M'},'acceptWithin':{'result':'PT10M'}},
        'pricing':{'currency':'CNY','depositFormula':'0','paymentWeights':{'result':1},'priceFactors':{'result':{}},'priceFormula':{'result':'9'}},
        'acceptance':{'result':{'verifiers':{'script':reference},'notCovered':[]}},'authorizableSteps':[],'samples':[]}


def test_original_signed_accepted_price_can_be_paid_after_ruling_without_new_fulfillment(platform,tmp_path,monkeypatch):
    with participants(platform,tmp_path,fee=0,agent_host=DeliverHost()) as (p,a,host,t,task):
        ref,admission=formed(a,candidate(a,p,t,task));delivered(a,ref)
        client,_=a.modules[0].fulfillment.client(ref);pay=client._pay
        def interrupted(*args,**kwargs):raise RuntimeFault('unknown_result','Isolated interruption before installment payment')
        monkeypatch.setattr(client,'_pay',interrupted)
        with pytest.raises(RuntimeFault):
            with client.transport.operation(uid()):client.accept(ref,1)
        card=client._record(ref)['card'];assert card['signatures']['7']
        current=preview(p,admission,'payment-overdue',1)
        monkeypatch.setattr(platform.arbitration_evidence,'clock',lambda:parse_time(current['deadline']).timestamp()+1)
        current=preview(p,admission,'payment-overdue',1);assert current['eligible']
        receipt=submit(p,{'admissionId':admission,'ground':'payment-overdue','period':1,'basisDigest':current['basisDigest']})
        wait_for(lambda:snapshot(platform,receipt['caseRef'])['state']=='ruled')
        assert json.loads(snapshot(platform,receipt['caseRef'])['ruling'])['liable']=='client'
        assert not platform.db.rows('SELECT * FROM payment_records WHERE ref=?',ref)
        with client.transport.operation(uid()):voucher=pay('installment',client.template(card['templateDigest']),card=card,period=1)
        assert voucher['purpose']=='installment'
        assert len(platform.db.rows('SELECT * FROM payment_records WHERE ref=?',ref))==1
        with pytest.raises(RuntimeFault) as late:
            with client.transport.operation(uid()):client.transport.send(card['provider'],'acceptance.notify',{'card':card})
        assert late.value.code=='closed'
        assert snapshot(platform,receipt['caseRef'])['execution']=='pending'
        assert platform.db.one('SELECT state FROM delegation_admissions WHERE id=?',admission)['state']=='committed'


def test_request_only_ruling_has_one_reputation_result_and_no_fabricated_contract_ledger(platform,tmp_path,monkeypatch):
    with participants(platform,tmp_path,fee=1) as (p,a,host,t,task):
        host.wait=threading.Event()
        try:
            request(a,p,t,task);wait_for(lambda:len(host.turns)==1)
            admission=platform.db.one('SELECT id FROM delegation_admissions WHERE client=?',actor(a))['id']
            current=preview(a,admission,'proposal-overdue')
            monkeypatch.setattr(platform.arbitration_evidence,'clock',lambda:parse_time(current['deadline']).timestamp()+1)
            current=preview(a,admission,'proposal-overdue');assert current['eligible']
            receipt=submit(a,{'admissionId':admission,'ground':'proposal-overdue','basisDigest':current['basisDigest']})
            wait_for(lambda:snapshot(platform,receipt['caseRef'])['state']=='ruled')
            ruling=json.loads(snapshot(platform,receipt['caseRef'])['ruling'])
            assert ruling['liable']=='provider' and 'cardDigest' not in ruling
            assert ruling['subject']['kind']=='proposal-request'
            assert not platform.market.market.store.get('ledger',ruling['subject']['digest'])
            before=platform.market.reputation(p.identities[0].protocol_id)
            assert before['completed']==1 and before['successRate']==0
            assert submit(a,{'admissionId':admission,'ground':'proposal-overdue','basisDigest':current['basisDigest']})['caseRef']==receipt['caseRef']
            after=platform.market.reputation(p.identities[0].protocol_id)
            assert after['completed']==1 and after['successRate']==0
            assert len(platform.db.rows('SELECT * FROM payment_records WHERE ref=?',ruling['subject']['digest']))==1
        finally:host.wait.set()


@pytest.mark.parametrize('known',[False,True])
def test_actual_previous_core_ends_before_unknown_or_known_result_recovery(tmp_path,monkeypatch,known):
    settings=Settings(port=free_port(),data=tmp_path/'core',autoseed=True)
    with core(settings) as first:
        monkeypatch.setattr(first.arbitration_cases,'process_one',lambda:False)
        with participants(first,tmp_path,formation='countersign',fee=0,template_terms=short_terms) as (p,a,host,t,task):
            ref,admission=formed(a,candidate(a,p,t,task))
            wait_for(lambda:preview(a,admission,'start-overdue')['eligible'])
            evidence=preview(a,admission,'start-overdue')
            receipt=submit(a,{'admissionId':admission,'ground':'start-overdue','basisDigest':evidence['basisDigest']})
            row,attempt=first.arbitration_cases._claim()
            assert row['id']==receipt['caseRef'] and attempt
            if known:
                fresh=first.arbitration_cases._fresh(row)
                result=first.market.evaluate_case(json.loads(row['basis']),fresh.route,{})
                first.arbitration_cases._save_result(row,attempt,result)
            saved_owner,saved_actor=owner(a),actor(a)
    # The original HTTP thread, writers and OS ownership have actually ended.
    assert first._closed
    with core(settings) as restarted:
        calls=[]
        def no_recompute(*args):calls.append(True);raise AssertionError('Recovery must never repeat evaluation')
        monkeypatch.setattr(restarted.market,'evaluate_case',no_recompute)
        if known:wait_for(lambda:snapshot(restarted,receipt['caseRef'])['state']=='ruled')
        value=restarted.arbitration_cases.get(saved_owner,saved_actor,receipt['caseRef'])
        assert value['state']==('ruled' if known else 'unknown') and len(value['attempts'])==1
        assert value['attempts'][0]['state']==('evaluated' if known else 'unknown')
        assert not calls
        ledger=restarted.market.market.store.get('ledger',ref)
        assert (ledger is not None) is known
