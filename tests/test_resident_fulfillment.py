"""Complete resident lifecycle using real HTTP/WS, original A2C/AP2 and WASM."""
import copy
import json
import os
from pathlib import Path
from dataclasses import replace
import time
import uuid
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from a2c.codec import utcnow
from a2c.jws import Signer
from a2c import objects
from a2c.slots import verify_card
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from ioalab_sdk.runtime.host import HostResult
from ioalab_sdk.runtime.ipc import LocalClient
from ioalab_sdk.runtime.protocol import stamp
from test_resident_delegation import participants,QuoteHost
from test_resident_contracts import candidate,confirm,status
from test_resident_delegation import request,terminal
from ioalab_sdk.runtime.files import private_json
from test_assistant_role import user_decision
import test_resident_delegation as assembly
from conftest import wait_for


def uid():return str(uuid.uuid4())


VERIFIER='''import json
def verify(ctx):
    try:
        file=next(item for item in ctx.files if item["name"]=="result.json")
        value=json.load(open(file["path"],encoding="utf-8"))
        ok=value.get("delivered")=="actual file bytes"
    except Exception:
        ok=False
    return {"status":"pass" if ok else "fail","checks":[{"name":"expected-delivery-content","pass":ok}]}
'''


class ProductionHost(QuoteHost):
    def run(self,turn):
        try:return self.execute(turn)
        except Exception as exc:
            self.failure={'type':type(exc).__name__,'message':str(exc)}
            raise

    def execute(self,turn):
        data=json.loads(turn.input)
        if data['phase']!='production':return super().run(turn)
        self.turns.append(turn);turn.on_session(turn.session_id)
        if not data['steps']:
            result=turn.on_tool('provider.task.request_step',{'step':'real_name'},'step')
            assert result['status']=='waiting',result
        else:
            assert data['steps'][-1]['answer']['granted']
            files={'result.json':{'text':'{"delivered":"actual file bytes"}','mediaType':'application/json'}}
            tested=turn.on_tool('provider.task.selftest',{'files':files},'selftest')
            assert tested['status']=='pass',tested
            result=turn.on_tool('provider.task.deliver',{'files':files},'delivery')
            assert result['status']=='delivered',result
        return HostResult('completed',turn.session_id,{'explicitProductionHostDouble':True})


def configure_template(monkeypatch):
    original=assembly.template_from_definition
    def definition(value,**kwargs):
        value=copy.deepcopy(value);value['steps']=[{'name':'real_name','description':'必须由真实用户批准的业务环节'}]
        value['pricing']['deposit']='1'
        return original(value,**kwargs)
    monkeypatch.setattr(assembly,'template_from_definition',definition)


def operation(assistant,reference,key):
    value=LocalClient(assistant.config.home).call('assistant.delegation.status',{'contractRef':reference,'operationId':key})
    assert value['status']=='completed',value
    state=value['data']['operation']
    return state if state.get('status') in ('completed','rejected','failed','unknown') else None


def invoke(assistant,reference,name,**args):
    key=uid();value=LocalClient(assistant.config.home).call('assistant.delegation.'+name,{'contractRef':reference,**args},operation_id=key)
    assert value['status'] in ('accepted','waiting'),value
    return key,value


