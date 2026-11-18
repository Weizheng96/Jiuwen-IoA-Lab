"""Actual resident RPC/A2C/material WASM recovery with a native S01 boundary."""
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest
from a2c.codec import digest_bytes
from ioalab_sdk.provider import ProviderRole
from ioalab_sdk.runtime import (HostInput,HostRegistration,SubmissionResult,SubmissionError,
    SubmissionEvent,BindingAck,MachineTerminal,LocalClient,RuntimeConfig)
from ioalab_sdk.templates import template_from_definition
from submission_fixture import bound_runtime
from test_resident_delegation import QuoteHost,participants,uid,terminal
from test_resident_contracts import confirm,status
from test_assistant_role import user_decision
from conftest import wait_for

SOURCE="""from a2c_verifier import observe

def verify(ctx):
    first=observe({'question':'same original material observation'})
    second=observe({'question':'same original material observation'})
    ok=first==second=={'expected':True}
    return {'status':'pass' if ok else 'fail','checks':[{'check':'both actual observations','ok':ok}]}
"""


class MaterialHost(QuoteHost):
    registration=HostRegistration('material-native-boundary','1',readonly_context='isolated_session')
    def __init__(self):super().__init__();self.observations=[];self.allowed=False
    def submit(self,operation_id,input,session_id=None,*,execution=None,on_event=None,on_tool=None):
        if execution.kind!='observation':
            return super().submit(operation_id,input,session_id,execution=execution,on_event=on_event,on_tool=on_tool)
        self.observations.append((operation_id,input.attempt.nonce,json.loads(input.text)))
        unique=list(dict.fromkeys(value[0] for value in self.observations))
        if operation_id==unique[-1] and len(unique)>1 and not self.allowed:
            return SubmissionResult(operation_id,input.attempt.nonce,'unknown',session_id,
                error=SubmissionError('not_ready','original_native_bridge','not_submitted','Original model not dispatched'))
        sid=session_id or 'actual-observation-'+str(len(unique));request='actual-request-'+input.attempt.nonce
        accepted=SubmissionResult(operation_id,input.attempt.nonce,'running',sid,request,'accepted')
        assert on_event(SubmissionEvent('session','session',accepted))==BindingAck(sid,request)
        return replace(accepted,state='completed',output={'observation':{'expected':True}},
            terminal=MachineTerminal(operation_id,input.attempt.nonce,sid,request,'completed','actual-native-end:'+request))


def terms(platform,formation):
    def make(ref):
        definition={'formation':formation,'deliverable':{'name':'result'},
            'pricing':{'proposalFee':1 if formation=='proposal' else 0,'price':'9'},
            'verifier':{'id':ref['id'],'source':SOURCE,'kind':'skill','disputeModels':['test-observation-model']}}
        draft=template_from_definition(definition,packages=SimpleNamespace(put=lambda **_:ref),payment_provider=platform.payment_provider)
        value={key:val for key,val in draft.items() if key in ('formation','deliverables','inputs','deadlines','pricing','acceptance','authorizableSteps','samples','extensions')}
        value['pricing']={key:val for key,val in value['pricing'].items() if key!='paymentProviders'}
        if formation!='proposal':value['acceptance']['result']['verifiers']={'skill':ref}
        value['inputs']=[{'name':'original','description':'Actual original material for pre-signing review','required':True,'specSchema':{},'reviewBeforeSigning':True,'verifiers':{'skill':ref}}]
        return value
    return make


def pending_job(provider,crash_gap=False):
    items=LocalClient(provider.config.home).call('provider.tasks.get',{'profile':'service'})['data']['items']
    return next((item for item in items if item['status']==('unknown' if crash_gap else 'waiting') and item['phase'] in ('quote','decision')),None)


def observed(host,count):
    return lambda:len(dict.fromkeys(value[0] for value in host.observations))>=count


