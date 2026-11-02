"""Original HTTP/WS/A2C/AP2 evidence, explicit host and owner test drivers."""
import copy
from datetime import timedelta
import json

import httpx
import pytest

from a2c import objects
from a2c.codec import utcnow,parse_time
from ioalab_sdk.adapters.transport import SessionTransport
from ioalab_sdk.runtime.host import HostResult
from ioalab_sdk.runtime.ipc import LocalClient
from ioalab_sdk.runtime.protocol import RuntimeFault
from ioalab_sdk.provider.production import Production
from test_resident_delegation import participants,QuoteHost,request,terminal,uid
from test_resident_contracts import candidate,confirm,status
from test_resident_fulfillment import invoke,operation,accept_preview
from test_assistant_role import user_decision
from conftest import wait_for
import test_resident_delegation as assembly


def formed(assistant,quote):
    begun,_,_=confirm(assistant,quote);ref=begun['data']['contractRef']
    wait_for(lambda:status(assistant,ref));result=status(assistant,ref)
    assert result['status']=='completed',result
    return ref,result['data']['admissionId']


def preview(runtime,admission,ground,period=None):
    args={'agentId':runtime.identities[0].agent_id,'admissionId':admission,'ground':ground}
    if period is not None:args['period']=period
    return runtime.account.api('POST','/arbitrations/preview',args)


@pytest.mark.parametrize('formation',['proposal','countersign'])
def test_no_delivery_needed_for_start_deadline_and_preview_has_no_effects(platform,tmp_path,monkeypatch,formation):
    with participants(platform,tmp_path,formation=formation,fee=0) as (p,a,host,t,task):
        q=candidate(a,p,t,task);ref,admission=formed(a,q)
        before=preview(a,admission,'start-overdue')
        assert not before['eligible'] and before['reason']=='not_due'
        assert before['subject']=={'kind':'contract','digest':ref}
        assert before['evidence']['card']['fulfillment']=={} and not before['evidence']['steps']
        due=parse_time(before['deadline']);monkeypatch.setattr(platform.arbitration_evidence,'clock',lambda:due.timestamp())
        exact=preview(a,admission,'start-overdue');assert not exact['eligible']
        monkeypatch.setattr(platform.arbitration_evidence,'clock',lambda:due.timestamp()+1)
        after=preview(a,admission,'start-overdue');assert after['eligible']
        assert after['basisDigest']==before['basisDigest']
        assert platform.market.market.store.get('ledger',ref) is None
        with platform.db.lock:
            count=platform.db.conn.total_changes
            platform.arbitration_evidence.preview(a.account.account['id'],
                {'agentId':a.identities[0].agent_id,'admissionId':admission,'ground':'start-overdue'})
            assert platform.db.conn.total_changes==count
        turns=len(host.turns)
        monkeypatch.setattr(platform.arbitration_evidence,'clock',lambda:(utcnow()+timedelta(days=40)).timestamp())
        assert preview(a,admission,'start-overdue')['eligible']
        assert len(host.turns)==turns and not platform.db.rows('SELECT * FROM payment_records WHERE ref=?',ref)


def test_preview_authentication_fields_and_party_are_checked(platform,tmp_path):
    with participants(platform,tmp_path,fee=0) as (p,a,host,t,task):
        q=candidate(a,p,t,task);ref,admission=formed(a,q)
        args={'agentId':a.identities[0].agent_id,'admissionId':admission,'ground':'start-overdue'}
        for bad in ({**args,'ground':'choose-provider-as-loser'},{**args,'period':True},{**args,'period':1},
                    {**args,'card':q['card']},{**args,'ground':'deliver-overdue'},
                    {**args,'ground':'deliver-overdue','period':True},{**args,'ground':'deliver-overdue','period':2}):
            with pytest.raises(httpx.HTTPStatusError) as error:a.account.api('POST','/arbitrations/preview',bad)
            assert error.value.response.status_code==400
        with pytest.raises(httpx.HTTPStatusError) as error:preview(p,admission,'start-overdue')
        assert error.value.response.status_code==403
        with pytest.raises(httpx.HTTPStatusError) as error:preview(a,admission,'payment-overdue',1)
        assert error.value.response.status_code==403
        response=httpx.post(platform.s.base_url+'/api/v1/arbitrations/preview',json=args,trust_env=False)
        assert response.status_code in (401,403)