@pytest.mark.parametrize('formation',['proposal','countersign'])
@pytest.mark.parametrize('lose_payment',[False,True])
def test_full_contract_with_actual_user_step_delivery_wasm_payment_close_and_reputation(platform,tmp_path,monkeypatch,formation,lose_payment):
    configure_template(monkeypatch)
    with participants(platform,tmp_path,formation=formation,fee=1 if formation=='proposal' else 0,agent_host=ProductionHost(),verifier_source=VERIFIER) as (provider,assistant,host,template,task):
        quote=candidate(assistant,provider,template,task);selected,_,_=confirm(assistant,quote);ref=selected['data']['contractRef']
        wait_for(lambda:status(assistant,ref));assert status(assistant,ref)['data']['formed']
        # Withdraw new candidate eligibility without deleting the original offer.
        offered=platform.publications.templates;actor=provider.identities[0].agent_id
        monkeypatch.setattr(platform.publications,'templates',lambda service:[] if service==actor else offered(service))
        platform.db.run('UPDATE delegation_intents SET expires=? WHERE service=?',time.time()-1,actor)
        key,_=invoke(assistant,ref,'start')
        wait_for(lambda:operation(assistant,ref,key));assert operation(assistant,ref,key)['status']=='completed',operation(assistant,ref,key)
        wait_for(lambda:status(assistant,ref)['data']['stepRequest'])
        sr=status(assistant,ref)['data']['stepRequest'];client=LocalClient(assistant.config.home)
        assert status(assistant,ref)['data']['availablePeriods']==0
        # The model cannot supply its own granted/confirmed flag.
        assert client.call('assistant.delegation.answer_step',{'contractRef':ref,'stepRequestDigest':objects.object_digest(sr),'granted':True})['status']=='rejected'
        forged=Signer('urn:test-foreign-user',Ed25519PrivateKey.generate())
        answer=objects.signed(objects.new('step-authorization',stepRequestDigest=objects.object_digest(sr),granted=True,jwk=forged.inline_jwk),forged,utcnow())
        relation=assistant.store.interaction(interaction_id=status(assistant,ref)['data']['interactionId'])
        task_id=assistant.modules[0].fulfillment.client(ref)[0].store.get('cards',ref)['task']
        bad={'messageId':uid(),'kind':'a2c.request','recipient':provider.identities[0].agent_id,'interactionId':relation['id'],
             'expiresAt':stamp(time.time()+300),
             'body':{'admissionId':status(assistant,ref)['data']['admissionId'],'op':'step.authorize','taskId':task_id,'body':{'stepAuthorization':answer},'files':{}}}
        from ioalab_sdk.runtime.protocol import RuntimeFault
        with pytest.raises(RuntimeFault):assistant.connection.call(assistant.identities[0].agent_id,'message.send',bad,operation_id=uid())
        key,pending=invoke(assistant,ref,'answer_step',stepRequestDigest=objects.object_digest(sr))
        assert pending['status']=='waiting'
        user_decision(assistant,pending['data']['confirmationRef'],decision='answer',answers=[{'selected_options':['allow']}])
        assert client.call('assistant.delegation.answer_step',{'contractRef':ref,'stepRequestDigest':objects.object_digest(sr)},operation_id=key)['status']=='accepted'
        wait_for(lambda:operation(assistant,ref,key));assert operation(assistant,ref,key)['status']=='completed',operation(assistant,ref,key)
        try:wait_for(lambda:status(assistant,ref)['data']['availablePeriods']==1)
        except AssertionError:raise AssertionError({'hostFailure':getattr(host,'failure',None),'executionErrors':provider.modules[0].execution.errors,'roleError':provider.modules[0].error})
        key,_=invoke(assistant,ref,'fetch',period=1)
        wait_for(lambda:operation(assistant,ref,key));assert operation(assistant,ref,key)['status']=='completed',operation(assistant,ref,key)
        key,_=invoke(assistant,ref,'verify',period=1)
        wait_for(lambda:operation(assistant,ref,key));assert operation(assistant,ref,key)['data']['report']['status']=='pass',operation(assistant,ref,key)
        assert not status(assistant,ref)['data']['card']['signatures']['7']
        assert not platform.db.rows("SELECT * FROM payment_records WHERE ref=? AND purpose='installment'",ref)
        key,pending=invoke(assistant,ref,'accept',period=1);assert pending['status']=='waiting'
        preview=assistant.confirmations.get(pending['data']['confirmationRef'],detail=True)['binding']['preview']
        assert preview['report']['status']=='pass' and preview['amount']==8
        user_decision(assistant,pending['data']['confirmationRef'])
        original_call=assistant.connection.call;blocked_close=[];dropped=[]
        def before_payment(actor,kind,payload,**kwargs):
            if kind=='ap2.pay' and payload['ap2'].get('card',{}).get('signatures',{}).get('7') and not blocked_close:
                endpoint=provider.modules[0].execution.contexts[relation['id']]
                close={'admissionId':status(assistant,ref)['data']['admissionId'],'op':'card.close','body':{'card':endpoint.provider.card(ref)}}
                with pytest.raises(RuntimeFault) as rejected:provider.connection.call(provider.identities[0].agent_id,'a2c.market',close,operation_id=uid())
                assert rejected.value.code=='not_ready'
                assert platform.market.market.store.get('ledger',ref) is None
                assert platform.db.one('SELECT state FROM delegation_admissions WHERE id=?',close['admissionId'])['state']=='committed'
                blocked_close.append(True)
            result=original_call(actor,kind,payload,**kwargs)
            if lose_payment and kind=='ap2.pay' and payload['ap2'].get('card',{}).get('signatures',{}).get('7') and not dropped:
                dropped.append(payload['ap2']['card']['signatures']['7'][0])
                raise RuntimeFault('unknown_result','test lost the actual committed installment receipt')
            return result
        monkeypatch.setattr(assistant.connection,'call',before_payment)
        paused=threading.Event();release=threading.Event()
        if lose_payment:
            from ioalab_sdk.assistant.fulfillment import Fulfillment
            original_finish=Fulfillment.finish
            def pause_after_saved_result(owner,operation_id,record,result):
                outcome=original_finish(owner,operation_id,record,result)
                if owner.runtime is assistant and record['name']=='accept' and not paused.is_set():
                    paused.set();assert release.wait(5),'test recovery finalization was not released'
                return outcome
            monkeypatch.setattr(Fulfillment,'finish',pause_after_saved_result)
        assert client.call('assistant.delegation.accept',{'contractRef':ref,'period':1},operation_id=key)['status']=='accepted'
        if lose_payment:
            try:
                assert paused.wait(10),'original actual receipt was not recovered'
                attempted=threading.Event();returned=threading.Event()
                def concurrent_scan():
                    attempted.set();assistant.modules[0].fulfillment.recover();returned.set()
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future=pool.submit(concurrent_scan)
                    try:
                        assert attempted.wait(5)
                        assert not returned.wait(.1),'another consumer finalized the same original job during its receipt commit'
                    finally:release.set()
                    future.result(timeout=5)
            finally:release.set()
            wait_for(lambda:operation(assistant,ref,key) and operation(assistant,ref,key)['status']=='completed')
        wait_for(lambda:operation(assistant,ref,key));assert operation(assistant,ref,key)['status']=='completed',operation(assistant,ref,key)
        wait_for(lambda:status(assistant,ref)['data']['closed'])
        actual=status(assistant,ref)['data'];protocol,_=assistant.modules[0].fulfillment.client(ref)
        assert verify_card(template,actual['card'],protocol.keys).accepted==1
        assert all(actual['card']['signatures'][str(i)] for i in range(1,8) if i!=3 or formation=='proposal')
        assert actual['payment']=='paid' and actual['vouchers'][0]['amount']==8
        payments=platform.db.rows('SELECT purpose,amount FROM payment_records WHERE ref=? ORDER BY purpose',ref)
        assert payments==[{'purpose':'deposit','amount':'1.00'},{'purpose':'installment','amount':'8.00'}]
        aid=actual['admissionId']
        assert platform.db.one('SELECT state FROM delegation_admissions WHERE id=?',aid)['state']=='released'
        assert platform.market.reputation(provider.identities[0].protocol_id)['completed']==1
        assert blocked_close==[True]
        assert bool(dropped)==lose_payment
        if dropped:assert actual['card']['signatures']['7']==dropped
        key,_=invoke(assistant,ref,'pay',period=1)
        wait_for(lambda:operation(assistant,ref,key))
        assert operation(assistant,ref,key)['data']['payment']=='paid'
        assert len(platform.db.rows('SELECT * FROM payment_records WHERE ref=?',ref))==2
        turns=[turn for turn in host.turns if json.loads(turn.input)['phase']=='production']
        assert len(turns)==2 and len({turn.session_id for turn in turns})==1