def test_errored_material_observation_never_allows_original_task_to_sign_or_replay(platform,tmp_path,monkeypatch):
    monkeypatch.setenv('A2C_CACHE_DIR',str(tmp_path/'verifier-cache'))
    def arbitration_only(*args,**kwargs):pytest.fail('Material review must not call the arbitration model')
    monkeypatch.setitem(platform.market.market.models,'test-observation-model',arbitration_only)
    class ErroredMaterial(MaterialHost):
        def submit(self,*args,**kwargs):
            result=super().submit(*args,**kwargs)
            if kwargs['execution'].kind=='observation':
                return replace(result,error=SubmissionError('native_guard_failed','native_definition','accepted',
                    'Original observation guard failed'))
            return result
    host=ErroredMaterial();host.allowed=True
    with participants(platform,tmp_path,formation='proposal',fee=1,agent_host=host,verifier_source=SOURCE,
            verifier_kind='skill',template_terms=terms(platform,'proposal'),models=1) as (provider,assistant,_,template,task):
        rpc=LocalClient(assistant.config.home);op=uid()
        args={'taskRef':task,'service':provider.identities[0].agent_id,'templateDigest':template['templateDigest'],
            'spec':{'result':{}},'materials':{'original':{'text':'actual original material','spec':{}}}}
        pending=rpc.call('assistant.delegation.request',args,operation_id=op)
        assert pending['status']=='waiting'
        user_decision(assistant,pending['data']['confirmationRef'])
        started=rpc.call('assistant.delegation.request',args,operation_id=op)
        assert started['status']=='accepted'
        original=wait_pending_job(provider,True,observed(host,1))
        assert not original['canResume'] and len(host.observations)==1
        refused=LocalClient(provider.config.home).call('provider.task.resume',{'profile':'service','taskRef':original['taskRef']})
        assert refused['status'] in ('unknown','rejected')
        assert len(host.observations)==1 and not host.turns


def wait_pending_job(provider,crash_gap=False,ready=lambda:True):
    # Store contention may first park the job before its host child (A2C guard
    # contract); read the job only after the expected host children were entered.
    snapshot=[]
    def observe():
        if not ready():return False
        value=pending_job(provider,crash_gap)
        if value is not None:snapshot.append(value)
        return value is not None
    wait_for(observe)
    return snapshot[0]