@pytest.mark.parametrize('fee',[0,1])
def test_unanswered_proposal_uses_actual_request_fee_and_terminal_answer(platform,tmp_path,monkeypatch,fee):
    import threading
    with participants(platform,tmp_path,fee=fee) as (p,a,host,t,task):
        host.wait=threading.Event()
        started,_=request(a,p,t,task);ref=started['data']['quoteRef']
        try:
            wait_for(lambda:len(host.turns)==1)
            admission=platform.db.one('SELECT id FROM delegation_admissions WHERE client=?',a.identities[0].agent_id)['id']
            monkeypatch.setattr(platform.arbitration_evidence,'clock',lambda:(utcnow()+timedelta(hours=1)).timestamp())
            value=preview(a,admission,'proposal-overdue')
            assert value['subject']['kind']=='proposal-request'
            assert value['evidence']['card'] is None and value['eligible']==bool(fee)
            assert value['reason']==('deadline_passed' if fee else 'proposal_fee_not_paid')
            assert len(value['evidence']['payments'])==fee
        finally:host.wait.set()
        wait_for(lambda:terminal(a,ref));assert terminal(a,ref)['status']=='completed'
        value=preview(a,admission,'proposal-overdue')
        assert not value['eligible'] and value['reason']==('answer_exists' if fee else 'proposal_fee_not_paid')


def test_paid_fee_without_submitted_request_does_not_prove_provider_timeout(platform,tmp_path,monkeypatch):
    send=SessionTransport.send
    def before_submission(self,to,op,*args,**kwargs):
        if op=='request.submit':raise RuntimeFault('unknown_result','Isolated fault before submitting signed request')
        return send(self,to,op,*args,**kwargs)
    with participants(platform,tmp_path,fee=1) as (p,a,host,t,task):
        monkeypatch.setattr(SessionTransport,'send',before_submission)
        started,_=request(a,p,t,task);ref=started['data']['quoteRef']
        wait_for(lambda:terminal(a,ref));assert terminal(a,ref)['status']=='unknown'
        row=platform.db.one('SELECT * FROM delegation_admissions WHERE client=?',a.identities[0].agent_id)
        assert platform.db.one("SELECT 1 FROM payment_records WHERE ref=? AND purpose='proposal-fee'",row['binding'])
        monkeypatch.setattr(platform.arbitration_evidence,'clock',lambda:(utcnow()+timedelta(hours=1)).timestamp())
        value=preview(a,row['id'],'proposal-overdue')
        assert not value['eligible'] and value['reason']=='request_not_submitted'
        assert len(value['evidence']['payments'])==1 and not host.turns


class TwoStepHost(QuoteHost):
    def run(self,turn):
        data=json.loads(turn.input)
        if data['phase']!='production':return super().run(turn)
        self.turns.append(turn);turn.on_session(turn.session_id)
        step=('first','second')[len(data['steps'])]
        result=turn.on_tool('provider.task.request_step',{'step':step},'original-'+step)
        assert result['status']=='waiting',result
        return HostResult('completed',turn.session_id,{'testDouble':True})


def test_every_original_step_and_answer_contributes_to_deadline(platform,tmp_path,monkeypatch):
    make=assembly.template_from_definition
    def terms(value,**kwargs):
        value=copy.deepcopy(value);value['steps']=[{'name':s} for s in ('first','second')]
        return make(value,**kwargs)
    monkeypatch.setattr(assembly,'template_from_definition',terms)
    with participants(platform,tmp_path,fee=0,agent_host=TwoStepHost()) as (p,a,host,t,task):
        q=candidate(a,p,t,task);ref,admission=formed(a,q)
        key,_=invoke(a,ref,'start');wait_for(lambda:operation(a,ref,key))
        wait_for(lambda:status(a,ref)['data']['stepRequest'])
        one=preview(p,admission,'authorize-overdue',1)
        assert len(one['evidence']['steps'])==1 and not one['eligible']
        # In this virtual clock 35 minutes passed, but the unanswered step
        # pauses delivery by 15 minutes, extending the 30 minute deadline.
        monkeypatch.setattr(platform.arbitration_evidence,'clock',lambda:(utcnow()+timedelta(minutes=35)).timestamp())
        assert not preview(a,admission,'deliver-overdue',1)['eligible']
        assert preview(p,admission,'authorize-overdue',1)['eligible']
        sr=status(a,ref)['data']['stepRequest'];key,pending=invoke(a,ref,'answer_step',stepRequestDigest=objects.object_digest(sr))
        user_decision(a,pending['data']['confirmationRef'],decision='answer',answers=[{'selected_options':['allow']}])
        assert LocalClient(a.config.home).call('assistant.delegation.answer_step',{'contractRef':ref,'stepRequestDigest':objects.object_digest(sr)},operation_id=key)['status']=='accepted'
        wait_for(lambda:operation(a,ref,key))
        wait_for(lambda:status(a,ref)['data']['stepRequest'] and status(a,ref)['data']['stepRequest']['step']=='second')
        value=preview(p,admission,'authorize-overdue',1)
        steps=value['evidence']['steps'];assert len(steps)==2
        assert {s['request']['step']:bool(s['answer']) for s in steps}=={'first':True,'second':False}
        assert value['eligible']
        # Missing retained original progress must stop adjudication, even when
        # the latest contract projection still looks usable.
        original=platform.messaging.delivery.protocol_history_in
        def missing(conn,message):
            data=original(conn,message)
            if any((r['body']['reply'].get('task') or {}).get('state')=='auth-required' for r in data['results']):
                data['results']=[]
            return data
        with monkeypatch.context() as patch:
            patch.setattr(platform.messaging.delivery,'protocol_history_in',missing)
            with pytest.raises(httpx.HTTPStatusError) as error:preview(p,admission,'authorize-overdue',1)
            assert error.value.response.status_code==409 and error.value.response.json()['error']['code']=='history_gap'


