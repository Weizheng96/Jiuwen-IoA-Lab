"""Actual L2 WASM decisions with bounded, model-budgeted owner observations."""
import json
import os
from pathlib import Path
import threading
from types import SimpleNamespace
import uuid

import pytest
from a2c.codec import digest_bytes
from a2c.verifier.acceptance import run_target
from a2c.verifier.package import build
from ioalab_sdk.runtime.host import HostResult
from ioalab_sdk.runtime.observations import model_port
from ioalab_sdk.runtime.protocol import RuntimeFault
from ioalab_sdk.runtime.scheduling import Scheduler,JobHandler,JobOutcome
from ioalab_sdk.runtime.store import RuntimeStore
from conftest import wait_for

SOURCE='''from a2c_verifier import observe
def verify(ctx):
    observation=observe({'task':'Read result.json. Return exactly one key expected: JSON boolean true if its delivered field is the string actual file bytes, otherwise false. Do not return the compared strings or a verdict.', 'outputSchema':{'type':'object','properties':{'expected':{'type':'boolean'}},'required':['expected'],'additionalProperties':False}})
    ok=isinstance(observation,dict) and observation.get('expected') is True
    return {'status':'pass' if ok else 'fail','checks':[{'check':'actual-observation-value','ok':ok}]}
'''


@pytest.fixture(autouse=True)
def owned_verifier_cache(tmp_path,monkeypatch):
    monkeypatch.setenv('A2C_CACHE_DIR',str(tmp_path/'a2c-cache'))


def verifier():
    blob=build({'v.py':SOURCE},{'id':'observation@1','kind':'skill','runtime':'cpython-3.13-wasi','entry':'v:verify','reads':[]})
    return blob,{'skill':{'id':'observation@1','uri':'memory:package','digest':digest_bytes(blob),'disputeModels':['unused-arbitration-model']}}


class ObservationHost:
    def __init__(self,expected=True):self.expected=expected;self.turns=[];self.entered=threading.Event();self.release=None;self.before=None
    def capabilities(self):return {'available':True,'explicitTestDouble':True}
    def run(self,turn):
        assert not turn.definition.skills and not turn.definition.native_tools and not turn.on_interaction
        assert [tool.name for tool in turn.definition.tools]==['verifier.observe']
        assert turn.session_id is None
        data=json.loads(turn.input);assert data['files']['result.json']['text']=='{"delivered":"actual file bytes"}'
        self.turns.append(turn);self.entered.set()
        if self.before:self.before(turn)
        if self.release:assert self.release.wait(10)
        session='observation-'+str(uuid.uuid4());turn.on_session(session)
        # A model's claimed verdict cannot override the program's boolean check.
        assert turn.on_tool('verifier.observe',{'observation':{'expected':self.expected,'status':'pass'}},'observation')['status']=='recorded'
        return HostResult('completed',session,{'explicitTestDouble':True})


class Harness:
    def __init__(self,root,host,*,max_models=1):
        self.store=RuntimeStore(root/'runtime.sqlite','http://local',['a'],owner_id='owner')
        self.config=SimpleNamespace(home=root);self.require_execution=lambda:None
        self.identity=SimpleNamespace(agent_id='a',profile='personal');self.host=host
        self.blob,self.refs=verifier()
        self.scheduler=Scheduler(self.store,{'verify':JobHandler(self.verify,'verify-v1'),
            'control':JobHandler(self.control,'control-v1',False,control=True)},max_models=max_models).start()
    def verify(self,job):
        try:
            result=run_target(self.refs,fetch=lambda _:self.blob,template={'deliverables':[{'name':'result','specSchema':{}}]},card={},target='result',files={'result.json':b'{"delivered":"actual file bytes"}'},period=1,
                              model=model_port(self,self.host,self.identity,job['interaction']))
        except Exception as exc:
            self.failure={'type':type(exc).__name__,'message':str(exc)}
            raise
        return JobOutcome('completed',{'verdict':result.status,'errors':result.errors})
    def control(self,job):
        with pytest.raises(RuntimeFault,match='no model permit'):model_port(self,self.host,self.identity,job['interaction'])({}, {})
        return JobOutcome('completed',{'control':True})
    def submit(self,interaction,handler='verify'):
        return self.scheduler.submit('a',interaction,handler,{},operation_id=str(uuid.uuid4()),session='business-'+interaction)['reference']
    def close(self):self.scheduler.close();self.store.close()


@pytest.mark.parametrize('expected,verdict',[(True,'pass'),(False,'fail')])
def test_original_wasm_decides_from_observations_without_replacing_business_session(tmp_path,expected,verdict):
    host=ObservationHost(expected);runtime=Harness(tmp_path,host)
    try:
        with pytest.raises(RuntimeFault,match='current local model job'):model_port(runtime,host,runtime.identity,'one')({}, {})
        job=runtime.submit('one');wait_for(lambda:runtime.scheduler.get(job)['status'] in ('completed','unknown'))
        actual=runtime.scheduler.get(job)
        assert actual['status']=='completed',getattr(runtime,'failure',actual)
        assert actual['data']['verdict']==verdict and actual['sessionId']=='business-one'
        records=runtime.store.checkpoints('model-observations:a')
        assert len(records)==1
        record=next(iter(records.values()))
        assert record['state']=='completed' and record['sessionId'].startswith('observation-')
        assert record['interactionId']=='one' and record['files']['result.json']==digest_bytes(b'{"delivered":"actual file bytes"}')
        assert len(host.turns)==1
    finally:runtime.close()