@pytest.mark.parametrize('formation',['proposal','countersign'])
@pytest.mark.parametrize('crash_gap',[False,True])
def test_real_material_pre_review_restarts_same_task_and_ordinal_without_repeating_completed_model(platform,tmp_path,formation,monkeypatch,crash_gap):
    monkeypatch.setenv('A2C_CACHE_DIR',str(tmp_path/'verifier-cache'))
    def arbitration_only(*args,**kwargs):pytest.fail('Material review must not call the arbitration model')
    monkeypatch.setitem(platform.market.market.models,'test-observation-model',arbitration_only)
    host=MaterialHost()
    with participants(platform,tmp_path,formation=formation,fee=1 if formation=='proposal' else 0,
            agent_host=host,verifier_source=SOURCE,verifier_kind='skill',template_terms=terms(platform,formation),models=1) as (provider,assistant,_,template,task):
        caller=LocalClient(assistant.config.home);request_op=uid()
        if crash_gap:
            # Interrupt only the host-refused second child's pending, not a contention recovery.
            with provider.store.transaction() as conn:
                conn.execute("CREATE TEMP TRIGGER interrupt_material_wait BEFORE UPDATE OF state ON runtime_jobs WHEN NEW.state='waiting' AND json_extract(NEW.result,'$.reason')='material_not_submitted' AND json_extract(NEW.result,'$.materialObservation.proof')='not_submitted' BEGIN SELECT RAISE(ABORT,'fixture interruption after persisted material pending'); END")
        arguments={'taskRef':task,'service':provider.identities[0].agent_id,'templateDigest':template['templateDigest'],
            'spec':{'result':{}},'materials':{'original':{'text':'actual original material','spec':{}}}}
        result=caller.call('assistant.delegation.request',arguments,operation_id=request_op)
        if formation=='proposal':
            assert result['status']=='waiting',result
            user_decision(assistant,result['data']['confirmationRef'])
            result=caller.call('assistant.delegation.request',arguments,operation_id=request_op)
            assert result['status']=='accepted',result
            reference=result['data']['quoteRef']
        else:
            assert result['status']=='completed',result
            accepted,_,_=confirm(assistant,result['data'])
            reference=accepted['data']['contractRef']
        original=wait_pending_job(provider,crash_gap,observed(host,2))
        original_ids=list(dict.fromkeys(value[0] for value in host.observations))
        assert len(original_ids)==2 and original_ids[0]!=original_ids[1]
        assert host.observations[0][2]['request']==host.observations[1][2]['request']
        if not crash_gap:assert original['result']['materialObservation']['child']['operation']==original_ids[1]
        old_task=original['taskRef'];old_iid=original['interactionId']
        old_fee=len(platform.db.rows("SELECT * FROM transfers WHERE purpose='proposal-fee' AND payee_id=?",provider.account.account['id']))
        assert old_fee==(1 if formation=='proposal' else 0)
        provider.close();host.allowed=True
        fresh=bound_runtime(RuntimeConfig.load(tmp_path/'provider/config.json'),host,lambda selected:ProviderRole(selected)).start()
        try:
            wait_for(lambda:fresh.connection.connected)
            if formation=='proposal':
                try:wait_for(lambda:terminal(assistant,reference))
                except AssertionError:
                    state=LocalClient(fresh.config.home).call('runtime.status',{})['data']
                    pytest.fail('Original material task did not converge: '+json.dumps({'roleErrors':state['roleErrors'],
                        'schedulerError':state['scheduler']['error']}))
                finished=terminal(assistant,reference)
                assert finished['status']=='completed',finished
                assert finished['data']['card']['signatures']['2'] and finished['data']['card']['signatures']['3']
            else:
                wait_for(lambda:status(assistant,reference))
                finished=status(assistant,reference)
                assert finished['status']=='completed' and finished['data']['formed'],finished
                assert finished['data']['card']['signatures']['1'] and finished['data']['card']['signatures']['2']
            items=LocalClient(fresh.config.home).call('provider.tasks.get',{'profile':'service','taskRef':old_task})['data']['items']
            assert len(items)==1 and items[0]['taskRef']==old_task and items[0]['interactionId']==old_iid
            assert items[0]['status']=='completed'
            assert sum(value[0]==original_ids[0] for value in host.observations)==1
            assert list(dict.fromkeys(value[0] for value in host.observations))==original_ids
            assert len(platform.db.rows("SELECT * FROM transfers WHERE purpose='proposal-fee' AND payee_id=?",fresh.account.account['id']))==old_fee
        finally:fresh.close()


def test_accepted_unknown_first_material_child_blocks_scope_and_does_not_replay_after_restart(platform,tmp_path,monkeypatch):
    monkeypatch.setenv('A2C_CACHE_DIR',str(tmp_path/'verifier-cache'))
    def arbitration_only(*args,**kwargs):pytest.fail('Material review must not call the arbitration model')
    monkeypatch.setitem(platform.market.market.models,'test-observation-model',arbitration_only)
    class UnknownFirst(MaterialHost):
        def submit(self,operation_id,input,session_id=None,*,execution=None,on_event=None,on_tool=None):
            if execution.kind!='observation':
                return super().submit(operation_id,input,session_id,execution=execution,on_event=on_event,on_tool=on_tool)
            self.observations.append((operation_id,input.attempt.nonce,json.loads(input.text)))
            original=SubmissionResult(operation_id,input.attempt.nonce,'unknown','actual-unknown-session','actual-unknown-request','accepted')
            assert on_event(SubmissionEvent('session','session',original))==BindingAck(original.session_id,original.request_id)
            return original
    host=UnknownFirst()
    with participants(platform,tmp_path,formation='proposal',fee=1,agent_host=host,verifier_source=SOURCE,
            verifier_kind='skill',template_terms=terms(platform,'proposal'),models=1) as (provider,assistant,_,template,task):
        rpc=LocalClient(assistant.config.home);op=uid()
        args={'taskRef':task,'service':provider.identities[0].agent_id,'templateDigest':template['templateDigest'],
              'spec':{'result':{}},'materials':{'original':{'text':'actual original material','spec':{}}}}
        pending=rpc.call('assistant.delegation.request',args,operation_id=op)
        assert pending['status']=='waiting'
        user_decision(assistant,pending['data']['confirmationRef'])
        started=rpc.call('assistant.delegation.request',args,operation_id=op)
        assert started['status']=='accepted'
        original=wait_pending_job(provider,True,observed(host,1))
        assert len(host.observations)==1
        first=host.observations[0][:2]
        provider.close()
        fresh=bound_runtime(RuntimeConfig.load(tmp_path/'provider/config.json'),host,lambda selected:ProviderRole(selected)).start()
        try:
            wait_for(lambda:fresh.connection.connected)
            actual=LocalClient(fresh.config.home).call('provider.tasks.get',{'profile':'service','taskRef':original['taskRef']})['data']['items'][0]
            assert actual['status']=='unknown' and not actual['canResume']
            refused=LocalClient(fresh.config.home).call('provider.task.resume',{'profile':'service','taskRef':original['taskRef']})
            assert refused['status'] in ('unknown','rejected')
            assert len(host.observations)==1 and host.observations[0][:2]==first
            assert actual['interactionId']==original['interactionId']
            assert len(platform.db.rows("SELECT * FROM transfers WHERE purpose='proposal-fee' AND payee_id=?",fresh.account.account['id']))==1
        finally:fresh.close()


