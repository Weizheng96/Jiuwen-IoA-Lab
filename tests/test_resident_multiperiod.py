"""Two original A2C periods over actual HTTP/WS; explicit host and owner drivers."""
import base64
import json
import os
from pathlib import Path
import traceback
from contextvars import ContextVar

import httpx

import pytest
from a2c import objects
from a2c.client import Client
from a2c.slots import verify_card
from ioalab_sdk.assistant.role import AssistantRole
from ioalab_sdk.assistant.fulfillment import Fulfillment
from ioalab_sdk.provider import ProviderRole
from ioalab_sdk.runtime.host import HostResult
from ioalab_sdk.runtime.files import private_json
from ioalab_sdk.runtime.ipc import LocalClient
from ioalab_sdk.runtime.files_api import PrivateFiles
from ioalab_sdk.adapters.transport import SessionTransport
from ioalab_sdk.runtime.protocol import RuntimeFault
from ioalab_sdk.runtime.resident import ResidentRuntime
from conftest import wait_for
from test_assistant_role import user_decision
from test_resident_contracts import confirm
from test_resident_delegation import participants,terminal,uid
from test_resident_fulfillment import invoke,operation
from test_task_verifiers import contract_state


VERIFIER='''import json
def verify(ctx):
    try:
        value=json.loads(ctx.read("delivery.json"))
        ok=value=={"part":ctx.target,"value":"contents for "+ctx.target}
    except Exception:
        ok=False
    return {"status":"pass" if ok else "fail","checks":[{"name":"current-period-content","pass":ok}]}
'''


def terms(formation,reference):
    value={'formation':formation,'deliverables':[{'name':name,'description':name,'specSchema':{}} for name in ('draft','release')],
        'inputs':[],'deadlines':{'respondWithin':'PT10M','startWithin':'PT30M','authorizeWithin':'PT15M',
            'deliverWithin':{'draft':'PT30M','release':'PT30M'}},
        'pricing':{'currency':'CNY','depositFormula':'1','paymentWeights':{'draft':1,'release':1}},
        'authorizableSteps':[{'name':'release_permission','description':'每期均需原用户同意的业务环节'}],'samples':[]}
    if formation=='proposal':value['pricing']['proposalFee']=1
    else:
        value['pricing'].update(priceFactors={'draft':{},'release':{}},priceFormula={'draft':'3','release':'6'})
        value['deadlines']['acceptWithin']={'draft':'PT10M','release':'PT10M'}
        value['acceptance']={name:{'verifiers':{'script':reference},'notCovered':[]} for name in ('draft','release')}
    return value


class TwoPeriodHost:
    def __init__(self):self.turns=[];self.verifier=None;self.failure=None
    def capabilities(self):return {'available':True,'explicitTwoPeriodHostDouble':True}
    def cancel(self,operation):return True
    def run(self,turn):
        try:return self.execute(turn)
        except Exception as exc:self.failure={'type':type(exc).__name__,'message':str(exc)};raise
    def execute(self,turn):
        self.turns.append(turn);sid=turn.session_id or 'two-period-'+uid();turn.on_session(sid)
        data=json.loads(turn.input)
        if data['phase']=='quote':
            result=turn.on_tool('provider.task.quote',{
                'acceptance':{name:{'verifiers':{'script':self.verifier},'notCovered':[]} for name in ('draft','release')},
                'acceptWithin':{'draft':'PT10M','release':'PT10M'},'lineItems':{'draft':3,'release':6}},'quote')
            assert result['status']=='recorded',result
        elif data['phase']=='decision':
            assert turn.on_tool('provider.task.decision',{'approved':True},'decision')['status']=='recorded'
        else:
            period=data['period'];assert period==len(data['card']['signatures']['7'])+1
            current=[step for step in data['steps'] if step['request']['period']==period]
            if not current:
                result=turn.on_tool('provider.task.request_step',{'step':'release_permission'},'step-'+str(period))
                assert result['status']=='waiting',result
            else:
                assert current[-1]['answer']['granted']
                target=data['card']['spec'];name=('draft','release')[period-1];assert name in target
                files={'delivery.json':{'mediaType':'application/json','text':json.dumps({'part':name,'value':'contents for '+name})}}
                tested=turn.on_tool('provider.task.selftest',{'files':files},'selftest-'+str(period))
                assert tested['status']=='pass',tested
                result=turn.on_tool('provider.task.deliver',{'files':files},'delivery-'+str(period))
                assert result['status']=='delivered',result
        return HostResult('completed',sid,{'explicitTwoPeriodHostDouble':True})