def test_actual_bad_delivery_fails_wasm_and_never_signs_acceptance_or_pays(platform,tmp_path):
    class BadHost(QuoteHost):
        def run(self,turn):
            data=json.loads(turn.input)
            if data['phase']!='production':return super().run(turn)
            self.turns.append(turn);turn.on_session(turn.session_id)
            result=turn.on_tool('provider.task.deliver',{'files':{'result.json':{'text':'{"delivered":"wrong value"}','mediaType':'application/json'}}},'bad-delivery')
            assert result['status']=='delivered'
            return HostResult('completed',turn.session_id,{'explicitBadHost':True})
    with participants(platform,tmp_path,agent_host=BadHost(),verifier_source=VERIFIER) as (provider,assistant,host,template,task):
        quote=candidate(assistant,provider,template,task);selected,_,_=confirm(assistant,quote);ref=selected['data']['contractRef']
        wait_for(lambda:status(assistant,ref))
        key,_=invoke(assistant,ref,'start');wait_for(lambda:operation(assistant,ref,key))
        wait_for(lambda:status(assistant,ref)['data']['availablePeriods']==1)
        key,_=invoke(assistant,ref,'fetch',period=1);wait_for(lambda:operation(assistant,ref,key))
        value=LocalClient(assistant.config.home).call('assistant.delegation.accept',{'contractRef':ref,'period':1})
        assert value['status']=='rejected' and value['data']['report']['status']=='fail',value
        assert not status(assistant,ref)['data']['card']['signatures']['7']
        assert not platform.db.rows('SELECT * FROM payment_records WHERE ref=?',ref)
        assert platform.market.market.store.get('ledger',ref) is None