@pytest.mark.parametrize('data_source',['output','callback'])
def test_original_material_receipt_late_machine_end_and_data_resume_without_resubmitting_first_observation(platform,tmp_path,monkeypatch,data_source):
    monkeypatch.setenv('A2C_CACHE_DIR',str(tmp_path/'verifier-cache'))
    def arbitration_only(*args,**kwargs):pytest.fail('Material review must not call the arbitration model')
    monkeypatch.setitem(platform.market.market.models,'test-observation-model',arbitration_only)
    class LateMaterial(MaterialHost):
        registration=HostRegistration('late-material-boundary','1',('get_request',),readonly_context='isolated_session')
        def __init__(self):super().__init__();self.allowed=True;self.pending=None;self.ready=False;self.queries=[]
        def submit(self,operation_id,input,session_id=None,*,execution=None,on_event=None,on_tool=None):
            if execution.kind!='observation' or self.pending is not None:
                return super().submit(operation_id,input,session_id,execution=execution,on_event=on_event,on_tool=on_tool)
            self.observations.append((operation_id,input.attempt.nonce,json.loads(input.text)))
            receipt=SubmissionResult(operation_id,input.attempt.nonce,'unknown','late-original-sid','late-original-request','accepted')
            assert on_event(SubmissionEvent('original-session','session',receipt))==BindingAck(receipt.session_id,receipt.request_id)
            if data_source=='callback':
                from ioalab_sdk.runtime import HostToolCall
                value=on_tool(HostToolCall(operation_id,input.attempt.nonce,receipt.session_id,receipt.request_id,
                    'actual-observation-call','verifier.observe',{'observation':{'expected':True}}))
                assert value.error is None
            self.pending=receipt
            return receipt
        def get_request(self,query):
            request_id=query.request_id
            assert (query.operation_id,query.attempt_id,query.session_id,request_id)==(self.pending.operation_id,
                self.pending.attempt_id,self.pending.session_id,self.pending.request_id)
            self.queries.append(request_id)
            if not self.ready:return self.pending
            return replace(self.pending,state='completed',
                output={'observation':{'expected':True}} if data_source=='output' else 'ordinary final content',
                terminal=MachineTerminal(self.pending.operation_id,self.pending.attempt_id,self.pending.session_id,
                    request_id,'completed','actual-late-native-end'))
    host=LateMaterial()
    with participants(platform,tmp_path,formation='proposal',fee=1,agent_host=host,verifier_source=SOURCE,
            verifier_kind='skill',template_terms=terms(platform,'proposal'),models=1) as (provider,assistant,_,template,task):
        rpc=LocalClient(assistant.config.home);operation=uid()
        args={'taskRef':task,'service':provider.identities[0].agent_id,'templateDigest':template['templateDigest'],
            'spec':{'result':{}},'materials':{'original':{'text':'actual original material','spec':{}}}}
        initial=rpc.call('assistant.delegation.request',args,operation_id=operation)
        assert initial['status']=='waiting'
        user_decision(assistant,initial['data']['confirmationRef'])
        started=rpc.call('assistant.delegation.request',args,operation_id=operation)
        assert started['status']=='accepted'
        original=wait_pending_job(provider,True,observed(host,1))
        assert len(host.observations)==1
        first=host.observations[0][:2]
        host.ready=True
        wait_for(lambda:terminal(assistant,started['data']['quoteRef']))
        finished=terminal(assistant,started['data']['quoteRef'])
        assert finished['status']=='completed',finished
        assert finished['data']['card']['signatures']['2'] and finished['data']['card']['signatures']['3']
        current=LocalClient(provider.config.home).call('provider.tasks.get',{'profile':'service','taskRef':original['taskRef']})['data']['items'][0]
        assert current['taskRef']==original['taskRef'] and current['interactionId']==original['interactionId'] and current['status']=='completed'
        assert host.queries and set(host.queries)=={'late-original-request'}
        assert len(host.observations)==2 and host.observations[0][:2]==first
        assert host.observations[0][0]!=host.observations[1][0]
        assert len(platform.db.rows("SELECT * FROM transfers WHERE purpose='proposal-fee' AND payee_id=?",provider.account.account['id']))==1


