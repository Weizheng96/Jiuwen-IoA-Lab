"""Public administrator review, original A2C facts and shared SDK evidence checks."""
import copy
from datetime import datetime,timedelta
import json

import httpx
import pytest
from a2c.codec import utcnow,parse_time
from ioalab_sdk.adapters.arbitration import verify_case,read_case
from ioalab_sdk.runtime.protocol import RuntimeFault
from test_arbitration_processing import platform,process,snapshot,short_terms
from test_arbitration_cases import actor,owner,submit,read
from test_arbitration_evidence import preview,formed,delivered,DeliverHost
from test_resident_delegation import participants,uid
from test_resident_contracts import candidate
from conftest import wait_for


def api(platform,method,path,body=None,*,operator='admin'):
    return httpx.request(method,platform.s.base_url+'/api/admin/arbitrations/'+path,json=body,
        headers={'Authorization':'Bearer '+platform.auth.token(operator)},trust_env=False)


def view(platform,case_ref):
    response=api(platform,'GET',case_ref);assert response.status_code==200,response.text
    return response.json()


def begin(platform,a,admission,ground='start-overdue',period=None):
    wait_for(lambda:preview(a,admission,ground,period)['eligible'])
    current=preview(a,admission,ground,period)
    return submit(a,{'admissionId':admission,'ground':ground,'basisDigest':current['basisDigest'],
        **({'period':period} if period is not None else {})})['caseRef']


def unknown(platform,monkeypatch):
    original=platform.market.evaluate_case
    def interrupted(*args):raise OSError('private-installation-or-model-canary')
    monkeypatch.setattr(platform.market,'evaluate_case',interrupted)
    assert process(platform)
    monkeypatch.setattr(platform.market,'evaluate_case',original)


def body(value,*,liable='none',operation=None):
    return {'operationId':operation or uid(),'expectedEvidenceDigest':value['review']['expectedEvidenceDigest'],
        'liable':liable,'reason':'独立管理员依据本案原记录确认结论'}


def test_explicit_same_input_reevaluation_preserves_unknown_attempt_and_recovers_original_action(platform,tmp_path,monkeypatch):
    monkeypatch.setattr(platform.arbitration_cases,'process_one',lambda:False)
    with participants(platform,tmp_path,formation='countersign',fee=0,template_terms=short_terms) as (p,a,host,t,task):
        ref,admission=formed(a,candidate(a,p,t,task));case=begin(platform,a,admission)
        unknown(platform,monkeypatch);current=view(platform,case)
        assert current['state']=='unknown' and current['evaluation'] is None
        assert set(current['review']['availableActions'])=={'review','reevaluate'}
        args={key:value for key,value in body(current).items() if key!='liable'}
        started=api(platform,'POST',case+'/reevaluate',args);assert started.status_code==200,started.text
        assert api(platform,'POST',case+'/reevaluate',args).json()==started.json()
        assert process(platform)
        current=view(platform,case);assert current['state']=='ruled'
        assert [attempt['state'] for attempt in current['attempts']]==['unknown','evaluated']
        assert api(platform,'POST',case+'/reevaluate',args).json()==started.json()
        changed={**args,'reason':'另一份申请不能覆盖原操作'}
        assert api(platform,'POST',case+'/reevaluate',changed).status_code==409
        assert current['ruling']['liable']=='provider'
        assert not platform.db.rows('SELECT * FROM payment_records WHERE ref=?',ref)


def test_independent_neutral_review_is_historically_verifiable_and_tampered_snapshots_fail(platform,tmp_path,monkeypatch):
    monkeypatch.setattr(platform.arbitration_cases,'process_one',lambda:False)
    with participants(platform,tmp_path,formation='countersign',fee=0,template_terms=short_terms) as (p,a,host,t,task):
        ref,admission=formed(a,candidate(a,p,t,task));case=begin(platform,a,admission)
        unknown(platform,monkeypatch);current=view(platform,case);args=body(current)
        platform.auth.set_roles('admin',owner(a),['admin'],[])
        try:
            assert api(platform,'POST',case+'/review',args,operator=owner(a)).status_code==403
            assert api(platform,'POST',case+'/review',args,operator='assistant:'+owner(a)).status_code in (401,403)
        finally:platform.auth.set_roles('admin',owner(a),[],['admin'])
        response=api(platform,'POST',case+'/review',args);assert response.status_code==200,response.text
        assert api(platform,'POST',case+'/review',args).json()==response.json()
        result=read(a,'/arbitrations',agentId=actor(a),admissionId=admission)
        assert result['state']=='ruled' and result['ruling']['liable']=='none' and result['evaluation'] is None
        assert 'protocolHistory' not in result['decisionEvidence']
        client,_=a.modules[0].fulfillment.client(ref);subject={'kind':'contract','digest':ref}
        assert read_case(a.account,None,actor(a),admission,subject,keys=client.keys)['ruling']==result['ruling']
        # The same genuine signed facts remain valid after the consumer clock advances.
        import a2c.codec as codec
        future=utcnow()+timedelta(days=366)
        class FutureDateTime(datetime):
            @classmethod
            def now(cls,tz=None):return future.astimezone(tz) if tz else future.replace(tzinfo=None)
        with monkeypatch.context() as clock:
            clock.setattr(codec,'datetime',FutureDateTime)
            assert verify_case(result,a.account,None,admission,subject,keys=client.keys)['caseRef']==case
        for mutate in (
            lambda value:value['decisionEvidence']['card']['derived'].update(total=999),
            lambda value:value['decisionEvidence'].update(payments=[{'forged':True}]),
            lambda value:value['ruling'].update(caseRef=uid()),
            lambda value:value['ruling'].update(issuedAt='2000-01-01T00:00:00Z'),
        ):
            bad=copy.deepcopy(result);mutate(bad)
            with pytest.raises(RuntimeFault) as invalid:verify_case(bad,a.account,None,admission,subject,keys=client.keys)
            assert invalid.value.code=='invalid_evidence'
        assert platform.market.reputation(p.identities[0].protocol_id)['completed']==0
        assert not platform.db.rows('SELECT * FROM payment_records WHERE ref=?',ref)
        assert platform.db.one('SELECT state FROM delegation_admissions WHERE id=?',admission)['state']=='committed'