@pytest.mark.skipif(not os.environ.get('IOALAB_REAL_JIUWEN_DATA_DIR'),reason='requires explicitly configured original Jiuwen/model')
def test_real_original_jiuwen_produces_native_file_and_completes_original_contract(platform,tmp_path,monkeypatch):
    from ioalab_jiuwen.process import ProcessHost
    configure_template(monkeypatch)
    host=ProcessHost(json.loads(os.environ['IOALAB_REAL_JIUWEN_COMMAND']),os.environ['IOALAB_REAL_JIUWEN_DATA_DIR'],model=os.environ.get('IOALAB_REAL_JIUWEN_MODEL'))
    assert host.probe()['available'],host.capabilities()
    events=[];turns=[];original=host.run
    def event(value):
        if value.get('event_type') in ('chat.tool_call','chat.tool_result','interaction.requested'):
            events.append(value);private_json(tmp_path/'native-production-events.json',events)
    def execute(turn):
        turns.append(turn)
        return original(replace(turn,on_event=event))
    host.run=execute
    business={'native_tools':{'write_file':'allow'},'instructions':
        '本测试业务为生产结构化摘要。报价使用resources.outputVerifier作为script，报价9，验收PT10M；审查用decision。'
        '生产第一轮，若steps为空，先用provider.task.request_step请求real_name，之后结束本轮。'
        '收到真实granted后，用原生write_file在输入artifactDirectory目录中生成result.json，内容为{"delivered":"actual file bytes"}。'
        '以files={"result.json":{"artifactPath":"result.json","mediaType":"application/json"}}调用selftest，pass后deliver。'
        '不使用text替代文件，不修改验收器，不自行批准，不需要补充业务信息。交付或等待后结束本轮。'}
    with participants(platform,tmp_path,agent_host=host,verifier_source=VERIFIER,business=business) as (provider,assistant,host,template,task):
        def drive(check):
            pending=[value for value in provider.confirmations.list() if value['status']=='pending']
            assert not pending,{'unexpectedNativeQuestions':pending}
            return check()
        started,_=request(assistant,provider,template,task);quote_ref=started['data']['quoteRef']
        wait_for(lambda:drive(lambda:terminal(assistant,quote_ref)),timeout=180)
        result=terminal(assistant,quote_ref);assert result['status']=='completed',result
        selected,_,_=confirm(assistant,result['data']);ref=selected['data']['contractRef']
        wait_for(lambda:drive(lambda:status(assistant,ref)),timeout=180)
        assert status(assistant,ref)['data']['formed']
        key,_=invoke(assistant,ref,'start');wait_for(lambda:operation(assistant,ref,key))
        wait_for(lambda:drive(lambda:status(assistant,ref)['data']['stepRequest']),timeout=180)
        sr=status(assistant,ref)['data']['stepRequest'];key,pending=invoke(assistant,ref,'answer_step',stepRequestDigest=objects.object_digest(sr))
        user_decision(assistant,pending['data']['confirmationRef'],decision='answer',answers=[{'selected_options':['allow']}])
        LocalClient(assistant.config.home).call('assistant.delegation.answer_step',{'contractRef':ref,'stepRequestDigest':objects.object_digest(sr)},operation_id=key)
        wait_for(lambda:drive(lambda:status(assistant,ref)['data']['availablePeriods']==1),timeout=180)
        key,_=invoke(assistant,ref,'fetch',period=1);wait_for(lambda:operation(assistant,ref,key))
        key,pending=invoke(assistant,ref,'accept',period=1)
        preview=assistant.confirmations.get(pending['data']['confirmationRef'],detail=True)['binding']['preview']
        assert preview['report']['status']=='pass' and preview['amount']==8
        user_decision(assistant,pending['data']['confirmationRef'])
        LocalClient(assistant.config.home).call('assistant.delegation.accept',{'contractRef':ref,'period':1},operation_id=key)
        wait_for(lambda:operation(assistant,ref,key));assert operation(assistant,ref,key)['status']=='completed'
        wait_for(lambda:status(assistant,ref)['data']['closed'])
        actual=status(assistant,ref)['data'];assert actual['payment']=='paid'
        production=[turn for turn in turns if json.loads(turn.input)['phase']=='production']
        assert len(production)==2 and len({turn.session_id for turn in production})==1
        file=Path(production[-1].workspace)/'artifacts/result.json'
        assert json.loads(file.read_text(encoding='utf-8'))=={'delivered':'actual file bytes'}
        calls=[value['payload'].get('tool',{}) for value in events if value.get('event_type')=='chat.tool_call']
        assert any(call.get('name')=='write_file' for call in calls),calls
        assert all(turn.definition.native_tools==(('write_file','allow'),) for turn in turns)
        assert not turns[0].allow_native_tools and all(turn.allow_native_tools for turn in production)
        assert platform.db.one('SELECT state FROM delegation_admissions WHERE id=?',actual['admissionId'])['state']=='released'
        assert platform.market.reputation(provider.identities[0].protocol_id)['completed']==1
        report={'scope':'Real original Jiuwen production, native write_file, same Session, original A2C/AP2/WASM and scripted trusted browser confirmations; not native web visibility or video',
                'model':host.capabilities()['model'],'interactionId':actual['interactionId'],'sessionId':production[-1].session_id,'cardDigest':ref,
                'nativeFileCreated':True,'contentVerifier':'expected-delivery-content','payment':'paid','closed':True,'reputationCompleted':1,
                'paymentRecords':platform.db.rows('SELECT purpose,amount FROM payment_records WHERE ref=? ORDER BY purpose',ref)}
        if os.environ.get('IOALAB_REAL_FULFILLMENT_REPORT'):private_json(Path(os.environ['IOALAB_REAL_FULFILLMENT_REPORT']),report)