@pytest.mark.parametrize('corruption',[None,'missing_format','missing_attempt','wrong_owner'])
def test_only_explicit_original_unclaimed_material_record_can_resume_after_adapter_becomes_ready(platform,tmp_path,monkeypatch,corruption):
    monkeypatch.setenv('A2C_CACHE_DIR',str(tmp_path/'verifier-cache'))
    def arbitration_only(*args,**kwargs):pytest.fail('Material review must not call the arbitration model')
    monkeypatch.setitem(platform.market.market.models,'test-observation-model',arbitration_only)
    host=MaterialHost();host.allowed=True
    host.registration=HostRegistration('prepared-material-boundary','1',readonly_context='unavailable')
    with participants(platform,tmp_path,formation='proposal',fee=1,agent_host=host,verifier_source=SOURCE,
            verifier_kind='skill',template_terms=terms(platform,'proposal'),models=1) as (provider,assistant,_,template,task):
        rpc=LocalClient(assistant.config.home);operation=uid()
        args={'taskRef':task,'service':provider.identities[0].agent_id,'templateDigest':template['templateDigest'],
            'spec':{'result':{}},'materials':{'original':{'text':'actual original material','spec':{}}}}
        initial=rpc.call('assistant.delegation.request',args,operation_id=operation)
        assert initial['status']=='waiting'
        user_decision(assistant,initial['data']['confirmationRef'])
        started=rpc.call('assistant.delegation.request',args,operation_id=operation)
        assert started['status']=='accepted'
        original=wait_pending_job(provider)
        reference=original['result']['materialObservation']
        assert reference['proof']=='prepared_unclaimed' and reference['child']['attempt'] is None
        assert not host.observations
        provider.close()
        host.registration=HostRegistration('prepared-material-boundary','1',readonly_context='isolated_session')
        fresh=bound_runtime(RuntimeConfig.load(tmp_path/'provider/config.json'),host,lambda selected:ProviderRole(selected))
        try:
            if corruption is not None:
                namespace='model-observations:'+reference['owner']['actor'];key=reference['child']['operation']
                record=fresh.store.checkpoint(namespace,key)
                if corruption=='missing_format':record.pop('materialFormat')
                elif corruption=='missing_attempt':record.pop('submissionAttempt')
                else:record['materialOwner']['actor']='another-actor'
                fresh.store.checkpoint_set(namespace,key,record)
            fresh.start();wait_for(lambda:fresh.connection.connected)
            if corruption is None:
                wait_for(lambda:terminal(assistant,started['data']['quoteRef']))
                finished=terminal(assistant,started['data']['quoteRef'])
                assert finished['status']=='completed',finished
                assert host.observations[0][0]==reference['child']['operation'] and len(host.observations)==2
                assert finished['data']['card']['signatures']['2'] and finished['data']['card']['signatures']['3']
            else:
                refused=LocalClient(fresh.config.home).call('provider.task.resume',{'profile':'service','taskRef':original['taskRef']})
                assert refused['status'] in ('unknown','rejected')
                snapshots=[]
                def settled_task():
                    value=LocalClient(fresh.config.home).call('provider.tasks.get',{'profile':'service','taskRef':original['taskRef']})['data']['items'][0]
                    if value['status'] in ('waiting','unknown'):snapshots.append(value)
                    return bool(snapshots)
                wait_for(settled_task)
                current=snapshots[0]
                assert current['status'] in ('waiting','unknown') and not current['canResume']
                assert not host.observations
            assert len(platform.db.rows("SELECT * FROM transfers WHERE purpose='proposal-fee' AND payee_id=?",fresh.account.account['id']))==1
        finally:fresh.close()