def await_operation(assistant,reference,key):
    wait_for(lambda:operation(assistant,reference,key))
    result=operation(assistant,reference,key)
    assert result['status']=='completed',{'result':result,'diagnostic':getattr(assistant,'two_period_diagnostic',None)}
    return result


def approve_step(assistant,reference,period,old_digest=None,*,timeout=20):
    wait_for(lambda:(contract_state(assistant,reference).get('stepRequest') or {}).get('period')==period,timeout=timeout)
    step=contract_state(assistant,reference)['stepRequest'];digest=objects.object_digest(step)
    if old_digest:
        previous=LocalClient(assistant.config.home).call('assistant.delegation.answer_step',{'contractRef':reference,'stepRequestDigest':old_digest})
        assert previous['status']=='rejected' and previous['error']['code']=='forbidden',previous
        assert contract_state(assistant,reference)['stepRequest']==step
    key,pending=invoke(assistant,reference,'answer_step',stepRequestDigest=digest)
    assert pending['status']=='waiting',pending
    user_decision(assistant,pending['data']['confirmationRef'],decision='answer',answers=[{'selected_options':['allow']}])
    result=LocalClient(assistant.config.home).call('assistant.delegation.answer_step',{'contractRef':reference,'stepRequestDigest':digest},operation_id=key)
    assert result['status']=='accepted',result
    await_operation(assistant,reference,key)
    return digest


def accept_period(assistant,reference,period,prior_confirmation=None,*,recover_payment=False):
    key,pending=invoke(assistant,reference,'accept',period=period)
    job=pending['data']['job']['reference']
    wait_for(lambda:assistant.scheduler.get(job)['status'] in ('waiting','completed','rejected','failed','unknown'))
    current=assistant.scheduler.get(job);assert current['status']=='waiting',{'result':current,'diagnostic':getattr(assistant,'two_period_diagnostic',None)}
    confirmation=current['data']['confirmationRef']
    if prior_confirmation:
        rejected=LocalClient(assistant.config.home).call('assistant.delegation.accept',{'contractRef':reference,'period':period,'confirmationRef':prior_confirmation},operation_id=key)
        assert rejected['status']=='rejected' and rejected['error']['code']=='id_conflict',rejected
        assert len(contract_state(assistant,reference)['card']['signatures']['7'])==period-1
    preview=assistant.confirmations.get(confirmation,detail=True)['binding']['preview']
    assert preview['period']==period and preview['report']['status']=='pass' and preview['amount']==4
    user_decision(assistant,confirmation)
    result=LocalClient(assistant.config.home).call('assistant.delegation.accept',{'contractRef':reference,'period':period},operation_id=key)
    assert result['status']=='accepted',result
    if recover_payment:
        wait_for(lambda:(operation(assistant,reference,key) or {}).get('status')=='completed')
    await_operation(assistant,reference,key)
    return key,confirmation