def test_single_model_budget_allows_nested_observation_and_parallel_protocol_control(tmp_path):
    host=ObservationHost();host.release=threading.Event();runtime=Harness(tmp_path,host,max_models=1)
    try:
        first=runtime.submit('one');assert host.entered.wait(5)
        second=runtime.submit('two');control=runtime.submit('one',handler='control')
        wait_for(lambda:runtime.scheduler.get(control)['status']=='completed')
        assert runtime.scheduler.get(first)['status']=='running' and runtime.scheduler.get(second)['status']=='queued'
        assert len(host.turns)==1
        host.release.set();wait_for(lambda:all(runtime.scheduler.get(job)['status']=='completed' for job in (first,second)))
        assert all(runtime.scheduler.get(job)['data']['verdict']=='pass' for job in (first,second))
        assert len(host.turns)==2
    finally:host.release.set();runtime.close()


def test_missing_observation_host_propagates_the_actual_error_without_claiming_pass(tmp_path):
    runtime=Harness(tmp_path,None)
    try:
        job=runtime.submit('one');wait_for(lambda:runtime.scheduler.get(job)['status'] in ('completed','unknown'))
        assert runtime.scheduler.get(job)['status']=='unknown'
        assert runtime.failure['type']=='RuntimeFault' and 'owner observation model' in runtime.failure['message']
        assert not runtime.store.checkpoints('model-observations:a')
    finally:runtime.close()


@pytest.mark.parametrize('decision',['approve','reject','expire','bad-observation','missing-host'])
def test_resident_l2_selftest_async_preview_real_confirmation_and_original_payment(platform,tmp_path,monkeypatch,decision):
    from concurrent.futures import ThreadPoolExecutor
    from a2c import objects
    from ioalab_sdk.runtime.ipc import LocalClient
    from test_resident_delegation import participants
    from test_resident_contracts import candidate,confirm,status
    from test_resident_fulfillment import ProductionHost,configure_template,invoke,operation
    from test_assistant_role import user_decision
    class Host(ProductionHost):
        def __init__(self):super().__init__();self.observer=ObservationHost()
        def run(self,turn):
            return self.observer.run(turn) if turn.definition.name=='ioa_verifier_observation' else super().run(turn)
    configure_template(monkeypatch);host=Host()
    def arbitration_only(*args):raise AssertionError('Normal acceptance must use the owner model, not the registered arbitration model')
    monkeypatch.setitem(platform.market.market.models,'test-observation-model',arbitration_only)
    assistant_host=None if decision=='missing-host' else ObservationHost(decision!='bad-observation')
    if assistant_host:assistant_host.release=threading.Event()
    with participants(platform,tmp_path,agent_host=host,assistant_host=assistant_host,models=1,verifier_source=SOURCE,verifier_kind='skill') as (provider,assistant,host,template,task):
        lock_checks=[]
        def check_unlocked(turn):
            endpoint=provider.modules[0].execution.contexts[turn.interaction_id]
            def acquire():
                acquired=endpoint.lock.acquire(timeout=1)
                if acquired:endpoint.lock.release()
                return acquired
            with ThreadPoolExecutor(max_workers=1) as pool:assert pool.submit(acquire).result(timeout=2),'selftest held the endpoint lock during observation'
            lock_checks.append(True)
        host.observer.before=check_unlocked
        quote=candidate(assistant,provider,template,task);selected,_,_=confirm(assistant,quote);ref=selected['data']['contractRef']
        wait_for(lambda:status(assistant,ref))
        formed=status(assistant,ref)
        assert formed['status']=='completed' and formed['data']['formed'],formed
        key,_=invoke(assistant,ref,'start');wait_for(lambda:operation(assistant,ref,key))
        wait_for(lambda:status(assistant,ref)['data']['stepRequest'])
        step=status(assistant,ref)['data']['stepRequest'];key,pending=invoke(assistant,ref,'answer_step',stepRequestDigest=objects.object_digest(step))
        user_decision(assistant,pending['data']['confirmationRef'],decision='answer',answers=[{'selected_options':['allow']}])
        client=LocalClient(assistant.config.home)
        client.call('assistant.delegation.answer_step',{'contractRef':ref,'stepRequestDigest':objects.object_digest(step)},operation_id=key)
        wait_for(lambda:operation(assistant,ref,key))
        wait_for(lambda:status(assistant,ref)['data']['availablePeriods']==1)
        assert lock_checks==[True] and len(host.observer.turns)==1
        key,_=invoke(assistant,ref,'fetch',period=1);wait_for(lambda:operation(assistant,ref,key))
        before=status(assistant,ref)['data'];interaction=before['interactionId']
        original_session=provider.store.interaction(interaction_id=interaction)['session']
        key,started=invoke(assistant,ref,'accept',period=1);assert started['status']=='accepted'
        job=started['data']['job']['reference']
        try:
            if assistant_host:
                assert assistant_host.entered.wait(5)
                assert 'preview' not in assistant.modules[0].fulfillment.record(key)
                assert client.call('runtime.status')['status']=='completed'
                assert not status(assistant,ref)['data']['card']['signatures']['7']
                assistant_host.release.set()
            wait_for(lambda:assistant.scheduler.get(job)['status'] in ('waiting','completed','rejected','failed','unknown'))
            preview=assistant.scheduler.get(job)
            if decision in ('bad-observation','missing-host'):
                assert preview['status']==('rejected' if decision=='bad-observation' else 'failed'),preview
                if decision=='bad-observation':assert preview['data']['report']['status']=='fail'
                else:assert preview['data']['error']['code']=='not_ready'
            else:
                assert preview['status']=='waiting',preview
                confirmation=preview['data']['confirmationRef']
                assert assistant.confirmations.get(confirmation,detail=True)['binding']['preview']['report']['status']=='pass'
                if decision=='expire':
                    clock=assistant.confirmations.clock
                    assistant.confirmations.clock=lambda:clock()+901
                else:user_decision(assistant,confirmation,decision=decision)
                if decision in ('reject','expire'):
                    # The real user decision/expiry alone ends the wait; no model
                    # or SDK accept call is needed to release this interaction.
                    wait_for(lambda:assistant.scheduler.get(job)['status']=='rejected')
                    assert assistant.scheduler.get(job)['data']['reason']==('expired' if decision=='expire' else 'rejected')
                    assert client.call('assistant.delegation.accept',{'contractRef':ref,'period':1},operation_id=key)['status']=='rejected'
                    checked,_=invoke(assistant,ref,'verify',period=1)
                    wait_for(lambda:operation(assistant,ref,checked))
                    assert operation(assistant,ref,checked)['status']=='completed'
                else:
                    resumed=client.call('assistant.delegation.accept',{'contractRef':ref,'period':1},operation_id=key)
                    assert resumed['status']=='accepted' and resumed['data']['job']['reference']==job
                    wait_for(lambda:operation(assistant,ref,key))
                    assert operation(assistant,ref,key)['status']=='completed'
                    wait_for(lambda:status(assistant,ref)['data']['closed'])
                    assert status(assistant,ref)['data']['payment']=='paid'
                    assert len(assistant_host.turns)==2,'actual acceptance must rerun the original L2 verifier'
            if decision!='approve':
                assert not status(assistant,ref)['data']['card']['signatures']['7']
                assert not platform.db.rows("SELECT * FROM payment_records WHERE ref=? AND purpose='installment'",ref)
            assert provider.store.interaction(interaction_id=interaction)['session']==original_session
            assert all(turn.session_id is None for turn in host.observer.turns)
        finally:
            if assistant_host:assistant_host.release.set()