class DeliverHost(QuoteHost):
    def run(self,turn):
        data=json.loads(turn.input)
        if data['phase']!='production':return super().run(turn)
        self.turns.append(turn);turn.on_session(turn.session_id)
        value=turn.on_tool('provider.task.deliver',{'files':{'result.txt':{'text':'actual delivered bytes','mediaType':'text/plain'}}},'actual-delivery')
        assert value['status']=='delivered',value
        return HostResult('completed',turn.session_id,{'testDouble':True})


def delivered(a,ref):
    key,_=invoke(a,ref,'start');wait_for(lambda:operation(a,ref,key))
    started=operation(a,ref,key)
    assert started['status']=='completed',{'stage':'start','operation':started}
    wait_for(lambda:status(a,ref)['data']['availablePeriods']==1)
    key,_=invoke(a,ref,'fetch',period=1);wait_for(lambda:operation(a,ref,key))
    fetched=operation(a,ref,key)
    assert fetched['status']=='completed',{'stage':'fetch','operation':fetched}
    return {'start':started,'fetch':fetched}


class SameIdVerifierHost(DeliverHost):
    def run(self,turn):
        if json.loads(turn.input)['phase']!='quote':return super().run(turn)
        self.turns.append(turn);turn.on_session(turn.session_id or 'dual-verifier-session')
        turn.on_tool('provider.task.quote',{'acceptance':{'result':{'verifiers':{'script':self.verifier,'skill':self.skill},'notCovered':[]}},
            'acceptWithin':{'result':'PT10M'},'lineItems':{'result':9}},'dual-verifier-quote')
        return HostResult('completed',turn.session_id,{'testDouble':True})


@pytest.mark.parametrize('dual',[False,True])
def test_signed_failed_report_precludes_accept_timeout_and_bad_signature_is_rejected(platform,tmp_path,monkeypatch,dual):
    with participants(platform,tmp_path,fee=0,agent_host=SameIdVerifierHost() if dual else DeliverHost(),verifier_source="def verify(ctx):\n    return {'status':'fail'}\n") as (p,a,host,t,task):
        if dual:
            monkeypatch.setitem(platform.market.market.models,'evidence-fixed-observer',lambda *_:{'unused':True})
            uploaded=LocalClient(p.config.home).call('provider.verifier.publish',{'profile':'service','verifierId':host.verifier['id'],
                'source':"def verify(ctx):\n    return {'status':'fail'}\n",'kind':'skill'})
            assert uploaded['status']=='completed',uploaded
            host.skill={**uploaded['data'],'disputeModels':['evidence-fixed-observer']}
        q=candidate(a,p,t,task);ref,admission=formed(a,q);receipts=delivered(a,ref)
        client,_=a.modules[0].fulfillment.client(ref)
        before=client.store.get('cards',ref)
        signatures=before['card']['signatures']
        assert len(signatures['6'])==1 and not signatures['7'],{'stage':'before_accept',
            'receipts':receipts,'state':before.get('state'),'signatures':{'6':signatures['6'],'7':signatures['7']}}
        with client.transport.operation(uid()):result=client.accept(ref,1)
        assert result.status=='fail' and not result.card['signatures']['7']
        monkeypatch.setattr(platform.arbitration_evidence,'clock',lambda:(utcnow()+timedelta(hours=1)).timestamp())
        claim=preview(a,admission,'acceptance-failed',1)
        assert claim['eligible'] and claim['route']=='recompute' and claim['requiredEvidence']==['deliveryFiles']
        value=preview(p,admission,'accept-overdue',1)
        assert not value['eligible'] and value['reason']=='failing_report_exists'
        original=platform.db.one('SELECT progress FROM delegation_contracts WHERE admission=?',admission)['progress']
        bad=json.loads(original);bad['fulfillment']['periods'][0]['report']['results'][0]['status']='pass'
        try:
            platform.db.run('UPDATE delegation_contracts SET progress=? WHERE admission=?',json.dumps(bad),admission)
            with pytest.raises(httpx.HTTPStatusError) as error:preview(p,admission,'accept-overdue',1)
            assert error.value.response.status_code==409
        finally:platform.db.run('UPDATE delegation_contracts SET progress=? WHERE admission=?',original,admission)


