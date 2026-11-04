"""Real case control/query boundaries; host and user actions are explicit doubles."""
from urllib.parse import urlencode
import threading

import httpx
import pytest

from a2c.codec import digest_bytes,parse_time
from ioalab.errors import LabError
from ioalab_sdk.runtime.protocol import RuntimeFault
from test_arbitration_evidence import preview,formed,DeliverHost,delivered
from test_resident_delegation import participants,request,terminal,uid
from test_resident_contracts import candidate
from conftest import wait_for


@pytest.fixture(autouse=True)
def admission_stage_only(platform,monkeypatch):
    # These tests isolate admission from the separately tested background worker.
    monkeypatch.setattr(platform.arbitration_cases,'process_one',lambda:False)


def actor(runtime):return runtime.identities[0].agent_id
def owner(runtime):return runtime.account.account['id']
def submit(runtime,payload,operation=None):
    return runtime.connection.call(actor(runtime),'arbitration.request',payload,operation_id=operation or uid())


def read(runtime,path,**params):return runtime.account.api('GET',path+'?'+urlencode(params))


def due(platform,assistant,admission,monkeypatch):
    current=preview(assistant,admission,'start-overdue')
    deadline=parse_time(current['deadline']).timestamp()
    monkeypatch.setattr(platform.arbitration_evidence,'clock',lambda:deadline+1)
    return {'admissionId':admission,'ground':'start-overdue','basisDigest':current['basisDigest']}


@pytest.mark.parametrize('formation',['proposal','countersign'])
def test_real_ws_admission_and_both_parties_restore_one_case_without_adjudicating(platform,tmp_path,monkeypatch,formation):
    with participants(platform,tmp_path,formation=formation,fee=0) as (p,a,host,t,task):
        ref,admission=formed(a,candidate(a,p,t,task))
        first=preview(a,admission,'start-overdue')
        payload={'admissionId':admission,'ground':'start-overdue','basisDigest':first['basisDigest']}
        assert read(a,'/arbitrations',agentId=actor(a),admissionId=admission) is None
        with pytest.raises(RuntimeFault) as early:submit(a,payload)
        assert early.value.code=='not_ready'
        assert not platform.db.one('SELECT 1 FROM arbitration_cases WHERE admission=?',admission)
        payload=due(platform,a,admission,monkeypatch);key=uid();turns=len(host.turns)
        receipt=submit(a,payload,key)
        assert not receipt['existing'] and receipt['subject']=={'kind':'contract','digest':ref}
        assert submit(a,payload,key)==receipt
        restored=read(a,'/operations/'+key,agentId=actor(a),operation='arbitration.request')
        assert restored==receipt
        for party in (a,p):
            result=read(party,'/arbitrations',agentId=actor(party),admissionId=admission)
            assert result['caseRef']==receipt['caseRef'] and result['state']=='queued'
            assert result['ruling'] is None and result['execution']=='pending' and not result['attempts']
            assert 'protocolHistory' not in result['assessment']['evidence']
        alternate={**payload,'ground':'deliver-overdue','period':1}
        assert submit(a,alternate)['caseRef']==receipt['caseRef']
        assert submit(p,{**payload,'ground':'payment-overdue','period':1})['caseRef']==receipt['caseRef']
        assert platform.db.one('SELECT COUNT(*) AS n FROM arbitration_cases WHERE admission=?',admission)['n']==1
        with pytest.raises(LabError) as changed:
            platform.arbitration_cases.prepare(owner(a),actor(a),'arbitration.request',key,alternate)
        assert changed.value.code=='id_conflict'
        assert platform.market.market.store.get('ledger',ref) is None
        assert not platform.db.rows('SELECT * FROM payment_records WHERE ref=?',ref) and len(host.turns)==turns


