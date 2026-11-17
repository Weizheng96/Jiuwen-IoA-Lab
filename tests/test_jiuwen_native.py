"""Native product/control boundary, actual SDK setup ledger; no host or model."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import hashlib
import asyncio
from dataclasses import dataclass,replace
from enum import Enum
import importlib.util
from importlib.resources import files
import sys
from types import SimpleNamespace
import time

import pytest

from ioalab_jiuwen.native_host import native_adapter
from ioalab_sdk.runtime import (BindingAck,HostDefinition,HostExecution,HostInput,
    HostRegistry,RuntimeFault,RuntimeStore,SubmissionAttempt,encode)
from ioalab_sdk.runtime.native_sources import NativeSources


@contextmanager
def native(tmp_path):
    bundle=native_adapter(host_ref='host',resource_root=tmp_path/'native')
    host=bundle['host'];resource=bundle['native_resources']['host']
    store=RuntimeStore(tmp_path/'runtime.sqlite','http://platform',['actor'],owner_id='owner')
    registry=HostRegistry({'host':host},'host');registry.bind(store)
    sources=NativeSources(store,registry,[SimpleNamespace(profile='assistant',agent_id='actor')],
        {'releaseId':'native-fixture'},'account',tmp_path)
    sources.attach({'url':'http://127.0.0.1:12345','bootId':'boot'})
    issuer=sources.provision('host','assistant')
    from ioalab_sdk.runtime import native_source_descriptor
    descriptor=json.loads(native_source_descriptor(tmp_path,'host','assistant').read_text(encoding='utf-8'))
    authorization='Bearer '+descriptor['grant']
    scope=sources.authenticate(authorization)
    reference=issuer({'sessionId':'source-sid','workspace':str(tmp_path),'captureId':'source-call'},authorization)
    resource.bind_entry_control(sources.entry_control_for('host'));resource.start()
    source={'sessionId':'source-sid','requestId':'source-request','userId':'native-user',
        'channelId':'web','workspace':str(tmp_path),'projectId':'native-project','projectDir':str(tmp_path),
        'mode':'agent.work.normal','workMode':'normal'}
    poll={'purpose':'jiuwen.native','action':'poll','workerId':'worker','entryRef':reference['entryRef'],'source':source,'features':[]}
    handler=lambda body:sources.exchange(authorization,body,bundle['native_handlers'])
    try:yield bundle,resource,sources,reference,poll,handler,scope
    finally:resource.close();store.close()


def bind(sources,reference,handler,poll):
    source=sources.setup_source(reference)
    assert sources.entry_bindings.begin('setup',source=source)['phase']=='intent'
    assert handler(poll)['bound'] is True
    assert sources.entry_bindings.get('setup')['phase']=='bound'


def test_native_setup_requires_original_sdk_intent_then_saves_and_completes(tmp_path):
    with native(tmp_path) as (bundle,resource,sources,reference,poll,handler,_):
        assert handler(poll)=={'bound':False,'command':None}
        assert not (tmp_path/'native'/'binding.json').exists()
        bind(sources,reference,handler,poll)
        original=(tmp_path/'native'/'binding.json').read_bytes()
        assert handler(poll)['bound'] is True
        assert (tmp_path/'native'/'binding.json').read_bytes()==original
        changed={**poll,'source':{**poll['source'],'userId':'another-native-user'}}
        with pytest.raises(RuntimeFault):handler(changed)
        assert (tmp_path/'native'/'binding.json').read_bytes()==original


def test_native_valid_binding_restart_reuses_original_receipt(tmp_path):
    with native(tmp_path) as (_,resource,sources,reference,poll,handler,scope):
        bind(sources,reference,handler,poll);resource.close()
        original=(tmp_path/'native'/'binding.json').read_bytes()
        restarted=native_adapter(host_ref='host',resource_root=tmp_path/'native')
        entry=restarted['native_resources']['host'];entry.bind_entry_control(sources.entry_control_for('host'));entry.start()
        assert entry.exchange(scope,poll)['bound'] is True
        assert (tmp_path/'native'/'binding.json').read_bytes()==original
        entry.close()


def test_native_saved_receipt_recovers_after_sdk_complete_ack_is_lost(tmp_path):
    with native(tmp_path) as (_,resource,sources,reference,poll,handler,scope):
        original=sources.entry_control_for('host');calls=[]
        def lose_ack(scope,payload):
            value=original(scope,payload)
            if payload['action']=='complete' and not calls:
                calls.append(payload);raise OSError('lost complete acknowledgement')
            return value
        resource.bind_entry_control(lose_ack)
        sources.entry_bindings.begin('setup',source=sources.setup_source(reference))
        with pytest.raises(OSError):handler(poll)
        receipt=(tmp_path/'native'/'binding.json').read_bytes()
        assert sources.entry_bindings.get('setup')['phase']=='bound'
        assert handler(poll)['bound'] is True
        assert (tmp_path/'native'/'binding.json').read_bytes()==receipt


def test_native_context_save_failure_recovers_original_claim_and_receipt(tmp_path,monkeypatch):
    from ioalab_jiuwen import native_transport
    with native(tmp_path) as (_,resource,sources,reference,poll,handler,_):
        write=native_transport.private_json;failed=[]
        def disk(path,value):
            if path.name=='binding.json' and not failed:
                assert sources.entry_bindings.get('setup')['phase']=='reserved'
                failed.append(value['savedReceiptRef']);raise OSError('disk rejected native save')
            write(path,value)
        monkeypatch.setattr(native_transport,'private_json',disk)
        sources.entry_bindings.begin('setup',source=sources.setup_source(reference))
        with pytest.raises(OSError):handler(poll)
        assert not (tmp_path/'native'/'binding.json').exists()
        assert sources.entry_bindings.get('setup')['phase']=='reserved'
        assert handler(poll)['bound'] is True
        saved=json.loads((tmp_path/'native'/'binding.json').read_text(encoding='utf-8'))
        assert saved['savedReceiptRef']==failed[0]


@pytest.mark.parametrize('change',[
    lambda r:r['source'].update(userId='changed-owner'),
    lambda r:r['source'].update(sessionId='changed-session'),
    lambda r:r['source'].update(workspace='relative'),
    lambda r:r['source'].update(projectId='changed-project'),
    lambda r:r['scope'].update(accountBindingRef='changed-account'),
    lambda r:r.update(installationRef='changed-installation'),
    lambda r:r.update(hostRef='changed-host'),
    lambda r:r.update(format='unknown-format'),
    lambda r:r.update(unexpected='extra-field'),
    lambda r:r.update(targetDigest=None),
    lambda r:r.update(savedReceiptRef=[]),
])
def test_native_tampered_binding_restart_cannot_ack_save_or_dispatch(tmp_path,change):
    with native(tmp_path) as (_,resource,sources,reference,poll,handler,scope):
        bind(sources,reference,handler,poll);resource.close()
        path=tmp_path/'native'/'binding.json'
        saved=json.loads(path.read_text(encoding='utf-8'));change(saved)
        path.write_text(json.dumps(saved),encoding='utf-8');tampered=path.read_bytes()
        restarted=native_adapter(host_ref='host',resource_root=tmp_path/'native')
        entry=restarted['native_resources']['host'];actions=[]
        trusted=sources.entry_control_for('host')
        def control(scope,payload):
            actions.append(payload['action']);return trusted(scope,payload)
        entry.bind_entry_control(control)
        with pytest.raises(RuntimeFault):entry.start()
        with pytest.raises(RuntimeFault):entry.exchange(scope,poll)
        result=restarted['host'].submit('never-create',HostInput('notice',attempt=SubmissionAttempt(
            'nonce','scope','actor','assistant')),on_event=lambda e:None)
        assert result.request_id is None and result.error.dispatch=='not_submitted'
        assert actions==[] and path.read_bytes()==tampered
        assert not (tmp_path/'native'/('request-'+hashlib.sha256(b'never-create').hexdigest()+'.json')).exists()


def test_native_recomputed_tampered_target_cannot_reuse_original_sdk_claim(tmp_path):
    with native(tmp_path) as (_,resource,sources,reference,poll,handler,scope):
        bind(sources,reference,handler,poll);resource.close()
        path=tmp_path/'native'/'binding.json';plan=tmp_path/'native'/'setup-obligation.json'
        saved=json.loads(path.read_text(encoding='utf-8'))
        saved['source']['sessionId']='substituted-session'
        target={k:saved[k] for k in ('hostRef','installationRef','accountBindingRef','source')}
        saved['targetDigest']=hashlib.sha256(encode(target).encode()).hexdigest()
        obligation=json.loads(plan.read_text(encoding='utf-8'));obligation['targetDigest']=saved['targetDigest']
        path.write_text(json.dumps(saved),encoding='utf-8');plan.write_text(json.dumps(obligation),encoding='utf-8')
        original=path.read_bytes()
        restarted=native_adapter(host_ref='host',resource_root=tmp_path/'native')
        entry=restarted['native_resources']['host'];entry.bind_entry_control(sources.entry_control_for('host'));entry.start()
        with pytest.raises(RuntimeFault):entry.exchange(scope,poll)
        result=restarted['host'].submit('never-create',HostInput('notice',attempt=SubmissionAttempt(
            'nonce','scope','actor','assistant')),on_event=lambda e:None)
        assert result.error.dispatch=='not_submitted' and result.request_id is None
        assert path.read_bytes()==original


def snapshot(command,sid='target-sid',state='queued',**changes):
    value={'session_id':sid,'request_id':command['plannedRequestId'],'execution_id':'actual-execution',
        'generation':1,'channel_id':'web','work_kind':'session_message','state':state,
        'finished_at':5.0 if state in ('succeeded','failed','cancelled') else None}
    return {**value,**changes}


def report(handler,command,value):
    return handler({'purpose':'jiuwen.native','action':'report','workerId':'worker',
        'commandId':command['commandId'],'report':value})


def command_from(handler,poll):
    end=time.monotonic()+2
    while time.monotonic()<end:
        value=handler(poll)['command']
        if value is not None:return value
        time.sleep(0.01)
    raise AssertionError('original S01 command was not made available')


def test_native_new_session_actual_request_p28_terminal_and_no_second_dispatch(tmp_path):
    with native(tmp_path) as (bundle,_,sources,reference,poll,handler,_):
        bind(sources,reference,handler,poll)
        host=bundle['host'];events=[]
        def event(value):
            events.append(value)
            if value.kind=='session':return BindingAck(value.result.session_id,value.result.request_id)
        input=HostInput('domain-free notice',attempt=SubmissionAttempt('attempt','scope','actor','assistant'))
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(host.submit,'operation',input,on_event=event)
            command=command_from(handler,poll)
            assert command['sessionId'] is None
            assert handler(poll)['command'] is None
            report(handler,command,{'stage':'created','sessionId':'target-sid'})
            report(handler,command,{'stage':'snapshot','snapshot':snapshot(command)})
            assert events[0].result.request_id==command['plannedRequestId']
            assert events[0].result.session_id=='target-sid'
            report(handler,command,{'stage':'snapshot','snapshot':snapshot(command,state='succeeded')})
            result=future.result(timeout=2)
        assert result.terminal.state=='completed' and result.request_id==command['plannedRequestId']
        assert host.get_request(result.request_id).terminal==result.terminal
        assert host.submit('operation',input,on_event=event).terminal==result.terminal
        assert handler(poll)['command'] is None
        report(handler,command,{'stage':'snapshot','snapshot':snapshot(command,state='running')})
        assert host.get_request(result.request_id).terminal==result.terminal


def test_native_same_session_wrong_request_is_not_binding_and_unknown_never_requeues(tmp_path):
    with native(tmp_path) as (bundle,_,sources,reference,poll,handler,_):
        bind(sources,reference,handler,poll)
        events=[];host=bundle['host']
        input=HostInput('notice',attempt=SubmissionAttempt('attempt','scope','actor','assistant'))
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(host.submit,'operation',input,'source-sid',on_event=events.append)
            command=command_from(handler,poll)
            with pytest.raises(RuntimeFault):
                report(handler,command,{'stage':'snapshot','snapshot':snapshot(command,'source-sid',request_id='other-request')})
            assert not events
            report(handler,command,{'stage':'error'})
            result=future.result(timeout=2)
        assert result.request_id is None and result.terminal is None and result.error.dispatch=='unknown'
        assert host.submit('operation',input,'source-sid',on_event=events.append)==result
        assert handler(poll)['command'] is None


def test_native_professional_definition_is_rejected_before_any_native_command(tmp_path):
    with native(tmp_path) as (bundle,_,sources,reference,poll,handler,_):
        bind(sources,reference,handler,poll)
        execution=HostExecution('actor','assistant','business','interaction','job',
            HostDefinition('original','1','frozen-digest','professional rules'),workspace=str(tmp_path))
        result=bundle['host'].submit('business',HostInput('input',attempt=SubmissionAttempt('n','s','actor','assistant')),
            execution=execution,on_event=lambda e:None)
        assert result.request_id is None and result.error.dispatch=='not_submitted'
        assert handler(poll)['command'] is None


def test_native_real_machine_terminal_survives_sdk_event_failure(tmp_path):
    with native(tmp_path) as (bundle,_,sources,reference,poll,handler,_):
        bind(sources,reference,handler,poll)
        def event(value):
            if value.kind=='session':return BindingAck(value.result.session_id,value.result.request_id)
            raise OSError('SDK event persistence unavailable')
        input=HostInput('notice',attempt=SubmissionAttempt('attempt','scope','actor','assistant'))
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(bundle['host'].submit,'operation',input,'source-sid',on_event=event)
            command=command_from(handler,poll)
            report(handler,command,{'stage':'snapshot','snapshot':snapshot(command,'source-sid')})
            report(handler,command,{'stage':'snapshot','snapshot':snapshot(command,'source-sid','succeeded')})
            result=future.result(timeout=2)
        assert result.state=='completed' and result.terminal.state=='completed'
        assert result.error.code=='event_reporting_unknown'
        assert bundle['host'].get_request(result.request_id).terminal==result.terminal


@pytest.mark.parametrize('event_failure',[False,True])
def test_native_guard_late_failure_keeps_real_terminal_and_sdk_error_together(tmp_path,event_failure):
    with native(tmp_path) as (bundle,_,sources,reference,poll,handler,_):
        poll={**poll,'features':['request_turn']};bind(sources,reference,handler,poll)
        seen=[];effects=[]
        def event(value):
            seen.append(value)
            if value.kind=='session':return BindingAck(value.result.session_id,value.result.request_id)
            if event_failure:raise OSError('SDK event write failed after the native machine ended')
        def sdk(call):effects.append(call);raise AssertionError('guard failure must not execute SDK effects')
        input=HostInput('original business',attempt=SubmissionAttempt('attempt','scope','actor','assistant'))
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(bundle['host'].submit,'operation',input,'business-sid',
                execution=business(tmp_path),on_event=event,on_tool=sdk)
            command=command_from(handler,poll)
            report(handler,command,{'stage':'snapshot','snapshot':snapshot(command,'business-sid')})
            report(handler,command,{'stage':'snapshot','snapshot':snapshot(command,'business-sid','succeeded'),
                'guardError':'native_definition_or_tool_collision'})
            result=future.result(timeout=2)
        assert result.state==result.terminal.state=='completed'
        assert result.error.code=='native_guard_failed' and result.error.dispatch=='accepted'
        assert result.output is None and not effects
        assert seen[-1].result.error.code=='native_guard_failed'
        assert bundle['host'].get_request(result.request_id).error==result.error
        if event_failure:assert 'SDK event reporting is unknown' in result.error.message
        bundle['native_resources']['host'].close()
        restored=native_adapter(host_ref='host',resource_root=tmp_path/'native')
        restored['native_resources']['host'].start()
        try:
            recovered=restored['host'].get_request(result.request_id)
            assert recovered==result
            assert (recovered.session_id,recovered.request_id)==('business-sid',command['plannedRequestId'])
            assert recovered.terminal==result.terminal and recovered.error==result.error
            assert not effects
        finally:restored['native_resources']['host'].close()


@pytest.mark.parametrize('fault',['missing','corrupt'])
def test_native_original_request_missing_receipt_restores_unknown_without_dispatch(tmp_path,fault):
    with native(tmp_path) as (bundle,_,sources,reference,poll,handler,_):
        bind(sources,reference,handler,poll)
        input=HostInput('notice',attempt=SubmissionAttempt('attempt','scope','actor','assistant'))
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(bundle['host'].submit,'operation',input,'source-sid',on_event=acked)
            command=command_from(handler,poll)
            report(handler,command,{'stage':'snapshot','snapshot':snapshot(command,'source-sid','succeeded')})
            result=future.result(timeout=2)
        # Inject OS receipt loss through the adapter's returned original evidence
        # reference, never by predicting its private filename/layout.
        from pathlib import Path
        receipt=Path(result.terminal.evidence_ref)
        if fault=='missing':receipt.unlink()
        else:receipt.write_text('{}',encoding='utf-8')
        bundle['native_resources']['host'].close()
        restored=native_adapter(host_ref='host',resource_root=tmp_path/'native')
        restored['native_resources']['host'].start()
        try:
            assert restored['host'].get_request(result.request_id) is None
            assert restored['host'].get_request('not-an-actual-request') is None
            assert restored['host'].check()['state']=='unavailable'
        finally:restored['native_resources']['host'].close()


@pytest.mark.parametrize('fault',['nonce','session','operation','account','digest'])
def test_native_restored_request_index_conflicts_remain_unknown(tmp_path,fault):
    with native(tmp_path) as (bundle,_,sources,reference,poll,handler,_):
        bind(sources,reference,handler,poll)
        input=HostInput('notice',attempt=SubmissionAttempt('attempt','scope','actor','assistant'))
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(bundle['host'].submit,'operation',input,'source-sid',on_event=acked)
            command=command_from(handler,poll)
            report(handler,command,{'stage':'snapshot','snapshot':snapshot(command,'source-sid','succeeded')})
            result=future.result(timeout=2)
        # OS corruption of a reference record. Locate by its declared format,
        # never infer a private filename from the operation/request hash.
        references=[]
        for path in (tmp_path/'native').iterdir():
            if path.is_file():
                value=json.loads(path.read_text(encoding='utf-8'))
                if value.get('format')=='ioalab.jiuwen-request/1':references.append((path,value))
        assert len(references)==1
        path,value=references[0]
        if fault=='nonce':value['submissionAttempt']['nonce']='another-attempt'
        elif fault=='session':value['sessionId']='another-session'
        elif fault=='operation':value['operation']='another-operation'
        elif fault=='account':value['nativeScope']['accountBindingRef']='another-account'
        else:value['recordDigest']='0'*64
        path.write_text(json.dumps(value),encoding='utf-8')
        bundle['native_resources']['host'].close()
        restored=native_adapter(host_ref='host',resource_root=tmp_path/'native')
        restored['native_resources']['host'].start()
        try:
            assert restored['host'].get_request(result.request_id) is None
            assert restored['host'].check()['state']=='unavailable'
        finally:restored['native_resources']['host'].close()


def test_native_restored_nonterminal_snapshot_is_not_fresh_running_claim(tmp_path):
    with native(tmp_path) as (bundle,resource,sources,reference,poll,handler,_):
        bind(sources,reference,handler,poll)
        input=HostInput('notice',attempt=SubmissionAttempt('attempt','scope','actor','assistant'))
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(bundle['host'].submit,'operation',input,'source-sid',on_event=acked)
            command=command_from(handler,poll)
            report(handler,command,{'stage':'snapshot','snapshot':snapshot(command,'source-sid','running')})
            resource.close()
            original=future.result(timeout=2)
        restored=native_adapter(host_ref='host',resource_root=tmp_path/'native')
        restored['native_resources']['host'].start()
        try:
            known=restored['host'].get_request(original.request_id)
            assert known.state=='unknown' and known.terminal is None
            assert known.error.dispatch=='unknown'
            assert (known.session_id,known.request_id)==(original.session_id,command['plannedRequestId'])
        finally:restored['native_resources']['host'].close()


def test_native_worker_creates_after_sdk_bind_and_uses_same_real_session_lane(tmp_path,monkeypatch):
    # Only the external native constructor/Runtime transport is replaced. The
    # actual adapter, OS receipts and SDK entry ledger remain in the path.
    module=SimpleNamespace(SessionCreateInput=lambda **kw:SimpleNamespace(**kw))
    monkeypatch.setitem(sys.modules,'jiuwenswarm.runtime.session_provisioner',module)
    path=files('ioalab_jiuwen').joinpath('resources','native','worker.py')
    spec=importlib.util.spec_from_file_location('native_worker_fixture',str(path))
    worker_module=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker_module)
    class State(str,Enum):
        QUEUED='queued';SUCCEEDED='succeeded'
        @property
        def terminal(self):return self is State.SUCCEEDED
    class Work(str,Enum):MESSAGE='session_message'
    @dataclass
    class Snapshot:
        session_id:str
        request_id:str
        execution_id:str
        generation:int=1
        channel_id:str='web'
        work_kind:object=Work.MESSAGE
        root_work_kind:object=None
        state:object=State.QUEUED
        finished_at:object=None
    with native(tmp_path) as (bundle,_,sources,reference,poll,handler,_):
        sources.entry_bindings.begin('setup',source=sources.setup_source(reference))
        created=[];sent=[];executions={}
        class Runtime:
            async def describe_session(self,*,session_id):
                return SimpleNamespace(session_id=session_id,channel_id='web',user_id='native-user',
                    project_id='native-project',project_dir=str(tmp_path))
            async def prepare_session_create(self,value):
                assert sources.entry_bindings.get('setup')['phase']=='bound'
                assert value.user_id=='native-user' and value.channel_id=='web' and value.persist_session is True
                created.append(value)
                return SimpleNamespace(result=SimpleNamespace(session_id='actual-created-sid'),commit_timing='actual-timing')
            async def commit_session_provision(self,value,*,timing):
                assert timing==value.commit_timing
                return value.result
            async def send_session_message(self,**kw):
                sent.append(kw)
                value=Snapshot(kw['target_session_id'],kw['request_id'],'native-execution-'+str(len(sent)))
                executions[value.execution_id]=value
                return value
            def get_session_execution(self,execution_id):
                return replace(executions[execution_id],state=State.SUCCEEDED,finished_at=1.0)
        def event(value):
            if value.kind=='session':return BindingAck(value.result.session_id,value.result.request_id)
        async def scenario():
            ready=asyncio.Event();loop=asyncio.get_running_loop()
            def post(body):
                result=handler(body)
                if body['action']=='poll':loop.call_soon_threadsafe(ready.set)
                return {'data':result}
            worker=worker_module.NativeWorker(Runtime(),poll['source'],reference['entryRef'],post)
            worker.start()
            try:
                await asyncio.wait_for(ready.wait(),2)
                first=await asyncio.wait_for(asyncio.to_thread(bundle['host'].submit,'external',
                    HostInput('echo first',attempt=SubmissionAttempt('one','s1','actor','assistant')),
                    on_event=event),5)
                second=await asyncio.wait_for(asyncio.to_thread(bundle['host'].submit,'continuation',
                    HostInput('echo follow-up',attempt=SubmissionAttempt('two','s2','actor','assistant')),
                    first.session_id,on_event=event),5)
                return first,second
            finally:worker.close()
        first,second=asyncio.run(scenario())
        assert len(created)==1 and len(sent)==2
        assert [row['target_session_id'] for row in sent]==['actual-created-sid','actual-created-sid']
        assert first.terminal.state==second.terminal.state=='completed'
        assert first.request_id!=second.request_id


# ---- 199 minimal original-request business chain (native body is a boundary substitute) ----

def business(tmp_path,deadline=None,enabled=False):
    from ioalab_sdk.runtime import HostToolDeclaration
    return HostExecution('actor','assistant','business','interaction','job',
        HostDefinition('original','1','frozen-digest','frozen professional rules'),
        tools=(HostToolDeclaration('sdk_echo','Domain-free SDK echo',{'type':'object','properties':{'text':{'type':'string'}}}),),
        authorization={'nativeTools':{},'nativeToolsEnabled':enabled},deadline=deadline,workspace=str(tmp_path))


def acked(value):
    if value.kind=='session':return BindingAck(value.result.session_id,value.result.request_id)


@pytest.mark.parametrize('deadline,code',[('2000-01-01T00:00:00Z','expired'),('2999-01-01T00:00:00','invalid_input')])
def test_native_business_unusable_original_deadline_is_not_submitted(tmp_path,deadline,code):
    with native(tmp_path) as (bundle,_,sources,reference,poll,handler,_):
        bind(sources,reference,handler,poll);handler({**poll,'features':['request_turn']})
        result=bundle['host'].submit('business',HostInput('input',attempt=SubmissionAttempt('n','s','actor','assistant')),
            execution=business(tmp_path,deadline),on_event=acked,on_tool=lambda call:None)
        assert result.error.code==code and result.error.dispatch=='not_submitted' and result.request_id is None
        assert handler({**poll,'features':['request_turn']})['command'] is None


def test_native_preflight_failure_new_sdk_attempt_reuses_created_session(tmp_path):
    with native(tmp_path) as (bundle,_,sources,reference,poll,handler,_):
        poll={**poll,'features':['request_turn']};bind(sources,reference,handler,poll)
        host=bundle['host'];execution=business(tmp_path)
        def attempt(nonce):return HostInput('echo please',attempt=SubmissionAttempt(nonce,'scope','actor','assistant'))
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(host.submit,'business',attempt('one'),execution=execution,on_event=acked,on_tool=lambda c:None)
            command=command_from(handler,poll)
            report(handler,command,{'stage':'created','sessionId':'created-sid'})
            report(handler,command,{'stage':'preflight_error'})
            first=future.result(timeout=2)
        assert first.error.dispatch=='not_submitted' and first.request_id is None
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(host.submit,'business',attempt('two'),execution=execution,on_event=acked,on_tool=lambda c:None)
            retry=command_from(handler,poll)
            assert retry['sessionId']=='created-sid' and retry['commandId']!=command['commandId']
            with pytest.raises(RuntimeFault):report(handler,retry,{'stage':'created','sessionId':'another-sid'})
            report(handler,retry,{'stage':'snapshot','snapshot':snapshot(retry,'created-sid')})
            report(handler,retry,{'stage':'snapshot','snapshot':snapshot(retry,'created-sid','succeeded')})
            second=future.result(timeout=2)
        assert second.attempt_id=='two' and second.session_id=='created-sid' and second.terminal.state=='completed'
        # Accepted/terminal work is never re-dispatched under another attempt.
        with pytest.raises(RuntimeFault):host.submit('business',attempt('three'),execution=execution,on_event=acked,on_tool=lambda c:None)
        assert host.submit('business',attempt('two'),execution=execution,on_event=acked,on_tool=lambda c:None)==second


def test_native_callback_resources_keep_original_value_and_report_collection(tmp_path):
    from ioalab_sdk.runtime import HostResource,HostToolResult
    with native(tmp_path) as (bundle,_,sources,reference,poll,handler,_):
        poll={**poll,'features':['request_turn']};bind(sources,reference,handler,poll)
        calls=[]
        def on_tool(call):
            calls.append(call)
            return HostToolResult(value={'echo':'kept'},resources=(HostResource('r','out.txt','text/plain',None,'job','sdk:out'),))
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(bundle['host'].submit,'business',HostInput('echo',attempt=SubmissionAttempt('a','s','actor','assistant')),
                execution=business(tmp_path),on_event=acked,on_tool=on_tool)
            command=command_from(handler,poll)
            report(handler,command,{'stage':'created','sessionId':'target-sid'})
            report(handler,command,{'stage':'snapshot','snapshot':snapshot(command)})
            reply=handler({'purpose':'jiuwen.native','action':'sdk_tool','workerId':'worker','commandId':command['commandId'],
                'sessionId':'target-sid','requestId':command['plannedRequestId'],'callId':'native-call','name':'sdk_echo',
                'arguments':{'text':'kept'}})
            assert reply['value']=={'echo':'kept'} and reply['error']['phase']=='callback_resources'
            report(handler,command,{'stage':'snapshot','snapshot':snapshot(command,state='succeeded')})
            result=future.result(timeout=2)
        assert len(calls)==1 and calls[0].call_id=='native-call'
        assert result.terminal.state=='completed' and result.error.phase=='callback_resources' and result.error.dispatch=='accepted'


def test_native_original_request_installs_frozen_definition_and_echoes_after_p28(tmp_path,monkeypatch):
    from ioalab_sdk.runtime import HostToolResult
    module=SimpleNamespace(SessionCreateInput=lambda **kw:SimpleNamespace(**kw))
    monkeypatch.setitem(sys.modules,'jiuwenswarm.runtime.session_provisioner',module)
    def load(name):
        path=files('ioalab_jiuwen').joinpath('resources','native',name+'.py')
        spec=importlib.util.spec_from_file_location('native_'+name+'_fixture',str(path))
        value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value
    worker_module=load('worker');turns=load('turn')
    class State(str,Enum):
        QUEUED='queued';SUCCEEDED='succeeded'
        @property
        def terminal(self):return self is State.SUCCEEDED
    class Work(str,Enum):MESSAGE='session_message'
    @dataclass
    class Snapshot:
        session_id:str
        request_id:str
        execution_id:str
        generation:int=1
        channel_id:str='web'
        work_kind:object=Work.MESSAGE
        root_work_kind:object=None
        state:object=State.QUEUED
        finished_at:object=None
    class Builder:
        def __init__(self):self.sections={'user':'original user settings'}
        def get_section(self,name):return self.sections.get(name)
        def add_section(self,section):self.sections[section.name]=section
        def remove_section(self,name):self.sections.pop(name,None)
    class Manager:
        def __init__(self):self.tools={}
        def get(self,name):return self.tools[name][0] if name in self.tools else None
        def add_ability(self,card,tool):self.tools[card.name]=(card,tool)
        def remove_ability(self,name):self.tools.pop(name,None)
    class Ctx:
        def __init__(self,agent,inputs):self.agent=agent;self.inputs=inputs;self.finished=None
        def request_force_finish(self,result):self.finished=result
    react=SimpleNamespace(prompt_builder=Builder(),ability_manager=Manager());owner=SimpleNamespace(react_agent=react)
    def tool(declaration,func):
        card=SimpleNamespace(name=declaration['name'])
        return SimpleNamespace(card=card,ability=func,info=SimpleNamespace(name=card.name))
    unloaded=[];seen=[];calls=[]
    async def prepare_turn(runtime,source,sid,command,callback):
        turn=turns.PreparedTurn(sid,command['plannedRequestId'],command['execution'],
            lambda name,text:SimpleNamespace(name=name,content=text),tool,callback)
        turn.reserve(owner,react)
        async def unload():unloaded.append(sid)
        return turns.Preparation(turn,unload)
    def on_tool(call):
        calls.append(call);return HostToolResult(value={'echo':call.arguments['text']})
    with native(tmp_path) as (bundle,_,sources,reference,poll,handler,_):
        sources.entry_bindings.begin('setup',source=sources.setup_source(reference))
        executions={};finished=asyncio.Event()
        async def invocation(route):
            # Native lane: the per-request guard Rail registered on the actual Agent.
            turn=turns.PENDING[route]
            turn.on_invoke(owner,route)
            window=Ctx(react,SimpleNamespace(tools=[SimpleNamespace(name='native_write')]))
            turn.on_model(window,route)
            seen.append(([t.name for t in window.inputs.tools],react.prompt_builder.get_section(turns.SECTION).content))
            ctx=Ctx(react,SimpleNamespace(tool_name='sdk_echo',tool_args={'text':'domain-free'},tool_call=SimpleNamespace(id='native-call')))
            turn.on_tool(ctx,route)
            seen.append(await react.ability_manager.tools[ctx.inputs.tool_name][1](**ctx.inputs.tool_args))
            turn.after_tool(ctx);turn.after_invoke(route);finished.set()
        class Runtime:
            async def describe_session(self,*,session_id):
                return SimpleNamespace(session_id=session_id,channel_id='web',user_id='native-user',
                    project_id='native-project',project_dir=str(tmp_path))
            async def prepare_session_create(self,value):
                return SimpleNamespace(result=SimpleNamespace(session_id='business-sid'),commit_timing='t')
            async def commit_session_provision(self,value,*,timing):return value.result
            async def send_session_message(self,**kw):
                value=Snapshot(kw['target_session_id'],kw['request_id'],'native-execution')
                executions[value.execution_id]=value
                asyncio.get_running_loop().create_task(invocation((value.session_id,value.request_id)))
                return value
            def get_session_execution(self,execution_id):
                value=executions[execution_id]
                return replace(value,state=State.SUCCEEDED,finished_at=1.0) if finished.is_set() else value
        async def scenario():
            ready=asyncio.Event();loop=asyncio.get_running_loop()
            def post(body):
                result=handler(body)
                if body['action']=='poll':loop.call_soon_threadsafe(ready.set)
                return {'data':result}
            worker=worker_module.NativeWorker(Runtime(),poll['source'],reference['entryRef'],post,prepare_turn)
            worker.start()
            try:
                await asyncio.wait_for(ready.wait(),2)
                return await asyncio.wait_for(asyncio.to_thread(bundle['host'].submit,'business',
                    HostInput('echo domain-free',attempt=SubmissionAttempt('one','s1','actor','assistant')),
                    execution=business(tmp_path,'2999-01-01T00:00:00Z'),on_event=acked,on_tool=on_tool),5)
            finally:
                await asyncio.sleep(0.05);worker.close()
        result=asyncio.run(scenario())
    assert seen[0]==(['sdk_echo'],'frozen professional rules')
    assert seen[1]=={'echo':'domain-free'}
    assert [(c.session_id,c.request_id,c.call_id,c.name) for c in calls]==[('business-sid',result.request_id,'native-call','sdk_echo')]
    assert result.terminal.state=='completed' and result.session_id=='business-sid'
    assert react.prompt_builder.sections=={'user':'original user settings'} and not react.ability_manager.tools
    assert unloaded==['business-sid'] and not turns.PENDING


def test_native_extension_loader_selects_the_entry_rail_not_the_request_guard():
    # Installed workswarm 0.2.8 RailManager._extract_class_name instantiates the
    # first match of this pattern with no arguments.
    import re
    text=files('ioalab_jiuwen').joinpath('resources','native','rail.py').read_text(encoding='utf-8')
    matches=re.findall(r"class\s+(\w+Rail)\s*\(\s*(DeepAgentRail|AgentRail)\s*\)",text)
    assert matches[0][0]=='IoAEntryRail'


def _p07_binding(tmp_path,**change):
    from ioalab_sdk.runtime import NATIVE_ENTRY_BIND_OPERATION
    python=str(tmp_path/'python')
    binding={'format':'ioalab.native-entry/1','hostRef':'jiuwen-host','setupOperation':NATIVE_ENTRY_BIND_OPERATION,
        'sourceOperations':['assistant.message.send'],
        'profiles':{'assistant':{'profile':'a1','descriptorFile':str(tmp_path/'descriptor.json'),
            'ensureCommand':[python,'ensure'],'callerCommand':[python,'call']}}}
    return {**binding,**change}


def test_native_resource_export_accepts_empty_profiles_before_account_setup(tmp_path):
    from ioalab_jiuwen.native_skills import native_skills,NATIVE_FILES
    binding=_p07_binding(tmp_path,profiles={})
    result=native_skills.resource_files(binding)
    assert set(result)=={*NATIVE_FILES,'entry-binding.json'}
    assert json.loads(result['entry-binding.json'])==binding
    assert json.loads(native_skills.resource_files(_p07_binding(tmp_path))['entry-binding.json'])['profiles']['assistant']['profile']=='a1'


@pytest.mark.parametrize('change',['duplicate','setup'])
def test_native_resource_export_rejects_duplicate_or_setup_source_operations(tmp_path,change):
    from ioalab_jiuwen.native_skills import native_skills
    binding=_p07_binding(tmp_path)
    operations=(['assistant.message.send','assistant.message.send'] if change=='duplicate'
        else ['assistant.message.send',binding['setupOperation']])
    with pytest.raises(RuntimeFault):native_skills.resource_files({**binding,'sourceOperations':operations})