@pytest.mark.parametrize('amount',[0,9])
def test_actual_paid_installment_is_not_inferred_from_missing_client_receipt(platform,tmp_path,monkeypatch,amount):
    make=assembly.template_from_definition
    def terms(value,**kwargs):
        value=copy.deepcopy(value);value['pricing']['price']=str(amount)
        return make(value,**kwargs)
    monkeypatch.setattr(assembly,'template_from_definition',terms)
    advance=Production.advance
    def hold_closure(self,endpoint):
        if any(rec['card']['signatures']['7'] for rec in endpoint.store.all('cards').values()):return
        return advance(self,endpoint)
    monkeypatch.setattr(Production,'advance',hold_closure)
    with participants(platform,tmp_path,formation='countersign',fee=0,agent_host=DeliverHost()) as (p,a,host,t,task):
        q=candidate(a,p,t,task);ref,admission=formed(a,q);delivered(a,ref)
        key,pending=accept_preview(a,ref);user_decision(a,pending['data']['confirmationRef'])
        assert LocalClient(a.config.home).call('assistant.delegation.accept',{'contractRef':ref,'period':1},operation_id=key)['status']=='accepted'
        wait_for(lambda:operation(a,ref,key));assert operation(a,ref,key)['status']=='completed'
        monkeypatch.setattr(platform.arbitration_evidence,'clock',lambda:(utcnow()+timedelta(hours=1)).timestamp())
        value=preview(p,admission,'payment-overdue',1)
        assert not value['eligible'] and value['reason']==('payment_recorded' if amount else 'zero_amount')
        paid=value['evidence']['payments'];assert len(paid)==bool(amount)
        if not amount:return
        assert paid[0]['voucher']['signature']
        assert 'paymentMandate' not in json.dumps(value) and 'checkoutMandate' not in json.dumps(value)
        original=platform.db.one('SELECT reply FROM payment_records WHERE ref=?',ref)['reply'];bad=json.loads(original)
        bad['voucher']['amount']=123
        try:
            platform.db.run('UPDATE payment_records SET reply=? WHERE ref=?',json.dumps(bad),ref)
            with pytest.raises(httpx.HTTPStatusError) as error:preview(p,admission,'payment-overdue',1)
            assert error.value.response.status_code==409
        finally:platform.db.run('UPDATE payment_records SET reply=? WHERE ref=?',original,ref)


@pytest.mark.parametrize('damage',['missing_request','scope','template'])
def test_missing_original_request_or_changed_binding_is_not_a_usable_snapshot(platform,tmp_path,monkeypatch,damage):
    with participants(platform,tmp_path,fee=0) as (p,a,host,t,task):
        q=candidate(a,p,t,task);ref,admission=formed(a,q)
        original=platform.admissions.evidence_in
        def damaged(conn,identifier):
            value=copy.deepcopy(original(conn,identifier))
            if damage=='missing_request':value['signedRequest']=None
            elif damage=='scope':value['admission']['scope']='sha256:'+'0'*64
            else:value['admission']['template']='sha256:'+'0'*64
            return value
        monkeypatch.setattr(platform.admissions,'evidence_in',damaged)
        with pytest.raises(httpx.HTTPStatusError) as error:preview(a,admission,'start-overdue')
        assert error.value.response.status_code==409 and error.value.response.json()['error']['code']=='history_gap'