def test_material_ownership_manifest_bytes_and_scoped_admin_reads(platform,tmp_path,monkeypatch):
    with participants(platform,tmp_path,fee=0,agent_host=DeliverHost(),
            verifier_source="def verify(ctx):\n    return {'status':'fail'}\n") as (p,a,host,t,task):
        ref,admission=formed(a,candidate(a,p,t,task));delivered(a,ref)
        client,_=a.modules[0].fulfillment.client(ref)
        with client.transport.operation(uid()):failed=client.accept(ref,1)
        assert failed.status=='fail'
        original=preview(a,admission,'acceptance-failed',1)
        payload={'admissionId':admission,'ground':'acceptance-failed','period':1,'basisDigest':original['basisDigest']}
        with pytest.raises(RuntimeFault) as missing:submit(a,payload)
        assert missing.value.code=='invalid_input'
        body=b'actual delivered bytes';sha=digest_bytes(body)
        foreign=platform.files.put(owner(p),actor(p),uid(),'result.txt',sha,body)
        with pytest.raises(RuntimeFault) as wrong_owner:submit(a,{**payload,'materials':{'deliveryFiles':{'result.txt':foreign}}})
        assert wrong_owner.value.code=='forbidden'
        wrong=platform.files.put(owner(a),actor(a),uid(),'result.txt',digest_bytes(b'wrong bytes'),b'wrong bytes')
        with pytest.raises(RuntimeFault) as bad_manifest:submit(a,{**payload,'materials':{'deliveryFiles':{'result.txt':wrong}}})
        assert bad_manifest.value.code=='invalid_input'
        own=platform.files.put(owner(a),actor(a),uid(),'result.txt',sha,body)
        path=platform.files.path(sha);before=path.read_bytes()
        try:
            path.write_bytes(b'damaged original')
            with pytest.raises(RuntimeFault) as corrupt:submit(a,{**payload,'materials':{'deliveryFiles':{'result.txt':own}}})
            assert corrupt.value.code=='not_ready'
        finally:path.write_bytes(before)
        assert not platform.db.one('SELECT 1 FROM arbitration_cases WHERE admission=?',admission)
        accepted=submit(a,{**payload,'materials':{'deliveryFiles':{'result.txt':own}}});case_ref=accepted['caseRef']
        before_money=platform.db.rows('SELECT * FROM payment_records WHERE ref=?',ref)
        for party in (a,p):
            url=platform.s.base_url+'/api/v1/arbitrations/'+case_ref+'/materials/'+own['fileId']
            result=httpx.get(url,params={'agentId':actor(party)},headers={'Authorization':'Bearer '+platform.auth.token(owner(party))},trust_env=False)
            assert result.status_code==200 and result.content==body
        admin={'Authorization':'Bearer '+platform.auth.token('admin')}
        base=platform.s.base_url+'/api/admin/arbitrations'
        listed=httpx.get(base,headers=admin,trust_env=False);assert listed.status_code==200
        assert case_ref in {c['caseRef'] for c in listed.json()['cases']}
        view=httpx.get(base+'/'+case_ref,headers=admin,trust_env=False);assert view.status_code==200
        assert view.json()['materials']=={'deliveryFiles':{'result.txt':own}}
        downloaded=httpx.get(base+'/'+case_ref+'/materials/'+own['fileId'],headers=admin,trust_env=False)
        assert downloaded.status_code==200 and downloaded.content==body
        outside=httpx.get(base+'/'+case_ref+'/materials/'+wrong['fileId'],headers=admin,trust_env=False)
        assert outside.status_code==404
        platform.auth.set_roles('admin',owner(a),['admin'],[])
        try:
            own_admin={'Authorization':'Bearer '+platform.auth.token(owner(a))}
            blocked=httpx.get(base+'/'+case_ref,headers=own_admin,trust_env=False)
            assert blocked.status_code==403
            assert case_ref not in {c['caseRef'] for c in httpx.get(base,headers=own_admin,trust_env=False).json()['cases']}
            sdk_admin={'Authorization':'Bearer '+platform.auth.token('assistant:'+owner(a))}
            assert httpx.get(base,headers=sdk_admin,trust_env=False).status_code in (401,403)
        finally:platform.auth.set_roles('admin',owner(a),[],['admin'])
        denied=httpx.get(base+'/'+case_ref,headers={'Authorization':'Bearer '+platform.auth.token(owner(a))},trust_env=False)
        assert denied.status_code in (401,403)
        assert {r['action'] for r in platform.db.rows('SELECT * FROM arbitration_audit WHERE case_id=?',case_ref)}=={'read','material'}
        assert platform.db.rows('SELECT * FROM payment_records WHERE ref=?',ref)==before_money
        assert platform.market.market.store.get('ledger',ref) is None


def test_current_lease_is_rechecked_after_evidence_preparation(platform,tmp_path,monkeypatch):
    with participants(platform,tmp_path,fee=0) as (p,a,host,t,task):
        ref,admission=formed(a,candidate(a,p,t,task));payload=due(platform,a,admission,monkeypatch)
        prepare=platform.arbitration_cases.prepare
        def disconnect(*args,**kwargs):
            result=prepare(*args,**kwargs)
            connection=platform.db.one('SELECT connection FROM delivery_leases WHERE actor=?',actor(a))['connection']
            platform.messaging.delivery.disconnect(connection)
            return result
        monkeypatch.setattr(platform.arbitration_cases,'prepare',disconnect)
        with pytest.raises(RuntimeFault) as stale:submit(a,payload)
        assert stale.value.code=='stale_generation'
        assert not platform.db.one('SELECT 1 FROM arbitration_cases WHERE admission=?',admission)
        assert platform.market.market.store.get('ledger',ref) is None


@pytest.mark.parametrize('fee',[0,1])
def test_original_proposal_request_can_be_a_case_without_fabricating_a_card(platform,tmp_path,monkeypatch,fee):
    with participants(platform,tmp_path,fee=fee) as (p,a,host,t,task):
        host.wait=threading.Event()
        started,_=request(a,p,t,task);quote=started['data']['quoteRef']
        try:
            wait_for(lambda:len(host.turns)==1)
            admission=platform.db.one('SELECT id FROM delegation_admissions WHERE client=?',actor(a))['id']
            initial=preview(a,admission,'proposal-overdue')
            end=parse_time(initial['deadline']).timestamp()+1
            monkeypatch.setattr(platform.arbitration_evidence,'clock',lambda:end)
            current=preview(a,admission,'proposal-overdue')
            payload={'admissionId':admission,'ground':'proposal-overdue','basisDigest':current['basisDigest']}
            if fee:
                receipt=submit(a,payload)
                assert receipt['subject']==current['subject'] and receipt['subject']['kind']=='proposal-request'
                case=read(p,'/arbitrations',agentId=actor(p),admissionId=admission)
                assert case['caseRef']==receipt['caseRef'] and case['assessment']['evidence']['card'] is None
                assert case['ruling'] is None and case['period'] is None
                assert len(case['assessment']['evidence']['payments'])==1
                assert platform.market.market.store.get('ledger',receipt['subject']['digest']) is None
            else:
                with pytest.raises(RuntimeFault) as unpaid:submit(a,payload)
                assert unpaid.value.code=='not_ready'
                assert not platform.db.one('SELECT 1 FROM arbitration_cases WHERE admission=?',admission)
        finally:host.wait.set()
        wait_for(lambda:terminal(a,quote))