def run_two_periods(platform,tmp_path,monkeypatch,formation,scenario,*,host=None,business=None,timeout=20):
    host=host or TwoPeriodHost();fresh=[]
    wait=lambda condition:wait_for(condition,timeout=timeout)
    fulfill_run=Fulfillment.run
    def diagnosed(owner,job):
        try:return fulfill_run(owner,job)
        except Exception:
            owner.runtime.two_period_diagnostic=traceback.format_exc();raise
    monkeypatch.setattr(Fulfillment,'run',diagnosed)
    with participants(platform,tmp_path,formation=formation,fee=1 if formation=='proposal' else 0,agent_host=host,
        verifier_source=VERIFIER,business=business,template_terms=lambda reference:terms(formation,reference)) as (provider,assistant,host,template,task):
        args={'taskRef':task,'service':provider.identities[0].agent_id,'templateDigest':template['templateDigest'],'spec':{'draft':{},'release':{}}}
        quoted=LocalClient(assistant.config.home).call('assistant.delegation.request',args)
        wait(lambda:terminal(assistant,quoted['data']['quoteRef']))
        quote=terminal(assistant,quoted['data']['quoteRef']);assert quote['status']=='completed',quote
        selected,_,_=confirm(assistant,quote['data']);reference=selected['data']['contractRef']
        wait(lambda:contract_state(assistant,reference).get('formed'))
        relation=provider.store.interaction(interaction_id=contract_state(assistant,reference)['interactionId'])
        interaction,sid=relation['id'],relation['session'];assert sid
        admission=contract_state(assistant,reference)['admissionId']
        key,_=invoke(assistant,reference,'start');await_operation(assistant,reference,key)
        delivery_task=assistant.modules[0].fulfillment.client(reference)[0].store.get('cards',reference)['task']
        first_step=approve_step(assistant,reference,1,timeout=timeout)
        wait(lambda:contract_state(assistant,reference).get('availablePeriods')==1)
        key,_=invoke(assistant,reference,'fetch',period=1);await_operation(assistant,reference,key)
        assert len([turn for turn in host.turns if json.loads(turn.input)['phase']=='production'])==2
        assert not platform.db.rows("SELECT * FROM payment_records WHERE ref=? AND purpose='installment'",reference)
        assert platform.db.one('SELECT state FROM delegation_admissions WHERE id=?',admission)['state']=='committed'
        dropped=[]
        if scenario=='lost-first-payment':
            connection_call=assistant.connection.call
            def lose_once(actor,kind,body,**kwargs):
                result=connection_call(actor,kind,body,**kwargs)
                if kind=='ap2.pay' and len(body.get('ap2',{}).get('card',{}).get('signatures',{}).get('7',[]))==1 and not dropped:
                    dropped.append(result);raise RuntimeFault('unknown_result','Actual first-period payment reply lost')
                return result
            monkeypatch.setattr(assistant.connection,'call',lose_once)
        first_accept,first_confirmation=accept_period(assistant,reference,1,recover_payment=scenario=='lost-first-payment')
        wait(lambda:(contract_state(assistant,reference).get('stepRequest') or {}).get('period')==2)
        middle=contract_state(assistant,reference)
        assert middle['acceptedPeriods']==1 and middle['payment']=='partial' and not middle['closed']
        assert platform.db.one('SELECT state FROM delegation_admissions WHERE id=?',admission)['state']=='committed'
        assert platform.market.reputation(provider.identities[0].protocol_id)['completed']==0
        assert bool(dropped)==(scenario=='lost-first-payment')
        if scenario=='restart':
            wait_for(lambda:provider.scheduler.status()['active']==0 and assistant.scheduler.status()['active']==0)
            pconfig,aconfig=provider.config,assistant.config;provider.close();assistant.close()
            provider=ResidentRuntime(pconfig,modules=(ProviderRole(host),),host_status=host.capabilities).start();fresh.append(provider)
            assistant=ResidentRuntime(aconfig,modules=(AssistantRole(),)).start();fresh.append(assistant)
            wait_for(lambda:provider.connection.connected and assistant.connection.connected)
        try:
            approve_step(assistant,reference,2,first_step,timeout=timeout)
            wait(lambda:contract_state(assistant,reference).get('availablePeriods')==2)
            read_failures=[];saved_receipt=[]
            if scenario=='after-receipt-loss':
                original_fetch=Client.fetch_delivery
                def lose_after_receipt(client,contract,period):
                    result=original_fetch(client,contract,period)
                    if contract==reference and period==2 and not saved_receipt:
                        saved_receipt.append(client.card(contract)['signatures']['6'][1])
                        raise httpx.RemoteProtocolError('Read result lost after real receipt persisted',request=httpx.Request('GET','http://isolated.test/delivery'))
                    return result
                monkeypatch.setattr(Client,'fetch_delivery',lose_after_receipt)
            if scenario=='file-read-failure':
                reading=ContextVar('two-period-file-read',default=False)
                original_reply,original_get=SessionTransport._reply,PrivateFiles.get
                def scoped_reply(transport,payload):
                    card=payload.get('reply',{}).get('body',{}).get('card') or {}
                    token=reading.set(transport.runtime is assistant and len(card.get('signatures',{}).get('5',[]))==2 and bool(payload.get('files')))
                    try:return original_reply(transport,payload)
                    finally:reading.reset(token)
                def lose_read(files,reference):
                    if reading.get() and not read_failures:
                        read_failures.append(reference)
                        raise httpx.RemoteProtocolError('Immutable file GET disconnected',request=httpx.Request('GET','http://isolated.test/delivery'))
                    return original_get(files,reference)
                monkeypatch.setattr(SessionTransport,'_reply',scoped_reply)
                monkeypatch.setattr(PrivateFiles,'get',lose_read)
            key,_=invoke(assistant,reference,'fetch',period=2)
            if scenario=='file-read-failure':
                wait_for(lambda:operation(assistant,reference,key))
                failure=operation(assistant,reference,key)
                assert failure['status']=='failed' and failure['data']['error']['recovery']=='retry_read',failure
                protocol,_=assistant.modules[0].fulfillment.client(reference)
                original=protocol.store.get('cards',reference)
                assert len(original['card']['signatures']['6'])==1 and '2' not in original.get('deliveries',{})
                assert len(platform.db.rows('SELECT * FROM payment_records WHERE ref=?',reference))==2
                replay=LocalClient(assistant.config.home).call('assistant.delegation.fetch',{'contractRef':reference,'period':2},operation_id=key)
                assert replay['status']=='failed' and len(read_failures)==1
                key,_=invoke(assistant,reference,'fetch',period=2)
            if scenario=='after-receipt-loss':
                wait_for(lambda:(operation(assistant,reference,key) or {}).get('status')=='completed')
            await_operation(assistant,reference,key)
            second_accept,second_confirmation=accept_period(assistant,reference,2,first_confirmation)
            wait_for(lambda:contract_state(assistant,reference).get('closed'))
            actual=contract_state(assistant,reference)
            protocol,_=assistant.modules[0].fulfillment.client(reference)
            stored=protocol.store.get('cards',reference)
            assert stored['task']==delivery_task
            assert verify_card(template,actual['card'],protocol.keys).accepted==2
            assert all(len(actual['card']['signatures'][str(index)])==2 for index in (4,5,6,7))
            assert actual['interactionId']==interaction and actual['payment']=='paid'
            if saved_receipt:assert actual['card']['signatures']['6'][1]==saved_receipt[0]
            assert provider.store.interaction(interaction_id=interaction)['session']==sid
            assert len({turn.session_id for turn in host.turns if turn.session_id})==1
            files={period:json.loads(base64.b64decode(stored['deliveries'][str(period)]['delivery.json'])) for period in (1,2)}
            assert files[1]['part']=='draft' and files[2]['part']=='release' and files[1]!=files[2]
            assert platform.db.rows('SELECT purpose,period,amount FROM payment_records WHERE ref=? ORDER BY purpose,period',reference)==[
                {'purpose':'deposit','period':0,'amount':'1.00'},{'purpose':'installment','period':1,'amount':'4.00'},{'purpose':'installment','period':2,'amount':'4.00'}]
            assert platform.db.one('SELECT state FROM delegation_admissions WHERE id=?',actual['admissionId'])['state']=='released'
            assert platform.market.reputation(provider.identities[0].protocol_id)['completed']==1
            for period,key in ((1,first_accept),(2,second_accept)):
                replay=LocalClient(assistant.config.home).call('assistant.delegation.accept',{'contractRef':reference,'period':period},operation_id=key)
                assert replay['status']=='completed',replay
            key,_=invoke(assistant,reference,'fetch',period=1);await_operation(assistant,reference,key)
            assert len(platform.db.rows('SELECT * FROM payment_records WHERE ref=?',reference))==3
            assert len([turn for turn in host.turns if json.loads(turn.input)['phase']=='production'])==4
            reports=[assistant.confirmations.get(value,detail=True)['binding']['preview']['report'] for value in (first_confirmation,second_confirmation)]
            return {'formation':formation,'scenario':scenario,'interactionId':interaction,'sessionId':sid,'taskId':delivery_task,
                'contractRef':reference,'periods':2,'wasmReports':reports,'paymentRecords':platform.db.rows('SELECT purpose,period,amount FROM payment_records WHERE ref=? ORDER BY purpose,period',reference),
                'closed':True,'reputationCompleted':1,'capacityReleased':True,'productionRounds':4,
                'deliveryFiles':{str(period):{'part':value['part']} for period,value in files.items()},
                'limits':['Public backend SDK and original A2C/WASM/AP2; owner confirmations are isolated test drivers',
                    'Configured prepublished verifier; not per-request checker generation, native frontend, actual miniapp, Linux or new film']}
        finally:
            for runtime in reversed(fresh):runtime.close()