def test_claim_evidence_store_contention_preserves_listener_and_resumes_the_same_material_job(platform,tmp_path,monkeypatch):
    import threading
    from a2c.provider import Provider
    monkeypatch.setenv('A2C_CACHE_DIR',str(tmp_path/'verifier-cache'))
    def arbitration_only(*args,**kwargs):pytest.fail('Material review must not call the arbitration model')
    monkeypatch.setitem(platform.market.market.models,'test-observation-model',arbitration_only)
    enabled=threading.Event();held=threading.Event();busy=threading.Event();release=threading.Event()
    holder=[];workers=[]
    prepare=Provider.resume_material_review
    class ObservedLock:
        def __init__(self,original):self.original=original
        def acquire(self,*args,**kwargs):
            acquired=self.original.acquire(*args,**kwargs)
            if kwargs.get('blocking') is False and not acquired and held.is_set():busy.set()
            return acquired
        def release(self):return self.original.release()
        def __enter__(self):self.acquire();return self
        def __exit__(self,*_):self.release()
    from ioalab_sdk.runtime.scheduling import CURRENT_JOB
    def qualified_prepare(self,task_id,*,guard):
        reply=prepare(self,task_id,guard=guard)
        # Hold the Store only for the listener's claim qualification; the original
        # job's own finish would block on it instead of qualifying the claim.
        if enabled.is_set() and CURRENT_JOB.get() is None:
            enabled.clear()
            def hold_store():
                with holder[0].store.lock:
                    held.set()
                    assert release.wait(5),'Release the controlled original Store reader'
            thread=threading.Thread(target=hold_store,daemon=True);workers.append(thread);thread.start()
            assert held.wait(5),'The original Store lock must really be held before returning the public preparation'
        return reply
    monkeypatch.setattr(Provider,'resume_material_review',qualified_prepare)
    host=MaterialHost()
    with participants(platform,tmp_path,formation='proposal',fee=1,agent_host=host,verifier_source=SOURCE,
            verifier_kind='skill',template_terms=terms(platform,'proposal'),models=1) as (provider,assistant,_,template,task):
        holder.append(provider)
        monkeypatch.setattr(provider.store,'lock',ObservedLock(provider.store.lock))
        rpc=LocalClient(assistant.config.home);operation=uid()
        args={'taskRef':task,'service':provider.identities[0].agent_id,'templateDigest':template['templateDigest'],
            'spec':{'result':{}},'materials':{'original':{'text':'actual original material','spec':{}}}}
        initial=rpc.call('assistant.delegation.request',args,operation_id=operation)
        assert initial['status']=='waiting'
        user_decision(assistant,initial['data']['confirmationRef'])
        started=rpc.call('assistant.delegation.request',args,operation_id=operation)
        assert started['status']=='accepted'
        original=wait_pending_job(provider,ready=observed(host,2));first=host.observations[0][:2]
        try:
            enabled.set()
            assert busy.wait(5),'Real claim qualification must observe contention after the public A2C guard returned'
        finally:
            release.set()
            for thread in workers:thread.join(5);assert not thread.is_alive()
        state=LocalClient(provider.config.home).call('runtime.status',{})['data']
        assert not state['roleErrors']
        assert any(identity['profile']=='service' and identity['executionReady'] for identity in state['identities'])
        host.allowed=True
        wait_for(lambda:terminal(assistant,started['data']['quoteRef']))
        finished=terminal(assistant,started['data']['quoteRef'])
        assert finished['status']=='completed' and finished['data']['card']['signatures']['2'] and finished['data']['card']['signatures']['3']
        current=LocalClient(provider.config.home).call('provider.tasks.get',{'profile':'service','taskRef':original['taskRef']})['data']['items'][0]
        assert current['taskRef']==original['taskRef'] and current['interactionId']==original['interactionId'] and current['status']=='completed'
        assert host.observations[0][:2]==first and sum(value[0]==first[0] for value in host.observations)==1
        assert len(set(value[0] for value in host.observations))==2
        assert len(platform.db.rows("SELECT * FROM transfers WHERE purpose='proposal-fee' AND payee_id=?",provider.account.account['id']))==1