def payment_terms(reference):
    terms=short_terms(reference)
    terms['deadlines'].update(startWithin='PT30M')
    return terms


@pytest.mark.parametrize('normal_close',[False,True])
def test_review_sees_later_original_payment_and_preserves_a_prior_legal_conclusion(platform,tmp_path,monkeypatch,normal_close):
    monkeypatch.setattr(platform.arbitration_cases,'process_one',lambda:False)
    with participants(platform,tmp_path,formation='countersign',fee=0,agent_host=DeliverHost(),template_terms=payment_terms) as (p,a,host,t,task):
        ref,admission=formed(a,candidate(a,p,t,task));delivered(a,ref)
        client,_=a.modules[0].fulfillment.client(ref);pay=client._pay
        def before_payment(*args,**kwargs):raise RuntimeFault('unknown_result','Isolated loss before payment')
        monkeypatch.setattr(client,'_pay',before_payment)
        with pytest.raises(RuntimeFault):
            with client.transport.operation(uid()):client.accept(ref,1)
        deadline=preview(p,admission,'payment-overdue',1)['deadline']
        monkeypatch.setattr(platform.arbitration_evidence,'clock',lambda:parse_time(deadline).timestamp()+1)
        case=begin(platform,p,admission,'payment-overdue',1)
        finalize=platform.arbitration_cases._finalize
        def interrupted(*args):raise OSError('Isolated interruption after known result')
        monkeypatch.setattr(platform.arbitration_cases,'_finalize',interrupted)
        with pytest.raises(OSError):process(platform)
        monkeypatch.setattr(platform.arbitration_cases,'_finalize',finalize)
        before=view(platform,case)
        card=client._record(ref)['card']
        with client.transport.operation(uid()):voucher=pay('installment',client.template(card['templateDigest']),card=card,period=1)
        if normal_close:
            with client.transport.operation(uid()):client.transport.send(card['provider'],'payment.notify',{'voucher':voucher})
            # Normal close precedes the original work.start terminal reply;
            # both facts belong to the evidence snapshot reviewed below.
            wait_for(lambda:platform.market.market.store.get('ledger',ref) and platform.db.one(
                "SELECT message FROM delegation_protocol_calls WHERE admission=? AND op='work.start' AND state='completed'",admission))
            ledger=platform.market.market.store.get('ledger',ref)
            assert ledger['outcome']=='completed'
            reputation=platform.market.reputation(p.identities[0].protocol_id)
        assert process(platform)
        current=view(platform,case);assert current['state']=='needs_review'
        assert current['assessment']['evidence']['payments']==[]
        assert len(current['review']['evidence']['payments'])==1
        assert api(platform,'POST',case+'/review',body(before)).status_code==409
        assert api(platform,'POST',case+'/review',body(current,liable='provider')).status_code==403
        response=api(platform,'POST',case+'/review',body(current));assert response.status_code==200,response.text
        current=view(platform,case)
        assert current['evaluation']['liable']=='client' and current['ruling']['liable']=='none'
        assert len(current['decisionEvidence']['payments'])==1
        if normal_close:
            assert platform.market.market.store.get('ledger',ref)==ledger
            assert platform.market.reputation(p.identities[0].protocol_id)['completed']==reputation['completed']==1
            assert platform.market.reputation(p.identities[0].protocol_id)['successRate']==1
            assert current['execution']=='finished'
            assert platform.db.one('SELECT state FROM delegation_admissions WHERE id=?',admission)['state']=='released'
        else:
            assert platform.market.market.store.get('ledger',ref)['outcome']=='arbitrated'
            assert current['execution']=='pending'