@pytest.mark.skipif(not os.environ.get('IOALAB_REAL_JIUWEN_DATA_DIR'),reason='requires explicitly configured original Jiuwen/model')
def test_real_original_jiuwen_observation_is_judged_by_actual_l2_wasm(tmp_path):
    from ioalab_jiuwen.process import ProcessHost
    host=ProcessHost(json.loads(os.environ['IOALAB_REAL_JIUWEN_COMMAND']),os.environ['IOALAB_REAL_JIUWEN_DATA_DIR'],model=os.environ.get('IOALAB_REAL_JIUWEN_MODEL'))
    assert host.probe()['available'],host.capabilities()
    runtime=Harness(tmp_path,host)
    try:
        job=runtime.submit('real');wait_for(lambda:runtime.scheduler.get(job)['status'] in ('completed','unknown'),timeout=150)
        actual=runtime.scheduler.get(job);assert actual['status']=='completed' and actual['data']['verdict']=='pass',actual
        records=list(runtime.store.checkpoints('model-observations:a').values())
        assert len(records)==1 and records[0]['state']=='completed'
        assert records[0]['sessionId']!=actual['sessionId']
        if os.environ.get('IOALAB_REAL_L2_REPORT'):
            from ioalab_sdk.runtime import observations,scheduling
            from ioalab_jiuwen import process
            sources={label:digest_bytes(Path(module.__file__).read_bytes()) for label,module in (
                ('src/access/sdk/ioalab_sdk/runtime/observations.py',observations),
                ('src/access/sdk/ioalab_sdk/runtime/scheduling.py',scheduling),
                ('src/hosts/jiuwen/ioalab_jiuwen/process.py',process))}
            Path(os.environ['IOALAB_REAL_L2_REPORT']).write_text(json.dumps({'scope':'Real original Jiuwen JSON observations consumed by actual L2 WASM; not full business, native web or video',
                'model':host.capabilities()['model'],'verdict':actual['data']['verdict'],'observation':records[0],
                'verifierSourceDigest':digest_bytes(SOURCE.encode()),'sources':sources},ensure_ascii=False,indent=2),encoding='utf-8')
    finally:runtime.close()