def test_store_contention_while_recording_pending_material_keeps_the_same_job_recoverable(platform,tmp_path,monkeypatch):
    import sys
    from ioalab_sdk.runtime import MaterialObservations,MaterialObservationBlocked
    monkeypatch.setenv('A2C_CACHE_DIR',str(tmp_path/'verifier-cache'))
    def arbitration_only(*args,**kwargs):pytest.fail('Material review must not call the arbitration model')
    monkeypatch.setitem(platform.market.market.models,'test-observation-model',arbitration_only)
    original_eligible=MaterialObservations.eligible;injected=[]
    def contended(self,owner):
        caller=sys._getframe(1).f_code
        # The endpoint's direct qualification after a proven undispatched child.
        if not injected and caller.co_name=='finish' and caller.co_filename.endswith('endpoint.py'):
            injected.append(owner['jobId'])
            raise MaterialObservationBlocked('Original SDK material evidence is busy',reason='evidence_busy')
        return original_eligible(self,owner)
    monkeypatch.setattr(MaterialObservations,'eligible',contended)
    host=MaterialHost()
    with participants(platform,tmp_path,formation='proposal',fee=1,agent_host=host,verifier_source=SOURCE,
            verifier_kind='skill',template_terms=terms(platform,'proposal'),models=1) as (provider,assistant,_,template,task):
        rpc=LocalClient(assistant.config.home);operation=uid()
        args={'taskRef':task,'service':provider.identities[0].agent_id,'templateDigest':template['templateDigest'],
            'spec':{'result':{}},'materials':{'original':{'text':'actual original material','spec':{}}}}
        initial=rpc.call('assistant.delegation.request',args,operation_id=operation)
        assert initial['status']=='waiting'
        user_decision(assistant,initial['data']['confirmationRef'])
        started=rpc.call('assistant.delegation.request',args,operation_id=operation)
        assert started['status']=='accepted'
        original=wait_pending_job(provider,ready=observed(host,2))
        ids=list(dict.fromkeys(value[0] for value in host.observations))
        assert injected==[original['taskRef']]
        reference=original['result']['materialObservation']
        assert reference['proof']=='not_submitted' and reference['child']['operation']==ids[1]
        host.allowed=True
        wait_for(lambda:terminal(assistant,started['data']['quoteRef']))
        finished=terminal(assistant,started['data']['quoteRef'])
        assert finished['status']=='completed' and finished['data']['card']['signatures']['2'] and finished['data']['card']['signatures']['3']
        current=LocalClient(provider.config.home).call('provider.tasks.get',{'profile':'service','taskRef':original['taskRef']})['data']['items'][0]
        assert current['taskRef']==original['taskRef'] and current['interactionId']==original['interactionId'] and current['status']=='completed'
        assert sum(value[0]==ids[0] for value in host.observations)==1
        assert list(dict.fromkeys(value[0] for value in host.observations))==ids
        assert len(platform.db.rows("SELECT * FROM transfers WHERE purpose='proposal-fee' AND payee_id=?",provider.account.account['id']))==1