@pytest.mark.parametrize('formation',['proposal','countersign'])
@pytest.mark.parametrize('scenario',['normal','restart','lost-first-payment','file-read-failure','after-receipt-loss'])
def test_two_periods_keep_original_context_each_user_gate_files_and_actual_payments(platform,tmp_path,monkeypatch,formation,scenario):
    run_two_periods(platform,tmp_path,monkeypatch,formation,scenario)


@pytest.mark.skipif(not os.environ.get('IOALAB_REAL_JIUWEN_COMMAND'),reason='Opt-in original Jiuwen model invocation')
def test_original_jiuwen_completes_two_periods_in_the_same_session(platform,tmp_path,monkeypatch):
    from ioalab_jiuwen.process import ProcessHost
    host=ProcessHost(json.loads(os.environ['IOALAB_REAL_JIUWEN_COMMAND']),os.environ['IOALAB_REAL_JIUWEN_DATA_DIR'],model=os.environ.get('IOALAB_REAL_JIUWEN_MODEL'))
    assert host.probe()['available'],host.capabilities()
    host.turns=[];native_run=host.run
    def recorded(turn):host.turns.append(turn);return native_run(turn)
    host.run=recorded
    business={'instructions':'本业务按两期交付draft、release。quote使用业务资源outputVerifier作为两期真实script引用，验收各PT10M，lineItems分别为draft=3、release=6。'
        '每期production先检查本期period的release_permission授权；缺少本期答复则调用request_step并结束，不复用其他期次的同意。仅本期答复granted=true后才生产。'
        '每期交付文件名为delivery.json，JSON对象有part和value两项。首期完整内容为{"part":"draft","value":"contents for draft"}；第二期为{"part":"release","value":"contents for release"}。'
        'selftest通过后以files的text交付实际JSON字节。当前信息完整，不需要外部工具或补充提问。用户授权由SDK真实环节处理，不自行同意。'}
    result=run_two_periods(platform,tmp_path,monkeypatch,'proposal','normal',host=host,business=business,timeout=180)
    result.update(model=host.capabilities()['model'],scope='Original Jiuwen public Process completes two sequential A2C periods, each real user gate/WASM/AP2 in one UUID/task/Session')
    if os.environ.get('IOALAB_REAL_MULTIPERIOD_REPORT'):private_json(Path(os.environ['IOALAB_REAL_MULTIPERIOD_REPORT']),result)
